import copy
from contextlib import closing
import json
from pathlib import Path
import unittest

import test_cli as fixtures
from agent.ledger import Ledger
from agent.resume_context import resume_view


class ResumeViewTests(unittest.TestCase):
    def test_navigation_retains_decisions_and_does_not_mutate_full_evidence(self):
        comments = [{'id': kind, 'author_kind': kind, 'body': kind * 1000} for kind in ('human', 'unknown', 'bot')]
        context = {'issue': {'comments': comments, 'description': 'accepted scope'}, 'fingerprint': 'original',
                   'handoff': {'stale': False, 'content': 'current handoff'},
                   'conversation_history': [{'item_id': 'old', 'handoff': 'old' * 10000,
                                             'messages': [{'body': 'keep the human reply'}]}],
                   'session_messages': [{'body': 'latest request'}], 'human_question': {'id': 'open'},
                   'tasks': [{'id': 'done', 'state': 'delivered', 'checkpoint': {'huge': 'x' * 10000},
                              'evidence': {'prs': ['draft'], 'verification': 'retained checks'}},
                             {'id': 'waiting', 'state': 'awaiting_input', 'checkpoint': {'pending_question': 'asset?'}}],
                   'plan': {'candidate': 'same'}, 'coordination': {'generation': 3, 'authority': 'delegation'}}
        original = copy.deepcopy(context)
        view = resume_view(context)
        self.assertEqual(context, original)
        self.assertEqual(view['issue']['comments'][:2], comments[:2])
        self.assertEqual(view['issue']['comments'][2]['id'], 'bot')
        self.assertNotIn('body', view['issue']['comments'][2])
        for key in ('fingerprint', 'handoff', 'plan', 'coordination', 'session_messages', 'human_question'):
            self.assertEqual(view[key], context[key])
        self.assertEqual(view['conversation_history'][0]['messages'], context['conversation_history'][0]['messages'])
        self.assertEqual(view['tasks'][0]['evidence'], context['tasks'][0]['evidence'])
        self.assertEqual(view['tasks'][1], context['tasks'][1])
        self.assertEqual(len(view['context_view']['omitted_paths']), 3)
        self.assertLess(len(json.dumps(view)), len(json.dumps(context)) / 2)

    def test_stale_handoff_requires_complete_read_and_missing_author_is_not_bot(self):
        view = resume_view({'handoff': {'stale': True}, 'issue': {'comments': [{'body': 'unknown decision'}]}})
        self.assertTrue(view['context_view']['requires_full_view'])
        self.assertEqual(view['issue']['comments'][0]['body'], 'unknown decision')


class CloseoutCliTests(unittest.TestCase):
    setUp = fixtures.CliTests.setUp
    run_cli = fixtures.CliTests.run_cli
    seeded_item = fixtures.CliTests.seeded_item
    json_file = fixtures.CliTests.json_file
    calls = fixtures.CliTests.calls

    def prepare(self):
        item = self.seeded_item()
        token = self.run_cli('claim', '--item', item, '--worker-id', 'w')['token']
        path = self.json_file('outcome 校验.json', {'summary': 'finished', 'verification': 'exact retained reports',
                                                'prs': ['https://github.com/Kuaiwa-Network/Farm-Client/pull/1']})
        return item, token, path

    def test_preflight_is_local_claim_fenced_and_does_not_certify_completion(self):
        item, token, path = self.prepare()
        before_calls = self.calls()
        with closing(Ledger(self.db)) as ledger:
            before = ledger.item(item)
        result = self.run_cli('finish', '--item', item, '--token', token, '--outcome', 'delivered',
                              '--input', path, '--check-input')
        self.assertTrue(result['input_valid'])
        self.assertFalse(result['completion_checked'])
        self.assertEqual(self.calls(), before_calls)
        with closing(Ledger(self.db)) as ledger:
            after = ledger.item(item)
            for key in ('state', 'checkpoint', 'evidence', 'generation'):
                self.assertEqual(after[key], before[key])
            self.assertEqual(ledger.connection.execute('SELECT COUNT(*) FROM outbox').fetchone()[0], 0)
        refused = self.run_cli('finish', '--item', item, '--token', token, '--outcome', 'delivered',
                               '--input', path, success=False)
        self.assertIn('comment_action_id', refused.stderr)
        refused = self.run_cli('finish', '--item', item, '--token', 'wrong', '--outcome', 'delivered',
                               '--input', path, '--check-input', success=False)
        self.assertFalse(refused.stdout.strip())

    def test_bad_verification_shape_is_rejected_before_any_delivery_or_network(self):
        item, token, path = self.prepare()
        path = self.json_file('outcome.json', {'summary': 'done', 'verification': {'tests': 'passed'},
                                              'prs': ['https://github.com/Kuaiwa-Network/Farm-Client/pull/1']})
        calls = self.calls()
        for flags in ([], ['--check-input']):
            error = self.run_cli('finish', '--item', item, '--token', token, '--outcome', 'delivered',
                                 '--input', path, *flags, success=False)
            self.assertIn('verification must be a nonempty string', error.stderr)
        self.assertEqual(self.calls(), calls)

    def test_check_ready_reuses_local_gates_without_network_or_comment(self):
        item, token, path = self.prepare()
        calls = self.calls()
        result = self.run_cli('finish', '--item', item, '--token', token, '--outcome', 'delivered',
                              '--input', path, '--check-ready')
        self.assertTrue(result['local_completion_checked'])
        self.assertFalse(result['completion_checked'])
        self.assertEqual(self.calls(), calls)
        self.run_cli('finish', '--item', item, '--token', 'wrong', '--outcome', 'delivered',
                     '--input', path, '--check-ready', success=False)

    def test_consumer_preflight_and_index_use_current_claim_and_configured_item_root(self):
        from test_closing import deferred_plan
        self.db = self.root / 'host' / 'agent' / 'ledger.sqlite3'
        config = self.json_file('config.json', {'client_id': 'c', 'client_secret': 's', 'webhook_secret': 'w',
                                               'runtime': 'fake', 'local_root': str(self.root / 'host')})
        self.env['FARMBOT_CONFIG'] = str(config)
        item, token, _ = self.prepare()
        with closing(Ledger(self.db)) as ledger:
            ledger.checkpoint(item, token, {'plan': deferred_plan()})
        calls = self.calls()
        self.assertTrue(self.run_cli('closing-preflight', '--item', item, '--token', token)['consumer_inputs_checked'])
        state = self.root / 'host' / 'runs' / item
        state.mkdir(parents=True, exist_ok=True)
        (state / 'report.json').write_text('{"passed":7}', encoding='utf-8')
        path = self.json_file('index-input.json', {'pins': {'candidate': 'c' * 40}, 'reports': [
            {'path': 'report.json', 'purpose': 'candidate', 'summary': '7 passed'}]})
        result = self.run_cli('evidence-index', '--item', item, '--token', token, '--input', path)
        self.assertEqual(Path(result['index']).parent, state.resolve())
        verified = self.run_cli('evidence-index', '--item', item, '--token', token, '--verify', result['index'])
        self.assertTrue(verified['hashes_verified'])
        self.assertFalse(verified['requires_issue_review'])
        self.assertEqual(self.calls(), calls)
        with closing(Ledger(self.db)) as ledger:
            ledger.connection.execute('UPDATE work_items SET requeue_requested=1 WHERE id=?', (item,))
        self.run_cli('closing-preflight', '--item', item, '--token', token, success=False)
        self.run_cli('evidence-index', '--item', item, '--token', token, '--input', path, success=False)


    def test_resume_cli_is_opt_in_and_leaves_the_complete_view_available(self):
        item, token, _ = self.prepare()
        full = self.run_cli('issue-context', '--item', item)
        self.assertNotIn('context_view', full)
        view = self.run_cli('issue-context', '--item', item, '--view', 'resume')
        self.assertEqual(view['fingerprint'], full['fingerprint'])
        self.assertEqual(view['human_question'], full['human_question'])
        self.assertEqual(self.run_cli('issue-context', '--item', item, '--view', 'full'), full)
        self.assertNotIn(token, json.dumps(view))

    def test_preflight_rejects_invalid_prs_and_no_change_conflicts(self):
        item, token, _ = self.prepare()
        for changes in ({'prs': ['https://github.com/owner/repo/issues/1']},
                        {'prs': 'https://github.com/owner/repo/pull/1'}, {'prs': []},
                        {'no_change': 'already fixed'}, {'summary': ''}):
            with self.subTest(changes=changes):
                evidence = {'summary': 'done', 'verification': 'checks',
                            'prs': ['https://github.com/owner/repo/pull/1'], **changes}
                path = self.json_file('bad.json', evidence)
                self.run_cli('finish', '--item', item, '--token', token, '--outcome', 'delivered',
                             '--input', path, '--check-input', success=False)

    def test_preflight_supports_no_change_and_rejects_retired_claim(self):
        item, token, _ = self.prepare()
        path = self.json_file('no-change.json', {'summary': 'already fixed', 'verification': 'checked source',
                                               'no_change': 'existing behavior meets acceptance'})
        self.assertTrue(self.run_cli('finish', '--item', item, '--token', token, '--outcome', 'delivered',
                                    '--input', path, '--check-input')['input_valid'])
        with closing(Ledger(self.db)) as ledger:
            ledger.cancel(item, 'operator stop')
        self.run_cli('finish', '--item', item, '--token', token, '--outcome', 'delivered',
                     '--input', path, '--check-input', success=False)
