import json
import re
import shlex
import tempfile
import unittest
from pathlib import Path

from agent.skills import SkillError, enabled_skills, load_skills

ROOT = Path(__file__).resolve().parents[1]


def write_skill(root, name, **manifest):
    """One skill directory under root with a valid manifest; `manifest` overrides its keys."""
    directory = Path(root) / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(f"# {name}", encoding="utf-8")
    base = {"name": name, "trigger": ["mention"], "intents": [], "writes": [], "resources": [], "gates": [],
            "mcp": [], "budget": {"lease_seconds": 1, "max_hours": 1, "renew_minutes": 1}}
    (directory / "skill.json").write_text(json.dumps({**base, **manifest}), encoding="utf-8")
    return directory


def staged_skill(root, name="feature"):
    """A fixture staged skill with an initial root, shaped like the proposed feature manifest (spec §9.5)."""
    write_skill(root, name, trigger=["delegation"], writes=["Farm-Contract", "common", "farm-hive", "Farm-Client"],
                resources=["unity_slot"], staged=True, initial_root="Farm-Contract", reads=["farmgui"],
                budget={"lease_seconds": 2700, "max_hours": 10, "renew_minutes": 10})
    return load_skills(root)[name]


# The Phase B `feature` manifest (plan, Shared Interfaces) without its name, which Phase B's opt-in fixture reproduces.
FEATURE_SHAPE = dict(trigger=["delegation"], intents=["label:Bot/Code"], writes=["Farm-Contract", "common", "farm-hive"],
                     initial_root="Farm-Contract", staged=True, reads=["Farm-Contract", "Farm-Client", "farmgui"],
                     resources=[], gates=["answers", "config_ready", "closing", "pr_review"], mcp=[],
                     budget={"lease_seconds": 2700, "max_hours": 10, "renew_minutes": 10}, opt_in=True, exclusive=True)


def opt_in_skill(root, name="feature", **overrides):
    """Phase B's fixture skill: the `feature` manifest of the Phase B plan's Shared Interfaces, staged from its
    initial root Farm-Contract, writing Farm-Contract, common and farm-hive, reading Farm-Contract, Farm-Client and
    farmgui, opt-in and exclusive. `overrides` replaces any of its keys, such as `exclusive=False`; each name is
    written once per root.

    Loaded beside this checkout's skills it is not enabled. A test that runs it names it in `enabled_skills`, or adds
    it to `Scheduler.enabled_skills`, and gives the dispatch an AUTHORITY part for it, as `use_staged_skill` in
    tests/test_scheduler.py does for `staged_skill`."""
    write_skill(root, name, **{**FEATURE_SHAPE, **overrides})
    return load_skills(root)[name]


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

    def test_the_note_on_commands_without_a_token_names_them_in_their_own_paragraph(self):
        """The note that `fetch-issue` and `issue-context` take no `--token-file` stays with the paragraph about them,
        and never reads as if about a documented command that takes one, as it did under the `withdraw` example."""
        notes = [paragraph for paragraph in self.reference().split("\n\n")
                 if re.search(r"accepts? `--token-file`", paragraph)]
        self.assertTrue(notes, "workers need to know which commands take no token")
        for paragraph in notes:
            with self.subTest(paragraph=paragraph[:60]):
                self.assertIn("`fetch-issue`", paragraph)
                self.assertIn("`issue-context`", paragraph)
                self.assertNotRegex(paragraph, r"python3 -m agent .*--token-file")

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
                        # A worker fills ISSUE_BRANCH with its own branch; the fixture issue is FARM-1 (plan P9).
                        checkpoint = json.loads(example.replace("ISSUE_BRANCH", "farmbot/farm-1"))
                        ledger.checkpoint(item["id"], claimed["token"], checkpoint)
                        saved = ledger.issue_context(item["id"])["handoff"]["content"]
                        self.assertEqual(saved, checkpoint["handoff"])
            finally:
                ledger.close()

    def test_the_notices_section_names_every_notice_kind(self):
        from agent.ledger import NOTICE_KINDS
        section = self.reference().split("\n## Notices\n", 1)[1].split("\n## ", 1)[0]
        for kind in NOTICE_KINDS:
            with self.subTest(kind=kind):
                self.assertRegex(section, rf"`(--kind )?{kind}`")


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
        for kind, line in (("started", "👀 FarmBot 已开始处理：正在定位问题或要改动的位置，验证结果和草稿 PR 会补充在本 issue。"),
                           ("blocker", "FarmBot 暂停处理。"),
                           ("delivery", "FarmBot 已提交修复或改动（草稿 PR，待 review）：")):
            with self.subTest(kind=kind):
                self.assertIn(f"\n## {kind}\n{line}\n", text)
        self.assertIn("\nFarmBot 已确认无需改动：\n", text)

    def test_a_named_instance_starts_as_itself(self):
        text = self.rendered("TestBot")
        self.assertIn("\n## started\n👀 TestBot 已开始处理：正在定位问题或要改动的位置，验证结果和草稿 PR 会补充在本 issue。\n", text)
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
                       "If that answer has no author either, stop asking",
                       # A question notice, the grouped issue comment, carries the mentions itself (spec §5.1).
                       "The comment carries `owner.person.url`", "`issue-context.recovery.notices`"):
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


class WithdrawalInstructionTests(unittest.TestCase):
    """A worker whose delegation went, or whose work a newer delegation took over, withdraws rather than pausing or
    publishing (withdrawn-work design J2, J3, §7.2)."""

    @staticmethod
    def text(*parts):
        """The file's text with each run of whitespace, line breaks included, read as one space."""
        return " ".join(ROOT.joinpath(*parts).read_text(encoding="utf-8").split())

    def test_j2_j3_skill_texts_withdraw_instead_of_pausing(self):
        fix = self.text("skills", "fix", "SKILL.md")
        for phrase in ("If `fetch-issue` prints `delegated: false` or `withdrawn: true`",
                       "any command refuses with `delegation withdrawn`", "publish nothing and ask nothing",
                       "run `withdraw` and exit", "`withdraw` ends the job as cancelled",
                       "If `withdraw` refuses because the card is delegated again, continue",
                       "revoked delegation is never a question; follow the withdrawal rule above"):
            with self.subTest(skill="fix", phrase=phrase):
                self.assertIn(phrase, fix)
        # The rule it replaces told the worker to finish blocked, and the pause list sent revoked delegation to
        # await-input: the two contradicted each other (J2).
        self.assertNotIn("re-delegated away, stop publication and finish blocked", fix)
        self.assertNotIn("(such as revoked delegation,", fix)
        chat = self.text("skills", "chat", "SKILL.md")
        for phrase in ("`issue-context.coordination.authority`", "do not ask a question: `await-input` will refuse",
                       "the card is no longer delegated to `bot_name`", "finish delivered",
                       "With `mention` or `operator` authority, delegation does not matter to you"):
            with self.subTest(skill="chat", phrase=phrase):
                self.assertIn(phrase, chat)
        reference = self.text("references", "worker-cli.md")
        for phrase in ("`withdrawn`", "`withdraw`", "save a checkpoint first; exit after it succeeds"):
            with self.subTest(reference=phrase):
                self.assertIn(phrase, reference)

    def test_the_fix_skill_asks_only_through_await_input(self):
        """Silent-delegation design A8, P9: a question posted with `activity` parks nothing, so its thread waits for
        an answer no job reads. The skill told workers both not to do that and to do it; now only `await-input` asks,
        and the CLI refuses the other way. No skill tells a worker to post an elicitation itself."""
        fix = self.text("skills", "fix", "SKILL.md")
        self.assertNotIn("`--type elicitation` only for a question", fix)
        self.assertIn("use `activity --type thought` for progress; a question always goes through `await-input`, "
                      "which posts it and parks the job", fix)
        for skill in sorted(path.parent.name for path in (ROOT / "skills").glob("*/SKILL.md")):
            with self.subTest(skill=skill):
                self.assertNotIn("--type elicitation", self.text("skills", skill, "SKILL.md"))


class SkillRegistryTests(unittest.TestCase):
    def test_repository_skills_load_with_expected_authority(self):
        skills = load_skills(ROOT / "skills")
        self.assertEqual(set(skills), {"chat", "feature", "fix"})
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


class StageManifestTests(unittest.TestCase):
    """Optional stage keys (spec §9.5): initial_root, staged and reads."""

    def test_the_fix_manifest_names_the_bot_label_it_answers(self):
        self.assertEqual(load_skills(ROOT / "skills")["fix"].intents, ("label:Bot/修改",))

    def test_fix_is_staged_from_a_neutral_start_and_chat_is_not_staged(self):
        skills = load_skills(ROOT / "skills")
        self.assertEqual((skills["fix"].staged, skills["fix"].initial_root, skills["fix"].reads), (True, None, ()))
        self.assertEqual((skills["chat"].staged, skills["chat"].initial_root, skills["chat"].reads), (False, None, ()))

    def test_the_stage_keys_are_optional_and_exposed_on_the_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_skill(tmp, "plain")
            plain = load_skills(Path(tmp))["plain"]
            self.assertEqual((plain.staged, plain.initial_root, plain.reads), (False, None, ()))
            staged = staged_skill(tmp)
            self.assertEqual((staged.staged, staged.initial_root, staged.reads), (True, "Farm-Contract", ("farmgui",)))

    def test_invalid_stage_keys_are_rejected(self):
        writes = ["Farm-Contract", "farm-hive"]
        cases = [("initial_root", dict(writes=writes, staged=True, initial_root="farmgui")),
                 ("initial_root", dict(writes=writes, initial_root="Farm-Contract")),
                 ("staged", dict(staged=True)),
                 ("staged", dict(writes=writes, staged="yes")),
                 ("staged", dict(writes=writes, staged=1)),
                 ("reads", dict(reads="farmgui")),
                 ("reads", dict(reads=[""]))]
        for key, manifest in cases:
            with self.subTest(manifest=manifest), tempfile.TemporaryDirectory() as tmp:
                write_skill(tmp, "bad", **manifest)
                with self.assertRaisesRegex(SkillError, key):
                    load_skills(Path(tmp))

    def test_an_unknown_manifest_key_is_refused(self):
        """A misspelled stage key must not load as the key's default: "stagged": true would run unstaged, with
        every worktree writable."""
        for manifest in (dict(writes=["Farm-Contract"], stagged=True), dict(description="a note")):
            with self.subTest(manifest=manifest), tempfile.TemporaryDirectory() as tmp:
                write_skill(tmp, "bad", **manifest)
                with self.assertRaisesRegex(SkillError, "unknown key"):
                    load_skills(Path(tmp))


class OptInManifestTests(unittest.TestCase):
    """Phase B plan P1 and P8: optional `opt_in` and `exclusive` manifest keys."""

    def test_both_keys_default_to_false_and_this_checkouts_skills_set_neither(self):
        skills = load_skills(ROOT / "skills")
        for name in ("chat", "fix"):
            with self.subTest(skill=name):
                self.assertEqual((skills[name].opt_in, skills[name].exclusive), (False, False))
        with tempfile.TemporaryDirectory() as tmp:
            write_skill(tmp, "plain")
            plain = load_skills(Path(tmp))["plain"]
            self.assertEqual((plain.opt_in, plain.exclusive), (False, False))

    def test_the_fixture_has_the_shape_of_the_phase_b_feature_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            feature = opt_in_skill(tmp)
            self.assertEqual((feature.name, feature.trigger, feature.intents, feature.writes, feature.initial_root,
                              feature.staged, feature.reads, feature.resources, feature.gates, feature.mcp,
                              feature.budget, feature.opt_in, feature.exclusive),
                             ("feature", ("delegation",), ("label:Bot/Code",), ("Farm-Contract", "common", "farm-hive"),
                              "Farm-Contract", True, ("Farm-Contract", "Farm-Client", "farmgui"), (),
                              ("answers", "config_ready", "closing", "pr_review"), (),
                              {"lease_seconds": 2700, "max_hours": 10, "renew_minutes": 10}, True, True))
            other = opt_in_skill(tmp, "other", exclusive=False, reads=["Farm-Contract"])
            self.assertEqual((other.exclusive, other.reads, other.opt_in), (False, ("Farm-Contract",), True))

    def test_both_keys_must_be_booleans(self):
        for key in ("opt_in", "exclusive"):
            for value in ("yes", "true", 1, 0, None, []):
                with self.subTest(key=key, value=value), tempfile.TemporaryDirectory() as tmp:
                    write_skill(tmp, "bad", **{key: value})
                    with self.assertRaisesRegex(SkillError, f"{key} must be true or false"):
                        load_skills(Path(tmp))


class EnabledSkillsTests(unittest.TestCase):
    """spec §9.11: the private host config chooses which of the checkout's skills an instance runs."""

    def setUp(self):
        from agent.dispatch import SKILL_AUTHORITY
        self.skills = load_skills(ROOT / "skills")
        self.authority = SKILL_AUTHORITY

    def test_without_the_key_every_loaded_skill_runs(self):
        """Every loaded skill but the opt-in ones (Phase B plan, P1), which chat and fix are not."""
        from agent.config import Config
        self.assertIsNone(Config("c", "s", "w").enabled_skills)
        enabled = enabled_skills(self.skills, None, authority=self.authority)
        self.assertEqual(enabled, {name: skill for name, skill in self.skills.items() if not skill.opt_in})
        self.assertLessEqual({"chat", "fix"}, set(enabled))

    def test_a_list_selects_skills_and_loads_from_the_private_profile(self):
        from agent.config import load_config
        self.assertEqual(set(enabled_skills(self.skills, ["chat"], authority=self.authority)), {"chat"})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            base = {"client_id": "c", "client_secret": "s", "webhook_secret": "w"}
            path.write_text(json.dumps({**base, "enabled_skills": ["chat", "fix"]}), encoding="utf-8")
            self.assertEqual(load_config(path).enabled_skills, ["chat", "fix"])
            path.write_text(json.dumps(base), encoding="utf-8")
            self.assertIsNone(load_config(path).enabled_skills)

    def test_an_unknown_name_or_a_missing_chat_is_a_configuration_error(self):
        with self.assertRaisesRegex(SkillError, "does not have: fgui"):
            enabled_skills(self.skills, ["chat", "fgui"], authority=self.authority)
        for names in (["fix"], []):
            with self.subTest(names=names), self.assertRaisesRegex(SkillError, "must include chat"):
                enabled_skills(self.skills, names, authority=self.authority)

    def test_a_skill_the_dispatch_cannot_brief_is_refused_only_while_it_is_enabled(self):
        """Task 11 builds no launch message for a skill without its AUTHORITY part; refusing at startup beats
        failing every launch after its worktrees were made."""
        briefs_chat_only = {"chat": "the chat part"}
        for names in (None, ["chat", "fix"]):
            with self.subTest(names=names), self.assertRaisesRegex(SkillError, "AUTHORITY part for enabled skills: fix"):
                enabled_skills(self.skills, names, authority=briefs_chat_only)
        self.assertEqual(set(enabled_skills(self.skills, ["chat"], authority=briefs_chat_only)), {"chat"})

    def test_every_repository_skill_has_its_authority_part(self):
        from agent.dispatch import SKILL_AUTHORITY
        self.assertLessEqual(set(self.skills), set(SKILL_AUTHORITY))

    def test_the_list_must_name_distinct_skills(self):
        from agent.config import Config
        for value in ("chat", ["chat", "chat"], ["chat", ""], ["chat", 3], {"chat": True}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Config("c", "s", "w", enabled_skills=value)

    def with_fixture(self, tmp, **overrides):
        """This checkout's skills and the opt-in fixture, as a checkout that ships one would load them."""
        fixture = opt_in_skill(tmp, **overrides)
        return {**self.skills, fixture.name: fixture}

    def test_without_the_key_an_opt_in_skill_is_loaded_but_not_enabled(self):
        """P1: deploying an opt-in skill starts nothing new; it needs no AUTHORITY part until a host names it."""
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(set(enabled_skills(self.with_fixture(tmp), None, authority=self.authority)),
                             {"chat", "fix"})

    def test_a_list_may_name_an_opt_in_skill_which_then_needs_its_authority_part(self):
        with tempfile.TemporaryDirectory() as tmp:
            skills = self.with_fixture(tmp)
            briefed = {**self.authority, "feature": "the fixture part"}
            unbriefed = {name: part for name, part in self.authority.items() if name != "feature"}
            self.assertEqual(set(enabled_skills(skills, ["chat", "fix", "feature"], authority=briefed)),
                             {"chat", "fix", "feature"})
            self.assertEqual(set(enabled_skills(skills, ["chat", "feature"], authority=briefed)), {"chat", "feature"})
            with self.assertRaisesRegex(SkillError, "AUTHORITY part for enabled skills: feature"):
                enabled_skills(skills, ["chat", "feature"], authority=unbriefed)

    def test_chat_cannot_be_opt_in(self):
        """Every route that is not write work falls back to chat, so no host may lose it by leaving out a list."""
        with tempfile.TemporaryDirectory() as tmp:
            write_skill(tmp, "chat", opt_in=True)
            skills = load_skills(Path(tmp))
            with self.assertRaisesRegex(SkillError, "chat cannot be opt-in"):
                enabled_skills(skills, None, authority={"chat": "the chat part"})
            self.assertEqual(set(enabled_skills(skills, ["chat"], authority={"chat": "the chat part"})), {"chat"})



class FeatureManifestTests(unittest.TestCase):
    """Phase B, Task 12: the feature manifest is exactly the plan's Shared Interfaces, and it is opt-in (P1)."""

    SHARED_INTERFACE = {
        "name": "feature",
        "trigger": ["delegation"],
        "intents": ["label:Bot/Code"],
        "writes": ["Farm-Contract", "common", "farm-hive"],
        "initial_root": "Farm-Contract",
        "staged": True,
        "reads": ["Farm-Contract", "Farm-Client", "farmgui"],
        "resources": [],
        "gates": ["answers", "config_ready", "closing", "pr_review"],
        "mcp": [],
        "budget": {"lease_seconds": 2700, "max_hours": 10, "renew_minutes": 10},
        "opt_in": True,
        "exclusive": True,
    }

    def test_the_manifest_file_is_the_shared_interface_and_task_1s_fixture(self):
        raw = json.loads((ROOT / "skills" / "feature" / "skill.json").read_text(encoding="utf-8"))
        self.assertEqual(raw, self.SHARED_INTERFACE)
        # Tasks 2-11 tested against FEATURE_SHAPE; the real manifest must be that shape, or their tests prove
        # nothing about it.
        self.assertEqual(raw, {"name": "feature", **FEATURE_SHAPE})

    def test_the_manifest_loads_as_a_staged_opt_in_exclusive_skill_without_tools(self):
        feature = load_skills(ROOT / "skills")["feature"]
        self.assertEqual((feature.trigger, feature.intents, feature.writes, feature.initial_root, feature.staged,
                          feature.reads, feature.resources, feature.gates, feature.mcp, feature.budget),
                         (("delegation",), ("label:Bot/Code",), ("Farm-Contract", "common", "farm-hive"),
                          "Farm-Contract", True, ("Farm-Contract", "Farm-Client", "farmgui"), (),
                          ("answers", "config_ready", "closing", "pr_review"), (),
                          {"lease_seconds": 2700, "max_hours": 10, "renew_minutes": 10}))
        self.assertEqual((feature.opt_in, feature.exclusive), (True, True))
        self.assertTrue(feature.skill_md.is_file())

    def test_feature_runs_only_where_enabled_skills_names_it(self):
        from agent.dispatch import SKILL_AUTHORITY
        skills = load_skills(ROOT / "skills")
        self.assertIn("feature", skills)  # loaded on every host that has this checkout
        self.assertEqual(set(enabled_skills(skills, None, authority=SKILL_AUTHORITY)), {"chat", "fix"})
        for names in (["chat", "fix", "feature"], ["chat", "feature"]):
            with self.subTest(names=names):
                self.assertEqual(set(enabled_skills(skills, names, authority=SKILL_AUTHORITY)), set(names))



class FeatureInstructionTests(unittest.TestCase):
    """Phase B, Task 13: the feature skill's safety sentences, pinned so that dropping one is a visible change.
    Whitespace is normalized, so rewrapping a paragraph changes nothing here."""

    def raw(self):
        return (ROOT / "skills" / "feature" / "SKILL.md").read_text(encoding="utf-8")

    def text(self):
        return " ".join(self.raw().split())

    def assert_phrases(self, phrases):
        text = self.text()
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_the_design_document_is_read_only_and_only_as_farmbots_app(self):
        self.assert_phrases((
            "lark-cli --profile PROFILE docs +fetch --as bot", "lark-cli --profile PROFILE drive +download --as bot",
            "`tools.lark_cli.profile`", "Never `--as user`, never another profile",
            "Never set or export a `LARKSUITE_CLI_` variable", "only a relative path under the current directory",
            "Fetch only links found in the card's description, its human comments and this job's session messages",
            "never draft from a paraphrase", "Never guess what the document says"))

    def test_rulings_come_only_from_named_authors_and_never_by_default(self):
        self.assert_phrases((
            "`[DECIDED:<Linear user name>@<date>]`", "`author.name`", "not the `displayName` handle",
            "Never invent a name, a date or a ruling", "An item stands only when a named person answers it",
            "Write no `默认·3 个工作日未异议` marker",
            "Ask for a one-line answer to the high-confidence section too, never 不用答",
            "The comment carries `owner.person.url` always, and `creator.url` when the round has a 主策 section",
            "ends with a `[farmbot:…]` marker line"))
        # A comment and a session reply are both dated in UTC+8; neither falls back to the UTC date.
        self.assertEqual(self.text().count("calendar date of its `created_at` in UTC+8"), 2)

    def test_farmbot_names_the_config_and_never_writes_designer_data(self):
        self.assert_phrases((
            "yours to define, never as questions", "Declare, never populate",
            "Never write data rows, data values or global-key values", "declare only type-neutral ones",
            "never through a spreadsheet tool", "A field the client reads is never `server`",
            "per column the exact header text"))

    def test_stage_a_follows_farm_contract_and_reads_the_client_in_its_checkouts(self):
        self.assert_phrases((
            "is that 交棒: create no Codex task, chip or issue", "`[UNREVIEWED]` never backs a proto field",
            "never install or upgrade a tool", "the next stage starts without waiting for the merge",
            "read Farm-Client and farmgui in their default-branch checkouts under `reads`",
            "with the commit you read"))
        self.assertNotIn("not read in this job", self.text())

    def test_the_job_publishes_drafts_and_never_merges(self):
        self.assert_phrases((
            "They do not let you merge, deploy, run Jenkins or any CI job, change CI",
            "Never force-push and never rewrite a published branch", "Only `status: verified` authorizes the push",
            "--draft"))

    def test_every_issue_branch_is_recorded_at_intake_after_the_foreign_work_check(self):
        self.assert_phrases((
            "record every write repository's issue branch", "after \"Other people's work\" has found nothing foreign",
            "the name `git -C WORKTREE branch --show-current` prints", "never change a recorded name"))

    def test_a_cleanup_commit_is_inspected_and_the_remote_is_merged_never_forced(self):
        self.assert_phrases((
            "`wip(<eight characters>): preserve ended work`", "Never push it as it is",
            "`git reset --soft HEAD~1`", "`git merge origin/BRANCH`", "Never rebase a published commit"))

    def test_a_stage_limit_stops_the_job_after_that_stage(self):
        self.assert_phrases((
            "the latest message that sets or lifts a limit decides", "do not hand off to or start a later stage",
            "`{\"kind\": \"stage_limit\", \"reason\": \"waiting\"", "a message after the limit asks you to go on"))

    def test_notices_follow_the_request_ids_of_the_plan(self):
        self.assert_phrases((
            "numbered from 1 (`questions-1`, `foreign-work-1`)",
            "each re-ask takes the next number from 2 (`config-needed-2`",
            "a `stage` notice only for a stage that is skipped", "stage A ends with the `merge_request` notice"))

    def test_a_removed_delegation_or_a_closed_card_ends_the_attempt(self):
        self.assert_phrases((
            "when `fetch-issue` returns `delegated: false`", "`withdrawn: true`",
            "`delegation withdrawn`", "publish nothing and ask nothing",
            "save a checkpoint", "run `withdraw` and exit", "Never ask to be delegated again",
            "`in_scope: false`"))
        section = self.raw().split("## Delegation, closure and the label", 1)[1].split("\n## ", 1)[0]
        self.assertNotIn("post a blocker that names", section)
        self.assertNotIn("and finish blocked", section)

    def test_nothing_resumes_the_job_but_a_person(self):
        self.assert_phrases(("Nothing times out, and comments alone never resume you",
                             "never treat silence, a timer, or a comment nobody wrote as an answer"))

    def test_every_documented_worker_command_parses(self):
        from agent.__main__ import parser
        commands = [line.strip() for block in re.findall(r"```bash\n(.*?)```", self.raw(), re.DOTALL)
                    for line in block.splitlines() if line.strip().startswith("python3 -m agent ")]
        self.assertTrue(commands)
        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(parser().parse_args(shlex.split(command)[3:]).item, "ITEM_ID")

    def test_every_plan_example_is_a_plan_the_ledger_saves(self):
        """Each json block is saved as a checkpoint plan of a claimed feature item on card FARM-1, so the ledger's
        own validation, P9's issue-branch rule included once it lands, judges it."""
        from agent.ledger import Ledger
        from test_ledger import ISSUE, SESSION, issue
        examples = re.findall(r"```json\n(.*?)```", self.raw(), re.DOTALL)
        self.assertTrue(examples)
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp) / "ledger.sqlite3")
            try:
                ledger.observe_issue(issue())
                ledger.ensure_session(SESSION, ISSUE, delegation=True)
                item = ledger.create_work_item(issue_id=ISSUE, session_id=SESSION, skill="feature")["id"]
                token = ledger.claim(item, worker_id="w")["token"]
                for example in examples:
                    with self.subTest(example=example[:60]):
                        plan = json.loads(example)
                        ledger.checkpoint(item, token, {"plan": plan})
                        self.assertEqual(ledger.issue_context(item)["plan"], plan)
            finally:
                ledger.close()


class FeatureCommentTemplateTests(unittest.TestCase):
    """Phase B, Task 13: the feature worker's comment templates, rendered for an instance."""

    def rendered(self, name="FarmBot"):
        return (ROOT / "references" / "comment-templates.md").read_text(encoding="utf-8").replace("<bot_name>", name)

    def section(self, name):
        return self.rendered().split(f"\n## {name}\n", 1)[1].split("\n## ", 1)[0]

    def test_the_feature_start_comment_names_the_instance(self):
        self.assertIn("\n## feature started\n👀 TestBot 已开始处理这张功能卡：", self.rendered("TestBot"))

    def test_the_notices_that_ask_people_to_act_mention_the_owner(self):
        for name in ("feature questions", "feature merge request", "feature config needed"):
            with self.subTest(name=name):
                self.assertIn("<owner.person.url>", self.section(name))
        for name in ("feature questions", "feature config needed"):
            with self.subTest(name=name):
                self.assertIn("<creator.url>", self.section(name))

    def test_the_question_round_asks_every_recipient_and_records_only_real_answers(self):
        section = self.section("feature questions")
        for phrase in ("### 主策", "### 服务端", "### 客户端", "高置信度的也请回一句", "没人回答的不会默认成立",
                       "其他（请说）"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, section)

    def test_the_merge_request_never_merges(self):
        section = self.section("feature merge request")
        for phrase in ("不会合并", "我会去 GitHub 核对"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, section)

    def test_the_config_needed_comment_asks_for_exact_headers_and_defines_ready(self):
        section = self.section("feature config needed")
        for phrase in ("表头文字必须完全一致", "会被静默丢弃", "「配置就绪」指", "不必是 main",
                       "合并即确认这些表名、列名和字段名", "Done 或 Canceled"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, section)

    def test_a_stage_limit_is_said_in_the_notice_that_ends_the_stage(self):
        for name in ("feature stage", "feature merge request", "feature config needed"):
            with self.subTest(name=name):
                self.assertIn("按本卡要求，FarmBot 在阶段 <字母> 后停下；要继续请回复本会话或 @FarmBot。",
                              self.section(name))
