"""Offline server resource tests: real ledger, fake Docker; never uses the host's services."""
import dataclasses
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

from agent.config import Config
from agent.ledger import Ledger, LedgerError
from agent.resource_admission import ResourceAdmission, validate_settings as validate_admission
from agent.resource_recovery import RecoveryStore
from agent.server_slots import DockerServices, DockerUnavailable, ServerSlotPool, ServerSlotError, read_resource, validate_settings
from agent.server_slots import relay_source_digest
from agent.skills import load_skills
from agent.slots import SlotPool
from test_ledger import issue, PIN

SKILLS = load_skills(Path(__file__).resolve().parents[1] / "skills", replay_legacy=True)
IMAGES = {role: "sha256:" + str(index) * 64 for index, role in enumerate(("mongo", "redis", "etcd", "nats", "proxy"), 1)}
SETTINGS = {"count": 3, "docker": str(Path(tempfile.gettempdir()).absolute() / "docker"), "images": IMAGES}


class Services:
    def __init__(self):
        self.started, self.cleaned = [], []
        self.error = None
        self.after_start = None

    def preflight(self, count):
        self.count = count
        return {'daemon_id': 'fixture', 'endpoint_sha256': '1' * 64}

    def start(self, lease):
        self.started.append(dict(lease))
        if self.after_start:
            self.after_start(lease)
        if self.error:
            raise self.error

    def cleanup(self, lease):
        if self.error:
            raise self.error
        self.cleaned.append(dict(lease))


class ServerPoolFixture(unittest.TestCase):
    settings = SETTINGS

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # macOS's /var temporary alias resolves to /private/var. The actual managed root is canonical;
        # links introduced inside it must still be refused by the controller.
        self.root = Path(self.temp.name).resolve() / "controller 根 with spaces"
        self.db = self.root.parent / "agent" / "ledger.sqlite3"
        self.now = 1000
        self.sequence = 0
        self.ledger = self.connection()
        self.addCleanup(self.ledger.close)
        self.services = Services()
        self.exited = True
        self.admission = ResourceAdmission({}, observe=lambda: {"available_mib": 100000, "commit_mib": 100000})
        self.pool = self.make_pool()
        self.pool.ensure()

    def connection(self):
        return Ledger(self.db, clock=lambda: self.now)

    def make_pool(self):
        pool = ServerSlotPool(self.connection, self.settings, host="test", root=self.root,
                              state_dir=lambda item: self.root.parent / "runs" / item,
                              quiescent=lambda item: self.exited, admission=self.admission,
                              services=self.services, clock=lambda: self.now)
        self.addCleanup(pool.close)
        return pool

    def request(self, commit="a" * 40, *, root="farm-hive", mode="batch", skill="fix", reserve=True, plan=None,
                execution_contract='staged-v1'):
        self.sequence += 1
        number = self.sequence
        identity = str(uuid4())
        self.ledger.observe_issue(issue(id=identity, identifier=f"FARM-{number}", branch_name=f"farmbot/farm-{number}"))
        self.ledger.ensure_session(identity, identity, delegation=True)
        item = self.ledger.create_work_item(issue_id=identity, session_id=identity, skill=skill, target=PIN,
                                            execution_contract=execution_contract)
        self.ledger.connection.execute("UPDATE work_items SET root_repo=? WHERE id=?", (root, item["id"]))
        if not reserve:
            return item["id"]
        token = self.ledger.claim(item["id"], worker_id="worker")["token"]
        self.ledger.await_resource(item["id"], token, "server_slot", mode, skill=SKILLS[skill], commit_sha=commit,
                                   server_test_plan=plan)
        return item["id"]

class PoolTests(ServerPoolFixture):
    def test_daemon_outage_retries_same_reservation_without_reclaiming_worker(self):
        item = self.request()
        self.services.error = DockerUnavailable('info unavailable')
        self.pool.tick()
        reservation = self.ledger.active_reservation(item)
        before = self.ledger.item(item)
        path = self.pool._path(reservation['resource'])
        lease = json.loads(path.read_text())
        self.assertEqual(lease['state'], 'waiting_for_docker')
        self.assertNotEqual(self.ledger.slot(reservation['resource'])['state'], 'held')
        self.services.error = None
        self.assertEqual(self.pool.tick()['granted'], 1)
        self.assertEqual(self.ledger.active_reservation(item)['reservation_id'], reservation['reservation_id'])
        self.assertEqual(self.ledger.item(item)['generation'], before['generation'])
        self.assertEqual(json.loads(path.read_text())['state'], 'ready')
        self.assertEqual(self.services.started[0]['ports'], self.services.started[1]['ports'])

    def test_outage_cancel_waits_for_quiescence_and_never_restarts_services(self):
        item = self.request()
        self.services.error = DockerUnavailable('info unavailable')
        self.pool.tick()
        reservation = self.ledger.active_reservation(item)
        self.ledger.cancel(item, 'Stop')
        self.exited = False
        self.services.error = None
        self.pool.tick()
        self.assertEqual(len(self.services.started), 1)
        self.assertFalse(self.services.cleaned)
        self.exited = True
        self.assertEqual(self.pool.tick()['settled'], 1)
        self.assertEqual(self.ledger.slot(reservation['resource'])['state'], 'idle_closed')

    def test_daemon_outage_during_cleanup_retries_after_return(self):
        item = self.request()
        self.pool.tick()
        reservation = self.ledger.active_reservation(item)
        self.ledger.cancel(item, 'Stop')
        self.services.error = DockerUnavailable('info unavailable')
        self.pool.tick()
        self.assertNotEqual(self.ledger.slot(reservation['resource'])['state'], 'held')
        self.assertFalse(self.services.cleaned)
        self.services.error = None
        self.assertEqual(self.pool.tick()['settled'], 1)

    def test_outage_never_extends_startup_deadline_or_publishes_late_readiness(self):
        item = self.request()
        self.services.error = DockerUnavailable('info unavailable')
        self.pool.tick()
        reservation = self.ledger.active_reservation(item)
        lease = json.loads(self.pool._path(reservation['resource']).read_text())
        self.services.error = None
        self.services.after_start = lambda _: setattr(self, 'now', lease['expires_at'] + 1)
        self.assertEqual(self.pool.tick()['granted'], 0)
        self.assertEqual(self.ledger.item(item)['state'], 'blocked')
        self.assertFalse((self.root.parent / 'runs' / item / 'server-resource.json').exists())

    def missing_journal(self):
        previous = self.request()
        self.pool.tick()
        lease = dict(self.services.started[-1], docker_binding={'daemon_id': 'fixture', 'endpoint_sha256': '1'*64})
        self.pool._save(lease)
        self.ledger.cancel(previous, 'previous run done')
        self.pool.tick()
        item = self.request()
        self.services.error = ServerSlotError('local Docker command unavailable or timed out: info')
        self.pool.tick()
        current = self.ledger.active_reservation(item)
        self.pool._path(current['resource']).unlink()  # Historical failure before the journal was saved.
        self.services.error = None
        return item, current

    def test_docker_probe_failure_keeps_startup_identity_in_journal(self):
        binding = {'daemon_id': 'fixture', 'endpoint_sha256': '1'*64}
        with patch.object(self.services, 'preflight', return_value=binding):
            self.pool.ensure()
        item = self.request()
        def start(lease):
            saved = json.loads(self.pool._path(lease['slot']).read_text())
            self.assertEqual(saved['docker_binding'], binding)
            self.assertEqual(saved['reservation_id'], lease['reservation_id'])
            raise ServerSlotError('local Docker command unavailable or timed out: info')
        with patch.object(self.services, 'start', side_effect=start):
            self.pool.tick()
        reservation = self.ledger.active_reservation(item)
        self.assertEqual(self.ledger.slot(reservation['resource'])['state'], 'held')
        self.assertIn('timed out: info', self.ledger.connection.execute(
            "SELECT reason FROM audit WHERE kind='server_slot_held' ORDER BY id DESC").fetchone()[0])
        self.pool.recover(reservation['resource'], 'daemon restored')
        self.assertEqual(self.services.cleaned[-1]['docker_binding'], binding)

    def test_missing_journal_recovery_preserves_checkpoint_and_never_cleans(self):
        item, reservation = self.missing_journal()
        before = self.ledger.item(item)
        cleaned = list(self.services.cleaned)
        with patch.object(self.services, 'verify_absent', create=True) as verify:
            self.pool.recover(reservation['resource'], 'operator recovery')
        verify.assert_called_once()
        self.assertEqual(self.services.cleaned, cleaned)
        self.assertEqual(self.ledger.item(item), before)
        self.assertEqual(self.ledger.reservation(reservation['reservation_id'])['state'], 'released')
        self.assertEqual(self.ledger.slot(reservation['resource'])['state'], 'idle_closed')
        receipt = self.pool.folder(reservation['resource']) / (reservation['reservation_id'] + '.missing-journal-recovery.json')
        proof = json.loads(receipt.read_text())
        self.assertNotIn('token', proof)
        self.assertEqual(proof['state'], 'absence_verified')

    def test_missing_journal_recovery_refuses_live_worker_or_changed_generation(self):
        item, reservation = self.missing_journal()
        with patch.object(self.services, 'verify_absent', create=True) as verify:
            self.exited = False
            with self.assertRaisesRegex(ServerSlotError, 'teardown'):
                self.pool.recover(reservation['resource'], 'operator recovery')
            self.exited = True
            self.ledger.connection.execute('UPDATE work_items SET generation=generation+1 WHERE id=?', (item,))
            with self.assertRaisesRegex(ServerSlotError, 'current-generation'):
                self.pool.recover(reservation['resource'], 'operator recovery')
            verify.assert_not_called()
        self.assertEqual(self.ledger.slot(reservation['resource'])['state'], 'held')

    def test_missing_journal_recovery_retains_hold_if_absence_cannot_be_proved(self):
        item, reservation = self.missing_journal()
        for error in ('daemon changed', 'Docker resources exist', 'inspection timed out'):
            with self.subTest(error=error), patch.object(self.services, 'verify_absent', create=True,
                                                        side_effect=ServerSlotError(error)):
                with self.assertRaisesRegex(ServerSlotError, error):
                    self.pool.recover(reservation['resource'], 'operator recovery')
                self.assertEqual(self.ledger.reservation(reservation['reservation_id'])['state'], 'active')
                self.assertEqual(self.ledger.slot(reservation['resource'])['state'], 'held')

    def test_missing_journal_recovery_requires_untampered_historical_identity(self):
        item, reservation = self.missing_journal()
        previous = self.services.started[0]
        path = self.pool.folder(reservation['resource']) / (previous['reservation_id'] + '.json')
        value = json.loads(path.read_text())
        value['owner'] = 'foreign'
        path.write_text(json.dumps(value))
        with patch.object(self.services, 'verify_absent', create=True) as verify:
            with self.assertRaisesRegex(ServerSlotError, 'invalid Docker identity'):
                self.pool.recover(reservation['resource'], 'operator recovery')
            verify.assert_not_called()

    def test_corrupt_existing_journal_never_uses_absence_recovery(self):
        item, reservation = self.missing_journal()
        self.pool._path(reservation['resource']).write_text('invalid json')
        with patch.object(self.services, 'verify_absent', create=True) as verify:
            with self.assertRaises(json.JSONDecodeError):
                self.pool.recover(reservation['resource'], 'operator recovery')
            verify.assert_not_called()

    def test_missing_journal_recovery_rechecks_fence_after_inspection(self):
        item, reservation = self.missing_journal()
        def change(_):
            self.ledger.connection.execute('UPDATE work_items SET generation=generation+1 WHERE id=?', (item,))
        with patch.object(self.services, 'verify_absent', create=True, side_effect=change):
            with self.assertRaisesRegex(ServerSlotError, 'ownership changed'):
                self.pool.recover(reservation['resource'], 'operator recovery')
        self.assertEqual(self.ledger.slot(reservation['resource'])['state'], 'held')

    def test_missing_journal_recovery_refuses_absent_history_and_wrong_slot_path(self):
        item = self.request()
        self.services.error = ServerSlotError('binding failed')
        self.pool.tick()
        reservation = self.ledger.active_reservation(item)
        self.pool._path(reservation['resource']).unlink()
        with patch.object(self.services, 'verify_absent', create=True) as verify:
            with self.assertRaisesRegex(ServerSlotError, 'no settled'):
                self.pool.recover(reservation['resource'], 'operator recovery')
            self.ledger.connection.execute('UPDATE slots SET folder=? WHERE slot_id=?',
                                           (str(self.root.parent), reservation['resource']))
            with self.assertRaisesRegex(ServerSlotError, 'configuration'):
                self.pool.recover(reservation['resource'], 'operator recovery')
            verify.assert_not_called()

    def test_missing_journal_recovery_preserves_cancellation(self):
        item, reservation = self.missing_journal()
        self.ledger.cancel_reservations(item, 'operator cancellation')
        with patch.object(self.services, 'verify_absent', create=True):
            self.pool.recover(reservation['resource'], 'operator recovery')
        self.assertEqual(self.ledger.reservation(reservation['reservation_id'])['state'], 'cancelled')

    def test_single_issue_acceptance_never_starts_services_for_older_paused_request(self):
        from agent.maintenance import Maintenance
        older = self.request('a'*40)
        selected = self.request('b'*40)
        before = self.ledger.reservations()[0]
        store = Maintenance(self.ledger)
        store.pause('accept FARM-2 first')
        store.resume(only_issue='FARM-2')
        self.assertEqual(self.pool.tick()['granted'], 1)
        self.assertEqual([lease['item_id'] for lease in self.services.started], [selected])
        self.assertIsNone(self.ledger.active_reservation(older))
        self.assertEqual(self.ledger.reservation(before['reservation_id']), before)
        store.resume()
        self.assertEqual(self.pool.tick()['granted'], 1)
        self.assertEqual([lease['item_id'] for lease in self.services.started], [selected, older])

    def test_maintenance_settles_existing_services_but_does_not_grant_next_request(self):
        from agent.maintenance import Maintenance
        item = self.request('a'*40)
        self.pool.tick()
        lease = self.services.started[0]
        waiting = self.request('b'*40)
        store = Maintenance(self.ledger)
        store.pause('maintenance')
        self.ledger.release(lease['reservation_id'], lease['token'], 'tests finished')
        self.exited = False
        self.assertEqual(self.pool.tick(), {'settled':0, 'granted':0})
        self.assertEqual(self.services.cleaned, [])
        self.exited = True
        self.assertEqual(self.pool.tick(), {'settled':1, 'granted':0})
        self.assertEqual(len(self.services.started), 1)
        self.assertIsNone(self.ledger.active_reservation(waiting))
        store.resume()
        self.assertEqual(self.pool.tick()['granted'], 1)
        self.assertEqual(self.ledger.active_reservation(waiting)['commit_sha'], 'b'*40)

    def test_general_job_can_reserve_server_services_at_its_exact_candidate(self):
        item = self.request(skill="work")
        self.assertEqual(self.pool.tick()["granted"], 1)
        self.assertEqual(self.services.started[0]["commit"], "a" * 40)
        self.assertEqual(self.ledger.item(item)["state"], "queued")
        self.ledger.cancel(item, "general issue stopped")
        self.assertEqual(self.pool.tick()["settled"], 1)
        self.assertEqual(len(self.services.cleaned), 1)
        self.assertEqual(self.services.cleaned[0]["reservation_id"], self.services.started[0]["reservation_id"])

    def test_three_candidates_receive_distinct_services_and_fourth_waits(self):
        items = [self.request(str(index) * 40) for index in range(1, 5)]
        self.assertEqual(self.pool.tick()["granted"], 3)
        self.assertEqual([self.ledger.item(item)["state"] for item in items], ["queued"] * 3 + ["awaiting_resource"])
        self.assertEqual(len({lease["project"] for lease in self.services.started}), 3)
        self.assertEqual(len({p for lease in self.services.started for p in lease["ports"].values()}), 12)
        self.assertEqual([lease["commit"] for lease in self.services.started], [str(i) * 40 for i in range(1, 4)])
        self.assertEqual(self.pool.tick()["granted"], 0)

    def test_requesting_worker_must_exit_before_grant_without_losing_fifo(self):
        item = self.request()
        self.exited = False
        self.assertEqual(self.pool.tick()["granted"], 0)
        self.assertEqual(self.services.started, [])
        self.assertEqual(self.ledger.reservations()[0]["state"], "queued")
        self.exited = True
        self.assertEqual(self.pool.tick()["granted"], 1)
        self.assertEqual(self.ledger.item(item)["state"], "queued")

    def test_release_waits_for_teardown_and_retains_the_journal(self):
        item = self.request()
        self.pool.tick()
        lease = self.services.started[0]
        self.ledger.release(lease["reservation_id"], lease["token"], "done")
        self.exited = False
        self.assertEqual(self.pool.tick()["settled"], 0)
        self.assertEqual(self.services.cleaned, [])
        self.exited = True
        self.assertEqual(self.pool.tick()["settled"], 1)
        archived = self.pool.folder(lease["slot"]) / (lease["reservation_id"] + ".json")
        self.assertEqual(json.loads(archived.read_text())["state"], "cleaned")
        self.assertEqual(self.ledger.slot(lease["slot"])["state"], "idle_closed")

    def test_cancel_one_does_not_reset_another(self):
        a, b = self.request(), self.request("b" * 40)
        self.pool.tick()
        self.ledger.cancel(a, "stop")
        self.assertEqual(self.pool.tick()["settled"], 1)
        self.assertEqual([lease["item_id"] for lease in self.services.cleaned], [a])
        self.assertEqual(self.ledger.active_reservation(b)["state"], "active")

    def test_timeout_blocks_without_retry_and_waits_for_teardown(self):
        item = self.request()
        self.pool.tick()
        self.now += 7201
        self.exited = False
        self.pool.tick()
        self.assertEqual(self.ledger.item(item)["state"], "blocked")
        self.assertEqual(self.ledger.item(item)['stage'], 'server-verification-timeout')
        self.pool.tick()
        self.assertFalse(self.services.cleaned)
        self.exited = True
        self.assertEqual(self.pool.tick()["settled"], 1)

    def test_restart_keeps_an_active_lease_and_reconciles_cancellation(self):
        item = self.request()
        self.pool.tick()
        restarted = self.make_pool()
        restarted.ensure()
        self.assertEqual(restarted.tick(), {"settled": 0, "granted": 0})
        self.ledger.cancel(item, "stop after restart")
        self.assertEqual(restarted.tick()["settled"], 1)
        self.assertEqual(len(self.services.started), 1)

    def test_partial_creation_holds_without_unity_recovery_and_verified_operator_recovery(self):
        item = self.request()
        self.services.error = ServerSlotError("daemon unavailable")
        self.pool.tick()
        lease = self.services.started[0]
        self.assertEqual(self.ledger.slot(lease["slot"])["state"], "held")
        RecoveryStore(self.ledger).discover("test")
        self.assertEqual(RecoveryStore(self.ledger).pending("test"), [])
        with self.assertRaisesRegex(LedgerError, "Docker cleanup"):
            self.ledger.recover_slot(lease["slot"], "try")
        self.services.error = None
        self.exited = False
        with self.assertRaisesRegex(ServerSlotError, "teardown"):
            self.pool.recover(lease["slot"], "try")
        self.assertEqual(self.services.cleaned, [])
        self.exited = True
        self.pool.recover(lease["slot"], "local daemon restored")
        self.assertEqual(self.ledger.slot(lease["slot"])["state"], "idle_closed")
        self.assertEqual(self.ledger.item(item)["state"], "failed")

    def test_cancel_during_startup_does_not_resume_and_next_tick_cleans(self):
        item = self.request()
        self.services.after_start = lambda lease: self.ledger.cancel(item, "stop during setup")
        self.assertEqual(self.pool.tick()["granted"], 0)
        self.assertEqual(self.ledger.item(item)["state"], "cancelled")
        self.assertEqual(self.pool.tick()["settled"], 1)

    def test_tampered_journal_holds_without_touching_services(self):
        item = self.request()
        self.pool.tick()
        lease = dict(self.services.started[0])
        lease["commit"] = "e" * 40
        self.pool._save(lease)
        self.ledger.cancel(item, "stop")
        self.pool.tick()
        self.assertFalse(self.services.cleaned)
        self.assertEqual(self.ledger.slot(lease["slot"])["state"], "held")

    def test_read_resource_uses_controller_journal_and_rejects_ledger_redirect(self):
        item = self.request()
        self.pool.tick()
        reservation = self.ledger.active_reservation(item)
        slot = self.ledger.slot(reservation["resource"])
        with patch("agent.server_slots.time.time", return_value=self.now):
            resource = read_resource(slot, reservation, self.root)
        self.assertEqual(resource["commit"], "a" * 40)
        self.assertNotIn("token", resource)
        self.assertTrue(all("127.0.0.1" in resource["environment"][key]
                            for key in ("MONGO_URI", "REDIS_ADDR", "ETCD_ENDPOINTS", "NATS_URL")))
        self.assertEqual(resource["environment"]["CLUSTER_REQUIRED"], "1")
        with self.assertRaisesRegex(ServerSlotError, "configuration"):
            read_resource({**slot, "folder": str(self.root.parent)}, reservation, self.root)

    def test_mismatched_stage_mode_skill_and_commit_refused(self):
        for options in ({"root": "Farm-Client"}, {"mode": "interactive"}, {"skill": "chat"}, {"commit": "main"}):
            with self.subTest(options=options), self.assertRaises(LedgerError):
                self.request(**options)
        self.assertEqual(self.services.started, [])

    def test_open_reservation_prevents_hold_and_wait_and_stale_token_release(self):
        item = self.request()
        self.pool.tick()
        token = self.ledger.claim(item, worker_id="resume")["token"]
        with self.assertRaisesRegex(LedgerError, "release the resource"):
            self.ledger.await_resource(item, token, "server_slot", "batch", skill=SKILLS["fix"], commit_sha="a" * 40)
        lease = self.services.started[0]
        with self.assertRaisesRegex(LedgerError, "token"):
            self.ledger.release(lease["reservation_id"], "not-the-token", "try")

    def test_unavailable_preflight_never_grants(self):
        self.pool.ready = False
        self.request()
        self.assertTrue(self.pool.tick()["unavailable"])
        self.assertFalse(self.services.started)

    def test_missing_restart_handover_fails_instead_of_stranding_the_job(self):
        item = self.request()
        self.pool.tick()
        self.ledger.connection.execute("UPDATE work_items SET state='awaiting_resource' WHERE id=?", (item,))
        self.assertEqual(self.pool.tick()["settled"], 1)
        self.assertEqual(self.ledger.item(item)["state"], "failed")

    def test_unity_pool_cannot_settle_or_park_server_resources(self):
        item = self.request()
        self.pool.tick()
        self.ledger.cancel(item, "stop")
        unity = SlotPool(self.ledger, None, [], host="test", editors_root=self.root.parent / "editors")
        self.assertEqual((unity.settle(), unity.park_idle()), (0, 0))
        self.assertEqual(self.ledger.active_reservation(item)["state"], "cancel_requested")

    def test_stale_generation_cannot_cancel_a_new_claim_or_recycle_the_slot(self):
        item = self.request()
        self.pool.tick()
        self.ledger.connection.execute("UPDATE work_items SET generation=generation+1 WHERE id=?", (item,))
        self.now += 1801
        self.pool.tick()
        self.assertEqual(self.ledger.item(item)["state"], "queued")
        self.assertFalse(self.services.cleaned)
        self.assertEqual(self.ledger.slots()[0]["state"], "held")

    def test_scheduler_revalidates_head_and_grants_only_lease_environment_without_docker_or_slot_writes(self):
        from agent.scheduler import Scheduler
        from test_scheduler import FakeLauncher, FakeWorktrees
        class RecordingLauncher(FakeLauncher):
            def spawn(self, *args, **options):
                options.pop("model_settings", None)
                return super().spawn(*args, **options)
        item = self.request()
        self.pool.tick()
        launcher = RecordingLauncher(self.root.parent / "runs")
        trees = FakeWorktrees(self.root.parent / "worktrees")
        scheduler = Scheduler(self.ledger, launcher, SKILLS, trees, replay_legacy=True, skill_root=Path(__file__).resolve().parents[1] / "skills",
                              db_path=self.db, runtime_name="codex", host="test", server_slots_root=self.root)
        with patch("agent.server_slots.time.time", return_value=self.now), patch.object(
                trees, "verification_commit", create=True, return_value="a" * 40) as verify:
            scheduler.launch(self.ledger.item(item))
        verify.assert_called_once_with("farm-hive", item, "a" * 40)
        self.assertEqual(launcher.spawn_env["CLUSTER_REQUIRED"], "1")
        self.assertIn("127.0.0.1", launcher.spawn_env["MONGO_URI"])
        self.assertEqual(launcher.spawn_env["GOMAXPROCS"], "2")
        # Launcher, exercised with real children by test_worker_retention, now
        # owns attempt-local Go paths instead of the scheduler sharing job caches.
        self.assertNotIn('GOCACHE', launcher.spawn_env)
        self.assertNotIn('GOTMPDIR', launcher.spawn_env)
        self.assertEqual(launcher.spawned[0][2], {})
        self.assertFalse(any(self.root.is_relative_to(Path(path)) for path in launcher.spawn_writable))

    def test_a_held_or_stale_reservation_stops_the_launch_before_any_checkout(self):
        """A held slot keeps its reservation active until recovery: a launch that will not run must not prepare,
        and so fetch, the job's worktrees and read-only checkouts on every scheduler tick."""
        from agent.scheduler import Scheduler
        from test_scheduler import FakeLauncher, FakeWorktrees
        for stale in (False, True):
            with self.subTest(stale=stale):
                item = self.request(skill="work")
                self.pool.tick()
                reservation = self.ledger.active_reservation(item)
                if stale:
                    self.ledger.connection.execute("UPDATE work_items SET generation=generation+1 WHERE id=?", (item,))
                else:
                    self.ledger.set_slot_state(reservation["resource"], "held")
                launcher, trees = FakeLauncher(self.root.parent / "runs"), FakeWorktrees(self.root.parent / "worktrees")
                scheduler = Scheduler(self.ledger, launcher, SKILLS, trees,
                                      replay_legacy=True, skill_root=Path(__file__).resolve().parents[1] / "skills", db_path=self.db,
                                      runtime_name="codex", host="test", server_slots_root=self.root)
                self.assertIsNone(scheduler.launch(self.ledger.item(item)))
                self.assertEqual((trees.added, trees.reads, launcher.spawned), ([], [], []))

    def test_a_slot_held_while_the_checkouts_are_prepared_still_stops_the_launch(self):
        """The check before the checkouts does not replace the one after them: preparing a first Farm-Client clone
        can take minutes, and recovery may hold the slot meanwhile."""
        from agent.scheduler import Scheduler
        from test_scheduler import FakeLauncher, FakeWorktrees
        item = self.request(skill="work")
        self.pool.tick()
        reservation = self.ledger.active_reservation(item)

        class HeldMeanwhile(FakeWorktrees):
            def add(inner, *args, **kwargs):
                self.ledger.set_slot_state(reservation["resource"], "held")
                return super().add(*args, **kwargs)

        launcher, trees = FakeLauncher(self.root.parent / "runs"), HeldMeanwhile(self.root.parent / "worktrees")
        scheduler = Scheduler(self.ledger, launcher, SKILLS, trees,
                              replay_legacy=True, skill_root=Path(__file__).resolve().parents[1] / "skills", db_path=self.db,
                              runtime_name="codex", host="test", server_slots_root=self.root)
        self.assertIsNone(scheduler.launch(self.ledger.item(item)))
        self.assertTrue(trees.added)
        self.assertEqual(launcher.spawned, [])

    def test_server_capability_reports_configuration_without_granting_endpoints_or_a_resource(self):
        from agent.scheduler import Scheduler
        from test_scheduler import FakeLauncher, FakeWorktrees
        for skill in ("work", "fix", "feature"):
            for configured in (True, False):
                with self.subTest(skill=skill, configured=configured):
                    item = self.request(skill=skill, reserve=False)
                    launcher = FakeLauncher(self.root.parent / "runs")
                    scheduler = Scheduler(self.ledger, launcher, SKILLS, FakeWorktrees(self.root.parent / "worktrees"),
                        replay_legacy=True, skill_root=Path(__file__).resolve().parents[1] / "skills", db_path=self.db,
                        runtime_name="fake", host="test", server_slots_root=self.root if configured else None)
                    scheduler.launch(self.ledger.item(item))
                    payload = json.loads(launcher.spawned[-1][1].split("\n\n", 1)[1])
                    self.assertEqual(payload["tools"]["server_tests"],
                        {"status": "configured" if configured else "unavailable", "resource": "server_slot"})
                    self.assertIsNone(payload["resource"])
                    self.assertNotIn("MONGO_URI", launcher.spawn_env)
                    self.assertEqual(self.ledger.reservations(), [])


class HandoffSettlementTests(ServerPoolFixture):
    settings = {**SETTINGS, "count": 1}

    def released_handoff(self, *, cancelled=False):
        item = self.request()
        self.assertEqual(self.pool.tick()["granted"], 1)
        lease = self.services.started[0]
        token = self.ledger.claim(item, worker_id="verification")["token"]
        self.ledger.checkpoint(item, token, {"handoff": {
            "facts": [], "hypotheses": [], "checks": [],
            "repositories": [], "next_actions": ["Read any follow-up after delivery"],
        }})
        if cancelled:
            self.ledger.cancel_reservations(item, "server verification stopped")
        state = "cancelled" if cancelled else "released"
        self.assertEqual(self.ledger.release(lease["reservation_id"], lease["token"], "worker quiescent"), state)
        pr = "https://github.com/example/farm-hive/pull/1"
        delivery = self.ledger.prepare_comment(item, token, "delivery", "Server fix: " + pr)
        self.ledger.confirm_comment(delivery["action_id"], "fixture-delivery")
        self.ledger.observe_issue(issue(id=self.ledger.item(item)["issue_id"],
            identifier=f"FARM-{self.sequence}", branch_name=f"farmbot/farm-{self.sequence}",
            description="Follow-up arrived after server verification and delivery."))
        finished = self.ledger.finish(item, token, "delivered", {
            "summary": "Server fix delivered", "verification": "Isolated server verification",
            "prs": [pr], "comment_action_id": delivery["action_id"],
        })
        self.assertEqual((finished["state"], finished["generation"]), ("queued", 1))
        self.assertEqual(self.ledger.reservation(lease["reservation_id"])["generation"], 0)
        return item, lease

    def test_released_lease_waits_for_successor_teardown_then_reuses_only_slot(self):
        item, lease = self.released_handoff()
        token = self.ledger.claim(item, worker_id="follow-up")["token"]
        current = self.ledger.item(item)
        reservation = self.ledger.reservation(lease["reservation_id"])
        journal = self.pool._path(lease["slot"])
        original = journal.read_bytes()
        self.assertEqual(len(self.ledger.slots()), 1)

        self.exited = False
        self.assertEqual(self.pool.tick(), {"settled": 0, "granted": 0})
        self.assertEqual(self.ledger.slot(lease["slot"])["state"], "switching")
        self.assertEqual(journal.read_bytes(), original)
        self.assertFalse(self.services.cleaned)

        self.exited = True
        self.assertEqual(self.pool.tick(), {"settled": 1, "granted": 0})
        self.assertEqual(self.ledger.slot(lease["slot"])["state"], "idle_closed")
        self.assertEqual(self.ledger.item(item), current)
        self.assertEqual(self.ledger.reservation(lease["reservation_id"]), reservation)
        self.assertEqual([entry["reservation_id"] for entry in self.services.cleaned], [lease["reservation_id"]])
        self.assertFalse(journal.exists())
        archived = self.pool.folder(lease["slot"]) / (lease["reservation_id"] + ".json")
        self.assertEqual(json.loads(archived.read_text(encoding="utf-8"))["state"], "cleaned")

        self.ledger.await_resource(item, token, "server_slot", "batch", skill=SKILLS["fix"], commit_sha="b" * 40)
        self.assertEqual(self.pool.tick()["granted"], 1)
        fresh = self.ledger.active_reservation(item)
        self.assertEqual((fresh["resource"], fresh["generation"]), (lease["slot"], 1))
        self.assertNotEqual(fresh["reservation_id"], lease["reservation_id"])

    def test_restart_settles_released_lease_without_changing_successor(self):
        item, lease = self.released_handoff()
        current = self.ledger.item(item)
        restarted = self.make_pool()
        restarted.ensure()
        self.assertEqual(restarted.tick(), {"settled": 1, "granted": 0})
        self.assertEqual(self.ledger.item(item), current)
        self.assertEqual(self.ledger.slot(lease["slot"])["state"], "idle_closed")

    def test_cancelled_lease_can_settle_after_handoff(self):
        item, lease = self.released_handoff(cancelled=True)
        current = self.ledger.item(item)
        self.assertEqual(self.pool.tick()["settled"], 1)
        self.assertEqual(self.ledger.item(item), current)
        self.assertEqual(self.ledger.reservation(lease["reservation_id"])["state"], "cancelled")
        self.assertEqual(self.ledger.slot(lease["slot"])["state"], "idle_closed")

    def assert_held_without_cleanup(self, item, lease):
        current = self.ledger.item(item)
        journal = self.pool._path(lease["slot"])
        original = journal.read_bytes()
        self.assertEqual(self.pool.tick()["settled"], 0)
        self.assertEqual(self.ledger.slot(lease["slot"])["state"], "held")
        self.assertFalse(self.services.cleaned)
        self.assertEqual(journal.read_bytes(), original)
        self.assertEqual(self.ledger.item(item), current)

    def test_handoff_does_not_bypass_journal_ownership(self):
        item, lease = self.released_handoff()
        retained, _ = self.pool._lease(lease["slot"])
        self.pool._save({**retained, "commit": "e" * 40})
        self.assert_held_without_cleanup(item, lease)

    def test_handoff_does_not_bypass_docker_ownership(self):
        item, lease = self.released_handoff()
        self.services.error = ServerSlotError("Docker resource ownership mismatch")
        self.assert_held_without_cleanup(item, lease)

    def test_released_lease_from_a_future_generation_stays_held(self):
        item, lease = self.released_handoff()
        self.ledger.connection.execute("UPDATE reservations SET generation=2 WHERE reservation_id=?",
                                       (lease["reservation_id"],))
        self.assert_held_without_cleanup(item, lease)

    def test_open_cancel_requested_lease_from_an_older_generation_stays_held(self):
        item = self.request()
        self.pool.tick()
        lease = self.services.started[0]
        self.ledger.cancel_reservations(item, "stop old verification")
        self.ledger.connection.execute("UPDATE work_items SET generation=generation+1 WHERE id=?", (item,))
        self.assert_held_without_cleanup(item, lease)
        self.assertEqual(self.ledger.reservation(lease["reservation_id"])["state"], "cancel_requested")

    def test_previously_held_handoff_requires_guarded_recovery(self):
        item, lease = self.released_handoff()
        current = self.ledger.item(item)
        self.ledger.set_slot_state(lease["slot"], "held")
        self.assert_held_without_cleanup(item, lease)
        self.exited = False
        with self.assertRaisesRegex(ServerSlotError, "teardown"):
            self.pool.recover(lease["slot"], "recover retired verification")
        self.assertFalse(self.services.cleaned)
        self.exited = True
        self.pool.recover(lease["slot"], "recover retired verification")
        self.assertEqual(self.ledger.item(item), current)
        self.assertEqual(self.ledger.slot(lease["slot"])["state"], "idle_closed")
        archived = self.pool.folder(lease["slot"]) / (lease["reservation_id"] + ".json")
        self.assertEqual(json.loads(archived.read_text(encoding="utf-8"))["state"], "recovered")


class AdmissionTests(ServerPoolFixture):
    def test_memory_and_commit_pressure_queue_without_starting_or_skipping_tests(self):
        self.admission.observe = lambda: {"available_mib": 20000, "commit_mib": 5000}
        item = self.request()
        self.assertEqual(self.pool.tick()["granted"], 0)
        self.assertEqual(self.ledger.item(item)["state"], "awaiting_resource")
        self.admission.observe = lambda: {"available_mib": 20000, "commit_mib": 20000}
        self.assertEqual(self.pool.tick()["granted"], 1)

    def test_quarantined_resource_still_consumes_budget(self):
        self.admission.settings["max_reserved_mib"] = 4608
        first, second = self.request(), self.request()
        self.pool.tick()
        lease = self.services.started[0]
        self.ledger.set_slot_state(lease["slot"], "held")
        self.assertEqual(self.pool.tick()["granted"], 0)
        self.assertEqual(self.ledger.item(second)["state"], "awaiting_resource")

    def test_unknown_memory_fails_closed(self):
        self.admission.observe = lambda: (_ for _ in ()).throw(OSError())
        self.request()
        self.assertEqual(self.pool.tick()["granted"], 0)
        self.assertIn("unavailable", self.admission.last_reason)

    def test_two_pool_threads_cannot_oversubscribe_shared_budget(self):
        self.admission.settings["max_reserved_mib"] = 4608
        self.request()
        self.request()
        barrier = threading.Barrier(2)
        grants = []
        def acquire():
            connection = self.connection()
            try:
                barrier.wait()
                grants.append(self.admission.acquire(connection, "server_slot", host="test", owner="pool"))
            finally:
                connection.close()
        threads = [threading.Thread(target=acquire) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(sum(grant is not None for grant in grants), 1)


class SettingsTests(unittest.TestCase):
    def test_start_retries_only_read_only_probe_then_rechecks_original_identity(self):
        now = [0]
        services = DockerServices(SETTINGS, clock=lambda: now[0], sleep=lambda seconds: now.__setitem__(0, now[0]+seconds))
        lease = {'docker_binding': {'daemon_id': 'original'}}
        with patch.object(services, 'binding', side_effect=[DockerUnavailable('offline'), {'daemon_id':'other'}]) as binding, \
                patch.object(services, 'command') as command:
            with self.assertRaisesRegex(ServerSlotError, 'daemon/context changed'):
                services.start(lease)
            self.assertEqual(binding.call_count, 2)
            command.assert_not_called()
        now[0] = 0
        with patch.object(services, 'binding', side_effect=DockerUnavailable('offline')), \
                patch.object(services, 'command') as command:
            with self.assertRaises(DockerUnavailable):
                services.start(lease)
            self.assertEqual(now[0], 180)
            command.assert_not_called()

    def test_failed_mutation_is_never_replayed(self):
        services = DockerServices(SETTINGS)
        lease = dict(docker_binding={'daemon_id':'original'}, project='fixture', owner='owner',
                     reservation_id='reservation', slot='server_slot:1')
        with patch.object(services, 'binding', return_value=lease['docker_binding']), \
                patch.object(services, 'inspect', return_value=None), \
                patch.object(services, 'command', side_effect=ServerSlotError('network response lost')) as command:
            with self.assertRaisesRegex(ServerSlotError, 'response lost'):
                services.start(lease)
            command.assert_called_once()

    def test_start_rechecks_inherited_identity_before_creating_resources(self):
        services = DockerServices(SETTINGS)
        binding = {'daemon_id': 'verified', 'endpoint_sha256': '1' * 64}
        for current in ({**binding, 'daemon_id': 'changed'}, {**binding, 'endpoint_sha256': '2' * 64}):
            with self.subTest(current=current), patch.object(services, 'binding', return_value=current), \
                    patch.object(services, 'command') as command:
                with self.assertRaisesRegex(ServerSlotError, 'changed before server setup'):
                    services.start({'docker_binding': binding})
                command.assert_not_called()

    def test_absence_proof_checks_names_and_labels_without_mutations(self):
        services = DockerServices(SETTINGS)
        lease = {'docker_binding': {'daemon_id': 'fixture'}, 'project': 'farmbot-server-test',
                 'owner': 'owner', 'reservation_id': 'lease', 'slot': 'server_slot:1'}
        with patch.object(services, 'binding', return_value=lease['docker_binding']), \
                patch.object(services, 'inspect', return_value=None) as inspect, \
                patch.object(services, 'command', return_value='') as command:
            services.verify_absent(lease)
        self.assertEqual(inspect.call_count, 10)
        self.assertEqual(command.call_count, 6)
        self.assertTrue(all(call.args[1] == 'ls' for call in command.call_args_list))
        self.assertTrue(all('--all' in call.args for call in command.call_args_list if call.args[0] == 'container'))

    def test_absence_proof_refuses_existing_renamed_or_unknown_resources(self):
        services = DockerServices(SETTINGS)
        lease = {'docker_binding': {'daemon_id': 'fixture'}, 'project': 'farmbot-server-test',
                 'owner': 'owner', 'reservation_id': 'lease', 'slot': 'server_slot:1'}
        with patch.object(services, 'binding', return_value=lease['docker_binding']), \
                patch.object(services, 'inspect', return_value={'Id': 'existing'}), \
                patch.object(services, 'command') as command:
            with self.assertRaisesRegex(ServerSlotError, 'found Docker resources'):
                services.verify_absent(lease)
            command.assert_not_called()
        with patch.object(services, 'binding', return_value=lease['docker_binding']), \
                patch.object(services, 'inspect', return_value=None), \
                patch.object(services, 'command', return_value='renamed-resource'):
            with self.assertRaisesRegex(ServerSlotError, 'labelled'):
                services.verify_absent(lease)
        with patch.object(services, 'binding', return_value={'daemon_id': 'different'}), \
                patch.object(services, 'command') as command:
            with self.assertRaisesRegex(ServerSlotError, 'daemon/context'):
                services.verify_absent(lease)
            command.assert_not_called()
        with patch.object(services, 'binding', side_effect=[lease['docker_binding'], {'daemon_id': 'different'}]), \
                patch.object(services, 'inspect', return_value=None), \
                patch.object(services, 'command', return_value=''):
            with self.assertRaisesRegex(ServerSlotError, 'during missing-journal'):
                services.verify_absent(lease)

    def test_invalid_or_oversized_budgets_and_unpinned_images_refused(self):
        for raw in ({"unity_mib": 100, "max_reserved_mib": 10}, {"min_commit_mib": True}, {"typo": 1}):
            with self.assertRaises(ValueError):
                validate_admission(raw)
        for raw in ({**SETTINGS, "count": True}, {**SETTINGS, "images": {**IMAGES, "mongo": "mongo:latest"}},
                    {**SETTINGS, "docker": "docker"}, {**SETTINGS, "context": "-remote"}):
            with self.assertRaises(ValueError):
                validate_settings(raw)
        with self.assertRaisesRegex(ValueError, "resource_admission"):
            Config("dummy", "dummy", "dummy", server_slots=SETTINGS)

    def test_remote_docker_and_undersized_backend_refused_without_creating_resources(self):
        services = DockerServices(SETTINGS)
        with patch.object(services, "command", return_value=json.dumps([{"Endpoints": {"docker": {"Host": "tcp://remote:2375"}}}])):
            with self.assertRaisesRegex(ServerSlotError, "local"):
                services.preflight(3)
        with patch.object(services, "command", side_effect=[json.dumps([{"Endpoints": {"docker": {"Host": "npipe:////./pipe/local"}}}]),
                                                          json.dumps({"OSType": "linux", "ID": "fixture"}),
                                                          json.dumps({"OSType": "linux", "MemTotal": 1024})]):
            with self.assertRaisesRegex(ServerSlotError, "memory"):
                services.preflight(3)

    def test_docker_environment_does_not_inherit_remote_selectors(self):
        calls = []
        def run(argv, **options):
            calls.append((argv, options))
            return subprocess.CompletedProcess(argv, 0, "ok", "")
        with patch.dict("os.environ", {"DOCKER_HOST": "tcp://live", "COMPOSE_PROJECT_NAME": "live", "GH_TOKEN": "dummy"}):
            self.assertEqual(DockerServices(SETTINGS, run=run).command("info"), "ok")
        self.assertEqual(calls[0][0][1:3], ["--context", "default"])
        self.assertFalse({"DOCKER_HOST", "COMPOSE_PROJECT_NAME", "GH_TOKEN"} & calls[0][1]["env"].keys())

    def test_foreign_resource_makes_cleanup_leave_everything_intact(self):
        services = DockerServices(SETTINGS)
        lease = {"project": "farmbot-server-test", "owner": "owner", "reservation_id": "lease", "slot": "server_slot:1"}
        with patch.object(services, "binding", return_value=None), patch.object(services, "command", return_value=json.dumps([{"Id": "foreign", "Config": {"Labels": {}}}])) as command:
            with self.assertRaisesRegex(ServerSlotError, "ownership"):
                services.cleanup(lease)
        self.assertTrue(all(call.args[1] == "inspect" for call in command.call_args_list))

    def test_docker_network_absence_is_distinguished_from_daemon_failure(self):
        def run(argv, **options):
            return subprocess.CompletedProcess(argv, 1, "", "Error response from daemon: network fixture not found")
        service = DockerServices(SETTINGS, run=run)
        self.assertIsNone(service.command("network", "inspect", "fixture", missing=True))
        with self.assertRaises(ServerSlotError):
            service.command("network", "inspect", "other-name", missing=True)

    def test_daemon_change_and_remote_named_pipe_cannot_authorize_cleanup(self):
        services = DockerServices(SETTINGS)
        with patch.object(services, "binding", return_value={"daemon_id": "new"}), patch.object(services, "command") as command:
            with self.assertRaisesRegex(ServerSlotError, "daemon/context"):
                services.cleanup({"docker_binding": {"daemon_id": "old"}})
            command.assert_not_called()
        with patch.object(services, "command", return_value=json.dumps([
                {"Endpoints": {"docker": {"Host": "npipe:////remote/pipe/docker_engine"}}}])):
            with self.assertRaisesRegex(ServerSlotError, "local"):
                services.binding()

    def test_relay_source_provenance_is_canonical_across_checkout_line_endings(self):
        with patch.object(Path, "read_bytes", return_value=b"fixed source\r\n"):
            windows = relay_source_digest()
        with patch.object(Path, "read_bytes", return_value=b"fixed source\n"):
            self.assertEqual(relay_source_digest(), windows)

    def test_wrong_relay_provenance_is_a_read_only_preflight_failure(self):
        services = DockerServices(SETTINGS)
        def command(*args, **options):
            if args[0] == "context":
                return json.dumps([{"Endpoints": {"docker": {"Host": "npipe:////./pipe/local"}}}])
            if args[0] == "info":
                return json.dumps({"OSType": "linux", "ID": "fixture", "MemTotal": 16 * 1024**3})
            self.assertEqual(args[:2], ("image", "inspect"))
            role = next(role for role, identity in IMAGES.items() if identity == args[2])
            entrypoint = {"etcd": ["/etcd"], "nats": ["/nats-server"], "proxy": ["/server-proxy"]}.get(role)
            return json.dumps([{"Os": "linux", "Config": {"Entrypoint": entrypoint, "Labels": {}}}])
        with patch.object(services, "command", side_effect=command):
            with self.assertRaisesRegex(ServerSlotError, "provenance"):
                services.preflight(3)


class ToolPreparationTests(unittest.TestCase):
    def test_packaging_cannot_adopt_existing_state_or_accept_relative_tools(self):
        from agent.server_slot_tools import prepare
        with tempfile.TemporaryDirectory() as temporary, patch("agent.server_slot_tools.DockerServices") as docker:
            root = Path(temporary).resolve()
            with self.assertRaises(ValueError):
                prepare("go", root / "docker", root / "new")
            with self.assertRaises(ValueError):
                prepare(root / "go", root / "docker", root)
            docker.assert_not_called()

    def test_wrong_go_version_stops_before_build_or_image_mutation(self):
        from agent.server_slot_tools import prepare
        with tempfile.TemporaryDirectory() as temporary, patch("agent.server_slot_tools.DockerServices") as docker:
            root = Path(temporary).resolve()
            docker.return_value.binding.return_value = {"daemon_id": "fixture"}
            docker.return_value.command.return_value = json.dumps({"Architecture": "x86_64"})
            with patch("agent.server_slot_tools.subprocess.check_output", return_value="go version go1.26.5 windows/amd64"), \
                    patch("agent.server_slot_tools.subprocess.run") as run:
                with self.assertRaisesRegex(ValueError, "pinned Go"):
                    prepare(root / "go", root / "docker", root / "new")
                run.assert_not_called()
            self.assertTrue(all(call.args[0] == "info" for call in docker.return_value.command.call_args_list))
