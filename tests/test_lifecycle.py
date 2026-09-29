import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from agent.lifecycle import UNDELEGATED_STATES, Lifecycle
from agent.config import Config
from agent.ledger import LedgerError
from agent.linear_api import LinearError
from agent.session_progress import SessionProgress
from agent.skills import load_skills
from agent.withdrawal import CLOSED, CLOSED_CHAT, UNDELEGATED, UNDELEGATED_CHAT
import test_scheduler
from test_ledger import ISSUE, OTHER, PIN, SESSION, issue
from test_receiver import APP
from test_skills import opt_in_skill, write_skill


def status(issue_id=ISSUE, **changes):
    return dict(id=issue_id, status='Todo', status_type='unstarted', archived=False,
                delegate_id=APP, updated_at='2026-09-21T00:00:00Z', **changes)


class LifecycleTests(unittest.TestCase):
    item = test_scheduler.SchedulerTests.item

    def setUp(self):
        test_scheduler.SchedulerTests.setUp(self)
        self.tokens = {}  # {item_id: claim token} of the jobs `job` claimed

    def lifecycle(self, snapshot=None):
        self.snapshot = snapshot or status()
        self.status_api = SimpleNamespace(app_user_id=APP, issue_status=lambda _: self.snapshot)
        return Lifecycle(self.ledger, self.status_api, self.scheduler, clock=lambda: self.now)

    def test_all_closure_types_cancel_every_unfinished_state(self):
        for change in ({'status_type': 'completed'}, {'status_type': 'canceled'}, {'status_type': 'duplicate'},
                       {'archived': True}):
            for state in ('queued', 'running', 'awaiting_input', 'awaiting_resource'):
                with self.subTest(change=change, state=state):
                    iid, session = str(uuid4()), str(uuid4())
                    job = self.item(issue_id=iid, session=session)
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
                    # Each active job's session gets one closing response (withdrawn-work design E1), and a later
                    # read of the closed card says nothing more.
                    lifecycle.refresh(iid)
                    self.assertEqual(self.said(session), [('response', CLOSED.format(bot='FarmBot'))])

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
        reads = []
        self.status_api.issue_status = lambda iid: reads.append(iid) or self.snapshot
        self.assertFalse(lifecycle.preflight(job))
        self.assertEqual(self.ledger.item(job['id'])['state'], 'queued')
        # The issue is marked undelegated: a held-back job waits for the issue's next due read (design G3).
        self.now += 59
        self.assertFalse(lifecycle.preflight(job))
        self.assertEqual(reads, [ISSUE])

    def said(self, session):
        """What the session was told, as (kind, body) pairs."""
        return [(kind, body) for sid, kind, body in self.api.activities if sid == session]

    def job(self, skill='fix', authority=None, state='queued', issue_id=None, session=None):
        """A job of `skill` with `authority` on a card of its own (a card has one active job), left in `state`: queued,
        running (claimed), awaiting_input (a question), awaiting_resource (a queued Unity request) or blocked."""
        issue_id, session = issue_id or str(uuid4()), session or str(uuid4())
        self.ledger.observe_issue(issue(id=issue_id))
        self.ledger.ensure_session(session, issue_id, delegation=True)
        job = self.ledger.create_work_item(issue_id=issue_id, session_id=session, skill=skill, target=PIN,
                                           authority=authority)
        if state in ('running', 'awaiting_input', 'awaiting_resource'):
            self.tokens[job['id']] = self.ledger.claim(job['id'], worker_id='test')['token']
        if state == 'awaiting_input':
            self.ledger.await_input(job['id'], self.tokens[job['id']], '哪个服？')
        elif state == 'awaiting_resource':
            self.ledger.await_resource(job['id'], self.tokens[job['id']], 'unity_slot', 'batch')
        elif state == 'blocked':
            self.ledger.connection.execute("UPDATE work_items SET state='blocked' WHERE id=?", (job['id'],))
        return self.ledger.item(job['id'])

    def reads(self, delegate=None, app=APP):
        """A lifecycle whose status reads find every card open and delegated to `self.delegate`, first `delegate`:
        not this app. It counts the reads in `self.read_count`."""
        self.delegate, self.read_count = delegate, 0

        def issue_status(issue_id):
            self.read_count += 1
            return {**status(issue_id), 'delegate_id': self.delegate}
        self.status_api = SimpleNamespace(app_user_id=app, issue_status=issue_status)
        return Lifecycle(self.ledger, self.status_api, self.scheduler, clock=lambda: self.now)

    def confirm(self, lifecycle, *issue_ids):
        """Two status reads of each issue a reconcile interval apart, as the poll makes them."""
        for issue_id in issue_ids:
            lifecycle.refresh(issue_id)
        self.now += 60
        for issue_id in issue_ids:
            lifecycle.refresh(issue_id)

    def state(self, job):
        return self.ledger.item(job['id'])['state']

    # Withdrawn-work design P2, P3: removing the delegation ends the work it authorised once a second read confirms.
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
                    self.assertEqual(self.ledger.item(job['id'])['state'], state)  # one read only marks (P3)
                    self.now += 60
                    self.assertIsNotNone(lifecycle.refresh(iid))
                    self.assertEqual(self.ledger.item(job['id'])['state'], 'cancelled')
                    lifecycle.refresh(iid)  # a later status read finds nothing left to cancel or say
                    self.assertEqual(self.said(session), [('response', UNDELEGATED.format(bot='FarmBot'))])
                    self.assertIn(job['id'], self.launcher.stopped)
                    self.scheduler.tick()  # cleanup preserves its source before the worktrees go
                    self.assertIn(('removed', job['id'], None), self.trees.added)
        for words in ('不再委派给 FarmBot', '分支和草稿 PR 都保留', '重新委派给 FarmBot'):
            self.assertIn(words, UNDELEGATED.format(bot='FarmBot'))

    def test_the_delegation_s_waiting_work_ends_its_claimed_worker_is_flagged_and_a_mention_s_stays(self):
        """Design P2, G1-G4: the rule follows each job's authority, not its skill's initial root. The old rule kept
        a waiting fix and a delegation's conversation on a card nobody delegated, which stranded the card."""
        feature = self.serve_feature()
        running = self.job(feature.name, state='running')
        fix = self.job('fix', state='awaiting_input')
        chat = self.job('chat', 'delegation')
        mention = self.job('chat', 'mention', 'awaiting_input')
        self.confirm(self.reads(), *(job['issue_id'] for job in (running, fix, chat, mention)))
        self.assertEqual([self.state(job) for job in (running, fix, chat, mention)],
                         ['running', 'cancelled', 'cancelled', 'awaiting_input'])
        flagged = self.ledger.item(running['id'])
        self.assertEqual((flagged['withdraw_reason'], flagged['withdraw_deadline']), ('undelegated', self.now + 1200))
        self.assertEqual(self.said(fix['session_id']), [('response', UNDELEGATED.format(bot='FarmBot'))])
        self.assertEqual(self.said(chat['session_id']), [('response', UNDELEGATED_CHAT.format(bot='FarmBot'))])
        self.assertEqual(self.said(running['session_id']) + self.said(mention['session_id']), [])
        self.assertNotIn(running['id'], self.launcher.stopped)

    def test_a_claim_between_the_status_read_and_the_cancel_keeps_its_worker(self):
        feature = self.serve_feature()
        job = self.item(skill=feature.name)
        lifecycle = self.lifecycle({**status(), 'delegate_id': None})
        lifecycle.refresh(ISSUE)
        self.now += 60
        listed = self.ledger.item(job['id'])  # queued when the confirming read listed it
        self.ledger.claim(job['id'], worker_id='test')
        with patch.object(self.ledger, 'unfinished_for_issue', return_value=[listed]):
            lifecycle.refresh(ISSUE)
        self.assertEqual(self.ledger.item(job['id'])['state'], 'running')
        self.assertEqual((self.api.activities, self.launcher.stopped), ([], []))

    def meanwhile(self, change):
        """Patch the ledger's listing of an issue's work so that `change` runs after the listing, before the read acts
        on it: what the receiver's or a worker's own connection can commit in that window."""
        listing = self.ledger.unfinished_for_issue

        def listed_then_changed(issue_id):
            rows = listing(issue_id)
            change()
            return rows
        return patch.object(self.ledger, 'unfinished_for_issue', listed_then_changed)

    def test_an_answer_that_demotes_a_conversation_during_the_confirming_read_keeps_it(self):
        """Design P1, A2d: a person's answer makes the delegation's conversation a mention's, which no withdrawal of
        the delegation touches. When the answer lands after the confirming read listed the conversation, the cancel
        or the flag rechecks its authority and leaves it alone."""
        for state in ('awaiting_input', 'running'):
            with self.subTest(state=state):
                chat = self.job('chat', 'delegation', state)
                lifecycle = self.reads()
                lifecycle.refresh(chat['issue_id'])
                self.now += 60

                def answered():
                    self.ledger.push_inbox(chat['id'], '公共测试服', resume_waiting=True, demote_to_mention=True)
                with self.meanwhile(answered):
                    lifecycle.refresh(chat['issue_id'])
                current = self.ledger.item(chat['id'])
                self.assertEqual((current['state'], current['authority'], current['withdraw_deadline']),
                                 ('queued' if state == 'awaiting_input' else 'running', 'mention', None))
                self.assertEqual(self.said(chat['session_id']), [])
                self.assertNotIn(chat['id'], self.launcher.stopped)

    def test_work_a_conversation_hands_over_to_during_the_confirming_read_is_kept(self):
        """Design P3, A11 through a handover: the confirming read listed a queued delegation conversation, which its
        worker then claimed and handed over to a new fix before the cancel. The cancel follows the handover to that
        fix, created after the read began, and leaves it: the read cannot have seen what authorised it."""
        chat = self.job('chat', 'delegation')
        self.ledger.push_inbox(chat['id'], '请修复')
        lifecycle = self.reads()
        lifecycle.refresh(chat['issue_id'])
        self.now += 60
        fixes = []

        def handed_over():
            token = self.ledger.claim(chat['id'], worker_id='test')['token']
            message = self.ledger.issue_context(chat['id'])['session_messages'][-1]['id']
            fixes.append(self.ledger.request_repair(chat['id'], token, message, APP, 'Confirmed repair.',
                                                    delegate_id=APP))
        with self.meanwhile(handed_over):
            lifecycle.refresh(chat['issue_id'])
        self.assertEqual((self.state(chat), self.state(fixes[0])), ('delivered', 'queued'))
        self.assertEqual((self.api.activities, self.launcher.stopped), ([], []))

    def test_e3_a_blocked_job_claimed_again_during_the_read_is_flagged_not_killed(self):
        """Design R8: a claimed worker on an unreachable issue is told to stop, never killed at once. A blocked job the
        deciding read listed, which a retry then gave back to a worker before the cancel, is left to the next read,
        which flags it."""
        iid = str(uuid4())
        blocked = self.job('fix', state='blocked', issue_id=iid)
        lifecycle = self.not_found()
        for elapsed in (0, 450):
            self.now = 1000.0 + elapsed
            lifecycle.refresh(iid)
        self.now = 1900.0

        def retried_and_claimed():
            self.ledger.retry(blocked['id'], 'a person asked to retry')
            self.ledger.claim(blocked['id'], worker_id='test')
        with self.meanwhile(retried_and_claimed):
            lifecycle.refresh(iid)
        self.assertEqual(self.state(blocked), 'running')
        self.assertNotIn(blocked['id'], self.launcher.stopped)
        self.now += 300
        lifecycle.refresh(iid)
        current = self.ledger.item(blocked['id'])
        self.assertEqual((current['state'], current['withdraw_reason']), ('running', 'unreachable'))

    def test_an_unknown_app_identity_cancels_nothing(self):
        feature = self.serve_feature()
        job = self.item(skill=feature.name)
        api = SimpleNamespace(app_user_id=None, issue_status=lambda _: {**status(), 'delegate_id': None})
        Lifecycle(self.ledger, api, self.scheduler, clock=lambda: self.now).refresh(ISSUE)
        self.assertEqual(self.ledger.item(job['id'])['state'], 'queued')

    def test_an_enqueued_job_is_told_by_issue_comment(self):
        feature = self.serve_feature()
        job = self.item(session=f'local-{ISSUE}', skill=feature.name)
        self.confirm(self.lifecycle({**status(), 'delegate_id': None}), ISSUE)
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

    # Withdrawn-work design §6: each scenario under its ID. "Confirm" is two reads a reconcile interval apart.

    def test_a1_waiting_delegation_chat_is_cancelled_after_a_confirmed_removal(self):
        progress = SessionProgress(self.ledger, self.api)
        chat = self.job('chat', 'delegation')
        progress.tick()  # the chat's heartbeat row, which its closing notice must drop
        token = self.ledger.claim(chat['id'], worker_id='test')['token']
        self.ledger.await_input(chat['id'], token, '哪个服？')
        lifecycle = self.reads()
        lifecycle.refresh(chat['issue_id'])
        self.assertEqual(self.state(chat), 'awaiting_input')
        self.assertEqual(self.ledger.status_check(chat['issue_id'])['undelegated_since'], 1000.0)
        self.assertEqual((self.api.activities, self.api.comments), ([], []))
        self.now += 60
        lifecycle.refresh(chat['issue_id'])
        self.assertEqual(self.state(chat), 'cancelled')
        self.assertEqual(self.said(chat['session_id']), [('response', UNDELEGATED_CHAT.format(bot='FarmBot'))])
        self.assertIsNone(self.ledger.connection.execute("SELECT 1 FROM session_progress WHERE item_id=?",
                                                         (chat['id'],)).fetchone())

    def test_a2_removal_that_leaves_the_session_open_posts_there_once(self):
        fix = self.job('fix', state='awaiting_input')
        lifecycle = self.reads()
        self.confirm(lifecycle, fix['issue_id'])
        self.assertEqual(self.state(fix), 'cancelled')
        self.now += 60
        lifecycle.refresh(fix['issue_id'])
        self.assertEqual(self.said(fix['session_id']), [('response', UNDELEGATED.format(bot='FarmBot'))])

    def test_a3_delegation_moved_to_another_app_is_withdrawal(self):
        chat = self.job('chat', 'delegation', 'awaiting_input')
        lifecycle = self.reads(delegate=str(uuid4()))
        lifecycle.refresh(chat['issue_id'])
        self.assertEqual(self.state(chat), 'awaiting_input')
        self.now += 60
        lifecycle.refresh(chat['issue_id'])
        self.assertEqual(self.state(chat), 'cancelled')
        self.assertEqual(self.said(chat['session_id']), [('response', UNDELEGATED_CHAT.format(bot='FarmBot'))])

    def test_a5_a_read_that_finds_the_delegation_clears_the_mark(self):
        fix = self.job('fix', state='awaiting_input')
        lifecycle = self.reads()
        lifecycle.refresh(fix['issue_id'])
        self.delegate = APP
        self.now += 30
        lifecycle.refresh(fix['issue_id'])
        self.assertIsNone(self.ledger.status_check(fix['issue_id'])['undelegated_since'])
        self.delegate = None
        self.now += 60
        lifecycle.refresh(fix['issue_id'])
        self.assertEqual(self.state(fix), 'awaiting_input')
        self.assertEqual(self.ledger.status_check(fix['issue_id'])['undelegated_since'], self.now)
        self.assertEqual(self.api.activities, [])

    def test_a8_mention_chat_is_kept_when_the_delegation_goes(self):
        chat = self.job('chat', 'mention', 'awaiting_input')
        self.confirm(self.reads(), chat['issue_id'])
        self.assertEqual(self.state(chat), 'awaiting_input')
        self.assertEqual((self.api.activities, self.api.comments, self.launcher.stopped), ([], [], []))

    def test_a9_running_chat_is_flagged_then_stopped_after_ten_minutes(self):
        chat = self.job('chat', 'delegation')
        self.ledger.set_worker(chat['id'], 4321, 'h', test_scheduler.SKILLS['chat'].budget['lease_seconds'])
        token = self.ledger.claim(chat['id'], worker_id='test')['token']
        self.confirm(self.reads(), chat['issue_id'])
        flagged = self.ledger.item(chat['id'])
        self.assertEqual((flagged['state'], flagged['withdraw_reason']), ('running', 'undelegated'))
        self.assertEqual(flagged['withdraw_deadline'], self.now + 600)  # twice chat's five-minute renew interval
        self.assertEqual(self.api.activities, [])
        self.now += 300
        self.ledger.renew(chat['id'], token)  # a worker that keeps renewing, but never withdraws
        self.now += 299
        self.scheduler.tick()
        self.assertEqual(self.state(chat), 'running')
        self.now += 1
        self.scheduler.tick()
        self.assertEqual(self.state(chat), 'cancelled')
        with self.assertRaises(LedgerError):
            self.ledger.renew(chat['id'], token)
        self.assertEqual(self.said(chat['session_id']), [('response', UNDELEGATED_CHAT.format(bot='FarmBot'))])
        self.assertIn(chat['id'], self.launcher.stopped)
        self.scheduler.tick()
        self.assertEqual(len(self.api.activities), 1)

    def test_a11_item_created_after_the_read_started_is_not_cancelled(self):
        """A stale read that began before a re-delegation took the card over (design K9) cancels nothing the new
        session created: a read acts only on work older than its own start (P3)."""
        chat = self.job('chat', 'delegation', 'awaiting_input')
        lifecycle = self.reads()
        lifecycle.refresh(chat['issue_id'])  # marks the card at 1000
        self.now += 60
        self.ledger.ensure_session('session-2', chat['issue_id'], delegation=True)
        read = self.status_api.issue_status
        created = []

        def redelegated_during_the_read(issue_id):
            self.now += 1
            created.append(self.ledger.supersede(chat['id'], ('awaiting_input',), session_id='session-2', skill='fix',
                                                 reason='a new delegation session took the card over',
                                                 authority='delegation')[1])
            return read(issue_id)
        self.status_api.issue_status = redelegated_during_the_read
        lifecycle.refresh(chat['issue_id'])
        self.assertEqual(self.state(chat), 'cancelled')
        self.assertEqual(self.state(created[0]), 'queued')
        self.assertEqual((self.api.activities, self.api.comments), ([], []))

    def test_c9_duplicate_closure_posts_one_closing_response_per_active_item(self):
        iid = str(uuid4())
        blocked = self.job('fix', state='blocked', issue_id=iid, session='fix-session')
        chat = self.job('chat', 'delegation', 'awaiting_input', issue_id=iid, session='chat-session')
        self.lifecycle({**status(iid), 'status_type': 'duplicate'}).refresh(iid)
        self.assertEqual([self.state(blocked), self.state(chat)], ['cancelled', 'cancelled'])
        self.assertEqual(self.api.activities, [('chat-session', 'response', CLOSED_CHAT)])

    def test_e1_closure_notices_only_active_items(self):
        """A job that was active gets one closing response in its own session, a conversation's without branch words;
        a blocked job has reported already and ends silently."""
        for skill, text in (('fix', CLOSED.format(bot='FarmBot')), ('chat', CLOSED_CHAT)):
            for state in ('queued', 'running', 'awaiting_input', 'blocked'):
                with self.subTest(skill=skill, state=state):
                    job = self.job(skill, state=state)
                    self.lifecycle({**status(job['issue_id']), 'archived': True}).refresh(job['issue_id'])
                    self.assertEqual(self.state(job), 'cancelled')
                    self.assertEqual(self.said(job['session_id']), [] if state == 'blocked' else [('response', text)])
        self.assertNotIn('分支', CLOSED_CHAT)

    def not_found(self, kind='not_found'):
        """A lifecycle whose status reads fail with Linear's `kind` of error while every other call succeeds."""
        self.status_api = SimpleNamespace(app_user_id=APP, last_success_at=0.0)

        def issue_status(issue_id):
            self.status_api.last_success_at = self.now  # the host's other calls go through meanwhile
            raise LinearError(kind)
        self.status_api.issue_status = issue_status
        return Lifecycle(self.ledger, self.status_api, self.scheduler, clock=lambda: self.now)

    def test_e3_not_found_three_times_over_fifteen_minutes_cancels_silently(self):
        iid = str(uuid4())
        blocked = self.job('fix', state='blocked', issue_id=iid)
        chat = self.job('chat', 'mention', 'awaiting_input', issue_id=iid)
        running_issue = str(uuid4())
        running = self.job('fix', state='running', issue_id=running_issue)
        lifecycle = self.not_found()
        for elapsed in (0, 450):
            self.now = 1000.0 + elapsed
            self.assertIsNone(lifecycle.refresh(iid))
            lifecycle.refresh(running_issue)
            self.assertEqual([self.state(blocked), self.state(chat), self.state(running)],
                             ['blocked', 'awaiting_input', 'running'])
        self.now = 1899.0  # a third read, short of fifteen minutes after the first
        lifecycle.refresh(iid)
        self.assertEqual(self.state(chat), 'awaiting_input')
        self.now = 1900.0
        lifecycle.refresh(iid)
        lifecycle.refresh(running_issue)
        self.assertEqual([self.state(blocked), self.state(chat)], ['cancelled', 'cancelled'])
        # A claimed worker is told to stop, not killed, and a mention's conversation goes too: the card is gone.
        self.assertEqual((self.state(running), self.ledger.item(running['id'])['withdraw_reason']),
                         ('running', 'unreachable'))
        self.assertEqual((self.api.activities, self.api.comments), ([], []))

    def test_e3_not_found_while_nothing_else_succeeds_cancels_nothing(self):
        chat = self.job('chat', 'mention', 'awaiting_input')
        lifecycle = self.not_found()

        def outage(issue_id):
            raise LinearError('not_found')
        self.status_api.issue_status = outage
        for elapsed in (0, 450, 900, 1800):
            self.now = 1000.0 + elapsed
            lifecycle.refresh(chat['issue_id'])
        self.assertEqual(self.state(chat), 'awaiting_input')

    def test_e5_forbidden_or_rate_limited_never_counts_as_unreachable(self):
        for kind in ('forbidden', 'ratelimited', 'auth', 'rejected'):
            with self.subTest(kind=kind):
                chat = self.job('chat', 'mention', 'awaiting_input')
                lifecycle = self.not_found(kind)
                for _ in range(4):
                    lifecycle.refresh(chat['issue_id'])
                    self.now += 600
                check = self.ledger.status_check(chat['issue_id'])
                self.assertEqual(self.state(chat), 'awaiting_input')
                self.assertEqual((check['failures'], check['unreachable_since']), (4, None))
                self.assertIn('LinearError', check['error'])
                self.assertGreater(check['due_at'], check['checked_at'])  # backed off as before

    def test_f1_undelegated_queued_fix_is_read_once_per_interval_then_cancelled(self):
        fix = self.job('fix')
        lifecycle = self.reads()
        self.scheduler.preflight = lifecycle.preflight
        for _ in range(30):
            self.scheduler.tick()
            self.now += 2
        self.assertEqual((self.read_count, self.launcher.spawned, self.state(fix)), (1, [], 'queued'))
        self.assertEqual(self.now, 1060.0)
        self.assertEqual(lifecycle.tick(), {'checked': fix['issue_id'], 'ok': True})
        self.assertEqual(self.state(fix), 'cancelled')
        self.assertEqual(self.said(fix['session_id']), [('response', UNDELEGATED.format(bot='FarmBot'))])

    def test_f2_queued_delegation_chat_is_not_launched_while_undelegated(self):
        iid = str(uuid4())
        chat = self.job('chat', 'delegation', issue_id=iid)
        lifecycle = self.reads()
        self.assertFalse(lifecycle.preflight(chat))
        self.assertEqual(self.state(chat), 'queued')
        self.ledger.cancel(chat['id'], 'next case')
        mention = self.job('chat', 'mention', issue_id=iid)
        self.assertTrue(lifecycle.preflight(mention))

    def test_f3_retry_delayed_item_is_cancelled(self):
        fix = self.job('fix')
        self.ledger.connection.execute('UPDATE work_items SET retry_not_before=? WHERE id=?', (self.now + 3600, fix['id']))
        self.confirm(self.reads(), fix['issue_id'])
        self.assertEqual(self.state(fix), 'cancelled')

    def test_f4_launched_unclaimed_item_is_cancelled_and_its_worker_stopped(self):
        fix = self.job('fix')
        self.ledger.set_worker(fix['id'], 4321, 'h')
        self.confirm(self.reads(), fix['issue_id'])
        self.assertEqual(self.state(fix), 'cancelled')
        self.assertIn(fix['id'], self.launcher.stopped)
        with self.assertRaises(LedgerError):
            self.ledger.claim(fix['id'], worker_id='late')

    def test_f5_f6_parked_for_a_question_or_a_wait_is_cancelled_and_the_label_stays(self):
        for reason in ('question', 'waiting'):
            with self.subTest(reason=reason):
                fix = self.job('fix', state='running')
                self.ledger.await_input(fix['id'], self.tokens[fix['id']], '配置好了请回复。', reason=reason)
                lifecycle = self.reads()
                lifecycle.api = Mock(wraps=self.status_api, app_user_id=APP)
                self.confirm(lifecycle, fix['issue_id'])
                self.assertEqual(self.state(fix), 'cancelled')
                # Only status reads reach Linear from the lifecycle: nothing takes needs-more-info off the card.
                self.assertEqual({name for name, *_ in lifecycle.api.method_calls}, {'issue_status'})
                self.assertEqual(self.said(fix['session_id']), [('response', UNDELEGATED.format(bot='FarmBot'))])

    def test_f7_queued_reservation_is_cancelled(self):
        fix = self.job('fix', state='awaiting_resource')
        self.confirm(self.reads(), fix['issue_id'])
        self.assertEqual(self.state(fix), 'cancelled')
        self.assertEqual([r['state'] for r in self.ledger.reservations() if r['item_id'] == fix['id']], ['cancelled'])

    def test_f8_granted_slot_is_released_through_settlement(self):
        fix = self.job('fix', state='awaiting_resource')
        self.ledger.ensure_slot('unity_slot:1', kind='unity_slot', host='h', folder=str(self.slot_folder))
        granted = self.ledger.acquire('unity_slot', owner='pool', host='h')
        self.ledger.resume(fix['id'], 'slot granted')
        self.confirm(self.reads(), fix['issue_id'])
        self.assertEqual(self.state(fix), 'cancelled')
        self.assertEqual(self.ledger.reservation(granted['reservation_id'])['state'], 'cancel_requested')
        self.assertIn(granted['reservation_id'], [r['reservation_id'] for r in self.ledger.reservations_to_settle()])

    def test_f10_pending_handoff_is_cancelled_and_its_retiring_worker_stopped(self):
        fix = self.job('fix')
        self.scheduler.tick()  # launched: the handoff keeps this worker's pid until its teardown is proven
        token = self.ledger.claim(fix['id'], worker_id='first')['token']
        self.ledger.checkpoint(fix['id'], token, {'handoff': {
            'facts': [], 'hypotheses': [], 'checks': [], 'repositories': [], 'next_actions': ['Check server']}})
        self.ledger.handoff_repository(fix['id'], token, 'farm-hive', skill=test_scheduler.SKILLS['fix'])
        self.launcher.stopped.clear()
        self.confirm(self.reads(), fix['issue_id'])
        current = self.ledger.item(fix['id'])
        self.assertEqual((current['state'], current['next_root_repo']), ('cancelled', None))
        self.assertIn(fix['id'], self.launcher.stopped)

    def test_f13_blocked_item_is_untouched_by_undelegation(self):
        fix = self.job('fix', state='blocked')
        self.confirm(self.reads(), fix['issue_id'])
        self.assertEqual(self.state(fix), 'blocked')
        self.assertEqual(self.api.activities, [])

    def test_g_rule_follows_authority_not_initial_root(self):
        feature = self.serve_feature()
        root = Path(self.tmp.name) / 'fgui-skills'
        write_skill(root, 'fgui', trigger=['delegation'], writes=['farmgui'])
        fgui = load_skills(root)['fgui']
        self.scheduler.skills = {**self.scheduler.skills, 'fgui': fgui}
        self.assertIsNone(fgui.initial_root)
        withdrawn = [self.job(feature.name), self.job('fix'), self.job('fgui'), self.job('chat', 'delegation')]
        operator_issue = str(uuid4())
        kept = [self.job('chat', 'mention'),
                self.job('chat', 'operator', issue_id=operator_issue, session=f'local-{operator_issue}')]
        self.confirm(self.reads(), *(job['issue_id'] for job in withdrawn + kept))
        self.assertEqual([self.state(job) for job in withdrawn], ['cancelled'] * 4)
        self.assertEqual([self.state(job) for job in kept], ['queued'] * 2)

    def test_h1_poll_alone_confirms_without_any_webhook(self):
        fix = self.job('fix', state='awaiting_input')
        lifecycle = self.reads()
        self.assertEqual(lifecycle.tick()['checked'], fix['issue_id'])
        self.assertEqual(lifecycle.tick(), {'checked': None})  # the next read is an interval away
        self.now += 60
        self.assertEqual(lifecycle.tick()['checked'], fix['issue_id'])
        self.assertEqual(self.state(fix), 'cancelled')

    def test_i1_enqueued_write_item_is_withdrawn_by_comment_and_operator_chat_kept(self):
        fix_issue, chat_issue = str(uuid4()), str(uuid4())
        # The authorities `agent.service enqueue` records: delegation for a write job, operator for a chat.
        fix = self.job('fix', 'delegation', issue_id=fix_issue, session=f'local-{fix_issue}')
        chat = self.job('chat', 'operator', issue_id=chat_issue, session=f'local-{chat_issue}')
        self.confirm(self.reads(), fix_issue, chat_issue)
        self.assertEqual([self.state(fix), self.state(chat)], ['cancelled', 'queued'])
        self.assertEqual((self.api.comments, self.api.activities),
                         ([(fix_issue, UNDELEGATED.format(bot='FarmBot'))], []))

    def test_j6_unknown_identity_refuses_delegation_launches(self):
        fix, mention = self.job('fix'), self.job('chat', 'mention')
        lifecycle = self.reads(app=None)
        self.assertFalse(lifecycle.preflight(fix))
        self.assertTrue(lifecycle.preflight(mention))
        self.now += 60
        lifecycle.refresh(fix['issue_id'])
        self.assertEqual(self.state(fix), 'queued')  # an unknown identity withdraws nothing either

    def test_k8_flap_shorter_than_an_interval_cancels_nothing(self):
        fix = self.job('fix', state='awaiting_input')
        lifecycle = self.reads()
        lifecycle.refresh(fix['issue_id'])
        self.delegate = APP
        self.now += 30
        lifecycle.refresh(fix['issue_id'])
        self.delegate = None
        self.now += 31
        lifecycle.refresh(fix['issue_id'])
        self.assertEqual(self.state(fix), 'awaiting_input')
        self.assertEqual(self.api.activities, [])
