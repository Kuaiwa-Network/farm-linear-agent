"""Moved build caches are archived; source and live Editors are never disturbed."""
import json
from pathlib import Path
import tempfile
import unittest

from agent.unity_cache import archive_relocated_bee


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / 'slot 中文'
        self.bee = self.root / 'Library/Bee'; self.bee.mkdir(parents=True)
        (self.bee / 'cached.dag').write_bytes(b'old cache')
        (self.root / 'Assets').mkdir(); (self.root / 'Assets/source.cs').write_text('source')
        self.log = self.root / 'Logs/farmbot-editor.log'; self.log.parent.mkdir()
        self.log.write_text('Could not find a part of the path "C:\\old-slot\\Library\\Bee\\artifacts\\HotUpdate.dll.mvfrm".')

    def test_old_absolute_path_archives_only_bee_and_remembers_evidence(self):
        proof = archive_relocated_bee(self.root, editor_open=lambda: False)
        self.assertFalse(self.bee.exists())
        self.assertEqual((Path(proof['archive']) / 'cached.dag').read_bytes(), b'old cache')
        self.assertEqual((self.root / 'Assets/source.cs').read_text(), 'source')
        self.bee.mkdir(); (self.bee / 'new.dag').write_text('new')
        self.assertIsNone(archive_relocated_bee(self.root, editor_open=lambda: False))
        self.assertEqual((self.bee / 'new.dag').read_text(), 'new')

    def test_live_editor_and_editor_starting_during_maintenance_refuse_move(self):
        for probes in ([True], [False, True]):
            answers = iter(probes)
            with self.assertRaisesRegex(ValueError, 'Editor'):
                archive_relocated_bee(self.root, editor_open=lambda: next(answers))
            self.assertTrue((self.bee / 'cached.dag').exists())

    def test_marker_from_previous_location_also_triggers_archive(self):
        self.log.unlink()
        (self.root / 'Library/farmbot-bee-location.json').write_text(json.dumps({'project': 'old-root'}))
        proof = archive_relocated_bee(self.root, editor_open=lambda: False)
        self.assertEqual(proof['previous_project'], 'old-root')

    def test_missing_or_current_location_cache_needs_no_archive(self):
        self.log.write_text('normal compile')
        self.assertIsNone(archive_relocated_bee(self.root, editor_open=lambda: False))
        self.assertTrue(self.bee.exists())

    def test_linked_cache_is_never_followed(self):
        other = self.root / 'other'; self.bee.rename(other)
        try:
            self.bee.symlink_to(other, target_is_directory=True)
        except OSError:
            self.skipTest('symlink creation unavailable')
        with self.assertRaisesRegex(Exception, 'links'):
            archive_relocated_bee(self.root, editor_open=lambda: False)
        self.assertEqual((other / 'cached.dag').read_bytes(), b'old cache')
