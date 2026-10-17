import copy
from contextlib import closing
import json
from pathlib import Path
import unittest
import os
from unittest.mock import patch

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

    def test_saved_claim_is_secret_free_exclusive_and_immediately_usable(self):
        self.db = self.root / 'host' / 'agent' / 'ledger.sqlite3'
        self.env['FARMBOT_CONFIG'] = str(self.json_file('config.json', {
            'client_id': 'c', 'client_secret': 's', 'webhook_secret': 'w', 'runtime': 'fake',
            'local_root': str(self.root / 'host')}))
        item = self.seeded_item()
        state = self.root / 'host' / 'runs' / item
        state.mkdir(parents=True)
        path = state / 'token'
        args = ('claim', '--item', item, '--worker-id', 'saved', '--context', '--save-token', str(path))
        bad = self.run_cli(*args[:-1], str(self.root / 'token'), success=False)
        self.assertIn('state_dir/token', bad.stderr)
        with closing(Ledger(self.db)) as ledger:
            self.assertEqual(ledger.item(item)['state'], 'queued')
        result = self.run_cli(*args)
        token = path.read_text(encoding='utf-8')
        self.assertNotIn('token', result)
        self.assertNotIn(token, json.dumps(result))
        self.assertEqual(result['token_file'], str(path))
        self.assertEqual(result['context']['context_view']['name'], 'resume')
        if os.name != 'nt':
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.run_cli('renew', '--item', item, '--token-file', str(path))
        self.run_cli(*args, success=False)
        self.assertEqual(path.read_text(encoding='utf-8'), token)

    def test_saved_claim_refuses_links_and_storage_failure_before_claim(self):
        from agent.worker_start import claim_to_file
        from agent.config import Config
        from agent.ledger import LedgerError
        item = self.seeded_item()
        config = Config('c', 's', 'w', local_root=self.root / 'host')
        state = self.root / 'host' / 'runs' / item
        state.mkdir(parents=True)
        with closing(Ledger(self.db)) as ledger:
            with patch('agent.worker_start.os.open', side_effect=PermissionError):
                with self.assertRaises(LedgerError):
                    claim_to_file(ledger, item, 'w', state / 'token', config)
            self.assertEqual(ledger.item(item)['state'], 'queued')
            (state / 'token').mkdir()
            with self.assertRaises(LedgerError):
                claim_to_file(ledger, item, 'w', state / 'token', config)
            self.assertEqual(ledger.item(item)['state'], 'queued')

    def test_startup_argv_uses_configured_interpreter_paths_and_one_claim(self):
        from agent.dispatch import startup_navigation
        import sys
        current = self.root / '中文 Client'
        current.mkdir()
        (current / 'CLAUDE.md').write_text('required repository rules', encoding='utf-8')
        result = startup_navigation({'id': 'item', 'skill': 'work', 'stage': 'unity-verifying'},
            self.root, self.root / 'skill.md', {'Farm-Client': current}, 'Farm-Client',
            self.root / 'database with spaces.sqlite3', self.root / 'state with spaces')
        argv = result['claim_argv']
        self.assertEqual(argv[0], sys.executable)
        self.assertEqual(argv[argv.index('--save-token') + 1], str(self.root / 'state with spaces' / 'token'))
        self.assertIn(str(current / 'CLAUDE.md'), result['read_together'])
        self.assertEqual(argv.count('claim'), 1)

    def test_uncertain_token_write_preserves_file_and_never_reclaims(self):
        from agent.worker_start import claim_to_file
        from agent.config import Config
        from agent.ledger import LedgerError
        item = self.seeded_item()
        config = Config('c', 's', 'w', local_root=self.root / 'host')
        state = self.root / 'host' / 'runs' / item
        state.mkdir(parents=True)
        with closing(Ledger(self.db)) as ledger:
            with patch('agent.worker_start.os.fsync', side_effect=OSError):
                with self.assertRaises(LedgerError):
                    claim_to_file(ledger, item, 'w', state / 'token', config)
            secret = (state / 'token').read_text(encoding='utf-8')
            self.assertTrue(secret)
            self.assertEqual(ledger.item(item)['state'], 'running')
            with self.assertRaises(LedgerError):
                claim_to_file(ledger, item, 'w', state / 'token', config)
            self.assertEqual((state / 'token').read_text(encoding='utf-8'), secret)

    def test_combined_resource_handoff_saves_checkpoint_but_keeps_claim_on_refusal(self):
        item = self.seeded_item()
        token = self.run_cli('claim', '--item', item, '--worker-id', 'w')['token']
        checkpoint = self.json_file('handoff.json', {'stage': 'prepared', 'summary': 'original reports retained'})
        args = ('await-resource', '--item', item, '--token', token, '--resource', 'unity_slot',
                '--mode', 'interactive', '--checkpoint', str(checkpoint))
        # fix's legacy candidate validation needs configured Git. The checkpoint
        # persists, but no resource is acquired and the current claim remains valid.
        self.run_cli(*args, success=False)
        with closing(Ledger(self.db)) as ledger:
            self.assertEqual(ledger.item(item)['stage'], 'prepared')
            self.assertEqual(ledger.item(item)['state'], 'running')
            self.assertFalse(ledger.reservations())
        self.run_cli('renew', '--item', item, '--token', token)
        self.run_cli(*args[:4], 'wrong', *args[5:], success=False)

    def test_claim_can_return_resume_context_without_a_second_call_or_live_api(self):
        item = self.seeded_item()
        calls = self.calls()
        result = self.run_cli('claim', '--item', item, '--worker-id', 'resume-worker', '--context')
        self.assertTrue(result['token'])
        self.assertEqual(result['context']['context_view']['name'], 'resume')
        self.assertNotIn(result['token'], json.dumps(result['context']))
        self.assertEqual(result['context'], self.run_cli('issue-context', '--item', item, '--view', 'resume'))
        self.assertEqual(self.calls(), calls)
        refused = self.run_cli('claim', '--item', item, '--worker-id', 'another', '--context', success=False)
        self.assertFalse(refused.stdout.strip())
        self.assertIn('only a queued', refused.stderr)

    def test_claim_context_does_not_hide_maintenance_or_restore_cancelled_work(self):
        from agent.maintenance import Maintenance
        item = self.seeded_item()
        with closing(Ledger(self.db)) as ledger:
            Maintenance(ledger).pause('operator maintenance')
        result = self.run_cli('claim', '--item', item, '--worker-id', 'worker', '--context')
        self.assertTrue(result['maintenance_requested'])
        with closing(Ledger(self.db)) as ledger:
            ledger.cancel(item, 'operator stop')
        refused = self.run_cli('claim', '--item', item, '--worker-id', 'worker', '--context', success=False)
        self.assertFalse(refused.stdout.strip())

    def test_plain_claim_retains_existing_response(self):
        item = self.seeded_item()
        result = self.run_cli('claim', '--item', item, '--worker-id', 'legacy')
        self.assertIn('token', result)
        self.assertNotIn('context', result)

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
