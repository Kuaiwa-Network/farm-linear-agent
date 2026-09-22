"""Run FarmBot: receiver HTTP server plus scheduler loop in one process (spec §3)."""
from collections import namedtuple
from datetime import datetime, timezone
import argparse
import json
import shutil
import signal
import threading
from pathlib import Path

from .config import Paths, configure, linear_api, load_config, ROOT
from .deploy import AGENTS, install, missing_tools
from .launcher import RUNTIMES, Launcher
from .ledger import Ledger
from .lifecycle import Lifecycle
from .publication import PublicationVerifier
from .receiver import Receiver, make_server
from .router import WRITE_SKILLS
from .scheduler import Scheduler
from .skills import load_skills
from .slots import SlotError, SlotPool, UnityIdentity, slot_entry
from .worktrees import Worktrees

Components = namedtuple("Components",
                        "config paths api ledger skills worktrees launcher scheduler receiver server pool lifecycle",
                        defaults=(None,))


def build(config, runtime_override=None):
    paths = Paths(config)
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    api = linear_api(config)
    identity = api.identity()
    skills = load_skills(ROOT / "skills")
    worktrees = Worktrees(paths.repos, paths.worktrees, config.repos)
    runtime = RUNTIMES[runtime_override or config.runtime]
    launcher = Launcher(paths.runs, runtime, config.host)
    ledger = Ledger(paths.ledger, check_same_thread=False)
    # One list of entries, read by both: the pool switches and runs the slots it describes, and the
    # scheduler tells the worker which -buildTarget that slot was switched to.
    entries = [slot_entry(raw) for raw in config.slots]
    scheduler = Scheduler(ledger, launcher, skills, worktrees, skill_root=ROOT / "skills", db_path=paths.ledger,
                          runtime_name=runtime.name, host=config.host, max_concurrent=config.max_concurrent,
                          codex_workers=config.codex_workers,
                          config_path=config.source_path,
                          slot_entries={entry["id"]: entry for entry in entries},
                          guidance_for=lambda item: (ledger.session(item["session_id"]) or {}).get("guidance") or "",
                          api=api, control_ledger_factory=lambda: Ledger(paths.ledger),
                          publication=PublicationVerifier(worktrees))
    lifecycle = Lifecycle(Ledger(paths.ledger, check_same_thread=False), api, scheduler,
                          interval=config.reconcile_seconds)

    def preflight(item):
        current = Ledger(paths.ledger)
        try:
            return Lifecycle(current, api, scheduler, interval=config.reconcile_seconds).preflight(item)
        finally:
            current.close()
    scheduler.preflight = preflight
    receiver = Receiver(paths.ledger, config.webhook_secret,
                        {"oauthClientId": config.client_id, "appUserId": identity["viewer"]["id"],
                         "organizationId": identity["organization"]["id"]},
                        api, lambda: Ledger(paths.ledger, check_same_thread=False), set(skills), scheduler,
                        worktrees=worktrees, default_server_environment=config.default_server_environment)
    server = make_server(receiver, config.port)
    # A factory, not the scheduler's connection: the pool runs on its own thread and two threads on one
    # sqlite3.Connection interleave their BEGIN IMMEDIATE blocks. The receiver already takes one of these.
    pool = SlotPool(lambda: Ledger(paths.ledger, check_same_thread=False),
                    worktrees, entries, host=config.host,
                    editors_root=paths.editors, state_dir=launcher.state_dir,
                    # The one process FarmBot starts outside the worker seatbelt, and the pool is what
                    # composes its argv: Unity cannot run inside sandbox_workspace_write at all.
                    run_unsandboxed=launcher.run_unsandboxed,
                    mcp=UnityIdentity(ROOT / "agent" / "probes" / "editor-readiness.cs.txt"))
    return Components(config, paths, api, ledger, skills, worktrees, launcher, scheduler, receiver, server, pool, lifecycle)


def seed_clones(config, source_root=None):
    """Create FarmBot's bare clones ahead of the first launch, seeding from local checkouts when given."""
    paths = Paths(config)
    trees = Worktrees(paths.repos, paths.worktrees, config.repos)
    report = {}
    for repo in config.repos:
        seed = None
        if source_root:
            base = Path(source_root).expanduser().resolve()  # git reads a relative path against the clone
            for name in (repo, f"farm-{repo}"):  # the common repo is checked out as farm-common
                candidate = base / name
                if (candidate / ".git").exists() or (candidate / "HEAD").exists():
                    seed = candidate
                    break
        if trees.clone_path(repo).exists():
            report[repo] = "present"
            continue
        trees.ensure_clone(repo, seed_from=seed)
        report[repo] = f"seeded from {seed}" if seed else "created"
    return report


def enqueue(config, *, issue_ref, skill, commit=None, session=None):
    """Create a work item directly, for a host whose webhook cannot be delivered.

    Two honesties. The delegation flag is read from the issue, never asserted: spec §4 says a write-capable
    skill starts only from delegation, and the operator's invocation of this command is recorded in `audit`
    as the human act rather than being disguised as one in Linear. And the session id is synthetic, prefixed
    `local-` — no Linear agent session exists for it — so `create_activity` would fail on every call, and the
    scheduler and the worker both read that prefix and report through an issue comment instead.
    """
    paths = Paths(config)
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    api = linear_api(config)
    ledger = Ledger(paths.ledger)
    try:
        issue = api.fetch_issue(issue_ref)
        observed = ledger.observe_issue(issue)
        delegated = issue.get("delegate_id") == api.app_user_id
        if skill in WRITE_SKILLS and not delegated:
            raise RuntimeError(f"{skill} is write-capable and this issue is not delegated to FarmBot; "
                               f"delegate it in Linear first (spec §4)")
        session = session or f"local-{observed['id']}"
        ledger.ensure_session(session, observed["id"], delegated)
        trees = Worktrees(paths.repos, paths.worktrees, config.repos)
        target = {"repository": "Farm-Client", "requested_ref": "default",
                  "commit_sha": commit or trees.resolve_commit("Farm-Client"),
                  "server_environment": config.default_server_environment,
                  "selected_at": datetime.now(timezone.utc).isoformat()}
        ledger.set_session_target(session, target)
        item = ledger.create_work_item(issue_id=observed["id"], session_id=session, skill=skill, target=target)
        ledger.note(item["id"], "enqueue", f"operator enqueued {skill} for {observed['identifier']}")
        return item
    finally:
        ledger.close()


def serve(config_path=None, components=None):
    components = build(load_config(config_path)) if components is None else components
    stop = threading.Event()

    def guarded(name, body):
        """One loop iteration never kills its thread: log the kind of failure and back off."""
        def loop():
            while not stop.is_set():
                try:
                    body()
                except Exception as exc:
                    print(json.dumps({"event": "loop_error", "loop": name, "error": type(exc).__name__}), flush=True)
                    stop.wait(1.0)
        return loop

    def receive_once():
        if not components.receiver.process_one():
            stop.wait(0.1)

    def schedule_once():
        components.scheduler.tick()
        stop.wait(1.0)

    def pool_once():
        # A slot switch is git checkout plus git lfs checkout on a multi-GB .git, an Editor refresh and a
        # compile wait — minutes. Scheduler.tick() holds its lock for its whole body and runs every second,
        # so running a switch inside one would freeze reaping, recovery and Stop.
        components.pool.tick()
        stop.wait(2.0)

    threads = [threading.Thread(target=guarded("receive", receive_once), daemon=True),
               threading.Thread(target=guarded("schedule", schedule_once), daemon=True),
               threading.Thread(target=guarded("pool", pool_once), daemon=True)]
    if components.lifecycle is not None:
        def reconcile_once():
            result = components.lifecycle.tick()
            stop.wait(0.1 if result["checked"] else 1.0)
        threads.append(threading.Thread(target=guarded("lifecycle", reconcile_once), daemon=True))
    main_thread = threading.current_thread() is threading.main_thread()
    previous_sigterm = signal.getsignal(signal.SIGTERM) if main_thread else None

    def terminate(signum, frame):
        # Unwind the main thread, including ensure() at startup. Calling server.shutdown() here
        # would deadlock: serve_forever() cannot finish while its own thread waits in shutdown().
        if not stop.is_set():
            raise KeyboardInterrupt

    try:
        if main_thread:
            signal.signal(signal.SIGTERM, terminate)
        try:
            components.pool.ensure()
        except SlotError as exc:
            # A host whose slot cannot be prepared must still answer Linear: the pool is simply empty,
            # every unity_slot request stays queued, and the operator gets the folder to inspect.
            print(json.dumps({"event": "slot_pool_unavailable", "error": str(exc)}), flush=True)
        for thread in threads:
            thread.start()
        print(json.dumps({"event": "ready", "listen": f"http://127.0.0.1:{components.server.server_address[1]}",
                          "runtime": components.launcher.runtime.name, "host": components.config.host,
                          "skills": sorted(components.skills)}), flush=True)
        components.server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        try:
            components.server.server_close()
            # Before joins: a pool thread waiting on a batch Editor must be released before its
            # ledger closes, and the Editor must not outlive the service holding the slot folder.
            components.launcher.stop_all_unsandboxed()
            for thread in threads:
                if thread.ident is not None:  # ensure() may have been interrupted before threads start
                    thread.join(timeout=20)
            components.receiver.close()
            components.ledger.close()
            components.pool.close()
            if components.lifecycle is not None:
                components.lifecycle.ledger.close()
        finally:
            if main_thread:
                signal.signal(signal.SIGTERM, previous_sigterm)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python3 -m agent.service")
    parser.add_argument("command", choices=["configure", "serve", "status", "seed-clones", "install-launchd",
                                            "enqueue", "slots", "doctor", "recover-worker-cleanup"])
    parser.add_argument("--config")
    parser.add_argument("--from", dest="source_root", help="directory holding local checkouts to seed from")
    parser.add_argument("--issue", help="Linear issue id or identifier to enqueue work for")
    parser.add_argument("--skill", default="fix")
    parser.add_argument("--commit", help="pin this commit instead of resolving the default branch")
    parser.add_argument("--item", help="terminal work item whose legacy cleanup needs recovery")
    parser.add_argument("--reason", help="operator explanation for cleanup recovery")
    args = parser.parse_args(argv)
    if args.command == "recover-worker-cleanup":
        from .cleanup_recovery import recover_after_boot
        if not args.item or not args.reason:
            parser.error("recover-worker-cleanup requires --item and --reason")
        print(json.dumps(recover_after_boot(load_config(args.config), args.item, args.reason), indent=2))
        return 0
    if args.command == "doctor":
        from .doctor import run
        report = run(args.config)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return {"ok": 0, "attention": 1, "incomplete": 2}[report["status"]]
    if args.command == "configure":
        return configure(args.config)
    if args.command == "install-launchd":
        config = load_config(args.config)
        missing = missing_tools(config)
        if missing:
            raise RuntimeError(f"not on PATH: {', '.join(missing)}; install them before writing launchd agents, "
                               "because a launchd job cannot resolve a bare name")
        target = Path.home() / "Library" / "LaunchAgents"
        written = install(config, target, cloudflared=shutil.which("cloudflared"), config_path=config.source_path)
        print(json.dumps({label: str(path) for label, path in written.items()}, indent=2))
        print("\nLoad them with:")
        for label in AGENTS.values():
            print(f"  launchctl bootstrap gui/$(id -u) {target}/{label}.plist")
        print("\nStop and remove with `launchctl bootout gui/$(id -u)/<label>`.")
        return 0
    if args.command == "seed-clones":
        print(json.dumps(seed_clones(load_config(args.config), args.source_root), ensure_ascii=False, indent=2))
        return 0
    if args.command == "status":
        config = load_config(args.config)
        ledger = Ledger(Paths(config).ledger)
        try:
            print(json.dumps({**ledger.status(), **ledger.lifecycle_status(), "borrowed_comments": ledger.borrowed_comments()},
                             ensure_ascii=False, indent=2))
        finally:
            ledger.close()
        return 0
    if args.command == "enqueue":
        if not (args.issue or "").strip():
            raise SystemExit("enqueue needs --issue: the Linear issue id or identifier to create work for")
        config = load_config(args.config)
        print(json.dumps(enqueue(config, issue_ref=args.issue, skill=args.skill, commit=args.commit),
                         ensure_ascii=False, indent=2))
        return 0
    if args.command == "slots":
        config = load_config(args.config)
        ledger = Ledger(Paths(config).ledger)
        try:
            print(json.dumps({"slots": ledger.slots(), "reservations": ledger.reservations(
                ("queued", "active", "cancel_requested"))}, ensure_ascii=False, indent=2))
        finally:
            ledger.close()
        return 0
    serve(args.config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
