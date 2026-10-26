"""Owned resource bridges and recovery with local state, never a live Editor/Docker."""
import argparse
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from agent.execution import queue_resource, resource_status
from agent.ledger import LedgerError
from agent import worker_resources
import test_single_worker as single_fixture
import test_server_slots as server_fixture
import test_worktrees as git_fixture


class UnityBridgeTests(unittest.TestCase):
    def setUp(self):
        self.f = single_fixture.SingleWorkerTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.claim_owner()
        self.grant = self.f.reservation()
        self.args = argparse.Namespace(item=self.f.key, db=str(self.f.path), command='unity-tool',
            name='run_tests', input=str(Path(self.f.tmp.name) / 'input.json'))
        Path(self.args.input).write_text('{"mode":"EditMode"}', encoding='utf-8')
        self.paths = Mock(ledger=self.f.path, runs=Path(self.f.tmp.name) / 'runs')
        self.paths_patch = patch('agent.worker_resources.Paths', return_value=self.paths)
        self.paths_patch.start()
        self.addCleanup(self.paths_patch.stop)

    def test_tool_uses_owned_instance_and_preserves_raw_results(self):
        client = Mock()
        client._rpc.return_value = {'content': [{'type': 'text', 'text': 'job-123'}]}
        factory = Mock(return_value=client)
        result = worker_resources.unity(self.args, self.f.ledger, Mock(), self.f.token, client_factory=factory)
        client.select_instance.assert_called_once_with('instance-one')
        client._rpc.assert_called_once_with('tools/call', {'name': 'run_tests', 'arguments': {'mode': 'EditMode'}})
        self.assertEqual(result['content'][0]['text'], 'job-123')

    def test_stop_during_connect_cannot_issue_a_test(self):
        client = Mock()
        client.select_instance.side_effect = lambda instance: self.f.ledger.cancel(self.f.key, 'Stop')
        with self.assertRaises(LedgerError):
            worker_resources.unity(self.args, self.f.ledger, Mock(), self.f.token, client_factory=lambda *a, **k: client)
        client._rpc.assert_not_called()

    def test_release_and_instance_override_are_fenced(self):
        client = Mock()
        self.args.name = 'set_active_instance'
        with self.assertRaisesRegex(LedgerError, 'bound'):
            worker_resources.unity(self.args, self.f.ledger, Mock(), self.f.token, client_factory=lambda *a, **k: client)
        client._rpc.assert_not_called()
        self.f.ledger.release(self.grant['reservation_id'], self.grant['token'], 'worker reported quiescent')
        with self.assertRaisesRegex(LedgerError, 'not ready'):
            worker_resources.unity(self.args, self.f.ledger, Mock(), self.f.token, client_factory=lambda *a, **k: client)
        client._rpc.assert_not_called()

    def test_ambiguous_mutation_is_never_retried(self):
        client = Mock()
        client._rpc.side_effect = TimeoutError('response lost')
        with self.assertRaises(TimeoutError):
            worker_resources.unity(self.args, self.f.ledger, Mock(), self.f.token, client_factory=lambda *a, **k: client)
        self.assertEqual(client._rpc.call_count, 1)

    def result_command(self):
        self.args.command = 'unity-test-result'
        self.args.job_id = 'b' * 32
        client = Mock()
        client.call_tool.return_value = {'result': {'schema': 1, 'job_id': self.args.job_id,
                                                   'available': False, 'reason': 'result_not_retained'}}
        return client

    def test_retained_report_keeps_candidate_identity_and_does_not_release_or_rerun(self):
        client = self.result_command()
        result = worker_resources.unity(self.args, self.f.ledger, Mock(), self.f.token,
                                        client_factory=lambda *a, **k: client)
        self.assertEqual(result['reservation_id'], self.grant['reservation_id'])
        self.assertEqual(result['commit_sha'], 'a' * 40)
        self.assertEqual(result['instance'], 'instance-one')
        self.assertFalse(result['report']['available'])
        self.assertEqual(self.f.ledger.reservation(self.grant['reservation_id'])['state'], 'active')
        client.select_instance.assert_called_once_with('instance-one')
        self.assertEqual(client.call_tool.call_args.args[0], 'execute_code')
        client._rpc.assert_not_called()

    def test_retained_report_fences_stop_release_withdrawal_and_held_resource(self):
        for event in ('stop', 'release', 'withdraw', 'held', 'held-during-read'):
            with self.subTest(event=event):
                # Independent fixture for each transition, including the read's final fence.
                f = UnityBridgeTests()
                f.setUp()
                try:
                    client = f.result_command()
                    def change(*a, **kw):
                        if event == 'stop':
                            f.f.ledger.cancel(f.f.key, 'Stop')
                        elif event == 'release':
                            f.f.ledger.release(f.grant['reservation_id'], f.grant['token'], 'worker reported quiescent')
                        elif event == 'withdraw':
                            f.f.ledger.connection.execute('UPDATE work_items SET withdraw_deadline=? WHERE id=?',
                                                         (f.f.now + 60, f.f.key))
                        elif event == 'held-during-read':
                            f.f.ledger.hold(f.grant['reservation_id'], 'recovery during read')
                        return {'result': {'schema': 1, 'job_id': f.args.job_id, 'available': False}}
                    client.call_tool.side_effect = change
                    if event == 'held':
                        f.f.ledger.hold(f.grant['reservation_id'], 'recovery')
                    with self.assertRaises(LedgerError):
                        worker_resources.unity(f.args, f.f.ledger, Mock(), f.f.token,
                                               client_factory=lambda *a, **k: client)
                    if event == 'held':
                        client.call_tool.assert_not_called()
                finally:
                    f.doCleanups()


class ServerOwnerTests(unittest.TestCase):
    def test_docker_restart_preserves_live_claim_and_reservation(self):
        from agent.server_slots import DockerUnavailable
        f = self.f
        key, token, queued = self.request()
        f.exited = False
        before = f.ledger.item(key)
        f.services.error = DockerUnavailable('daemon offline')
        f.pool.tick()
        reservation = f.ledger.active_reservation(key)
        self.assertEqual(f.ledger.item(key)['state'], 'running')
        self.assertNotEqual(f.ledger.slot(reservation['resource'])['state'], 'held')
        f.services.error = None
        self.assertEqual(f.pool.tick()['granted'], 1)
        after = f.ledger.item(key)
        self.assertEqual((after['generation'], after['worker_pid'], after['checkpoint']),
                         (before['generation'], before['worker_pid'], before['checkpoint']))
        self.assertEqual(after['stage'], 'resource-ready')
        self.assertEqual(f.ledger.active_reservation(key)['reservation_id'], reservation['reservation_id'])

    def test_waiting_docker_cannot_start_during_maintenance_or_after_generation_change(self):
        from agent.server_slots import DockerUnavailable
        from agent.maintenance import Maintenance
        f = self.f
        key, token, queued = self.request()
        f.services.error = DockerUnavailable('daemon offline')
        f.pool.tick()
        f.services.error = None
        with patch.object(Maintenance, 'requested', return_value=True):
            self.assertEqual(f.pool.tick()['granted'], 0)
        self.assertEqual(len(f.services.started), 1)
        f.ledger.connection.execute('UPDATE work_items SET generation=generation+1 WHERE id=?', (key,))
        self.assertEqual(f.pool.tick()['granted'], 0)
        self.assertEqual(len(f.services.started), 1)
        self.assertEqual(f.ledger.slot(f.ledger.active_reservation(key)['resource'])['state'], 'held')

    def setUp(self):
        self.f = server_fixture.ServerPoolFixture()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.pool.single_only = True

    def request(self):
        f = self.f
        key = f.request(skill='work', reserve=False, execution_contract='single-worker-v1')
        f.ledger.set_worker(key, 123, 'test')
        token = f.ledger.claim(key, worker_id='same-owner')['token']
        queued = queue_resource(f.ledger, key, token, 'server_slot', 'batch', 'a'*40,
            test_plan={'packages': ['./breeding'], 'tests': ['TestCount'], 'race': False})
        return key, token, queued

    def test_services_are_granted_to_live_owner_and_cleanup_waits_for_quiescence(self):
        f = self.f
        legacy = f.request()
        key, token, queued = self.request()
        f.exited = False
        self.assertEqual(f.pool.tick()['granted'], 1)
        self.assertIsNone(f.ledger.active_reservation(legacy))
        status = resource_status(f.ledger, key, token)
        self.assertTrue(status['ready'])
        self.assertEqual(f.ledger.item(key)['worker_pid'], 123)
        reservation = f.ledger.active_reservation(key)
        auth = (f.root.parent / 'runs' / key / 'reservation.token').read_text()
        f.ledger.release(reservation['reservation_id'], auth, 'worker reported quiescent')
        self.assertEqual(f.pool.tick()['settled'], 0)
        self.assertEqual(f.services.cleaned, [])
        f.ledger.renew(key, token)
        f.exited = True
        self.assertEqual(f.pool.tick()['settled'], 1)
        self.assertEqual(len(f.services.cleaned), 1)

    def test_preparation_failure_is_reported_to_same_owner_without_new_claim(self):
        f = self.f
        key, token, _ = self.request()
        f.services.error = RuntimeError('service unavailable')
        self.assertEqual(f.pool.tick()['granted'], 0)
        self.assertEqual(f.ledger.renew(key, token)['stage'], 'resource-preparation-failed')
        status = resource_status(f.ledger, key, token)
        self.assertEqual(status['state'], 'held')
        self.assertFalse(status['ready'])


class CandidateRecoveryTests(unittest.TestCase):
    def test_successor_restores_unpublished_source_instead_of_moving_main(self):
        f = git_fixture.WorktreeTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        original = f.trees.add('Farm-Client', 'previous', 'farmbot/farm-1')
        (original / 'candidate.txt').write_text('retained implementation', encoding='utf-8')
        saved = f.trees.preserve('previous')
        sha = saved['committed']['Farm-Client']
        f.trees.remove_preserved('previous', saved)
        (f.origin / 'new-main.txt').write_text('unrelated main advance', encoding='utf-8')
        git_fixture.git('add', '.', cwd=f.origin)
        git_fixture.git('commit', '-qm', 'new main', cwd=f.origin)
        restored = f.trees.add('Farm-Client', 'next', 'farmbot/farm-1', recovery_from=['previous'])
        self.assertEqual(f.trees.head(restored), sha)
        self.assertEqual((restored / 'candidate.txt').read_text(encoding='utf-8'), 'retained implementation')
        self.assertFalse((restored / 'new-main.txt').exists())


class PublicationTests(unittest.TestCase):
    def test_original_candidate_can_publish_after_main_advanced_but_wrong_destination_cannot(self):
        import test_cli
        from agent.__main__ import run
        from agent.publication import PublicationError
        f = test_cli.CliTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        args, ledger, config, api, github = f.publication_fixture()
        ledger.connection.execute("UPDATE work_items SET execution_contract='single-worker-v1',skill='work',root_repo=NULL WHERE id=?", (args.item,))
        path = Path(config.local_root) / 'worktrees' / args.item / 'Farm-Client'
        candidate = git_fixture.git('rev-parse', 'HEAD', cwd=path)
        (f.root / 'origin' / 'unrelated.txt').write_text('main advanced', encoding='utf-8')
        git_fixture.git('add', '.', cwd=f.root / 'origin')
        git_fixture.git('commit', '-qm', 'advance', cwd=f.root / 'origin')
        with patch('agent.__main__.load_config', return_value=config), patch('agent.publication.github_api', side_effect=github):
            result = run(args, ledger, lambda: api)
            self.assertEqual(result['head'], candidate)
            self.assertEqual(git_fixture.git('rev-parse', 'HEAD', cwd=path), candidate)
            git_fixture.git('remote', 'set-url', '--push', 'origin', 'https://github.com/foreign/repo.git', cwd=path)
            with self.assertRaises(PublicationError):
                run(args, ledger, lambda: api)


class NativeUiTests(unittest.TestCase):
    def test_owner_exports_only_selected_packages_without_presentation_receipts(self):
        from types import SimpleNamespace
        from agent import worker_ui
        f = single_fixture.SingleWorkerTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        app = 'e5a8c16d-9f85-4123-acf5-94e41c3304d5'
        f.claim_owner(delegate_id=app)
        root = Path(f.tmp.name).resolve()
        paths = SimpleNamespace(repos=root/'repos', worktrees=root/'worktrees', runs=root/'runs')
        config = SimpleNamespace(repos={}, issue_prefix='FARM',
            fgui_export={'executable': 'pinned.exe', 'tool_sha256': {'pinned': 'a'*64}})
        trees = Mock()
        trees.git_in.return_value = 'farmbot/farm-1'
        trees.head.return_value = 'a'*40
        api = SimpleNamespace(app_user_id=app, fetch_issue=lambda _: f.ledger.issue(f.item['issue_id']))
        packages = {'AnimalBreeding': 'pkg1'}
        result = SimpleNamespace(evidence=lambda: {'published': True})
        def publish(*args, **kwargs):
            self.assertEqual(args[1], packages)
            self.assertEqual(kwargs['components'], ['AnimalBreeding/view'])
            kwargs['fence']()
            self.assertEqual(kwargs['verify_source']()['head'], 'a'*40)
            return result
        with patch('agent.worker_ui.Paths', return_value=paths), patch('agent.worker_ui.Worktrees', return_value=trees), \
                patch('agent.worker_ui.owned_source', return_value={'head': 'a'*40}), \
                patch('agent.worker_ui.publish', side_effect=publish) as publisher:
            trees.git_in.return_value = 'main'
            with self.assertRaises(LedgerError):
                worker_ui.export(f.ledger, f.key, f.token, config, lambda: api, packages)
            publisher.assert_not_called()
            trees.git_in.return_value = 'farmbot/farm-1'
            exported = worker_ui.export(f.ledger, f.key, f.token, config, lambda: api, packages,
                                        components=['AnimalBreeding/view'])
        self.assertEqual(publisher.call_count, 1)
        self.assertEqual(exported['packages'], packages)
        self.assertEqual(exported['result'], {'published': True})
        self.assertEqual(f.ledger.item(f.key)['state'], 'running')

    def test_publish_ui_cli_accepts_repeatable_component_scope(self):
        from agent.__main__ import parser
        args = parser().parse_args(['--db', 'unused', 'publish-ui', '--item', 'item', '--packages', 'packages.json',
                                   '--component', 'Guild/view', '--component', 'Guild/card'])
        self.assertEqual(args.component, ['Guild/view', 'Guild/card'])
