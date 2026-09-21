import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent.config import Config, Paths
from agent.ledger import Ledger, LedgerError
from test_ledger import issue, ISSUE, SESSION


class CleanupRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.config = Config('c', 's', 'w', host='windows-test', local_root=Path(self.tmp.name))
        self.ledger = Ledger(Paths(self.config).ledger, clock=lambda: 100)
        self.addCleanup(self.ledger.close)
        self.ledger.observe_issue(issue())
        self.ledger.ensure_session(SESSION, ISSUE, delegation=True)
        self.item = self.ledger.create_work_item(issue_id=ISSUE, session_id=SESSION, skill='fix')
        self.ledger.set_worker(self.item['id'], 123, 'windows-test')
        self.ledger.fail_queued(self.item['id'], 'exited')
        self.ledger.record_cleanup(self.item['id'], {}, error='unverified')

    def recover(self, boot):
        from agent.cleanup_recovery import recover_after_boot
        with patch('agent.cleanup_recovery.windows_boot_time', return_value=boot):
            return recover_after_boot(self.config, self.item['id'], 'operator verified reboot')

    def test_same_boot_does_not_clear_cleanup_fence(self):
        with self.assertRaisesRegex(LedgerError, 'restart'):
            self.recover(90)
        self.assertEqual(self.ledger.cleanup_record(self.item['id'])['error'], 'unverified')

    def test_later_boot_records_proof_without_deleting_or_retrying(self):
        result = self.recover(110)
        self.assertEqual(result['processes'], [123])
        self.assertEqual(result['boot_time'], 110)
        self.assertEqual(self.ledger.item(self.item['id'])['state'], 'failed')
        self.assertFalse(self.ledger.cleanup_record(self.item['id'])['done'])
        self.assertIsNone(self.ledger.cleanup_record(self.item['id'])['error'])
        event = self.ledger.connection.execute("SELECT reason FROM audit WHERE kind='cleanup_recovery'").fetchone()
        self.assertIn('operator verified reboot', event['reason'])

    def test_active_retry_is_never_certified(self):
        self.ledger.retry(self.item['id'], 'retry')
        with self.assertRaisesRegex(LedgerError, 'terminal'):
            self.recover(110)

    def test_other_host_cannot_certify_cleanup(self):
        self.config.host = 'different-host'
        with self.assertRaisesRegex(LedgerError, 'host'):
            self.recover(110)
