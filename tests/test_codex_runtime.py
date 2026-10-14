"""Offline npm fixtures: complete native bundles and candidate integrity boundaries."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from agent.codex_runtime import RECEIPT, _npm_tools, main, prepare, verify


class CodexRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "runtime 空间"
        self.root.mkdir()
        self.node = self.root / "node tool.exe"
        self.npm = self.root / "npm entry.js"
        self.node.write_text("fake", encoding="utf-8")
        self.npm.write_text("fake", encoding="utf-8")
        self.target = ("win32", "x64")
        self.nested = False
        self.reported_version = "0.162.1"
        self.installed_version = "0.162.1"
        self.calls = []

    def package(self, root):
        (root / "package.json").write_text('{}', encoding="utf-8")
        (root / "package-lock.json").write_text('{"lockfileVersion":3}', encoding="utf-8")
        wrapper = root / "node_modules/@openai/codex"
        wrapper.mkdir(parents=True)
        (wrapper / "package.json").write_text(json.dumps({"name": "@openai/codex", "version": self.installed_version}), encoding="utf-8")
        suffix = "-".join(self.target)
        base = wrapper if self.nested else root
        native = base / "node_modules/@openai" / f"codex-{suffix}"
        native.mkdir(parents=True)
        (native / "package.json").write_text(json.dumps({"name": "@openai/codex", "version": f"{self.installed_version}-{suffix}"}), encoding="utf-8")
        vendor = native / "vendor/native-target"
        (vendor / "bin").mkdir(parents=True)
        name = "codex.exe" if self.target[0] == "win32" else "codex"
        (vendor / "bin" / name).write_bytes(b"native Codex fixture")
        (vendor / "codex-resources").mkdir()
        self.helper = vendor / "codex-resources/helper.exe"
        self.helper.write_bytes(b"matching native helper")
        return vendor

    def fake_run(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        self.assertTrue(kwargs['check'])
        if argv[1] == "-p":
            return subprocess.CompletedProcess(argv, 0, json.dumps(dict(zip(("platform", "arch"), self.target))), "")
        if "install" in argv:
            self.package(kwargs['cwd'])
            return subprocess.CompletedProcess(argv, 0, "installed\n", "")
        self.assertEqual(argv[1:], ["--version"])
        return subprocess.CompletedProcess(argv, 0, f"codex-cli {self.reported_version}\n", "")

    def prepare(self, directory=None, **kwargs):
        return prepare(directory or self.root / "candidate", node=str(self.node), npm_cli=str(self.npm),
                       runner=self.fake_run, **kwargs)

    def test_latest_install_is_separate_and_receipt_pins_helpers_and_lock(self):
        receipt = self.prepare()
        install, options = self.calls[1]
        self.assertEqual(install[:3], [str(self.node), str(self.npm), "install"])
        self.assertIn("@openai/codex@latest", install)
        for flag in ("--ignore-scripts", "--no-bin-links", "--registry=https://registry.npmjs.org"):
            self.assertIn(flag, install)
        self.assertEqual(options['cwd'], self.root / "candidate")
        self.assertNotIn("shell", options)
        self.assertEqual(receipt['version'], "0.162.1")
        self.assertEqual(receipt['pins'][str(self.helper)], hashlib.sha256(self.helper.read_bytes()).hexdigest())
        self.assertIn(str(self.root / "candidate/package-lock.json"), receipt['pins'])
        with patch("subprocess.run", side_effect=AssertionError("verify must not execute anything")):
            self.assertEqual(verify(self.root / "candidate" / RECEIPT), receipt)

    def test_platform_bundles_and_nested_npm_optional_dependencies(self):
        for system in ("win32", "darwin", "linux"):
            for arch in ("x64", "arm64"):
                for nested in (False, True):
                    with self.subTest(system=system, arch=arch, nested=nested):
                        self.target, self.nested = (system, arch), nested
                        root = self.root / f"{system}-{arch}-{nested}"
                        receipt = self.prepare(root)
                        self.assertEqual(verify(root / RECEIPT), receipt)
                        self.assertEqual(Path(receipt['binary']).name, "codex.exe" if system == "win32" else "codex")

    def test_explicit_release_is_recorded_and_mismatch_is_refused(self):
        receipt = self.prepare(version="0.162.1")
        self.assertIn("@openai/codex@0.162.1", self.calls[1][0])
        self.assertEqual(receipt['requested_version'], "0.162.1")
        with self.assertRaises(ValueError):
            self.prepare(self.root / "mismatch", version="0.160.0")
        self.assertFalse((self.root / "mismatch" / RECEIPT).exists())

    def test_existing_directory_is_never_overwritten(self):
        destination = self.root / "candidate"
        destination.mkdir()
        sentinel = destination / "original.exe"
        sentinel.write_bytes(b"active runtime")
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual(sentinel.read_bytes(), b"active runtime")
        self.assertEqual(self.calls, [])

    def test_relative_roots_checkouts_and_version_ranges_are_refused_before_npm(self):
        for version in ("^0.162.1", "alpha", "latest; echo secret", "../0.162.1"):
            with self.assertRaises(ValueError):
                self.prepare(version=version)
        with self.assertRaises(ValueError):
            self.prepare(Path("relative candidate"))
        checkout = self.root / "checkout"
        checkout.mkdir()
        (checkout / ".git").write_text("gitdir: elsewhere", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.prepare(checkout / "candidate")
        self.assertEqual(self.calls, [])

    def test_unknown_platform_is_refused_before_creating_candidate(self):
        self.target = ("linux", "ia32")
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse((self.root / "candidate").exists())
        self.assertEqual(len(self.calls), 1)

    def test_changed_missing_and_added_bundle_files_are_refused(self):
        for change in ("changed", "missing", "added"):
            with self.subTest(change=change):
                root = self.root / change
                self.prepare(root)
                if change == "changed":
                    self.helper.write_bytes(b"different helper")
                elif change == "missing":
                    self.helper.unlink()
                else:
                    (self.helper.parent / "unexpected.exe").write_bytes(b"new executable")
                with self.assertRaises(ValueError):
                    verify(root / RECEIPT)

    def test_receipt_cannot_redirect_binary_or_path_outside_candidate(self):
        root = self.root / "candidate"
        receipt = self.prepare(root)
        for key, value in (("binary", str(self.node)), ("path_prepend", [str(self.root)])):
            with self.subTest(key=key):
                changed = dict(receipt, **{key: value})
                (root / RECEIPT).write_text(json.dumps(changed), encoding="utf-8")
                with self.assertRaises(ValueError):
                    verify(root / RECEIPT)

    def test_hardlinked_bundle_file_is_refused(self):
        root = self.root / "candidate"
        self.prepare(root)
        os.link(self.helper, self.root / "outside helper.exe")
        with self.assertRaises(ValueError):
            verify(root / RECEIPT)

    def test_symlinked_bundle_file_or_directory_is_refused(self):
        for directory in (False, True):
            with self.subTest(directory=directory):
                root = self.root / f"link-{directory}"
                self.prepare(root)
                if directory:
                    (self.helper.parent / "linked-dir").symlink_to(self.root, target_is_directory=True)
                else:
                    self.helper.unlink()
                    self.helper.symlink_to(self.node)
                with self.assertRaises(ValueError):
                    verify(root / RECEIPT)

    def test_linked_candidate_parent_is_refused_without_touching_its_target(self):
        linked = self.root / "linked"
        linked.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.prepare(linked / "candidate")
        self.assertFalse((self.root / "candidate").exists())

    def test_native_reported_version_must_match_package(self):
        self.reported_version = "0.160.0"
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse((self.root / "candidate" / RECEIPT).exists())

    def test_native_package_version_or_missing_binary_refuses_a_receipt(self):
        for missing in (False, True):
            with self.subTest(missing=missing):
                root = self.root / f"bad-native-{missing}"
                def mismatched(argv, **kwargs):
                    result = self.fake_run(argv, **kwargs)
                    if "install" in argv:
                        native = root / "node_modules/@openai/codex-win32-x64"
                        if missing:
                            (native / "vendor/native-target/bin/codex.exe").unlink()
                        else:
                            (native / "package.json").write_text('{"version":"0.160.0-win32-x64"}', encoding="utf-8")
                    return result
                with self.assertRaises(ValueError):
                    prepare(root, node=str(self.node), npm_cli=str(self.npm), runner=mismatched)
                self.assertFalse((root / RECEIPT).exists())

    def test_malformed_receipt_is_refused_without_execution(self):
        root = self.root / "candidate"
        self.prepare(root)
        (root / RECEIPT).write_text('[]', encoding="utf-8")
        with patch("subprocess.run", side_effect=AssertionError("must not execute receipt data")):
            with self.assertRaises(ValueError):
                verify(root / RECEIPT)

    def test_windows_npm_shim_is_resolved_to_javascript_instead_of_executed(self):
        tool_dir = self.root / "node installation"
        npm_cli = tool_dir / "node_modules/npm/bin/npm-cli.js"
        npm_cli.parent.mkdir(parents=True)
        npm_cli.write_text("fake", encoding="utf-8")
        with patch("agent.codex_runtime.shutil.which", side_effect=[str(self.node), str(tool_dir / "npm.cmd")]):
            self.assertEqual(_npm_tools(), (str(self.node), str(npm_cli)))

    def test_failed_install_keeps_logs_without_a_verified_receipt(self):
        def failing(argv, **kwargs):
            if "install" in argv:
                raise subprocess.CalledProcessError(1, argv, output="install log", stderr="private failure")
            return self.fake_run(argv, **kwargs)
        root = self.root / "failed"
        with self.assertRaises(subprocess.CalledProcessError):
            prepare(root, node=str(self.node), npm_cli=str(self.npm), runner=failing)
        self.assertEqual((root / "npm-install.stderr.log").read_text(encoding="utf-8"), "private failure")
        self.assertFalse((root / RECEIPT).exists())

    def test_cli_errors_do_not_print_private_npm_failure_values(self):
        stderr = io.StringIO()
        with patch("agent.codex_runtime.prepare", side_effect=ValueError("private host value")), contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as error:
                main(["prepare", "--directory", str(self.root / "candidate")])
        self.assertEqual(error.exception.code, 1)
        self.assertNotIn("private host value", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
