"""A selected config must survive child working directories and conflicting ambient state."""
import contextlib
import io
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent.config import linear_api, load_config
from agent.deploy import AGENTS
from agent.ledger import LedgerError
from agent.service import build, main
from test_ledger import ISSUE, issue


class ConfigPropagationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.origin = self.root / 'origin'
        self.origin.mkdir()
        for args in (('init', '-q', '-b', 'main'), ('commit', '--allow-empty', '-qm', 'fixture')):
            subprocess.run(['git', '-c', 'user.name=Test', '-c', 'user.email=test@localhost', *args],
                           cwd=self.origin, capture_output=True, check=True)
        stub = self.root / 'stub'
        stub.mkdir()
        (stub / 'issue.json').write_text(json.dumps(issue()), encoding='utf-8')
        patcher = patch.dict(os.environ, {'FARMBOT_LINEAR_STUB_DIR': str(stub)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.probe = self.root / 'probe.py'
        self.probe.write_text(
            'import json,os,sys\n'
            'from pathlib import Path\n'
            'from agent.config import load_config,Paths\n'
            'sys.stdin.read()\n'
            'config=load_config(secure_permissions=False)\n'
            'Path(sys.argv[1]).write_text(json.dumps(dict(host=config.host, '
            'config=os.environ.get("FARMBOT_CONFIG"), ledger=str(Paths(config).ledger))), encoding="utf-8")\n',
            encoding='utf-8')

    def config_file(self, directory, name, **extra):
        path = directory / (name + '.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        data = dict(client_id='fixture', client_secret='fixture', webhook_secret='fixture',
                    host=name, runtime='fake', port=0, repos={'Farm-Client': str(self.origin)},
                    local_root=str(directory / (name + '-state')))
        path.write_text(json.dumps({**data, **extra}), encoding='utf-8')
        return path

    def close_components(self, components):
        for item_id in list(components.launcher.running()):
            components.launcher.stop(item_id)
        components.launcher.poll()
        components.server.server_close()
        components.receiver.close()
        components.pool.close()
        components.lifecycle.ledger.close()
        components.progress.ledger.close()
        components.ledger.close()

    def launch_probe(self, components, item):
        handle = components.scheduler.launch(item)
        self.assertIsNotNone(handle)
        handle.process.wait(timeout=15)
        finished = components.launcher.poll()
        self.assertEqual(len(finished), 1)
        self.assertEqual(finished[0].returncode, 0,
                         (handle.run_dir / 'stderr.log').read_text(encoding='utf-8'))
        return json.loads(handle.last_message_path.read_text(encoding='utf-8'))

    def test_selected_config_wins_for_initial_and_resumed_children(self):
        for selection in ('explicit', 'environment', 'default'):
            with self.subTest(selection=selection):
                directory = self.root / selection / 'profile files 测试'
                decoy = self.config_file(directory, 'other-profile')
                # A JSON key must not be able to spoof the file's actual provenance.
                selected = self.config_file(directory, 'selected-profile', source_path=str(decoy))
                relative = selected.relative_to(self.root)
                with contextlib.chdir(self.root), patch('agent.config.DEFAULT_CONFIG', selected), \
                        patch.dict(os.environ, {'FARMBOT_CONFIG': str(relative)}):
                    if selection == 'explicit':
                        os.environ['FARMBOT_CONFIG'] = str(decoy)
                        config = load_config(relative)
                    elif selection == 'environment':
                        config = load_config()
                    else:
                        del os.environ['FARMBOT_CONFIG']
                        config = load_config()
                with patch.dict(os.environ, {'FARMBOT_CONFIG': str(decoy)}):
                    components = build(config)
                    self.addCleanup(self.close_components, components)
                    components.launcher.runtime = components.launcher.runtime._replace(
                        command=[sys.executable, str(self.probe), '{last_message}'])
                    components.ledger.observe_issue(issue())
                    components.ledger.ensure_session('session', ISSUE, delegation=False)
                    item = components.ledger.create_work_item(issue_id=ISSUE, session_id='session', skill='chat')
                    expected = {'host': 'selected-profile', 'config': str(selected),
                                'ledger': str(directory / 'selected-profile-state' / 'agent' / 'ledger.sqlite3')}
                    self.assertEqual(self.launch_probe(components, item), expected)
                    token = components.ledger.claim(item['id'], worker_id='fixture')['token']
                    components.ledger.await_input(item['id'], token, 'Continue?')
                    components.ledger.push_inbox(item['id'], 'Continue', resume_waiting=True)
                    self.assertEqual(self.launch_probe(components, components.ledger.item(item['id'])), expected)
                    self.assertEqual(os.environ['FARMBOT_CONFIG'], str(decoy), 'parent environment changed')

    def test_installed_service_keeps_environment_selected_config(self):
        selected = self.config_file(self.root / 'profile files 测试', 'selected-profile')
        with contextlib.chdir(self.root), patch.dict(os.environ, {'FARMBOT_CONFIG': str(selected.relative_to(self.root))}), \
                patch('agent.service.missing_tools', return_value=[]), \
                patch('agent.service.shutil.which', return_value='cloudflared'), \
                patch('agent.service.Path.home', return_value=self.root), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['install-launchd']), 0)
        path = self.root / 'Library' / 'LaunchAgents' / (AGENTS['serve'] + '.plist')
        arguments = plistlib.loads(path.read_bytes())['ProgramArguments']
        self.assertEqual(arguments[-2:], ['--config', str(selected)])

    def test_tilde_config_path_is_expanded_before_loading(self):
        selected = self.config_file(self.root / 'profiles', 'selected-profile')
        variable = 'USERPROFILE' if os.name == 'nt' else 'HOME'
        with patch.dict(os.environ, {variable: str(self.root)}):
            config = load_config('~/profiles/selected-profile.json', secure_permissions=False)
        self.assertEqual(config.host, 'selected-profile')
        self.assertEqual(config.source_path, selected)

    def test_worker_api_can_read_a_host_secured_config_without_chmod(self):
        selected = self.config_file(self.root / 'profiles', 'selected-profile')
        load_config(selected)  # Trusted host hardens the file before launching workers.
        identity = {'viewer': {'id': 'test-app', 'name': 'FarmBot'},
                    'organization': {'id': 'test-org', 'name': 'Test'}}
        with patch.dict(os.environ, {'FARMBOT_CONFIG': str(selected)}), \
                patch('agent.config.os.chmod', side_effect=PermissionError('read-only config')), \
                patch('agent.linear_api.LinearAPI.graphql', return_value=identity):
            os.environ.pop('FARMBOT_LINEAR_STUB_DIR', None)
            api = linear_api()
        self.assertEqual(api.client_id, 'fixture')
        self.assertEqual(api.app_user_id, 'test-app')

    def test_read_only_config_does_not_disable_worker_pr_destination_checks(self):
        from agent.__main__ import check_pr_targets
        selected = self.config_file(self.root / 'profiles', 'selected-profile',
                                    repos={'Farm-Client': 'https://github.com/test/sandbox.git'})
        load_config(selected)
        with patch.dict(os.environ, {'FARMBOT_CONFIG': str(selected)}), \
                patch('agent.config.os.chmod', side_effect=PermissionError('read-only config')):
            check_pr_targets(['https://github.com/test/sandbox/pull/1'])
            with self.assertRaisesRegex(LedgerError, 'not under a configured repository'):
                check_pr_targets(['https://github.com/production/source/pull/1'])
