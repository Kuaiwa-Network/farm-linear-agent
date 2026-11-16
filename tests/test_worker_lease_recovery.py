"""A disconnected owner can retry only after fenced, bounded, verified teardown."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from agent.execution import queue_resource
from agent.launcher import Finished, Launcher, RUNTIMES
from agent.ledger import LedgerError
from agent.maintenance import Maintenance
from test_ledger import LedgerBase
import test_single_worker


class WorkerLeaseRecoveryTests(LedgerBase):
    owner = test_single_worker.SingleWorkerTests.owner
    scheduler = test_single_worker.SingleWorkerTests.scheduler
    reservation = test_single_worker.SingleWorkerTests.reservation

    def launch(self):
        self.owner()
        self.sched = self.scheduler()
        self.sched.tick()
        self.token = self.ledger.claim(self.key, worker_id='owner')['token']
        self.ledger.checkpoint(self.key, self.token, {
            'summary': 'FARM-2173 assets inspected', 'reports': ['private/report.txt'],
            'handoff': 'Continue original candidate'})
        return self.ledger.item(self.key)

    def expire(self):
        self.now = self.ledger.item(self.key)['lease_expires_at'] + 1

    def test_connection_gap_retries_after_teardown_preserving_dirty_source_and_reports(self):
        row = self.launch()
        source = self.trees.root / self.key / 'Farm-Client' / 'pending 修改.txt'
        source.write_text('uncommitted candidate', encoding='utf-8')
        self.expire()
        with patch.object(self.launcher, 'assert_quiescent', wraps=self.launcher.assert_quiescent) as proof:
            self.sched._recover()
        proof.assert_called_once_with(self.key, row['worker_pid'], [])
        saved = self.ledger.item(self.key)
        self.assertEqual((saved['state'], saved['capacity_retries']), ('queued', 1))
        self.assertEqual(saved['retry_not_before'], self.now + 60)
        self.assertEqual(saved['checkpoint'], row['checkpoint'])
        self.assertEqual(saved['generation'], row['generation'])
        self.assertIsNone(saved['worker_pid'])
        self.assertEqual(self.launcher.stopped, [self.key])
        self.assertNotIn(('removed', self.key, None), self.trees.added)
        with self.assertRaises(LedgerError):
            self.ledger.renew(self.key, self.token)
        with self.assertRaises(LedgerError):
            self.ledger.claim(self.key, worker_id='too early')
        self.assertEqual(self.sched.tick()['launched'], 0)
        self.now += 60
        self.assertEqual(self.sched.tick()['launched'], 1)
        resumed = self.ledger.claim(self.key, worker_id='replacement')
        self.assertNotEqual(resumed['token'], self.token)
        self.assertEqual(source.read_text(encoding='utf-8'), 'uncommitted candidate')

    def test_three_retries_persist_across_reopen_then_fail_until_explicit_retry(self):
        self.launch()
        for attempt, delay in enumerate((60, 180, 600), 1):
            self.expire()
            self.sched._recover()
            saved = self.open_ledger().item(self.key)
            self.assertEqual((saved['capacity_retries'], saved['retry_not_before']), (attempt, self.now + delay))
            self.now += delay
            self.assertEqual(self.sched.tick()['launched'], 1)
            self.token = self.ledger.claim(self.key, worker_id=f'retry-{attempt}')['token']
        self.expire()
        self.sched.tick()
        self.assertEqual(self.ledger.item(self.key)['state'], 'failed')
        self.assertEqual(self.sched.tick()['launched'], 0)
        self.assertEqual(self.ledger.retry(self.key, 'operator retry')['capacity_retries'], 0)

    def test_capacity_and_lease_expiry_share_one_allowance(self):
        row = self.launch()
        self.ledger.defer_capacity_retry(self.key, row['worker_pid'])
        self.sched.active.clear()
        self.now += 60
        self.sched.tick()
        self.ledger.claim(self.key, worker_id='after capacity')
        self.expire()
        self.sched._recover()
        self.assertEqual(self.ledger.item(self.key)['capacity_retries'], 2)
        self.assertEqual(self.ledger.item(self.key)['retry_not_before'], self.now + 180)

    def test_unverified_descendants_retain_expired_claim_and_retry_after_restart(self):
        row = self.launch()
        self.expire()
        self.sched.active.clear()
        with patch.object(self.launcher, 'assert_quiescent', side_effect=RuntimeError('unknown descendant')):
            self.sched._recover()
        saved = self.open_ledger().item(self.key)
        self.assertEqual((saved['state'], saved['worker_pid'], saved['capacity_retries']),
                         ('running', row['worker_pid'], 0))
        with self.assertRaises(LedgerError):
            self.ledger.renew(self.key, self.token)
        self.sched._recover()
        self.assertEqual(self.ledger.item(self.key)['capacity_retries'], 1)

    def test_restart_kills_only_verified_owned_worker_before_retry(self):
        row = self.launch()
        self.sched.active.clear()
        self.launcher.alive_pids.add(row['worker_pid'])
        self.expire()
        with patch.object(self.launcher, 'owned_pid', return_value=False):
            self.sched._recover()
        self.assertEqual(self.launcher.killed, [])
        self.assertEqual(self.ledger.item(self.key)['capacity_retries'], 0)
        self.sched._recover()
        self.assertEqual(self.launcher.killed, [row['worker_pid']])
        self.assertEqual(self.ledger.item(self.key)['capacity_retries'], 1)

    def test_old_handle_must_be_consumed_before_retry(self):
        self.launch()
        handle = self.sched.active[self.key]
        self.expire()
        with patch.object(self.launcher, 'running', return_value={self.key: handle}):
            self.sched._recover()
        self.assertEqual(self.ledger.item(self.key)['capacity_retries'], 0)
        self.launcher.finished.append(Finished(self.key, 1, '', True, 'stopped', None, handle.pid))
        self.sched._reap()
        self.assertEqual(self.ledger.item(self.key)['capacity_retries'], 1)

    def test_process_that_exits_after_expiry_uses_same_bound(self):
        row = self.launch()
        self.expire()
        self.launcher.finished.append(Finished(self.key, 1, '', False, 'exited', None, row['worker_pid']))
        self.sched._reap()
        self.assertEqual(self.ledger.item(self.key)['capacity_retries'], 1)

    def test_runtime_budget_is_not_retried_even_with_expired_lease(self):
        row = self.launch()
        self.expire()
        self.launcher.finished.append(Finished(self.key, 1, '', True, 'budget', None, row['worker_pid']))
        self.sched._reap()
        self.assertEqual(self.ledger.item(self.key)['state'], 'failed')
        self.assertEqual(self.ledger.item(self.key)['capacity_retries'], 0)

    def test_stale_pid_generation_and_unexpired_claim_cannot_be_recovered(self):
        row = self.launch()
        settle = self.ledger.recover_expired_worker
        self.assertIsNone(settle(self.key, row['worker_pid'], row['generation']))
        self.expire()
        self.assertIsNone(settle(self.key, row['worker_pid'] + 1, row['generation']))
        self.assertIsNone(settle(self.key, row['worker_pid'], row['generation'] + 1))
        self.assertEqual(self.ledger.item(self.key)['capacity_retries'], 0)

    def test_old_finished_record_does_not_retire_successor(self):
        row = self.launch()
        handle = self.sched.active[self.key]
        self.launcher.finished.append(Finished(self.key, 1, '', False, 'exited', None, row['worker_pid'] - 1))
        self.sched._reap()
        self.assertEqual(self.ledger.item(self.key)['state'], 'running')
        self.assertIs(self.sched.active[self.key], handle)

    def test_cancellation_during_teardown_is_not_resurrected(self):
        self.launch()
        self.expire()
        with patch.object(self.launcher, 'assert_quiescent',
                          side_effect=lambda *args: self.ledger.cancel(self.key, 'human stopped')):
            self.sched._recover()
        self.assertEqual(self.ledger.item(self.key)['state'], 'cancelled')
        self.assertEqual(self.ledger.item(self.key)['capacity_retries'], 0)

    def test_withdrawal_prevents_automatic_retry(self):
        row = self.launch()
        self.expire()
        self.ledger.flag_withdrawal(self.key, 'undelegated', self.now + 30)
        result = self.ledger.recover_expired_worker(self.key, row['worker_pid'], row['generation'])
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(self.ledger.item(self.key)['capacity_retries'], 0)

    def test_maintenance_prevents_automatic_retry(self):
        self.launch()
        Maintenance(self.ledger).pause('planned maintenance')
        self.expire()
        self.sched._recover()
        self.assertEqual(self.ledger.item(self.key)['state'], 'failed')
        self.assertEqual(self.ledger.item(self.key)['capacity_retries'], 0)

    def test_open_reservation_keeps_existing_failure_recovery_path(self):
        row = self.launch()
        queued = queue_resource(self.ledger, self.key, self.token, 'unity_slot', 'interactive', 'a' * 40)
        self.expire()
        result = self.ledger.recover_expired_worker(self.key, row['worker_pid'], row['generation'])
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(self.ledger.item(self.key)['capacity_retries'], 0)
        self.assertEqual(self.ledger.reservation(queued['reservation_id'])['state'], 'queued')

    def test_active_editor_reservation_is_not_released_or_retried(self):
        row = self.launch()
        grant = self.reservation()
        self.expire()
        result = self.ledger.recover_expired_worker(self.key, row['worker_pid'], row['generation'])
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(self.ledger.item(self.key)['capacity_retries'], 0)
        self.assertEqual(self.ledger.reservation(grant['reservation_id'])['state'], 'active')
        self.assertEqual(self.ledger.slot('unity:1')['state'], 'interactive_busy')

    def test_certified_old_job_does_not_signal_reused_pid(self):
        row = self.launch()
        self.sched.active.clear()
        self.launcher.alive_pids.add(row['worker_pid'])
        self.expire()
        with patch.object(self.launcher, 'certified_pids', create=True, return_value={row['worker_pid']}):
            self.sched._recover()
        self.assertEqual(self.launcher.killed, [])
        self.assertEqual(self.ledger.item(self.key)['capacity_retries'], 1)

    def test_closed_issue_cannot_be_requeued(self):
        row = self.launch()
        metadata = self.ledger.issue(row['issue_id'])
        metadata['archived'] = True
        self.ledger.connection.execute('UPDATE issues SET metadata=? WHERE id=?', (json.dumps(metadata), row['issue_id']))
        self.expire()
        self.sched._recover()
        self.assertEqual(self.ledger.item(self.key)['state'], 'failed')
        self.assertEqual(self.ledger.item(self.key)['capacity_retries'], 0)

    def test_dispatch_renews_before_reads_after_connection_gap(self):
        self.launch()
        prompt = self.launcher.spawned[0][1]
        authority = prompt.split('\n\n', 1)[0]
        self.assertIn('run renew as the first action', authority)
        self.assertIn('before any more reads, edits, publication or delivery', authority)
        self.assertIn('expired or retired claim, stop immediately', authority)

    @unittest.skipUnless(os.name == 'nt', 'native Windows Job Object recovery')
    def test_native_expired_owner_and_child_exit_before_retry_without_touching_other_process(self):
        self.owner()
        sched = self.scheduler()
        root = Path(self.tmp.name)
        child_file = root / 'child.txt'
        worker = root / '租约 worker.py'
        worker.write_text('import subprocess,sys,time\nfrom pathlib import Path\n'
                          'sys.stdin.read()\n'
                          'child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"])\n'
                          f'Path({str(child_file)!r}).write_text(str(child.pid))\n'
                          'time.sleep(60)\n', encoding='utf-8')
        launcher = Launcher(root / 'native runs', RUNTIMES['fake']._replace(command=[sys.executable, str(worker)]), 'test')
        sched.launcher = launcher
        unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        self.addCleanup(lambda: (unrelated.terminate(), unrelated.wait(timeout=5)))
        handle = launcher.spawn(self.key, 'connection stalled', {}, 60, root)
        self.addCleanup(lambda: (launcher.stop(self.key), launcher.poll()))
        self.ledger.set_worker(self.key, handle.pid, 'test', lease_seconds=2700)
        token = self.ledger.claim(self.key, worker_id='stalled')['token']
        sched.active[self.key] = handle
        deadline = time.monotonic() + 10
        while not child_file.exists() and time.monotonic() < deadline:
            time.sleep(.03)
        self.assertTrue(child_file.exists())
        child = int(child_file.read_text())
        self.expire()
        sched._recover()
        self.assertEqual(self.ledger.item(self.key)['capacity_retries'], 0)
        with self.assertRaises(LedgerError):
            self.ledger.renew(self.key, token)
        sched._reap()
        self.assertEqual(self.ledger.item(self.key)['capacity_retries'], 1)
        self.assertFalse(launcher.alive(child))
        self.assertIsNone(unrelated.poll())
        self.assertNotIn(self.key, launcher.running())
        launcher.assert_quiescent(self.key, handle.pid)
