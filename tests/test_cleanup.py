"""Cleanup must preserve source and prove process/resource safety before deleting worktrees."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from agent.ledger import LedgerError
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
        (item / 'Farm-Client').symlink_to(self.origin, target_is_directory=True)
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
