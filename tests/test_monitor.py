"""The office status monitor: its configuration, its HTTP surface and its read-only guarantees."""
import json
import tempfile
import unittest
from pathlib import Path

from agent.config import Config, Paths, load_config, monitor_settings


def config(**changes):
    return Config(**({"client_id": "c", "client_secret": "s", "webhook_secret": "w", "port": 8765} | changes))


class MonitorConfigTests(unittest.TestCase):
    def test_defaults_keep_the_monitor_on_loopback(self):
        self.assertEqual(monitor_settings(config()), {"bind": "127.0.0.1", "port": 8780, "hostnames": ()})

    def test_a_lan_block_loads_from_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config 配置.json"
            path.write_text(json.dumps({"client_id": "c", "client_secret": "s", "webhook_secret": "w",
                                        "monitor": {"bind": "0.0.0.0", "port": 8781,
                                                    "hostnames": ["farmbot-host.local"]}}), encoding="utf-8")
            loaded = load_config(path, secure_permissions=False)
        self.assertEqual(monitor_settings(loaded),
                         {"bind": "0.0.0.0", "port": 8781, "hostnames": ("farmbot-host.local",)})

    def test_invalid_blocks_are_refused(self):
        for block in ([], {"bind": "localhost"}, {"bind": "::"}, {"bind": "127.000.0.1"}, {"bind": 127},
                      {"port": 8765}, {"port": 0}, {"port": 70000}, {"port": "8780"}, {"port": True},
                      {"hostnames": "farmbot.local"}, {"hostnames": ["Farmbot.local"]}, {"hostnames": ["a b"]},
                      {"hostnames": ["x"] * 17}, {"host": "0.0.0.0"},
                      # A dotless string, a non-string name and a 255-character name of valid labels.
                      {"hostnames": "localhost"}, {"hostnames": [1]}, {"hostnames": [".".join(["a" * 63] * 4)]}):
            with self.subTest(block=block), self.assertRaises(ValueError):
                config(monitor=block)

    def test_only_a_present_block_is_compared_with_the_receiver_port(self):
        # A config without a block loads whatever its receiver port, since serve and workers never read the
        # block; a block that omits its port is still checked with the default.
        self.assertEqual(monitor_settings(config(port=8780))["port"], 8780)
        self.assertEqual(monitor_settings(config(port=8780, monitor={}))["port"], 8780)
        with self.assertRaises(ValueError):
            config(port=8780, monitor={"bind": "0.0.0.0"})

    def test_the_heartbeat_lives_in_the_state_root_beside_the_controller_lock(self):
        root = Path(tempfile.gettempdir()) / "farm root 状态"
        self.assertEqual(Paths(config(local_root=root)).heartbeat, root / "service-heartbeat.json")
