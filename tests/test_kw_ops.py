import json
import os
import tempfile
import traceback
import unittest
from pathlib import Path
from unittest.mock import patch

from agent import kw_ops
from agent.config import Config, load_config

URL = "https://gm.test/mcp"
CONFIG = {"url": URL, "token_env": "KW_OPS_TOKEN"}
TOKEN = "dummy-token-value"
ENV = {"KW_OPS_TOKEN": TOKEN}


class KwOpsConfigTests(unittest.TestCase):
    def test_no_block_means_not_configured(self):
        self.assertEqual(Config("c", "s", "w").kw_ops, {})

    def test_a_valid_block_is_accepted_and_loaded_from_the_private_profile(self):
        for url in (URL, "https://gm.test:8080/mcp", "http://localhost:8080/mcp",
                    "http://127.0.0.1/mcp", "http://[::1]/mcp"):
            with self.subTest(url=url):
                block = {**CONFIG, "url": url}
                self.assertEqual(Config("c", "s", "w", kw_ops=block).kw_ops, block)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"client_id": "c", "client_secret": "s", "webhook_secret": "w",
                                        "kw_ops": CONFIG}), encoding="utf-8")
            self.assertEqual(load_config(path).kw_ops, CONFIG)

    def test_invalid_blocks_are_rejected(self):
        for block in ([], {"url": URL}, {**CONFIG, "token": "inline-secret"}, {**CONFIG, "url": "ftp://gm.test/mcp"},
                      {**CONFIG, "url": "http://gm.test/mcp"}, {**CONFIG, "url": "http://10.0.0.1/mcp"},
                      {**CONFIG, "url": "http://localhost.evil/mcp"},
                      {**CONFIG, "url": "gm.test/mcp"}, {**CONFIG, "url": 5}, {**CONFIG, "token_env": "kw_ops_token"},
                      {**CONFIG, "token_env": "KW OPS"}, {**CONFIG, "token_env": ""}, {**CONFIG, "token_env": None}):
            with self.subTest(block=block), self.assertRaises(ValueError):
                Config("c", "s", "w", kw_ops=block)

    def test_a_token_variable_cannot_be_replaced_by_worker_setup(self):
        for name in ("CODEX_HOME", "CLAUDE_CONFIG_DIR", "CLAUDE_CODE_DISABLE_AUTO_MEMORY",
                     "FARMBOT_ITEM_ID", "FARMBOT_DB", "FARMBOT_CONFIG", "PYTHONPATH"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "conflicts with a worker"):
                Config("c", "s", "w", kw_ops={**CONFIG, "token_env": name})

    def test_a_url_names_a_host_and_carries_no_credentials_or_stray_characters(self):
        for url in ("http://?", "http://@", "http://:", "http:///mcp", "https://gm.test:dummy-secret/mcp",
                    "https://gm.test:99999/mcp", "http://[dummy-secret]/mcp", "http://@gm.test/mcp",
                    "https://user@gm.test/mcp", "https://user:dummy-secret@gm.test/mcp",
                    "https://gm.test/mcp?token=dummy-secret", "https://gm.test/mcp#dummy-secret",
                    "https://gm.test/mcp\x00", "https://gm.test/m cp", " https://gm.test/mcp", "https://gm.test/m\ncp",
                    "https://gm\ttest/mcp"):
            with self.subTest(url=url):
                with self.assertRaises(ValueError) as caught:
                    Config("c", "s", "w", kw_ops={**CONFIG, "url": url})
                # One message for every URL failure; nothing in the error, even a chained one, repeats the value.
                self.assertEqual(str(caught.exception), "kw_ops url must use https, or http on loopback")
                self.assertNotIn("dummy-secret", "".join(traceback.format_exception(caught.exception)))

    def test_unity_child_environment_keeps_host_settings_but_removes_the_token(self):
        with patch.dict(os.environ, {"KW_OPS_TOKEN": TOKEN, "UNITY_LICENSE_MARKER": "kept"}):
            child = kw_ops.child_environment("KW_OPS_TOKEN")
        self.assertNotIn("KW_OPS_TOKEN", child)
        self.assertEqual(child["UNITY_LICENSE_MARKER"], "kept")
        self.assertEqual(kw_ops.child_environment("KW_OPS_TOKEN", {"KW_OPS_TOKEN": TOKEN, "OTHER": "yes"}),
                         {"OTHER": "yes"})
        if os.name == "nt":
            self.assertEqual(kw_ops.child_environment("KW_OPS_TOKEN", {"kw_ops_token": TOKEN, "OTHER": "yes"}),
                             {"OTHER": "yes"})
        self.assertIsNone(kw_ops.child_environment(None))


class KwOpsResolutionTests(unittest.TestCase):
    def resolve(self, grants, config, runtime, environ):
        resolution = kw_ops.resolve(grants, config, runtime, environ)
        # Whatever the outcome, the token's value is in nothing FarmBot injects or tells the worker.
        self.assertNotIn(TOKEN, repr(resolution))
        return resolution

    def test_manifest_grants_map_to_access_levels(self):
        self.assertIsNone(kw_ops.access(()))
        self.assertIsNone(kw_ops.access(("kw_ops:write", "KW_OPS", "kw_ops ")))
        self.assertEqual(kw_ops.access(("kw_ops",)), "full")
        self.assertEqual(kw_ops.access(("kw_ops:read",)), "read")
        self.assertEqual(kw_ops.access(("kw_ops:read", "kw_ops")), "full")

    def test_a_full_codex_grant_names_the_token_variable_and_never_its_value(self):
        resolution = self.resolve(("kw_ops",), CONFIG, "codex", ENV)
        self.assertEqual(resolution.tools, {"access": "full"})
        self.assertEqual(resolution.server, {"url": URL, "bearer_token_env_var": "KW_OPS_TOKEN"})
        self.assertEqual(resolution.keep_env, "KW_OPS_TOKEN")

    def test_a_read_grant_enables_only_the_query_tools(self):
        self.assertEqual(kw_ops.READ_TOOLS, ("gm_list_targets", "gm_query_players", "gm_player_detail",
                                             "gm_guild_query", "gm_time_get", "gm_reward_types",
                                             "gm_reward_catalog"))
        resolution = self.resolve(("kw_ops:read",), CONFIG, "codex", ENV)
        self.assertEqual(resolution.tools, {"access": "read"})
        self.assertEqual(resolution.server["enabled_tools"], list(kw_ops.READ_TOOLS))

    def test_a_skill_without_a_grant_gets_nothing(self):
        self.assertEqual(self.resolve((), CONFIG, "codex", ENV), kw_ops.Resolution(None, None, None))

    def test_an_unavailable_grant_says_what_is_missing_and_injects_nothing(self):
        cases = {"not configured": ({}, "codex", ENV), "KW_OPS_TOKEN is not set": (CONFIG, "codex", {}),
                 "empty variable": (CONFIG, "codex", {"KW_OPS_TOKEN": ""}), "claude": (CONFIG, "claude", ENV)}
        for label, (config, runtime, environ) in cases.items():
            with self.subTest(label):
                resolution = self.resolve(("kw_ops",), config, runtime, environ)
                self.assertEqual(resolution.tools["status"], "unavailable")
                self.assertIsNone(resolution.server)
                self.assertIsNone(resolution.keep_env)
        self.assertIn("not configured", self.resolve(("kw_ops",), {}, "codex", ENV).tools["reason"])
        self.assertIn("KW_OPS_TOKEN", self.resolve(("kw_ops",), CONFIG, "codex", {}).tools["reason"])
        self.assertIn("claude", self.resolve(("kw_ops",), CONFIG, "claude", ENV).tools["reason"])

    def test_a_whitespace_only_token_counts_as_unset(self):
        unset = self.resolve(("kw_ops",), CONFIG, "codex", {})
        for value in ("   ", " \t\n"):
            with self.subTest(value=value):
                self.assertEqual(self.resolve(("kw_ops",), CONFIG, "codex", {"KW_OPS_TOKEN": value}), unset)

    def test_every_other_runtime_is_refused_whatever_the_host_provides(self):
        cases = {"claude read grant": (("kw_ops:read",), CONFIG, "claude", ENV),
                 "claude on an unconfigured host": (("kw_ops",), {}, "claude", ENV),
                 "claude without the token": (("kw_ops",), CONFIG, "claude", {}),
                 "fake read grant": (("kw_ops:read",), CONFIG, "fake", ENV)}
        for label, (grants, config, runtime, environ) in cases.items():
            with self.subTest(label):
                reason = f"kw_ops is not supported on the {runtime} runtime"
                self.assertEqual(self.resolve(grants, config, runtime, environ),
                                 kw_ops.Resolution({"status": "unavailable", "reason": reason}, None, None))
