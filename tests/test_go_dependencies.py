import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent import go_dependencies as dep
from agent.go_cache_process import _drain_posix, run as native_run
from agent.ledger import LedgerError
from agent.worktrees import Worktrees, WorktreeError
from agent.workflow import JobWorktrees
from test_ledger import LedgerBase
from test_worktrees import git


def settings(root):
    tools = root/'tools and 中文'; tools.mkdir()
    for name in ('go', 'git', 'gh'): (tools/name).write_bytes(name.encode())
    return SimpleNamespace(local_root=root/'state', instance_id='test-cache', expected_app_user_id='test-app',
        repos={'farm-hive':'fixture'}, go_dependencies={
            'go':str(tools/'go'), 'go_sha256':dep.digest(b'go'), 'git':str(tools/'git'), 'gh':str(tools/'gh'),
            'cache_root':str(root/'cache'), 'work_root':str(root/'preparation'),
            'modules':{'farm-hive':['.']}, 'private_prefixes':['github.com/Kuaiwa-Network']})


class Fixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve(); self.config = settings(self.root)


class StorageTests(Fixture):
    def test_explicit_adoption_and_instance_ownership(self):
        cache = Path(self.config.go_dependencies['cache_root']); cache.mkdir()
        (cache/'previous').write_text('preserved')
        with self.assertRaisesRegex(ValueError, 'adoption'): dep.initialize(self.config)
        dep.initialize(self.config, adopt_existing=True)
        dep.roots(self.config)
        self.assertEqual((cache/'previous').read_text(), 'preserved')
        self.config.instance_id = 'another-host'
        with self.assertRaisesRegex(ValueError, 'ownership'): dep.roots(self.config)

    def test_rejects_worker_roots_ancestor_and_shared_storage(self):
        for key, value in (('cache_root', self.config.local_root/'cache'),
                           ('cache_root', self.root), ('work_root', self.root/'cache')):
            block = dict(self.config.go_dependencies); block[key] = str(value)
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                dep.validate_settings(block, self.config.local_root, self.config.repos)

    def test_no_install_or_changed_tool(self):
        dep.initialize(self.config)
        Path(self.config.go_dependencies['go']).write_bytes(b'new executable')
        with self.assertRaisesRegex(ValueError, 'digest'): dep.roots(self.config)

    def test_storage_cannot_be_shared_by_same_app_at_another_state_root(self):
        dep.initialize(self.config)
        self.config.local_root=self.root/'different-state'
        with self.assertRaisesRegex(ValueError,'ownership'): dep.roots(self.config)

    def test_no_module_path_or_private_prefix_injection(self):
        for value in ('../foreign', '/foreign', 'designer/../foreign', 'dir;cmd', 'a\\b'):
            with self.subTest(value=value), self.assertRaises(ValueError): dep.module_path(value)
        block = dict(self.config.go_dependencies, private_prefixes=['https://evil.invalid'])
        with self.assertRaises(ValueError): dep.validate_settings(block, self.config.local_root, self.config.repos)

    def test_sandbox_environment_stays_offline_and_private(self):
        with patch.dict(os.environ, {'GOFLAGS':'evil', 'GH_TOKEN':'SECRET', 'FARMBOT_CONFIG':'SECRET'}, clear=False):
            env = dep.environment(self.config.go_dependencies, self.root, online=False)
        self.assertEqual((env['GOPROXY'], env['GOSUMDB'], env['GOTOOLCHAIN']), ('off','off','local'))
        self.assertEqual(env['GOFLAGS'], '-mod=readonly')
        self.assertNotIn('GH_TOKEN', env); self.assertNotIn('FARMBOT_CONFIG', env)
        self.assertEqual(env['GOVCS'], '*:off')
        self.assertEqual(env['GONOPROXY'], 'none')


class PreparationTests(Fixture):
    def setUp(self):
        super().setUp(); dep.initialize(self.config)
        self.values = {'go.mod':b'module fixture\n\ngo 1.25.1\n', 'go.sum':b''}
        self.calls = []; self.inject_replace = self.inject_failure = self.inject_input_write = False

    def runner(self, argv, *, cwd, env, log, **options):
        label = Path(log).name; self.calls.append((label, dict(env)))
        if label == 'parse':
            return json.dumps({'Replace':[{'New':{'Path':'../foreign'}}]} if self.inject_replace else {}).encode()
        if label == 'download':
            if self.inject_failure: raise RuntimeError('SECRET token from fake downloader')
            cache = Path(env['GOMODCACHE'])
            source = cache/'example.org/m@v1.0.0'; source.mkdir(parents=True)
            (source/'go.mod').write_text('module example.org/m\n')
            (source/'source.go').write_text('package m\n')
            archive = cache/'cache/download/example.org/m/@v'; archive.mkdir(parents=True, exist_ok=True)
            for suffix in ('info','mod','zip','ziphash','lock'):
                (archive/('v1.0.0.'+suffix)).write_bytes(b'bytes' if suffix != 'lock' else b'')
            (archive/'list').write_text('v1.0.0\n')
            (Path(cwd)/'go.sum').write_bytes(b'disposable supplements\n')
            return json.dumps({'Dir':str(source)}).encode()
        if self.inject_input_write and label == 'offline-verify':
            (Path(cwd)/'go.mod').write_bytes(b'mutated')
        return b'{}' if 'graph' in label else b'all modules verified\n'

    def test_expands_missing_source_and_preserves_committed_inputs(self):
        cache = Path(self.config.go_dependencies['cache_root'])
        module = cache/'example.org/m@v1.0.0'; module.mkdir(parents=True)
        (module/'source.go').write_text('package m\n')
        result = dep.prepare(self.config, self.values, runner=self.runner)
        self.assertEqual(result['state'], 'ready')
        self.assertTrue((module/'go.mod').is_file())
        self.assertEqual((module/'source.go').read_text(), 'package m\n')
        self.assertEqual(result['inputs'], {n:dep.digest(v) for n,v in self.values.items()})
        for label, env in self.calls:
            self.assertEqual(env['GOPROXY'], 'https://proxy.golang.org' if label in ('parse','download','online-graph') else 'off')
        self.assertFalse(any('tool' in label or 'build' in label or 'install' in label for label,_ in self.calls))

    def test_existing_version_index_is_preserved(self):
        cache = Path(self.config.go_dependencies['cache_root'])
        path = cache/'cache/download/example.org/m/@v/list'; path.parent.mkdir(parents=True)
        path.write_text('v0.9.0\n')
        dep.prepare(self.config, self.values, runner=self.runner)
        self.assertEqual(path.read_text(), 'v0.9.0\n')

    def test_warm_cache_is_proved_offline_without_download(self):
        cache=Path(self.config.go_dependencies['cache_root'])
        folder=cache/'example.org/m@v1.0.0'; folder.mkdir(parents=True)
        (folder/'go.mod').write_text('module example.org/m\n')
        normal=self.runner
        def runner(argv, **options):
            if Path(options['log']).name=='warm-graph':
                self.calls.append(('warm-graph',dict(options['env'])))
                return json.dumps({'Dir':str(folder)}).encode()
            return normal(argv,**options)
        result=dep.prepare(self.config,self.values,runner=runner)
        self.assertEqual(result['added_files'],0)
        self.assertEqual([c[0] for c in self.calls],['parse','warm-graph','warm-verify'])

    def test_warm_graph_only_module_without_dir_uses_escaped_cache_path(self):
        cache=Path(self.config.go_dependencies['cache_root'])
        folder=cache/'example.org/!upper/m@v1.0.0'; folder.mkdir(parents=True)
        (folder/'go.mod').write_text('module example.org/Upper/m\n')
        self.assertTrue(dep.complete_source({'Path':'example.org/Upper/m','Version':'v1.0.0'},cache))

    def test_foreign_cache_bytes_are_not_overwritten(self):
        path = Path(self.config.go_dependencies['cache_root'])/'example.org/m@v1.0.0/source.go'
        path.parent.mkdir(parents=True); path.write_text('different')
        with self.assertRaisesRegex(RuntimeError, 'differs'): dep.prepare(self.config, self.values, runner=self.runner)
        self.assertEqual(path.read_text(), 'different')

    def test_no_local_replace_execution_or_failed_download_readiness(self):
        self.inject_replace = True
        with self.assertRaisesRegex(RuntimeError, 'Local Go'): dep.prepare(self.config, self.values, runner=self.runner)
        self.assertEqual([c[0] for c in self.calls], ['parse'])
        self.inject_replace = False; self.inject_failure = True
        with self.assertRaises(RuntimeError): dep.prepare(self.config, self.values, runner=self.runner)
        self.assertFalse(list(Path(self.config.go_dependencies['work_root']).glob('*/receipt.json')))

    def test_mutated_offline_inputs_never_become_ready(self):
        self.inject_input_write = True
        with self.assertRaisesRegex(RuntimeError, 'inputs changed'): dep.prepare(self.config, self.values, runner=self.runner)
        self.assertFalse(list(Path(self.config.go_dependencies['work_root']).glob('*/receipt.json')))

    def test_cancellation_prevents_publication(self):
        def cancelled(): return any(label == 'offline-verify' for label,_ in self.calls)
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            dep.prepare(self.config, self.values, runner=self.runner, cancelled=cancelled)
        self.assertFalse((Path(self.config.go_dependencies['cache_root'])/'example.org').exists())

    def test_legacy_module_without_archived_go_mod_is_valid(self):
        normal = self.runner
        def runner(argv, **options):
            value = normal(argv, **options)
            if Path(options['log']).name != 'download': return value
            result = json.loads(value); folder = Path(result['Dir']); (folder/'go.mod').unlink()
            archive = folder.parent/'legacy.zip'
            with zipfile.ZipFile(archive,'w') as stream: stream.writestr('example.org/m@v1.0.0/source.go','package m\n')
            result.update(Zip=str(archive),Path='example.org/m',Version='v1.0.0')
            return json.dumps(result).encode()
        self.assertEqual(dep.prepare(self.config,self.values,runner=runner)['state'],'ready')

    def test_missing_archived_go_mod_is_rejected(self):
        normal = self.runner
        def runner(argv, **options):
            value = normal(argv, **options)
            if Path(options['log']).name != 'download': return value
            result = json.loads(value); folder = Path(result['Dir']); (folder/'go.mod').unlink()
            archive = folder.parent/'modern.zip'
            with zipfile.ZipFile(archive,'w') as stream: stream.writestr('example.org/m@v1.0.0/go.mod','module example.org/m\n')
            result.update(Zip=str(archive),Path='example.org/m',Version='v1.0.0')
            return json.dumps(result).encode()
        with self.assertRaisesRegex(RuntimeError,'missing its archived'):
            dep.prepare(self.config,self.values,runner=runner)


class RequestTests(LedgerBase):
    def setUp(self):
        super().setUp()
        self.root = Path(self.tmp.name).resolve(); self.config = settings(self.root)
        dep.initialize(self.config)
        item = self.new_item(); self.item_id = item['id']
        self.ledger.connection.execute("UPDATE work_items SET root_repo='farm-hive' WHERE id=?", (self.item_id,))
        self.token = self.ledger.claim(self.item_id, worker_id='worker')['token']
        source = self.root/'origin'; source.mkdir(); git('init','-b','main',cwd=source)
        self.inputs = {'go.mod':b'module fixture\n\ngo 1.25.1\n', 'go.sum':b''}
        # Exercise Windows-style checkout conversion on every host, independent
        # of its Git settings. Committed dependency inputs retain their LF bytes.
        (source/'.gitattributes').write_bytes(b'go.mod text eol=crlf\n')
        for name, raw in self.inputs.items(): (source/name).write_bytes(raw)
        git('add','.',cwd=source); git('commit','-qm','inputs',cwd=source)
        self.trees = Worktrees(self.root/'clones', self.root/'worktrees', {'farm-hive':str(source)})
        self.path = self.trees.add('farm-hive', self.item_id, 'farmbot/farm-1')
        self.head = self.trees.head(self.path)

    def request(self, **options):
        return dep.request(self.ledger, JobWorktrees(self.trees,self.ledger), self.config,
                           self.item_id, self.token, '.', self.head, **options)

    def controller(self, preparer):
        return dep.Controller(self.ledger, self.trees, self.config, preparer=preparer)

    def test_real_owned_snapshot_ready_and_idempotent(self):
        self.assertEqual((self.path/'go.mod').read_bytes(), self.inputs['go.mod'].replace(b'\n', b'\r\n'))
        result = self.request(); self.assertEqual(result['state'], 'pending')
        self.assertEqual(self.request()['id'], result['id'])
        preparer = Mock(return_value={'state':'ready','cache_root':'fixture'})
        control = self.controller(preparer)
        self.assertTrue(control.tick()); self.assertFalse(control.tick())
        self.assertEqual(self.request()['state'], 'ready')
        self.assertEqual(preparer.call_args.args[1], self.inputs)
        self.assertEqual(self.ledger.item(self.item_id)['state'], 'running')
        self.assertIsNone(self.ledger.human_question(self.item_id))

    def test_dirty_candidate_and_wrong_claim_are_refused(self):
        (self.path/'uncommitted').write_text('keep')
        with self.assertRaises(WorktreeError): self.request()
        with self.assertRaises(LedgerError):
            dep.request(self.ledger, JobWorktrees(self.trees,self.ledger), self.config,
                        self.item_id, 'wrong', '.', self.head)
        self.assertEqual((self.path/'uncommitted').read_text(), 'keep')

    def test_stop_rejects_request_and_cancels_queued_preparation(self):
        result = self.request()
        self.ledger.connection.execute("UPDATE work_items SET state='cancelled' WHERE id=?", (self.item_id,))
        preparer = Mock(); self.assertTrue(self.controller(preparer).tick()); preparer.assert_not_called()
        self.assertEqual(dep.status(self.ledger,self.config,self.item_id,result['id'])['state'],'cancelled')
        with self.assertRaises(LedgerError): self.request()

    def test_cancel_during_preparation_never_records_ready(self):
        result = self.request()
        def stop(config, values, *, cancelled):
            self.ledger.connection.execute("UPDATE work_items SET state='cancelled' WHERE id=?", (self.item_id,))
            self.assertTrue(cancelled()); return {'state':'ready'}
        self.controller(stop).tick()
        self.assertEqual(dep.status(self.ledger,self.config,self.item_id,result['id'])['state'],'cancelled')

    def test_new_claim_in_same_generation_fences_old_preparation(self):
        result=self.request()
        self.ledger.connection.execute("UPDATE work_items SET token='new-claim-digest' WHERE id=?",(self.item_id,))
        preparer=Mock(); self.controller(preparer).tick(); preparer.assert_not_called()
        self.assertEqual(dep.status(self.ledger,self.config,self.item_id,result['id'])['state'],'cancelled')

    def test_failure_redaction_and_bounded_corrected_retry(self):
        preparer = Mock(side_effect=RuntimeError('SECRET token and signed URL'))
        for number in range(3):
            result = self.request(retry=number>0); self.controller(preparer).tick()
            failed = dep.status(self.ledger,self.config,self.item_id,result['id'])
            self.assertEqual(failed['state'],'failed'); self.assertNotIn('SECRET',json.dumps(failed))
        with self.assertRaisesRegex(LedgerError,'retry limit'): self.request(retry=True)

    def test_controller_refuses_candidate_changed_after_request(self):
        result = self.request()
        (self.path/'go.mod').write_text('changed')
        preparer = Mock(); self.controller(preparer).tick(); preparer.assert_not_called()
        self.assertEqual(dep.status(self.ledger,self.config,self.item_id,result['id'])['state'],'failed')

    def test_host_settings_change_invalidates_request_and_cached_readiness(self):
        original=self.request()
        self.config.go_dependencies['go_sha256']='a'*64
        preparer=Mock(); self.controller(preparer).tick(); preparer.assert_not_called()
        self.assertEqual(dep.status(self.ledger,self.config,self.item_id,original['id'])['state'],'failed')
        self.assertNotEqual(self.request()['id'],original['id'])

    def test_poll_does_not_return_readiness_for_previous_host_settings(self):
        original=self.request(); self.controller(Mock(return_value={'state':'ready'})).tick()
        self.config.go_dependencies['go_sha256']='a'*64
        self.assertEqual(dep.status(self.ledger,self.config,self.item_id,original['id'])['state'],'cancelled')

    def test_authenticated_worker_cli_request_and_poll(self):
        from agent.__main__ import parser, run
        self.config.repos['farm-hive']=str(self.root/'origin')
        db=self.root/'ledger.sqlite3'
        paths=SimpleNamespace(ledger=db,repos=self.root/'clones',worktrees=self.root/'worktrees')
        args=parser().parse_args(['--db',str(db),'prepare-go-dependencies','--item',self.item_id,
                                 '--token',self.token,'--module','.','--commit',self.head])
        with patch('agent.__main__.load_config',return_value=self.config), patch('agent.__main__.Paths',return_value=paths):
            result=run(args,self.ledger,Mock())
            poll=parser().parse_args(['--db',str(db),'go-dependencies','--item',self.item_id,
                                     '--token',self.token,'--request-id',result['id']])
            self.assertEqual(run(poll,self.ledger,Mock())['state'],'pending')


class ProcessTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup); self.root = Path(tmp.name).resolve()

    def test_native_contained_process_success(self):
        data = native_run([sys.executable,'-c','print("native owned")'], cwd=self.root, env=dict(os.environ), log=self.root/'native')
        self.assertEqual(data.strip(), b'native owned')

    def test_native_timeout_drains_process(self):
        with self.assertRaisesRegex(RuntimeError,'timed out'):
            native_run([sys.executable,'-c','import time; time.sleep(60)'], cwd=self.root,
                       env=dict(os.environ),log=self.root/'timeout',timeout=.1)

    def test_cancelled_before_spawn(self):
        with patch('agent.go_cache_process.subprocess.Popen') as spawn, self.assertRaises(RuntimeError):
            native_run([sys.executable],cwd=self.root,env={},log=self.root/'cancel',cancelled=lambda:True)
        spawn.assert_not_called()


class PosixProcessTests(unittest.TestCase):
    def setUp(self):
        # Exercise the POSIX cleanup branches on Windows without native signals.
        sigkill = patch('agent.go_cache_process.signal.SIGKILL', 9, create=True)
        sigkill.start()
        self.addCleanup(sigkill.stop)

    def test_posix_zombie_permission_error_requires_group_absence_after_reap(self):
        process = Mock(pid=123, poll=Mock(return_value=0))
        def signal_group(pid, sig):
            self.assertEqual(pid, process.pid)
            if sig:
                process.wait.assert_not_called()
                raise PermissionError()
            process.wait.assert_called_once_with(timeout=5)
            raise ProcessLookupError()
        with patch('agent.go_cache_process.os.killpg', side_effect=signal_group, create=True):
            _drain_posix(process)
        process.kill.assert_not_called()

    def test_posix_denied_or_present_group_never_certifies_drain(self):
        for probe in (PermissionError(), None):
            with self.subTest(probe=type(probe).__name__):
                process = Mock(pid=123, poll=Mock(return_value=None))
                with patch('agent.go_cache_process.os.killpg', side_effect=[PermissionError(), probe], create=True), \
                        patch('agent.go_cache_process.time.monotonic', side_effect=[0, 6]), \
                        self.assertRaisesRegex(RuntimeError, 'did not drain'):
                    _drain_posix(process)
                process.kill.assert_called_once_with()
                process.wait.assert_called_once_with(timeout=5)

    def test_posix_zombie_probe_is_retried_until_group_absent(self):
        process = Mock(pid=123, poll=Mock(return_value=0))
        with patch('agent.go_cache_process.os.killpg',
                   side_effect=[PermissionError(), PermissionError(), ProcessLookupError()], create=True) as killpg, \
                patch('agent.go_cache_process.time.sleep'):
            _drain_posix(process)
        self.assertEqual(killpg.call_count, 3)

    def test_posix_unexpected_signal_error_reaps_child_and_fails(self):
        process = Mock(pid=123, poll=Mock(return_value=None))
        with patch('agent.go_cache_process.os.killpg', side_effect=OSError('unexpected'), create=True), \
                self.assertRaisesRegex(OSError, 'unexpected'):
            _drain_posix(process)
        process.kill.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=5)
