"""Private host configuration: the lark_cli block (P5; spec §5.4, §8.3, D12)."""
import json
import os
import tempfile
import traceback
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.config import LARK_CLI_SKILLS, Config, load_config, require_lark_cli


class LarkCliConfigTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="飞书 配置 ")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.local_root = self.root / "state"
        # Validation reads the path only. The temporary directories and the user's home are refused, so the
        # accepted example lies elsewhere.
        self.home = "/srv/farmbot/lark cli 家"

    def config(self, **values):
        return Config("c", "s", "w", **{"local_root": self.local_root, **values})

    def test_no_block_means_not_configured(self):
        self.assertEqual(self.config().lark_cli, {})

    def test_a_profile_and_a_home_are_accepted_and_loaded_from_the_private_profile(self):
        for name in ("farmbot", "FarmBot.2", "farmbot_test-1", "f" * 64):
            with self.subTest(name=name):
                self.assertEqual(self.config(lark_cli={"profile": name}).lark_cli, {"profile": name})
        block = {"profile": "farmbot", "home": self.home}
        path = self.root / "config.json"
        path.write_text(json.dumps({"client_id": "c", "client_secret": "s", "webhook_secret": "w",
                                    "local_root": str(self.local_root), "lark_cli": block}, ensure_ascii=False),
                        encoding="utf-8")
        if os.name == "nt":
            with self.assertRaisesRegex(ValueError, "macOS and Linux"):
                load_config(path)
        else:
            self.assertEqual(load_config(path).lark_cli, block)

    def test_invalid_blocks_are_refused_without_repeating_a_value(self):
        for block in ([], "farmbot", {"home": self.home}, {"profile": ""}, {"profile": "-farmbot"},
                      {"profile": ".farmbot"}, {"profile": "farm bot"}, {"profile": "f" * 65}, {"profile": "飞书"},
                      {"profile": "farmbot;id"}, {"profile": 5},
                      {"profile": "farmbot", "app_id": "cli_dummy0000000000"},
                      {"profile": "farmbot", "app_secret": "dummy-secret-value"},
                      {"profile": "farmbot", "secret_env": "LARKSUITE_CLI_APP_SECRET"}):
            with self.subTest(block=block):
                with self.assertRaises(ValueError) as caught:
                    self.config(lark_cli=block)
                text = "".join(traceback.format_exception(caught.exception))
                self.assertNotIn("dummy-secret-value", text)
                self.assertNotIn("cli_dummy0000000000", text)

    @unittest.skipIf(os.name == "nt", "Windows refuses every lark_cli home")
    def test_a_home_lies_outside_local_root_the_users_home_and_every_temporary_directory(self):
        """Workers write under local_root and, in Codex's sandbox, the temporary directories: they must not be able to
        change the store they read. The user's own home holds the user's own lark-cli store (Shared Interfaces)."""
        for home in ("relative/lark", "~/lark", str(Path.home()), str(Path.home() / "FarmBot" / "lark-cli"),
                     str(self.local_root / "lark-cli"), str(Path(tempfile.gettempdir()) / "lark"), "/tmp/lark",
                     "/var/tmp/lark", self.home + "\n", 5):
            with self.subTest(home=home), self.assertRaisesRegex(ValueError, "lark_cli home must be"):
                self.config(lark_cli={"profile": "farmbot", "home": home})
        with self.assertRaisesRegex(ValueError, "lark_cli home must be"):
            self.config(local_root=Path("/srv/farmbot/state"),
                        lark_cli={"profile": "farmbot", "home": "/srv/farmbot/state/lark-cli"})
        self.config(local_root=Path("/srv/farmbot/state"), lark_cli={"profile": "farmbot", "home": self.home})
        with patch.dict(os.environ, {"TMPDIR": "/srv/farmbot-scratch"}), \
                self.assertRaisesRegex(ValueError, "lark_cli home must be"):
            self.config(lark_cli={"profile": "farmbot", "home": "/srv/farmbot-scratch/lark"})

    @unittest.skipUnless(os.name == "nt", "lark-cli keeps secrets per Windows user, whatever HOME says")
    def test_a_home_is_refused_on_windows(self):
        with self.assertRaisesRegex(ValueError, "macOS and Linux"):
            self.config(lark_cli={"profile": "farmbot", "home": self.home})

    def test_a_host_that_enables_a_skill_reading_the_design_doc_names_a_profile(self):
        self.assertEqual(LARK_CLI_SKILLS, ("feature",))
        with self.assertRaisesRegex(ValueError, "feature reads the 策划案 with lark-cli"):
            require_lark_cli(self.config(), {"chat", "fix", "feature"})
        require_lark_cli(self.config(lark_cli={"profile": "farmbot"}), {"chat", "fix", "feature"})
        require_lark_cli(self.config(), {"chat", "fix"})
