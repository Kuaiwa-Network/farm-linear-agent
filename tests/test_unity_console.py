"""A fresh compiler check must not inherit expected test errors or erase evidence."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent.slots import SlotError, UnityIdentity, UnityRefreshPending
from agent.unity_mcp import UnityMcp, UnityMcpError


class ConsoleClient:
    def __init__(self):
        self.history = ['BusinessStatsService: expected InvalidProtocolBufferException']
        self.new_errors = []
        self.calls = []
        self.state = {'tests': {'is_running': False},
                      'compilation': {'is_compiling': False, 'is_domain_reload_pending': False},
                      'assets': {'is_updating': False},
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
        self.identity = UnityIdentity(Path(self.tmp.name) / 'probe.cs', console_evidence_root=self.root,
                                      clock=lambda: 100)
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
        self.assertEqual(self.client.calls[2], ('refresh_unity', {'compile': 'request', 'wait_for_ready': False}))

    def test_async_refresh_cannot_certify_old_idle_state_before_compilation_starts(self):
        self.client.state['compilation']['last_compile_finished_unix_ms'] = 99999
        self.identity.refresh(self.slot)
        with self.assertRaisesRegex(TimeoutError, 'no completed compilation'):
            self.identity.wait_quiet(self.slot, 10)
        self.client.state['compilation']['is_compiling'] = True
        with self.assertRaises(TimeoutError):
            self.identity.wait_quiet(self.slot, 10)
        self.client.state['compilation'].update(is_compiling=False, last_domain_reload_after_unix_ms=100000)
        self.identity.wait_quiet(self.slot, 10)
        self.assertEqual(sum(name == 'refresh_unity' for name, _ in self.client.calls), 1)

    def test_lost_refresh_reply_is_reconciled_without_replaying_console_clear_or_compile(self):
        original = self.client.call_tool
        def lost(name, arguments):
            result = original(name, arguments)
            if name == 'refresh_unity':
                raise ConnectionResetError('reload closed the connection')
            return result
        self.client.call_tool = lost
        with self.assertRaises(UnityRefreshPending):
            self.identity.refresh(self.slot)
        with self.assertRaisesRegex(TimeoutError, 'reload closed'):
            self.identity.wait_quiet(self.slot, 10)
        self.client.state['compilation']['last_compile_finished_unix_ms'] = 100000
        self.identity.wait_quiet(self.slot, 10)
        self.assertEqual([name for name, _ in self.client.calls], ['read_console', 'read_console', 'refresh_unity'])

    def test_rejected_refresh_without_new_compile_evidence_never_becomes_ready(self):
        original = self.client.call_tool
        def rejected(name, arguments):
            if name == 'refresh_unity':
                raise UnityMcpError(name, {'message': 'tests_running'})
            return original(name, arguments)
        self.client.call_tool = rejected
        with self.assertRaises(UnityRefreshPending):
            self.identity.refresh(self.slot)
        for value in (None, 99999, float('nan'), float('inf'), 100001, True):
            self.client.state['compilation']['last_compile_finished_unix_ms'] = value
            with self.assertRaisesRegex(TimeoutError, 'tests_running'):
                self.identity.wait_quiet(self.slot, 10)

    def test_mcp_failure_identifies_operation_and_machine_code_without_echoing_arbitrary_data(self):
        for message, expected in [('refresh_timeout_waiting_for_ready', 'refresh_timeout_waiting_for_ready'),
                                  ('private credential: do not print', 'unspecified')]:
            result = {'content': [{'text': json.dumps({'success': False, 'message': message})}]}
            with self.assertRaises(UnityMcpError) as caught:
                UnityMcp._payload(result, 'content', 'refresh_unity')
            self.assertIn(f'refresh_unity: {expected}', str(caught.exception))
            self.assertNotIn('private credential', str(caught.exception))

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
