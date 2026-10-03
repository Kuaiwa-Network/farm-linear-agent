"""Explicit feature credentials, with dummy values and no credential store or remote calls."""
import json
import os
from pathlib import Path
import tempfile
import traceback
import unittest
from unittest.mock import patch

from agent.config import Config, load_config, require_lark_cli
from agent.kw_ops import child_environment
from agent.lark_cli import secret_value, tools, worker_credentials

BLOCK = {"app_id": "cli_dummy0000000000", "secret_env": "FEATURE_FEISHU_SECRET"}
SECRET = "dummy-feature-secret"


class LarkEnvironmentTests(unittest.TestCase):
    def test_environment_variant_loads_without_an_inline_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "private config.json"
            path.write_text(json.dumps({"client_id": "c", "client_secret": "s", "webhook_secret": "w",
                                        "lark_cli": BLOCK}), encoding="utf-8")
            config = load_config(path)
            self.assertEqual(config.lark_cli, BLOCK)
            require_lark_cli(config, {"feature"})

    def test_invalid_variants_never_repeat_credential_values(self):
        for block in ({"app_id": BLOCK["app_id"]}, {"secret_env": BLOCK["secret_env"]},
                      {**BLOCK, "profile": "farmbot"}, {**BLOCK, "home": "/private/store"},
                      {**BLOCK, "app_secret": SECRET}, {**BLOCK, "app_id": SECRET},
                      {**BLOCK, "secret_env": SECRET}, {**BLOCK, "secret_env": None},
                      {**BLOCK, "secret_env": "LARKSUITE_CLI_APP_SECRET"},
                      {**BLOCK, "secret_env": "CODEX_HOME"}, {**BLOCK, "secret_env": "FARMBOT_CONFIG"},
                      {**BLOCK, "secret_env": "PATH"}):
            with self.subTest(keys=sorted(block)), self.assertRaises(ValueError) as caught:
                Config("c", "s", "w", lark_cli=block)
            self.assertNotIn(SECRET, "".join(traceback.format_exception(caught.exception)))
            self.assertNotIn(BLOCK["app_id"], str(caught.exception))

    def test_kw_ops_cannot_share_the_lark_source(self):
        with self.assertRaisesRegex(ValueError, "must differ"):
            Config("c", "s", "w", lark_cli=BLOCK,
                   kw_ops={"url": "https://gm.example.test", "token_env": BLOCK["secret_env"]})

    def test_tools_metadata_contains_neither_app_id_secret_nor_source_name(self):
        payload = tools(BLOCK, "codex", {BLOCK["secret_env"]: SECRET})
        self.assertEqual(payload, {"authentication": "environment"})
        for value in (*BLOCK.values(), SECRET):
            self.assertNotIn(value, json.dumps(payload))

    def test_missing_empty_or_unsupported_credentials_have_no_profile_fallback(self):
        for env in ({}, {BLOCK["secret_env"]: "  "}):
            with self.subTest(env=bool(env)):
                self.assertEqual(tools(BLOCK, "codex", env)["status"], "unavailable")
                with self.assertRaisesRegex(ValueError, "not set"):
                    worker_credentials(BLOCK, "codex", env)
        self.assertEqual(tools(BLOCK, "claude", {BLOCK["secret_env"]: SECRET})["status"], "unavailable")
        with self.assertRaisesRegex(ValueError, "codex runtime"):
            worker_credentials(BLOCK, "claude", {BLOCK["secret_env"]: SECRET})

    def test_only_the_configured_secret_supplies_strict_bot_credentials(self):
        env = {BLOCK["secret_env"]: SECRET, "LARKSUITE_CLI_APP_SECRET": "ambient-other-secret"}
        self.assertEqual(worker_credentials(BLOCK, "codex", env), {
            "LARKSUITE_CLI_APP_ID": BLOCK["app_id"], "LARKSUITE_CLI_APP_SECRET": SECRET,
            "LARKSUITE_CLI_STRICT_MODE": "bot"})

    def test_controller_children_withhold_the_custom_source_and_standard_overrides(self):
        env = {BLOCK["secret_env"]: SECRET, "KW_OPS_TOKEN": "dummy-token",
               "larksuite_cli_user_access_token": "dummy-user-token", "MARKER": "kept"}
        self.assertEqual(child_environment("KW_OPS_TOKEN", env, secret_env=BLOCK["secret_env"]), {"MARKER": "kept"})

    def test_windows_resolves_and_withholds_source_names_case_insensitively(self):
        with patch("agent.lark_cli.os.name", "nt"):
            env = {BLOCK["secret_env"].lower(): SECRET, "MARKER": "kept"}
            self.assertEqual(secret_value(BLOCK, env), SECRET)
            self.assertEqual(child_environment(None, env, secret_env=BLOCK["secret_env"]), {"MARKER": "kept"})
            self.assertIsNone(secret_value(BLOCK, {**env, BLOCK["secret_env"]: "ambiguous"}))
