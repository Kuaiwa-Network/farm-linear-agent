"""Live issue reads reprioritize unlaunched work without changing worker/resource ownership."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from agent.execution import queue_resource
from agent.ledger import LedgerError
from agent.lifecycle import Lifecycle
from agent.scheduler import Scheduler
from agent.skills import load_skills
from test_ledger import LedgerBase, ISSUE, OTHER, issue
from test_lifecycle import status
from test_receiver import APP
from test_scheduler import FakeAPI, FakeLauncher, FakeWorktrees

ROOT = Path(__file__).resolve().parents[1]
OLD = '2026-10-23T00:00:00Z'
NEW = '2026-10-23T00:01:00Z'


class PriorityTests(LedgerBase):
    def item(self, issue_id=ISSUE, priority=2):
        self.ledger.observe_issue(issue(id=issue_id, priority=priority, updated_at=OLD, delegate_id=APP))
        session = str(uuid4())
        self.ledger.ensure_session(session, issue_id, delegation=True)
        return self.ledger.create_work_item(issue_id=issue_id, session_id=session, skill='work')

    def refresh(self, issue_id=ISSUE, priority=1, version=NEW):
        return self.ledger.apply_issue_status({**status(issue_id), 'priority': priority, 'updated_at': version})

    def queued(self):
        return [row['issue_id'] for row in self.ledger.queue()]

    def scheduler(self):
        self.launcher = FakeLauncher(Path(self.tmp.name) / 'runs')
        return Scheduler(self.ledger, self.launcher, load_skills(ROOT / 'skills'),
                         FakeWorktrees(Path(self.tmp.name) / 'wt'), skill_root=ROOT / 'skills',
                         db_path=self.path, runtime_name='fake', host='h', max_concurrent=1, api=FakeAPI())

    def test_status_promotion_demotion_and_clear_reorder_original_items(self):
        first = self.item(priority=2)
        self.now += 1
        second = self.item(OTHER, priority=4)
        self.assertEqual(self.queued(), [ISSUE, OTHER])
        self.refresh(OTHER, 1)
        self.assertEqual(self.queued(), [OTHER, ISSUE])
        self.refresh(OTHER, 3, '2026-10-23T00:02:00Z')
        self.assertEqual(self.queued(), [ISSUE, OTHER])
        self.refresh(ISSUE, 0, '2026-10-23T00:03:00Z')
        self.assertEqual(self.queued(), [OTHER, ISSUE])
        self.assertEqual(self.ledger.item(first['id'])['priority'], 5)
        for original in (first, second):
            current = self.ledger.item(original['id'])
            self.assertEqual((current['created_at'], current['generation'], current['state']),
                             (original['created_at'], 0, 'queued'))
        self.ledger.close()
        self.ledger = self.open_ledger()
        self.assertEqual(self.queued(), [OTHER, ISSUE])

    def test_full_snapshot_reorders_without_changing_input_fingerprint(self):
        self.item(priority=4)
        self.item(OTHER, priority=2)
        before = self.ledger.connection.execute('SELECT fingerprint FROM issues WHERE id=?', (ISSUE,)).fetchone()[0]
        observed = self.ledger.observe_issue(issue(priority=1, updated_at=NEW, delegate_id=APP))
        self.assertEqual(observed['fingerprint'], before)
        self.assertEqual(self.queued(), [ISSUE, OTHER])

    def test_equal_priority_retains_original_creation_order(self):
        self.item(priority=4)
        self.now += 10
        self.item(OTHER, priority=1)
        self.refresh(priority=1)
        self.assertEqual(self.queued(), [ISSUE, OTHER])

    def test_old_status_and_full_snapshots_cannot_undo_new_priority(self):
        item = self.item(priority=4)
        self.refresh(priority=1)
        self.refresh(priority=4, version=OLD)
        for version in (OLD, NEW, None):
            self.ledger.observe_issue(issue(priority=4, updated_at=version))
            self.assertEqual(self.ledger.issue(ISSUE)['priority'], 1)
            self.assertEqual(self.ledger.item(item['id'])['priority'], 1)

    def test_older_status_cannot_undo_full_snapshot_priority(self):
        item = self.item(priority=4)
        self.ledger.observe_issue(issue(priority=1, updated_at=NEW))
        self.refresh(priority=4, version=OLD)
        self.assertEqual(self.ledger.item(item['id'])['priority'], 1)

    def test_legacy_status_without_priority_preserves_saved_value(self):
        item = self.item(priority=1)
        self.ledger.apply_issue_status({**status(), 'updated_at': NEW})
        self.assertEqual(self.ledger.issue(ISSUE)['priority'], 1)
        self.assertEqual(self.ledger.item(item['id'])['priority'], 1)

    def test_existing_queue_refreshes_even_when_issue_version_is_unchanged(self):
        item = self.item(priority=4)
        self.refresh(priority=1)
        # Before this change, issue metadata could already be fresh while the
        # work item retained its original priority across controller restarts.
        self.ledger.connection.execute('UPDATE work_items SET priority=4 WHERE id=?', (item['id'],))
        self.ledger.close()
        self.ledger = self.open_ledger()
        self.refresh(priority=1)
        self.assertEqual(self.ledger.item(item['id'])['priority'], 1)

    def test_invalid_status_priority_is_rejected_without_partial_update(self):
        item = self.item(priority=2)
        original = self.ledger.issue(ISSUE)
        for priority in (True, False, None, '1', 1.5, -1, 5):
            with self.subTest(priority=priority), self.assertRaises(LedgerError):
                self.refresh(priority=priority)
            self.assertEqual(self.ledger.issue(ISSUE), original)
            self.assertEqual(self.ledger.item(item['id'])['priority'], 2)

    def test_started_and_terminal_work_is_not_reprioritized_or_restarted(self):
        launched = self.item(priority=4)
        self.ledger.set_worker(launched['id'], 4242, 'h')
        before = self.ledger.item(launched['id'])
        self.refresh(priority=1)
        self.assertEqual(self.ledger.item(launched['id']), before)
        token = self.ledger.claim(launched['id'], worker_id='test')['token']
        before = self.ledger.item(launched['id'])
        self.ledger.observe_issue(issue(priority=2, updated_at='2026-10-23T00:02:00Z'))
        self.assertEqual(self.ledger.item(launched['id']), before)
        self.ledger.renew(launched['id'], token)
        self.ledger.cancel(launched['id'], 'test cancellation')
        before = self.ledger.item(launched['id'])
        self.refresh(priority=3, version='2026-10-23T00:03:00Z')
        self.assertEqual(self.ledger.item(launched['id']), before)
        self.assertEqual(self.queued(), [])

    def test_status_refresh_preserves_resource_fifo_and_claims(self):
        first = self.item(priority=3)
        second = self.item(OTHER, priority=4)
        for item in (first, second):
            token = self.ledger.claim(item['id'], worker_id='test')['token']
            queue_resource(self.ledger, item['id'], token, 'unity_slot', 'interactive', 'a' * 40)
        before = self.ledger.reservations()
        self.refresh(OTHER, 1)
        self.assertEqual(self.ledger.reservations(), before)
        self.assertEqual([row['item_id'] for row in self.ledger.reservation_queue('unity_slot')],
                         [first['id'], second['id']])

    def test_lifecycle_refresh_promotes_waiting_work_without_preempting_active_worker(self):
        first = self.item(priority=2)
        scheduler = self.scheduler()
        self.assertEqual(scheduler.tick()['launched'], 1)
        waiting = self.item(OTHER, priority=4)
        api = SimpleNamespace(app_user_id=APP, issue_status=lambda iid:
                              {**status(iid), 'priority': 1, 'updated_at': NEW})
        lifecycle = Lifecycle(self.ledger, api, scheduler, clock=lambda: self.now)
        self.assertIsNotNone(lifecycle.refresh(ISSUE))
        self.ledger.request_status_check(OTHER)
        self.assertEqual(lifecycle.tick(), {'checked': OTHER, 'ok': True})
        self.assertEqual(self.ledger.item(waiting['id'])['priority'], 1)
        self.assertEqual(scheduler.tick()['launched'], 0)
        self.assertEqual([entry[0] for entry in self.launcher.spawned], [first['id']])
        self.assertEqual(self.launcher.stopped, [])

    def test_preflight_demotion_reorders_before_spending_last_worker_seat(self):
        first = self.item(priority=1)
        second = self.item(OTHER, priority=2)
        scheduler = self.scheduler()
        api = SimpleNamespace(app_user_id=APP, issue_status=lambda iid:
                              {**status(iid), 'priority': 4 if iid == ISSUE else 2, 'updated_at': NEW})
        scheduler.preflight = Lifecycle(self.ledger, api, scheduler, clock=lambda: self.now).preflight
        self.assertEqual(scheduler.tick()['launched'], 0)
        self.assertEqual(self.queued(), [OTHER, ISSUE])
        self.assertEqual(scheduler.tick()['launched'], 1)
        self.assertEqual([entry[0] for entry in self.launcher.spawned], [second['id']])
        self.assertIsNone(self.ledger.item(first['id'])['worker_pid'])

    def test_other_connection_promotion_during_preflight_reorders_batch(self):
        first = self.item(priority=2)
        second = self.item(OTHER, priority=4)
        scheduler = self.scheduler()
        other = self.open_ledger()

        def preflight(item):
            other.apply_issue_status({**status(OTHER), 'priority': 1, 'updated_at': NEW})
            return True

        scheduler.preflight = preflight
        self.assertEqual(scheduler.tick()['launched'], 0)
        self.assertEqual(scheduler.tick()['launched'], 1)
        self.assertEqual([entry[0] for entry in self.launcher.spawned], [second['id']])
        self.assertIsNone(self.ledger.item(first['id'])['worker_pid'])

    def test_failed_refresh_preserves_priority_and_uses_existing_backoff(self):
        item = self.item(priority=4)
        scheduler = self.scheduler()
        api = SimpleNamespace(app_user_id=APP)
        with patch.object(api, 'issue_status', create=True, side_effect=OSError('offline')) as call:
            lifecycle = Lifecycle(self.ledger, api, scheduler, clock=lambda: self.now)
            self.assertFalse(lifecycle.preflight(item))
            self.assertFalse(lifecycle.preflight(item))
            self.assertEqual(call.call_count, 1)
        self.assertEqual(self.ledger.item(item['id'])['priority'], 4)
        self.assertEqual(self.ledger.item(item['id'])['state'], 'queued')
