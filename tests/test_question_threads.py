"""Question comments survive retries and collect independently attributed answers."""
import copy
import hashlib
import hmac
import json
from unittest.mock import Mock, patch

from agent.__main__ import parser, run
from agent.ledger import LedgerError
from agent.question_threads import prepare, collect_replies
from agent.receiver import Receiver
from test_ledger import LedgerBase, ISSUE, SESSION, OWNER, DESIGNER, issue
from test_receiver import APP, IDENTITY


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
        self.api.session_has_artificial_root.return_value = False
        self.receiver = Receiver(self.path, 'secret', IDENTITY, self.api, self.open_ledger,
                                 skills={'chat', 'work'}, scheduler=Mock(), clock=lambda: self.now)
        self.addCleanup(self.receiver.close)
        self.trigger_count = 0

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
        self.ledger.push_inbox(self.item, 'Please review and continue', author=OWNER, resume_waiting=True)
        self.token = self.ledger.claim(self.item, worker_id='resumed')['token']
        self.ledger.pop_inbox(self.item, self.token)
        self.checkpoint()

    def messages(self):
        return [m for m in self.ledger.issue_context(self.item)['session_messages']
                if m['body'].startswith('Reply to question ')]

    def trigger(self, *, session_reply=False, author=OWNER, body='@TestBot everyone has answered; review and continue'):
        self.trigger_count += 1
        session = SESSION if session_reply else f'mention-{self.trigger_count}'
        event = {'type': 'AgentSessionEvent', 'action': 'prompted' if session_reply else 'created',
                 'webhookTimestamp': 100_000, **IDENTITY,
                 'agentSession': {'id': session, 'issue': {'id': ISSUE}, 'creator': author}}
        if session_reply:
            event['agentActivity'] = {'id': f'prompt-{self.trigger_count}', 'agentSessionId': session,
                                     'content': {'type': 'prompt', 'body': body}, 'user': author}
        else:
            event['agentSession']['sourceComment'] = {'id': f'trigger-{self.trigger_count}', 'body': body}
        payload = json.dumps(event).encode()
        signature = hmac.new(b'secret', payload, hashlib.sha256).hexdigest()
        self.assertEqual(self.receiver.receive(payload, signature, now_ms=100_000), (200, 'accepted'))
        self.assertTrue(self.receiver.process_one())

    def test_question_is_a_comment_and_session_points_to_it(self):
        parked = self.ask()
        self.assertEqual(parked['state'], 'awaiting_input')
        self.api.create_comment.assert_called_once()
        body = self.api.create_comment.call_args.args[1]
        self.assertIn('Q1: reset period?', body)
        self.assertIn('@TestBot', body)
        self.assertIn('等大家回答完毕后', body)
        self.assertIn('[farmbot:', body)
        self.assertIn('issue 评论', self.api.create_activity.call_args.args[1]['body'])
        self.assertNotIn('Q1: reset period?', self.api.create_activity.call_args.args[1]['body'])

    def test_two_people_answer_separately_without_losing_first_answer(self):
        self.ask()
        question = self.ledger.human_question(self.item)
        self.card['comments'].append(self.comment('answer-1'))
        collect_replies(self.ledger, self.api, ISSUE)
        first = self.messages()[-1]
        self.assertEqual(first['author'], OWNER)
        self.assertEqual(self.ledger.item(self.item)['state'], 'awaiting_input')
        self.resume()
        self.ask('Only Q2 remains: which asset?')
        self.assertEqual(self.ledger.item(self.item)['state'], 'awaiting_input')
        self.assertEqual(self.ledger.human_question(self.item)['id'], question['id'])
        self.assertEqual(self.ledger.human_question(self.item)['after_message_id'], question['after_message_id'])
        self.api.create_comment.assert_called_once()
        self.card['comments'].append(self.comment('answer-2', 'Q2: approved asset A', author=DESIGNER))
        collect_replies(self.ledger, self.api, ISSUE)
        second = self.messages()[-1]
        self.assertEqual(second['author'], DESIGNER)
        self.resume()
        self.assertIsNotNone(self.ledger.human_question(self.item))
        self.ledger.resolve_question(self.item, self.token, question['id'], [first['id'], second['id']])
        self.assertIsNone(self.ledger.human_question(self.item))
        self.ask('New question round')
        self.assertNotEqual(self.ledger.human_question(self.item)['id'], question['id'])
        self.assertEqual(self.api.create_comment.call_count, 2)
        collect_replies(self.ledger, self.api, ISSUE)
        self.assertEqual(self.ledger.item(self.item)['state'], 'awaiting_input')
        self.assertEqual(len(self.messages()), 2)  # Old-thread replies cannot answer this new round.

    def test_repeated_collection_is_idempotent_and_delivers_an_edit_once(self):
        self.ask()
        reply = self.comment('answer')
        self.card['comments'].append(reply)
        collect_replies(self.ledger, self.api, ISSUE)
        collect_replies(self.ledger, self.api, ISSUE)
        self.assertEqual(len(self.messages()), 1)
        stale = copy.deepcopy(reply)
        reply.update(body='Correction: weekly', updated_at='2026-10-23T00:02:00Z')
        collect_replies(self.ledger, self.api, ISSUE)
        collect_replies(self.ledger, self.api, ISSUE)
        self.assertEqual(len(self.messages()), 2)
        self.card['comments'][-1] = stale
        collect_replies(self.ledger, self.api, ISSUE)
        collect_replies(self.open_ledger(), self.api, ISSUE)
        self.assertEqual(len(self.messages()), 2)

    def test_only_attributed_humans_in_the_question_thread_are_collected(self):
        self.ask()
        self.card['comments'] += [self.comment('unrelated', parent=None),
            self.comment('other-thread', parent='another-question'),
            self.comment('bot', kind='bot', author=None), self.comment('unknown', kind='unknown', author=None),
            self.comment('unattributed', author=None), self.comment('empty', '  ')]
        collect_replies(self.ledger, self.api, ISSUE)
        self.assertEqual(self.messages(), [])
        self.assertEqual(self.ledger.item(self.item)['state'], 'awaiting_input')
        self.card['comments'].append(self.comment('nested', parent='bot'))
        collect_replies(self.ledger, self.api, ISSUE)
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
            self.api.create_activity.side_effect = None
            self.trigger()
        self.api.create_activity.side_effect = reply_during_pointer
        self.assertEqual(self.ask()['state'], 'queued')
        self.assertEqual(len(self.messages()), 1)

    def test_no_fetch_before_confirmed_comment_or_after_resolution(self):
        collect_replies(self.ledger, self.api, ISSUE)
        prepare(self.ledger, self.item, self.token, 'Question?', 'TestBot')
        collect_replies(self.ledger, self.api, ISSUE)
        self.api.fetch_issue.assert_not_called()
        self.ask()
        self.card['comments'].append(self.comment('answer'))
        collect_replies(self.ledger, self.api, ISSUE)
        self.resume()
        self.ledger.resolve_question(self.item, self.token, self.ledger.human_question(self.item)['id'],
                                     [self.messages()[-1]['id']])
        self.api.fetch_issue.reset_mock()
        collect_replies(self.ledger, self.api, ISSUE)
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
                collect_replies(self.ledger, self.api, ISSUE)
                self.assertEqual(self.messages(), [])
                self.card = before
                self.card['updated_at'] = '2026-10-23T02:00:00Z'
                self.ledger.observe_issue(self.card)
        self.ledger.cancel(self.item, 'Stopped')
        collect_replies(self.ledger, self.api, ISSUE)
        self.assertEqual(self.ledger.item(self.item)['state'], 'cancelled')
        self.assertEqual(self.messages(), [])

    def test_concurrent_resolution_during_fetch_cannot_route_to_a_new_round(self):
        self.ask()
        self.card['comments'].append(self.comment('answer'))
        collect_replies(self.ledger, self.api, ISSUE)
        self.resume()
        question = self.ledger.human_question(self.item)
        def resolved(_):
            self.ledger.resolve_question(self.item, self.token, question['id'], [self.messages()[-1]['id']])
            return copy.deepcopy(self.card)
        self.api.fetch_issue.side_effect = resolved
        collect_replies(self.ledger, self.api, ISSUE)
        self.assertIsNone(self.ledger.human_question(self.item))
        self.assertEqual(len(self.messages()), 1)

    def test_lifecycle_does_not_collect_or_wake_on_ordinary_replies(self):
        from agent.lifecycle import Lifecycle
        self.ask()
        self.card['comments'].append(self.comment('answer'))
        self.api.fetch_issue.reset_mock()
        lifecycle = Lifecycle(self.ledger, self.api, Mock(), clock=lambda: self.now)
        self.assertIsNotNone(lifecycle.refresh(ISSUE))
        self.assertEqual(self.ledger.item(self.item)['state'], 'awaiting_input')
        self.api.fetch_issue.assert_not_called()
        self.assertEqual(self.messages(), [])
        self.assertIsNotNone(self.ledger.human_question(self.item))
        self.api.awaiting_reply.assert_called_with(ISSUE, True)

    def test_failed_trigger_read_preserves_question_without_resuming(self):
        self.ask()
        question = self.ledger.human_question(self.item)
        self.api.fetch_issue.side_effect = TimeoutError('offline')
        self.trigger()
        event = self.receiver.db.execute('SELECT status,error FROM webhook_events').fetchone()
        self.assertEqual((event['status'], event['error']), ('uncertain', 'TimeoutError'))
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
            collect_replies(self.ledger, self.api, ISSUE)
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
        collect_replies(self.ledger, self.api, ISSUE)
        self.assertEqual(self.messages(), [])
        self.assertEqual(self.ledger.item(self.item)['state'], 'cancelled')

    def test_final_mention_collects_everyones_answers_once_before_resuming(self):
        self.ask()
        self.card['comments'] += [self.comment('answer-1'),
                                  self.comment('answer-2', 'Q2: asset A', author=DESIGNER)]
        self.assertEqual(self.messages(), [])
        self.api.fetch_issue.reset_mock()
        self.trigger()
        self.assertEqual([m['author'] for m in self.messages()], [OWNER, DESIGNER])
        self.assertEqual(self.ledger.item(self.item)['state'], 'queued')
        self.assertIsNotNone(self.ledger.human_question(self.item))
        self.api.fetch_issue.assert_called_once_with(ISSUE)
        self.trigger()
        self.assertEqual(len(self.messages()), 2)

    def test_session_reply_can_trigger_the_same_collection(self):
        self.ask()
        self.card['comments'].append(self.comment('answer'))
        self.trigger(session_reply=True)
        self.assertEqual(len(self.messages()), 1)
        self.assertEqual(self.ledger.item(self.item)['state'], 'queued')

    def test_early_mention_preserves_round_and_later_answers_wait_for_next_mention(self):
        self.ask()
        question = self.ledger.human_question(self.item)
        self.card['comments'].append(self.comment('answer-1'))
        self.trigger()
        first = self.messages()[0]
        self.resume()
        self.ask('Only Q2 remains')
        self.card['comments'].append(self.comment('answer-2', 'Q2: asset A', author=DESIGNER))
        self.assertEqual(len(self.messages()), 1)
        self.assertEqual(self.ledger.item(self.item)['state'], 'awaiting_input')
        self.trigger()
        self.resume()
        self.ledger.resolve_question(self.item, self.token, question['id'], [first['id'], self.messages()[-1]['id']])
        self.assertIsNone(self.ledger.human_question(self.item))
        self.api.create_comment.assert_called_once()

    def test_mention_without_an_answer_does_not_resolve_the_question(self):
        self.ask()
        question = self.ledger.human_question(self.item)
        self.trigger()
        self.assertEqual(self.messages(), [])
        self.assertEqual(self.ledger.human_question(self.item)['id'], question['id'])

    def test_trigger_without_a_human_author_does_not_collect_answers(self):
        self.ask()
        self.card['comments'].append(self.comment('answer'))
        self.trigger(author=None)
        self.assertEqual(self.messages(), [])

    def test_lost_delegation_at_mention_does_not_import_or_resume(self):
        self.ask()
        self.card['comments'].append(self.comment('answer'))
        self.card['delegate_id'] = None
        self.card['updated_at'] = '2026-10-23T01:00:00Z'
        self.trigger()
        self.assertEqual(self.messages(), [])
        self.assertEqual(self.ledger.item(self.item)['state'], 'awaiting_input')
