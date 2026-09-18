import hashlib
import hmac
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.config import Config
from agent.service import build
from test_ledger import ISSUE, issue

ROOT = Path(__file__).resolve().parents[1]
APP = "e5a8c16d-9f85-4123-acf5-94e41c3304d5"
REPOS = ("Farm-Client", "farm-hive", "farmgui", "common")


def git(*args, cwd):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True, capture_output=True)


class EndToEndTests(unittest.TestCase):
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
        env = {"FARMBOT_LINEAR_STUB_DIR": str(self.stub), "FARMBOT_CONFIG": str(root / "none.json"),
               "FAKE_CLI_REPO": str(ROOT), "FAKE_CLI_MODE": "cli",
               "FAKE_CLI_STEPS": json.dumps([
                   ["claim", "--item", "{item}", "--worker-id", "fake"],
                   ["prepare-comment", "--item", "{item}", "--token", "{token}", "--kind", "started", "--body-file", str(root / "started.md")],
                   ["post-comment", "--item", "{item}", "--token", "{token}", "--action-id", "{action_id}"],
                   ["prepare-comment", "--item", "{item}", "--token", "{token}", "--kind", "blocker", "--body-file", str(root / "blocker.md")],
                   ["post-comment", "--item", "{item}", "--token", "{token}", "--action-id", "{action_id}"],
                   ["finish", "--item", "{item}", "--token", "{token}", "--outcome", "blocked", "--input", str(root / "outcome.json")]])}
        (root / "started.md").write_text("👀 FarmBot 已开始处理", encoding="utf-8")
        (root / "blocker.md").write_text("缺少信息", encoding="utf-8")
        self.env_patch = patch.dict(os.environ, env)
        self.env_patch.start(); self.addCleanup(self.env_patch.stop)
        config = Config(client_id="client", client_secret="s", webhook_secret="signing-secret", host="test", runtime="fake",
                        repos=remotes, max_concurrent=2, port=0, local_root=root / "local")
        self.c = build(config)
        self.addCleanup(self.c.receiver.close)
        self.addCleanup(self.c.ledger.close)
        self.addCleanup(self.c.server.server_close)
        self.root = root

    def signed(self, event):
        body = json.dumps(event).encode()
        return body, hmac.new(b"signing-secret", body, hashlib.sha256).hexdigest()

    def created_event(self):
        return {"type": "AgentSessionEvent", "action": "created", "webhookTimestamp": int(time.time() * 1000), "organizationId": "org",
                "oauthClientId": "client", "appUserId": APP,
                "agentSession": {"id": "session-e2e", "issue": {"id": ISSUE, "identifier": "FARM-1", "url": "u"}}}

    def calls(self):
        path = self.stub / "calls.jsonl"
        return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def wait_state(self, item_id, states, timeout=40):
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.c.scheduler.tick()
            state = self.c.ledger.item(item_id)["state"]
            if state in states:
                return state
            time.sleep(0.2)
        self.fail(f"item never reached {states}; last state {state}")

    def test_delegated_bug_runs_a_worker_that_comments_and_finishes_blocked(self):
        received = time.time()
        self.assertEqual(self.c.receiver.receive(*self.signed(self.created_event())), (200, "accepted"))
        self.assertTrue(self.c.receiver.process_one())
        acked = time.time()
        self.assertLess(acked - received, 10)
        self.assertEqual(self.calls()[-1]["content"]["type"], "thought")
        item = self.c.ledger.items_for_session("session-e2e")[0]
        self.c.scheduler.tick()
        self.assertIsNotNone(self.c.ledger.item(item["id"])["worker_pid"])
        self.assertTrue((self.c.paths.worktrees / item["id"] / "Farm-Client").is_dir())
        # The worker prepares and posts both comments itself; its finish retries until outcome.json names
        # the blocker action, which only exists once the outbox row does.
        deadline = time.time() + 30
        while time.time() < deadline and len(self.c.ledger.outbox(item["id"])) < 2:
            time.sleep(0.2)
        blocker = next(a for a in self.c.ledger.outbox(item["id"]) if a["kind"] == "blocker")
        (self.root / "outcome.json").write_text(json.dumps({"summary": "缺少信息", "comment_action_id": blocker["action_id"]}), encoding="utf-8")
        self.assertEqual(self.wait_state(item["id"], {"blocked", "failed"}), "blocked")
        methods = [c["method"] for c in self.calls()]
        self.assertEqual(methods.count("create_comment"), 2)
        self.assertIn("👀 FarmBot 已开始处理", self.calls()[[i for i, m in enumerate(methods) if m == "create_comment"][0]]["body"])
        self.assertFalse((self.c.paths.worktrees / item["id"]).exists())

    def test_stop_kills_a_running_worker_within_five_seconds(self):
        with patch.dict(os.environ, {"FAKE_CLI_MODE": "sleep"}):
            self.c.receiver.receive(*self.signed(self.created_event())); self.c.receiver.process_one()
            item = self.c.ledger.items_for_session("session-e2e")[0]
            self.c.scheduler.tick()
            stop = self.created_event()
            stop["action"] = "prompted"
            stop["agentActivity"] = {"id": "stop-1", "signal": "stop", "content": {"type": "prompt"}, "createdAt": "2026-09-18T00:00:00Z"}
            started = time.time()
            self.assertEqual(self.c.receiver.receive(*self.signed(stop)), (200, "stop received"))
            self.c.receiver.process_one()
            self.assertLess(time.time() - started, 5)
            self.assertEqual(self.c.ledger.item(item["id"])["state"], "cancelled")
            self.assertEqual(self.calls()[-1]["content"]["type"], "response")
            self.c.scheduler.tick()
            self.assertEqual(self.c.launcher.running(), {})
