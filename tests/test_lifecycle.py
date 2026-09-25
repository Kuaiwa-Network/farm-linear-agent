import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from agent.lifecycle import Lifecycle
from agent.config import Config
from agent.ledger import LedgerError
import test_scheduler
from test_ledger import ISSUE, OTHER, SESSION, issue
from test_receiver import APP


def status(issue_id=ISSUE, **changes):
    return dict(id=issue_id, status='Todo', status_type='unstarted', archived=False,
                delegate_id=APP, updated_at='2026-09-21T00:00:00Z', **changes)


class LifecycleTests(unittest.TestCase):
    setUp = test_scheduler.SchedulerTests.setUp
    item = test_scheduler.SchedulerTests.item

    def lifecycle(self, snapshot=None):
        self.snapshot = snapshot or status()
        self.status_api = SimpleNamespace(app_user_id=APP, issue_status=lambda _: self.snapshot)
        return Lifecycle(self.ledger, self.status_api, self.scheduler, clock=lambda: self.now)

    def test_all_closure_types_cancel_every_unfinished_state(self):
        for change in ({'status_type': 'completed'}, {'status_type': 'canceled'}, {'status_type': 'duplicate'},
                       {'archived': True}):
            for state in ('queued', 'running', 'awaiting_input', 'awaiting_resource'):
                with self.subTest(change=change, state=state):
                    iid = str(uuid4()); job = self.item(issue_id=iid, session=str(uuid4()))
                    token = None
                    if state != 'queued':
                        token = self.ledger.claim(job['id'], worker_id='test')['token']
                    if state == 'awaiting_input':
                        self.ledger.await_input(job['id'], token, 'question?')
                    elif state == 'awaiting_resource':
                        self.ledger.await_resource(job['id'], token, 'unity_slot', 'batch')
                    lifecycle = self.lifecycle({**status(iid), **change})
                    lifecycle.refresh(iid)
                    self.assertEqual(self.ledger.item(job['id'])['state'], 'cancelled')
                    if token:
                        with self.assertRaises(LedgerError):
                            self.ledger.renew(job['id'], token)

    def test_preflight_refuses_every_closure_type_and_admits_a_started_issue(self):
        for change in ({'status_type': 'completed'}, {'status_type': 'canceled'}, {'status_type': 'duplicate'},
                       {'archived': True}):
            with self.subTest(change=change):
                iid = str(uuid4()); job = self.item(issue_id=iid, session=str(uuid4()))
                self.assertFalse(self.lifecycle({**status(iid), **change}).preflight(job))
                self.assertEqual(self.ledger.item(job['id'])['state'], 'cancelled')
        job = self.item()
        self.assertTrue(self.lifecycle({**status(), 'status': 'In Review', 'status_type': 'started'}).preflight(job))
        self.assertEqual(self.ledger.item(job['id'])['state'], 'queued')

    def test_api_failure_is_visible_and_backed_off_without_cancelling(self):
        job = self.item()
        lifecycle = self.lifecycle()
        with patch.object(self.status_api, 'issue_status', side_effect=OSError('offline')) as call:
            self.assertFalse(lifecycle.preflight(job))
            self.assertFalse(lifecycle.preflight(job))
            self.assertEqual(call.call_count, 1)
        self.assertEqual(self.ledger.item(job['id'])['state'], 'queued')
        self.assertTrue(self.ledger.status_check(ISSUE)['error'])
        self.assertGreater(self.ledger.status_check(ISSUE)['due_at'], self.now)

    def test_reopen_does_not_revive_cancelled_job(self):
        job = self.item()
        lifecycle = self.lifecycle({**status(), 'status_type': 'completed'})
        lifecycle.refresh(ISSUE)
        self.snapshot = {**status(), 'updated_at': '2026-09-21T01:00:00Z'}
        lifecycle.refresh(ISSUE)
        self.assertEqual(self.ledger.item(job['id'])['state'], 'cancelled')
        self.assertEqual(self.ledger.queue(), [])

    def test_old_status_and_full_snapshots_cannot_undo_closure(self):
        self.item()
        lifecycle = self.lifecycle({**status(), 'status_type': 'completed', 'updated_at': '2026-09-21T01:00:00Z'})
        lifecycle.refresh(ISSUE)
        self.snapshot = status()
        lifecycle.refresh(ISSUE)
        self.ledger.observe_issue(issue(updated_at='2026-09-21T00:00:00Z'))
        self.assertEqual(self.ledger.issue(ISSUE)['status_type'], 'completed')

    def test_lost_delegation_blocks_write_launch_but_keeps_waiting_job(self):
        job = self.item()
        lifecycle = self.lifecycle({**status(), 'delegate_id': None})
        self.assertFalse(lifecycle.preflight(job))
        self.assertEqual(self.ledger.item(job['id'])['state'], 'queued')

    def test_poll_is_fair_when_first_issue_fails(self):
        self.item()
        self.item(issue_id=OTHER, session='other')
        lifecycle = self.lifecycle()
        called = []
        def fetch(iid):
            called.append(iid)
            if iid == ISSUE:
                raise OSError('offline')
            return status(iid)
        self.status_api.issue_status = fetch
        lifecycle.tick(); lifecycle.tick(); lifecycle.tick()
        self.assertEqual(set(called), {ISSUE, OTHER})
        self.assertEqual(len(called), 2)

    def test_preflight_runs_outside_lock_and_cancellation_during_preparation_prevents_spawn(self):
        job = self.item()
        def check(item):
            self.assertFalse(self.scheduler.lock._is_owned())
            return True
        self.scheduler.preflight = check
        original = self.trees.add
        def prepare(*args):
            path = original(*args)
            self.ledger.cancel(job['id'], 'closed during fetch')
            return path
        with patch.object(self.trees, 'add', side_effect=prepare):
            self.scheduler.tick()
        self.assertEqual(self.launcher.spawned, [])
        self.assertNotIn(('removed', job['id'], None), self.trees.added)

    def test_failed_preflight_never_spawns(self):
        self.item()
        self.scheduler.preflight = lambda item: False
        self.scheduler.tick()
        self.assertEqual(self.launcher.spawned, [])

    def test_config_reconcile_interval_is_positive_and_finite(self):
        self.assertEqual(Config('a', 'b', 'c').reconcile_seconds, 60)
        for value in (0, -1, True, float('inf'), '60'):
            with self.assertRaises(ValueError):
                Config('a', 'b', 'c', reconcile_seconds=value)

    def test_stop_returns_while_preparation_holds_scheduler_lock(self):
        import threading
        from agent.ledger import Ledger
        job = self.item()
        self.scheduler.control_ledger_factory = lambda: Ledger(self.scheduler.db_path)
        self.scheduler.lock.acquire()
        stopped = threading.Event()
        def stop():
            self.scheduler.stop(job['id'], 'closed')
            stopped.set()
        thread = threading.Thread(target=stop, daemon=True)
        thread.start()
        try:
            self.assertTrue(stopped.wait(1), 'Stop waited for preparation lock')
        finally:
            self.scheduler.lock.release()
            thread.join(5)
