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
    # key -> callable, run *after* that request has been answered. This is how a test makes the world move
    # between two of collect()'s reads: a checkout that shifts under the probe, or an Editor that starts
    # compiling while the probe runs. Nothing else in the suite can express "and then, mid-observation".
    hooks = {}
    sse = False

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        method = body.get("method")
        params = body.get("params") or {}
        # `seen` is what makes "refused before any request" and "the gate stopped the probe" checkable:
        # without it those tests assert an exception that a hundred other bugs would also raise.
        key = (f"tools/call:{params.get('name')}" if method == "tools/call" else
               f"resources/read:{params.get('uri')}" if method == "resources/read" else method)
        self.seen.append(key)
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
        data = (f"event: message\ndata: {payload}\n\n".encode() if self.sse else payload.encode())
        # The reply is composed from the world as it was, then the hook moves the world, and only then is
        # the reply released. Running the hook after the write instead races the client, which is already
        # free to send its next request: the first draft of this lost that race and saw no change at all.
        hook = self.hooks.get(key)
        if hook:
            hook()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream" if self.sse else "application/json")
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
        Handler.hooks = {}
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


def enrich(sample):
    """The MCP server's own `_enrich_advice_and_staleness`, mirrored clause for clause.

    `staleness` and `advice` are not plugin fields. The plugin never sends them: the server computes both
    from `observed_at_unix_ms` at read time, on every read, and staples them onto the snapshot
    (mcpforunityserver services/resources/editor_state.py:178-216). So they are a *function* of the
    timestamp, and a fixture that sets them independently describes a snapshot the real server cannot emit.

    This fixture used to do exactly that — a hardcoded `is_stale: False` and `ready_for_tools: True`
    alongside whatever timestamp a test asked for. Two costs. The tests named for the live defect asserted
    an impossible payload: a two-minute-old sample that the server had nonetheless called fresh. And
    `ready()` could have kept `state['staleness']['is_stale'] is False` or `advice.ready_for_tools` with
    the whole suite still green, because the fixture satisfied both by construction — a tighter copy
    (`age_ms > 2000`) of the very bound Task 13 removed, re-armed at a quarter of the threshold.
    """
    now_ms = int(time.time() * 1000)
    try:
        observed_ms = int(sample["observed_at_unix_ms"])
    except Exception:   # the server's own fallback: an absent or unusable field is read as *now* (:180-184)
        observed_ms = now_ms
    age_ms = max(0, now_ms - observed_ms)   # a clock-skewed Editor reporting the future clamps to 0 (:186)
    is_stale = age_ms > 2000                # the server's conservative default, not the plugin's (:188)
    compilation = sample.get("compilation") or {}
    assets = sample.get("assets") if isinstance(sample.get("assets"), dict) else {}
    refresh = assets.get("refresh") or {}
    # Same reasons in the same order the server appends them (:195-205); `stale_status` is last.
    blocking = [reason for reason, blocked in (
        ("compiling", compilation.get("is_compiling") is True),
        ("domain_reload", compilation.get("is_domain_reload_pending") is True),
        ("running_tests", (sample.get("tests") or {}).get("is_running") is True),
        ("asset_refresh", refresh.get("is_refresh_in_progress") is True),
        ("stale_status", is_stale)) if blocked]
    ready_for_tools = len(blocking) == 0
    sample["advice"] = {"ready_for_tools": ready_for_tools, "blocking_reasons": blocking,
                        "recommended_retry_after_ms": 0 if ready_for_tools else 500,
                        "recommended_next_action": "none" if ready_for_tools else "retry_later"}
    sample["staleness"] = {"age_ms": age_ms, "is_stale": is_stale}
    return sample


def state(**overrides):
    """One `unity-mcp/editor_state@2` sample, with `staleness` and `advice` derived exactly as the server
    derives them — see `enrich`. Neither is accepted as an override: they are the server's arithmetic on
    `observed_at_unix_ms`, so letting a test dictate them is how a snapshot no Editor can produce gets into
    the suite, and how a gate clause reading them stays pinned by nothing.

    `play_mode` is nested under `editor`, where the live plugin puts it (live-editor.json). The brief's
    fixture had it at the top level, which would have made the ready() clause unreachable: the fresh-idle
    test would fail on the missing `editor` key and the playing test would pass for that same reason
    instead of for the flag it names.
    """
    if {"staleness", "advice"} & set(overrides):
        raise TypeError("staleness and advice are derived from observed_at_unix_ms, not inputs")
    play_mode = overrides.pop("play_mode", {"is_playing": False, "is_paused": False, "is_changing": False})
    base = {"schema_version": "unity-mcp/editor_state@2", "observed_at_unix_ms": 0,
            # Required on the wire and always emitted (editor_state.py:242 setdefaults it), so it belongs
            # in the base shape even though nothing reads it yet. The deferred sequence-based liveness
            # signal in the Task 13 write-up needs a fixture that can express it.
            "sequence": 3,
            "unity": {"instance_id": INSTANCE},
            "editor": {"is_focused": False, "play_mode": play_mode},
            "compilation": {"is_compiling": False, "is_domain_reload_pending": False},
            # `refresh` is the fourth reason the server can block on, and the installed plugin hardcodes it
            # false (EditorStateCache.cs:473-478); it is here so the mirror above reads the live shape.
            "assets": {"is_updating": False, "refresh": {"is_refresh_in_progress": False}},
            "tests": {"is_running": False}}
    base.update(overrides)
    return enrich(base)


class ReadyTests(unittest.TestCase):
    # Every way the snapshot can positively report busy, one flag at a time, so that no single clause of the
    # gate can be deleted with the suite still green. `CollectTests` reuses the list against the collector.
    BUSY = (
        ("playing", {"play_mode": {"is_playing": True, "is_paused": False, "is_changing": False}}),
        ("paused in play mode", {"play_mode": {"is_playing": False, "is_paused": True, "is_changing": False}}),
        ("entering or leaving play mode",
         {"play_mode": {"is_playing": False, "is_paused": False, "is_changing": True}}),
        ("compiling", {"compilation": {"is_compiling": True, "is_domain_reload_pending": False}}),
        ("reloading the domain", {"compilation": {"is_compiling": False, "is_domain_reload_pending": True}}),
        ("importing assets", {"assets": {"is_updating": True}}),
        ("running tests", {"tests": {"is_running": True}}),
    )

    def test_an_explicitly_idle_editor_is_ready_however_old_its_snapshot_is(self):
        """`observed_at_unix_ms` is when the state last *changed*, not a heartbeat: EditorStateCache.OnUpdate
        returns before BuildSnapshot whenever nothing it tracks has moved (EditorStateCache.cs:346-351) and
        nothing a client can call reaches the private ForceUpdate. So the more reliably idle the Editor, the
        older its sample grows; the live rehearsal measured 117 seconds with `sequence` frozen at 3 while
        `execute_code` worked throughout, and the ten-second bound this replaces turned that into a
        probe-stage hold. 1970 is the extreme, and it is not a default: a server filling in a field the
        plugin omitted stamps it with *now* (editor_state.py:241), so an epoch timestamp can only be an
        Editor whose tracked state has genuinely not moved since.

        Past two seconds the server's own verdict on this sample is "not ready", and the gate admits it
        anyway, on the flags. What pins that is `enrich` DERIVING `staleness`/`advice` from the timestamp
        the way the server does -- re-adding either clause to `ready()` fails this test with the two
        assertions below deleted, and passes against a fixture that hardcodes the pair however loudly the
        assertions are written. They document the derivation; they do not substitute for it."""
        now_ms = int(time.time() * 1000)
        for age_ms in (0, 500, 10_001, 117_000, now_ms):
            with self.subTest(age_ms=age_ms):
                sample = state(observed_at_unix_ms=now_ms - age_ms)
                self.assertIs(sample["staleness"]["is_stale"], age_ms > 2000)
                self.assertIs(sample["advice"]["ready_for_tools"], age_ms <= 2000)
                self.assertTrue(ready(sample, INSTANCE))

    def test_a_snapshot_that_reports_any_kind_of_busy_is_refused_fresh_or_frozen(self):
        """The safety property that had to survive losing the clock. It survives because every flag here has
        a change trigger that rebuilds the snapshot — the compilation edge bypasses even the one-second
        throttle (EditorStateCache.cs:295-300), playModeStateChanged and beforeAssemblyReload call ForceUpdate
        directly (:260, :268-273), and is_updating, tests-running and the activity phase are all in the
        hasChanges set (:336-344) — so an old sample saying idle means nothing has become busy since."""
        now_ms = int(time.time() * 1000)
        for name, busy in self.BUSY:
            for age_ms in (0, 117_000):
                with self.subTest(busy=name, age_ms=age_ms):
                    self.assertFalse(ready(state(observed_at_unix_ms=now_ms - age_ms, **busy), INSTANCE))

    def test_a_missing_flag_a_foreign_instance_and_an_unusable_timestamp_never_pass(self):
        """Dropping the freshness bound must not drop the well-formedness checks around it. A missing boolean
        still fails, because `unknown` beats a guess; the instance comparison is the only thing standing
        between a worker and somebody else's Editor; and `observed_at_unix_ms` is still required to be a
        finite number, because `collect` records it as evidence in the ledger row."""
        now_ms = int(time.time() * 1000)
        self.assertFalse(ready(state(observed_at_unix_ms=now_ms), "other@ffffffffffffffff"))
        self.assertFalse(ready(state(observed_at_unix_ms=now_ms), None))
        self.assertFalse(ready(state(observed_at_unix_ms=now_ms, schema_version="unity-mcp/editor_state@3"),
                               INSTANCE))
        for absent in ({"play_mode": {"is_playing": False, "is_paused": False}},
                       {"compilation": {"is_domain_reload_pending": False}},
                       {"assets": {}},
                       {"tests": {"is_running": None}}):
            with self.subTest(absent=absent):
                self.assertFalse(ready(state(observed_at_unix_ms=now_ms, **absent), INSTANCE))
        for unusable in (None, True, "1700000000000", float("nan"), float("inf")):
            with self.subTest(observed_at_unix_ms=unusable):
                self.assertFalse(ready(state(observed_at_unix_ms=unusable), INSTANCE))

    def test_a_fresh_idle_editor_of_the_expected_instance_is_ready(self):
        """A sample taken half a second ago is still ready. Removing the upper bound removed the lower one
        too, so a clock-skewed Editor reporting the future is no longer refused for that alone — the server's
        own staleness arithmetic never saw it either, since it clamps a negative age to zero
        (editor_state.py:186) — so the fixture's mirror of it calls a minute-in-the-future sample fresh,
        exactly as the live server would."""
        now_ms = int(time.time() * 1000)
        self.assertTrue(ready(state(observed_at_unix_ms=now_ms - 500), INSTANCE))
        skewed = state(observed_at_unix_ms=now_ms + 60_000)
        self.assertEqual(skewed["staleness"], {"age_ms": 0, "is_stale": False})
        self.assertTrue(ready(skewed, INSTANCE))

    def test_the_aggregate_is_match_only_when_every_check_matches_and_unknown_is_never_match(self):
        self.assertEqual(aggregate({"a": "match", "b": "match"}), "match")
        self.assertEqual(aggregate({"a": "match", "b": "unknown"}), "unknown")
        self.assertEqual(aggregate({"a": "mismatch", "b": "unknown"}), "mismatch")

    def test_quiet_ignores_the_instance_and_play_mode_but_not_the_four_busy_flags(self):
        """The refresh wait cannot compare instances — hearing from the instance is what it is waiting for —
        and must not mind play mode, because what it is waiting out is a compile or an import."""
        self.assertTrue(quiet(state(observed_at_unix_ms=0, unity={"instance_id": "anything"},
                                    play_mode={"is_playing": True, "is_paused": False, "is_changing": False})))
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

    def test_an_idle_editor_whose_snapshot_stopped_changing_minutes_ago_still_certifies(self):
        """Task 13, and the thing that halted Step 4 of the live rehearsal. The Editor was demonstrably idle
        and demonstrably usable — every busy flag False, `execute_code` answering throughout — and the gate
        refused it purely because `observed_at_unix_ms` had not moved for 117 seconds. Every check came back
        `unknown`, so the aggregate was `unknown`, so the pool held the slot: a permanent hold on exactly the
        Editors that are behaving best.

        The payload below is the whole snapshot the live server emits at that age, not just its timestamp:
        `is_stale` True and `ready_for_tools` False, blocked on `stale_status` alone. So this is the gate
        overruling the server's advice on the evidence of the flags and the probe — which is the decision
        Task 13 actually made, and which the old fixture could not express."""
        frozen = state(observed_at_unix_ms=int(time.time() * 1000) - 117_000)
        self.assertIs(frozen["staleness"]["is_stale"], True)
        self.assertIs(frozen["advice"]["ready_for_tools"], False)
        self.assertEqual(frozen["advice"]["blocking_reasons"], ["stale_status"])
        Handler.replies["mcpforunity://editor/state"] = frozen
        result = self.run_collect()
        self.assertEqual(result["aggregate"], "match", result["checks"])
        self.assertEqual(result["checks"]["editor_ready"], "match")
        self.assertIn("tools/call:execute_code", Handler.seen)

    def test_an_editor_that_reports_busy_never_reaches_the_probe_and_aggregates_to_unknown(self):
        """`unknown` is not `match`, so this holds the slot rather than producing evidence about an Editor
        that was still compiling. Running the probe anyway is the failure that matters here. Each sample is
        frozen 117 seconds back as well — the age the rehearsal measured — so the refusal is the busy flag
        and can never again be the clock."""
        frozen = int(time.time() * 1000) - 117_000
        for name, busy in ReadyTests.BUSY:
            with self.subTest(busy=name):
                Handler.seen = []
                Handler.replies["mcpforunity://editor/state"] = state(observed_at_unix_ms=frozen, **busy)
                result = self.run_collect()
                self.assertEqual(result["aggregate"], "unknown")
                self.assertEqual(set(result["checks"].values()), {"unknown"})
                self.assertNotIn("tools/call:execute_code", Handler.seen)

    def test_the_probes_own_live_flags_are_what_certify_editor_ready(self):
        """With the freshness bound gone this is the whole of the live evidence, so every conjunct of it has
        to be load-bearing. `execute_code` runs on the main thread, which is the thread a compile, an import
        or a play-mode transition occupies, so these five cannot be stale by construction — and a main thread
        wedged badly enough to make the snapshot meaningless cannot answer here at all, which is what the old
        ten-second bound was really catching."""
        for key in ("isPlaying", "isPlayingOrWillChangePlaymode", "isCompiling", "isUpdating"):
            with self.subTest(probe_flag=key):
                Handler.replies["tools/call:execute_code"] = {"result": {**self.probe, key: True}}
                result = self.run_collect()
                self.assertEqual(result["checks"]["editor_ready"], "unknown")
                self.assertEqual(result["aggregate"], "unknown")
        with self.subTest(probe_flag="scene.isDirty"):
            Handler.replies["tools/call:execute_code"] = {
                "result": {**self.probe, "scene": {**self.probe["scene"], "isDirty": True}}}
            self.assertEqual(self.run_collect()["checks"]["editor_ready"], "unknown")

    def test_a_checkout_that_was_already_dirty_fails_source_clean(self):
        snapshot = source_snapshot(str(self.repository))
        self.assertEqual((snapshot["commit_sha"], snapshot["dirty"]), (self.head, []))
        (self.repository / "Assets" / "b.cs").write_text("// b", encoding="utf-8")
        result = self.run_collect()
        self.assertEqual(result["checks"]["source_clean"], "mismatch")
        self.assertEqual(result["aggregate"], "mismatch")

    def test_a_checkout_that_moves_while_the_probe_runs_fails_source_stable(self):
        """The two snapshots exist for exactly this, and nothing else in the suite could see it: the dirty
        test above makes both snapshots *equally* dirty, so only source_clean flips and source_stable could
        be hardcoded to 'match' unnoticed. Here the slot is committed clean on both sides and still moves
        underneath the probe — which is what a second FarmBot, or an operator running git in the folder,
        would do to an interactive run."""
        def commit_during_the_probe():
            (self.repository / "Assets" / "c.cs").write_text("// c", encoding="utf-8")
            git("add", ".", cwd=self.repository)
            git("commit", "-qm", "moved under the probe", cwd=self.repository)

        Handler.hooks["tools/call:execute_code"] = commit_during_the_probe
        result = self.run_collect()
        self.assertNotEqual(result["source_before"]["commit_sha"], result["source_after"]["commit_sha"])
        # Both snapshots are clean, so source_clean cannot see this and source_stable is the only witness.
        self.assertEqual(result["checks"]["source_clean"], "match")
        self.assertEqual(result["checks"]["source_stable"], "mismatch")
        self.assertEqual(result["aggregate"], "mismatch")

    def test_an_editor_that_stops_being_the_same_idle_editor_mid_probe_fails_editor_ready(self):
        """`collect` re-reads editor/state after the probe and compares it with the read before, so that a
        probe which finished against an Editor that had started compiling — or against a different Editor —
        is not trusted. Only the `before` read was covered: the not-ready test above never reaches the
        second one, so both halves of that clause could be deleted with the suite still green."""
        fresh = state(observed_at_unix_ms=int(time.time() * 1000))
        for name, after in (
                ("it started compiling",
                 state(observed_at_unix_ms=int(time.time() * 1000),
                       compilation={"is_compiling": True, "is_domain_reload_pending": False})),
                ("it is a different Editor under the same instance id",
                 state(observed_at_unix_ms=int(time.time() * 1000),
                       unity={"instance_id": INSTANCE, "unity_version": "2022.3.63f1"}))):
            with self.subTest(after=name):
                Handler.replies["mcpforunity://editor/state"] = fresh
                Handler.hooks["resources/read:mcpforunity://editor/state"] = (
                    lambda after=after: Handler.replies.__setitem__("mcpforunity://editor/state", after))
                result = self.run_collect()
                self.assertEqual(result["checks"]["editor_ready"], "unknown")
                self.assertEqual(result["aggregate"], "unknown")
                # Everything the probe itself reported still matched; only the re-read refused it.
                self.assertEqual(result["checks"]["loaded_assemblies"], "match")
                self.assertEqual(result["checks"]["source_commit"], "match")


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
        # Every call goes through `_client`, which pins the session, so an Editor this folder can be matched
        # to has to be connected for any of them. The discovery tests below override this reply.
        Handler.replies.update({
            "mcpforunity://instances": {"instances": [{"id": INSTANCE,
                                                       "dataPath": str(self.folder / "Assets")}]},
            "mcpforunity://editor/state": state(observed_at_unix_ms=int(time.time() * 1000)),
            "tools/call:read_console": {"success": True, "data": []},
        })

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

    def test_every_editor_call_pins_the_instance_even_before_the_slot_row_records_it(self):
        """The pin in `_client` had no coverage: deleting it left the whole suite green, because collect()
        does its own selection and nothing else asked. It also used to no-op exactly when it mattered —
        `switch()` writes slots.instance only after the console read, so on a fresh slot's first interactive
        switch these three calls all run with a NULL row, and the compile gate would talk to whatever the
        server routed to by default."""
        calls = {"refresh": lambda slot: self.identity.refresh(slot),
                 "wait_quiet": lambda slot: self.identity.wait_quiet(slot, 5),
                 "console_errors_since": lambda slot: self.identity.console_errors_since(slot, "deadbeef")}
        for name, call in calls.items():
            for recorded in (INSTANCE, None):
                with self.subTest(call=name, row_instance=recorded):
                    Handler.seen = []
                    call({**self.slot, "instance": recorded})
                    self.assertIn("tools/call:set_active_instance", Handler.seen)

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
        # Unity answered; this reader did not understand it. That is a FarmBot gap, and recover-slot cannot
        # fix it, which is the whole distinction `fault` carries.
        self.assertEqual((caught.exception.stage, caught.exception.fault), ("probe", "farmbot"))

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
