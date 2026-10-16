"""Persistent Editors: real local Git pins, fake Unity/MCP, isolated ledgers only."""
import json
import threading
from pathlib import Path
from uuid import uuid4

from agent.config import Config, load_config
from agent.dispatch import dispatch_message
from agent.resource_admission import ResourceAdmission
from agent.slots import SlotError, SlotPool, fixed_editor_count, slot_entry
from agent.scheduler import Scheduler
from test_scheduler import FakeLauncher, ROOT
from test_ledger import ISSUE, OTHER, SELECTED_AT, SKILLS, issue
from test_slots import FakeMcp, FakeUnity, SlotFixture, git


class FixedEditorPoolTests(SlotFixture):
    def test_maintenance_requires_fresh_editor_quiescence_not_just_idle_slot_state(self):
        from agent.maintenance import Maintenance, request
        pool = self.fixed_pool(1)
        pool.ensure()
        store = Maintenance(self.ledger)
        store.pause('maintenance')
        current = request(self.ledger.connection)
        self.assertTrue(pool.maintenance_blockers(current))
        self.mcp.ready = False
        pool.tick()
        self.assertEqual(self.ledger.slot('unity_slot:1')['state'], 'idle_open')
        self.assertEqual(pool.maintenance_blockers(current), [{'code':'resource_probe', 'subject':'unity_slot:1'}])
        self.mcp.ready = True
        self.advance(6)
        pool.tick()
        self.assertEqual(pool.maintenance_blockers(current), [])
        self.advance(16)
        self.assertTrue(pool.maintenance_blockers(current))
        store.resume()
        store.pause('next maintenance')
        self.assertTrue(pool.maintenance_blockers(request(self.ledger.connection)))

    def test_maintenance_preserves_queued_commit_without_warming_or_granting(self):
        from agent.maintenance import Maintenance
        store = Maintenance(self.ledger)
        store.pause('upgrade')
        pool = self.fixed_pool(1)
        pool.ensure()
        candidate = self.commit('maintenance-candidate')
        item = self.waiting(ISSUE, candidate, 'interactive')
        before = list(self.mcp.calls)
        self.assertEqual(pool.tick()['granted'], 0)
        self.assertEqual(self.mcp.calls, before)
        self.assertEqual(self.ledger.slot('unity_slot:1')['state'], 'idle_closed')
        self.assertIsNone(self.ledger.active_reservation(item))
        self.assertEqual(self.ledger.reservations()[0]['commit_sha'], candidate)
        store.resume()
        self.assertEqual(pool.tick()['granted'], 1)
        self.assertEqual(self.ledger.active_reservation(item)['commit_sha'], candidate)

    def test_pool_reserves_worker_before_switch_and_preparation_never_holds_scheduler_lock(self):
        launcher = FakeLauncher(self.root / 'runs')
        scheduler = Scheduler(self.ledger, launcher, SKILLS, self.trees, skill_root=ROOT / 'skills',
                              db_path=self.root / 'ledger.sqlite3', runtime_name='fake', host='test', max_concurrent=1)
        pool = self.fixed_pool(1, worker_acquire=scheduler.acquire_unity)
        pool.ensure()
        item = self.waiting(ISSUE, self.commit('candidate'), 'interactive')
        scheduler.active['busy'] = object()
        before = list(self.mcp.calls)
        self.assertEqual(pool.tick()['granted'], 0)
        self.assertEqual(self.mcp.calls, before)
        self.assertIsNone(self.ledger.active_reservation(item))
        self.assertEqual(self.ledger.slot('unity_slot:1')['state'], 'idle_open')
        scheduler.active.clear()
        original = pool.switch
        def switch(*args):
            observed = []
            def probe():
                locked = scheduler.lock.acquire(timeout=1)
                observed.append(locked)
                if locked:
                    scheduler.lock.release()
            thread = threading.Thread(target=probe)
            thread.start(); thread.join(2)
            self.assertEqual(observed, [True])
            self.assertEqual(self.ledger.pending_unity_workers('test'), {item})
            return original(*args)
        pool.switch = switch
        self.assertEqual(pool.tick()['granted'], 1)
        self.assertEqual(self.ledger.item(item)['stage'], 'unity-worker-ready')

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

    def test_metadata_dirty_release_recovers_the_verified_candidate_not_the_previous_idle_commit(self):
        from agent.identity import source_snapshot
        from agent.resource_recovery import RecoveryStore
        from agent.unity_recovery import SourceMetaReconciled, UnityRecovery

        relative = Path("Assets") / "资源 图集" / "texture.png.meta"
        (self.origin / relative).parent.mkdir(parents=True)
        (self.origin / relative).write_text("textureCompression: 1\n", encoding="utf-8")
        git("add", ".", cwd=self.origin)
        git("commit", "-qm", "initial metadata", cwd=self.origin)
        pool = self.fixed_pool()
        previous = pool.ensure()[0]["parked_commit"]
        candidate = self.commit("candidate")
        self.assertNotEqual(previous, candidate)
        item = self.waiting(ISSUE, candidate, "interactive")
        pool.tick()
        reservation = self.ledger.active_reservation(item)
        slot = self.ledger.slot(reservation["resource"])
        folder = Path(slot["folder"])
        self.assertEqual(slot["parked_commit"], candidate)

        (folder / relative).write_text("textureCompression: 0\n", encoding="utf-8")
        token = pool.token_path(item).read_text(encoding="utf-8").strip()
        self.ledger.release(reservation["reservation_id"], token, "tests complete")
        self.assertEqual(pool.park_idle(), 0)
        self.assertEqual(self.ledger.slot(slot["slot_id"])["state"], "held")
        self.assertIsNone(self.ledger.active_reservation_on(slot["slot_id"]))
        store = RecoveryStore(self.ledger)
        store.discover("test")
        recovery = store.pending("test")[0]
        self.assertEqual(recovery["commit_sha"], candidate)
        evidence = self.root / "resource recovery" / recovery["id"]
        with self.assertRaises(SourceMetaReconciled) as caught:
            UnityRecovery(pool).repair({**recovery, "evidence_dir": str(evidence)})
        self.assertEqual(caught.exception.evidence["head"], candidate)
        self.assertEqual(self.trees.head(folder), candidate)
        self.assertEqual(source_snapshot(folder)["dirty"], [])
        self.assertTrue((evidence / "source-meta.json").is_file())
        self.assertEqual((folder / relative).read_text(encoding="utf-8"), "textureCompression: 1\n")
        other = self.ledger.slot("unity_slot:2")
        self.assertIn(other["folder"], self.mcp.open_folders)
        self.assertFalse(any(call == ("terminate", "unity_slot:2") for call in self.mcp.calls))

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

    def test_restart_recovers_acquired_interactive_request_before_worker_handoff(self):
        from agent.resource_recovery import RecoveryStore
        from agent.ledger import LedgerError
        pool = self.fixed_pool(1)
        pool.ensure()
        candidate = self.commit('interrupted candidate')
        item = self.waiting(ISSUE, candidate, 'interactive')
        before = self.ledger.item(item)
        reservation = self.ledger.acquire('unity_slot', owner='previous-controller', host='test')
        calls = list(self.mcp.calls)
        pool.ensure()  # controller died after acquire, before switch/handoff
        self.assertEqual(self.mcp.calls, calls)  # startup only fences; recovery owns physical work
        self.assertEqual(self.ledger.slot('unity_slot:1')['state'], 'held')
        self.assertEqual(self.ledger.item(item)['stage'], 'waiting_for_recovery')
        self.assertEqual(self.ledger.item(item)['checkpoint'], before['checkpoint'])
        self.assertEqual(self.ledger.item(item)['generation'], before['generation'])
        self.assertEqual(self.ledger.item(item)['target'], before['target'])
        with self.assertRaises(LedgerError):
            self.ledger.release(reservation['reservation_id'], reservation['token'], 'stale controller')
        store = RecoveryStore(self.ledger)
        recovery = store.pending('test')[0]
        self.assertEqual((recovery['reservation_id'], recovery['commit_sha'], recovery['recovery_kind']),
                         (reservation['reservation_id'], candidate, 'setup'))
        pool.ensure()
        self.assertEqual(len(store.pending('test')), 1)
        recovery = store.begin(recovery['id'])
        store.detach(recovery['id'], recovery['attempts'])  # production first verifies worker teardown
        store.complete(recovery['id'], recovery['attempts'], candidate, self.mcp.instance)
        self.assertEqual(store.job_attempts(item), 0)
        self.assertEqual(store.job_attempts(item, recovery_kind='setup'), 1)
        self.assertEqual(pool.tick()['granted'], 1)
        self.assertEqual(self.ledger.item(item)['stage'], 'unity-worker-ready')
        self.assertEqual(self.ledger.active_reservation(item)['commit_sha'], candidate)
        self.assertEqual(self.ledger.item(item)['checkpoint'], before['checkpoint'])

    def test_restart_recovers_switch_completed_before_handoff(self):
        pool = self.fixed_pool(1)
        pool.ensure()
        candidate = self.commit('ready but not handed off')
        item = self.waiting(ISSUE, candidate, 'interactive')
        self.ledger.acquire('unity_slot', owner='previous-controller', host='test')
        pool.switch('unity_slot:1', candidate, 'interactive')
        self.assertEqual(self.ledger.slot('unity_slot:1')['state'], 'interactive_busy')
        self.assertEqual(self.ledger.item(item)['state'], 'awaiting_resource')
        calls = list(self.mcp.calls)
        pool.ensure()
        self.assertEqual(self.mcp.calls, calls)
        self.assertEqual(self.ledger.slot('unity_slot:1')['state'], 'held')
        self.assertEqual(self.ledger.item(item)['stage'], 'waiting_for_recovery')

    def test_restart_preserves_already_handed_off_and_running_workers(self):
        pool = self.fixed_pool(1)
        pool.ensure()
        item = self.waiting(ISSUE, self.commit('handed off'), 'interactive')
        self.assertEqual(pool.tick()['granted'], 1)
        reservation = self.ledger.active_reservation(item)
        for state in ('queued', 'running'):
            if state == 'running':
                self.ledger.claim(item, worker_id='resumed')
            pool.ensure()
            self.assertEqual(self.ledger.item(item)['state'], state)
            self.assertEqual(self.ledger.active_reservation(item)['reservation_id'], reservation['reservation_id'])
            self.assertEqual(self.ledger.slot('unity_slot:1')['state'], 'interactive_busy')
            self.assertEqual(self.ledger.connection.execute('SELECT count(*) FROM resource_recoveries').fetchone()[0], 0)

    def test_restart_does_not_revive_cancelled_preparation(self):
        pool = self.fixed_pool(1)
        pool.ensure()
        item = self.waiting(ISSUE, self.commit('cancelled preparation'), 'interactive')
        self.ledger.acquire('unity_slot', owner='previous-controller', host='test')
        self.ledger.cancel(item, 'operator stopped')
        pool.ensure()
        self.assertEqual(self.ledger.item(item)['state'], 'cancelled')
        self.assertEqual(self.ledger.connection.execute('SELECT count(*) FROM resource_recoveries').fetchone()[0], 0)

    def test_dirty_source_startup_keeps_editor_fenced_across_ticks_and_restart(self):
        from agent.resource_recovery import RecoveryStore
        from agent.identity import CHECKS
        class DirtySource(FakeMcp):
            def probe(self, slot, target):
                if slot['slot_id'] == 'unity_slot:1':
                    return {'aggregate':'mismatch', 'checks':dict.fromkeys(CHECKS, 'match') | {'source_clean':'mismatch'}}
                return super().probe(slot, target)
        pool = self.fixed_pool(mcp=DirtySource())
        pool.ensure()
        self.assertEqual(self.ledger.slot('unity_slot:1')['state'], 'held')
        self.assertEqual(self.ledger.slot('unity_slot:2')['state'], 'idle_open')
        recovery = self.ledger.connection.execute("SELECT * FROM resource_recoveries WHERE slot_id='unity_slot:1'").fetchone()
        self.assertEqual((recovery['state'], recovery['detached'], recovery['recovery_kind']), ('pending', 0, 'candidate'))
        item = self.waiting(ISSUE, self.commit('new clean candidate'))
        for _ in range(3): pool.tick()
        pool.ensure()
        self.assertEqual(self.ledger.active_reservation(item)['resource'], 'unity_slot:2')
        self.assertEqual(self.ledger.slot('unity_slot:1')['state'], 'held')
        self.assertEqual(len([c for c in self.mcp.calls if c[0] == 'start']), 2)
        self.assertFalse(any(c[0] == 'terminate' for c in self.mcp.calls))
        self.assertEqual(len(list(self.ledger.connection.execute("SELECT id FROM resource_recoveries WHERE slot_id='unity_slot:1'"))), 1)
        self.assertEqual(RecoveryStore(self.ledger).late_candidates('test'), [])

    def test_incomplete_source_identity_still_requires_editor_recovery(self):
        class DirtySource(FakeMcp):
            dirty = True
            def probe(self, slot, target):
                if self.dirty: return {'aggregate':'mismatch', 'checks':{'source_clean':'mismatch'}}
                return super().probe(slot, target)
        mcp = DirtySource(); pool = self.fixed_pool(1, mcp=mcp)
        pool.ensure(); mcp.dirty = False
        pool.tick(); pool.ensure()
        self.assertEqual(self.ledger.slot('unity_slot:1')['state'], 'held')
        self.assertEqual(self.ledger.connection.execute('SELECT recovery_kind FROM resource_recoveries').fetchone()[0], 'setup')
        from agent.ledger import LedgerError
        with self.assertRaisesRegex(LedgerError, 'automatic recovery'):
            self.ledger.recover_slot('unity_slot:1', 'operator verified corrected source and project identity')
        self.assertEqual(self.ledger.slot('unity_slot:1')['state'], 'held')
        self.assertFalse(any(c[0] == 'terminate' for c in mcp.calls))

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
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "idle_open")
        self.assertIsNone(self.ledger.active_reservation(item))
        self.advance(31)
        pool.tick()
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "held")
        self.assertEqual(self.ledger.item(item)["state"], "awaiting_resource")
        self.assertEqual(len([c for c in self.mcp.calls if c[0] == "start"]), 1)
        self.assertFalse(any(c[0] == "terminate" for c in self.mcp.calls))

    def test_one_failed_process_read_blocks_grants_then_reuses_the_same_editor(self):
        from agent.unity import UnityError
        pool = self.fixed_pool(1)
        pool.ensure()
        item = self.waiting(ISSUE, self.trees.head(self.origin))
        original = pool.editor_pid
        pool.editor_pid = lambda folder: (_ for _ in ()).throw(UnityError('temporary timeout'))
        pool.tick()
        self.assertIsNone(self.ledger.active_reservation(item))
        self.assertEqual(list(self.ledger.connection.execute('SELECT id FROM resource_recoveries')), [])
        pool.editor_pid = original
        pool.tick()
        self.assertEqual(self.ledger.active_reservation(item)['resource'], 'unity_slot:1')
        self.assertEqual(len([c for c in self.mcp.calls if c[0] == 'start']), 1)
        self.assertFalse(any(c[0] == 'terminate' for c in self.mcp.calls))


class FixedEditorConfigTests(SlotFixture):
    def test_configuration_selects_two_or_three_and_round_trips_private_file(self):
        entries = [{"id": f"unity_slot:{n}"} for n in (1, 2, 3)]
        for count in (1, 2, 3):
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
