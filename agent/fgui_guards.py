"""Run the actual hydrated Client guards; Inconclusive is pending, never pass."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import time
from uuid import uuid4
import xml.etree.ElementTree as ET

from .fgui_client import _client, installation_record
from .fgui_publisher import _environment, _hash_file
from .fgui_records import _canonical, _live
from .fgui_scope import verify_candidate
from .ledger import LedgerError
from .preview_upload import _plain_path
from .uploads import _read_regular

METHODS = {
    "Farm.Tests.Unit.FguiDependencyGuardTests.No_unsanctioned_published_dependency_edges",
    "Farm.Tests.Unit.FguiOrphanAtlasGuardTests.Package_dirs_hold_exactly_the_files_their_descriptors_declare",
}
TRX_NS = {"t": "http://microsoft.com/schemas/VisualStudio/TeamTest/2010"}


def guard_results(path):
    """Bounded complete TRX identities/results from this fresh controller run."""
    _plain_path(Path(path))
    if Path(path).stat().st_nlink != 1:
        raise LedgerError("UI guard results must not be hardlinked")
    raw = _read_regular(Path(path), 4 << 20)
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise LedgerError("UI guard results cannot declare external/entity content")
    try:
        root = ET.fromstring(raw)
        definitions = {}
        for unit in root.findall("t:TestDefinitions/t:UnitTest", TRX_NS):
            method = unit.find("t:TestMethod", TRX_NS)
            identity = unit.attrib["id"]
            if identity in definitions or method is None:
                raise ValueError("ambiguous test definition")
            definitions[identity] = method.attrib["className"].split(",", 1)[0] + "." + method.attrib["name"]
        results = root.findall("t:Results/t:UnitTestResult", TRX_NS)
        observed = [definitions[r.attrib["testId"]] for r in results]
        counters = root.find("t:ResultSummary/t:Counters", TRX_NS)
        if (len(results) != 2 or set(observed) != METHODS or len(definitions) != 2
                or any(r.attrib.get("outcome") != "Passed" for r in results) or counters is None
                or any(int(counters.get(key, "-1")) != 2 for key in ("total", "executed", "passed"))
                or any(int(counters.get(key, "0")) != 0 for key in ("failed", "error", "timeout", "inconclusive", "aborted", "notExecuted"))):
            raise ValueError("incomplete or nonpassing guard result")
    except (ET.ParseError, KeyError, ValueError, TypeError):
        raise LedgerError("actual hydrated UI guards must both pass; missing, skipped or Inconclusive results remain pending") from None
    return {"tests": sorted(observed), "passed": 2, "trx_sha256": hashlib.sha256(raw).hexdigest()}


def _native_run(command, cwd, run, *, fence, timeout=600):
    if os.name != "nt":
        raise LedgerError("this UI guard execution route requires native Windows")
    from .windows_job import CREATE_SUSPENDED, WindowsJob, active
    job = WindowsJob(); process = None; began = time.monotonic()
    env = _environment()
    env.update(DOTNET_CLI_HOME=str(run / "dotnet-home"), DOTNET_CLI_TELEMETRY_OPTOUT="1",
               DOTNET_SKIP_FIRST_TIME_EXPERIENCE="1", MSBUILDDISABLENODEREUSE="1")
    def bounded():
        for name in ("stdout.log", "stderr.log"):
            if (run / name).stat().st_size > 4 << 20:
                raise LedgerError("UI guard process log exceeds its bound")
    try:
        with (run / "stdout.log").open("xb") as stdout, (run / "stderr.log").open("xb") as stderr:
            fence()
            process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                       creationflags=subprocess.CREATE_NO_WINDOW | CREATE_SUSPENDED)
            try:
                job.assign(process)
            except BaseException:
                process.kill(); process.wait(timeout=5); raise
            fence(); job.resume(process)
            while process.poll() is None:
                fence(); bounded()
                if time.monotonic() - began >= timeout:
                    raise LedgerError("actual UI guard process timed out; preserve its evidence")
                time.sleep(.1)
            code = process.wait(timeout=5)
            deadline = time.monotonic() + 1
            while active(job.handle) and time.monotonic() < deadline:
                fence(); bounded(); time.sleep(.02)
            if active(job.handle):
                raise LedgerError("UI guards left owned descendants running; preserve their evidence")
            fence(); bounded()
            if code != 0:
                raise LedgerError("actual hydrated Client UI guards failed; preserve their logs/results")
    finally:
        try:
            if active(job.handle):
                job.terminate_and_wait(timeout=5)
                if active(job.handle): raise LedgerError("UI guard cleanup remains uncertain; preserve recovery evidence")
            if process is not None: process.wait(timeout=5)
        finally:
            job.close()
    return {"exit_code": code, "job_assigned_before_startup": True, "job_empty": True,
            "seconds": round(time.monotonic() - began, 3)}


def verify_guards(workflow, receipt_id):
    scope = verify_candidate(workflow, receipt_id)
    workflow.ledger.require_no_reservation(workflow.item_id)
    client, _, _ = _client(workflow)
    tool = shutil.which("dotnet")
    if not tool or not Path(tool).is_absolute():
        raise LedgerError("actual UI guards require the selected native dotnet executable")
    tool_sha = _hash_file(Path(tool), 64 << 20)
    run = workflow.paths.runs / workflow.item_id / "ui-checks" / uuid4().hex
    _plain_path(run.parent); run.parent.mkdir(parents=True, exist_ok=True); _plain_path(run.parent)
    run.mkdir(); _plain_path(run)
    command = [tool, "test", "tests/Farm.Tests.Unit/Farm.Tests.Unit.csproj", "-c", "Release",
               "--disable-build-servers", "--filter", "FullyQualifiedName~FguiDependencyGuardTests|FullyQualifiedName~FguiOrphanAtlasGuardTests",
               "--logger", "trx;LogFileName=fgui-guards.trx", "--results-directory", str(run)]
    process = _native_run(command, client, run, fence=workflow.fence)
    result = guard_results(run / "fgui-guards.trx")
    workflow.fence(refresh=True)
    workflow.ledger.require_no_reservation(workflow.item_id)
    if verify_candidate(workflow, receipt_id) != scope or _hash_file(Path(tool), 64 << 20) != tool_sha:
        raise LedgerError("UI guard candidate or selected tool changed during verification")
    installed = installation_record(workflow.ledger, workflow.item_id, receipt_id)
    proof = {"format": "farmbot-fgui-guards-v1", "receipt_id": receipt_id, "commit": scope["commit"],
             "source_head": scope["source_head"], "installation_sha256": installed["installation_sha256"],
             "dotnet_sha256": tool_sha, "process": process, **result}
    proof["guards_sha256"] = hashlib.sha256(_canonical(proof).encode("utf-8")).hexdigest()
    with workflow.ledger._transaction():
        _live(workflow.ledger, workflow.item_id, workflow.token, ("Farm-Client",))
        workflow.ledger.require_no_reservation(workflow.item_id)
        workflow.ledger._audit(workflow.item_id, "fgui_guards_complete", details=proof)
    return proof
