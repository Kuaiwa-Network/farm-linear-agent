"""The office status monitor: its configuration, its HTTP surface and its read-only guarantees."""
import contextlib
import http.client
import io
import json
import os
import re
import socket
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from agent import monitor
from agent.config import Config, Paths, load_config, monitor_settings
from agent.heartbeat import LOOPS
from agent.ledger import Ledger
from agent.monitor import allowed_host, make_monitor_server, probe_health, run
from agent.monitor_view import ATTENTION_CODES, DISPLAY_STATES, OUTCOMES, VERDICTS, WORKER_STATES
from agent.service import main
from agent.skills import load_skills


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


class PageTests(unittest.TestCase):
    STATIC = Path(__file__).resolve().parents[1] / "agent" / "monitor_static"

    def text(self, name):
        return (self.STATIC / name).read_text(encoding="utf-8")

    def test_the_page_loads_only_its_own_assets_and_has_no_inline_code(self):
        html = self.text("index.html")
        self.assertIn('<html lang="zh-CN">', html)
        self.assertEqual(re.findall(r"<script\b([^>]*)>(.*?)</script>", html, re.S), [(' src="/monitor.js" defer', "")])
        self.assertEqual(re.findall(r'<link\b[^>]*href="([^"]+)"', html), ["/monitor.css"])
        self.assertNotRegex(html, r"\sstyle=|\son[a-z]+=|https?://")

    def test_the_script_never_turns_data_into_markup(self):
        script = self.text("monitor.js")
        for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(",
                          "new Function", "setAttribute", ".style."):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, script)

    def test_every_code_the_status_view_emits_has_a_label(self):
        script = self.text("monitor.js")
        for code in (*VERDICTS, *DISPLAY_STATES, *OUTCOMES, *ATTENTION_CODES, *LOOPS, *Ledger.SLOT_STATES,
                     *WORKER_STATES):
            with self.subTest(code=code):
                self.assertRegex(script, rf"\b{code}:")

    def test_an_exhausted_slot_recovery_asks_for_a_person(self):
        # The controller has given up automatic repair, so the page must not claim it is still repairing.
        script = self.text("monitor.js")
        self.assertIn('"exhausted"', script)
        self.assertIn("自动修复已放弃", script)


def fake_receiver(status=200, delay=0.0):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            time.sleep(delay)
            body = b'{"status": "FarmBot ready"}'
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def closed_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class HostCheckTests(unittest.TestCase):
    def test_ip_literals_localhost_and_configured_names_are_allowed(self):
        for header in ("127.0.0.1:8780", "192.0.2.10", "LOCALHOST:8780", "localhost.", "[::1]:8780",
                       "farmbot-host.local:8780"):
            with self.subTest(header=header):
                self.assertTrue(allowed_host(header, ("farmbot-host.local",)))
        for header in ("evil.example", "127.0.0.1.nip.io:8780", "farmbot-host.local.evil.example", "[::1"):
            with self.subTest(header=header):
                self.assertFalse(allowed_host(header, ("farmbot-host.local",)))
        self.assertIsNone(allowed_host(None, ()))
        self.assertIsNone(allowed_host(" ", ()))


class ProbeHealthTests(unittest.TestCase):
    def serve(self, **kwargs):
        server = fake_receiver(**kwargs)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address[1]

    def test_only_a_200_counts_as_answering(self):
        self.assertTrue(probe_health(self.serve())["ok"])
        failing = probe_health(self.serve(status=500))
        self.assertEqual((failing["ok"], failing["status"]), (False, 500))
        refused = probe_health(closed_port())
        self.assertEqual((refused["ok"], refused["status"]), (False, None))
        # Windows retries a refused loopback connect and can reach the timeout first.
        self.assertIn(refused["error_type"], ("ConnectionRefusedError", "TimeoutError"))

    def test_a_hung_receiver_times_out(self):
        slow = probe_health(self.serve(delay=1.0), timeout=0.2)
        self.assertEqual((slow["ok"], slow["error_type"]), (False, "TimeoutError"))
        self.assertLess(slow["latency_ms"], 1000)

    def test_proxy_settings_cannot_intercept_the_probe(self):
        port = self.serve()
        proxy = f"http://127.0.0.1:{closed_port()}"
        with patch.dict(os.environ, {"http_proxy": proxy, "HTTP_PROXY": proxy, "no_proxy": "", "NO_PROXY": ""}):
            self.assertTrue(probe_health(port)["ok"])


class MonitorServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "state 状态"
        self.now = [1_000_000.0]
        self.config = config(local_root=self.root, port=closed_port(), monitor={"hostnames": ["farmbot-host.local"]})
        Ledger(Paths(self.config).ledger).close()
        self.server = make_monitor_server(self.config, port=0, clock=lambda: self.now[0])
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method="GET", path="/", host="127.0.0.1"):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
        self.addCleanup(connection.close)
        connection.putrequest(method, path, skip_host=True)
        if host is not None:
            connection.putheader("Host", host)
        connection.endheaders()
        response = connection.getresponse()
        return response, response.read()

    def test_the_page_and_its_assets_are_served_with_security_headers(self):
        for path, kind in (("/", "text/html"), ("/monitor.js", "text/javascript"), ("/monitor.css", "text/css")):
            with self.subTest(path=path):
                response, body = self.request(path=path)
                self.assertEqual(response.status, 200)
                self.assertTrue(response.getheader("Content-Type").startswith(kind))
                policy = response.getheader("Content-Security-Policy")
                self.assertIn("script-src 'self'", policy)
                self.assertIn("frame-ancestors 'none'", policy)
                for name, value in (("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "no-referrer"),
                                    ("X-Frame-Options", "DENY"), ("Cache-Control", "no-store")):
                    self.assertEqual(response.getheader(name), value)
                self.assertGreater(len(body), 0)

    def test_status_json_reports_the_instance_and_an_unresponsive_service(self):
        response, body = self.request(path="/api/status")
        document = json.loads(body)
        self.assertEqual(response.getheader("Content-Type"), "application/json; charset=utf-8")
        self.assertEqual(document["instance"]["bot_name"], "FarmBot")
        self.assertEqual((document["verdict"], document["verdict_since"]), ("unresponsive", 1_000_000.0))

    def test_head_matches_get_without_a_body(self):
        response, body = self.request("HEAD", "/")
        self.assertEqual((response.status, body), (200, b""))
        self.assertGreater(int(response.getheader("Content-Length")), 0)

    def test_unknown_paths_methods_and_hosts_are_refused(self):
        self.assertEqual(self.request(path="/api/status/../../config.json")[0].status, 404)
        self.assertEqual(self.request(path="/.git/config")[0].status, 404)
        for method in ("POST", "PUT", "DELETE", "PATCH", "OPTIONS"):
            with self.subTest(method=method):
                response, _ = self.request(method, "/api/status")
                self.assertEqual((response.status, response.getheader("Allow")), (405, "GET, HEAD"))
        self.assertEqual(self.request(host="evil.example")[0].status, 421)
        self.assertEqual(self.request(host="farmbot-host.local:8780")[0].status, 200)
        self.assertEqual(self.request(host=None)[0].status, 400)

    def test_the_status_is_built_at_most_once_per_snapshot_interval(self):
        calls = []
        real = monitor.build_status

        def counting(*args, **kwargs):
            calls.append(kwargs["now"])
            return real(*args, **kwargs)

        with patch("agent.monitor.build_status", side_effect=counting):
            threads = [threading.Thread(target=self.request, kwargs={"path": "/api/status"}) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(20)
            self.assertEqual(len(calls), 1)
            self.now[0] += monitor.SNAPSHOT_TTL + 0.5
            self.request(path="/api/status")
        self.assertEqual(len(calls), 2)

    def test_renewal_intervals_come_from_the_skill_manifests(self):
        calls = []
        real = monitor.build_status

        def recording(*args, **kwargs):
            calls.append(kwargs["renew_seconds"])
            return real(*args, **kwargs)

        with patch("agent.monitor.build_status", side_effect=recording):
            self.request(path="/api/status")
        skills = load_skills(Path(__file__).resolve().parents[1] / "skills")
        self.assertEqual(calls, [{name: skill.budget["renew_minutes"] * 60 for name, skill in skills.items()}])

    def test_unresponsive_stays_anchored_to_the_first_observed_failure(self):
        first = json.loads(self.request(path="/api/status")[1])
        self.now[0] += 10
        later = json.loads(self.request(path="/api/status")[1])
        self.assertEqual((first["verdict_since"], later["verdict_since"], later["generated_at"]),
                         (1_000_000.0, 1_000_000.0, 1_000_010.0))

    def test_serving_status_never_modifies_the_ledger_or_the_state_root(self):
        ledger = Paths(self.config).ledger
        before = (ledger.stat().st_mtime_ns, ledger.read_bytes())
        self.request(path="/api/status")
        self.assertEqual((ledger.stat().st_mtime_ns, ledger.read_bytes()), before)
        self.assertFalse(ledger.with_name(ledger.name + "-journal").exists())
        self.assertFalse(Paths(self.config).heartbeat.exists())
        self.assertFalse((self.root / ".controller.lock").exists())


class MonitorRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write_config(self, **changes):
        path = self.root / "config 配置.json"
        path.write_text(json.dumps({"client_id": "c", "client_secret": "s", "webhook_secret": "w", "port": 8765,
                                    "local_root": str(self.root / "state")} | changes), encoding="utf-8")
        return path

    def run_monitor(self, path):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = run(path)
        return code, [json.loads(line) for line in out.getvalue().splitlines()]

    def test_refusals_exit_one_with_one_json_line(self):
        code, lines = self.run_monitor(self.write_config(monitor={"port": 8765}))
        self.assertEqual((code, lines[0]["event"], lines[0]["error"]), (1, "monitor_failed", "ValueError"))
        with socket.socket() as busy:
            busy.bind(("127.0.0.1", 0))
            busy.listen()
            code, lines = self.run_monitor(self.write_config(monitor={"port": busy.getsockname()[1]}))
        self.assertEqual((code, lines[0]["event"]), (1, "monitor_failed"))
        (self.root / "state").mkdir()
        (self.root / "state" / "environment.json").write_text(json.dumps({"version": 1, "instance_id": "someone-else"}),
                                                              encoding="utf-8")
        code, lines = self.run_monitor(self.write_config(environment="development", instance_id="dev-mac",
                                                         expected_bot_name="TestBot", expected_app_user_id="app",
                                                         expected_organization_id="org"))
        self.assertEqual((code, lines[0]["event"], lines[0]["error"]), (1, "monitor_failed", "RuntimeError"))

    def test_a_monitor_that_would_take_the_receivers_port_refuses_to_start(self):
        for changes in ({"port": 8780}, {"port": 8780, "monitor": {}}):
            with self.subTest(changes=changes):
                code, lines = self.run_monitor(self.write_config(**changes))
                self.assertEqual((code, lines[0]["event"], lines[0]["error"]), (1, "monitor_failed", "ValueError"))

    def test_the_service_cli_dispatches_monitor(self):
        with patch("agent.monitor.run", return_value=0) as monitor_run:
            self.assertEqual(main(["monitor", "--config", "selected.json"]), 0)
        monitor_run.assert_called_once_with("selected.json")
