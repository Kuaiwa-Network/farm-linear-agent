import json
import unittest
from pathlib import Path

from agent.dispatch import dispatch_message

ROOT = Path(__file__).resolve().parents[1]


def payload_of(message):
    return json.loads(message.split("\n\n", 1)[1])


class DispatchTests(unittest.TestCase):
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
