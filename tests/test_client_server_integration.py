"""Offline pair admission, fake services/Unity and native contained loopback programs."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from agent.integration import companion, get, validate_plan
from agent.integration_runtime import execute
from agent.ledger import LedgerError
from agent.slots import SlotPool
from test_server_slots import ServerPoolFixture, SKILLS


def plan():
    return {'server_commit':'b'*40, 'server_argv':['game-server','--bind','{host}:{port}'],
            'scenarios':['client action persists on server and returns to Client'], 'startup_seconds':3}


class PairTests(ServerPoolFixture):
    def request_pair(self):
        item = self.request(root='Farm-Client', reserve=False)
        token = self.ledger.claim(item, worker_id='worker')['token']
        self.ledger.await_resource(item, token, 'unity_slot', 'interactive', skill=SKILLS['fix'],
                                   commit_sha='a'*40, integration_plan=plan())
        self.ledger.ensure_slot('unity_slot:1', kind='unity_slot', host='test', folder=str(self.root / 'editor'),
                               mcp_address='http://localhost:9999')
        return item

    def acquire(self):
        return self.admission.acquire(self.ledger, 'unity_slot', owner='pool', host='test')

    def test_memory_wait_holds_neither_resource_then_atomically_acquires_both(self):
        item = self.request_pair()
        self.admission.settings['max_reserved_mib'] = 8192
        self.assertIsNone(self.acquire())
        self.assertEqual(self.ledger.slot('unity_slot:1')['state'], 'idle_closed')
        self.assertTrue(all(s['state']=='idle_closed' for s in self.ledger.slots(kind='server_slot')))
        self.assertIsNone(self.ledger.active_reservation(item))
        self.admission.settings['max_reserved_mib'] = 16384
        lease = self.acquire()
        paired = companion(self.ledger, lease)
        self.assertEqual(self.ledger.active_reservation_on(paired['resource'])['reservation_id'], lease['reservation_id'])
        self.assertEqual(paired['commit_sha'], 'b'*40)
        self.assertEqual(self.ledger.slot(paired['resource'])['state'], 'switching')
        self.assertEqual(len(self.ledger.reservations()), 1)

    def test_server_capacity_wait_does_not_hold_unity(self):
        self.request_pair()
        for slot in self.ledger.slots(kind='server_slot'):
            self.ledger.set_slot_state(slot['slot_id'], 'held')
        self.assertIsNone(self.acquire())
        self.assertEqual(self.ledger.slot('unity_slot:1')['state'], 'idle_closed')

    def test_false_free_companion_cannot_be_double_granted_and_hold_cannot_be_released_by_worker(self):
        self.request_pair()
        lease = self.acquire()
        paired = companion(self.ledger, lease)
        self.ledger.set_slot_state(paired['resource'], 'idle_closed')
        self.request()
        with self.assertRaisesRegex(LedgerError, 'returned to the pool early'):
            self.ledger.acquire('server_slot', owner='server-pool', host='test')
        self.ledger.set_slot_state(paired['resource'], 'held')
        with self.assertRaisesRegex(LedgerError, 'held'):
            self.ledger.release(lease['reservation_id'], lease['token'], 'worker reported quiescent')
        self.assertEqual(self.ledger.slot(paired['resource'])['state'], 'held')

    def test_pair_and_normal_server_share_fifo_without_half_leases(self):
        self.admission.settings['max_reserved_mib'] = 32768
        older = self.request()
        item = self.request_pair()
        self.assertIsNone(self.acquire())
        self.assertEqual(self.pool.tick()['granted'], 1)
        lease = self.acquire()
        self.assertEqual(lease['item_id'], item)
        later = self.request()
        # The two active requests retain distinct server identities.
        self.assertEqual(self.pool.tick()['granted'], 1)
        self.assertNotEqual(self.ledger.active_reservation(later)['resource'], companion(self.ledger, lease)['resource'])

    def test_older_pair_is_not_overtaken_by_normal_server(self):
        self.request_pair()
        self.request()
        self.assertEqual(self.pool.tick()['granted'], 0)
        self.assertIsNotNone(self.acquire())

    def test_two_pool_connections_cannot_split_or_duplicate_a_pair(self):
        import threading
        self.request_pair()
        grants = []
        barrier = threading.Barrier(2)
        def acquire():
            ledger = self.connection()
            try:
                barrier.wait()
                grants.append(self.admission.acquire(ledger, 'unity_slot', owner='pool', host='test'))
            finally:
                ledger.close()
        threads = [threading.Thread(target=acquire) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(sum(g is not None for g in grants), 1)
        self.assertEqual(len([s for s in self.ledger.slots(kind='server_slot') if s['state']=='switching']), 1)

    def test_handover_release_waits_for_worker_and_cleans_exact_companion(self):
        item = self.request_pair()
        lease = self.acquire()
        unity = SlotPool(self.ledger, None, [], host='test', editors_root=self.root / 'editors',
                         state_dir=self.pool.state_dir, prepare_integration=self.pool.prepare_integration)
        with patch.object(unity, 'switch'), patch.object(unity, '_resume_hand_over', return_value=True):
            self.assertTrue(unity._hand_over(lease))
        server = companion(self.ledger, lease)
        self.ledger.resume(item, 'both ready')
        self.ledger.release(lease['reservation_id'], lease['token'], 'worker reported quiescent')
        self.exited = False
        self.assertEqual(self.pool.tick()['settled'], 0)
        self.exited = True
        self.assertEqual(self.pool.tick()['settled'], 1)
        self.assertEqual(self.services.cleaned[0]['reservation_id'], lease['reservation_id'])
        self.assertEqual(self.ledger.slot(server['resource'])['state'], 'idle_closed')

    def test_stop_during_service_start_preserves_pair_for_verified_cleanup(self):
        item = self.request_pair()
        lease = self.acquire()
        self.services.after_start = lambda value:self.ledger.cancel(item, 'Stop')
        unity = SlotPool(self.ledger, None, [], host='test', editors_root=self.root / 'editors',
                         state_dir=self.pool.state_dir, prepare_integration=self.pool.prepare_integration)
        with patch.object(unity, 'switch') as switch:
            self.assertFalse(unity._hand_over(lease))
        switch.assert_not_called()
        self.assertEqual(self.ledger.slot('unity_slot:1')['state'], 'switching')
        self.exited = False
        self.assertEqual(self.pool.tick()['settled'], 0)
        self.exited = True
        self.assertEqual(self.pool.tick()['settled'], 1)
        self.assertEqual(self.ledger.slot(companion(self.ledger, lease)['resource'])['state'], 'idle_closed')

    def test_combined_deadline_fences_worker_and_preserves_candidate(self):
        item = self.request_pair()
        lease = self.acquire()
        self.pool.prepare_integration(lease)
        self.ledger.resume(item, 'ready')
        self.ledger.claim(item, worker_id='client')
        self.now += 1801
        self.pool.tick()
        self.assertEqual(self.ledger.item(item)['state'], 'blocked')
        self.assertEqual(self.ledger.reservation(lease['reservation_id'])['state'], 'cancel_requested')
        self.assertEqual(get(self.ledger, lease['reservation_id'])['plan'], plan())

    def test_interrupted_pair_retains_plan_and_cleans_old_services_after_token_fencing(self):
        from agent.resource_recovery import RecoveryStore
        item = self.request_pair()
        lease = self.acquire()
        self.pool.prepare_integration(lease)
        store = RecoveryStore(self.ledger)
        store.interrupted_handoff('unity_slot:1', 'test')
        recovery = store.pending('test')[0]
        attempt = store.begin(recovery['id'])
        store.detach(recovery['id'], attempt['attempts'])
        replacement = self.ledger.reservation_queue('unity_slot')[0]
        self.assertEqual(get(self.ledger, replacement['reservation_id'])['plan'], plan())
        self.assertIsNone(get(self.ledger, replacement['reservation_id'])['server_slot'])
        self.assertEqual(self.pool.tick()['settled'], 1)
        self.assertEqual(self.services.cleaned[0]['reservation_id'], lease['reservation_id'])

    def test_uncertain_setup_holds_both_and_recovery_requires_verified_teardown(self):
        item = self.request_pair()
        lease = self.acquire()
        self.services.error = RuntimeError('backend disconnected during create')
        unity = SlotPool(self.ledger, None, [], host='test', editors_root=self.root / 'editors',
                         state_dir=self.pool.state_dir, prepare_integration=self.pool.prepare_integration)
        self.assertFalse(unity._hand_over(lease))
        server_slot = companion(self.ledger, lease)['resource']
        self.assertEqual(self.ledger.slot(server_slot)['state'], 'held')
        self.assertEqual(self.ledger.slot('unity_slot:1')['state'], 'held')
        self.services.error = None
        self.exited = False
        with self.assertRaisesRegex(RuntimeError, 'teardown'):
            self.pool.recover(server_slot, 'offline fixture verified backend')
        self.exited = True
        self.pool.recover(server_slot, 'offline fixture verified backend')
        self.assertEqual(self.ledger.slot(server_slot)['state'], 'idle_closed')
        self.assertEqual(self.ledger.slot('unity_slot:1')['state'], 'held')

    def test_scheduler_grants_mcp_and_server_snapshot_without_server_source_writes(self):
        from agent.scheduler import Scheduler
        from test_scheduler import FakeLauncher, FakeWorktrees
        item = self.request_pair()
        lease = self.acquire()
        self.pool.prepare_integration(lease)
        self.ledger.set_slot_state('unity_slot:1', 'interactive_busy', instance='owned-instance')
        self.ledger.resume(item, 'ready')
        launcher = FakeLauncher(self.root.parent / 'runs')
        trees = FakeWorktrees(self.root.parent / 'worktrees')
        scheduler = Scheduler(self.ledger, launcher, SKILLS, trees, skill_root=Path(__file__).parents[1] / 'skills',
                              db_path=self.db, runtime_name='fake', host='test', server_slots_root=self.root, replay_legacy=True)
        snapshot = self.root.parent / 'server read snapshot'
        with patch('agent.server_slots.time.time', return_value=self.now), \
                patch.object(trees, 'verification_commit', create=True), \
                patch.object(trees, 'read_candidate', create=True, return_value=snapshot):
            self.assertIsNotNone(scheduler.launch(self.ledger.item(item)))
        payload = json.loads(launcher.spawned[-1][1].split('\n\n', 1)[1])
        self.assertEqual(payload['resource']['integration']['commit'], 'b'*40)
        self.assertEqual(payload['resource']['commit'], 'a'*40)
        self.assertEqual(set(launcher.spawned[-1][2]), {'unity'})
        self.assertNotIn(str(snapshot), launcher.spawn_writable)
        self.assertEqual(launcher.spawn_env['MONGO_REQUIRED'], '1')


class SinglePairTests(ServerPoolFixture):
    def queue(self):
        from agent.execution import queue_resource
        item = self.request(root=None, skill='work', reserve=False, execution_contract='single-worker-v1')
        self.ledger.set_worker(item, 123, 'test')
        token = self.ledger.claim(item, worker_id='same-worker')['token']
        request = queue_resource(self.ledger, item, token, 'unity_slot', 'interactive', 'a'*40,
                                 integration_plan=plan())
        self.ledger.ensure_slot('unity_slot:1', kind='unity_slot', host='test', folder=str(self.root / 'editor'),
                               mcp_address='http://localhost:9999')
        self.pool.single_only = True
        return item, token, request

    def test_same_worker_claim_gets_both_resources_and_releases_without_relaunch(self):
        from agent.execution import resource_status
        item, token, request = self.queue()
        self.exited = False
        lease = self.admission.acquire(self.ledger, 'unity_slot', owner='pool', host='test', single_only=True)
        unity = SlotPool(self.ledger, None, [], host='test', editors_root=self.root / 'editors',
                         state_dir=self.pool.state_dir, prepare_integration=self.pool.prepare_integration)
        def switch(slot, commit, mode):
            self.ledger.set_slot_state(slot, 'interactive_busy', instance='owned-instance')
        with patch.object(unity, 'switch', side_effect=switch):
            self.assertTrue(unity._hand_over(lease))
        current = self.ledger.renew(item, token)
        self.assertEqual((current['state'], current['worker_pid']), ('running', 123))
        self.assertTrue(resource_status(self.ledger, item, token)['ready'])
        self.ledger.release(lease['reservation_id'], lease['token'], 'worker reported quiescent')
        self.assertEqual(self.pool.tick()['settled'], 0)
        self.assertEqual(self.ledger.renew(item, token)['state'], 'running')
        self.exited = True
        self.assertEqual(self.pool.tick()['settled'], 1)

    def test_companion_hold_prevents_ready_status_and_unity_access(self):
        from agent.execution import resource_ready, resource_status
        item, token, _ = self.queue()
        lease = self.admission.acquire(self.ledger, 'unity_slot', owner='pool', host='test', single_only=True)
        self.pool.prepare_integration(lease)
        self.ledger.set_slot_state('unity_slot:1', 'interactive_busy')
        resource_ready(self.ledger, lease)
        self.ledger.set_slot_state(companion(self.ledger, lease)['resource'], 'held')
        result = resource_status(self.ledger, item, token)
        self.assertEqual(result['state'], 'held')
        self.assertFalse(result['ready'])


class RuntimeTests(unittest.TestCase):
    def resource(self, root):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        source = root / 'server source 测试 with spaces'
        source.mkdir()
        code = ('import socket,sys,time; s=socket.socket(); s.bind((sys.argv[1],int(sys.argv[2]))); '
                's.listen(); time.sleep(30)')
        p = {**plan(), 'server_argv':[sys.executable,'-c',code,'{host}','{port}']}
        return {'commit':'a'*40, 'instance':'owned', 'mcp_address':'http://localhost:9999',
                'integration':{'commit':'b'*40, 'plan':p, 'source':str(source), 'game_port':port,
                               'environment':{}, 'expires_at':time.time()+15}}

    def test_real_loopback_pair_records_both_commits_and_cleans_children(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resource = self.resource(root)
            code = ('import os,socket; s=socket.create_connection((os.environ["FARMBOT_GAME_HOST"],'
                    'int(os.environ["FARMBOT_GAME_PORT"]))); s.close()')
            result = execute(resource, [sys.executable,'-c',code], root / 'run')
            self.assertEqual(result['status'], 'passed', result)
            self.assertTrue(result['teardown'])
            self.assertEqual((result['client_commit'], result['server_commit']), ('a'*40, 'b'*40))
            self.assertIsNotNone(result['ready_at'])
            with self.assertRaises(OSError):
                socket.create_connection(('127.0.0.1', resource['integration']['game_port']), timeout=.1)

    def test_failed_client_and_cancel_both_stop_game_server(self):
        for stop in (False, True):
            with self.subTest(stop=stop), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                resource = self.resource(root)
                def check():
                    if stop and (root / 'run' / 'client.log').exists():
                        raise LedgerError('claim revoked by Stop')
                code = 'import time; time.sleep(30)' if stop else 'raise SystemExit(2)'
                result = execute(resource, [sys.executable,'-c',code], root / 'run', check=check)
                self.assertEqual(result['status'], 'failed')
                self.assertTrue(result['teardown'], result)

    def test_native_server_descendants_are_stopped_with_their_owned_command(self):
        from agent.launcher import Launcher
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resource = self.resource(root)
            prefix = ('import subprocess,os,pathlib; p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"]); '
                      'pathlib.Path(os.environ["FARMBOT_INTEGRATION_DIR"],"child.pid").write_text(str(p.pid)); ')
            argv = resource['integration']['plan']['server_argv']
            argv[2] = argv[2].replace('s=socket.socket();', prefix + 's=socket.socket();')
            result = execute(resource, [sys.executable,'-c','pass'], root / 'run')
            self.assertTrue(result['teardown'], result)
            child = int((root / 'run' / 'child.pid').read_text())
            if os.name=='nt':
                self.assertFalse(Launcher.alive(child))
            else:
                status = subprocess.run(['ps','-p',str(child),'-o','stat='], capture_output=True, text=True)
                self.assertTrue(status.returncode or status.stdout.strip().startswith('Z'))

    def test_startup_failure_never_runs_client(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resource = self.resource(root)
            resource['integration']['plan']['server_argv'] = [sys.executable,'-c','raise SystemExit(3)']
            result = execute(resource, [sys.executable,'-c','raise SystemExit(99)'], root / 'run')
            self.assertEqual(result['status'], 'failed')
            self.assertFalse((root / 'run' / 'client.log').exists())
            self.assertTrue(result['teardown'])

    def test_invalid_plans_cannot_select_another_endpoint_or_skip_scenarios(self):
        for changed in ({'server_commit':'main'}, {'server_argv':['server','https://live']}, {'scenarios':[]},
                        {'startup_seconds':True}, {'server_argv':['server','{host}','{port}','{other}']}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                validate_plan({**plan(), **changed})
