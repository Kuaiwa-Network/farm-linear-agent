import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from agent.startup_diagnostics import first_execution


class TimingTests(unittest.TestCase):
    def event(self, second, kind, **extra):
        return json.dumps({'timestamp':f'2026-10-23T11:{second//60:02d}:{second%60:02d}Z',
                          'type':'response_item', 'payload':{'type':kind, **extra}})

    def test_yield_and_unrelated_wait_cannot_be_mistaken_for_execution_completion(self):
        lines = [self.event(0,'custom_tool_call',name='exec',call_id='a',input='SECRET'),
                 self.event(30,'custom_tool_call_output',call_id='a',output='Script running with cell ID 7'),
                 self.event(31,'function_call',name='wait',call_id='b',arguments='{"cell_id":"other"}'),
                 self.event(32,'function_call_output',call_id='b',output='{"wall_time_seconds":1}'),
                 self.event(60,'function_call',name='wait',call_id='c',arguments='{"cell_id":"7"}'),
                 self.event(61,'function_call_output',call_id='c',output='{"wall_time_seconds":0.9,"output":"SECRET"}')]
        result = first_execution(lines)
        self.assertEqual(result['tool_elapsed_seconds'],61)
        self.assertEqual(result['reported_command_seconds'],.9)
        self.assertEqual(result['outside_reported_command_seconds'],60.1)
        self.assertNotIn('SECRET',json.dumps(result))
        self.assertEqual(len(result['unattributed_components']),4)

    def test_pending_and_missing_duration_do_not_invent_shell_start_or_review_blame(self):
        lines = [self.event(0,'function_call',name='exec_command',call_id='a')]
        self.assertEqual(first_execution(lines)['state'],'pending_or_unobserved')
        lines.append(self.event(20,'function_call_output',call_id='a',output='failure'))
        result=first_execution(lines)
        self.assertIsNone(result['reported_command_seconds'])
        self.assertIsNone(result['outside_reported_command_seconds'])

    def test_timing_audit_failure_cannot_lose_a_successful_private_claim(self):
        from agent.worker_start import claim_to_file
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root/'item'/'token'
            destination.parent.mkdir()
            ledger = Mock()
            ledger.claim.return_value = {'token':'fixture-private-token', 'state':'running'}
            ledger.note.side_effect = sqlite3.OperationalError('fixture timing unavailable')
            with patch('agent.worker_start.Paths', return_value=SimpleNamespace(runs=root)):
                result = claim_to_file(ledger, 'item', 'worker', destination, object())
            self.assertEqual(destination.read_text(encoding='utf-8'), 'fixture-private-token')
            self.assertNotIn('fixture-private-token', json.dumps(result))
            self.assertFalse(result['startup_timing']['audit_recorded'])
            self.assertLessEqual(result['startup_timing']['command_entered_at'],
                                 result['startup_timing']['claim_completed_at'])
            ledger.claim.assert_called_once()
