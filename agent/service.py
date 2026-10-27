"""Run FarmBot: receiver HTTP server plus scheduler loop in one process (spec §3)."""
from collections import namedtuple
from datetime import datetime, timezone
import argparse
import json
import os
import shutil
import signal
import threading
import time
from pathlib import Path

from .config import Paths, configure, linear_api, load_config, require_lark_cli, ROOT
from .deploy import install, missing_tools
from .dispatch import SKILL_AUTHORITY
from .environment import ControllerGuard, check_ownership, validate_runtime
from .heartbeat import Heartbeat, INTERVAL as HEARTBEAT_INTERVAL, RETRY_AFTER as HEARTBEAT_RETRY, source_revision
from .launcher import RUNTIMES, Launcher
from .ledger import Ledger
from .lifecycle import Lifecycle
from .publication import PublicationVerifier
from .resource_recovery import RecoveryController
from .resource_admission import ResourceAdmission
from .server_slots import ServerSlotPool, ServerSlotError
from .receiver import Receiver, make_server
from .router import BOT_GROUP, BOT_SKILLS, WRITE_SKILLS, bot_children
from .workflow import JobWorktrees
from .scheduler import Scheduler
from .session_progress import SessionProgress
from .skills import enabled_skills, load_skills
from .slots import SlotError, SlotPool, UnityIdentity, fixed_editor_count, slot_entry
from .worktrees import Worktrees
from .unity import editor_holds_project

Components = namedtuple("Components",
                        "config paths api ledger skills worktrees launcher scheduler receiver server pool lifecycle progress recovery server_pool go_dependencies review",
                        defaults=(None, None, None, None, None, None))


def build(config, runtime_override=None, *, replay_legacy=False):
    if replay_legacy and (runtime_override or config.runtime) != 'fake':
        raise ValueError('historical workflow replay is available only to offline fake fixtures')
    validate_runtime(config, runtime_override)
    check_ownership(config, require_initialized=True)
    if config.go_dependencies:
        from .go_dependencies import ensure as ensure_dependencies
        ensure_dependencies(config)
    skills = load_skills(ROOT / "skills", replay_legacy=replay_legacy, include_internal=True)
    # spec §9.11, before any client or state exists: a name the checkout lacks, or a skill the dispatch cannot
    # brief, stops startup.
    enabled = enabled_skills(skills, config.enabled_skills, authority={**SKILL_AUTHORITY, 'review': 'internal delivery trigger'})
    if replay_legacy:
        require_lark_cli(config, enabled)
    elif set(enabled) - {'work', 'chat', 'review'}:
        raise ValueError('retired execution profiles: configure enabled_skills as [chat, work]; adopt settled historical jobs explicitly')
    # Delivery is an internal trigger, never an intake route or ordinary worker.
    enabled.pop('review', None)
    skills.pop('review', None)
    paths = Paths(config)
    api = linear_api(config)
    identity = api.identity()
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    worktrees = Worktrees(paths.repos, paths.worktrees, config.repos)
    runtime = RUNTIMES[runtime_override or config.runtime]
    token_env = config.kw_ops.get("token_env")
    launcher = Launcher(paths.runs, runtime, config.host, token_env=token_env, lark_cli=config.lark_cli,
                        codex_windows_sandbox=config.codex_windows_sandbox)
    ledger = Ledger(paths.ledger, check_same_thread=False)
    # One list of entries, read by both: the pool switches and runs the slots it describes, and the
    # scheduler tells the worker which -buildTarget that slot was switched to.
    entries = [slot_entry(raw) for raw in config.slots]
    editor_count = fixed_editor_count(config.unity_pool, config.slots)
    if editor_count:
        entries = entries[:editor_count]
    scheduler = Scheduler(ledger, launcher, skills, worktrees, skill_root=ROOT / "skills", db_path=paths.ledger,
                          replay_legacy=replay_legacy,
                          runtime_name=runtime.name, host=config.host, max_concurrent=config.max_concurrent,
                          codex_workers=config.codex_workers,
                          config_path=config.source_path,
                          issue_prefix=config.issue_prefix,
                          bot_name=config.expected_bot_name,
                          kw_ops=config.kw_ops,
                          lark_cli=config.lark_cli,
                          go_dependencies=config.go_dependencies,
                          default_server_environment=config.default_server_environment,
                          server_slots_root=paths.server_slots if config.server_slots else None,
                          enabled_skills=set(enabled),
                          slot_entries={entry["id"]: entry for entry in entries},
                          unity_pool=config.unity_pool,
                          guidance_for=lambda item: (ledger.session(item["session_id"]) or {}).get("guidance") or "",
                          api=api, control_ledger_factory=lambda: Ledger(paths.ledger),
                          publication=PublicationVerifier(JobWorktrees(worktrees, ledger), issue_prefix=config.issue_prefix))
    lifecycle = Lifecycle(Ledger(paths.ledger, check_same_thread=False), api, scheduler,
                          interval=config.reconcile_seconds)

    def preflight(item):
        current = Ledger(paths.ledger)
        try:
            return Lifecycle(current, api, scheduler, interval=config.reconcile_seconds).preflight(item)
        finally:
            current.close()
    scheduler.preflight = preflight
    if not replay_legacy:
        from .version_targets import TargetController
        scheduler.version_targets = TargetController(ledger, worktrees, config.version_targets)
    receiver = Receiver(paths.ledger, config.webhook_secret,
                        {"oauthClientId": config.client_id, "appUserId": identity["viewer"]["id"],
                         "organizationId": identity["organization"]["id"]},
                        api, lambda: Ledger(paths.ledger, check_same_thread=False), set(enabled), scheduler,
                        worktrees=worktrees, default_server_environment=config.default_server_environment,
                        bot_name=config.expected_bot_name)
    server = make_server(receiver, config.port)
    # A factory, not the scheduler's connection: the pool runs on its own thread and two threads on one
    # sqlite3.Connection interleave their BEGIN IMMEDIATE blocks. The receiver already takes one of these.
    admission = ResourceAdmission(config.resource_admission) if config.resource_admission else None
    pool = SlotPool(lambda: Ledger(paths.ledger, check_same_thread=False),
                    worktrees, entries, host=config.host,
                    editors_root=paths.editors, state_dir=launcher.state_dir,
                    # The pool composes each Unity argv outside the worker seatbelt;
                    # independent reserved slots may execute their batches together.
                    run_unsandboxed=launcher.run_unsandboxed,
                    concurrent_batches=True,
                    admission=admission,
                    keep_open=bool(editor_count),
                    worker_acquire=scheduler.acquire_unity,
                    mcp=UnityIdentity(ROOT / "agent" / "probes" / "editor-readiness.cs.txt",
                                      token_env=token_env, secret_env=config.lark_cli.get("secret_env"),
                                      console_evidence_root=paths.config_dir / 'unity-console'))
    progress = SessionProgress(Ledger(paths.ledger, check_same_thread=False), api)
    recovery_ledger = Ledger(paths.ledger, check_same_thread=False)
    recovery_pool = SlotPool(recovery_ledger, worktrees, entries, host=config.host, editors_root=paths.editors,
                            state_dir=launcher.state_dir,
                            admission=admission,
                            keep_open=bool(editor_count),
                            mcp=UnityIdentity(ROOT / 'agent' / 'probes' / 'editor-readiness.cs.txt',
                                              token_env=token_env, secret_env=config.lark_cli.get("secret_env"),
                                              console_evidence_root=paths.config_dir / 'unity-console'),
                            editor_pid=lambda folder: editor_holds_project(folder, strict=True))
    recovery = RecoveryController(recovery_ledger, recovery_pool, host=config.host,
                                  evidence_root=paths.config_dir / 'resource-recovery',
                                  fence=scheduler.fence_resource_worker, api=api)
    pool.single_only = not replay_legacy
    server_pool = (ServerSlotPool(lambda: Ledger(paths.ledger, check_same_thread=False), config.server_slots,
                                 host=config.host, root=paths.server_slots, state_dir=launcher.state_dir,
                                 single_only=not replay_legacy,
                                 quiescent=scheduler.resource_quiescent, admission=admission)
                   if config.server_slots else None)
    if server_pool:
        def prepare_integration(reservation):
            if not server_pool.ready or not server_pool.docker_binding:
                raise ServerSlotError('server pool prerequisites unavailable')
            # Never share the server loop's SQLite connection with Unity hand-over.
            helper = ServerSlotPool(lambda: Ledger(paths.ledger), config.server_slots,
                host=config.host, root=paths.server_slots, state_dir=launcher.state_dir,
                quiescent=scheduler.resource_quiescent, admission=admission)
            try:
                # Keep the startup-verified identity across the thread-local helper.
                # DockerServices.start still checks the current daemon against it.
                helper.docker_binding = dict(server_pool.docker_binding)
                return helper.prepare_integration(reservation)
            finally:
                helper.close()
        pool.prepare_integration = prepare_integration
    dependencies = None
    if config.go_dependencies:
        from .go_dependencies import Controller as DependencyController
        dependencies = DependencyController(Ledger(paths.ledger, check_same_thread=False), worktrees, config)
    review = None
    from .review_policy import settings as review_settings
    review_policy = review_settings(config.auto_merge, config.repos)
    if review_policy['mode'] != 'off' or ledger.connection.execute("SELECT 1 FROM review_attempts WHERE state IN "
            "('queued','running','submitted','stopping') LIMIT 1").fetchone():
        from .review_controller import Controller as ReviewController
        from .review_git import CandidateGit
        from .review_github import GitHub as ReviewGitHub, StubGitHub as ReviewStubGitHub
        from .review_store import ReviewStore
        from .review_workers import ReviewWorkers
        review_ledger = Ledger(paths.ledger, check_same_thread=False)
        review_launcher = Launcher(paths.runs / 'review', runtime, config.host, token_env=token_env,
            lark_cli=config.lark_cli, codex_windows_sandbox=config.codex_windows_sandbox)
        candidate_git = CandidateGit(worktrees)
        review_workers = ReviewWorkers(ReviewStore(review_ledger), review_launcher, candidate_git, config, review_policy,
                                       author_launcher=launcher)
        stub = os.environ.get('FARMBOT_REVIEW_GITHUB_STUB_DIR')
        review_github = ReviewStubGitHub(config.repos, stub) if stub else ReviewGitHub(config.repos)
        review = ReviewController(review_ledger, config, api, review_github, candidate_git, review_workers)
    # `skills` is the enabled set the ready line reports, not every manifest the checkout loads.
    return Components(config, paths, api, ledger, set(enabled), worktrees, launcher, scheduler, receiver, server, pool,
                      lifecycle, progress, recovery, server_pool, dependencies, review)


def seed_clones(config, source_root=None):
    """Create FarmBot's bare clones ahead of the first launch, seeding from local checkouts when given."""
    with ControllerGuard(config):
        return _seed_clones(config, source_root)


def _seed_clones(config, source_root=None):
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


def enqueue(config, *, issue_ref, skill, commit=None, session=None, replay_legacy=False):
    """Create a work item directly, for a host whose webhook cannot be delivered.

    Two honesties. The delegation flag is read from the issue, never asserted: spec §4 says a write-capable
    skill starts only from delegation, and the operator's invocation of this command is recorded in `audit`
    as the human act rather than being disguised as one in Linear. And the session id is synthetic, prefixed
    `local-` — no Linear agent session exists for it — so `create_activity` would fail on every call, and the
    scheduler and the worker both read that prefix and report through an issue comment instead.
    """
    if replay_legacy and config.runtime != 'fake':
        raise ValueError('historical enqueue replay requires an offline fake fixture')
    check_ownership(config, require_initialized=True)
    if not replay_legacy and skill not in ('work', 'chat'):
        raise RuntimeError('new issues use work; old profiles are retained only for history and explicit adoption')
    loaded = load_skills(ROOT / "skills", replay_legacy=replay_legacy)
    enabled = enabled_skills(loaded, config.enabled_skills, authority=SKILL_AUTHORITY)
    if replay_legacy:
        require_lark_cli(config, enabled)
    elif set(enabled) - {'work', 'chat'}:
        raise ValueError('configure enabled_skills as [chat, work] before new intake')
    if skill not in enabled:
        # An opt-in skill is loaded but runs only where the list names it (P1); say so, as the list alone does not.
        rule = (f"{skill} is opt-in, so enabled_skills in the private config must name it"
                if skill in loaded and loaded[skill].opt_in else "enabled_skills in the private config chooses them")
        raise RuntimeError(f"{skill} is not a skill this instance runs ({', '.join(sorted(enabled))}); {rule} "
                           "(spec §9.11)")
    if skill in ("work", "feature", "fgui", "scene") and commit is not None:
        # Initial Code/UI authoring has no Client verification target.
        raise RuntimeError(f"--commit pins a fix's Farm-Client target, and a {skill} job takes none")
    api = linear_api(config)
    paths = Paths(config)
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(paths.ledger)
    try:
        issue = api.fetch_issue(issue_ref)
        observed = ledger.observe_issue(issue)
        delegated = issue.get("delegate_id") == api.app_user_id
        if skill in WRITE_SKILLS and not delegated:
            raise RuntimeError(f"{skill} is write-capable and this issue is not delegated to FarmBot; "
                               f"delegate it in Linear first (spec §4)")
        # fgui and feature start only on a card whose one Bot child names them, as a delegation does (spec §9.11,
        # D18). fix reads no label here, as before (Bot label group design §8).
        wanted = [child for child, name in BOT_SKILLS.items() if name == skill and name != "fix"]
        children = bot_children(issue.get("label_groups"))
        if wanted and children != wanted:
            carries = "、".join(f"{BOT_GROUP}/{child}" for child in children) or "no Bot label"
            raise RuntimeError(f"{skill} starts only on a card labelled {BOT_GROUP}/{wanted[0]}, as a delegation "
                               f"does; this card carries {carries}")
        session = session or f"local-{observed['id']}"
        ledger.ensure_session(session, observed["id"], delegated)
        target = None
        if skill not in ("work", "feature", "fgui", "scene"):
            # Initial Code/UI work gets no Client target, as for a delegation.
            trees = Worktrees(paths.repos, paths.worktrees, config.repos)
            target = {"repository": "Farm-Client", "requested_ref": "default",
                      "commit_sha": commit or trees.resolve_commit("Farm-Client"),
                      "server_environment": config.default_server_environment,
                      "selected_at": datetime.now(timezone.utc).isoformat()}
            ledger.set_session_target(session, target)
        # A write job exists only under the card's delegation, checked above, so losing it withdraws the job. A
        # conversation needs none: the operator started it, and it stays (withdrawn-work design P1). This is keyed on
        # the operator path, not on the session's delegation flag, which records the live delegate.
        item = ledger.create_work_item(issue_id=observed["id"], session_id=session, skill=skill, target=target,
                                       authority="delegation" if skill in WRITE_SKILLS else "operator")
        ledger.note(item["id"], "enqueue", f"operator enqueued {skill} for {observed['identifier']}")
        return item
    finally:
        ledger.close()


def serve(config_path=None, components=None):
    if components is None:
        config = load_config(config_path)
        with ControllerGuard(config):
            return _serve(build(config))
    # Injected components belong to isolated tests or an embedding caller.
    with ControllerGuard(components.config):
        return _serve(components)


def _serve(components):
    stop = threading.Event()
    # The beats' own event, set only once the loop threads have joined. Draining can take minutes, and beats that
    # stopped with the loops would age into 无响应 on the monitor during a clean shutdown.
    beat_stop = threading.Event()
    def workers():
        return {**components.launcher.running(), **(components.review.workers.launcher.running() if components.review else {})}
    heartbeat = Heartbeat(runtime=components.launcher.runtime.name, revision=source_revision(ROOT),
                          workers=workers,
                          capacity={"max_workers": components.config.max_concurrent,
                                    **({'review_workers': components.review.policy['workers']} if components.review else {}),
                                    "unity_slots": (components.config.unity_pool.get("editors")
                                                    or len(components.config.slots)),
                                    "server_slots": components.config.server_slots.get("count", 0),
                                    "enabled_skills": sorted(components.skills)})
    heartbeat_path = Paths(components.config).heartbeat
    components.scheduler.maintenance_epoch = heartbeat.started_at
    components.scheduler.maintenance_resources = getattr(components.pool, 'maintenance_blockers', None)
    version_sync = None
    def maintenance_busy():
        busy = (heartbeat.unsettled_loops(('pool', 'server_pool', 'resource_recovery', 'go_dependencies', 'worker_retention', 'review'))
                + (components.review.workers.blockers() if components.review else []))
        if version_sync is not None and version_sync.busy:
            busy.append('version_labels')
        return busy
    components.scheduler.maintenance_busy = maintenance_busy
    # The receiver's handler counts each /webhook outcome into it (receiver.make_server).
    components.server.heartbeat = heartbeat
    beat_lock = threading.Lock()
    beat_failing = False

    def beat():
        """Write one beat; True when it was written. Any failure is skipped, not only OSError: a writer failure
        never stops serving, and a skipped beat shows on the monitor as age, never as a stopped service. One
        heartbeat_error line marks the start of each run of failed writes, so a persistent failure is logged once."""
        nonlocal beat_failing
        with beat_lock:  # one beat at a time, so the log follows the order of the writes
            try:
                heartbeat.write(heartbeat_path)
            except Exception as exc:
                if not beat_failing:
                    beat_failing = True
                    try:
                        print(json.dumps({"event": "heartbeat_error", "error": type(exc).__name__}), flush=True)
                    except Exception:
                        pass  # the line is best effort too: it must never raise out of a beat
                return False
            beat_failing = False
            return True

    def beat_loop():
        while not beat_stop.wait(HEARTBEAT_INTERVAL):
            beat()

    def guarded(name, work, *, error_pause=1.0):
        """One loop iteration never kills its thread: log the kind of failure and back off.

        `work` returns how long to pause before the next iteration. The heartbeat times the work and not the
        pause, so a loop that sleeps between ticks is not reported as busy."""
        def loop():
            while not stop.is_set():
                heartbeat.loop_started(name)
                try:
                    pause = work()
                except Exception as exc:
                    heartbeat.loop_failed(name, exc)
                    print(json.dumps({"event": "loop_error", "loop": name, "error": type(exc).__name__}), flush=True)
                    pause = error_pause
                else:
                    heartbeat.loop_finished(name)
                stop.wait(pause)
        return loop

    def receive_once():
        return 0.0 if components.receiver.process_one() else 0.1

    def schedule_once():
        components.scheduler.tick()
        return 1.0

    def pool_once():
        # A slot switch is git checkout plus git lfs checkout on a multi-GB .git, an Editor refresh and a
        # compile wait — minutes. Scheduler.tick() holds its lock for its whole body and runs every second,
        # so running a switch inside one would freeze reaping, recovery and Stop.
        components.pool.tick()
        return 2.0

    threads = [threading.Thread(target=guarded("receive", receive_once), daemon=True),
               threading.Thread(target=guarded("schedule", schedule_once), daemon=True),
               threading.Thread(target=guarded("pool", pool_once), daemon=True)]
    if components.review is not None:
        def review_once():
            components.review.tick()
            return components.review.policy['poll_seconds']
        threads.append(threading.Thread(target=guarded('review', review_once), daemon=True))
    from .version_targets import LabelSync
    if components.config.version_targets.get('sync_labels', False):
        version_sync = LabelSync(components.worktrees, components.api, components.config.version_targets)
        def version_sync_once():
            version_sync.tick()
            return max(0.1, version_sync.next_at - version_sync.clock())
        threads.append(threading.Thread(target=guarded('version_labels', version_sync_once,
            error_pause=version_sync.policy['sync_seconds']), daemon=True))
    from .worker_retention import Controller as RetentionController
    retention = RetentionController(components.launcher, components.config.worker_retention)
    if retention.policy['enabled']:
        def retention_once():
            return .1 if retention.tick(stop.is_set) else 5.0
        threads.append(threading.Thread(target=guarded('worker_retention', retention_once), daemon=True))
    if components.lifecycle is not None:
        def reconcile_once():
            return 0.1 if components.lifecycle.tick()["checked"] else 1.0
        threads.append(threading.Thread(target=guarded("lifecycle", reconcile_once), daemon=True))
    if components.progress is not None:
        def progress_once():
            return 0.1 if components.progress.tick() else 5.0
        threads.append(threading.Thread(target=guarded("progress", progress_once), daemon=True))
    if components.recovery is not None:
        def recover_resources_once():
            components.recovery.tick()
            return 15.0
        threads.append(threading.Thread(target=guarded('resource_recovery', recover_resources_once), daemon=True))
    if components.go_dependencies is not None:
        components.go_dependencies.stop = stop.is_set
        def dependencies_once():
            from .maintenance import Maintenance
            if Maintenance(components.go_dependencies.ledger).blocks_admission():
                return 2.0
            return .1 if components.go_dependencies.tick() else 2.0
        threads.append(threading.Thread(target=guarded('go_dependencies', dependencies_once), daemon=True))
    if components.server_pool is not None:
        def server_pool_once():
            if not components.server_pool.ready:
                components.server_pool.ensure()
            components.server_pool.tick()
            return 2.0
        threads.append(threading.Thread(target=guarded('server_pool', server_pool_once), daemon=True))
    beat_thread = threading.Thread(target=beat_loop, daemon=True)
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
        # The first beat precedes ensure(): preparing slots can take minutes, and the monitor shows 正在启动.
        beat()
        beat_thread.start()
        try:
            components.pool.ensure()
        except SlotError as exc:
            # A host whose slot cannot be prepared must still answer Linear: the pool is simply empty,
            # every unity_slot request stays queued, and the operator gets the folder to inspect.
            print(json.dumps({"event": "slot_pool_unavailable", "error": str(exc)}), flush=True)
        if components.server_pool is not None:
            try:
                components.server_pool.ensure()
            except ServerSlotError as exc:
                print(json.dumps({"event": "server_pool_unavailable", "error": str(exc)}), flush=True)
        for thread in threads:
            thread.start()
        heartbeat.set_phase("serving")
        beat()
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
                    # Keep the root lock and DB connections until all controller
                    # mutations have stopped, even when an operation drains slowly.
                    thread.join()
            retention.close()
            if components.review is not None:
                components.review.close()
            # Batch executions have separate connections and outlive a pool tick.
            # Drain them while the root lock and serving heartbeat still belong to
            # this controller, after the launcher has fenced all further spawns.
            drain_batches = getattr(components.pool, "drain_batches", None)
            if drain_batches is not None:
                drain_batches()
            beat_stop.set()
            if beat_thread.ident is not None:
                beat_thread.join()
            heartbeat.set_phase("stopped")
            # The one beat no later beat repairs, so only it is retried: a Windows sharing violation would
            # otherwise leave the monitor on 无响应 instead of 已停止.
            if not beat():
                time.sleep(HEARTBEAT_RETRY)
                beat()
            components.receiver.close()
            components.ledger.close()
            components.pool.close()
            if components.lifecycle is not None:
                components.lifecycle.ledger.close()
            if components.progress is not None:
                components.progress.ledger.close()
            if components.recovery is not None:
                components.recovery.close()
            if components.server_pool is not None:
                components.server_pool.close()
            if components.go_dependencies is not None:
                components.go_dependencies.ledger.close()
        finally:
            beat_stop.set()  # also when a join or close raised, so the beat thread stops with serve
            if main_thread:
                signal.signal(signal.SIGTERM, previous_sigterm)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python3 -m agent.service")
    parser.add_argument("command", choices=["configure", "serve", "status", "seed-clones", "install-launchd",
                                            "enqueue", "slots", "doctor", "export-diagnostics", "recover-worker-cleanup", "monitor",
                                            "recover-server-slot", "maintenance-pause", "maintenance-status",
                                            "review-status", "review-pause", "review-resume", "review-incident",
                                            "maintenance-resume", "adopt-single-worker", "resume-ui-scope", "resume-ui-source", "resume-comparison", "resume-server-evidence"])
    parser.add_argument("--config")
    parser.add_argument("--from", dest="source_root", help="directory holding local checkouts to seed from")
    parser.add_argument("--issue", help="Linear issue id or identifier for enqueue or export-diagnostics")
    parser.add_argument("--except-issue", action="append", help="maintenance-pause: keep selected issues active while others drain")
    parser.add_argument("--only-issue", action='append', help="maintenance-resume only: allow this known issue; repeat for a selected group")
    parser.add_argument("--skill", default="work")
    parser.add_argument("--commit", help="pin this commit instead of resolving the default branch")
    parser.add_argument("--item", help="work item selected for recovery or export-diagnostics")
    parser.add_argument("--slot", help="held server slot to reconcile through verified Docker cleanup")
    parser.add_argument("--reason", help="operator explanation for maintenance or recovery")
    parser.add_argument('--review-run', help='review run selected for an attributed incident')
    parser.add_argument('--event-id', help='stable deduplication identity for an incident')
    parser.add_argument('--incident-kind', choices=('revert', 'reopen'))
    parser.add_argument("--evidence-manifest", help="host-reviewed retained server evidence manifest")
    parser.add_argument("--output", help="export-diagnostics: new ZIP outside the instance state root")
    parser.add_argument("--include-logs", action="store_true",
                        help="export-diagnostics: include redacted worker log tails for --issue or --item")
    args = parser.parse_args(argv)
    if args.except_issue is not None and args.command != 'maintenance-pause':
        parser.error('--except-issue requires maintenance-pause')
    if (args.output is not None or args.include_logs) and args.command != 'export-diagnostics':
        parser.error('--output and --include-logs require export-diagnostics')
    if args.only_issue is not None and args.command != 'maintenance-resume':
        parser.error('--only-issue requires maintenance-resume')
    if args.command.startswith('review-'):
        from .review_operator import control
        print(json.dumps(control(load_config(args.config, secure_permissions=args.command != 'review-status'),
            args.command, reason=args.reason, run=args.review_run, event=args.event_id, kind=args.incident_kind),
            ensure_ascii=False, indent=2))
        return 0
    if args.command == 'export-diagnostics':
        if not args.config or not args.output:
            parser.error('export-diagnostics requires --config and --output')
        from .diagnostics import ExportError, run
        try:
            result = run(args.config, args.output, issue=args.issue, item=args.item, include_logs=args.include_logs)
        except ExportError as exc:
            print(json.dumps({'status': 'error', 'error': str(exc)}))
            return 1
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result['status'] == 'partial' else 0
    if args.command == 'adopt-single-worker':
        if not args.item or not args.reason:
            parser.error('adopt-single-worker requires --item and --reason')
        from .execution import adopt
        config = load_config(args.config)
        check_ownership(config, require_initialized=True)
        paths = Paths(config)
        launcher = Launcher(paths.runs, RUNTIMES[config.runtime], config.host)
        ledger = Ledger(paths.ledger)
        try:
            result = adopt(ledger, args.item, launcher, args.reason)
            print(json.dumps({k: result[k] for k in ('id', 'state', 'stage', 'execution_contract')}, ensure_ascii=False))
        finally:
            ledger.close()
        return 0
    if args.command == 'resume-server-evidence':
        if not args.item or not args.evidence_manifest or not args.reason:
            parser.error('resume-server-evidence requires --item, --evidence-manifest and --reason')
        from .server_recovery import resume_server
        print(json.dumps(resume_server(load_config(args.config), args.item, args.evidence_manifest, args.reason), indent=2))
        return 0
    if args.command == 'resume-comparison':
        if not args.item or not args.commit or not args.reason:
            parser.error('resume-comparison requires --item, --commit and --reason')
        from .verification_recovery import resume_comparison
        print(json.dumps(resume_comparison(load_config(args.config), args.item, args.commit, args.reason), indent=2))
        return 0
    if args.command in ('resume-ui-scope', 'resume-ui-source'):
        if not args.item or not args.reason:
            parser.error(args.command + ' requires --item and --reason')
        from .ui_scope_recovery import resume_scope
        print(json.dumps(resume_scope(load_config(args.config), args.item, args.reason,
                                     source_only=args.command == 'resume-ui-source'), indent=2))
        return 0
    if args.command.startswith('maintenance-'):
        from .maintenance import Maintenance, read_status
        config = load_config(args.config, secure_permissions=args.command != 'maintenance-status')
        check_ownership(config, require_initialized=True)
        if args.command == 'maintenance-pause' and not args.reason:
            parser.error('maintenance-pause requires --reason')
        if args.command != 'maintenance-status':
            ledger = Ledger(Paths(config).ledger)
            try:
                if args.command == 'maintenance-pause':
                    Maintenance(ledger).pause(args.reason, except_issues=args.except_issue)
                else:
                    selected = args.only_issue
                    Maintenance(ledger).resume(only_issue=selected[0] if selected and len(selected) == 1 else selected)
            finally:
                ledger.close()
        print(json.dumps(read_status(config), ensure_ascii=False, indent=2))
        return 0
    if args.command == "recover-server-slot":
        if not args.slot or not args.reason:
            parser.error("recover-server-slot requires --slot and --reason")
        config = load_config(args.config)
        if not config.server_slots:
            raise ServerSlotError("no configured server slot pool")
        with ControllerGuard(config):
            paths = Paths(config)
            launcher = Launcher(paths.runs, RUNTIMES[config.runtime], config.host)
            current = Ledger(paths.ledger)
            def quiescent(item_id):
                try:
                    launcher.assert_quiescent(item_id, current.last_worker_pid(item_id))
                except Exception:
                    return False
                return True
            pool = ServerSlotPool(lambda: Ledger(paths.ledger), config.server_slots, host=config.host,
                                  root=paths.server_slots, state_dir=launcher.state_dir, quiescent=quiescent,
                                  admission=ResourceAdmission(config.resource_admission))
            try:
                print(json.dumps(pool.recover(args.slot, args.reason)))
            finally:
                pool.close()
                current.close()
        return 0
    if args.command == "monitor":
        from .monitor import run
        return run(args.config)
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
        for label in written:
            print(f"  launchctl bootstrap gui/$(id -u) {target}/{label}.plist")
        print("\nStop and remove with `launchctl bootout gui/$(id -u)/<label>`.")
        return 0
    if args.command == "seed-clones":
        print(json.dumps(seed_clones(load_config(args.config), args.source_root), ensure_ascii=False, indent=2))
        return 0
    if args.command == "status":
        config = load_config(args.config)
        check_ownership(config, require_initialized=True)
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
        check_ownership(config, require_initialized=True)
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
