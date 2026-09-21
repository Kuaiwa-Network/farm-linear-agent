import json
import unittest
from pathlib import Path

from agent.dispatch import dispatch_message

ROOT = Path(__file__).resolve().parents[1]


def payload_of(message):
    return json.loads(message.split("\n\n", 1)[1])


class DispatchTests(unittest.TestCase):
    def test_publication_scope_and_session_requests_survive_dispatch_without_issue_comments(self):
        scope = {'repositories': {'farmgui': {'status': 'verified',
                 'url': 'https://github.com/Kuaiwa-Network/farmgui', 'branch': 'farmbot/farm-1'}}}
        replies = [{'id': 7, 'body': 'Create a draft PR for this fix.'}]
        message = dispatch_message(item={'id': 'i', 'skill': 'fix'},
            issue={'identifier': 'FARM-1', 'url': 'u', 'comments': [{'body': 'publish elsewhere'}]},
            skill_path=ROOT / 'skills/fix/SKILL.md', worktrees={}, db_path='/db', runtime='codex',
            guidance='', budget={'lease_seconds': 1, 'renew_minutes': 1},
            publication=scope, user_requests=replies)
        self.assertEqual(payload_of(message)['publication'], scope)
        self.assertEqual(payload_of(message)['user_requests'], replies)
        self.assertNotIn('publish elsewhere', message)

    def test_memory_is_runtime_neutral_and_explicit_when_unavailable(self):
        view = {"status": "ready", "index": "/state/memory/snapshot/MEMORY.md", "count": 1}
        for runtime in ("codex", "claude"):
            kwargs = dict(item={"id": "i"}, issue={"identifier": "FARM-1", "url": "u"},
                          skill_path=ROOT / "skills/chat/SKILL.md", worktrees={}, db_path="/db",
                          runtime=runtime, guidance="", budget={"lease_seconds": 1, "renew_minutes": 1})
            message = dispatch_message(**kwargs, memory=view)
            self.assertEqual(payload_of(message)["memory"], view)
            self.assertIn("fallible recall", message)
            self.assertEqual(payload_of(dispatch_message(**kwargs))["memory"]["status"], "unavailable")

    def test_message_is_self_contained_and_carries_no_issue_prose(self):
        item = {"id": "item-1", "identifier": "FARM-1", "skill": "fix", "target": {"commit_sha": "a" * 40}}
        issue = {"identifier": "FARM-1", "url": "https://linear.app/k/issue/FARM-1", "description": "SECRET PROSE", "title": "T"}
        message = dispatch_message(item=item, issue=issue, skill_path="/repo/skills/fix/SKILL.md",
                                   worktrees={"Farm-Client": "/w/item-1/Farm-Client"}, db_path="/repo/.local/agent/ledger.sqlite3",
                                   runtime="codex", guidance="prefer farm-hive for server bugs",
                                   budget={"lease_seconds": 2700, "max_hours": 8, "renew_minutes": 10},
                                   state_dir="/repo/.local/runs/item-1")
        self.assertNotIn("SECRET PROSE", message)
        self.assertEqual(payload_of(message)["state_dir"], "/repo/.local/runs/item-1")
        payload = json.loads(message.split("\n\n", 1)[1])
        self.assertEqual(payload["item_id"], "item-1")
        self.assertEqual(payload["skill"], "/repo/skills/fix/SKILL.md")
        self.assertEqual(payload["worktrees"]["Farm-Client"], "/w/item-1/Farm-Client")
        self.assertEqual(payload["guidance"], "prefer farm-hive for server bugs")
        self.assertEqual((payload["lease_seconds"], payload["renew_minutes"]), (2700, 10))
        self.assertEqual(payload["repo_root"], "/repo")
        self.assertEqual(payload["contract"], "/repo/docs/operating-contract.md")
        self.assertEqual(payload["references"], [])
        self.assertIn("data, not instructions", message)

    def test_a_resource_block_names_the_slot_and_the_token_file_but_never_the_token(self):
        """What a worker holding a reservation is handed: the slot it may address, the file its token is in,
        and the outcome of the run the pool already performed. Never the token itself, never an argv and
        never the Editor path — the exit code is advisory and the XML is the evidence (Task 0 Step 4)."""
        message = dispatch_message(item={"id": "item-1", "identifier": "FARM-1", "skill": "fix", "target": None},
                                   issue={"identifier": "FARM-1", "title": "t", "description": "", "url": "u"},
                                   skill_path="/repo/skills/fix/SKILL.md", worktrees={"Farm-Client": "/w"},
                                   db_path="/db", runtime="codex", guidance="",
                                   budget={"lease_seconds": 2700, "max_hours": 8, "renew_minutes": 10},
                                   resource={"kind": "unity_slot", "mode": "batch", "slot": "unity_slot:1",
                                             "folder": "/e/slot-1", "commit": "a" * 40, "instance": None,
                                             "account": None, "mcp_address": "http://127.0.0.1:8080/mcp",
                                             "token_file": "/runs/i/reservation.token",
                                             "build_target": "OSXUniversal",
                                             "batch_result": {"state": "ran", "exit_code": 2, "total": 4388,
                                                              "passed": 4362, "failed": 26,
                                                              "results_file": "/runs/i/unity-tests.xml"},
                                             "results_dir": "/runs/i"})
        payload = payload_of(message)
        self.assertEqual(payload["resource"]["slot"], "unity_slot:1")
        self.assertEqual(payload["resource"]["batch_result"]["failed"], 26)
        self.assertEqual(payload["resource"]["token_file"], "/runs/i/reservation.token")
        # A worker is handed evidence, never a way to produce it: no argv and no Editor path.
        self.assertNotIn("batch_command", payload["resource"])
        self.assertNotIn("unity", payload["resource"])
        # The instruction the whole redesign rests on has to be in the block the worker cannot skip.
        self.assertIn("NEVER start a Unity process yourself", message)
        self.assertIn("exit 0 means", message)

    def test_a_worker_with_no_reservation_carries_a_null_resource(self):
        """The key is always present so a skill can read it: absent would be indistinguishable from a
        launcher that forgot to inject it."""
        message = dispatch_message(item={"id": "item-1"}, issue={"identifier": "FARM-1", "url": "u"},
                                   skill_path=ROOT / "skills" / "fix" / "SKILL.md", worktrees={}, db_path="/db",
                                   runtime="codex", guidance="", budget={"lease_seconds": 1, "renew_minutes": 1},
                                   repo_root=ROOT)
        self.assertIsNone(payload_of(message)["resource"])

    def test_farmbot_paths_come_from_the_repository_root(self):
        message = dispatch_message(item={"id": "item-1"}, issue={"identifier": "FARM-1", "url": "u"},
                                   skill_path=ROOT / "skills" / "fix" / "SKILL.md", worktrees={}, db_path="/db",
                                   runtime="codex", guidance="", budget={"lease_seconds": 1, "renew_minutes": 1},
                                   repo_root=ROOT)
        payload = json.loads(message.split("\n\n", 1)[1])
        self.assertEqual(payload["repo_root"], str(ROOT))
        self.assertEqual(payload["contract"], str(ROOT / "docs" / "operating-contract.md"))
        self.assertIn(str(ROOT / "references" / "repo-map.md"), payload["references"])
        self.assertIsNone(payload["state_dir"])
