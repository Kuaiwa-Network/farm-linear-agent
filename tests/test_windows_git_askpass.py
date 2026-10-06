"""Native callback protocol tests with a compiled dummy gh; never uses host auth."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FAKE_GH = r'''
using System;
using System.IO;
public static class FakeGh {
    public static int Main(string[] args) {
        File.WriteAllText(Environment.GetEnvironmentVariable("ASKPASS_FIXTURE_MARKER"), "started");
        if (String.Join(" ", args) != "auth git-credential get") return 2;
        if (Console.In.ReadToEnd() != "protocol=https\nhost=github.com\n\n") return 3;
        Console.Error.WriteLine("dummy-private-error");
        string mode = Environment.GetEnvironmentVariable("ASKPASS_FIXTURE_MODE");
        if (mode == "failed") return 1;
        if (mode == "oversized") { Console.Write(new String('x', 9000)); return 0; }
        Console.Write("protocol=https\nhost=github.com\nusername=fixture-user\n");
        if (mode != "missing") Console.Write("password=dummy-only\n");
        if (mode == "duplicate") Console.Write("username=other-user\n");
        if (mode == "wrong_host") Console.Write("host=other.example\n");
        if (mode == "malformed") Console.Write("malformed line\n");
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
        Console.OutputEncoding = Encoding.UTF8;
        var original = Console.InputEncoding;
        int result = FarmBotNativeGitAskpass.Main(args);
        if (Console.InputEncoding.CodePage != original.CodePage ||
            Convert.ToBase64String(Console.InputEncoding.GetPreamble()) !=
            Convert.ToBase64String(original.GetPreamble())) return 2;
        return result;
    }
}
'''


@unittest.skipUnless(os.name == "nt", "native Windows credential callback")
class NativeGitAskpassTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="native Git 回调 ")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
        if not compiler.is_file():
            raise RuntimeError("Windows fixture requires the installed .NET Framework C# compiler")
        cls.helper = cls.root / "askpass.exe"
        cls.gh = cls.root / "gh.exe"
        fake = cls.root / "dummy.cs"
        fake.write_text(FAKE_GH, encoding="utf-8")
        for source, output in ((ROOT / "tools/windows_git_askpass.cs", cls.helper), (fake, cls.gh)):
            subprocess.run([str(compiler), "/nologo", "/target:exe", "/out:" + str(output), str(source)],
                           capture_output=True, check=True, timeout=30)
        cls.utf8_helper = cls.root / "utf8-askpass.exe"
        console = cls.root / "utf8-console.cs"
        console.write_text(UTF8_CONSOLE, encoding="utf-8")
        subprocess.run([str(compiler), "/nologo", "/target:exe", "/main:Utf8Console",
                        "/out:" + str(cls.utf8_helper), str(ROOT / "tools/windows_git_askpass.cs"), str(console)],
                       capture_output=True, check=True, timeout=30)

    def setUp(self):
        self.marker = self.root / "dummy-started.txt"
        self.marker.unlink(missing_ok=True)
        self.env = {**os.environ, "FARMBOT_NATIVE_GH": str(self.gh),
                    "ASKPASS_FIXTURE_MARKER": str(self.marker), "ASKPASS_FIXTURE_MODE": "success"}

    def call(self, *args):
        return subprocess.run([str(self.helper), *args], env=self.env, capture_output=True,
                              encoding="utf-8-sig", timeout=20)

    def assert_refused(self, result):
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_username_and_password_are_only_the_requested_field(self):
        for prompt, expected in (("Username for 'https://github.com': ", "fixture-user\n"),
                                 ("Password for 'https://fixture-user@github.com': ", "dummy-only\n")):
            with self.subTest(prompt=prompt):
                result = self.call(prompt)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stdout, expected)
                self.assertEqual(result.stderr, "")
                self.assertTrue(self.marker.exists())

    def test_utf8_console_does_not_add_a_bom_to_the_credential_request(self):
        for prompt, expected in (("Username for 'https://github.com': ", "fixture-user\n"),
                                 ("Password for 'https://fixture-user@github.com': ", "dummy-only\n")):
            with self.subTest(prompt=prompt):
                result = subprocess.run([str(self.utf8_helper), prompt], env=self.env, capture_output=True,
                                        encoding="utf-8", timeout=20)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stdout, expected)
                self.assertEqual(result.stderr, "")
                self.assertTrue(self.marker.exists())

    def test_wrong_destination_or_prompt_never_starts_credential_lookup(self):
        prompts = ("Username for 'http://github.com': ", "Username for 'https://github.com.other.example': ",
                   "Password for 'https://github.com:444': ", "Username for 'https://github.com?token=x': ",
                   "Username for 'https://github.com#fragment': ", "Username for 'https://user@github.com': ",
                   "Password for 'https://other.example': ", "unexpected input")
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                self.assert_refused(self.call(prompt))
                self.assertFalse(self.marker.exists())

    def test_invalid_argument_count_never_starts_credential_lookup(self):
        for args in ((), ("one", "two")):
            self.assert_refused(self.call(*args))
            self.assertFalse(self.marker.exists())

    def test_unavailable_or_wrong_executable_never_starts_credential_lookup(self):
        for exe in ("", "relative/gh.exe", str(self.root / "missing" / "gh.exe"), str(self.helper)):
            with self.subTest(exe=exe):
                self.env["FARMBOT_NATIVE_GH"] = exe
                self.assert_refused(self.call("Username for 'https://github.com': "))
                self.assertFalse(self.marker.exists())

    def test_failed_or_malformed_lookup_never_outputs_a_credential_or_diagnostic(self):
        for mode in ("failed", "oversized", "missing", "duplicate", "wrong_host", "malformed"):
            with self.subTest(mode=mode):
                self.env["ASKPASS_FIXTURE_MODE"] = mode
                self.assert_refused(self.call("Password for 'https://fixture-user@github.com': "))

    def test_password_for_another_user_is_refused_without_output(self):
        self.assert_refused(self.call("Password for 'https://other-user@github.com': "))


if __name__ == "__main__":
    unittest.main()
