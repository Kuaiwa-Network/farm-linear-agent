"""Portable command bounds without rejecting long literal Go test filters."""
import subprocess
import unittest

from agent.integration import validate_argv


class CommandArgumentTests(unittest.TestCase):
    def test_long_literal_argument_and_full_package_selection_fit(self):
        argv = ['go', 'test', '-run', '^(Test' + 'x' * 6400 + ')$']
        argv += ['./package' + str(i) for i in range(128)]
        self.assertEqual(validate_argv(argv), argv)

    def test_total_limit_counts_windows_quoting_and_utf16_including_terminator(self):
        self.assertEqual(validate_argv(['x' * 29999]), ['x' * 29999])
        for argv in (['x' * 30000], ['x' * 16000, 'y' * 16000],
                     ['"' * 15000], ['\U0001f33e' * 15000], ['x'] * 257):
            with self.subTest(lengths=[len(arg) for arg in argv]):
                with self.assertRaises(ValueError):
                    validate_argv(argv)
        argv = ['C:\\tools with spaces\\go.exe', '测试', 'a"b\\', '\U0001f33e']
        self.assertLess(len(subprocess.list2cmdline(argv).encode('utf-16-le')) // 2 + 1, 30000)
        self.assertEqual(validate_argv(argv), argv)

    def test_invalid_shapes_and_nul_are_rejected_without_echoing_arguments(self):
        for argv in ('go test', [], [''], [None], ['private\0value']):
            with self.subTest(argv=argv), self.assertRaises(ValueError) as error:
                validate_argv(argv)
            self.assertNotIn('private', str(error.exception))
