"""Question comments survive retries and collect independently attributed answers."""
import copy
from unittest.mock import Mock, patch

from agent.__main__ import parser, run
from agent.ledger import LedgerError
from agent.question_threads import prepare, reconcile
from test_ledger import LedgerBase, ISSUE, SESSION, OWNER, DESIGNER, issue
from test_receiver import APP


class QuestionThreadTests(LedgerBase):
    def setUp(self):
        super().setUp()
        self.card = issue(delegate_id=APP, updated_at='2026-10-23T00:00:00Z')
        self.ledger.observe_issue(self.card)
        self.ledger.ensure_session(SESSION, ISSUE, delegation=True)
        self.item = self.ledger.create_work_item(issue_id=ISSUE, session_id=SESSION, skill='work')['id']
        self.ledger.set_worker(self.item, 123, 'test')
        self.token = self.ledger.claim(self.item, worker_id='test')['token']
        self.checkpoint()
        self.api = Mock(app_user_id=APP)
        self.api.issue_status.side_effect = lambda _: copy.deepcopy(self.card)
        self.api.fetch_issue.side_effect = lambda _: copy.deepcopy(self.card)
        self.api.awaiting_reply.return_value = True
        self.api.create_comment.side_effect = self.post

    def checkpoint(self):
        self.ledger.checkpoint(self.item, self.token, {'summary': 'Saved work', 'handoff': 'Continue retained work'})

    def post(self, issue_id, body):
        self.assertEqual(issue_id, ISSUE)
        count = sum(c['id'].startswith('root') for c in self.card['comments'])
        key = f'root-{count + 1}' if count else 'root'
        self.card['comments'].append(self.comment(key, body, kind='bot', author=None, parent=None))
        return key

    def comment(self, key, body='Q1: daily', *, parent='root', author=OWNER, kind='human', minute=1):
        return {'id': key, 'body': body, 'parent_id': parent, 'author_kind': kind, 'author': author,
                'url': f'https://linear.app/example/issue/FARM-1#comment-{key}',
                'created_at': '2026-10-23T00:00:00Z', 'updated_at': f'2026-10-23T00:{minute:02d}:00Z'}

    def ask(self, body='Q1: reset period? Q2: which asset?'):
        args = parser().parse_args(['--db', str(self.path), 'await-input', '--item', self.item,
                                   '--token', self.token, '--question', body])
        with patch('agent.__main__.configured_bot_name', return_value='TestBot'):
            return run(args, self.ledger, lambda: self.api)

    def resume(self):
        self.token = self.ledger.claim(self.item, worker_id='resumed')['token']
        self.ledger.pop_inbox(self.item, self.token)
        self.checkpoint()

    def messages(self):
        return self.ledger.issue_context(self.item)['session_messages']

    def test_question_is_a_comment_and_session_points_to_it(self):
        parked = self.ask()
        self.assertEqual(parked['state'], 'awaiting_input')
        self.api.create_comment.assert_called_once()
        body = self.api.create_comment.call_args.args[1]
        self.assertIn('Q1: reset period?', body)
        self.assertIn('无需 @提及', body)
        self.assertIn('[farmbot:', body)
        self.assertIn('issue 评论', self.api.create_activity.call_args.args[1]['body'])
        self.assertNotIn('Q1: reset period?', self.api.create_activity.call_args.args[1]['body'])

    def test_two_people_answer_separately_without_losing_first_answer(self):
        self.ask()
        question = self.ledger.human_question(self.item)
        self.card['comments'].append(self.comment('answer-1'))
        reconcile(self.ledger, self.api, ISSUE)
        first = self.messages()[-1]
        self.assertEqual(first['author'], OWNER)
        self.assertEqual(self.ledger.item(self.item)['state'], 'queued')
        self.resume()
        self.ask('Only Q2 remains: which asset?')
        self.assertEqual(self.ledger.item(self.item)['state'], 'awaiting_input')
        self.assertEqual(self.ledger.human_question(self.item)['id'], question['id'])
        self.assertEqual(self.ledger.human_question(self.item)['after_message_id'], question['after_message_id'])
        self.api.create_comment.assert_called_once()
        self.card['comments'].append(self.comment('answer-2', 'Q2: approved asset A', author=DESIGNER))
        reconcile(self.ledger, self.api, ISSUE)
        second = self.messages()[-1]
        self.assertEqual(second['author'], DESIGNER)
        self.resume()
        self.assertIsNotNone(self.ledger.human_question(self.item))
        self.ledger.resolve_question(self.item, self.token, question['id'], [first['id'], second['id']])
        self.assertIsNone(self.ledger.human_question(self.item))
        self.ask('New question round')
        self.assertNotEqual(self.ledger.human_question(self.item)['id'], question['id'])
        self.assertEqual(self.api.create_comment.call_count, 2)
        reconcile(self.ledger, self.api, ISSUE)
        self.assertEqual(self.ledger.item(self.item)['state'], 'awaiting_input')
        self.assertEqual(len(self.messages()), 2)  # Old-thread replies cannot answer this new round.

    def test_poll_is_idempotent_and_delivers_an_edit_once(self):
        self.ask()
        reply = self.comment('answer')
        self.card['comments'].append(reply)
        reconcile(self.ledger, self.api, ISSUE)
        reconcile(self.ledger, self.api, ISSUE)
        self.assertEqual(len(self.messages()), 1)
        stale = copy.deepcopy(reply)
        reply.update(body='Correction: weekly', updated_at='2026-10-23T00:02:00Z')
        reconcile(self.ledger, self.api, ISSUE)
        reconcile(self.ledger, self.api, ISSUE)
        self.assertEqual(len(self.messages()), 2)
        self.card['comments'][-1] = stale
        reconcile(self.ledger, self.api, ISSUE)
        reconcile(self.open_ledger(), self.api, ISSUE)
        self.assertEqual(len(self.messages()), 2)

    def test_only_attributed_humans_in_the_question_thread_wake_it(self):
        self.ask()
        self.card['comments'] += [self.comment('unrelated', parent=None),
            self.comment('other-thread', parent='another-question'),
            self.comment('bot', kind='bot', author=None), self.comment('unknown', kind='unknown', author=None),
            self.comment('unattributed', author=None), self.comment('empty', '  ')]
        reconcile(self.ledger, self.api, ISSUE)
        self.assertEqual(self.messages(), [])
        self.assertEqual(self.ledger.item(self.item)['state'], 'awaiting_input')
        self.card['comments'].append(self.comment('nested', parent='bot'))
        reconcile(self.ledger, self.api, ISSUE)
        self.assertEqual(len(self.messages()), 1)

    def test_lost_post_response_reconciles_marker_on_retry(self):
        def ambiguous(issue_id, body):
            self.post(issue_id, body)
            raise TimeoutError('response lost')
        self.api.create_comment.side_effect = ambiguous
        with self.assertRaises(TimeoutError):
            self.ask()
        self.assertEqual(self.ledger.item(self.item)['state'], 'running')
        self.ask()
        self.api.create_comment.assert_called_once()
        self.assertEqual(self.ledger.item(self.item)['state'], 'awaiting_input')

    def test_reply_between_comment_and_park_is_not_stranded(self):
        def reply_during_pointer(*_):
            self.card['comments'].append(self.comment('fast-answer'))
            reconcile(self.ledger, self.api, ISSUE)
        self.api.create_activity.side_effect = reply_during_pointer
        self.assertEqual(self.ask()['state'], 'queued')
        self.assertEqual(len(self.messages()), 1)

    def test_no_poll_before_confirmed_comment_or_after_resolution(self):
        reconcile(self.ledger, self.api, ISSUE)
        prepare(self.ledger, self.item, self.token, 'Question?', 'TestBot')
        reconcile(self.ledger, self.api, ISSUE)
        self.api.fetch_issue.assert_not_called()
        self.ask()
        self.card['comments'].append(self.comment('answer'))
        reconcile(self.ledger, self.api, ISSUE)
        self.resume()
        self.ledger.resolve_question(self.item, self.token, self.ledger.human_question(self.item)['id'],
                                     [self.messages()[-1]['id']])
        self.api.fetch_issue.reset_mock()
        reconcile(self.ledger, self.api, ISSUE)
        self.api.fetch_issue.assert_not_called()

    def test_closed_withdrawn_and_stopped_jobs_cannot_resume_from_comments(self):
        self.ask()
        self.card['comments'].append(self.comment('answer'))
        for changes in ({'archived': True}, {'status_type': 'completed'}, {'delegate_id': None}):
            with self.subTest(changes=changes):
                before = copy.deepcopy(self.card)
                self.card.update(changes)
                # Give the restored snapshot a fresh version too.
                self.card['updated_at'] = '2026-10-23T01:00:00Z'
                reconcile(self.ledger, self.api, ISSUE)
                self.assertEqual(self.messages(), [])
                self.card = before
                self.card['updated_at'] = '2026-10-23T02:00:00Z'
                self.ledger.observe_issue(self.card)
        self.ledger.cancel(self.item, 'Stopped')
        reconcile(self.ledger, self.api, ISSUE)
        self.assertEqual(self.ledger.item(self.item)['state'], 'cancelled')
        self.assertEqual(self.messages(), [])

    def test_concurrent_resolution_during_fetch_cannot_route_to_a_new_round(self):
        self.ask()
        self.card['comments'].append(self.comment('answer'))
        reconcile(self.ledger, self.api, ISSUE)
        self.resume()
        question = self.ledger.human_question(self.item)
        def resolved(_):
            self.ledger.resolve_question(self.item, self.token, question['id'], [self.messages()[-1]['id']])
            return copy.deepcopy(self.card)
        self.api.fetch_issue.side_effect = resolved
        reconcile(self.ledger, self.api, ISSUE)
        self.assertIsNone(self.ledger.human_question(self.item))
        self.assertEqual(len(self.messages()), 1)

    def test_lifecycle_polls_thread_and_keeps_attention_until_explicit_resolution(self):
        from agent.lifecycle import Lifecycle
        self.ask()
        self.card['comments'].append(self.comment('answer'))
        lifecycle = Lifecycle(self.ledger, self.api, Mock(), clock=lambda: self.now)
        self.assertIsNotNone(lifecycle.refresh(ISSUE))
        self.assertEqual(self.ledger.item(self.item)['state'], 'queued')
        self.assertIsNotNone(self.ledger.human_question(self.item))
        self.api.awaiting_reply.assert_called_with(ISSUE, True)

    def test_comment_read_failure_preserves_question_and_records_retryable_status(self):
        from agent.lifecycle import Lifecycle
        self.ask()
        question = self.ledger.human_question(self.item)
        self.api.fetch_issue.side_effect = TimeoutError('offline')
        lifecycle = Lifecycle(self.ledger, self.api, Mock(), clock=lambda: self.now)
        self.assertIsNone(lifecycle.refresh(ISSUE))
        self.assertIn('TimeoutError', self.ledger.status_check(ISSUE)['error'])
        self.assertEqual(self.ledger.human_question(self.item), question)
        self.assertEqual(self.ledger.item(self.item)['state'], 'awaiting_input')

    def test_new_comment_is_refused_when_fresh_full_read_loses_delegation(self):
        self.api.fetch_issue.side_effect = lambda _: {**copy.deepcopy(self.card), 'delegate_id': None}
        with self.assertRaisesRegex(LedgerError, 'withdrawn'):
            self.ask()
        self.api.create_comment.assert_not_called()
        self.api.create_activity.assert_not_called()

    def test_changed_issue_response_cannot_deliver_replies(self):
        from test_ledger import OTHER
        self.ask()
        self.api.fetch_issue.side_effect = lambda _: {**copy.deepcopy(self.card), 'id': OTHER}
        with self.assertRaisesRegex(ValueError, 'different issue'):
            reconcile(self.ledger, self.api, ISSUE)
        self.assertEqual(self.messages(), [])

    def test_changed_issue_response_cannot_post_question(self):
        from test_ledger import OTHER
        self.api.fetch_issue.side_effect = lambda _: {**copy.deepcopy(self.card), 'id': OTHER}
        with self.assertRaisesRegex(LedgerError, 'different issue'):
            self.ask()
        self.api.create_comment.assert_not_called()

    def test_stop_during_comment_read_cannot_resurrect_job(self):
        self.ask()
        self.card['comments'].append(self.comment('answer'))
        def stopped(_):
            self.ledger.cancel(self.item, 'Stopped during read')
            return copy.deepcopy(self.card)
        self.api.fetch_issue.side_effect = stopped
        reconcile(self.ledger, self.api, ISSUE)
        self.assertEqual(self.messages(), [])
        self.assertEqual(self.ledger.item(self.item)['state'], 'cancelled')
