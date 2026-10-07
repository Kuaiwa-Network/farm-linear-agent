"""Native binary filter transport and Git integration; no network or host credentials."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from tools.build_windows_lfs_filter import selector

ROOT = Path(__file__).resolve().parents[1]
FAKE = r'''
using System;
using System.Diagnostics;
using System.IO;
using System.Threading;
public static class FakeLfs {
    public static int Main(string[] args) {
        if (String.Join(" ", args) != "filter-process") return 2;
        File.WriteAllText(Environment.GetEnvironmentVariable("FILTER_FIXTURE_MARKER"), Process.GetCurrentProcess().Id.ToString());
        string mode = Environment.GetEnvironmentVariable("FILTER_FIXTURE_MODE");
        if (mode == "failed") return 23;
        if (mode == "hold") { Thread.Sleep(30000); return 0; }
        Stream input = Console.OpenStandardInput(), output = Console.OpenStandardOutput();
        byte[] buffer = new byte[17]; int size;
        while ((size = input.Read(buffer, 0, buffer.Length)) != 0) {
            output.Write(buffer, 0, size); output.Flush();
        }
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
        Encoding original = Console.InputEncoding;
        int result = FarmBotNativeLfsFilter.Main(args);
        if (Console.InputEncoding.CodePage != original.CodePage ||
            Convert.ToBase64String(Console.InputEncoding.GetPreamble()) !=
            Convert.ToBase64String(original.GetPreamble())) return 2;
        return result;
    }
}
'''


class FilterSelectorTests(unittest.TestCase):
    def test_absolute_native_path_supports_unicode_without_shell_characters(self):
        self.assertEqual(selector(r'C:\tools\原生\farmbot-lfs-filter.exe'), 'C:/tools/原生/farmbot-lfs-filter.exe')

    def test_spaces_short_aliases_arguments_and_shell_characters_are_refused(self):
        for value in ('relative.exe', '/tmp/filter.exe', 'C:/Program Files/filter.exe', 'C:/PROGRA~1/filter.exe',
                      'C:/tools/filter.exe filter-process', 'C:/tools/a&b.exe', 'C:/tools/a%b.exe',
                      'C:/tools/a[b.exe', 'C:/tools/a#b.exe', 'C:/tools/a\nb.exe', 'C:/tools/a\u00a0b.exe'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                selector(value)


@unittest.skipUnless(os.name == 'nt', 'native Windows Git LFS filter adapter')
class NativeLfsFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='native-lfs-filter-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name).resolve()
        cls.compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        if not cls.compiler.is_file():
            raise RuntimeError('Native filter fixtures require the installed Windows C# compiler')
        cls.helper = cls.root / 'farmbot-lfs-filter.exe'
        cls.fake = cls.root / 'git-lfs.exe'
        source = cls.root / 'fake.cs'; source.write_text(FAKE, encoding='utf-8')
        for target, source in ((cls.helper, ROOT / 'tools/windows_lfs_filter.cs'), (cls.fake, source)):
            subprocess.run([str(cls.compiler), '/nologo', '/target:exe', '/out:' + str(target), str(source)],
                           capture_output=True, check=True, timeout=30)

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix='case-', dir=self.root))
        self.marker = self.work / 'started.txt'
        self.settings = self.helper.with_suffix('.lfs-filter')
        self.env = dict(os.environ, FILTER_FIXTURE_MARKER=str(self.marker), FILTER_FIXTURE_MODE='echo', PYTHONUTF8='1')
        self.pin()

    def pin(self, executable=None, digest=None, settings=None):
        executable = executable or self.fake
        digest = digest or hashlib.sha256(executable.read_bytes()).hexdigest()
        (settings or self.settings).write_text(f'{executable}\n{digest}\n', encoding='utf-8', newline='\n')

    def call(self, data=b'', *args, helper=None):
        return subprocess.run([str(helper or self.helper), *args], input=data, env=self.env, capture_output=True, timeout=15)

    def refused(self, result):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (1, b'', b''))
        self.assertFalse(self.marker.exists())

    def test_fragmented_binary_stream_is_preserved_without_text_conversion(self):
        data = bytes(range(256)) * 700 + b'\x00\xff\r\n\xe4\xb8\xad'
        result = self.call(data)
        self.assertEqual((result.returncode, result.stderr), (0, b''))
        self.assert_binary_equal(result.stdout, data)

    def assert_binary_equal(self, actual, expected):
        # Exact equality, with bounded diagnostics instead of quadratic difflib
        # formatting for large repetitive binary inputs after a mismatch.
        self.assertTrue(actual == expected,
                        'binary bytes differ: actual size={} sha256={}, expected size={} sha256={}'.format(
                            len(actual), hashlib.sha256(actual).hexdigest(),
                            len(expected), hashlib.sha256(expected).hexdigest()))

    def test_utf8_console_adds_no_preamble_and_restores_input_encoding(self):
        source = self.work / 'utf8.cs'; source.write_text(UTF8_CONSOLE, encoding='utf-8')
        helper = self.work / 'utf8-filter.exe'
        subprocess.run([str(self.compiler), '/nologo', '/target:exe', '/main:Utf8Console', '/out:' + str(helper),
                        str(ROOT / 'tools/windows_lfs_filter.cs'), str(source)], capture_output=True, check=True, timeout=30)
        self.pin(settings=helper.with_suffix('.lfs-filter'))
        data = bytes(range(256)) * 700 + b'\x00\xff\r\n\xe4\xb8\xad'
        result = self.call(data, helper=helper)
        self.assertEqual((result.returncode, result.stderr), (0, b''))
        self.assert_binary_equal(result.stdout, data)

    def test_child_failure_code_is_preserved(self):
        self.env['FILTER_FIXTURE_MODE'] = 'failed'
        result = self.call()
        self.assertEqual((result.returncode, result.stdout, result.stderr), (23, b'', b''))

    def test_arguments_are_refused_before_starting_selected_executable(self):
        self.refused(self.call(b'', 'filter-process'))

    def test_missing_malformed_oversized_or_bom_settings_are_refused(self):
        self.settings.unlink()
        self.refused(self.call())
        for data in (b'bad\n', b'x' * 8193, b'\xef\xbb\xbf' + str(self.fake).encode() + b'\n' + b'0' * 64 + b'\n',
                     str(self.fake).encode() + b'\n' + b'0' * 64 + b'\nextra\n'):
            with self.subTest(size=len(data)):
                self.settings.write_bytes(data); self.refused(self.call())

    def test_changed_executable_hash_is_refused_without_diagnostics(self):
        self.pin(digest='0' * 64); self.refused(self.call())

    def test_reparse_settings_are_refused(self):
        real = self.work / 'real.settings'; real.write_bytes(self.settings.read_bytes())
        self.settings.unlink(); self.settings.symlink_to(real)
        try: self.refused(self.call())
        finally: self.settings.unlink()

    def test_reparse_target_executable_is_refused(self):
        alias = self.work / 'git-lfs.exe'; alias.symlink_to(self.fake)
        self.pin(executable=alias); self.refused(self.call())

    def test_pinned_child_and_adapter_support_spaces_and_unicode(self):
        child_root = self.work / 'child 原生 路径'; child_root.mkdir()
        child = child_root / 'git-lfs.exe'; shutil.copy2(self.fake, child)
        helper = child_root / 'farmbot-lfs-filter.exe'; shutil.copy2(self.helper, helper)
        self.pin(executable=child, settings=helper.with_suffix('.lfs-filter'))
        result = self.call(b'\x00native\xff', helper=helper)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'\x00native\xff', b''))

    def test_adapter_and_native_child_remain_in_assigned_job_and_drain(self):
        from agent.windows_job import WindowsJob, process_ids
        job = WindowsJob(); self.addCleanup(job.close)
        self.env['FILTER_FIXTURE_MODE'] = 'hold'
        process = subprocess.Popen([str(self.helper)], env=self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, creationflags=0x00000004)  # CREATE_SUSPENDED
        try:
            job.assign(process); job.resume(process)
            deadline = time.monotonic() + 5
            while not self.marker.exists() and time.monotonic() < deadline: time.sleep(.02)
            self.assertTrue(self.marker.exists())
            child = int(self.marker.read_text(encoding='utf-8'))
            members = process_ids(job.handle)
            self.assertIn(process.pid, members); self.assertIn(child, members)
            job.terminate_and_wait()
            process.communicate(timeout=5)
            self.assertTrue(WindowsJob.empty(job.name))
        finally:
            job.terminate_and_wait(); process.communicate(timeout=5)

    def git_env(self, selected=True):
        env = {k: v for k, v in self.env.items() if not k.upper().startswith('GIT_')}
        env.update(GIT_CONFIG_GLOBAL='NUL', GIT_CONFIG_SYSTEM='NUL', GIT_CONFIG_NOSYSTEM='1', GIT_TERMINAL_PROMPT='0',
                   GIT_LFS_SKIP_SMUDGE='1')
        if selected:
            env.update(GIT_CONFIG_COUNT='2', GIT_CONFIG_KEY_0='filter.lfs.process', GIT_CONFIG_VALUE_0=selector(self.helper),
                       GIT_CONFIG_KEY_1='filter.lfs.required', GIT_CONFIG_VALUE_1='true')
        return env

    def real_lfs(self):
        value = shutil.which('git-lfs.exe')
        if not value: raise RuntimeError('Native filter integration requires installed Git LFS')
        return Path(value).resolve()

    def test_actual_git_clean_and_stat_refresh_preserve_source_with_no_helper_shell(self):
        self.pin(executable=self.real_lfs())
        original = self.work / 'source 原图'; original.mkdir()
        env = self.git_env(); env['GIT_TRACE'] = '1'
        traces = []
        def git(cwd, *args, environment=env, allowed=(0,)):
            result = subprocess.run(['git', '-c', 'core.hooksPath=NUL', '-c', 'core.fsmonitor=false',
                                     '-c', 'user.name=Offline filter fixture', '-c', 'user.email=offline@example.invalid', *args],
                                    cwd=cwd, env=environment, capture_output=True, timeout=30)
            self.assertIn(result.returncode, allowed, result.stderr.decode('utf-8', errors='replace'))
            traces.append(result.stderr.decode('utf-8', errors='replace'))
            return result.stdout.decode('utf-8').strip()
        git(original, 'init', '-q')
        (original / '.gitattributes').write_text('*.png filter=lfs diff=lfs merge=lfs -text\n', encoding='utf-8')
        data = bytes(range(256)) * 100
        (original / 'image.png').write_bytes(data)
        (original / 'layout.xml').write_text('<text fontSize="42"/>\n', encoding='utf-8')
        git(original, 'add', '--', '.gitattributes', 'image.png', 'layout.xml'); git(original, 'commit', '-qm', 'offline baseline')
        tree = git(original, 'rev-parse', 'HEAD^{tree}')
        smudge = subprocess.run(['git', '-c', 'core.hooksPath=NUL', '-c', 'core.fsmonitor=false',
                                 'cat-file', '--filters', 'HEAD:image.png'], cwd=original,
                                env={**env, 'GIT_LFS_SKIP_SMUDGE': '0'}, capture_output=True, timeout=30)
        self.assertEqual((smudge.returncode, smudge.stdout), (0, data))
        traces.append(smudge.stderr.decode('utf-8', errors='replace'))
        materialized = self.work / 'pointer clone 中文'
        git(self.work, 'clone', '-q', '--local', '--no-hardlinks', str(original), str(materialized), environment=self.git_env(False))
        self.assertTrue((materialized / 'image.png').read_bytes().startswith(b'version https://git-lfs'))
        (materialized / 'image.png').write_bytes(data)
        (materialized / 'layout.xml').write_text('<text fontSize="40"/>\n', encoding='utf-8')
        expected = git(materialized, 'rev-parse', 'HEAD:image.png')
        self.assertEqual(git(materialized, 'hash-object', '--path=image.png', 'image.png'), expected)
        git(materialized, 'update-index', '--really-refresh', '--', 'image.png', allowed=(0, 1))
        self.assertEqual(git(materialized, 'status', '--porcelain'), 'M layout.xml')
        self.assertEqual(git(materialized, 'write-tree'), tree)
        self.assertEqual((materialized / 'image.png').read_bytes(), data)
        self.assertIn('fontSize="40"', (materialized / 'layout.xml').read_text(encoding='utf-8'))
        trace = '\n'.join(traces)
        self.assertIn(selector(self.helper), trace)
        self.assertNotRegex(trace, r'(?i)(?:sh\.exe|[/\\]sh(?:\s|\x27|\x22)|\bsh -c)')

    def test_builder_pins_existing_lfs_without_installing_configuration(self):
        output = self.work / 'built'
        result = subprocess.run([sys.executable, '-B', str(ROOT / 'tools/build_windows_lfs_filter.py'),
                                 '--output-directory', str(output), '--lfs-executable', str(self.real_lfs())],
                                env=self.env, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads((output / 'receipt-private.json').read_text(encoding='utf-8'))
        self.assertEqual(receipt['helper_sha256'], hashlib.sha256((output / 'farmbot-lfs-filter.exe').read_bytes()).hexdigest())
        self.assertEqual(receipt['lfs_sha256'], hashlib.sha256(self.real_lfs().read_bytes()).hexdigest())
        self.assertNotIn(b'client_secret', result.stdout)

    def test_builder_refuses_existing_and_unsafe_output_before_writing(self):
        for output in (self.work, self.work / 'unsafe space', self.work / 'unsafe~alias'):
            with self.subTest(name=output.name):
                existed = output.exists()
                result = subprocess.run([sys.executable, '-B', str(ROOT / 'tools/build_windows_lfs_filter.py'),
                                         '--output-directory', str(output), '--lfs-executable', str(self.real_lfs())],
                                        env=self.env, capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(output.exists(), existed)
                self.assertTrue(json.loads(result.stderr)['build_stopped'])


if __name__ == '__main__': unittest.main()
