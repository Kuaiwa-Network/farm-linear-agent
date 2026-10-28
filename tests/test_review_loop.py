import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from agent.review_loop import ReviewLoop


class ReviewLoopTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.executescript('CREATE TABLE review_deliveries(item_id,generation); '
            'CREATE TABLE review_runs(id,item_id,generation,state); '
            'CREATE TABLE review_prs(run_id,state,retry_at);')
        self.now = 1000.0
        self.controller = SimpleNamespace(db=self.db, policy={'mode': 'merge', 'poll_seconds': 300},
                                          tick=Mock(), process_workers=Mock(return_value=False),
                                          workers=SimpleNamespace(busy=Mock(return_value=0)))
        self.loop = ReviewLoop(self.controller, clock=lambda: self.now)

    def test_local_checks_do_not_poll_remote_until_due(self):
        self.loop.step()
        for t in (1005, 1010, 1020):
            self.now = t
            self.loop.step()
        self.assertEqual(self.controller.tick.call_count, 1)
        self.now = 1300
        self.loop.step()
        self.assertEqual(self.controller.tick.call_count, 2)

    def test_new_delivery_and_worker_completion_wake_full_tick(self):
        self.loop.step()
        self.db.execute("INSERT INTO review_deliveries VALUES('work',1)")
        self.now += 5
        self.loop.step()
        self.assertEqual(self.controller.tick.call_count, 2)
        self.db.execute("INSERT INTO review_runs VALUES('run','work',1,'queued')")
        self.controller.process_workers.return_value = True
        self.now += 5
        self.loop.step()
        self.assertEqual(self.controller.tick.call_count, 3)

    def test_due_retry_wakes_once_and_retains_remote_backoff(self):
        self.db.execute("INSERT INTO review_runs VALUES('run','work',1,'queued')")
        self.db.execute("INSERT INTO review_prs VALUES('run','reviewing',1060)")
        self.loop.step()
        self.now = 1059
        self.loop.step()
        self.assertEqual(self.controller.tick.call_count, 1)
        self.now = 1060
        self.loop.step()
        self.assertEqual(self.controller.tick.call_count, 2)
        self.now = 1065
        self.loop.step()
        self.assertEqual(self.controller.tick.call_count, 2)

    def test_disabled_mode_does_not_spin_on_unenrolled_deliveries(self):
        self.controller.policy['mode'] = 'off'
        self.db.execute("INSERT INTO review_deliveries VALUES('work',1)")
        self.loop.step()
        self.now += 5
        self.loop.step()
        self.assertEqual(self.controller.tick.call_count, 1)

    def test_stale_worker_teardown_wakes_waiting_work_without_a_report(self):
        self.loop.step()
        self.now += 5
        self.controller.workers.busy.side_effect = [1, 0]
        self.loop.step()
        self.assertEqual(self.controller.tick.call_count, 2)
