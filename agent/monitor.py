"""The office status monitor: a read-only page about one FarmBot instance (docs/operating-contract.md).

It runs beside `serve` on the same host, never inside it, so it can still report a service that has stopped
or wedged. It writes nothing, signals nothing, never takes the controller lock, and makes no request but the
receiver's /health on loopback.
"""
import http.client
import ipaddress
import json
import threading
import time
import urllib.error
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from .config import Paths, ROOT, load_config, monitor_settings
from .environment import check_ownership
from .heartbeat import read as read_heartbeat, source_revision
from .monitor_view import build_status
from .receiver import ExclusiveServer
from .skills import load_skills

STATIC = Path(__file__).resolve().parent / "monitor_static"
ASSETS = {"/": ("index.html", "text/html; charset=utf-8"),
          "/monitor.js": ("monitor.js", "text/javascript; charset=utf-8"),
          "/monitor.css": ("monitor.css", "text/css; charset=utf-8")}
SNAPSHOT_TTL = 2.0
HEALTH_TIMEOUT = 2.0
REQUEST_TIMEOUT = 5
HEADERS = (("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"),
           ("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "no-referrer"),
           ("X-Frame-Options", "DENY"), ("Cache-Control", "no-store"))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A 3xx is the probe's answer, never a hop: it surfaces as an HTTPError carrying that status."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# An empty ProxyHandler: a desktop proxy's environment variables must not carry a loopback probe elsewhere. No
# redirects either, so whatever holds the receiver port cannot send the probe past loopback /health.
_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def probe_health(port, *, timeout=HEALTH_TIMEOUT, now=None):
    """GET the receiver's /health on loopback. Only a 200 counts as answering, and that proves no more."""
    started = time.monotonic()
    result = {"ok": False, "status": None, "latency_ms": None, "error_type": None,
              "checked_at": time.time() if now is None else now}
    try:
        with _DIRECT.open(f"http://127.0.0.1:{port}/health", timeout=timeout) as response:
            result["status"] = response.status
    except urllib.error.HTTPError as exc:
        result["status"] = exc.code
        exc.close()
    except (OSError, http.client.HTTPException) as exc:
        reason = getattr(exc, "reason", None)
        result["error_type"] = type(reason if isinstance(reason, BaseException) else exc).__name__
    result["latency_ms"] = round((time.monotonic() - started) * 1000)
    result["ok"] = result["status"] == 200
    return result


def allowed_host(header, hostnames):
    """True for an IP literal, localhost or a configured name; False otherwise; None when there is no Host.

    DNS rebinding needs a hostname the attacker controls, so refusing unknown names is the whole defense."""
    if not header or not header.strip():
        return None
    host = header.strip().lower()
    if host.startswith("["):
        end = host.find("]")
        if end < 0:
            return False
        name = host[1:end]
    else:
        name = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    name = name.rstrip(".")
    if name == "localhost" or name in hostnames:
        return True
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return False
    return True


def make_monitor_server(config, *, port=None, clock=time.time):
    """A server for this config's monitor listener; `port` overrides it (tests pass 0)."""
    settings = monitor_settings(config)
    paths = Paths(config)
    assets = {route: ((STATIC / name).read_bytes(), kind) for route, (name, kind) in ASSETS.items()}
    instance = {"environment": config.environment, "instance_id": config.instance_id,
                "bot_name": config.expected_bot_name[:64], "host": str(config.host)[:64]}
    revision = source_revision(ROOT)
    renew_seconds = {name: skill.budget["renew_minutes"] * 60 for name, skill in load_skills(ROOT / "skills").items()}
    lock = threading.Lock()
    cache = {"at": None, "body": None, "failing_since": None}

    def status_body():
        # Serialized: however many teammates poll, the ledger is read at most once per SNAPSHOT_TTL.
        with lock:
            now = clock()
            if cache["body"] is not None and 0 <= now - cache["at"] < SNAPSHOT_TTL:
                return cache["body"]
            health = probe_health(config.port, timeout=HEALTH_TIMEOUT, now=now)
            if health["ok"]:
                cache["failing_since"] = None
            elif cache["failing_since"] is None:
                cache["failing_since"] = now
            document = build_status(paths.ledger, heartbeat=read_heartbeat(paths.heartbeat, now=now),
                                    health=health, instance=instance, monitor_revision=revision, now=now,
                                    failing_since=cache["failing_since"], renew_seconds=renew_seconds)
            cache["body"] = json.dumps(document, ensure_ascii=False, allow_nan=False).encode("utf-8")
            # Aged from the build's end, not its start: a probe that times out lasts as long as SNAPSHOT_TTL, and
            # the requests that waited for this build must reuse it rather than each start another.
            cache["at"] = clock()
            return cache["body"]

    class Handler(BaseHTTPRequestHandler):
        timeout = REQUEST_TIMEOUT
        server_version = "FarmBotMonitor"
        sys_version = ""

        def log_message(self, *_args):
            pass

        def send(self, status, body, kind="text/plain; charset=utf-8", extra=()):
            self.send_response(status)
            for name, value in (*HEADERS, *extra):
                self.send_header(name, value)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_GET(self):
            allowed = allowed_host(self.headers.get("Host"), settings["hostnames"])
            if allowed is None:
                return self.send(400, b"missing host\n")
            if not allowed:
                return self.send(421, b"unknown host\n")
            path = self.path.split("?", 1)[0]
            if path == "/api/status":
                return self.send(200, status_body(), "application/json; charset=utf-8")
            if path in assets:
                body, kind = assets[path]
                return self.send(200, body, kind)
            return self.send(404, b"not found\n")

        do_HEAD = do_GET

        def refuse(self):
            self.send(405, b"method not allowed\n", extra=(("Allow", "GET, HEAD"),))

        do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_TRACE = do_CONNECT = refuse

        def send_error(self, code, message=None, explain=None):
            # The base class answers an unknown method or a malformed request itself, bypassing send() and with it
            # HEADERS; its 501 for an unknown method is the spec's 405. A request line too malformed to name a
            # version still gets an HTTP/1.0 reply, since an HTTP/0.9 one has no status line or headers at all.
            if self.request_version == "HTTP/0.9":
                self.request_version = "HTTP/1.0"
            if code == HTTPStatus.NOT_IMPLEMENTED:
                return self.refuse()
            self.send(code, f"{self.responses.get(code, ('error',))[0].lower()}\n".encode())

    return ExclusiveServer((settings["bind"], settings["port"] if port is None else port), Handler)


def run(config_path=None):
    """The `monitor` command: exit 1 with one JSON line when it cannot start, else serve until interrupted."""
    try:
        config = load_config(config_path, secure_permissions=False)
        check_ownership(config)
        server = make_monitor_server(config)
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        print(json.dumps({"event": "monitor_failed", "error": type(exc).__name__, "detail": str(exc)[:300]},
                         ensure_ascii=False), flush=True)
        return 1
    settings = monitor_settings(config)
    print(json.dumps({"event": "monitor_ready", "listen": f"http://{settings['bind']}:{server.server_address[1]}",
                      "instance": config.instance_id}), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
