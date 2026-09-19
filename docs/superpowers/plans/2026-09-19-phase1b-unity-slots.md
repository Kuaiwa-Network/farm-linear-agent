# FarmBot Phase 1b: Unity slots, the slot switch and two-phase acquisition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two delegated items that both need Unity serialize on one slot through the two-phase flow, one in batch mode and one interactive, with the slot switched to each item's pinned commit and parked back on `origin/main` afterwards — which is Phase 1 done-criterion 3 (spec §17).

**Architecture:** Four new modules inside the existing `agent/` package. `agent/slots.py` owns the slot pool: it creates each slot as a long-lived detached worktree under `.local/editors/slot-<n>` with its LFS binaries materialized, grants reservations FIFO, performs the slot switch, starts the Editor for an interactive run and closes it gracefully before a batch one, parks the slot back on main and settles held slots after a quiescence probe. `agent/unity.py` holds every host-specific fact about the Editor — where the binary is, what a batch test run's argument vector looks like, and how to read the results file it writes. That vector is never handed to a worker: a Unity batch run inside the Codex `workspace-write` seatbelt hangs on a denied Mach lookup (Task 0's sandbox addendum), so the pool composes the argv from the slot's own configuration and `agent/launcher.py` runs it **outside** the worker's sandbox, handing the worker the results file and the exit code as evidence. `agent/unity_mcp.py` plus `agent/identity.py` are the ported FarmQA identity probe: a loopback JSON-RPC client and the check set that proves the Editor really loaded the pinned commit. The ledger gains `slots`, `reservations` and `identity_observations`; the scheduler gains nothing but a reservation-aware `launch` and a Stop that cancels reservations first and then kills any batch Editor the launcher is running for the item; the pool runs on its own thread beside the receiver and scheduler threads, because a slot switch takes minutes and `tick()` must stay under a second. Devices, FairyGUI, Play Mode session identity, QA scenarios and the Windows host are later plans.

**Tech Stack:** Python 3.11+ standard library only, `unittest`, git and `git-lfs`, Unity 2022.3.62f3 in batch mode, MCP for Unity 10.2.0 over loopback HTTP JSON-RPC.

**Spec:** `docs/superpowers/specs/2026-09-17-farm-linear-agent-design.md` (§5, §6, §7, §8, §10, §17 Phase 1, §18). Port sources named by the spec: `FarmTestAgent/tools/farmqa_controller.py`, `farmqa_identity.py`, `farmqa_unity_identity.py`, and the probe `FarmTestAgent/tests/probes/editor-readiness.cs.txt`. Executors read the spec and the Task 0 spike record alongside this plan.

## Scope

In: everything Phase 1 done-criterion 3 names — one Unity slot on the Mac, the slot switch, the identity probe, reservations with FIFO queue semantics and one active owner per resource, and two-phase acquisition for the `fix` skill, in both batch and interactive mode.

Out, and why:

- **The Windows host.** Spec §7 says "Slot 1 is FarmQA's existing isolated client copy on the Windows host". That host does not exist yet, so slot 1 is built fresh on the Mac and §7 is amended in Task 3. Every Mac-versus-Windows difference this plan meets becomes a configuration key or a discovered value, never a branch in logic, and the Windows plan inherits the checklist at the end of this document.
- **Play Mode, logged-in session identity and the per-slot test account.** `FarmTestAgent/tests/probes/session-identity.cs.txt` reflects private fields of `Farm.Core.NetWork.Net`, `PlayerModel` and `Nova.NetManager` by exact name on a Windows client at one commit, and degrades silently to `unsupported_schema` when a field moves. Criterion 3 needs Edit Mode, the pinned commit and the loaded HotUpdate module, nothing more. The account is still owed by the operator (see below) because Phase 2 cannot start without it.
- **QA scenarios, devices, FairyGUI, `fgui_editor` and `android_device` reservations.** Phases 2, 4 and 6. The reservation table this plan adds is keyed by `kind` and `resource` so those need schema changes, not a redesign.
- **`hosts`, `build_artifacts` and `baselines` (spec §10).** None is needed for criterion 3. `work_items.host` already exists and `slots.host` is added here, which is what §17's "host columns" asks for; baselines belong to the verify stage in Phase 3.
- **Batch results interpretation.** This plan runs a batch Editor, captures its results file and exit code, and records them. Comparing a failure set against a known-red baseline on main is spec §8's verify stage and belongs to Phase 3.
- **Naming a branch or PR as the pin, and correcting a pin after the fact.** Spec §6 says that when the request names a branch or PR that is the ref, and that the pin is echoed "so a human can correct it before work starts". This plan resolves only the client's remote default-branch head, `requested_ref` is always the literal `"default"`, and there is no `retarget` verb — a `prompted` correction cannot move an accepted item. Both belong to the phase that gives `fix` its PR handling (Phase 3). Criterion 3 needs neither, and the omission is recorded here so it is a decision rather than a gap.
- **Player builds in a slot.** A slot is for Edit Mode and PlayMode fixtures only. Measured on this Mac, the human's `Library/` is 12 GB, of which `Bee` is 7.6 GB and `BuildCache` 926 MB — both player-build artefacts. Task 0 Step 3 measured the import-only subset at **5.1 GB** (its `Bee` is 190 MB and it has no `BuildCache` at all), and that is the number the slot budget assumes. If a slot ever runs a player build the budget is wrong by more than a factor of two; Task 8 writes that rule into the operating contract.

## Provided by the operator, not by this plan

Five things this plan cannot create for itself. Task 0 ran on this Mac on 2026-09-19 and changed what this section is: only item 1 is still a blocker an operator must clear, and items 2 to 5 are now **measurements** — recorded here because the numbers in Tasks 3, 4 and 7 are read off them and because the Windows host re-measures each one. Item 5 is neither: it is a change to this repository that the measurement made necessary, and it is scheduled as work in Task 7. `docs/superpowers/spikes/2026-09-19-unity-slot-spike.md` is the source of truth for every number below.

1. **A dedicated test account on 公共测试服 for slot 1.** Spec §7 binds one account per slot because two logins on one account kick each other and scenario runs mutate account state. Nothing in this plan logs in — criterion 3 is Edit Mode only — so the `slots.account` column is written when the operator supplies the account and stays NULL until then. Phase 2's `qa` skill is blocked without it, so it is asked for now. **Still owed.**
2. **Disk and wall clock for the first `Library/` — measured, not estimated.** Task 0 Steps 1 to 3. The slot's source tree costs `git worktree add --detach` **1.6 s** (110 MB with smudge off), `git lfs fetch origin HEAD` **36.2 s** for **918 MB** over the LAN, and `git lfs checkout` **5.3 s**, which brings the tree to 979 MB: about **45 s** in all. The bare clone's LFS store grows 176 KB → 880 MB once and is shared by every later slot, so only the first one pays the download. `git lfs ls-files` counts **3,778** files at `origin/main`; the 3,619 an earlier draft quoted was measured at an older commit. The first `Library/` import at a fresh path is **170 s** and **5.1 GB** (`Artifacts` 2.9 GB, `PackageCache` 1.8 GB, `Bee` 190 MB, `BurstCache` 107 MB), with **zero** compile errors, and `HotUpdate.dll` is produced without `HybridCLRData` being provisioned first. **Budget 6 GB and about three and a half minutes per slot.** Spec §7's "tens of minutes" and this plan's earlier 15-minute budget do not hold on this M4. Free space is 393 GiB, so disk is not the constraint — and neither is memory; see Global Constraints for why Phase 1 still runs exactly one slot.
3. **`git.kuaiwa.com` access — exercised on 2026-09-19 and NOT a blocker on this Mac.** An earlier
draft of this plan called this a blocker from reading config files. Task 0 Step 2 disproved it with a real
fetch: **918 MB in 36.2 s**, run under `GIT_TERMINAL_PROMPT=0` with stdin closed, exit 0, no prompt, the
credential answered non-interactively by `osxkeychain`, and `Assets/DOTween/DOTween.dll` went from `vers`
to `MZ`. The host resolves to `192.168.1.201` on the LAN. So a slot on this Mac materializes its binaries
with no operator action. Three caveats remain, and they are checks rather than blockers: the address is
on the LAN, so any session off that network — or one whose traffic is captured by a VPN or by Clash in
TUN mode — still gets pointer files; the keychain must be unlocked, which it is for a `gui/501`
LaunchAgent while the user is logged in, but the spike's launchd step ran a *test suite*, not a fetch, so
an LFS fetch under the loaded agent is still unconfirmed; and `Packages/packages-lock.json` names three
git UPM dependencies on the same host
(`com.code-philosophy.hybridclr`, `com.psygames.unitywebsocket`, `com.unity.assetbundlebrowser`), so the
first import needs that LAN too. Seeding FarmBot's bare clone by copying the human's LFS objects is **not**
a faster alternative and is **not** a fallback: it was deliberately never measured, because it moves
~3.6 GB of LFS *history* to obtain the same 918 MB of HEAD objects that a 36-second network fetch obtains
directly, and the fetch also populates the shared bare-clone store that every later slot reads. It survives
only as the thing to measure if a future host is off this LAN. If it is ever used, the destination is
`<bare>/lfs/objects`, i.e. `.local/repos/Farm-Client.git/lfs/objects`, not `.git/lfs/objects`.
4. **Unity licensing on this one Mac — answered, except for contention.** `/Library/Application Support/Unity/Unity_lic.ulf` carries a masked serial (`F4-SCPA-...`) and `LicenseVersion 6.x`, and the addendum's run logged `Pro License: NO` beside it — so it is a serial-bearing licence with no Pro entitlement, neither the Personal seat one draft assumed nor the paid serial a later one asserted. What matters for this plan is that it resolves everywhere it was tried. Task 0 Steps 3 and 6 resolved it in **both** directions: a foreground batch Editor logged `Successfully resolved entitlement details` six times and ended `Exiting batchmode successfully now!`, and a one-shot LaunchAgent in the `gui/501` domain logged `entitlements=7`, exited 2 exactly as the foreground run did, and wrote a results file within four bytes of the foreground one. **No second seat is needed for the Mac and no login session beyond the one the LaunchAgent already has.** One half of the question is genuinely still open: contention with the operator's own Editor on `/Users/elendil/WorkSpaces/Farm/Farm-Client` was never exercised, because none was open during the spike. Until it is, `SlotPool.switch` keeps Task 4's `another_editor_running` preflight and refuses to start a second Editor with a sentence a human can act on; re-test before that preflight is relaxed.
5. **The service's scheduling band — answered, and a deployment change this plan now owes.** `~/Library/LaunchAgents/com.kuaiwa.farmbot.serve.plist`, installed by Plan 1c and generated by `agent/deploy.py`, sets `ProcessType` to `Background`, which on macOS puts the job and everything it spawns into the background band: lowered CPU priority, timer coalescing and throttled disk I/O. Task 0 Step 6 measured the cost on the *same* EditMode suite: **105 s under launchd against 14 s foreground with warm caches** — 7.5x, and the launchd run was the *later*, warmer one, so this is the scheduling band and not cache state. That is not an acceptable tax on a 17-second verification, and it compounds across a switch, an import and a test run. **`ProcessType` must become `Standard` before any Unity work runs under the service.** This is not an operator decision and not a question: it is a one-line change to the generator plus a test that pins it, and it is **scheduled as work in Task 7**, which is the task that arranges everything else a Unity child inherits from the service. Item 2 of the operational checklist tracks the half of Step 6 that is still genuinely open (contention), not this.

**No longer provided by the operator.** An earlier draft listed the MCP for Unity server process as a prerequisite — the operator confirming it starts, and enabling `execute_code` through the MCP window because `Editor/Tools/ExecuteCode.cs` sets `AutoRegister = false`. Task 0 Step 5 voided both. **Opening the Editor starts the server itself**, as a `uvx` child of the Editor:

```
uvx --offline --from mcpforunityserver==10.2.0 mcp-for-unity \
  --transport http --http-url http://127.0.0.1:8080 --project-scoped-tools \
  --pidfile <slot>/Library/MCPForUnity/RunState/mcp_http_8080.pid \
  --unity-instance-token <per-instance token>
```

It registered 35 tools from the plugin and exposes 48 over MCP, **`execute_code` among them and already enabled** (it requires an `action: "execute"` argument alongside `code`). Note `--project-scoped-tools` and the **per-slot** pidfile under the slot's own `Library/`, which is what Task 4's `close_editor` reads to reap the server. No operator action is required here, and nothing in this plan waits for one.

## Global Constraints

- Python 3.11+, standard library only in `agent/`; no third-party imports (spec §13).
- Tests use `unittest`, real temporary SQLite files and real subprocesses; the whole suite must stay green **and warning-free** under `python3 -W error -m unittest discover -s tests -v` from the repo root. It stands at 139 tests before this plan.
- `unittest`'s `-k` is a plain substring pattern with **no boolean operators** — unlike pytest's. `-k "a or b"` matches nothing, prints `NO TESTS RAN` and exits 5, which would silently satisfy every "expected: FAIL" gate in this plan. Repeated `-k` flags are ORed, so every selection below is written `-k one -k two`. Before writing an expected count into a step, run the selection and count what it matches: `-k slot` already matches one pre-existing test today.
- Every server a test starts is closed as well as shut down (`addCleanup(server.server_close)` beside `addCleanup(server.shutdown)`), as `tests/test_service.py`, `tests/test_end_to_end.py` and `tests/test_receiver.py` already do, because an unclosed socket raises `ResourceWarning` and the suite runs under `-W error`.
- No test in this suite starts Unity, touches `/Applications`, reaches the network or opens the real ledger. Unity, git-lfs and the Unity MCP are reached through injected collaborators that tests substitute, exactly as `FakeLauncher` substitutes the launcher today (spec §18).
- **Every verification step in this plan runs without a live Linear webhook.** The tunnel cannot currently deliver to this Mac, so work items are created directly through the ledger by `python3 -m agent.service enqueue` (Task 8) and the automated proof of criterion 3 drives the scheduler in-process with the `fake` runtime and the Linear stub (Task 10).
- Host-agnostic by construction: every Mac-versus-Windows difference is a config key or a value discovered at run time. The only module allowed to know an operating system is `agent/unity.py`, and it learns the rest from `ProjectSettings/ProjectVersion.txt`. No macOS path is written into any other module or into any default.
- The human's own checkouts are never touched. Slots are worktrees of FarmBot's own bare clone under `.local/`; `/Users/elendil/WorkSpaces/Farm/Farm-Client` is read only as an explicit, operator-invoked LFS seed source (spec §8).
- A slot is always checked out detached at a full 40-hex commit. git refuses the same branch in two worktrees, which is also why the pin is a commit (spec §8).
- Slot git runs **with** LFS smudge; task-worktree git keeps `GIT_LFS_SKIP_SMUDGE=1`. Mixing them gives Unity pointer files and a failure that reads as project corruption (spec §7).
- **Phase 1 runs exactly one slot, and the reason is contention, not memory.** Task 0 Step 5 measured the open Editor at **2.32 GB** resident; on this host's 24 GB two slots would fit comfortably, so an earlier draft's "the 24 GB of RAM caps the pool at one" was wrong. The real reason is the done criterion: §17 criterion 3 asks for two items that both need Unity to **serialize on one slot**, which is only demonstrable if they contend for it. A second slot would remove the contention and the proof would prove nothing. Adding one is the operational checklist's item 6, after criterion 3 is discharged.
- No slot is ever released on a timer. A release follows a passing quiescence probe; a failing probe holds the slot for the operator's `recover-slot` (spec §7). In batch mode the quiescence probe is not an MCP call: it is the confirmation that no Unity process holds the slot folder and that the results file was written or its absence recorded.
- **One `sqlite3.Connection` per thread.** `Ledger._transaction()` is a bare `BEGIN IMMEDIATE`/`COMMIT` on `isolation_level=None` (`agent/ledger.py:266-273`). Two threads sharing one connection do not get two transactions: one thread's `BEGIN` lands inside the other's, raising `cannot start a transaction within a transaction`, and either thread's `COMMIT` or `ROLLBACK` applies to the other's half-written work. WAL and `busy_timeout` protect separate connections, not two users of one handle. `agent/service.py` already hands the receiver a *factory* (`lambda: Ledger(...)`) for exactly this reason; the pool thread gets its own connection the same way (Task 6).
- Reservation tokens are hashed in the ledger and the raw token exists exactly once, at acquisition. A token never travels as a command-line argument; it is written to the item's state directory and read from a file (spec §15).
- Enforcement is tool injection: a worker reaches the Unity MCP only while its item holds an interactive reservation, and never because a repository-local `.codex/config.toml` in a worktree says so (spec §7, §8).
- **A worker never starts a Unity process, in either mode.** Task 0's sandbox addendum measured a `Unity -batchmode -runTests` run inside the Codex `workspace-write` seatbelt hanging indefinitely — 25 minutes at 0.0% CPU, no results file — on a denied Mach lookup for `com.apple.hiservices-xpcservice`, with **zero** file-permission denials; Mach service access is not expressible through `sandbox_workspace_write`, so no list of writable roots can fix it. The batch Editor is therefore started by `agent/launcher.py`, outside the sandbox, from an argv the pool composes out of the slot's own configuration. That is exactly **one** process in FarmBot that runs outside a seatbelt, it is named in one module, its argv never contains a value the worker supplied, the slot folder is in no worker's writable roots in either mode, and for as long as it runs the launcher holds its handle under the item's id and asks every second whether the run is still wanted, so a Stop, the CLI's `cancel` or a service shutdown ends it within seconds (Task 9) instead of leaving it on the slot for up to its 30-minute deadline.
- Linear comment and activity language is zh-CN and concise (spec §9).
- Never merge, deploy, change issue status or assignee (spec §1 non-goals).
- Private runtime state lives under `.local/` and is git-ignored; nothing secret enters the ledger, reports or git (spec §15).
- Commit after every task with a conventional-commit message and a `Co-Authored-By: <model> <noreply@anthropic.com>` trailer naming the model that authored the commit, exactly as that model's own attribution reminder states. The trailer lines inside this plan's commit steps are templates for that line, not literal text.

---

## File structure

| Path | Responsibility | Change |
|---|---|---|
| `docs/superpowers/spikes/2026-09-19-unity-slot-spike.md` | **new**: recorded answers to the eight environment questions Task 0 asks, plus the sandbox addendum (Step 9) | create |
| `agent/worktrees.py` | `resolve_commit`; slot worktrees created and moved with LFS smudge enabled | modify |
| `agent/receiver.py` | resolve and store the pinned target when a session is first seen | modify |
| `agent/ledger.py` | `slots`, `reservations`, `identity_observations`; session and item targets; reservation lifecycle | modify |
| `agent/config.py` | `Paths.editors`; the shape of a `slots` entry | modify |
| `agent/unity.py` | **new**: locate the Editor binary for this host, build the batch test command and read the results file it writes | create |
| `agent/launcher.py` | `run_unsandboxed`: the one place a process is started outside the worker seatbelt, because a batch Editor inside it hangs; `kill_group` and `stop_unsandboxed`, so a Stop and a shutdown can reach it | modify |
| `agent/unity_mcp.py` | **new**: loopback JSON-RPC client for MCP for Unity (ported from `FarmTestAgent/tools/farmqa_unity_identity.py`) | create |
| `agent/identity.py` | **new**: source snapshot, Editor readiness gate and the identity check set (ported from `FarmTestAgent/tools/farmqa_identity.py`) | create |
| `agent/probes/editor-readiness.cs.txt` | **new**: the C# probe run through `execute_code` (copied from `FarmTestAgent/tests/probes/`) | create |
| `agent/slots.py` | **new**: the slot pool — ensure, grant, switch, probe, run the batch, settle, park | create |
| `agent/scheduler.py` | `launch` injects the Unity MCP from the reservation; `stop` cancels reservations, then kills the batch Editor, then the worker | modify |
| `agent/dispatch.py` | the dispatch payload carries a `resource` block | modify |
| `agent/service.py` | the pool thread; `enqueue` and `slot` subcommands; `Scheduler`'s slot entries | modify |
| `agent/deploy.py` | `ProcessType` becomes `Standard`, so a Unity child does not inherit the background band | modify |
| `agent/__main__.py` | `await-resource` really enqueues; `release-resource`, `reservations`, `slots`, `recover-slot` | modify |
| `skills/fix/SKILL.md` | verification-ladder rungs 4 and 5 become real | modify |
| `docs/operating-contract.md` | slots exist; what a worker may ask for and must give back | modify |
| `docs/superpowers/specs/2026-09-17-farm-linear-agent-design.md` | §7 amendment: slot 1 is built fresh on the first host | modify |
| `tests/test_slots.py` | **new**: ensure, grant, switch, park, hold, settle | create |
| `tests/test_unity.py` | **new**: Editor discovery and the batch command | create |
| `tests/test_identity.py` | **new**: the MCP client against a real loopback server, and the check set | create |
| `tests/test_ledger.py`, `tests/test_worktrees.py`, `tests/test_receiver.py`, `tests/test_scheduler.py`, `tests/test_launcher.py`, `tests/test_dispatch.py`, `tests/test_cli.py`, `tests/test_service.py`, `tests/test_deploy.py`, `tests/test_end_to_end.py` | reservations, pinned targets, injection, the scheduling band, the two-item serialization proof | modify |
| `tests/fake_cli.py` | the `cli` mode picks the `<item>:resumed` script when the launch message carries a `resource` block, and substitutes `{token_file}` | modify |
| `scripts/check-rehearsal.py` | **new**: read-only checker that asserts the live rehearsal's ledger transitions and results files | create |
| `reports/2026-09-19-slot-rehearsal/report.md` | **new**: the dated record of the live rehearsal (spec §18) | create |

---

### Task 0: Unity slot spike (record, do not automate) — **DONE on the Mac, 2026-09-19**

Eight facts about this host decide whether the rest of the plan's steps are minutes or hours, and none of them can be read out of documentation. The output is a recorded document, not code. Run it on this Mac. Mark the record "Mac 2026-09-19; re-run on the Windows host in the Windows plan".

> **Status: executed on this Mac on 2026-09-19. The answers are in `docs/superpowers/spikes/2026-09-19-unity-slot-spike.md`, and that record — not the questions below — is what Tasks 3, 4 and 7 read.** The steps are kept in full because the Windows host re-runs them in the Windows plan; where a Mac answer changed a step's expectation, the step says so inline. Five of the eight answers came back different from what this plan assumed, and three of them (the `-runTests` exit code, the graceful close, and `.vscode/settings.json`) forced design changes that are already folded into Tasks 3, 4 and 7. A ninth question, asked afterwards as the record's addendum and kept here as Step 9, forced the largest change of all: a Unity batch run inside a Codex worker's sandbox hangs, so the batch run moved out of the worker and into the launcher (Tasks 4, 7, 8 and 9). Do not re-run Task 0 on this Mac before starting Task 1.

**Preconditions, checked and recorded before Step 1.** Each is a prerequisite the plan cannot create; a failing one stops the spike rather than producing a misleading number. All six passed on the Mac (git-lfs 3.7.1, the LFS endpoint reachable at `192.168.1.201`, `osxkeychain` with a credential stored, `uvx` at `/Users/elendil/.local/bin/uvx`, nothing on 8080, neither farmbot job loaded).

```bash
git lfs version                                   # 3.7.1 here; there is no per-command --quiet flag
curl -s -o /dev/null -w '%{http_code}\n' --max-time 5 http://git.kuaiwa.com/farm/clientlfs.git/info/lfs  # host is on the LAN; verified reachable 2026-09-19
git config --get-urlmatch credential.helper http://git.kuaiwa.com   # expect: osxkeychain (verified 2026-09-19)
which uvx                                         # the MCP server for Unity is a uvx process, not the Editor
lsof -nP -iTCP:8080 -sTCP:LISTEN                   # nothing here means the MCP endpoint will not answer
launchctl print gui/$(id -u)/com.kuaiwa.farmbot.serve >/dev/null && echo loaded || echo "not loaded"
```

**Why:** spec §7 says a `Library/` import is "tens of minutes and several GB", and the installed test-framework documentation explicitly disclaims any common definition of `-runTests` exit codes. The first is an estimate the slot-count decision rests on, the second is the launcher's contract with a batch run. Guessing either one puts a wrong number into Task 4 and a wrong branch into Task 7. Both guesses turned out wrong: the import is 170 s, not tens of minutes, and the exit code is actively misleading — exit 0 means *nothing ran*.

**Files:**
- Create: `docs/superpowers/spikes/2026-09-19-unity-slot-spike.md` — **created 2026-09-19**

- [x] **Step 1: Build a throwaway slot and time the cold import** — done; 1.6 s, 110 MB, DLL is a pointer

The spike slot lives under `$HOME`, not `/tmp`: on macOS `/tmp` is a symlink to `/private/tmp`, and Step 5 compares Unity's own instance hash against `sha1(<slot>/Assets)` in Python, which the two spellings would make ambiguous. Smudge is off here so this step times the checkout *alone* — the LFS cost is Step 2's to measure — and `GIT_TERMINAL_PROMPT=0` makes a missing `git.kuaiwa.com` credential fail instead of blocking on a prompt underneath `time`.

```bash
REPO=/Users/elendil/WorkSpaces/Farm/farm-linear-agent/.local/repos/Farm-Client.git
SPIKE=$HOME/farmbot-slot-spike
SLOT=$SPIKE/slot-1
mkdir -p "$SPIKE"
git -C "$REPO" fetch --quiet --prune origin
COMMIT=$(git -C "$REPO" rev-parse origin/main)
time env GIT_LFS_SKIP_SMUDGE=1 GIT_TERMINAL_PROMPT=0 \
  git -C "$REPO" worktree add --detach "$SLOT" "$COMMIT"
du -sh "$SLOT"
git -C "$SLOT" lfs ls-files | wc -l
head -c 4 "$SLOT/Assets/DOTween/DOTween.dll" | xxd | head -1
```

Record: the wall clock of `worktree add`, the tree size, and that the DLL begins `vers` — with smudge off it must be a pointer, which is the baseline Step 2 measures against. **Success condition:** the worktree exists and `git lfs ls-files` counts the tracked LFS files. *(Mac: 1.6 s after a 2.5 s `fetch --prune`, tree 110 MB, DLL begins `vers`, and the count is **3,778** — the 3,619 an earlier draft wrote into this success condition was measured at an older commit, so the count is recorded, not asserted against a constant.)*

- [x] **Step 2: Measure the two ways to get the LFS objects** — done; the network fetch wins and the copy was deliberately not measured

```bash
du -sh /Users/elendil/WorkSpaces/Farm/farm-linear-agent/.local/repos/Farm-Client.git/lfs
time git -C "$SLOT" lfs fetch origin HEAD    # ~0.91 GB at HEAD, measured with `git lfs ls-files -s`
time git -C "$SLOT" lfs checkout
head -c 4 "$SLOT/Assets/DOTween/DOTween.dll" | xxd | head -1   # must now be MZ
```

and separately, time seeding by copy. The destination for a worktree of a bare clone is the **bare clone's** object store, not a `.git/lfs` inside the worktree:

```bash
time rsync -a /Users/elendil/WorkSpaces/Farm/Farm-Client/.git/lfs/objects/ \
  /Users/elendil/WorkSpaces/Farm/farm-linear-agent/.local/repos/Farm-Client.git/lfs/objects/
du -sh /Users/elendil/WorkSpaces/Farm/farm-linear-agent/.local/repos/Farm-Client.git/lfs
```

Record: bytes and wall clock for each; whether the fetch failed on **credentials** (git-lfs prints a 401 or `Authorization` error) as against **reachability** (a connection refused or timeout), quoting git-lfs's stderr verbatim, because Task 3 must tell those two apart; and that the copy moves 3.6 GB of history against the 0.91 GB a HEAD fetch needs. **Success condition:** one of the two paths turns the DLL from `vers` into `MZ`. The decision line names which one Task 3 uses by default.

*(Mac: `git lfs fetch origin HEAD` **36.2 s / 918 MB**, `git lfs checkout` **5.3 s** at 147 MB/s, DLL now begins `MZ`, tree 110 MB → 979 MB, the bare clone's store 176 KB → 880 MB and shared by every later slot. No prompt and no failure under `GIT_TERMINAL_PROMPT=0` with stdin closed. The rsync seed was a **stated deviation**: it copies ~3.6 GB of history for the same 918 MB of HEAD objects, so measuring a strictly worse path was not worth the disk. **Decision: Task 3 fetches over the network by default**; the failure-mode discrimination stays, because it is what Task 3's error message needs.)*

- [x] **Step 3: Time the first `Library/` import at a fresh path** — done; 170 s, 5.1 GB, and one finding the plan did not expect

A cold import resolves nine git UPM dependencies from scratch, because `Library/PackageCache` is per project and a new worktree has none. Three of them are on the LAN-only host (`com.code-philosophy.hybridclr`, `com.psygames.unitywebsocket`, `com.unity.assetbundlebrowser` under `http://git.kuaiwa.com/mazhangli/...`) and the rest are GitHub, so this step needs both networks. `/HybridCLRData/` is gitignored and is 1.9 GB in the human's checkout; whether a fresh slot produces `HotUpdate.dll` without it is exactly what this step finds out.

```bash
UNITY=/Applications/Unity/Hub/Editor/2022.3.62f3/Unity.app/Contents/MacOS/Unity
# -accept-apiupdate is deliberately NOT passed: spec §8 does not list it, and this plan treats a tree it
# dirtied as a finding. The Mac run omitted it and the tree still came back dirty — see the note below.
time "$UNITY" -batchmode -quit -silent-crashes \
  -projectPath "$SLOT" -logFile "$SPIKE/import.log"
du -sh "$SLOT/Library" "$SLOT/Library/PackageCache"
du -sh "$SLOT"/Library/* | sort -h | tail -12
grep -c "error CS" "$SPIKE/import.log"
grep -iE "hybridclr|installer" "$SPIKE/import.log" | head -20
git -C "$SLOT" status --porcelain | head       # did -accept-apiupdate rewrite tracked scripts?
ls -l "$SLOT/Library/ScriptAssemblies/HotUpdate.dll"
```

Record: wall clock, total `Library/` size, `PackageCache` size, the largest subfolders, whether the licence resolved (`grep -i entitlement "$SPIKE/import.log"`), whether HybridCLR logged an installer warning, whether `HybridCLRData` had to be generated before `HotUpdate.dll` appeared, and whether the working tree came back dirty. **Success condition:** the process exits 0, `Library/ScriptAssemblies/HotUpdate.dll` exists, and `git status --porcelain` is empty. A dirty tree is a finding, not a pass.

*(Mac: **170 s**, `Library/` **5.1 GB** — `Artifacts` 2.9 GB, `PackageCache` 1.8 GB, `Bee` 190 MB, `BurstCache` 107 MB, `ArtifactDB` 80 MB, `ScriptAssemblies` 50 MB. **0** compile errors, `HotUpdate.dll` present at 7,211,008 bytes, the licence resolved six times and the run ended `Exiting batchmode successfully now!`. HybridCLR needed **no** provisioning: no installer warning, and `HotUpdate.dll` appeared without `HybridCLRData` being generated first, so Task 3's one-time provisioning branch is not taken on this host.*
*The tree **did** come back dirty, and the cause is not `-accept-apiupdate`, which was not used: Unity rewrites `.vscode/settings.json` to name the generated solution after the **project folder**, so `"dotnet.defaultSolution": "Farm-Client.slnx"` becomes `"slot-1.slnx"` on every Editor run of every slot. The slot switch's clean-tree precondition would therefore fail every time. **Decision, now implemented in Task 3 Step 3 (`skip_generated`, called from `add_slot` and `checkout_commit`): a new slot is marked `git update-index --skip-worktree .vscode/settings.json`.**)*

- [x] **Step 4: Record the `-runTests` contract** — done, and it is the most consequential answer in the spike

Run the EditMode assembly twice, once expected green and once with a filter that cannot match:

```bash
"$UNITY" -batchmode -silent-crashes -projectPath "$SLOT" \
  -runTests -testPlatform EditMode -assemblyNames HotUpdate.Tests \
  -testResults "$SPIKE/green.xml" -logFile "$SPIKE/green.log"
echo "green exit=$?"

"$UNITY" -batchmode -silent-crashes -projectPath "$SLOT" \
  -runTests -testPlatform EditMode -assemblyNames NoSuchAssembly \
  -testResults "$SPIKE/empty.xml" -logFile "$SPIKE/empty.log"
echo "empty exit=$?"
```

Record: both exit codes, whether the results XML was written in each case, whether `-quit` was needed (it must not be used — the runner exits on its own, spec §8), and the wall clock of each. **Success condition:** a results file exists for the green run and its root element carries `total`, `passed` and `failed` counts. The decision line states whether Task 7 may trust the exit code or must read the XML.

*(Mac, and the answer is that **the exit code must not be trusted**: run A with `-assemblyNames HotUpdate.Tests` exited **2** and wrote 2,536,173 bytes of XML reading `result="Failed(Child)"`, total 4388, passed 4362, failed 26, in 17 s. Run B with `-assemblyNames NoSuchAssembly` exited **0** and wrote 652 bytes reading `result="Passed"` with total **0**, in 10 s. So exit 0 means "nothing ran" and exit 2 means "tests failed": a typo in an assembly name yields a green exit code over zero tests. **Decision, now in Task 7: parse the XML and assert `total > 0` before a run counts as evidence; the exit code is advisory only.** Run A also establishes that `origin/main` is **known-red at 26 of 4388** across eleven classes, listed in the spike record — almost all configuration-table contract tests. A `fix` worker meets those 26 on every run and must not attribute them to its own change.)*

- [x] **Step 5: Record the interactive path** — done; the endpoint, the instance id, App Nap and the close all answered

This is the step Task 4's interactive branch is built on, so it measures the **whole** interactive life cycle, not just the refresh: start, probe, refresh, close. Start the Editor from a shell, the way the pool will, rather than from the Hub:

```bash
"$UNITY" -projectPath "$SLOT" -logFile "$SPIKE/interactive.log" &
EDITOR_PID=$!
time (until lsof -nP -iTCP:8080 -sTCP:LISTEN >/dev/null 2>&1; do sleep 2; done)   # endpoint up
ls -l "$SLOT/Temp/UnityLockfile"
curl -s -X POST http://127.0.0.1:8080/mcp -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"spike","version":"0"}}}'
```

then, with the Editor open and **not** focused, read `mcpforunity://instances`, `mcpforunity://editor/state` and call `execute_code` with `FarmTestAgent/tests/probes/editor-readiness.cs.txt` (it takes an `action: "execute"` argument alongside `code`). Finally try to close it through `execute_code` with `UnityEditor.EditorApplication.Exit(0);`, then fall back to `SIGTERM` on the Editor pid, and record the state of `Temp/UnityLockfile`, of the MCP server process and of port 8080 afterwards.

Record, in this order:

1. **Whether the endpoint answers at all**, and what had to be true first: the `uvx` MCP server process running (name it and say whether the Editor started it), and whether `execute_code` is registered (`Editor/Tools/ExecuteCode.cs` sets `AutoRegister = false`). If the endpoint does not answer, the rest of this step cannot run and Task 4's preflight is the only thing that saves the plan. *(Mac: the Editor **starts the server itself** as a `uvx` child with `--project-scoped-tools` and a per-slot pidfile at `<slot>/Library/MCPForUnity/RunState/mcp_http_8080.pid`; 35 plugin tools, 48 exposed over MCP, `execute_code` among them and **already enabled**. Both operator prerequisites this plan listed are void.)*
2. **Cold Editor start to a usable endpoint**, in seconds, from the `time` above. Task 4's `open_editor` needs this number as its timeout. *(Mac: **16 s**. Task 4 allows 120 s, which is generous headroom rather than a guess.)*
3. **The live instance id for this folder**, next to *both* candidate hashes: `sha1(<slot>/Assets)[:16]` and `sha1(<the dataPath the probe itself returns>)[:16]`. The plugin computes it as `SHA1(Application.dataPath)` truncated to 16 hex characters (`Editor/Helpers/ProjectIdentityUtility.cs`), and the probe's own payload carries `dataPath`, so record both and say which one matched. *(Mac: the live id was `slot-1@54462c1bfe7b5261` and `sha1("<slot>/Assets")[:16]` — **no trailing slash** — reproduces it exactly; `sha1("<slot>")` and `sha1("<slot>/Assets/")` do not.)*
4. **An unfocused `refresh_unity` plus recompile**, three times, each timed, **each preceded by a real source edit** so every sample forces a genuine recompile. **Threshold:** if any of the three exceeds **120 seconds**, record the verdict as "stalled" — that absolute number, not a subjective reading and not a ratio between samples, is what "App Nap stalls it" means here. An earlier draft also called a spread of more than three-to-one a stall; that rule is withdrawn, because a no-op refresh is legitimately sub-second and inflates the ratio while breaching nothing (a `scope=all` attempt with no edit produced 8.3 / 0.6 / 3.3 s, a 13x spread that means nothing). A spread is worth recording, but only the absolute threshold decides. Also record whether focus was stolen.
5. **The resident memory of the open Editor** (`ps -o rss= -p $EDITOR_PID`), which is the input to the second-slot decision. *(Mac: **2.32 GB**, not the "several GB" the spec assumed. On 24 GB, memory does not cap the pool at one slot — see Global Constraints for the reason it stays at one anyway.)*
6. **The close:** whether `EditorApplication.Exit(0)` through `execute_code` returns *and whether the Editor actually exits*, how long `SIGTERM` on the Editor pid takes, whether `Temp/UnityLockfile` is left behind, whether the `uvx` MCP server outlives the Editor and keeps port 8080 bound, and whether `Library/` survived intact.

*(Mac, item 4: **9.6 / 7.7 / 5.7 s** against the 120 s threshold, no focus stolen. **Verdict: OK** — no `NSAppSleepDisabled`, no separate macOS user, no batch-only restriction on this Mac. Item 6, and this one changed the design: `EditorApplication.Exit(0)` returned `"exiting"` in 0.1 s and **did not close the Editor** — it was still running and fully responsive two minutes later. `SIGTERM` on the pid worked, process gone in **1 s**; `Temp/UnityLockfile` was **still present at +32 s**, so Unity leaves it behind on an ordinary close and a `close_editor` that waited for it would hang forever; `Library/` was intact at 5.1 GB; and the MCP server **outlived the Editor**, still holding port 8080 and still answering HTTP 200. Task 4's `close_editor` is rewritten accordingly: SIGTERM, remove the lockfile by hand, reap the server from the pidfile.)*

**Success condition:** the probe returns a JSON payload naming `HotUpdate` with a `moduleMvid`; the unfocused refresh does not meet the 120-second stall threshold; and the close ends with the Editor process gone, the lockfile gone and port 8080 free — by whatever combination of request and signal the host actually requires. If the refresh stalls, record it and stop — Task 4's interactive branch needs a different shape and the plan must be revised before Task 4.

- [x] **Step 6: Record whether a launchd job can drive Unity, and at what speed** — done; the licence works, the scheduling band costs 7.5x

Use a one-shot LaunchAgent or a `serve`-hosted call (`launchctl asuser $(id -u) ...` is not a substitute). Record three things.

1. **The licence.** Does the batch Editor acquire the seat in `/Library/Application Support/Unity/Unity_lic.ulf`? Note that `com.kuaiwa.farmbot.serve` is a LaunchAgent in the `gui/501` domain, so a window server exists whenever the user is logged in; if it fails, the failure is the licensing IPC, and the log line is what the operator needs.
2. **Contention with the human's own Editor.** Repeat the run with the operator's Editor open on `/Users/elendil/WorkSpaces/Farm/Farm-Client`. Record the exact error on whichever of the two is refused. This decides whether `SlotPool.switch` needs the `pgrep` preflight Task 4 adds.
3. **The throttled cost.** The plist sets `ProcessType` to `Background`, so the launchd run inherits the background scheduling band while the foreground run did not. Record both wall clocks side by side, and run a **warm-cache foreground control** immediately afterwards so cache state cannot be confused with the scheduling band.

**Success condition:** either it works and the two wall clocks are within a factor the operator accepts, or the record names the failure precisely enough for them to choose between a login session, `ProcessType=Standard` and a purchased seat.

*(Mac: measured cheaply, by running the **same EditMode suite** as Step 4 under a one-shot `com.kuaiwa.farmbot.spike` agent rather than paying for a second cold import; the agent was booted out and its plist deleted afterwards, and neither farmbot job was loaded. **(1) The licence works under launchd:** `entitlements=7`, exit code 2 exactly as foreground, results XML 2,536,169 bytes against the foreground run's 2,536,173. No second seat, no login-session requirement, no `pgrep` preflight needed on licensing grounds. **(2) Contention was NOT exercised** — the operator had no Editor open — so that half of the question is still open and Task 4 keeps `another_editor_running`. **(3) The Background band costs 7.5x:** 17 s foreground cold, **105 s under launchd**, 14 s foreground warm as the control — and the launchd run was the *later*, warmer one, so this is scheduling, not cache state. **`ProcessType=Standard` is now an owed deployment change in `agent/deploy.py`**, checklist item 2.)*

- [x] **Step 7: Write the record** — done; `docs/superpowers/spikes/2026-09-19-unity-slot-spike.md` exists and is the source of truth

Create `docs/superpowers/spikes/2026-09-19-unity-slot-spike.md` with one section per step: the command, the measured numbers, and the answer. End with a decision list of exactly eight lines, one per question: LFS strategy (and whether the failure mode is auth or reachability), import cost foreground versus launchd, batch exit-code contract, instance id source, App Nap verdict against the 120-second threshold, Editor RSS, Editor start timeout, and the graceful-close method. Then clean up:

```bash
git -C /Users/elendil/WorkSpaces/Farm/farm-linear-agent/.local/repos/Farm-Client.git worktree remove --force "$SLOT"
rm -rf "$SPIKE"
```

- [x] **Step 8: Commit** — done, in `d1b0b28`

```bash
git add docs/superpowers/spikes/2026-09-19-unity-slot-spike.md
git commit -m "docs: record the Unity slot spike on the Mac

Import cost, LFS strategy, batch exit codes, the live Editor instance id, App Nap
and the launchd licence question, measured rather than assumed. Plan 1b's Tasks 3,
4 and 7 read their defaults from this record.

Co-Authored-By: <model> <noreply@anthropic.com>"
```

- [x] **Step 9: Run the batch suite inside a worker's sandbox** — done on the Mac as the record's addendum, 2026-09-19; it hangs

Steps 3 to 6 all ran the Editor from a shell or a plain LaunchAgent, with no seatbelt. A `fix` worker is a `codex exec --approve-for-me` process, which runs under the `workspace-write` sandbox that `agent/launcher.py:106` configures, so the question those steps never asked is whether the Editor works *inside* that sandbox at all. Reproduce the launcher's arrangement by hand — `CODEX_HOME` nested inside a state directory as `Launcher.spawn` lays it out, `writable_roots = [<state dir>, <slot>]`, `network_access = true` — and give the worker one instruction: run Step 4's green command and wait for it.

```bash
mkdir -p "$SPIKE/state/home"
cat > "$SPIKE/state/home/config.toml" <<EOF
[sandbox_workspace_write]
writable_roots = ["$SPIKE/state", "$SLOT"]
network_access = true
EOF
CODEX_HOME="$SPIKE/state/home" codex exec --cd "$SLOT" --approve-for-me --skip-git-repo-check \
  "Run exactly this command and wait for it to exit: $UNITY -batchmode -silent-crashes -projectPath $SLOT \
   -runTests -testPlatform EditMode -assemblyNames HotUpdate.Tests \
   -testResults $SPIKE/sandboxed.xml -logFile $SPIKE/sandboxed.log"
```

Record: whether the Editor exits and with what code, whether the results XML is written, the wall clock against Step 4's, the CPU and RSS of a run that has not exited after five minutes, the last lines of the Unity log, and whether the sandbox logged any file-permission denial. **Success condition:** the run exits and writes a results file whose `total` matches Step 4's. If it does not, the record must name the failing service or operation from the log, because that decides whether the fix is a configuration entry or a design change.

*(Mac: **it hangs.** Killed at 25 minutes at 0.0% CPU and 113 MB RSS, log frozen at 2,289 bytes, no results file, **zero** file-permission denials; the same command on the same slot minutes later, unsandboxed, exited 2 in 19 s over 4388 tests. Licensing succeeded inside the sandbox — the log then stops at `Connection Invalid error for service com.apple.hiservices-xpcservice`, a Mach service lookup the seatbelt denies and `sandbox_workspace_write` cannot grant. **Decision, now built into Tasks 4, 7, 8 and 9: a worker never starts a Unity process; the pool composes the batch argv, `Launcher.run_unsandboxed` runs it outside the seatbelt, and the worker is handed the results as evidence.** The Windows host must answer this question for its own sandbox before its slot runs a batch item: Windows has no seatbelt, so the answer may differ, and the design is host-safe either way — checklist item 5.)*

---

### Task 1: Every work item pins a commit

Criterion 3 says the slot is "switched to each pinned commit", and today no pin is ever written. `agent/receiver.py:197` passes `target=(session or {}).get("target")`, `Ledger.session` reads `sessions.target_json`, and **nothing in the repository writes that column** — so `work_items.target_json` is always NULL and the dispatch payload ships `"target": null`. This task resolves the pin at receipt and echoes it in the first activity, which spec §6 requires so a human can correct it before work starts.

**Why:** the slot switch takes a commit. Without this task the pool has nothing to switch to, and the done criterion cannot be demonstrated no matter how correct the rest of the plan is.

**Decision this task encodes:** three of them. First, the pin is resolved once, when the session is first seen, from the client's remote default-branch head, and it is validated and reprojected before it is stored. `checked_target` is ported from `FarmTestAgent/tools/farmqa_controller.py:16-36`: the commit must be a full lowercase 40-hex string, and every key that is not target metadata is dropped, so nothing that arrives in an event can ride along into the dispatch payload. Changing the session default later never retargets an accepted item, because the item snapshots the session target at creation (spec §6).

Second, **the receiver resolves the pin with one bounded `git ls-remote`, never with a clone or a fetch.** The item snapshots the target at creation, so the pin has to exist before `create_work_item`, which puts it on the receiver's synchronous acknowledgment path — and `resolve_commit` is `ensure_clone` (a full bare clone of Farm-Client when absent: minutes and gigabytes) plus `fetch` (600-second git timeout) plus an extra `ls-remote`. That is the hazard Plan 1a added `seed-clones` to avoid, one layer earlier, and it would blow spec §17 criterion 1's ten seconds to the first activity. So the receiver calls a new `Worktrees.remote_head(repo, timeout=8)` — one `ls-remote --symref` against the configured remote URL, no clone, no fetch, an explicit small timeout and a 60-second per-repo cache so a burst of events pays once. `resolve_commit` keeps its clone-based shape for the pool, which is allowed to be slow. A commit resolved this way may not yet be in FarmBot's clone when the pool grants the slot, which is why Task 4's switch fetches first.

Third, **the pin rides inside the acknowledgment, not beside it.** `LinearAPI.create_activity` puts `activity_id` into the mutation as `input.id`, which Linear treats as the activity's identity, and raises `RuntimeError("Linear did not confirm activity creation")` when the mutation does not report success. Every existing call site sends exactly one activity per `ack_id`. A second `_send(session_id, ack_id, ...)` would at best be deduped — losing the routing ACK — and at worst raise and make the event retry. One event therefore still produces exactly one activity, whose body is the ACK sentence with the pin sentence appended.

**Files:**
- Modify: `agent/worktrees.py` (`resolve_commit`, `remote_head`)
- Modify: `agent/ledger.py` (`checked_target`, `set_session_target`)
- Modify: `agent/receiver.py` (`_decide_and_act` — there is no `_process_session`)
- Modify: `agent/service.py` (`build` passes `worktrees` and `default_server_environment` to the receiver)
- Test: `tests/test_worktrees.py`, `tests/test_ledger.py`, `tests/test_receiver.py`

**Interfaces:**
- Consumes: `Worktrees.default_branch(repo)` and `Worktrees.ensure_clone(repo)` as they exist; `Ledger.ensure_session(session_id, issue_id, delegation, guidance=None)`, which already returns the session view; `Ledger.create_work_item(..., target=None)`.
- Produces:
  - `Worktrees.resolve_commit(repo, ref=None) -> str`, a full 40-hex commit. With no `ref` it fetches and resolves `origin/<default branch>`. This is the pool's path and may take minutes.
  - `Worktrees.remote_head(repo, timeout=8) -> str`, a full 40-hex commit read straight from the remote with `ls-remote`, cached for 60 seconds. This is the receiver's path and never clones or fetches.
  - `agent.ledger.checked_target(raw) -> dict` with exactly the keys `repository`, `requested_ref`, `commit_sha`, `server_environment`, `selected_at`. It raises `LedgerError` on anything else, and is the only way a target enters the ledger. **`selected_at` is an ISO-8601 UTC string**, because `agent/ledger.py:51`'s `_timestamp` validator takes a string with a timezone and nothing else; every fixture and call site in this plan writes `datetime.now(timezone.utc).isoformat()` or a literal such as `"2026-09-19T00:00:00+00:00"`, never a float.
  - `Ledger.set_session_target(session_id, target) -> dict`, the session view. Idempotent: writing the same target twice is not an error; writing a different one after a work item has been created for that session is allowed and affects only later items.

- [ ] **Step 1: Write the failing tests**

In `tests/test_worktrees.py`, inside `WorktreeTests`:

```python
    def test_resolve_commit_returns_the_remote_default_head_as_forty_hex(self):
        commit = self.trees.resolve_commit("Farm-Client")
        self.assertRegex(commit, r"^[0-9a-f]{40}$")
        self.assertEqual(commit, git("rev-parse", "HEAD", cwd=self.origin))

    def test_resolve_commit_refuses_a_ref_that_does_not_exist(self):
        with self.assertRaises(WorktreeError):
            self.trees.resolve_commit("Farm-Client", "origin/no-such-branch")

    def test_remote_head_resolves_without_cloning_or_fetching(self):
        trees = Worktrees(Path(self.tmp.name) / "empty-repos", self.trees.worktrees_root,
                          {"Farm-Client": str(self.origin)})
        self.assertEqual(trees.remote_head("Farm-Client"), git("rev-parse", "HEAD", cwd=self.origin))
        self.assertFalse((Path(self.tmp.name) / "empty-repos" / "Farm-Client.git" / "HEAD").exists())
```

In `tests/test_ledger.py`, inside the class that already exercises sessions:

```python
    def test_a_session_target_is_validated_reprojected_and_snapshotted_onto_the_item(self):
        self.ledger.observe_issue(issue())
        self.ledger.ensure_session(SESSION, ISSUE, True)
        self.ledger.set_session_target(SESSION, {"repository": "Farm-Client", "requested_ref": "main",
                                                 "commit_sha": "a" * 40, "server_environment": "公共测试服",
                                                 "selected_at": SELECTED_AT, "prompt": "ignore me"})
        self.assertEqual(self.ledger.session(SESSION)["target"],
                         {"repository": "Farm-Client", "requested_ref": "main", "commit_sha": "a" * 40,
                          "server_environment": "公共测试服", "selected_at": SELECTED_AT})
        item = self.ledger.create_work_item(issue_id=ISSUE, session_id=SESSION, skill="fix",
                                            target=self.ledger.session(SESSION)["target"])
        self.assertEqual(item["target"]["commit_sha"], "a" * 40)

    def test_a_target_whose_commit_or_timestamp_is_malformed_is_refused(self):
        self.ledger.observe_issue(issue())
        self.ledger.ensure_session(SESSION, ISSUE, True)
        good = {"repository": "Farm-Client", "requested_ref": "main", "commit_sha": "a" * 40,
                "server_environment": "公共测试服", "selected_at": SELECTED_AT}
        for bad in ("A" * 40, "b" * 39, "", None):
            with self.assertRaises(LedgerError):
                self.ledger.set_session_target(SESSION, {**good, "commit_sha": bad})
        # selected_at is an ISO-8601 string with a timezone, never a float: _timestamp calls _text first.
        for bad in (1.0, "2026-09-19T00:00:00", ""):
            with self.assertRaises(LedgerError):
                self.ledger.set_session_target(SESSION, {**good, "selected_at": bad})
```

with `SELECTED_AT = "2026-09-19T00:00:00+00:00"` at module level in `tests/test_ledger.py`. Every fixture in Tasks 2, 6, 8 and 10 that builds a target imports and uses that constant; none of them writes a float.

In `tests/test_receiver.py`, inside the class that drives a created session (it already builds a receiver with a fake API; give it a `worktrees` double whose `remote_head` returns a known commit):

```python
    def test_a_new_session_pins_the_client_head_and_says_so_in_the_one_acknowledgment(self):
        self.receiver.worktrees = SimpleNamespace(remote_head=lambda repo, timeout=8: "c" * 40)
        self.deliver(self.created_event())
        self.assertTrue(self.receiver.process_one())
        target = self.ledger.session("session-1")["target"]
        self.assertEqual((target["commit_sha"], target["server_environment"]), ("c" * 40, "公共测试服"))
        self.assertEqual(self.ledger.items_for_session("session-1")[0]["target"]["commit_sha"], "c" * 40)
        # One event, one activity: create_activity treats activity_id as the activity's identity.
        self.assertEqual(len(self.api.activities), 1)
        self.assertIn("c" * 7, self.api.activities[0]["content"]["body"])

    def test_a_session_whose_client_head_cannot_be_resolved_still_starts_without_a_pin(self):
        self.receiver.worktrees = SimpleNamespace(remote_head=_raise(WorktreeError("origin unreachable")))
        self.deliver(self.created_event())
        self.assertTrue(self.receiver.process_one())
        self.assertIsNone(self.ledger.session("session-1")["target"])
        self.assertEqual(self.ledger.items_for_session("session-1")[0]["target"], None)
        self.assertEqual(len(self.api.activities), 1)

    def test_the_receiver_never_clones_or_fetches_to_resolve_a_pin(self):
        """spec §17 criterion 1 gives the first activity ten seconds; ensure_clone is minutes (Plan 1a's
        seed-clones exists for exactly this)."""
        self.receiver.worktrees = SimpleNamespace(remote_head=lambda repo, timeout=8: "c" * 40,
                                                  ensure_clone=_raise(AssertionError("cloned on the ack path")),
                                                  fetch=_raise(AssertionError("fetched on the ack path")),
                                                  resolve_commit=_raise(AssertionError("resolved the slow way")))
        self.deliver(self.created_event())
        self.assertTrue(self.receiver.process_one())
        self.assertEqual(self.ledger.session("session-1")["target"]["commit_sha"], "c" * 40)
```

Add `from types import SimpleNamespace` and `from agent.worktrees import WorktreeError` to `tests/test_receiver.py`, and a module-level helper:

```python
def _raise(exc):
    def raiser(*args, **kwargs):
        raise exc
    return raiser
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -W error -m unittest discover -s tests -v -k target -k resolve_commit -k remote_head -k pins -k without_a_pin -k acknowledgment -k clones`
Expected: FAIL. `Worktrees` has no `resolve_commit` or `remote_head`, `Ledger` has no `set_session_target`, and the receiver never writes a target. (Repeated `-k` flags are ORed; a single `-k "a or b"` matches nothing and would let this gate pass on zero tests.)

- [ ] **Step 3: Resolve a commit in the worktrees**

In `agent/worktrees.py`, add after `default_branch`:

```python
    COMMIT = re.compile(r"^[0-9a-f]{40}$")

    def resolve_commit(self, repo, ref=None):
        """A pinned target is always a full commit: git refuses the same branch in two worktrees (spec §8).
        This is the pool's path: it clones if it must and fetches every time, so it may take minutes."""
        clone = self.ensure_clone(repo)
        self.fetch(repo)
        ref = ref or f"origin/{self.default_branch(repo)}"
        commit = _git("rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}", cwd=clone)
        if not self.COMMIT.match(commit):
            raise WorktreeError(f"{ref} did not resolve to a commit")
        return commit

    def remote_head(self, repo, timeout=8):
        """The receiver's path: one ls-remote against the remote URL, no clone and no fetch, so pinning a new
        session cannot cost the ten seconds spec §17 criterion 1 gives the first activity. Cached for 60s so a
        burst of events pays once."""
        if repo not in self.remotes:
            raise WorktreeError(f"unknown repository: {repo}")
        cached = self._head_cache.get(repo)
        if cached and time.monotonic() - cached[0] < 60:
            return cached[1]
        self.repos_root.mkdir(parents=True, exist_ok=True)
        out = _git("ls-remote", "--exit-code", "--", self.remotes[repo], "HEAD",
                   cwd=self.repos_root, timeout=timeout)
        commit = out.split()[0] if out else ""
        if not self.COMMIT.match(commit):
            raise WorktreeError(f"{repo}: origin HEAD did not resolve to a commit")
        self._head_cache[repo] = (time.monotonic(), commit)
        return commit
```

with `import time` at the top of the module and `self._head_cache = {}` in `__init__`. This task also gives `_git` its `timeout=600` keyword (today the 600 is a literal inside the call), so `remote_head` can ask for eight seconds; Task 3 adds the `env` keyword to the same signature, and neither change touches an existing call site. `subprocess.TimeoutExpired` is not caught inside `_git` — the receiver's own `except` in Step 5 is what turns a slow origin into an unpinned session.

- [ ] **Step 4: Validate and store the target in the ledger**

In `agent/ledger.py`, add beside the other validators:

```python
COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
TARGET_KEYS = ("repository", "requested_ref", "commit_sha", "server_environment", "selected_at")


def checked_target(raw):
    """Validate then reproject: only target metadata reaches the ledger, the dispatch payload and the slot."""
    if not isinstance(raw, dict):
        raise LedgerError("target must be an object")
    _text(raw.get("repository"), "target repository")
    _text(raw.get("requested_ref"), "target requested_ref")
    _text(raw.get("server_environment"), "target server_environment")
    commit = raw.get("commit_sha")
    if not isinstance(commit, str) or not COMMIT_SHA.match(commit):
        raise LedgerError("target commit_sha must be a full lowercase 40-character hex commit")
    _timestamp(raw.get("selected_at"), "target selected_at")
    return {key: raw[key] for key in TARGET_KEYS}
```

and, next to `ensure_session`:

```python
    def set_session_target(self, session_id, target):
        """The pin for later items in this session. An accepted item keeps the target it snapshotted (spec §6)."""
        target = checked_target(target)
        with self._transaction():
            if self.session(session_id) is None:
                raise LedgerError(f"unknown session: {session_id}")
            self.connection.execute("UPDATE sessions SET target_json=? WHERE session_id=?",
                                    (_json(target), session_id))
        return self.session(session_id)
```

- [ ] **Step 5: Pin at receipt and echo the pin**

In `agent/receiver.py`, give `Receiver.__init__` a `worktrees=None` keyword and store it. Three details about *where* the block goes, each of which the obvious placement gets wrong:

- `session = self.ledger.session(prepared["session_id"])` is read at `agent/receiver.py:170`, **before** `ensure_session` at line 173. The method's `def` is line 167 and the issue fetch and `observe_issue` are 168–169; those three lines stay exactly as they are. For a brand-new session — the only case this task exists for — that local is `None`. The block therefore rebinds the variable from `ensure_session`'s own return value, which is already the session view, and guards only on the target.
- `session_id = prepared["session_id"]` is not bound until after `route(...)`. The block uses `prepared["session_id"]` explicitly rather than depending on a name that does not exist yet.
- The pin does **not** get its own `_send`. It is appended to the ACK body that the existing code already sends with this event's `ack_id`.

Replace the four-line block that begins `session = self.ledger.session(prepared["session_id"])` and ends with the `self.ledger.ensure_session(...)` call — `agent/receiver.py:170-173` today; anchor on the text, not the numbers, which drift — with:

```python
        session = self.ledger.session(prepared["session_id"])
        is_delegation = (session["delegation"] if session else
                         prepared["action"] == "created" and issue.get("delegate_id") == self.identity["appUserId"])
        session = self.ledger.ensure_session(prepared["session_id"], issue["id"], is_delegation, prepared["guidance"])
        pin = ""
        if session.get("target") is None and self.worktrees is not None:
            try:
                commit = self.worktrees.remote_head(TARGET_REPO, timeout=PIN_TIMEOUT)
                session = self.ledger.set_session_target(prepared["session_id"], {
                    "repository": TARGET_REPO, "requested_ref": "default", "commit_sha": commit,
                    "server_environment": self.default_server_environment,
                    "selected_at": datetime.now(timezone.utc).isoformat()})
                pin = f"\n目标已锁定：{TARGET_REPO}@{commit[:7]}（{self.default_server_environment}）。"
            except Exception:
                # A pin that cannot be resolved must not stop the session; the item runs unpinned and any
                # Unity rung will refuse it, which is a recorded gap rather than a silent wrong-commit run.
                pin = "\n暂时无法锁定客户端提交，本次将不做 Unity 验证。"
```

and append `pin` to the two ACK bodies the `work` and `chat` branches already send:

```python
            self._send(session_id, ack_id, {"type": "thought", "body": ACK.get(decision.skill, ACK["chat"]) + pin})
```

with `TARGET_REPO = "Farm-Client"` and `PIN_TIMEOUT = 8` at module level, `from datetime import datetime, timezone` in the imports, and `self.default_server_environment` passed in from `service.build` as `config.default_server_environment`. Eight seconds is the budget: spec §17 criterion 1 gives the whole acknowledgment ten, and `fetch_issue` has already spent some of it. In `agent/service.py`, pass both through:

```python
    receiver = Receiver(paths.ledger, config.webhook_secret,
                        {"oauthClientId": config.client_id, "appUserId": identity["viewer"]["id"],
                         "organizationId": identity["organization"]["id"]},
                        api, lambda: Ledger(paths.ledger, check_same_thread=False), set(skills), scheduler,
                        worktrees=worktrees, default_server_environment=config.default_server_environment)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 -W error -m unittest discover -s tests -v -k target -k resolve_commit -k remote_head -k pins -k without_a_pin -k acknowledgment -k clones`
Expected: PASS, eleven tests: the eight new ones plus the three the substrings already match today (`-k target` selects `test_pr_targets_accept_ssh_remotes_ignore_case_and_refuse_a_config_without_github`, `-k clones` the two `SeedCloneTests` cases). `-k without_a_pin` is in the list because `test_a_session_whose_client_head_cannot_be_resolved_still_starts_without_a_pin` contains none of the other six substrings — `_a_pin` is not `pins` — and without it both this gate and Step 2's run seven of the eight.

- [ ] **Step 7: Run the whole suite and commit**

Run: `python3 -W error -m unittest discover -s tests`
Expected: OK, 147 tests (+8: three in `tests/test_worktrees.py`, two in `tests/test_ledger.py`, three in `tests/test_receiver.py`).

```bash
git add agent/worktrees.py agent/ledger.py agent/receiver.py agent/service.py tests/test_worktrees.py tests/test_ledger.py tests/test_receiver.py
git commit -m "feat(receiver): pin every session to a resolved client commit

sessions.target_json was read by the receiver and written by nothing, so every
work item shipped a null target. A new session now resolves Farm-Client's remote
default head to a full commit, validates and reprojects it, and echoes the pin in
the first activity so a human can correct it before work starts.

Co-Authored-By: <model> <noreply@anthropic.com>"
```

---

### Task 2: The ledger holds slots and reservations

Port `FarmTestAgent/tools/farmqa_controller.py`, reshaped around FarmBot's work items. Copy the state set, the partial unique index, the hashed owner token and the transition rules; drop `event_key`, `organization_id`, `session_id` and the receiver-table validation, because FarmBot has work items with a lifecycle instead. Everything not mentioned here has no counterpart and is not ported.

**Why:** `await_resource` and `resume` have existed since Plan 1a and nothing drives them, so an `awaiting_resource` item is inert: `queue()` selects `state='queued' AND worker_pid IS NULL`, and `tests/test_scheduler.py:221` asserts that as intended 1a behaviour. The reservation row is the thing that makes the wait finite, and the unique index is the thing that makes "one worker at a time on one Editor folder" true across processes rather than by convention.

**Decision this task encodes:** three of them. First, FIFO is `ORDER BY sequence`, arrival order, not the `priority, created_at, id` order `queue()` uses — a late high-priority item must not jump ahead of one that already waited, and criterion 3's "two items serialize" is only assertable on a deterministic order. Second, the unique index is on `resource`, not on FarmQA's constant `(1)`, so a second slot needs no schema change **and no change to `acquire`**: there is deliberately no kind-wide pre-check, because one would be a semantic restriction rather than an optimisation — it would refuse to grant slot 2 while slot 1 was busy, and it would starve one host while another host's reservation was active. The free-slot query is the whole gate, and a busy slot is simply not free. The table CHECK that forbids an `active` row without a `resource` is what makes the index binding, because SQLite treats NULLs as distinct. Third, an item may hold at most one open reservation, enforced by `one_open_reservation_per_item`, so a worker that wants a different mode must release the slot it holds first.

**Files:**
- Modify: `agent/ledger.py` (schema block, `await_resource`, `cancel`, new reservation and slot methods)
- Test: `tests/test_ledger.py`

**Interfaces:**
- Consumes: `Ledger._transaction()` (`BEGIN IMMEDIATE`), `_hash_token`, `_audit`, `_set_state`, `_owned`, and `await_resource(item_id, token, resource, mode)` as it exists.
- Produces:
  - `Ledger.ensure_slot(slot_id, *, kind, host, folder, instance=None, mcp_address=None, account=None) -> dict`, an upsert that never clears a discovered `instance` with `None`.
  - `Ledger.slots(kind=None, host=None) -> list[dict]`, `Ledger.slot(slot_id) -> dict | None`.
  - `Ledger.set_slot_state(slot_id, state, *, parked_commit=..., instance=..., last_switch_at=...) -> dict`. States: `idle_closed`, `idle_open`, `switching`, `interactive_busy`, `batch_busy`, `held`.
  - `Ledger.await_resource(item_id, token, resource, mode)` unchanged in signature; it now also inserts the `queued` reservation in the same transaction, copying the item's pinned `commit_sha` and `generation`. It raises `LedgerError` when the item has no pinned commit or already holds an open reservation.
  - `Ledger.acquire(kind, *, owner, host) -> dict | None` returning `{reservation_id, token, resource, item_id, mode, commit_sha, folder}`. The raw token exists exactly once, as in `claim`.
  - `Ledger.reservation(reservation_id) -> dict | None` (token-free), `Ledger.reservations(states=None) -> list[dict]`, `Ledger.active_reservation(item_id) -> dict | None`, `Ledger.reservations_to_settle() -> list[dict]`.
  - `Ledger.release(reservation_id, token, reason) -> str`, returning `"released"` or `"cancelled"`; `cancel_requested` resolves to `cancelled`, `active` to `released`, and an already-terminal reservation returns its state unchanged.
  - `Ledger.hold(reservation_id, reason) -> dict` — the reservation stays open, the slot becomes `held`.
  - `Ledger.recover_slot(slot_id, reason) -> dict` — the operator's way out of `held`: any open reservation on that slot is force-released with the reason, the slot becomes `idle_closed` and its `parked_commit` is cleared to NULL, because after a recovery the ledger no longer knows what commit the folder is on.
  - `Ledger.cancel_reservations(item_id, reason) -> list[dict]` — `queued` → `cancelled`, `active` → `cancel_requested`, in one statement.
  - `Ledger.record_identity(item_id, reservation_id, slot_id, result) -> str`, the observation id.

- [ ] **Step 1: Write the failing tests**

Add a new class at the end of `tests/test_ledger.py`. The existing `LedgerTests.setUp` seeds an issue and a session and is reused through a small helper; this class needs two items on two issues, so it builds its own.

```python
class ReservationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = 1000.0
        self.ledger = Ledger(Path(self.tmp.name) / "l.sqlite3", clock=lambda: self.now, lease_seconds=60)
        self.addCleanup(self.ledger.close)
        self.ledger.ensure_slot("unity_slot:1", kind="unity_slot", host="mac", folder="/e/slot-1")

    def item(self, issue_id, commit, priority=2):
        # The helper's keyword is `id`, not `issue_id`: tests/test_ledger.py defines `def issue(id=ISSUE,
        # **changes)`, so issue(issue_id=OTHER) would leave the row on ISSUE and the create would then fail
        # with "unknown issue". The two items must be on two issue rows because create_work_item refuses a
        # second active item on one issue.
        self.ledger.observe_issue(issue(id=issue_id, priority=priority))
        session = f"session-{issue_id}"
        self.ledger.ensure_session(session, issue_id, True)
        target = {"repository": "Farm-Client", "requested_ref": "main", "commit_sha": commit,
                  "server_environment": "公共测试服", "selected_at": SELECTED_AT}
        return self.ledger.create_work_item(issue_id=issue_id, session_id=session, skill="fix", target=target)

    def waiting(self, issue_id, commit, mode, priority=2):
        item = self.item(issue_id, commit, priority=priority)
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        self.ledger.await_resource(item["id"], token, "unity_slot", mode)
        return item["id"]

    def test_two_requests_are_granted_in_arrival_order_not_priority_order(self):
        # The second issue is *more* urgent, so an implementation that ordered by priority would grant it
        # first and this test would fail. With both at the same priority the assertion proves nothing.
        first = self.waiting(ISSUE, "a" * 40, "batch")
        second = self.waiting(OTHER, "b" * 40, "interactive", priority=1)
        granted = self.ledger.acquire("unity_slot", owner="pool", host="mac")
        self.assertEqual((granted["item_id"], granted["mode"], granted["commit_sha"]), (first, "batch", "a" * 40))
        self.assertIsNone(self.ledger.acquire("unity_slot", owner="pool", host="mac"))
        self.assertEqual(self.ledger.release(granted["reservation_id"], granted["token"], "batch finished"), "released")
        self.assertEqual(self.ledger.acquire("unity_slot", owner="pool", host="mac")["item_id"], second)

    def test_the_unique_index_refuses_a_second_active_owner_of_one_slot(self):
        self.waiting(ISSUE, "a" * 40, "batch")
        granted = self.ledger.acquire("unity_slot", owner="pool", host="mac")
        with self.assertRaises(sqlite3.IntegrityError):
            self.ledger.connection.execute(
                """INSERT INTO reservations(reservation_id,item_id,generation,kind,mode,resource,host,commit_sha,
                   state,owner,token_hash,created_at,acquired_at)
                   VALUES('r2',?,0,'unity_slot','batch','unity_slot:1','mac',?,'active','x','y',?,?)""",
                (granted["item_id"], "a" * 40, self.now, self.now))

    def test_a_worker_may_not_stack_a_second_request_while_it_holds_one(self):
        item = self.item(ISSUE, "a" * 40)
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        self.ledger.await_resource(item["id"], token, "unity_slot", "batch")
        self.ledger.resume(item["id"], "granted")
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        with self.assertRaises(LedgerError):
            self.ledger.await_resource(item["id"], token, "unity_slot", "interactive")

    def test_an_item_without_a_pinned_commit_cannot_request_a_slot(self):
        self.ledger.observe_issue(issue())
        self.ledger.ensure_session(SESSION, ISSUE, True)
        item = self.ledger.create_work_item(issue_id=ISSUE, session_id=SESSION, skill="fix")
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        with self.assertRaises(LedgerError):
            self.ledger.await_resource(item["id"], token, "unity_slot", "batch")

    def test_a_wrong_token_can_neither_read_nor_release_a_reservation(self):
        self.waiting(ISSUE, "a" * 40, "batch")
        granted = self.ledger.acquire("unity_slot", owner="pool", host="mac")
        with self.assertRaises(LedgerError):
            self.ledger.release(granted["reservation_id"], "not-the-token", "nope")
        self.assertEqual(self.ledger.reservation(granted["reservation_id"])["state"], "active")
        self.assertNotIn("token", self.ledger.reservation(granted["reservation_id"]))

    def test_stop_cancels_a_queued_reservation_and_only_marks_an_active_one(self):
        first = self.waiting(ISSUE, "a" * 40, "batch")
        second = self.waiting(OTHER, "b" * 40, "interactive")
        granted = self.ledger.acquire("unity_slot", owner="pool", host="mac")
        self.ledger.cancel_reservations(first, "Linear stop")
        self.ledger.cancel_reservations(second, "Linear stop")
        self.assertEqual(self.ledger.reservation(granted["reservation_id"])["state"], "cancel_requested")
        self.assertEqual([r["state"] for r in self.ledger.reservations() if r["item_id"] == second], ["cancelled"])
        self.assertEqual(self.ledger.release(granted["reservation_id"], granted["token"], "stopped"), "cancelled")

    def test_cancelling_a_work_item_cancels_its_reservations_in_the_same_transaction(self):
        item = self.waiting(ISSUE, "a" * 40, "batch")
        self.ledger.cancel(item, "operator")
        self.assertEqual([r["state"] for r in self.ledger.reservations() if r["item_id"] == item], ["cancelled"])

    def test_a_failing_probe_holds_the_slot_until_the_operator_recovers_it(self):
        self.waiting(ISSUE, "a" * 40, "batch")
        granted = self.ledger.acquire("unity_slot", owner="pool", host="mac")
        self.ledger.hold(granted["reservation_id"], "quiescence probe failed")
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "held")
        self.waiting(OTHER, "b" * 40, "batch")
        self.assertIsNone(self.ledger.acquire("unity_slot", owner="pool", host="mac"))
        self.ledger.recover_slot("unity_slot:1", "operator closed Unity by hand")
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "idle_closed")
        self.assertIsNotNone(self.ledger.acquire("unity_slot", owner="pool", host="mac"))

    def test_a_reservation_is_listed_for_settlement_once_its_item_is_no_longer_running(self):
        item = self.waiting(ISSUE, "a" * 40, "batch")
        granted = self.ledger.acquire("unity_slot", owner="pool", host="mac")
        self.ledger.resume(item, "granted")
        self.assertEqual(self.ledger.reservations_to_settle(), [])
        token = self.ledger.claim(item, worker_id="w")["token"]
        self.ledger.fail(item, "worker died")
        self.assertEqual([r["reservation_id"] for r in self.ledger.reservations_to_settle()],
                         [granted["reservation_id"]])

    def test_an_identity_observation_is_appended_per_item(self):
        item = self.waiting(ISSUE, "a" * 40, "batch")
        granted = self.ledger.acquire("unity_slot", owner="pool", host="mac")
        self.ledger.record_identity(item, granted["reservation_id"], "unity_slot:1",
                                    {"aggregate": "match", "checks": {"source_commit": "match"}})
        rows = self.ledger.identity_observations(item)
        self.assertEqual(rows[0]["result"]["aggregate"], "match")

    def test_one_hosts_active_reservation_does_not_starve_another_host(self):
        """The Windows plan inherits this file. A pre-check without a host predicate would make one host's
        busy slot block the other host's pool forever."""
        self.ledger.ensure_slot("unity_slot:2", kind="unity_slot", host="win", folder="/e/slot-2")
        self.waiting(ISSUE, "a" * 40, "batch")
        self.waiting(OTHER, "b" * 40, "batch")
        self.assertIsNotNone(self.ledger.acquire("unity_slot", owner="pool", host="mac"))
        second = self.ledger.acquire("unity_slot", owner="pool", host="win")
        self.assertEqual(second["resource"], "unity_slot:2")

    def test_two_connections_acquiring_at_once_produce_exactly_one_grant(self):
        """The 'across processes rather than by convention' claim, tested across two connections rather than
        two calls on one. BEGIN IMMEDIATE takes the write lock for the whole transaction, so the loser reads a
        slot that is already 'switching' and returns None — the unique index never has to fire."""
        self.waiting(ISSUE, "a" * 40, "batch")
        other = Ledger(Path(self.tmp.name) / "l.sqlite3", clock=lambda: self.now, lease_seconds=60)
        self.addCleanup(other.close)
        barrier, results = threading.Barrier(2), []
        lock = threading.Lock()

        def run(ledger):
            barrier.wait()
            granted = ledger.acquire("unity_slot", owner="pool", host="mac")
            with lock:
                results.append(granted)

        threads = [threading.Thread(target=run, args=(l,)) for l in (self.ledger, other)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)
        self.assertEqual(len([r for r in results if r is not None]), 1, results)
        self.assertEqual(len(self.ledger.reservations(("active",))), 1)
```

Add `import sqlite3`, `import threading` and `from pathlib import Path` to the module's imports if they are not there, and extend the existing `from agent.ledger import ...` line with `LedgerError` and `Ledger` if either is missing. `OTHER` is the second issue uuid the module defines; `SELECTED_AT` is the constant Task 1 added.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -W error -m unittest discover -s tests -v -k Reservation`
Expected: FAIL with `AttributeError: 'Ledger' object has no attribute 'ensure_slot'`.

- [ ] **Step 3: Add the three tables**

In `agent/ledger.py`, inside the existing `executescript` block, after the `inbox` table and before `audit`:

```sql
                CREATE TABLE IF NOT EXISTS slots (
                    slot_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    host TEXT NOT NULL,
                    folder TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('idle_closed','idle_open','switching',
                        'interactive_busy','batch_busy','held')),
                    parked_commit TEXT,
                    instance TEXT,
                    mcp_address TEXT,
                    account TEXT,
                    last_switch_at REAL,
                    updated_at REAL NOT NULL,
                    UNIQUE(host, folder)
                );
                CREATE TABLE IF NOT EXISTS reservations (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    reservation_id TEXT NOT NULL UNIQUE,
                    item_id TEXT NOT NULL REFERENCES work_items(id),
                    generation INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    mode TEXT NOT NULL CHECK(mode IN ('interactive','batch')),
                    resource TEXT,
                    host TEXT,
                    commit_sha TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('queued','active','cancel_requested','cancelled','released')),
                    owner TEXT,
                    token_hash TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    acquired_at REAL,
                    released_at REAL,
                    release_reason TEXT,
                    CHECK ((owner IS NULL) = (token_hash IS NULL)),
                    CHECK (state NOT IN ('active','cancel_requested')
                           OR (token_hash IS NOT NULL AND resource IS NOT NULL))
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_owner_per_resource
                    ON reservations(resource)
                    WHERE state IN ('active','cancel_requested');
                CREATE UNIQUE INDEX IF NOT EXISTS one_open_reservation_per_item
                    ON reservations(item_id)
                    WHERE state IN ('queued','active','cancel_requested');
                CREATE INDEX IF NOT EXISTS reservations_queue ON reservations(kind, state, sequence);
                CREATE TABLE IF NOT EXISTS identity_observations (
                    observation_id TEXT PRIMARY KEY,
                    item_id TEXT NOT NULL REFERENCES work_items(id),
                    reservation_id TEXT NOT NULL,
                    slot_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    result_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS identity_by_item ON identity_observations(item_id, created_at);
```

`AUTOINCREMENT` on `sequence` is deliberate and matches `farmqa_controller.py:49`: FIFO must survive row deletion. `one_active_owner_per_resource` is FarmQA's `controller_single_slot` widened from the constant `(1)` to a column, and the second table CHECK is what makes it binding — SQLite treats NULLs as distinct, so without it a `queued` row could reach `active` with a NULL `resource` and the index would never fire.

- [ ] **Step 4: Add the slot methods**

In `agent/ledger.py`, after `set_worker`:

```python
    SLOT_STATES = ("idle_closed", "idle_open", "switching", "interactive_busy", "batch_busy", "held")
    FREE_SLOT_STATES = ("idle_closed", "idle_open")

    def _slot_row(self, slot_id):
        row = self.connection.execute("SELECT * FROM slots WHERE slot_id=?", (slot_id,)).fetchone()
        if row is None:
            raise LedgerError(f"unknown slot: {slot_id}")
        return row

    @staticmethod
    def _slot_view(row):
        return {key: row[key] for key in ("slot_id", "kind", "host", "folder", "state", "parked_commit",
                                          "instance", "mcp_address", "account", "last_switch_at")}

    def ensure_slot(self, slot_id, *, kind, host, folder, instance=None, mcp_address=None, account=None):
        """Upsert a slot from the host configuration. Discovered values are never cleared by a later None."""
        for value, name in ((slot_id, "slot_id"), (kind, "kind"), (host, "host"), (folder, "folder")):
            _text(value, name)
        with self._transaction():
            self.connection.execute(
                """INSERT INTO slots(slot_id,kind,host,folder,state,mcp_address,account,instance,updated_at)
                   VALUES(?,?,?,?,'idle_closed',?,?,?,?)
                   ON CONFLICT(slot_id) DO UPDATE SET kind=excluded.kind, host=excluded.host,
                       folder=excluded.folder,
                       mcp_address=COALESCE(excluded.mcp_address, slots.mcp_address),
                       account=COALESCE(excluded.account, slots.account),
                       instance=COALESCE(excluded.instance, slots.instance),
                       updated_at=excluded.updated_at""",
                (slot_id, kind, host, str(folder), mcp_address, account, instance, self.clock()))
        return self.slot(slot_id)

    def slot(self, slot_id):
        row = self.connection.execute("SELECT * FROM slots WHERE slot_id=?", (slot_id,)).fetchone()
        return self._slot_view(row) if row else None

    def slots(self, kind=None, host=None):
        clauses, values = [], []
        if kind:
            clauses.append("kind=?"); values.append(kind)
        if host:
            clauses.append("host=?"); values.append(host)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return [self._slot_view(row) for row in
                self.connection.execute(f"SELECT * FROM slots{where} ORDER BY slot_id", values)]

    _UNSET = object()

    def set_slot_state(self, slot_id, state, *, parked_commit=_UNSET, instance=_UNSET, last_switch_at=_UNSET):
        if state not in self.SLOT_STATES:
            raise LedgerError(f"slot state must be one of {self.SLOT_STATES}")
        with self._transaction():
            row = self._slot_row(slot_id)
            columns, values = ["state=?"], [state]
            for name, value in (("parked_commit", parked_commit), ("instance", instance),
                                ("last_switch_at", last_switch_at)):
                if value is not self._UNSET:
                    columns.append(f"{name}=?"); values.append(value)
            self.connection.execute(f"UPDATE slots SET {','.join(columns)},updated_at=? WHERE slot_id=?",
                                    (*values, self.clock(), row["slot_id"]))
            self._audit(slot_id, "slot", state)
        return self.slot(slot_id)
```

- [ ] **Step 5: Add the reservation lifecycle**

In `agent/ledger.py`, replace `await_resource` and add the rest after it:

```python
    RESERVATION_OPEN = ("queued", "active", "cancel_requested")

    @staticmethod
    def _reservation_view(row):
        return {key: row[key] for key in ("reservation_id", "sequence", "item_id", "generation", "kind", "mode",
                                          "resource", "host", "commit_sha", "state", "attempts", "created_at",
                                          "acquired_at", "released_at", "release_reason")}

    def await_resource(self, item_id, token, resource, mode):
        _text(resource, "resource")
        if mode not in ("interactive", "batch"):
            raise LedgerError("mode must be interactive or batch")
        with self._transaction():
            row = self._owned(item_id, token)
            target = json.loads(row["target_json"]) if row["target_json"] else None
            if not target or not COMMIT_SHA.match(target.get("commit_sha") or ""):
                raise LedgerError("a resource request needs a pinned commit; this item has none")
            open_row = self.connection.execute(
                f"""SELECT reservation_id FROM reservations WHERE item_id=? AND state IN
                    ({','.join('?' * len(self.RESERVATION_OPEN))})""",
                (row["id"], *self.RESERVATION_OPEN)).fetchone()
            if open_row:
                raise LedgerError("release the resource this item already holds before requesting another")
            reservation_id = str(uuid4())
            self.connection.execute(
                """INSERT INTO reservations(reservation_id,item_id,generation,kind,mode,commit_sha,state,created_at)
                   VALUES(?,?,?,?,?,?,'queued',?)""",
                (reservation_id, row["id"], row["generation"], resource, mode, target["commit_sha"], self.clock()))
            self._set_state(row["id"], "awaiting_resource", f"needs {resource}:{mode}", token=None,
                            lease_expires_at=None, worker_pid=None, needs_resource=f"{resource}:{mode}")
            self._audit(row["id"], "reservation", "queued", details={"reservation_id": reservation_id, "mode": mode})
            return self._view(self._row(row["id"]))

    def acquire(self, kind, *, owner, host):
        """Grant the oldest queued request of this kind a free slot. FIFO by arrival, never by item priority.

        There is deliberately no kind-wide "is anything active?" pre-check. It would read as an optimisation
        and behave as a restriction: with a second slot it would refuse to grant slot 2 while slot 1 was busy,
        and with a second host it would starve one host behind the other. A busy slot is not in
        FREE_SLOT_STATES, which is the whole gate; the partial unique index is the fence behind it. Because
        the body runs inside BEGIN IMMEDIATE, two connections racing here serialize: the loser reads the slot
        this transaction already moved to 'switching' and returns None, so the index never has to fire.
        """
        _text(kind, "kind"); _text(owner, "owner"); _text(host, "host")
        with self._transaction():
            row = self.connection.execute(
                "SELECT * FROM reservations WHERE kind=? AND state='queued' ORDER BY sequence LIMIT 1",
                (kind,)).fetchone()
            if row is None:
                return None
            # Spec §7 scheduling preference: interactive wants an Editor already open, batch wants none.
            order = ("idle_open", "idle_closed") if row["mode"] == "interactive" else ("idle_closed", "idle_open")
            free = [s for s in self.slots(kind=kind, host=host) if s["state"] in self.FREE_SLOT_STATES]
            free.sort(key=lambda s: (order.index(s["state"]), s["slot_id"]))
            if not free:
                return None
            slot = free[0]
            token = "res_" + secrets.token_urlsafe(32)
            self.connection.execute(
                """UPDATE reservations SET state='active',resource=?,host=?,owner=?,token_hash=?,acquired_at=?
                   WHERE reservation_id=?""",
                (slot["slot_id"], host, owner, _hash_token(token), self.clock(), row["reservation_id"]))
            self.connection.execute("UPDATE slots SET state='switching',updated_at=? WHERE slot_id=?",
                                    (self.clock(), slot["slot_id"]))
            self._audit(row["item_id"], "reservation", "acquired",
                        details={"reservation_id": row["reservation_id"], "slot": slot["slot_id"]})
            granted = self._reservation_view(self.connection.execute(
                "SELECT * FROM reservations WHERE reservation_id=?", (row["reservation_id"],)).fetchone())
            granted["folder"] = slot["folder"]
            granted["token"] = token  # the only time the raw token exists outside the pool
            return granted

    def reservation(self, reservation_id):
        row = self.connection.execute("SELECT * FROM reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
        return self._reservation_view(row) if row else None

    def reservations(self, states=None):
        states = tuple(states) if states else None
        sql = "SELECT * FROM reservations"
        values = ()
        if states:
            sql += f" WHERE state IN ({','.join('?' * len(states))})"
            values = states
        return [self._reservation_view(row) for row in self.connection.execute(sql + " ORDER BY sequence", values)]

    def active_reservation(self, item_id):
        row = self.connection.execute(
            "SELECT * FROM reservations WHERE item_id=? AND state IN ('active','cancel_requested')",
            (item_id,)).fetchone()
        return self._reservation_view(row) if row else None

    def reservations_to_settle(self):
        """A slot is only useful to a running worker: anything else is the pool's to probe and release.

        A slot in 'held' is excluded. hold() leaves the reservation 'active' and only changes the slot, so
        without this clause the same reservation would come back on every two-second pool tick: the pool
        would re-probe a slot the operator has been told to recover, write an audit row each time, and — if a
        probe happened to pass later — release and park a slot spec §7 says must wait for recover-slot.
        """
        rows = self.connection.execute(
            """SELECT r.* FROM reservations r JOIN work_items w ON w.id=r.item_id
               LEFT JOIN slots s ON s.slot_id=r.resource
               WHERE r.state IN ('active','cancel_requested')
                 AND COALESCE(s.state,'') <> 'held'
                 AND (r.state='cancel_requested' OR w.state NOT IN ('queued','running','awaiting_resource'))
               ORDER BY r.sequence""")
        return [self._reservation_view(row) for row in rows]

    def _reservation_owned(self, reservation_id, token):
        row = self.connection.execute("SELECT * FROM reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
        if row is None:
            raise LedgerError(f"unknown reservation: {reservation_id}")
        presented = _hash_token(token) if isinstance(token, str) and token.isascii() and token else ""
        if not presented or not secrets.compare_digest(row["token_hash"] or "", presented):
            raise LedgerError("reservation token required")
        return row

    def release(self, reservation_id, token, reason):
        """FarmQA's rule (farmqa_controller.py:139): cancel_requested resolves to cancelled, active to released."""
        _text(reason, "reason")
        with self._transaction():
            row = self._reservation_owned(reservation_id, token)
            if row["state"] in ("released", "cancelled"):
                return row["state"]
            state = "cancelled" if row["state"] == "cancel_requested" else "released"
            self.connection.execute(
                "UPDATE reservations SET state=?,released_at=?,release_reason=? WHERE reservation_id=?",
                (state, self.clock(), reason[:500], reservation_id))
            if row["resource"]:
                self.connection.execute("UPDATE slots SET state='switching',updated_at=? WHERE slot_id=?",
                                        (self.clock(), row["resource"]))
            self._audit(row["item_id"], "reservation", state, details={"reservation_id": reservation_id,
                                                                      "reason": reason[:200]})
            return state

    def hold(self, reservation_id, reason):
        """A failing quiescence probe keeps the reservation open and takes the slot out of the pool (spec §7)."""
        _text(reason, "reason")
        with self._transaction():
            row = self.connection.execute("SELECT * FROM reservations WHERE reservation_id=?",
                                          (reservation_id,)).fetchone()
            if row is None or row["state"] not in ("active", "cancel_requested"):
                raise LedgerError("only an active reservation can be held")
            self.connection.execute("UPDATE slots SET state='held',updated_at=? WHERE slot_id=?",
                                    (self.clock(), row["resource"]))
            self._audit(row["item_id"], "reservation", "held", details={"reservation_id": reservation_id,
                                                                       "reason": reason[:200]})
            return self._reservation_view(row)

    def recover_slot(self, slot_id, reason):
        """The operator's way out of held: force-release whatever holds the slot and return it to the pool.

        parked_commit is cleared, not kept. Whatever the slot was doing when it was held, the ledger no longer
        knows what commit the folder is on, and park_idle will not correct it because the state is no longer
        'switching'. A NULL says "unknown", and the pool's next grant does a full switch rather than trusting
        a stale value.
        """
        _text(reason, "reason")
        with self._transaction():
            row = self._slot_row(slot_id)
            self.connection.execute(
                """UPDATE reservations SET state=CASE WHEN state='cancel_requested' THEN 'cancelled' ELSE 'released' END,
                   released_at=?,release_reason=? WHERE resource=? AND state IN ('active','cancel_requested')""",
                (self.clock(), f"recover-slot: {reason}"[:500], slot_id))
            self.connection.execute(
                "UPDATE slots SET state='idle_closed',parked_commit=NULL,updated_at=? WHERE slot_id=?",
                (self.clock(), slot_id))
            self._audit(slot_id, "slot", "recovered", details={"reason": reason[:200]})
            return self._slot_view(self._slot_row(row["slot_id"]))

    def cancel_reservations(self, item_id, reason):
        """Stop: queued requests die, an active one keeps the slot and is marked for the probe (spec §7)."""
        _text(reason, "reason")
        with self._transaction():
            self.connection.execute(
                """UPDATE reservations
                   SET state=CASE WHEN state='queued' THEN 'cancelled' ELSE 'cancel_requested' END,
                       released_at=CASE WHEN state='queued' THEN ? ELSE released_at END,
                       release_reason=CASE WHEN state='queued' THEN ? ELSE release_reason END
                   WHERE item_id=? AND state IN ('queued','active')""",
                (self.clock(), reason[:500], item_id))
            self._audit(item_id, "reservation", "cancel requested", details={"reason": reason[:200]})
        return [r for r in self.reservations() if r["item_id"] == item_id]

    def record_identity(self, item_id, reservation_id, slot_id, result):
        observation_id = str(uuid4())
        with self._transaction():
            self.connection.execute(
                """INSERT INTO identity_observations(observation_id,item_id,reservation_id,slot_id,created_at,result_json)
                   VALUES(?,?,?,?,?,?)""",
                (observation_id, item_id, reservation_id, slot_id, self.clock(), _json(result)))
            self._audit(item_id, "identity", str(result.get("aggregate", "unknown")))
        return observation_id

    def identity_observations(self, item_id):
        rows = self.connection.execute(
            "SELECT * FROM identity_observations WHERE item_id=? ORDER BY created_at", (item_id,))
        return [{"observation_id": r["observation_id"], "reservation_id": r["reservation_id"],
                 "slot_id": r["slot_id"], "created_at": r["created_at"],
                 "result": json.loads(r["result_json"])} for r in rows]
```

In `cancel`, add the reservation sweep inside the existing transaction, immediately before `_set_state`, so the operator CLI path cannot orphan a slot:

```python
            self.connection.execute(
                """UPDATE reservations
                   SET state=CASE WHEN state='queued' THEN 'cancelled' ELSE 'cancel_requested' END,
                       released_at=CASE WHEN state='queued' THEN ? ELSE released_at END,
                       release_reason=CASE WHEN state='queued' THEN ? ELSE release_reason END
                   WHERE item_id=? AND state IN ('queued','active')""",
                (self.clock(), reason[:500], row["id"]))
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 -W error -m unittest discover -s tests -v -k Reservation`
Expected: PASS, twelve tests.

- [ ] **Step 7: Run the whole suite and commit**

Run: `python3 -W error -m unittest discover -s tests`
Expected: OK, 159 tests (+12 in `tests/test_ledger.py`).

```bash
git add agent/ledger.py tests/test_ledger.py
git commit -m "feat(ledger): slots, reservations and identity observations

Ports the FarmQA controller queue onto work items: FIFO by arrival, hashed owner
token, and one active owner per resource enforced by a partial unique index rather
than by convention. await-resource now enqueues the request in the same
transaction that parks the item, and cancelling an item cancels its reservations.

Co-Authored-By: <model> <noreply@anthropic.com>"
```

---

### Task 3: Slot 1 exists as a detached worktree with real binaries

Create the slot folder itself. A slot is a long-lived worktree of FarmBot's own bare clone under `.local/editors/slot-<n>`, detached at a commit, with its LFS objects materialized. Copy nothing from `Worktrees.add_detached`: that helper hardcodes `<worktrees_root>/<item_id>/<repo>` and runs under `GIT_LFS_SKIP_SMUDGE=1`.

**Why:** `agent/worktrees.py:11` sets `GIT_ENV = {"GIT_LFS_SKIP_SMUDGE": "1", ...}` and `_git` applies it to every call in the module, deliberately — task worktrees are for code and Farm-Client carries gigabytes of binaries. FarmBot's bare clone therefore holds 176 KB of LFS objects against 3.6 GB in the human's checkout. A slot built through the existing helper would hand Unity **3,778** pointer files (Task 0 Step 1's count at `origin/main`; an earlier draft said 3,619, measured at an older commit), and the import would fail in a way that reads as project corruption rather than as a configuration mistake.

**LFS strategy, settled by measurement.** Task 0 Step 2 decided it: **fetch over the network**, `git lfs fetch origin HEAD` then `git lfs checkout` — **36.2 s for 918 MB** plus **5.3 s** to check out, non-interactive under `GIT_TERMINAL_PROMPT=0` with the credential supplied by `osxkeychain`, exit 0, and `Assets/DOTween/DOTween.dll` goes from `vers` to `MZ`. The fetch also populates the **bare clone's shared** object store (176 KB → 880 MB), so only the first slot on a host pays the download. The rsync-seed alternative was deliberately not measured and is not a fallback: it moves ~3.6 GB of LFS *history* to obtain the same 918 MB of HEAD objects. What survives from that question is the error message: `materialize` must still tell a **credentials** failure (401 or `Authorization`) from a **reachability** failure (connection refused or timeout), because those are the operator's two different problems.

**Design:** `_git` takes its environment as an argument with the pointer-preserving map as the default, and a second map without `GIT_LFS_SKIP_SMUDGE` is used for slot calls only. Slot creation then has four parts that each fail loudly: `git worktree add --detach`, `git lfs fetch` plus `git lfs checkout`, a sampled check that no tracked LFS file still begins with `version https://git-lfs`, and `git update-index --skip-worktree` on the files Unity regenerates per folder. `SlotPool.ensure` is called once at service start, is idempotent, and never disturbs a slot that is busy, switching or held — a restart must not move a folder an Editor has open. The test fixture's origin has no LFS objects at all, so these tests also pin the rule that a zero-object fetch is success rather than an error.

**Why the fourth part exists.** Task 0 Step 3 found that **`.vscode/settings.json` is rewritten on every Editor run**: Unity names the generated solution after the *project folder*, so `"dotnet.defaultSolution": "Farm-Client.slnx"` becomes `"slot-1.slnx"` the first time a slot opens, and would become `"slot-2.slnx"` in the next slot. It is a tracked file, so the slot switch's "confirm tracked files are clean" precondition in Task 4 would fail on **every** switch, permanently and for a reason that looks like a human edited the folder. This is not `-accept-apiupdate`; that flag was not used. Marking the path `skip-worktree` at slot-creation time is the narrow fix: git stops comparing it, so it can never dirty the slot and can never ride into a commit, while the clean check stays meaningful for every other tracked file.

**One-time provisioning this task does not do.** `/HybridCLRData/` is gitignored and is 1.9 GB in the human's checkout, and HybridCLR is what produces `Library/ScriptAssemblies/HotUpdate.dll`, which is the identity probe's whole subject. Task 0 Step 3 answered this on the Mac: a fresh slot produced `HotUpdate.dll` at 7,211,008 bytes with **no** installer warning and **without** `HybridCLRData` being generated first, so no generator invocation is added to `SlotPool.ensure` here and the commit body says so. The Windows host re-answers it from its own spike run; if that host needs the generator, it is added here, guarded so it runs only when `HybridCLRData` is absent.

**Files:**
- Modify: `agent/config.py` (`Paths.editors`)
- Modify: `agent/worktrees.py` (`_git` environment argument, `add_slot`, `checkout_commit`, `materialize`, `skip_generated`, `slot_clean`, `pointers_remain`)
- Create: `agent/slots.py` (`SlotError`, `SlotPool.__init__`, `SlotPool.ensure`)
- Modify: `docs/superpowers/specs/2026-09-17-farm-linear-agent-design.md` (§7, the slot-1 sentence)
- Test: `tests/test_worktrees.py`, `tests/test_slots.py`

**Interfaces:**
- Consumes: `Worktrees.ensure_clone(repo)`, `Worktrees.resolve_commit(repo, ref=None)` from Task 1, `Ledger.ensure_slot` and `Ledger.set_slot_state` from Task 2.
- Produces:
  - `agent.config.Paths.editors`, `Path(config.local_root) / "editors"`.
  - `Worktrees.add_slot(repo, path, commit) -> Path`, creating the worktree if absent, materializing LFS either way, and marking the per-folder generated files `skip-worktree`.
  - `Worktrees.checkout_commit(path, commit) -> str`, `Worktrees.materialize(path) -> str`, `Worktrees.skip_generated(path) -> list[str]`, `Worktrees.slot_clean(path) -> bool`, `Worktrees.pointers_remain(path) -> bool`.
  - `agent.slots.SlotPool(ledger, worktrees, entries, *, host, editors_root, unity=None, mcp=None, clock=time.time)`. `entries` is `config.slots` after defaults are applied by `agent.slots.slot_entry(raw)`.
  - `SlotPool.ensure() -> list[dict]`, the slot views after registration. Raises `SlotError` when a slot folder cannot be made usable, naming which of the four parts failed.

- [ ] **Step 1: Write the failing tests**

In `tests/test_worktrees.py`, inside `WorktreeTests`:

```python
    def test_a_slot_worktree_is_detached_at_the_commit_and_lives_where_it_is_told(self):
        # Unity rewrites .vscode/settings.json with the *folder* name on every Editor run (Task 0 Step 3), so
        # the slot must stop tracking it or every switch fails its clean check. The file is created on the
        # origin here because the fixture's repository does not carry one.
        (self.origin / ".vscode").mkdir()
        (self.origin / ".vscode" / "settings.json").write_text(
            '{"dotnet.defaultSolution": "Farm-Client.slnx"}\n', encoding="utf-8")
        git("add", ".", cwd=self.origin)
        git("commit", "-qm", "vscode", cwd=self.origin)
        commit = self.trees.resolve_commit("Farm-Client")
        slot = Path(self.tmp.name) / "editors" / "slot-1"
        self.assertEqual(self.trees.add_slot("Farm-Client", slot, commit), slot)
        self.assertEqual(git("rev-parse", "HEAD", cwd=slot), commit)
        self.assertEqual(git("symbolic-ref", "-q", "HEAD", cwd=slot, allow_failure=True), "")
        self.assertTrue(self.trees.slot_clean(slot))
        # lowercase 'h' in ls-files -v is the skip-worktree bit.
        self.assertIn("h .vscode/settings.json", git("ls-files", "-v", ".vscode/settings.json", cwd=slot))
        (slot / ".vscode" / "settings.json").write_text('{"dotnet.defaultSolution": "slot-1.slnx"}\n',
                                                        encoding="utf-8")
        self.assertTrue(self.trees.slot_clean(slot))   # the Editor's rewrite no longer dirties the slot

    def test_slot_git_runs_without_the_pointer_preserving_environment(self):
        seen = []
        real = subprocess.run

        def record(args, **kwargs):
            seen.append((args[1] if args[0] == "git" else args[0], dict(kwargs.get("env") or {})))
            return real(args, **kwargs)

        commit = self.trees.resolve_commit("Farm-Client")
        # Both calls must be inside the patch, or the second list is empty and the comparison is [] == ['1'].
        with patch("agent.worktrees.subprocess.run", record):
            self.trees.add_slot("Farm-Client", Path(self.tmp.name) / "editors" / "slot-2", commit)
            slot_calls = [env for name, env in seen if name == "worktree"]
            self.trees.add("Farm-Client", "item-1", "farmbot/x")
            task_calls = [env for name, env in seen if name == "worktree"][len(slot_calls):]
        self.assertTrue(slot_calls and task_calls)
        # _git merges os.environ, so assert the *value*: key absence would depend on the developer's shell.
        self.assertEqual({env.get("GIT_LFS_SKIP_SMUDGE") for env in slot_calls}, {None})
        self.assertEqual({env.get("GIT_LFS_SKIP_SMUDGE") for env in task_calls}, {"1"})
```

`git(...)` in this module is the existing helper; give it an `allow_failure=False` keyword so the detached-HEAD assertion can read an empty string instead of raising. Add `from unittest.mock import patch` and `import subprocess` to the module if missing.

Create `tests/test_slots.py`:

```python
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent.ledger import Ledger
from agent.slots import SlotError, SlotPool, slot_entry
from agent.worktrees import Worktrees


def git(*args, cwd):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=str(cwd), check=True,
                   capture_output=True)


class SlotFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.origin = self.root / "origin"
        self.origin.mkdir(parents=True)
        git("init", "-q", "-b", "main", ".", cwd=self.origin)
        (self.origin / "README.md").write_text("client", encoding="utf-8")
        git("add", ".", cwd=self.origin); git("commit", "-qm", "init", cwd=self.origin)
        self.trees = Worktrees(self.root / "repos", self.root / "worktrees", {"Farm-Client": str(self.origin)})
        self.now = 1000.0
        self.ledger = Ledger(self.root / "ledger.sqlite3", clock=lambda: self.now, lease_seconds=60)
        self.addCleanup(self.ledger.close)
        self.entry = slot_entry({"id": "unity_slot:1", "repo": "Farm-Client"})

    def advance(self, seconds):
        """The injected sleep, wired into pool() by Task 4 when SlotPool.__init__ grows a `sleep` argument.
        The fixture's clock is a variable, so a pool loop that waits on a deadline makes progress instead of
        spinning forever under a constant clock."""
        self.now += seconds

    def pool(self, worktrees=None, **kwargs):
        # Task 4 extends this with the collaborators its constructor adds (sleep, editor_scan, editor_pid);
        # here it passes only what Task 3's constructor accepts, or every case below raises TypeError.
        return SlotPool(self.ledger, worktrees or self.trees, [self.entry], host="test",
                        editors_root=self.root / "editors", clock=lambda: self.now, **kwargs)

    def commit(self, message="more"):
        (self.origin / f"{message}.txt").write_text(message, encoding="utf-8")
        git("add", ".", cwd=self.origin); git("commit", "-qm", message, cwd=self.origin)
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.origin, capture_output=True,
                              text=True, check=True).stdout.strip()


class EnsureTests(SlotFixture):
    def test_ensure_registers_the_slot_and_creates_its_folder_parked_on_main(self):
        head = self.trees.resolve_commit("Farm-Client")
        [slot] = self.pool().ensure()
        # The default folder is slot-<n>, from the id's suffix. Every other assertion in this file, the
        # Architecture paragraph and Task 11's `du -sh .local/editors/slot-1` all depend on this one name.
        self.assertEqual(slot["folder"], str(self.root / "editors" / "slot-1"))
        self.assertEqual((slot["state"], slot["parked_commit"]), ("idle_closed", head))
        self.assertTrue((self.root / "editors" / "slot-1" / "README.md").is_file())

    def test_ensure_is_idempotent_and_never_disturbs_a_busy_slot(self):
        for state in SlotPool.BUSY:  # held is the one where moving the folder would do the most damage
            with self.subTest(state=state):
                self.pool().ensure()
                self.ledger.set_slot_state("unity_slot:1", state)
                self.commit(f"later-{state}")
                [slot] = self.pool().ensure()
                self.assertEqual(slot["state"], state)
                self.ledger.set_slot_state("unity_slot:1", "idle_closed")

    def test_a_slot_whose_binaries_are_still_pointers_is_refused_by_name(self):
        class Pointers(Worktrees):
            def pointers_remain(self, path):
                return True

        trees = Pointers(self.root / "repos", self.root / "worktrees", {"Farm-Client": str(self.origin)})
        pool = SlotPool(self.ledger, trees, [self.entry], host="test", editors_root=self.root / "editors",
                        clock=lambda: self.now)
        with self.assertRaises(SlotError) as caught:
            pool.ensure()
        self.assertIn("lfs", str(caught.exception).lower())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -W error -m unittest discover -s tests -v -k slot`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent.slots'` and, in `tests/test_worktrees.py`, `AttributeError: 'Worktrees' object has no attribute 'add_slot'`. Note that `-k slot` already matches one pre-existing test today (`test_await_resource_is_refused_without_slots_and_errors_are_clean`, which Task 8 replaces), so the selection is six here, not five.

- [ ] **Step 3: Give the worktrees a slot-shaped git**

In `agent/worktrees.py`, replace the environment constant and `_git`, then add the slot methods after `add_detached`:

```python
# Task worktrees are for code: LFS pointers stay pointers (Farm-Client carries gigabytes of binaries), and a
# missing credential fails at once instead of waiting on a prompt no one will answer.
GIT_ENV = {"GIT_LFS_SKIP_SMUDGE": "1", "GIT_TERMINAL_PROMPT": "0"}
# A slot is what Unity opens, so its binaries must be real files. Smudge stays on for every slot call (spec §7).
SLOT_ENV = {"GIT_TERMINAL_PROMPT": "0"}
LFS_POINTER = b"version https://git-lfs"


def _git(*args, cwd, env=GIT_ENV, timeout=600):
    result = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
                            env={**os.environ, **env})
    if result.returncode:
        raise WorktreeError(f"git {args[0]} failed: {result.stderr.strip()[:500]}")
    return result.stdout.strip()
```

Task 1 already added the `timeout` keyword; this task adds `env`. Both default to today's behaviour, so no existing call site changes.

```python
    def add_slot(self, repo, path, commit):
        """A slot is a long-lived detached worktree whose binaries are files, not pointers (spec §7, §8)."""
        if not self.COMMIT.match(commit or ""):
            raise WorktreeError("a slot is only ever checked out at a full commit")
        clone = self.ensure_clone(repo)
        path = Path(path)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            _git("worktree", "add", "--quiet", "--detach", str(path), commit, cwd=clone, env=SLOT_ENV, timeout=7200)
        self.materialize(path)
        self.skip_generated(path)
        return path

    def checkout_commit(self, path, commit):
        if not self.COMMIT.match(commit or ""):
            raise WorktreeError("a slot is only ever moved to a full commit")
        _git("checkout", "--detach", "--force", commit, cwd=path, env=SLOT_ENV, timeout=3600)
        self.materialize(path)
        self.skip_generated(path)   # idempotent; a forced checkout is the one thing that could drop the bit
        return commit

    def materialize(self, path):
        """Fetch then check out the LFS objects this checkout needs. The store is shared with every other
        worktree of the same clone, so only the first slot pays the full download.

        There is no --quiet: git-lfs has no per-command quiet flag, and `git lfs fetch --quiet` exits non-zero
        with `Error: unknown flag: --quiet` (verified against git-lfs/3.7.1). Since _git raises on any non-zero
        return, passing it would make every call to materialize fail, which is every slot this plan creates.
        Output is captured by _git already; GIT_LFS_PROGRESS silences the progress meter if it ever matters.
        A repository with no LFS objects fetches zero and succeeds, which is what the test fixture's origin is.
        """
        if shutil.which("git-lfs") is None:
            return "git-lfs is not installed"
        try:
            _git("lfs", "fetch", "origin", "HEAD", cwd=path, env=SLOT_ENV, timeout=7200)
        except WorktreeError as exc:
            # Task 3's error must tell the operator which of the two problems they have (see the operator
            # items): a 401/Authorization failure is a missing credential for git.kuaiwa.com, anything else
            # is reachability. GIT_TERMINAL_PROMPT=0 turns the first into an error instead of a hung prompt.
            kind = "credentials" if re.search(r"401|Authoriz|credential", str(exc), re.I) else "reachability"
            raise WorktreeError(f"git lfs fetch failed ({kind}): {exc}") from exc
        _git("lfs", "checkout", cwd=path, env=SLOT_ENV, timeout=3600)
        return "materialized"

    # Tracked files Unity regenerates per *folder*, so they differ in every slot and in none of them is the
    # difference a change anyone wants. Task 0 Step 3: the Editor rewrites .vscode/settings.json on every run
    # because the generated solution is named after the project folder (Farm-Client.slnx -> slot-1.slnx),
    # which would fail Task 4's clean-tree precondition on every switch, for ever.
    GENERATED_PER_FOLDER = (".vscode/settings.json",)

    def skip_generated(self, path):
        """Mark the per-folder generated files skip-worktree so they can neither dirty the slot nor be
        committed. Idempotent, and a file the repository does not carry is skipped rather than an error —
        git update-index refuses an unknown path, and the Windows host's list may differ."""
        marked = []
        for name in self.GENERATED_PER_FOLDER:
            if not (Path(path) / name).exists():
                continue
            _git("update-index", "--skip-worktree", "--", name, cwd=path, env=SLOT_ENV)
            marked.append(name)
        return marked

    def slot_clean(self, path):
        """Tracked files only: Library/, Temp/ and Logs/ are Unity's and are never part of the check (spec §7)."""
        return _git("status", "--porcelain=v1", "--untracked-files=no", cwd=path, env=SLOT_ENV) == ""

    def pointers_remain(self, path):
        """One sample of the tracked LFS files proves whether smudge really ran before Unity opens the folder."""
        if shutil.which("git-lfs") is None:
            return False
        names = _git("lfs", "ls-files", "--name-only", cwd=path, env=SLOT_ENV).splitlines()[:20]
        for name in names:
            candidate = Path(path) / name
            if candidate.is_file():
                with candidate.open("rb") as handle:
                    if handle.read(len(LFS_POINTER)) == LFS_POINTER:
                        return True
        return False
```

`COMMIT` is the class attribute Task 1 added. `shutil` is already imported by this module.

- [ ] **Step 4: Add the editors root**

In `agent/config.py`, inside `Paths.__init__`:

```python
        self.editors = Path(config.local_root) / "editors"
```

- [ ] **Step 5: Write the pool's registration half**

Create `agent/slots.py`:

```python
"""The Unity slot pool: long-lived project folders Unity is pointed at, and nothing else (spec §7).

A slot is a detached worktree of FarmBot's own clone with a built Library/. Task worktrees are for code and
never open Unity. Everything host-specific lives in the slot's configuration entry or in agent/unity.py.
"""
from pathlib import Path
import time

from .worktrees import WorktreeError

DEFAULTS = {
    "kind": "unity_slot",
    "repo": "Farm-Client",
    # The plugin's own default local base URL; per-slot addressing is set_active_instance, not a second port.
    "mcp_address": "http://127.0.0.1:8080/mcp",
    "folder": None,          # defaults to <editors_root>/slot-<the id's suffix>, e.g. .local/editors/slot-1
    "instance": None,        # the Editor instance id Name@hash; discovered on the first interactive attach
    "unity": None,           # absolute Editor path; discovered from the project version when absent
    "build_target": None,    # the enum name the identity probe expects, e.g. StandaloneOSX or Android
    "build_target_argument": None,   # the -buildTarget spelling, e.g. OSXUniversal
    "test_platform": "EditMode",
    "test_assemblies": ("HotUpdate.Tests",),
    "account": None,         # the dedicated 公共测试服 account; supplied by the operator, never by code
}


class SlotError(RuntimeError):
    pass


def slot_entry(raw):
    """Apply defaults to one config slot entry. Unknown keys are refused rather than silently ignored."""
    unknown = set(raw) - set(DEFAULTS) - {"id"}
    if unknown:
        raise SlotError(f"unknown slot configuration keys: {sorted(unknown)}")
    if not isinstance(raw.get("id"), str) or not raw["id"].strip():
        raise SlotError("a slot entry needs an id such as unity_slot:1")
    entry = {**DEFAULTS, **raw}
    entry["test_assemblies"] = tuple(entry["test_assemblies"])
    return entry


class SlotPool:
    BUSY = ("interactive_busy", "batch_busy", "switching", "held")

    def __init__(self, ledger, worktrees, entries, *, host, editors_root, unity=None, mcp=None, clock=time.time):
        self.ledger = ledger
        self.worktrees = worktrees
        self.entries = {entry["id"]: entry for entry in entries}
        self.host = host
        self.editors_root = Path(editors_root)
        self.unity = unity
        self.mcp = mcp
        self.clock = clock

    def folder(self, entry):
        """<editors_root>/slot-<n> for the id unity_slot:<n>. The `folder` key is the escape hatch; the
        default name is pinned by a test, because the spec amendment, Task 11 and every assertion in
        tests/test_slots.py name `slot-1` and a different spelling would silently fail all of them."""
        return Path(entry["folder"]) if entry["folder"] else self.editors_root / f"slot-{entry['id'].split(':', 1)[-1]}"

    def ensure(self):
        """Register every configured slot and make sure its folder is usable. Idempotent, and a restart never
        moves a folder something else is using."""
        views = []
        for slot_id, entry in self.entries.items():
            folder = self.folder(entry)
            record = self.ledger.ensure_slot(slot_id, kind=entry["kind"], host=self.host, folder=folder,
                                             mcp_address=entry["mcp_address"], account=entry["account"],
                                             instance=entry["instance"])
            if record["state"] in self.BUSY:
                views.append(record)
                continue
            fresh = not folder.exists()
            try:
                commit = self.worktrees.resolve_commit(entry["repo"])
                self.worktrees.add_slot(entry["repo"], folder, commit)
            except WorktreeError as exc:
                raise SlotError(f"{slot_id}: could not prepare {folder}: {exc}") from exc
            if self.worktrees.pointers_remain(folder):
                raise SlotError(f"{slot_id}: {folder} still holds git-lfs pointer files; Unity would import "
                                f"them as corrupt assets. Fetch or seed the LFS objects before starting.")
            if fresh or record["state"] == "idle_closed":
                record = self.ledger.set_slot_state(slot_id, "idle_closed", parked_commit=commit)
            views.append(record)
        return views
```

- [ ] **Step 6: Amend the spec**

In `docs/superpowers/specs/2026-09-17-farm-linear-agent-design.md`, §7, replace the sentence reading `Slot 1 is FarmQA's existing isolated client copy on the Windows host.` with:

```markdown
Slot 1 is built fresh on whichever host runs FarmBot first: a detached worktree of FarmBot's own clone
under `.local/editors/slot-1`, with its LFS objects materialized and its `Library/` imported once. Measured
on the Mac on 2026-09-19: about 45 seconds for the tree and its 918 MB of LFS objects, then 170 seconds and
5.1 GB for the first import — roughly six gigabytes and three and a half minutes in all. FarmQA's isolated
client copy on the Windows host is a candidate seed for that host's slot, not a prerequisite for slot 1.
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python3 -W error -m unittest discover -s tests -v -k slot`
Expected: PASS, six tests — three in `tests/test_slots.py`, two in `tests/test_worktrees.py`, and the one pre-existing CLI test the substring also matches.

- [ ] **Step 8: Run the whole suite and commit**

Run: `python3 -W error -m unittest discover -s tests`
Expected: OK, 164 tests (+5).

```bash
git add agent/config.py agent/worktrees.py agent/slots.py docs/superpowers/specs/2026-09-17-farm-linear-agent-design.md tests/test_worktrees.py tests/test_slots.py
git commit -m "feat(slots): create slot 1 as a detached worktree with real binaries

Task worktrees keep GIT_LFS_SKIP_SMUDGE=1 by design, which would have handed Unity
3,778 pointer files. Slot git now runs with smudge on, fetches and checks out the
LFS objects (36s for 918MB over the LAN, measured), and refuses the folder by name
if any tracked pointer survives. .vscode/settings.json is marked skip-worktree,
because Unity rewrites it with the slot's own folder name on every Editor run and
would fail every later switch's clean check. A fresh slot produces HotUpdate.dll
without HybridCLRData being provisioned first, so no generator step is added. The
spec's claim that slot 1 is the Windows host's existing copy is amended.

Co-Authored-By: <model> <noreply@anthropic.com>"
```

---

### Task 4: The slot switch moves the slot to a commit and parks it back

The heart of the plan. Spec §7, verbatim: confirm tracked files are clean, `git checkout --detach <commit>`, `git lfs checkout`, then in interactive mode ask the Editor to refresh and wait for compilation with zero Console errors. In batch mode the batchmode Editor compiles on start. When a slot becomes idle the pool parks it back on `origin/main`.

**Why:** the switch is the whole reason slots exist: `Library/` is built once and then moved between commits instead of being rebuilt per task. Task 0 Step 5 measured three unfocused forced recompiles at **9.6 / 7.7 / 5.7 seconds** against Step 3's **170-second** first import, which is what makes the design pay.

**Decision this task encodes:** five.

1. **A switch fetches before it checks out.** The pinned commit is not guaranteed to be in FarmBot's bare clone: `enqueue --commit <sha>` lets an operator name any commit, Task 1's receiver pin comes from a bare `ls-remote` that never touched the clone, and minutes to hours pass between the pin and the grant. Without a fetch, `checkout --detach` fails with `fatal: reference is not a tree`, which this plan would classify as a retryable git failure, re-queue once, and then fail the item as a bogus "verification gap". The fetch is the first action of the git stage, so its own failure is retryable, and the tests below depend on it: they create a commit on the fixture origin *after* `ensure()` and expect the switch to reach it.
2. **The pool starts and closes the Editor; nothing else does.** Spec §7's state table says an `idle_closed` slot accepts interactive work, which means something has to start an Editor, and that a batch request on an `idle_open` slot is accepted "after a graceful close". With exactly one slot, the graceful close is not an edge case — it is the required path for every batch run that follows an interactive one. Without `open_editor`, an interactive grant on a closed slot would refresh and probe an MCP server with no Editor behind it, fail at the probe stage, hold the slot and fail the item. Without `close_editor`, the ledger would say the slot is free while a real Editor still held `Temp/UnityLockfile`, and the next batch run would start `Unity -batchmode` on a locked folder. Both directions get a test.

   **The close is a signal, not a request, and it cleans up after Unity.** Task 0 Step 5 measured all three parts of this and each one contradicts what the plan first assumed. `EditorApplication.Exit(0)` through `execute_code` returned `"exiting"` in 0.1 s and **did not close the Editor** — it was still running and fully responsive two minutes later, so it cannot be the mechanism. `SIGTERM` on the Editor pid works and the process is gone in **1 s**. Unity leaves `Temp/UnityLockfile` behind even on an ordinary close — it was still present at **+32 s** — so the lockfile must be removed by hand, and an earlier draft's "wait for the lockfile to disappear" would have hung for ever, on every close, with no diagnostic. And the `uvx` MCP server **outlives the Editor**: it kept port 8080 bound and still answered HTTP 200 after the Editor was gone, so it must be reaped from `<slot>/Library/MCPForUnity/RunState/mcp_http_8080.pid` or the next `open_editor` meets a stale server that answers for an Editor that no longer exists.
3. **The interactive switch waits for quiet and reads the Console.** `refresh_unity` returns immediately; probing straight afterwards normally observes `compilation.is_compiling` or `is_domain_reload_pending` and aggregates to `unknown`, which this plan routes to a permanent hold. So a `wait_for_quiet` sits between them, and a compile-error check sits after it with its **own** stage, because a project that fails to compile is the item's problem and must not take the slot out of the pool for a human to recover.
4. **A switch that fails at a git step** releases the reservation and re-queues it once at the tail; a second failure fails the item with the reason recorded as a verification gap, because with one slot an endless retry is an endless wait. A switch that fails at the *probe* never retries: it holds the slot and fails the item, because §7 says a failing probe holds the slot for the operator's `recover-slot` and there is no second slot to try. A `compile` failure fails the item and leaves the slot in the pool.
5. **The switch runs on the pool's own thread**, never inside `Scheduler.tick()` — see Task 6.

**Files:**
- Create: `agent/unity.py`
- Modify: `agent/slots.py` (`SlotPool.switch`, `SlotPool.park`, `SlotPool.open_editor`, `SlotPool.close_editor`, `SlotPool.clear_stale_lock`, `SlotPool.another_editor_running`)
- Test: `tests/test_unity.py`, `tests/test_slots.py`

**Interfaces:**
- Consumes: `Worktrees.fetch`, `Worktrees.slot_clean`, `Worktrees.checkout_commit`, `Worktrees.head`, `Ledger.set_slot_state`.
- Produces:
  - `agent.unity.project_version(project) -> str`, read from `ProjectSettings/ProjectVersion.txt`.
  - `agent.unity.candidates(version, system=None) -> list[Path]`, the same probe order Farm-Client's `pack.sh` uses per platform.
  - `agent.unity.editor_path(project, override=None, system=None, exists=None, environ=None) -> Path`, raising `UnityError` naming every path tried. `environ` defaults to `os.environ` and is injectable so a developer with `FARMBOT_UNITY_EXE` exported cannot make the discovery tests pass or fail for the wrong reason.
  - `agent.unity.read_results(path) -> dict`, the batch run's only evidence: `{"total", "passed", "failed", "result"}` read from the test-framework XML's root element, raising `UnityError` when the file is missing, unparseable or carries no `total`. It exists because Task 0 Step 4 measured the exit code as actively misleading — exit 0 means *nothing ran* — so the contract has to be executable rather than described, and Task 7's pool, Task 11's checker and the worker's own report all read the same function's answer.
  - **`agent.unity.writable_roots()` is removed, not repurposed.** It existed to widen the *worker's* seatbelt so a Unity child of the worker could write the UPM cache, the Editor log, EditorPrefs and the licence files. Task 0's addendum voided that job: the worker never starts Unity at all now, and the launcher's invocation is unsandboxed and needs no roots. Keeping the function would leave a list nothing relies on and a test asserting a property nothing checks — and the addendum says so plainly: "its list is no longer load-bearing for correctness". The Windows host's `%LOCALAPPDATA%\Unity` is therefore not a configuration key this plan owes; if a future phase ever sandboxes the batch run again, it is re-derived then, against whatever policy language can express Mach lookups.
  - `agent.unity.other_editor_project(folder, system=None, run=None) -> str | None` and `agent.unity.editor_holds_project(folder, system=None, run=None) -> int | None`, the two halves of one process listing: an Editor on some *other* folder, and the **pid** of the one holding *this* folder. Both take an injectable `run`, so no test shells out to `pgrep`.
  - `agent.unity.batch_test_command(editor, project, *, results, log, test_platform="EditMode", assemblies=(), build_target=None) -> list[str]`. No `-quit` and no `-nographics` (spec §8), and no `-accept-apiupdate` either: spec §8 does not list it, and it authorises the API Updater to rewrite scripts under `Assets/`, which would leave tracked files modified and make the *next* switch fail its clean check — an intermittent failure whose cause is two runs earlier. **Task 7's pool calls this function and `agent/launcher.py` runs the argv outside the worker's sandbox; the worker never receives it and never composes one**, so no host-specific flag is ever spelled out in prose and no value a worker supplied ever reaches an unsandboxed command line.
  - `SlotPool.switch(slot_id, commit, mode) -> dict`, the slot view. Raises `SlotError` with `.stage` set to `"git"`, `"editor"`, `"compile"` or `"probe"` so the caller can apply the right rule.
  - `SlotPool.open_editor(slot, entry) -> str`, starting `Unity -projectPath <folder>` and waiting until the MCP endpoint answers and reports this instance; returns the discovered instance id. Raises `SlotError(stage="editor")` on timeout. The timeout is **120 s** against Task 0 Step 5's measured **16 s** cold start — generous headroom over a measured number, not a guess.
  - `SlotPool.close_editor(slot) -> bool`, in three parts, all of them required by Task 0 Step 5: `SIGTERM` the Editor pid and confirm the process is gone (1 s measured), remove `Temp/UnityLockfile` **by hand** because Unity leaves it behind on an ordinary close, and reap the `uvx` MCP server named in `<slot>/Library/MCPForUnity/RunState/mcp_http_<port>.pid`, whose `<port>` comes from the slot's configured `mcp_address` (8080 on this Mac) and which otherwise keeps that port bound and answering. Raises `SlotError(stage="editor")` if the Editor process survives the SIGTERM window. It never waits for the lockfile to disappear on its own, because it never does.
  - `SlotPool.wait_for_quiet(slot, timeout) -> None`, polling `mcpforunity://editor/state` until compilation, domain reload, asset import and test running are all false. Raises `SlotError(stage="probe")` on timeout.
  - `SlotPool.park(slot_id, mode) -> dict`, moving the slot to the repo's remote default head and recording `parked_commit`. The departing mode decides the parked state, so it is passed in rather than read back from a slot row the release has already overwritten.
  - `SlotPool.clear_stale_lock(folder, alive) -> bool`, removing `Temp/UnityLockfile` only when `alive()` says the process is gone (spec §7). Called from the batch branch of `switch`, from `park` on the closed path and from `settle` in Task 6, and tested in all three branches; not the dead, unverified code it would otherwise be.
  - `SlotPool.editor_is_open(slot) -> bool`, whether a **live Unity process** holds the slot folder. It is a process question, never a file question: Task 0 Step 5 found `Temp/UnityLockfile` still present 32 seconds after an ordinary close, so the lock file is not a liveness marker and a stale one would make `switch` skip `open_editor` and refresh an endpoint with no Editor behind it — a probe-stage hold on the only slot. The answer comes from `agent.unity.editor_holds_project(folder)`, injected as `editor_pid` for the same reason `editor_scan` is.
  - `SlotPool.another_editor_running(folder) -> str | None`, the cheap preflight: a Unity process on this host whose command line names a *different* project folder. It survives the spike because contention is the one licensing question Task 0 Step 6 could **not** exercise — no operator Editor was open — and until it is, refusing to start a second Editor is cheaper than discovering what happens.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_unity.py`:

```python
import tempfile
import unittest
from pathlib import Path

from agent.unity import (UnityError, batch_test_command, candidates, editor_holds_project, editor_path,
                         other_editor_project, project_version, read_results)


class UnityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name) / "slot-1"
        (self.project / "ProjectSettings").mkdir(parents=True)
        (self.project / "ProjectSettings" / "ProjectVersion.txt").write_text(
            "m_EditorVersion: 2022.3.62f3\nm_EditorVersionWithRevision: 2022.3.62f3 (96770f904ca7)\n",
            encoding="utf-8")

    def test_the_editor_is_found_per_host_from_the_project_version(self):
        self.assertEqual(project_version(self.project), "2022.3.62f3")
        mac = candidates("2022.3.62f3", system="Darwin")[0]
        windows = candidates("2022.3.62f3", system="Windows")
        self.assertTrue(str(mac).endswith("Unity.app/Contents/MacOS/Unity"))
        self.assertTrue(all(str(p).endswith("Editor/Unity.exe") for p in windows), windows)
        found = editor_path(self.project, system="Darwin", exists=lambda p: p == mac)
        self.assertEqual(found, mac)

    def test_an_absent_editor_names_every_path_it_tried(self):
        # environ is injected: an exported FARMBOT_UNITY_EXE would otherwise decide this test's outcome.
        with self.assertRaises(UnityError) as caught:
            editor_path(self.project, system="Windows", exists=lambda p: False, environ={})
        self.assertIn("2022.3.62f3", str(caught.exception))
        self.assertGreaterEqual(str(caught.exception).count("Unity.exe"), 2)

    def test_the_batch_command_never_quits_never_drops_graphics_and_never_rewrites_sources(self):
        command = batch_test_command("/u/Unity", self.project, results=self.project / "r.xml",
                                     log=self.project / "r.log", assemblies=("HotUpdate.Tests",),
                                     build_target="OSXUniversal")
        self.assertEqual(command[0], "/u/Unity")
        self.assertNotIn("-quit", command)              # the test runner exits on its own (spec §8)
        self.assertNotIn("-nographics", command)        # PlayMode fixtures render FairyGUI (spec §8)
        self.assertNotIn("-accept-apiupdate", command)  # would rewrite Assets/ and dirty the next switch
        self.assertEqual(command[command.index("-testPlatform") + 1], "EditMode")
        self.assertEqual(command[command.index("-projectPath") + 1], str(self.project))
        self.assertEqual(command[command.index("-buildTarget") + 1], "OSXUniversal")

    def test_the_results_file_is_the_evidence_and_a_zero_total_is_a_verification_gap(self):
        """Task 0 Step 4, the spike's own "most important answer": exit 0 means *nothing ran* and exit 2
        means tests failed, so the exit code is advisory and the XML is the evidence. The three cases are
        the three the contract has to separate — a real run, an empty run behind a green exit code, and no
        run at all — and each of the three callers (the pool, the checker, the worker's report) asks this
        one function rather than parsing the file again."""
        red = self.project / "red.xml"
        red.write_text('<?xml version="1.0"?><test-run result="Failed(Child)" total="4388" passed="4362" '
                       'failed="26" />', encoding="utf-8")
        self.assertEqual(read_results(red), {"result": "Failed(Child)", "total": 4388, "passed": 4362,
                                             "failed": 26})
        empty = self.project / "empty.xml"
        empty.write_text('<?xml version="1.0"?><test-run result="Passed" total="0" passed="0" failed="0" />',
                         encoding="utf-8")
        # Not an error to read — a green exit code over an empty run is exactly what a wrong -assemblyNames
        # produces, and the caller is the one that must call total == 0 a gap rather than a pass.
        self.assertEqual(read_results(empty)["total"], 0)
        for broken in (self.project / "missing.xml", self.project / "truncated.xml"):
            broken.write_text("<test-run", encoding="utf-8") if broken.name == "truncated.xml" else None
            with self.assertRaises(UnityError):
                read_results(broken)

    def test_another_editor_on_a_different_project_is_seen_and_our_own_is_not(self):
        """The two halves of one process listing, and they must not answer the same question. The first is
        the contention preflight; the second is what `SlotPool.editor_is_open` asks instead of looking at
        Temp/UnityLockfile, which Task 0 Step 5 found still present 32 s after the process was gone."""
        listing = (f"901 /Applications/Unity/Hub/Editor/2022.3.62f3/Unity.app/Contents/MacOS/Unity "
                   f"-projectPath /Users/x/WorkSpaces/Farm/Farm-Client\n")
        self.assertEqual(other_editor_project(self.project, system="Darwin", run=lambda: listing),
                         "/Users/x/WorkSpaces/Farm/Farm-Client")
        mine = f"901 Unity -projectPath {self.project}\n"
        self.assertIsNone(other_editor_project(self.project, system="Darwin", run=lambda: mine))
        self.assertIsNone(other_editor_project(self.project, system="Darwin", run=lambda: ""))
        # editor_holds_project is the complement: our folder, and a pid rather than a bool.
        self.assertEqual(editor_holds_project(self.project, system="Darwin", run=lambda: mine), 901)
        self.assertIsNone(editor_holds_project(self.project, system="Darwin", run=lambda: listing))
        self.assertIsNone(editor_holds_project(self.project, system="Darwin", run=lambda: ""))
```

In `tests/test_slots.py`, first extend `SlotFixture.pool` with the three collaborators this task's
constructor adds, then add the rest:

```python
    def pool(self, worktrees=None, **kwargs):
        # editor_scan and editor_pid are both stubbed: a developer with their own Unity Editor open would
        # otherwise fail every switch test on their machine, and neither test may shell out to pgrep.
        # editor_pid answers from the fake's open_folders, which is what makes it liveness rather than the
        # lock file — the whole point of Task 0 Step 5's +32 s finding.
        mcp = kwargs.get("mcp")
        kwargs.setdefault("editor_scan", lambda folder: None)
        kwargs.setdefault("editor_pid",
                          lambda folder: 4242 if mcp is not None and str(folder) in mcp.open_folders else None)
        return SlotPool(self.ledger, worktrees or self.trees, [self.entry], host="test",
                        editors_root=self.root / "editors", clock=lambda: self.now,
                        sleep=self.advance, **kwargs)
```

```python
INSTANCE = "slot-1@0123456789abcdef"


class FakeMcp:
    """Stands in for the Unity MCP client: records what the pool asked the Editor to do, and stands in for
    the Editor *process* by keeping `open_folders` — which is what the injected `editor_pid` reads, exactly
    as `agent.unity.editor_holds_project` reads the real process listing.

    It also writes the lock file on start and deliberately does **not** remove it on terminate, because the
    real Editor does not either (Task 0 Step 5: still present at +32 s after the process was gone). The two
    are kept apart on purpose: `open_folders` is liveness, the lock file is the litter Unity leaves. The
    pool's own removal of that litter is what the close test proves, and a double that tidied up after
    itself would hide the bug the spike found."""

    def __init__(self, ready=True, quiet=True, console_errors=(), instance=INSTANCE):
        self.ready = ready
        self.quiet = quiet
        self.console_errors = list(console_errors)
        self.instance = instance
        self.calls = []
        self.open_folders = set()

    def _lock(self, slot):
        return Path(slot["folder"]) / "Temp" / "UnityLockfile"

    def start(self, slot, entry):
        self.calls.append(("start", slot["slot_id"]))
        self._lock(slot).parent.mkdir(parents=True, exist_ok=True)
        self._lock(slot).write_text("1", encoding="utf-8")
        self.open_folders.add(slot["folder"])
        return self.instance

    def terminate(self, slot, timeout):
        """SIGTERM and confirm the pid is gone. The lock file is left exactly where Unity leaves it."""
        self.calls.append(("terminate", slot["slot_id"]))
        self.open_folders.discard(slot["folder"])

    def reap_server(self, slot):
        """The uvx MCP server outlives the Editor and keeps port 8080 bound; the pool kills it by pidfile."""
        self.calls.append(("reap_server", slot["slot_id"]))

    def refresh(self, slot):
        self.calls.append(("refresh", slot["slot_id"]))

    def wait_quiet(self, slot, timeout):
        self.calls.append(("wait_quiet", slot["slot_id"]))
        if not self.quiet:
            raise TimeoutError("still compiling")

    def console_errors_since(self, slot, marker):
        self.calls.append(("console", slot["slot_id"]))
        return list(self.console_errors)

    def probe(self, slot, target):
        self.calls.append(("probe", slot["slot_id"]))
        return {"aggregate": "match" if self.ready else "mismatch",
                "checks": {"source_commit": "match" if self.ready else "mismatch"}}

    def quiescent(self, slot, mode):
        self.calls.append(("quiescent", slot["slot_id"]))
        return self.ready


class SwitchTests(SlotFixture):
    def test_a_batch_switch_moves_the_slot_to_the_commit_and_marks_it_busy(self):
        self.pool().ensure()
        commit = self.commit("fix")          # made on the origin *after* ensure: the switch must fetch
        pool = self.pool(mcp=FakeMcp())
        slot = pool.switch("unity_slot:1", commit, "batch")
        self.assertEqual(slot["state"], "batch_busy")
        self.assertEqual(self.trees.head(self.root / "editors" / "slot-1"), commit)
        self.assertEqual(pool.mcp.calls, [])  # a batch run on a closed slot needs no MCP at all (spec §7)

    def test_an_interactive_switch_on_a_closed_slot_starts_the_editor_before_it_probes(self):
        self.pool().ensure()
        commit = self.commit("fix")
        pool = self.pool(mcp=FakeMcp())
        slot = pool.switch("unity_slot:1", commit, "interactive")
        self.assertEqual(slot["state"], "interactive_busy")
        self.assertEqual([name for name, _ in pool.mcp.calls],
                         ["start", "refresh", "wait_quiet", "console", "probe"])
        # The instance the Editor reported is recorded, so the worker and the next probe can address it.
        self.assertEqual(self.ledger.slot("unity_slot:1")["instance"], INSTANCE)

    def test_an_interactive_switch_on_an_open_slot_does_not_restart_the_editor(self):
        self.pool().ensure()
        pool = self.pool(mcp=FakeMcp())
        pool.switch("unity_slot:1", self.commit("one"), "interactive")
        pool.park("unity_slot:1", "interactive")
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "idle_open")
        pool.mcp.calls.clear()
        pool.switch("unity_slot:1", self.commit("two"), "interactive")
        self.assertNotIn("start", [name for name, _ in pool.mcp.calls])

    def test_a_batch_switch_on_an_open_slot_closes_the_editor_gracefully_first(self):
        """All three parts of Task 0 Step 5's close, in one assertion: the Editor is signalled rather than
        asked (EditorApplication.Exit(0) does not close it), the lock file is removed by the *pool* because
        Unity leaves it behind, and the uvx MCP server is reaped because it outlives the Editor and would
        otherwise still be holding port 8080 when the next open_editor runs."""
        self.pool().ensure()
        pool = self.pool(mcp=FakeMcp())
        pool.switch("unity_slot:1", self.commit("one"), "interactive")
        pool.park("unity_slot:1", "interactive")
        pool.mcp.calls.clear()
        pool.switch("unity_slot:1", self.commit("two"), "batch")
        self.assertEqual([name for name, _ in pool.mcp.calls], ["terminate", "reap_server"])
        self.assertFalse((self.root / "editors" / "slot-1" / "Temp" / "UnityLockfile").exists())

    def test_a_dirty_slot_refuses_to_switch_and_says_which_stage_failed(self):
        self.pool().ensure()
        (self.root / "editors" / "slot-1" / "README.md").write_text("edited by hand", encoding="utf-8")
        with self.assertRaises(SlotError) as caught:
            self.pool(mcp=FakeMcp()).switch("unity_slot:1", self.commit("fix"), "batch")
        self.assertEqual(caught.exception.stage, "git")

    def test_a_failing_identity_probe_fails_at_the_probe_stage_not_the_git_stage(self):
        self.pool().ensure()
        with self.assertRaises(SlotError) as caught:
            self.pool(mcp=FakeMcp(ready=False)).switch("unity_slot:1", self.commit("fix"), "interactive")
        self.assertEqual(caught.exception.stage, "probe")

    def test_a_refresh_that_never_goes_quiet_fails_at_the_probe_stage_before_any_probe(self):
        self.pool().ensure()
        pool = self.pool(mcp=FakeMcp(quiet=False))
        with self.assertRaises(SlotError) as caught:
            pool.switch("unity_slot:1", self.commit("fix"), "interactive")
        self.assertEqual(caught.exception.stage, "probe")
        self.assertNotIn("probe", [name for name, _ in pool.mcp.calls])

    def test_a_compile_error_fails_the_item_at_its_own_stage_and_leaves_the_slot_usable(self):
        """spec §7 asks for 'zero Console errors'. A project that does not compile is the item's problem: it
        must not take the one slot out of the pool until a human runs recover-slot."""
        self.pool().ensure()
        pool = self.pool(mcp=FakeMcp(console_errors=["Assets/A.cs(3,1): error CS1002: ; expected"]))
        with self.assertRaises(SlotError) as caught:
            pool.switch("unity_slot:1", self.commit("fix"), "interactive")
        self.assertEqual(caught.exception.stage, "compile")
        self.assertIn("CS1002", str(caught.exception))

    def test_a_stale_lockfile_is_cleared_and_a_live_one_is_kept(self):
        self.pool().ensure()
        lock = self.root / "editors" / "slot-1" / "Temp" / "UnityLockfile"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("1", encoding="utf-8")
        self.assertFalse(SlotPool.clear_stale_lock(lock.parent.parent, lambda: True))
        self.assertTrue(lock.exists())
        self.assertTrue(SlotPool.clear_stale_lock(lock.parent.parent, lambda: False))
        self.assertFalse(lock.exists())
        self.assertFalse(SlotPool.clear_stale_lock(lock.parent.parent, lambda: False))

    def test_park_returns_a_batch_slot_to_main_closed_and_an_interactive_slot_to_main_open(self):
        self.pool().ensure()
        pool = self.pool(mcp=FakeMcp())
        pool.switch("unity_slot:1", self.commit("fix"), "batch")
        main = self.trees.resolve_commit("Farm-Client")
        slot = pool.park("unity_slot:1", "batch")
        self.assertEqual((slot["state"], slot["parked_commit"]), ("idle_closed", main))
        self.assertEqual(self.trees.head(self.root / "editors" / "slot-1"), main)
        pool.switch("unity_slot:1", self.commit("more"), "interactive")
        main = self.trees.resolve_commit("Farm-Client")
        slot = pool.park("unity_slot:1", "interactive")
        # spec §7: "After an interactive run the Editor stays open, parked on main."
        self.assertEqual((slot["state"], slot["parked_commit"]), ("idle_open", main))
```

Three notes. The probe test switches to a commit that is already `origin/main`'s child, so the git stage succeeds and only the probe can fail; that is the point of the assertion. Every test creates its commit on the fixture origin *after* `ensure()`, which is what makes the fetch at the top of `switch` load-bearing rather than decorative. And `park` takes the departing mode as an argument: in production `park` is reached from `park_idle`, by which time `Ledger.release` has already overwritten the slot's state to `switching`, so a ternary on `slot["state"]` would be false every time and `idle_open` would be unreachable — while a unit test that called `park` directly from `batch_busy` would still pass and hide it.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -W error -m unittest discover -s tests -v -k UnityTests -k SwitchTests`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent.unity'` and `AttributeError: 'SlotPool' object has no attribute 'switch'`.

- [ ] **Step 3: Write the Unity module**

Create `agent/unity.py`:

```python
"""Where the Editor is on this host, and how a batch test run is spelled (spec §8).

The probe order is Farm-Client's own pack.sh, so a host that builds by hand and FarmBot agree about which
Editor is in use. This is the only module in agent/ allowed to name an operating system or an install path.
"""
import os
import platform
import re
from pathlib import Path

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
```

`read_results` adds `from xml.etree import ElementTree` to the module's imports and is the whole of the exit-code contract as code: three callers read it — the pool in Task 7, `scripts/check-rehearsal.py` in Task 11, and the `fix` worker itself when it writes its report — and none of them parses the file a second time.

The `-assemblyNames` separator is the one the installed `com.unity.test-framework@1.1.33` documentation gives; Phase 1 passes a single assembly, and Task 0's spike records the observed behaviour if more than one is ever needed.

- [ ] **Step 4: Write the switch and the park**

In `agent/slots.py`, extend `SlotError` and add the methods:

```python
class SlotError(RuntimeError):
    STAGES = ("git", "editor", "compile", "probe")

    def __init__(self, message, stage="git"):
        super().__init__(message)
        # "git" and "editor" may be retried once at the tail of the queue; "compile" fails the item and leaves
        # the slot in the pool; "probe" holds the slot for the operator's recover-slot (spec §7).
        self.stage = stage
```

```python
    BUSY_FOR = {"interactive": "interactive_busy", "batch": "batch_busy"}

    def switch(self, slot_id, commit, mode):
        """Spec §7's sequence, in order. The caller has already marked the slot 'switching' by acquiring it.

        `was_open` therefore cannot come from the slot row — `acquire` has already overwritten its state with
        the transient 'switching'. It is a question about the host: is a Unity process alive on this folder?
        See `editor_is_open` for why it is not a question about Temp/UnityLockfile.
        """
        slot = self.ledger.slot(slot_id)
        if slot is None:
            raise SlotError(f"unknown slot: {slot_id}")
        folder = Path(slot["folder"])
        entry = self.entries.get(slot_id, DEFAULTS)
        was_open = bool(self.mcp is not None and self.editor_is_open(slot))
        other = self.another_editor_running(folder)
        if other:
            # The licence is a paid serial and resolves fine both foreground and under launchd (Task 0 Step
            # 6), but two Editors contending for it was the one case the spike could not run, because the
            # operator had none open. Until that is measured, do not discover it during a graded item: a
            # lost licence race reports as something that reads like project corruption, so hold the slot
            # with a sentence a human can act on instead.
            raise SlotError(f"{slot_id}: another Unity Editor is open on {other}; close it, then "
                            f"`recover-slot --slot {slot_id}`", stage="probe")
        try:
            self.worktrees.fetch(entry["repo"])   # the pin may post-date the clone's last fetch
            if not self.worktrees.slot_clean(folder):
                raise SlotError(f"{slot_id}: tracked files are dirty; a human edited the slot folder")
            if mode == "batch" and was_open:
                # spec §7: a batch request on an idle-open slot is accepted "after a graceful close".
                self.close_editor(slot)
            self.worktrees.checkout_commit(folder, commit)
            if self.worktrees.pointers_remain(folder):
                raise SlotError(f"{slot_id}: git-lfs pointers survived the checkout")
        except WorktreeError as exc:
            raise SlotError(f"{slot_id}: git stage failed: {exc}", stage="git") from exc
        if mode == "batch":
            # A batch run needs no MCP at all; its result is a results file and an exit code (spec §7). The
            # only thing it needs from the pool is a folder no dead Editor still claims.
            self.clear_stale_lock(folder, lambda: False)
            return self.ledger.set_slot_state(slot_id, self.BUSY_FOR[mode], last_switch_at=self.clock())
        instance = slot.get("instance")
        if not was_open:
            instance = self.open_editor(slot, entry)
        try:
            self.mcp.refresh(slot)
            self.wait_for_quiet(slot, entry["quiet_timeout"])
        except SlotError:
            raise
        except Exception as exc:
            raise SlotError(f"{slot_id}: the Editor never went quiet after the refresh: {exc}", stage="probe") from exc
        errors = self.mcp.console_errors_since(slot, commit)
        if errors:
            # Not a probe failure: the slot is fine, the commit does not compile. Keep the slot in the pool.
            raise SlotError(f"{slot_id}: {len(errors)} compile error(s) at {commit[:7]}: {errors[0]}",
                            stage="compile")
        if instance and instance != slot.get("instance"):
            self.ledger.set_slot_state(slot_id, "switching", instance=instance)
            slot = self.ledger.slot(slot_id)
        try:
            observation = self.mcp.probe(slot, {"commit_sha": commit,
                                                "build_target": entry.get("build_target"),
                                                "repository": str(folder)})
        except Exception as exc:
            raise SlotError(f"{slot_id}: identity probe failed: {exc}", stage="probe") from exc
        if observation.get("aggregate") != "match":
            raise SlotError(f"{slot_id}: identity probe did not match: {observation.get('checks')}",
                            stage="probe")
        self.last_observation = observation
        return self.ledger.set_slot_state(slot_id, self.BUSY_FOR[mode], last_switch_at=self.clock())

    def editor_is_open(self, slot):
        """A live Unity process on this folder — never Temp/UnityLockfile.

        The lock file looks like the obvious answer and is the wrong one. Task 0 Step 5 measured it **still
        present at +32 s** after the Editor process was gone, and Task 4's own close_editor exists to delete
        it by hand for exactly that reason. Reading it here would make a stale lock say "open": switch()
        would skip open_editor, refresh() would hit an endpoint with no Editor behind it, wait_for_quiet
        would time out, and the single slot would end held at the probe stage after any crash, unreaped
        close or service restart. `agent.unity.editor_holds_project` asks the process listing instead and
        returns a pid; it is injected as `editor_pid` so no test in this suite shells out to pgrep."""
        return self.editor_pid(slot["folder"]) is not None

    def open_editor(self, slot, entry):
        """Start the Editor and wait until the MCP endpoint answers and names this folder's instance. The
        timeout is the cold-start figure Task 0 Step 5 measured; the returned instance id is what the worker
        pins its own MCP session with."""
        try:
            return self.mcp.start(slot, entry)
        except Exception as exc:
            raise SlotError(f"{slot['slot_id']}: the Editor did not come up: {exc}", stage="editor") from exc

    def close_editor(self, slot, timeout=None):
        """Close the Editor and leave the folder and the port fit for the next run. Three parts, all measured
        in Task 0 Step 5, none of them optional:

        1. SIGTERM the Editor pid. `EditorApplication.Exit(0)` through execute_code is **not** used: it
           returns "exiting" in 0.1 s and the Editor keeps running, fully responsive, indefinitely. The
           signal works and the process was gone in 1 s.
        2. Remove Temp/UnityLockfile by hand, on *every* close and not only after a crash. Unity leaves it
           behind on an ordinary close (still present at +32 s), so there is nothing to wait for — an
           earlier draft of this method waited for it to disappear, which would have hung for ever on every
           batch run that followed an interactive one. Starting `Unity -batchmode` on a folder whose lock
           survives is the corruption the slot design exists to avoid.
        3. Reap the uvx MCP server from <slot>/Library/MCPForUnity/RunState/mcp_http_<port>.pid, where
           <port> is the slot's own mcp_address port (8080 here). It outlives the Editor, keeps that port
           bound and still answers HTTP 200, so the next open_editor would attach to a server whose Editor
           is gone and read somebody else's state — or none.
        """
        timeout = self.entries.get(slot["slot_id"], DEFAULTS)["close_timeout"] if timeout is None else timeout
        folder = Path(slot["folder"])
        try:
            self.mcp.terminate(slot, timeout)
        except Exception as exc:
            raise SlotError(f"{slot['slot_id']}: the Editor did not stop within {timeout}s of SIGTERM: {exc}",
                            stage="editor") from exc
        self.clear_stale_lock(folder, lambda: False)   # the pid is confirmed gone; Unity left the lock behind
        if (folder / "Temp" / "UnityLockfile").exists():
            # Deliberately the file, not editor_is_open: the process question was already answered by
            # terminate(). What is checked here is that the litter Unity leaves is actually gone, because
            # the next `Unity -batchmode` on a folder whose lock survives is the corruption slots prevent.
            raise SlotError(f"{slot['slot_id']}: Temp/UnityLockfile could not be removed", stage="editor")
        try:
            self.mcp.reap_server(slot)
        except Exception as exc:
            # A surviving server is not worth failing the close over, but it must be visible: the next
            # open_editor is the thing that will meet it, and it fails there with a clearer message.
            raise SlotError(f"{slot['slot_id']}: the MCP server on {slot['mcp_address']} outlived the Editor "
                            f"and could not be reaped: {exc}", stage="editor") from exc
        return True

    def wait_for_quiet(self, slot, timeout):
        """spec §7's 'wait for compilation'. refresh_unity returns at once, so probing straight afterwards
        normally observes is_compiling or is_domain_reload_pending and aggregates to unknown — which this plan
        routes to a permanent hold.

        Task 0 Step 5 measured three unfocused forced recompiles at 9.6 / 7.7 / 5.7 s, so the deadline is the
        only thing that decides: a sample is 'stalled' when it crosses the absolute timeout, never because it
        differs from another sample by some ratio. A no-op refresh is legitimately sub-second and would make
        any ratio rule fire on a healthy Editor. App Nap does not stall an unfocused Editor on this Mac."""
        deadline = self.clock() + timeout
        while True:
            try:
                self.mcp.wait_quiet(slot, max(1.0, deadline - self.clock()))
                return
            except TimeoutError:
                if self.clock() >= deadline:
                    raise SlotError(f"{slot['slot_id']}: still not quiet after {timeout}s", stage="probe")
                self.sleep(1.0)

    def another_editor_running(self, folder):
        """A Unity Editor on this host that is not this slot's. Returns the other project path, or None.

        The process listing lives in agent/unity.py, which is the only module allowed to know a host, and the
        whole call is injectable (`editor_scan`) so that no test in this suite shells out to pgrep — a
        developer with their own Editor open would otherwise fail every switch test on their machine.
        """
        return self.editor_scan(folder)

    def park(self, slot_id, mode):
        """When a slot becomes idle it goes back to the remote default head, so Library/ tracks main in small
        steps and the slot is ready for a baseline run (spec §7).

        The departing mode is an argument, not something read back from the slot row: in production park() is
        reached from park_idle(), by which time Ledger.release has already set the slot to 'switching', so a
        ternary on slot['state'] would always choose idle_closed and spec §7's "after an interactive run the
        Editor stays open, parked on main" would be unimplemented — while a unit test calling park() directly
        from batch_busy would still pass and hide it.
        """
        slot = self.ledger.slot(slot_id)
        entry = self.entries.get(slot_id, DEFAULTS)
        folder = Path(slot["folder"])
        try:
            commit = self.worktrees.resolve_commit(entry["repo"])
            if self.worktrees.head(folder) != commit:
                self.worktrees.checkout_commit(folder, commit)
        except WorktreeError as exc:
            raise SlotError(f"{slot_id}: could not park on the default branch: {exc}", stage="git") from exc
        state = "idle_open" if mode == "interactive" and self.editor_is_open(slot) else "idle_closed"
        if state == "idle_closed":
            self.clear_stale_lock(folder, lambda: False)
        return self.ledger.set_slot_state(slot_id, state, parked_commit=commit, last_switch_at=self.clock())

    @staticmethod
    def clear_stale_lock(folder, alive):
        """An Editor that died mid-run leaves Unity's lock file behind; it is removed only after the process is
        confirmed gone (spec §7). Callers: the batch branch of switch(), park() on the closed path, and
        settle() in Task 6 — each of which has already established that no process holds the folder, which is
        why each passes `lambda: False` rather than guessing a second time. Not dead code and not untested."""
        lock = Path(folder) / "Temp" / "UnityLockfile"
        if not lock.exists() or alive():
            return False
        lock.unlink()
        return True
```

Add three arguments to `SlotPool.__init__`: `self.sleep = sleep or time.sleep` (injected so no test waits on a real clock), `self.editor_scan = editor_scan or agent.unity.other_editor_project` and `self.editor_pid = editor_pid or agent.unity.editor_holds_project` (both injected so no test in this suite shells out to `pgrep`; `SlotFixture.pool` stubs the first with `lambda folder: None` and answers the second from `FakeMcp.open_folders`). Add two keys to `DEFAULTS`, both seeded from Task 0 Step 5's measured numbers with headroom: `"quiet_timeout": 300` (measured recompiles 5.7–9.6 s; the 120-second absolute threshold is the stall verdict, and this is the pool's own deadline above it) and `"close_timeout": 60` (the Editor was gone **1 second** after SIGTERM, so a minute is already extravagant — it was 120 when the close was expected to wait on a lockfile that in fact never disappears). In `agent/unity.py`, beside `batch_test_command`:

```python
def _editor_processes(system=None, run=None):
    """(pid, project_path) for every Unity Editor on this host. The only place a host's process-listing
    spelling is written, and `run` is injectable so no test in this suite shells out to pgrep."""
    system = system or platform.system()
    command = (["pgrep", "-fl", "Unity.app/Contents/MacOS/Unity"] if system == "Darwin" else
               ["pgrep", "-fl", "Unity"] if system != "Windows" else
               ["powershell", "-NoProfile", "-Command",
                "(Get-CimInstance Win32_Process | Where-Object Name -eq 'Unity.exe' | "
                "ForEach-Object { \"$($_.ProcessId) $($_.CommandLine)\" })"])
    run = run or (lambda: subprocess.run(command, capture_output=True, text=True, timeout=5).stdout)
    try:
        out = run()
    except (OSError, subprocess.TimeoutExpired):
        return []
    found = []
    for line in (out or "").splitlines():
        match = re.search(r"-projectPath\s+\"?([^\"\s]+)", line)
        pid = re.match(r"\s*(\d+)\b", line)
        if match and pid:
            found.append((int(pid.group(1)), match.group(1)))
    return found


def other_editor_project(folder, system=None, run=None):
    """The project path of a Unity Editor on this host that is not this slot's, or None.

    The licence here is a paid serial, not a Personal seat, and it resolves both foreground and under
    launchd (Task 0 Step 6) — but two Editors contending for it was the one case that spike could not
    exercise, because the operator had none open. A second Editor that loses a licence race reports it as
    something that reads like project corruption. This is the cheap preflight that turns that into a
    sentence, and it is removed when contention has actually been measured."""
    for _, project in _editor_processes(system, run):
        if Path(project).resolve() != Path(folder).resolve():
            return project
    return None


def editor_holds_project(folder, system=None, run=None):
    """The pid of a Unity process that holds *this* folder, or None — `other_editor_project`'s complement
    over the same listing.

    It returns a pid rather than a bool because a caller needs the number: `UnityIdentity.terminate`
    signals it, while `SlotPool.editor_is_open` only asks whether it is None. It exists because
    Temp/UnityLockfile cannot answer the question at all — Task 0 Step 5 found the lock still present 32 s
    after the process was gone, so the file is litter Unity leaves behind, not a liveness marker."""
    for pid, project in _editor_processes(system, run):
        if Path(project).resolve() == Path(folder).resolve():
            return pid
    return None
```

with `import subprocess` added to that module. Both readers go through one listing, so a host's spelling is
written once and a Windows change is a change to `_editor_processes` alone.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -W error -m unittest discover -s tests -v -k UnityTests -k SwitchTests`
Expected: PASS, fifteen tests (five in `tests/test_unity.py`, ten in `tests/test_slots.py`).

- [ ] **Step 6: Run the whole suite and commit**

Run: `python3 -W error -m unittest discover -s tests`
Expected: OK, 179 tests (+15).

```bash
git add agent/unity.py agent/slots.py tests/test_unity.py tests/test_slots.py
git commit -m "feat(slots): the slot switch, the Editor life cycle and the park back to main

Spec section 7's sequence, in order: fetch, confirm clean, close a stale Editor,
checkout --detach, git lfs checkout, and in interactive mode start the Editor if it
is closed, refresh, wait for quiet, read the Console and run the identity probe. The
four failure stages get four different rules: git and editor retry once, compile
fails the item and keeps the slot, probe holds the slot for an operator.
agent/unity.py is now the only module that knows an install path or a host path.

Co-Authored-By: <model> <noreply@anthropic.com>"
```

---

### Task 5: The identity probe proves the Editor loaded the pinned commit

Port `FarmTestAgent/tools/farmqa_unity_identity.py` and `farmqa_identity.py`. Copy the files first, then replace the pieces shown here. Everything not mentioned — the redirect-refusing opener, the empty `ProxyHandler`, the 1 MiB response bound, the `initialize` handshake with `protocolVersion` pinned to `2024-11-05`, the single-matching-id SSE parse, the `_payload` unwrapping — stays verbatim, because it is small, adversarial and already proven against exactly this plugin version.

**Why:** the slot switch moves a folder; only the probe proves that the Editor in front of a worker is that folder, at that commit, in Edit Mode, with the HotUpdate module actually loaded. Without it an interactive run can produce confident evidence about the wrong build, which is worse than no evidence.

**Design:** four changes to the ported code, each because FarmBot is not FarmQA. The endpoint is a constructor argument instead of a hardcoded `9090`, because this Mac's plugin defaults to `8080`. The "exactly one connected instance" rule at `farmqa_identity.py:88-90` becomes "the expected instance id is present", followed by `set_active_instance` and a re-read of `project/info` after selection — the strict rule is baked into four FarmQA call sites and would make a second slot impossible, and would also fail if any other Editor under this user connected to the same server. The expected assembly set stays `{HotUpdate, AOTScripts, Nova.Runtime, MCPForUnity.Editor}` but moves into the probe's arguments **and out of the C# probe**. The copied `editor-readiness.cs.txt` hardcodes the filter (`if (name != "HotUpdate" && name != "AOTScripts" && name != "Nova.Runtime" && name != "MCPForUnity.Editor") continue;`), so passing the set from Python alone would be theatre: the Python side can only compare against what the C# chose to return, and a different project would still need a code change — in the `.cs.txt`. So Step 3 also deletes that `continue` and lets the probe return every loaded assembly, and `collect` does the filtering. That is a two-line change to the copied file and it is what makes the Windows or Android slot genuinely configuration-only. All four assemblies exist in this project today, in `Library/ScriptAssemblies`. `verdict` is no longer the constant `BLOCKED`: the aggregate is `match` only when every check is `match`, and `unknown` is never `match` (`farmqa_request_session.py:138-139`).

**Files:**
- Create: `agent/unity_mcp.py` (from `FarmTestAgent/tools/farmqa_unity_identity.py`)
- Create: `agent/identity.py` (from `FarmTestAgent/tools/farmqa_identity.py`)
- Create: `agent/probes/editor-readiness.cs.txt` (copied from `FarmTestAgent/tests/probes/editor-readiness.cs.txt`)
- Modify: `agent/slots.py` (`UnityIdentity`, the real collaborator the pool's `mcp` argument takes)
- Test: `tests/test_identity.py`

**Interfaces:**
- Consumes: nothing in `agent/`; standard library only.
- Produces:
  - `agent.unity_mcp.UnityMcp(endpoint, timeout=30)` with `read_resource(uri)`, `call_tool(name, arguments)` and `select_instance(instance)`. The constructor raises `ValueError` unless the endpoint is `http`, host exactly `127.0.0.1`, an explicit port, path exactly `/mcp`, and no userinfo, query or fragment.
  - `agent.identity.source_snapshot(repository) -> dict` with `repository`, `commit_sha`, `dirty`, `index_sha256`.
  - `agent.identity.ready(state, instance) -> bool`, the Editor-state gate, unchanged clause for clause.
  - `agent.identity.collect(client, *, repository, instance, expected, probe_source) -> dict` with `checks`, `aggregate`, `observed_at` and the raw samples.
  - `agent.slots.UnityIdentity(probe_path, timeout=120, start_timeout=120, clock=time.time, sleep=time.sleep)`, the pool's `mcp` collaborator in production. The endpoint is **not** a constructor argument: it is read per call from the slot row's `mcp_address`, so one instance serves every slot. It implements the whole surface `SlotPool` and `FakeMcp` share — the probe half (`refresh`, `wait_quiet`, `console_errors_since`, `probe`, `quiescent`) and the Editor life cycle `switch` and `close_editor` drive (`discover_instance`, `start`, `terminate`, `reap_server`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_identity.py`. The MCP client is tested against a real loopback `http.server` in a thread, which is the house discipline: real sockets, no mocks of the transport.

```python
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from agent.identity import aggregate, ready
from agent.unity_mcp import UnityMcp


class Handler(BaseHTTPRequestHandler):
    replies = {}
    sse = False

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        method = body.get("method")
        if method == "initialize":
            result = {"protocolVersion": "2024-11-05", "capabilities": {}, "serverInfo": {"name": "u", "version": "1"}}
        elif method == "notifications/initialized":
            self.send_response(202); self.end_headers(); return
        elif method == "resources/read":
            uri = body["params"]["uri"]
            result = {"contents": [{"type": "text", "text": json.dumps(self.replies[uri])}]}
        else:
            result = {"content": [{"type": "text", "text": json.dumps(self.replies.get(method, {}))}]}
        payload = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result})
        if self.sse:
            data = f"event: message\ndata: {payload}\n\n".encode()
            self.send_response(200); self.send_header("Content-Type", "text/event-stream")
        else:
            data = payload.encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Mcp-Session-Id", "session-1")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class McpTests(unittest.TestCase):
    def setUp(self):
        Handler.replies = {"mcpforunity://instances": {"instances": [{"id": "slot-1@0123456789abcdef"}]},
                           "mcpforunity://project/info": {"projectRoot": "/e/slot-1", "platform": "StandaloneOSX"}}
        Handler.sse = False
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)  # -W error turns the unclosed socket into a failure
        self.endpoint = f"http://127.0.0.1:{self.server.server_address[1]}/mcp"

    def test_only_a_loopback_mcp_endpoint_is_accepted(self):
        for bad in ("https://127.0.0.1:8080/mcp", "http://10.0.0.5:8080/mcp", "http://127.0.0.1:8080/",
                    "http://user@127.0.0.1:8080/mcp", "http://127.0.0.1:8080/mcp?a=1"):
            with self.assertRaises(ValueError, msg=bad):
                UnityMcp(bad)
        self.assertIsNotNone(UnityMcp(self.endpoint))

    def test_a_resource_reads_the_same_through_json_and_through_one_sse_message(self):
        client = UnityMcp(self.endpoint)
        self.assertEqual(client.read_resource("mcpforunity://instances")["instances"][0]["id"],
                         "slot-1@0123456789abcdef")
        Handler.sse = True
        other = UnityMcp(self.endpoint)
        self.assertEqual(other.read_resource("mcpforunity://project/info")["platform"], "StandaloneOSX")

    def test_an_unlisted_resource_uri_is_refused_before_any_request(self):
        client = UnityMcp(self.endpoint)
        with self.assertRaises(ValueError):
            client.read_resource("mcpforunity://something/else")


def state(**overrides):
    base = {"schema_version": "unity-mcp/editor_state@2", "observed_at_unix_ms": 0,
            "unity": {"instance_id": "slot-1@0123456789abcdef"}, "staleness": {"is_stale": False},
            "advice": {"ready_for_tools": True},
            "play_mode": {"is_playing": False, "is_paused": False, "is_changing": False},
            "compilation": {"is_compiling": False, "is_domain_reload_pending": False},
            "assets": {"is_updating": False}, "tests": {"is_running": False}}
    base.update(overrides)
    return base


class ReadyTests(unittest.TestCase):
    def setUp(self):
        self.now_ms = 1_700_000_000_000

    def test_a_fresh_idle_editor_of_the_expected_instance_is_ready(self):
        self.assertTrue(ready(state(observed_at_unix_ms=self.now_ms - 500), "slot-1@0123456789abcdef",
                              now_ms=self.now_ms))

    def test_a_stale_a_future_a_playing_and_a_compiling_editor_are_all_refused(self):
        instance = "slot-1@0123456789abcdef"
        self.assertFalse(ready(state(observed_at_unix_ms=self.now_ms - 10_001), instance, now_ms=self.now_ms))
        self.assertFalse(ready(state(observed_at_unix_ms=self.now_ms + 1), instance, now_ms=self.now_ms))
        self.assertFalse(ready(state(observed_at_unix_ms=self.now_ms,
                                     play_mode={"is_playing": True, "is_paused": False, "is_changing": False}),
                               instance, now_ms=self.now_ms))
        self.assertFalse(ready(state(observed_at_unix_ms=self.now_ms,
                                     compilation={"is_compiling": True, "is_domain_reload_pending": False}),
                               instance, now_ms=self.now_ms))
        self.assertFalse(ready(state(observed_at_unix_ms=self.now_ms), "other@ffffffffffffffff",
                               now_ms=self.now_ms))

    def test_the_aggregate_is_match_only_when_every_check_matches_and_unknown_is_never_match(self):
        self.assertEqual(aggregate({"a": "match", "b": "match"}), "match")
        self.assertEqual(aggregate({"a": "match", "b": "unknown"}), "unknown")
        self.assertEqual(aggregate({"a": "mismatch", "b": "unknown"}), "mismatch")

    def test_quiet_ignores_the_instance_and_the_clock_but_not_the_four_busy_flags(self):
        """The refresh wait cannot compare instances — hearing from the instance is what it is waiting for —
        and cannot bound staleness, because a compiling Editor stops publishing fresh samples."""
        self.assertTrue(quiet(state(observed_at_unix_ms=0, unity={"instance_id": "anything"})))
        for busy in ({"compilation": {"is_compiling": True, "is_domain_reload_pending": False}},
                     {"compilation": {"is_compiling": False, "is_domain_reload_pending": True}},
                     {"assets": {"is_updating": True}},
                     {"tests": {"is_running": True}}):
            self.assertFalse(quiet(state(**busy)), busy)
```

with `quiet` added to the `from agent.identity import ...` line.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -W error -m unittest discover -s tests -v -k McpTests -k ReadyTests`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent.unity_mcp'`.

- [ ] **Step 3: Copy the client and make its endpoint configuration**

```bash
cp /Users/elendil/WorkSpaces/Farm/FarmTestAgent/tools/farmqa_unity_identity.py agent/unity_mcp.py
mkdir -p agent/probes
cp /Users/elendil/WorkSpaces/Farm/FarmTestAgent/tests/probes/editor-readiness.cs.txt agent/probes/editor-readiness.cs.txt
```

Then in `agent/unity_mcp.py`: rename the class to `UnityMcp`, replace the module docstring with `"""Loopback JSON-RPC client for MCP for Unity (spec §7). Ported from FarmTestAgent/tools/farmqa_unity_identity.py."""`, delete the `9090` default so the endpoint is a required argument, and replace the instance-selection helper with:

```python
    RESOURCES = ("mcpforunity://custom-tools", "mcpforunity://instances", "mcpforunity://project/info",
                 "mcpforunity://editor/state")

    def select_instance(self, instance):
        """HTTP selection is per MCP session (the Mcp-Session-Id header), so each worker pins its own slot.
        UNITY_MCP_DEFAULT_INSTANCE is a stdio-only variable and has no effect on this path."""
        listed = [entry.get("id") for entry in self.read_resource("mcpforunity://instances").get("instances", [])]
        if instance not in listed:
            raise ValueError(f"instance {instance} is not connected; connected: {listed}")
        return self.call_tool("set_active_instance", {"instance": instance})
```

- [ ] **Step 4: Copy the identity module and open its constants**

```bash
cp /Users/elendil/WorkSpaces/Farm/FarmTestAgent/tools/farmqa_identity.py agent/identity.py
```

Also open the C# probe's assembly filter, so the expected set really is configuration. In `agent/probes/editor-readiness.cs.txt`, delete the two lines

```csharp
    if (name != "HotUpdate" && name != "AOTScripts" && name != "Nova.Runtime" && name != "MCPForUnity.Editor")
        continue;
```

so the probe returns every loaded assembly and `collect` filters in Python against `expected["assemblies"]`. Leave everything else in the file verbatim, including the `dataPath` field, which Task 0 Step 5 reads.

Then in `agent/identity.py`: keep `source_snapshot`, `ready`, `safe_text` and `project_matches` as they are, except that `ready` takes `now_ms=None` (defaulting to the wall clock) so the gate is testable without sleeping. Replace the observation write, which targeted `controller_identity_observations` directly, with a plain return value — `Ledger.record_identity` owns that table now. Replace the hardcoded verdict with:

```python
def aggregate(checks):
    """match only when every check is match; unknown is never match (farmqa_request_session.py:138-139)."""
    values = set(checks.values())
    if "mismatch" in values:
        return "mismatch"
    if values - {"match"}:
        return "unknown"
    return "match"
```

and make `collect(client, *, repository, instance, expected, probe_source)` take the expected assembly set from `expected["assemblies"]` instead of the module-level `{'HotUpdate','AOTScripts','Nova.Runtime','MCPForUnity.Editor'}`, and the build target from `expected["build_target"]` instead of `StandaloneWindows64`. Keep the order of operations exactly: source snapshot, read `custom-tools`, read `instances`, select, read `editor/state`, `ready()` gate, read `project/info`, run the probe, re-read `editor/state`, second source snapshot. Keep `source_stable` — the before-and-after comparison is what makes "the slot really was on that commit for the whole run" checkable. Keep the rule that no SQLite transaction is ever held across an MCP call.

- [ ] **Step 5: Give the pool its real collaborator**

At the end of `agent/slots.py`:

```python
class UnityIdentity:
    """The pool's `mcp` collaborator in production: one MCP session per call, pinned to the slot's instance,
    plus the Editor's own life cycle, because starting and closing it is what the state table in spec §7
    requires and nothing else in FarmBot does it."""

    def __init__(self, probe_path, timeout=120, start_timeout=120, clock=time.time, sleep=time.sleep):
        self.probe_path = Path(probe_path)
        self.timeout = timeout
        # Task 0 Step 5 measured cold Editor start to a usable MCP endpoint at 16 s. 120 is generous headroom
        # over a measured number; the 600 an earlier draft carried was a guess at an unmeasured one.
        self.start_timeout = start_timeout
        self.clock = clock
        self.sleep = sleep

    def _client(self, slot):
        from .unity_mcp import UnityMcp
        client = UnityMcp(slot["mcp_address"], timeout=self.timeout)
        if slot.get("instance"):
            client.select_instance(slot["instance"])
        return client

    def discover_instance(self, slot):
        """The instance id for this folder, read from the live server rather than assumed.

        Nothing else writes slots.instance, and without it the whole interactive path is dead: collect()
        passes the instance to ready(), which compares it against unity.instance_id, so a NULL instance makes
        ready() False, the aggregate unknown and every interactive switch a probe-stage hold. The plugin
        derives the id as SHA1(Application.dataPath) truncated to 16 hex characters
        (Editor/Helpers/ProjectIdentityUtility.cs) and Task 0 Step 5 settled the exact input:
        sha1("<folder>/Assets")[:16] — no trailing slash — reproduced the live slot-1@54462c1bfe7b5261
        exactly, while sha1("<folder>") and sha1("<folder>/Assets/") did not. It is still **read, not
        recomputed**: the rule is known, but the live value is what the worker must address, and a folder
        that is moved or symlinked would make a recomputed hash confidently wrong.
        """
        from .unity_mcp import UnityMcp
        folder = Path(slot["folder"]).resolve()
        client = UnityMcp(slot["mcp_address"], timeout=self.timeout)
        for entry in client.read_resource("mcpforunity://instances").get("instances", []):
            for key in ("projectPath", "dataPath", "path"):
                value = entry.get(key)
                if value and Path(value).resolve() in (folder, folder / "Assets"):
                    return entry.get("id")
        raise SlotError(f"no connected Editor instance reports {folder}", stage="editor")

    def start(self, slot, entry):
        """Start the Editor for an interactive run and wait until the MCP server reports this folder.

        The endpoint is served by a uvx process, not by the Editor itself, but Task 0 Step 5 established that
        the Editor **starts it** — with --project-scoped-tools and a per-slot pidfile — so no operator action
        and no separate agent is needed. What must still read as an editor-stage failure rather than an
        identity mismatch is the other direction: a server left behind by a previous Editor still answers on
        that address, which is why close_editor reaps it and why the message below names both possibilities.
        """
        from .unity import editor_path
        command = [str(editor_path(slot["folder"], override=entry.get("unity"))), "-projectPath",
                   str(slot["folder"]), "-logFile", str(Path(slot["folder"]) / "Logs" / "farmbot-editor.log")]
        subprocess.Popen(command, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = self.clock() + self.start_timeout
        while True:
            try:
                return self.discover_instance(slot)
            except Exception:
                if self.clock() >= deadline:
                    raise SlotError(f"{slot['slot_id']}: no MCP instance after {self.start_timeout}s "
                                    f"(measured cold start is 16s); either the Editor did not come up, or a "
                                    f"stale server from a previous Editor still holds {slot['mcp_address']}",
                                    stage="editor")
                self.sleep(5.0)

    def terminate(self, slot, timeout):
        """Task 0 Step 5 settled this: `EditorApplication.Exit(0)` through execute_code returns "exiting" and
        the Editor keeps running, so it is not used at all. SIGTERM the pid that holds the folder — measured
        gone in 1 s — and confirm it. Removing Temp/UnityLockfile is the pool's job, not this method's,
        because Unity leaves it behind even here."""
        from .unity import editor_holds_project
        pid = editor_holds_project(slot["folder"])
        if pid is None:
            return
        os.kill(pid, signal.SIGTERM)
        deadline = self.clock() + timeout
        while editor_holds_project(slot["folder"]) is not None:
            if self.clock() >= deadline:
                raise SlotError(f"Unity pid {pid} still holds {slot['folder']} {timeout}s after SIGTERM",
                                stage="editor")
            self.sleep(1.0)

    def reap_server(self, slot):
        """The uvx MCP server is a child of the Editor but outlives it: after the kill it still held port
        8080 and answered HTTP 200 (Task 0 Step 5). Its pid is in the slot's own RunState pidfile, which is
        also why one host can eventually run two slots. A missing or stale pidfile is not an error.

        The pidfile's name is **derived from the port**, not hardcoded: the spike saw
        `--pidfile <slot>/Library/MCPForUnity/RunState/mcp_http_8080.pid` beside
        `--http-url http://127.0.0.1:8080`, and `mcp_address` is a configuration key (DEFAULTS, Task 3). A
        second slot on another port would otherwise never be reaped, and a stale server holding that port
        is exactly what this method exists to prevent."""
        port = urllib.parse.urlsplit(slot["mcp_address"]).port or 8080
        pidfile = (Path(slot["folder"]) / "Library" / "MCPForUnity" / "RunState"
                   / f"mcp_http_{port}.pid")
        try:
            pid = int(pidfile.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        pidfile.unlink(missing_ok=True)

    def refresh(self, slot):
        self._client(slot).call_tool("refresh_unity", {})

    def wait_quiet(self, slot, timeout):
        """One sample of editor/state. The pool's wait_for_quiet owns the loop and the deadline; this raises
        TimeoutError when the Editor is still compiling, reloading, importing or running tests."""
        from .identity import quiet
        if not quiet(self._client(slot).read_resource("mcpforunity://editor/state")):
            raise TimeoutError("editor is not quiet")

    def console_errors_since(self, slot, marker):
        """spec §7's 'zero Console errors'. Reads the Editor's log entries and returns the error lines."""
        result = self._client(slot).call_tool("read_console", {"types": ["error"], "count": 50})
        return [line for line in (result or {}).get("lines", []) if "error CS" in line or "Exception" in line]

    def probe(self, slot, target):
        from .identity import collect
        client = self._client(slot)
        return collect(client, repository=target["repository"], instance=slot.get("instance"),
                       expected={"build_target": target.get("build_target"),
                                 "commit_sha": target["commit_sha"],
                                 "assemblies": ("HotUpdate", "AOTScripts", "Nova.Runtime", "MCPForUnity.Editor")},
                       probe_source=self.probe_path.read_text(encoding="utf-8"))

    def quiescent(self, slot, mode):
        """Spec §7's release predicate.

        In interactive mode: the Editor is back in Edit Mode, nothing is compiling, importing or running
        tests, and the state sample is fresh. In batch mode it is **not** a constant True — the batch release
        is defined as the Editor process being gone and its results file written, and returning True
        regardless would let park_idle run `git checkout --force` and `git lfs checkout` over a folder that
        still held Temp/UnityLockfile and a half-written Library/. That is precisely the corruption slots
        exist to avoid, and it would read as a project problem rather than a pool bug.
        """
        from .identity import ready
        from .unity import editor_holds_project
        if mode == "batch":
            # A live Unity on this folder holds the slot; a dead one leaves only a stale lock, which
            # SlotPool.settle clears through clear_stale_lock before anything checks the folder out.
            return not editor_holds_project(slot["folder"])
        state = self._client(slot).read_resource("mcpforunity://editor/state")
        return ready(state, slot.get("instance"))
```

One small addition comes with it. `quiet(state)` is a new two-line helper in `agent/identity.py` beside `ready`: the same four clauses (`compilation.is_compiling`, `compilation.is_domain_reload_pending`, `assets.is_updating`, `tests.is_running`) with neither the instance comparison nor the staleness bound, because during a refresh the instance is what we are waiting to hear from; `ready` keeps both. `agent.unity.editor_holds_project` needs nothing here — Task 4 already added it, because `SlotPool.editor_is_open` asks it the same question `terminate` and `quiescent` do. `import os`, `import signal` and `import urllib.parse` go at the top of `agent/slots.py` for `terminate` and `reap_server`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 -W error -m unittest discover -s tests -v -k McpTests -k ReadyTests`
Expected: PASS, seven tests.

- [ ] **Step 7: Run the whole suite and commit**

Run: `python3 -W error -m unittest discover -s tests`
Expected: OK, 186 tests (+7).

```bash
git add agent/unity_mcp.py agent/identity.py agent/probes/editor-readiness.cs.txt agent/slots.py tests/test_identity.py
git commit -m "feat(identity): port the FarmQA Editor identity probe

The loopback JSON-RPC client, the editor_state@2 readiness gate and the source
snapshot come over unchanged. Four things open up: the endpoint is configuration
rather than port 9090, the expected instance must be present rather than alone, the
expected assembly set moves out of the C# probe and into the probe's arguments, and
the verdict is an aggregate over the checks rather than a constant.

Co-Authored-By: <model> <noreply@anthropic.com>"
```

---

### Task 6: The pool grants a slot on its own thread and settles it afterwards

Two-phase acquisition becomes real. An `awaiting_resource` item has been inert since Plan 1a because `Scheduler.tick` drains only `ledger.queue()`, which selects `state='queued' AND worker_pid IS NULL`. The pool now grants the oldest request, performs the switch, writes the reservation token where the worker can read it, and calls `ledger.resume`, after which the *existing* launch loop picks the item up on the next tick with no change to the queue query. (Task 7 adds one more step between the switch and the token for a batch grant — the batch Editor itself, run outside the worker's sandbox — in `_hand_over`, which is written here. Nothing in this task anticipates it beyond leaving that method the obvious place for it.)

**Why:** a slot switch is `git checkout --detach` plus `git lfs checkout` on a repository whose `.git` is 3.7 GB, plus an Editor refresh and a compile wait — minutes, not milliseconds. `Scheduler.tick()` holds `self.lock` for its whole body and `agent/service.py` runs it every second. Running the switch inside a tick would freeze reaping, recovery and the 5-second Stop budget of done-criterion 2.

**Decision this task encodes:** two.

First, the pool gets its own thread, beside the receiver and scheduler threads that `serve` already starts, and the scheduler never probes Unity and never blocks on one. **It also gets its own `sqlite3.Connection`.** The two threads meet in SQLite, where `BEGIN IMMEDIATE` and the partial unique indexes serialize them — but only if they are two connections. `agent/service.py:30` builds one `Ledger(paths.ledger, check_same_thread=False)` and hands it to the scheduler; handing the same object to the pool would give two threads one connection whose `_transaction()` is a bare `BEGIN IMMEDIATE`/`COMMIT`, so one thread's `BEGIN` can land inside the other's open transaction and either thread's `COMMIT` or `ROLLBACK` would apply to the other's half-written work. WAL and `busy_timeout` do nothing about that. The receiver already takes a factory (`lambda: Ledger(...)`) for this exact reason, and the pool follows it.

Second, the settlement rule is a query, not a flag: the pool settles any reservation that is `cancel_requested`, or whose item is no longer `queued`, `running` or `awaiting_resource` — those three states are exactly the window in which a granted slot is still on its way to a worker or being used by one — **and whose slot is not `held`**, because a held slot is waiting for a human, not for another probe.

**Files:**
- Modify: `agent/ledger.py` (`fail_queued` accepts an `awaiting_resource` item; `requeue_reservation`)
- Modify: `agent/slots.py` (`SlotPool.tick`, `grant`, `settle`, `park_idle`, the token file, the ledger factory)
- Modify: `agent/service.py` (`build` constructs the pool with its own ledger; `serve` starts its thread)
- Modify: `tests/test_scheduler.py` (delete `test_awaiting_resource_stays_parked_in_phase_1a`, line 221)
- Test: `tests/test_slots.py`, `tests/test_service.py`

**Interfaces:**
- Consumes: `Ledger.acquire`, `release`, `hold`, `reservations_to_settle`, `resume`, `fail_queued`, `cancel_reservations`, `record_identity`; `SlotPool.switch`, `park`, `clear_stale_lock` from Task 4.
- Produces:
  - `Ledger.requeue_reservation(reservation_id, reason) -> str`, the new reservation id: one more `queued` row at the tail of the queue with `attempts` incremented, for the same item, generation and commit.
  - `Ledger.fail_queued(item_id, reason)` accepts an item in `queued` **or** `awaiting_resource`. Its two existing callers in `agent/scheduler.py` pass queued items and are unaffected.
  - `SlotPool(ledger, ...)` where `ledger` may be a `Ledger` **or** a zero-argument factory. When it is a factory the pool calls it once, on the thread that will use it, and closes it in `SlotPool.close()`. Tests pass an object, `service.build` passes a factory.
  - `SlotPool(..., state_dir=None, owner="pool")`. `state_dir` is a callable taking an item id and returning that item's private directory — `Launcher.state_dir` in production.
  - `SlotPool.tick() -> dict` with `settled`, `granted` and `parked` counts. Safe to call from one thread only; it is the pool thread's whole loop body.
  - `SlotPool.token_path(item_id) -> Path`, `<state_dir>/reservation.token`, mode 0600.

- [ ] **Step 1: Write the failing tests**

In `tests/test_slots.py`, add a class that reuses `SlotFixture` and seeds work items the way `ReservationTests` does. Import `ISSUE`, `OTHER`, `SESSION` and `issue` from `test_ledger`, as `tests/test_scheduler.py` already does.

```python
class PoolTests(SlotFixture):
    def waiting(self, issue_id, commit, mode):
        # issue(id=...), not issue(issue_id=...): the helper's keyword is `id` and the wrong one leaves the
        # row on ISSUE, after which create_work_item raises "unknown issue".
        self.ledger.observe_issue(issue(id=issue_id))
        session = f"session-{issue_id}"
        self.ledger.ensure_session(session, issue_id, True)
        target = {"repository": "Farm-Client", "requested_ref": "main", "commit_sha": commit,
                  "server_environment": "公共测试服", "selected_at": SELECTED_AT}
        item = self.ledger.create_work_item(issue_id=issue_id, session_id=session, skill="fix", target=target)
        token = self.ledger.claim(item["id"], worker_id="w")["token"]
        self.ledger.await_resource(item["id"], token, "unity_slot", mode)
        return item["id"]

    def finish_worker(self, item_id, reason="worker finished"):
        """A granted item is 'queued' again, and Ledger.fail refuses anything that is not 'running'. The
        worker's own path is claim-then-terminal, so the fixture takes it rather than inventing one."""
        self.ledger.claim(item_id, worker_id="w2")
        return self.ledger.fail(item_id, reason)

    def pool(self, **kwargs):
        kwargs.setdefault("state_dir", lambda item_id: self.root / "runs" / item_id)
        return super().pool(**kwargs)

    def test_a_queued_request_is_granted_switched_and_resumed_for_a_fresh_worker(self):
        self.pool().ensure()
        commit = self.commit("fix")
        item = self.waiting(ISSUE, commit, "batch")
        pool = self.pool(mcp=FakeMcp())
        self.assertEqual(pool.tick()["granted"], 1)
        self.assertEqual(self.ledger.item(item)["state"], "queued")
        self.assertEqual(self.ledger.item(item)["needs_resource"], None)
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "batch_busy")
        self.assertEqual(self.trees.head(self.root / "editors" / "slot-1"), commit)
        token = pool.token_path(item)
        self.assertTrue(token.is_file())
        self.assertEqual(token.stat().st_mode & 0o077, 0)

    def test_two_requests_serialize_on_the_one_slot_batch_first_then_interactive(self):
        self.pool().ensure()
        first_commit = self.commit("first")
        first = self.waiting(ISSUE, first_commit, "batch")
        second_commit = self.commit("second")
        second = self.waiting(OTHER, second_commit, "interactive")
        pool = self.pool(mcp=FakeMcp())
        pool.tick()
        self.assertEqual(self.ledger.item(second)["state"], "awaiting_resource")
        self.assertEqual(pool.tick()["granted"], 0)
        self.finish_worker(first, "batch worker finished")   # any terminal state hands the slot back
        self.assertEqual(pool.tick()["settled"], 1)
        self.assertEqual(pool.tick()["granted"], 1)
        self.assertEqual(self.ledger.item(second)["state"], "queued")
        self.assertEqual(self.trees.head(self.root / "editors" / "slot-1"), second_commit)
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "interactive_busy")

    def test_two_requests_serialize_in_the_other_order_interactive_first_then_batch(self):
        """The steady state once spec §7's 'after an interactive run the Editor stays open' is real. The
        batch-first ordering never exercises the graceful close, so on its own it would sign off a system
        that is broken for every subsequent pair of items."""
        self.pool().ensure()
        first = self.waiting(ISSUE, self.commit("first"), "interactive")
        second_commit = self.commit("second")
        second = self.waiting(OTHER, second_commit, "batch")
        pool = self.pool(mcp=FakeMcp())
        pool.tick()
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "interactive_busy")
        self.finish_worker(first)
        pool.tick()
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "idle_open")
        self.assertEqual(pool.tick()["granted"], 1)
        # The close is SIGTERM then a reap of the uvx MCP server; the pool removes the lock file itself.
        self.assertEqual([("terminate", "unity_slot:1"), ("reap_server", "unity_slot:1")], pool.mcp.calls[-2:])
        self.assertFalse((self.root / "editors" / "slot-1" / "Temp" / "UnityLockfile").exists())
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "batch_busy")
        self.assertEqual(self.trees.head(self.root / "editors" / "slot-1"), second_commit)

    def test_a_failing_probe_holds_the_slot_and_fails_the_item_without_retrying(self):
        self.pool().ensure()
        item = self.waiting(ISSUE, self.commit("fix"), "interactive")
        pool = self.pool(mcp=FakeMcp(ready=False))
        pool.tick()
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "held")
        self.assertEqual(self.ledger.item(item)["state"], "failed")
        self.assertEqual([r["state"] for r in self.ledger.reservations()], ["active"])

    def test_a_held_slot_is_not_probed_again_on_the_next_tick(self):
        """hold() leaves the reservation active and only changes the slot, so without the 'held' exclusion in
        reservations_to_settle the pool would re-probe every two seconds a slot the operator was told to
        recover, and might eventually release one spec §7 says must wait."""
        self.pool().ensure()
        self.waiting(ISSUE, self.commit("fix"), "interactive")
        pool = self.pool(mcp=FakeMcp(ready=False))
        pool.tick()
        count = lambda: self.ledger.connection.execute("SELECT count(*) FROM audit").fetchone()[0]
        before = count()
        self.assertEqual(pool.tick(), {"settled": 0, "granted": 0, "parked": 0})
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "held")
        self.assertEqual([r["state"] for r in self.ledger.reservations()], ["active"])
        self.assertEqual(count(), before)

    def test_a_slot_is_held_when_the_release_gate_does_not_pass(self):
        """Global constraint: no slot is ever released on a timer. These are the three ways the gate fails."""
        for label, build, break_it in (
            ("not quiescent", lambda: FakeMcp(ready=False), None),
            ("probe raised", lambda: RaisingQuiescence(), None),
            ("token file gone", lambda: FakeMcp(), "token"),
        ):
            with self.subTest(label):
                self.setUp()
                self.pool().ensure()
                item = self.waiting(ISSUE, self.commit("fix"), "batch")
                pool = self.pool(mcp=build())
                pool.tick()
                if break_it == "token":
                    pool.token_path(item).unlink()
                self.finish_worker(item)
                self.assertEqual(pool.tick()["settled"], 0)
                self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "held")
                self.assertEqual([r["state"] for r in self.ledger.reservations()], ["active"])

    def test_a_stop_between_the_grant_and_the_switch_does_not_kill_the_pool_thread(self):
        """resume() raises unless the item is awaiting; a Stop during the multi-minute switch cancels it, and
        an uncaught LedgerError there would take grant(), tick() and the pool thread down with the slot busy
        and the reservation open."""
        self.pool().ensure()
        item = self.waiting(ISSUE, self.commit("fix"), "interactive")
        pool = self.pool(mcp=CancellingMcp(self.ledger, item))
        self.assertEqual(pool.tick()["granted"], 0)
        self.assertEqual(self.ledger.item(item)["state"], "cancelled")
        self.assertEqual([r["state"] for r in self.ledger.reservations()], ["cancelled"])
        self.assertIn(self.ledger.slot("unity_slot:1")["state"], ("switching", "idle_closed"))
        self.assertEqual(pool.tick()["parked"], 1)   # and the slot comes back to the pool by itself

    def test_a_git_stage_failure_is_requeued_once_at_the_tail_and_fails_the_item_the_second_time(self):
        self.pool().ensure()
        first = self.waiting(ISSUE, self.commit("first"), "batch")
        pool = self.pool(mcp=FakeMcp(), worktrees=BrokenCheckout(self.trees))
        pool.tick()
        requeued = [r for r in self.ledger.reservations() if r["state"] == "queued"]
        self.assertEqual([(r["item_id"], r["attempts"]) for r in requeued], [(first, 1)])
        self.assertEqual(self.ledger.item(first)["state"], "awaiting_resource")
        second = self.waiting(OTHER, self.commit("second"), "batch")
        self.assertEqual([r["item_id"] for r in self.ledger.reservations() if r["state"] == "queued"],
                         [first, second])   # re-queued at the tail, but still ahead of a later arrival
        pool.tick()
        self.assertEqual(self.ledger.item(first)["state"], "failed")
        reasons = [row["reason"] for row in self.ledger.connection.execute(
            "SELECT reason FROM audit WHERE item_id=?", (first,))]
        self.assertTrue(any("verification gap" in r for r in reasons), reasons)
        self.assertEqual(len([r for r in self.ledger.reservations() if r["item_id"] == first]), 2)

    def test_a_released_slot_is_parked_back_on_main_when_nothing_is_waiting(self):
        self.pool().ensure()
        item = self.waiting(ISSUE, self.commit("fix"), "batch")
        pool = self.pool(mcp=FakeMcp())
        pool.tick()
        main = self.trees.resolve_commit("Farm-Client")
        self.finish_worker(item, "done")
        # One tick settles, releases and parks: tick() computes all three counts in one call, so asserting
        # parked on a *later* tick would see 0 and the slot already idle.
        self.assertEqual(pool.tick(), {"settled": 1, "granted": 0, "parked": 1})
        slot = self.ledger.slot("unity_slot:1")
        self.assertEqual((slot["state"], slot["parked_commit"]), ("idle_closed", main))
        self.assertEqual(self.trees.head(self.root / "editors" / "slot-1"), main)
```

Three small doubles go beside `FakeMcp` in the same file:

```python
class RaisingQuiescence(FakeMcp):
    def quiescent(self, slot, mode):
        raise RuntimeError("the endpoint did not answer")


class CancellingMcp(FakeMcp):
    """Cancels the item from inside the switch, which is where a Linear Stop lands in production."""
    def __init__(self, ledger, item_id):
        super().__init__()
        self.ledger, self.item_id = ledger, item_id

    def refresh(self, slot):
        super().refresh(slot)
        self.ledger.cancel_reservations(self.item_id, "Linear stop")
        self.ledger.cancel(self.item_id, "Linear stop")


class BrokenCheckout:
    """Everything the real Worktrees does, except that moving the slot always fails at the git stage."""
    def __init__(self, trees):
        self._trees = trees

    def __getattr__(self, name):
        return getattr(self._trees, name)

    def checkout_commit(self, path, commit):
        raise WorktreeError("reference is not a tree")
```

In `tests/test_scheduler.py`, delete `test_awaiting_resource_stays_parked_in_phase_1a` (line 221). Its comment says the behaviour is Phase 1a's on purpose; this task is where it stops being true, and `PoolTests` above replaces it.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -W error -m unittest discover -s tests -v -k Pool`
Expected: FAIL with `AttributeError: 'SlotPool' object has no attribute 'tick'`.

- [ ] **Step 3: Let a waiting item fail, and let a request be re-queued**

In `agent/ledger.py`, widen the `fail_queued` guard and add the re-queue:

```python
            if row["state"] not in ("queued", "awaiting_resource"):
                raise LedgerError("only a queued or waiting work item can fail before claim")
```

```python
    def requeue_reservation(self, reservation_id, reason):
        """One more chance at the tail of the queue after a retryable failure, never a third."""
        _text(reason, "reason")
        with self._transaction():
            row = self.connection.execute("SELECT * FROM reservations WHERE reservation_id=?",
                                          (reservation_id,)).fetchone()
            if row is None or row["state"] not in ("released", "cancelled"):
                raise LedgerError("only a closed reservation can be re-queued")
            fresh = str(uuid4())
            self.connection.execute(
                """INSERT INTO reservations(reservation_id,item_id,generation,kind,mode,commit_sha,state,
                   attempts,created_at) VALUES(?,?,?,?,?,?,'queued',?,?)""",
                (fresh, row["item_id"], row["generation"], row["kind"], row["mode"], row["commit_sha"],
                 row["attempts"] + 1, self.clock()))
            self._audit(row["item_id"], "reservation", "requeued", details={"reservation_id": fresh,
                                                                            "reason": reason[:200]})
            return fresh
```

- [ ] **Step 4: Write the pool's loop**

In `agent/slots.py`, extend `__init__` with `state_dir=None, owner="pool"` and add:

```python
    def token_path(self, item_id):
        return Path(self.state_dir(item_id)) / "reservation.token"

    def _write_token(self, item_id, reservation):
        """The token goes to a file, never onto a command line: the prompt is written to disk and arguments are
        visible to every process on the host (spec §15)."""
        path = self.token_path(item_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with os.fdopen(os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600), "w", encoding="utf-8") as handle:
            handle.write(reservation["token"])
        return path

    def _read_token(self, item_id):
        try:
            return self.token_path(item_id).read_text(encoding="utf-8").strip()
        except OSError:
            return None

    def tick(self):
        """The pool thread's whole loop body. Everything slow lives here and nothing here holds a ledger
        transaction across a git command, a subprocess or an MCP call."""
        return {"settled": self.settle(), "granted": self.grant(), "parked": self.park_idle()}

    def grant(self):
        """`granted` counts hand-overs that reached a worker, not acquisitions that were attempted. A
        switch that failed and a Stop that landed mid-switch both give the slot back, so counting them
        would make tick()'s own report disagree with the ledger — and the Stop test asserts zero."""
        granted = 0
        for kind in sorted({entry["kind"] for entry in self.entries.values()}):
            reservation = self.ledger.acquire(kind, owner=self.owner, host=self.host)
            if reservation is None:
                continue
            granted += 1 if self._hand_over(reservation) else 0
        return granted

    def _hand_over(self, reservation):
        """True when the item really has the slot: the switch succeeded, the token is on disk and resume()
        put the item back in the queue. False on every path that gave the slot back."""
        item_id, slot_id = reservation["item_id"], reservation["resource"]
        self.last_observation = None
        try:
            self.switch(slot_id, reservation["commit_sha"], reservation["mode"])
        except SlotError as exc:
            self._switch_failed(reservation, exc)
            return False
        if self.last_observation is not None:
            self.ledger.record_identity(item_id, reservation["reservation_id"], slot_id, self.last_observation)
        self._write_token(item_id, reservation)
        try:
            self.ledger.resume(item_id, f"{slot_id} acquired ({reservation['mode']})")
        except LedgerError:
            # A Stop landed during the multi-minute switch, so the item is already cancelled and resume()
            # refuses it. Uncaught, that LedgerError would come out of grant(), out of tick() and take the
            # pool thread down with the slot busy and the reservation open. Give the slot back instead;
            # park_idle returns it to main on the next pass.
            self.token_path(item_id).unlink(missing_ok=True)
            self.ledger.release(reservation["reservation_id"], reservation["token"], "item cancelled mid-switch")
            return False
        return True

    def _switch_failed(self, reservation, exc):
        reason = str(exc)[:400]
        if exc.stage == "probe":
            # Spec §7: a failing probe holds the slot for the operator's recover command. With one slot there
            # is nowhere to retry, so the item records the gap instead of waiting for a human.
            self.ledger.hold(reservation["reservation_id"], reason)
            self.ledger.fail_queued(reservation["item_id"], f"slot held after a failed probe: {reason}")
            return
        self.ledger.release(reservation["reservation_id"], reservation["token"], reason)
        self.ledger.set_slot_state(reservation["resource"], "idle_closed")
        if exc.stage == "compile":
            # The slot is fine; this commit does not build. Fail the item and leave the slot in the pool.
            self.ledger.fail_queued(reservation["item_id"], f"compile errors at the pinned commit: {reason}")
        elif reservation["attempts"] >= 1:
            self.ledger.fail_queued(reservation["item_id"], f"verification gap: {reason}")
        else:
            self.ledger.requeue_reservation(reservation["reservation_id"], reason)

    def settle(self):
        """A slot is only useful to a running worker. Anything else is probed and then released or held.

        Nothing here is on a timer (global constraint). Three things make the gate fail and all three hold the
        slot: the quiescence check says no, the quiescence check raises, or the token file is gone. A batch
        reservation's quiescence is not a constant — it is the confirmation that no Unity process holds the
        folder — and only once that passes may the stale lock be cleared and the slot checked out, because
        `git checkout --force` plus `git lfs checkout` over a live Editor's Library/ is the corruption slots
        exist to prevent.
        """
        settled = 0
        for reservation in self.ledger.reservations_to_settle():
            slot = self.ledger.slot(reservation["resource"])
            token = self._read_token(reservation["item_id"])
            if token is None:
                self.ledger.hold(reservation["reservation_id"], "reservation token file is missing")
                continue
            try:
                quiet = self.mcp.quiescent(slot, reservation["mode"]) if self.mcp is not None else True
            except Exception as exc:
                quiet = False
                reason = f"quiescence probe raised {type(exc).__name__}"
            else:
                reason = "quiescent" if quiet else "not quiescent"
            if not quiet:
                self.ledger.hold(reservation["reservation_id"], reason)
                continue
            if reservation["mode"] == "batch":
                # The process is confirmed gone by the quiescence check above, so the lock is stale by
                # definition; clear_stale_lock takes the same liveness answer rather than a second guess.
                self.clear_stale_lock(slot["folder"], lambda: False)
            self.ledger.release(reservation["reservation_id"], token, reason)
            self.token_path(reservation["item_id"]).unlink(missing_ok=True)
            self._departing[reservation["resource"]] = reservation["mode"]
            settled += 1
        return settled

    def park_idle(self):
        """After the last release, the slot goes back to main so Library/ tracks main in small steps (spec §7).

        The departing mode comes from `_departing`, which settle() wrote a moment ago, because Ledger.release
        has already overwritten the slot's state to 'switching' and the state that decides idle_open from
        idle_closed is no longer readable from the row.
        """
        parked = 0
        for slot in self.ledger.slots(host=self.host):
            if slot["state"] != "switching" or self.ledger.active_reservation_on(slot["slot_id"]):
                continue
            mode = self._departing.pop(slot["slot_id"], "batch")
            try:
                self.park(slot["slot_id"], mode)
            except SlotError:
                self.ledger.set_slot_state(slot["slot_id"], "held")
                continue
            parked += 1
        return parked

    def close(self):
        if self._owns_ledger:
            self.ledger.close()
```

`self._departing = {}` goes in `__init__` beside the rest. The ledger argument is resolved there too, once, on the thread that will use it:

```python
        self._owns_ledger = callable(ledger) and not isinstance(ledger, Ledger)
        self.ledger = ledger() if self._owns_ledger else ledger
```

Add `import os` and `from .ledger import Ledger, LedgerError` to the module. `Ledger.active_reservation_on(slot_id)` is one more small reader beside `active_reservation`:

```python
    def active_reservation_on(self, slot_id):
        row = self.connection.execute(
            "SELECT * FROM reservations WHERE resource=? AND state IN ('active','cancel_requested')",
            (slot_id,)).fetchone()
        return self._reservation_view(row) if row else None
```

- [ ] **Step 5: Start the pool thread**

In `agent/service.py`, extend `Components` with `pool`, build it in `build`, and give `serve` a third loop:

```python
Components = namedtuple("Components", "config paths api ledger skills worktrees launcher scheduler receiver server pool")
```

```python
    # A factory, not the scheduler's connection: the pool runs on its own thread and two threads on one
    # sqlite3.Connection interleave their BEGIN IMMEDIATE blocks. The receiver already takes one of these.
    pool = SlotPool(lambda: Ledger(paths.ledger, check_same_thread=False),
                    worktrees, [slot_entry(raw) for raw in config.slots], host=config.host,
                    editors_root=paths.editors, state_dir=launcher.state_dir,
                    mcp=UnityIdentity(ROOT / "agent" / "probes" / "editor-readiness.cs.txt"))
```

```python
    def pool_once():
        components.pool.tick()
        stop.wait(2.0)
```

```python
    threads = [threading.Thread(target=guarded("receive", receive_once), daemon=True),
               threading.Thread(target=guarded("schedule", schedule_once), daemon=True),
               threading.Thread(target=guarded("pool", pool_once), daemon=True)]
```

and call `pool.ensure()` once, immediately before the threads start, inside a `try/except SlotError` that prints the failure and leaves the pool empty rather than refusing to serve — a host without a usable slot must still answer Linear. Add `components.pool.close()` to `serve`'s `finally` block beside `components.ledger.close()`, and `from .slots import SlotPool, SlotError, UnityIdentity, slot_entry` to the imports.

`serve`'s `finally` block also gains, **before** `components.pool.close()`:

```python
        for owner in components.launcher.stop_all_unsandboxed():
            print(json.dumps({"event": "batch_killed_on_shutdown", "item": owner}), flush=True)
```

All three threads are daemons, so a shutdown does not wait for the pool thread — and the pool thread is
exactly where a batch Editor is being waited on, for up to `batch_timeout`. Without this line the Editor
survives the service that started it, keeps `Temp/UnityLockfile` and the slot folder open, and the next
`ensure()` after a restart finds a slot that some other Editor appears to hold. It is logged rather than
silent because an operator restarting the service deserves to know a Unity run died with it.

That last behaviour is a one-line guard in front of the whole service's availability, so it gets a test. In `tests/test_service.py`:

```python
    def test_serve_still_starts_when_the_slot_pool_cannot_be_prepared(self):
        components = build(self.config)
        self.addCleanup(components.server.server_close)
        components.pool = SimpleNamespace(ensure=_raise(SlotError("slot-1: still holds git-lfs pointers")),
                                          tick=lambda: {}, close=lambda: None)
        thread = threading.Thread(target=serve, kwargs={"components": components}, daemon=True)
        thread.start()
        self.addCleanup(components.server.shutdown)
        self.assertTrue(self.wait_for_ready(components))
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 -W error -m unittest discover -s tests -v -k PoolTests -k slot_pool`
Expected: PASS, nine `PoolTests` cases plus the one service case.

- [ ] **Step 7: Run the whole suite and commit**

Run: `python3 -W error -m unittest discover -s tests`
Expected: OK, 195 tests (nine new in `tests/test_slots.py`, one in `tests/test_service.py`, one Phase 1a test deleted).

```bash
git add agent/ledger.py agent/slots.py agent/service.py tests/test_slots.py tests/test_scheduler.py tests/test_service.py
git commit -m "feat(slots): grant, switch and settle reservations on the pool thread

awaiting_resource items were inert: the scheduler drains only the queued items and
never looked at reservations. The pool now runs beside the receiver and scheduler
threads, because a switch takes minutes while tick() holds its lock and runs every
second. Nothing in the pool holds a ledger transaction across git, a subprocess or
an MCP call.

Co-Authored-By: <model> <noreply@anthropic.com>"
```

---

### Task 7: A worker is launched with exactly the tools its reservation allows

Spec §7: "A worker reaches the Unity MCP or device-mcp only if the launcher put that server into its configuration, and only while the item holds the matching reservation." `Launcher.spawn` has taken an `mcp_servers` argument since Plan 1a and `agent/scheduler.py:60` passes a hardcoded `{}`. This task makes that argument a function of the reservation.

**Why:** the skill manifest's `resources` and `mcp` fields are parsed and validated by `agent/skills.py` and read by no production code at all — only by `tests/test_skills.py`. Injection from the manifest alone would give every `fix` worker the Unity MCP whether or not it holds a slot, which is precisely the enforcement §7 forbids. There is a second reason to be careful here: `Farm-Client/.codex/config.toml` is tracked in git and carries an MCP server URL, so it lands in every worktree a worker runs in. The isolated home makes it inert, and the plan says so explicitly: repository-local agent configuration is never a source of tool configuration. Separately and more seriously, that file also carries a plaintext bearer token for an internal MCP server, in git history. That is a secret-hygiene problem owed to whoever owns Farm-Client, not something this plan can fix; FarmBot's guarantee is only that the file never becomes a source of tool configuration, and `tests/test_launcher.py` already builds the isolated home, so the guarantee is worth asserting there.

**Design:** an interactive reservation adds one MCP server named `unity`, pointing at the slot's `mcp_address`; the worker pins its own session with `set_active_instance`, because HTTP selection is per MCP session and the launcher cannot do it on the worker's behalf. The server entry is built **per runtime**: `{"url": ...}` for the codex TOML writer and `{"type": "http", "url": ...}` for the `claude` JSON writer, because `Launcher.write_mcp_config` emits `{"mcpServers": servers}` and an entry there with a bare `url` and no `type` is not a valid HTTP server definition — spec §8 requires `claude -p` to work with the same manifest and dispatch message, and §19's first risk is that the default runtime may have to switch after Task 0. Getting this wrong makes interactive slots silently lose their only tool.

**A batch reservation adds no MCP server, no writable roots and no argv — because the worker does not run Unity at all.** Task 0 Step 6 ran the batch suite under a plain one-shot LaunchAgent with **no seatbelt**, so it verified the *unsandboxed* run and nothing else; an earlier draft of this task claimed it verified the sandboxed one and was wrong. The spike's addendum tested what Step 6 did not, and the answer is worse than a missing root: under `sandbox_workspace_write`, with `writable_roots = [state_dir, slot]` and `network_access = true` — this launcher's own default set — the Editor **hung indefinitely**, killed at 25 minutes at 0.0% CPU and 113 MB RSS, log frozen at 2,289 bytes, no results file, while the identical command on the identical slot minutes later exited **2 in 19 s** with 2,536,767 bytes of XML and 4388 tests. There were **zero** file-permission denials and licensing *succeeded* inside the seatbelt; the log stops at `Connection Invalid error for service com.apple.hiservices-xpcservice`, a **Mach service** lookup, which is a different axis of the seatbelt that `[sandbox_workspace_write]` does not express. No `writable_roots` entry can fix it, and `codex exec --approve-for-me` is documented as using the workspace-write sandbox, so this holds for every Codex worker FarmBot spawns.

**So the division of labour moves, exactly as spec §7 already divides the slot switch** — "Before a run the launcher, not the worker, moves the slot" — extended to the run itself. The worker *requests* a batch run by asking for a `batch` reservation and exiting; the pool switches the slot and then **runs the Editor through `agent/launcher.py`, outside any sandbox**; the fresh worker that resumes is handed the results file, the parsed totals and the exit code in its `resource` block and reads them as evidence. Two alternatives are **rejected and must not be reintroduced**: giving the worker `danger-full-access` removes the seatbelt from an agent that also holds repository write access and network, which defeats §15; and hand-maintaining a seatbelt profile with Mach-service exceptions is not expressible here and would mean tracking an undocumented set of services Unity happens to need.

**What that buys, stated as properties rather than hopes.** The argv is composed by the pool from the *slot entry's* configuration — `unity`, `build_target_argument`, `test_platform`, `test_assemblies` — and from the item's own state directory; **no value a worker supplied ever reaches it**, so the one unsandboxed command line in FarmBot is not an injection surface. The slot folder is in **no** worker's writable roots in either mode, so a worker cannot write the slot even by accident, which is a stronger guarantee than the one the old batch branch gave. And the flags that matter — no `-quit`, no `-nographics`, no `-accept-apiupdate` — are enforced in the one function that builds the argv, never in prose a worker might paraphrase. The dispatch payload carries the token's *path*, never the token.

**`resource.batch_command` is gone from the payload in both modes.** An interactive worker still has the better route it always had, and now so does a batch one: Task 0 Step 5 found that **MCP for Unity exposes `run_tests` asynchronously** — it returns a `job_id`, is polled with `get_test_job` (which takes a `wait_timeout`), and exposes `clear_stuck` for a job orphaned by a domain reload — so an interactive slot never needs a second Editor, which is the corruption slots exist to prevent, the same class as `git checkout --force` over an open `Library/`. That is the interactive verify path, it runs inside the Editor the pool already started, and it is what rung 5 of `skills/fix/SKILL.md` tells the worker to use (Task 8 Step 5).

**The exit-code contract, measured rather than assumed — and now enforced by the pool rather than trusted to the worker.** Task 0 Step 4 ran the EditMode suite twice. With `-assemblyNames HotUpdate.Tests` the Editor exited **2** and wrote 2.5 MB of XML with `total="4388" passed="4362" failed="26"`. With `-assemblyNames NoSuchAssembly` it exited **0** and wrote 652 bytes reading `result="Passed"` with `total="0"`. So the mapping is the opposite of the intuitive one: **exit 0 means nothing ran and exit 2 means tests failed**, and a typo in an assembly name produces a green exit code over an empty run. The contract is therefore: **read the results XML through `agent.unity.read_results`, assert `total > 0`, and treat the exit code as advisory only.** The pool applies it the moment the run ends and writes the verdict into `resource.batch_result` as `state`: `"ran"`, or `"gap"` when the file is missing, unparseable or reports `total == 0`, or `"timeout"` when the Editor outlived its deadline. A gap is not a failed verification — it is *no* verification, and the worker reports it as a verification gap rather than as evidence either way. The same sentence is in the `AUTHORITY` block in Step 3 and in rung 4 of `skills/fix/SKILL.md` (Task 8 Step 5), which is where the worker actually reads it.

**`origin/main` is known-red: 26 failures of 4388.** Task 0 Step 4 recorded the failing set by class — `AnimalBarnConfigTests` (2), `AnimalUpgradeCostConfigTests` (3), `DressUpConfigContractTests` (1), `LeaderboardConfigContractTests` (1), `GrowthTaskGroupConfigTests` (1), `FirstChargeHeroCropConfigTests` (1), `ConfigProtobufGeneratedGoldenTests` (1), `FriendShelfUnlockConfigTests` (6), `NetworkProtobufGeneratedSmokeTests` (1), `StatInfoConfigTests` (5), `VisitSharedRequestTests` (4) — almost all configuration-table contract tests, which reads as exported tables being out of step with the code rather than as broken code. A `fix` worker will meet these 26 on **every** batch run in this phase and **must not attribute them to its own change**. Comparing a failure set against a stored baseline is still spec §8's verify stage and still belongs to Phase 3 (see Scope); what Phase 1 owes is the warning, so the worker's report says "26 pre-existing failures on main, unchanged" instead of inventing a regression. The list in the spike record is the baseline until it is re-measured.

**The scheduling band, which everything Unity here inherits.** Task 0 Step 6 measured the *same* EditMode suite at **105 s under launchd against 14 s foreground with warm caches** — 7.5x — because `agent/deploy.py:29` writes `"ProcessType": "Background"` into the serve agent's plist and macOS gives that job and every child it spawns lowered CPU priority, timer coalescing and throttled disk I/O. The batch Editor is now started by `Launcher.run_unsandboxed` on the pool thread, which makes it a direct child of `serve` rather than a grandchild through the worker — the band is inherited either way, so the fix is the same and the need for it is if anything plainer. It is one line in the generator and one assertion, it needs no operator and no host, and it belongs here because this is the task that decides what a Unity child inherits from the service.

**Files:**
- Modify: `agent/launcher.py` (`run_unsandboxed`, the only process started outside the worker seatbelt)
- Modify: `agent/slots.py` (`SlotPool.run_batch`, called from `_hand_over` after a batch switch; `__init__` gains `run_unsandboxed`)
- Modify: `agent/scheduler.py` (`launch`, `Scheduler.__init__` gains `slot_entries`)
- Modify: `agent/dispatch.py` (`dispatch_message` gains `resource=None`)
- Modify: `agent/service.py` (`build` fills `Scheduler(slot_entries=...)` from the same entries it hands the pool, and hands `SlotPool` the launcher's `run_unsandboxed`)
- Modify: `agent/deploy.py` (`ProcessType` becomes `Standard`)
- Test: `tests/test_launcher.py`, `tests/test_slots.py`, `tests/test_scheduler.py`, `tests/test_dispatch.py`, `tests/test_deploy.py`

**Interfaces:**
- Consumes: `Launcher.spawn(item_id, message, mcp_servers, budget_seconds, cwd, extra_env=None, writable=())`, unchanged; `Ledger.active_reservation(item_id)`; `Ledger.slot(slot_id)`; `agent.unity.batch_test_command`, `agent.unity.editor_path` and `agent.unity.read_results` from Task 4.
- Produces:
  - `Launcher.run_unsandboxed(argv, *, cwd, timeout, log=None, env=None, owner=None) -> Unsandboxed(returncode, timed_out, seconds)`. **The one place in FarmBot that starts a process outside the worker seatbelt**, and the reason the module owns it: `agent/launcher.py` is already where the sandbox is configured, so both sides of that decision are readable in one file and auditable by one test. It writes no `CODEX_HOME`, no `config.toml` and no sandbox table; it takes an argv the caller composed and never one a worker supplied. On timeout it terminates the process group and sets `timed_out`, because the thing this exists to run is the thing Task 0 watched hang for 25 minutes. `owner` registers the process under a work item for the lifetime of the call, which is the only reason anything else can reach it.
  - `Launcher.kill_group(pid, grace=5.0)`, `Launcher.stop_unsandboxed(owner) -> bool`, `Launcher.stop_all_unsandboxed() -> list`. **These exist because the redesign created a reachability hole and this is where it is closed.** Moving the Editor out of the worker removed the only thing that ever killed it: `Launcher.stop` resolves a handle that `spawn` wrote, the batch Editor never went through `spawn`, the worker that requested it has already exited, and `SlotPool.run_batch` then blocks in `wait(timeout=batch_timeout)` — up to half an hour — on the pool thread, which is a daemon. Without these three, a Stop leaves a real Editor running on a cancelled item and a `serve` shutdown orphans it with the slot folder still open, which the next `ensure` reads as a slot some other Editor holds. `kill_group` takes the process group rather than `kill_pid`'s `ps`-derived descendants walk, because a wedged Editor's children may be re-parented and those are the ones still holding the folder; every process it kills was started with `start_new_session=True`, so the pid is its own group leader.
  - `SlotPool.run_batch(slot, reservation) -> dict`, the batch run itself: compose the argv with `agent.unity.batch_test_command` from the slot entry, run it through `run_unsandboxed` with the slot folder as cwd, then read the XML with `agent.unity.read_results` and write the summary to `<state_dir>/unity-batch.json`. The summary is `{"state", "exit_code", "results_file", "log_file", "seconds", "total", "passed", "failed", "result"}` with `state` one of `"ran"`, `"gap"` or `"timeout"`. It raises `SlotError(stage="probe")` only when a timed-out Editor is still holding the folder afterwards — a Unity process the pool could not kill is precisely §7's "a failing probe holds the slot for the operator's `recover-slot`". A gap never raises: the slot is fine and the worker owes the report.
  - `SlotPool(..., run_unsandboxed=None)`, injected so no test in this suite starts a process it did not write. `service.build` passes `launcher.run_unsandboxed`; a pool built without one refuses a batch grant with a `SlotError` rather than silently handing a worker a run that never happened.
  - `dispatch_message(..., resource=None)`. When given, the payload gains a `resource` object with `kind`, `mode`, `slot`, `folder`, `instance`, `mcp_address`, `account`, `commit`, `token_file`, `build_target`, `batch_result` and `results_dir`. **There is no `batch_command` key and no `unity` key, in either mode** — the worker receives neither an argv to run nor the Editor binary to run it with, because it never starts Unity. `batch_result` is the summary `SlotPool.run_batch` wrote, and it is `None` in `interactive` mode.
  - `Scheduler.launch` unchanged in signature and return value.
  - `agent.launcher.write_mcp_config` unchanged in signature; the scheduler hands it a per-runtime server entry rather than one shape for both writers.
  - `agent.deploy.plist(...)` unchanged in signature; the rendered job's `ProcessType` becomes `Standard`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_launcher.py`, the property the whole redesign rests on:

```python
    def test_an_unsandboxed_run_is_the_only_way_out_of_the_seatbelt_and_carries_no_sandbox_config(self):
        """Task 0's addendum: `Unity -batchmode -runTests` inside sandbox_workspace_write hangs for ever on a
        denied Mach lookup — 25 min at 0.0% CPU, no results file — with zero file-permission denials, while
        the same command unsandboxed exits 2 in 19 s. So the batch Editor is started here, not by the worker.
        This asserts the two halves of that: the run really happens, and it writes none of the isolated home,
        CODEX_HOME or sandbox_workspace_write machinery that would put it back inside the seatbelt."""
        launcher = Launcher(Path(self.tmp.name) / "runs", RUNTIMES["fake"], host="test")
        log = Path(self.tmp.name) / "unity-editor.log"
        result = launcher.run_unsandboxed([sys.executable, "-c", "import sys; sys.stderr.write('hi'); "
                                           "sys.exit(2)"], cwd=self.tmp.name, timeout=30, log=log)
        self.assertEqual((result.returncode, result.timed_out), (2, False))
        self.assertIn("hi", log.read_text(encoding="utf-8"))
        self.assertEqual(list((Path(self.tmp.name) / "runs").rglob("config.toml")), [])
        slow = launcher.run_unsandboxed([sys.executable, "-c", "import time; time.sleep(30)"],
                                        cwd=self.tmp.name, timeout=0.5)
        self.assertTrue(slow.timed_out)   # the 25-minute hang must end at a deadline, not at an operator

    def test_an_owned_unsandboxed_run_can_be_killed_by_item_and_takes_its_children_with_it(self):
        """The reachability hole the redesign opened. The batch Editor is a direct child of `serve`, not a
        descendant of any worker — the worker asked for the reservation and exited — so `stop()`'s handle
        lookup and its `descendants` walk both miss it, and `run_batch` is meanwhile blocked in `wait()` for
        up to `batch_timeout`. This asserts the two things that close it: the run is reachable by item id,
        and the kill takes the process GROUP, so a child that outlived its parent dies too. That child is
        the case that matters — it is what would still be holding the slot folder open."""
        launcher = Launcher(Path(self.tmp.name) / "runs", RUNTIMES["fake"], host="test")
        marker = Path(self.tmp.name) / "child.pid"
        # Parent spawns a grandchild that survives it, then sleeps; killing only the parent would leave the
        # grandchild alive, which is precisely the wedged-Editor shape Task 0 measured.
        script = ("import os, subprocess, sys, time, pathlib;"
                  "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']);"
                  "pathlib.Path(sys.argv[1]).write_text(str(c.pid));"
                  "time.sleep(60)")
        started = threading.Event()
        result = {}

        def run():
            started.set()
            result["run"] = launcher.run_unsandboxed([sys.executable, "-c", script, str(marker)],
                                                     cwd=self.tmp.name, timeout=60, owner="itm_batch")
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        started.wait(5)
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        grandchild = int(marker.read_text())

        self.assertTrue(launcher.stop_unsandboxed("itm_batch"))
        thread.join(15)
        self.assertFalse(thread.is_alive())
        self.assertFalse(Launcher.alive(grandchild))          # the group, not just the pid
        self.assertFalse(launcher.stop_unsandboxed("itm_batch"))  # deregistered; idempotent for Stop
```

`tests/test_launcher.py` gains `import threading` for this case if it is not already imported.

In `tests/test_slots.py`, beside `PoolTests`, the pool doing the run the worker is no longer allowed to do:

```python
    def test_the_pool_runs_the_batch_itself_and_hands_the_worker_the_results(self):
        """The worker never starts Unity; the launcher does, outside the sandbox. What the worker gets is
        this summary, and the argv is built from the slot entry alone — nothing the worker supplied."""
        self.pool().ensure()
        item = self.waiting(ISSUE, self.commit("fix"), "batch")
        pool = self.pool(mcp=FakeMcp(), run_unsandboxed=FakeUnity(total=4388, passed=4362, failed=26, code=2))
        self.assertEqual(pool.tick()["granted"], 1)
        argv = pool.run_unsandboxed.argv[-1]
        self.assertNotIn("-quit", argv)
        self.assertEqual(argv[argv.index("-projectPath") + 1], str(self.root / "editors" / "slot-1"))
        summary = json.loads((self.root / "runs" / item / "unity-batch.json").read_text(encoding="utf-8"))
        self.assertEqual((summary["state"], summary["exit_code"]), ("ran", 2))
        self.assertEqual((summary["total"], summary["failed"]), (4388, 26))
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "batch_busy")

    def test_a_batch_run_with_no_results_is_a_verification_gap_and_not_a_slot_failure(self):
        """Task 0 Step 4's trap: `-assemblyNames NoSuchAssembly` exits **0** and writes total="0". The slot is
        fine, so it stays in the pool and the item stays alive; the worker is told it has no evidence."""
        self.pool().ensure()
        item = self.waiting(ISSUE, self.commit("fix"), "batch")
        pool = self.pool(mcp=FakeMcp(), run_unsandboxed=FakeUnity(total=0, passed=0, failed=0, code=0))
        self.assertEqual(pool.tick()["granted"], 1)
        summary = json.loads((self.root / "runs" / item / "unity-batch.json").read_text(encoding="utf-8"))
        self.assertEqual((summary["state"], summary["exit_code"]), ("gap", 0))
        self.assertEqual(self.ledger.item(item)["state"], "queued")
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "batch_busy")

    def test_a_batch_editor_that_outlives_its_timeout_and_still_holds_the_folder_holds_the_slot(self):
        """The 25-minute hang, as the pool would meet it. A deadline is not enough on its own: what decides
        between "gap" and "hold" is whether the process is gone afterwards, which is the same liveness
        question editor_is_open asks and never the lock file."""
        self.pool().ensure()
        item = self.waiting(ISSUE, self.commit("fix"), "batch")
        pool = self.pool(mcp=FakeMcp(), run_unsandboxed=FakeUnity(hang=True),
                         editor_pid=lambda folder: 4242)
        self.assertEqual(pool.tick()["granted"], 0)
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "held")
        self.assertEqual(self.ledger.item(item)["state"], "failed")
```

with one more double beside `FakeMcp`:

```python
class FakeUnity:
    """Stands in for `Launcher.run_unsandboxed`. It writes the results file the way the real Editor does —
    before the exit code, and independently of it — because the whole contract is that the file and the code
    disagree. `hang=True` reproduces the addendum's Editor: the deadline expires and nothing is written."""

    def __init__(self, total=0, passed=0, failed=0, code=0, hang=False):
        self.totals, self.code, self.hang, self.argv = (total, passed, failed), code, hang, []

    def __call__(self, argv, *, cwd, timeout, log=None, env=None):
        self.argv.append(list(argv))
        if self.hang:
            return Unsandboxed(returncode=None, timed_out=True, seconds=timeout)
        total, passed, failed = self.totals
        results = Path(argv[argv.index("-testResults") + 1])
        results.parent.mkdir(parents=True, exist_ok=True)
        results.write_text(f'<test-run result="Failed(Child)" total="{total}" passed="{passed}" '
                           f'failed="{failed}" />', encoding="utf-8")
        return Unsandboxed(returncode=self.code, timed_out=False, seconds=1.0)
```

and `SlotFixture.pool` gains one more default, so Task 6's nine `PoolTests` cases keep passing unchanged:

```python
        kwargs.setdefault("run_unsandboxed", FakeUnity(total=4388, passed=4362, failed=26, code=2))
```

In `tests/test_scheduler.py`, inside `SchedulerTests`:

```python
    def test_a_batch_reservation_gives_the_worker_results_and_neither_an_argv_nor_the_slot(self):
        """The worker does not run Unity: under the Codex seatbelt the Editor hangs for ever on a denied Mach
        lookup (Task 0's addendum), so the pool ran it already and this is the evidence. Two absences matter
        as much as the presence — no argv to execute, and no write access to the slot folder."""
        item = self.granted_item(mode="batch")
        self.scheduler.tick()
        item_id, message, servers, _, _ = self.launcher.spawned[-1]
        self.assertEqual((item_id, servers), (item, {}))
        self.assertNotIn(str(self.slot_folder), self.launcher.spawn_writable)
        payload = json.loads(message.split("\n\n", 1)[1])
        self.assertNotIn("batch_command", payload["resource"])
        self.assertNotIn("unity", payload["resource"])   # nor the binary it would have run
        self.assertEqual(payload["resource"]["batch_result"]["total"], 4388)
        self.assertTrue(payload["resource"]["batch_result"]["results_file"].endswith("unity-tests.xml"))

    def test_an_interactive_reservation_injects_the_slot_address_and_not_its_folder(self):
        item = self.granted_item(mode="interactive")
        self.scheduler.tick()
        _, message, servers, _, _ = self.launcher.spawned[-1]
        self.assertEqual(servers, {"unity": {"url": "http://127.0.0.1:8080/mcp"}})
        self.assertNotIn(str(self.slot_folder), self.launcher.spawn_writable)
        payload = json.loads(message.split("\n\n", 1)[1])
        self.assertEqual(payload["resource"]["mode"], "interactive")
        self.assertTrue(payload["resource"]["token_file"].endswith("reservation.token"))
        self.assertNotIn("res_", message)  # the token itself never reaches the prompt on disk
        # No run happened and none is offered: an Editor is live on that folder and run_tests over MCP is
        # the verify path (Task 0 Step 5).
        self.assertIsNone(payload["resource"]["batch_result"])
        self.assertNotIn("batch_command", payload["resource"])
```

In `tests/test_deploy.py`, one assertion that the deployed job does not put Unity in the background band:

```python
    def test_the_serve_agent_runs_in_the_standard_band_so_unity_is_not_throttled(self):
        """Task 0 Step 6: the same EditMode suite took 105 s under launchd's Background band against 14 s
        foreground with warm caches, against a warm-cache control — 7.5x, and a launchd job's band is
        inherited by every process it spawns, which here means the worker and its Editor."""
        job = plistlib.loads(plist("com.kuaiwa.farmbot.serve", ["/bin/true"], "/tmp", "/tmp").encode("utf-8"))
        self.assertEqual(job["ProcessType"], "Standard")
```

`granted_item(mode=..., issue_id=ISSUE)` is a new helper on `SchedulerTests`. It extends the existing `item()` helper: it seeds an issue and a session, creates the work item with a pinned `target` (commit `"a" * 40`, `selected_at` the ISO string), claims it, calls `await_resource(..., "unity_slot", mode)`, calls `ledger.ensure_slot("unity_slot:1", kind="unity_slot", host="test", folder=self.slot_folder, mcp_address="http://127.0.0.1:8080/mcp", instance="slot-1@0123456789abcdef")`, calls `ledger.acquire("unity_slot", owner="pool", host="test")`, sets the slot to the mode's busy state (`interactive_busy` or `batch_busy`) and then `ledger.resume` — leaving the item queued with an active reservation on a busy slot, which is exactly the state the pool leaves behind. That `set_slot_state` stands in for `SlotPool.switch`, which is what writes the busy state in production: `acquire` alone leaves the slot `switching`, no pool thread runs in `tests/test_scheduler.py`, and Task 9's ordering test reads that state back. It sets `self.slot_folder` (a real temporary directory holding `ProjectSettings/ProjectVersion.txt`) and `self.unity_binary` (a touched file, passed as the slot entry's `unity` override). Neither is read by the scheduler any more — after this task `agent/scheduler.py` resolves no Editor — but both keep the slot entry honest and stop any later change quietly reaching into `/Applications`. In `batch` mode it also writes `<state_dir>/unity-batch.json` with `{"state": "ran", "exit_code": 2, "total": 4388, "passed": 4362, "failed": 26, "results_file": "<state_dir>/unity-tests.xml", ...}` — the summary `SlotPool.run_batch` leaves behind in production, and the reason a fresh batch worker has evidence at all. `waiting_item(mode=...)`, used by Task 9, stops one step earlier: it never acquires, so the reservation stays `queued`.

In `tests/test_dispatch.py`. The module's existing tests build their dicts inline, so this one does too rather than depending on names that are not there:

```python
    def test_a_resource_block_names_the_slot_and_the_token_file_but_never_the_token(self):
        message = dispatch_message(item={"id": "i-1", "skill": "fix", "identifier": "FARM-1",
                                         "target": None, "checkpoint": {}, "evidence": {}},
                                   issue={"identifier": "FARM-1", "title": "t", "description": "",
                                          "url": "u", "labels": [], "comments": []},
                                   skill_path="/skills/fix/SKILL.md", worktrees={"Farm-Client": "/w"},
                                   db_path="/db", runtime="codex", guidance="",
                                   budget={"max_hours": 2, "max_usd": 5},
                                   resource={"kind": "unity_slot", "mode": "batch", "slot": "unity_slot:1",
                                             "folder": "/e/slot-1", "commit": "a" * 40, "instance": None,
                                             "account": None, "mcp_address": "http://127.0.0.1:8080/mcp",
                                             "token_file": "/runs/i/reservation.token",
                                             "build_target": "OSXUniversal",
                                             "batch_result": {"state": "ran", "exit_code": 2, "total": 4388,
                                                              "passed": 4362, "failed": 26,
                                                              "results_file": "/runs/i/unity-tests.xml"},
                                             "results_dir": "/runs/i"})
        payload = json.loads(message.split("\n\n", 1)[1])
        self.assertEqual(payload["resource"]["slot"], "unity_slot:1")
        self.assertEqual(payload["resource"]["batch_result"]["failed"], 26)
        # A worker is handed evidence, never a way to produce it: no argv and no Editor path.
        self.assertNotIn("batch_command", payload["resource"])
        self.assertNotIn("unity", payload["resource"])
        self.assertNotIn("token_file", json.dumps(payload).replace('"token_file"', ""))  # path only, no value
```

Match the keyword names and the two dict shapes to whatever `tests/test_dispatch.py` already passes; the point of the test is the `resource` block, not a second copy of the existing fixture.

Also in `tests/test_launcher.py`, an injected server entry has to survive **both** writers, because §19's first risk is that the default runtime changes after Task 0:

```python
    def test_an_injected_http_server_is_written_in_each_runtime_s_own_shape(self):
        home = Path(self.tmp.name) / "home"
        home.mkdir()
        toml = write_mcp_config(home, "toml", {"unity": {"url": "http://127.0.0.1:8080/mcp"}}).read_text()
        self.assertIn("[mcp_servers.unity]", toml)
        self.assertIn('url = "http://127.0.0.1:8080/mcp"', toml)
        written = json.loads(write_mcp_config(home, "json",
                                              {"unity": {"type": "http", "url": "http://127.0.0.1:8080/mcp"}}).read_text())
        self.assertEqual(written["mcpServers"]["unity"]["type"], "http")
```

and leave the existing writable-roots assertion as it is. It must **not** grow a slot-folder case: after this task no slot folder is ever passed through `writable`, in either mode, and a test asserting that one can be would document the opposite of the rule.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -W error -m unittest discover -s tests -v -k reservation -k unsandboxed -k batch -k resource_block -k writable -k runtime_s_own_shape -k standard_band`
Expected: FAIL. `Launcher` has no `run_unsandboxed`, `SlotPool` has no `run_batch`, `dispatch_message()` rejects the `resource` keyword, the scheduler still passes `{}`, and the rendered plist still says `Background`. Count what `-k batch` matches before running it: Task 4's `test_the_batch_command_never_quits_never_drops_graphics_and_never_rewrites_sources` already matches and is expected to pass.

- [ ] **Step 3: Carry the resource in the dispatch payload**

In `agent/dispatch.py`, add `resource=None` to the signature and one line to the payload, after `"target"`:

```python
        "resource": resource,
```

and extend `AUTHORITY` with one sentence:

```python
    "When a resource block is present you hold that reservation for this run only: address the Editor with "
    "the instance id given and release it through the ledger CLI when you are done. You must NEVER start a "
    "Unity process yourself — not against the slot folder, not against a task worktree, not in batchmode "
    "and not through any script or tool that would. Unity cannot run inside your sandbox: it hangs for "
    "ever on a denied Mach lookup and there is no flag you can add that fixes it. The batch run was "
    "already performed for you, outside your sandbox, before you were started; resource.batch_result is "
    "its outcome and resource.batch_result.results_file is the XML. To run tests on an interactive slot, "
    "use the unity MCP server's run_tests tool (it returns a job_id, polls with get_test_job, and has "
    "clear_stuck for a job a domain reload orphaned); to get a fresh batch run, release your reservation "
    "and request a new batch one. A batch run's evidence is that XML, never the exit code: exit 0 means "
    "nothing ran and exit 2 means tests failed, so read total from the file and report a missing, "
    "unparseable or zero-total result — batch_result.state of 'gap' or 'timeout' — as a verification gap "
    "rather than as a pass or a failure. main is known-red at 26 of 4388; those failures are not yours."
```

- [ ] **Step 4: Run the batch outside the worker's sandbox**

In `agent/launcher.py`, beside `spawn` — deliberately beside it, so the two halves of the sandbox decision are one screen apart:

```python
# module level, beside Handle and Finished
Unsandboxed = namedtuple("Unsandboxed", "returncode timed_out seconds")
```

```python
    def run_unsandboxed(self, argv, *, cwd, timeout, log=None, env=None, owner=None):
        """Run one process OUTSIDE the worker seatbelt. This is the only method in FarmBot that does.

        Task 0's sandbox addendum: `Unity -batchmode -runTests` under [sandbox_workspace_write] hangs for
        ever — 25 minutes at 0.0% CPU, log frozen at 2,289 bytes, no results file — because the seatbelt
        denies a Mach lookup for com.apple.hiservices-xpcservice and the Editor blocks instead of exiting.
        There were zero file-permission denials, and Mach service access is not expressible through
        sandbox_workspace_write, so no writable_roots entry can fix it. The same command unsandboxed exits 2
        in 19 s. Hence: the launcher runs the Editor, the worker never does.

        The argv is the caller's, composed from configuration — never anything a worker supplied. Nothing
        here writes an isolated home, a CODEX_HOME or a sandbox table; this is a plain subprocess, and the
        deadline exists because the thing it runs is the thing that was watched hang.

        env=None on purpose: the Editor inherits the service's own environment, including the real HOME, and
        Task 0 Steps 3 and 6 measured licensing resolving under exactly that — foreground and under launchd.
        """
        start = time.monotonic()
        handle = open(log, "w", encoding="utf-8") if log else subprocess.DEVNULL
        kwargs = {"start_new_session": True} if os.name != "nt" else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        try:
            process = subprocess.Popen([str(part) for part in argv], cwd=str(cwd), env=env,
                                       stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.STDOUT,
                                       **kwargs)
            # Registered under the item, because this process is the one thing in FarmBot that nothing else
            # can reach. The worker that asked for the run has already exited; the Editor is a direct child
            # of `serve`, not a descendant of any worker, so `Launcher.stop`'s handle lookup and its
            # `descendants` walk both miss it entirely. Without this line a Stop leaves a real Editor running
            # for the rest of `batch_timeout` on a cancelled item, and a `serve` shutdown orphans it holding
            # the slot folder — the exact outcome Task 9 is named after preventing.
            if owner is not None:
                with self._unsandboxed_lock:
                    self._unsandboxed[owner] = process
            try:
                return Unsandboxed(process.wait(timeout=timeout), False, time.monotonic() - start)
            except subprocess.TimeoutExpired:
                # Take the group, not the pid: a hung Editor has children, and leaving them holding the slot
                # folder is what turns a gap into a held slot. `start_new_session=True` above made this pid a
                # process-group leader precisely so this call can exist; `kill_pid` walks `ps` for
                # descendants, which misses anything Unity re-parented on its way down.
                self.kill_group(process.pid)
                return Unsandboxed(None, True, time.monotonic() - start)
        finally:
            if owner is not None:
                with self._unsandboxed_lock:
                    self._unsandboxed.pop(owner, None)
            if log:
                handle.close()

    def kill_group(self, pid, grace=5.0):
        """SIGTERM the whole process group, then SIGKILL what is left.

        `kill_pid` exists beside this and is the wrong tool here: it walks `ps` for descendants, which sees
        only processes still parented to `pid`. A Unity Editor that is wedged — the case Task 0 measured,
        25 minutes at 0.0% CPU — leaves children that may have been re-parented, and those are exactly the
        ones still holding the slot folder open. Every process this method exists to kill was started by
        `run_unsandboxed` with `start_new_session=True`, so the pid is its own group leader and the group is
        the honest unit. Mirrors the killpg pair `stop()` already uses at agent/launcher.py:216 and :226.
        """
        try:
            group = os.getpgid(pid)
        except (ProcessLookupError, PermissionError):
            return
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(group, sig)
            except ProcessLookupError:
                return
            deadline = time.monotonic() + (grace if sig is signal.SIGTERM else 1.0)
            while time.monotonic() < deadline:
                if not self.alive(pid):
                    return
                time.sleep(0.05)

    def stop_unsandboxed(self, owner):
        """Kill the unsandboxed run registered for this item, if one is in flight. Returns True if it was.

        The reachability fix. `stop()` resolves `self._handles`, which `spawn` populates — and the batch
        Editor never went through `spawn`. So Stop, service shutdown and `recover-slot` all had no way to
        touch it, while `SlotPool.run_batch` sat in `process.wait(timeout=batch_timeout)` for up to half an
        hour on the pool thread. Both callers reach it through here.
        """
        with self._unsandboxed_lock:
            process = self._unsandboxed.get(owner)
        if process is None or process.poll() is not None:
            return False
        self.kill_group(process.pid)
        return True

    def stop_all_unsandboxed(self):
        """Every in-flight unsandboxed run, for service shutdown. The pool thread is a daemon, so without
        this an Editor outlives the process that started it and keeps the slot folder open across a
        restart — which the next `ensure` then reads as a slot another Editor already holds."""
        with self._unsandboxed_lock:
            owners = list(self._unsandboxed)
        return [owner for owner in owners if self.stop_unsandboxed(owner)]
```

`Launcher.__init__` gains the two attributes these need, beside the existing `self._handles = {}` and
`self._stopping = {}`:

```python
        self._unsandboxed = {}
        self._unsandboxed_lock = threading.Lock()
```

and `agent/launcher.py` gains `import threading` — the module does not import it today, and this is the
first thing in it touched from two threads at once: `run_unsandboxed` registers from the pool thread while
`Scheduler.stop` reads from the scheduler thread. `signal` and `os` are already imported.

In `agent/slots.py`, add the run and call it from `_hand_over`. `__init__` gains `self.run_unsandboxed = run_unsandboxed`:

```python
    def run_batch(self, slot, reservation):
        """The batch run itself, performed by the launcher outside any sandbox and never by the worker.

        The argv comes from the slot entry and the item's state directory only. That is the security
        property this redesign rests on: exactly one process in FarmBot escapes the seatbelt, and no part of
        its command line is a value a worker chose.
        """
        if self.run_unsandboxed is None:
            raise SlotError(f"{slot['slot_id']}: no unsandboxed runner; a batch slot cannot be granted",
                            stage="editor")
        entry = self.entries.get(slot["slot_id"], DEFAULTS)
        state_dir = Path(self.state_dir(reservation["item_id"]))
        state_dir.mkdir(parents=True, exist_ok=True)
        results, log = state_dir / "unity-tests.xml", state_dir / "unity-editor.log"
        results.unlink(missing_ok=True)   # never let a previous run's file be read as this run's evidence
        try:
            editor = agent.unity.editor_path(slot["folder"], override=entry.get("unity"))
        except agent.unity.UnityError as exc:
            # The message names every path tried. One re-queue at the tail is wasted on a misconfigured
            # host, and the second failure records the paths where an operator will read them.
            raise SlotError(f"{slot['slot_id']}: {exc}", stage="editor") from exc
        argv = agent.unity.batch_test_command(
            editor, slot["folder"], results=results, log=log,
            test_platform=entry.get("test_platform", "EditMode"),
            assemblies=tuple(entry.get("test_assemblies", ())),
            build_target=entry.get("build_target_argument"))
        # log=None: the Editor's own -logFile already points at `log`, and a second capture of the same
        # stream would give the worker two files that disagree about where the run stopped.
        # owner=: the only thread that could otherwise reach this Editor is this one, and it is about to
        # block in `wait()` for up to `batch_timeout`. Registering it under the item is what lets
        # `Scheduler.stop` and a service shutdown kill it (Task 7's `stop_unsandboxed`).
        run = self.run_unsandboxed(argv, cwd=slot["folder"], timeout=entry["batch_timeout"], log=None,
                                   owner=reservation["item_id"])
        summary = {"state": "ran", "exit_code": run.returncode, "seconds": round(run.seconds, 1),
                   "results_file": str(results), "log_file": str(log),
                   "total": None, "passed": None, "failed": None, "result": None}
        try:
            # Task 0 Step 4, the contract: the exit code is advisory and actively misleading — exit 0 means
            # *nothing ran*. The file decides, and total == 0 is a verification gap, not a pass.
            summary.update(agent.unity.read_results(results))
            if summary["total"] == 0:
                summary["state"] = "gap"
        except agent.unity.UnityError as exc:
            summary.update({"state": "gap", "result": str(exc)[:200]})
        if run.timed_out:
            summary["state"] = "timeout"
        # Written before the raise below, not after it: the operator who reads a held slot needs the record
        # of what happened on it, and a summary that only exists on the happy path is the one nobody has.
        (state_dir / "unity-batch.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        if run.timed_out:
            self.clear_stale_lock(slot["folder"], lambda: self.editor_pid(slot["folder"]) is not None)
            if self.editor_pid(slot["folder"]) is not None:
                # A Unity the pool could not kill still holds the folder. Spec §7: a failing probe holds the
                # slot for the operator's recover-slot; there is no second slot to try.
                raise SlotError(f"{slot['slot_id']}: the batch Editor outlived its "
                                f"{entry['batch_timeout']}s deadline and still holds the folder",
                                stage="probe")
        return summary
```

and in `_hand_over`, immediately after the `switch` succeeds:

```python
        if reservation["mode"] == "batch":
            try:
                self.run_batch(self.ledger.slot(slot_id), reservation)
            except SlotError as exc:
                self._switch_failed(reservation, exc)
                return False
```

Add `"batch_timeout": 1800` to `DEFAULTS` — 19 s measured for the EditMode suite (Task 0 Step 4) and 105 s for the same suite under launchd's Background band, so half an hour is headroom of two orders of magnitude and still ends the hang the addendum watched run past 25 minutes. Add `import json` and `import agent.unity` to the module if they are not already there.

A `gap` and a `timeout` are deliberately **not** failures of the switch: `run_batch` returns the summary and the item goes on to its worker, which reports the gap. Only an Editor that survived its own kill raises, because that is the one case where the *slot* is the problem.

- [ ] **Step 5: Build the servers and the resource block from the reservation**

In `agent/scheduler.py`, inside `launch`, after `paths = self._worktrees_for(...)`:

```python
        reservation = self.ledger.active_reservation(item["id"])
        servers, slot, resource = {}, None, None
        if reservation is not None and reservation["kind"] in skill.resources:
            slot = self.ledger.slot(reservation["resource"])
            entry = self.slot_entries.get(slot["slot_id"], {})
            state_dir = self.launcher.state_dir(item["id"])
            resource = {"kind": reservation["kind"], "mode": reservation["mode"], "slot": slot["slot_id"],
                        "folder": slot["folder"], "instance": slot["instance"], "account": slot["account"],
                        "mcp_address": slot["mcp_address"], "commit": reservation["commit_sha"],
                        "token_file": str(Path(state_dir) / "reservation.token"),
                        # No `unity` key. A worker that may never start the Editor has no use for its path,
                        # and handing it one would be an instruction the AUTHORITY block then has to argue
                        # against. Resolving the binary is the pool's job now, in run_batch and open_editor.
                        "build_target": entry.get("build_target_argument"),
                        # The outcome of the run the POOL already performed through the launcher, outside
                        # this worker's sandbox — never an argv for the worker to execute. Unity cannot run
                        # inside sandbox_workspace_write at all: it hangs on a denied Mach lookup with no
                        # file-permission denial to fix (Task 0's addendum). None on an interactive slot,
                        # where run_tests over MCP is the verify path (Task 0 Step 5).
                        "batch_result": (self._batch_result(state_dir)
                                         if reservation["mode"] == "batch" else None),
                        "results_dir": str(state_dir)}
            if reservation["mode"] == "interactive":
                # Enforcement is tool injection (spec §7): the server exists for this worker only while the
                # reservation is held, and the worker pins its own MCP session with set_active_instance.
                # The shape is the runtime's, not one guess for both: write_mcp_config's JSON writer emits
                # mcpServers, where an entry without a type is not an HTTP server.
                servers["unity"] = ({"url": slot["mcp_address"]}
                                    if self.launcher.runtime.mcp_format == "toml"
                                    else {"type": "http", "url": slot["mcp_address"]})
```

with one small reader beside `launch`:

```python
    @staticmethod
    def _batch_result(state_dir):
        """What the pool's own run left behind. Missing is itself a verification gap the worker must report,
        so it is represented rather than dropped — a batch worker with no `batch_result` key at all would
        read as "no slot" instead of "no evidence"."""
        try:
            return json.loads((Path(state_dir) / "unity-batch.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"state": "gap", "exit_code": None, "results_file": None,
                    "result": "the pool recorded no batch run for this reservation"}
```

then pass `resource=resource` to `dispatch_message` and `servers` instead of `{}` to `spawn`. **The `writable` list does not change at all**, and that is the point: no slot folder and no Unity host path is ever added to a worker's roots, in either mode. The worker reads the results XML in its own state directory, which `Launcher.spawn` already makes writable, and writes nothing in the slot.

`Scheduler.__init__` gains `slot_entries=None` (a `{slot_id: entry}` map, `{}` by default), which `service.build` fills from the same `slot_entry(raw)` list it hands the pool — a one-line change in `agent/service.py`, staged in this task's commit rather than left for a later one. `service.build` also hands `SlotPool` the launcher's own runner — `run_unsandboxed=launcher.run_unsandboxed`, on the same construction that already passes `state_dir=launcher.state_dir`. After this task `agent/scheduler.py` imports nothing from `agent/unity.py` at all: it names no Unity binary, no host path and no argv, which is the plainest statement of where the Editor is started and where it is not.

Finally, in `agent/deploy.py`, one line in `plist`:

```python
        # Standard, not Background. A launchd job's scheduling band is inherited by everything it spawns,
        # and Task 0 Step 6 measured the same EditMode suite at 105 s under Background against 14 s
        # foreground with warm caches — 7.5x, against a warm-cache control, so it is the band and not the
        # cache. A slot switch, an import and a test run each pay it.
        "ProcessType": "Standard",
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 -W error -m unittest discover -s tests -v -k reservation -k unsandboxed -k batch -k resource_block -k writable -k runtime_s_own_shape -k standard_band`
Expected: PASS. Nine new tests — one in `tests/test_launcher.py` (`run_unsandboxed`), three in `tests/test_slots.py` (the run, the gap, the timeout), two in `tests/test_scheduler.py`, one in `tests/test_dispatch.py`, one more in `tests/test_launcher.py` (the two writer shapes) and one in `tests/test_deploy.py` — plus whatever the substrings already match, including Task 4's batch-command test and Task 6's nine `PoolTests` cases, which stay green on the `run_unsandboxed` default added to `SlotFixture.pool`.

- [ ] **Step 7: Run the whole suite and commit**

Run: `python3 -W error -m unittest discover -s tests`
Expected: OK, 205 tests (+10).

```bash
git add agent/launcher.py agent/slots.py agent/scheduler.py agent/dispatch.py agent/service.py agent/deploy.py \
        tests/test_launcher.py tests/test_slots.py tests/test_scheduler.py tests/test_dispatch.py tests/test_deploy.py
git commit -m "feat(scheduler): run the batch in the launcher and inject tools from the reservation

A Unity batch run cannot happen inside a worker's sandbox: measured, the Editor hangs
for ever on a denied Mach lookup with no file-permission denial to fix. So the pool
composes the argv from the slot's own configuration and the launcher runs it outside
the seatbelt, and the worker is handed the results file, the parsed totals and the
exit code as evidence. A worker now gets no argv, no Editor path and no write access
to the slot in either mode; an interactive reservation still injects exactly one MCP
server for the life of that run. The serve agent also leaves launchd's background
band: the same EditMode suite costs 7.5x there, and a job's band is inherited by
every Editor it spawns.

Co-Authored-By: <model> <noreply@anthropic.com>"
```

---

### Task 8: The worker asks for a slot, gives it back, and the operator can see the pool

`agent/__main__.py:176-183` currently makes `await-resource` raise `no unity slots configured on this host`, and `skills/fix/SKILL.md` tells the worker that rungs 4 and 5 return an error. This task turns the refusal into a real request, adds the verbs that give a slot back and show its state, and rewrites the two documents in the same commit, as spec §11 requires.

**Why:** without a webhook, the operator's only way to create work is the ledger, and the only way to watch a slot is the CLI. This task is also what makes the live rehearsal in Task 11 runnable at all.

**Decision this task encodes:** two.

First, an interactive worker releases its own slot through `release-resource` after its own quiescence check, and the pool is the backstop rather than the primary path (spec §7). The worker's outcome is data: `--outcome quiescent` asks for a release, `--outcome unclean` asks for a hold, and either way the pool re-checks before acting on a slot the worker may have wedged. `cancel` stays an operator verb and is now safe, because `Ledger.cancel` cancels the item's reservations in the same transaction (Task 2).

Second, **`enqueue` does not manufacture authority and does not pretend to have a Linear session.** Spec §4's rule of authority says write-capable skills start only from delegation, "so every code change traces back to an explicit human act on the issue". Calling `ensure_session(..., delegation=True)` unconditionally would assert delegation that no one performed. So `enqueue` fetches the issue and refuses a write-capable skill unless that issue's `delegate_id` is this app user; the operator's own invocation is the explicit human act and is written to `audit`. And because the synthetic session id names no Linear agent session, every `create_activity` for such an item would fail against the real API: `enqueue` records that on the item, the worker falls back to an issue comment, and the operating contract says an enqueued item posts no session activities. Otherwise §9's reporting surface would be silently dead for exactly the items Task 11 rehearses.

**Files:**
- Modify: `agent/__main__.py` (`parser`, `run`)
- Modify: `agent/service.py` (`enqueue` and `slots` subcommands)
- Modify: `agent/scheduler.py` (a `local-` session id posts an issue comment instead of a session activity)
- Modify: `skills/fix/SKILL.md` (the verification ladder, rungs 4 and 5)
- Modify: `docs/operating-contract.md` (authority table, limits, the resource paragraph)
- Test: `tests/test_cli.py`, `tests/test_service.py`

**Interfaces:**
- Consumes: the ledger methods from Tasks 2 and 6; `resolve_token(args)` in `agent/__main__.py`, which already reads `--token-file`.
- Produces:
  - `await-resource --item --resource --mode {interactive,batch}` now returns the item view with `state` `awaiting_resource`, or fails with the ledger's message when the item has no pin or already holds a slot.
  - `release-resource --item --token-file --outcome {quiescent,unclean}` — records the worker's own verdict and, for `quiescent`, releases; for `unclean`, holds. Returns the reservation view.
  - `reservations`, `slots` — operator readers, token-free.
  - `recover-slot --slot --reason` — operator verb, the only way out of `held`.
  - `python3 -m agent.service enqueue --issue <uuid> --skill fix [--commit <sha>] [--session <id>]` — creates an issue snapshot from a fetched issue, a session and a queued work item with a pinned target, so the whole flow runs with no webhook. Returns the work item id.
  - `python3 -m agent.service slots` — the pool's view: slot states, parked commits, queue depth.

- [ ] **Step 1: Write the failing tests**

In `tests/test_cli.py`, replace `test_await_resource_is_refused_without_slots_and_errors_are_clean` (lines 191-195) with:

```python
    def test_a_worker_requests_a_slot_and_the_request_is_queued(self):
        item = self.seeded_item(target={"repository": "Farm-Client", "requested_ref": "main",
                                        "commit_sha": "a" * 40, "server_environment": "公共测试服",
                                        "selected_at": SELECTED_AT})
        token = self.run_cli("claim", "--item", item, "--worker-id", "w")["token"]
        view = self.run_cli("await-resource", "--item", item, "--token", token,
                            "--resource", "unity_slot", "--mode", "batch")
        self.assertEqual((view["state"], view["needs_resource"]), ("awaiting_resource", "unity_slot:batch"))
        self.assertEqual(self.run_cli("reservations")[0]["mode"], "batch")

    def test_an_unpinned_item_is_told_why_it_cannot_have_a_slot(self):
        item = self.seeded_item()
        token = self.run_cli("claim", "--item", item, "--worker-id", "w")["token"]
        process = self.run_cli("await-resource", "--item", item, "--token", token,
                              "--resource", "unity_slot", "--mode", "batch", success=False)
        self.assertIn("pinned commit", process.stderr)
        self.assertEqual(process.stdout, "")

    def test_a_worker_releases_its_own_slot_and_an_unclean_one_is_held(self):
        item, token_file = self.granted_item(mode="interactive")
        view = self.run_cli("release-resource", "--item", item, "--token-file", str(token_file),
                            "--outcome", "quiescent")
        self.assertEqual(view["state"], "released")
        self.assertEqual(self.run_cli("slots")[0]["state"], "switching")
        # granted_item parks the slot first, because no pool thread runs in this file and Ledger.acquire
        # only grants a slot in FREE_SLOT_STATES — release() left it 'switching'.
        other, other_token = self.granted_item(mode="interactive", issue_id=OTHER)
        self.run_cli("release-resource", "--item", other, "--token-file", str(other_token), "--outcome", "unclean")
        self.assertEqual(self.run_cli("slots")[0]["state"], "held")
        self.run_cli("recover-slot", "--slot", "unity_slot:1", "--reason", "operator closed Unity")
        self.assertEqual(self.run_cli("slots")[0]["state"], "idle_closed")
```

`seeded_item` gains an optional `target=None` and `SELECTED_AT` is imported from `test_ledger`, the same ISO-8601 constant every other fixture in this plan uses. `granted_item(mode=..., issue_id=ISSUE)` is a new helper on `CliTests` that seeds a pinned item, claims it, calls `await-resource`, registers `unity_slot:1` through the ledger, **sets the slot to `idle_closed` if it is not already free**, acquires the reservation, sets the slot to the mode's busy state, and writes the raw token to `<state_dir>/reservation.token` at mode 0600, returning `(item_id, token_path)` — the state the pool leaves behind for a worker. Those two `set_slot_state` calls stand in for the pool: `SlotPool.park_idle` is what returns a `switching` slot to the pool in production and `SlotPool.switch` is what writes the busy state, and no pool thread runs in `tests/test_cli.py`. Without the first, `Ledger.acquire` returns `None` on the second call — `release` left the slot `switching`, which is not in `FREE_SLOT_STATES` — and the `--outcome unclean` assertion is unreachable.

In `tests/test_service.py`:

```python
    def test_enqueue_creates_a_pinned_work_item_without_any_webhook(self):
        item_id = enqueue(self.config, issue_ref=ISSUE, skill="fix", commit="a" * 40)["id"]
        ledger = Ledger(Paths(self.config).ledger)
        self.addCleanup(ledger.close)
        item = ledger.item(item_id)
        self.assertEqual((item["state"], item["skill"]), ("queued", "fix"))
        self.assertEqual(item["target"]["commit_sha"], "a" * 40)

    def test_enqueue_refuses_a_write_capable_skill_on_an_issue_nobody_delegated(self):
        """spec §4: fix, fgui and feature start only from delegation, so that every code change traces back
        to an explicit human act on the issue. enqueue is an operator shortcut past the webhook, not past
        the rule of authority."""
        (self.stub / "issue.json").write_text(json.dumps(issue(labels=["Bug"], delegate_id=None)),
                                              encoding="utf-8")
        with self.assertRaises(RuntimeError) as caught:
            enqueue(self.config, issue_ref=ISSUE, skill="fix", commit="a" * 40)
        self.assertIn("delegate", str(caught.exception).lower())
```

Both run under `FARMBOT_LINEAR_STUB_DIR`, so `fetch_issue` reads the stub's `issue.json` and nothing touches the network. The stub's fixture issue carries `delegate_id=APP`, which is what makes the first test's enqueue legitimate.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -W error -m unittest discover -s tests -v -k slot -k resource -k enqueue`
Expected: FAIL. `await-resource` raises `no unity slots configured on this host`, and there is no `release-resource`, `slots`, `reservations`, `recover-slot` or `enqueue`.

- [ ] **Step 3: Add the verbs**

In `agent/__main__.py`, inside `parser()`, after the existing `await-resource` block:

```python
    release = cmd("release-resource", "--item", token=True)
    release.add_argument("--outcome", required=True, choices=["quiescent", "unclean"])
    cmd("reservations"); cmd("slots")
    cmd("recover-slot", "--slot", "--reason")
```

and in `run()`, replace the whole `await-resource` branch with:

```python
    if c == "await-resource":
        return ledger.await_resource(args.item, resolve_token(args), args.resource, args.mode)
    if c == "release-resource":
        reservation = ledger.active_reservation(args.item)
        if reservation is None:
            raise LedgerError("this item holds no resource")
        if args.outcome == "unclean":
            # The worker says it left the Editor in a state it could not settle; the slot waits for an operator.
            return ledger.hold(reservation["reservation_id"], "worker reported an unclean release")
        ledger.release(reservation["reservation_id"], resolve_token(args), "worker reported quiescent")
        return ledger.reservation(reservation["reservation_id"])
    if c == "reservations":
        return ledger.reservations()
    if c == "slots":
        return ledger.slots()
    if c == "recover-slot":
        return ledger.recover_slot(args.slot, args.reason)
```

The `load_config()` lookup that produced the old refusal goes away with it, and with it the only place in `agent/__main__.py` that read the host configuration.

- [ ] **Step 4: Add the operator's way in without a webhook**

In `agent/service.py`:

```python
def enqueue(config, *, issue_ref, skill, commit=None, session=None):
    """Create a work item directly, for a host whose webhook cannot be delivered.

    Two honesties. The delegation flag is read from the issue, never asserted: spec §4 says a write-capable
    skill starts only from delegation, and the operator's invocation of this command is recorded in `audit`
    as the human act rather than being disguised as one in Linear. And the session id is synthetic — no
    Linear agent session exists for it — so `activities_supported` is False on it and the worker reports
    through an issue comment instead of calling create_activity once per step and failing every time.
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
```

`WRITE_SKILLS` is the set `agent/receiver.py` already defines; import it rather than writing a second copy. `Ledger.note(item_id, kind, reason)` is a three-line public wrapper around the existing private `_audit`, so an operator action leaves the same trail a webhook would. `agent/scheduler.py:76`'s `create_activity` call gains one guard: a session id that begins `local-` gets `api.create_comment(issue_id, body)` instead, because Linear has no session to attach an activity to.

and extend `main`:

```python
    parser.add_argument("command", choices=["configure", "serve", "status", "seed-clones", "install-launchd",
                                            "enqueue", "slots"])
    parser.add_argument("--issue"); parser.add_argument("--skill", default="fix"); parser.add_argument("--commit")
```

```python
    if args.command == "enqueue":
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
```

- [ ] **Step 5: Rewrite the skill's verification ladder**

In `skills/fix/SKILL.md`, replace rungs 4 and 5 and the sentence beginning `Until slots exist` with:

```markdown
4. EditMode or PlayMode fixtures need a Unity slot in batch mode. Checkpoint your handoff, then
   `await-resource --resource unity_slot --mode batch` and exit. **You are asking for a run, not for
   permission to perform one.** While you are gone the pool switches the slot to your pinned commit and
   FarmBot runs the Editor itself, outside your sandbox, because Unity cannot run inside it: measured on
   2026-09-19, `Unity -batchmode -runTests` under the worker seatbelt hung for 25 minutes at 0.0% CPU and
   wrote nothing, dying on a denied Mach service lookup that no sandbox setting can grant. **Never start a
   Unity process yourself, in any mode, by any route.** A fresh worker resumes with a `resource` block
   carrying the slot folder, the pinned commit, a results directory and `resource.batch_result` — the
   outcome of the run that already happened: `state`, `exit_code`, `seconds`, `results_file`, `log_file`,
   and `total` / `passed` / `failed` parsed from the XML. **The exit code is advisory only and is actively
   misleading: exit 0 means *nothing ran* and exit 2 means *tests failed***. Your evidence is
   `resource.batch_result.results_file`: read it for the failing tests, and treat `total` greater than zero
   as the precondition for claiming anything at all. `state` says which case you are in — `"ran"` is
   evidence, `"gap"` (missing, unparseable or `total="0"`, usually a wrong `-assemblyNames`) and
   `"timeout"` are **verification gaps**, neither a pass nor a failure, and you report them as gaps.
   **`main` is known-red at 26 failures of 4388**, almost all configuration-table contract tests; they are
   in the spike record by class, they will appear in your run, and they are not caused by your change — say
   so rather than treating them as a regression. If you need another run, `release-resource --outcome
   quiescent` and request a fresh batch reservation: one grant is one run. Otherwise release and move on.
5. Behaviour no test covers needs an interactive slot: `await-resource --resource unity_slot --mode
   interactive`. The fresh worker gets one MCP server named `unity`; call `set_active_instance` with the
   `resource.instance` from your launch message before anything else, because the server is shared per user
   and the selection is per MCP session. **`resource.batch_result` is `null` here and there is no argv and
   no Editor path anywhere in your `resource` block — never start a Unity process of your own.** An Editor
   is already running on that folder, and a second `Unity -batchmode` on a folder a live Editor holds
   corrupts it. To run tests from an interactive slot, use the MCP server's own `run_tests` tool: it is
   asynchronous, returns a `job_id`, is polled with `get_test_job` (which takes a `wait_timeout`, so poll
   with one rather than in a busy loop), and exposes `clear_stuck` for a job a domain reload orphaned. It
   runs inside the Editor that is already open, which is why an interactive slot needs no second process at
   all. Leave the Editor in Edit Mode with nothing compiling, then
   `release-resource --outcome quiescent`; if you cannot, `--outcome unclean`, which holds the slot for an
   operator instead of handing a wedged Editor to the next worker.

You hold at most one slot at a time: release before requesting a different mode. **Never start a Unity
process — not on a slot, not on a task worktree, not in batch mode, not through a script, a build tool or a
helper that would do it for you.** Unity does not work inside your sandbox and FarmBot runs it for you,
outside, before you are started. You never edit the slot folder either, and no slot ever runs a player
build — slots budget an import-only `Library/` and a player build triples it.

If your work item was created by an operator with `enqueue` rather than by a Linear delegation, its session
is local and Linear has no agent session for it: report through an issue comment and do not expect session
activities to appear.
```

- [ ] **Step 6: Rewrite the operating contract**

In `docs/operating-contract.md`: in the authority table, change `fix`'s Resources cell from `Unity slot (not yet available)` to `Unity slot (one, batch or interactive, two-phase)`. In the work-item states paragraph, replace `A waiting item has no process and holds no resource.` with `A waiting item has no process. An item in awaiting_resource holds a queued reservation; only the pool's grant turns it back into queued work.` And replace the first bullet under `## Limits in this phase` with:

```markdown
- One host at a time (the Mac since 2026-09-18; Windows follows in its own plan) and one Unity slot. Two
  items that both need Unity serialize on it in arrival order; a worker holds at most one slot and releases
  it after its own quiescence check. **A worker never starts a Unity process**: the Editor does not work
  inside a worker's sandbox, so FarmBot performs a batch run itself, outside that sandbox, between the
  request and the worker that reads its results — one grant is one run. A failing probe holds the slot
  until an operator runs `recover-slot`. No slot is ever released on a timer. A slot runs Edit Mode and PlayMode fixtures and never
  a player build: the budget is an import-only `Library/`, and a player build adds several GB of `Bee` and
  `BuildCache` to it. The receiver and the tunnel run as launchd agents and restart at login; while the host
  config names no named tunnel, the public hostname changes whenever the tunnel restarts and must be
  re-entered in Linear, and until it is, work is created with `python3 -m agent.service enqueue`. An
  enqueued item has a local session that Linear does not know about, so it reports through issue comments
  and posts no session activities; `enqueue` still refuses a write-capable skill on an issue that was never
  delegated to FarmBot, because the rule of authority is not what the missing webhook excuses.
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python3 -W error -m unittest discover -s tests -v -k slot -k resource -k enqueue`
Expected: PASS. The three CLI tests and the two service tests are green, and the `PoolTests` and `SwitchTests` cases still pass. One pre-existing test disappears here: `test_await_resource_is_refused_without_slots_and_errors_are_clean` is replaced, not added to.

- [ ] **Step 8: Run the whole suite and commit**

Run: `python3 -W error -m unittest discover -s tests`
Expected: OK, 209 tests (+5 new, −1 replaced).

```bash
git add agent/__main__.py agent/service.py agent/scheduler.py skills/fix/SKILL.md \
        docs/operating-contract.md tests/test_cli.py tests/test_service.py
git commit -m "feat(cli): request, release and recover a Unity slot

await-resource stops being a refusal. A worker gives the slot back with
release-resource and its own verdict, an operator reads slots and reservations and
clears a held slot with recover-slot, and enqueue creates a pinned work item on a
host whose webhook cannot be delivered. The skill and the operating contract say so.

Co-Authored-By: <model> <noreply@anthropic.com>"
```

---

### Task 9: Stop and recovery never orphan a slot

Spec §7: "Stop cancels queued reservations, marks active ones `cancel_requested`, kills the worker, then applies the release rule." The order matters in both directions.

**Why:** `Scheduler.stop` kills the worker before taking its lock, deliberately, so a human pressing Stop is never queued behind an in-flight tick — done-criterion 2 gives it five seconds. A quiescence probe talks to a possibly wedged Editor over MCP and cannot run inside that budget. And in the other direction, an item stopped while in `awaiting_resource` must have its queued reservation cancelled, or the pool will later perform a multi-minute slot switch for a worker that no longer exists.

**Decision this task encodes:** `Scheduler.stop` does three fast things — cancel reservations, kill, cancel the item — and never probes. The probe happens on the pool thread, on the next pool tick, which is what makes `cancel_requested` a state rather than a flag. Every other path that drives an item terminal (`_reap`, `_recover`, a worker's own `finish`) needs no new code at all, because `reservations_to_settle` already selects on the item's state.

**Files:**
- Modify: `agent/scheduler.py` (`stop`)
- Test: `tests/test_scheduler.py`, `tests/test_slots.py`

**Interfaces:**
- Consumes: `Ledger.cancel_reservations` from Task 2.
- Produces: no signature change. `Scheduler.stop(item_id, reason)` gains one call, before the kill.

- [ ] **Step 1: Write the failing tests**

In `tests/test_scheduler.py`:

```python
    def test_stop_cancels_the_reservation_before_it_kills_the_worker(self):
        """Ordering is asserted, not implied: both orders leave the same end state, so the fake launcher
        samples the reservation at the moment stop() reaches it. A wall-clock assertion here would prove
        nothing — FakeLauncher.stop appends to a list and returns, so it is fast whatever the real one does.
        Done-criterion 2's five-second budget is defended by the rule that stop() only touches SQLite and
        signals, which this ordering assertion is the test of."""
        item = self.granted_item(mode="interactive")
        self.scheduler.tick()
        seen = []
        self.launcher.on_stop = lambda i: seen.append(self.ledger.active_reservation(i)["state"])
        self.scheduler.stop(item, "Linear stop")
        self.assertEqual(seen, ["cancel_requested"])
        reservation = self.ledger.active_reservation(item)
        self.assertEqual(reservation["state"], "cancel_requested")
        self.assertEqual(self.ledger.item(item)["state"], "cancelled")
        # Still held: stop() never touches the slot row. granted_item leaves it in the busy state the pool's
        # switch would have written, so this asserts that stop left it alone rather than releasing it — the
        # release is the pool's, on its next tick, after a quiescence probe that does not fit five seconds.
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "interactive_busy")

    def test_stop_on_an_item_that_is_only_waiting_kills_its_queued_request(self):
        item = self.waiting_item(mode="batch")
        self.scheduler.stop(item, "Linear stop")
        self.assertEqual([r["state"] for r in self.ledger.reservations() if r["item_id"] == item], ["cancelled"])
        self.assertEqual(self.ledger.item(item)["state"], "cancelled")

    def test_stop_reaches_the_batch_editor_the_worker_no_longer_owns(self):
        """This task is named 'Stop and recovery never orphan a slot', and the sandbox redesign put the one
        process that can orphan one outside everything Stop used to reach. The Editor is started by the
        launcher on the pool thread, the worker that asked for it has already exited, and `launcher.stop`
        resolves a `spawn` handle that was never written for it. So Stop must kill it explicitly, and it
        must do so BEFORE the worker kill, for the same reason the cancel comes first: the pool thread is
        sitting in `wait()` and the sooner it is released the sooner the slot can settle."""
        item = self.waiting_item(mode="batch")
        self.scheduler.stop(item, "Linear stop")
        self.assertEqual(self.launcher.unsandboxed_stopped, [item])
        self.assertLess(self.launcher.order.index(("unsandboxed", item)),
                        self.launcher.order.index(("worker", item)))
```

`FakeLauncher` gains the mechanism both ordering assertions need: `self.on_stop = None`,
`self.unsandboxed_stopped = []` and `self.order = []` in `__init__`; `self.on_stop and self.on_stop(item_id)`
plus `self.order.append(("worker", item_id))` as the first statements of `stop`; and a
`stop_unsandboxed(self, owner)` that appends to both `unsandboxed_stopped` and `order` and returns `True`.
Without that last method the fake no longer matches the real `Launcher`, and `Scheduler.stop` would raise
`AttributeError` in every existing Stop test rather than in a new one — which is the failure this test is
here to make loud.

In `tests/test_slots.py`, inside `PoolTests`:

```python
    def test_the_pool_settles_a_cancel_requested_reservation_as_cancelled_and_parks(self):
        self.pool().ensure()
        item = self.waiting(ISSUE, self.commit("fix"), "interactive")
        pool = self.pool(mcp=FakeMcp())
        pool.tick()
        self.ledger.cancel_reservations(item, "Linear stop")
        self.ledger.cancel(item, "Linear stop")
        self.assertEqual(pool.tick()["settled"], 1)
        self.assertEqual([r["state"] for r in self.ledger.reservations()], ["cancelled"])
        self.assertEqual(self.ledger.slot("unity_slot:1")["parked_commit"],
                         self.trees.resolve_commit("Farm-Client"))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -W error -m unittest discover -s tests -v -k stop -k settles`
Expected: FAIL. Stop leaves the reservation `active`, and the queued request of a waiting item survives.

- [ ] **Step 3: Cancel first, then kill**

In `agent/scheduler.py`, at the top of `stop`:

```python
    def stop(self, item_id, reason):
        # First, so a worker polling its reservation sees cancel_requested and so the pool can no longer hand
        # a slot to an item whose worker is about to be dead. All three steps here are SQLite and signals; the
        # quiescence probe belongs to the pool thread and would not fit the 5-second budget (spec §7, §17).
        try:
            self.ledger.cancel_reservations(item_id, reason)
        except LedgerError:
            pass
        # The batch Editor is not a worker and never went through `spawn`, so `launcher.stop` below cannot
        # see it: its handle lookup and its `descendants` walk both start from a worker pid, and by now that
        # worker has already exited — it asked for the reservation and quit. Killing the group here is what
        # keeps this task's title true. It is one `killpg` plus a bounded wait, well inside the 5-second
        # budget of done-criterion 2, and it is idempotent when no batch run is in flight.
        self.launcher.stop_unsandboxed(item_id)
        killed = self.launcher.stop(item_id)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -W error -m unittest discover -s tests -v -k stop -k settles`
Expected: PASS, four new tests plus the existing Stop tests.

- [ ] **Step 5: Run the whole suite and commit**

Run: `python3 -W error -m unittest discover -s tests`
Expected: OK, 213 tests (+4).

```bash
git add agent/scheduler.py tests/test_scheduler.py tests/test_slots.py
git commit -m "fix(scheduler): Stop cancels reservations before it kills the worker

A stop on a waiting item left its queued request alive, so the pool would have paid
for a slot switch for a worker that no longer existed. An active reservation is now
marked cancel_requested and keeps the slot until the pool's own probe releases or
holds it, which keeps the kill inside its five-second budget.

Co-Authored-By: <model> <noreply@anthropic.com>"
```

---

### Task 10: Two items serialize on the one slot, offline

The task whose only purpose is to prove Phase 1 done-criterion 3. It runs with the `fake` runtime, the Linear stub and a fake slot pool collaborator, drives the receiver and the scheduler in-process, and needs no webhook, no tunnel and no Unity.

**Why:** criterion 3 is a claim about ordering under contention, and ordering under contention is exactly what a live rehearsal demonstrates least reliably. The offline test is the regression guard; Task 11 is the evidence that the same code drives a real Editor.

**Files:**
- Modify: `tests/test_end_to_end.py` (extract the fixture into a mixin; add `TwoPhaseTests`)
- Modify: `agent/config.py` (`StubLinear.fetch_issue` answers `issue-<ref>.json` when it exists)
- Modify: `tests/fake_cli.py` (pick the `:resumed` script when the launch message carries a `resource` block)
- Test: itself

**Interfaces:**
- Consumes: `build(config)`, which now returns a `pool`; `tests/fake_cli.py`'s `cli` mode and its `FAKE_CLI_STEPS`; the `RUNTIMES["fake"]` runtime; `FakeUnity` from `tests/test_slots.py`, imported rather than copied so the double that stands in for the Editor is the same one everywhere.
- Produces: no production **behaviour**. Two test classes over one shared fixture, plus one line in `agent.config.StubLinear.fetch_issue` — which lives in `agent/` but is the offline Linear double, reached only when the config names the `stub` API, and therefore test scaffolding wherever it sits. Nothing on a real Linear path changes.

**Three things the fixture has to get right**, each of which the obvious version gets wrong:

- **The shared fixture is a mixin, not a base `TestCase`.** Subclassing `EndToEndTests` would re-run its two heavyweight, timing-sensitive tests a second time under a setUp that has built a real slot worktree and a pool — duplicated work for no coverage, and two tests the totals would not account for. The setup moves into a plain `Fixture` class that does not derive from `TestCase`, and `EndToEndTests` and `TwoPhaseTests` both use it.
- **The stub has to answer per issue.** `agent.config.StubLinear.fetch_issue` ignores its argument and always returns `issue.json`, so two `enqueue` calls would observe the same issue row and the second `create_work_item` would raise `an active work item already exists for this issue`. The stub gains one line: it reads `issue-<ref>.json` when that file exists and falls back to `issue.json` otherwise, and the fixture writes one file per issue. The test then asserts the two items really do carry different `issue_id`s.
- **The proof is durable evidence, not a collaborator's call log.** A batch switch calls the MCP zero times by design (Task 4 asserts exactly that), and `park` never calls it either, so a recording double can only ever see the interactive commit — an assertion like `mcp.switched == [commit_a, commit_b, main]` is unsatisfiable and an executor would "fix" the proof rather than the system. Criterion 3 is asserted against the reservations table, `slots.parked_commit`, the audit trail and `Worktrees.head` sampled at each stage.
- **The unsandboxed runner is substituted too, for the same reason the MCP is.** `service.build` hands the real pool `launcher.run_unsandboxed`, and after Task 7 a batch grant calls it — so a fixture that replaced only `mcp` would start a real Editor from the end-to-end suite, against the global constraint that no test in this suite starts Unity. The fixture replaces both, and its slot entry carries a `unity` override pointing at a touched file so `editor_path` never looks in `/Applications` either.

- [ ] **Step 1: Write the failing tests**

In `tests/test_end_to_end.py`, extract the existing `setUp` body into `class Fixture:` (a plain object with a `build_environment(self)` method — it takes no arguments, because it uses `self.addCleanup` and builds its own temporary root), leave `EndToEndTests` calling it, and add a second class. The fixture gains a real slot: a bare clone, an editors root, one configured slot entry, and a `SlotPool` whose `mcp` is `RecordingMcp`. Each item gets its own `FAKE_CLI_STEPS`, selected through one script that reads `FARMBOT_ITEM_ID`.

```python
class TwoPhaseTests(unittest.TestCase, Fixture):
    def setUp(self):
        self.build_environment()   # the mixin's own method; it uses self.addCleanup, so it needs a TestCase
        self.pool = self.c.pool
        self.pool.mcp = RecordingMcp()
        # Both collaborators, not just the MCP: after Task 7 a batch grant calls run_unsandboxed, and the one
        # build() supplied is the real launcher's. FakeUnity writes the results file the way a real batch run
        # does, so `resource.batch_result` reaches the resumed fake worker exactly as it would in production.
        self.pool.run_unsandboxed = FakeUnity(total=4388, passed=4362, failed=26, code=2)
        self.pool.ensure()
        self.commit_a = self.new_commit("a")
        self.commit_b = self.new_commit("b")
        self.folder = Path(self.c.ledger.slot("unity_slot:1")["folder"])
        self.heads = []

    def drain(self, timeout=60):
        """One turn of the three loops serve() runs, in this thread, in the order serve() starts them —
        yielding *between* the scheduler and the pool, because pool.tick() settles and grants in one call and
        a transient state observed only after it would never be seen."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.c.receiver.process_one()
            self.c.scheduler.tick()
            yield "before_pool"
            self.pool.tick()
            self.heads.append(self.c.worktrees.head(self.folder))
            yield "after_pool"
            time.sleep(0.1)
        self.fail(f"the loops did not reach a terminal state within {timeout}s")

    def terminal(self, item_id):
        return self.c.ledger.item(item_id)["state"] in ("blocked", "delivered", "failed", "cancelled")

    def test_two_items_needing_unity_serialize_on_the_single_slot(self):
        first = self.enqueue_item(ISSUE, mode="batch", commit=self.commit_a)
        second = self.enqueue_item(OTHER, mode="interactive", commit=self.commit_b)
        self.assertNotEqual(self.c.ledger.item(first)["issue_id"], self.c.ledger.item(second)["issue_id"])
        for phase in self.drain():
            if phase == "before_pool" and self.terminal(first) and not self.terminal(second):
                # Observed between the scheduler and the pool: the first item is done and the second is still
                # waiting, which is the only window in which that pair of states exists.
                self.assertEqual(self.c.ledger.item(second)["state"], "awaiting_resource")
                break
        for _ in self.drain():
            if self.terminal(first) and self.terminal(second):
                break
        # Durable evidence: exactly two reservations were ever acquired, in arrival order, never overlapping.
        acquired = [r for r in self.c.ledger.reservations() if r["acquired_at"] is not None]
        self.assertEqual([(r["item_id"], r["mode"]) for r in acquired],
                         [(first, "batch"), (second, "interactive")])
        self.assertLessEqual(acquired[0]["released_at"], acquired[1]["acquired_at"])

    def test_the_slot_visits_each_pinned_commit_and_is_parked_back_on_main(self):
        first = self.enqueue_item(ISSUE, mode="batch", commit=self.commit_a)
        second = self.enqueue_item(OTHER, mode="interactive", commit=self.commit_b)
        for _ in self.drain():
            if self.terminal(first) and self.terminal(second):
                break
        main = self.c.worktrees.resolve_commit("Farm-Client")
        # The batch item visited commit_a, the interactive item commit_b, and the slot ended on main. `heads`
        # is git's own answer sampled after every pool tick, so no collaborator has to have been told. The
        # last three distinct values are asserted because the first ticks happen while the slot is still
        # parked on main, before either worker has asked for it.
        observed = [h for i, h in enumerate(self.heads) if i == 0 or h != self.heads[i - 1]]
        self.assertEqual(observed[-3:], [self.commit_a, self.commit_b, main])
        parked = [row["reason"] for row in self.c.ledger.connection.execute(
            "SELECT reason FROM audit WHERE item_id='unity_slot:1' AND kind='slot'")]
        self.assertEqual(parked[-1], "idle_open")   # spec §7: after an interactive run the Editor stays open
        slot = self.c.ledger.slot("unity_slot:1")
        self.assertEqual(slot["parked_commit"], main)
        self.assertEqual([o["result"]["aggregate"] for o in self.c.ledger.identity_observations(second)],
                         ["match"])
        self.assertEqual(self.c.ledger.identity_observations(first), [])   # a batch run never probes
```

Two helpers and one double, written out here because the assertions depend on their exact behaviour:

```python
    def new_commit(self, name):
        origin = self.root / "origins" / "Farm-Client"
        (origin / f"{name}.txt").write_text(name, encoding="utf-8")
        git("add", ".", cwd=origin); git("commit", "-qm", name, cwd=origin)
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=origin, capture_output=True,
                              text=True, check=True).stdout.strip()

    def enqueue_item(self, issue_id, *, mode, commit):
        """One work item created the way a host without a webhook creates them, with its worker's whole
        script decided up front. The first launch asks for the slot and exits; the fresh worker the pool
        resumes gives it back and finishes."""
        (self.stub / f"issue-{issue_id}.json").write_text(
            json.dumps(issue(id=issue_id, labels=["Bug"], delegate_id=APP)), encoding="utf-8")
        item = enqueue(self.c.config, issue_ref=issue_id, skill="fix", commit=commit)
        self.scripts[item["id"]] = [
            ["claim", "--item", "{item}", "--worker-id", "fake"],
            ["await-resource", "--item", "{item}", "--token", "{token}",
             "--resource", "unity_slot", "--mode", mode],
        ]
        self.scripts[item["id"] + ":resumed"] = [
            ["claim", "--item", "{item}", "--worker-id", "fake2"],
            ["release-resource", "--item", "{item}", "--token-file", "{token_file}",
             "--outcome", "quiescent"],
            ["finish", "--item", "{item}", "--token", "{token}", "--outcome", "blocked",
             "--input", str(self.root / "outcome.json")],
        ]
        return item["id"]


class RecordingMcp(FakeMcp):
    """Always matches and is always quiescent, and keeps the lock file honest so the pool's open/closed
    reasoning is the real one. It records nothing the assertions rely on — the evidence is in the ledger."""
```

`FAKE_CLI_STEPS` becomes a map from item id to script, written to the environment by the fixture before each launch; `tests/fake_cli.py` already reads `FARMBOT_ITEM_ID`, and picks `<item>:resumed` when the launch message carries a `resource` block.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -W error -m unittest discover -s tests -v -k TwoPhase`
Expected: FAIL on the fixture first (`build()` must expose `pool`, Task 6, and `StubLinear.fetch_issue` must answer per ref), then on the ordering assertions.

- [ ] **Step 3: Make the two-phase script work end to end**

The only change outside the test files is the one line in `agent.config.StubLinear.fetch_issue` named in Files, and it is scaffolding for the offline double rather than a behaviour change on any real path. Nothing else should need touching. If a test fails for a reason other than its own fixture, fix the production code and say in the commit body which of Tasks 1 to 9 the gap belonged to — this task exists to find exactly those gaps.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -W error -m unittest discover -s tests -v -k TwoPhase`
Expected: PASS, two tests. The first proves the serialization, the second proves "switched to each pinned commit and parked back on main afterwards". Confirm they are two and not four: if `TwoPhaseTests` still inherits `EndToEndTests`'s test methods, the extraction into `Fixture` did not happen.

**Both orderings must pass before criterion 3 is marked done.** These two tests run batch first; `PoolTests.test_two_requests_serialize_in_the_other_order_interactive_first_then_batch` (Task 6) covers the reverse, which is the ordering that exercises the graceful close and is the steady state once the Editor stays open. Task 11 Step 4 repeats the live pair in that order.

- [ ] **Step 5: Run the whole suite and commit**

Run: `python3 -W error -m unittest discover -s tests`
Expected: OK, 215 tests (+2).

```bash
git add tests/test_end_to_end.py agent/config.py tests/fake_cli.py
git commit -m "test: two items needing Unity serialize on the single slot

Phase 1 done-criterion 3, offline: one batch item and one interactive item are
granted the one slot in arrival order, the slot visits each item's pinned commit and
is parked back on main afterwards. No webhook, no tunnel and no Editor: the fake
runtime drives the real receiver, scheduler and pool in one process.

Co-Authored-By: <model> <noreply@anthropic.com>"
```

---

### Task 11: The live rehearsal on this Mac, with no webhook

The last task produces evidence, not code: the same flow against the real slot, the real Editor and the real ledger, driven by `enqueue` instead of a Linear delegation. Run it on this Mac with the service stopped, so every tick is deliberate.

**Why:** the offline test proves the ordering; only a live run proves that `Library/` survives a switch, that the launcher's unsandboxed batch Editor really writes its results where the worker reads them, and that the identity probe agrees with a real `mcpforunity://instances` read. Spec §18 asks for exactly this as the phase's live checklist, recorded as a dated report.

It also closes the one loop Task 0 could not: the addendum measured the *sandboxed* Editor hanging and the *unsandboxed* one passing, but both by hand, outside FarmBot. Step 3 is the first time the launcher's own `run_unsandboxed` starts a real Editor. The thing to watch for is a hang: if `unity-batch.json` comes back `"state": "timeout"` with a frozen log, something has put the run back inside a seatbelt, and that is a finding about `Launcher.run_unsandboxed` rather than about Unity.

**Files:**
- Create: `reports/2026-09-19-slot-rehearsal/report.md`
- Create: `scripts/check-rehearsal.py`

- [ ] **Step 1: Provision slot 1 and record the cost**

```bash
python3 -m agent.service seed-clones --from /Users/elendil/WorkSpaces/Farm
python3 -m agent.service slots
```

With the `slots` list still empty, add one entry to `.local/agent/config.json`:

```json
"slots": [{"id": "unity_slot:1", "repo": "Farm-Client", "build_target": "StandaloneOSX",
           "build_target_argument": "OSXUniversal", "test_assemblies": ["HotUpdate.Tests"]}]
```

then run `python3 -m agent.service slots` again. Expected: one slot, state `idle_closed`, `parked_commit` equal to `git -C .local/repos/Farm-Client.git rev-parse origin/main`, and `.local/editors/slot-1` populated. Record `du -sh .local/editors/slot-1` and the wall clock. If `git lfs fetch` fails, record which of the two failures the error named — **credentials** for `git.kuaiwa.com` or **reachability** of the LAN. Only then, and as a recovery rather than a plan, seed the objects by copy into `.local/repos/Farm-Client.git/lfs/objects`: Task 0 Step 2 decided the opposite for the normal path — the network fetch is the default and the copy was deliberately never measured, because it moves ~3.6 GB of history for the same 918 MB of HEAD objects. Record which path was used, and if it was the copy, record its wall clock, because that is the measurement the spike declined to make.

- [ ] **Step 2: Build `Library/` once, deliberately**

Run the import command from Task 0 Step 3 against `.local/editors/slot-1`, in the foreground, and record the wall clock and `du -sh .local/editors/slot-1/Library`. This is the one-time cost the operator was warned about; it is done by hand here rather than inside a scheduler tick.

- [ ] **Step 3: Drive one batch item with no webhook, on a closed slot**

The issue must be delegated to FarmBot in Linear first: `enqueue` refuses a write-capable skill otherwise, which is spec §4's rule of authority and not something the missing webhook excuses.

```bash
python3 -m agent.service enqueue --issue <a real, delegated issue uuid> --skill fix
python3 -m agent.service serve
```

Expected, in order: the item is launched, the worker calls `await-resource --mode batch` and exits, `python3 -m agent.service slots` shows the reservation `active` and the slot `batch_busy`, **the pool runs the Editor itself through `Launcher.run_unsandboxed`** and writes `unity-tests.xml`, `unity-editor.log` and `unity-batch.json` under `.local/runs/<item>/`, the *second* worker starts with `resource.batch_result` already filled in and runs no Unity of its own, and after `release-resource` the slot returns to `idle_closed` parked on main.

Two things to check that a reading of the terminal will not give you. First, **nothing under `.local/runs/<item>/` may contain a second Unity invocation**: `grep -rl "\-runTests" .local/runs/<item>/*/stdout.log` must come back empty, because a worker that tried to start its own Editor would hang for 25 minutes and this is where that would show. Second, the Editor's parent: while the run is in flight, `ps -o ppid= -p $(pgrep -f "slot-1.*runTests")` should name the `serve` process, not a `codex` worker — that is the whole redesign, observable in one line.

This is also the first time the exit-code contract meets a real Editor, so assert it rather than reading it: record the exit code **and** the results XML's root attributes, and check that `total` is greater than zero. An exit code of 0 with `total="0"` is the failure this contract exists to catch — a wrong `-assemblyNames`, reported as a verification gap and not as a pass, and `unity-batch.json` should already say `"state": "gap"` if so. **Expect the run to be red**: `origin/main` is known-red at **26 failures of 4388** (Task 0 Step 4, listed by class in Task 7), so `failed="26"` is the baseline and a passing run is the surprising result. Record the count and say whether it matches; anything other than 26 is worth a line in the report.

Run the Step 7 checker and paste its output; record the wall clock of the switch and the first ten lines of the results file.

- [ ] **Step 4: Drive one interactive item on the same slot**

Nothing is opened by hand, and there is **no MCP-server prerequisite**: Task 0 Step 5 established that opening the Editor starts the `uvx` server itself, with `--project-scoped-tools` and a per-slot pidfile, so an earlier draft's "the operator confirms the server is listening" is void (checklist item 8, and the front matter's "No longer provided by the operator").

The precondition is therefore the opposite of what that draft asked for. Confirm two things before starting: that `python3 -m agent.service slots` still shows `idle_closed`, and that `lsof -nP -iTCP:8080 -sTCP:LISTEN` prints **nothing**. A listener on 8080 in front of a closed slot is not readiness — it is the stale server `close_editor` exists to reap, left over from Step 3's close (the spike saw it still holding the port and answering HTTP 200 after the Editor was gone). If something is listening, the close in Step 3 did not reap it: that is a finding about `UnityIdentity.reap_server`, it belongs in the report, and `open_editor` will fail at the editor stage with the message that names exactly this case.

Then enqueue a second item whose worker asks for `--mode interactive`. Expected: the pool **starts the Editor itself**, discovers the instance and writes it to `slots.instance`, switches the slot, waits for quiet, finds zero Console errors, records an `identity_observations` row whose `aggregate` is `match`, the worker's launch message contains a `unity` MCP server and that instance id, and `release-resource --outcome quiescent` parks the slot — `idle_open`, with the Editor still running. Record the probe result verbatim, with the live instance id next to the value `sha1(<folder>/Assets)[:16]` computes and next to the `dataPath` the probe returned.

- [ ] **Step 5: Repeat the pair in the other order**

This is the ordering the batch-first rehearsal never exercises, and it is the steady state: the slot is now `idle_open` with a live Editor. Enqueue an interactive item and then a batch item. Expected: the interactive item is granted with no Editor start (the Editor is already open), and when the batch item is granted the pool closes the Editor gracefully first — `Temp/UnityLockfile` disappears before `Unity -batchmode` starts, and the batch run does not fail on a project lock. **Criterion 3 is not marked done until both orderings pass here and in the suite.**

- [ ] **Step 6: Stop a worker while it holds the slot**

With an interactive worker running, run `python3 -m agent.__main__ --db .local/agent/ledger.sqlite3 cancel --item <id> --reason "rehearsal stop"`, and time it. Expected: under five seconds, the reservation `cancel_requested`, the item `cancelled`, and on the next pool tick either a release with the slot parked or a `held` slot naming the reason. If it is held, run `recover-slot` and record that the slot returns to the pool.

**Then do it again against a batch run, which is the case the offline tests cannot fully prove.** Enqueue a second batch item, wait until `python3 -m agent.service slots` shows the slot `batch_busy` and a real `Unity -batchmode` process exists (`pgrep -fl 'MacOS/Unity -batchmode'`), then cancel it the same way and time it. Expected: under five seconds; **the Unity process is gone** — check with `pgrep`, not with the ledger, because the ledger's opinion is exactly what this step exists to corroborate; `Temp/UnityLockfile` is removed; and the slot settles rather than hanging until `batch_timeout`.

This is the one case where the ledger and reality could disagree without anyone noticing. The batch Editor is started by the launcher on the pool thread and is a direct child of `serve`, so no worker owns it and `launcher.stop` cannot see it; if `stop_unsandboxed` were ever quietly dropped, every offline test would still pass — they use a fake — while a real Stop would leave an Editor running for up to half an hour holding the slot. Record the measured seconds and the `pgrep` output verbatim, both before and after.

For the same reason, record what a **service restart** does while a batch run is in flight: enqueue a batch item, wait for `batch_busy`, `launchctl kickstart -k gui/$(id -u)/com.kuaiwa.farmbot.serve`, and confirm from `pgrep` that no Unity process survived the restart and that the next `ensure()` finds the slot usable rather than apparently held by another Editor. The pool thread is a daemon, so nothing waits for it on the way down; `stop_all_unsandboxed` in `serve`'s `finally` is the only thing standing between a restart and an orphan.

- [ ] **Step 7: Check the transitions with a script, not by eye**

Every transition this task asks a human to watch is already in the ledger, so assert it instead of narrating it. Add `scripts/check-rehearsal.py` (about twenty-five lines, standard library, read-only): it opens `.local/agent/ledger.sqlite3`, and for each item prints PASS or FAIL for the expected `audit` sequence (`reservation queued` → `reservation acquired` → `slot <busy state>` → `reservation released` → `slot idle_*`), the `reservations.acquired_at` ordering, `slots.parked_commit` against `git rev-parse origin/main`, the `identity_observations` aggregate, and the elapsed seconds between the `cancel requested` audit row and the item's `cancelled` row.

It also reads each batch item's `unity-tests.xml` through `agent.unity.read_results` — the same function the pool used, so the checker cannot disagree with the ledger by parsing the file a second way — because the one contract Task 0 called "the most important answer in this spike" is otherwise checked by eye or not at all. Three assertions: **`total > 0`** — a missing, unparseable or zero-total file is a verification gap, not a result, whatever the exit code said; **`unity-batch.json` exists and its `state` is `"ran"`**, which is the pool's own verdict on the run it performed and the only record that the launcher, not a worker, started the Editor; and **`failed == 26`**, printed as `BASELINE` rather than `PASS` because it is main's known-red set (Task 0 Step 4) and not this rehearsal's achievement. A different number is not a failure of the rehearsal either; it is a line in the report and a reason to re-measure the baseline.

Paste its output into the report verbatim.

- [ ] **Step 8: Write the report**

Create `reports/2026-09-19-slot-rehearsal/report.md`: what was run, the measured costs, the checker's output for both orderings, the probe result, the Stop timing, and a short list of anything that behaved differently from this plan. Redact nothing but paths outside the repository and any account identifier. Raw logs stay in `.local/runs/` and are not committed (spec §9, §15).

- [ ] **Step 9: Commit**

```bash
git add reports/2026-09-19-slot-rehearsal/report.md scripts/check-rehearsal.py
git commit -m "docs: record the live slot rehearsal on the Mac

One batch item and one interactive item through the real slot, the real Editor and
the real ledger, in both orderings, created with enqueue because the webhook cannot
be delivered to this host yet. The transitions are asserted by a checker that reads
the ledger, not narrated from the terminal.

Co-Authored-By: <model> <noreply@anthropic.com>"
```

---

## Self-review

**Spec coverage.** §7 in full for `unity_slot`: the reservation queue with FIFO, the six states and the hashed owner token (Task 2), the slot folder and its binaries (Task 3), the slot switch, the Editor's start and graceful close, the compile wait and the park (Task 4), the identity probe (Task 5), scheduling preference by mode, two-phase acquisition and the release rule (Tasks 6 and 8), tool injection as enforcement (Task 7), and Stop's ordering (Task 9). §6's pinned target, which the repository never wrote, is Task 1; §6's "when the request names a branch or PR" and the human correction of a pin are deferred in Scope with the phase that owns them. §8's slot worktrees and the batch command without `-quit`, `-nographics` or `-accept-apiupdate` are Tasks 3, 4 and 7. §8's writable roots are answered by removal rather than by a list: Task 0's addendum measured a batch Editor **hanging** inside `sandbox_workspace_write` on a denied Mach lookup with zero file-permission denials, so no set of roots can make the run work, and Task 7 moves the run to `Launcher.run_unsandboxed` instead. The worker's roots are therefore exactly what they were before slots existed — its worktrees, its state directory and the ledger — and the slot folder is in none of them, in either mode. §10's `slots`, `reservations` and `identity_observations` are Task 2; `hosts`, `build_artifacts` and `baselines` are deferred in Scope with their reasons. §17 Phase 1 criterion 3 is discharged by Task 10's two test methods and Task 6's reverse-ordering case offline, and Task 11's report live, in both orderings; criterion 2's five-second Stop is re-proved under contention in Task 9. §18's "slot pool acquisition by mode and preference, slot state machine and switch, two-phase resource acquisition" are the test names in `tests/test_slots.py`.

**Deferred by design, not gaps.** Play Mode and the logged-in session identity probe, the per-slot test account's use, `fgui_editor` and `android_device` kinds, baseline comparison of batch results, naming a ref other than the default head and retargeting a pin after the fact, player builds in a slot, and the second slot with its stdio MCP pinning. Each is named in Scope with the phase that owns it. The `qa` skill is untouched by this plan.

**Placeholders.** None. Every step names its files, shows its code and gives the command to run with the output to expect. The two places this plan deliberately did not invent an answer were Task 0's spike questions and the operator-provided resources. **Task 0 has since been executed on this Mac (2026-09-19) and its record answers all of them**, so the numbers in Tasks 3, 4 and 7 are measured rather than budgeted; the steps stay written as questions with success conditions because the Windows host re-runs them. What remains genuinely unanswered is named in three places and nowhere else: the 公共测试服 account, Editor contention with the operator's own Editor, and every figure's re-measurement on Windows.

**Type consistency.** Eleven signature changes, each checked against its existing callers. `Worktrees.ensure_clone(repo, seed_from=None)` is untouched. `_git(*args, cwd, env=GIT_ENV, timeout=600)` keeps its old behaviour for every existing call site, which all pass only `cwd`; `timeout` arrives in Task 1 and `env` in Task 3. `Ledger.await_resource(item_id, token, resource, mode)` keeps its signature and gains two refusals, so `tests/test_ledger.py:214` needs a pinned item — Task 2's fixture provides one. `Ledger.fail_queued` widens its accepted states, and its two callers in `agent/scheduler.py` pass queued items. `dispatch_message(..., resource=None)` defaults to today's payload byte for byte, and its one production caller is `Scheduler.launch`. `Scheduler.__init__` gains `slot_entries=None`, whose only production construction is `build`'s.

Four arrive with the sandbox redesign in Task 7, and none of them touches an existing caller. `Launcher.run_unsandboxed(argv, *, cwd, timeout, log=None, env=None)` is a **new** method beside `spawn`; nothing called it before and its only production caller is `SlotPool.run_batch` through the injection. `SlotPool.__init__` gains `run_unsandboxed=None`, and the only production construction is `service.build`'s, updated in the same task; a pool built without it refuses a batch grant rather than handing out a run that never happened, which is why the default is `None` and not a silent no-op. `SlotPool.run_batch(slot, reservation)` is new, called only from `_hand_over`'s batch branch. `agent.unity.read_results(path)` is new with three callers, all reading one answer. Against those, one **removal**: `agent.unity.writable_roots()` goes, and it had exactly one caller — `Scheduler.launch`'s batch branch, deleted in the same task — plus one test, replaced by `read_results`'s. After this, `agent/scheduler.py` imports nothing from `agent/unity.py` at all. `Receiver.__init__` gains two keyword arguments with defaults, so the only construction outside tests, in `service.build`, is updated in the same task. `SlotPool(ledger, ...)` accepts a `Ledger` or a factory, and the only production construction passes a factory. `Components` gains a trailing field, and every construction of it is `build`'s single call. `SlotPool.park` gains a required `mode`, and its only two callers are `park_idle` and the tests. Two more arrive with the spike's findings: `Worktrees.skip_generated(path)` is new and is called only from `add_slot` and `checkout_commit`, both in Task 3; and `agent.unity.editor_holds_project(folder)` returns a **pid or None** rather than a bool. It arrives in Task 4, with `other_editor_project`, over a shared `_editor_processes` listing, and has three callers: `SlotPool.editor_is_open` (`is not None`), `UnityIdentity.quiescent` (`not editor_holds_project(...)`, unaffected by the pid) and `UnityIdentity.terminate`, which signals it and is the reason it returns a pid at all. The class is `agent.slots.UnityIdentity` throughout — Task 5's Files, its Interfaces, the class definition, `service.build`'s construction and the import line all name it.

**Concurrency.** Two writers now use the ledger: the scheduler thread and the pool thread, on **two connections** — `service.build` hands the pool a factory, exactly as it already hands one to the receiver, because two threads on one `sqlite3.Connection` do not get two transactions and would commit and roll back each other's work. Given two connections, every mutation goes through `_transaction()`, which is `BEGIN IMMEDIATE`, and the two partial unique indexes are the fences that survive a crash between them; `Ledger`'s `timeout=10` is what absorbs a contended `BEGIN`. A `ReservationTests` case runs two connections into `acquire` from two threads behind a barrier and asserts exactly one grant, so the "across processes rather than by convention" claim is tested rather than argued. The pool never holds a transaction across a git command, a subprocess or an MCP call — the discipline `farmqa_identity.py:155-167` established and the reason a Stop can land at any moment. That rule earns its keep in Task 7: the batch Editor is now one of those subprocesses, it ran 19 s foreground in the spike and is allowed up to `batch_timeout` (1800 s), and it runs on the pool thread with no transaction open, so a Stop lands during it exactly as it lands during a switch. `Scheduler.tick()` gains no slow work at all — in particular it does **not** run Unity and does not wait on one — so its lock is held for the same time as today.

**Test counts.** Counts are the drift alarm, so each is a delta per file with a running total beside it, and each is confirmed by running the selection rather than by arithmetic. The suite stands at 139 before Task 0 and **215** after Task 11: +8 → 147 (Task 1), +12 → 159, +5 → 164, +15 → 179, +7 → 186, +10 new and one Phase 1a test deleted → 195, +10 → 205, +5 new and one replaced → 209, +4 → 213, +2 → 215. Task 11 adds none: it produces a report and a read-only checker script, not tests. Two of those numbers depend on a refactor rather than on new tests: Task 10's total assumes `TwoPhaseTests` does **not** inherit `EndToEndTests`'s two test methods, and Task 8's assumes `test_await_resource_is_refused_without_slots_and_errors_are_clean` was replaced rather than kept. **Task 4's +15 is unchanged across the sandbox redesign but its contents moved**: `writable_roots` and its test are deleted and `read_results` and its test take their place, one for one. **Task 7 is +10, not the +5 an earlier draft wrote**: the two scheduler cases, the dispatch case, the `write_mcp_config` case and the `tests/test_deploy.py` case that pins `ProcessType=Standard`, plus the five the redesign brings — `run_unsandboxed` and the owned-run group kill in `tests/test_launcher.py`, and the run, the gap and the timeout in `tests/test_slots.py`. **Task 9 is +4, not +3**: the third and fourth are the reachability pair the redesign made necessary — a Stop must kill the launcher-run batch Editor, which no worker owns, and must do it before the worker kill. Task 6's nine `PoolTests` cases are unaffected in number because Task 7 gives `SlotFixture.pool` a default `run_unsandboxed` rather than editing each case.

## Operational checklist, owed but not code

These need the Windows host, the operator, or a decision a test cannot make, and are tracked here so they are not lost. Task 0 ran on 2026-09-19 and closed three of them; the closed items are kept, marked, so nobody re-opens a question that has an answer.

1. **The dedicated 公共测试服 account for slot 1.** **Still owed.** Write it into the slot's config entry as `account`. Nothing in Phase 1 logs in; Phase 2's `qa` skill cannot start without it.
2. **Editor contention — STILL OPEN, and the only part of Task 0 Step 6 that is.** The licence half is **answered**: Task 0 Step 6 ran a batch Editor from a one-shot LaunchAgent in the `gui/501` domain and it resolved the entitlement (`entitlements=7`), exited exactly as the foreground run did and wrote a results file within four bytes of it. No login session and no purchased seat are needed on this Mac. The speed half is answered too, and badly — the same suite took **105 s under launchd against 14 s foreground with warm caches**, a **7.5x** tax from the background scheduling band, confirmed against a warm-cache control — but that half is **not tracked here any more, because it is code**: `ProcessType=Standard` in `agent/deploy.py` is a step of **Task 7**, with a Files entry and a `tests/test_deploy.py` case that pins it. What remains in this item is the question a test cannot answer: contention with the operator's own Editor on `Farm-Client` was never exercised, because none was open during the spike. Until it is, `SlotPool.switch` keeps its `another_editor_running` preflight and refuses to start a second Editor with a named reason; re-test before relaxing it.
3. **App Nap — RESOLVED, no action.** Task 0 Step 5 measured three unfocused forced recompiles at 9.6 / 7.7 / 5.7 s against the 120-second stall threshold, with no focus stolen. No `NSAppSleepDisabled`, no separate macOS user for the slot, and no batch-only restriction on the Mac. The plan's earlier "a spread of more than three to one is a stall" rule is withdrawn as a bad proxy: a no-op refresh is legitimately sub-second and inflates the ratio, so stall detection keys on the absolute threshold. This has no Windows analogue.
4. **The Windows slot's configuration**, for the Windows plan to inherit: `unity` (`Unity.exe` under one of the three probe roots in `agent/unity.py`), `build_target` (`Android` on the QA host) with its `-buildTarget` spelling, `mcp_address` (the plugin's `MCPForUnity.HttpUrl` EditorPrefs value, which lives in the registry there and in a plist here), the slot folder, and the host's own test account. No code change should be needed; if one is, the Windows plan records which module leaked a host assumption.
5. **Re-run Task 0's spike on Windows.** Import cost, LFS reachability from that host, `-runTests` exit codes under that Unity, and whether a scheduled task in Session 0 can render for PlayMode fixtures — the Windows counterpart of the launchd question.
6. **The second slot.** Spec §7 adds a slot when waiting routinely exceeds the length of a run. The `awaiting_resource` wait times are now in the ledger, so that decision is a query. **Memory is no longer an argument against it:** Task 0 Step 5 measured the open Editor at 2.32 GB resident, so two fit inside this host's 24 GB, and about 6 GB of disk each against 393 GiB free is not a constraint either. Phase 1 stays at one slot only because done-criterion 3 needs two items to *contend* for it. What a second slot does still need is the per-slot MCP addressing question — the server is started per Editor with `--project-scoped-tools` and a pidfile named for its own port, which `UnityIdentity.reap_server` already derives from the slot's configured `mcp_address` rather than hardcoding, so the second slot's port is a config key and not a code change — and it is the point at which the stdio transport and `UNITY_MCP_DEFAULT_INSTANCE` pinning become worth the spike §7 asks for.
7. **The webhook.** Until the tunnel delivers, every live run is driven by `python3 -m agent.service enqueue`, on issues that were delegated to FarmBot in Linear by hand, and those items report through issue comments because Linear has no agent session for them. Criterion 5's real delivery still needs the webhook; it is tracked in the 1c checklist and is not blocked by this plan.
8. **The MCP for Unity server process — RESOLVED, no action.** The endpoint on 8080 is a `uvx`-launched process, but Task 0 Step 5 showed the **Editor starts it itself** on open, with `--project-scoped-tools` and a per-slot pidfile at `<slot>/Library/MCPForUnity/RunState/mcp_http_8080.pid`, and that `execute_code` is registered and enabled without anyone touching the MCP window. No launchd agent of its own and no operator step. One consequence did become work: the server **outlives** the Editor and keeps the port bound, so `SlotPool.close_editor` reaps it from that pidfile (Task 4).
9. **The secret in `Farm-Client/.codex/config.toml`.** Tracked in git with a plaintext bearer token in its history. Owed to whoever owns Farm-Client; FarmBot's only guarantee is that the file never becomes a source of tool configuration.

## Execution

Plan complete and saved to `docs/superpowers/plans/2026-09-19-phase1b-unity-slots.md`. Two execution options:

1. **Subagent-driven (recommended):** a fresh subagent per task, review between tasks, fast iteration. Requires the superpowers plugin enabled in this directory so `superpowers:subagent-driven-development` loads.
2. **Inline execution:** run the tasks in this session with `superpowers:executing-plans`, batch execution with checkpoints.

**Task 0 is done** (Mac, 2026-09-19, committed in `d1b0b28`), so execution starts at Task 1. Its record, `docs/superpowers/spikes/2026-09-19-unity-slot-spike.md`, is read by Tasks 3, 4 and 7 and is the source of truth for every measured number in them; App Nap does not stall an unfocused Editor here, so Task 4's interactive branch keeps its shape. The `ProcessType=Standard` change to `agent/deploy.py` is scheduled work, in Task 7, and must land before any Unity work runs under the service.

**The sandbox redesign is written in, and there is no blocker.** The spike's addendum found that a `Unity -batchmode` run inside the Codex `workspace-write` seatbelt **hangs indefinitely** on a denied Mach lookup, with no file-permission denial to fix. Tasks 4, 7 and 8 now carry the answer rather than the concession: the pool composes the argv from the slot's own configuration, `Launcher.run_unsandboxed` runs the Editor outside the worker's sandbox, `agent.unity.read_results` applies the exit-code contract to the XML, and the worker is handed `resource.batch_result` as evidence with no argv, no Editor path and no write access to the slot. `agent.unity.writable_roots()` is removed, because the seatbelt it widened is not the one the Editor runs under any more. Every task can start in order.

The Windows deployment plan is the only Phase 1 plan that follows this one, and it re-runs Task 0's steps on that host.
