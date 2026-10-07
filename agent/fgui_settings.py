"""Optional native UI export selection and read-only binary diagnostics."""
import math
from pathlib import Path
import re

from .fgui_publisher import TOOL_FILES, MAX_TIMEOUT, _native_windows, _tools as validate_tool
from .ledger import LedgerError


def validate_settings(block):
    """An explicit tool selection; no lookup, filesystem read or inline credentials."""
    if block == {}:
        return
    if (not isinstance(block, dict) or set(block) - {"executable", "tool_sha256", "timeout_seconds"}
            or not {"executable", "tool_sha256"} <= set(block)):
        raise ValueError("fgui_export accepts executable, tool_sha256 and optional timeout_seconds only")
    executable = block["executable"]
    if (not isinstance(executable, str) or not executable.isprintable() or not Path(executable).is_absolute()
            or Path(executable).name != "FairyGUI-Editor.exe"):
        raise ValueError("fgui_export executable must be an explicit absolute native FairyGUI-Editor.exe")
    pins = block["tool_sha256"]
    if (not isinstance(pins, dict) or set(pins) != set(TOOL_FILES)
            or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) for value in pins.values())):
        raise ValueError("fgui_export tool_sha256 must pin exactly the four native publisher binaries")
    timeout = block.get("timeout_seconds", MAX_TIMEOUT)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= MAX_TIMEOUT:
        raise ValueError("fgui_export timeout_seconds must be positive, finite and at most 180")


def publisher_diagnostic(block):
    """Sanitized offline findings; never executes tools or reads a license/profile."""
    validate_settings(block)
    result = {"configured": bool(block), "native_windows": _native_windows(), "ok": False,
              "tool_sha256": dict(block.get("tool_sha256", {})), "timeout_seconds": block.get("timeout_seconds", MAX_TIMEOUT)}
    if not block:
        result["reason"] = "native publisher not configured; UI authoring remains available"
    elif not result["native_windows"]:
        result["reason"] = "native Windows export must be verified on a Windows host"
    else:
        try:
            validate_tool(block["executable"], block["tool_sha256"])
            result.update(ok=True, reason="selected native binary hashes match; execution and license acceptance remain separate")
        except (LedgerError, OSError, ValueError):
            result["reason"] = "selected native binaries are unavailable, unsafe or differ from their configured hashes"
    return result
