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
from agent.monitor_view import ATTENTION_CODES, DISPLAY_STATES, OUTCOMES, SCHEMA_VERSION, VERDICTS, WORKER_STATES
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

    def block(self, opening):
        """monitor.js from `opening`, which ends with a brace, to the brace that closes it.

        No string or comment in the script holds a brace, and each template's `${` closes, so counting braces
        finds the match."""
        script = self.text("monitor.js")
        self.assertEqual(script.count(opening), 1, opening)
        start = script.index(opening) + len(opening) - 1
        depth = 0
        for end in range(start, len(script)):
            depth += {"{": 1, "}": -1}.get(script[end], 0)
            if depth == 0:
                return script[start:end + 1]
        self.fail(f"{opening} is never closed")

    def assert_labelled(self, name, codes):
        """Each code is a key of monitor.js's own `name` map, not merely a key somewhere in the script."""
        keys = set(re.findall(r"(?:^|[{,])\s*([a-z_][a-z0-9_]*):", self.block(f"const {name} = {{"), re.M))
        self.assertTrue(codes)
        for code in codes:
            with self.subTest(code=code):
                self.assertIn(code, keys)

    def test_the_page_loads_only_its_own_assets_and_has_no_inline_code(self):
        html = self.text("index.html")
        self.assertIn('<html lang="zh-CN">', html)
        self.assertEqual(re.findall(r"<script\b([^>]*)>(.*?)</script>", html, re.S), [(' src="/monitor.js" defer', "")])
        self.assertEqual(re.findall(r'<link\b[^>]*href="([^"]+)"', html), ["/monitor.css"])
        self.assertNotRegex(html, r"\sstyle=|\son[a-z]+=|https?://")

    def test_the_script_never_turns_data_into_markup(self):
        script = self.text("monitor.js")
        # The spec's list as written (Verification), with `eval` as a word, since a plain substring would also
        # refuse words such as "evaluate"; then this page's own additions.
        for forbidden in (r"innerHTML", r"outerHTML", r"insertAdjacentHTML", r"\beval\b", r"Function\(",
                          r"document\.write", r"eval\(", r"new Function", r"setAttribute", r"\.style\."):
            with self.subTest(forbidden=forbidden):
                self.assertNotRegex(script, forbidden)

    def test_every_verdict_has_a_label(self):
        self.assert_labelled("VERDICT", VERDICTS)

    def test_every_display_state_has_a_label(self):
        self.assert_labelled("STATE", DISPLAY_STATES)

    def test_every_skill_has_a_label(self):
        self.assert_labelled("SKILL", load_skills(Path(__file__).resolve().parents[1] / "skills"))

    def test_every_outcome_has_a_label(self):
        self.assert_labelled("OUTCOME", OUTCOMES)

    def test_every_loop_has_a_label(self):
        self.assert_labelled("LOOP", LOOPS)

    def test_every_attention_code_has_a_label(self):
        self.assert_labelled("ATTENTION", ATTENTION_CODES)

    def test_every_worker_state_has_a_label(self):
        # lease_expired and renewal_overdue are attention codes as well: only WORKER's own keys count here.
        self.assert_labelled("WORKER", WORKER_STATES)

    def test_every_slot_state_has_a_label(self):
        self.assert_labelled("SLOT", Ledger.SLOT_STATES)

    def test_a_held_slot_words_its_recovery_alike_in_its_row_and_its_attention_line(self):
        script = self.text("monitor.js")
        shared = self.block("function recoveryText(recovery) {")
        # Every recovery text, each written once and only in the shared function, so no second copy can drift.
        for text in ("等待修复", "修复中（第 ${attempts}/${most} 次）", "自动修复已放弃", "，需要人工处理"):
            with self.subTest(text=text):
                self.assertIn(text, shared)
                self.assertEqual(script.count(text), 1)
        # The controller has given up automatic repair, so the page must not claim it is still repairing.
        self.assertRegex(shared, r'if \(recovery\.state === "exhausted"\) return `自动修复已放弃\$\{')
        # The slot's row: the urgent style for an exhausted recovery, the muted one otherwise, in the shared words.
        self.assertIn('cell(recovery.state === "exhausted" ? "side urgent" : "side muted", recoveryText(recovery))',
                      self.block("function recoveryCell(recovery) {"))
        # The attention line reads `<slot> 已隔离，<the row's words>`, so an exhausted slot's line reads
        # 已隔离，自动修复已放弃… when the page runs. Without a slot record or its recovery, it keeps its own words.
        held = self.block("slot_held: (a, work) => {")
        self.assertIn("? `${a.subject} 已隔离，${recoveryText(slot.recovery)}`", held)
        self.assertIn(": `${a.subject} 已隔离，控制器正在自动修复", held)

    def test_only_a_status_document_of_the_views_version_is_accepted_and_kept(self):
        script = self.text("monitor.js")
        # The one version the page reads is the one agent/monitor_view.py emits.
        self.assertIn(f"const SCHEMA_VERSION = {SCHEMA_VERSION};", script)
        poll = self.block("async function poll() {")
        gate = "if (version !== SCHEMA_VERSION) {"
        # A body that is not an object has no version, and any other body fails the poll at the gate.
        self.assertIn('const version = next !== null && typeof next === "object" ? next.schema_version : undefined;',
                      poll)
        self.assertIn(gate, poll)
        self.assertIn("throw new Error(", self.block(gate))
        # Only a body past the gate, whose ledger section has been read, is kept.
        order = [gate, "const ledgerOk = next.ledger.ok;", "doc = next;", "if (ledgerOk) lastGood = next;"]
        for step in order:
            self.assertEqual(poll.count(step), 1, step)
        self.assertEqual([poll.index(step) for step in order], sorted(poll.index(step) for step in order))
        # Nothing else becomes the page's data: each is assigned at its declaration and past the gate, nowhere else.
        self.assertEqual(re.findall(r"\b(doc|lastGood) = (\w+);", script),
                         [("doc", "null"), ("lastGood", "null"), ("doc", "next"), ("lastGood", "next")])

    def test_another_document_version_asks_for_a_refresh_and_the_page_never_reloads_itself(self):
        script = self.text("monitor.js")
        # Another version is a number: a monitor upgraded under the open tab. Other bodies are a lost connection.
        self.assertIn('otherVersion = typeof version === "number";', self.block("if (version !== SCHEMA_VERSION) {"))
        # Each completed poll decides afresh, before the render: a success clears it, as any other failure does.
        poll = self.block("async function poll() {")
        self.assertIn("let otherVersion = false;", poll)
        self.assertIn("upgraded = otherVersion;", poll)
        self.assertLess(poll.index("upgraded = otherVersion;"), poll.index("render();"))
        banner = self.block("function renderBanner() {")
        self.assertIn('if (upgraded) messages.push("监控已更新，请刷新页面。");', banner)
        self.assertIn("else if (failingSince !== null) messages.push(`与监控的连接已中断", banner)
        self.assertEqual(script.count("监控已更新，请刷新页面。"), 1)
        # Refreshing is the viewer's decision: the script never navigates or reloads.
        self.assertNotRegex(script, r"\blocation\b|\.reload\(|history\.go")

    def test_a_render_that_throws_shows_a_page_error_instead_of_a_stale_verdict(self):
        poll = self.block("async function poll() {")
        # The render's exception is still logged, the error state is shown, and the next poll is still scheduled.
        self.assertEqual(poll.count("render();"), 1)
        rendered = poll[poll.index("render();"):]
        caught = rendered[rendered.index("} catch (error) {"):rendered.index("} finally {")]
        self.assertIn("console.error(error);", caught)
        self.assertIn("showRenderError();", caught)
        self.assertLess(caught.index("console.error(error);"), caught.index("showRenderError();"))
        self.assertIn("setTimeout(poll, POLL_MS);", rendered[rendered.index("} finally {"):])
        shown = self.block("function showRenderError() {")
        # The pill in the bad tone, the tab title and the dimmed content; and no tick writes the pill again.
        for write in ('verdict.textContent = "页面显示出错";', 'verdict.classList.add("bad");',
                      'document.title = `页面显示出错 · ${byId("title").textContent}`;',
                      'byId("content").classList.add("stale");', "clocks = [];"):
            with self.subTest(write=write):
                self.assertIn(write, shown)
        # Every other verdict tone is removed, so the pill keeps no trace of the verdict the render left.
        tones = set(re.findall(r'\["[^"]+", "([a-z]+)"\]', self.block("const VERDICT = {"))) - {"bad"}
        removed = re.search(r"verdict\.classList\.remove\(([^)]*)\);", shown)
        self.assertIsNotNone(removed)
        self.assertEqual(set(re.findall(r'"([a-z]+)"', removed.group(1))), tones)
        # Only writes that cannot throw: no document read, no rendering helper, no markup.
        self.assertNotRegex(shown, r"\bdoc\b|\blastGood\b|timed\(|replaceChildren|append\(|className|\bel\(")


def fake_receiver(status=200, delay=0.0, headers=()):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.server.requests.append(self.path)
            time.sleep(delay)
            body = b'{"status": "FarmBot ready"}'
            self.send_response(status)
            for name, value in headers:
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.requests = []
    # A short poll, so each cleanup's shutdown does not wait out serve_forever's default half second.
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
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
        for header in ("evil.example", "127.0.0.1.evil.example:8780", "farmbot-host.local.evil.example", "[::1"):
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

    def test_a_redirect_is_the_answer_and_is_never_followed(self):
        elsewhere = fake_receiver()
        self.addCleanup(elsewhere.server_close)
        self.addCleanup(elsewhere.shutdown)
        # A Location the probe could follow, and one urllib cannot even parse: the probe reads neither.
        for location in (f"http://127.0.0.1:{elsewhere.server_address[1]}/health", "http://[::1"):
            for code in (302, 301, 303, 307, 308):
                with self.subTest(code=code, location=location):
                    redirected = probe_health(self.serve(status=code, headers=(("Location", location),)))
                    self.assertEqual((redirected["ok"], redirected["status"]), (False, code))
        self.assertEqual(elsewhere.requests, [])

    def test_any_other_failure_is_not_answering(self):
        # Whatever holds the receiver port is untrusted: an error the probe does not foresee must not escape it.
        with patch.object(monitor._DIRECT, "open", side_effect=ValueError("boom")):
            failed = probe_health(closed_port())
        self.assertEqual((failed["ok"], failed["status"], failed["error_type"]), (False, None, "ValueError"))


class MonitorServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "state 状态"
        self.now = [1_000_000.0]
        self.config = config(local_root=self.root, port=closed_port(), monitor={"hostnames": ["farmbot-host.local"]})
        Ledger(Paths(self.config).ledger).close()
        self.server = self.start(self.config, clock=lambda: self.now[0])

    def start(self, loaded, **kwargs):
        server = make_monitor_server(loaded, port=0, **kwargs)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def request(self, method="GET", path="/", host="127.0.0.1", server=None):
        connection = http.client.HTTPConnection("127.0.0.1", (server or self.server).server_address[1], timeout=10)
        self.addCleanup(connection.close)
        connection.putrequest(method, path, skip_host=True)
        if host is not None:
            connection.putheader("Host", host)
        connection.endheaders()
        response = connection.getresponse()
        return response, response.read()

    def exchange(self, data):
        """Send raw bytes and read until the server closes; return the reply's status line and header lines."""
        with socket.create_connection(self.server.server_address, timeout=10) as connection:
            connection.sendall(data)
            reply = b""
            while chunk := connection.recv(65536):
                reply += chunk
        status, *headers = reply.partition(b"\r\n\r\n")[0].decode("latin-1").split("\r\n")
        return status, headers

    def test_the_page_and_its_assets_are_served_with_security_headers(self):
        # The spec's policy, written out rather than read from monitor.HEADERS, so any weakening fails here.
        policy = ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; "
                  "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        for path, kind in (("/", "text/html"), ("/monitor.js", "text/javascript"), ("/monitor.css", "text/css")):
            with self.subTest(path=path):
                response, body = self.request(path=path)
                self.assertEqual(response.status, 200)
                self.assertTrue(response.getheader("Content-Type").startswith(kind))
                self.assertEqual(response.getheader("Content-Security-Policy"), policy)
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

    def test_an_unknown_method_gets_405_with_the_security_headers(self):
        response, _ = self.request("FOO", "/api/status")
        self.assertEqual((response.status, response.getheader("Allow")), (405, "GET, HEAD"))
        for name, value in monitor.HEADERS:
            self.assertEqual(response.getheader(name), value)

    def test_a_malformed_request_line_gets_400_with_the_security_headers(self):
        # The first line names no version, so the base class alone would answer in HTTP/0.9: a bare body with no
        # status line or headers. The second names one but has a word too many.
        for line in (b"NONSENSE\r\n", b"GET / extra HTTP/1.1\r\n"):
            with self.subTest(line=line):
                status, headers = self.exchange(line)
                self.assertTrue(status.startswith("HTTP/1.0 400 "), status)
                for name, value in monitor.HEADERS:
                    self.assertIn(f"{name}: {value}", headers)

    def test_a_versionless_request_gets_a_versioned_reply_with_the_security_headers(self):
        # A valid HTTP/0.9 GET never reaches send_error. The status is not asserted: 3.13 ignores headers after a
        # versionless line and answers 400 for the missing Host, while an interpreter that reads them answers 200.
        status, headers = self.exchange(b"GET /api/status\r\nHost: 127.0.0.1\r\n\r\n")
        self.assertTrue(status.startswith("HTTP/1.0 "), status)
        for name, value in monitor.HEADERS:
            self.assertIn(f"{name}: {value}", headers)

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

    def test_requests_that_waited_for_a_slow_build_reuse_it(self):
        # In production the /health timeout equals SNAPSHOT_TTL, so probing a hung receiver makes every build last
        # the whole interval. Aged from its start, a build would be stale before a waiting request could read it.
        receiver = fake_receiver(delay=1.0)
        self.addCleanup(receiver.server_close)
        self.addCleanup(receiver.shutdown)
        server = self.start(config(local_root=self.root, port=receiver.server_address[1]))  # the real clock
        calls, replies = [], []
        real = monitor.build_status

        def counting(*args, **kwargs):
            calls.append(kwargs["now"])
            return real(*args, **kwargs)

        def fetch():
            response, body = self.request(path="/api/status", server=server)
            replies.append((response.status, json.loads(body)["service"]["health"]["error_type"]))

        with patch.multiple("agent.monitor", HEALTH_TIMEOUT=0.3, SNAPSHOT_TTL=0.3, build_status=counting):
            threads = [threading.Thread(target=fetch) for _ in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(20)
        # The probe read the shortened timeout at call time and gave up on the receiver, which answers after 1 s.
        self.assertEqual((len(calls), replies), (1, [(200, "TimeoutError")] * 4))

    def test_a_failed_build_answers_500_and_prints_one_line_per_run_of_failures(self):
        calls, failing = [], [True]
        real = monitor.build_status

        def flaky(*args, **kwargs):
            calls.append(kwargs["now"])
            if failing[0]:
                raise TypeError("a document the view cannot build")
            return real(*args, **kwargs)

        def lines():
            return [json.loads(line) for line in out.getvalue().splitlines()]

        def status():
            return self.request(path="/api/status")[0].status

        failed = {"event": "status_failed", "error": "TypeError"}
        out, err = io.StringIO(), io.StringIO()
        # The request threads print, and sys.stdout is process-wide, so their lines land here too.
        with patch("agent.monitor.build_status", side_effect=flaky), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            response, body = self.request(path="/api/status")
            self.assertEqual((response.status, body), (500, b"status unavailable\n"))
            self.assertEqual(response.getheader("Content-Type"), "text/plain; charset=utf-8")
            for name, value in monitor.HEADERS:
                self.assertEqual(response.getheader(name), value)
            self.assertEqual(lines(), [failed])
            # Within the same snapshot interval, the failure's 500 is answered again without another build.
            self.assertEqual(status(), 500)
            self.assertEqual((len(calls), lines()), (1, [failed]))
            # The next interval builds again, and fails in the same run of failures, so quietly.
            self.now[0] += monitor.SNAPSHOT_TTL + 0.5
            self.assertEqual(status(), 500)
            self.assertEqual((len(calls), lines()), (2, [failed]))
            failing[0] = False
            self.now[0] += monitor.SNAPSHOT_TTL + 0.5
            self.assertEqual(status(), 200)
            failing[0] = True
            self.now[0] += monitor.SNAPSHOT_TTL + 0.5
            self.assertEqual(status(), 500)
            # Nor is the last good snapshot served within the failure's interval: the failure replaced it.
            self.assertEqual(status(), 500)
        # The recovery ended the first run of failures, so the second prints its own line, and only one.
        self.assertEqual((len(calls), lines()), (4, [failed, failed]))
        self.assertEqual(err.getvalue(), "")  # and no traceback for any of them

    def test_concurrent_requests_during_a_failure_streak_build_once_per_interval(self):
        # However many teammates poll, a failing build reads the ledger at most once per interval, as a good one does.
        calls = []

        def failing(*args, **kwargs):
            calls.append(kwargs["now"])
            raise TypeError("a document the view cannot build")

        def burst():
            statuses = []
            threads = [threading.Thread(target=lambda: statuses.append(self.request(path="/api/status")[0].status))
                       for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(20)
            return statuses

        out = io.StringIO()
        with patch("agent.monitor.build_status", side_effect=failing), contextlib.redirect_stdout(out):
            self.assertEqual(burst(), [500] * 8)
            self.assertEqual(len(calls), 1)
            self.now[0] += monitor.SNAPSHOT_TTL + 0.5
            self.assertEqual(burst(), [500] * 8)
        self.assertEqual(len(calls), 2)
        # One run of failures, however long and however many requests: one line.
        self.assertEqual([json.loads(line) for line in out.getvalue().splitlines()],
                         [{"event": "status_failed", "error": "TypeError"}])

    def test_an_unwritable_log_still_answers_500(self):
        closed = io.StringIO()
        closed.close()  # printing to it raises
        with patch("agent.monitor.build_status", side_effect=TypeError("boom")), contextlib.redirect_stdout(closed):
            response, body = self.request(path="/api/status")
        self.assertEqual((response.status, body), (500, b"status unavailable\n"))

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
        # Every case here must refuse. One that regressed would bind its port and serve forever, hanging the suite;
        # serving fails the test at once instead.
        serving = AssertionError("run() started serving instead of refusing")
        with contextlib.redirect_stdout(out), patch.object(monitor.ExclusiveServer, "serve_forever",
                                                           side_effect=serving):
            code = run(path)
        lines = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(len(lines), 1, lines)
        return code, lines

    def test_refusals_exit_one_with_one_json_line(self):
        # An unreadable config: a missing file, then one that is not JSON.
        broken = self.root / "broken 损坏.json"
        broken.write_text("{not json", encoding="utf-8")
        for path, error in ((self.root / "missing 缺失.json", "FileNotFoundError"), (broken, "JSONDecodeError")):
            with self.subTest(path=path.name):
                code, lines = self.run_monitor(path)
                self.assertEqual((code, lines[0]["event"], lines[0]["error"]), (1, "monitor_failed", error))
        code, lines = self.run_monitor(self.write_config(monitor={"bind": "localhost"}))
        self.assertEqual((code, lines[0]["event"], lines[0]["error"]), (1, "monitor_failed", "ValueError"))
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

    def test_an_error_no_check_names_is_still_one_json_line(self):
        # A slot entry with neither folder nor id makes the ownership check's path validation raise KeyError.
        code, lines = self.run_monitor(self.write_config(environment="development", instance_id="dev-mac",
                                                         expected_bot_name="TestBot", expected_app_user_id="app",
                                                         expected_organization_id="org", slots=[{}]))
        self.assertEqual((code, lines[0]["event"], lines[0]["error"]), (1, "monitor_failed", "KeyError"))

    def test_a_monitor_that_would_take_the_receivers_port_refuses_to_start(self):
        for changes in ({"port": 8780}, {"port": 8780, "monitor": {}}):
            with self.subTest(changes=changes):
                code, lines = self.run_monitor(self.write_config(**changes))
                self.assertEqual((code, lines[0]["event"], lines[0]["error"]), (1, "monitor_failed", "ValueError"))

    def test_the_service_cli_dispatches_monitor(self):
        with patch("agent.monitor.run", return_value=0) as monitor_run:
            self.assertEqual(main(["monitor", "--config", "selected.json"]), 0)
        monitor_run.assert_called_once_with("selected.json")
