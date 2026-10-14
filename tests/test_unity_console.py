"""A fresh compiler check must not inherit expected test errors or erase evidence."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent.slots import SlotError, UnityIdentity


class ConsoleClient:
    def __init__(self):
        self.history = ['BusinessStatsService: expected InvalidProtocolBufferException']
        self.new_errors = []
        self.calls = []
        self.state = {'tests': {'is_running': False},
                      'editor': {'play_mode': {'is_playing': False, 'is_changing': False}}}

    def read_resource(self, uri):
        return self.state

    def call_tool(self, name, args):
        self.calls.append((name, args))
        if name == 'read_console':
            if args['action'] == 'clear':
                self.history = []
            else:
                return self.history
        elif name == 'refresh_unity':
            self.history = list(self.new_errors)
        return {}


class UnityConsoleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / '恢复 证据'
        self.identity = UnityIdentity(Path(self.tmp.name) / 'probe.cs', console_evidence_root=self.root)
        self.slot = {'slot_id': 'unity_slot:1', 'instance': 'expected', 'folder': self.tmp.name}
        self.client = ConsoleClient()
        self.pinned = patch.object(self.identity, '_client', return_value=self.client)
        self.pinned.start()
        self.addCleanup(self.pinned.stop)

    def test_expected_test_exception_is_archived_before_a_clean_refresh(self):
        self.identity.refresh(self.slot)
        self.assertEqual(self.identity.console_errors_since(self.slot, 'commit'), [])
        [archive] = self.root.glob('*.json')
        evidence = json.loads(archive.read_text(encoding='utf-8'))
        self.assertIn('InvalidProtocolBufferException', evidence['console'][0])
        self.assertEqual(evidence['slot_id'], self.slot['slot_id'])
        self.assertEqual(self.client.calls[1], ('read_console', {'action': 'clear'}))
        self.assertEqual(self.client.calls[2], ('refresh_unity', {'compile': 'request', 'wait_for_ready': True}))

    def test_same_candidate_is_recompiled_and_new_compiler_errors_still_block(self):
        self.client.history = ['Assets/A.cs: error CS1002: stale compiler failure']
        self.client.new_errors = ['Assets/A.cs: error CS1002: current compiler failure']
        self.identity.refresh(self.slot)
        self.assertEqual(self.identity.console_errors_since(self.slot, 'same-commit'), self.client.new_errors)

    def test_failed_archive_preserves_console_and_does_not_refresh(self):
        with patch.object(Path, 'write_text', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):
                self.identity.refresh(self.slot)
        self.assertEqual(len(self.client.calls), 1)
        self.assertIn('InvalidProtocolBufferException', self.client.history[0])

    def test_unknown_console_shape_is_never_cleared(self):
        self.client.history = {'unexpected': []}
        with self.assertRaises(SlotError):
            self.identity.refresh(self.slot)
        self.assertEqual(len(self.client.calls), 1)

    def test_live_or_unknown_test_state_never_clears_console(self):
        for tests in ({'is_running': True}, {}):
            with self.subTest(tests=tests):
                self.client.state['tests'] = tests
                with self.assertRaises(SlotError):
                    self.identity.refresh(self.slot)
                self.assertEqual(self.client.calls, [])

    def test_play_mode_is_never_cleared(self):
        self.client.state['editor']['play_mode']['is_playing'] = True
        with self.assertRaises(SlotError):
            self.identity.refresh(self.slot)
        self.assertEqual(self.client.calls, [])
