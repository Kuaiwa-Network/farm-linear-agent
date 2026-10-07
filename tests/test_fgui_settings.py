"""Explicit host schema and sanitized read-only publisher diagnostics."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent.config import Config
from agent.fgui_publisher import TOOL_FILES
from agent.fgui_settings import publisher_diagnostic, validate_settings


class SettingsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="UI tools 中文 ")
        self.addCleanup(temp.cleanup); self.root = Path(temp.name).resolve()
        self.pins = {}
        for name in TOOL_FILES:
            data = ("fixture:" + name).encode("utf-8"); (self.root / name).write_bytes(data)
            self.pins[name] = hashlib.sha256(data).hexdigest()
        self.block = {"executable": str(self.root / "FairyGUI-Editor.exe"), "tool_sha256": self.pins}

    def test_default_is_optional_and_never_selects_an_executable(self):
        self.assertEqual(Config("c", "s", "w").fgui_export, {})
        with patch("agent.fgui_settings.validate_tool") as tool:
            self.assertFalse(publisher_diagnostic({})["ok"]); tool.assert_not_called()

    def test_config_accepts_exact_native_selection_and_bounded_timeout(self):
        self.block["timeout_seconds"] = 90
        self.assertEqual(Config("c", "s", "w", fgui_export=self.block).fgui_export, self.block)

    def test_unknown_fields_or_inline_secret_are_refused_without_repeating_value(self):
        self.block["secret"] = "private-value-never-echo"
        with self.assertRaises(ValueError) as result:
            Config("c", "s", "w", fgui_export=self.block)
        self.assertNotIn("private-value", str(result.exception))

    def test_relative_wrong_executable_and_nonstring_path_refuse(self):
        for name in ("FairyGUI-Editor.exe", str(self.root / "other.exe"), None, False):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_settings({**self.block, "executable": name})

    def test_pin_set_and_each_digest_must_be_exact(self):
        for pins in ({}, {**self.pins, "unexpected.dll": "a"*64}, {**self.pins, "UnityPlayer.dll": "bad"}):
            with self.subTest(pins=pins), self.assertRaises(ValueError):
                validate_settings({**self.block, "tool_sha256": pins})

    def test_invalid_timeouts_refuse_without_clamping(self):
        for value in (0, -1, 181, True, "180", float("inf"), float("nan")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_settings({**self.block, "timeout_seconds": value})

    def test_nonwindows_diagnostic_is_pending_without_reading_or_executing_tool(self):
        with patch("agent.fgui_settings._native_windows", return_value=False), patch("agent.fgui_settings.validate_tool") as tool:
            result = publisher_diagnostic(self.block); tool.assert_not_called()
        self.assertFalse(result["ok"]); self.assertNotIn(str(self.root), str(result))

    def test_readonly_diagnostic_checks_all_four_hashes_without_process_execution(self):
        with patch("agent.fgui_settings._native_windows", return_value=True), patch("subprocess.run") as run:
            result = publisher_diagnostic(self.block); run.assert_not_called()
        self.assertTrue(result["ok"]); self.assertEqual(result["tool_sha256"], self.pins)
        self.assertNotIn(str(self.root), str(result)); self.assertIn("license acceptance remain separate", result["reason"])

    def test_missing_or_changed_binary_is_a_sanitized_gap(self):
        (self.root / "UnityPlayer.dll").write_bytes(b"changed")
        with patch("agent.fgui_settings._native_windows", return_value=True):
            result = publisher_diagnostic(self.block)
        self.assertFalse(result["ok"]); self.assertNotIn(str(self.root), str(result))
        (self.root / "UnityPlayer.dll").unlink()
        with patch("agent.fgui_settings._native_windows", return_value=True):
            self.assertFalse(publisher_diagnostic(self.block)["ok"])

    def test_returned_diagnostic_cannot_mutate_configured_hashes(self):
        with patch("agent.fgui_settings._native_windows", return_value=False):
            result = publisher_diagnostic(self.block)
        result["tool_sha256"]["UnityPlayer.dll"] = "b" * 64
        self.assertEqual(self.block["tool_sha256"], self.pins)
