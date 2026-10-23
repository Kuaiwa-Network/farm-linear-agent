import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from agent.launcher import Launcher, RUNTIMES
from agent.worker_retention import retire_attempt


class CacheReuseTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='cache 空间 ')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        code = 'import json,os; print(json.dumps(dict((k,os.environ[k]) for k in ("GOCACHE","GOTMPDIR","FARMBOT_BUILD_ROOT"))))'
        self.launcher = Launcher(self.root/'runs', RUNTIMES['fake']._replace(command=[sys.executable,'-c',code]), 'test')

    def attempt(self, item):
        handle = self.launcher.spawn(item, 'prompt', {}, 20, self.root)
        deadline = time.monotonic()+10
        while not self.launcher.poll() and time.monotonic()<deadline:
            time.sleep(.02)
        self.assertEqual(self.launcher.running(),{})
        return handle, json.loads((handle.run_dir/'stdout.log').read_text(encoding='utf-8'))

    def test_same_item_warms_go_cache_but_new_attempt_and_other_item_keep_temp_build_isolated(self):
        first, env = self.attempt('one')
        artifact = Path(env['GOCACHE'])/'test-cache'
        artifact.write_text('compiled',encoding='utf-8')
        second, newer = self.attempt('one')
        _, peer = self.attempt('two')
        self.assertEqual(env['GOCACHE'],newer['GOCACHE'])
        self.assertNotEqual(env['GOCACHE'],peer['GOCACHE'])
        for key in ('GOTMPDIR','FARMBOT_BUILD_ROOT'):
            self.assertNotEqual(env[key],newer[key])
        self.assertTrue(artifact.exists())
        result = retire_attempt(self.launcher.runs_root, first.run_dir, apply=True, now=time.time()+48*3600)
        self.assertEqual(result['status'],'complete',result)
        self.assertFalse(artifact.exists())
        self.assertTrue((second.run_dir/'stdout.log').exists())

    def test_incomplete_old_attempt_blocks_cache_reuse_without_deleting_evidence(self):
        attempt = self.root/'runs'/'one'/'old'
        attempt.mkdir(parents=True)
        (attempt/'process.json').write_text('{"state":"preparing"}',encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError,'incomplete launch'):
            self.launcher.spawn('one','prompt',{},20,self.root)
        self.assertTrue((attempt/'process.json').exists())


    @unittest.skipUnless(os.name == 'nt', 'Windows nested worker Job')
    def test_service_job_cleanup_preserves_real_outer_worker(self):
        from agent.integration_runtime import Command
        marker=self.root/'nested-finished'
        script=self.root/'nested.py'
        script.write_text("import os,time,sys\nfrom pathlib import Path\nfrom agent.integration_runtime import Command\n"
            + "child=Command([sys.executable,'-c','import time;time.sleep(30)'],cwd=os.getcwd(),env=os.environ.copy(),log='child.log')\n"
            + "child.stop()\n" + f"Path({str(marker)!r}).write_text('worker survived',encoding='utf-8')\n"
            + "time.sleep(30)\n",encoding='utf-8')
        launcher=Launcher(self.root/'nested-runs',RUNTIMES['fake']._replace(command=[sys.executable,str(script)]),'test')
        handle=launcher.spawn('owner','prompt',{},45,self.root,
                              extra_env={'PYTHONPATH':str(Path(__file__).resolve().parents[1])})
        try:
            deadline=time.monotonic()+10
            while not marker.exists() and time.monotonic()<deadline:
                time.sleep(.02)
            self.assertTrue(marker.exists(),(handle.run_dir/'stderr.log').read_text(encoding='utf-8'))
            self.assertTrue(Launcher.alive(handle.pid))
            self.assertEqual(launcher.poll(),[])
        finally:
            launcher.stop('owner',1)
            launcher.poll()
