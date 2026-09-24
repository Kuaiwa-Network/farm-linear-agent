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
                      {"hostnames": "farmbot-host.local"}, {"hostnames": ["Farmbot-host.local"]},
                      {"hostnames": ["a b"]}, {"hostnames": ["x"] * 17}, {"host": "0.0.0.0"},
                      # A dotless string, a non-string name and a 255-character name of valid labels.
                      {"hostnames": "localhost"}, {"hostnames": [1]}, {"hostnames": [".".join(["a" * 63] * 4)]}):
            with self.subTest(block=block):
                loaded = config(monitor=block)
                with self.assertRaises(ValueError):
                    monitor_settings(loaded)

    def test_the_monitor_port_is_never_the_receivers(self):
        # Absent, empty or populated, the block's effective port is compared, the 8780 default included.
        for loaded in (config(port=8780), config(port=8780, monitor={}),
                       config(port=8780, monitor={"bind": "0.0.0.0"})):
            with self.subTest(monitor=loaded.monitor), self.assertRaises(ValueError):
                monitor_settings(loaded)
        self.assertEqual(monitor_settings(config(port=8780, monitor={"port": 8781}))["port"], 8781)

    def test_an_invalid_block_never_stops_a_config_loading(self):
        # Serve's and the workers' view: they neither read nor validate the block, so only the monitor refuses it.
        self.assertEqual(config(monitor={"bind": "localhost"}).monitor, {"bind": "localhost"})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config 配置.json"
            path.write_text(json.dumps({"client_id": "c", "client_secret": "s", "webhook_secret": "w",
                                        "monitor": {"Bind": "0.0.0.0", "port": "8781"}}), encoding="utf-8")
            loaded = load_config(path, secure_permissions=False)
        self.assertIsInstance(loaded, Config)
        with self.assertRaises(ValueError):
            monitor_settings(loaded)

    def test_the_heartbeat_lives_in_the_state_root_beside_the_controller_lock(self):
        root = Path(tempfile.gettempdir()) / "farm root 状态"
        self.assertEqual(Paths(config(local_root=root)).heartbeat, root / "service-heartbeat.json")
