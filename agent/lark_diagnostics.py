"""Offline diagnostics; decrypted keychain plaintext is never copied into Python values."""
import base64
import json
import os
from pathlib import Path

_WINDOWS = os.name == "nt"


def _failure(stage, error=None):
    result = {"status": "unavailable", "stage": stage, "credential_available": False}
    code = getattr(error, "winerror", None)
    if isinstance(code, int):
        result["windows_error_code"] = code
    return result


def profile_status(block, environ=None):
    """Inspect only the selected Windows bot profile, never another store or identity.

    This is an offline availability check, not authentication or document acceptance.
    File/plaintext secret references and personal stores are deliberately unsupported.
    """
    if not _WINDOWS or set(block) != {"profile"}:
        return {"status": "unsupported", "stage": "authentication_route"}
    environ = os.environ if environ is None else environ
    home = environ.get("USERPROFILE")
    if not home or not Path(home).is_absolute():
        return _failure("profile_path")
    try:
        with (Path(home) / ".lark-cli" / "config.json").open("rb") as stream:
            data = stream.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            return _failure("profile_config")
        config = json.loads(data)
    except (OSError, ValueError, RecursionError) as exc:
        return _failure("profile_read", exc)
    if not isinstance(config, dict) or not isinstance(config.get("apps"), list):
        return _failure("profile_config")
    apps = config["apps"]
    if len(apps) != 1 or not isinstance(apps[0], dict):
        return _failure("profile_isolation")
    app = apps[0]
    if (block["profile"] not in (app.get("name"), app.get("appId")) or app.get("strictMode") != "bot"
            or app.get("users")):
        return _failure("profile_isolation")
    app_id, reference = app.get("appId"), app.get("appSecret")
    if (not isinstance(app_id, str) or not app_id or not isinstance(reference, dict)
            or reference.get("source") != "keychain"
            or reference.get("id") != "appsecret:" + app_id):
        return _failure("credential_reference")
    return _windows_keychain_status(reference["id"])


def _windows_keychain_status(account):
    """Mirror lark-cli 1.0.82's service/account and DPAPI entropy without copying plaintext."""
    import ctypes as C
    from ctypes import wintypes as W
    import winreg

    stage = "registry_open"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\LarkCli\keychain\lark-cli",
                            0, winreg.KEY_QUERY_VALUE) as key:
            stage = "registry_read"
            name = base64.urlsafe_b64encode(account.encode("utf-8")).decode("ascii").rstrip("=")
            encoded, kind = winreg.QueryValueEx(key, name)
        if kind != winreg.REG_SZ or not isinstance(encoded, str):
            return _failure("credential_encoding")
        try:
            ciphertext = base64.b64decode(encoded, validate=True)
        except ValueError:
            return _failure("credential_encoding")
        if not ciphertext:
            return _failure("credential_encoding")
        stage = "dpapi_decrypt"

        class Blob(C.Structure):
            _fields_ = [("size", W.DWORD), ("data", C.POINTER(C.c_ubyte))]

        encrypted = (C.c_ubyte * len(ciphertext)).from_buffer_copy(ciphertext)
        entropy = b"lark-cli\0" + account.encode("utf-8")
        entropy_buffer = (C.c_ubyte * len(entropy)).from_buffer_copy(entropy)
        source = Blob(len(ciphertext), encrypted)
        extra = Blob(len(entropy), entropy_buffer)
        output = Blob()
        crypt = C.WinDLL("crypt32", use_last_error=True)
        kernel = C.WinDLL("kernel32", use_last_error=True)
        crypt.CryptUnprotectData.argtypes = [C.POINTER(Blob), C.c_void_p, C.POINTER(Blob),
                                           C.c_void_p, C.c_void_p, W.DWORD, C.POINTER(Blob)]
        crypt.CryptUnprotectData.restype = W.BOOL
        kernel.LocalFree.argtypes = [C.c_void_p]
        kernel.LocalFree.restype = C.c_void_p
        try:
            if not crypt.CryptUnprotectData(C.byref(source), None, C.byref(extra),
                                            None, None, 1, C.byref(output)):
                return {**_failure(stage), "windows_error_code": C.get_last_error()}
            available = bool(output.data and output.size)
            return {"status": "available" if available else "unavailable", "stage": "dpapi_decrypt",
                    "credential_available": available}
        finally:
            if output.data:
                try:
                    C.memset(output.data, 0, output.size)
                finally:
                    kernel.LocalFree(output.data)
    except (OSError, ValueError, TypeError) as exc:
        return _failure(stage, exc)
