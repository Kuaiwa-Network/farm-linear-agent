"""Retained owner, latest request, native process isolation and priority pause."""
import os
from contextlib import closing
import sys
import time
import unittest
from unittest.mock import patch

from agent.execution import queue_resource, resource_status
from agent.ledger import Ledger, LedgerError
from agent.maintenance import Maintenance, status
from agent.resource_handoff import release
from agent.resource_processes import Commands, quiescent
import test_resource_handoff as handoff
import test_server_slots as server
import test_maintenance as maintenance
import test_single_worker as single
from test_ledger import OWNER
import test_ledger as ledger_tests


class ProcessScopeTests(server.ServerPoolFixture):
    start, save = handoff.HandoffTests.start, handoff.HandoffTests.save

    def command(self, key, token, grant, code='pass'):
        return Commands(self.ledger, key, token, grant['reservation_id']).command(
            [sys.executable, '-c', code], cwd=self.root, env=os.environ.copy(), log=self.root/'child.log')

    @unittest.skipUnless(os.name == 'nt', 'retained owner requires native Windows Job containment')
    def test_release_retains_owner_and_cleans_exact_reservation_including_pair(self):
        for pair in (False, True):
            with self.subTest(pair=pair):
                key, token, grant = self.start(pair=pair, managed=True)
                child = self.command(key, token, grant)
                child.stop()
                self.save(key, token)
                before = self.ledger.item(key)
                self.assertTrue(release(self.ledger, grant['reservation_id'], grant['token'])['keep_worker'])
                after = self.ledger.renew(key, token)
                for field in ('state', 'generation', 'worker_pid', 'checkpoint'):
                    self.assertEqual(before[field], after[field])
                self.assertTrue(quiescent(self.ledger, grant['reservation_id']))
                self.assertFalse(self.exited)
                self.assertEqual(self.pool.tick()['settled'], 1)
                self.assertEqual(self.services.cleaned[-1]['reservation_id'], grant['reservation_id'])
                with self.assertRaises(LedgerError):
                    self.command(key, token, grant)

    def test_native_live_process_and_incomplete_identity_hold_release(self):
        key, token, grant = self.start(managed=True)
        child = self.command(key, token, grant, 'import time; time.sleep(30)')
        self.save(key, token)
        try:
            with self.assertRaisesRegex(LedgerError, 'teardown uncertain'):
                release(self.ledger, grant['reservation_id'], grant['token'])
            self.ledger.connection.execute("UPDATE resource_commands SET state='quiescent'")
            with self.assertRaises(LedgerError):
                release(self.ledger, grant['reservation_id'], grant['token'])
        finally:
            child.stop()
        self.ledger.connection.execute("UPDATE resource_commands SET state='starting',identity=NULL")
        with self.assertRaises(LedgerError):
            release(self.ledger, grant['reservation_id'], grant['token'])

    def test_unknown_native_proof_uses_full_worker_recovery(self):
        key, token, grant = self.start(managed=True)
        child = self.command(key, token, grant)
        child.stop()
        self.save(key, token)
        release(self.ledger, grant['reservation_id'], grant['token'])
        with patch('agent.resource_processes.native_empty', return_value=False):
            self.assertEqual(self.pool.tick()['settled'], 0)
            self.exited = True
            self.assertEqual(self.pool.tick()['settled'], 1)

    def test_generation_change_fences_old_command_and_scope(self):
        key, token, grant = self.start(managed=True)
        self.ledger.connection.execute('UPDATE work_items SET generation=generation+1 WHERE id=?', (key,))
        with self.assertRaises(LedgerError):
            self.command(key, token, grant)
        self.assertFalse(quiescent(self.ledger, grant['reservation_id']))

    def test_posix_scope_requires_outer_worker_teardown_before_reuse(self):
        key, token, grant = self.start(managed=True)
        self.save(key, token)
        with patch('agent.resource_processes.os.name', 'posix'):
            result = release(self.ledger, grant['reservation_id'], grant['token'])
            self.assertTrue(result['worker_handoff'])
            self.assertFalse(quiescent(self.ledger, grant['reservation_id']))
        with self.assertRaises(LedgerError):
            self.ledger.renew(key, token)
        self.assertEqual(self.pool.tick()['settled'], 0)
        self.exited = True
        self.assertEqual(self.pool.tick()['settled'], 1)

    def test_pause_during_creation_refuses_start_and_preserves_incomplete_proof(self):
        from agent.integration_runtime import Command
        key, token, grant = self.start(managed=True)
        def creating(*args, identified, **kwargs):
            def identified_after_pause(child):
                Maintenance(self.ledger).pause('creation raced pause')
                identified(child)
            return Command(*args, identified=identified_after_pause, **kwargs)
        with patch('agent.integration_runtime.Command', side_effect=creating):
            with self.assertRaises(LedgerError):
                self.command(key, token, grant)
        saved = self.ledger.connection.execute('SELECT state,identity FROM resource_commands').fetchone()
        self.assertEqual(saved['state'], 'starting')
        self.assertIsNone(saved['identity'])
        self.assertFalse(quiescent(self.ledger, grant['reservation_id'], sealed=False))

    @unittest.skipUnless(os.name == 'nt', 'native Windows nested Jobs')
    def test_job_stops_grandchild_and_keeps_sibling_and_claim_alive(self):
        from agent.integration_runtime import Command
        from agent.launcher import Launcher
        from agent.windows_job import WindowsJob
        key, token, grant = self.start(managed=True)
        pid_file = self.root/'grandchild.pid'
        code = ("import subprocess,sys,time,pathlib; "
                "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']); "
                f"pathlib.Path({str(pid_file)!r}).write_text(str(p.pid)); time.sleep(30)")
        sibling = Command([sys.executable, '-c', 'import time;time.sleep(30)'],
                          cwd=self.root, env=os.environ.copy(), log=self.root/'sibling.log')
        child = self.command(key, token, grant, code)
        try:
            deadline = time.monotonic()+10
            while not pid_file.exists() and time.monotonic()<deadline:
                time.sleep(.02)
            self.assertTrue(pid_file.exists())
            identity = child.identity()
            self.assertFalse(WindowsJob.empty(identity['windows_job']))
            child.stop()
            self.assertTrue(WindowsJob.empty(identity['windows_job']))
            self.assertFalse(Launcher.alive(int(pid_file.read_text())))
            self.assertFalse(sibling.exited())
            self.ledger.renew(key, token)
        finally:
            if child.job.handle:
                child.stop()
            sibling.stop()

    def test_interrupted_runner_retains_identity_and_requires_owned_worker_recovery(self):
        key, token, grant = self.start(managed=True)
        with patch('agent.integration_runtime.Command', side_effect=RuntimeError('interrupted before identity')):
            with self.assertRaises(RuntimeError):
                self.command(key, token, grant)
        saved = self.ledger.connection.execute('SELECT state,identity FROM resource_commands').fetchone()
        self.assertEqual(saved['state'], 'starting')
        self.assertIsNone(saved['identity'])
        self.ledger.hold_owned(grant['reservation_id'], grant['token'], 'uncertain runner')
        with self.assertRaisesRegex(RuntimeError, 'teardown'):
            self.pool.recover(grant['resource'], 'fixture recovery')
        self.exited = True
        self.pool.recover(grant['resource'], 'fixture recovery after all-worker proof')
        self.assertEqual(self.ledger.slot(grant['resource'])['state'], 'idle_closed')
        self.assertEqual(self.ledger.connection.execute('SELECT state FROM resource_commands').fetchone()[0], 'starting')

    def test_selected_owner_can_request_and_acquire_services_during_priority_pause(self):
        key = self.request(skill='work', reserve=False, execution_contract='single-worker-v1')
        self.ledger.set_worker(key, 123, 'test')
        token = self.ledger.claim(key, worker_id='selected')['token']
        Maintenance(self.ledger).pause('priority', except_issues=['FARM-1'])
        queue_resource(self.ledger, key, token, 'server_slot', 'batch', 'a'*40,
                       test_plan={'packages':['./ledger'],'tests':['TestCount'],'race':False})
        self.pool.single_only = True
        self.assertEqual(self.pool.tick()['granted'], 1)
        self.assertFalse(resource_status(self.ledger, key, token)['maintenance_requested'])


class PriorityTests(unittest.TestCase):
    setUp = maintenance.MaintenanceTests.setUp
    scheduler_for = maintenance.MaintenanceTests.scheduler_for
    other_issue = maintenance.MaintenanceTests.other_issue
    claim = maintenance.MaintenanceTests.claim
    beat = maintenance.MaintenanceTests.beat
    observe = maintenance.MaintenanceTests.observe

    def test_priority_survives_restart_and_signals_resource_poll_without_retiring_selected(self):
        self.claim()
        other = self.other_issue()['id']
        self.ledger.connection.execute("UPDATE work_items SET execution_contract='single-worker-v1',skill='work' WHERE id=?", (other,))
        self.ledger.set_worker(other, 124, 'h')
        token = self.ledger.claim(other, worker_id='other')['token']
        queued = queue_resource(self.ledger, other, token, 'unity_slot', 'interactive', 'a'*40)
        before = self.ledger.item(self.id)
        self.store.pause('let selected issue finish', except_issues=['FARM-1'])
        self.assertFalse(self.ledger.renew(self.id, self.token)['maintenance_requested'])
        self.assertTrue(resource_status(self.ledger, other, token)['maintenance_requested'])
        self.assertEqual(resource_status(self.ledger, other, token)['state'], 'queued')
        self.assertEqual(self.ledger.reservation_queue('unity_slot'), [])
        self.assertEqual(self.ledger.item(self.id)['generation'], before['generation'])
        with self.assertRaisesRegex(LedgerError, 'not requested'):
            self.store.handoff(self.id, self.token)
        with closing(Ledger(self.path, clock=lambda:self.now)) as restarted:
            self.assertFalse(Maintenance(restarted).requested(self.id))
            self.assertTrue(Maintenance(restarted).requested(other))
        self.observe()
        self.assertFalse(status(self.ledger.connection, ('fresh', self.beat()), self.now)['ready_to_stop'])
        self.store.resume()
        self.assertEqual(self.ledger.reservation_queue('unity_slot')[0]['reservation_id'], queued['reservation_id'])

    def test_resume_preserves_prior_admission_restriction_and_human_wait(self):
        other = self.other_issue()['id']
        self.store.pause('old restriction')
        self.store.resume(only_issue='FARM-1')
        self.ledger.connection.execute("UPDATE work_items SET state='awaiting_input' WHERE id=?", (other,))
        self.store.pause('priority', except_issues=['FARM-1'])
        with self.assertRaises(LedgerError):
            self.store.pause('overwrite pause')
        self.store.resume()
        self.assertFalse(self.store.may_launch(other))
        self.assertEqual(self.ledger.item(other)['state'], 'awaiting_input')

    def test_paused_server_request_does_not_drive_idle_editor_reclamation(self):
        from agent.integration import server_pending
        other = self.other_issue()['id']
        self.ledger.connection.execute("UPDATE work_items SET execution_contract='single-worker-v1',skill='work' WHERE id=?", (other,))
        self.ledger.set_worker(other, 124, 'h')
        token = self.ledger.claim(other, worker_id='other')['token']
        queue_resource(self.ledger, other, token, 'server_slot', 'batch', 'a'*40,
                       test_plan={'packages':['./ledger'], 'tests':[], 'race':False})
        self.store.pause('priority', except_issues=['FARM-1'])
        self.assertEqual(server_pending(self.ledger), [])
        self.store.resume()
        self.assertEqual(len(server_pending(self.ledger)), 1)

    def test_monitor_distinguishes_selected_worker_from_paused_others(self):
        from agent.monitor_view import build_status
        from test_monitor_view import beat, HEALTHY, INSTANCE
        self.claim()
        self.other_issue()
        self.store.pause('private operator reason', except_issues=['FARM-1'])
        self.observe()
        document = build_status(self.path, heartbeat=('fresh', beat()), health=HEALTHY, instance=INSTANCE,
                                monitor_revision=('b'*40, False), now=self.now)
        self.assertTrue(document['ledger']['ok'])
        workers = {row['identifier']: row for row in document['active']}
        self.assertEqual(workers['FARM-1']['display_state'], 'running')
        self.assertEqual(workers['FARM-2']['display_state'], 'maintenance_paused')
        self.assertEqual(document['service']['maintenance']['state'], 'prioritizing')
        self.assertFalse(document['service']['maintenance']['ready_to_stop'])


class ScopeTests(ledger_tests.LedgerBase):
    setUp = single.SingleWorkerTests.setUp
    claim_owner = single.SingleWorkerTests.claim_owner
    owner = single.SingleWorkerTests.owner

    def test_consumed_latest_request_still_fences_old_todo_until_reviewed(self):
        from agent.request_scope import require_reviewed
        self.claim_owner()
        self.ledger.checkpoint(self.key, self.token, {'handoff':'Old full feature acceptance pending'})
        self.ledger.push_inbox(self.key, 'Resolve conflicts only; report unrelated failures', author=OWNER)
        current = self.ledger.issue_context(self.key)['current_request']
        self.assertTrue(current['review_required'])
        self.ledger.pop_inbox(self.key, self.token)
        with self.assertRaisesRegex(LedgerError, 'new attributed request'):
            queue_resource(self.ledger, self.key, self.token, 'unity_slot', 'interactive', single.SHA)
        with self.assertRaisesRegex(LedgerError, 'new attributed request'):
            self.ledger.finish(self.key, self.token, 'delivered', {'summary': 'Old checklist completed'})
        self.ledger.checkpoint(self.key, self.token, {'request_scope': {
            'through_message_id':current['latest_message_id'], 'summary':'Only real PR conflicts and affected validation'}})
        require_reviewed(self.ledger, self.key)
        self.assertIn('Old full feature', self.ledger.issue_context(self.key)['handoff']['content'])
        self.ledger.push_inbox(self.key, 'Only fix the failure introduced by this merge', author=OWNER)
        with self.assertRaises(LedgerError):
            require_reviewed(self.ledger, self.key)
        self.ledger.checkpoint(self.key, self.token, {'summary':'Preserve in-flight report'})
        self.assertTrue(self.ledger.issue_context(self.key)['current_request']['review_required'])

    def test_status_returns_withdrawal_and_pending_request(self):
        self.claim_owner()
        self.ledger.push_inbox(self.key, 'Stop after the current test', author=OWNER)
        self.ledger.connection.execute('UPDATE work_items SET withdraw_deadline=?,withdraw_reason=? WHERE id=?',
                                      (self.now+60, 'withdrawn', self.key))
        result = resource_status(self.ledger, self.key, self.token)
        self.assertEqual(result['state'], 'none')
        self.assertEqual(result['withdraw_reason'], 'withdrawn')
        self.assertEqual(result['inbox_pending'], 1)
