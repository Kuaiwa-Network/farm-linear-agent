"""Opt-in UI authority, repository/resource fences and offline host diagnostics."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent import dispatch, doctor
from agent.config import Config, Paths, require_lark_cli
from agent.ledger import Ledger, LedgerError
from agent.skills import enabled_skills, load_skills
from test_ledger import ISSUE, SESSION, issue
from test_lark_cli import BLOCK, SECRET

ROOT = Path(__file__).resolve().parents[1]
SKILLS = load_skills(ROOT/"skills")
LARK = {"found": True, "version": "1.0.82", "required": None, "ok": True,
        "profile": {"name": "farmbot", "home": None, "exists": True, "other_profiles": 0,
                    "user_logins": 0, "master_key_file": False}}


class FguiScopeTests(unittest.TestCase):
    def test_authoring_is_opt_in_exclusive_rooted_and_has_no_client_or_resource_grant(self):
        ui = SKILLS["fgui"]
        self.assertEqual((ui.writes, ui.initial_root, ui.reads), (("farmgui",), "farmgui", ("Farm-Client",)))
        self.assertEqual((ui.resources, ui.mcp), ((), ()))
        self.assertTrue(ui.opt_in and ui.exclusive and ui.staged)
        self.assertEqual(ui.budget, {"lease_seconds": 2700, "max_hours": 6, "renew_minutes": 10})
        self.assertEqual(set(enabled_skills(SKILLS, None, authority=dispatch.SKILL_AUTHORITY)), {"chat", "fix"})
        self.assertEqual(set(enabled_skills(SKILLS, ["chat", "fgui"], authority=dispatch.SKILL_AUTHORITY)), {"chat", "fgui"})

    def test_ui_uses_the_same_explicit_document_reader_config_boundary(self):
        config = Config("dummy", "dummy", "dummy")
        for skills in ({"fgui"}, {"fgui", "feature"}):
            with self.subTest(skills=sorted(skills)), self.assertRaisesRegex(ValueError, "reads the 策划案"):
                require_lark_cli(config, skills)
        for block in ({"profile": "farmbot"}, BLOCK):
            config.lark_cli = block
            require_lark_cli(config, {"fgui"})
        config.lark_cli = {}
        require_lark_cli(config, {"chat", "fix"})

    def test_fgui_cannot_handoff_to_client_or_reserve_unity_even_with_a_valid_claim(self):
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        ledger = Ledger(Path(tmp)/"ledger.sqlite3")
        self.addCleanup(ledger.close)
        ledger.observe_issue(issue())
        ledger.ensure_session(SESSION, ISSUE, delegation=True)
        item = ledger.create_work_item(issue_id=ISSUE, session_id=SESSION, skill="fgui")
        token = ledger.claim(item["id"], worker_id="ui-scope")["token"]
        with self.assertRaisesRegex(LedgerError, "not a fgui target"):
            ledger.handoff_repository(item["id"], token, "Farm-Client", skill=SKILLS["fgui"])
        with self.assertRaisesRegex(LedgerError, "not a resource of fgui"):
            ledger.await_resource(item["id"], token, "unity_slot", "batch", skill=SKILLS["fgui"])
        self.assertEqual(ledger.item(item["id"])["state"], "running")
        self.assertIsNone(ledger.item(item["id"])["next_root_repo"])
        self.assertEqual(ledger.reservations(), [])

    def test_instructions_require_fresh_rounds_human_attribution_and_the_authoring_limit(self):
        text = " ".join(SKILLS["fgui"].skill_md.read_text(encoding="utf-8").split())
        for phrase in ("comments alone do not", "named human", "invalidate approval", "visual_approved",
                       "export_requested", "Phase E is unavailable", 'pause.kind="stage_limit"',
                       "no resource or MCP grant", "no package cycles", "publish nothing and ask nothing",
                       "same id and exact body", "Never invent a name", "prepared"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase.casefold(), text.casefold())
        self.assertNotIn("--type elicitation", text)


class FguiToolchainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="UI doctor ")
        self.addCleanup(self.tmp.cleanup)
        self.config = Config("doctor-only", "doctor-only", "doctor-only", runtime="codex",
                             local_root=Path(self.tmp.name)/"empty", enabled_skills=["chat", "fgui"], lark_cli=BLOCK)
        self.commands = []
        self.payload = {"pillow": "12.3.0", "font": {"name": "prepared.ttf", "sha256": "a"*64}}

    def probe(self, argv, env):
        self.commands.append((argv, env))
        text = json.dumps(self.payload) if "-c" in argv else "Python 3.13.16" if argv[0] == sys.executable else "git-lfs/3.7.1"
        return True, subprocess.CompletedProcess(argv, 0, stdout=text, stderr="")

    def report(self):
        with patch("agent.doctor._run", side_effect=self.probe), patch("agent.doctor._lark_cli", return_value=copy.deepcopy(LARK)):
            return doctor.diagnose(self.config)

    def test_only_ui_native_tools_are_probed_with_secrets_withheld_and_no_ledger_created(self):
        denied = {BLOCK["secret_env"]: SECRET, "LARKSUITE_CLI_APP_SECRET": "ambient-secret",
                  "LARKSUITE_CLI_USER_ACCESS_TOKEN": "ambient-user", "LARKSUITE_CLI_CONFIG_DIR": "alternate"}
        with patch.dict(os.environ, denied):
            report = self.report()
        ui = report["tools"]["fgui"]
        self.assertEqual(ui["missing"], [])
        self.assertEqual(set(ui["entries"]), {"python", "git_lfs", "pillow", "cjk_font", "lark_cli"})
        self.assertNotIn("feature", report["tools"])
        self.assertEqual({cmd[0] for cmd, _ in self.commands}, {sys.executable, "git-lfs"})
        for argv, env in self.commands:
            self.assertFalse(set(denied) & set(env))
            self.assertEqual((env["LARKSUITE_CLI_NO_UPDATE_NOTIFIER"], env["LARKSUITE_CLI_REMOTE_META"]), ("1", "off"))
            self.assertNotIn("bash", argv)
        imaging = next(argv for argv, _ in self.commands if "-c" in argv)
        self.assertEqual(imaging[0:3], [sys.executable, "-I", "-B"])
        self.assertFalse(Paths(self.config).ledger.exists())
        self.assertEqual({f["code"] for f in report["findings"]}, {"ledger_unreadable"})
        self.assertNotIn(SECRET, json.dumps(report))
        self.assertNotIn(BLOCK["app_id"], json.dumps(report))

    def test_missing_or_wrong_pillow_and_font_remain_named_gaps(self):
        for payload, missing in (({}, ["cjk_font", "pillow"]),
                                 ({"pillow": "12.2.0", "font": None}, ["cjk_font", "pillow"]),
                                 ({"pillow": "12.3.0", "font": None}, ["cjk_font"])):
            self.payload = payload
            with self.subTest(payload=payload):
                report = self.report()
                self.assertEqual(report["tools"]["fgui"]["missing"], missing)
                self.assertIn("fgui_toolchain_incomplete", {f["code"] for f in report["findings"]})

    def test_default_host_does_not_probe_ui_tools_or_read_a_profile(self):
        self.config.enabled_skills = ["chat", "fix"]
        with patch("agent.doctor.fgui_toolchain") as probe:
            report = doctor.diagnose(self.config)
        probe.assert_not_called()
        self.assertNotIn("fgui", report["tools"])

    def test_combined_host_reuses_the_already_sanitized_feature_reader_probe(self):
        self.config.enabled_skills = ["chat", "feature", "fgui"]
        feature = {"entries": {"lark_cli": copy.deepcopy(LARK)}, "missing": [], "optional_missing": []}
        with patch("agent.doctor.feature_toolchain", return_value=feature), patch("agent.doctor._run", side_effect=self.probe), \
                patch("agent.doctor._lark_cli") as reader:
            report = doctor.diagnose(self.config)
        reader.assert_not_called()
        self.assertIs(report["tools"]["fgui"]["entries"]["lark_cli"], feature["entries"]["lark_cli"])
