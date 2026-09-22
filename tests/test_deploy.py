"""launchd agents: the property lists FarmBot writes, and what installing them touches (spec §17)."""
import plistlib
from dataclasses import replace
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.config import Config
from agent.deploy import AGENTS, install, missing_tools, plist, tunnel_arguments

ROOT = Path(__file__).resolve().parents[1]


class DeployTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = Config(client_id="c", client_secret="s", webhook_secret="w",
                             local_root=self.root / "local", port=8765)

    def test_a_rendered_plist_parses_and_keeps_the_job_alive(self):
        text = plist("com.example.job", ["/bin/echo", "hi"], self.root, self.root / "logs")
        parsed = plistlib.loads(text.encode("utf-8"))
        self.assertEqual(parsed["Label"], "com.example.job")
        self.assertEqual(parsed["ProgramArguments"], ["/bin/echo", "hi"])
        self.assertTrue(parsed["RunAtLoad"] and parsed["KeepAlive"])
        self.assertEqual(parsed["WorkingDirectory"], str(self.root))
        self.assertTrue(parsed["StandardErrorPath"].endswith("com.example.job.err.log"))

    def test_the_serve_agent_runs_in_the_standard_band_so_unity_is_not_throttled(self):
        """Task 0 Step 6: the same EditMode suite took 105 s under launchd's Background band against 14 s
        foreground with warm caches — 7.5x, against a warm-cache control — and a launchd job's band is
        inherited by every process it spawns, which here means the worker and the batch Editor the pool
        starts beside it."""
        job = plistlib.loads(plist("com.kuaiwa.farmbot.serve", ["/bin/true"], "/tmp", "/tmp").encode("utf-8"))
        self.assertEqual(job["ProcessType"], "Standard")
        target = self.root / "LaunchAgents"
        install(self.config, target, python="/usr/bin/python3", cloudflared="/usr/bin/cloudflared")
        installed = plistlib.loads((target / f"{AGENTS['serve']}.plist").read_bytes())
        self.assertEqual(installed["ProcessType"], "Standard")

    def test_a_quick_tunnel_is_the_default_and_a_named_tunnel_is_one_config_key(self):
        self.assertEqual(tunnel_arguments(self.config, cloudflared="/usr/bin/cloudflared")[:4],
                         ["/usr/bin/cloudflared", "tunnel", "--url", "http://127.0.0.1:8765"])
        named = Config(client_id="c", client_secret="s", webhook_secret="w",
                       local_root=self.root / "local", tunnel={"name": "farmbot"})
        self.assertEqual(tunnel_arguments(named, cloudflared="/usr/bin/cloudflared"),
                         ["/usr/bin/cloudflared", "tunnel", "--no-autoupdate", "run", "farmbot"])

    def test_install_writes_both_agents_and_never_runs_launchctl(self):
        target = self.root / "LaunchAgents"
        written = install(self.config, target, python="/usr/bin/python3", cloudflared="/usr/bin/cloudflared")
        self.assertEqual(sorted(written), sorted(AGENTS.values()))
        serve = plistlib.loads((target / f"{AGENTS['serve']}.plist").read_bytes())
        self.assertEqual(serve["ProgramArguments"], ["/usr/bin/python3", "-u", "-m", "agent.service", "serve"])
        self.assertEqual(serve["WorkingDirectory"], str(ROOT))
        self.assertTrue((self.config.local_root / "agent" / "logs").is_dir())

    def test_both_agents_carry_a_path_because_launchd_supplies_almost_none(self):
        target = self.root / "LaunchAgents"
        install(self.config, target, python="/usr/bin/python3", cloudflared="/usr/bin/cloudflared",
                path="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin")
        for label in AGENTS.values():
            job = plistlib.loads((target / f"{label}.plist").read_bytes())
            # launchd gives a job only /usr/bin:/bin:/usr/sbin:/sbin, which holds no codex, gh or cloudflared,
            # so a worker spawned under it would die at Popen with FileNotFoundError.
            self.assertEqual(job["EnvironmentVariables"]["PATH"], "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin")
            self.assertIn("HOME", job["EnvironmentVariables"])

    def test_explicit_profiles_install_distinct_labels_without_replacing_legacy_agents(self):
        target = self.root / "LaunchAgents"
        legacy = install(self.config, target)
        original = {label: path.read_bytes() for label, path in legacy.items()}
        for environment, instance in (("development", "mac-dev"),
                                      ("production", "mac-dev"),
                                      ("development", "second-dev")):
            config = replace(self.config, environment=environment, instance_id=instance,
                             local_root=self.root / environment / instance,
                             expected_app_user_id="app", expected_organization_id="org")
            expected = {f"com.kuaiwa.farmbot.{environment}.{instance}.{kind}" for kind in AGENTS}
            with patch("subprocess.run", side_effect=AssertionError("must not start jobs")), \
                    patch("subprocess.Popen", side_effect=AssertionError("must not start jobs")):
                written = install(config, target)
            self.assertEqual(set(written), expected)
            for label, path in written.items():
                self.assertEqual(path.name, f"{label}.plist")
                job = plistlib.loads(path.read_bytes())
                self.assertEqual(job["Label"], label)
                self.assertEqual(Path(job["StandardErrorPath"]).parent,
                                 config.local_root / "agent" / "logs")
                self.assertEqual(Path(job["StandardErrorPath"]).name, f"{label}.err.log")
        self.assertEqual(len(list(target.glob("*.plist"))), 8)
        self.assertEqual({label: path.read_bytes() for label, path in legacy.items()}, original)

    def test_direct_install_uses_loaded_config_source_unless_explicitly_overridden(self):
        target = self.root / "LaunchAgents"
        self.config.source_path = self.root / "profile files 测试" / "selected.json"
        override = self.root / "override.json"
        for explicit, selected in ((None, self.config.source_path), (override, override)):
            with self.subTest(explicit=explicit):
                written = install(self.config, target, config_path=explicit)
                serve = plistlib.loads(written[AGENTS["serve"]].read_bytes())
                self.assertEqual(serve["ProgramArguments"][-2:], ["--config", str(selected.resolve())])

    def test_missing_tools_names_what_this_host_cannot_resolve(self):
        self.assertEqual(missing_tools(self.config, which=lambda name: f"/somewhere/{name}"), [])
        self.assertEqual(missing_tools(self.config, which=lambda name: None), ["codex", "cloudflared"])
        only_runtime = lambda name: None if name == "cloudflared" else f"/somewhere/{name}"
        self.assertEqual(missing_tools(self.config, which=only_runtime), ["cloudflared"])

    def test_the_installed_job_names_the_config_it_was_installed_with(self):
        target = self.root / "LaunchAgents"
        elsewhere = self.root / "elsewhere.json"
        elsewhere.write_text("{}", encoding="utf-8")
        install(self.config, target, python="/usr/bin/python3", cloudflared="/usr/bin/cloudflared",
                config_path=elsewhere)
        serve = plistlib.loads((target / f"{AGENTS['serve']}.plist").read_bytes())
        self.assertEqual(serve["ProgramArguments"][-2:], ["--config", str(elsewhere.resolve())])
        install(self.config, target, python="/usr/bin/python3", cloudflared="/usr/bin/cloudflared")
        plain = plistlib.loads((target / f"{AGENTS['serve']}.plist").read_bytes())
        self.assertNotIn("--config", plain["ProgramArguments"])
