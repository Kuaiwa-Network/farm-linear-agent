"""Maintenance must retire claims only through durable handoff and proven process teardown."""
import json
from contextlib import closing
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agent import dispatch
from agent.__main__ import parser, run
from agent.ledger import Ledger, LedgerError
from agent.launcher import Finished, Launcher, RUNTIMES
from agent.maintenance import Maintenance, request, status
from agent.readonly_db import snapshot_connection
from agent.scheduler import Scheduler
from test_ledger import ISSUE, SESSION, PIN, issue
from test_scheduler import FakeLauncher, FakeWorktrees, SKILLS, ROOT

HANDOFF = {'facts': [{'claim': 'same candidate, no new failures', 'evidence': 'results/candidate.xml'}],
           'hypotheses': [], 'checks': [], 'repositories': [], 'next_actions': ['compare saved reports']}


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='维护 pause ')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.now = 1000.0
        self.path = self.root / 'ledger.sqlite3'
        self.ledger = Ledger(self.path, clock=lambda: self.now)
        self.addCleanup(self.ledger.close)
        self.ledger.observe_issue(issue())
        self.ledger.ensure_session(SESSION, ISSUE, delegation=True)
        self.item = self.ledger.create_work_item(issue_id=ISSUE, session_id=SESSION, skill='fix', target=PIN)
        self.id = self.item['id']
        self.store = Maintenance(self.ledger)
        self.launcher = FakeLauncher(self.root / 'runs')
        self.scheduler = self.scheduler_for(self.ledger)

    def scheduler_for(self, ledger):
        return Scheduler(ledger, self.launcher, SKILLS, FakeWorktrees(self.root / 'worktrees'),
                         skill_root=ROOT / 'skills', db_path=self.path, runtime_name='fake', host='h')

    def claim(self):
        self.ledger.set_worker(self.id, 123, 'h')
        self.token = self.ledger.claim(self.id, worker_id='worker')['token']
        self.ledger.checkpoint(self.id, self.token, {'stage': 'candidate-compared', 'handoff': HANDOFF})
        return self.token

    def beat(self, **changes):
        return {'phase': 'serving', 'started_at': 999, 'workers': {},
                'loops': {'schedule': {'finished_at': self.now, 'consecutive_errors': 0}}, **changes}

    def observe(self, busy=()):
        self.store.observe(self.launcher, busy, controller_started_at=999)

    def test_pause_is_idempotent_and_blocks_both_resource_pools_and_normal_launch(self):
        first = self.store.pause('deploy after handoff')
        self.now += 1
        self.assertEqual(first, self.store.pause('still waiting'))
        self.assertFalse(self.store.may_launch(self.id))
        self.assertIsNone(self.scheduler.launch(self.item))
        for kind in ('unity_slot', 'server_slot'):
            self.assertIsNone(self.ledger.acquire(kind, host='h', owner='pool'))
        self.assertEqual(self.launcher.spawned, [])

    def other_issue(self):
        other = '10000000-0000-4000-8000-000000000002'
        self.ledger.observe_issue(issue(other, identifier='FARM-2'))
        self.ledger.ensure_session('session-2', other, delegation=True)
        return self.ledger.create_work_item(issue_id=other, session_id='session-2', skill='fix', target=PIN)

    def queue_resource(self, item, kind='unity_slot'):
        if kind == 'server_slot':
            self.ledger.connection.execute("UPDATE work_items SET root_repo='farm-hive' WHERE id=?", (item,))
        token = self.ledger.claim(item, worker_id='resource-fixture')['token']
        self.ledger.checkpoint(item, token, {'stage':'candidate-ready'})
        options = {'commit_sha':PIN['commit_sha']} if kind == 'server_slot' else {}
        self.ledger.await_resource(item, token, kind, 'batch', skill=SKILLS['fix'], **options)
        return self.ledger.reservations()[-1]

    def test_only_issue_survives_restart_and_fences_claim_and_launch_without_changing_inputs(self):
        other = self.other_issue()
        before = self.ledger.item(other['id'])
        self.store.pause('accept FARM-1 first')
        self.store.resume(only_issue='farm-1')
        self.assertTrue(self.store.may_launch(self.id))
        self.assertFalse(self.store.may_launch(other['id']))
        self.assertIsNone(self.scheduler.launch(other))
        with self.assertRaisesRegex(LedgerError, 'single-issue'):
            self.ledger.claim(other['id'], worker_id='raced-launch')
        with closing(Ledger(self.path, clock=lambda:self.now)) as restarted:
            self.assertFalse(Maintenance(restarted).may_launch(other['id']))
            self.assertEqual(status(restarted.connection, ('fresh', self.beat()), self.now)['only_issue'], 'FARM-1')
        self.assertEqual(self.ledger.item(other['id']), before)
        self.store.resume()
        self.assertTrue(self.store.may_launch(other['id']))
        self.assertNotIn('only_issue', status(self.ledger.connection, ('fresh', self.beat()), self.now))

    def assert_scoped_resource_queue(self, kind):
        other = self.other_issue()
        older = self.queue_resource(other['id'], kind)
        selected = self.queue_resource(self.id, kind)
        self.ledger.ensure_slot(kind+':1', kind=kind, host='h', folder=str(self.root/'slot'))
        before = self.ledger.reservation(older['reservation_id'])
        self.store.pause('accept one issue')
        self.store.resume(only_issue='FARM-1')
        self.assertEqual([r['item_id'] for r in self.ledger.reservation_queue(kind)], [self.id])
        acquired = self.ledger.acquire(kind, host='h', owner='pool', expected_reservation=selected['reservation_id'])
        self.assertEqual(acquired['item_id'], self.id)
        self.assertEqual(self.ledger.reservation(older['reservation_id']), before)

    def test_single_issue_unity_queue_preserves_other_issue_position_and_candidate(self):
        self.assert_scoped_resource_queue('unity_slot')

    def test_single_issue_server_queue_preserves_other_issue_position_and_candidate(self):
        self.assert_scoped_resource_queue('server_slot')

    def test_single_issue_scope_accepts_same_issue_tasks_and_never_a_different_issue(self):
        other = self.other_issue()
        self.store.pause('one logical issue')
        self.store.resume(only_issue='FARM-1')
        self.assertTrue(self.store.may_launch(self.id))
        self.assertFalse(self.store.may_launch(other['id']))
        # A task shares its parent's issue identity and is admitted with it.
        self.ledger.connection.execute("UPDATE work_items SET issue_id=?,parent_id=?,skill='fgui' WHERE id=?",
                                      (ISSUE, self.id, other['id']))
        self.assertTrue(self.store.may_launch(other['id']))

    def test_single_issue_resume_rejects_unknown_issue_and_unsettled_other_work_atomically(self):
        from agent.admission_scope import read
        other = self.other_issue()
        with self.assertRaisesRegex(LedgerError, 'request maintenance'):
            self.store.resume(only_issue='FARM-1')
        self.store.pause('acceptance')
        with self.assertRaisesRegex(LedgerError, 'known issue'):
            self.store.resume(only_issue='FARM-999')
        self.assertTrue(self.store.requested())
        self.ledger.set_worker(other['id'], 9876, 'h')
        with self.assertRaisesRegex(LedgerError, 'settle other issue'):
            self.store.resume(only_issue='FARM-1')
        self.assertTrue(self.store.requested())
        self.assertIsNone(read(self.ledger.connection))

    def test_single_issue_resume_cannot_strand_another_issues_active_resource(self):
        other = self.other_issue()
        self.queue_resource(other['id'])
        self.ledger.ensure_slot('unity_slot:1', kind='unity_slot', host='h', folder=str(self.root/'slot'))
        self.ledger.acquire('unity_slot', host='h', owner='pool')
        self.store.pause('acceptance')
        with self.assertRaisesRegex(LedgerError, 'settle other issue'):
            self.store.resume(only_issue='FARM-1')
        self.assertTrue(self.store.requested())

    def test_host_cli_sets_only_issue_while_worker_cli_has_no_admission_authority(self):
        from agent.service import main
        with patch('agent.service.load_config', return_value=Mock(local_root=self.root)), \
                patch('agent.service.check_ownership'), patch('agent.service.Ledger') as ledger, \
                patch('agent.maintenance.Maintenance') as store, \
                patch('agent.maintenance.read_status', return_value={'state':'off','only_issue':'FARM-1'}), \
                patch('builtins.print'):
            self.assertEqual(main(['maintenance-resume','--config','config.json','--only-issue','FARM-1']), 0)
        store.return_value.resume.assert_called_once_with(only_issue='FARM-1')
        ledger.return_value.close.assert_called_once()
        with patch('sys.stderr'), self.assertRaises(SystemExit):
            parser().parse_args(['maintenance-resume','--only-issue','FARM-1'])

    def test_damaged_issue_scope_fails_closed_without_disclosing_raw_data(self):
        from agent.admission_scope import KIND
        for value in ({'private':'do-not-print'}, {'cleared':1}, [], {'identifier':'do-not-print','issue_id':ISSUE}):
            with self.subTest(value=value):
                self.ledger.note('maintenance', KIND, details=value)
                with self.assertRaisesRegex(LedgerError, 'scope is damaged') as caught:
                    self.store.may_launch(self.id)
                self.assertNotIn('do-not-print', str(caught.exception))
                with self.assertRaises(LedgerError):
                    self.ledger.acquire('unity_slot', host='h', owner='pool')
        self.store.resume()
        self.assertTrue(self.store.may_launch(self.id))

    def test_scheduler_runs_only_selected_issue_even_when_another_has_higher_priority(self):
        other = self.other_issue()
        self.ledger.connection.execute('UPDATE work_items SET priority=1 WHERE id=?', (other['id'],))
        self.store.pause('accept first')
        self.store.resume(only_issue='FARM-1')
        self.scheduler.tick()
        self.assertEqual([entry[0] for entry in self.launcher.spawned], [self.id])
        self.assertEqual(self.ledger.item(other['id'])['state'], 'queued')

    def test_monitor_names_acceptance_issue_without_misreporting_capacity_wait(self):
        from agent.monitor_view import build_status
        from test_monitor_view import beat, HEALTHY, INSTANCE
        other = self.other_issue()
        self.queue_resource(other['id'])
        self.store.pause('private operator reason')
        self.store.resume(only_issue='FARM-1')
        document = build_status(self.path, heartbeat=('fresh', beat()), health=HEALTHY, instance=INSTANCE,
                                monitor_revision=('b'*40, False), now=self.now)
        self.assertTrue(document['ledger']['ok'])
        paused = next(r for r in document['active'] if r['identifier']=='FARM-2')
        self.assertEqual(paused['display_state'], 'acceptance_paused')
        self.assertEqual(paused['waiting_for_issue'], 'FARM-1')
        self.assertIsNone(paused['queue_position'])
        self.assertIsNone(paused['resource_wait'])
        self.assertEqual(document['service']['maintenance']['only_issue'], 'FARM-1')
        self.assertNotIn('private operator reason', json.dumps(document))

    def test_claim_and_renew_signal_pause_without_revoking_an_inflight_test(self):
        token = self.claim()
        self.store.pause('maintenance')
        renewed = self.ledger.renew(self.id, token)
        self.assertTrue(renewed['maintenance_requested'])
        self.assertEqual(renewed['state'], 'running')
        self.assertGreater(renewed['lease_expires_at'], self.now)
        self.store.resume()
        self.assertFalse(self.ledger.renew(self.id, token)['maintenance_requested'])

    def test_restart_handoff_never_waits_old_lease_or_changes_candidate_or_budgets(self):
        token = self.claim()
        self.ledger.connection.execute("UPDATE work_items SET capacity_retries=2,publication_retries=1 WHERE id=?", (self.id,))
        original = self.ledger.item(self.id)
        self.store.pause('maintenance')
        result = self.store.handoff(self.id, token)
        self.assertIsNone(result['lease_expires_at'])
        with self.assertRaises(LedgerError):
            self.ledger.renew(self.id, token)
        with self.assertRaisesRegex(LedgerError, 'teardown'):
            self.ledger.claim(self.id, worker_id='too-early')
        self.assertEqual(self.ledger.launched(), [])
        # A new controller connection performs recovery before the old lease could expire.
        restarted = Ledger(self.path, clock=lambda: self.now)
        self.addCleanup(restarted.close)
        scheduler = self.scheduler_for(restarted)
        with patch.object(self.launcher, 'assert_quiescent', side_effect=RuntimeError('child alive')):
            scheduler.tick()
        self.assertEqual(restarted.item(self.id)['worker_pid'], 123)
        self.assertEqual(self.launcher.killed, [])
        scheduler.tick()
        after = restarted.item(self.id)
        for key in ('generation', 'root_repo', 'stage', 'target', 'checkpoint', 'evidence', 'capacity_retries', 'publication_retries'):
            self.assertEqual(after[key], original[key], key)
        self.assertIsNone(after['worker_pid'])
        self.assertEqual(after['state'], 'queued')
        self.assertEqual(self.launcher.spawned, [])
        Maintenance(restarted).resume()
        self.assertTrue(Maintenance(restarted).may_launch(self.id))
        resumed = restarted.claim(self.id, worker_id='replacement')
        self.assertEqual(resumed['target'], original['target'])
        self.assertEqual(resumed['generation'], original['generation'])

    def test_resume_before_teardown_does_not_allow_overlap(self):
        token = self.claim()
        self.store.pause('maintenance')
        self.store.handoff(self.id, token)
        self.store.resume()
        self.assertFalse(self.store.may_launch(self.id))
        with patch.object(self.launcher, 'assert_quiescent', side_effect=RuntimeError('child alive')):
            self.scheduler.tick()
        self.assertEqual(self.launcher.spawned, [])
        self.scheduler.tick()
        self.assertEqual(len(self.launcher.spawned), 1)

    def test_reaping_acknowledged_worker_is_not_a_failure(self):
        token = self.claim()
        self.store.pause('maintenance')
        self.store.handoff(self.id, token)
        self.launcher.finished.append(Finished(self.id, 0, '', False, 'exited', None, 123))
        self.scheduler.tick()
        self.assertEqual(self.ledger.item(self.id)['state'], 'queued')
        self.assertFalse(self.store.pending(self.id))

    def test_handoff_requires_request_current_checkpoint_and_valid_claim(self):
        token = self.claim()
        with self.assertRaisesRegex(LedgerError, 'not requested'):
            self.store.handoff(self.id, token)
        self.store.pause('maintenance')
        with self.assertRaises(LedgerError):
            self.store.handoff(self.id, 'wrong')
        self.ledger.connection.execute("UPDATE work_items SET checkpoint='{}' WHERE id=?", (self.id,))
        with self.assertRaisesRegex(LedgerError, 'checkpoint'):
            self.store.handoff(self.id, token)
        self.assertEqual(self.ledger.item(self.id)['state'], 'running')

    def test_handoff_refuses_any_unsettled_reservation(self):
        token = self.claim()
        self.store.pause('maintenance')
        for state in ('queued', 'active', 'cancel_requested'):
            with self.subTest(state=state):
                self.ledger.connection.execute("INSERT OR REPLACE INTO reservations "
                    "(reservation_id,item_id,kind,state,generation,mode,commit_sha,created_at,resource,owner,token_hash) "
                    "VALUES('reservation',?,'unity_slot',?,0,'interactive',?,?,'unity_slot:1','pool','hash')",
                    (self.id, state, 'a'*40, self.now))
                with self.assertRaisesRegex(LedgerError, 'release resources'):
                    self.store.handoff(self.id, token)

    def test_cancellation_during_handoff_is_never_resurrected(self):
        token = self.claim()
        self.store.pause('maintenance')
        self.store.handoff(self.id, token)
        self.ledger.cancel(self.id, 'operator Stop')
        self.store.settle(self.launcher)
        self.assertEqual(self.ledger.item(self.id)['state'], 'cancelled')
        self.assertFalse(self.store.pending(self.id))

    def test_readiness_requires_fresh_controller_and_proven_empty_processes(self):
        self.claim()
        self.store.pause('maintenance')
        self.observe()
        self.assertFalse(status(self.ledger.connection, ('fresh', self.beat()), self.now)['ready_to_stop'])
        self.store.handoff(self.id, self.token)
        self.store.settle(self.launcher)
        self.observe(['pool'])
        self.assertFalse(status(self.ledger.connection, ('fresh', self.beat()), self.now)['ready_to_stop'])
        with patch.object(self.launcher, 'assert_quiescent', side_effect=RuntimeError('unverified')):
            self.observe()
        self.assertFalse(status(self.ledger.connection, ('fresh', self.beat()), self.now)['ready_to_stop'])
        self.observe()
        self.assertTrue(status(self.ledger.connection, ('fresh', self.beat()), self.now)['ready_to_stop'])
        for state, beat, now in [('stale', self.beat(), self.now),
                                 ('fresh', self.beat(phase='stopped'), self.now),
                                 ('fresh', self.beat(started_at=self.now+1), self.now+1),
                                 ('fresh', self.beat(), self.now+16)]:
            self.assertFalse(status(self.ledger.connection, (state, beat), now)['ready_to_stop'])

    def test_held_or_switching_resource_never_reports_ready(self):
        self.store.pause('maintenance')
        self.ledger.ensure_slot('unity_slot:1', kind='unity_slot', host='h', folder=str(self.root/'slot'))
        for state in ('switching', 'held', 'interactive_busy', 'batch_busy'):
            self.ledger.set_slot_state('unity_slot:1', state)
            self.observe()
            view = status(self.ledger.connection, ('fresh', self.beat()), self.now)
            self.assertFalse(view['ready_to_stop'])
            self.assertIn('resource_unsettled', [b['code'] for b in view['blockers']])

    def test_admitted_resource_worker_can_only_drain_and_gets_maintenance_instruction(self):
        self.store.pause('maintenance')
        with patch.object(self.ledger, 'active_reservation', return_value={'state':'active'}):
            self.assertTrue(self.store.may_launch(self.id))
        self.assertIn('maintenance-handoff', dispatch.COMMON_AUTHORITY)
        self.assertIn('do not start new work or tests', dispatch.COMMON_AUTHORITY)

    def test_readonly_legacy_status_does_not_create_tables(self):
        legacy = self.root/'old.sqlite3'
        with closing(sqlite3.connect(legacy)) as db:
            db.execute('CREATE TABLE old (id TEXT)')
            db.commit()
        with snapshot_connection(legacy) as db:
            self.assertEqual(status(db, ('missing', None), self.now)['state'], 'off')
            self.assertIsNone(request(db))
            self.assertEqual([r[0] for r in db.execute('SELECT name FROM sqlite_master')], ['old'])

    def test_cli_uses_claim_token_and_exposes_no_operator_resume_command(self):
        token = self.claim()
        self.store.pause('maintenance')
        token_file = self.root/'token'
        token_file.write_text(token, encoding='utf-8')
        args = parser().parse_args(['--db', str(self.path), 'maintenance-handoff', '--item', self.id,
                                   '--token-file', str(token_file)])
        result = run(args, self.ledger, Mock(side_effect=AssertionError('no Linear calls')))
        self.assertEqual(result['state'], 'queued')
        self.assertIn('exit', result['next_action'])

    def test_new_observation_between_heartbeats_does_not_flicker_or_trust_previous_controller(self):
        self.store.pause('maintenance')
        self.observe()
        beat = self.beat(loops={'schedule': {'finished_at': self.now-4, 'consecutive_errors': 0}})
        self.assertTrue(status(self.ledger.connection, ('fresh', beat), self.now)['ready_to_stop'])
        beat['started_at'] = 998
        self.assertFalse(status(self.ledger.connection, ('fresh', beat), self.now)['ready_to_stop'])

    def test_monitor_names_waiting_issues_and_hides_stale_resource_waits(self):
        from agent.monitor_view import build_status
        from test_monitor_view import beat, HEALTHY, INSTANCE
        self.claim()
        self.store.pause('do not publish operator reason')
        self.observe()
        document = build_status(self.path, heartbeat=('fresh', beat()), health=HEALTHY, instance=INSTANCE,
                                monitor_revision=('b'*40, False), now=self.now)
        self.assertTrue(document['ledger']['ok'])
        self.assertEqual(document['active'][0]['display_state'], 'maintenance_handoff')
        self.assertIsNone(document['active'][0]['queue_position'])
        blockers = document['service']['maintenance']['blockers']
        self.assertIn(self.item['identifier'], [b['subject'] for b in blockers])
        self.assertNotIn('do not publish', json.dumps(document))
        self.store.handoff(self.id, self.token)
        self.store.settle(self.launcher)
        document = build_status(self.path, heartbeat=('fresh', beat()), health=HEALTHY, instance=INSTANCE,
                                monitor_revision=('b'*40, False), now=self.now)
        self.assertEqual(document['active'][0]['display_state'], 'maintenance_paused')

    def test_old_observation_format_or_malformed_public_data_never_reports_ready(self):
        self.store.pause('maintenance')
        for value in ([], {}, {'controller_started_at':999, 'blockers':'private'},
                      {'controller_started_at':999, 'blockers':[{'code':'credential', 'subject':'private'}]}):
            self.ledger.connection.execute('UPDATE maintenance_control SET checked_at=?,blockers=?',
                                           (self.now, json.dumps(value)))
            view = status(self.ledger.connection, ('fresh', self.beat()), self.now)
            self.assertFalse(view['ready_to_stop'])
            self.assertNotIn('private', json.dumps(view))

    def test_readonly_status_cli_never_constructs_a_ledger_or_calls_linear(self):
        from agent.service import main
        with patch('agent.service.load_config', return_value=object()) as load, \
                patch('agent.service.check_ownership') as ownership, \
                patch('agent.service.Ledger', side_effect=AssertionError('must remain read only')), \
                patch('agent.maintenance.read_status', return_value={'state':'off'}), \
                patch('builtins.print'):
            self.assertEqual(main(['maintenance-status', '--config', 'config.json']), 0)
        load.assert_called_once_with('config.json', secure_permissions=False)
        ownership.assert_called_once()

    def test_expired_claim_cannot_acknowledge_maintenance(self):
        token = self.claim()
        self.store.pause('maintenance')
        self.now += 999999
        with self.assertRaisesRegex(LedgerError, 'lease expired'):
            self.store.handoff(self.id, token)
        self.assertFalse(self.store.pending(self.id))

    def test_controller_activity_proof_does_not_depend_on_heartbeat_serialization(self):
        from agent.heartbeat import Heartbeat
        beat = Heartbeat(runtime='fake', clock=lambda: self.now)
        with patch.object(beat, 'payload', side_effect=ValueError('display unavailable')):
            self.assertEqual(beat.started_at, self.now)
            beat.loop_started('pool')
            self.assertEqual(beat.unsettled_loops(('pool', 'server_pool')), ['pool'])
            beat.loop_failed('pool', RuntimeError('probe failed'))
            self.assertEqual(beat.unsettled_loops(('pool',)), ['pool'])
            beat.loop_finished('pool')
            self.assertEqual(beat.unsettled_loops(('pool',)), [])

    @unittest.skipUnless(os.name == 'nt', 'native Windows Job Object handoff')
    def test_native_windows_job_must_exit_before_maintenance_handoff_settles(self):
        self.now = time.time()
        launcher = Launcher(self.root/'native-runs', RUNTIMES['fake'], 'test')
        release = self.root/'allow-worker-exit'
        claimed, drain, acknowledged = (self.root/name for name in ('claimed', 'drain', 'acknowledged'))
        checkpoint, token_file = self.root/'checkpoint.json', self.root/'claim.token'
        checkpoint.write_text(json.dumps({'handoff': HANDOFF}), encoding='utf-8')
        stub = self.root/'linear-stub'
        stub.mkdir()
        steps = [['claim', '--item', '{item}', '--worker-id', 'native-worker'],
                 ['file', str(token_file), '{token}'], ['file', str(claimed), 'ready'], ['wait', str(drain)],
                 ['renew', '--item', '{item}', '--token-file', str(token_file)],
                 ['expect', 'maintenance_requested', 'true'],
                 ['checkpoint', '--item', '{item}', '--token-file', str(token_file), '--input', str(checkpoint)],
                 ['maintenance-handoff', '--item', '{item}', '--token-file', str(token_file)],
                 ['file', str(acknowledged), 'handoff'], ['wait', str(release)]]
        prompt = 'fixture\n\n' + json.dumps({'database':str(self.path), 'item_id':self.id})
        handle = launcher.spawn(self.id, prompt, {}, 30, self.root, extra_env={
            'FAKE_CLI_MODE':'cli', 'FAKE_CLI_REPO':str(ROOT),
            'FARMBOT_CONFIG':str(self.root/'absent-offline-config.json'),
            'FARMBOT_LINEAR_STUB_DIR':str(stub), 'PYTHONPATH':str(ROOT),
            'FAKE_CLI_STEPS':json.dumps(steps)})
        self.addCleanup(launcher.stop, self.id, 2)
        self.ledger.set_worker(self.id, handle.pid, 'test')
        def wait_file(path):
            deadline = time.monotonic()+10
            while not path.exists() and time.monotonic() < deadline:
                time.sleep(.03)
            self.assertTrue(path.exists(), 'native worker did not reach ' + path.name)
        wait_file(claimed)
        self.store.pause('maintenance')
        drain.touch()
        wait_file(acknowledged)
        self.assertIsNone(self.ledger.item(self.id)['lease_expires_at'])
        self.store.settle(launcher)
        self.assertTrue(self.store.pending(self.id))
        release.touch()
        deadline = time.monotonic()+10
        while time.monotonic() < deadline:
            if launcher.poll():
                break
            time.sleep(.03)
        else:
            self.fail('native fixture did not exit')
        self.store.settle(launcher)
        self.assertFalse(self.store.pending(self.id))
        self.assertIsNone(self.ledger.item(self.id)['worker_pid'])
        self.assertEqual(self.ledger.item(self.id)['state'], 'queued')


if __name__ == '__main__':
    unittest.main()
