import copy
from pathlib import Path
import tempfile
import unittest

from agent.evidence_index import create_index, verify_index


class EvidenceIndexTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / '报告 space'
        self.root.mkdir()
        (self.root / 'result.json').write_text('{"passed":7}', encoding='utf-8')
        self.item = {'id': 'one', 'claimed_fingerprint': 'scope', 'current_fingerprint': 'scope'}
        self.value = {'pins': {'candidate': 'a' * 40}, 'reports': [
            {'path': 'result.json', 'purpose': 'focused tests', 'summary': '7 passed'}]}

    def test_reuse_is_immutable_compact_and_not_authorization(self):
        before = (self.root / 'result.json').read_bytes()
        created = create_index(self.root, self.item, self.value)
        self.assertEqual(create_index(self.root, self.item, self.value), created)
        verified = verify_index(self.root, self.item, created['index'])
        self.assertTrue(verified['hashes_verified'])
        self.assertFalse(verified['evidence_is_authority'])
        self.assertFalse(verified['requires_issue_review'])
        self.assertEqual(verified['reports'][0]['path'], 'result.json')
        self.assertEqual((self.root / 'result.json').read_bytes(), before)
        changed = dict(self.item, current_fingerprint='new scope')
        self.assertTrue(verify_index(self.root, changed, created['index'])['requires_issue_review'])

    def test_missing_changed_report_and_index_tampering_fail_closed(self):
        created = create_index(self.root, self.item, self.value)
        report = self.root / 'result.json'
        report.write_text('changed', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'report changed'):
            verify_index(self.root, self.item, created['index'])
        report.unlink()
        with self.assertRaisesRegex(ValueError, 'existing regular file'):
            verify_index(self.root, self.item, created['index'])
        Path(created['index']).write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'identity changed'):
            verify_index(self.root, self.item, created['index'])

    def test_cross_item_and_outside_paths_are_rejected(self):
        created = create_index(self.root, self.item, self.value)
        with self.assertRaisesRegex(ValueError, 'another item'):
            verify_index(self.root, dict(self.item, id='two'), created['index'])
        outside = self.root.parent / 'outside.txt'
        outside.write_text('outside', encoding='utf-8')
        self.value['reports'][0]['path'] = '../outside.txt'
        with self.assertRaisesRegex(ValueError, 'inside this item'):
            create_index(self.root, self.item, self.value)

    def test_invalid_pins_duplicate_paths_and_unbounded_notes_are_rejected(self):
        for mutate in (lambda v: v['pins'].update(candidate='short'),
                       lambda v: v['reports'].append(v['reports'][0]),
                       lambda v: v['reports'][0].update(summary='x' * 501)):
            value = copy.deepcopy(self.value)
            mutate(value)
            with self.assertRaises(ValueError):
                create_index(self.root, self.item, value)

    def test_linked_report_is_rejected_even_when_target_stays_inside(self):
        link = self.root / 'link.json'
        try:
            link.symlink_to(self.root / 'result.json')
        except OSError as exc:
            self.skipTest(f'symlinks unavailable: {exc}')
        self.value['reports'][0]['path'] = 'link.json'
        with self.assertRaisesRegex(ValueError, 'links or junctions'):
            create_index(self.root, self.item, self.value)
