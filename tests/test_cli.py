import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_ledger import ISSUE, OTHER, issue

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
        self.env.pop("FARMBOT_TOKEN", None)

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

    def seeded_item(self, issue_id=ISSUE, session="session-1", skill="fix"):
        """Create a work item the way the receiver would, then return its id."""
        from agent.ledger import Ledger
        ledger = Ledger(self.db)
        ledger.observe_issue(issue(id=issue_id, labels=["Bug"]))
        ledger.ensure_session(session, issue_id, delegation=True)
        item = ledger.create_work_item(issue_id=issue_id, session_id=session, skill=skill)
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
        posted = self.run_cli("post-comment", "--item", item, "--token", token, "--action-id", action["action_id"])
        self.assertEqual(posted["remote_id"], "stub-comment-1")
        self.assertEqual(self.calls()[-1]["method"], "create_comment")
        self.assertIn(action["marker"], self.calls()[-1]["body"])
        self.run_cli("checkpoint", "--item", item, "--token", token, "--input",
                     self.json_file("cp.json", {"stage": "diagnose", "published_prs": ["https://github.com/o/r/pull/9"]}))
        blocker = self.root / "blocker.md"
        blocker.write_text("需要设备型号。", encoding="utf-8")
        blocked = self.run_cli("prepare-comment", "--item", item, "--token", token, "--kind", "blocker", "--body-file", str(blocker))
        self.run_cli("post-comment", "--item", item, "--token", token, "--action-id", blocked["action_id"])
        finished = self.run_cli("finish", "--item", item, "--token", token, "--outcome", "blocked", "--input",
                                self.json_file("out.json", {"summary": "缺少设备信息", "comment_action_id": blocked["action_id"]}))
        self.assertEqual(finished["state"], "blocked")
        final = self.calls()[-1]  # finish completes the Linear session; the worker posts nothing itself
        self.assertEqual((final["method"], final["session_id"], final["content"]["type"]), ("create_activity", "session-1", "response"))
        self.assertIn("缺少设备信息", final["content"]["body"])

    def test_delivered_fix_completes_the_session_with_its_prs_and_chat_answers_do_not_double_post(self):
        item = self.seeded_item()
        token = self.run_cli("claim", "--item", item, "--worker-id", "w")["token"]
        body = self.root / "d.md"
        body.write_text("已修复。", encoding="utf-8")
        action = self.run_cli("prepare-comment", "--item", item, "--token", token, "--kind", "delivery", "--body-file", str(body))
        self.run_cli("post-comment", "--item", item, "--token", token, "--action-id", action["action_id"])
        self.run_cli("finish", "--item", item, "--token", token, "--outcome", "delivered", "--input",
                     self.json_file("d.json", {"summary": "修好了", "comment_action_id": action["action_id"],
                                               "verification": "dotnet test", "prs": ["https://github.com/o/r/pull/9"]}))
        final = self.calls()[-1]
        self.assertEqual(final["content"]["type"], "response")
        self.assertIn("https://github.com/o/r/pull/9", final["content"]["body"])
        chat = self.seeded_item(issue_id=OTHER, session="session-2", skill="chat")
        chat_token = self.run_cli("claim", "--item", chat, "--worker-id", "w2")["token"]
        before = len(self.calls())
        self.run_cli("finish", "--item", chat, "--token", chat_token, "--outcome", "delivered", "--input",
                     self.json_file("c.json", {"summary": "answered", "comment_action_id": None, "verification": "answered in session", "prs": []}))
        self.assertEqual(len(self.calls()), before)  # the chat answer was already the session's response

    def test_post_comment_reconciles_an_existing_marker_instead_of_posting_twice(self):
        item = self.seeded_item()
        token = self.run_cli("claim", "--item", item, "--worker-id", "w")["token"]
        body = self.root / "b.md"
        body.write_text("x", encoding="utf-8")
        action = self.run_cli("prepare-comment", "--item", item, "--token", token, "--kind", "started", "--body-file", str(body))
        existing = issue(labels=["Bug"], comments=[{"id": "c-existing", "body": f"x\n\n{action['marker']}", "author_kind": "bot",
                                                   "created_at": "2026-09-18T00:00:00Z", "updated_at": "2026-09-18T00:00:00Z"}])
        (self.stub / "issue.json").write_text(json.dumps(existing), encoding="utf-8")
        posted = self.run_cli("post-comment", "--item", item, "--token", token, "--action-id", action["action_id"])
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

    def test_token_file_authorizes_a_renew_and_a_missing_token_is_refused(self):
        item = self.seeded_item()
        token = self.run_cli("claim", "--item", item, "--worker-id", "w")["token"]
        path = self.root / "token"
        path.write_text(token, encoding="utf-8")
        renewed = self.run_cli("renew", "--item", item, "--token-file", str(path))
        self.assertEqual(renewed["state"], "running")
        self.assertNotIn("token", renewed)
        process = self.run_cli("renew", "--item", item, success=False)
        self.assertIn("claim token required", process.stderr)

    def test_post_comment_refuses_an_action_id_from_another_item(self):
        mine = self.seeded_item()
        token = self.run_cli("claim", "--item", mine, "--worker-id", "w")["token"]
        body = self.root / "b.md"
        body.write_text("x", encoding="utf-8")
        action = self.run_cli("prepare-comment", "--item", mine, "--token", token, "--kind", "started", "--body-file", str(body))
        other = self.seeded_item(issue_id=OTHER, session="session-2")
        other_token = self.run_cli("claim", "--item", other, "--worker-id", "w2")["token"]
        process = self.run_cli("post-comment", "--item", other, "--token", other_token, "--action-id", action["action_id"],
                               success=False)
        self.assertIn("unknown comment action", process.stderr)
        self.assertNotIn("create_comment", [c["method"] for c in self.calls()])

    def write_config(self, repos):
        config = self.root / "config.json"
        config.write_text(json.dumps({"client_id": "c", "client_secret": "s", "webhook_secret": "w", "repos": repos,
                                      "local_root": str(self.root / "local")}), encoding="utf-8")
        self.env["FARMBOT_CONFIG"] = str(config)

    def test_an_explicit_token_beats_a_stale_environment_token(self):
        item = self.seeded_item()
        token = self.run_cli("claim", "--item", item, "--worker-id", "w")["token"]
        self.env["FARMBOT_TOKEN"] = "stale-token-from-an-earlier-item"
        self.assertEqual(self.run_cli("renew", "--item", item, "--token", token)["state"], "running")
        process = self.run_cli("renew", "--item", item, success=False)  # the environment alone is still consulted
        self.assertNotIn("claim token required", process.stderr)

    def test_pr_targets_accept_ssh_remotes_ignore_case_and_refuse_a_config_without_github(self):
        item = self.seeded_item()
        token = self.run_cli("claim", "--item", item, "--worker-id", "w")["token"]
        ok = self.json_file("ok.json", {"published_prs": ["https://github.com/Kuaiwa-Network/Farm-Client/pull/1"]})
        self.write_config({"Farm-Client": "git@github.com:kuaiwa-network/farm-client.git"})
        self.assertEqual(self.run_cli("checkpoint", "--item", item, "--token", token, "--input", ok)["state"], "running")
        self.write_config({"Farm-Client": "https://example.com/farm/Farm-Client.git"})
        process = self.run_cli("checkpoint", "--item", item, "--token", token, "--input", ok, success=False)
        self.assertIn("no configured GitHub repository", process.stderr)
        empty = self.json_file("none.json", {"published_prs": []})
        self.assertEqual(self.run_cli("checkpoint", "--item", item, "--token", token, "--input", empty)["state"], "running")

    def test_checkpoint_refuses_a_pr_outside_the_configured_repositories(self):
        self.write_config({"Farm-Client": "https://github.com/Kuaiwa-Network/Farm-Client.git"})
        item = self.seeded_item()
        token = self.run_cli("claim", "--item", item, "--worker-id", "w")["token"]
        process = self.run_cli("checkpoint", "--item", item, "--token", token, "--input",
                               self.json_file("bad.json", {"published_prs": ["https://github.com/other/repo/pull/1"]}),
                               success=False)
        self.assertIn("not under a configured repository", process.stderr)
        accepted = self.run_cli("checkpoint", "--item", item, "--token", token, "--input",
                                self.json_file("ok.json", {"published_prs": ["https://github.com/Kuaiwa-Network/Farm-Client/pull/1"]}))
        self.assertEqual(accepted["state"], "running")

    def test_await_resource_is_refused_without_slots_and_errors_are_clean(self):
        item = self.seeded_item()
        token = self.run_cli("claim", "--item", item, "--worker-id", "w")["token"]
        process = self.run_cli("await-resource", "--item", item, "--token", token, "--resource", "unity_slot", "--mode", "batch", success=False)
        self.assertIn("no unity slots", process.stderr)
        self.run_cli("claim", "--item", item, "--worker-id", "w2", success=False)
