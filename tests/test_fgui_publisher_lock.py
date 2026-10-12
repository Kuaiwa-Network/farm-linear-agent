"""Publisher mutex polling stays responsive without repeatedly renewing SQLite claims."""
import unittest
from unittest.mock import MagicMock, Mock, patch

from agent import fgui_publisher as publisher
from agent.ledger import LedgerError
import test_ledger as fixtures


class ExportLockWaitTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.sleeps = []
        self.enterContext(patch.object(publisher.time, "monotonic", side_effect=lambda: self.now))
        self.enterContext(patch.object(publisher.time, "sleep", side_effect=self.sleep))

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def test_long_contention_bounds_fence_writes_but_retries_every_tenth_second(self):
        fixture = fixtures.LedgerBase()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        ledger = fixture.ledger
        ledger.clock = lambda: 1000 + self.now
        item = fixture.new_item("fgui", target=None)
        token = ledger.claim(item["id"], worker_id="waiting-export")["token"]
        fences = []

        def fence():
            fences.append(self.now)
            ledger.renew(item["id"], token)

        with patch.object(publisher, "_WindowsLock", side_effect=publisher.ExportLockBusy("busy")) as mutex:
            with self.assertRaisesRegex(publisher.ExportLockBusy, "timed out"):
                publisher._acquire_export_lock(fence, 180)
        self.assertGreater(mutex.call_count, 1700)
        self.assertLessEqual(len(fences), 181)
        self.assertTrue(all(b - a >= 1 - 1e-9 for a, b in zip(fences, fences[1:])))
        self.assertTrue(all(0 < delay <= .1 for delay in self.sleeps))
        self.assertAlmostEqual(self.now, 180)
        self.assertEqual(ledger.connection.execute("SELECT COUNT(*) FROM audit WHERE item_id=? AND kind='renew'",
                                                  (item["id"],)).fetchone()[0], len(fences))

    def test_acquisition_always_rechecks_authority_before_returning_the_lock(self):
        lock = MagicMock()
        fence = Mock()
        with patch.object(publisher, "_WindowsLock", return_value=lock):
            self.assertIs(publisher._acquire_export_lock(fence, 5), lock)
        fence.assert_called_once_with()
        lock.__exit__.assert_not_called()

    def test_stop_on_acquisition_releases_the_mutex_without_returning_authority(self):
        lock = MagicMock()
        with patch.object(publisher, "_WindowsLock", return_value=lock):
            with self.assertRaisesRegex(LedgerError, "stopped"):
                publisher._acquire_export_lock(Mock(side_effect=LedgerError("stopped")), 5)
        lock.__exit__.assert_called_once()

    def test_stop_while_busy_is_observed_at_the_bounded_fence_cadence(self):
        def fence():
            if self.now >= .35:
                raise LedgerError("stopped")
        with patch.object(publisher, "_WindowsLock", side_effect=publisher.ExportLockBusy("busy")):
            with self.assertRaisesRegex(LedgerError, "stopped"):
                publisher._acquire_export_lock(fence, 5)
        self.assertLessEqual(self.now, 1.1 + 1e-9)

    def test_lease_expiry_during_wait_is_refused_by_the_real_ledger(self):
        fixture = fixtures.LedgerBase()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        ledger = fixture.ledger
        ledger.clock = lambda: 1000 + self.now
        item = fixture.new_item("fgui", target=None)
        token = ledger.claim(item["id"], worker_id="waiting-export")["token"]

        def stall(seconds):
            self.now += 61

        with patch.object(publisher.time, "sleep", side_effect=stall), \
                patch.object(publisher, "_WindowsLock", side_effect=publisher.ExportLockBusy("busy")):
            with self.assertRaisesRegex(LedgerError, "lease"):
                publisher._acquire_export_lock(lambda: ledger.renew(item["id"], token), 180)

    def test_acquisition_after_a_slow_fence_cannot_overrun_the_wait_deadline(self):
        lock = MagicMock()

        def slow_fence():
            self.now += 2

        with patch.object(publisher, "_WindowsLock", return_value=lock):
            with self.assertRaisesRegex(publisher.ExportLockBusy, "timed out"):
                publisher._acquire_export_lock(slow_fence, 1)
        lock.__exit__.assert_called_once()


if __name__ == "__main__":
    unittest.main()
