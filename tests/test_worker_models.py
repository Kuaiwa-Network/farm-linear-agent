import json
import os
import tempfile
import time
import tomllib
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agent.config import Config, load_config
from agent.launcher import Launcher, RUNTIMES, _codex_model_settings
import test_scheduler


class LatestSolSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "catalog home 农场"
        self.home.mkdir()
        self.cache = self.home / "models_cache.json"
        self.seeds = {str(self.home / "auth.json"): "auth.json"}
        self.settings = {"model": "latest-sol", "reasoning_effort": "xhigh"}
        self.baseline = {"model": "gpt-6.1-sol", "reasoning_effort": "xhigh"}

    @staticmethod
    def model(slug, *efforts, **fields):
        return {"slug": slug, "visibility": "list",
                "supported_reasoning_levels": [{"effort": effort} for effort in efforts], **fields}

    def catalog(self, *entries):
        self.cache.write_text(json.dumps({"models": list(entries)}), encoding="utf-8")

    def select(self, settings=None):
        return _codex_model_settings(self.settings if settings is None else settings, self.seeds)

    def test_newest_stable_sol_uses_numeric_versions(self):
        self.catalog(self.model("gpt-6.9-sol", "xhigh"), self.model("gpt-6.10-sol", "xhigh"),
                     self.model("gpt-6.2-sol", "xhigh"))
        self.assertEqual(self.select(), {"model": "gpt-6.10-sol", "reasoning_effort": "xhigh"})
        self.catalog(self.model("gpt-6.10-sol", "xhigh"), self.model("gpt-7-sol", "xhigh"))
        self.assertEqual(self.select()["model"], "gpt-7-sol")

    def test_other_families_snapshots_hidden_and_malformed_entries_are_ignored(self):
        self.catalog(self.model("gpt-6.2-sol", "xhigh"), self.model("gpt-99-astra", "xhigh"),
                     self.model("gpt-99-sol-mini", "xhigh"), self.model("gpt-99-sol-preview", "xhigh"),
                     self.model("gpt-99-sol-2026-10-07", "xhigh"), self.model("gpt-99x1-sol", "xhigh"),
                     self.model("gpt-99-sol", "xhigh", visibility="hidden"),
                     self.model("gpt-98-sol", "xhigh", hidden=True),
                     self.model("gpt-97-sol", "xhigh", supported_reasoning_levels=None),
                     self.model(None, "xhigh"), None, [])
        self.assertEqual(self.select(), {"model": "gpt-6.2-sol", "reasoning_effort": "xhigh"})

    def test_newer_model_never_reduces_requested_effort(self):
        self.catalog(self.model("gpt-6.2-sol", "high", "xhigh"), self.model("gpt-7-sol", "high"))
        self.assertEqual(self.select(), {"model": "gpt-6.2-sol", "reasoning_effort": "xhigh"})
        self.assertEqual(self.select({"model": "latest-sol", "reasoning_effort": "high"}),
                         {"model": "gpt-7-sol", "reasoning_effort": "high"})

    def test_missing_unusable_and_older_catalogs_retain_release_baseline(self):
        self.assertEqual(self.select(), self.baseline)
        for raw in (b"not json", b"\xff", b"null", b"[]", b'{"models":null}',
                    b'{"models":[]}', b" " * ((1 << 20) + 1), b"[" * 2000 + b"]" * 2000):
            with self.subTest(raw_length=len(raw)):
                self.cache.write_bytes(raw)
                self.assertEqual(self.select(), self.baseline)
        self.catalog(self.model("gpt-6-sol", "xhigh"), self.model("gpt-5.6-sol", "xhigh"))
        self.assertEqual(self.select(), self.baseline)
        self.catalog(self.model("gpt-7-sol", "high"))
        self.assertEqual(self.select(), self.baseline)

    def test_read_failure_retains_baseline(self):
        with patch("agent.launcher._read_worker_text", side_effect=PermissionError("fixture")):
            self.assertEqual(self.select(), self.baseline)

    def test_explicit_model_pin_bypasses_catalog_without_mutating_settings(self):
        pin = {"model": "gpt-5.6-sol", "reasoning_effort": "high"}
        with patch("agent.launcher._read_worker_text", side_effect=AssertionError("must not read")):
            selected = self.select(pin)
        self.assertEqual(selected, pin)
        self.assertIsNot(selected, pin)
        self.assertEqual(self.settings["model"], "latest-sol")

    def test_source_home_is_independent_of_inherited_codex_home(self):
        self.catalog(self.model("gpt-6.2-sol", "xhigh"))
        decoy = self.root / "decoy home"
        decoy.mkdir()
        (decoy / "models_cache.json").write_text(json.dumps(
            {"models": [self.model("gpt-99-sol", "xhigh")]}), encoding="utf-8")
        with patch.dict(os.environ, {"CODEX_HOME": str(decoy)}):
            self.assertEqual(self.select()["model"], "gpt-6.2-sol")
            self.assertEqual(_codex_model_settings(self.settings, {}), self.baseline)

    def test_each_actual_attempt_resolves_refreshed_catalog_into_isolated_home(self):
        runtime = RUNTIMES["codex"]._replace(seed_files=self.seeds, command=RUNTIMES["fake"].command)
        launcher = Launcher(self.root / "runs", runtime, "test")
        self.addCleanup(launcher.stop, "job")
        for slug in ("gpt-6.1-sol", "gpt-6.2-sol"):
            with self.subTest(slug=slug):
                self.catalog(self.model(slug, "xhigh"))
                handle = launcher.spawn("job", "hello", {}, 60, self.root, model_settings=self.settings)
                handle.process.wait(timeout=10)
                deadline = time.monotonic() + 10
                while not launcher.poll():
                    self.assertLess(time.monotonic(), deadline, "owned worker did not settle")
                    time.sleep(.02)
                config = tomllib.loads((handle.run_dir / "home/config.toml").read_text(encoding="utf-8"))
                self.assertEqual(config["model"], slug)
                self.assertEqual(config["model_reasoning_effort"], "xhigh")
                self.assertEqual(config["sandbox_mode"], "workspace-write")
                self.assertFalse((handle.run_dir / "home/models_cache.json").exists())
        self.assertEqual(self.settings, {"model": "latest-sol", "reasoning_effort": "xhigh"})


class WorkerModelConfigTests(unittest.TestCase):
    def test_private_config_loads_per_skill_model(self):
        settings = {"fix": {"model": "gpt-5.6-sol", "reasoning_effort": "high"}}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps(dict(client_id="c", client_secret="s", webhook_secret="w",
                                            codex_workers=settings)))
            self.assertEqual(getattr(load_config(path), "codex_workers", None), settings)

    def test_invalid_settings_are_rejected(self):
        for settings in ([], {"fix": {"model": ""}}, {"fix": {"reasoning_effort": "hgh"}},
                         {"fix": {"extra_args": ["--anything"]}}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                Config("c", "s", "w", codex_workers=settings)

    def test_launch_writes_model_and_effort_at_toml_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = RUNTIMES["codex"]._replace(seed_files={}, command=RUNTIMES["fake"].command)
            launcher = Launcher(Path(tmp) / "runs", runtime, "test")
            handle = launcher.spawn("fix-job", "hello", {"unity": {"url": "http://localhost/mcp"}},
                                    60, tmp, model_settings={"model": "gpt-5.6-sol",
                                                            "reasoning_effort": "high"})
            self.addCleanup(launcher.stop, "fix-job")
            handle.process.wait(timeout=10)
            launcher.poll()
            config = tomllib.loads((handle.run_dir / "home/config.toml").read_text())
            self.assertEqual(config["model"], "gpt-5.6-sol")
            self.assertEqual(config["model_reasoning_effort"], "high")
            self.assertEqual(config["mcp_servers"]["unity"]["url"], "http://localhost/mcp")
            self.assertFalse(config["features"]["memories"])


class WorkerModelRoutingTests(unittest.TestCase):
    def test_default_model_reaches_new_and_resumed_workers(self):
        fixture = test_scheduler.SchedulerTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.scheduler.runtime_name = "codex"
        received = []
        original = fixture.launcher.spawn
        def capture(*args, **kwargs):
            received.append(kwargs.pop("model_settings", None))
            return original(*args, **kwargs)
        fixture.launcher.spawn = capture
        fix = fixture.item()
        fixture.scheduler.launch(fix)
        token = fixture.ledger.claim(fix["id"], worker_id="first")["token"]
        fixture.ledger.await_input(fix["id"], token, "Continue?")
        fixture.ledger.push_inbox(fix["id"], "Continue", resume_waiting=True)
        fixture.scheduler.launch(fixture.ledger.item(fix["id"]))
        from test_ledger import OTHER
        chat = fixture.item(issue_id=OTHER, session="chat-session", skill="chat")
        fixture.scheduler.launch(chat)
        expected = {"model": "latest-sol", "reasoning_effort": "xhigh"}
        self.assertEqual(received, [expected, expected, expected])

    def test_explicit_skill_settings_override_default(self):
        # Reuse the scheduler fixture without inheriting its entire test suite.
        fixture = test_scheduler.SchedulerTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.scheduler.runtime_name = "codex"
        fixture.scheduler.codex_workers = {"fix": {"model": "gpt-5.6-sol", "reasoning_effort": "high"}}
        original = fixture.launcher.spawn
        received = []
        def capture(*args, **kwargs):
            received.append(kwargs.pop("model_settings", None))
            return original(*args, **kwargs)
        fixture.launcher.spawn = capture
        fix = fixture.item()
        fixture.scheduler.launch(fix)
        token = fixture.ledger.claim(fix["id"], worker_id="first")["token"]
        fixture.ledger.await_input(fix["id"], token, "Continue?")
        fixture.ledger.push_inbox(fix["id"], "Continue", resume_waiting=True)
        fixture.scheduler.launch(fixture.ledger.item(fix["id"]))
        from test_ledger import OTHER
        chat = fixture.item(issue_id=OTHER, session="chat-session", skill="chat")
        fixture.scheduler.launch(chat)
        self.assertEqual(received, [fixture.scheduler.codex_workers["fix"],
                                    fixture.scheduler.codex_workers["fix"],
                                    {"model": "latest-sol", "reasoning_effort": "xhigh"}])

    def test_partial_skill_override_keeps_default_model(self):
        fixture = test_scheduler.SchedulerTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.scheduler.runtime_name = "codex"
        fixture.scheduler.codex_workers = {"fix": {"reasoning_effort": "high"}}
        received = []
        original = fixture.launcher.spawn
        def capture(*args, **kwargs):
            received.append(kwargs.pop("model_settings", None))
            return original(*args, **kwargs)
        fixture.launcher.spawn = capture
        fixture.scheduler.launch(fixture.item())
        self.assertEqual(received, [{"model": "latest-sol", "reasoning_effort": "high"}])
