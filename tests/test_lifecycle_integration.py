"""Temporary Git + production service components + real worker, with stub Linear only."""
import json
from pathlib import Path
import sys
import time
import unittest

from agent.ledger import LedgerError
import test_service
from test_ledger import ISSUE, PIN, issue
from test_receiver import APP
from test_worktrees import git


class LifecycleIntegrationTests(unittest.TestCase):
    setUp = test_service.ServeTests.setUp
    drain_workers = test_service.ServeTests.drain_workers

    def snapshot(self, **changes):
        raw = issue(labels=['Bug'], delegate_id=APP,
                    updated_at='2026-09-21T00:00:00Z')
        raw.update(changes)
        (self.stub / 'issue.json').write_text(json.dumps(raw))
        return raw

    def job(self):
        self.c.ledger.observe_issue(self.snapshot())
        self.c.ledger.ensure_session('integration-session', ISSUE, delegation=True)
        return self.c.ledger.create_work_item(issue_id=ISSUE, session_id='integration-session', skill='fix', target=PIN)

    def test_close_running_worker_preserves_source_logs_and_fresh_restart_evidence(self):
        job = self.job()
        script = '''import os,pathlib,sys,time
from agent.ledger import Ledger
sys.stdin.read()
ledger = Ledger(os.environ['FARMBOT_DB'])
item = os.environ['FARMBOT_ITEM_ID']
token = ledger.claim(item, worker_id='integration-worker')['token']
ledger.checkpoint(item, token, {{'progress': 'unfinished integration fix'}})
pathlib.Path('README.md').write_text('tracked unfinished fix')
pathlib.Path('new-source.txt').write_text('untracked unfinished fix')
pathlib.Path('test-token.txt').write_text(token)
print('worker evidence retained', flush=True)
time.sleep(120)
'''
        self.c.launcher.runtime = self.c.launcher.runtime._replace(command=[sys.executable, '-c', script])
        self.assertEqual(self.c.scheduler.tick()['launched'], 1)
        handle = self.c.scheduler.active[job['id']]
        worktree = self.c.paths.worktrees / job['id'] / 'Farm-Client'
        deadline = time.monotonic() + 10
        while not (worktree / 'test-token.txt').exists() and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertTrue((worktree / 'test-token.txt').exists())
        token = (worktree / 'test-token.txt').read_text()
        (worktree / 'test-token.txt').unlink()  # fixture credential is not source to preserve
        before_logs = set(self.c.paths.runs.rglob('*.log'))
        self.snapshot(status='Done', status_type='completed', updated_at='2026-09-21T01:00:00Z')
        self.c.lifecycle.refresh(ISSUE)
        self.assertIsNotNone(handle.process.poll())
        with self.assertRaises(LedgerError):
            self.c.ledger.renew(job['id'], token)
        self.c.scheduler.tick()
        cleanup = self.c.ledger.cleanup_record(job['id'])
        self.assertTrue(cleanup['done'], cleanup)
        self.assertFalse(worktree.exists())
        self.assertTrue(before_logs <= set(self.c.paths.runs.rglob('*.log')))
        self.assertIn('worker evidence retained', (handle.run_dir / 'stdout.log').read_text())
        saved = cleanup['result']; ref = saved['refs']['Farm-Client']
        clone = self.c.worktrees.clone_path('Farm-Client')
        self.assertEqual(git('show', ref + ':README.md', cwd=clone), 'tracked unfinished fix')
        self.assertEqual(git('show', ref + ':new-source.txt', cwd=clone), 'untracked unfinished fix')
        self.snapshot(updated_at='2026-09-21T02:00:00Z')
        self.c.lifecycle.refresh(ISSUE)
        self.assertEqual(self.c.scheduler.tick()['launched'], 0)
        successor = self.c.ledger.retry(job['id'], 'explicit restart after reopening')
        self.assertNotEqual(job['id'], successor['id'])
        context = self.c.ledger.issue_context(successor['id'])['recovery']
        self.assertTrue(context['revalidation_required'])
        self.assertEqual(context['cleanup']['result']['refs'], saved['refs'])
        self.assertEqual(self.c.ledger.item(job['id'])['state'], 'cancelled')
        self.assertEqual(self.c.scheduler.tick()['launched'], 1)
        self.assertIn(successor['id'], self.c.scheduler.active)

    def test_open_paused_reply_keeps_job_and_worktree(self):
        job = self.job()
        path = self.c.worktrees.add('Farm-Client', job['id'], 'farmbot/paused')
        (path / 'unfinished.txt').write_text('keep while waiting')
        token = self.c.ledger.claim(job['id'], worker_id='test')['token']
        self.c.ledger.await_input(job['id'], token, 'Which behavior?')
        self.c.lifecycle.refresh(ISSUE)
        self.c.scheduler.tick()
        self.assertEqual(self.c.ledger.item(job['id'])['state'], 'awaiting_input')
        self.assertTrue(path.exists())
        resumed = self.c.ledger.push_inbox(job['id'], 'Use zero tables', resume_waiting=True)
        self.assertEqual((resumed['item_id'], resumed['state']), (job['id'], 'queued'))
        self.assertIsNone(self.c.ledger.cleanup_record(job['id']))
