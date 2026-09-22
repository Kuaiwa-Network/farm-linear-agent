"""Real ledger transitions; only external worker/editor operations are substituted."""
import json
import unittest
from pathlib import Path

from agent.ledger import LedgerError
from test_ledger import LedgerBase, PIN


class RecoveryTests(LedgerBase):
    def running_with_slot(self):
        item = self.new_item()
        claim = self.ledger.claim(item['id'], worker_id='before-resource')
        self.ledger.await_resource(item['id'], claim['token'], 'unity_slot', 'interactive', commit_sha='b' * 40)
        self.ledger.ensure_slot('unity_slot:1', kind='unity_slot', host='test', folder=str(self.path.parent / 'slot-1'))
        reservation = self.ledger.acquire('unity_slot', owner='pool', host='test')
        self.ledger.set_slot_state('unity_slot:1', 'interactive_busy')
        self.ledger.resume(item['id'], 'resource ready')
        claim = self.ledger.claim(item['id'], worker_id='test-worker')
        self.ledger.set_worker(item['id'], 4242, 'test')
        self.ledger.checkpoint(item['id'], claim['token'], {'stage': 'verify-fix', 'saved': 'keep me'})
        return item['id'], claim['token'], reservation

    def store(self, ledger=None):
        from agent.resource_recovery import RecoveryStore
        return RecoveryStore(ledger or self.ledger)

    def begin(self):
        store = self.store()
        return store.begin(store.pending('test')[0]['id'])

    def test_stale_attempt_cannot_detach_publish_or_fail_new_attempt(self):
        _, _, r = self.running_with_slot()
        self.ledger.hold(r['reservation_id'], 'stalled')
        first = self.begin()
        self.now += 901
        second = self.begin()
        store = self.store()
        for action in (lambda: store.detach(first['id'], first['attempts']),
                       lambda: store.complete(first['id'], first['attempts'], 'b' * 40, 'instance'),
                       lambda: store.failed(first['id'], first['attempts'], 'late failure')):
            with self.assertRaisesRegex(ValueError, 'stale'):
                action()
        self.assertEqual(store.get(first['id']), second)
        self.assertEqual(self.ledger.slot('unity_slot:1')['state'], 'held')

    def test_expired_lease_cannot_start_overlapping_physical_repair(self):
        _, _, r = self.running_with_slot()
        self.ledger.hold(r['reservation_id'], 'stalled')
        from agent.resource_recovery import RecoveryController
        calls = []
        other = RecoveryController(self.open_ledger(), None, host='test', inspect=lambda s: {},
                                   fence=lambda r: None, repair=lambda r: calls.append('overlap'),
                                   evidence_root=self.path.parent / 'recovery')
        def repair(record):
            self.now += 901
            self.assertEqual(other.tick()['repaired'], 0)
            self.assertEqual(calls, [])
            self.assertEqual(self.store().get(record['id'])['attempts'], 1)
            return 'b' * 40, 'instance'
        controller = RecoveryController(self.ledger, None, host='test', inspect=lambda s: {},
                                        fence=lambda r: None, repair=repair,
                                        evidence_root=self.path.parent / 'recovery')
        self.assertEqual(controller.tick()['repaired'], 1)

    def test_unclean_release_revokes_worker_and_waits_for_controller_without_question(self):
        item, token, reservation = self.running_with_slot()
        self.ledger.hold_owned(reservation['reservation_id'], reservation['token'], 'test stalled')
        row = self.ledger.item(item)
        self.assertEqual((row['state'], row['stage']), ('awaiting_resource', 'waiting_for_recovery'))
        self.assertEqual(row['checkpoint']['saved'], 'keep me')
        self.assertIsNone(row['worker_pid'])
        with self.assertRaises(LedgerError):
            self.ledger.renew(item, token)
        self.assertEqual(self.ledger.slot('unity_slot:1')['state'], 'held')
        self.assertEqual(self.store().pending('test')[0]['worker_pid'], 4242)

    def test_detach_preserves_exact_fix_commit_and_allows_other_slot_before_repair(self):
        item, _, r = self.running_with_slot()
        self.ledger.hold_owned(r['reservation_id'], r['token'], 'stalled')
        recovery = self.begin()
        self.store().detach(recovery['id'], recovery['attempts'])
        self.ledger.ensure_slot('unity_slot:2', kind='unity_slot', host='test', folder=str(self.path.parent / 'slot-2'))
        replacement = self.ledger.acquire('unity_slot', owner='pool', host='test')
        self.assertEqual((replacement['item_id'], replacement['commit_sha'], replacement['mode'], replacement['resource']),
                         (item, 'b' * 40, 'interactive', 'unity_slot:2'))
        self.assertNotEqual(replacement['reservation_id'], r['reservation_id'])
        with self.assertRaises(LedgerError):
            self.ledger.release(r['reservation_id'], r['token'], 'late old worker')
        self.assertEqual(self.ledger.slot('unity_slot:1')['state'], 'held')

    def test_detach_is_idempotent_after_restarting_ledger(self):
        _, _, r = self.running_with_slot()
        self.ledger.hold_owned(r['reservation_id'], r['token'], 'stalled')
        recovery = self.begin()
        self.store().detach(recovery['id'], recovery['attempts'])
        self.store(self.open_ledger()).detach(recovery['id'], recovery['attempts'])
        self.assertEqual(len(self.ledger.reservations(states=('queued',))), 1)
        self.assertEqual(self.store().job_attempts(r['item_id']), 1)

    def test_stop_during_recovery_does_not_resurrect_job(self):
        item, _, r = self.running_with_slot()
        self.ledger.hold_owned(r['reservation_id'], r['token'], 'stalled')
        recovery = self.begin()
        self.ledger.cancel(item, 'user Stop')
        self.store().detach(recovery['id'], recovery['attempts'])
        self.assertEqual(self.ledger.item(item)['state'], 'cancelled')
        self.assertEqual(self.ledger.reservations(states=('queued', 'active', 'cancel_requested')), [])

    def test_held_slot_does_not_turn_genuine_question_into_automatic_continuation(self):
        item, token, r = self.running_with_slot()
        self.ledger.await_input(item, token, 'Which server should we test?')
        self.ledger.hold(r['reservation_id'], 'cannot settle editor')
        recovery = self.begin()
        self.store().detach(recovery['id'], recovery['attempts'])
        self.assertEqual(self.ledger.item(item)['state'], 'awaiting_input')
        self.assertEqual(self.ledger.issue_context(item)['pending_question'], 'Which server should we test?')
        self.assertEqual(self.ledger.reservations(states=('queued',)), [])

    def test_explicit_legacy_adoption_preserves_checkpoint_and_removes_obsolete_question(self):
        item, token, r = self.running_with_slot()
        self.ledger.await_input(item, token, 'Legacy request to operate the Unity host')
        self.ledger.set_slot_state('unity_slot:1', 'held')
        recovery = self.store().adopt('unity_slot:1', 'verified infrastructure-only pause')
        row = self.ledger.item(item)
        self.assertEqual(row['state'], 'awaiting_resource')
        self.assertEqual(row['checkpoint']['saved'], 'keep me')
        self.assertNotIn('pending_question', row['checkpoint'])
        self.assertEqual(recovery['commit_sha'], r['commit_sha'])
        self.assertEqual(recovery['resume_job'], 1)

    def test_repeated_hangs_exhaust_job_budget_without_losing_checkpoint(self):
        item, _, r = self.running_with_slot()
        for attempt in range(3):
            self.ledger.hold(r['reservation_id'], 'same test stalled')
            store = self.store(self.open_ledger())
            recovery = self.begin()
            store.detach(recovery['id'], recovery['attempts'])
            store.complete(recovery['id'], recovery['attempts'], 'b' * 40, 'instance-1')
            if attempt < 2:
                r = self.ledger.acquire('unity_slot', owner='pool', host='test')
                self.ledger.set_slot_state('unity_slot:1', 'interactive_busy')
                self.ledger.resume(item, 'retry ready')
                self.ledger.claim(item, worker_id='retry')
        self.assertEqual(self.ledger.item(item)['state'], 'failed')
        self.assertEqual(self.ledger.item(item)['checkpoint']['saved'], 'keep me')
        self.assertEqual(self.ledger.reservations(states=('queued',)), [])
        self.assertIn('repeated', self.store().notifications()[0]['body'])

    def test_repair_backoff_and_attempts_survive_restart(self):
        _, _, r = self.running_with_slot()
        self.ledger.hold(r['reservation_id'], 'stalled')
        store = self.store()
        recovery = store.pending('test')[0]
        for attempt in range(3):
            record = store.begin(recovery['id'])
            self.assertEqual(record['attempts'], attempt + 1)
            store.failed(recovery['id'], record['attempts'], 'editor cannot start')
            store = self.store(self.open_ledger())
            self.assertEqual(store.due('test'), [])
            self.now += 601
        self.assertEqual(store.pending('test'), [])
        self.assertEqual(self.ledger.slot('unity_slot:1')['state'], 'held')

    def test_two_recovery_consumers_cannot_lease_the_same_repair(self):
        _, _, r = self.running_with_slot()
        self.ledger.hold(r['reservation_id'], 'stalled')
        recovery = self.store().pending('test')[0]
        self.assertIsNotNone(self.store().begin(recovery['id']))
        self.assertIsNone(self.store(self.open_ledger()).begin(recovery['id']))

    def test_watchdog_requires_observed_lack_of_progress_not_active_flag(self):
        item, _, r = self.running_with_slot()
        from agent.resource_recovery import RecoveryController
        snapshot = {'state': {'tests': {'is_running': True, 'current_job_id': 'job1'}},
                    'job': {'status': 'running', 'progress': {'completed': 17}, 'last_update_unix_ms': 100}}
        controller = RecoveryController(self.ledger, None, host='test', inspect=lambda slot: snapshot,
                                        fence=lambda r: None, repair=lambda r: ('b' * 40, 'instance'),
                                        evidence_root=self.path.parent / 'recovery')
        controller.watch()
        self.now += 179
        controller.watch()
        self.assertEqual(self.ledger.item(item)['state'], 'running')
        snapshot['job']['progress']['completed'] = 18
        controller.watch()
        self.now += 179
        controller.watch()
        self.assertEqual(self.ledger.item(item)['state'], 'running')
        self.now += 2
        controller.watch()
        self.assertEqual(self.ledger.item(item)['stage'], 'waiting_for_recovery')
        self.assertTrue(list((self.path.parent / 'recovery').glob('*/watchdog.json')))

    def test_controller_never_repairs_or_detaches_before_worker_teardown(self):
        _, _, r = self.running_with_slot()
        self.ledger.hold(r['reservation_id'], 'stalled')
        from agent.resource_recovery import RecoveryController
        def refuse(record):
            raise RuntimeError('old worker still alive')
        def repair(record):
            self.fail('must not touch editor before teardown')
        controller = RecoveryController(self.ledger, None, host='test', inspect=lambda slot: {},
                                        fence=refuse, repair=repair, evidence_root=self.path.parent / 'recovery')
        controller.tick()
        self.assertEqual(self.ledger.reservation(r['reservation_id'])['state'], 'active')
        self.assertEqual(self.ledger.reservations(states=('queued',)), [])
        self.assertEqual(self.store().pending('test')[0]['attempts'], 1)

    def test_all_exhausted_slots_finish_waiting_job_with_infrastructure_failure(self):
        item, _, r = self.running_with_slot()
        self.ledger.hold(r['reservation_id'], 'stalled')
        recovery = self.begin()
        self.store().detach(recovery['id'], recovery['attempts'])
        for index in range(3):
            record = recovery if index == 0 else self.store().begin(recovery['id'])
            self.store().failed(recovery['id'], record['attempts'], 'editor startup failed')
            self.now += 601
        self.store().fail_unserviceable('test')
        self.assertEqual(self.ledger.item(item)['state'], 'failed')
        self.assertIn('infrastructure', self.store().notifications()[0]['body'])

    def test_repair_keeps_other_slot_schedulable(self):
        item, _, r = self.running_with_slot()
        self.ledger.hold(r['reservation_id'], 'stalled')
        self.ledger.ensure_slot('unity_slot:2', kind='unity_slot', host='test', folder=str(self.path.parent / 'slot-2'))
        from agent.resource_recovery import RecoveryController
        def repair(record):
            replacement = self.ledger.acquire('unity_slot', owner='pool', host='test')
            self.assertEqual((replacement['item_id'], replacement['resource']), (item, 'unity_slot:2'))
            return 'b' * 40, 'instance-1'
        controller = RecoveryController(self.ledger, None, host='test', inspect=lambda slot: {},
                                        fence=lambda r: None, repair=repair, evidence_root=self.path.parent / 'recovery')
        controller.tick()
        self.assertEqual(self.ledger.slot('unity_slot:1')['state'], 'idle_open')


if __name__ == '__main__':
    unittest.main()
