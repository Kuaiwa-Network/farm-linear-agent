"""Native LFS authentication boundaries; dummy executables, never host credentials."""
import hashlib
import contextlib
import ctypes
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from tools import build_windows_git_askpass as builder
from tools.build_windows_git_askpass import lfs_origin

ROOT = Path(__file__).resolve().parents[1]
FAKE_GCM = r'''
using System;
using System.IO;
using System.Text;
public static class FakeGcm {
    public static int Main(string[] args) {
        Console.OutputEncoding = new UTF8Encoding(false);
        File.WriteAllText(Environment.GetEnvironmentVariable("LFS_FIXTURE_MARKER"), "started");
        if (String.Join(" ", args) != "get") return 2;
        if (Console.In.ReadToEnd() != Environment.GetEnvironmentVariable("LFS_FIXTURE_QUERY")) return 3;
        if (Environment.GetEnvironmentVariable("GCM_INTERACTIVE") != "never" ||
            Environment.GetEnvironmentVariable("GIT_TERMINAL_PROMPT") != "0" ||
            Environment.GetEnvironmentVariable("GCM_GUI_PROMPT") != "false") return 4;
        Console.Error.WriteLine("dummy-private-diagnostic");
        string mode = Environment.GetEnvironmentVariable("LFS_FIXTURE_MODE");
        if (mode == "failed") return 1;
        if (mode == "unicode") { Console.Write("username=fixture 用户\npassword=dummy=口令\n"); return 0; }
        if (mode == "oversized") { Console.Write(new String('x', 9000)); return 0; }
        if (mode == "wrong_protocol") Console.Write("protocol=ftp\n");
        if (mode == "wrong_host") Console.Write("host=other.example\n");
        if (mode == "origin_fields") Console.Write("protocol=http\nhost=lfs.example:8080\n");
        Console.Write("username=fixture-user\n");
        if (mode != "missing") Console.Write("password=dummy-only=+\n");
        if (mode == "duplicate") Console.Write("username=other-user\n");
        if (mode == "malformed") Console.Write("malformed line\n");
        if (mode == "control") Console.Write("extra=bad\0value\n");
        return 0;
    }
}
'''
UTF8_CONSOLE = r'''
using System;
using System.Text;
public static class Utf8Console {
    public static int Main(string[] args) {
        Console.InputEncoding = Encoding.UTF8;
        var original = Console.InputEncoding;
        int result = FarmBotNativeGitAndLfsAskpass.Main(args);
        if (Console.InputEncoding.CodePage != original.CodePage ||
            Convert.ToBase64String(Console.InputEncoding.GetPreamble()) !=
            Convert.ToBase64String(original.GetPreamble())) return 2;
        return result;
    }
}
'''


class LfsOriginTests(unittest.TestCase):
    def test_origin_has_only_an_explicit_http_server(self):
        for value, expected in (("http://lfs.example:8080", "http://lfs.example:8080"),
                                ("https://LFS.example/", "https://lfs.example"),
                                ("https://lfs.example:443", "https://lfs.example")):
            with self.subTest(value=value):
                self.assertEqual(lfs_origin(value), expected)

    def test_credentials_paths_queries_and_github_are_refused(self):
        for value in ("lfs.example", "file:///tmp/a", "ftp://lfs.example", "http://", "http://u@lfs.example",
                      "http://lfs.example/repo", "http://lfs.example?x", "http://lfs.example#x",
                      "http://lfs.example?", "http://lfs.example#", "http://github.com:8080",
                      "https://GITHUB.COM", "https://github.com.", "http://lfs.example\n", "http://lfs.example\\repo",
                      "http://lfs.example:wrong", "http://lfs.example:0"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                lfs_origin(value)


@unittest.skipUnless(os.name == "nt", "native Windows LFS credential callback")
class NativeLfsAskpassTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="native LFS 回调 ")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
        if not cls.compiler.is_file():
            raise RuntimeError("Windows fixture requires the installed .NET Framework C# compiler")
        cls.sources = [ROOT / "tools/windows_git_askpass.cs", ROOT / "tools/windows_lfs_askpass.cs"]
        cls.helper = cls.root / "askpass.exe"
        cls.utf8_helper = cls.root / "utf8-askpass.exe"
        cls.gcm = cls.root / "git-credential-manager.exe"
        cls.gh = cls.root / "gh.exe"
        fake = cls.root / "dummy.cs"
        fake.write_text(FAKE_GCM, encoding="utf-8")
        console = cls.root / "utf8-console.cs"
        console.write_text(UTF8_CONSOLE, encoding="utf-8")
        from test_windows_git_askpass import FAKE_GH
        gh_source = cls.root / "dummy-gh.cs"
        gh_source.write_text(FAKE_GH, encoding="utf-8")
        for output, entry, sources in ((cls.helper, "FarmBotNativeGitAndLfsAskpass", cls.sources),
                                        (cls.utf8_helper, "Utf8Console", [*cls.sources, console]),
                                        (cls.gcm, "FakeGcm", [fake]), (cls.gh, "FakeGh", [gh_source])):
            subprocess.run([str(cls.compiler), "/nologo", "/target:exe", "/main:" + entry,
                            "/out:" + str(output), *(str(p) for p in sources)],
                           capture_output=True, check=True, timeout=30)
        cls.digest = hashlib.sha256(cls.gcm.read_bytes()).hexdigest()

    def setUp(self):
        self.marker = self.root / "gcm-started.txt"
        self.gh_marker = self.root / "gh-started.txt"
        self.marker.unlink(missing_ok=True)
        self.gh_marker.unlink(missing_ok=True)
        self.env = {**os.environ, "FARMBOT_NATIVE_GH": str(self.gh),
                    "ASKPASS_FIXTURE_MARKER": str(self.gh_marker), "ASKPASS_FIXTURE_MODE": "success",
                    "LFS_FIXTURE_MARKER": str(self.marker), "LFS_FIXTURE_MODE": "success",
                    "LFS_FIXTURE_QUERY": "protocol=http\nhost=lfs.example:8080\n\n",
                    "GCM_INTERACTIVE": "always", "GCM_GUI_PROMPT": "true", "GIT_TERMINAL_PROMPT": "1"}
        self.settings = self.helper.with_suffix(".lfs")
        self.pin()
        self.utf8_helper.with_suffix(".lfs").write_bytes(self.settings.read_bytes())

    def pin(self, origin="http://lfs.example:8080", executable=None, digest=None):
        self.settings.write_text(f"{origin}\n{executable or self.gcm}\n{digest or self.digest}\n", encoding="utf-8")

    def call(self, *args, executable=None):
        return subprocess.run([str(executable or self.helper), *args], env=self.env, capture_output=True,
                              encoding="utf-8", timeout=20)

    def assert_refused(self, result, lookup=False):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (1, "", ""))
        self.assertEqual(self.marker.exists(), lookup)
        self.assertFalse(self.gh_marker.exists())

    def test_server_only_lookup_returns_only_requested_field(self):
        for prompt, expected in (("Username for 'http://lfs.example:8080/repo/info/lfs': ", "fixture-user\n"),
                                 ("Password for 'http://fixture-user@lfs.example:8080/repo': ", "dummy-only=+\n")):
            with self.subTest(prompt=prompt):
                result = self.call(prompt)
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, expected, ""))
                self.assertTrue(self.marker.exists())
                self.assertFalse(self.gh_marker.exists())

    def test_utf8_console_does_not_add_bom_or_change_input_encoding(self):
        result = self.call("Password for 'http://fixture-user@lfs.example:8080': ", executable=self.utf8_helper)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "dummy-only=+\n", ""))

    def test_https_origin_and_unicode_credentials(self):
        self.pin("https://lfs.example")
        self.env["LFS_FIXTURE_QUERY"] = "protocol=https\nhost=lfs.example\n\n"
        self.env["LFS_FIXTURE_MODE"] = "unicode"
        for prompt, expected in (("Username for 'https://lfs.example/repo': ", "fixture 用户\n"),
                                 ("Password for 'https://fixture%20%E7%94%A8%E6%88%B7@lfs.example': ", "dummy=口令\n")):
            with self.subTest(prompt=prompt):
                result = self.call(prompt)
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, expected, ""))

    def test_native_short_directory_alias_names_the_same_pinned_executable(self):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        short = kernel.GetShortPathNameW
        short.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32)
        short.restype = ctypes.c_uint32
        buffer = ctypes.create_unicode_buffer(32768)
        size = short(str(self.root), buffer, len(buffer))
        self.assertTrue(size and size < len(buffer), "Windows fixture needs a native short directory alias")
        executable = Path(buffer.value) / self.gcm.name
        self.assertEqual(executable.read_bytes(), self.gcm.read_bytes())
        self.pin(executable=executable)
        result = self.call("Username for 'http://lfs.example:8080': ")
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "fixture-user\n", ""))

    def test_reparse_pins_and_drive_relative_paths_are_refused_before_lookup(self):
        original = self.root / "original-pin.lfs"
        self.settings.rename(original)
        try:
            self.settings.symlink_to(original)
            self.assert_refused(self.call("Username for 'http://lfs.example:8080': "))
        finally:
            self.settings.unlink(missing_ok=True)
            original.rename(self.settings)
        link = self.root / "linked-gcm-directory"
        link.symlink_to(self.root, target_is_directory=True)
        try:
            self.pin(executable=link / self.gcm.name)
            self.assert_refused(self.call("Username for 'http://lfs.example:8080': "))
        finally:
            link.unlink()
        for executable in (str(self.gcm)[2:], self.gcm.drive + str(self.gcm)[3:]):
            self.pin(executable=executable)
            self.assert_refused(self.call("Username for 'http://lfs.example:8080': "))

    def test_scheme_host_port_and_prompt_are_checked_before_lookup(self):
        for prompt in ("Username for 'https://lfs.example:8080': ", "Username for 'http://other.example:8080': ",
                       "Username for 'http://lfs.example': ", "Username for 'http://lfs.example:8080?x': ",
                       "Username for 'http://lfs.example:8080#x': ", "Username for 'http://u@lfs.example:8080': ",
                       "Username for 'http://lfs.example:8080\\repo': ", "arbitrary", "Username for 'http://lfs.example:8080': \n"):
            with self.subTest(prompt=prompt):
                self.assert_refused(self.call(prompt))

    def test_argument_count_is_checked_before_lookup(self):
        for args in ((), ("Username for 'http://lfs.example:8080': ", "extra")):
            self.assert_refused(self.call(*args))

    def test_missing_malformed_and_replaced_pins_fail_before_lookup(self):
        for contents in (None, b"bad\n", b"x" * 17000, self.settings.read_bytes() + b"extra\n",
                         b"\xef\xbb\xbf" + self.settings.read_bytes()):
            with self.subTest(contents_size=None if contents is None else len(contents)):
                self.settings.unlink(missing_ok=True)
                if contents is not None:
                    self.settings.write_bytes(contents)
                self.assert_refused(self.call("Username for 'http://lfs.example:8080': "))
        for origin, executable, digest in (("http://github.com", self.gcm, self.digest),
                                           ("http://lfs.example:8080/path", self.gcm, self.digest),
                                           ("http://lfs.example:8080", self.gh, self.digest),
                                           ("http://lfs.example:8080", self.gcm, "0" * 64)):
            self.pin(origin, executable, digest)
            self.assert_refused(self.call("Username for 'http://lfs.example:8080': "))

    def test_environment_cannot_redirect_pinned_lfs_lookup(self):
        self.env.update(FARMBOT_NATIVE_LFS_ORIGIN="http://other.example", FARMBOT_NATIVE_LFS_GCM=str(self.gh))
        result = self.call("Username for 'http://lfs.example:8080': ")
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "fixture-user\n", ""))
        self.assertTrue(self.marker.exists())
        self.assertFalse(self.gh_marker.exists())

    def test_failed_and_malformed_responses_are_quiet(self):
        for mode in ("failed", "oversized", "missing", "duplicate", "wrong_protocol", "wrong_host", "malformed", "control"):
            with self.subTest(mode=mode):
                self.env["LFS_FIXTURE_MODE"] = mode
                self.assert_refused(self.call("Username for 'http://lfs.example:8080': "), lookup=True)

    def test_origin_fields_are_accepted_when_they_match(self):
        self.env["LFS_FIXTURE_MODE"] = "origin_fields"
        result = self.call("Username for 'http://lfs.example:8080': ")
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "fixture-user\n", ""))

    def test_password_username_must_match_returned_identity(self):
        self.assert_refused(self.call("Password for 'http://other-user@lfs.example:8080': "), lookup=True)

    def test_github_route_cannot_fall_back_to_lfs_credentials(self):
        self.settings.unlink()
        result = self.call("Password for 'https://fixture-user@github.com': ")
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "dummy-only\n", ""))
        self.assertTrue(self.gh_marker.exists())
        self.assertFalse(self.marker.exists())
        self.env["ASKPASS_FIXTURE_MODE"] = "failed"
        result = self.call("Username for 'https://github.com': ")
        self.assertEqual((result.returncode, result.stdout, result.stderr), (1, "", ""))
        self.assertFalse(self.marker.exists())

    def build(self, output, *options):
        with mock.patch.object(builder.sys, "argv", ["builder", "--output-directory", str(output), *options]), \
                mock.patch.object(builder.shutil, "which", return_value=str(self.gh)), \
                mock.patch.object(builder, "selector", side_effect=str), \
                mock.patch.dict(os.environ, PYTHONUTF8="1"), contextlib.redirect_stdout(io.StringIO()) as stdout:
            result = builder.main()
        self.assertEqual(result, 0)
        return json.loads(stdout.getvalue()), json.loads((output / "receipt-private.json").read_text(encoding="utf-8"))

    def test_optional_build_pins_origin_executable_and_source_identities(self):
        output = self.root / "optional-build"
        public, receipt = self.build(output, "--lfs-origin", "http://LFS.example:8080/",
                                     "--gcm-executable", str(self.gcm))
        self.assertTrue(public["lfs_origin_pinned"])
        self.assertFalse(public["credentials_configured"])
        settings = output / "askpass.lfs"
        self.assertEqual(settings.read_text(encoding="utf-8"), f"http://lfs.example:8080\n{self.gcm}\n{self.digest}\n")
        self.assertEqual(receipt["gcm_sha256"], self.digest)
        self.assertEqual(receipt["lfs_settings_sha256"], hashlib.sha256(settings.read_bytes()).hexdigest())
        self.assertEqual(receipt["lfs_source_sha256"], hashlib.sha256(self.sources[1].read_bytes()).hexdigest())
        result = self.call("Username for 'http://lfs.example:8080/repo': ", executable=output / "askpass.exe")
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "fixture-user\n", ""))
        self.assertNotIn("lfs.example", json.dumps(public))
        self.assertNotIn("dummy-only", json.dumps(receipt))

    def test_default_build_remains_github_only(self):
        output = self.root / "github-only-build"
        public, receipt = self.build(output)
        self.assertFalse(public["lfs_origin_pinned"])
        self.assertNotIn("gcm_sha256", receipt)
        self.assertFalse((output / "askpass.lfs").exists())
        self.assert_refused(self.call("Username for 'http://lfs.example:8080': ", executable=output / "askpass.exe"))
        result = self.call("Username for 'https://github.com': ", executable=output / "askpass.exe")
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "fixture-user\n", ""))

    def test_incomplete_or_invalid_lfs_selection_creates_no_output(self):
        output = self.root / "refused-build"
        for options in (("--lfs-origin", "http://lfs.example"), ("--gcm-executable", str(self.gcm)),
                        ("--lfs-origin", "http://github.com", "--gcm-executable", str(self.gcm)),
                        ("--lfs-origin", "http://lfs.example", "--gcm-executable", str(self.gh))):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.build(output, *options)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
