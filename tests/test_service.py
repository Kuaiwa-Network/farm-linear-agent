"""serve() wiring: the HTTP receiver and both loops run and shut down cleanly (spec §3)."""
import contextlib
import hashlib
import hmac
import io
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent.config import Config, Paths
from agent.launcher import Launcher
from agent.ledger import Ledger
from agent.service import Components, build, enqueue, main, seed_clones, serve
from agent.slots import SlotError
from test_ledger import ISSUE, issue

APP = "e5a8c16d-9f85-4123-acf5-94e41c3304d5"
REPOS = ("Farm-Client", "farm-hive", "farmgui", "common", "Farm-Contract")


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
        self.addCleanup(self.c.lifecycle.ledger.close)
        self.addCleanup(self.c.progress.ledger.close)
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

    def test_lifecycle_owns_its_connection_and_launches_require_preflight(self):
        self.assertIsNot(self.c.lifecycle.ledger.connection, self.c.ledger.connection)
        self.assertTrue(callable(self.c.scheduler.preflight))
        control = self.c.scheduler.control_ledger_factory()
        try:
            self.assertIsNot(control.connection, self.c.ledger.connection)
        finally:
            control.close()

    def test_build_hands_the_pool_the_launchers_runner_and_the_scheduler_the_same_slot_entries(self):
        """The production wiring of Task 7's two injections, asserted on build() rather than on objects a
        test constructed. A pool with no runner refuses every batch grant, and a scheduler with no entries
        silently drops `build_target` out of every resource block — both are green in the direct tests."""
        self.assertEqual(self.c.pool.run_unsandboxed, self.c.launcher.run_unsandboxed)
        config = Config(client_id="client", client_secret="s", webhook_secret="signing-secret", host="test",
                        runtime="fake", repos=dict(self.c.config.repos), max_concurrent=2, port=0,
                        local_root=Path(self.tmp.name) / "slotted",
                        codex_workers={"fix": {"model": "gpt-5.6-sol", "reasoning_effort": "high"}},
                        slots=[{"id": "unity_slot:1", "repo": "Farm-Client", "build_target_argument": "OSXUniversal"}])
        components = build(config)
        self.addCleanup(components.server.server_close)
        self.addCleanup(components.ledger.close)
        self.addCleanup(components.receiver.close)
        self.addCleanup(components.pool.close)
        self.addCleanup(components.lifecycle.ledger.close)
        self.addCleanup(components.progress.ledger.close)
        self.assertEqual(components.scheduler.slot_entries, components.pool.entries)
        self.assertEqual(components.scheduler.slot_entries["unity_slot:1"]["build_target_argument"], "OSXUniversal")
        self.assertEqual(components.pool.run_unsandboxed, components.launcher.run_unsandboxed)
        self.assertEqual(components.scheduler.codex_workers, config.codex_workers)

    def test_shutdown_kills_an_unsandboxed_run_instead_of_orphaning_it_on_the_slot(self):
        """The batch Editor is a direct child of `serve` and the pool thread that waits on it is a daemon, so
        without a kill in serve()'s finally a restart leaves a real Unity holding the slot folder — which the
        next ensure() then reads as a slot some other Editor already has."""
        marker = Path(self.tmp.name) / "unsandboxed.pid"
        script = ("import os, pathlib, sys, time;"
                  "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()));"
                  "time.sleep(120)")
        finished = threading.Event()

        def run():
            try:
                self.c.launcher.run_unsandboxed([sys.executable, "-c", script, str(marker)],
                                                cwd=self.tmp.name, timeout=120, owner="itm_batch")
            finally:
                finished.set()

        runner = threading.Thread(target=run, daemon=True)
        problems = []

        def serve_once():
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    serve(components=self.c)
            except BaseException as exc:
                problems.append(exc)

        thread = threading.Thread(target=serve_once, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 20)
        self.assertTrue(self.wait_for_health(), problems)
        self.addCleanup(self.c.server.shutdown)
        runner.start()
        self.addCleanup(runner.join, 20)
        self.addCleanup(self.c.launcher.stop_unsandboxed, "itm_batch")
        deadline = time.time() + 15
        while not marker.exists() and time.time() < deadline:
            time.sleep(0.05)
        self.assertTrue(marker.exists(), "the unsandboxed run never started")
        pid = int(marker.read_text())
        self.c.server.shutdown()
        thread.join(timeout=20)
        self.assertFalse(thread.is_alive())
        self.assertEqual(problems, [])
        self.assertTrue(finished.wait(15), "serve's finally left the pool's Editor running")
        gone = time.time() + 10
        while Launcher.alive(pid) and time.time() < gone:
            time.sleep(0.05)
        self.assertFalse(Launcher.alive(pid))

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


@unittest.skipIf(os.name == "nt", "POSIX service termination contract")
class SignalShutdownTests(unittest.TestCase):
    def test_sigterm_reaps_the_batch_child_and_closes_a_running_service(self):
        self.check_sigterm("serving")

    def test_sigterm_during_pool_ensure_also_cleans_up_and_restores_the_handler(self):
        self.check_sigterm("startup")

    def check_sigterm(self, phase):
        """Removing the signal handler or placing ensure outside finally orphans this real child."""
        script = textwrap.dedent('''
            import signal, sqlite3, sys, threading
            from pathlib import Path
            from agent.config import Config
            from agent.service import build, serve

            root, phase = Path(sys.argv[1]), sys.argv[2]
            components = build(Config(client_id="c", client_secret="s", webhook_secret="w",
                                      host="signal-test", runtime="fake", repos={}, port=0,
                                      local_root=root / "local"))
            original_pool = components.pool
            child = ("import os,pathlib,sys,time;"
                     "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()));time.sleep(120)")

            class Pool:
                def ensure(self):
                    if phase == "startup":
                        self.runner = threading.Thread(target=self.tick, daemon=True)
                        self.runner.start()
                        threading.Event().wait(120)

                def tick(self):
                    components.launcher.run_unsandboxed(
                        [sys.executable, "-c", child, str(root / "child.pid")],
                        cwd=root, timeout=120, owner="temporary-batch")

                def close(self):
                    if phase == "startup":
                        self.runner.join(10)
                        assert not self.runner.is_alive()
                    original_pool.close()

            components = components._replace(pool=Pool())
            (root / "port").write_text(str(components.server.server_address[1]))
            previous = signal.getsignal(signal.SIGTERM)
            serve(components=components)
            assert components.server.socket.fileno() == -1
            assert signal.getsignal(signal.SIGTERM) == previous
            for ledger in (components.ledger, original_pool.ledger):
                try:
                    ledger.connection.execute("SELECT 1")
                except sqlite3.ProgrammingError:
                    pass
                else:
                    raise AssertionError("service left a ledger connection open")
            (root / "closed").write_text("closed")
        ''')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stub = root / "stub"
            stub.mkdir()
            env = dict(os.environ, FARMBOT_LINEAR_STUB_DIR=str(stub),
                       FARMBOT_CONFIG=str(root / "unused.json"))
            child_pid = None
            with (root / "service.log").open("w+") as output:
                process = subprocess.Popen([sys.executable, "-W", "error", "-c", script,
                                            str(root), phase], env=env, stdout=output, stderr=output)
                try:
                    deadline = time.monotonic() + 15
                    marker = root / "child.pid"
                    while ((not marker.exists() or not marker.read_text()) and process.poll() is None
                           and time.monotonic() < deadline):
                        time.sleep(0.02)
                    self.assertTrue(marker.exists(), (root / "service.log").read_text())
                    child_pid = int(marker.read_text())
                    self.assertTrue(Launcher.alive(child_pid))
                    if phase == "serving":
                        port = int((root / "port").read_text())
                        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as response:
                            self.assertEqual(response.status, 200)
                    process.send_signal(signal.SIGTERM)
                    process.wait(timeout=20)
                    self.assertEqual(process.returncode, 0, (root / "service.log").read_text())
                    self.assertFalse(Launcher.alive(child_pid), "SIGTERM orphaned the batch process")
                    self.assertTrue((root / "closed").exists(), "service skipped resource cleanup")
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=10)
                    if child_pid is not None and Launcher.alive(child_pid):
                        try:
                            os.killpg(child_pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass


class EnqueueTests(unittest.TestCase):
    """The operator's way in on a host whose webhook cannot be delivered (spec §11)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.stub = root / "stub"
        self.stub.mkdir()
        (self.stub / "issue.json").write_text(json.dumps(issue(labels=["Bug"], delegate_id=APP)), encoding="utf-8")
        patcher = patch.dict(os.environ, {"FARMBOT_LINEAR_STUB_DIR": str(self.stub),
                                          "FARMBOT_CONFIG": str(root / "none.json")})
        patcher.start()
        self.addCleanup(patcher.stop)
        # No repos: every test here passes an explicit commit, so nothing ever reaches git or the network.
        self.config = Config(client_id="c", client_secret="s", webhook_secret="w", host="test",
                             runtime="fake", repos={}, local_root=root / "local")

    def test_enqueue_creates_a_pinned_work_item_without_any_webhook(self):
        item_id = enqueue(self.config, issue_ref=ISSUE, skill="fix", commit="a" * 40)["id"]
        ledger = Ledger(Paths(self.config).ledger)
        self.addCleanup(ledger.close)
        item = ledger.item(item_id)
        self.assertEqual((item["state"], item["skill"]), ("queued", "fix"))
        self.assertEqual(item["target"]["commit_sha"], "a" * 40)
        # The session is synthetic and says so: Linear has no agent session with this id, which is what
        # the scheduler and the skill both read to report through an issue comment instead.
        self.assertTrue(item["session_id"].startswith("local-"), item["session_id"])
        self.assertEqual([row["kind"] for row in ledger.connection.execute(
            "SELECT kind FROM audit WHERE item_id=?", (item_id,))].count("enqueue"), 1)

    def test_enqueue_refuses_a_write_capable_skill_on_an_issue_nobody_delegated(self):
        """spec §4: fix, fgui and feature start only from delegation, so that every code change traces back
        to an explicit human act on the issue. enqueue is an operator shortcut past the webhook, not past
        the rule of authority."""
        (self.stub / "issue.json").write_text(json.dumps(issue(labels=["Bug"], delegate_id=None)),
                                              encoding="utf-8")
        with self.assertRaises(RuntimeError) as caught:
            enqueue(self.config, issue_ref=ISSUE, skill="fix", commit="a" * 40)
        self.assertIn("delegate", str(caught.exception).lower())
        ledger = Ledger(Paths(self.config).ledger)
        self.addCleanup(ledger.close)
        self.assertIsNone(ledger.active_item_for_issue(ISSUE))

    def test_the_enqueue_and_slots_subcommands_run_the_way_the_operating_contract_prints_them(self):
        """The contract now tells an operator to create work with `agent.service enqueue` and to watch the
        pool with `agent.service slots`. enqueue() and Ledger.slots() are tested directly above and in
        test_ledger; what is untested without this is `main`'s own dispatch — the two lines a typo in a
        `choices` list or a missing flag would break, silently, on the host that has no webhook."""
        config_path = Path(self.tmp.name) / "config.json"
        config_path.write_text(json.dumps({"client_id": "c", "client_secret": "s", "webhook_secret": "w",
                                           "host": "test", "runtime": "fake", "repos": {},
                                           "local_root": str(Path(self.tmp.name) / "local")}), encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(["enqueue", "--config", str(config_path), "--issue", ISSUE,
                                   "--commit", "a" * 40]), 0)
        created = json.loads(out.getvalue())
        self.assertEqual(created["state"], "queued")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(["slots", "--config", str(config_path)]), 0)
        view = json.loads(out.getvalue())
        self.assertEqual((view["slots"], view["reservations"]), ([], []))  # nothing registered, nothing queued
        with self.assertRaises(SystemExit):
            main(["enqueue", "--config", str(config_path)])  # an enqueue with no issue names nothing

    def test_enqueue_allows_a_read_only_skill_on_an_undelegated_issue(self):
        """chat writes nothing, so the rule of authority does not apply to it; refusing it would make the
        refusal above a test of `enqueue` refusing everything."""
        (self.stub / "issue.json").write_text(json.dumps(issue(labels=["Bug"], delegate_id=None)),
                                              encoding="utf-8")
        item = enqueue(self.config, issue_ref=ISSUE, skill="chat", commit="a" * 40)
        self.assertEqual((item["state"], item["skill"]), ("queued", "chat"))


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
