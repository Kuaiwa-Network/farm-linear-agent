"""Review handoff is an issue status, never a persistent PR merge blocker."""
from types import SimpleNamespace
from unittest.mock import Mock
import unittest

from agent.attention import REVIEW_LABEL, reconcile_review_label
from agent.lifecycle import Lifecycle
from agent.linear_api import LinearError
from test_review import APP, ReviewFixture
import test_attention


class ReviewLabelApiTests(unittest.TestCase):
    api = test_attention.ReplyLabelTests.api

    def test_creates_review_label_and_preserves_reply_and_other_labels(self):
        self.api(issue_labels=[{'id': 'bug', 'name': 'Bug'}, {'id': 'reply', 'name': '待回复'}]).awaiting_review('issue')
        value = self.http.calls[-2][2]['variables']['input']
        self.assertEqual((value['name'], value['teamId'], value['color']), (REVIEW_LABEL, 'team', '#F2C94C'))
        self.assertIn('不阻止', value['description'])
        self.assertEqual(self.http.calls[-1][2]['variables'], {'id': 'issue', 'label': 'new'})

    def test_removes_only_the_review_status_label(self):
        self.api(issue_labels=[{'id': 'bug', 'name': 'Bug'}, {'id': 'reply', 'name': '待回复'},
                               {'id': 'review', 'name': REVIEW_LABEL}]).awaiting_review('issue', False)
        self.assertEqual(self.http.calls[-1][2]['variables'], {'id': 'issue', 'label': 'review'})
        self.assertIn('issueRemoveLabel', self.http.calls[-1][2]['query'])

    def test_changed_generation_fences_a_slow_label_read(self):
        self.assertFalse(self.api().awaiting_review('issue', still_current=lambda: False))
        self.assertEqual(len(self.http.calls), 2)  # OAuth and read only.

    def test_generation_change_during_label_creation_prevents_add(self):
        current = Mock(side_effect=[True, True, False])
        self.assertFalse(self.api().awaiting_review('issue', still_current=current))
        self.assertNotIn('FarmBotAddReplyLabel', '\n'.join(c[2].get('query', '')
                         for c in self.http.calls if isinstance(c[2], dict)))

    def test_other_bot_delegation_cannot_be_modified(self):
        api = self.api()
        self.http.answers['FarmBotReplyLabel'][0]['data']['issue']['delegate'] = {'id': 'other-bot'}
        self.assertFalse(api.awaiting_review('issue'))
        self.assertEqual(len(self.http.calls), 2)

    def test_closed_issue_never_gains_waiting_status(self):
        api = self.api()
        self.http.answers['FarmBotReplyLabel'][0]['data']['issue']['state']['type'] = 'completed'
        self.assertFalse(api.awaiting_review('issue'))
        self.assertEqual(len(self.http.calls), 2)

    def test_partial_issue_labels_cannot_authorize_a_status_write(self):
        api = self.api()
        self.http.answers['FarmBotReplyLabel'][0]['data']['issue']['labels']['pageInfo'] = {'hasNextPage': True}
        with self.assertRaises(LinearError):
            api.awaiting_review('issue')
        self.assertEqual(len(self.http.calls), 2)


class ReviewAttentionTests(ReviewFixture):
    def setUp(self):
        super().setUp()
        self.api = SimpleNamespace(app_user_id=APP, awaiting_review=Mock(return_value=True))
        self.run, self.pr = self.delivery()
        self.issue_id = self.ledger.item(self.run['item_id'])['issue_id']

    def handoff(self):
        self.store.handoff(self.run['id'], '需要人工确认数据迁移方案。')

    def sync(self):
        return reconcile_review_label(self.ledger, self.api, self.issue_id)

    def test_handoff_sets_issue_label_without_reopening_delivered_run(self):
        self.handoff()
        self.sync()
        self.assertEqual(self.api.awaiting_review.call_args.args, (self.issue_id, True))
        self.assertTrue(self.api.awaiting_review.call_args.kwargs['still_current']())
        self.assertEqual(self.store.row('review_runs', self.run['id'])['state'], 'delivered')

    def test_new_generation_clears_old_status_even_after_lost_api_response(self):
        self.handoff()
        self.api.awaiting_review.side_effect = OSError('response lost after add')
        with self.assertRaises(OSError):
            self.sync()
        self.store.db.execute("UPDATE work_items SET state='queued',generation=generation+1 WHERE id=?", (self.run['item_id'],))
        self.api.awaiting_review.side_effect = None
        self.sync()
        self.assertEqual(self.api.awaiting_review.call_args.args, (self.issue_id, False))
        self.assertEqual(self.store.row('review_runs', self.run['id'])['disposition'], 'human_handoff')

    def test_new_explicit_work_item_clears_prior_handoff_and_fences_late_add(self):
        self.handoff()
        self.sync()
        old_fence = self.api.awaiting_review.call_args.kwargs['still_current']
        self.now += 1
        self.ledger.ensure_session('new-request', self.issue_id, delegation=True)
        self.ledger.create_work_item(issue_id=self.issue_id, session_id='new-request', skill='work')
        self.assertFalse(old_fence())
        self.sync()
        self.assertEqual(self.api.awaiting_review.call_args.args, (self.issue_id, False))

    def test_chat_does_not_clear_current_handoff(self):
        self.handoff()
        self.sync()
        self.now += 1
        self.ledger.ensure_session('chat-request', self.issue_id, delegation=False)
        self.ledger.create_work_item(issue_id=self.issue_id, session_id='chat-request', skill='chat')
        self.sync()
        self.assertEqual(self.api.awaiting_review.call_args.args, (self.issue_id, True))

    def test_unowned_same_named_label_is_not_swept(self):
        self.sync()
        self.api.awaiting_review.assert_not_called()

    def test_completed_issue_clears_managed_handoff_label(self):
        self.handoff()
        self.sync()
        issue = self.ledger.issue(self.issue_id)
        self.ledger.observe_issue({**issue, 'status': 'Done', 'status_type': 'completed'})
        self.sync()
        self.assertEqual(self.api.awaiting_review.call_args.args, (self.issue_id, False))

    def test_lifecycle_retries_label_errors_without_blocking_status_reads(self):
        self.handoff()
        self.api.awaiting_review.side_effect = [OSError('offline'), True]
        self.api.issue_status = lambda iid: dict(id=iid, status='In Review', status_type='started', archived=False,
            delegate_id=APP, updated_at='2026-10-28T00:00:00Z')
        lifecycle = Lifecycle(self.ledger, self.api, SimpleNamespace(), clock=lambda: self.now, review_labels=True)
        self.assertIsNotNone(lifecycle.refresh(self.issue_id))
        self.assertIsNone(self.ledger.status_check(self.issue_id)['error'])
        self.assertIsNone(self.ledger.due_issue())
        self.now += 61
        self.assertEqual(lifecycle.tick(), {'checked': self.issue_id, 'ok': True})
        self.assertEqual(self.api.awaiting_review.call_count, 2)
        self.assertFalse(self.ledger.status_check(self.issue_id)['requested'])

    def test_lifecycle_without_review_write_authority_does_not_sync_label(self):
        self.handoff()
        self.api.issue_status = lambda iid: dict(id=iid, status='In Review', status_type='started', archived=False,
            delegate_id=APP, updated_at='2026-10-28T00:00:00Z')
        lifecycle = Lifecycle(self.ledger, self.api, SimpleNamespace(), clock=lambda: self.now)
        self.assertIsNotNone(lifecycle.refresh(self.issue_id))
        self.api.awaiting_review.assert_not_called()
