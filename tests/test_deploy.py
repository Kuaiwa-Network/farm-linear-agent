"""launchd agents: the property lists FarmBot writes, and what installing them touches (spec §17)."""
import plistlib
import tempfile
import unittest
from pathlib import Path

from agent.config import Config
from agent.deploy import AGENTS, install, plist, tunnel_arguments

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
