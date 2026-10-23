import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent.maintenance import Maintenance
from agent.resource_processes import quiescent
from agent.server_test_runtime import run_owned
import test_continuation_controls as controls
import test_server_slots as fixtures


class ServerRunnerTests(fixtures.ServerPoolFixture):
    start, save = controls.ProcessScopeTests.start, controls.ProcessScopeTests.save

    def execute(self, code, *, pause=False, changed=False, hold=False, arguments=()):
        key, token, grant = self.start(managed=True)
        paths = SimpleNamespace(ledger=self.db, repos=self.root/'repos', worktrees=self.root/'worktrees',
                                runs=self.root/'runs', server_slots=self.root)
        module = paths.worktrees/key/'farm-hive'/'game'
        module.mkdir(parents=True)
        (module/'go.mod').write_text('module fixture',encoding='utf-8')
        script = module/'fake go 测试.py'
        script.write_text(code,encoding='utf-8')
        resource = {'test_argv':[sys.executable,str(script), *arguments], 'environment':{'TEST_ENDPOINT':'isolated'},
                    'test_plan':grant['server_test_plan'], 'expires_at':time.time()+15}
        trees=Mock()
        if changed:
            trees.verification_commit.side_effect=[None,RuntimeError('changed')]
        config=SimpleNamespace(repos={},go_dependencies={})
        args=SimpleNamespace(item=key,db=str(self.db),module='game')
        original=self.ledger.renew
        calls=0
        def renew(*args):
            nonlocal calls
            calls+=1
            if pause and calls==2:
                Maintenance(self.ledger).pause('collect and stop')
            if hold and calls==2:
                self.ledger.set_slot_state(grant['resource'], 'held')
            return original(*args)
        with patch('agent.config.load_config',return_value=config), patch('agent.config.Paths',return_value=paths), \
             patch('agent.worktrees.Worktrees',return_value=trees), patch('agent.server_slots.read_resource',return_value=resource), \
             patch.object(self.ledger,'renew',side_effect=renew):
            result=run_owned(self.ledger,args,token)
        return key,token,grant,result,trees

    def test_actual_command_is_contained_and_preserves_plan_source_logs_and_claim(self):
        key,token,grant,result,trees=self.execute('import os; print(os.environ["TEST_ENDPOINT"])')
        self.assertEqual(result['status'],'completed',result)
        self.assertTrue(result['teardown'])
        self.assertEqual(result['test_plan'],grant['server_test_plan'])
        self.assertEqual(result['commit'],grant['commit_sha'])
        self.assertEqual(Path(result['output']).read_text(encoding='utf-8').strip(),'isolated')
        self.assertTrue(quiescent(self.ledger,grant['reservation_id'],sealed=False))
        self.ledger.renew(key,token)
        self.assertEqual(trees.verification_commit.call_count,2)
        self.assertEqual(self.services.cleaned,[])

    def test_resource_hold_stops_commands_without_lifting_recovery(self):
        key, token, grant, result, _ = self.execute('import time; time.sleep(30)', hold=True)
        self.assertEqual(result['status'], 'failed', result)
        self.assertTrue(result['teardown'])
        self.assertEqual(self.ledger.slot(grant['resource'])['state'], 'held')
        self.assertIsNotNone(self.ledger.active_reservation(key))
        self.assertEqual(self.services.cleaned, [])

    def test_long_exact_test_filter_reaches_native_child_unchanged_and_settles(self):
        from agent.server_test_plan import test_argv
        plan = {'packages': ['./modules/guild'], 'race': True,
                'tests': ['TestGuildTitle' + str(i).zfill(3) + 'x' * 40 for i in range(120)]}
        arguments = test_argv(plan)[1:]
        self.assertGreater(len(arguments[arguments.index('-run') + 1]), 4096)
        key, token, grant, result, _ = self.execute(
            'import json,sys; print(json.dumps(sys.argv[1:]))', arguments=arguments)
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(json.loads(Path(result['output']).read_text(encoding='utf-8')), arguments)
        self.assertTrue(result['teardown'], result)
        self.assertTrue(quiescent(self.ledger, grant['reservation_id'], sealed=False))
        self.ledger.renew(key, token)

    def test_rejected_arguments_do_not_create_an_uncertain_command(self):
        key, token, grant, result, _ = self.execute('raise AssertionError("must not run")',
                                                  arguments=['x' * 30000])
        self.assertEqual(result['status'], 'failed', result)
        self.assertIsNone(result['exit_code'])
        self.assertTrue(result['teardown'], result)
        self.assertEqual(self.ledger.connection.execute('SELECT count(*) FROM resource_commands').fetchone()[0], 0)
        self.assertFalse(Path(result['output']).exists())
        self.assertTrue(quiescent(self.ledger, grant['reservation_id'], sealed=False))
        self.ledger.renew(key, token)

    def test_interrupted_creation_still_requires_recovery(self):
        with patch('agent.integration_runtime.Command', side_effect=RuntimeError('unknown creation outcome')):
            _, _, grant, result, _ = self.execute('pass')
        self.assertEqual(result['status'], 'failed', result)
        self.assertFalse(result['teardown'], result)
        self.assertFalse(quiescent(self.ledger, grant['reservation_id'], sealed=False))
        self.assertEqual(self.ledger.connection.execute('SELECT state FROM resource_commands').fetchone()[0], 'starting')

    def test_failure_source_change_and_maintenance_are_not_passes_and_drain_commands(self):
        for code,pause,changed in [('raise SystemExit(3)',False,False),('pass',False,True),
                                   ('import time; time.sleep(30)',True,False)]:
            with self.subTest(pause=pause,changed=changed):
                key,token,grant,result,_=self.execute(code,pause=pause,changed=changed)
                self.assertEqual(result['status'],'failed',result)
                self.assertTrue(result['teardown'],result)
                self.assertTrue(quiescent(self.ledger,grant['reservation_id'],sealed=False))
                self.ledger.release(grant['reservation_id'],grant['token'],'test cleanup')
                self.exited=True
                self.pool.tick()
                if pause:
                    Maintenance(self.ledger).resume()
