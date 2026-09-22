import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_ledger import ISSUE, OTHER, PIN, issue

ROOT = Path(__file__).resolve().parents[1]
SLOT = "unity_slot:1"
HOST = "test-host"


class CliTests(unittest.TestCase):
    def publication_fixture(self, issue_prefix='FARM'):
        from types import SimpleNamespace
        from agent.config import load_config
        from agent.__main__ import parser
        from agent.ledger import Ledger
        from test_worktrees import git
        item, token, _, _, path = self.verification_fixture()
        git('branch', '-m', f'farmbot/{issue_prefix.lower()}-1', cwd=path)
        git('remote', 'set-url', 'origin', 'https://github.com/Kuaiwa-Network/Farm-Client.git', cwd=path)
        config = load_config(self.env['FARMBOT_CONFIG'])
        config.issue_prefix = issue_prefix
        config.repos['Farm-Client'] = 'https://github.com/Kuaiwa-Network/Farm-Client.git'
        args = parser().parse_args(['--db', str(self.db), 'verify-publication', '--item', item,
                                   '--token', token, '--repo', 'Farm-Client'])
        ledger = Ledger(self.db)
        self.addCleanup(ledger.close)
        current = ledger.issue(ledger.item(item)['issue_id'])
        current['identifier'] = f'{issue_prefix}-1'
        current['delegate_id'] = 'e5a8c16d-9f85-4123-acf5-94e41c3304d5'
        api = SimpleNamespace(app_user_id='e5a8c16d-9f85-4123-acf5-94e41c3304d5', fetch_issue=lambda _: current)
        def github(endpoint, **kwargs):
            if '/branches/' in endpoint:
                return None
            return {'full_name': 'Kuaiwa-Network/Farm-Client', 'private': True,
                    'owner': {'login': 'Kuaiwa-Network'}, 'permissions': {'push': True},
                    'html_url': 'https://github.com/Kuaiwa-Network/Farm-Client', 'default_branch': 'main'}
        return args, ledger, config, api, github

    def test_publication_check_returns_verified_destination_for_a_current_claim(self):
        from unittest.mock import patch
        from agent.__main__ import run
        args, ledger, config, api, github = self.publication_fixture()
        with patch('agent.__main__.load_config', return_value=config), patch('agent.publication.github_api', side_effect=github):
            result = run(args, ledger, lambda: api)
        self.assertEqual(result['repository'], 'Kuaiwa-Network/Farm-Client')
        self.assertEqual(result['branch'], 'farmbot/farm-1')
        self.assertEqual(ledger.item(args.item)['state'], 'running')

    def test_checkpoint_reconciles_verified_pr_that_linear_attached_before_registration(self):
        self.checkpoint_reconciles_late_pr('FARM')

    def test_test_workspace_checkpoint_reconciles_verified_late_pr(self):
        self.checkpoint_reconciles_late_pr('FBTEST')

    def test_test_workspace_publication_check_uses_configured_issue_prefix(self):
        from unittest.mock import patch
        from agent.__main__ import run
        args, ledger, config, api, github = self.publication_fixture('FBTEST')
        with patch('agent.__main__.load_config', return_value=config), patch('agent.publication.github_api', side_effect=github):
            result = run(args, ledger, lambda: api)
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(result['branch'], 'farmbot/fbtest-1')

    def checkpoint_reconciles_late_pr(self, issue_prefix):
        from unittest.mock import patch
        from agent.__main__ import run, parser
        args, ledger, config, api, github = self.publication_fixture(issue_prefix)
        from agent.worktrees import Worktrees
        from agent.config import Paths
        # GitHub returns canonical casing even when configuration uses lowercase.
        config.repos['Farm-Client'] = 'https://github.com/kuaiwa-network/farm-client.git'
        paths = Paths(config)
        trees = Worktrees(paths.repos, paths.worktrees, config.repos)
        url = 'https://github.com/Kuaiwa-Network/Farm-Client/pull/1330'
        raw = api.fetch_issue(None)
        raw['attachments'] = [url]
        ledger.observe_issue(raw)
        repo = github('repos/Kuaiwa-Network/Farm-Client')
        pr = {'html_url': url, 'state': 'open', 'draft': True,
              'head': {'ref': f'farmbot/{issue_prefix.lower()}-1', 'sha': trees.head(paths.worktrees / args.item / 'Farm-Client'), 'repo': repo},
              'base': {'ref': 'main', 'repo': repo}}
        def with_pr(endpoint, **kwargs):
            return pr if endpoint.endswith('/pulls/1330') else github(endpoint, **kwargs)
        cp = parser().parse_args(['--db', str(self.db), 'checkpoint', '--item', args.item, '--token', args.token,
                                  '--input', self.json_file('late.json', {'published_prs': [url]})])
        with patch('agent.__main__.load_config', return_value=config), patch('agent.publication.github_api', side_effect=with_pr):
            result = run(cp, ledger, lambda: api)
        self.assertEqual(result['state'], 'running')
        self.assertEqual(ledger.issue_context(args.item)['published_prs'], [url])

    def test_rejected_checkpoint_blocks_await_input_before_posting_question(self):
        item = self.seeded_item()
        token = self.run_cli('claim', '--item', item, '--worker-id', 'w')['token']
        self.run_cli('checkpoint', '--item', item, '--token', token,
                     '--input', self.json_file('invalid.json', {'handoff': {'facts': []}}), success=False)
        result = self.run_cli('await-input', '--item', item, '--token', token, '--question', 'Which behavior?', success=False)
        self.assertIn('checkpoint', result.stderr)
        self.assertEqual(self.calls(), [])

    def test_publication_check_rejects_lost_delegation_and_wrong_ledger(self):
        from unittest.mock import patch
        from agent.__main__ import run
        args, ledger, config, api, github = self.publication_fixture()
        api.fetch_issue(None)['delegate_id'] = '10000000-0000-4000-8000-000000000009'
        with patch('agent.__main__.load_config', return_value=config), patch('agent.publication.github_api', side_effect=github):
            with self.assertRaisesRegex((RuntimeError, ValueError), 'delegated'):
                run(args, ledger, lambda: api)
            config.local_root = self.root / 'another-host'
            with self.assertRaisesRegex((RuntimeError, ValueError), 'configured host ledger'):
                run(args, ledger, lambda: api)

    def test_publication_check_fences_cancellation_during_remote_verification(self):
        from unittest.mock import patch
        from agent.__main__ import run
        args, ledger, config, api, github = self.publication_fixture()
        def cancelled(endpoint, **kwargs):
            if '/branches/' in endpoint:
                ledger.cancel(args.item, 'closed during verification')
            return github(endpoint, **kwargs)
        with patch('agent.__main__.load_config', return_value=config), patch('agent.publication.github_api', side_effect=cancelled):
            with self.assertRaises((RuntimeError, ValueError)):
                run(args, ledger, lambda: api)

    def verification_fixture(self, skill="fix"):
        from agent.worktrees import Worktrees
        from test_worktrees import git
        origin = self.root / "origin"
        origin.mkdir()
        git("init", "-q", "-b", "main", ".", cwd=origin)
        (origin / "fix.cs").write_text("old")
        git("add", ".", cwd=origin)
        git("commit", "-qm", "baseline", cwd=origin)
        baseline = git("rev-parse", "HEAD", cwd=origin)
        local = self.root / "local"
        self.db = local / "agent" / "ledger.sqlite3"
        self.env["FARMBOT_CONFIG"] = self.json_file("config.json", {
            "client_id": "test", "client_secret": "test", "webhook_secret": "test",
            "local_root": str(local), "repos": {"Farm-Client": str(origin)}})
        item = self.seeded_item(skill=skill, target={**PIN, "commit_sha": baseline})
        trees = Worktrees(local / "repos", local / "worktrees", {"Farm-Client": str(origin)})
        path = trees.add("Farm-Client", item, "farmbot/fix")
        (path / "fix.cs").write_text("fixed")
        git("commit", "-qam", "fix", cwd=path)
        fixed = trees.head(path)
        token = self.run_cli("claim", "--item", item, "--worker-id", "w")["token"]
        return item, token, baseline, fixed, path

    def test_await_resource_can_select_committed_fix_without_changing_baseline(self):
        item, token, baseline, fixed, path = self.verification_fixture()
        result = self.run_cli("await-resource", "--item", item, "--token", token,
                              "--resource", "unity_slot", "--mode", "batch", "--commit", fixed)
        self.assertEqual(result["state"], "awaiting_resource")
        self.assertEqual(result["target"]["commit_sha"], baseline)
        reservation = self.run_cli("reservations")[0]
        self.assertEqual(reservation["commit_sha"], fixed)

    def test_explicit_commit_refuses_dirty_worktree_and_stale_claim(self):
        item, token, baseline, fixed, path = self.verification_fixture()
        (path / "fix.cs").write_text("not committed")
        args = ("await-resource", "--item", item, "--token", token,
                "--resource", "unity_slot", "--mode", "interactive", "--commit", fixed)
        self.assertIn("clean", self.run_cli(*args, success=False).stderr)
        (path / "fix.cs").write_text("fixed")
        self.run_cli("cancel", "--item", item, "--reason", "stop")
        self.run_cli(*args, success=False)
        self.assertEqual(self.run_cli("reservations"), [])

    def test_chat_cannot_select_a_fix_commit(self):
        item, token, baseline, fixed, path = self.verification_fixture(skill="chat")
        self.run_cli("await-resource", "--item", item, "--token", token,
                     "--resource", "unity_slot", "--mode", "batch", "--commit", fixed, success=False)
        self.assertEqual(self.run_cli("reservations"), [])

    def test_commit_validation_cannot_queue_after_a_concurrent_cancel(self):
        from unittest.mock import patch
        from agent.__main__ import parser, run
        from agent.config import load_config
        from agent.ledger import Ledger, LedgerError
        item, token, baseline, fixed, path = self.verification_fixture()
        config = load_config(self.env["FARMBOT_CONFIG"])
        args = parser().parse_args(["--db", str(self.db), "await-resource", "--item", item,
                                   "--token", token, "--resource", "unity_slot", "--mode", "batch", "--commit", fixed])
        ledger = Ledger(self.db)
        self.addCleanup(ledger.close)
        def cancel_during_git(*_):
            other = Ledger(self.db)
            try:
                other.cancel(item, "issue closed during checkout validation")
            finally:
                other.close()
            return fixed
        with patch("agent.__main__.load_config", return_value=config), \
                patch("agent.worktrees.Worktrees.verification_commit", side_effect=cancel_during_git):
            with self.assertRaises(LedgerError):
                run(args, ledger, lambda: None)
        self.assertEqual(ledger.reservations(), [])
        self.assertEqual(ledger.item(item)["state"], "cancelled")

    def test_commit_validation_rejects_a_different_configured_ledger(self):
        item, token, baseline, fixed, path = self.verification_fixture()
        config_path = Path(self.env["FARMBOT_CONFIG"])
        config = json.loads(config_path.read_text())
        config["local_root"] = str(self.root / "another-host")
        config_path.write_text(json.dumps(config))
        result = self.run_cli("await-resource", "--item", item, "--token", token,
                              "--resource", "unity_slot", "--mode", "batch", "--commit", fixed, success=False)
        self.assertIn("configured host ledger", result.stderr)
        self.assertEqual(self.run_cli("reservations"), [])

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

    def seeded_item(self, issue_id=ISSUE, session="session-1", skill="fix", target=None):
        """Create a work item the way the receiver would, then return its id.

        `target` is the pin the receiver snapshots onto the item. It defaults to None because most tests
        here never ask for a resource, and `await_resource` is the one command that refuses an item without
        one: a slot cannot be switched to a commit that does not exist.
        """
        from agent.ledger import Ledger
        ledger = Ledger(self.db)
        ledger.observe_issue(issue(id=issue_id, labels=["Bug"]))
        ledger.ensure_session(session, issue_id, delegation=True)
        item = ledger.create_work_item(issue_id=issue_id, session_id=session, skill=skill, target=target)
        ledger.close()
        return item["id"]

    def test_status_exposes_pending_cleanup_and_status_read_failures(self):
        from agent.ledger import Ledger
        item = self.seeded_item()
        ledger = Ledger(self.db)
        try:
            ledger.cancel(item, "closed")
            ledger.record_cleanup(item, {}, error="disk full")
            ledger.finish_status_check(ISSUE, 60, "status API unavailable")
        finally:
            ledger.close()
        view = self.run_cli("status")
        self.assertEqual(view["cleanup_pending"][0]["item_id"], item)
        self.assertEqual(view["cleanup_pending"][0]["error"], "disk full")
        self.assertEqual(view["issue_status_errors"][0]["error"], "status API unavailable")

    def granted_item(self, mode="interactive", issue_id=ISSUE):
        """Leave an item in exactly the state the pool leaves behind for a fresh worker: a granted
        reservation, a slot in that mode's busy state, and the raw token on disk at 0600.

        The two `set_slot_state` calls stand in for the pool, because no pool thread runs in this file:
        `SlotPool.park_idle` is what returns a `switching` slot to the pool in production and
        `SlotPool.switch` is what writes the busy state. Without the first, a second call here gets None
        out of `Ledger.acquire` — `release` left the slot `switching`, which is not in FREE_SLOT_STATES.
        """
        from agent.ledger import Ledger
        session = f"session-{issue_id[-1]}"
        item = self.seeded_item(issue_id=issue_id, session=session, target=PIN)
        claim_token = self.run_cli("claim", "--item", item, "--worker-id", "w")["token"]
        self.run_cli("await-resource", "--item", item, "--token", claim_token,
                     "--resource", "unity_slot", "--mode", mode)
        ledger = Ledger(self.db)
        try:
            ledger.ensure_slot(SLOT, kind="unity_slot", host=HOST, folder=str(self.root / "slot-1"))
            if ledger.slot(SLOT)["state"] not in Ledger.FREE_SLOT_STATES:
                ledger.set_slot_state(SLOT, "idle_closed")          # SlotPool.park_idle
            granted = ledger.acquire("unity_slot", owner="pool", host=HOST)
            self.assertIsNotNone(granted, "the pool found no free slot to grant")
            ledger.set_slot_state(SLOT, f"{mode}_busy")              # SlotPool.switch
        finally:
            ledger.close()
        state_dir = self.root / "state" / item
        state_dir.mkdir(parents=True, exist_ok=True)
        token_file = state_dir / "reservation.token"
        token_file.write_text(granted["token"], encoding="utf-8")
        token_file.chmod(0o600)
        return item, token_file

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
        self.assertTrue(any(c["method"] == "needs_more_info" and c["issue_id"] == ISSUE for c in self.calls()))

    def test_resume_work_refreshes_delegation_and_hands_message_to_original_fix(self):
        from agent.ledger import Ledger
        app = "e5a8c16d-9f85-4123-acf5-94e41c3304d5"
        fix = self.seeded_item()
        self.run_cli("cancel", "--item", fix, "--reason", "stopped")
        chat = self.seeded_item(session="mention", skill="chat")
        ledger = Ledger(self.db)
        try:
            ledger.push_inbox(chat, "请接着做，初始为零，只计算主动解锁")
            message = ledger.connection.execute("select id from inbox where item_id=?", (chat,)).fetchone()[0]
        finally:
            ledger.close()
        token = self.run_cli("claim", "--item", chat, "--worker-id", "chat")["token"]
        self.run_cli("resume-work", "--item", chat, "--token", token, "--message-id", str(message), success=False)
        (self.stub / "issue.json").write_text(json.dumps(issue(labels=["Bug"], delegate_id=app)))
        resumed = self.run_cli("resume-work", "--item", chat, "--token", token, "--message-id", str(message))
        self.assertNotEqual(resumed["id"], fix)
        self.assertEqual((resumed["predecessor_id"], resumed["state"]), (fix, "queued"))
        self.assertEqual(self.calls()[-1]["content"]["type"], "response")

    def test_retry_checks_current_status_and_delegation_before_successor(self):
        fix = self.seeded_item()
        self.run_cli("cancel", "--item", fix, "--reason", "stopped")
        (self.stub / "issue.json").write_text(json.dumps(issue(status_type="completed")))
        self.run_cli("retry", "--item", fix, "--reason", "restart", success=False)
        (self.stub / "issue.json").write_text(json.dumps(issue(delegate_id=None)))
        self.run_cli("retry", "--item", fix, "--reason", "restart", success=False)
        (self.stub / "issue.json").write_text(json.dumps(issue(delegate_id="e5a8c16d-9f85-4123-acf5-94e41c3304d5")))
        resumed = self.run_cli("retry", "--item", fix, "--reason", "restart")
        self.assertNotEqual(resumed["id"], fix)
        self.assertEqual(resumed["predecessor_id"], fix)

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

    def test_a_second_item_on_one_unchanged_issue_finishes_without_posting_twice(self):
        """The live rehearsal's Finding 2, end to end through the CLI a worker actually drives: the second
        item's prepare/post/finish must reach a terminal state and leave Linear with one comment."""
        first = self.seeded_item()
        token = self.run_cli("claim", "--item", first, "--worker-id", "w")["token"]
        body = self.root / "blocker.md"
        body.write_text("缺少客户端导出产物，需要发布决策。", encoding="utf-8")
        action = self.run_cli("prepare-comment", "--item", first, "--token", token, "--kind", "blocker",
                              "--body-file", str(body))
        self.run_cli("post-comment", "--item", first, "--token", token, "--action-id", action["action_id"])
        self.run_cli("finish", "--item", first, "--token", token, "--outcome", "blocked", "--input",
                     self.json_file("first.json", {"summary": "阻塞", "comment_action_id": action["action_id"]}))
        second = self.seeded_item()
        self.assertNotEqual(second, first)
        second_token = self.run_cli("claim", "--item", second, "--worker-id", "w2")["token"]
        again = self.run_cli("prepare-comment", "--item", second, "--token", second_token, "--kind", "blocker",
                             "--body-file", str(body))
        self.assertEqual(again["action_id"], action["action_id"])
        self.assertTrue(again["deduplicated"])
        posted = self.run_cli("post-comment", "--item", second, "--token", second_token,
                              "--action-id", again["action_id"])
        self.assertEqual(posted["remote_id"], "stub-comment-1")
        finished = self.run_cli("finish", "--item", second, "--token", second_token, "--outcome", "blocked",
                                "--input", self.json_file("second.json", {"summary": "同一结论",
                                                                          "comment_action_id": again["action_id"]}))
        self.assertEqual(finished["state"], "blocked")
        self.assertEqual([c["method"] for c in self.calls()].count("create_comment"), 1)
        # An enqueued item's only reporting surface is the issue comment, and it posted none. The operator
        # command that is already run has to be the one that says so.
        borrowed = self.run_cli("status")["borrowed_comments"]
        self.assertEqual([row["item_id"] for row in borrowed], [second])
        self.assertEqual(borrowed[0]["prepared_by"], first)

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

    def test_a_worker_requests_a_slot_and_the_request_is_queued(self):
        item = self.seeded_item(target=PIN)
        token = self.run_cli("claim", "--item", item, "--worker-id", "w")["token"]
        view = self.run_cli("await-resource", "--item", item, "--token", token,
                            "--resource", "unity_slot", "--mode", "batch")
        self.assertEqual((view["state"], view["needs_resource"]), ("awaiting_resource", "unity_slot:batch"))
        self.assertEqual(self.run_cli("reservations")[0]["mode"], "batch")
        # Carried from the test this replaced: a parked item is not claimable, and the refusal is clean.
        self.run_cli("claim", "--item", item, "--worker-id", "w2", success=False)

    def test_an_unpinned_item_is_told_why_it_cannot_have_a_slot(self):
        item = self.seeded_item()
        token = self.run_cli("claim", "--item", item, "--worker-id", "w")["token"]
        process = self.run_cli("await-resource", "--item", item, "--token", token,
                               "--resource", "unity_slot", "--mode", "batch", success=False)
        self.assertIn("pinned commit", process.stderr)
        self.assertEqual(process.stdout, "")

    def test_a_worker_releases_its_own_slot_and_an_unclean_one_is_held(self):
        item, token_file = self.granted_item(mode="interactive")
        view = self.run_cli("release-resource", "--item", item, "--token-file", str(token_file),
                            "--outcome", "quiescent")
        self.assertEqual(view["state"], "released")
        self.assertEqual(self.run_cli("slots")[0]["state"], "switching")
        # granted_item parks the slot first, because no pool thread runs in this file and Ledger.acquire
        # only grants a slot in FREE_SLOT_STATES — release() left it 'switching'.
        other, other_token = self.granted_item(mode="interactive", issue_id=OTHER)
        self.run_cli("release-resource", "--item", other, "--token-file", str(other_token), "--outcome", "unclean")
        self.assertEqual(self.run_cli("slots")[0]["state"], "held")
        self.run_cli("recover-slot", "--slot", SLOT, "--reason", "operator closed Unity")
        self.assertEqual(self.run_cli("slots")[0]["state"], "idle_closed")

    def test_neither_outcome_acts_on_a_reservation_the_caller_cannot_prove_it_holds(self):
        """The test above passes the right token to both outcomes, so it would pass against a `hold` that
        checked nothing. `unclean` is the consequential outcome — it takes the host's only slot out of the
        pool until an operator runs `recover-slot` — so it is the one that must not be open to any string.
        The fresh worker's own claim token is the realistic wrong one: the skill tells a worker to carry it
        on every other call, and `release` already refuses it because the ledger stores a different hash.
        """
        from agent.ledger import Ledger
        item, token_file = self.granted_item(mode="interactive")
        ledger = Ledger(self.db)
        ledger.resume(item, "the pool granted the slot")  # SlotPool.hand_over, so a fresh worker may claim
        ledger.close()
        claim_token = self.run_cli("claim", "--item", item, "--worker-id", "fresh")["token"]
        claim_file = self.root / "claim.token"
        claim_file.write_text(claim_token, encoding="utf-8")
        nonsense = self.root / "nonsense.token"
        nonsense.write_text("res_not-the-one-the-pool-wrote", encoding="utf-8")
        for label, wrong in (("the claim token", claim_file), ("a made-up token", nonsense)):
            for outcome in ("quiescent", "unclean"):
                with self.subTest(token=label, outcome=outcome):
                    process = self.run_cli("release-resource", "--item", item, "--token-file", str(wrong),
                                           "--outcome", outcome, success=False)
                    self.assertIn("reservation token required", process.stderr)
                    self.assertEqual(process.stdout, "")
                    # Nothing moved: the slot is neither back in the pool nor out of it.
                    self.assertEqual(self.run_cli("slots")[0]["state"], "interactive_busy")
                    self.assertEqual(self.run_cli("reservations")[0]["state"], "active")
        # The token the pool actually wrote still works, so the refusals above are about the token and not
        # about the verb having been broken.
        self.assertEqual(self.run_cli("release-resource", "--item", item, "--token-file", str(token_file),
                                      "--outcome", "unclean")["state"], "active")
        self.assertEqual(self.run_cli("slots")[0]["state"], "held")

    def test_release_resource_refuses_an_item_that_holds_nothing(self):
        item = self.seeded_item(target=PIN)
        token = self.run_cli("claim", "--item", item, "--worker-id", "w")["token"]
        path = self.root / "t"
        path.write_text(token, encoding="utf-8")
        process = self.run_cli("release-resource", "--item", item, "--token-file", str(path),
                               "--outcome", "quiescent", success=False)
        self.assertIn("holds no resource", process.stderr)

    def test_a_no_change_delivery_completes_the_session_as_no_change(self):
        item = self.seeded_item()
        token = self.run_cli("claim", "--item", item, "--worker-id", "w")["token"]
        body = self.root / "d.md"
        body.write_text("主干已修复。", encoding="utf-8")
        action = self.run_cli("prepare-comment", "--item", item, "--token", token, "--kind", "delivery",
                              "--body-file", str(body))
        self.run_cli("post-comment", "--item", item, "--token", token, "--action-id", action["action_id"])
        self.run_cli("finish", "--item", item, "--token", token, "--outcome", "delivered", "--input",
                     self.json_file("nc.json", {"summary": "已确认主干修复", "comment_action_id": action["action_id"],
                                                "verification": "对比源表与已提交配置",
                                                "no_change": "主干提交已修正", "prs": []}))
        final = self.calls()[-1]
        self.assertEqual(final["content"]["type"], "response")
        self.assertIn("无需改动", final["content"]["body"])
