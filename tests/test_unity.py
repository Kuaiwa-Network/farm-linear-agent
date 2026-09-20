import tempfile
import unittest
from pathlib import Path

from agent.unity import (UnityError, batch_test_command, candidates, editor_holds_project, editor_path,
                         other_editor_project, project_version, read_results)


class UnityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name) / "slot-1"
        (self.project / "ProjectSettings").mkdir(parents=True)
        (self.project / "ProjectSettings" / "ProjectVersion.txt").write_text(
            "m_EditorVersion: 2022.3.62f3\nm_EditorVersionWithRevision: 2022.3.62f3 (96770f904ca7)\n",
            encoding="utf-8")

    def test_the_editor_is_found_per_host_from_the_project_version(self):
        self.assertEqual(project_version(self.project), "2022.3.62f3")
        mac = candidates("2022.3.62f3", system="Darwin")[0]
        windows = candidates("2022.3.62f3", system="Windows")
        self.assertTrue(str(mac).endswith("Unity.app/Contents/MacOS/Unity"))
        self.assertTrue(all(str(p).endswith("Editor/Unity.exe") for p in windows), windows)
        found = editor_path(self.project, system="Darwin", exists=lambda p: p == mac)
        self.assertEqual(found, mac)

    def test_an_absent_editor_names_every_path_it_tried(self):
        # environ is injected: an exported FARMBOT_UNITY_EXE would otherwise decide this test's outcome.
        with self.assertRaises(UnityError) as caught:
            editor_path(self.project, system="Windows", exists=lambda p: False, environ={})
        self.assertIn("2022.3.62f3", str(caught.exception))
        self.assertGreaterEqual(str(caught.exception).count("Unity.exe"), 2)

    def test_the_batch_command_never_quits_never_drops_graphics_and_never_rewrites_sources(self):
        command = batch_test_command("/u/Unity", self.project, results=self.project / "r.xml",
                                     log=self.project / "r.log", assemblies=("HotUpdate.Tests",),
                                     build_target="OSXUniversal")
        self.assertEqual(command[0], "/u/Unity")
        self.assertNotIn("-quit", command)              # the test runner exits on its own (spec §8)
        self.assertNotIn("-nographics", command)        # PlayMode fixtures render FairyGUI (spec §8)
        self.assertNotIn("-accept-apiupdate", command)  # would rewrite Assets/ and dirty the next switch
        self.assertEqual(command[command.index("-testPlatform") + 1], "EditMode")
        self.assertEqual(command[command.index("-projectPath") + 1], str(self.project))
        self.assertEqual(command[command.index("-buildTarget") + 1], "OSXUniversal")

    def test_the_results_file_is_the_evidence_and_a_zero_total_is_a_verification_gap(self):
        """Task 0 Step 4, the spike's own "most important answer": exit 0 means *nothing ran* and exit 2
        means tests failed, so the exit code is advisory and the XML is the evidence. The three cases are
        the three the contract has to separate — a real run, an empty run behind a green exit code, and no
        run at all — and each of the three callers (the pool, the checker, the worker's report) asks this
        one function rather than parsing the file again."""
        red = self.project / "red.xml"
        red.write_text('<?xml version="1.0"?><test-run result="Failed(Child)" total="4388" passed="4362" '
                       'failed="26" />', encoding="utf-8")
        self.assertEqual(read_results(red), {"result": "Failed(Child)", "total": 4388, "passed": 4362,
                                             "failed": 26})
        empty = self.project / "empty.xml"
        empty.write_text('<?xml version="1.0"?><test-run result="Passed" total="0" passed="0" failed="0" />',
                         encoding="utf-8")
        # Not an error to read — a green exit code over an empty run is exactly what a wrong -assemblyNames
        # produces, and the caller is the one that must call total == 0 a gap rather than a pass.
        self.assertEqual(read_results(empty)["total"], 0)
        # A results file with no total at all is the third case: neither evidence of a run nor of a failure.
        headless = self.project / "headless.xml"
        headless.write_text('<?xml version="1.0"?><test-run result="Passed" />', encoding="utf-8")
        for broken in (self.project / "missing.xml", self.project / "truncated.xml", headless):
            if broken.name == "truncated.xml":
                broken.write_text("<test-run", encoding="utf-8")
            with self.subTest(file=broken.name), self.assertRaises(UnityError):
                read_results(broken)

    def test_another_editor_on_a_different_project_is_seen_and_our_own_is_not(self):
        """The two halves of one process listing, and they must not answer the same question. The first is
        the contention preflight; the second is what `SlotPool.editor_is_open` asks instead of looking at
        Temp/UnityLockfile, which Task 0 Step 5 found still present 32 s after the process was gone."""
        listing = ("901 /Applications/Unity/Hub/Editor/2022.3.62f3/Unity.app/Contents/MacOS/Unity "
                   "-projectPath /Users/x/WorkSpaces/Farm/Farm-Client\n")
        self.assertEqual(other_editor_project(self.project, system="Darwin", run=lambda: listing),
                         "/Users/x/WorkSpaces/Farm/Farm-Client")
        mine = f"901 Unity -projectPath {self.project}\n"
        self.assertIsNone(other_editor_project(self.project, system="Darwin", run=lambda: mine))
        self.assertIsNone(other_editor_project(self.project, system="Darwin", run=lambda: ""))
        # editor_holds_project is the complement: our folder, and a pid rather than a bool.
        self.assertEqual(editor_holds_project(self.project, system="Darwin", run=lambda: mine), 901)
        self.assertIsNone(editor_holds_project(self.project, system="Darwin", run=lambda: listing))
        self.assertIsNone(editor_holds_project(self.project, system="Darwin", run=lambda: ""))
