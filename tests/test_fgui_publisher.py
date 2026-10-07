"""Prepared publisher boundaries, scripted exports and actual native containment."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

from PIL import Image

from agent import fgui_publisher as publisher
from agent.fgui_approval import source_digest
from agent.ledger import LedgerError
from test_fgui_export import package_bytes


class PublisherTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = self.root / "source space 验证"; self.project.mkdir()
        (self.project / "settings").mkdir()
        (self.project / "assets/One").mkdir(parents=True)
        (self.project / "FGUIProject.fairy").write_text('<projectDescription type="Unity"/>', encoding="utf-8")
        (self.project / "settings/Publish.json").write_text('{"codeGeneration":{"allowGenCode":false}}', encoding="utf-8")
        (self.project / "assets/One/package.xml").write_text('<packageDescription id="pack0001"/>', encoding="utf-8")
        self.tools = self.root / "tools"; self.tools.mkdir()
        for name in publisher.TOOL_FILES:
            (self.tools / name).write_bytes(b"pinned test tool " + name.encode())
        self.pins = {name: hashlib.sha256((self.tools / name).read_bytes()).hexdigest() for name in publisher.TOOL_FILES}
        self.run = self.root / "attempt space 验证"
        self.fences = 0
        self.native = patch.object(publisher, "_native_windows", return_value=True)
        self.native.start(); self.addCleanup(self.native.stop)
        self.runner = patch.object(publisher, "_windows_run", side_effect=self.export)
        self.runner_mock = self.runner.start(); self.addCleanup(self.runner.stop)

    def source(self):
        return {"head": "a" * 40, "files": publisher._source(self.project, {"One": "pack0001"})}

    def fence(self):
        self.fences += 1

    def export(self, command, cwd, run, fence, timeout):
        output = Path(command[command.index("-o") + 1])
        (output / "One_fui.bytes").write_bytes(package_bytes())
        Image.new("RGBA", (2, 2), "red").save(output / "One_atlas0.png")
        (run / "publisher.log").write_text("Publish completed\n", encoding="utf-8")
        return {"exit_code": 0, "job_assigned_before_export": True, "job_assigned_before_startup": True,
                "job_empty": True, "seconds": .01}

    def publish(self, **kwargs):
        values = dict(fence=self.fence, verify_source=self.source)
        values.update(kwargs)
        return publisher.publish(self.project, {"One": "pack0001"}, self.tools / publisher.TOOL_FILES[0],
                                 self.pins, self.run, **values)

    def rejected_before_process(self, message, **kwargs):
        with self.assertRaisesRegex(LedgerError, message):
            self.publish(**kwargs)
        self.runner_mock.assert_not_called()

    def test_exact_native_arguments_private_immutable_snapshot_and_receipt(self):
        result = self.publish()
        command, cwd, run, fence, timeout = self.runner_mock.call_args.args
        self.assertEqual(command, [str(self.tools / publisher.TOOL_FILES[0]), "-batchmode", "-p",
            str(self.project / "FGUIProject.fairy"), "-b", "One", "-o", str(self.run / "staging"),
            "-logFile", str(self.run / "publisher.log")])
        self.assertEqual((cwd, run, timeout), (self.tools, self.run, 180))
        self.assertEqual(result.source_head, "a" * 40)
        self.assertEqual(result.source_digest, source_digest(self.source()["files"]))
        self.assertEqual(len(result.snapshot.artifacts), 2)
        self.assertGreaterEqual(self.fences, 4)
        evidence = result.evidence()
        self.assertNotIn(str(self.root), json.dumps(evidence))
        self.assertEqual(json.loads((self.run / "receipt.json").read_text())['state'], "complete")
        (self.run / "staging/One_atlas0.png").write_bytes(b"later change")
        self.assertEqual(result.evidence(), evidence)

    def test_all_tool_pins_required_and_mutation_refused_before_run(self):
        self.pins.pop("baselib.dll")
        self.rejected_before_process("four tool pins")
        self.pins["baselib.dll"] = "b" * 64
        self.rejected_before_process("selected hash")

    def test_invalid_pin_and_executable_name_refused(self):
        self.pins["UnityPlayer.dll"] = "invalid"
        self.rejected_before_process("hash is invalid")
        with self.assertRaisesRegex(LedgerError, "explicit native executable"):
            publisher._tools(self.tools / "another.exe", self.pins)

    def test_tool_hardlink_refused(self):
        os.link(self.tools / "baselib.dll", self.root / "alias")
        self.rejected_before_process("unlinked regular")

    def test_only_unity_project_allowed(self):
        (self.project / "FGUIProject.fairy").write_text('<projectDescription type="Other"/>')
        self.rejected_before_process("Unity project")

    def test_generation_must_be_explicitly_disabled_without_settings_changes(self):
        settings = self.project / "settings/Publish.json"
        for data in ('{}', '{"codeGeneration":{"allowGenCode":true}}', '{"codeGeneration":{"allowGenCode":0}}'):
            with self.subTest(data=data):
                settings.write_text(data)
                self.rejected_before_process("explicitly disabled")
                self.assertEqual(settings.read_text(), data)

    def test_ambiguous_json_keys_xml_entities_and_malformed_xml_refused(self):
        settings = self.project / "settings/Publish.json"
        settings.write_text('{"codeGeneration":{"allowGenCode":true,"allowGenCode":false}}')
        self.rejected_before_process("duplicate keys")
        settings.write_text('{"codeGeneration":{"allowGenCode":false}}')
        for data in ('<!DOCTYPE X><projectDescription type="Unity"/>', '<projectDescription'):
            with self.subTest(data=data):
                (self.project / "FGUIProject.fairy").write_text(data)
                self.rejected_before_process("entities|malformed")

    def test_manifest_identity_mismatch_refused(self):
        (self.project / "assets/One/package.xml").write_text('<packageDescription id="wrong000"/>')
        self.rejected_before_process("manifest identity differs")

    def test_required_source_hashes_and_full_commit_refused_when_incomplete(self):
        self.rejected_before_process("full source identity", verify_source=lambda: {"head": "abc", "files": {}})
        self.rejected_before_process("omits or differs", verify_source=lambda: {"head": "a" * 40, "files": {"other": "b" * 64}})

    def test_source_identity_rejects_traversal_and_non_hash_values(self):
        for name, digest in (("../escape", "b" * 64), ("/absolute", "b" * 64), ("x", "bad")):
            with self.subTest(name=name):
                source = self.source(); source["files"][name] = digest
                self.rejected_before_process("invalid paths or hashes", verify_source=lambda: source)

    def test_private_staging_cannot_reuse_or_overlap_source_tools(self):
        for path in (self.project / "output", self.tools / "staging", self.project, self.root):
            with self.subTest(path=path):
                self.run = path
                self.rejected_before_process("new private staging")

    def test_existing_watched_output_refused_before_publisher(self):
        (self.project / "output").mkdir()
        self.rejected_before_process("watched output directory")

    def test_must_supply_live_fences_and_finite_bounded_timeout(self):
        for values in (dict(fence=None), dict(verify_source=None), dict(timeout=0), dict(timeout=181),
                       dict(timeout=True), dict(timeout=float("nan")), dict(timeout=float("inf"))):
            with self.subTest(values=values):
                self.rejected_before_process("live fences", **values)

    def test_stop_before_prepare_creates_no_attempt(self):
        def stopped():
            raise LedgerError("stopped")
        self.rejected_before_process("stopped", fence=stopped)
        self.assertFalse(self.run.exists())

    def test_failed_process_and_uncertain_quiescence_never_validate_output(self):
        for data in ({"exit_code": 2, "job_empty": True, "job_assigned_before_export": True},
                     {"exit_code": 0, "job_empty": False}, {"exit_code": 0, "job_empty": True},
                     {"exit_code": 0, "job_empty": True, "job_assigned_before_export": True,
                      "job_assigned_before_startup": False}):
            with self.subTest(data=data), tempfile.TemporaryDirectory() as tmp:
                self.run = Path(tmp).resolve() / "attempt"
                self.runner_mock.side_effect = lambda *args: data
                with self.assertRaisesRegex(LedgerError, "quiescent successful"):
                    self.publish()
                self.assertEqual(json.loads((self.run / "receipt.json").read_text())['state'], "interrupted")

    def test_changed_source_and_tools_after_process_refuse_success_retain_staging(self):
        original = self.export
        def changed(*args):
            result = original(*args)
            (self.project / "FGUIProject.fairy").write_text('<projectDescription type="Unity" version="changed"/>')
            return result
        self.runner_mock.side_effect = changed
        with self.assertRaisesRegex(LedgerError, "source changed"):
            self.publish()
        self.assertTrue((self.run / "staging/One_fui.bytes").is_file())
        self.run = self.root / "second"
        def tool_changed(*args):
            result = original(*args); (self.tools / "baselib.dll").write_bytes(b"changed")
            return result
        self.runner_mock.side_effect = tool_changed
        with self.assertRaisesRegex(LedgerError, "selected hash"):
            self.publish()

    def test_same_config_but_changed_tracked_source_identity_refused(self):
        before = self.source(); after = {"head": "b" * 40, "files": before["files"]}
        with self.assertRaisesRegex(LedgerError, "source changed"):
            self.publish(verify_source=unittest.mock.Mock(side_effect=[before, after]))

    def test_license_failure_missing_completion_invalid_utf8_and_log_bound_refused(self):
        for data in (b"Publish completed\nLicense required", b"not completed", b"\xff", b"x" * (publisher.MAX_LOG_BYTES + 1)):
            with self.subTest(size=len(data)), tempfile.TemporaryDirectory() as tmp:
                self.run = Path(tmp).resolve() / "attempt"
                def failed_log(*args):
                    result = self.export(*args); (args[2] / "publisher.log").write_bytes(data)
                    return result
                self.runner_mock.side_effect = failed_log
                with self.assertRaises((LedgerError, UnicodeError)):
                    self.publish()
                self.assertEqual(json.loads((self.run / "receipt.json").read_text())['state'], "interrupted")

    def test_zero_exit_with_orphan_artifact_refuses_inventory(self):
        def orphan(*args):
            result = self.export(*args); (args[2] / "staging/orphan").write_bytes(b"unknown")
            return result
        self.runner_mock.side_effect = orphan
        with self.assertRaisesRegex(LedgerError, "orphan"):
            self.publish()

    def test_no_platform_fallback(self):
        with patch.object(publisher, "_native_windows", return_value=False):
            self.rejected_before_process("native Windows")

    def test_native_environment_withholds_runtime_and_credential_selectors(self):
        with patch.dict(os.environ, {"FARMBOT_CONFIG": "live", "fake_cli_mode": "live", "GH_TOKEN": "secret",
                "LARKSUITE_CLI_CONFIG_DIR": "live", "CODEX_HOME": "runtime", "OPENAI_API_KEY": "secret"}):
            env = publisher._environment()
            self.assertEqual(env["PYTHONUTF8"], "1")
            self.assertFalse(any(k.upper().startswith(("FARMBOT_", "FAKE_CLI_", "LARK")) for k in env))
            self.assertFalse(set(env) & {"GH_TOKEN", "CODEX_HOME", "OPENAI_API_KEY"})
            self.assertEqual(env["PATH"], os.environ["PATH"])


@unittest.skipUnless(os.name == "nt", "native Windows publisher Job Objects")
class NativePublisherTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / "native space 验证"; self.root.mkdir()
        self.run = self.root / "run"; self.run.mkdir()
        self.script = self.root / "publisher.py"
        self.foreign = patch.object(publisher, "_foreign_editors", return_value=False)
        self.foreign.start(); self.addCleanup(self.foreign.stop)
        # Real native locks, isolated name: tests never contend with a licensed app.
        original = publisher._WindowsLock
        self.lock_name = "Local\\FarmBot-TestPublisher-" + uuid.uuid4().hex
        lock = patch.object(publisher, "_WindowsLock", side_effect=lambda: original(self.lock_name))
        lock.start(); self.addCleanup(lock.stop)

    def invoke(self, source, *, fence=lambda: None, timeout=5):
        self.script.write_text(source, encoding="utf-8")
        return publisher._windows_run([sys.executable, str(self.script)], self.root, self.run, fence, timeout)

    def receipt(self):
        result = json.loads((self.run / "native-process.json").read_text())
        from agent.windows_job import WindowsJob
        self.assertTrue(result["job_empty"])
        self.assertTrue(WindowsJob.empty(result["job"]))
        return result

    def test_native_success_assigns_before_launch_and_is_quiescent(self):
        result = self.invoke('print("native export")\n')
        self.assertEqual(result["exit_code"], 0)
        self.assertTrue(result["job_assigned_before_export"])
        self.assertTrue(result["job_assigned_before_startup"])
        self.assertEqual(self.receipt()["state"], "complete")
        self.assertIn("native export", (self.run / "stdout.log").read_text())

    def test_native_delayed_assignment_precedes_redirector_and_gate_startup(self):
        from agent.windows_job import WindowsJob
        marker = self.root / "gate-started"
        real_gate = Path(publisher.__file__).with_name("windows_worker_gate.py").read_text(encoding="utf-8")
        (self.root / "windows_worker_gate.py").write_text(
            'from pathlib import Path\n' + f'Path({str(marker)!r}).touch()\n' + real_gate, encoding="utf-8")
        assign = WindowsJob.assign
        def delayed(job, process):
            time.sleep(.5)
            self.assertFalse(marker.exists())
            assign(job, process)
        with patch.object(publisher, "__file__", str(self.root / "fgui_publisher.py")), \
                patch.object(WindowsJob, "assign", delayed):
            result = self.invoke('print("contained startup")\n')
        self.assertTrue(result["job_assigned_before_startup"])
        self.assertTrue(marker.exists()); self.assertEqual(self.receipt()["state"], "complete")

    def test_native_assignment_failure_never_runs_publisher(self):
        marker = self.root / "must-not-exist"
        with patch("agent.windows_job.WindowsJob.assign", side_effect=OSError("denied")):
            with self.assertRaisesRegex(OSError, "denied"):
                self.invoke(f'from pathlib import Path\nPath({str(marker)!r}).touch()\n')
        self.assertFalse(marker.exists())
        self.assertFalse(self.receipt()["job_assigned_before_export"])

    def test_native_stop_after_assignment_never_opens_gate(self):
        marker = self.root / "must-not-exist"; calls = 0
        def fence():
            nonlocal calls
            calls += 1
            if calls == 2:
                raise LedgerError("stopped before gate")
        with self.assertRaisesRegex(LedgerError, "stopped before gate"):
            self.invoke(f'from pathlib import Path\nPath({str(marker)!r}).touch()\n', fence=fence)
        self.assertFalse(marker.exists())
        self.assertTrue(self.receipt()["job_assigned_before_export"])

    def test_native_timeout_reaps_tree_preserves_unrelated_process(self):
        from agent.windows_job import alive
        unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(lambda: (unrelated.terminate(), unrelated.wait()) if unrelated.poll() is None else None)
        child = self.root / "child"
        source = ('import subprocess,sys,time\nfrom pathlib import Path\n'
                  'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"])\n'
                  f'Path({str(child)!r}).write_text(str(p.pid))\ntime.sleep(60)\n')
        with self.assertRaisesRegex(LedgerError, "timed out"):
            self.invoke(source, timeout=2)
        self.assertFalse(alive(int(child.read_text())))
        self.assertIsNone(unrelated.poll())
        self.assertEqual(self.receipt()["state"], "interrupted")

    def test_native_stop_during_running_publisher_reaps_owned_tree(self):
        marker = self.root / "started"
        def fence():
            if marker.exists():
                raise LedgerError("stopped")
        with self.assertRaisesRegex(LedgerError, "stopped"):
            self.invoke(f'import time\nfrom pathlib import Path\nPath({str(marker)!r}).touch()\ntime.sleep(60)\n', fence=fence)
        self.assertEqual(self.receipt()["state"], "interrupted")

    def test_native_exited_parent_with_lingering_child_is_refused_and_reaped(self):
        from agent.windows_job import alive
        child = self.root / "child"
        with self.assertRaisesRegex(LedgerError, "lingering owned"):
            self.invoke('import subprocess,sys\nfrom pathlib import Path\n'
                        'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"])\n'
                        f'Path({str(child)!r}).write_text(str(p.pid))\n')
        self.assertFalse(alive(int(child.read_text())))
        self.receipt()

    def test_native_parent_exit_waits_for_bounded_descendant_quiescence(self):
        result = self.invoke('import subprocess,sys\n'
            'subprocess.Popen([sys.executable,"-c","import time; time.sleep(.2)"])\n')
        self.assertEqual(result["exit_code"], 0)
        self.assertTrue(result["job_empty"])
        self.assertGreater(result["quiescence_seconds"], .05)
        self.receipt()

    def test_native_competing_process_is_never_terminated(self):
        self.foreign.stop()
        with patch.object(publisher, "_foreign_editors", side_effect=[False, True]):
            with self.assertRaisesRegex(LedgerError, "competing FairyGUI"):
                self.invoke('import time\ntime.sleep(60)\n')
        self.receipt()

    def test_native_existing_editor_refuses_before_any_process(self):
        self.foreign.stop()
        with patch.object(publisher, "_foreign_editors", return_value=True), patch.object(subprocess, "Popen") as process:
            with self.assertRaisesRegex(LedgerError, "existing FairyGUI"):
                self.invoke('raise SystemExit(0)\n')
            process.assert_not_called()

    def test_native_mutex_excludes_second_process(self):
        module_root = str(Path(__file__).resolve().parents[1])
        ready = self.root / "lock-ready"
        script = (f'import sys,time\nfrom pathlib import Path\nsys.path.insert(0,{module_root!r})\n'
                  'from agent.fgui_publisher import _WindowsLock\n'
                  f'with _WindowsLock({self.lock_name!r}):\n Path({str(ready)!r}).touch()\n time.sleep(60)\n')
        owner = subprocess.Popen([sys.executable, "-c", script])
        self.addCleanup(lambda: (owner.terminate(), owner.wait()) if owner.poll() is None else None)
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertTrue(ready.exists())
        with self.assertRaisesRegex(LedgerError, "machine export lock"):
            self.invoke('raise SystemExit(0)\n')
        self.assertIsNone(owner.poll())

    def test_native_nested_job_inside_owned_worker(self):
        from agent.launcher import Launcher, RUNTIMES
        root = str(Path(__file__).resolve().parents[1]); result = self.root / "nested-result.json"
        worker = self.root / "worker.py"
        worker.write_text('import sys,json\nfrom pathlib import Path\n'
            f'sys.path.insert(0,{root!r})\nfrom agent.fgui_publisher import _windows_run\n'
            f'run=Path({str(self.run)!r})\n'
            f'r=_windows_run([sys.executable,"-c","print(123)"],{str(self.root)!r},run,lambda:None,5)\n'
            f'Path({str(result)!r}).write_text(json.dumps(r))\n', encoding="utf-8")
        launcher = Launcher(self.root / "outer-runs", RUNTIMES['fake']._replace(command=[sys.executable, str(worker)]), "test")
        handle = launcher.spawn("nested", "prompt", {}, 10, self.root)
        self.addCleanup(launcher.stop, "nested")
        deadline = time.monotonic() + 10; finished = []
        while not finished and time.monotonic() < deadline:
            finished = launcher.poll(); time.sleep(.02)
        self.assertTrue(finished)
        self.assertEqual(finished[0].returncode, 0, (handle.run_dir / "stderr.log").read_text(encoding="utf-8"))
        launcher.assert_quiescent("nested", handle.pid)
        self.assertTrue(json.loads(result.read_text())["job_empty"])
        self.receipt()


if __name__ == "__main__":
    unittest.main()
