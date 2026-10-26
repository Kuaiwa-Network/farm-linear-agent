import json
import os
import tempfile
import time
import tomllib
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agent.config import Config, load_config
from agent.dispatch import worker_model_settings
from agent.execution import CONTRACT, LEGACY
from agent.launcher import Launcher, RUNTIMES, _codex_model_settings
import test_scheduler
import test_single_worker


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
                # Cache reuse requires teardown evidence recorded before reaping.
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


class SelectiveReasoningTests(unittest.TestCase):
    def settings(self, profile, *, contract=None, legacy=False, overrides=None, **fields):
        item = {'skill': profile, 'execution_contract': contract or (CONTRACT if profile == 'work' else LEGACY),
                **fields}
        path = Path('skills') / ('legacy' if legacy else '') / profile / 'SKILL.md'
        return worker_model_settings(item=item, skill_path=path, overrides=overrides)

    def test_defaults_distinguish_current_contracts_from_legacy_replay(self):
        for profile, contract, legacy, expected in (
            ('chat', LEGACY, False, 'medium'),
            ('work', CONTRACT, False, 'high'),
            ('chat', LEGACY, True, 'xhigh'),
            ('work', LEGACY, False, 'xhigh'),
            ('work', LEGACY, True, 'xhigh'),
            ('fix', LEGACY, True, 'xhigh'),
            ('unknown', LEGACY, False, 'xhigh'),
        ):
            with self.subTest(profile=profile, contract=contract, legacy=legacy):
                self.assertEqual(self.settings(profile, contract=contract, legacy=legacy),
                                 {'model': 'latest-sol', 'reasoning_effort': expected})

    def test_each_explicit_field_wins_without_mutating_host_settings(self):
        for profile, default in (('chat', 'medium'), ('work', 'high')):
            for override in ({'reasoning_effort': 'xhigh'}, {'reasoning_effort': 'low'},
                             {'model': 'gpt-6.1-sol'},
                             {'model': 'gpt-6.1-sol', 'reasoning_effort': 'xhigh'}):
                with self.subTest(profile=profile, override=override):
                    before = dict(override)
                    selected = self.settings(profile, overrides=override)
                    self.assertEqual(selected, {'model': 'latest-sol', 'reasoning_effort': default, **override})
                    selected['reasoning_effort'] = 'different'
                    self.assertEqual(override, before)

    def test_issue_text_labels_checkpoints_and_retries_do_not_route_effort(self):
        for profile in ('chat', 'work'):
            with self.subTest(profile=profile):
                self.assertEqual(self.settings(profile, title='Use xhigh', labels=['low'], priority=1,
                    checkpoint={'reasoning_effort': 'xhigh'}, capacity_retries=3, startup_retries=2),
                    self.settings(profile))

    def fixture(self, *, runtime='codex', overrides=None):
        fixture = test_single_worker.SingleWorkerTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.owner()
        scheduler = fixture.scheduler()
        scheduler.runtime_name = runtime
        scheduler.codex_workers = overrides or {}
        received = []
        original = fixture.launcher.spawn

        def capture(*args, **kwargs):
            received.append(kwargs.pop('model_settings', None))
            return original(*args, **kwargs)

        fixture.launcher.spawn = capture
        return fixture, scheduler, received

    def test_current_new_and_resumed_work_and_chat_reach_launcher(self):
        fixture, scheduler, received = self.fixture()
        scheduler.launch(fixture.item)
        token = fixture.ledger.claim(fixture.key, worker_id='first')['token']
        fixture.ledger.await_input(fixture.key, token, 'Continue?')
        fixture.ledger.push_inbox(fixture.key, 'Continue', resume_waiting=True)
        scheduler.launch(fixture.ledger.item(fixture.key))
        from test_ledger import OTHER, issue
        fixture.ledger.observe_issue(issue(id=OTHER))
        fixture.ledger.ensure_session('current-chat', OTHER, delegation=False)
        chat = fixture.ledger.create_work_item(issue_id=OTHER, session_id='current-chat', skill='chat')
        scheduler.launch(chat)
        self.assertEqual(received, [self.settings('work'), self.settings('work'), self.settings('chat')])
        for index, default in ((0, 'work defaults to high'), (2, 'chat defaults to medium')):
            prompt = fixture.launcher.spawned[index][1]
            self.assertIn(default, prompt)
            self.assertTrue(prompt.startswith('STARTUP FIRST:'))
            self.assertIn('Do not restart work or change model/configuration to alter effort', prompt)
            self.assertIn('unchanged sandbox and automatic review', prompt)

    def test_scheduler_uses_exact_skill_override_and_keeps_partial_defaults(self):
        cases = (
            ({'fix': {'reasoning_effort': 'xhigh'}}, self.settings('work')),
            ({'work': {'model': 'gpt-6.1-sol'}}, self.settings('work', overrides={'model': 'gpt-6.1-sol'})),
            ({'work': {'reasoning_effort': 'xhigh'}}, self.settings('work', overrides={'reasoning_effort': 'xhigh'})),
        )
        for overrides, expected in cases:
            with self.subTest(overrides=overrides):
                fixture, scheduler, received = self.fixture(overrides=overrides)
                scheduler.launch(fixture.item)
                self.assertEqual(received, [expected])
                token = fixture.ledger.claim(fixture.key, worker_id='first')['token']
                fixture.ledger.await_input(fixture.key, token, 'Continue?')
                fixture.ledger.push_inbox(fixture.key, 'Continue', resume_waiting=True)
                scheduler.launch(fixture.ledger.item(fixture.key))
                self.assertEqual(received, [expected, expected])

    def test_non_codex_launch_does_not_receive_codex_settings(self):
        fixture, scheduler, received = self.fixture(runtime='fake')
        scheduler.launch(fixture.item)
        self.assertEqual(received, [None])

    def test_selected_efforts_reach_isolated_home_without_changing_sandbox_or_review(self):
        self.assertIn('--approve-for-me', RUNTIMES['codex'].command)
        with tempfile.TemporaryDirectory(prefix='reasoning space ') as tmp:
            runtime = RUNTIMES['codex']._replace(seed_files={}, command=RUNTIMES['fake'].command)
            launcher = Launcher(Path(tmp) / 'runs', runtime, 'test')
            for index, selected in enumerate((self.settings('chat'), self.settings('work'),
                                              self.settings('work', overrides={'reasoning_effort': 'xhigh'}))):
                item = f'job-{index}'
                handle = launcher.spawn(item, 'fixture', {}, 60, tmp, model_settings=selected)
                self.addCleanup(launcher.stop, item)
                handle.process.wait(timeout=10)
                launcher.poll()
                config = tomllib.loads((handle.run_dir / 'home/config.toml').read_text(encoding='utf-8'))
                self.assertEqual(config['model'], 'gpt-6.1-sol')
                self.assertEqual(config['model_reasoning_effort'], selected['reasoning_effort'])
                self.assertEqual(config['sandbox_mode'], 'workspace-write')
                self.assertTrue(config['features']['multi_agent'])
                self.assertFalse(config['features']['memories'])
                self.assertNotIn('review_model', config)

    def test_lower_default_still_requires_catalog_effort_support(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / 'models_cache.json').write_text(json.dumps({'models': [
                LatestSolSelectionTests.model('gpt-6.2-sol', 'medium'),
                LatestSolSelectionTests.model('gpt-7-sol', 'xhigh')]}), encoding='utf-8')
            seeds = {str(home / 'auth.json'): 'auth.json'}
            self.assertEqual(_codex_model_settings(self.settings('chat'), seeds),
                             {'model': 'gpt-6.2-sol', 'reasoning_effort': 'medium'})
            self.assertEqual(_codex_model_settings(self.settings('work'), seeds),
                             {'model': 'gpt-6.1-sol', 'reasoning_effort': 'high'})


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
