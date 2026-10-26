"""Server release/relaunch ordering with real SQLite and fake isolated services."""
from pathlib import Path
from unittest.mock import patch

from agent.execution import queue_resource
from agent.ledger import LedgerError
from agent.resource_handoff import release, pending_cleanup, requested, STAGE
from agent.scheduler import Scheduler
from test_server_slots import ServerPoolFixture, SKILLS
from test_scheduler import FakeLauncher, FakeWorktrees


class HandoffTests(ServerPoolFixture):
    def start(self, *, pair=False, managed=False):
        key = self.request(skill='work', reserve=False, execution_contract='single-worker-v1')
        self.ledger.set_worker(key, 123, 'test')
        token = self.ledger.claim(key, worker_id='owner')['token']
        options = dict(test_plan={'packages':['./breeding'], 'tests':['TestCount'], 'race':False})
        kind, mode = 'server_slot', 'batch'
        if pair:
            from test_client_server_integration import plan
            self.ledger.ensure_slot('unity_slot:1', kind='unity_slot', host='test', folder=str(self.root/'editor'))
            options, kind, mode = dict(integration_plan=plan()), 'unity_slot', 'interactive'
        queue_resource(self.ledger, key, token, kind, mode, 'a'*40, **options)
        if not managed:
            self.ledger.connection.execute('DELETE FROM resource_process_scopes WHERE item_id=?', (key,))
        self.exited = False
        if pair:
            grant = self.admission.acquire(self.ledger, kind, owner='pool', host='test')
            self.pool.prepare_integration(grant)
            self.ledger.set_slot_state('unity_slot:1', 'interactive_busy')
        else:
            self.pool.single_only = True
            self.assertEqual(self.pool.tick()['granted'], 1)
            grant = self.ledger.active_reservation(key)
            grant['token'] = (self.pool.state_dir(key)/'reservation.token').read_text()
        self.now += 1
        return key, token, grant

    def save(self, key, token):
        self.ledger.checkpoint(key, token, {'summary':'Tests completed; reports retained',
            'candidates':{'farm-hive':'a'*40}, 'reports':['reports/results.json'],
            'handoff':'Continue Client checks at the retained candidate'})

    def scheduler(self):
        launcher = FakeLauncher(self.root.parent/'runs')
        trees = FakeWorktrees(self.root.parent/'worktrees')
        return Scheduler(self.ledger, launcher, SKILLS, trees, skill_root=Path(__file__).parents[1]/'skills',
                         db_path=self.db, runtime_name='fake', host='test')

    def test_release_exit_cleanup_relaunch_preserves_checkpoint_and_generation(self):
        key, token, grant = self.start()
        self.save(key, token)
        before = self.ledger.item(key)
        result = release(self.ledger, grant['reservation_id'], grant['token'])
        after = self.ledger.item(key)
        self.assertTrue(result['worker_handoff'])
        self.assertEqual((after['state'], after['stage'], after['worker_pid']), ('queued', STAGE, None))
        for field in ('checkpoint','generation','capacity_retries','startup_retries','publication_retries'):
            self.assertEqual(after[field], before[field])
        with self.assertRaises(LedgerError): self.ledger.renew(key, token)
        self.assertTrue(requested(self.ledger, after))
        self.assertTrue(pending_cleanup(self.ledger, key))
        scheduler = self.scheduler()
        self.assertIsNone(scheduler.launch(after))
        self.assertEqual(self.pool.tick()['settled'], 0)
        self.assertEqual(self.services.cleaned, [])
        self.exited = True
        self.assertEqual(self.pool.tick()['settled'], 1)
        self.assertFalse(pending_cleanup(self.ledger, key))
        with patch.object(scheduler.launcher, 'assert_quiescent', side_effect=RuntimeError('uncertified earlier attempt')):
            self.assertIsNone(scheduler.launch(after))
        self.assertIsNotNone(scheduler.launch(after))
        self.assertEqual(self.ledger.item(key)['checkpoint'], before['checkpoint'])
        self.assertEqual(len(self.services.cleaned), 1)

    def test_missing_stale_checkpoint_or_wrong_reservation_token_cannot_retire_claim(self):
        key, token, grant = self.start()
        with self.assertRaisesRegex(LedgerError, 'current checkpoint'):
            release(self.ledger, grant['reservation_id'], grant['token'])
        self.save(key, token)
        with self.assertRaisesRegex(LedgerError, 'reservation token'):
            release(self.ledger, grant['reservation_id'], token)
        self.ledger.connection.execute('UPDATE reservations SET acquired_at=? WHERE reservation_id=?',
                                      (self.now+1, grant['reservation_id']))
        with self.assertRaisesRegex(LedgerError, 'current checkpoint'):
            release(self.ledger, grant['reservation_id'], grant['token'])
        self.assertEqual(self.ledger.item(key)['state'], 'running')
        self.assertEqual(self.ledger.reservation(grant['reservation_id'])['state'], 'active')

    def test_combined_release_retires_owner_and_waits_for_server_half(self):
        key, token, grant = self.start(pair=True)
        self.save(key, token)
        self.assertTrue(release(self.ledger, grant['reservation_id'], grant['token'])['worker_handoff'])
        self.assertTrue(pending_cleanup(self.ledger, key))
        self.assertEqual(self.pool.tick()['settled'], 0)
        self.exited = True
        self.assertEqual(self.pool.tick()['settled'], 1)
        self.assertFalse(pending_cleanup(self.ledger, key))

    def test_cleanup_failure_holds_slot_and_blocks_relaunch_after_restart(self):
        key, token, grant = self.start()
        self.save(key, token)
        release(self.ledger, grant['reservation_id'], grant['token'])
        self.exited = True
        self.services.error = RuntimeError('Docker ownership unavailable')
        self.pool.tick()
        self.assertEqual(self.ledger.slot(grant['resource'])['state'], 'held')
        restarted = self.connection()
        try:
            self.assertTrue(requested(restarted, restarted.item(key)))
            self.assertTrue(pending_cleanup(restarted, key))
        finally: restarted.close()
        self.assertIsNone(self.scheduler().launch(self.ledger.item(key)))

    def test_cancelled_or_withdrawn_owner_is_not_requeued_by_release(self):
        key, token, grant = self.start()
        self.ledger.cancel(key, 'Stop')
        result = release(self.ledger, grant['reservation_id'], grant['token'])
        self.assertNotIn('worker_handoff', result)
        self.assertEqual(self.ledger.item(key)['state'], 'cancelled')
        key, token, grant = self.start()
        self.ledger.connection.execute('UPDATE work_items SET withdraw_deadline=? WHERE id=?', (self.now+5,key))
        result = release(self.ledger, grant['reservation_id'], grant['token'])
        self.assertNotIn('worker_handoff', result)
        self.assertEqual(self.ledger.item(key)['state'], 'running')

    def test_repeated_release_does_not_requeue_a_newer_claim(self):
        key, token, grant = self.start()
        self.save(key, token)
        release(self.ledger, grant['reservation_id'], grant['token'])
        self.exited = True
        self.pool.tick()
        self.ledger.set_worker(key,124,'test')
        new = self.ledger.claim(key,worker_id='resumed')['token']
        result = release(self.ledger, grant['reservation_id'], grant['token'])
        self.assertNotIn('worker_handoff',result)
        self.ledger.renew(key,new)
