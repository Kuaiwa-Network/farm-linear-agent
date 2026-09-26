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


class RunReportInstructionTests(unittest.TestCase):
    """Run reports are FarmBot's private evidence. Told to commit one under FarmBot's own checkout, which is
    not a worker writable root, FARM-1282's workers committed reports/<date>-<identifier>/report.md into
    Farm-Client and Farm-Contract instead."""

    def worker_reads(self):
        """What a worker is told to read: its skill, every reference and the operating contract."""
        paths = [*(ROOT / "skills").glob("*/SKILL.md"), *(ROOT / "references").glob("*.md"),
                 ROOT / "docs" / "operating-contract.md"]
        return {path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8") for path in sorted(paths)}

    def test_no_worker_instruction_names_a_report_path_inside_a_repository(self):
        for name, text in self.worker_reads().items():
            with self.subTest(name=name):
                self.assertNotIn("reports/<", text)
                self.assertNotIn("repo_root>/reports", text)

    def test_the_fix_skill_and_report_format_write_the_report_in_state_dir(self):
        texts = self.worker_reads()
        for name in ("skills/fix/SKILL.md", "references/evidence-format.md"):
            with self.subTest(name=name):
                self.assertIn("`STATE_DIR/report.md`", texts[name])

    def test_a_later_attempt_of_the_same_job_writes_a_new_report(self):
        """Retries and resumes keep the job's id, so they share its state_dir (Launcher.state_dir): a fixed
        report.md would let a later attempt overwrite the evidence of an earlier one."""
        texts = self.worker_reads()
        for name in ("skills/fix/SKILL.md", "references/evidence-format.md"):
            with self.subTest(name=name):
                self.assertIn("`STATE_DIR/report-2.md`", texts[name])

    def test_candidate_lessons_go_to_shared_memory_not_a_pull_request(self):
        text = self.worker_reads()["references/evidence-format.md"]
        self.assertNotIn("promotion is a PR", text)
        self.assertIn("references/memory.md", text)


class PeopleInstructionTests(unittest.TestCase):
    """Fix workers name deciders and mention people only from issue-context's Linear users (spec §5.1, §5.3, D17)."""

    def test_the_fix_skill_names_deciders_from_authors_and_mentions_the_owner_and_creator(self):
        text = (ROOT / "skills" / "fix" / "SKILL.md").read_text(encoding="utf-8")
        for phrase in ("`[DECIDED:<Linear user name>@<date>]`", "`author.name`", "the `displayName` handle",
                       "the date is the calendar date of its `created_at` in UTC+8", "`created_at` itself is UTC",
                       "the comment's own `url`", "Only when the comment has no `url`", "`owner.person.url`",
                       "`creator.url`", "Never invent a name", "`默认·3 个工作日未异议`",
                       # A FarmBot instance's comment is known by its marker, not by what Linear says its author is.
                       "ends with a `[farmbot:…]` marker line", "whatever its `author` says", "it is never a human's ruling",
                       # An unattributable answer is asked for once more through the one channel A1 has, then given up.
                       "Ask for it once more", "with `await-input`, saying whose answer you need",
                       "If that answer has no author either, stop asking"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)
        # Both ruling sources, a comment and a session reply, are dated in UTC+8; neither falls back to the UTC date.
        self.assertEqual(text.count("calendar date of its `created_at` in UTC+8"), 2)
        self.assertNotIn("date part of its `created_at`", text)

    def test_the_comments_that_ask_a_human_to_act_mention_the_owner(self):
        text = (ROOT / "references" / "comment-templates.md").read_text(encoding="utf-8")
        for kind in ("blocker", "delivery"):
            with self.subTest(kind=kind):
                section = text.split(f"\n## {kind}\n", 1)[1].split("\n## ", 1)[0]
                self.assertIn("<owner.person.url>", section)


class SkillRegistryTests(unittest.TestCase):
    def test_repository_skills_load_with_expected_authority(self):
        skills = load_skills(ROOT / "skills")
        self.assertEqual(set(skills), {"chat", "fix"})
        self.assertEqual(skills["fix"].trigger, ("delegation",))
        self.assertIn("Farm-Client", skills["fix"].writes)
        self.assertEqual(skills["fix"].resources, ("unity_slot",))
        self.assertEqual(skills["chat"].writes, ())
        self.assertTrue(skills["fix"].skill_md.is_file())
        self.assertEqual(skills["fix"].mcp, ("kw_ops",))
        self.assertEqual(skills["chat"].mcp, ("kw_ops:read",))

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

    def test_an_unknown_tool_grant_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad"
            bad.mkdir()
            (bad / "SKILL.md").write_text("# bad", encoding="utf-8")
            (bad / "skill.json").write_text(json.dumps({"name": "bad", "trigger": ["mention"], "intents": [], "writes": [],
                                                        "resources": [], "gates": [], "mcp": ["kw_ops:write"],
                                                        "budget": {"lease_seconds": 1, "max_hours": 1, "renew_minutes": 1}}), encoding="utf-8")
            with self.assertRaisesRegex(SkillError, "unknown mcp grant"):
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
