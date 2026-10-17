import copy
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from agent.evidence_index import _file, create_index, verify_index


class EvidenceIndexTests(unittest.TestCase):
    def directory_alias(self, link, target):
        if os.name == 'nt':
            # Junctions need no symlink privilege and exercise the native check.
            subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(link), str(target)],
                           check=True, capture_output=True)
            self.addCleanup(link.rmdir)
            self.assertTrue(link.is_junction())
        else:
            link.symlink_to(target, target_is_directory=True)
            self.addCleanup(link.unlink)
            self.assertTrue(link.is_symlink())
        return link

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

    def test_ancestor_alias_accepts_absolute_reports_and_index_without_changing_identity(self):
        alias = self.directory_alias(self.root.parent / 'ancestor alias', self.root.parent.resolve())
        aliased_root = alias / self.root.name
        report = aliased_root / 'result.json'
        self.assertNotEqual(report, report.resolve())
        self.assertEqual(_file(self.root, str(report)), report.resolve())
        original = create_index(self.root, self.item, self.value)
        self.value['reports'][0]['path'] = str(report)
        self.assertEqual(create_index(aliased_root, self.item, self.value), original)
        verified = verify_index(self.root, self.item, str(aliased_root / Path(original['index']).name))
        self.assertTrue(verified['hashes_verified'])

    def test_alias_does_not_hide_links_at_or_below_item_boundary(self):
        alias = self.directory_alias(self.root.parent / 'ancestor alias', self.root.parent.resolve())
        nested = self.root / 'nested'
        nested.mkdir()
        (nested / 'result.json').write_text('nested', encoding='utf-8')
        self.directory_alias(self.root / 'back-in', nested.resolve())
        self.directory_alias(self.root / 'back-root', self.root.resolve())
        self.directory_alias(self.root.parent / 'root-link', self.root.resolve())
        for path in (self.root / 'back-in' / 'result.json',
                     alias / self.root.name / 'back-in' / 'result.json',
                     alias / self.root.name / 'back-root' / 'result.json',
                     self.root.parent / 'root-link' / 'result.json'):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'links or junctions'):
                _file(self.root, str(path))

    def test_alias_cannot_import_foreign_report_or_traverse_parent(self):
        alias = self.directory_alias(self.root.parent / 'ancestor alias', self.root.parent.resolve())
        foreign = self.root.parent / 'foreign'
        foreign.mkdir()
        (foreign / 'result.json').write_text('foreign', encoding='utf-8')
        self.directory_alias(self.root / 'outside', foreign.resolve())
        for path in (alias / 'foreign' / 'result.json',
                     alias / self.root.name / 'outside' / 'result.json',
                     alias / self.root.name / '..' / 'foreign' / 'result.json',
                     alias / self.root.name / 'outside' / '..' / 'result.json'):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'inside this item'):
                _file(self.root, str(path))

    @unittest.skipUnless(os.name == 'nt', 'Windows 8.3 path aliases')
    def test_windows_short_name_report_has_same_canonical_identity(self):
        import ctypes
        get_short = ctypes.WinDLL('kernel32', use_last_error=True).GetShortPathNameW
        get_short.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
        get_short.restype = ctypes.c_uint32
        target = self.root / 'result.json'
        buffer = ctypes.create_unicode_buffer(32768)
        size = get_short(str(target), buffer, len(buffer))
        self.assertTrue(0 < size < len(buffer), ctypes.get_last_error())
        short = Path(buffer.value)
        if short == target.resolve():
            self.skipTest('8.3 names unavailable on this volume')
        self.assertEqual(_file(self.root, str(short)), target.resolve())
