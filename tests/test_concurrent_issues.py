"""Independent owners share worker capacity without broadening issue admission."""
import json
from contextlib import closing
from unittest.mock import Mock, patch

from agent.admission_scope import KIND, read
from agent.execution import queue_resource
from agent.ledger import Ledger, LedgerError
from agent.maintenance import Maintenance, status
import test_single_worker as fixtures
from test_ledger import issue


class ConcurrentIssuesTests(fixtures.LedgerBase):
    scheduler = fixtures.SingleWorkerTests.scheduler

    def item(self, number):
        self.now += 1
        key = f'10000000-0000-4000-8000-{number:012d}'
        self.ledger.observe_issue(issue(key, identifier=f'FARM-{number}', branch_name=f'farmbot/farm-{number}'))
        self.ledger.ensure_session(f'session-{number}', key, delegation=True)
        return self.ledger.create_work_item(issue_id=key, session_id=f'session-{number}', skill='work')

    def test_four_isolated_owners_keep_seats_and_claims_while_two_unity_slots_serialize(self):
        items = [self.item(n) for n in range(1, 6)]
        scheduler = self.scheduler()
        scheduler.max_concurrent = 4
        store = Maintenance(self.ledger)
        store.pause('selected group')
        store.resume(only_issue=['FARM-1', 'FARM-2', 'FARM-3', 'FARM-4'])
        self.assertEqual(scheduler.tick()['launched'], 4)
        self.assertEqual(set(scheduler.active), {item['id'] for item in items[:4]})
        prompts = [json.loads(spawn[1].split('\n\n', 1)[1]) for spawn in self.launcher.spawned]
        for repo in fixtures.SKILLS['work'].writes:
            self.assertEqual(len({p['worktrees'][repo] for p in prompts}), 4)
        tokens = {}
        reservations = []
        for index, item in enumerate(items[:4]):
            token = self.ledger.claim(item['id'], worker_id=f'worker-{index}')['token']
            tokens[item['id']] = token
            reservations.append(queue_resource(self.ledger, item['id'], token, 'unity_slot', 'interactive', fixtures.SHA))
        for n in (1, 2):
            self.ledger.ensure_slot(f'unity:{n}', kind='unity_slot', host='test', folder=f'/slot-{n}')
        acquire = lambda: scheduler.acquire_unity(self.ledger, lambda db, kind, **opts: db.acquire(kind, **opts),
                                                 'unity_slot', host='test', owner='pool')
        grants = [acquire() for _ in range(2)]
        self.assertTrue(all(grants))
        self.assertIsNone(acquire())
        self.assertEqual(scheduler.tick()['launched'], 0)
        self.assertEqual(len(scheduler.active), 4)
        self.assertEqual(len(self.launcher.spawned), 4)
        self.assertEqual(len(self.ledger.reservation_queue('unity_slot')), 2)
        for item in items[:4]:
            renewed = self.ledger.renew(item['id'], tokens[item['id']])
            self.assertEqual(renewed['state'], 'running')
        with self.assertRaises(LedgerError):
            self.ledger.renew(items[1]['id'], tokens[items[0]['id']])
        with self.assertRaisesRegex(LedgerError, 'acceptance'):
            self.ledger.claim(items[4]['id'], worker_id='outside')

    def test_worker_cap_still_limits_selected_group(self):
        items = [self.item(n) for n in range(1, 5)]
        scheduler = self.scheduler()
        scheduler.max_concurrent = 2
        self.assertEqual(scheduler.tick()['launched'], 2)
        self.assertEqual(len(scheduler.active), 2)
        self.assertEqual(len(self.launcher.spawned), 2)
        self.assertTrue(all(self.ledger.item(item['id'])['worker_pid'] is None for item in items[2:]))

    def test_preflight_cannot_spend_a_seat_taken_while_the_lock_was_released(self):
        first, second = self.item(1), self.item(2)
        scheduler = self.scheduler()
        scheduler.max_concurrent = 1
        def preflight(item):
            self.assertEqual(item['id'], first['id'])
            scheduler.launch(second)
            return True
        scheduler.preflight = preflight
        self.assertEqual(scheduler.tick()['launched'], 0)
        self.assertEqual(list(scheduler.active), [second['id']])
        self.assertEqual(len(self.launcher.spawned), 1)

    def test_group_survives_restart_and_monitor_excludes_unselected_queue_positions(self):
        from agent.monitor_view import build_status
        from test_monitor_view import beat, HEALTHY, INSTANCE
        items = [self.item(n) for n in range(1, 4)]
        before = self.ledger.item(items[2]['id'])
        store = Maintenance(self.ledger)
        store.pause('scope')
        store.resume(only_issue=['farm-1', 'FARM-2'])
        with closing(Ledger(self.path, clock=lambda: self.now)) as reopened:
            self.assertTrue(Maintenance(reopened).may_launch(items[0]['id']))
            self.assertTrue(Maintenance(reopened).may_launch(items[1]['id']))
            self.assertFalse(Maintenance(reopened).may_launch(items[2]['id']))
            self.assertEqual(status(reopened.connection, ('fresh', beat()), self.now)['only_issues'], ['FARM-1', 'FARM-2'])
        result = build_status(self.path, heartbeat=('fresh', beat()), health=HEALTHY, instance=INSTANCE,
                              monitor_revision=('b'*40, False), now=self.now)
        active = {job['identifier']: job for job in result['active']}
        self.assertEqual({active[f'FARM-{n}']['queue_position'] for n in (1, 2)}, {1, 2})
        self.assertEqual(active['FARM-3']['display_state'], 'acceptance_paused')
        self.assertEqual(active['FARM-3']['waiting_for_issue'], 'FARM-1, FARM-2')
        self.assertIsNone(active['FARM-3']['queue_position'])
        self.assertEqual(self.ledger.item(items[2]['id']), before)

    def test_invalid_groups_and_unknown_members_preserve_maintenance_and_previous_scope(self):
        self.item(1)
        self.item(2)
        store = Maintenance(self.ledger)
        store.pause('scope')
        store.resume(only_issue='FARM-1')
        store.pause('change scope')
        before = read(self.ledger.connection)
        for names in ([], ['FARM-1', 'farm-1'], ['FARM-1', 'FARM-999'], ['FARM-1', None], ['FARM-1']*33):
            with self.subTest(names=names), self.assertRaises(LedgerError):
                store.resume(only_issue=names)
            self.assertTrue(store.requested())
            self.assertEqual(read(self.ledger.connection), before)

    def test_damaged_group_fails_closed(self):
        item = self.item(1)
        valid = {'issue_id': item['issue_id'], 'identifier': 'FARM-1'}
        for value in ({'issues': []}, {'issues': [valid]}, {'issues': [valid, valid]},
                      {'issues': [valid, {'private': 'not-output'}]}):
            self.ledger.note('maintenance', KIND, details=value)
            with self.assertRaisesRegex(LedgerError, 'damaged') as caught:
                Maintenance(self.ledger).may_launch(item['id'])
            self.assertNotIn('not-output', str(caught.exception))

    def test_group_resume_refuses_other_live_owner_without_clearing_maintenance(self):
        items = [self.item(n) for n in range(1, 4)]
        self.ledger.set_worker(items[2]['id'], 123, 'test')
        store = Maintenance(self.ledger)
        store.pause('scope')
        with self.assertRaisesRegex(LedgerError, 'settle other issue'):
            store.resume(only_issue=['FARM-1', 'FARM-2'])
        self.assertTrue(store.requested())
        self.assertIsNone(read(self.ledger.connection))

    def test_host_cli_accepts_repeated_identifiers(self):
        from agent.service import main
        with patch('agent.service.load_config', return_value=Mock(local_root=self.tmp.name)), \
                patch('agent.service.check_ownership'), patch('agent.service.Ledger'), \
                patch('agent.maintenance.Maintenance') as store, \
                patch('agent.maintenance.read_status', return_value={}), patch('builtins.print'):
            self.assertEqual(main(['maintenance-resume', '--config', 'private.json',
                                   '--only-issue', 'FARM-1', '--only-issue', 'FARM-2']), 0)
        store.return_value.resume.assert_called_once_with(only_issue=['FARM-1', 'FARM-2'])
