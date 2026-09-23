import json
import re
import shlex
import tempfile
import unittest
from pathlib import Path

from agent.skills import SkillError, load_skills

ROOT = Path(__file__).resolve().parents[1]


class WorkerCliReferenceTests(unittest.TestCase):
    def reference(self):
        path = ROOT / "references" / "worker-cli.md"
        self.assertTrue(path.is_file(), "workers need a copyable CLI and checkpoint reference")
        return path.read_text(encoding="utf-8")

    def test_documented_worker_commands_parse_without_unknown_flags(self):
        from agent.__main__ import parser

        commands = [line for block in re.findall(r"```bash\n(.*?)```", self.reference(), re.DOTALL)
                    for line in block.splitlines() if line.startswith("python3 -m agent ")]
        self.assertTrue(commands, "the reference must provide executable command examples")
        for command in commands:
            with self.subTest(command=command):
                argv = shlex.split(command)[3:]
                if "--help" in argv:
                    continue
                args = parser().parse_args(argv)
                self.assertEqual(args.item, "ITEM_ID")

    def test_documented_checkpoint_is_accepted_and_available_to_the_next_worker(self):
        from agent.ledger import Ledger
        from tests.test_ledger import ISSUE, SESSION, issue

        examples = re.findall(r"```json\n(.*?)```", self.reference(), re.DOTALL)
        self.assertTrue(examples, "the reference must provide a complete checkpoint example")
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp) / "ledger.sqlite3")
            try:
                ledger.observe_issue(issue())
                ledger.ensure_session(SESSION, ISSUE, delegation=True)
                item = ledger.create_work_item(issue_id=ISSUE, session_id=SESSION, skill="fix")
                claimed = ledger.claim(item["id"], worker_id="reference-test")
                for example in examples:
                    with self.subTest(example=example):
                        checkpoint = json.loads(example)
                        ledger.checkpoint(item["id"], claimed["token"], checkpoint)
                        saved = ledger.issue_context(item["id"])["handoff"]["content"]
                        self.assertEqual(saved, checkpoint["handoff"])
            finally:
                ledger.close()


class CommentTemplateTests(unittest.TestCase):
    """Workers fill <bot_name> from their launch message. Rendered for FarmBot, the templates must read
    exactly as production's did before the name became configurable."""

    def rendered(self, name):
        text = (ROOT / "references" / "comment-templates.md").read_text(encoding="utf-8")
        return text.replace("<bot_name>", name)

    def test_templates_carry_no_hard_coded_bot_name(self):
        raw = (ROOT / "references" / "comment-templates.md").read_text(encoding="utf-8")
        self.assertNotIn("FarmBot", raw)
        self.assertIn("`bot_name`", raw)

    def test_farmbot_rendering_matches_the_production_comments(self):
        text = self.rendered("FarmBot")
        for kind, line in (("started", "👀 FarmBot 已开始处理：正在复现与定位问题，验证结果和草稿 PR 会补充在本 issue。"),
                           ("blocker", "FarmBot 暂停处理。"),
                           ("delivery", "FarmBot 已提交修复（草稿 PR，待 review）：")):
            with self.subTest(kind=kind):
                self.assertIn(f"\n## {kind}\n{line}\n", text)
        self.assertIn("\nFarmBot 已确认无需改动：\n", text)

    def test_a_named_instance_starts_as_itself(self):
        text = self.rendered("TestBot")
        self.assertIn("\n## started\n👀 TestBot 已开始处理：正在复现与定位问题，验证结果和草稿 PR 会补充在本 issue。\n", text)
        self.assertNotIn("FarmBot", text)


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
