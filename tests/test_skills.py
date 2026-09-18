import json
import tempfile
import unittest
from pathlib import Path

from agent.skills import SkillError, load_skills

ROOT = Path(__file__).resolve().parents[1]


class SkillRegistryTests(unittest.TestCase):
    def test_repository_skills_load_with_expected_authority(self):
        skills = load_skills(ROOT / "skills")
        self.assertEqual(set(skills), {"chat", "fix"})
        self.assertEqual(skills["fix"].trigger, ("delegation",))
        self.assertIn("Farm-Client", skills["fix"].writes)
        self.assertEqual(skills["fix"].resources, ("unity_slot",))
        self.assertEqual(skills["chat"].writes, ())
        self.assertTrue(skills["fix"].skill_md.is_file())

    def test_invalid_manifest_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad"
            bad.mkdir()
            (bad / "SKILL.md").write_text("# bad", encoding="utf-8")
            (bad / "skill.json").write_text(json.dumps({"name": "bad", "trigger": ["telepathy"], "intents": [], "writes": [],
                                                        "resources": [], "gates": [], "mcp": [],
                                                        "budget": {"lease_seconds": 1, "max_hours": 1, "renew_minutes": 1}}), encoding="utf-8")
            with self.assertRaises(SkillError):
                load_skills(Path(tmp))

    def test_manifest_name_must_match_directory_and_skill_md_must_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "alpha"
            d.mkdir()
            (d / "skill.json").write_text(json.dumps({"name": "beta", "trigger": ["mention"], "intents": [], "writes": [],
                                                      "resources": [], "gates": [], "mcp": [],
                                                      "budget": {"lease_seconds": 1, "max_hours": 1, "renew_minutes": 1}}), encoding="utf-8")
            with self.assertRaises(SkillError):
                load_skills(Path(tmp))
