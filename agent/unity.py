"""Where the Editor is on this host, and how a batch test run is spelled (spec §8).

The probe order is Farm-Client's own pack.sh, so a host that builds by hand and FarmBot agree about which
Editor is in use. This is the only module in agent/ allowed to name an operating system or an install path.
"""
import os
import platform
import re
import subprocess
from pathlib import Path
from xml.etree import ElementTree

VERSION = re.compile(r"^m_EditorVersion:\s*(\S+)\s*$", re.MULTILINE)


class UnityError(RuntimeError):
    pass


def project_version(project):
    path = Path(project) / "ProjectSettings" / "ProjectVersion.txt"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise UnityError(f"{path}: unreadable; is this a Unity project folder?") from exc
    match = VERSION.search(text)
    if not match:
        raise UnityError(f"{path} names no m_EditorVersion")
    return match.group(1)


def candidates(version, system=None):
    system = system or platform.system()
    if system == "Darwin":
        return [Path(f"/Applications/Unity/Hub/Editor/{version}/Unity.app/Contents/MacOS/Unity")]
    if system == "Windows":
        return [Path(f"D:/Program/UnityEditor/{version}/Editor/Unity.exe"),
                Path(f"D:/Unity Hub/{version}/Editor/Unity.exe"),
                Path(f"C:/Program Files/Unity/Hub/Editor/{version}/Editor/Unity.exe")]
    return [Path.home() / "Unity" / "Hub" / "Editor" / version / "Editor" / "Unity"]


def editor_path(project, override=None, system=None, exists=None, environ=None):
    """An explicit path wins, then FARMBOT_UNITY_EXE, then the per-host probe for the project's own version.

    environ is injectable so that a developer who happens to export FARMBOT_UNITY_EXE cannot silently decide
    the outcome of the discovery tests — the global constraint says no test touches /Applications, and
    nothing else stops the environment from redirecting this lookup."""
    exists = exists or (lambda path: path.exists())
    environ = os.environ if environ is None else environ
    explicit = override or environ.get("FARMBOT_UNITY_EXE")
    if explicit:
        path = Path(explicit)
        if not exists(path):
            raise UnityError(f"configured Unity editor not found: {path}")
        return path
    version = project_version(project)
    tried = candidates(version, system)
    for path in tried:
        if exists(path):
            return path
    raise UnityError(f"no Unity {version} editor found; tried: " + ", ".join(str(p) for p in tried))


def batch_test_command(editor, project, *, results, log, test_platform="EditMode", assemblies=(),
                       build_target=None):
    """spec §8: no -quit, because the test runner exits on its own and -quit races the results writer; no
    -nographics, because the PlayMode fixtures render FairyGUI; and no -accept-apiupdate, because it is not in
    §8's argument list and it lets the API Updater rewrite scripts under Assets/, which would leave the slot
    dirty and make the *next* switch fail its clean check two runs later."""
    command = [str(editor), "-batchmode", "-silent-crashes",
               "-projectPath", str(project),
               "-runTests", "-testPlatform", test_platform,
               "-testResults", str(results), "-logFile", str(log)]
    if build_target:
        command[2:2] = ["-buildTarget", str(build_target)]
    if assemblies:
        command += ["-assemblyNames", ";".join(assemblies)]
    return command


def read_results(path):
    """The only evidence a batch run produces. Task 0 Step 4: exit 0 means *nothing ran* and exit 2 means
    tests failed, so the caller must read this rather than the return code. Missing, unparseable or without a
    total is a *verification gap*, which is why those three raise instead of returning zeros that would read
    as a green empty run."""
    path = Path(path)
    try:
        root = ElementTree.parse(path).getroot()
    except OSError as exc:
        raise UnityError(f"{path}: no results file; the run produced no evidence either way") from exc
    except ElementTree.ParseError as exc:
        raise UnityError(f"{path}: unparseable results file: {exc}") from exc
    if root.get("total") is None:
        raise UnityError(f"{path}: results file has no total on <{root.tag}>")
    return {"result": root.get("result"), "total": int(root.get("total")),
            "passed": int(root.get("passed") or 0), "failed": int(root.get("failed") or 0)}


def _editor_processes(system=None, run=None, *, strict=False):
    """(pid, project_path, import_helper) for Unity processes on this host. The host's process-listing
    spelling is written, and `run` is injectable so no test in this suite shells out to pgrep."""
    system = system or platform.system()
    command = (["pgrep", "-fl", "Unity.app/Contents/MacOS/Unity"] if system == "Darwin" else
               ["pgrep", "-fa", "Unity"] if system != "Windows" else
               ["powershell", "-NoProfile", "-Command",
                "$ErrorActionPreference='Stop'; (Get-CimInstance Win32_Process | Where-Object Name -eq 'Unity.exe' | "
                "ForEach-Object { \"$($_.ProcessId) $($_.CommandLine)\" })"])
    try:
        if run:
            out = run()
        else:
            result = subprocess.run(command, capture_output=True, text=True, timeout=5)
            if strict and result.returncode != 0 and not (system != 'Windows' and result.returncode == 1):
                raise UnityError('Unity process inspection failed')
            out = result.stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        if strict:
            raise UnityError('Unity process ownership could not be inspected') from exc
        return []
    found = []
    for line in (out or "").splitlines():
        match = re.search(r'(?<!\S)"?-projectPath"?\s+(?:"([^"]+)"|(\S+))', line)
        pid = re.match(r"\s*(\d+)\b", line)
        if match and pid:
            if strict and match.group(2):
                # ps/pgrep flatten argv; an unquoted spaced path must never be
                # mistaken for a different project and treated as absent.
                tail = line[match.end():].strip()
                if tail and not tail.startswith(('-', '"-')):
                    raise UnityError('Unity project path is ambiguous in process listing')
            helper = bool(re.search(r'(?<!\S)"?-name"?\s+"?AssetImportWorker\d+"?(?:\s|$)', line))
            found.append((int(pid.group(1)), match.group(1) or match.group(2), helper))
        elif strict and line.strip():
            raise UnityError('Unity process identity is incomplete')
    return found


def other_editor_project(folder, system=None, run=None, allowed_projects=()):
    """An Editor outside this slot and the operator-configured pool, or None."""
    allowed = {Path(path).resolve() for path in (folder, *allowed_projects)}
    for _, project, _ in _editor_processes(system, run):
        if Path(project).resolve() not in allowed:
            return project
    return None


def editor_holds_project(folder, system=None, run=None, *, strict=False):
    """The pid of a Unity process that holds *this* folder, or None — `other_editor_project`'s complement
    over the same listing.

    It returns a pid rather than a bool because a caller needs the number: `UnityIdentity.terminate`
    signals it, while `SlotPool.editor_is_open` only asks whether it is None. It exists because
    Temp/UnityLockfile cannot answer the question at all — Task 0 Step 5 found the lock still present 32 s
    after the process was gone, so the file is litter Unity leaves behind, not a liveness marker."""
    found = [(pid, helper) for pid, project, helper in _editor_processes(system, run, strict=strict)
             if Path(project).resolve() == Path(folder).resolve()]
    editors = [pid for pid, helper in found if not helper]
    if strict and len(editors) > 1:
        raise UnityError('multiple Unity processes own the same project')
    # Import workers share their parent's project and executable. Prefer the
    # main editor for termination, but surviving helpers still prevent checkout
    # or lock removal after the main process exits.
    return editors[0] if editors else (found[0][0] if found else None)
