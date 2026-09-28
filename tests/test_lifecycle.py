import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from agent.lifecycle import UNDELEGATED, UNDELEGATED_STATES, Lifecycle
from agent.config import Config
from agent.ledger import LedgerError
import test_scheduler
from test_ledger import ISSUE, OTHER, SESSION, issue
from test_receiver import APP
from test_skills import opt_in_skill


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

    # Spec §9.8, D16: removing the delegation ends a job of a skill with an initial root that no worker holds.
    def serve_feature(self):
        """Serve Task 1's opt_in_skill, the plan's feature manifest with initial root Farm-Contract, beside fix and chat."""
        feature = opt_in_skill(Path(self.tmp.name) / 'fixture-skills')
        self.scheduler.skills = {**test_scheduler.SKILLS, feature.name: feature}
        return feature

    def parked(self, skill, state, issue_id=ISSUE, session=SESSION):
        job = self.item(issue_id=issue_id, session=session, skill=skill)
        if state == 'awaiting_input':
            token = self.ledger.claim(job['id'], worker_id='test')['token']
            self.ledger.await_input(job['id'], token, '配置好了请回复。', reason='waiting')
        elif state == 'awaiting_resource':
            # Parked for a slot directly: no Phase B feature job asks for one (P6, P11), but fgui will.
            self.ledger.connection.execute("UPDATE work_items SET state='awaiting_resource',needs_resource=? WHERE id=?",
                                           ('unity_slot:batch', job['id']))
        return job

    def test_removing_the_delegation_cancels_a_waiting_job_with_an_initial_root_and_says_so_once(self):
        feature = self.serve_feature()
        for state in UNDELEGATED_STATES:
            for delegate in (None, str(uuid4())):  # removed, or handed to another app
                with self.subTest(state=state, delegate=delegate):
                    iid, session = str(uuid4()), str(uuid4())
                    job = self.parked(feature.name, state, iid, session)
                    lifecycle = self.lifecycle({**status(iid), 'delegate_id': delegate})
                    self.assertIsNotNone(lifecycle.refresh(iid))
                    self.assertEqual(self.ledger.item(job['id'])['state'], 'cancelled')
                    lifecycle.refresh(iid)  # a later status read finds nothing left to cancel or say
                    self.assertEqual([(kind, body) for sid, kind, body in self.api.activities if sid == session],
                                     [('response', UNDELEGATED.format(bot='FarmBot'))])
                    self.assertIn(job['id'], self.launcher.stopped)
                    self.scheduler.tick()  # cleanup preserves its source before the worktrees go
                    self.assertIn(('removed', job['id'], None), self.trees.added)
        for words in ('不再委派给 FarmBot', '分支和草稿 PR 都保留', '重新委派给 FarmBot'):
            self.assertIn(words, UNDELEGATED.format(bot='FarmBot'))

    def test_a_claimed_worker_a_fix_and_a_conversation_keep_going_when_the_delegation_goes(self):
        feature = self.serve_feature()
        running = self.item(skill=feature.name)
        self.ledger.claim(running['id'], worker_id='test')
        fix = self.parked('fix', 'awaiting_input', OTHER, 'fix-session')
        chat_issue = str(uuid4())
        chat = self.parked('chat', 'queued', chat_issue, 'chat-session')
        for issue_id in (ISSUE, OTHER, chat_issue):
            self.lifecycle({**status(issue_id), 'delegate_id': None}).refresh(issue_id)
        self.assertEqual([self.ledger.item(job['id'])['state'] for job in (running, fix, chat)],
                         ['running', 'awaiting_input', 'queued'])
        self.assertEqual((self.api.activities, self.launcher.stopped), ([], []))

    def test_a_claim_between_the_status_read_and_the_cancel_keeps_its_worker(self):
        feature = self.serve_feature()
        job = self.item(skill=feature.name)
        listed = self.ledger.item(job['id'])  # queued when the lifecycle listed it
        self.ledger.claim(job['id'], worker_id='test')
        with patch.object(self.ledger, 'unfinished_for_issue', return_value=[listed]):
            self.lifecycle({**status(), 'delegate_id': None}).refresh(ISSUE)
        self.assertEqual(self.ledger.item(job['id'])['state'], 'running')
        self.assertEqual((self.api.activities, self.launcher.stopped), ([], []))

    def test_an_unknown_app_identity_cancels_nothing(self):
        feature = self.serve_feature()
        job = self.item(skill=feature.name)
        api = SimpleNamespace(app_user_id=None, issue_status=lambda _: {**status(), 'delegate_id': None})
        Lifecycle(self.ledger, api, self.scheduler, clock=lambda: self.now).refresh(ISSUE)
        self.assertEqual(self.ledger.item(job['id'])['state'], 'queued')

    def test_an_enqueued_job_is_told_by_issue_comment(self):
        feature = self.serve_feature()
        job = self.item(session=f'local-{ISSUE}', skill=feature.name)
        self.lifecycle({**status(), 'delegate_id': None}).refresh(ISSUE)
        self.assertEqual(self.ledger.item(job['id'])['state'], 'cancelled')
        self.assertEqual((self.api.comments, self.api.activities), ([(ISSUE, UNDELEGATED.format(bot='FarmBot'))], []))

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
