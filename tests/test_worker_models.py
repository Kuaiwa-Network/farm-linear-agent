import json
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agent.config import Config, load_config
from agent.launcher import Launcher, RUNTIMES
import test_scheduler


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
            runtime = RUNTIMES["codex"]._replace(seed_files={})
            launcher = Launcher(Path(tmp) / "runs", runtime, "test")
            process = Mock(pid=123)
            with patch("agent.launcher.subprocess.Popen", return_value=process):
                handle = launcher.spawn("fix-job", "hello", {"unity": {"url": "http://localhost/mcp"}},
                                        60, tmp, model_settings={"model": "gpt-5.6-sol",
                                                                "reasoning_effort": "high"})
            config = tomllib.loads((handle.run_dir / "home/config.toml").read_text())
            self.assertEqual(config["model"], "gpt-5.6-sol")
            self.assertEqual(config["model_reasoning_effort"], "high")
            self.assertEqual(config["mcp_servers"]["unity"]["url"], "http://localhost/mcp")
            self.assertFalse(config["features"]["memories"])


class WorkerModelRoutingTests(unittest.TestCase):
    def test_fix_and_resumed_fix_receive_settings_but_chat_does_not(self):
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
                                    fixture.scheduler.codex_workers["fix"], None])
