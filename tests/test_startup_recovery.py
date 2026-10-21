"""Startup outages preserve work, fence late claims and reduce queue admission."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from agent import dispatch
from agent.launcher import Finished, Launcher, RUNTIMES
from agent.ledger import LedgerError
from agent.maintenance import Maintenance
from agent.startup_recovery import StartupRecovery, DELAYS
from test_ledger import LedgerBase, issue
import test_single_worker

ROOT = test_single_worker.ROOT


class StartupTests(LedgerBase):
    owner = test_single_worker.SingleWorkerTests.owner
    scheduler = test_single_worker.SingleWorkerTests.scheduler

    def launch(self):
        self.owner()
        self.sched = self.scheduler()
        self.sched.tick()
        self.item_id = self.key
        return self.ledger.item(self.key)

    def another(self):
        identifier = str(uuid4())
        self.ledger.observe_issue(issue(id=identifier, identifier='FARM-2'))
        self.ledger.ensure_session(identifier, identifier, delegation=True)
        return self.ledger.create_work_item(issue_id=identifier, session_id=identifier, skill='work')

    def test_claim_is_first_tool_before_approval_retry_guidance_for_chat_and_work(self):
        for skill in ('work', 'chat'):
            text = dispatch.dispatch_message(item={'id': 'one', 'skill': skill, 'execution_contract': 'single-worker-v1'},
                issue={'identifier': 'FARM-1', 'url': 'https://linear.app/issue/1'},
                skill_path=ROOT / 'skills' / skill / 'SKILL.md', worktrees={}, db_path='C:/路径 with spaces/db',
                runtime='codex', guidance='', budget={'renew_minutes': 10, 'lease_seconds': 2700},
                repo_root='C:/source root', state_dir='C:/私有 notes')
            self.assertTrue(text.startswith('STARTUP FIRST:'))
            authority, payload = text.split('\n\n', 1)
            argv = json.loads(payload)['startup']['start_argv']
            self.assertIn(json.dumps(argv, ensure_ascii=False), authority.splitlines()[0])
            self.assertIn('Do not list directories, check token existence', authority)
            self.assertLess(authority.index('STARTUP FIRST'), authority.index('If automatic approval'))
            self.assertIn('unchanged sandbox and automatic review', authority)

    def test_ten_minute_timeout_preserves_checkpoint_and_reuses_worktrees(self):
        row = self.launch()
        checkpoint = {'summary': 'saved progress', 'candidates': {'Farm-Client': 'a' * 40}}
        self.ledger.connection.execute('UPDATE work_items SET checkpoint=? WHERE id=?', (json.dumps(checkpoint), self.key))
        self.now += 600
        self.sched.tick()
        self.assertEqual(self.ledger.item(self.key)['worker_pid'], row['worker_pid'])
        self.now += 1
        self.sched.tick()
        saved = self.ledger.item(self.key)
        self.assertEqual((saved['state'], saved['startup_retries'], saved['worker_pid']), ('queued', 1, None))
        self.assertEqual(saved['retry_not_before'], self.now + 60)
        self.assertEqual(saved['checkpoint'], checkpoint)
        self.assertEqual(saved['generation'], row['generation'])
        self.assertNotIn(('removed', self.key, None), self.trees.added)
        self.assertEqual(self.sched.tick()['launched'], 0)
        self.now += 60
        self.assertEqual(self.sched.tick()['launched'], 1)
        self.assertEqual(self.ledger.item(self.key)['checkpoint'], checkpoint)

    def test_all_three_delays_persist_and_exhaustion_requires_operator_retry(self):
        self.launch()
        for attempt, delay in enumerate(DELAYS, 1):
            self.now += 601
            self.sched.tick()
            reopened = self.open_ledger()
            saved = reopened.item(self.key)
            self.assertEqual((saved['startup_retries'], saved['retry_not_before']), (attempt, self.now + delay))
            self.assertEqual(saved['capacity_retries'], 0)
            self.now += max(delay, 600)
            self.assertEqual(self.sched.tick()['launched'], 1)
        self.now += 601
        self.sched.tick()
        self.assertEqual(self.ledger.item(self.key)['state'], 'failed')
        self.assertEqual(self.ledger.item(self.key)['startup_retries'], 3)
        from agent.monitor_view import _failure_code
        self.assertEqual(_failure_code('worker startup automatic retries exhausted (3)'), 'worker_startup_exhausted')
        self.assertEqual(self.ledger.retry(self.key, 'operator starts a fresh allowance')['startup_retries'], 0)

    def test_pending_fence_survives_restart_and_refuses_late_claim_until_quiescent(self):
        row = self.launch()
        pending = StartupRecovery(self.ledger).begin(row, 'timeout')
        with self.assertRaisesRegex(LedgerError, 'startup recovery'):
            self.open_ledger().claim(self.key, worker_id='late old worker')
        self.sched.active.clear()
        with patch.object(self.launcher, 'assert_quiescent', side_effect=RuntimeError('live descendant')):
            self.sched.tick()
        self.assertEqual(self.ledger.item(self.key)['worker_pid'], row['worker_pid'])
        self.assertEqual(self.ledger.item(self.key)['startup_retries'], 0)
        self.assertEqual(StartupRecovery(self.open_ledger()).pending(self.key), pending)
        self.sched.tick()
        self.assertIsNone(StartupRecovery(self.ledger).pending(self.key))
        self.assertEqual(self.ledger.item(self.key)['startup_retries'], 1)

    def test_live_unowned_pid_is_not_signalled_or_retried(self):
        row = self.launch()
        StartupRecovery(self.ledger).begin(row, 'timeout')
        self.sched.active.clear()
        self.launcher.alive_pids.add(row['worker_pid'])
        with patch.object(self.launcher, 'owned_pid', return_value=False):
            self.sched.tick()
        self.assertEqual(self.launcher.killed, [])
        self.assertEqual(self.ledger.item(self.key)['startup_retries'], 0)

    def test_claim_racing_timeout_wins_without_termination(self):
        row = self.launch()
        self.ledger.claim(self.key, worker_id='on time')
        self.assertIsNone(StartupRecovery(self.ledger).begin(row, 'timeout'))
        self.now += 601
        self.sched.tick()
        self.assertEqual(self.ledger.item(self.key)['state'], 'running')
        self.assertEqual(self.launcher.stopped, [])

    def test_cancelled_or_withdrawn_item_is_never_revived(self):
        row = self.launch()
        pending = StartupRecovery(self.ledger).begin(row, 'timeout')
        self.ledger.cancel(self.key, 'operator stops')
        self.assertIsNone(StartupRecovery(self.ledger).complete(pending))
        self.assertEqual(self.ledger.item(self.key)['state'], 'cancelled')
        other = self.another()
        self.ledger.set_worker(other['id'], 200, 'test')
        self.ledger.connection.execute('UPDATE work_items SET withdraw_deadline=? WHERE id=?', (self.now, other['id']))
        self.assertIsNone(StartupRecovery(self.ledger).begin(self.ledger.item(other['id']), 'timeout'))

    def test_maintenance_handoff_is_not_counted_as_startup_failure(self):
        row = self.launch()
        Maintenance(self.ledger).pause('rollout')
        self.assertIsNone(StartupRecovery(self.ledger).begin(row, 'timeout'))
        self.assertEqual(self.ledger.item(self.key)['startup_retries'], 0)

    def test_exit_before_claim_retries_but_running_failure_does_not(self):
        row = self.launch()
        self.launcher.finished.append(Finished(self.key, 1, '', False, 'exited', None, row['worker_pid']))
        self.sched.tick()
        self.assertEqual(self.ledger.item(self.key)['startup_retries'], 1)
        self.now += 60
        self.sched.tick()
        row = self.ledger.item(self.key)
        self.ledger.claim(self.key, worker_id='resumed')
        self.launcher.finished.append(Finished(self.key, 1, '', False, 'exited', None, row['worker_pid']))
        self.sched.tick()
        self.assertEqual(self.ledger.item(self.key)['state'], 'failed')
        self.assertEqual(self.ledger.item(self.key)['startup_retries'], 1)

    def test_capacity_exit_before_claim_uses_startup_budget_and_cooldown(self):
        row = self.launch()
        self.launcher.finished.append(Finished(self.key, 1, '', False, 'exited', 'model_capacity', row['worker_pid']))
        self.sched.tick()
        saved = self.ledger.item(self.key)
        self.assertEqual((saved['startup_retries'], saved['capacity_retries']), (1, 0))
        self.assertEqual(self.ledger.connection.execute('SELECT failures FROM worker_startup_control').fetchone()[0], 1)

    def test_cluster_cools_queue_and_successful_single_probe_restores_admission(self):
        first = self.launch()
        second = self.another()
        self.sched.tick()
        second = self.ledger.item(second['id'])
        store = StartupRecovery(self.ledger)
        store.begin(first, 'timeout')
        store.begin(second, 'timeout')
        self.sched.tick()
        self.assertFalse(StartupRecovery(self.open_ledger()).may_launch('test'))
        self.assertTrue(store.may_launch('other-host'))
        self.now += 179
        self.assertEqual(self.sched.tick()['launched'], 0)
        self.now += 1
        self.assertEqual(self.sched.tick()['launched'], 1)
        probe = self.ledger.launched()[0]
        self.assertFalse(store.may_launch('test'))
        self.ledger.claim(probe['id'], worker_id='healthy probe')
        self.assertTrue(StartupRecovery(self.open_ledger()).may_launch('test'))
        self.assertEqual(self.sched.tick()['launched'], 1)

    def test_unrelated_old_claim_cannot_clear_cooldown_and_failed_probe_extends_it(self):
        first = self.launch()
        second = self.another()
        self.sched.tick()
        second = self.ledger.item(second['id'])
        store = StartupRecovery(self.ledger)
        store.begin(first, 'timeout')
        store.begin(second, 'timeout')
        self.sched.tick()
        store.claimed({'host': 'test', 'id': 'old', 'generation': 0, 'worker_pid': 999})
        self.assertFalse(store.may_launch('test'))
        self.now += 180
        self.sched.tick()
        self.now += 601
        self.sched.tick()
        control = self.ledger.connection.execute('SELECT * FROM worker_startup_control').fetchone()
        self.assertEqual(control['cooldown_until'], self.now + 360)
        self.assertFalse(store.may_launch('test'))

    def test_stale_attempt_does_not_fence_a_successor(self):
        row = self.launch()
        self.ledger.set_worker(self.key, row['worker_pid'] + 100, 'test')
        self.assertIsNone(StartupRecovery(self.ledger).begin(row, 'stale poll'))
        self.assertIsNone(StartupRecovery(self.ledger).pending(self.key))

    def test_probe_claim_with_pending_input_generation_restores_admission(self):
        self.owner()
        self.ledger.connection.execute('INSERT INTO worker_startup_control VALUES(?,2,?,0,NULL,NULL,NULL)',
                                       ('test', self.now))
        self.ledger.connection.execute('UPDATE work_items SET requeue_requested=1 WHERE id=?', (self.key,))
        self.ledger.set_worker(self.key, 321, 'test')
        self.ledger.claim(self.key, worker_id='new input probe')
        self.assertTrue(StartupRecovery(self.ledger).may_launch('test'))
        self.assertIsNone(self.ledger.connection.execute('SELECT * FROM worker_startup_control').fetchone())

    def test_begin_is_idempotent_and_old_schema_upgrades_without_changing_work(self):
        self.owner()
        self.ledger.connection.execute('DROP TABLE worker_startup_recovery')
        self.ledger.connection.execute('DROP TABLE worker_startup_control')
        self.ledger.connection.execute('ALTER TABLE work_items DROP COLUMN startup_retries')
        reopened = self.open_ledger()
        self.assertEqual(reopened.item(self.key)['startup_retries'], 0)
        reopened.set_worker(self.key, 222, 'test')
        store = StartupRecovery(reopened)
        row = reopened.item(self.key)
        first = store.begin(row, 'timeout')
        self.assertEqual(store.begin(row, 'timeout'), first)
        self.assertEqual(reopened.connection.execute('SELECT failures FROM worker_startup_control').fetchone()[0], 1)

    def test_cancelled_probe_can_be_replaced_without_reopening_the_whole_queue(self):
        first = self.launch()
        other = self.another()
        self.sched.tick()
        store = StartupRecovery(self.ledger)
        store.begin(first, 'timeout')
        store.begin(self.ledger.item(other['id']), 'timeout')
        self.sched.tick()
        self.now += 180
        self.sched.tick()
        probe = self.ledger.launched()[0]
        self.ledger.cancel(probe['id'], 'operator cancelled probe')
        self.assertTrue(store.may_launch('test'))
        self.assertIsNotNone(self.ledger.connection.execute('SELECT * FROM worker_startup_control').fetchone())

    @unittest.skipUnless(os.name == 'nt', 'native Windows Job Object recovery')
    def test_native_timeout_reaps_descendants_before_retry_and_leaves_other_processes(self):
        self.owner()
        sched = self.scheduler()
        root = Path(self.tmp.name)
        child_file = root / 'child.txt'
        worker = root / '启动 worker.py'
        worker.write_text('import subprocess,sys,time\nfrom pathlib import Path\n'
                          'sys.stdin.read()\n'
                          'child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"])\n'
                          f'Path({str(child_file)!r}).write_text(str(child.pid))\n'
                          'time.sleep(60)\n', encoding='utf-8')
        launcher = Launcher(root / 'native runs', RUNTIMES['fake']._replace(command=[sys.executable, str(worker)]), 'test')
        sched.launcher = launcher
        unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        self.addCleanup(lambda: (unrelated.terminate(), unrelated.wait(timeout=5)))
        handle = launcher.spawn(self.key, 'startup stalled', {}, 60, root)
        self.addCleanup(lambda: (launcher.stop(self.key), launcher.poll()))
        self.ledger.set_worker(self.key, handle.pid, 'test')
        sched.active[self.key] = handle
        deadline = time.monotonic() + 10
        while not child_file.exists() and time.monotonic() < deadline:
            time.sleep(.03)
        self.assertTrue(child_file.exists())
        child = int(child_file.read_text())
        self.now += 601
        sched.tick()
        with self.assertRaisesRegex(LedgerError, 'startup recovery'):
            self.ledger.claim(self.key, worker_id='late')
        while self.ledger.item(self.key)['startup_retries'] == 0 and time.monotonic() < deadline:
            sched.tick()
            time.sleep(.03)
        self.assertEqual(self.ledger.item(self.key)['startup_retries'], 1)
        self.assertFalse(launcher.alive(child))
        self.assertIsNone(unrelated.poll())
        launcher.assert_quiescent(self.key, handle.pid)
