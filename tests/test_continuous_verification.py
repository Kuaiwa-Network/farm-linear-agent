"""Real SQLite continuation, progress and evidence fences without production resources."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent.config import Config, Paths
from agent.ledger import LedgerError
from agent.resource_admission import ResourceAdmission
from agent.test_baseline import ComparisonError
from agent.verification import compare_recorded_pair
from test_monitor_view import ViewBase
from test_ledger import SKILLS
from test_test_baseline import run, test


class ContinuousVerificationTests(ViewBase):
    def baseline(self):
        item = self.job()
        self.sql("UPDATE work_items SET root_repo='Farm-Client' WHERE id=?", item['id'])
        token = self.claim(item)['token']
        self.ledger.await_resource(item['id'], token, 'unity_slot', 'interactive', skill=SKILLS['fix'],
                                   commit_sha='a' * 40, comparison_candidate='b' * 40)
        self.ledger.ensure_slot('unity_slot:1', kind='unity_slot', host='test-host', folder=str(self.path.parent / 'slot'))
        reservation = self.ledger.acquire('unity_slot', owner='pool', host='test-host')
        self.ledger.set_slot_state('unity_slot:1', 'interactive_busy')
        self.ledger.resume(item['id'], 'baseline prepared')
        token = self.claim(item)['token']
        return item, token, reservation

    def queued_other(self):
        item = self.job()
        token = self.claim(item)['token']
        self.ledger.await_resource(item['id'], token, 'unity_slot', 'interactive', skill=SKILLS['fix'])
        return item

    def continue_pair(self, item, token, baseline, **kwargs):
        return self.ledger.await_resource(item['id'], token, 'unity_slot', 'interactive', skill=SKILLS['fix'],
            commit_sha=kwargs.get('candidate', 'b' * 40),
            continuation=(baseline['reservation_id'], kwargs.get('resource_token', baseline['token'])))

    def test_continuation_is_atomic_next_turn_with_distinct_immutable_history(self):
        item, token, baseline = self.baseline()
        older = self.queued_other()
        before = [(r['reservation_id'], r['sequence']) for r in self.ledger.reservations()]
        result = self.continue_pair(item, token, baseline)
        self.assertEqual(result['state'], 'awaiting_resource')
        self.assertEqual(result['target']['commit_sha'], 'a' * 40)
        rows = self.ledger.reservations()
        self.assertEqual(before, [(r['reservation_id'], r['sequence']) for r in rows[:-1]])
        self.assertEqual(rows[0]['state'], 'released')
        self.assertEqual(rows[-1]['commit_sha'], 'b' * 40)
        self.assertNotEqual(rows[-1]['reservation_id'], baseline['reservation_id'])
        self.assertEqual([r['item_id'] for r in self.ledger.reservation_queue('unity_slot')], [item['id'], older['id']])
        with self.assertRaises(LedgerError):
            self.ledger.renew(item['id'], token)
        self.assertEqual(self.ledger.slot('unity_slot:1')['state'], 'switching')
        self.assertIsNone(self.ledger.acquire('unity_slot', owner='pool', host='test-host'))
        # A restarted controller reconstructs the same next turn; no in-memory priority.
        from agent.ledger import Ledger
        other_connection = Ledger(self.path, clock=lambda: self.clock)
        self.addCleanup(other_connection.close)
        self.assertEqual(other_connection.reservation_queue('unity_slot')[0]['item_id'], item['id'])
        self.assertEqual(other_connection.pending_unity_workers('test-host'), {item['id']})
        self.assertEqual(other_connection.pending_unity_workers('another-host'), set())
        self.ledger.set_slot_state('unity_slot:1', 'idle_open')
        document = self.status()
        self.assertEqual(self.active(document, 'FARM-1')['queue_position'], 1)
        self.assertEqual(self.active(document, 'FARM-2')['queue_position'], 2)
        admission = ResourceAdmission({}, observe=lambda: {'available_mib': 100000, 'commit_mib': 100000})
        granted = admission.acquire(self.ledger, 'unity_slot', owner='pool', host='test-host',
                                    expected_reservation=rows[-1]['reservation_id'])
        self.assertEqual(granted['item_id'], item['id'])
        self.assertEqual(granted['resource'], baseline['resource'])

    def test_continuation_does_not_steal_other_slot_during_settlement(self):
        item, token, baseline = self.baseline()
        self.ledger.ensure_slot('unity_slot:2', kind='unity_slot', host='test-host', folder=str(self.path.parent / 'slot2'))
        self.continue_pair(item, token, baseline)
        self.assertIsNone(self.ledger.acquire('unity_slot', owner='pool', host='test-host'))
        self.ledger.set_slot_state('unity_slot:1', 'idle_open')
        self.assertEqual(self.ledger.acquire('unity_slot', owner='pool', host='test-host')['resource'], 'unity_slot:1')

    def test_older_request_after_continuation_becomes_actual_departing_owner(self):
        item, token, baseline = self.baseline()
        older = self.queued_other()
        self.continue_pair(item, token, baseline)
        self.ledger.set_slot_state('unity_slot:1', 'idle_open')
        candidate = self.ledger.acquire('unity_slot', owner='pool', host='test-host')
        self.ledger.release(candidate['reservation_id'], candidate['token'], 'worker reported quiescent')
        self.ledger.set_slot_state('unity_slot:1', 'idle_open')
        other = self.ledger.acquire('unity_slot', owner='pool', host='test-host')
        self.assertEqual(other['item_id'], older['id'])
        self.assertLess(other['sequence'], candidate['sequence'])
        self.assertEqual(self.ledger.last_reservation_on('unity_slot:1')['reservation_id'], other['reservation_id'])
        self.ledger.release(other['reservation_id'], other['token'], 'worker reported quiescent')
        self.assertEqual(self.ledger.last_reservation_on('unity_slot:1')['reservation_id'], other['reservation_id'])

    def test_invalid_continuation_leaves_baseline_and_claim_intact(self):
        item, token, baseline = self.baseline()
        for arguments in ({'candidate': 'c' * 40}, {'resource_token': token}, {'resource_token': 'stale'}):
            with self.subTest(arguments=list(arguments)), self.assertRaises(LedgerError):
                self.continue_pair(item, token, baseline, **arguments)
            self.assertEqual(self.ledger.reservations()[0]['state'], 'active')
            self.assertEqual(len(self.ledger.reservations()), 1)
            self.assertEqual(self.ledger.item(item['id'])['state'], 'running')
        self.ledger.set_slot_state('unity_slot:1', 'held')
        with self.assertRaises(LedgerError):
            self.continue_pair(item, token, baseline)
        self.ledger.set_slot_state('unity_slot:1', 'interactive_busy')
        self.sql('UPDATE work_items SET withdraw_deadline=? WHERE id=?', self.clock + 100, item['id'])
        with self.assertRaises(LedgerError):
            self.continue_pair(item, token, baseline)

    def test_monitor_unity_position_excludes_server_queue(self):
        server = self.job()
        self.sql("UPDATE work_items SET root_repo='farm-hive' WHERE id=?", server['id'])
        token = self.claim(server)['token']
        self.ledger.await_resource(server['id'], token, 'server_slot', 'batch', skill=SKILLS['fix'],
                                   commit_sha='a' * 40)
        self.queued_other()
        self.assertEqual(self.active(self.status(), 'FARM-2')['queue_position'], 1)

    def test_cancel_during_validation_and_rejected_checkpoint_do_not_release(self):
        item, token, baseline = self.baseline()
        self.ledger.continuation_baseline(item['id'], token, baseline['token'], 'b' * 40)
        with self.assertRaises(LedgerError):
            self.ledger.checkpoint(item['id'], token, {'handoff': {'invalid': True}})
        with self.assertRaisesRegex(LedgerError, 'checkpoint'):
            self.continue_pair(item, token, baseline)
        self.assertEqual(self.ledger.reservations()[0]['state'], 'active')
        self.ledger.cancel(item['id'], 'Stop during Git validation')
        with self.assertRaises(LedgerError):
            self.continue_pair(item, token, baseline)
        self.assertEqual(len(self.ledger.reservations()), 1)

    def test_priority_expires_when_slot_used_elsewhere_and_cannot_chain(self):
        item, token, baseline = self.baseline()
        older = self.queued_other()
        self.continue_pair(item, token, baseline)
        # A failed settlement loses the continuation; another job can use a recovered slot.
        self.ledger.set_slot_state('unity_slot:1', 'held')
        self.assertEqual(self.ledger.reservation_queue('unity_slot')[0]['item_id'], older['id'])
        self.assertEqual(self.ledger.pending_unity_workers('test-host'), set())
        self.ledger.ensure_slot('unity_slot:2', kind='unity_slot', host='test-host', folder=str(self.path.parent / 'slot2'))
        other = self.ledger.acquire('unity_slot', owner='pool', host='test-host')
        # Simulate an intervening reservation on the original slot (e.g. legacy controller).
        self.sql("UPDATE reservations SET resource='unity_slot:1' WHERE reservation_id=?", other['reservation_id'])
        self.ledger.release(other['reservation_id'], other['token'], 'worker reported quiescent')
        self.ledger.set_slot_state('unity_slot:1', 'idle_open')
        from agent.resource_queue import continuation_slot
        queued = self.ledger.connection.execute("SELECT * FROM reservations WHERE state='queued'").fetchone()
        self.assertIsNone(continuation_slot(self.ledger.connection, queued))
        candidate = self.ledger.acquire('unity_slot', owner='pool', host='test-host')
        self.ledger.set_slot_state(candidate['resource'], 'interactive_busy')
        self.ledger.resume(item['id'], 'candidate prepared')
        token = self.claim(item)['token']
        with self.assertRaises(LedgerError):
            self.continue_pair(item, token, candidate, candidate='c' * 40)

    def completed_pair(self):
        item, token, baseline = self.baseline()
        self.continue_pair(item, token, baseline)
        self.ledger.set_slot_state('unity_slot:1', 'idle_open')
        candidate = self.ledger.acquire('unity_slot', owner='pool', host='test-host')
        self.ledger.release(candidate['reservation_id'], candidate['token'], 'worker reported quiescent')
        self.ledger.set_slot_state('unity_slot:1', 'idle_open')
        self.ledger.resume(item['id'], 'candidate finished')
        token = self.claim(item)['token']
        config = Config('test', 'test', 'test', local_root=self.path.parent)
        paths = Paths(config)
        # This fixture's ledger has a custom path; bind the helper to the exact test DB.
        paths.ledger = self.path
        root = paths.runs / item['id']
        root.mkdir(parents=True)
        rows = [test('Animal.Counter'), test('Card.Cleanup', 'Failed:Error', 'package missing', 'Card.Cleanup()')]
        data = [run('a', copy.deepcopy(rows)), run('b', copy.deepcopy(rows)),
                {'affected_tests': ['Animal.Counter'], 'changed_paths': ['Assets/Animal/Tuning.cs'],
                 'unrelated_failures': {'Card.Cleanup': {'reason': 'unchanged dependencies', 'evidence': 'source review'}}}]
        data[0]['reservation_id'] = baseline['reservation_id']
        data[1]['reservation_id'] = candidate['reservation_id']
        files = [root / name for name in ('baseline.json', 'candidate.json', 'scope.json')]
        for file, value in zip(files, data):
            file.write_text(json.dumps(value), encoding='utf-8')
        args = SimpleNamespace(item=item['id'], db=str(self.path), baseline=str(files[0]),
                               candidate=str(files[1]), scope=str(files[2]))
        self.addCleanup(patch.stopall)
        patch('agent.verification.Paths', return_value=paths).start()
        return item, token, config, args, data

    def test_comparison_publishes_verdict_immediately_and_preserves_checkpoint(self):
        item, token, config, args, data = self.completed_pair()
        checkpoint = {'stage': 'old', 'retained_evidence': ['baseline', 'candidate'], 'custom_progress': {'complete': True}}
        self.ledger.checkpoint(item['id'], token, checkpoint)
        report = compare_recorded_pair(args, self.ledger, config, token)
        self.assertTrue(report['draft_eligible'])
        self.assertFalse(report['full_suite_passed'])
        self.assertEqual(self.ledger.item(item['id'])['stage'], 'client-verified-awaiting-publication')
        saved = self.ledger.item(item['id'])['checkpoint']
        self.assertEqual(saved['custom_progress'], checkpoint['custom_progress'])
        self.assertEqual(saved['retained_evidence'], checkpoint['retained_evidence'])
        self.ledger.verification_progress(item['id'], token, 'publication')
        self.assertEqual(self.active(self.status(), 'FARM-1')['stage'], 'client-publication-check')

    def test_comparison_blocker_and_missing_or_foreign_evidence_have_distinct_stages(self):
        item, token, config, args, data = self.completed_pair()
        data[1]['tests'][1]['message'] = 'new failure signature'
        Path(args.candidate).write_text(json.dumps(data[1]), encoding='utf-8')
        self.assertFalse(compare_recorded_pair(args, self.ledger, config, token)['draft_eligible'])
        self.assertEqual(self.ledger.item(item['id'])['stage'], 'client-verification-blocked')
        data[1]['reservation_id'] = 'another-job-reservation'
        Path(args.candidate).write_text(json.dumps(data[1]), encoding='utf-8')
        with self.assertRaisesRegex(ComparisonError, 'controller-selected pair'):
            compare_recorded_pair(args, self.ledger, config, token)
        self.assertEqual(self.ledger.item(item['id'])['stage'], 'client-verification-evidence-gap')
        Path(args.candidate).unlink()
        with self.assertRaises((OSError, ComparisonError)):
            compare_recorded_pair(args, self.ledger, config, token)

    def test_retry_recompares_completed_reports_without_requesting_another_baseline(self):
        item, token, config, args, data = self.completed_pair()
        for value, deadline, actual in ((data[0], '54.5053902', '60.6000252'),
                                        (data[1], '53.995945', '53.9964981')):
            value['tests'][1].update(state='Failed', message=(
                f'timed out waiting for the entry result\nExpected: less than {deadline}f\nBut was: {actual}f'),
                stackTrace='at Card.Cleanup() in Assets/Tests/Card.cs:94')
        data[2]['unrelated_failures']['Card.Cleanup']['clock_diagnostic'] = {
            'kind': 'unity_realtime_deadline', 'clock': 'UnityEngine.Time.realtimeSinceStartup',
            'source_path': 'Assets/Tests/Card.cs', 'baseline_source_sha256': '7' * 64,
            'candidate_source_sha256': '7' * 64, 'timeout_seconds': 15,
            'reason': 'unchanged assertion, predicate and dependencies', 'evidence': 'committed source audit'}
        files = [Path(args.baseline), Path(args.candidate), Path(args.scope)]
        for file, value in zip(files, data):
            file.write_text(json.dumps(value), encoding='utf-8')
        original_files = [file.read_bytes() for file in files]
        original_reservations = self.ledger.reservations()
        action = self.ledger.prepare_comment(item['id'], token, 'blocker', 'old rule rejected late baseline')
        self.ledger.confirm_comment(action['action_id'], 'stub-comment')
        self.ledger.finish(item['id'], token, 'blocked', {'summary': 'old rule rejected late baseline',
                                                        'comment_action_id': action['action_id']})
        resumed = self.ledger.retry(item['id'], 'operator requested offline recompare after rule correction')
        # The scheduler reopens the retained Client stage before comparison.
        self.sql("UPDATE work_items SET root_repo='Farm-Client' WHERE id=?", item['id'])
        new_token = self.claim(resumed)['token']
        with self.assertRaises(LedgerError):
            compare_recorded_pair(args, self.ledger, config, token)
        report = compare_recorded_pair(args, self.ledger, config, new_token)
        self.assertTrue(report['draft_eligible'])
        self.assertFalse(report['full_suite_passed'])
        self.assertEqual(self.ledger.item(item['id'])['stage'], 'client-verified-awaiting-publication')
        self.assertEqual(self.ledger.reservations(), original_reservations)
        self.assertEqual([file.read_bytes() for file in files], original_files)

    def test_later_diagnostic_does_not_discard_original_full_suite_blocker(self):
        item, token, config, args, data = self.completed_pair()
        data[1]['tests'][1]['message'] = 'new failure signature'
        Path(args.candidate).write_text(json.dumps(data[1]), encoding='utf-8')
        expected = compare_recorded_pair(args, self.ledger, config, token)
        self.assertFalse(expected['draft_eligible'])
        self.ledger.await_resource(item['id'], token, 'unity_slot', 'interactive', skill=SKILLS['fix'],
                                   commit_sha='b' * 40)
        focused = self.ledger.acquire('unity_slot', owner='pool', host='test-host')
        self.ledger.release(focused['reservation_id'], focused['token'], 'worker reported quiescent')
        self.ledger.set_slot_state('unity_slot:1', 'idle_open')
        self.ledger.resume(item['id'], 'focused diagnosis finished')
        token = self.claim(item)['token']
        files = [Path(args.baseline), Path(args.candidate), Path(args.scope)]
        original_files = [file.read_bytes() for file in files]
        original_reservations = self.ledger.reservations()
        actual = compare_recorded_pair(args, self.ledger, config, token)
        self.assertEqual(actual, expected)
        self.assertEqual(self.ledger.item(item['id'])['stage'], 'client-verification-blocked')
        self.assertEqual(self.ledger.reservations(), original_reservations)
        self.assertEqual([file.read_bytes() for file in files], original_files)

        # A focused passing report still cannot substitute for the complete selection.
        focused_data = run('b', [test('Animal.Counter')])
        focused_data['reservation_id'] = focused['reservation_id']
        focused_data['selection'] = {'test_names': ['Animal.Counter']}
        focused_file = Path(args.candidate).with_name('focused.json')
        focused_file.write_text(json.dumps(focused_data), encoding='utf-8')
        args.candidate = str(focused_file)
        self.assertFalse(compare_recorded_pair(args, self.ledger, config, token)['draft_eligible'])

    def test_comparison_requires_own_state_dir_settled_pair_and_current_claim(self):
        item, token, config, args, data = self.completed_pair()
        original = args.scope
        args.scope = str(self.path.parent / 'outside.json')
        with self.assertRaisesRegex(LedgerError, 'state_dir'):
            compare_recorded_pair(args, self.ledger, config, token)
        args.scope = original
        self.sql("UPDATE reservations SET release_reason='uncertain' WHERE commit_sha=?", 'b' * 40)
        with self.assertRaises(ComparisonError):
            compare_recorded_pair(args, self.ledger, config, token)
        self.ledger.cancel(item['id'], 'stop')
        prior = self.ledger.item(item['id'])['stage']
        with self.assertRaises(LedgerError):
            compare_recorded_pair(args, self.ledger, config, token)
        with self.assertRaises(LedgerError):
            self.ledger.verification_progress(item['id'], token, 'publication')
        self.assertEqual(self.ledger.item(item['id'])['stage'], prior)
