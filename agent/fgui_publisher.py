"""Prepared native Windows publisher; no worker command or export authority yet.

The caller must own the source, ground approval/export authority, validate Git
through the controller trust boundary, and supply live claim/source fences. This
helper owns only its fresh private output and its nested publisher Job Object.
"""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET

from .fgui_export import _package, snapshot_export
from .ledger import LedgerError
from .preview_upload import _plain_path
from .uploads import _read_regular

TOOL_FILES = ("FairyGUI-Editor.exe", "UnityPlayer.dll", "GameAssembly.dll", "baselib.dll")
MAX_LOG_BYTES = 4 << 20
MAX_TIMEOUT = 180
_MUTEX = "Global\\FarmBot-FairyGUI-Publisher-v1"


def _native_windows():
    return os.name == "nt"


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise LedgerError("publisher settings contain duplicate keys")
        result[key] = value
    return result


def _hash_file(path, limit):
    _plain_path(path)
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 < before.st_size <= limit:
        raise LedgerError("publisher input requires a bounded unlinked regular file")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            size += len(block)
            if size > limit:
                raise LedgerError("publisher input grew beyond its bound")
            digest.update(block)
    after = path.stat()
    _plain_path(path)
    fields = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_nlink)
    if fields(before) != fields(after) or size != after.st_size:
        raise LedgerError("publisher input changed while hashing")
    return digest.hexdigest()


def _tools(executable, pins):
    executable = Path(executable)
    _plain_path(executable)
    if executable.name != TOOL_FILES[0] or not isinstance(pins, dict) or set(pins) != set(TOOL_FILES):
        raise LedgerError("publisher requires the explicit native executable and all four tool pins")
    for name, expected in pins.items():
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise LedgerError("publisher tool hash is invalid")
        if _hash_file(executable.parent / name, 512 << 20) != expected:
            raise LedgerError("publisher tool differs from its selected hash")
    return executable


def _input(path, limit):
    try:
        before = _hash_file(path, limit)
        data = _read_regular(path, limit)
        if hashlib.sha256(data).hexdigest() != before or _hash_file(path, limit) != before:
            raise LedgerError("publisher input changed during snapshot")
        return data
    except OSError:
        raise LedgerError("publisher input is unavailable or exceeds its private bound") from None


def _xml(data):
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise LedgerError("publisher XML entities are refused")
    try:
        return ET.fromstring(data)
    except ET.ParseError:
        raise LedgerError("publisher project or package XML is malformed") from None


def _source(project, packages):
    _plain_path(project)
    _plain_path(project / "output")
    if (project / "output").exists():
        raise LedgerError("publisher source must not contain the watched output directory")
    inputs = {"FGUIProject.fairy": _input(project / "FGUIProject.fairy", 2 << 20),
              "settings/Publish.json": _input(project / "settings/Publish.json", 2 << 20)}
    if _xml(inputs["FGUIProject.fairy"]).get("type") != "Unity":
        raise LedgerError("publisher requires the reviewed Unity project")
    try:
        publish = json.loads(inputs["settings/Publish.json"], object_pairs_hook=_json_object)
    except (json.JSONDecodeError, UnicodeError):
        raise LedgerError("publisher settings are malformed") from None
    if (not isinstance(publish, dict) or not isinstance(publish.get("codeGeneration"), dict)
            or publish["codeGeneration"].get("allowGenCode") is not False):
        raise LedgerError("publisher code generation must be explicitly disabled before export")
    if not isinstance(packages, dict) or not 0 < len(packages) <= 100:
        raise LedgerError("publisher requires an explicit bounded package identity map")
    names, identities = set(), set()
    for name, identity in sorted(packages.items()):
        _package(name, identity)
        if name.casefold() in names or identity in identities:
            raise LedgerError("publisher selected package identities are duplicate")
        names.add(name.casefold()); identities.add(identity)
        relative = "assets/" + name + "/package.xml"
        inputs[relative] = _input(project / relative, 2 << 20)
        if _xml(inputs[relative]).get("id") != identity:
            raise LedgerError("publisher selected package manifest identity differs")
    return {name: hashlib.sha256(data).hexdigest() for name, data in inputs.items()}


def _source_evidence(verify_source, required):
    """Require a trusted caller's full source identity, never infer clean Git here."""
    evidence = verify_source()
    if (not isinstance(evidence, dict) or set(evidence) != {"head", "files"}
            or not isinstance(evidence["head"], str) or not re.fullmatch(r"[0-9a-f]{40}", evidence["head"])
            or not isinstance(evidence["files"], dict) or not 0 < len(evidence["files"]) <= 20000):
        raise LedgerError("publisher requires the caller's verified full source identity")
    for name, digest in evidence["files"].items():
        if (not isinstance(name, str) or not name or len(name) > 512 or "\\" in name or any(ord(c) < 32 for c in name)
                or Path(name).is_absolute() or ":" in name or any(p in ("", ".", "..") for p in name.split("/"))
                or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            raise LedgerError("publisher source identity contains invalid paths or hashes")
    if any(evidence["files"].get(name) != digest for name, digest in required.items()):
        raise LedgerError("publisher source identity omits or differs from its required inputs")
    return {"head": evidence["head"], "files": dict(evidence["files"])}


def _environment():
    denied = {"GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN",
              "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "CODEX_HOME"}
    env = {k: v for k, v in os.environ.items() if k.upper() not in denied
           and not k.upper().startswith(("FARMBOT_", "FAKE_CLI_", "LARK", "FEISHU", "KW_OPS"))}
    env["PYTHONUTF8"] = "1"
    return env


class _WindowsLock:
    def __init__(self, name=_MUTEX):
        from .windows_job import api, checked, close, wait, W, C
        self.close, self.release = close, api("ReleaseMutex", W.BOOL, W.HANDLE)
        self.handle = checked(api("CreateMutexW", W.HANDLE, C.c_void_p, W.BOOL, W.LPCWSTR)(None, False, name))
        state = wait(self.handle, 0)
        if state not in (0, 0x80):  # acquired / abandoned, still check foreign processes
            close(self.handle); self.handle = None
            raise LedgerError("another native publisher owns the machine export lock")
        self.abandoned = state == 0x80

    def __enter__(self):
        return self

    def __exit__(self, *args):
        from .windows_job import checked
        try:
            checked(self.release(self.handle))
        finally:
            self.close(self.handle); self.handle = None


def _foreign_editors(owned=()):
    """A refusal signal only; snapshot PIDs are never cleanup authority."""
    from .windows_job import api, close, W, C
    class Entry(C.Structure):
        _fields_ = [("size", W.DWORD), ("usage", W.DWORD), ("pid", W.DWORD), ("heap", C.c_size_t),
                    ("module", W.DWORD), ("threads", W.DWORD), ("parent", W.DWORD),
                    ("priority", W.LONG), ("flags", W.DWORD), ("exe", W.WCHAR * 260)]
    handle = api("CreateToolhelp32Snapshot", W.HANDLE, W.DWORD, W.DWORD)(2, 0)
    if handle == C.c_void_p(-1).value:
        raise C.WinError(C.get_last_error())
    first = api("Process32FirstW", W.BOOL, W.HANDLE, C.POINTER(Entry))
    next_entry = api("Process32NextW", W.BOOL, W.HANDLE, C.POINTER(Entry))
    try:
        entry = Entry(); entry.size = C.sizeof(entry)
        more = first(handle, C.byref(entry))
        if not more:
            raise LedgerError("publisher cannot establish the native process inventory")
        while more:
            if entry.exe.casefold().startswith("fairygui") and entry.pid not in owned:
                return True
            more = next_entry(handle, C.byref(entry))
        if C.get_last_error() != 18:  # ERROR_NO_MORE_FILES; an incomplete table is unknown
            raise C.WinError(C.get_last_error())
        return False
    finally:
        close(handle)


def _log_bounds(run):
    for name in ("stdout.log", "stderr.log", "publisher.log"):
        path = run / name; _plain_path(path)
        if path.exists():
            value = path.stat()
            if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1 or value.st_size > MAX_LOG_BYTES:
                raise LedgerError("publisher log exceeds its private bound or is not an unlinked regular file")


def _windows_run(command, cwd, run, fence, timeout):
    """Assign before opening the gate; terminate only this nested Job on failure."""
    from .windows_job import WindowsJob, active, process_ids
    if not _native_windows():
        raise LedgerError("this prepared publisher requires native Windows")
    with _WindowsLock() as lock:
        if _foreign_editors():
            raise LedgerError("an existing FairyGUI process prevents isolated batch export")
        fence()
        start = time.monotonic(); process = None; job = WindowsJob()
        result = {"state": "running", "job": job.name, "job_assigned_before_export": False, "job_empty": False,
                  "abandoned_lock": lock.abandoned, "seconds": 0, "max_owned_processes": 0}
        try:
            gate = Path(__file__).with_name("windows_worker_gate.py")
            _plain_path(gate)
            with (run / "stdout.log").open("xb") as stdout, (run / "stderr.log").open("xb") as stderr:
                process = subprocess.Popen([sys.executable, "-I", str(gate), str(run), *command],
                    cwd=cwd, env=_environment(), stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW)
                try:
                    job.assign(process)
                except BaseException:
                    process.kill(); process.wait(timeout=5)
                    raise
                result["job_assigned_before_export"] = True
                fence()  # Stop after assignment still cannot start the publisher.
                process.stdin.write(b"G"); process.stdin.flush(); process.stdin.close()
                while True:
                    fence()
                    owned = process_ids(job.handle)
                    result["max_owned_processes"] = max(result["max_owned_processes"], len(owned))
                    if _foreign_editors(owned):
                        raise LedgerError("a competing FairyGUI process appeared during export")
                    _log_bounds(run)
                    if process.poll() is not None:
                        break
                    if time.monotonic() - start >= timeout:
                        raise LedgerError("native publisher timed out")
                    time.sleep(.1)
                result["exit_code"] = process.wait(timeout=5)
                # A signalled gate can precede final descendant accounting by a
                # few ticks. Require observed emptiness after a bounded drain;
                # the parent's exit alone never establishes quiescence.
                drain_start = time.monotonic()
                while active(job.handle) and time.monotonic() - drain_start < 1:
                    fence()
                    if _foreign_editors(process_ids(job.handle)):
                        raise LedgerError("a competing FairyGUI process appeared during teardown")
                    _log_bounds(run)
                    time.sleep(.02)
                result["quiescence_seconds"] = round(time.monotonic() - drain_start, 3)
                if active(job.handle):
                    raise LedgerError("publisher exited with lingering owned descendants")
                _log_bounds(run)
                result["job_empty"] = True
                result["state"] = "complete"
        finally:
            try:
                if active(job.handle):
                    job.terminate_and_wait(timeout=10)
                if process is not None:
                    if process.stdin is not None and not process.stdin.closed:
                        process.stdin.close()
                    process.wait(timeout=5)
                if active(job.handle):
                    raise LedgerError("publisher owned process teardown remains uncertain")
            finally:
                try:
                    result["job_empty"] = active(job.handle) == 0
                    result["seconds"] = round(time.monotonic() - start, 3)
                    if result["state"] != "complete":
                        result["state"] = "interrupted"
                    _record(run, result, "native-process.json")
                finally:
                    job.close()
        result["seconds"] = round(time.monotonic() - start, 3)
        return result


def _record(run, evidence, name="receipt.json"):
    _plain_path(run)
    temporary = run / ("receipt-" + uuid.uuid4().hex + ".tmp")
    with temporary.open("xb") as stream:
        stream.write((json.dumps(evidence, indent=2) + "\n").encode("utf-8"))
        stream.flush(); os.fsync(stream.fileno())
    path = run / name; _plain_path(path)
    os.replace(temporary, path)


@dataclass(frozen=True)
class PublishResult:
    snapshot: object
    source_head: str
    source_digest: str
    tool_pins: tuple
    process: tuple
    log_sha256: str

    def evidence(self):
        return {"format": "farmbot-fgui-publish-v1", "source_head": self.source_head,
                "source_digest": self.source_digest, "tools": dict(self.tool_pins),
                "process": dict(self.process), "log_sha256": self.log_sha256,
                "export": self.snapshot.evidence()}


def publish(project, packages, executable, tool_pins, run, *, fence, verify_source, timeout=MAX_TIMEOUT):
    """Export once to new private staging; retain every failure for investigation.

    `verify_source` must revalidate an owned clean full commit, hydrated scoped
    inputs and all tracked hashes using trusted Git. It is required before/after
    the publisher. This module never selects live configuration or reads a license.
    """
    if not _native_windows():
        raise LedgerError("this prepared publisher requires native Windows")
    if not callable(fence) or not callable(verify_source) or isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= MAX_TIMEOUT:
        raise LedgerError("publisher requires live fences and a bounded timeout")
    project, run = Path(project), Path(run)
    _plain_path(project); _plain_path(run)
    executable = _tools(executable, tool_pins)
    tool_pins = dict(tool_pins)
    if (run.exists() or run.resolve().is_relative_to(project.resolve())
            or project.resolve().is_relative_to(run.resolve())
            or run.resolve().is_relative_to(executable.parent.resolve())
            or executable.parent.resolve().is_relative_to(run.resolve())):
        raise LedgerError("publisher requires new private staging outside source and installed tools")
    packages = dict(packages) if isinstance(packages, dict) else packages
    fence()
    required = _source(project, packages)
    source = _source_evidence(verify_source, required)
    fence()
    run.mkdir(exist_ok=False)
    staging = run / "staging"; staging.mkdir()
    evidence = {"format": "farmbot-fgui-publish-attempt-v1", "state": "prepared",
                "source_head": source["head"], "packages": packages, "tools": dict(tool_pins)}
    _record(run, evidence)
    try:
        command = [str(executable), "-batchmode", "-p", str(project / "FGUIProject.fairy"),
                   "-b", ",".join(sorted(packages)), "-o", str(staging), "-logFile", str(run / "publisher.log")]
        process = _windows_run(command, executable.parent, run, fence, timeout)
        evidence["process"] = process
        fence()
        _tools(executable, tool_pins)
        if _source(project, packages) != required or _source_evidence(verify_source, required) != source:
            raise LedgerError("publisher source changed during export")
        if process.get("exit_code") != 0 or not process.get("job_empty") or not process.get("job_assigned_before_export"):
            raise LedgerError("native publisher did not complete a quiescent successful export")
        log = _input(run / "publisher.log", MAX_LOG_BYTES)
        text = log.decode("utf-8", errors="strict")
        failures = ("license expired", "no license", "not licensed", "license is required", "license required", "unlicensed", "licence expired")
        if any(marker in text.lower() for marker in failures) or text.lower().count("publish completed") != len(packages):
            raise LedgerError("publisher log does not establish completion of the selected package set")
        snapshot = snapshot_export(staging, packages)
        fence()
        digest = hashlib.sha256(json.dumps(source, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        result = PublishResult(snapshot, source["head"], digest, tuple(sorted(tool_pins.items())),
                               tuple(sorted(process.items())), hashlib.sha256(log).hexdigest())
        evidence.update({"state": "complete", "result": result.evidence()}); _record(run, evidence)
        return result
    except BaseException as exc:
        evidence.update({"state": "interrupted", "error": type(exc).__name__})
        try:
            _record(run, evidence)
        except (OSError, LedgerError):
            pass
        raise
