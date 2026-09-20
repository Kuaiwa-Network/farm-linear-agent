"""The identity probe: the loopback MCP client, the readiness gate, the aggregate and the collector.

The MCP client is exercised against a real loopback `http.server` in a thread, which is this suite's rule
for transports: real sockets, never a mock of urllib. Nothing here starts Unity, reads /Applications or
touches the real slot; the Editor's replies are shaped exactly like the ones the spikes captured from the
live plugin (FarmTestAgent/reports/2026-09-16-unity-readiness/evidence/live-editor.json).
"""
import hashlib
import json
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from agent.identity import aggregate, collect, quiet, ready, source_snapshot
from agent.slots import SlotError, UnityIdentity
from agent.unity_mcp import UnityMcp

INSTANCE = "slot-1@0123456789abcdef"
MVID = "00000000-0000-4000-8000-0000000000%02d"


class Handler(BaseHTTPRequestHandler):
    replies = {}
    seen = []
    sse = False

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        method = body.get("method")
        params = body.get("params") or {}
        # `seen` is what makes "refused before any request" and "the gate stopped the probe" checkable:
        # without it those tests assert an exception that a hundred other bugs would also raise.
        self.seen.append(f"tools/call:{params.get('name')}" if method == "tools/call" else
                         f"resources/read:{params.get('uri')}" if method == "resources/read" else method)
        if method == "initialize":
            result = {"protocolVersion": "2024-11-05", "capabilities": {},
                      "serverInfo": {"name": "u", "version": "1"}}
        elif method == "notifications/initialized":
            self.send_response(202); self.end_headers(); return
        elif method == "resources/read":
            uri = params["uri"]
            result = {"contents": [{"type": "text", "text": json.dumps(self.replies[uri])}]}
        else:
            reply = self.replies.get(f"{method}:{params.get('name')}", self.replies.get(method, {}))
            result = {"content": [{"type": "text", "text": json.dumps(reply)}]}
        payload = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result})
        if self.sse:
            data = f"event: message\ndata: {payload}\n\n".encode()
            self.send_response(200); self.send_header("Content-Type", "text/event-stream")
        else:
            data = payload.encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Mcp-Session-Id", "session-1")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class Loopback(unittest.TestCase):
    """One real MCP server per test. Both cleanups are required: under `-W error` the socket a shut-down
    but unclosed ThreadingHTTPServer leaves behind becomes a ResourceWarning and fails the suite."""

    def setUp(self):
        Handler.replies = {"mcpforunity://instances": {"instances": [{"id": INSTANCE}]},
                           "mcpforunity://project/info": {"projectRoot": "/e/slot-1",
                                                          "platform": "StandaloneOSX"}}
        Handler.seen = []
        Handler.sse = False
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)
        self.endpoint = f"http://127.0.0.1:{self.server.server_address[1]}/mcp"


class McpTests(Loopback):
    def test_only_a_loopback_mcp_endpoint_is_accepted(self):
        for bad in ("https://127.0.0.1:8080/mcp", "http://10.0.0.5:8080/mcp", "http://127.0.0.1:8080/",
                    "http://user@127.0.0.1:8080/mcp", "http://127.0.0.1:8080/mcp?a=1",
                    "http://127.0.0.1/mcp", "http://127.0.0.1:8080/mcp#f"):
            with self.assertRaises(ValueError, msg=bad):
                UnityMcp(bad)
        self.assertIsNotNone(UnityMcp(self.endpoint))

    def test_a_resource_reads_the_same_through_json_and_through_one_sse_message(self):
        client = UnityMcp(self.endpoint)
        self.assertEqual(client.read_resource("mcpforunity://instances")["instances"][0]["id"], INSTANCE)
        Handler.sse = True
        other = UnityMcp(self.endpoint)
        self.assertEqual(other.read_resource("mcpforunity://project/info")["platform"], "StandaloneOSX")

    def test_an_unlisted_resource_uri_is_refused_before_any_request(self):
        client = UnityMcp(self.endpoint)
        with self.assertRaises(ValueError):
            client.read_resource("mcpforunity://something/else")
        # The brief asserted only the exception, which an allow-list applied *after* the handshake would
        # satisfy just as well. The refusal has to cost nothing on the wire, so the server is the witness.
        self.assertEqual(Handler.seen, [])

    def test_selecting_an_instance_that_is_not_connected_names_the_ones_that_are(self):
        client = UnityMcp(self.endpoint)
        with self.assertRaises(ValueError) as caught:
            client.select_instance("slot-9@ffffffffffffffff")
        self.assertIn(INSTANCE, str(caught.exception))
        self.assertNotIn("tools/call:set_active_instance", Handler.seen)
        self.assertEqual(client.select_instance(INSTANCE), {})
        self.assertIn("tools/call:set_active_instance", Handler.seen)


def state(**overrides):
    """One `unity-mcp/editor_state@2` sample.

    `play_mode` is nested under `editor`, where the live plugin puts it (live-editor.json). The brief's
    fixture had it at the top level, which would have made the ready() clause unreachable: the fresh-idle
    test would fail on the missing `editor` key and the playing test would pass for that same reason
    instead of for the flag it names.
    """
    play_mode = overrides.pop("play_mode", {"is_playing": False, "is_paused": False, "is_changing": False})
    base = {"schema_version": "unity-mcp/editor_state@2", "observed_at_unix_ms": 0,
            "unity": {"instance_id": INSTANCE}, "staleness": {"is_stale": False},
            "advice": {"ready_for_tools": True},
            "editor": {"is_focused": False, "play_mode": play_mode},
            "compilation": {"is_compiling": False, "is_domain_reload_pending": False},
            "assets": {"is_updating": False}, "tests": {"is_running": False}}
    base.update(overrides)
    return base


class ReadyTests(unittest.TestCase):
    def setUp(self):
        self.now_ms = 1_700_000_000_000

    def test_a_fresh_idle_editor_of_the_expected_instance_is_ready(self):
        self.assertTrue(ready(state(observed_at_unix_ms=self.now_ms - 500), INSTANCE, now_ms=self.now_ms))

    def test_a_stale_a_future_a_playing_and_a_compiling_editor_are_all_refused(self):
        self.assertFalse(ready(state(observed_at_unix_ms=self.now_ms - 10_001), INSTANCE, now_ms=self.now_ms))
        self.assertFalse(ready(state(observed_at_unix_ms=self.now_ms + 1), INSTANCE, now_ms=self.now_ms))
        self.assertFalse(ready(state(observed_at_unix_ms=self.now_ms,
                                     play_mode={"is_playing": True, "is_paused": False, "is_changing": False}),
                               INSTANCE, now_ms=self.now_ms))
        self.assertFalse(ready(state(observed_at_unix_ms=self.now_ms,
                                     compilation={"is_compiling": True, "is_domain_reload_pending": False}),
                               INSTANCE, now_ms=self.now_ms))
        self.assertFalse(ready(state(observed_at_unix_ms=self.now_ms), "other@ffffffffffffffff",
                               now_ms=self.now_ms))

    def test_the_aggregate_is_match_only_when_every_check_matches_and_unknown_is_never_match(self):
        self.assertEqual(aggregate({"a": "match", "b": "match"}), "match")
        self.assertEqual(aggregate({"a": "match", "b": "unknown"}), "unknown")
        self.assertEqual(aggregate({"a": "mismatch", "b": "unknown"}), "mismatch")

    def test_quiet_ignores_the_instance_and_the_clock_but_not_the_four_busy_flags(self):
        """The refresh wait cannot compare instances — hearing from the instance is what it is waiting for —
        and cannot bound staleness, because a compiling Editor stops publishing fresh samples."""
        self.assertTrue(quiet(state(observed_at_unix_ms=0, unity={"instance_id": "anything"})))
        for busy in ({"compilation": {"is_compiling": True, "is_domain_reload_pending": False}},
                     {"compilation": {"is_compiling": False, "is_domain_reload_pending": True}},
                     {"assets": {"is_updating": True}},
                     {"tests": {"is_running": True}}):
            self.assertFalse(quiet(state(**busy)), busy)


def git(*args, cwd):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=str(cwd),
                          check=True, capture_output=True, text=True).stdout


class CollectTests(Loopback):
    """The collector against the same real loopback server. The brief left `collect` untested altogether,
    and it is where all four of this task's changes actually live: the expected assembly set, the expected
    build target, the presence-not-uniqueness instance rule and the aggregate."""

    ASSEMBLIES = ("HotUpdate", "AOTScripts", "Nova.Runtime", "MCPForUnity.Editor")

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repository = Path(self.tmp.name) / "slot-1"
        (self.repository / "Assets").mkdir(parents=True)
        git("init", "-q", "-b", "main", ".", cwd=self.repository)
        (self.repository / "Assets" / "a.cs").write_text("// a", encoding="utf-8")
        git("add", ".", cwd=self.repository)
        git("commit", "-qm", "init", cwd=self.repository)
        self.head = git("rev-parse", "HEAD", cwd=self.repository).strip()
        self.probe = {
            "isPlaying": False, "isPlayingOrWillChangePlaymode": False, "isCompiling": False,
            "isUpdating": False, "unityVersion": "2022.3.62f3", "platform": "OSXEditor",
            "buildTarget": "StandaloneOSX", "dataPath": str(self.repository / "Assets"),
            "scene": {"name": "StartScene", "path": "Assets/StartScene.unity", "isDirty": False},
            # The C# filter is gone, so a live probe returns every loaded assembly; `collect` is what
            # narrows it. mscorlib and UnityEngine stand in for the ~200 the Editor really loads.
            "assemblies": [{"name": name, "moduleMvid": MVID % index, "hasGameTestDriver": False}
                           for index, name in enumerate(self.ASSEMBLIES + ("mscorlib", "UnityEngine"))],
        }
        Handler.replies.update({
            "mcpforunity://custom-tools": {"tools": []},
            "mcpforunity://instances": {"instances": [{"id": "other@aaaaaaaaaaaaaaaa"}, {"id": INSTANCE}]},
            "mcpforunity://project/info": {"projectRoot": str(self.repository), "platform": "StandaloneOSX"},
            "mcpforunity://editor/state": state(observed_at_unix_ms=int(time.time() * 1000)),
            "tools/call:set_active_instance": {},
            "tools/call:execute_code": {"result": self.probe},
        })

    def run_collect(self, **overrides):
        expected = {"build_target": "StandaloneOSX", "commit_sha": self.head,
                    "assemblies": self.ASSEMBLIES}
        expected.update(overrides)
        return collect(UnityMcp(self.endpoint), repository=str(self.repository), instance=INSTANCE,
                       expected=expected, probe_source="// probe")

    def test_a_matching_editor_aggregates_to_match_beside_a_second_connected_instance(self):
        """FarmQA demanded exactly one connected Editor; a second slot — or the operator's own Editor —
        would make that rule fail. Presence is what is required now, and the selection still happens."""
        result = self.run_collect()
        self.assertEqual(result["aggregate"], "match", result["checks"])
        self.assertEqual(set(result["checks"].values()), {"match"})
        self.assertIn("tools/call:set_active_instance", Handler.seen)
        # project/info is read after the selection, so it describes the instance that was pinned.
        self.assertLess(Handler.seen.index("tools/call:set_active_instance"),
                        Handler.seen.index("resources/read:mcpforunity://project/info"))
        self.assertEqual([a["name"] for a in result["editor"]["assemblies"]], list(self.ASSEMBLIES))
        self.assertEqual(result["source_before"]["commit_sha"], self.head)
        self.assertIsInstance(result["observed_at"], float)

    def test_the_expected_assembly_set_is_configuration_and_not_baked_into_the_csharp_probe(self):
        """The whole point of deleting the probe's `continue`: another project, or the Windows slot, is a
        configuration change. With the filter still in C# this test could not exist."""
        self.assertEqual(self.run_collect(assemblies=("HotUpdate",))["aggregate"], "match")
        narrowed = self.run_collect(assemblies=("HotUpdate", "Nova.Runtime"))
        self.assertEqual([a["name"] for a in narrowed["editor"]["assemblies"]], ["HotUpdate", "Nova.Runtime"])
        missing = self.run_collect(assemblies=("HotUpdate", "Farm.Android"))
        self.assertEqual(missing["checks"]["loaded_assemblies"], "mismatch")
        self.assertEqual(missing["aggregate"], "mismatch")

    def test_a_commit_or_a_build_target_the_editor_does_not_hold_is_a_mismatch_not_an_error(self):
        moved = self.run_collect(commit_sha="0" * 40)
        self.assertEqual(moved["checks"]["source_commit"], "mismatch")
        self.assertEqual(moved["aggregate"], "mismatch")
        self.assertNotIn("error_type", moved)
        android = self.run_collect(build_target="Android")
        self.assertEqual(android["checks"]["build_target"], "mismatch")

    def test_an_editor_that_is_not_ready_never_reaches_the_probe_and_aggregates_to_unknown(self):
        """`unknown` is not `match`, so this holds the slot rather than producing evidence about an Editor
        that was still compiling. Running the probe anyway is the failure that matters here."""
        Handler.replies["mcpforunity://editor/state"] = state(observed_at_unix_ms=0)  # 1970: hopelessly stale
        result = self.run_collect()
        self.assertEqual(result["aggregate"], "unknown")
        self.assertEqual(set(result["checks"].values()), {"unknown"})
        self.assertNotIn("tools/call:execute_code", Handler.seen)

    def test_a_dirty_or_moving_checkout_is_caught_by_the_two_source_snapshots(self):
        snapshot = source_snapshot(str(self.repository))
        self.assertEqual((snapshot["commit_sha"], snapshot["dirty"]), (self.head, []))
        (self.repository / "Assets" / "b.cs").write_text("// b", encoding="utf-8")
        result = self.run_collect()
        self.assertEqual(result["checks"]["source_clean"], "mismatch")
        self.assertEqual(result["aggregate"], "mismatch")


class UnityIdentityTests(Loopback):
    """The pool's real collaborator. Only the parts that need no Unity process are exercised here; the
    Editor life cycle itself is covered by the pool's FakeMcp in tests/test_slots.py."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name) / "slot-1"
        (self.folder / "Assets").mkdir(parents=True)
        self.slot = {"slot_id": "unity_slot:1", "folder": str(self.folder), "instance": None,
                     "mcp_address": self.endpoint}
        self.identity = UnityIdentity(Path(self.tmp.name) / "probe.cs.txt")

    def test_the_instance_is_read_from_the_server_and_matched_by_the_folder_it_reports(self):
        Handler.replies["mcpforunity://instances"] = {"instances": [
            {"id": "other@aaaaaaaaaaaaaaaa", "dataPath": "/somewhere/else/Assets"},
            {"id": INSTANCE, "dataPath": str(self.folder / "Assets")}]}
        self.assertEqual(self.identity.discover_instance(self.slot), INSTANCE)

    def test_an_instance_listing_without_any_path_falls_back_to_the_measured_hash_rule(self):
        """The installed server's HTTP branch lists id/name/hash/unity_version/connected_at/session_id and
        no path — `path` is "stdio only" by its own docstring — so the brief's path match alone would never
        fire and every interactive switch would die at the editor stage. The fallback is the rule Task 0
        Step 5 verified and ProjectIdentityUtility.ComputeProjectHash confirms: sha1(Application.dataPath)
        truncated to 16 lowercase hex, dataPath being "<folder>/Assets" with no trailing slash. It is still
        only ever used to pick out an id the server actually listed, so a moved or symlinked folder fails
        loudly instead of addressing the wrong Editor."""
        # Both spellings of the folder: the Editor hashes the -projectPath it was given, but on this Mac a
        # temp folder reaches it through /var -> /private/var, and a slot path could as easily.
        for folder in (self.folder, self.folder.resolve()):
            digest = hashlib.sha1(f"{folder}/Assets".encode("utf-8")).hexdigest()[:16]
            with self.subTest(folder=str(folder)):
                Handler.replies["mcpforunity://instances"] = {"instances": [
                    {"id": "other@aaaaaaaaaaaaaaaa", "name": "other", "hash": "aaaaaaaaaaaaaaaa"},
                    {"id": f"slot-1@{digest}", "name": "slot-1", "hash": digest}]}
                self.assertEqual(self.identity.discover_instance(self.slot), f"slot-1@{digest}")

    def test_no_editor_on_this_folder_is_an_editor_stage_slot_error_naming_the_folder(self):
        Handler.replies["mcpforunity://instances"] = {"instances": [
            {"id": "other@aaaaaaaaaaaaaaaa", "dataPath": "/somewhere/else/Assets",
             "hash": "aaaaaaaaaaaaaaaa"}]}
        with self.assertRaises(SlotError) as caught:
            self.identity.discover_instance(self.slot)
        self.assertEqual((caught.exception.stage, caught.exception.fault), ("editor", "external"))
        self.assertIn(str(self.folder), str(caught.exception))

    def test_the_console_is_read_in_every_shape_the_installed_plugin_actually_returns(self):
        """ReadConsole.cs returns the entries as a bare list under `data` when `count` is given and paging
        is not, and as `items` under a paging envelope when it is; an entry is a string in the default
        'plain' format and a dict in 'json'. The brief read `result["lines"]`, which is an AttributeError on
        the first of those — raised at a call site SlotPool.switch does not wrap."""
        compile_error = "Assets/HotUpdate/A.cs(3,5): error CS1002: ; expected"
        # `data` is the C# SuccessResponse payload, which is exactly how a bare list reaches the client.
        for name, data in (("bare list of plain strings", [compile_error, "just a Debug.LogError"]),
                           ("paging envelope of json entries",
                            {"cursor": 0, "total": 1, "items": [{"type": "Error", "message": compile_error}]}),
                           ("empty console", [])):
            with self.subTest(shape=name):
                Handler.replies["tools/call:read_console"] = {"success": True, "data": data}
                found = self.identity.console_errors_since(self.slot, "deadbeef")
                self.assertEqual(found, [compile_error] if data else [])

    def test_a_console_payload_in_no_known_shape_holds_the_slot_instead_of_certifying_it_clean(self):
        Handler.replies["tools/call:read_console"] = {"success": True,
                                                      "data": {"total": 0, "surprise": "a future server"}}
        with self.assertRaises(SlotError) as caught:
            self.identity.console_errors_since(self.slot, "deadbeef")
        self.assertEqual(caught.exception.stage, "probe")

    def test_the_server_pidfile_is_derived_from_the_slots_own_port_and_a_missing_one_is_no_error(self):
        """A second slot answers on another port; reaping the hardcoded 8080 pidfile would kill the *first*
        slot's server and leave this one's holding its port. Nothing is signalled here: the pidfile this
        call would read does not exist, and the 8080 one that does must be left strictly alone."""
        runstate = self.folder / "Library" / "MCPForUnity" / "RunState"
        runstate.mkdir(parents=True)
        # A pid far above any pid_max, so that a regression which reads this file signals nothing real and
        # is caught by the unlink that follows instead.
        (runstate / "mcp_http_8080.pid").write_text("1073741824", encoding="utf-8")
        self.identity.reap_server({**self.slot, "mcp_address": "http://127.0.0.1:8123/mcp"})
        self.assertTrue((runstate / "mcp_http_8080.pid").exists())
        (runstate / "mcp_http_8123.pid").write_text("not a pid", encoding="utf-8")
        self.identity.reap_server({**self.slot, "mcp_address": "http://127.0.0.1:8123/mcp"})
