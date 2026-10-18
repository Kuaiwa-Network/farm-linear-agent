"""A host upgrade requeues only a quiescent technical source wait, never approval."""
import json
import unittest

from agent.ledger import LedgerError
import test_ui_scope_recovery as fixtures


class UiSourceRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture = fixtures.UiScopeRecoveryTests()
        fixture.source_only = True
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        self.f = fixture.f

    def test_clean_legacy_wait_retains_original_review_export_and_candidate(self):
        f = self.f
        before = f.ledger.item(f.item)
        inbox = f.ledger.connection.execute('SELECT count(*) FROM inbox').fetchone()[0]
        result = self.fixture.resume()
        after = f.ledger.item(f.item)
        self.assertEqual(result['stage'], 'source-handoff-recheck')
        self.assertTrue(result['verification_required'])
        for key in ('generation', 'evidence', 'target', 'task_client_base', 'root_repo'):
            self.assertEqual(after[key], before[key], key)
        self.assertEqual(after['checkpoint']['plan'], before['checkpoint']['plan'])
        self.assertEqual(f.ledger.connection.execute('SELECT count(*) FROM inbox').fetchone()[0], inbox)
        self.assertIsNone(f.ledger.human_question(f.item))

    def test_human_wait_cannot_be_reclassified_as_a_source_upgrade(self):
        f = self.f
        cp = f.ledger.item(f.item)['checkpoint']; cp['pending_reason'] = 'question'
        f.ledger.connection.execute('UPDATE work_items SET checkpoint=? WHERE id=?', (json.dumps(cp), f.item))
        with self.assertRaisesRegex(LedgerError, 'not a human question'):
            self.fixture.resume()

    def test_existing_client_changes_are_not_discarded_for_source_handoff(self):
        f = self.f
        (f.client / 'changed.cs').write_text('// existing implementation\n', encoding='utf-8')
        f.source.commit(f.client)
        with self.assertRaisesRegex(LedgerError, 'unchanged Client entry'):
            self.fixture.resume()
        self.assertEqual(f.ledger.item(f.item)['state'], 'awaiting_input')
