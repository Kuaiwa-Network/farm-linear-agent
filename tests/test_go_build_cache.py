import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent import go_build_cache as cache
from agent.launcher import Launcher, RUNTIMES

GO = os.environ.get('GO_BUILD_CACHE_TEST_GO') or shutil.which('go')


class SeedTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='seed 空间 ')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        # Production refuses sandbox-writable temporary roots. Fixtures are
        # deliberately temporary; the separate boundary test checks that refusal.
        self.temps = patch.object(cache, 'worker_temp_roots', return_value=[])
        self.temps.start()
        self.addCleanup(self.temps.stop)
        go = self.root / 'go'
        go.write_bytes(b'pinned Go')
        self.config = SimpleNamespace(local_root=self.root / 'state', instance_id='test',
            expected_app_user_id='app', repos={'farm-hive': 'fixture'}, go_build_cache={},
            go_dependencies={'go': str(go), 'go_sha256': cache.fingerprint(go),
                             'cache_root': str(self.root / 'modules'), 'work_root': str(self.root / 'prep')})
        self.source = self.root / 'operator cache'
        self.name = 'aa/' + 'a' * 64 + '-d'
        path = self.source / self.name
        path.parent.mkdir(parents=True)
        path.write_bytes(b'compiled archive')
        (self.source / 'README').write_bytes(b'Go metadata')
        self.provenance = {'repo': 'farm-hive', 'commit': 'b' * 40, 'packages': ['./server']}

    def seed(self):
        self.config.go_build_cache = cache.publish(self.config, self.source, self.root / 'sealed', self.provenance)
        return cache.load(self.config, check_files=True)

    def rewrite_manifest(self, change):
        path = Path(self.config.go_build_cache['manifest'])
        data = json.loads(path.read_text(encoding='utf-8'))
        change(data)
        path.write_text(json.dumps(data), encoding='utf-8')
        self.config.go_build_cache['sha256'] = cache.fingerprint(path)

    def test_issues_receive_private_copies_and_never_promote_their_mutations(self):
        seed = self.seed()
        one, two = self.root / 'state/one', self.root / 'state/two'
        one.mkdir(parents=True); two.mkdir()
        first = seed.populate(one)
        self.assertEqual(first['copied_files'], 1)
        self.assertEqual(seed.populate(one)['copied_files'], 0)
        (one / self.name).write_bytes(b'worker modification')
        seed.populate(two)
        self.assertEqual((two / self.name).read_bytes(), b'compiled archive')
        self.assertEqual((seed.manifest.parent / 'cache' / self.name).read_bytes(), b'compiled archive')
        self.assertFalse(os.path.samefile(one / self.name, two / self.name))
        self.assertEqual((two / self.name).stat().st_nlink, 1)
        (two / self.name).unlink()
        self.assertEqual(seed.populate(two)['copied_files'], 1)  # Retention/partial imports recover.

    def test_bad_shared_bytes_never_publish_a_partial_private_artifact(self):
        seed = self.seed()
        target = self.root / 'private'
        target.mkdir()
        source = seed.manifest.parent / 'cache' / self.name
        source.write_bytes(b'x' * len(b'compiled archive'))
        with self.assertRaisesRegex(ValueError, 'changed during copy'):
            seed.populate(target)
        self.assertFalse((target / self.name).exists())
        self.assertEqual(list((target / 'aa').iterdir()), [])

    def test_changed_manifest_and_toolchain_are_refused(self):
        seed = self.seed()
        seed.manifest.write_bytes(b'{}')
        with self.assertRaisesRegex(ValueError, 'manifest changed'):
            cache.load(self.config)
        target = self.root / 'private'; target.mkdir()
        with self.assertRaisesRegex(ValueError, 'manifest changed'):
            seed.populate(target)

    def test_toolchain_and_owner_must_match(self):
        self.seed()
        self.config.instance_id = 'another'
        with self.assertRaisesRegex(ValueError, 'another instance'):
            cache.load(self.config)
        self.config.instance_id = 'test'
        Path(self.config.go_dependencies['go']).write_bytes(b'new Go')
        with self.assertRaisesRegex(ValueError, 'toolchain changed'):
            cache.load(self.config)

    def test_manifest_cannot_escape_seed_root(self):
        self.seed()
        self.rewrite_manifest(lambda d: d['files'].update({'../outside': {'size': 1, 'sha256': 'f' * 64}}))
        with self.assertRaisesRegex(ValueError, 'invalid entry'):
            cache.load(self.config)

    def test_worker_cache_and_sandbox_temp_cannot_be_published_or_configured(self):
        with self.assertRaisesRegex(ValueError, 'outside worker'):
            cache.publish(self.config, self.config.local_root / 'cache', self.root / 'sealed', self.provenance)
        with patch.object(cache, 'worker_temp_roots', return_value=[self.root]):
            with self.assertRaisesRegex(ValueError, 'outside worker'):
                cache.publish(self.config, self.source, self.root / 'sealed', self.provenance)
        block = {'manifest': str(self.config.local_root / 'seed/manifest.json'), 'sha256': 'a' * 64}
        with self.assertRaisesRegex(ValueError, 'outside worker'):
            cache.validate_settings(block, self.config.local_root, self.config.go_dependencies)
        block['manifest'] = str(self.root / 'modules/seed/manifest.json')
        with self.assertRaisesRegex(ValueError, 'separate'):
            cache.validate_settings(block, self.config.local_root, self.config.go_dependencies)

    def test_hardlinked_seed_is_rejected(self):
        seed = self.seed()
        source = seed.manifest.parent / 'cache' / self.name
        os.link(source, self.root / 'external')
        with self.assertRaisesRegex(ValueError, 'unlinked regular'):
            cache.load(self.config, check_files=True)

    def test_service_refuses_bad_seed_before_linear_or_ledger(self):
        from agent.config import Config
        from agent.service import build
        from test_go_dependencies import settings
        dependencies = settings(self.root).go_dependencies
        config = Config('client', 'secret', 'webhook', runtime='fake', local_root=self.root / 'state',
            repos={'farm-hive': 'fixture'}, go_dependencies=dependencies,
            go_build_cache={'manifest': str(self.root / 'missing/manifest.json'), 'sha256': 'a' * 64})
        with patch('agent.service.validate_runtime'), patch('agent.service.check_ownership'), \
                patch('agent.go_dependencies.ensure'), patch('agent.service.linear_api') as api, \
                patch('agent.service.Ledger') as ledger:
            with self.assertRaises(FileNotFoundError):
                build(config)
        api.assert_not_called()
        ledger.assert_not_called()

    def link_directory(self, target, link):
        if os.name == 'nt':
            subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)], check=True, capture_output=True)
        else:
            link.symlink_to(target, target_is_directory=True)

    def test_linked_destination_and_seed_ancestors_are_rejected(self):
        seed = self.seed()
        private = self.root / 'private'; private.mkdir()
        other = self.root / 'outside'; other.mkdir()
        self.link_directory(other, private / 'aa')
        with self.assertRaisesRegex(ValueError, 'links'):
            seed.populate(private)
        self.assertEqual(list(other.iterdir()), [])
        cache_dir = seed.manifest.parent / 'cache'
        moved = self.root / 'moved'; cache_dir.rename(moved)
        self.link_directory(moved, cache_dir)
        with self.assertRaisesRegex(ValueError, 'links'):
            cache.load(self.config, check_files=True)

    def test_launcher_uses_seed_and_trimpath_without_sharing_writes_or_scratch(self):
        seed = self.seed()
        code = 'import json,os; print(json.dumps(dict((k,os.environ.get(k)) for k in ("GOCACHE","GOTMPDIR","GOFLAGS","GOCACHEPROG"))))'
        launcher = Launcher(self.config.local_root / 'runs',
            RUNTIMES['fake']._replace(command=[sys.executable, '-c', code]), 'test', go_build_cache=seed)
        values = []
        for item, enabled in [('one', True), ('two', True), ('chat', False)]:
            handle = launcher.spawn(item, 'prompt', {}, 30, self.root, seed_go_cache=enabled,
                extra_env={'GOFLAGS': '-p=1 -trimpath=false', 'GOCACHEPROG': 'unwanted'})
            deadline = time.monotonic() + 10
            while launcher.running() and time.monotonic() < deadline:
                launcher.poll(); time.sleep(.02)
            self.assertFalse(launcher.running())
            env = json.loads((handle.run_dir / 'stdout.log').read_text(encoding='utf-8'))
            self.assertEqual((Path(env['GOCACHE']) / self.name).exists(), enabled)
            self.assertEqual((handle.run_dir / 'go-cache-seed.json').exists(), enabled)
            if enabled:
                self.assertEqual(env['GOFLAGS'], '-p=1 -trimpath=false -trimpath')
                self.assertIsNone(env['GOCACHEPROG'])
            values.append(env)
        self.assertNotEqual(values[0]['GOCACHE'], values[1]['GOCACHE'])
        self.assertNotEqual(values[0]['GOTMPDIR'], values[1]['GOTMPDIR'])

    @unittest.skipUnless(GO, 'native Go compiler unavailable')
    def test_real_go_reuses_compilation_in_another_checkout_and_invalidates_changed_source(self):
        from agent.go_cache_process import run
        go = str(Path(GO).resolve())
        self.config.go_dependencies.update(go=go, go_sha256=cache.fingerprint(go))
        self.source = self.root / 'warm-cache'
        self.source.mkdir()
        one, two = self.root / 'checkout one', self.root / 'checkout two'
        for root in (one, two):
            (root / 'pkg').mkdir(parents=True)
            (root / 'go.mod').write_text('module example.invalid/cachefixture\n\ngo 1.20\n', encoding='utf-8')
            (root / 'pkg/value.go').write_text('package fixture\nfunc Value() int { return 1 }\n', encoding='utf-8')
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(('GO', 'CGO_'))}
        env.update(GOENV='off', GOWORK='off', GOTOOLCHAIN='local', CGO_ENABLED='0',
                   GOPROXY='off', GOSUMDB='off', GOVCS='*:off', GONOPROXY='none',
                   GOMODCACHE=str(self.root / 'modules'))
        def build(root, cache_path, label):
            log = self.root / label
            run([go, 'build', '-x', '-trimpath', '-p=1', './pkg'], cwd=root,
                env={**env, 'GOCACHE': str(cache_path)}, log=log, timeout=60)
            output = Path(str(log) + '.err').read_text(encoding='utf-8')
            return bool(re.search(r'[/\\]compile(?:\.exe)?[" ]', output))
        self.assertTrue(build(one, self.source, 'cold'))
        seed = self.seed()
        private = self.root / 'private'; private.mkdir()
        self.assertGreater(seed.populate(private)['copied_files'], 0)
        self.assertFalse(build(two, private, 'warm'), 'identical source in another checkout rebuilt')
        (two / 'pkg/value.go').write_text('package fixture\nfunc Value() int { return 2 }\n', encoding='utf-8')
        self.assertTrue(build(two, private, 'changed'), 'changed source reused stale compilation')


if __name__ == '__main__':
    unittest.main()
