"""Read-only host diagnostics. Never open Ledger: its constructor migrates the DB."""
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import UUID

from .config import Paths, ROOT, load_config
from .dispatch import SKILL_AUTHORITY
from .foreign_work import plan_work
from .ledger import ACTIVE_STATES, AWAIT_REASONS
from .readonly_db import snapshot_connection
from .router import WRITE_SKILLS
from .skills import SkillError, enabled_skills, load_skills
from .stages import current_root, runtime_can_launch
from .withdrawal import SILENT_GRACE_SECONDS
from .worktrees import HOOKS_OFF, Worktrees

# The words a job's plan may use for its stages and pauses (the Phase B plan's shared interfaces). Doctor copies
# only these out of a worker-written plan, never its prose, question text or branch names.
STAGE_LETTERS = ("A", "B", "C", "D", "E", "F", "G")
PAUSE_KINDS = ("answers", "config_ready", "closing", "foreign_work", "stage_limit")
# When withdrawn work is late (withdrawn-work design §5.1 commit 6): the delegation's work outlives three status
# intervals after a read found its card undelegated (two reads an interval apart withdraw it), a flagged worker
# outlives its deadline by 5 minutes, a delegation waits 45 minutes for another session's worker, or a job waits
# 7 days for an answer.
UNDELEGATED_INTERVALS = 3
WITHDRAWAL_OVERDUE_SECONDS = 300
DEFERRED_SECONDS = 45 * 60
LONG_PARKED_SECONDS = 7 * 86400
# A closing activity FarmBot gave up (silent-delegation design P10: Linear refused it to the end, or its window passed
# while the controller was down) is listed for 7 days: nothing removes its row once the operator has looked at the
# thread, and a finding that never clears would hide the next one.
UNCLOSED_SECONDS = 7 * 86400
# A delegation Linear opened no session for (silent-delegation design P12, §7) is listed for 7 days when FarmBot found
# no open thread of its own to answer in, and while it is still unsettled more than 10 minutes after its grace ended.
# The receiver settles one when its grace ends; a settle whose read fails is tried again, which moves the episode's
# `due_at` on each time, so what is overdue is counted from the end of the grace and not from `due_at`.
SILENT_UNSEEN_SECONDS = 7 * 86400
SILENT_OVERDUE_SECONDS = 600


class _SchemaMismatch(ValueError):
    def __init__(self, missing):
        self.missing = missing
        super().__init__("ledger predates lifecycle diagnostics")


def probe_process(pid, item_id):
    """Check existence and the job marker without exposing process arguments.

    A matching command is evidence of ownership, not proof of worker progress.
    Unknown includes PID reuse and races between kill(0) and ps. Never kill here.
    """
    if type(pid) is not int or pid <= 0:
        return {"state": "unknown", "reason": "invalid_pid"}
    # Windows kill(pid, 0) does not have POSIX probe semantics.
    if os.name == "nt":
        return {"state": "unknown", "reason": "platform_not_supported"}
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return {"state": "dead", "reason": "not_found"}
    except (OSError, OverflowError):
        return {"state": "unknown", "reason": "permission_or_probe_error"}
    try:
        result = subprocess.run(["ps", "-o", "stat=", "-o", "command=", "-p", str(pid)],
                                capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        return {"state": "unknown", "reason": "inspection_failed"}
    parts = result.stdout.strip().split(None, 1)
    if result.returncode or len(parts) != 2:
        return {"state": "unknown", "reason": "inspection_failed"}
    if "Z" in parts[0]:
        return {"state": "dead", "reason": "zombie"}
    if item_id not in parts[1]:
        return {"state": "unknown", "reason": "ownership_unverified"}
    return {"state": "alive", "reason": "job_marker_matches"}


def _report(now):
    return {"schema_version": 1, "checked_at": now, "status": "ok", "host": None,
            "scope": "local ledger snapshot and recorded worker PIDs; no service, tunnel or Linear health probe",
            "counts": {}, "jobs": [], "slots": [], "reservations": [],
            "cleanup_pending": [], "issue_status_errors": [], "resource_recoveries": [], "findings": []}


def _finding(report, code, hint, *, incomplete=False, **evidence):
    report["findings"].append({"code": code, "severity": "unknown" if incomplete else "warning",
                               "hint": hint, **evidence})
    if incomplete:
        report["status"] = "incomplete"
    elif report["status"] == "ok":
        report["status"] = "attention"


def _snapshot(path):
    # One read transaction keeps all tables on the same snapshot; nothing here can create or migrate it.
    with snapshot_connection(path) as db:
        tables = {row["name"] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = [name for name in ("issue_checks", "job_cleanup") if name not in tables]
        columns = {row["name"] for row in db.execute("PRAGMA table_info(work_items)")}
        if "predecessor_id" not in columns:
            missing.append("work_items.predecessor_id")
        if missing:
            raise _SchemaMismatch(missing)
        def rows(query):
            return [dict(row) for row in db.execute(query)]
        # Read for the plan summary of a job with an initial root and dropped from every job entry (diagnose); a
        # ledger older than root_repo reads as having none.
        root_repo = "w.root_repo" if "root_repo" in columns else "NULL"
        # Withdrawn work (design §5.1 commit 6): each column is read only where the ledger has it, and a ledger
        # older than it reports none of the findings that need it.
        checks = {row["name"] for row in db.execute("PRAGMA table_info(issue_checks)")}
        present = {name: f"w.{name}" if name in columns else "NULL"
                   for name in ("authority", "withdraw_deadline", "withdraw_reason")}
        undelegated_since = "c.undelegated_since" if "undelegated_since" in checks else "NULL"
        withdrawal = {
            "active": rows(f"""SELECT w.id AS item_id,w.issue_id,json_extract(i.metadata,'$.identifier') AS identifier,
                w.skill,w.state,w.updated_at,{present['authority']} AS authority,
                {present['withdraw_deadline']} AS withdraw_deadline,{present['withdraw_reason']} AS withdraw_reason,
                json_extract(i.metadata,'$.delegate_id') AS stored_delegate_id,s.delegation AS session_delegation,
                {undelegated_since} AS undelegated_since
                FROM work_items w JOIN issues i ON i.id=w.issue_id
                LEFT JOIN sessions s ON s.session_id=w.session_id LEFT JOIN issue_checks c ON c.issue_id=w.issue_id
                WHERE w.state IN ('queued','running','awaiting_input','awaiting_resource')
                ORDER BY w.created_at,w.id"""),
            # The item a deferred delegation waits for is kept in its `error` column (Receiver.process_one).
            "deferred": rows("""SELECT session_id,received_at,error AS waits_for FROM webhook_events
                WHERE status='deferred' ORDER BY received_at""") if "webhook_events" in tables else [],
            "columns": {name for name in present if name in columns} | ({"undelegated_since"} & checks),
        }
        # Closing activities Linear refused to the end (silent-delegation design P10), read only where the ledger has
        # the table: never the activity's text, and none that newer work in its thread has spoken after. The ledger
        # drops a row when its job's state changes; the state clause skips one a revision without that drop left.
        unclosed = rows("""SELECT c.session_id,c.issue_id,json_extract(i.metadata,'$.identifier') AS identifier,
            c.item_id,c.kind,c.attempts,c.created_at AS owed_at,c.given_up_at,c.last_error
            FROM session_closures c LEFT JOIN issues i ON i.id=c.issue_id
            WHERE c.given_up_at IS NOT NULL
            AND (c.item_id IS NULL OR EXISTS (SELECT 1 FROM work_items w WHERE w.id=c.item_id AND w.state=c.item_state))
            AND NOT EXISTS (SELECT 1 FROM work_items w WHERE w.session_id=c.session_id AND w.created_at>c.created_at)
            ORDER BY c.created_at,c.session_id""") if "session_closures" in tables else []
        # Delegations Linear opened no session for (silent-delegation design P12), read only where the ledger has the
        # table: one still waiting to be settled, and one that met no open thread, unless a delegation session of the
        # card, a Linear thread, has been recorded since the read that found the card not delegated.
        silent = rows("""SELECT e.issue_id,json_extract(i.metadata,'$.identifier') AS identifier,e.state,e.since,
            e.due_at,e.settled_at,e.attempts,e.error
            FROM delegation_episodes e LEFT JOIN issues i ON i.id=e.issue_id
            WHERE e.state='waiting' OR (e.state='unseen' AND NOT EXISTS (
                SELECT 1 FROM sessions s WHERE s.issue_id=e.issue_id AND s.delegation=1
                AND s.session_id NOT LIKE 'local-%' AND s.created_at>=e.mark))
            ORDER BY e.since,e.issue_id""") if "delegation_episodes" in tables else []
        return {
            "_withdrawal": withdrawal,
            "_unclosed": unclosed,
            "_silent": silent,
            "counts": {row["state"]: row["count"] for row in rows(
                "SELECT state,COUNT(*) AS count FROM work_items GROUP BY state")},
            "jobs": rows(f"""SELECT w.id AS item_id,w.issue_id,json_extract(i.metadata,'$.identifier') AS identifier,
                w.skill,w.state,w.stage,w.host,w.worker_pid,w.lease_expires_at,w.updated_at,
                {root_repo} AS _root_repo,w.checkpoint AS _checkpoint
                FROM work_items w JOIN issues i ON i.id=w.issue_id
                WHERE w.state IN ('queued','running','awaiting_input','awaiting_resource')
                OR (w.state IN ('blocked','failed') AND NOT EXISTS (
                    SELECT 1 FROM work_items successor WHERE successor.predecessor_id=w.id))
                OR EXISTS (SELECT 1 FROM job_cleanup c WHERE c.item_id=w.id AND c.done=0)
                ORDER BY w.created_at,w.id"""),
            "slots": rows("SELECT slot_id,kind,host,folder,state,updated_at FROM slots ORDER BY slot_id"),
            "resource_recoveries": rows("""SELECT id,slot_id,item_id,state,attempts,detached,due_at,lease_until,
                error IS NOT NULL AS has_error,evidence,updated_at FROM resource_recoveries
                WHERE state != 'recovered' ORDER BY created_at,id""") if 'resource_recoveries' in tables else [],
            "reservations": rows("""SELECT reservation_id,item_id,resource,host,state,created_at,acquired_at
                FROM reservations WHERE state IN ('queued','active','cancel_requested') ORDER BY sequence"""),
            "cleanup_pending": rows("""SELECT item_id,worker_pid,error IS NOT NULL AS has_error,updated_at
                FROM job_cleanup WHERE done=0 ORDER BY updated_at,item_id"""),
            "issue_status_errors": rows("""SELECT c.issue_id,json_extract(i.metadata,'$.identifier') AS identifier,
                c.failures,c.checked_at,c.due_at FROM issue_checks c JOIN issues i ON i.id=c.issue_id
                WHERE c.error IS NOT NULL ORDER BY c.checked_at,c.issue_id"""),
        }


def _plan_summary(skill, root_repo, checkpoint_json, job, now):
    """Doctor's view of an unfinished job of a skill with an initial root (spec §9.11): its current root, the stage
    states and PR links its plan records and, while it waits for a person, the pause's kind (from the plan), reason
    and age (from the ledger: a parked item's updated_at is when it parked)."""
    try:
        checkpoint = json.loads(checkpoint_json)
    except (TypeError, ValueError, RecursionError):
        checkpoint = {}
    checkpoint = checkpoint if isinstance(checkpoint, dict) else {}
    plan = checkpoint.get("plan") if isinstance(checkpoint.get("plan"), dict) else {}
    stages = plan.get("stages") if isinstance(plan.get("stages"), dict) else {}
    pause = None
    if job["state"] == "awaiting_input":
        recorded = plan.get("pause") if isinstance(plan.get("pause"), dict) else {}
        pause = {"kind": recorded.get("kind") if recorded.get("kind") in PAUSE_KINDS else None,
                 "reason": checkpoint.get("pending_reason") if checkpoint.get("pending_reason") in AWAIT_REASONS else None,
                 "age_seconds": max(0, int(now - job["updated_at"]))}
    try:
        prs = sorted(plan_work(plan)[1].values())
    except RecursionError:  # checkpoint bounds a plan's size, not its depth
        prs = []
    return {"root": current_root(root_repo, skill),
            "stages": {letter: "skipped" if state.startswith("skipped") else state
                       for letter, state in sorted(stages.items())
                       if letter in STAGE_LETTERS and isinstance(state, str)
                       and (state in ("pending", "done") or state.startswith("skipped"))},
            "pause": pause,
            "prs": prs}


def _logs(paths, item_id):
    # Ledger item IDs are UUIDs. Do not traverse arbitrary paths in a damaged ledger.
    UUID(item_id)
    directory = paths.runs / item_id
    files = []
    try:
        mode = directory.lstat().st_mode
    except FileNotFoundError:
        return {"directory": str(directory), "files": []}
    if not stat.S_ISDIR(mode):
        raise OSError("run directory is not a regular directory")
    # glob() suppresses scanning errors on Python 3.13; iterdir() must expose them.
    for attempt in sorted(directory.iterdir()):
        try:
            mode = attempt.lstat().st_mode
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(mode):
            continue
        for name in ("stdout.log", "stderr.log", "last_message.txt", "process.json", "killed.json"):
            path = attempt / name
            try:
                mode = path.lstat().st_mode
            except FileNotFoundError:
                continue
            if stat.S_ISREG(mode):
                files.append(str(path))
    return {"directory": str(directory), "files": files}


# The toolchain a feature worker runs (spec §8.5, §9.11). Pins are the repositories' own: protoc in farm-hive's
# config/pb/toolchain.env, buf, Node and openspec in Farm-Contract's CI, the SDK in farm-common's global.json. Go comes
# from farm-hive's go.mod in FarmBot's clone, else GO_MINIMUM, its go directive on 2026-09-28.
PROBE_TIMEOUT = 15
GO_MINIMUM = "1.25.1"
DOTNET_SDK = "8.0.423"  # optional: only farm-common's acceptance script needs it, on macOS or Linux
# Each probe stays offline and writes nothing: no Go toolchain download, telemetry or update check.
PROBE_ENV = {"GOTOOLCHAIN": "local", "DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_NOLOGO": "1",
             "OPENSPEC_TELEMETRY": "0", "DO_NOT_TRACK": "1", "OPENSPEC_NO_UPDATE_CHECK": "1",
             # lark-cli would otherwise create <home>/.lark-cli/cache and fetch API metadata from Feishu at startup.
             "LARKSUITE_CLI_NO_UPDATE_NOTIFIER": "1", "LARKSUITE_CLI_REMOTE_META": "off"}
_VERSION = re.compile(r"\d+(?:\.\d+)+")
_GO_DIRECTIVE = re.compile(r"^(?:go\s+|toolchain\s+go)(\d+\.\d+(?:\.\d+)?)\s*$", re.MULTILINE)


def _run(argv, env):
    """(found, completed) for one read-only command found on PATH; completed is None when it could not run."""
    path = shutil.which(argv[0])
    if path is None:
        return False, None
    try:
        return True, subprocess.run([path, *argv[1:]], capture_output=True, text=True, encoding="utf-8",
                                    errors="replace", timeout=PROBE_TIMEOUT, env=env, cwd=tempfile.gettempdir(),
                                    stdin=subprocess.DEVNULL, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return True, None


def _version(completed):
    if completed is None or completed.returncode:
        return None
    match = _VERSION.search(completed.stdout + "\n" + completed.stderr)
    return match[0] if match else None


def _numbers(version):
    return tuple(int(part) for part in version.split("."))


def _satisfies(version, required):
    """An exact version, `>=` a minimum, or any readable version when nothing is required."""
    if version is None:
        return False
    if required is None:
        return True
    if not required.startswith(">="):
        return version == required
    found, minimum = _numbers(version), _numbers(required[2:])
    width = max(len(found), len(minimum))
    return found + (0,) * (width - len(found)) >= minimum + (0,) * (width - len(minimum))


def _probe_environment(config):
    from .kw_ops import child_environment
    filtered = child_environment(config.kw_ops.get("token_env"))
    source = os.environ if filtered is None else filtered
    return {**source, **PROBE_ENV}


def _go_directive(config, paths):
    """The newest Go version farm-hive's go.mod names (its go and toolchain lines), read from FarmBot's own clone
    without a fetch; None when there is no clone or no readable go.mod."""
    if "farm-hive" not in config.repos:
        return None
    env = {**_probe_environment(config), "GIT_TERMINAL_PROMPT": "0", "GIT_NO_LAZY_FETCH": "1"}
    trees = Worktrees(paths.repos, paths.worktrees, config.repos)
    try:
        if trees.clone_problems("farm-hive", environ=env):
            return None
    except (OSError, subprocess.SubprocessError):
        return None
    for ref in ("refs/remotes/origin/HEAD", "refs/remotes/origin/main"):
        try:
            # A missing blob in a promisor clone can fetch through host config even when the clone is safe.
            # The CLI option also fails closed on a Git too old to support it.
            result = subprocess.run(["git", "--no-lazy-fetch", "--git-dir", str(paths.repos / "farm-hive.git"), *HOOKS_OFF,
                                     "cat-file", "blob", f"{ref}:go.mod"], capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=PROBE_TIMEOUT, stdin=subprocess.DEVNULL,
                                    env=env, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None
        versions = _GO_DIRECTIVE.findall(result.stdout) if not result.returncode else []
        if versions:
            return max(versions, key=_numbers)
    return None


def _lark_cli(config, env):
    """lark-cli's presence and whether the configured profile exists in the store workers read. Of `profile list` only
    names and counts are kept: never an app ID or a user's name."""
    block = config.lark_cli
    home = block.get("home")
    env = {**env, "HOME": home} if home else env
    found, completed = _run(["lark-cli", "--version"], env)
    profile = {"name": block.get("profile"), "home": home, "exists": False, "other_profiles": None,
               "user_logins": None, "master_key_file": None}
    entry = {"found": found, "version": _version(completed), "required": None, "ok": False, "profile": profile}
    if entry["version"] is None or not block:
        return entry
    _, listed = _run(["lark-cli", "profile", "list"], env)
    try:
        profiles = json.loads(listed.stdout) if listed is not None and not listed.returncode else None
    except ValueError:
        profiles = None
    if not isinstance(profiles, list):
        return entry
    profiles = [item for item in profiles if isinstance(item, dict)]
    names = [item.get("name") for item in profiles]
    profile.update(exists=block["profile"] in names, other_profiles=sum(name != block["profile"] for name in names),
                   user_logins=sum(bool(item.get("user")) for item in profiles))
    if sys.platform == "darwin":  # lark-cli's file fallback for the Keychain master key (keychain-downgrade)
        store = Path(home) if home else Path.home()
        profile["master_key_file"] = (store / "Library" / "Application Support" / "lark-cli" / "master.key.file").is_file()
    entry["ok"] = profile["exists"]
    return entry


def feature_toolchain(config, paths):
    """tools.feature: one {"found", "version", "required", "ok"} entry per tool a feature worker runs, the names of
    required entries that are not ok, and of optional ones. Doctor never runs lark-cli against Feishu."""
    env = _probe_environment(config)
    directive = _go_directive(config, paths)
    probes = {"go": (["go", "version"], f">={directive or GO_MINIMUM}"), "protoc": (["protoc", "--version"], "35.1"),
              "buf": (["buf", "--version"], "1.72.0"), "node": (["node", "--version"], ">=22"),
              "openspec": (["openspec", "--version"], "1.7.0"), "python3": (["python3", "--version"], None),
              "git_lfs": (["git-lfs", "version"], None)}
    if os.name == "nt":  # the repositories' bash gates on the Windows worker (Git for Windows)
        probes.update({name: ([name, "--version"], None) for name in ("bash", "sha256sum", "mktemp", "awk")})
    entries = {}
    for name, (argv, required) in probes.items():
        found, completed = _run(argv, env)
        version = _version(completed)
        entries[name] = {"found": found, "version": version, "required": required,
                         "ok": found and _satisfies(version, required)}
    entries["go"]["source"] = "farm-hive go.mod" if directive else "default"
    found, completed = _run(["dotnet", "--list-sdks"], env)
    sdks = re.findall(r"^(\d+\.\d+\.\d+)", completed.stdout, re.MULTILINE) if completed and not completed.returncode else []
    entries["dotnet_sdk"] = {"found": found, "version": DOTNET_SDK if DOTNET_SDK in sdks else (", ".join(sdks) or None),
                             "required": DOTNET_SDK, "ok": DOTNET_SDK in sdks}
    entries["lark_cli"] = _lark_cli(config, env)
    optional = ("dotnet_sdk",)
    return {"entries": entries,
            "missing": sorted(name for name, entry in entries.items() if not entry["ok"] and name not in optional),
            "optional_missing": [name for name in optional if not entries[name]["ok"]]}


def diagnose(config, *, now=None):
    report = _report(time.time() if now is None else now)
    paths = Paths(config)
    report.update(host=config.host, ledger=str(paths.ledger),
                  service_logs=str(paths.config_dir / "logs"))
    # Doctor sees its own environment, not the running controller's, and never reports the token itself.
    kw_ops = config.kw_ops
    report["tools"] = {"kw_ops": ({"configured": True, "token_env": kw_ops["token_env"],
                                   "token_set_in_doctor_environment": bool(os.environ.get(kw_ops["token_env"], "").strip())}
                                  if kw_ops else {"configured": False})}
    # spec §9.11: which of this checkout's skills the config runs; serve refuses a list it cannot honour.
    configured = config.enabled_skills is not None
    loaded = {}
    try:
        loaded = load_skills(ROOT / "skills")
    except (SkillError, OSError) as exc:
        report["skills"] = {"loaded": None, "enabled": None, "configured": configured}
        _finding(report, "skills_unreadable", "Check the manifests under this checkout's skills/; serve cannot start.",
                 incomplete=True, error_type=type(exc).__name__)
    else:
        report["skills"] = {"loaded": sorted(loaded), "enabled": None, "configured": configured}
        # What serve would try to run: without the key, every loaded skill but the opt-in ones (P1).
        names = ({name for name, skill in loaded.items() if not skill.opt_in} if config.enabled_skills is None
                 else set(config.enabled_skills))
        try:
            enabled = enabled_skills(loaded, config.enabled_skills, authority=SKILL_AUTHORITY)
        except SkillError:
            _finding(report, "enabled_skills_invalid",
                     "serve refuses to start with these skills: enable only skills in this checkout's skills/ that "
                     "the dispatch AUTHORITY covers, and include chat.", unknown=sorted(names - set(loaded)),
                     unbriefed=sorted((names & set(loaded)) - set(SKILL_AUTHORITY)), missing_chat="chat" not in names)
        else:
            report["skills"]["enabled"] = sorted(enabled)
            # The scheduler's own launch rule. serve still starts and queues these skills' jobs, and each then fails.
            unsupported = sorted(name for name, skill in enabled.items()
                                 if not runtime_can_launch(skill, config.runtime))
            if unsupported:
                _finding(report, "skill_runtime_unsupported",
                         "serve accepts delegations to these skills, but each job fails at launch: a repository-staged "
                         "skill needs Codex's workspace-write sandbox. Set runtime to codex, or leave them out of "
                         "enabled_skills, then restart the settled service.", runtime=config.runtime, skills=unsupported)
    # Plan P10: FarmBot's git refuses a clone that holds what FarmBot does not write there. Names, never values.
    trees = Worktrees(paths.repos, paths.worktrees, config.repos)
    unexpected = {}
    for repo in sorted(config.repos):
        if trees.clone_path(repo).exists():
            try:
                problems = trees.clone_problems(repo, environ=_probe_environment(config))
            except (OSError, subprocess.SubprocessError) as exc:
                problems = [f"unreadable ({type(exc).__name__})"]
            if problems:
                unexpected[repo] = problems
    if unexpected:
        _finding(report, "clone_unexpected",
                 "FarmBot refuses to use these clones: each holds config keys, info/ files or remote definitions "
                 "that FarmBot does not write, which could make its git run a program outside the sandbox. Find "
                 "out who wrote them, remove them, and the next job uses the clone again.", clones=unexpected)
    # spec §8.5, §9.11: probed only where this host enables feature, so other hosts run nothing more.
    if "feature" in (report["skills"]["enabled"] or []):
        feature = report["tools"]["feature"] = feature_toolchain(config, paths)
        if not config.lark_cli:
            _finding(report, "lark_cli_unconfigured", "serve refuses to start: set lark_cli.profile to the lark-cli "
                     "profile that holds FarmBot's Feishu app (docs/development-workflow.md).")
        if feature["missing"]:
            _finding(report, "feature_toolchain_incomplete", "Install or fix these before this host runs feature; its "
                     "workers stop where a tool is missing.", tools=feature["missing"])
        profile = feature["entries"]["lark_cli"]["profile"]
        if profile["master_key_file"] and profile["user_logins"]:
            _finding(report, "lark_cli_store_exposed", "The lark-cli store feature workers read keeps its master key in "
                     "a file and holds a user login, which every sandboxed worker can read; use a FarmBot-only "
                     "lark-cli home (docs/development-workflow.md).", user_logins=profile["user_logins"])
    try:
        snapshot = _snapshot(paths.ledger)
    except (OSError, sqlite3.Error, ValueError) as exc:
        _finding(report, "ledger_unreadable", "Check the ledger path, permissions and schema with this service version; no migration was attempted.",
                 incomplete=True, error_type=type(exc).__name__,
                 **({"missing_schema": exc.missing} if isinstance(exc, _SchemaMismatch) else {}))
        return report
    withdrawal, unclosed, silent = snapshot.pop("_withdrawal"), snapshot.pop("_unclosed"), snapshot.pop("_silent")
    report.update(snapshot)
    report["counts"]["total"] = sum(report["counts"].values())
    rooted = {name: skill for name, skill in loaded.items() if skill.initial_root}
    for job in report["jobs"]:
        root_repo, checkpoint = job.pop("_root_repo"), job.pop("_checkpoint")
        if job["skill"] in rooted and job["state"] in ACTIVE_STATES:
            job["plan"] = _plan_summary(rooted[job["skill"]], root_repo, checkpoint, job, report["checked_at"])
    jobs = {job["item_id"]: job for job in report["jobs"]}
    for job in report["jobs"]:
        try:
            job["logs"] = _logs(paths, job["item_id"])
        except (OSError, ValueError) as exc:
            job["logs"] = {"directory": None, "files": []}
            _finding(report, "logs_unreadable", "Inspect the run directory and item ID.", incomplete=True,
                     item_id=job["item_id"], error_type=type(exc).__name__)
        evidence = {key: job[key] for key in ("item_id", "issue_id", "identifier", "logs")}
        if job["state"] == "running":
            lease = job["lease_expires_at"]
            if lease is None or lease <= report["checked_at"]:
                _finding(report, "lease_expired", "Inspect worker logs and scheduler recovery; the running claim has no valid lease.", **evidence)
            if job["worker_pid"] is None:
                _finding(report, "worker_pid_missing", "A running claim has no recorded PID; inspect scheduler and run logs.", **evidence)
        if job["worker_pid"] is not None:
            job["process"] = (probe_process(job["worker_pid"], job["item_id"]) if job["host"] == config.host
                              else {"state": "unknown", "reason": "host_mismatch"})
            state = job["process"]["state"]
            if state != "alive":
                _finding(report, "worker_dead" if state == "dead" else "worker_unverified",
                         "Inspect this job on its owning host; check run logs before taking recovery action.",
                         incomplete=state == "unknown", process=job["process"], **evidence)
        if job["state"] in ("failed", "blocked"):
            _finding(report, "job_" + job["state"], "Inspect the job's retained run logs and Linear context before retrying.", **evidence)
    for cleanup in report["cleanup_pending"]:
        job = jobs.get(cleanup["item_id"], {})
        if cleanup["worker_pid"] is not None:
            cleanup["process"] = (probe_process(cleanup["worker_pid"], cleanup["item_id"])
                                  if job.get("host") == config.host
                                  else {"state": "unknown", "reason": "host_mismatch"})
            if cleanup["process"]["state"] == "unknown":
                _finding(report, "cleanup_worker_unverified", "Inspect the retained cleanup PID on its owning host before releasing resources.",
                         incomplete=True, item_id=cleanup["item_id"], process=cleanup["process"], logs=job.get("logs"))
        _finding(report, "cleanup_pending", "Inspect job_cleanup in the ledger and run logs; cleanup may still be in progress or preserving uncertain process/source evidence.",
                 **cleanup, identifier=job.get("identifier"), logs=job.get("logs"))
    for error in report["issue_status_errors"]:
        _finding(report, "issue_status_error", "Inspect issue_checks.error and service logs for the stored Linear status failure; connectivity was not probed.", **error)
    owned_slots = {r["resource"] for r in report["reservations"] if r["state"] in ("active", "cancel_requested")}
    for slot in report["slots"]:
        if slot["state"] == "held":
            _finding(report, "slot_held", "Controller recovery owns this slot. Inspect resource_recoveries and retained diagnostics; do not release it manually.", slot_id=slot["slot_id"], host=slot["host"])
        # release() leaves a slot switching while park_idle() returns it to the pool.
        elif slot["state"] in ("interactive_busy", "batch_busy") and slot["slot_id"] not in owned_slots:
            _finding(report, "slot_without_reservation", "Inspect the slot and reservation history; a busy slot has no active reservation.", slot_id=slot["slot_id"], host=slot["host"])
    slots = {s["slot_id"]: s for s in report["slots"]}
    for reservation in report["reservations"]:
        if reservation["state"] == "cancel_requested":
            _finding(report, "reservation_cancel_pending", "Inspect the resource owner and service logs; cancellation has not settled yet.", **reservation)
        if reservation["state"] in ("active", "cancel_requested") and reservation["resource"] not in slots:
            _finding(report, "reservation_slot_missing", "Inspect reservation history; its assigned slot is absent from this ledger.", **reservation)
    _withdrawal_findings(report, config, withdrawal)
    for closure in unclosed:
        if report["checked_at"] - closure["given_up_at"] <= UNCLOSED_SECONDS:
            _finding(report, "unclosed_session",
                     "FarmBot gave up this thread's closing activity: Linear refused it six times, or it was still "
                     "owed 40 minutes after the first refusal because the controller was down. The thread may still "
                     "show FarmBot waiting and block the card's next delegation. Check the session in Linear and "
                     "archive it if it waits.", **closure)
    for episode in silent:
        waiting = episode["state"] == "waiting"
        overdue = report["checked_at"] - (episode["since"] + SILENT_GRACE_SECONDS)
        if (overdue > SILENT_OVERDUE_SECONDS if waiting
                else report["checked_at"] - episode["settled_at"] <= SILENT_UNSEEN_SECONDS):
            _finding(report, "silent_delegation",
                     "A read found the card delegated to this app again, no agent session followed within 90 s, and "
                     "FarmBot found none of its threads on the card open; or the settle has been failing for more "
                     "than 10 minutes. Most likely a delegation through Linear's API, or a thread FarmBot cannot read "
                     "still waits. Open the card: answer or archive a waiting thread, or ask the person to choose No "
                     "agent and delegate again. If UI re-delegation still opens no session, archive this bot's "
                     "completed sessions on the card before trying again. FarmBot now attempts automatic session "
                     "creation when no thread can take the delegation. An uncertain creation is recovered by its "
                     "issue-link marker and is never blindly repeated; after a request that never reached Linear, "
                     "let the controller observe No agent before delegating again.", **episode,
                     overdue_seconds=int(overdue) if waiting else None)
    return report


def _stored_authority(row):
    """What authorised the job, as the ledger reads it (design K2): a row written before authorities were recorded
    reads as its skill's default, a write job's the delegation's and a conversation's a mention's."""
    return row["authority"] or ("delegation" if row["skill"] in WRITE_SKILLS else "mention")


def _withdrawal_findings(report, config, withdrawal):
    """Work that withdrawal should have ended, or that nothing would end (withdrawn-work design §5.1 commit 6, §5.3,
    B1, E4, H4). Evidence names jobs and times, never a question, a payload or issue prose."""
    now, columns = report["checked_at"], withdrawal["columns"]
    in_prefix = re.compile(rf"{re.escape(config.issue_prefix)}-[0-9]+")
    app = (config.expected_app_user_id or "").lower()
    for row in withdrawal["active"]:
        evidence = {key: row[key] for key in ("item_id", "issue_id", "identifier", "skill", "state")}
        authority = _stored_authority(row)
        # A flagged worker was withdrawn and runs on through its grace; withdrawal_overdue lists it past its deadline.
        # Flagged work back in the queue is never launched and waits for a read to end it, so it is still listed.
        flagged_running = row["state"] == "running" and row["withdraw_deadline"] is not None
        if ("undelegated_since" in columns and authority == "delegation" and row["undelegated_since"] is not None
                and not flagged_running
                and now - row["undelegated_since"] > UNDELEGATED_INTERVALS * config.reconcile_seconds):
            _finding(report, "undelegated_work",
                     "A status read found the card not delegated to this app, and a second read an interval later "
                     "should have withdrawn this job. Check issue_status_error findings, the lifecycle loop and the "
                     "service logs; a read that finds the delegation back clears the mark.",
                     undelegated_since=row["undelegated_since"], **evidence)
        if ("withdraw_deadline" in columns and row["state"] == "running" and row["withdraw_deadline"] is not None
                and now - row["withdraw_deadline"] > WITHDRAWAL_OVERDUE_SECONDS):
            _finding(report, "withdrawal_overdue",
                     "The controller stops a withdrawn worker at its deadline, and this one still runs. Check the "
                     "scheduler loop, the worker's process and its run logs.",
                     withdraw_deadline=row["withdraw_deadline"], withdraw_reason=row["withdraw_reason"], **evidence)
        if row["state"] == "awaiting_input" and now - row["updated_at"] > LONG_PARKED_SECONDS:
            _finding(report, "long_parked",
                     "This job has waited more than 7 days for an answer. Check its session in Linear: a lost reply, or "
                     "a session archived while the card stayed delegated, never resumes it. Ask the person, or cancel it.",
                     parked_seconds=int(now - row["updated_at"]), **evidence)
        if not (isinstance(row["identifier"], str) and in_prefix.fullmatch(row["identifier"])):
            _finding(report, "outside_prefix",
                     "The job's issue is outside this host's issue_prefix, perhaps moved to another team, which FarmBot "
                     "does not detect. Decide whether to cancel it.", issue_prefix=config.issue_prefix, **evidence)
        if (app and (row["stored_delegate_id"] or "").lower() != app
                and (authority == "delegation" or (row["authority"] is None and row["session_delegation"]))):
            _finding(report, "stored_undelegated",
                     "The stored card is not delegated to the pinned app. Once this revision runs, a status read "
                     "confirming that withdraws the delegation's work; a conversation stored without an authority is "
                     "kept. Decide on each before deploying (withdrawn-work design §5.3).",
                     authority=authority, authority_recorded=row["authority"] is not None, **evidence)
    for event in withdrawal["deferred"]:
        if now - event["received_at"] > DEFERRED_SECONDS:
            _finding(report, "deferred_delegation",
                     "A delegation has waited more than 45 minutes for another session's worker to stop. Check that "
                     "job (withdrawal_overdue) and the receiver loop; a reply in the session routes it again.",
                     session_id=event["session_id"], waits_for=event["waits_for"],
                     deferred_seconds=int(now - event["received_at"]))


def run(config_path=None):
    try:
        config = load_config(config_path, secure_permissions=False)
        # Validate paths before reporting so malformed configuration remains machine-readable.
        Paths(config)
    except (OSError, ValueError, TypeError) as exc:
        report = _report(time.time())
        _finding(report, "config_unreadable", "Check the config path, permissions and JSON fields; configuration was not modified.",
                 incomplete=True, error_type=type(exc).__name__)
        return report
    return diagnose(config)
