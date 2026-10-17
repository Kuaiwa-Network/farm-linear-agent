import copy
import unittest

from agent.closing import validate_deferred_archive, validate_closing_inputs
from agent.ledger import LedgerError
import test_general_delivery as delivery_fixtures


PR = 'https://github.com/owner/Farm-Contract/pull/1'


def deferred_plan():
    return {
        'stages': {key: 'done' if key in 'AF' else 'skipped: no changes' for key in 'ABCDEF'},
        'client': {'inputs': {'network': {'required': False, 'reason': 'docs only; fields already exist',
            'base_commit': 'a'*40, 'source_commit': 'b'*40, 'consumer_commit': 'c'*40,
            'evidence': ['input-inventory.json']}}},
        'closing': {'waivers_removed': True, 'client_resynced': True, 'hive_resynced': True,
            'pin_written': True, 'writeback_opened': False,
            'acceptance': {'status': 'recorded', 'contract_pr': PR, 'head': 'd'*40,
                           'evidence': ['acceptance.json', 'contract-gates.json']},
            'deferred_archive': {'status': 'pending_contract_merge', 'contract_pr': PR,
                                 'docs_only': True, 'reason': 'archive follows merge; acceptance already published'}}}


class DeferredArchiveTests(unittest.TestCase):
    def test_consumer_preflight_precedes_acceptance_publication(self):
        plan = deferred_plan()
        del plan['closing']['acceptance']
        del plan['closing']['deferred_archive']
        validate_closing_inputs(plan)
        del plan['client']['inputs']['network']['source_commit']
        with self.assertRaisesRegex(ValueError, 'client.inputs.network.source_commit'):
            validate_closing_inputs(plan)

    def test_complete_draft_retains_pending_archive(self):
        plan = deferred_plan()
        original = copy.deepcopy(plan)
        validate_deferred_archive(plan, [PR])
        self.assertEqual(plan, original)

    def test_required_steps_and_false_archive_claim_cannot_be_deferred(self):
        for step in ('waivers_removed', 'client_resynced', 'hive_resynced', 'pin_written', 'writeback_opened'):
            plan = deferred_plan()
            plan['closing'][step] = step == 'writeback_opened'
            with self.subTest(step=step), self.assertRaises(ValueError):
                validate_deferred_archive(plan, [PR])

    def test_unresolved_stage_pause_and_unmerged_consumer_input_block(self):
        for section, key, value in (
                ('stages', 'F', 'pending'), ('stages', 'E', 'skipped: '),
                ('stages', 'C', 'done'), ('stages', 'D', 'done'),
                ('closing', 'acceptance', None), ('closing', 'deferred_archive', {})):
            plan = deferred_plan()
            plan[section][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate_deferred_archive(plan, [PR])
        for key, value in (('required', True), ('source_commit', 'unknown'), ('evidence', [])):
            plan = deferred_plan()
            plan['client']['inputs']['network'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_deferred_archive(plan, [PR])
        plan = deferred_plan()
        plan['pause'] = {'kind': 'answers'}
        with self.assertRaisesRegex(ValueError, 'pause'):
            validate_deferred_archive(plan, [PR])

    def test_unpublished_or_mismatched_acceptance_cannot_finish(self):
        for field, value in (('status', 'pending'), ('head', 'short'), ('evidence', []), ('contract_pr', 'wrong')):
            plan = deferred_plan()
            plan['closing']['acceptance'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_deferred_archive(plan, [PR])
        with self.assertRaises(ValueError):
            validate_deferred_archive(deferred_plan(), [])


class DeferredArchiveDeliveryTests(unittest.TestCase):
    setUp = delivery_fixtures.GeneralDeliveryTests.setUp
    outcome = delivery_fixtures.GeneralDeliveryTests.outcome

    def test_check_ready_finds_missing_consumer_identity_before_comment(self):
        plan = deferred_plan()
        del plan['client']['inputs']['network']['base_commit']
        self.ledger.checkpoint(self.parent, self.token, {'plan': plan})
        outcome = {'summary': 'done', 'verification': 'retained reports', 'prs': [PR]}
        with self.assertRaisesRegex(LedgerError, 'client.inputs.network.base_commit'):
            self.ledger.finish(self.parent, self.token, 'delivered', outcome, check_only=True)
        plan['client']['inputs']['network']['base_commit'] = 'a' * 40
        self.ledger.checkpoint(self.parent, self.token, {'plan': plan})
        before = self.ledger.item(self.parent)
        result = self.ledger.finish(self.parent, self.token, 'delivered', outcome, check_only=True)
        self.assertTrue(result['local_completion_checked'])
        self.assertFalse(result['completion_checked'])
        self.assertEqual(self.ledger.item(self.parent), before)
        self.assertEqual(self.ledger.connection.execute('SELECT COUNT(*) FROM outbox').fetchone()[0], 0)
        with self.assertRaisesRegex(LedgerError, 'comment_action_id'):
            self.ledger.finish(self.parent, self.token, 'delivered', outcome)

    def test_check_ready_rejects_changed_or_withdrawn_work(self):
        self.ledger.checkpoint(self.parent, self.token, {'plan': deferred_plan()})
        outcome = {'summary': 'done', 'verification': 'retained reports', 'prs': [PR]}
        for field, value, message in [('requeue_requested', 1, 'issue changed'),
                                       ('withdraw_deadline', 9999999999, 'withdrawn')]:
            with self.subTest(field=field):
                self.ledger.connection.execute(f'UPDATE work_items SET {field}=? WHERE id=?', (value, self.parent))
                with self.assertRaisesRegex(LedgerError, message):
                    self.ledger.finish(self.parent, self.token, 'delivered', outcome, check_only=True)
                self.ledger.connection.execute(f'UPDATE work_items SET {field}=? WHERE id=?',
                                               (0 if field == 'requeue_requested' else None, self.parent))


    def test_finish_checks_deferred_record_and_preserves_unfinished_archive(self):
        plan = deferred_plan()
        plan['closing']['pin_written'] = False
        self.ledger.checkpoint(self.parent, self.token, {'plan': plan})
        outcome = self.outcome('delivery', verification='Issue tests and recorded acceptance passed; archive pending merge', prs=[PR])
        with self.assertRaisesRegex(LedgerError, 'pin_written'):
            self.ledger.finish(self.parent, self.token, 'delivered', outcome)
        plan['closing']['pin_written'] = True
        self.ledger.checkpoint(self.parent, self.token, {'plan': plan})
        result = self.ledger.finish(self.parent, self.token, 'delivered', outcome)
        self.assertEqual(result['state'], 'delivered')
        saved = result['checkpoint']['plan']['closing']
        self.assertFalse(saved['writeback_opened'])
        self.assertEqual(saved['deferred_archive']['status'], 'pending_contract_merge')
