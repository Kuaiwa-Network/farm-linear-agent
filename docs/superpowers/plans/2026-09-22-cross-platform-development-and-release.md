# FarmBot cross-platform development and release plan

**Status:** On 2026-09-23 the separate test workspace, manual issue snapshots and sandbox
repositories were abandoned. Live development now uses the TestBot app inside the
production workspace, on real issues and the real repositories; see the
[development workflow](../../development-workflow.md). Implementation includes explicit
profile identity/ownership, controller locks, configurable issue-key publishing, instance
launchd labels and Mac/Windows CI. Remaining rollout phases are not complete. No production
deployment has been performed.

**Goal:** Develop and test FarmBot on macOS while a stable production revision runs on Windows, then promote a verified revision without mixing bot identities, credentials, work, or state.

**Architecture:** Keep one Python codebase. Run independent installations for local development, Windows acceptance, and production. Share versioned source and test fixtures; give each installation its own configuration, Linear identity, credentials, ledger, clones, worktrees, memory, logs, Unity slots, and tunnel.

**Tech stack:** Existing Python standard-library service and unittest suite; Git/Git LFS; GitHub Actions; existing worker CLI and Unity/MCP integration; launchd on macOS and an interactive-user scheduled task on Windows.

**Design basis:** The user's request for Mac development and Windows production, the current operating contract, and inspection of commit `6eed2cb`. The user subsequently approved the development workflow and implementation/provisioning on 2026-09-22; unchecked items remain incomplete. The actual production revision and Windows installation were not inspected.

## Recommendation and alternatives

Use a distinct Linear app named **TestBot** for live development. Keep **FarmBot** for production. Linear supports app-specific agent identities and agent-session webhooks; a distinct name also makes accidental delegation more visible. See [Linear's agent setup documentation](https://linear.app/developers/agents).

Three options:

| Option | Benefit | Limitation | Recommendation |
|---|---|---|---|
| Offline fixtures only | Fast, repeatable, no external effects | Cannot verify actual Linear sessions, CLI authentication, or Unity | Use for every change |
| Separate test app and isolated installations | Exercises real integrations while production continues | Requires separate credentials, fixtures and Windows acceptance | Use for live testing |
| Reuse production identity with another config or webhook | Less setup | No independent authority boundary; wrong credentials or endpoint can affect production | Do not use as the development workflow |

A branch separates source history, not credentials or operational state. Changing the bot's display name alone does not establish isolation.

## Environment layout

| Environment | Machine | Linear identity | State | Execution |
|---|---|---|---|---|
| Offline development | Mac and CI | Stub | Temporary directory | Fake worker, local Git remotes, fake Unity |
| Live development | Mac | TestBot | Dedicated absolute development root | Real worker; dedicated Mac Unity slot |
| Windows acceptance | Separate Windows host or suitable VM | FarmBot Test when concurrent; otherwise TestBot with only one active receiver | Dedicated acceptance root | Exact candidate revision, real worker and Unity |
| Production | Windows | FarmBot | Stable production root outside release directories | Pinned released revision |

Two real app identities are enough if Mac and Windows live testing alternate. Use a third test app if both need to receive agent events simultaneously. Each live app has its own client ID, client secret, webhook signing secret, and endpoint. Preserve the production endpoint throughout development.

The current choice (2026-09-23) is TestBot in the production Kuaiwa AI workspace, working on real issues the operator mentions or delegates and publishing to the real `Farm-Client`, `farm-hive`, `farmgui`, `common`, and `Farm-Contract` repositories with the host's ambient Git/GitHub login. It replaced a separate test workspace with manual issue snapshots and private sandbox repositories: snapshots lost evolving issue context, sandbox copies drifted from the code an issue described, and results had to be re-tested in the real workspace anyway. See the [development workflow](../../development-workflow.md).

The two bots are separated by app identity, pinned IDs and state root, not by workspace or repository. There is no team/project admission filter yet (Phase 2), and the real repositories' default branches are unprotected on the current GitHub plan, so FarmBot's own publication verification is the only guard against a wrong push. The launcher inherits the host environment and Git/GitHub authentication; its isolated worker home does not isolate publishing credentials.

Do not share a ledger, Git clone, Unity folder, MCP port, or memory directory between environments. Do not move Mac `.local` state to Windows: it contains host-specific paths, process evidence, worktrees and caches. Seed Windows clones and Unity imports separately. `default_server_environment` describes the game test target; it is not a FarmBot environment switch or an enforcement boundary.

If only one Windows production machine is available, hosted Windows CI can cover the offline suite. Schedule real desktop acceptance when dedicated resources are available. A second folder on the production desktop is weaker isolation: Unity process scanning currently considers other editors on the host, and CPU, disk, user session and credentials can still interfere. A VM requires its own successful Unity graphics/licensing rehearsal.

## What the code already supports

- `agent/config.py`: `--config`/`FARMBOT_CONFIG` selection and configurable `local_root`, port, host, repositories, runtime and Unity slots. `Paths` derives ledger, runs, clones, worktrees and editors from the root.
- `agent/config.py:StubLinear`, `agent/launcher.py:RUNTIMES['fake']`, `tests/fake_cli.py`: useful foundations for offline testing. Fake worker and stub Linear are independent choices; selecting just one does not disable all external effects.
- `tests/test_end_to_end.py`: temporary local repositories, signed webhook fixtures, fake workers and fake Unity/MCP exercise the real controller wiring.
- `agent/receiver.py`: signature and timestamp checks plus client/app-user/organization identity checks on agent-session events. Issue webhooks only request reconciliation for already tracked issues.
- `agent/launcher.py`, `agent/windows_job.py`, `agent/windows_worker_gate.py`: POSIX process handling and Windows Job Object containment already exist.
- `agent/unity.py`: platform-specific Editor discovery and explicit executable overrides already exist.
- `tests/test_windows_workers.py`: native Windows worker-tree tests already exist. They are skipped on macOS.

## Gaps that affect this workflow

1. **Config-file propagation is now implemented.** The loader records the selected absolute source path, the scheduler passes it to initial/resumed workers, and launchd installation embeds it regardless of selection method. Regression tests cover conflicting ambient profiles. This pins the file path, not its contents or environment ownership; restart a settled service after config edits.
2. **Explicit app identity is implemented.** Live profiles require pinned app-user/workspace IDs and a configurable expected display name. Existing configs remain legacy until explicitly migrated.
3. **State ownership and controller guards are implemented.** Explicit profiles require a fresh absolute state root, bind it to nonsecret identity and acquire an OS-backed controller lock. CLI/maintenance checks run before ledger construction. Native Windows evidence is still required.
4. **Configured test issue-key publishing is implemented.** Scheduler and verifier share `issue_prefix` while retaining repository/private/protected branch checks. This is not team/project admission; the current live scope is explicit delegation or @mention of the instance's own app.
5. **Installer labels now distinguish explicit instances.** launchd labels include environment and instance ID; legacy names remain stable. A Windows installer/supervisor is not yet implemented.
6. **Windows readiness is incomplete.** `doctor.probe_process()` returns `platform_not_supported` on Windows. Unity's command-line parser stops project paths at whitespace. A pure parser probe reproduced `C:\Farm Bot\editors\slot-1` being read as `C:\Farm`. Worker instructions also assume `python3`; use the actual host interpreter. Audit Windows CLI wrappers, encoding, Git path handling and shutdown behavior.
7. **CI added; controlled release operation remains open.** Mac/Windows CI uses Python 3.13 and uploads test evidence. There is still no drain command or release installer. Opening `Ledger` performs migrations; rollback cannot be treated as simply checking out an older commit. `/health` currently proves the HTTP handler responds, not that scheduling, reconciliation, the tunnel or Unity work.

## Constraints

- Preserve current delegation, publication, claim and process-ownership rules.
- Keep development operational while production stays on its release revision.
- Keep secrets and mutable runtime data outside Git and release artifacts.
- Live development actions target only issues the operator mentions or delegates to TestBot, and only the configured repositories.
- Prefer native Windows testing for native Windows behavior. Mac mocks are supporting tests, not Windows evidence.
- Keep the current controller/worker/slot architecture. A distributed scheduler or container migration is unnecessary for this goal.

## Phased implementation

### Phase 1 — Establish configuration and identity isolation

**Files:** `agent/config.py`, `agent/service.py`, `agent/scheduler.py`, `agent/launcher.py`, `agent/linear_api.py`, `agent/__main__.py`; new `tests/test_config.py`, plus existing service, launcher and Linear API tests.

- [x] Add explicit environment and instance identifiers, configurable display name, and expected app-user/organization IDs. Preserve existing production behavior in legacy compatibility mode; production state migration remains a separately planned operation.
- [x] Resolve one absolute config path when loading the file, carry it into the scheduler, and explicitly set the worker's `FARMBOT_CONFIG`; caller environment must not override the selected service config. Preserve that path in launchd installation as well.
- [x] Validate absolute state paths for live profiles. Record a nonsecret identity marker in the state root and refuse a conflicting profile/identity. Acquire an OS-backed exclusive controller lock for that root and hold it for the process lifetime; do not use stale PID files as the sole lock.
- [x] Make fake/stub activation an explicit offline mode. Refuse production startup with fake runtime or stub-related environment variables.
- [ ] Test with two configs and a deliberately wrong ambient `FARMBOT_CONFIG`: the spawned CLI must fetch, post and verify using the selected profile; mismatched identity/root and a second controller must fail before launching work or making mutations.
- [ ] Document ignored local config placement, absolute paths and credential setup. Until this phase lands, existing offline tests are the supported development entry point.

**Acceptance:** Two installations cannot accidentally share state, and the controller and every worker use the same selected identity and repositories.

### Phase 2 — Make live test scope explicit

**Files:** `agent/config.py`, `agent/receiver.py`, `agent/service.py`, `agent/lifecycle.py`, `agent/linear_api.py`, `agent/scheduler.py`, `agent/publication.py`, `agent/__main__.py`; receiver, lifecycle, publication and CLI tests.

- [ ] Configure allowed team/project/issue scope for live test profiles. Fetch the fields needed to enforce it; reject out-of-scope work before acknowledgments, label changes, repository setup or worker launches. Apply equivalent checks to `enqueue`, resumed work and worker-side external mutations, not only webhook intake.
- [x] Replace the hard-coded `FARM` publication assumption with a validated issue-key policy tied to the permitted team. Share branch validation between scheduler and publication verification and retain exact configured destination/protected-branch checks.
- [ ] Provision the TestBot app in the production workspace as a separate operational step. Enable Agent session events and Issue webhooks, using the test app's signing secret and endpoint. Follow [Linear's client-credentials setup](https://linear.app/developers/oauth-2-0-authentication).
- [ ] Test validly signed events for the wrong app and wrong organization, out-of-scope mentions/delegations, direct enqueue bypass attempts, test-team publishing, and a production push URL introduced through Git configuration. Assert no unintended external mutation.

**Acceptance:** Delegate a chosen real Bug to TestBot from Linear's UI, receive activities, ask/reply, stop and resume, then create a draft PR on that issue's `farmbot/<key>` branch. Production FarmBot ignores the session, and issues outside the configured scope are refused.

### Phase 3 — Add continuous Mac and Windows checks

**Files:** new `.github/workflows/tests.yml`, new `scripts/test-offline.py`, existing `tests/` fixtures, `README.md`.

- [x] Declare Python 3.13 as the CI baseline and install it explicitly on both CI operating systems; record the exact patch used in release evidence. This is a proposed support baseline, not a claim that older Python is unsupported by all current code.
- [x] Run unittest discovery on macOS and Windows for each PR. Install/check Git and Git LFS where fixtures require them. Use no production credentials or live Unity/model calls. GitHub supports an OS matrix and explicit Python setup; see [its Python CI guide](https://docs.github.com/en/actions/tutorials/build-and-test-code/python).
- [ ] Make the offline test entry point clear inherited FarmBot selectors and production Git/GitHub credentials before discovery; fixtures explicitly provide their own config, remotes and API clients. Keep test network use limited to loopback and fail on unexpected external access.
- [x] Preserve platform-specific skip reasons. Require Windows Job Object tests to execute on Windows rather than accepting an entirely skipped native suite. Report totals, skips and failures as CI artifacts.
- [ ] Pin the candidate's Python, worker CLI, Git/Git LFS, Unity version/build modules and MCP version in a release manifest. Authenticate real worker CLIs only in isolated acceptance environments.

**Acceptance:** The same commit passes both OS jobs. Baseline failures are resolved or explicitly classified before making these required checks.

### Phase 4 — Close platform gaps and add Windows installation

**Files:** `agent/unity.py`, `agent/doctor.py`, `agent/launcher.py`, `agent/slots.py`, `agent/dispatch.py`, `agent/deploy.py`, `agent/service.py`, `skills/chat/SKILL.md`, `skills/fix/SKILL.md`, `references/memory.md`; new Windows deployment adapter and corresponding tests.

- [ ] Fix quoted project-path parsing and cover spaces, Unicode, drive letters and slash variants. Preserve conservative behavior when process ownership cannot be established.
- [ ] Include the absolute `sys.executable` in worker dispatch data and update command instructions to use it with the host shell's quoting rules. Test paths containing spaces. Verify actual installed worker executables/wrappers on Windows.
- [ ] Add read-only Windows process diagnostics using existing native liveness/Job Object evidence plus ownership checks; keep uncertainty explicit. Extend readiness reporting to include revision, environment, controller loops and relevant dependencies without exposing credentials.
- [ ] Namespace launchd labels by instance and embed the absolute config/interpreter path. Add a Windows scheduled-task installer with explicit working directory, config, interpreter, logging, restart policy and instance name. Do not replace an existing production installation implicitly.
- [ ] Run interactive Unity under a dedicated logged-in Windows user. An interactive-token scheduled task is the initial recommendation; it depends on that user's session. Windows services cannot be assumed to access the interactive desktop. See [Microsoft's task security contexts](https://learn.microsoft.com/en-us/windows/win32/taskschd/security-contexts-for-running-tasks) and [interactive services guidance](https://learn.microsoft.com/en-us/windows/win32/services/interactive-services).
- [ ] Rehearse real worker exit, timeout, Stop, receiver crash and restart; verify descendants exit and unrelated processes survive. Rehearse Unity batch and interactive runs, slot release and stale MCP recovery. Test logon/reboot behavior and document the logged-in-session requirement.

**Acceptance:** A Windows acceptance installation runs the exact candidate with real tools, produces nonempty Unity test evidence, survives the lifecycle scenarios, and does not touch production resources. A successful Mac run alone cannot satisfy this gate.

### Phase 5 — Add controlled promotion and recovery

**Files:** `agent/service.py`, `agent/scheduler.py`, `agent/ledger.py`, `agent/receiver.py`, deployment adapters; new release script and migration/drain tests; `docs/operating-contract.md`.

- [ ] Add a persistent drain state with an operator command. Continue receiving/persisting events and servicing Stop; prevent new job claims/starts. Let already-running attempts finish their bounded execution. Park continuations that require another attempt, and report active processes/reservations/cleanup blockers.
- [ ] Define drain readiness as no running worker/batch operation or unsettled reservation, no in-flight controller mutation, and completed required cleanup. Waiting-for-input jobs and queued events persist; they do not have to be cancelled. Reuse existing slot quiescence/parking rules. A timeout reports blockers instead of force-killing work.
- [ ] Keep releases in separate immutable revision directories and production config/state at stable absolute locations. Stage the candidate in a new directory, then stop the drained old controller, confirm exclusive ownership is released, and switch the supervisor to the candidate.
- [ ] Back up the settled ledger with SQLite's backup mechanism plus relevant configuration and recovery refs/worktree state. Do not copy only a live SQLite main file and ignore its WAL.
- [ ] Record schema compatibility. Test migration against a disposable database fixture and rollback before promotion. Prefer backward-compatible migrations; refuse code-only rollback when schemas are incompatible.
- [ ] Restart into a held/drained state, verify identity, revision, ledger, loop health and slot readiness, then resume launches. Make the production cutover an explicit operator action using the already-reviewed candidate revision.
- [ ] For a failed pre-resume cutover, restore the previous compatible release. If rollback needs a data restore, require reconciliation of queued events, external comments/PRs and filesystem state. Never silently rewind a database after new external effects have occurred.

**Acceptance:** Rehearsal preserves queued work and parked jobs across upgrade, stops incompatible releases before mutation, and proves a rollback path. Development work continues independently during this process.

## Daily workflow after implementation

1. Create a feature branch/worktree on the Mac and run focused offline tests while changing code.
2. Run the full offline suite, then use TestBot for a deliberate integration scenario on a chosen real issue.
3. Open a PR and require both Mac and Windows CI.
4. Run the candidate revision in Windows acceptance when changing process handling, deployment, tool integration or Unity behavior; establish an initial full acceptance baseline before the first production promotion.
5. Record the accepted revision/tool versions; deploy that revision through drain, backup, switch, verify and resume.
6. Continue Mac work on the next change while Windows production stays pinned to its accepted revision.

The existing offline command is available now:

```sh
python3 -m unittest discover -s tests -v
```

Use the matching Python executable on Windows. The proposed profiles, guardrails, drain command and Windows installer above do not exist yet.

## Review focus

- A correctly selected receiver with a wrongly inherited worker config must not contact another environment.
- A test app or test issue key must not weaken production delegation or publication checks.
- A Windows path containing spaces or Chinese characters must not cause incorrect process ownership decisions.
- A desktop acceptance task must not rely on CI's headless environment proving Unity session behavior.
- A migration rollback must not replay old external actions or discard newly accepted work unnoticed.

## Verification record

- Inspected source at `6eed2cb`, configuration and deployment code, worker/Unity process management, CLI configuration consumers, publication checks and existing tests.
- Confirmed the quoted-path truncation with a pure Windows command-line fixture on the Mac; no Unity process was started.
- The initial sandboxed suite ran 505 tests with 5 failures, 33 errors and 10 skips. Localhost binding and process inspection were restricted; those results were not a trustworthy application baseline.
- The unchanged suite rerun outside those sandbox restrictions completed successfully on macOS with Python 3.13.14: **505 tests, 10 skipped, 0 failures/errors**, in 100.660 seconds. The skipped checks include native Windows Job Object tests. This is not Windows execution evidence. Full output: `/tmp/farmbot-dev-analysis-tests-unsandboxed.log`.
- No production host, production config, live Linear event, repository publishing or service restart was involved in this analysis.
- PR preparation: moved the documentation onto main at `5463f28` and rechecked the configuration propagation, fixed identity, issue-key policy, service labels and Windows diagnostic gaps. The suite result above belongs to the original analyzed revision `6eed2cb`; it is not a new test run on the PR base. Documentation links and formatting were checked on the PR branch.
