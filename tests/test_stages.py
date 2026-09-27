"""Repository scope per worker attempt, from the skill manifest (spec §9.4, §9.5)."""
import tempfile
import unittest
from pathlib import Path

from agent.skills import load_skills
from agent.stages import current_root, write_repositories
from test_skills import staged_skill, write_skill

ROOT = Path(__file__).resolve().parents[1]
SKILLS = load_skills(ROOT / "skills")


class StageTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.feature = staged_skill(self.root)

    def test_fix_investigates_unrooted_and_then_writes_only_its_root(self):
        fix = SKILLS["fix"]
        self.assertIsNone(current_root(None, fix))
        self.assertEqual(write_repositories({"skill": "fix", "root_repo": None}, fix), ())
        for repo in fix.writes:
            with self.subTest(repo=repo):
                self.assertEqual(write_repositories({"skill": "fix", "root_repo": repo}, fix), (repo,))
        with self.assertRaisesRegex(ValueError, "invalid fix repository root: farm-server"):
            write_repositories({"skill": "fix", "root_repo": "farm-server"}, fix)

    def test_an_unstaged_skill_writes_its_whole_scope(self):
        write_skill(self.root, "wide", writes=["farmgui", "Farm-Client"])
        wide = load_skills(self.root)["wide"]
        self.assertEqual(write_repositories({"skill": "wide", "root_repo": None}, wide), ("farmgui", "Farm-Client"))
        self.assertEqual(write_repositories({"skill": "chat", "root_repo": None}, SKILLS["chat"]), ())

    def test_a_staged_skill_starts_at_its_initial_root_and_then_writes_only_its_root(self):
        self.assertEqual(current_root(None, self.feature), "Farm-Contract")
        self.assertEqual(current_root("farm-hive", self.feature), "farm-hive")
        self.assertEqual(write_repositories({"skill": "feature", "root_repo": None}, self.feature), ("Farm-Contract",))
        self.assertEqual(write_repositories({"skill": "feature", "root_repo": "farm-hive"}, self.feature), ("farm-hive",))
        with self.assertRaisesRegex(ValueError, "invalid feature repository root: farmgui"):
            write_repositories({"skill": "feature", "root_repo": "farmgui"}, self.feature)
