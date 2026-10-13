"""Persistent Editors: real local Git pins, fake Unity/MCP, isolated ledgers only."""
import json
from pathlib import Path
from uuid import uuid4

from agent.config import Config, load_config
from agent.dispatch import dispatch_message
from agent.resource_admission import ResourceAdmission
from agent.slots import SlotError, SlotPool, fixed_editor_count, slot_entry
from test_ledger import ISSUE, OTHER, SELECTED_AT, SKILLS, issue
from test_slots import FakeMcp, FakeUnity, SlotFixture


class FixedEditorPoolTests(SlotFixture):
    def fixed_pool(self, count=2, mcp=None, **kwargs):
        self.mcp = mcp or FakeMcp()
        self.unity = FakeUnity(total=1, passed=1)
        entries = [slot_entry({**self.entry, "id": f"unity_slot:{n}",
                    "folder": str(self.root / "编辑器 pool" / f"slot-{n}")}) for n in range(1, count + 1)]
        pool = SlotPool(self.ledger, self.trees, entries, host="test", editors_root=self.root / "editors",
                        keep_open=True, mcp=self.mcp, clock=lambda: self.now, sleep=self.advance,
                        state_dir=lambda item: self.root / "runs" / item,
                        editor_scan=lambda folder: None,
                        editor_pid=lambda folder: 4242 if str(folder) in self.mcp.open_folders else None,
                        run_unsandboxed=self.unity, **kwargs)
        self.addCleanup(pool.close)
        return pool

    def waiting(self, issue_id, commit, mode="batch"):
        self.ledger.observe_issue(issue(id=issue_id))
        session = f"session-{issue_id}"
        self.ledger.ensure_session(session, issue_id, True)
        item = self.ledger.create_work_item(issue_id=issue_id, session_id=session, skill="fix",
            target={"repository": "Farm-Client", "requested_ref": "main", "commit_sha": commit,
                    "server_environment": "test", "selected_at": SELECTED_AT})
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        self.ledger.await_resource(item["id"], token, "unity_slot", mode, skill=SKILLS["fix"])
        return item["id"]

    def test_two_and_three_editors_start_once_and_survive_idle_ticks_and_restart(self):
        pool = self.fixed_pool(3)
        pool.ensure()
        self.assertEqual([s["state"] for s in self.ledger.slots()], ["idle_open"] * 3)
        for _ in range(3):
            pool.tick()
        pool.ensure()  # existing processes survive a controller startup too
        self.assertEqual(len([c for c in self.mcp.calls if c[0] == "start"]), 3)
        self.assertFalse(any(c[0] == "terminate" for c in self.mcp.calls))
        self.assertEqual(self.unity.argv, [])

    def test_fifo_queue_reuses_two_editors_at_exact_candidate_commits_without_batch(self):
        first_sha, second_sha = self.commit("first"), self.commit("second")
        first, second = self.waiting(ISSUE, first_sha), self.waiting(OTHER, second_sha)
        third = self.waiting(str(uuid4()), second_sha)
        pool = self.fixed_pool()
        pool.ensure()
        for _ in range(3):
            pool.tick()
        reservations = self.ledger.reservations()
        self.assertEqual([r["state"] for r in reservations], ["active", "active", "queued"])
        self.assertEqual([r["mode"] for r in reservations], ["interactive"] * 3)
        for item, sha in ((first, first_sha), (second, second_sha)):
            r = self.ledger.active_reservation(item)
            self.assertEqual(self.trees.head(Path(self.ledger.slot(r["resource"])["folder"])), sha)
        r = self.ledger.active_reservation(first)
        token = pool.token_path(first).read_text(encoding="utf-8").strip()
        self.ledger.release(r["reservation_id"], token, "tests complete")
        pool.tick()  # park only after the ordinary quiescence gate
        self.assertEqual(self.trees.head(Path(self.ledger.slot(r["resource"])["folder"])), first_sha)
        pool.tick()
        self.assertIsNotNone(self.ledger.active_reservation(third))
        self.assertEqual(len([c for c in self.mcp.calls if c[0] == "start"]), 2)
        self.assertFalse(any(c[0] == "terminate" for c in self.mcp.calls))
        self.assertEqual(self.unity.argv, [])

    def test_low_headroom_retains_first_editor_and_serves_queue_without_opening_second(self):
        memory = {"available_mib": 20000, "commit_mib": 20000}
        admission = ResourceAdmission({"unity_mib": 8192, "max_reserved_mib": 16384}, observe=lambda: memory)
        pool = self.fixed_pool(admission=admission)
        original_start = self.mcp.start
        def start(slot, entry):
            result = original_start(slot, entry)
            memory["commit_mib"] = 8000
            return result
        self.mcp.start = start
        pool.ensure()
        self.assertEqual([s["state"] for s in self.ledger.slots()], ["idle_open", "idle_closed"])
        item = self.waiting(ISSUE, self.trees.head(self.origin))
        admission.last_reason = "previous startup was refused"
        pool.tick()
        self.assertIsNotNone(self.ledger.active_reservation(item))
        self.assertEqual(len([c for c in self.mcp.calls if c[0] == "start"]), 1)
        self.assertFalse(any(c[0] == "terminate" for c in self.mcp.calls))

    def test_failed_warmup_is_quarantined_and_other_editor_remains_usable(self):
        class OneBroken(FakeMcp):
            def start(self, slot, entry):
                if slot["slot_id"] == "unity_slot:1":
                    raise TimeoutError("Editor did not connect")
                return super().start(slot, entry)
        pool = self.fixed_pool(mcp=OneBroken())
        pool.ensure()
        self.assertEqual([s["state"] for s in self.ledger.slots()], ["held", "idle_open"])
        recovery = self.ledger.connection.execute("select * from resource_recoveries").fetchone()
        self.assertEqual((recovery["slot_id"], recovery["recovery_kind"]), ("unity_slot:1", "setup"))
        item = self.waiting(ISSUE, self.trees.head(self.origin))
        pool.tick()
        self.assertEqual(self.ledger.active_reservation(item)["resource"], "unity_slot:2")

    def test_interrupted_warmup_enters_recovery_on_restart_without_reopening(self):
        pool = self.fixed_pool(1)
        pool.ensure()
        self.ledger.begin_editor_warmup("unity_slot:1", host="test")  # already open: refused
        self.ledger.set_slot_state("unity_slot:1", "idle_closed")
        self.ledger.begin_editor_warmup("unity_slot:1", host="test")
        pool.ensure()
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "held")
        self.assertEqual(len([c for c in self.mcp.calls if c[0] == "start"]), 1)

    def test_unselected_closed_slot_is_never_opened_or_granted(self):
        self.ledger.ensure_slot("unity_slot:3", kind="unity_slot", host="test", folder=str(self.root / "extra"))
        pool = self.fixed_pool()
        pool.ensure()
        for key in (ISSUE, OTHER, str(uuid4())):
            self.waiting(key, self.trees.head(self.origin))
        for _ in range(4):
            pool.tick()
        self.assertEqual(self.ledger.slot("unity_slot:3")["state"], "idle_closed")
        self.assertFalse(any(r["resource"] == "unity_slot:3" for r in self.ledger.reservations()))
        self.assertEqual(len(self.mcp.open_folders), 2)

    def test_outside_open_slot_blocks_activation_and_cannot_be_bypassed_by_tick(self):
        self.ledger.ensure_slot("unity_slot:3", kind="unity_slot", host="test", folder=str(self.root / "extra"))
        self.ledger.set_slot_state("unity_slot:3", "idle_open")
        pool = self.fixed_pool()
        with self.assertRaisesRegex(SlotError, "settle"):
            pool.ensure()
        pool.tick()
        self.assertEqual(self.mcp.open_folders, set())

    def test_fixed_pool_refuses_direct_batch_launch_before_closing_any_editor(self):
        pool = self.fixed_pool(1)
        pool.ensure()
        slot = self.ledger.slot("unity_slot:1")
        with self.assertRaisesRegex(SlotError, "interactive"):
            pool.switch(slot["slot_id"], slot["parked_commit"], "batch")
        with self.assertRaisesRegex(SlotError, "disabled"):
            pool.run_batch(slot, {})
        self.assertFalse(any(c[0] == "terminate" for c in self.mcp.calls))

    def test_queued_server_work_keeps_its_mode_and_does_not_close_warm_editors(self):
        admission = ResourceAdmission({}, observe=lambda: {"available_mib": 20000, "commit_mib": 20000})
        pool = self.fixed_pool(admission=admission)
        pool.ensure()
        self.ledger.observe_issue(issue())
        self.ledger.ensure_session("server", ISSUE, True)
        sha = self.trees.head(self.origin)
        item = self.ledger.create_work_item(issue_id=ISSUE, session_id="server", skill="fix",
            target={"repository": "farm-hive", "requested_ref": "main", "commit_sha": sha,
                    "server_environment": "test", "selected_at": SELECTED_AT})
        self.ledger.connection.execute("UPDATE work_items SET root_repo='farm-hive' WHERE id=?", (item["id"],))
        token = self.ledger.claim(item["id"], worker_id="server")["token"]
        self.ledger.await_resource(item["id"], token, "server_slot", "batch", skill=SKILLS["fix"], commit_sha=sha)
        admission.last_reason = "memory pressure"
        pool.tick()
        self.assertEqual(self.ledger.reservations()[0]["mode"], "batch")
        self.assertFalse(any(c[0] == "terminate" for c in self.mcp.calls))

    def test_active_batch_requires_settled_activation(self):
        legacy = self.pool()
        legacy.ensure()
        item = self.waiting(ISSUE, self.trees.head(self.origin))
        self.ledger.acquire("unity_slot", owner="old-controller", host="test")
        pool = self.fixed_pool(1)
        with self.assertRaisesRegex(SlotError, "settle"):
            pool.ensure()
        pool.tick()
        self.assertEqual(self.unity.argv, [])
        self.assertEqual(self.ledger.active_reservation(item)["mode"], "batch")

    def test_editor_disappearing_after_acquire_cannot_trigger_an_unbudgeted_cold_launch(self):
        pool = self.fixed_pool(1)
        pool.ensure()
        item = self.waiting(ISSUE, self.trees.head(self.origin), "interactive")
        reservation = self.ledger.acquire("unity_slot", owner="pool", host="test")
        self.mcp.open_folders.clear()
        self.assertFalse(pool._hand_over(reservation))
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "held")
        self.assertEqual(self.ledger.item(item)["state"], "awaiting_resource")
        self.assertEqual(len([c for c in self.mcp.calls if c[0] == "start"]), 1)

    def test_duplicate_project_folders_are_refused_before_starting_any_editor(self):
        pool = self.fixed_pool()
        pool.entries["unity_slot:2"]["folder"] = pool.entries["unity_slot:1"]["folder"]
        with self.assertRaisesRegex(SlotError, "distinct project"):
            pool.ensure()
        self.assertEqual(self.mcp.open_folders, set())

    def test_uncertain_editor_process_is_quarantined_without_reopening_or_grant(self):
        from agent.unity import UnityError
        pool = self.fixed_pool(1)
        pool.ensure()
        item = self.waiting(ISSUE, self.trees.head(self.origin))
        def unknown(folder):
            raise UnityError("process enumeration refused")
        pool.editor_pid = unknown
        pool.tick()
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "held")
        self.assertEqual(self.ledger.item(item)["state"], "awaiting_resource")
        self.assertEqual(len([c for c in self.mcp.calls if c[0] == "start"]), 1)
        self.assertFalse(any(c[0] == "terminate" for c in self.mcp.calls))


class FixedEditorConfigTests(SlotFixture):
    def test_configuration_selects_two_or_three_and_round_trips_private_file(self):
        entries = [{"id": f"unity_slot:{n}"} for n in (1, 2, 3)]
        for count in (2, 3):
            block = {"editors": count}
            self.assertEqual(fixed_editor_count(block, entries), count)
            path = self.root / "配置 profile.json"
            path.write_text(json.dumps({"client_id": "c", "client_secret": "s", "webhook_secret": "w",
                                        "slots": entries, "unity_pool": block}), encoding="utf-8")
            self.assertEqual(load_config(path).unity_pool, block)
        for block in ({"editors": True}, {"editors": 0}, {"editors": 4}, {"editors": 2, "batch": True}, None, []):
            with self.subTest(block=block), self.assertRaises(ValueError):
                Config("c", "s", "w", slots=entries, unity_pool=block)
        with self.assertRaisesRegex(ValueError, "max_reserved_mib"):
            Config("c", "s", "w", slots=entries, unity_pool={"editors": 3},
                   resource_admission={"unity_mib": 8192, "max_reserved_mib": 16384})

    def test_dispatch_overrides_legacy_batch_instructions_without_granting_editor_lifecycle(self):
        message = dispatch_message(item={"id": "i", "skill": "fix"},
            issue={"identifier": "FARM-1", "url": "u"}, skill_path=self.root / "skills/fix/SKILL.md",
            worktrees={}, db_path=self.root / "ledger.sqlite3", runtime="fake", guidance="", budget=SKILLS["fix"].budget,
            tools={"unity_pool": {"mode": "interactive", "editors": 2}})
        authority, payload = message.split("\n\n", 1)
        self.assertIn("replaces legacy skill instructions", authority)
        self.assertIn("no tests ran during that conversion", authority)
        self.assertIn("Never start, stop or restart an Editor yourself", authority)
        self.assertEqual(json.loads(payload)["tools"]["unity_pool"]["editors"], 2)
