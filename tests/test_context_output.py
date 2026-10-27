"""Conditional context reads retain evidence and never replace authority checks."""
import copy
from contextlib import closing
import json
from pathlib import Path
import unittest

import test_cli as fixtures
from test_ledger import OWNER
from agent.dispatch import dispatch_message
from agent.execution import CONTRACT, LEGACY
from agent.ledger import Ledger
from agent.resume_context import resume_view
from agent.skills import load_skills


class ConditionalResumeTests(unittest.TestCase):
    def context(self):
        return {
            'issue': {'description': 'requirements ' * 3000,
                      'project': {'content': 'Project Overview'},
                      'comments': [{'author_kind': 'human', 'body': 'current decision'},
                                   {'author_kind': 'unknown', 'body': 'unattributed evidence'},
                                   {'author_kind': 'bot', 'body': 'old report ' * 3000}]},
            'fingerprint': 'same-input-fingerprint',
            'coordination': {'id': 'item', 'state': 'running', 'generation': 3,
                             'authority': 'delegation', 'withdrawn': False},
            'current_request': {'latest_message_id': 4, 'review_required': True},
            'session_messages': [{'id': 4, 'body': 'latest request', 'author': {'id': 'person'}}],
            'human_question': {'id': 'unresolved'}, 'inbox_pending': 1,
            'handoff': {'stale': False, 'content': 'retained handoff'},
            'conversation_history': [{'handoff': 'old evidence ' * 3000}],
            'tasks': [{'state': 'delivered', 'checkpoint': {'summary': 'historical'}}],
            'resource_recovery': {'records': []},
        }

    def test_matching_digest_returns_small_explicit_receipt_without_mutation(self):
        context = self.context()
        original = copy.deepcopy(context)
        previous = resume_view(context)
        receipt = resume_view(context, if_changed=previous['context_view']['digest'])
        self.assertTrue(receipt['context_view']['unchanged'])
        self.assertEqual(receipt['context_view']['digest'], previous['context_view']['digest'])
        self.assertEqual(receipt['coordination'], context['coordination'])
        self.assertEqual(receipt['inbox_pending'], 1)
        self.assertNotIn('issue', receipt)
        self.assertNotIn('session_messages', receipt)
        self.assertLess(len(json.dumps(receipt)), len(json.dumps(previous)) / 10)
        self.assertEqual(context, original)
        self.assertEqual(resume_view(context), previous)
        receipt['coordination']['state'] = 'changed by consumer'
        self.assertEqual(context, original)

    def test_all_context_changes_invalidate_even_when_input_fingerprint_is_unchanged(self):
        context = self.context()
        digest = resume_view(context)['context_view']['digest']
        changes = (
            (('issue', 'project', 'content'), 'new Overview'),
            (('issue', 'comments', 0, 'body'), 'new decision'),
            (('issue', 'comments', 1, 'body'), 'new unknown-author evidence'),
            (('issue', 'comments', 2, 'body'), 'new omitted bot text'),
            (('current_request', 'latest_message_id'), 5),
            (('current_request', 'review_required'), False),
            (('session_messages', 0, 'body'), 'changed request'),
            (('session_messages', 0, 'author', 'id'), 'other person'),
            (('human_question', 'id'), 'another unresolved question'),
            (('coordination', 'state'), 'cancelled'),
            (('coordination', 'id'), 'other-item'),
            (('coordination', 'withdrawn'), True),
            (('coordination', 'authority'), 'mention'),
            (('coordination', 'generation'), 4),
            (('inbox_pending',), 0),
            (('handoff', 'stale'), True),
            (('conversation_history', 0, 'handoff'), 'changed omitted history'),
            (('tasks', 0, 'checkpoint', 'summary'), 'changed omitted checkpoint'),
            (('resource_recovery', 'records'), [{'state': 'held', 'evidence': 'retain'}]),
            (('future_context_field',), {'new': 'evidence'}),
        )
        for path, value in changes:
            with self.subTest(path=path):
                changed = copy.deepcopy(context)
                target = changed
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                result = resume_view(changed, if_changed=digest)
                self.assertFalse(result['context_view']['unchanged'])
                self.assertNotEqual(result['context_view']['digest'], digest)
                self.assertEqual(result['fingerprint'], context['fingerprint'])
                self.assertEqual(result, resume_view(changed))

    def test_dictionary_order_is_irrelevant_and_lost_context_can_be_reread(self):
        context = self.context()
        previous = resume_view(context)
        reordered = dict(reversed(list(context.items())))
        reordered['coordination'] = dict(reversed(list(context['coordination'].items())))
        self.assertTrue(resume_view(reordered, if_changed=previous['context_view']['digest'])
                        ['context_view']['unchanged'])
        self.assertEqual(resume_view(context, if_changed='0' * 64), previous)
        self.assertFalse(resume_view(context)['context_view']['unchanged'])

    def test_stale_handoff_remains_visible_in_matching_receipt(self):
        context = self.context()
        context['handoff']['stale'] = True
        previous = resume_view(context)
        receipt = resume_view(context, if_changed=previous['context_view']['digest'])
        self.assertTrue(receipt['context_view']['requires_full_view'])

    def test_malformed_digest_is_rejected(self):
        for value in ('', 'fingerprint', 'a' * 63, 'g' * 64):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'SHA-256'):
                resume_view(self.context(), if_changed=value)


class ConditionalContextCliTests(unittest.TestCase):
    setUp = fixtures.CliTests.setUp
    run_cli = fixtures.CliTests.run_cli
    seeded_item = fixtures.CliTests.seeded_item
    calls = fixtures.CliTests.calls

    def test_claim_digest_is_reusable_without_live_calls_renewal_or_consuming_requests(self):
        self.db = self.root / 'context with spaces \u4e2d\u6587.sqlite3'
        item = self.seeded_item(skill='chat')
        with closing(Ledger(self.db)) as ledger:
            ledger.push_inbox(item, 'read this request', author=OWNER)
        claimed = self.run_cli('claim', '--item', item, '--worker-id', 'worker', '--context')
        digest = claimed['context']['context_view']['digest']
        calls = self.calls()
        with closing(Ledger(self.db)) as ledger:
            before = ledger.item(item)
        result = self.run_cli('issue-context', '--item', item, '--view', 'resume', '--if-changed', digest)
        self.assertTrue(result['context_view']['unchanged'])
        self.assertEqual(result['inbox_pending'], 1)
        self.assertEqual(self.calls(), calls)
        self.assertNotIn(claimed['token'], json.dumps(result))
        with closing(Ledger(self.db)) as ledger:
            self.assertEqual(ledger.item(item), before)
            self.assertEqual(ledger.issue_context(item)['inbox_pending'], 1)

    def test_new_request_and_stop_invalidate_cached_context(self):
        item = self.seeded_item(skill='chat')
        original = self.run_cli('issue-context', '--item', item, '--view', 'resume')
        with closing(Ledger(self.db)) as ledger:
            ledger.push_inbox(item, 'new attributed request', author=OWNER)
        changed = self.run_cli('issue-context', '--item', item, '--view', 'resume',
                               '--if-changed', original['context_view']['digest'])
        self.assertFalse(changed['context_view']['unchanged'])
        self.assertEqual(changed['fingerprint'], original['fingerprint'])
        self.assertTrue(changed['current_request']['review_required'])
        self.assertEqual(changed['session_messages'][-1]['body'], 'new attributed request')
        with closing(Ledger(self.db)) as ledger:
            ledger.cancel(item, 'operator stop')
        stopped = self.run_cli('issue-context', '--item', item, '--view', 'resume',
                               '--if-changed', changed['context_view']['digest'])
        self.assertFalse(stopped['context_view']['unchanged'])
        self.assertEqual(stopped['coordination']['state'], 'cancelled')

    def test_full_view_is_unchanged_and_invalid_options_fail_without_output(self):
        item = self.seeded_item(skill='chat')
        full = self.run_cli('issue-context', '--item', item)
        with closing(Ledger(self.db)) as ledger:
            self.assertEqual(full, ledger.issue_context(item))
        self.assertNotIn('context_view', full)
        self.assertEqual(full, self.run_cli('issue-context', '--item', item, '--view', 'full'))
        for flags in (('--if-changed', '0' * 64),
                      ('--view', 'resume', '--if-changed', 'not-a-digest')):
            with self.subTest(flags=flags):
                failed = self.run_cli('issue-context', '--item', item, *flags, success=False)
                self.assertFalse(failed.stdout.strip())
        with closing(Ledger(self.db)) as ledger:
            self.assertEqual(ledger.issue_context(item), full)


class ContextOutputPromptTests(unittest.TestCase):
    def test_current_chat_and_work_receive_guidance_without_losing_startup_or_inputs(self):
        root = Path(__file__).resolve().parents[1]
        skills = load_skills(root / 'skills')
        for profile in ('chat', 'work'):
            with self.subTest(profile=profile):
                prompt = dispatch_message(
                    item={'id': 'context-output', 'skill': profile,
                          'execution_contract': CONTRACT if profile == 'work' else LEGACY},
                    issue={'identifier': 'FARM-1', 'url': 'https://linear.app/issue/FARM-1'},
                    skill_path=skills[profile].skill_md, worktrees={}, runtime='codex',
                    db_path=root / 'private state' / 'ledger.sqlite3',
                    state_dir=root / 'private state', repo_root=root,
                    guidance='Keep all required evidence', budget=skills[profile].budget,
                    user_requests=[{'id': 7, 'author': {'id': 'human'}, 'body': 'Full current request'}])
                authority, payload = prompt.split('\n\n', 1)
                self.assertTrue(prompt.startswith('STARTUP FIRST:'))
                self.assertEqual(authority.count('Context/output discipline:'), 1)
                self.assertIn('add --if-changed DIGEST only while you retain the context', authority)
                self.assertIn('Keep required fetch-issue, renewal, inbox, revalidation and publication checks', authority)
                self.assertIn('Read issue-context --view full when requires_full_view is true', authority)
                self.assertIn('not permission to truncate required instructions', authority)
                self.assertIn('Never rerun work merely to recover output', authority)
                data = json.loads(payload)
                self.assertEqual(data['guidance'], 'Keep all required evidence')
                self.assertEqual(data['user_requests'][0]['body'], 'Full current request')

    def test_active_skills_cli_and_contract_link_shared_guide(self):
        root = Path(__file__).resolve().parents[1]
        for document, link in (
            ('skills/work/SKILL.md', '../../references/worker-context-output.md'),
            ('skills/chat/SKILL.md', '../../references/worker-context-output.md'),
            ('references/single-worker-cli.md', 'worker-context-output.md'),
            ('references/chat-cli.md', 'worker-context-output.md'),
            ('docs/operating-contract.md', '../references/worker-context-output.md'),
        ):
            with self.subTest(document=document):
                path = root / document
                self.assertIn(f']({link})', path.read_text(encoding='utf-8'))
                self.assertEqual((path.parent / link).resolve(), root / 'references/worker-context-output.md')
                self.assertTrue((path.parent / link).is_file())


if __name__ == '__main__':
    unittest.main()
