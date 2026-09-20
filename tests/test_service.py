"""serve() wiring: the HTTP receiver and both loops run and shut down cleanly (spec §3)."""
import contextlib
import hashlib
import hmac
import io
import json
import os
import sqlite3
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent.config import Config
from agent.service import Components, build, seed_clones, serve
from agent.slots import SlotError
from test_ledger import ISSUE, issue

APP = "e5a8c16d-9f85-4123-acf5-94e41c3304d5"
REPOS = ("Farm-Client", "farm-hive", "farmgui", "common")


def git(*args, cwd):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True, capture_output=True)


class ServeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        remotes = {}
        for repo in REPOS:
            origin = root / "origins" / repo
            origin.mkdir(parents=True)
            git("init", "-q", "-b", "main", ".", cwd=origin)
            (origin / "README.md").write_text(repo, encoding="utf-8")
            git("add", ".", cwd=origin); git("commit", "-qm", "init", cwd=origin)
            remotes[repo] = str(origin)
        self.stub = root / "stub"; self.stub.mkdir()
        (self.stub / "issue.json").write_text(json.dumps(issue(labels=["Bug"], delegate_id=APP)), encoding="utf-8")
        self.env_patch = patch.dict(os.environ, {"FARMBOT_LINEAR_STUB_DIR": str(self.stub),
                                                 "FARMBOT_CONFIG": str(root / "none.json"),
                                                 "FAKE_CLI_MODE": "exit-immediately"})
        self.env_patch.start(); self.addCleanup(self.env_patch.stop)
        config = Config(client_id="client", client_secret="s", webhook_secret="signing-secret", host="test", runtime="fake",
                        repos=remotes, max_concurrent=2, port=0, local_root=root / "local")
        self.c = build(config)
        self.addCleanup(self.drain_workers)
        self.addCleanup(self.c.pool.close)
        self.addCleanup(self.c.receiver.close)
        self.addCleanup(self.c.ledger.close)
        self.addCleanup(self.c.server.server_close)

    def drain_workers(self):
        """A tick may have launched the fake worker before shutdown; never leave a child unreaped."""
        for item_id in list(self.c.launcher.running()):
            self.c.launcher.stop(item_id)
        self.c.launcher.poll()

    def created_event(self):
        return {"type": "AgentSessionEvent", "action": "created", "webhookTimestamp": int(time.time() * 1000),
                "organizationId": "org", "oauthClientId": "client", "appUserId": APP,
                "agentSession": {"id": "session-serve", "issue": {"id": ISSUE, "identifier": "FARM-1", "url": "u"}}}

    def test_serve_answers_health_accepts_a_signed_webhook_and_shuts_down(self):
        failures = []

        def run():
            try:
                serve(components=self.c)
            except BaseException as exc:  # the thread must not swallow a failure silently
                failures.append(exc)

        thread = threading.Thread(target=run, daemon=True)
        with contextlib.redirect_stdout(io.StringIO()):
            thread.start()
            self.addCleanup(thread.join, 20)
            url = f"http://127.0.0.1:{self.c.server.server_address[1]}"
            deadline = time.time() + 20
            while True:
                try:
                    with urllib.request.urlopen(f"{url}/health", timeout=5) as response:
                        self.assertEqual(response.status, 200)
                        break
                except (urllib.error.URLError, OSError):
                    if failures:
                        raise failures[0]
                    if time.time() > deadline:
                        raise
                    time.sleep(0.05)
            # shutdown() blocks until serve_forever has run, so only arm it once /health answered.
            self.addCleanup(self.c.server.shutdown)
            body = json.dumps(self.created_event()).encode()
            signature = hmac.new(b"signing-secret", body, hashlib.sha256).hexdigest()
            request = urllib.request.Request(f"{url}/webhook", data=body, headers={"Linear-Signature": signature})
            with urllib.request.urlopen(request, timeout=10) as response:
                self.assertEqual(json.load(response), {"status": "accepted"})
            self.c.server.shutdown()
            thread.join(timeout=20)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        self.assertIs(self.c.scheduler.api, self.c.api)  # worker deaths reach the session through the same client

    def test_build_gives_the_pool_its_own_connection_and_never_the_schedulers(self):
        """The rule this whole task exists for, asserted on the production wiring rather than on a SlotPool
        a test constructed. serve() runs pool.tick() on a third thread while the scheduler ticks on its own
        connection, and Ledger._transaction is a bare BEGIN IMMEDIATE/COMMIT: two threads sharing one
        connection do not get two transactions — one thread's BEGIN lands inside the other's and either
        COMMIT applies to the other's half-written work. build() passes check_same_thread=False, so sharing
        would not raise anything at all; nothing else in the suite calls build(), so without this the
        one-line change from a factory to `ledger` here is a silent, green regression.
        """
        self.assertIsNot(self.c.pool.ledger, self.c.ledger)
        self.assertIsNot(self.c.pool.ledger.connection, self.c.ledger.connection)
        # And the pool owns what it opened: close() closes its connection and leaves the scheduler's alone,
        # which is the other half of "the pool was handed a factory" and is what serve()'s finally relies on.
        self.c.pool.close()
        with self.assertRaises(sqlite3.ProgrammingError):
            self.c.pool.ledger.connection.execute("SELECT 1")
        self.assertEqual(self.c.ledger.connection.execute("SELECT 1").fetchone()[0], 1)

    def wait_for_health(self, timeout=20):
        url = f"http://127.0.0.1:{self.c.server.server_address[1]}/health"
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=5) as response:
                    return response.status == 200
            except (urllib.error.URLError, OSError):
                time.sleep(0.05)
        return False

    def test_serve_still_starts_and_keeps_ticking_when_the_slot_pool_cannot_be_prepared(self):
        """A host whose one slot is unusable must still answer Linear. `ensure()` raising at start-up would
        otherwise take the whole service down, and the pool thread — the third one `serve` starts — must run
        regardless, because that is what eventually settles and parks whatever the operator recovers."""
        ticks = threading.Event()
        failed = []

        def ensure():
            failed.append(True)
            raise SlotError("unity_slot:1: still holds git-lfs pointer files")

        pool = SimpleNamespace(ensure=ensure, tick=lambda: ticks.set(), close=lambda: None)
        components = self.c._replace(pool=pool)   # Components is a namedtuple; attribute assignment refuses
        out = io.StringIO()
        problems = []

        def run():
            try:
                with contextlib.redirect_stdout(out):
                    serve(components=components)
            except BaseException as exc:
                problems.append(exc)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 20)
        self.assertTrue(self.wait_for_health(), problems)
        self.addCleanup(self.c.server.shutdown)
        self.assertTrue(ticks.wait(20), "serve never started the pool thread")
        self.c.server.shutdown()
        thread.join(timeout=20)
        self.assertFalse(thread.is_alive())
        self.assertEqual(problems, [])
        self.assertEqual(failed, [True])
        self.assertIn("slot_pool_unavailable", out.getvalue())


class LoopGuardTests(unittest.TestCase):
    def test_a_raising_loop_body_is_logged_and_the_loop_keeps_running(self):
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("boom")
            return False

        released = threading.Event()
        receiver = Mock(); receiver.process_one.side_effect = flaky
        server = Mock(); server.server_address = ("127.0.0.1", 1234)
        server.serve_forever.side_effect = lambda: released.wait(20)
        launcher = Mock(); launcher.runtime.name = "fake"
        config = Mock(); config.host = "test"
        components = Components(config, None, None, Mock(), {"chat"}, None, launcher, Mock(), receiver, server,
                                Mock())
        out = io.StringIO()
        failures = []

        def run():
            try:
                with contextlib.redirect_stdout(out):
                    serve(components=components)
            except BaseException as exc:
                failures.append(exc)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 20)
        self.addCleanup(released.set)
        deadline = time.time() + 20
        while time.time() < deadline and (len(calls) < 2 or "loop_error" not in out.getvalue()):
            time.sleep(0.05)
        released.set()
        thread.join(timeout=20)
        logged = [json.loads(line) for line in out.getvalue().splitlines() if "loop_error" in line]
        self.assertEqual(logged[0], {"event": "loop_error", "loop": "receive", "error": "RuntimeError"})
        self.assertGreaterEqual(len(calls), 2)
        self.assertEqual(failures, [])


class SeedCloneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.origin = self.root / "origin"
        self.origin.mkdir(parents=True)
        git("init", "-q", "-b", "main", ".", cwd=self.origin)
        (self.origin / "README.md").write_text("hi\n", encoding="utf-8")
        git("add", ".", cwd=self.origin); git("commit", "-qm", "init", cwd=self.origin)
        self.config = Config(client_id="c", client_secret="s", webhook_secret="w",
                             repos={"Farm-Client": str(self.origin)}, local_root=self.root / "local")

    def test_seed_clones_reports_what_it_created_and_then_reuses_it(self):
        self.assertEqual(seed_clones(self.config), {"Farm-Client": "created"})
        self.assertTrue((self.root / "local" / "repos" / "Farm-Client.git").is_dir())
        self.assertEqual(seed_clones(self.config), {"Farm-Client": "present"})

    def test_seed_clones_reports_the_checkout_it_seeded_from(self):
        checkout = self.root / "sources" / "Farm-Client"
        checkout.parent.mkdir(parents=True)
        git("clone", "-q", str(self.origin), str(checkout), cwd=self.root)
        report = seed_clones(self.config, source_root=self.root / "sources")
        self.assertEqual(report, {"Farm-Client": f"seeded from {checkout.resolve()}"})
