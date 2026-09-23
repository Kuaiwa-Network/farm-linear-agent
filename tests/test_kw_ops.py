import json
import tempfile
import unittest
from pathlib import Path

from agent import kw_ops
from agent.config import Config, load_config

URL = "http://gm.test/mcp"
CONFIG = {"url": URL, "token_env": "KW_OPS_TOKEN"}
ENV = {"KW_OPS_TOKEN": "dummy-token-value"}


class KwOpsConfigTests(unittest.TestCase):
    def test_no_block_means_not_configured(self):
        self.assertEqual(Config("c", "s", "w").kw_ops, {})

    def test_a_valid_block_is_accepted_and_loaded_from_the_private_profile(self):
        self.assertEqual(Config("c", "s", "w", kw_ops=dict(CONFIG)).kw_ops, CONFIG)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"client_id": "c", "client_secret": "s", "webhook_secret": "w",
                                        "kw_ops": CONFIG}), encoding="utf-8")
            self.assertEqual(load_config(path).kw_ops, CONFIG)

    def test_invalid_blocks_are_rejected(self):
        for block in ([], {"url": URL}, {**CONFIG, "token": "inline-secret"}, {**CONFIG, "url": "ftp://gm.test/mcp"},
                      {**CONFIG, "url": "gm.test/mcp"}, {**CONFIG, "token_env": "kw_ops_token"},
                      {**CONFIG, "token_env": "KW OPS"}, {**CONFIG, "token_env": ""}):
            with self.subTest(block=block), self.assertRaises(ValueError):
                Config("c", "s", "w", kw_ops=block)


class KwOpsResolutionTests(unittest.TestCase):
    def test_manifest_grants_map_to_access_levels(self):
        self.assertIsNone(kw_ops.access(()))
        self.assertEqual(kw_ops.access(("kw_ops",)), "full")
        self.assertEqual(kw_ops.access(("kw_ops:read",)), "read")
        self.assertEqual(kw_ops.access(("kw_ops:read", "kw_ops")), "full")

    def test_a_full_codex_grant_names_the_token_variable_and_never_its_value(self):
        resolution = kw_ops.resolve(("kw_ops",), CONFIG, "codex", ENV)
        self.assertEqual(resolution.tools, {"access": "full"})
        self.assertEqual(resolution.server, {"url": URL, "bearer_token_env_var": "KW_OPS_TOKEN"})
        self.assertEqual(resolution.keep_env, "KW_OPS_TOKEN")
        self.assertNotIn("dummy-token-value", repr(resolution))

    def test_a_read_grant_enables_only_the_query_tools(self):
        self.assertEqual(kw_ops.READ_TOOLS, ("gm_list_targets", "gm_query_players", "gm_player_detail",
                                             "gm_guild_query", "gm_time_get", "gm_reward_types",
                                             "gm_reward_catalog"))
        resolution = kw_ops.resolve(("kw_ops:read",), CONFIG, "codex", ENV)
        self.assertEqual(resolution.tools, {"access": "read"})
        self.assertEqual(resolution.server["enabled_tools"], list(kw_ops.READ_TOOLS))

    def test_a_skill_without_a_grant_gets_nothing(self):
        self.assertEqual(kw_ops.resolve((), CONFIG, "codex", ENV), kw_ops.Resolution(None, None, None))

    def test_an_unavailable_grant_says_what_is_missing_and_injects_nothing(self):
        cases = {"not configured": ({}, "codex", ENV), "KW_OPS_TOKEN is not set": (CONFIG, "codex", {}),
                 "empty variable": (CONFIG, "codex", {"KW_OPS_TOKEN": ""}), "claude": (CONFIG, "claude", ENV)}
        for label, (config, runtime, environ) in cases.items():
            with self.subTest(label):
                resolution = kw_ops.resolve(("kw_ops",), config, runtime, environ)
                self.assertEqual(resolution.tools["status"], "unavailable")
                self.assertIsNone(resolution.server)
                self.assertIsNone(resolution.keep_env)
        self.assertIn("not configured", kw_ops.resolve(("kw_ops",), {}, "codex", ENV).tools["reason"])
        self.assertIn("KW_OPS_TOKEN", kw_ops.resolve(("kw_ops",), CONFIG, "codex", {}).tools["reason"])
        self.assertIn("claude", kw_ops.resolve(("kw_ops",), CONFIG, "claude", ENV).tools["reason"])
