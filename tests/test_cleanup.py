"""Cleanup must preserve source and prove process/resource safety before deleting worktrees."""
import json
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

from agent.launcher import _PINNED_EXIT, Launcher, RUNTIMES
from agent.ledger import LedgerError
from agent.scheduler import Scheduler
from agent.worktrees import WorktreeError
import test_worktrees
import test_scheduler


class PreservationTests(unittest.TestCase):
    setUp = test_worktrees.WorktreeTests.setUp

    def test_clean_unpushed_and_dirty_changes_survive_worktree_removal(self):
        path = self.trees.add('Farm-Client', 'item-1', 'farmbot/farm-1')
        (path / 'committed.txt').write_text('local committed change')
        test_worktrees.git('add', '.', cwd=path)
        test_worktrees.git('commit', '-qm', 'unpublished', cwd=path)
        (path / 'untracked.txt').write_text('also keep this')
        saved = self.trees.preserve('item-1')
        self.assertEqual(saved['errors'], {})
        self.trees.remove_preserved('item-1', saved)
        self.assertFalse(path.exists())
        clone = self.trees.clone_path('Farm-Client')
        ref = saved['refs']['Farm-Client']
        self.assertEqual(test_worktrees.git('rev-parse', ref, cwd=clone), saved['committed']['Farm-Client'])
        self.assertEqual(test_worktrees.git('show', ref + ':untracked.txt', cwd=clone), 'also keep this')
        self.assertEqual(test_worktrees.git('show', ref + ':committed.txt', cwd=clone), 'local committed change')

    def test_clean_detached_head_is_referenced(self):
        path = self.trees.add_detached('Farm-Client', 'item-1')
        before = self.trees.head(path)
        saved = self.trees.preserve('item-1')
        self.trees.remove_preserved('item-1', saved)
        self.assertEqual(test_worktrees.git('rev-parse', saved['refs']['Farm-Client'],
                                           cwd=self.trees.clone_path('Farm-Client')), before)

    def test_divergent_cleanup_retains_each_snapshot_after_git_pruning(self):
        path = self.trees.add('Farm-Client', 'item-1', 'farmbot/history')
        baseline = self.trees.head(path)
        (path / 'fix.txt').write_text('first attempt')
        first = self.trees.preserve('item-1')
        original = first['committed']['Farm-Client']
        test_worktrees.git('reset', '--hard', baseline, cwd=path)
        second = self.trees.preserve('item-1')
        self.assertEqual(second['errors'], {})
        self.trees.remove_preserved('item-1', second)
        clone = self.trees.clone_path('Farm-Client')
        test_worktrees.git('reflog', 'expire', '--expire=now', '--all', cwd=clone)
        test_worktrees.git('gc', '--prune=now', cwd=clone)
        snapshots = second.get('snapshots', {}).get('Farm-Client', {})
        self.assertEqual(set(snapshots), {original, baseline})
        for sha, ref in snapshots.items():
            self.assertEqual(test_worktrees.git('rev-parse', ref, cwd=clone), sha)
        self.assertEqual(test_worktrees.git('show', snapshots[original] + ':fix.txt', cwd=clone), 'first attempt')
        self.assertEqual(test_worktrees.git('rev-parse', second['refs']['Farm-Client'], cwd=clone), baseline)

    def test_first_cleanup_after_upgrade_archives_the_legacy_recovery_ref(self):
        path = self.trees.add('Farm-Client', 'item-1', 'farmbot/history')
        baseline = self.trees.head(path)
        (path / 'fix.txt').write_text('legacy work')
        legacy = self.trees.commit_wip('item-1', 'legacy work')['committed']['Farm-Client']
        clone = self.trees.clone_path('Farm-Client')
        latest = 'refs/farmbot/recovery/item-1'
        test_worktrees.git('update-ref', latest, legacy, cwd=clone)
        test_worktrees.git('reset', '--hard', baseline, cwd=path)
        saved = self.trees.preserve('item-1')
        self.assertEqual(saved['errors'], {})
        history = saved.get('snapshots', {}).get('Farm-Client', {})
        self.assertIn(legacy, history)
        self.assertEqual(test_worktrees.git('show', history[legacy] + ':fix.txt', cwd=clone), 'legacy work')
        self.assertEqual(test_worktrees.git('rev-parse', latest, cwd=clone), baseline)

    def test_conflicting_snapshot_prevents_recovery_ref_replacement_and_removal(self):
        path = self.trees.add('Farm-Client', 'item-1', 'farmbot/history')
        baseline = self.trees.head(path)
        first = self.trees.preserve('item-1')
        (path / 'fix.txt').write_text('new work')
        changed = self.trees.commit_wip('item-1', 'new work')['committed']['Farm-Client']
        clone = self.trees.clone_path('Farm-Client')
        test_worktrees.git('update-ref', f'refs/farmbot/recovery-history/item-1/{changed}', baseline, cwd=clone)
        saved = self.trees.preserve('item-1')
        self.assertIn('Farm-Client', saved['errors'])
        self.assertEqual(test_worktrees.git('rev-parse', first['refs']['Farm-Client'], cwd=clone), baseline)
        with self.assertRaises(WorktreeError):
            self.trees.remove_preserved('item-1', saved)
        self.assertEqual((path / 'fix.txt').read_text(), 'new work')

    def test_partial_preservation_failure_keeps_all_worktrees(self):
        path = self.trees.add('Farm-Client', 'item-1', 'farmbot/farm-1')
        (path / 'fix.txt').write_text('keep')
        with patch.object(self.trees, 'commit_wip', return_value={'committed': {}, 'errors': {'Farm-Client': 'disk full'}}):
            saved = self.trees.preserve('item-1')
        with self.assertRaises(WorktreeError):
            self.trees.remove_preserved('item-1', saved)
        self.assertEqual((path / 'fix.txt').read_text(), 'keep')

    def test_changed_work_after_preservation_is_not_deleted(self):
        path = self.trees.add('Farm-Client', 'item-1', 'farmbot/farm-1')
        saved = self.trees.preserve('item-1')
        (path / 'later.txt').write_text('late write')
        with self.assertRaises(WorktreeError):
            self.trees.remove_preserved('item-1', saved)
        self.assertTrue(path.exists())

    def test_symlinked_worktree_is_never_followed(self):
        item = self.trees.worktrees_root / 'item-1'
        item.mkdir(parents=True)
        try:
            (item / 'Farm-Client').symlink_to(self.origin, target_is_directory=True)
        except OSError as exc:
            if getattr(exc, 'winerror', None) == 1314:
                self.skipTest('Windows symlink privilege unavailable')
            raise
        before = test_worktrees.git('rev-parse', 'HEAD', cwd=self.origin)
        with self.assertRaises(WorktreeError):
            self.trees.preserve('item-1')
        self.assertEqual(test_worktrees.git('rev-parse', 'HEAD', cwd=self.origin), before)
        self.assertTrue((self.origin / 'README.md').exists())

    def test_replaced_ref_keeps_worktree(self):
        path = self.trees.add('Farm-Client', 'item-1', 'farmbot/farm-1')
        saved = self.trees.preserve('item-1')
        test_worktrees.git('update-ref', '-d', saved['refs']['Farm-Client'], cwd=self.trees.clone_path('Farm-Client'))
        with self.assertRaises(WorktreeError):
            self.trees.remove_preserved('item-1', saved)
        self.assertTrue(path.exists())

    def test_second_repository_error_keeps_first_repository(self):
        self.trees.remotes['second'] = str(self.origin)
        first = self.trees.add('Farm-Client', 'item-1', 'farmbot/farm-1')
        second = self.trees.add('second', 'item-1', 'farmbot/farm-1')
        (second / 'pending.txt').write_text('pending')
        with patch.object(self.trees, 'commit_wip', return_value={'committed': {}, 'errors': {'second': 'disk full'}}):
            saved = self.trees.preserve('item-1')
        with self.assertRaises(WorktreeError):
            self.trees.remove_preserved('item-1', saved)
        self.assertTrue(first.exists())
        self.assertTrue(second.exists())


class CancellationCleanupTests(unittest.TestCase):
    setUp = test_scheduler.SchedulerTests.setUp
    item = test_scheduler.SchedulerTests.item

    def test_stop_revokes_claim_before_kill_callback(self):
        item = self.item()
        self.scheduler.tick()
        token = self.ledger.claim(item['id'], worker_id='worker')['token']
        def observe(_):
            self.assertEqual(self.ledger.item(item['id'])['state'], 'cancelled')
            with self.assertRaises(LedgerError):
                self.ledger.renew(item['id'], token)
        self.launcher.on_stop = observe
        self.scheduler.stop(item['id'], 'issue closed')

    def test_cancelled_sweep_preserves_source_and_retains_logs(self):
        item = self.item()
        state = self.launcher.state_dir(item['id']) if hasattr(self.launcher, 'state_dir') else self.launcher.runs / item['id']
        state.mkdir(parents=True)
        log = state / 'stdout.log'; log.write_text('retained evidence')
        self.ledger.cancel(item['id'], 'closed')
        self.scheduler.tick()
        self.assertTrue(log.exists())
        self.assertTrue(self.ledger.cleanup_record(item['id'])['done'])
        actions = self.trees.added
        self.assertLess(actions.index(('committed', item['id'], 'wip(' + item['id'][:8] + '): preserve ended work')),
                        actions.index(('removed', item['id'], None)))

    def test_preservation_failure_retains_worktree_and_reports_error(self):
        item = self.item()
        self.trees.commit_fails = True
        self.ledger.cancel(item['id'], 'closed')
        self.scheduler.tick()
        self.assertNotIn(('removed', item['id'], None), self.trees.added)
        self.assertTrue(self.ledger.cleanup_record(item['id'])['error'])
        self.assertFalse(self.ledger.cleanup_record(item['id'])['done'])

    def test_unverified_live_pid_holds_cleanup(self):
        item = self.item()
        self.ledger.set_worker(item['id'], 777, 'h')
        self.ledger.cancel(item['id'], 'closed')
        self.launcher.alive_pids.add(777)
        with patch.object(self.launcher, 'owned_pid', return_value=False):
            self.scheduler.tick()
        self.assertNotIn(('removed', item['id'], None), self.trees.added)
        self.assertNotIn(777, self.launcher.killed)
        self.assertTrue(self.ledger.cleanup_record(item['id'])['error'])

    def test_restart_kills_owned_pid_and_then_cleans(self):
        item = self.item()
        self.ledger.set_worker(item['id'], 777, 'h')
        self.ledger.cancel(item['id'], 'closed')
        self.launcher.alive_pids.add(777)
        self.scheduler.tick()
        self.assertIn(777, self.launcher.killed)
        self.assertTrue(self.ledger.cleanup_record(item['id'])['done'])

    def test_successor_waits_for_failed_preservation(self):
        item = self.item()
        self.ledger.cancel(item['id'], 'closed')
        successor = self.ledger.retry(item['id'], 'restart')
        self.trees.commit_fails = True
        self.scheduler.tick()
        self.assertEqual(self.launcher.spawned, [])
        self.trees.commit_fails = False
        self.scheduler.tick()
        self.assertEqual(self.launcher.spawned[0][0], successor['id'])

    def test_restart_records_descendants_before_signalling(self):
        item = self.item()
        self.ledger.set_worker(item['id'], 777, 'h')
        self.ledger.cancel(item['id'], 'closed')
        self.launcher.alive_pids.add(777)
        def kill(pid):
            self.assertEqual(self.ledger.cleanup_record(item['id'])['result']['processes'], [777, 778])
            self.launcher.alive_pids.discard(pid)
            return [777, 778]
        with patch.object(self.launcher, 'descendants', return_value=[778], create=True), patch.object(self.launcher, 'kill_pid', side_effect=kill):
            self.scheduler.tick()
        self.assertTrue(self.ledger.cleanup_record(item['id'])['done'])

    def test_live_descendant_recorded_in_attempt_directory_holds_cleanup(self):
        item = self.item()
        self.ledger.cancel(item['id'], 'closed')
        attempt = self.launcher.state_dir(item['id']) / 'attempt-1'
        attempt.mkdir(parents=True)
        (attempt / 'killed.json').write_text(json.dumps({'pid': 777, 'descendants': [778]}))
        self.launcher.alive_pids.add(778)
        self.scheduler.tick()
        self.assertFalse(self.ledger.cleanup_record(item['id'])['done'])
        self.assertNotIn(('removed', item['id'], None), self.trees.added)

    def test_paused_worker_pid_survives_restart_and_cancellation(self):
        item = self.item()
        self.ledger.set_worker(item['id'], 777, 'h')
        token = self.ledger.claim(item['id'], worker_id='worker')['token']
        self.ledger.await_input(item['id'], token, 'question?')
        self.assertIsNone(self.ledger.item(item['id'])['worker_pid'])
        self.ledger.cancel(item['id'], 'closed before old worker exited')
        self.launcher.alive_pids.add(777)
        with patch.object(self.launcher, 'owned_pid', return_value=False):
            self.scheduler.tick()
        self.assertEqual(self.ledger.cleanup_record(item['id'])['worker_pid'], 777)
        self.assertFalse(self.ledger.cleanup_record(item['id'])['done'])

    def test_recovery_retains_unverifiable_live_worker(self):
        item = self.item()
        self.ledger.set_worker(item['id'], 777, 'h')
        self.launcher.alive_pids.add(777)
        self.scheduler.preflight = lambda _: False
        with patch.object(self.launcher, 'owned_pid', return_value=False):
            self.scheduler.tick()
        self.assertEqual(self.ledger.item(item['id'])['state'], 'queued')
        self.assertNotIn(('removed', item['id'], None), self.trees.added)

    def test_stale_terminal_snapshot_cannot_bypass_cancel_cleanup(self):
        item = self.item()
        self.ledger.cancel(item['id'], 'closed after sweep snapshot')
        self.trees.commit_fails = True
        self.scheduler._retire(item['id'], 'blocked')
        self.assertNotIn(('removed', item['id'], None), self.trees.added)
        self.assertTrue(self.ledger.cleanup_record(item['id'])['error'])

    def test_partial_removal_keeps_all_repository_evidence_on_retry(self):
        fixture = test_worktrees.WorktreeTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        trees = fixture.trees
        trees.remotes['second'] = str(fixture.origin)
        item = self.item()
        for repo in ('Farm-Client', 'second'):
            trees.add(repo, item['id'], 'farmbot/preserve')
        self.scheduler.worktrees = trees
        self.ledger.cancel(item['id'], 'closed')
        import agent.worktrees as module
        original = module._git
        def remove_failure(*args, **kwargs):
            if args[:2] == ('worktree', 'remove') and Path(args[-1]).name == 'second':
                raise WorktreeError('transient remove failure')
            return original(*args, **kwargs)
        with patch.object(module, '_git', side_effect=remove_failure):
            self.scheduler.tick()
        self.assertFalse(self.ledger.cleanup_record(item['id'])['done'])
        self.assertEqual(set(self.ledger.cleanup_record(item['id'])['result']['refs']), {'Farm-Client', 'second'})
        self.scheduler.tick()
        record = self.ledger.cleanup_record(item['id'])
        self.assertTrue(record['done'])
        self.assertEqual(set(record['result']['refs']), {'Farm-Client', 'second'})
        self.assertEqual(set(record['result']['committed']), {'Farm-Client', 'second'})
        self.assertEqual(set(record['result']['snapshots']), {'Farm-Client', 'second'})

    @unittest.skipIf(os.name == 'nt', 'Windows worker jobs contain children; tested in test_windows_workers')
    def test_exited_parent_with_detached_child_holds_cleanup_after_restart(self):
        import os
        import signal
        import sys
        from agent.launcher import Launcher, RUNTIMES
        item = self.item()
        marker = Path(self.tmp.name) / 'child.pid'
        script = ('import pathlib,subprocess,sys; sys.stdin.read(); '
                  'child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(120)"],'
                  'start_new_session=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); '
                  'pathlib.Path(sys.argv[1]).write_text(str(child.pid))')
        runtime = RUNTIMES['fake']._replace(command=[sys.executable, '-c', script, str(marker)])
        launcher = Launcher(Path(self.tmp.name) / 'runs', runtime, 'h')
        handle = launcher.spawn(item['id'], 'test', {}, 30, self.tmp.name)
        self.addCleanup(launcher.poll)
        self.addCleanup(launcher.stop, item['id'])
        handle.process.wait(timeout=10)
        child = int(marker.read_text())
        self.addCleanup(launcher._signal_pid, child, signal.SIGKILL)
        self.assertTrue(launcher.alive(child))
        self.ledger.set_worker(item['id'], handle.pid, 'h')
        token = self.ledger.claim(item['id'], worker_id='test')['token']
        self.ledger.await_input(item['id'], token, 'question')
        launcher.poll()
        self.scheduler.launcher = Launcher(Path(self.tmp.name) / 'runs', runtime, 'h')
        self.scheduler.stop(item['id'], 'closed')
        self.scheduler.tick()
        self.assertFalse(self.ledger.cleanup_record(item['id'])['done'])
        self.assertNotIn(('removed', item['id'], None), self.trees.added)
        self.assertTrue(self.scheduler.launcher.alive(child))

    def test_retry_cannot_start_while_worktree_removal_is_in_progress(self):
        item = self.item()
        self.ledger.fail_queued(item['id'], 'failed launch')
        original = self.trees.remove_preserved
        def remove(item_id, evidence):
            with self.assertRaises(LedgerError):
                self.ledger.retry(item_id, 'racing retry')
            original(item_id, evidence)
        with patch.object(self.trees, 'remove_preserved', side_effect=remove):
            self.scheduler._retire(item['id'], 'failed')
        self.assertTrue(self.ledger.cleanup_record(item['id'])['done'])
        self.assertEqual(self.ledger.item(item['id'])['state'], 'failed')
        self.assertEqual(self.ledger.retry(item['id'], 'retry after removal')['id'], item['id'])


@unittest.skipIf(os.name == 'nt', 'POSIX self-exit evidence; Windows workers are proved by Job Objects')
@unittest.skipUnless(_PINNED_EXIT, 'needs os.waitid (CPython 3.13+ on macOS)')
class SelfExitedWorkerCleanupTests(unittest.TestCase):
    """The real launcher under the scheduler. FakeLauncher.assert_quiescent does not model teardown evidence,
    so no FakeLauncher test can see a normally exited POSIX worker hold its cleanup."""
    item = test_scheduler.SchedulerTests.item

    def setUp(self):
        test_scheduler.SchedulerTests.setUp(self)
        root = Path(self.tmp.name)
        self.release = root / 'release'
        worker = root / 'worker.py'
        worker.write_text('import os, sys, time\nsys.stdin.read()\ndeadline = time.time() + 30\n'
                          f'while not os.path.exists({str(self.release)!r}) and time.time() < deadline:\n'
                          '    time.sleep(0.02)\n', encoding='utf-8')
        self.launcher = Launcher(root / 'runs', RUNTIMES['fake']._replace(command=[sys.executable, str(worker)]), 'h')
        self.scheduler = Scheduler(self.ledger, self.launcher, test_scheduler.SKILLS, self.trees,
                                   skill_root=test_scheduler.ROOT / 'skills', db_path=root / 'ledger.sqlite3',
                                   runtime_name='fake', host='h', max_concurrent=1, api=self.api)
        self.addCleanup(self.stop_workers)

    def stop_workers(self):
        for item_id in list(self.launcher.running()):
            self.launcher.stop(item_id, grace=1.0)
        deadline = time.monotonic() + 10
        while self.launcher.running() and time.monotonic() < deadline:
            self.launcher.poll()
            time.sleep(0.02)

    def wait_reaped(self, item_id):
        deadline = time.monotonic() + 10
        while item_id in self.launcher.running() and time.monotonic() < deadline:
            self.scheduler.tick()
            time.sleep(0.02)
        self.assertNotIn(item_id, self.launcher.running())

    def test_continuation_of_a_self_exited_paused_worker_launches(self):
        # A paused fix worker exits by itself, the job is cancelled, and a continuation is requested: the
        # successor waits for its predecessor's cleanup, which on POSIX never finished.
        item = self.item()
        self.scheduler.tick()
        token = self.ledger.claim(item['id'], worker_id='worker')['token']
        self.ledger.await_input(item['id'], token, 'question?')
        self.release.touch()
        self.wait_reaped(item['id'])
        self.ledger.cancel(item['id'], 'closed')
        self.scheduler.tick()
        self.release.unlink()
        successor = self.ledger.retry(item['id'], 'continue')
        self.scheduler.tick()
        self.assertIn(successor['id'], self.launcher.running(), self.ledger.cleanup_record(item['id'])['error'])

    def test_retirement_leaves_the_reap_of_a_registered_worker_to_poll(self):
        # Reaping in _retire would release the pid that pins the worker's session before poll() checks it.
        item = self.item()

        def exited_then_refused(item_id, pid, *args):
            deadline = time.monotonic() + 10
            while (os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None
                   and time.monotonic() < deadline):
                time.sleep(0.02)
            raise LedgerError('claim fenced')

        self.release.touch()
        with patch.object(self.ledger, 'set_worker', side_effect=exited_then_refused):
            self.scheduler.tick()
        self.assertEqual(self.ledger.item(item['id'])['state'], 'failed')
        self.wait_reaped(item['id'])
        record = self.ledger.cleanup_record(item['id'])
        self.assertTrue(record['done'], record['error'])

    def test_resource_fencing_certifies_a_worker_that_exited_by_itself(self):
        # Unity failover fences the revoked attempt. If it already exited, Stop has nothing to kill and its
        # still-pinned session is the proof; an unreaped zombie is not a live process of unknown ownership.
        item = self.item()
        self.release.touch()
        self.scheduler.tick()
        handle = self.launcher.running()[item['id']]
        deadline = time.monotonic() + 10
        while (os.waitid(os.P_PID, handle.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None
               and time.monotonic() < deadline):
            time.sleep(0.02)
        self.scheduler.fence_resource_worker({'item_id': item['id'], 'worker_pid': handle.pid})
        self.assertNotIn(item['id'], self.launcher.running())
        self.assertIs(json.loads((handle.run_dir / 'killed.json').read_text())['empty'], True)
