"""Claim-fenced diagnostics with temporary profiles and synthetic Windows credentials only."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from agent import lark_diagnostics as diagnostics
from agent.__main__ import parser, run
from agent.config import Config, Paths
from agent.ledger import Ledger, LedgerError
from test_ledger import ISSUE, issue


class ProfileStatusTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="飞书 profile ")
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.path = self.home / ".lark-cli" / "config.json"
        self.path.parent.mkdir()
        self.app = {"name": "farmbot", "appId": "cli_dummy", "strictMode": "bot", "users": [],
                    "appSecret": {"source": "keychain", "id": "appsecret:cli_dummy"}}
        self.store = self.enterContext(patch.object(diagnostics, "_windows_keychain_status"))
        self.enterContext(patch.object(diagnostics, "_WINDOWS", True))

    def check(self, config, block=None):
        self.path.write_text(json.dumps(config), encoding="utf-8")
        return diagnostics.profile_status(block or {"profile": "farmbot"}, {"USERPROFILE": str(self.home)})

    def test_only_selected_isolated_bot_profile_reaches_native_store(self):
        self.store.return_value = {"status": "available", "stage": "dpapi_decrypt", "credential_available": True}
        for selector in ("farmbot", "cli_dummy"):
            with self.subTest(selector=selector):
                result = self.check({"apps": [self.app]}, {"profile": selector})
                self.assertEqual(result, self.store.return_value)
                self.store.assert_called_with("appsecret:cli_dummy")
                self.assertNotIn("cli_dummy", json.dumps(result))

    def test_unsupported_routes_do_not_read_a_profile_or_store(self):
        for block in ({}, {"profile": "farmbot", "home": "/other"},
                      {"app_id": "cli_dummy", "secret_env": "DUMMY_SECRET"}):
            with self.subTest(block=block), patch.object(Path, "open", side_effect=AssertionError("profile read")):
                self.assertEqual(diagnostics.profile_status(block)["status"], "unsupported")
        with patch.object(diagnostics, "_WINDOWS", False):
            self.assertEqual(diagnostics.profile_status({"profile": "farmbot"})["status"], "unsupported")
        self.store.assert_not_called()

    def test_bad_profiles_fail_before_any_credential_lookup(self):
        configs = [[], {}, {"apps": None}, {"apps": []}, {"apps": [self.app, self.app]},
                   {"apps": [None]}]
        for changes in ({"name": "other"}, {"strictMode": "user"}, {"users": [{}]},
                        {"appId": ""}, {"appSecret": "dummy-inline-secret"},
                        {"appSecret": {"source": "file", "id": "dummy-private-path"}},
                        {"appSecret": {"source": "keychain", "id": "appsecret:other"}}):
            configs.append({"apps": [{**self.app, **changes}]})
        for config in configs:
            with self.subTest(config=config):
                result = self.check(config)
                self.assertEqual(result["status"], "unavailable")
                self.assertFalse(result["credential_available"])
                self.assertNotRegex(json.dumps(result), "cli_dummy|dummy-inline-secret|dummy-private-path")
        self.store.assert_not_called()

    def test_missing_malformed_and_oversized_profile_are_sanitized(self):
        for data in (None, b'{"secret": "dummy-private-value", invalid}', b" " * (1024 * 1024 + 1)):
            with self.subTest(size=None if data is None else len(data)):
                if data is not None:
                    self.path.write_bytes(data)
                result = diagnostics.profile_status({"profile": "farmbot"}, {"USERPROFILE": str(self.home)})
                self.assertEqual(result["status"], "unavailable")
                self.assertNotIn("dummy-private-value", json.dumps(result))
                self.assertNotIn(str(self.home), json.dumps(result))
        self.store.assert_not_called()

    def test_missing_or_relative_user_profile_never_reads_current_directory(self):
        for env in ({}, {"USERPROFILE": "."}):
            with self.subTest(env=env), patch.object(Path, "open", side_effect=AssertionError("profile read")):
                self.assertEqual(diagnostics.profile_status({"profile": "farmbot"}, env)["stage"], "profile_path")


class DocumentReaderCliTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="reader claim ")
        self.addCleanup(tmp.cleanup)
        self.config = Config("c", "s", "w", local_root=Path(tmp.name) / "host", lark_cli={"profile": "farmbot"})
        self.db = Paths(self.config).ledger
        self.now = 1000.0
        self.ledger = Ledger(self.db, clock=lambda: self.now)
        self.addCleanup(self.ledger.close)
        self.ledger.observe_issue(issue())
        self.load = self.enterContext(patch("agent.__main__.load_config", return_value=self.config))
        self.probe = self.enterContext(patch.object(diagnostics, "profile_status", return_value={
            "status": "unavailable", "stage": "dpapi_decrypt", "credential_available": False,
            "windows_error_code": 13}))
        self.api = Mock(side_effect=AssertionError("no network calls"))
        self.enterContext(patch.dict(os.environ, {"FARMBOT_TOKEN": ""}))

    def claim(self, skill):
        issue_id = "10000000-0000-4000-8000-00000000000" + str(("work", "feature", "fgui", "fix", "chat", "scene").index(skill) + 1)
        self.ledger.observe_issue(issue(id=issue_id))
        session = "session-" + skill
        self.ledger.ensure_session(session, issue_id, delegation=True)
        item = self.ledger.create_work_item(issue_id=issue_id, session_id=session, skill="work" if skill == "scene" else skill)
        if skill == "scene":
            # Internal presentation rows normally come from delegation; this fixture tests the CLI's role fence only.
            self.ledger.connection.execute("UPDATE work_items SET skill=? WHERE id=?", (skill, item["id"]))
        return self.ledger.claim(item["id"], worker_id="diagnostic-test")

    def invoke(self, claim, token=None, db=None):
        args = ["--db", str(db or self.db), "document-reader-status", "--item", claim["id"]]
        if token is not None:
            args.extend(["--token", token])
        return run(parser().parse_args(args), self.ledger, self.api)

    def test_live_document_claim_gets_only_sanitized_offline_result(self):
        for skill in ("work", "feature", "fgui"):
            with self.subTest(skill=skill):
                claim = self.claim(skill)
                self.assertEqual(self.invoke(claim, claim["token"]), self.probe.return_value)
                self.probe.assert_called_with({"profile": "farmbot"})
        self.api.assert_not_called()

    def test_missing_wrong_expired_and_cancelled_claims_stop_before_profile_read(self):
        claim = self.claim("work")
        for token in (None, "claim_wrong"):
            with self.subTest(token=bool(token)), self.assertRaises(LedgerError):
                self.invoke(claim, token)
        self.now += 3000
        with self.assertRaises(LedgerError):
            self.invoke(claim, claim["token"])
        self.now = 1000
        self.ledger.cancel(claim["id"], "operator cancelled")
        with self.assertRaises(LedgerError):
            self.invoke(claim, claim["token"])
        self.load.assert_not_called()
        self.probe.assert_not_called()

    def test_other_skills_cannot_probe_the_document_credential(self):
        for skill in ("fix", "chat", "scene"):
            with self.subTest(skill=skill):
                claim = self.claim(skill)
                with self.assertRaisesRegex(LedgerError, "document-reading claim"):
                    self.invoke(claim, claim["token"])
        self.probe.assert_not_called()

    def test_wrong_host_ledger_stops_before_probe(self):
        claim = self.claim("work")
        with self.assertRaisesRegex(LedgerError, "configured host ledger"):
            self.invoke(claim, claim["token"], db=self.db.parent / "other.sqlite3")
        self.probe.assert_not_called()

    def test_cancellation_during_probe_fences_the_result(self):
        claim = self.claim("work")

        def cancel(_):
            self.ledger.cancel(claim["id"], "operator cancelled")
            return {"status": "available", "credential_available": True}

        self.probe.side_effect = cancel
        with self.assertRaises(LedgerError):
            self.invoke(claim, claim["token"])

    def test_real_cli_uses_selected_config_and_token_file_without_network_or_store_access(self):
        self.now = time.time()
        claim = self.claim("work")
        root = self.db.parent.parent
        config_path, token_path = root / "诊断 config.json", root / "claim token"
        config_path.write_text(json.dumps({"client_id": "c", "client_secret": "s", "webhook_secret": "w",
            "runtime": "fake", "local_root": str(root),
            "lark_cli": {"app_id": "cli_dummy", "secret_env": "DUMMY_READER_SECRET"}}), encoding="utf-8")
        token_path.write_text(claim["token"], encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "agent", "--db", str(self.db), "document-reader-status",
            "--item", claim["id"], "--token-file", str(token_path)],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "FARMBOT_CONFIG": str(config_path), "FARMBOT_TOKEN": "", "PYTHONUTF8": "1"},
            capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"status": "unsupported", "stage": "authentication_route"})
        self.assertNotIn(claim["token"], result.stdout + result.stderr)


@unittest.skipUnless(os.name == "nt", "native Windows DPAPI required")
class WindowsCredentialStatusTests(unittest.TestCase):
    def setUp(self):
        import winreg
        self.account = "appsecret:cli_dummy"
        self.open = self.enterContext(patch.object(winreg, "OpenKey"))
        self.query = self.enterContext(patch.object(winreg, "QueryValueEx"))

    def encrypt(self, plaintext, account=None):
        """Make an in-memory synthetic DPAPI blob; never write credentials or registry keys."""
        import ctypes as C
        from ctypes import wintypes as W

        class Blob(C.Structure):
            _fields_ = [("size", W.DWORD), ("data", C.POINTER(C.c_ubyte))]

        source_buffer = (C.c_ubyte * len(plaintext)).from_buffer_copy(plaintext)
        entropy = b"lark-cli\0" + (account or self.account).encode("utf-8")
        entropy_buffer = (C.c_ubyte * len(entropy)).from_buffer_copy(entropy)
        source, extra, output = Blob(len(plaintext), source_buffer), Blob(len(entropy), entropy_buffer), Blob()
        crypt = C.WinDLL("crypt32", use_last_error=True)
        kernel = C.WinDLL("kernel32", use_last_error=True)
        crypt.CryptProtectData.argtypes = [C.POINTER(Blob), C.c_void_p, C.POINTER(Blob),
                                         C.c_void_p, C.c_void_p, W.DWORD, C.POINTER(Blob)]
        crypt.CryptProtectData.restype = W.BOOL
        kernel.LocalFree.argtypes, kernel.LocalFree.restype = [C.c_void_p], C.c_void_p
        if not crypt.CryptProtectData(C.byref(source), None, C.byref(extra), None, None, 1, C.byref(output)):
            raise C.WinError(C.get_last_error())
        try:
            return base64.b64encode(C.string_at(output.data, output.size)).decode("ascii")
        finally:
            kernel.LocalFree(output.data)

    def test_native_decryption_checks_only_presence_and_clears_plaintext(self):
        import ctypes as C
        import winreg
        secret = b"synthetic-test-secret"
        self.query.return_value = (self.encrypt(secret), winreg.REG_SZ)
        with patch.object(C, "memset", wraps=C.memset) as wipe:
            result = diagnostics._windows_keychain_status(self.account)
        self.assertEqual(result, {"status": "available", "stage": "dpapi_decrypt", "credential_available": True})
        self.assertNotIn(secret.decode(), json.dumps(result))
        self.assertEqual(wipe.call_args.args[1:], (0, len(secret)))
        self.open.assert_called_once_with(winreg.HKEY_CURRENT_USER, r"Software\LarkCli\keychain\lark-cli",
                                          0, winreg.KEY_QUERY_VALUE)
        self.query.assert_called_once_with(self.open.return_value.__enter__.return_value,
                                           base64.urlsafe_b64encode(self.account.encode()).decode().rstrip("="))

    def test_empty_plaintext_does_not_report_available(self):
        import winreg
        self.query.return_value = (self.encrypt(b""), winreg.REG_SZ)
        result = diagnostics._windows_keychain_status(self.account)
        self.assertEqual(result["status"], "unavailable")
        self.assertFalse(result["credential_available"])

    def test_bad_encoding_and_wrong_entropy_never_return_ciphertext_or_plaintext(self):
        import winreg
        cases = [("", "credential_encoding"), ("bad-base64-private", "credential_encoding"),
                 (base64.b64encode(b"corrupt-private-ciphertext").decode(), "dpapi_decrypt"),
                 (self.encrypt(b"synthetic-test-secret", account="other"), "dpapi_decrypt")]
        for encoded, stage in cases:
            with self.subTest(stage=stage):
                self.query.return_value = (encoded, winreg.REG_SZ)
                result = diagnostics._windows_keychain_status(self.account)
                self.assertEqual(result["stage"], stage)
                self.assertFalse(result["credential_available"])
                self.assertNotRegex(json.dumps(result), "private|synthetic-test-secret|cli_dummy")
                if stage == "dpapi_decrypt":
                    self.assertIsInstance(result["windows_error_code"], int)

    def test_registry_failures_return_only_stage_and_numeric_code(self):
        import ctypes as C
        for target, stage, code in ((self.open, "registry_open", 5), (self.query, "registry_read", 2)):
            with self.subTest(stage=stage):
                target.side_effect = C.WinError(code)
                self.assertEqual(diagnostics._windows_keychain_status(self.account), {
                    "status": "unavailable", "stage": stage, "credential_available": False, "windows_error_code": code})
                target.side_effect = None
