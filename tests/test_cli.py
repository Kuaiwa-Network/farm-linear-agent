import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_ledger import ISSUE, issue

ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "ledger.sqlite3"
        self.stub = self.root / "stub"
        self.stub.mkdir()
        (self.stub / "issue.json").write_text(json.dumps(issue(labels=["Bug"])), encoding="utf-8")
        self.env = {**os.environ, "FARMBOT_LINEAR_STUB_DIR": str(self.stub), "FARMBOT_CONFIG": str(self.root / "missing.json")}

    def run_cli(self, *args, success=True):
        process = subprocess.run([sys.executable, "-m", "agent", "--db", str(self.db), *args], cwd=ROOT, env=self.env,
                                 text=True, capture_output=True, timeout=30)
        if success:
            self.assertEqual(0, process.returncode, process.stderr)
            return json.loads(process.stdout)
        self.assertNotEqual(0, process.returncode)
        self.assertNotIn("Traceback", process.stderr)
        self.assertTrue(process.stderr.strip())
        return process

    def json_file(self, name, content):
        path = self.root / name
        path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
        return str(path)

    def calls(self):
        text = (self.stub / "calls.jsonl").read_text(encoding="utf-8") if (self.stub / "calls.jsonl").exists() else ""
        return [json.loads(line) for line in text.splitlines()]

    def seeded_item(self):
        """Create a work item the way the receiver would, then return its id."""
        from agent.ledger import Ledger
        ledger = Ledger(self.db)
        ledger.observe_issue(issue(labels=["Bug"]))
        ledger.ensure_session("session-1", ISSUE, delegation=True)
        item = ledger.create_work_item(issue_id=ISSUE, session_id="session-1", skill="fix")
        ledger.close()
        return item["id"]

    def test_fix_round_trip_through_the_cli(self):
        item = self.seeded_item()
        fetched = self.run_cli("fetch-issue", "--item", item)
        self.assertEqual(fetched["identifier"], "FARM-1")
        claimed = self.run_cli("claim", "--item", item, "--worker-id", "pid-1")
        token = claimed["token"]
        context = self.run_cli("issue-context", "--item", item)
        self.assertEqual(context["coordination"]["state"], "running")
        self.assertNotIn("token", json.dumps(context))
        body = self.root / "started.md"
        body.write_text("👀 FarmBot 已开始处理：正在复现。", encoding="utf-8")
        action = self.run_cli("prepare-comment", "--item", item, "--token", token, "--kind", "started", "--body-file", str(body))
        posted = self.run_cli("post-comment", "--action-id", action["action_id"])
        self.assertEqual(posted["remote_id"], "stub-comment-1")
        self.assertEqual(self.calls()[-1]["method"], "create_comment")
        self.assertIn(action["marker"], self.calls()[-1]["body"])
        self.run_cli("checkpoint", "--item", item, "--token", token, "--input",
                     self.json_file("cp.json", {"stage": "diagnose", "published_prs": ["https://github.com/o/r/pull/9"]}))
        blocker = self.root / "blocker.md"
        blocker.write_text("需要设备型号。", encoding="utf-8")
        blocked = self.run_cli("prepare-comment", "--item", item, "--token", token, "--kind", "blocker", "--body-file", str(blocker))
        self.run_cli("post-comment", "--action-id", blocked["action_id"])
        finished = self.run_cli("finish", "--item", item, "--token", token, "--outcome", "blocked", "--input",
                                self.json_file("out.json", {"summary": "缺少设备信息", "comment_action_id": blocked["action_id"]}))
        self.assertEqual(finished["state"], "blocked")

    def test_post_comment_reconciles_an_existing_marker_instead_of_posting_twice(self):
        item = self.seeded_item()
        token = self.run_cli("claim", "--item", item, "--worker-id", "w")["token"]
        body = self.root / "b.md"
        body.write_text("x", encoding="utf-8")
        action = self.run_cli("prepare-comment", "--item", item, "--token", token, "--kind", "started", "--body-file", str(body))
        existing = issue(labels=["Bug"], comments=[{"id": "c-existing", "body": f"x\n\n{action['marker']}", "author_kind": "bot",
                                                   "created_at": "2026-09-18T00:00:00Z", "updated_at": "2026-09-18T00:00:00Z"}])
        (self.stub / "issue.json").write_text(json.dumps(existing), encoding="utf-8")
        posted = self.run_cli("post-comment", "--action-id", action["action_id"])
        self.assertEqual(posted["remote_id"], "c-existing")
        self.assertNotIn("create_comment", [c["method"] for c in self.calls()])

    def test_activity_and_await_input_park_the_item(self):
        item = self.seeded_item()
        token = self.run_cli("claim", "--item", item, "--worker-id", "w")["token"]
        body = self.root / "q.md"
        body.write_text("需要哪个环境？", encoding="utf-8")
        self.run_cli("activity", "--item", item, "--token", token, "--type", "thought", "--body-file", str(body))
        self.assertEqual(self.calls()[-1]["method"], "create_activity")
        parked = self.run_cli("await-input", "--item", item, "--token", token, "--question", "需要哪个环境？")
        self.assertEqual(parked["state"], "awaiting_input")
        self.assertEqual(self.calls()[-1]["content"]["type"], "elicitation")

    def test_await_resource_is_refused_without_slots_and_errors_are_clean(self):
        item = self.seeded_item()
        token = self.run_cli("claim", "--item", item, "--worker-id", "w")["token"]
        process = self.run_cli("await-resource", "--item", item, "--token", token, "--resource", "unity_slot", "--mode", "batch", success=False)
        self.assertIn("no unity slots", process.stderr)
        self.run_cli("claim", "--item", item, "--worker-id", "w2", success=False)
