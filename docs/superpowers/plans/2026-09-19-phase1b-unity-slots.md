# FarmBot Phase 1b: Unity slots, the slot switch and two-phase acquisition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two delegated items that both need Unity serialize on one slot through the two-phase flow, one in batch mode and one interactive, with the slot switched to each item's pinned commit and parked back on `origin/main` afterwards — which is Phase 1 done-criterion 3 (spec §17).

**Architecture:** Three new modules inside the existing `agent/` package. `agent/slots.py` owns the slot pool: it creates each slot as a long-lived detached worktree under `.local/editors/slot-<n>` with its LFS binaries materialized, grants reservations FIFO, performs the slot switch, starts the Editor for an interactive run and closes it gracefully before a batch one, parks the slot back on main and settles held slots after a quiescence probe. `agent/unity.py` holds every host-specific fact about the Editor — where the binary is and what a batch test run's argument vector looks like. `agent/unity_mcp.py` plus `agent/identity.py` are the ported FarmQA identity probe: a loopback JSON-RPC client and the check set that proves the Editor really loaded the pinned commit. The ledger gains `slots`, `reservations` and `identity_observations`; the scheduler gains nothing but a reservation-aware `launch` and a Stop that cancels reservations first; the pool runs on its own thread beside the receiver and scheduler threads, because a slot switch takes minutes and `tick()` must stay under a second. Devices, FairyGUI, Play Mode session identity, QA scenarios and the Windows host are later plans.

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
- **Player builds in a slot.** A slot is for Edit Mode and PlayMode fixtures only. Measured on this Mac, the human's `Library/` is 12 GB, of which `Bee` is 7.6 GB and `BuildCache` 926 MB — both player-build artefacts. The import-only subset is roughly 3.5 GB, and that is the number the slot budget assumes. If a slot ever runs a player build the budget is wrong by a factor of three; Task 8 writes that rule into the operating contract.

## Provided by the operator, not by this plan

These cannot be created by code and block the steps named. They are repeated in the operational checklist at the end.

1. **A dedicated test account on 公共测试服 for slot 1.** Spec §7 binds one account per slot because two logins on one account kick each other and scenario runs mutate account state. Nothing in this plan logs in — criterion 3 is Edit Mode only — so the `slots.account` column is written when the operator supplies the account and stays NULL until then. Phase 2's `qa` skill is blocked without it, so it is asked for now.
2. **Disk and wall clock for the first `Library/`.** Measured on this Mac rather than estimated: the working tree is about 1.5 GB (`Assets/` alone is 1.2 GB), the LFS payload **at HEAD** is 0.91 GB across 3,619 files (`git lfs ls-files -s`), and FarmBot's bare clone is 35 MB plus its own LFS object store. Seeding that store by copying the human's `.git/lfs/objects` moves 3.6 GB, because that directory is history, not HEAD. Import-only `Library/` is roughly 3.5–4 GB and single-digit minutes of first import on this M4 — see item 3 for what that import needs before it can run at all. Budget 6 GB per slot and 15 minutes for the first import. Free space on this Mac is 393 GiB, so disk is not the constraint; the 24 GB of RAM is what caps the pool at one slot.
3. **`git.kuaiwa.com` access — measured on 2026-09-19 and NOT a blocker on this Mac.** An earlier
draft of this plan called this a blocker from reading config files. A direct test disproved it. The host
resolves to `192.168.1.201` and its LFS batch API answers; `credential.helper` for it resolves to
`osxkeychain` by urlmatch and `git credential fill` returns a stored credential for
`mazhangli@hiiland.com` **without prompting under `GIT_TERMINAL_PROMPT=0`**; and a fetch forced into a
*fresh* LFS store (`git -c lfs.storage=<tmp> lfs fetch origin HEAD --include=<file>`, stdin closed)
downloaded the object over the network and exited 0. So a slot on this Mac materializes its binaries
with no operator action. Three caveats remain, and they are checks rather than blockers: the address is
on the LAN, so any session off that network — or one whose traffic is captured by a VPN or by Clash in
TUN mode — still gets pointer files; the keychain must be unlocked, which it is for a `gui/501`
LaunchAgent while the user is logged in, but Task 0 confirms it once under the *loaded* agent rather
than from a shell; and `Packages/packages-lock.json` names three git UPM dependencies on the same host
(`com.code-philosophy.hybridclr`, `com.psygames.unitywebsocket`, `com.unity.assetbundlebrowser`), so the
first import needs that LAN too. Seeding FarmBot's bare clone by copying the human's LFS objects stays
available as a faster alternative, not as a fallback for a broken path; if used, the destination is
`<bare>/lfs/objects`, i.e. `.local/repos/Farm-Client.git/lfs/objects`, not `.git/lfs/objects`.
4. **Unity licensing on this one Mac, before any second host.** Corrected 2026-09-19: `/Library/Application Support/Unity/Unity_lic.ulf` is **not** a Personal seat as an earlier draft stated. It carries a masked serial (`F4-SCPA-...`) and `LicenseVersion 6.x`, i.e. a serial-based paid licence, and concurrent Editors on one machine are normally permitted under it. The operator's own Editor is routinely open on `/Users/elendil/WorkSpaces/Farm/Farm-Client` (none running at the time of writing). This is therefore a check, not an assumed constraint. Three questions, answered in Task 0 Step 6: (a) does a batch Editor started from the loaded LaunchAgent acquire the seat at all; (b) what happens when the operator's own Editor already holds it — record the exact error and decide whether the pool must refuse to grant while another Unity process is alive; (c) is a second seat needed for the Mac alone. Note that `com.kuaiwa.farmbot.serve` is a LaunchAgent in the `gui/501` domain, so it *does* have a window server whenever the user is logged in; the risk is the licensing IPC, not a missing session. Neither farmbot job is loaded today (`launchctl print gui/501/com.kuaiwa.farmbot.serve` → not found), so they must be bootstrapped before Task 0 Step 6 can run.
5. **The MCP for Unity server process.** Opening the Editor is not enough to make `http://127.0.0.1:8080/mcp` answer. In the installed package (`com.coplaydev.unity-mcp@30d2207509`, 10.2.0) `Editor/Helpers/HttpEndpointUtility.cs` defines `DefaultLocalBaseUrl = "http://127.0.0.1:8080"`, but the endpoint is served by a separate `uvx`-launched server process that the Editor manages and that must be installed and started. Nothing is listening on 8080 or 9090 on this Mac today. `uv` and `uvx` exist at `/Users/elendil/.local/bin`, which is on the launchd PATH, so the prerequisite is satisfiable — it is simply unstated. The operator confirms the server starts, and confirms that `execute_code` is enabled for this project through the MCP window (`Editor/Tools/ExecuteCode.cs` sets `AutoRegister = false`, so it is off until enabled). EditorPrefs on macOS live in `~/Library/Preferences/com.unity3d.UnityEditor5.x.plist` and are per *user*, not per project, so the human's Editor and the slot's Editor necessarily share one port and one server process.
6. **A decision on the service's scheduling band.** `~/Library/LaunchAgents/com.kuaiwa.farmbot.serve.plist`, installed by Plan 1c, sets `ProcessType` to `Background`, which on macOS puts the job and everything it spawns into the background band: lowered CPU priority, timer coalescing and throttled disk I/O. A Unity import and a batch test run inherit that from the service, so the numbers Task 0 measures by hand in a foreground shell are not the numbers the deployed service will see. Task 0 Step 6 measures both; if the throttled figure is unacceptable the operator decides whether to change the plist to `Standard`, which is a deployment change this plan then owes.

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
- No slot is ever released on a timer. A release follows a passing quiescence probe; a failing probe holds the slot for the operator's `recover-slot` (spec §7). In batch mode the quiescence probe is not an MCP call: it is the confirmation that no Unity process holds the slot folder and that the results file was written or its absence recorded.
- **One `sqlite3.Connection` per thread.** `Ledger._transaction()` is a bare `BEGIN IMMEDIATE`/`COMMIT` on `isolation_level=None` (`agent/ledger.py:266-273`). Two threads sharing one connection do not get two transactions: one thread's `BEGIN` lands inside the other's, raising `cannot start a transaction within a transaction`, and either thread's `COMMIT` or `ROLLBACK` applies to the other's half-written work. WAL and `busy_timeout` protect separate connections, not two users of one handle. `agent/service.py` already hands the receiver a *factory* (`lambda: Ledger(...)`) for exactly this reason; the pool thread gets its own connection the same way (Task 6).
- Reservation tokens are hashed in the ledger and the raw token exists exactly once, at acquisition. A token never travels as a command-line argument; it is written to the item's state directory and read from a file (spec §15).
- Enforcement is tool injection: a worker reaches the Unity MCP only while its item holds an interactive reservation, and never because a repository-local `.codex/config.toml` in a worktree says so (spec §7, §8).
- Linear comment and activity language is zh-CN and concise (spec §9).
- Never merge, deploy, change issue status or assignee (spec §1 non-goals).
- Private runtime state lives under `.local/` and is git-ignored; nothing secret enters the ledger, reports or git (spec §15).
- Commit after every task with a conventional-commit message and a `Co-Authored-By: <model> <noreply@anthropic.com>` trailer naming the model that authored the commit, exactly as that model's own attribution reminder states. The trailer lines inside this plan's commit steps are templates for that line, not literal text.

---

## File structure

| Path | Responsibility | Change |
|---|---|---|
| `docs/superpowers/spikes/2026-09-19-unity-slot-spike.md` | **new**: recorded answers to the seven environment questions Task 0 asks | create |
| `agent/worktrees.py` | `resolve_commit`; slot worktrees created and moved with LFS smudge enabled | modify |
| `agent/receiver.py` | resolve and store the pinned target when a session is first seen | modify |
| `agent/ledger.py` | `slots`, `reservations`, `identity_observations`; session and item targets; reservation lifecycle | modify |
| `agent/config.py` | `Paths.editors`; the shape of a `slots` entry | modify |
| `agent/unity.py` | **new**: locate the Editor binary for this host and build the batch test command | create |
| `agent/unity_mcp.py` | **new**: loopback JSON-RPC client for MCP for Unity (ported from `FarmTestAgent/tools/farmqa_unity_identity.py`) | create |
| `agent/identity.py` | **new**: source snapshot, Editor readiness gate and the identity check set (ported from `FarmTestAgent/tools/farmqa_identity.py`) | create |
| `agent/probes/editor-readiness.cs.txt` | **new**: the C# probe run through `execute_code` (copied from `FarmTestAgent/tests/probes/`) | create |
| `agent/slots.py` | **new**: the slot pool — ensure, grant, switch, probe, settle, park | create |
| `agent/scheduler.py` | `launch` injects the Unity MCP from the reservation; `stop` cancels reservations first | modify |
| `agent/dispatch.py` | the dispatch payload carries a `resource` block | modify |
| `agent/service.py` | the pool thread; `enqueue` and `slot` subcommands | modify |
| `agent/__main__.py` | `await-resource` really enqueues; `release-resource`, `reservations`, `slots`, `recover-slot` | modify |
| `skills/fix/SKILL.md` | verification-ladder rungs 4 and 5 become real | modify |
| `docs/operating-contract.md` | slots exist; what a worker may ask for and must give back | modify |
| `docs/superpowers/specs/2026-09-17-farm-linear-agent-design.md` | §7 amendment: slot 1 is built fresh on the first host | modify |
| `tests/test_slots.py` | **new**: ensure, grant, switch, park, hold, settle | create |
| `tests/test_unity.py` | **new**: Editor discovery and the batch command | create |
| `tests/test_identity.py` | **new**: the MCP client against a real loopback server, and the check set | create |
| `tests/test_ledger.py`, `tests/test_worktrees.py`, `tests/test_receiver.py`, `tests/test_scheduler.py`, `tests/test_launcher.py`, `tests/test_dispatch.py`, `tests/test_cli.py`, `tests/test_service.py`, `tests/test_end_to_end.py` | reservations, pinned targets, injection, the two-item serialization proof | modify |

---

### Task 0: Unity slot spike (record, do not automate)

Eight facts about this host decide whether the rest of the plan's steps are minutes or hours, and none of them can be read out of documentation. The output is a recorded document, not code. Run it on this Mac. Mark the record "Mac 2026-09-19; re-run on the Windows host in the Windows plan".

**Preconditions, checked and recorded before Step 1.** Each is a prerequisite the plan cannot create; a failing one stops the spike rather than producing a misleading number.

```bash
git lfs version                                   # 3.7.1 here; there is no per-command --quiet flag
curl -s -o /dev/null -w '%{http_code}\n' --max-time 5 http://git.kuaiwa.com/farm/clientlfs.git/info/lfs  # host is on the LAN; verified reachable 2026-09-19
git config --get-urlmatch credential.helper http://git.kuaiwa.com   # expect: osxkeychain (verified 2026-09-19)
which uvx                                         # the MCP server for Unity is a uvx process, not the Editor
lsof -nP -iTCP:8080 -sTCP:LISTEN                   # nothing here means the MCP endpoint will not answer
launchctl print gui/$(id -u)/com.kuaiwa.farmbot.serve >/dev/null && echo loaded || echo "not loaded"
```

**Why:** spec §7 says a `Library/` import is "tens of minutes and several GB", and the installed test-framework documentation explicitly disclaims any common definition of `-runTests` exit codes. The first is an estimate the slot-count decision rests on, the second is the launcher's contract with a batch run. Guessing either one puts a wrong number into Task 4 and a wrong branch into Task 7.

**Files:**
- Create: `docs/superpowers/spikes/2026-09-19-unity-slot-spike.md`

- [ ] **Step 1: Build a throwaway slot and time the cold import**

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

Record: the wall clock of `worktree add`, the tree size, and that the DLL begins `vers` — with smudge off it must be a pointer, which is the baseline Step 2 measures against. **Success condition:** the worktree exists and `git lfs ls-files` counts 3,619 files.

- [ ] **Step 2: Measure the two ways to get the LFS objects**

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

- [ ] **Step 3: Time the first `Library/` import at a fresh path**

A cold import resolves nine git UPM dependencies from scratch, because `Library/PackageCache` is per project and a new worktree has none. Three of them are on the LAN-only host (`com.code-philosophy.hybridclr`, `com.psygames.unitywebsocket`, `com.unity.assetbundlebrowser` under `http://git.kuaiwa.com/mazhangli/...`) and the rest are GitHub, so this step needs both networks. `/HybridCLRData/` is gitignored and is 1.9 GB in the human's checkout; whether a fresh slot produces `HotUpdate.dll` without it is exactly what this step finds out.

```bash
UNITY=/Applications/Unity/Hub/Editor/2022.3.62f3/Unity.app/Contents/MacOS/Unity
time "$UNITY" -batchmode -quit -silent-crashes -accept-apiupdate \
  -projectPath "$SLOT" -logFile "$SPIKE/import.log"
du -sh "$SLOT/Library" "$SLOT/Library/PackageCache"
du -sh "$SLOT"/Library/* | sort -h | tail -12
grep -c "error CS" "$SPIKE/import.log"
grep -iE "hybridclr|installer" "$SPIKE/import.log" | head -20
git -C "$SLOT" status --porcelain | head       # did -accept-apiupdate rewrite tracked scripts?
ls -l "$SLOT/Library/ScriptAssemblies/HotUpdate.dll"
```

Record: wall clock, total `Library/` size, `PackageCache` size, the largest subfolders, whether the licence resolved (`grep -i entitlement "$SPIKE/import.log"`), whether HybridCLR logged an installer warning, whether `HybridCLRData` had to be generated before `HotUpdate.dll` appeared, and whether the working tree came back dirty. **Success condition:** the process exits 0, `Library/ScriptAssemblies/HotUpdate.dll` exists, and `git status --porcelain` is empty. A dirty tree is a finding, not a pass: it means `-accept-apiupdate` rewrote sources, the next `switch` would fail its clean check, and Task 4 must drop the flag (it already does — spec §8 does not list it) and Task 3 must say whether `HybridCLRData` is a one-time provisioning step.

- [ ] **Step 4: Record the `-runTests` contract**

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

- [ ] **Step 5: Record the interactive path**

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

then, with the Editor open and **not** focused, read `mcpforunity://instances`, `mcpforunity://editor/state` and call `execute_code` with `FarmTestAgent/tests/probes/editor-readiness.cs.txt`. Finally close it through `execute_code` with `UnityEditor.EditorApplication.Exit(0);` and time how long `Temp/UnityLockfile` takes to disappear.

Record, in this order:

1. **Whether the endpoint answers at all**, and what had to be true first: the `uvx` MCP server process running (name it and say whether the Editor started it), and `execute_code` enabled for this project through the MCP window (`Editor/Tools/ExecuteCode.cs` sets `AutoRegister = false`). If the endpoint does not answer, the rest of this step cannot run and Task 4's preflight is the only thing that saves the plan.
2. **Cold Editor start to a usable endpoint**, in seconds, from the `time` above. Task 4's `open_editor` needs this number as its timeout.
3. **The live instance id for this folder**, next to *both* candidate hashes: `sha1(<slot>/Assets)[:16]` and `sha1(<the dataPath the probe itself returns>)[:16]`. The plugin computes it as `SHA1(Application.dataPath)` truncated to 16 hex characters (`Editor/Helpers/ProjectIdentityUtility.cs`), and the probe's own payload carries `dataPath`, so record both and say which one matched.
4. **An unfocused `refresh_unity` plus recompile**, three times, each timed. **Threshold:** if any of the three exceeds 120 seconds, or if the three differ by more than a factor of three, record the verdict as "stalled" — that, not a subjective reading, is what "App Nap stalls it" means here. Also record whether focus was stolen.
5. **The resident memory of the open Editor** (`ps -o rss= -p $EDITOR_PID`), which is the input to the second-slot decision.
6. **The graceful close:** whether `EditorApplication.Exit(0)` through `execute_code` returns, how long until the process is gone, how long until `Temp/UnityLockfile` disappears, and whether `Library/` was flushed. Task 4's `close_editor` waits on the lockfile, so this number is its timeout.

**Success condition:** the probe returns a JSON payload naming `HotUpdate` with a `moduleMvid`; the unfocused refresh does not meet the stall threshold; and the close removes the lockfile within a bounded time. If the refresh stalls, record it and stop — Task 4's interactive branch needs a different shape and the plan must be revised before Task 4. If the close does **not** remove the lockfile, Task 4's `close_editor` falls back to a terminate-then-verify on the Editor pid and the record says so.

- [ ] **Step 6: Record whether a launchd job can drive Unity, and at what speed**

Both farmbot jobs must be bootstrapped first — neither is loaded today. Then, from the service's own environment, run the Step 3 import a second time against a second throwaway slot through `launchctl` (`launchctl asuser $(id -u) ...` is not a substitute; use a one-shot agent or a `serve`-hosted call). Record three things.

1. **The licence.** Does the batch Editor acquire the single Personal seat in `/Library/Application Support/Unity/Unity_lic.ulf`? Note that `com.kuaiwa.farmbot.serve` is a LaunchAgent in the `gui/501` domain, so a window server exists whenever the user is logged in; if it fails, the failure is the licensing IPC, and the log line is what the operator needs.
2. **Contention with the human's own Editor.** Repeat the run with the operator's Editor open on `/Users/elendil/WorkSpaces/Farm/Farm-Client`. Record the exact error on whichever of the two is refused. This decides whether `SlotPool.switch` needs the `pgrep` preflight Task 4 adds.
3. **The throttled cost.** The plist sets `ProcessType` to `Background`, so the launchd run inherits the background scheduling band while Step 3's foreground run did not. Record both wall clocks side by side.

**Success condition:** either it works and the two wall clocks are within a factor the operator accepts, or the record names the failure precisely enough for them to choose between a login session, `ProcessType=Standard` and a purchased seat.

- [ ] **Step 7: Write the record**

Create `docs/superpowers/spikes/2026-09-19-unity-slot-spike.md` with one section per step: the command, the measured numbers, and the answer. End with a decision list of exactly eight lines, one per question: LFS strategy (and whether the failure mode is auth or reachability), import cost foreground versus launchd, batch exit-code contract, instance id source, App Nap verdict against the 120-second threshold, Editor RSS, Editor start timeout, graceful close method and its lockfile timeout. Then clean up:

```bash
git -C /Users/elendil/WorkSpaces/Farm/farm-linear-agent/.local/repos/Farm-Client.git worktree remove --force "$SLOT"
rm -rf "$SPIKE"
```

- [ ] **Step 8: Commit**

```bash
git add docs/superpowers/spikes/2026-09-19-unity-slot-spike.md
git commit -m "docs: record the Unity slot spike on the Mac

Import cost, LFS strategy, batch exit codes, the live Editor instance id, App Nap
and the launchd licence question, measured rather than assumed. Plan 1b's Tasks 3,
4 and 7 read their defaults from this record.

Co-Authored-By: <model> <noreply@anthropic.com>"
```

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

Run: `python3 -W error -m unittest discover -s tests -v -k target -k resolve_commit -k remote_head -k pins -k acknowledgment -k clones`
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

- `session = self.ledger.session(prepared["session_id"])` is read at `agent/receiver.py:167`, **before** `ensure_session` at line 170. For a brand-new session — the only case this task exists for — that local is `None`. The block therefore rebinds the variable from `ensure_session`'s own return value, which is already the session view, and guards only on the target.
- `session_id = prepared["session_id"]` is not bound until after `route(...)`. The block uses `prepared["session_id"]` explicitly rather than depending on a name that does not exist yet.
- The pin does **not** get its own `_send`. It is appended to the ACK body that the existing code already sends with this event's `ack_id`.

Replace lines 167–170 with:

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

Run: `python3 -W error -m unittest discover -s tests -v -k target -k resolve_commit -k remote_head -k pins -k acknowledgment -k clones`
Expected: PASS. Count the selection before writing a number here: the eight new tests plus whatever the substrings already match in the suite.

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

**Why:** `agent/worktrees.py:11` sets `GIT_ENV = {"GIT_LFS_SKIP_SMUDGE": "1", ...}` and `_git` applies it to every call in the module, deliberately — task worktrees are for code and Farm-Client carries gigabytes of binaries. FarmBot's bare clone therefore holds 176 KB of LFS objects against 3.6 GB in the human's checkout. A slot built through the existing helper would hand Unity 3,619 pointer files, and the import would fail in a way that reads as project corruption rather than as a configuration mistake.

**Design:** `_git` takes its environment as an argument with the pointer-preserving map as the default, and a second map without `GIT_LFS_SKIP_SMUDGE` is used for slot calls only. Slot creation then has three parts that each fail loudly: `git worktree add --detach`, `git lfs fetch` plus `git lfs checkout`, and a sampled check that no tracked LFS file still begins with `version https://git-lfs`. `SlotPool.ensure` is called once at service start, is idempotent, and never disturbs a slot that is busy, switching or held — a restart must not move a folder an Editor has open. The test fixture's origin has no LFS objects at all, so these tests also pin the rule that a zero-object fetch is success rather than an error.

**One-time provisioning this task does not do.** `/HybridCLRData/` is gitignored and is 1.9 GB in the human's checkout, and HybridCLR is what produces `Library/ScriptAssemblies/HotUpdate.dll`, which is the identity probe's whole subject. If Task 0 Step 3 finds that a fresh slot does **not** produce `HotUpdate.dll` without it, add the generator invocation here as a one-time step in `SlotPool.ensure`, guarded so it runs only when `HybridCLRData` is absent, and record the command in the spike. If Step 3 found `HotUpdate.dll` present, write one line in the commit body saying so, so the question is closed rather than forgotten.

**Files:**
- Modify: `agent/config.py` (`Paths.editors`)
- Modify: `agent/worktrees.py` (`_git` environment argument, `add_slot`, `checkout_commit`, `materialize`, `slot_clean`, `pointers_remain`)
- Create: `agent/slots.py` (`SlotError`, `SlotPool.__init__`, `SlotPool.ensure`)
- Modify: `docs/superpowers/specs/2026-09-17-farm-linear-agent-design.md` (§7, the slot-1 sentence)
- Test: `tests/test_worktrees.py`, `tests/test_slots.py`

**Interfaces:**
- Consumes: `Worktrees.ensure_clone(repo)`, `Worktrees.resolve_commit(repo, ref=None)` from Task 1, `Ledger.ensure_slot` and `Ledger.set_slot_state` from Task 2.
- Produces:
  - `agent.config.Paths.editors`, `Path(config.local_root) / "editors"`.
  - `Worktrees.add_slot(repo, path, commit) -> Path`, creating the worktree if absent and materializing LFS either way.
  - `Worktrees.checkout_commit(path, commit) -> str`, `Worktrees.materialize(path) -> str`, `Worktrees.slot_clean(path) -> bool`, `Worktrees.pointers_remain(path) -> bool`.
  - `agent.slots.SlotPool(ledger, worktrees, entries, *, host, editors_root, unity=None, mcp=None, clock=time.time)`. `entries` is `config.slots` after defaults are applied by `agent.slots.slot_entry(raw)`.
  - `SlotPool.ensure() -> list[dict]`, the slot views after registration. Raises `SlotError` when a slot folder cannot be made usable, naming which of the three parts failed.

- [ ] **Step 1: Write the failing tests**

In `tests/test_worktrees.py`, inside `WorktreeTests`:

```python
    def test_a_slot_worktree_is_detached_at_the_commit_and_lives_where_it_is_told(self):
        commit = self.trees.resolve_commit("Farm-Client")
        slot = Path(self.tmp.name) / "editors" / "slot-1"
        self.assertEqual(self.trees.add_slot("Farm-Client", slot, commit), slot)
        self.assertEqual(git("rev-parse", "HEAD", cwd=slot), commit)
        self.assertEqual(git("symbolic-ref", "-q", "HEAD", cwd=slot, allow_failure=True), "")
        self.assertTrue(self.trees.slot_clean(slot))

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
        """The injected sleep. The fixture's clock is a variable, so a pool loop that waits on a deadline
        makes progress instead of spinning forever under a constant clock."""
        self.now += seconds

    def pool(self, worktrees=None, **kwargs):
        # editor_scan is stubbed out: a developer with their own Unity Editor open would otherwise fail every
        # switch test on their machine, and no test in this suite shells out (global constraint).
        kwargs.setdefault("editor_scan", lambda folder: None)
        return SlotPool(self.ledger, worktrees or self.trees, [self.entry], host="test",
                        editors_root=self.root / "editors", clock=lambda: self.now,
                        sleep=self.advance, **kwargs)

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
        return path

    def checkout_commit(self, path, commit):
        if not self.COMMIT.match(commit or ""):
            raise WorktreeError("a slot is only ever moved to a full commit")
        _git("checkout", "--detach", "--force", commit, cwd=path, env=SLOT_ENV, timeout=3600)
        self.materialize(path)
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
under `.local/editors/slot-1`, with its LFS objects materialized and its `Library/` imported once. On the
Mac that is a measured 5 GB and single-digit minutes; FarmQA's isolated client copy on the Windows host is
a candidate seed for that host's slot, not a prerequisite for slot 1.
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
3,619 pointer files. Slot git now runs with smudge on, fetches and checks out the
LFS objects, and refuses the folder by name if any tracked pointer survives. The
spec's claim that slot 1 is the Windows host's existing copy is amended.

Co-Authored-By: <model> <noreply@anthropic.com>"
```

---

### Task 4: The slot switch moves the slot to a commit and parks it back

The heart of the plan. Spec §7, verbatim: confirm tracked files are clean, `git checkout --detach <commit>`, `git lfs checkout`, then in interactive mode ask the Editor to refresh and wait for compilation with zero Console errors. In batch mode the batchmode Editor compiles on start. When a slot becomes idle the pool parks it back on `origin/main`.

**Why:** the switch is the whole reason slots exist: `Library/` is built once and then moved between commits instead of being rebuilt per task. Measured on this Mac, a post-checkout incremental refresh is 12 to 24 seconds against a first import of several minutes, which is what makes the design pay.

**Decision this task encodes:** five.

1. **A switch fetches before it checks out.** The pinned commit is not guaranteed to be in FarmBot's bare clone: `enqueue --commit <sha>` lets an operator name any commit, Task 1's receiver pin comes from a bare `ls-remote` that never touched the clone, and minutes to hours pass between the pin and the grant. Without a fetch, `checkout --detach` fails with `fatal: reference is not a tree`, which this plan would classify as a retryable git failure, re-queue once, and then fail the item as a bogus "verification gap". The fetch is the first action of the git stage, so its own failure is retryable, and the tests below depend on it: they create a commit on the fixture origin *after* `ensure()` and expect the switch to reach it.
2. **The pool starts and closes the Editor; nothing else does.** Spec §7's state table says an `idle_closed` slot accepts interactive work, which means something has to start an Editor, and that a batch request on an `idle_open` slot is accepted "after a graceful close". With exactly one slot, the graceful close is not an edge case — it is the required path for every batch run that follows an interactive one. Without `open_editor`, an interactive grant on a closed slot would refresh and probe an MCP server with no Editor behind it, fail at the probe stage, hold the slot and fail the item. Without `close_editor`, the ledger would say the slot is free while a real Editor still held `Temp/UnityLockfile`, and the next batch run would start `Unity -batchmode` on a locked folder. Both directions get a test.
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
  - `agent.unity.writable_roots(system=None) -> list[Path]`, the host paths a Unity process writes **outside** the project. This is the only place those paths are named, so the Windows plan supplies its own instead of adding a branch to the scheduler.
  - `agent.unity.batch_test_command(editor, project, *, results, log, test_platform="EditMode", assemblies=(), build_target=None) -> list[str]`. No `-quit` and no `-nographics` (spec §8), and no `-accept-apiupdate` either: spec §8 does not list it, and it authorises the API Updater to rewrite scripts under `Assets/`, which would leave tracked files modified and make the *next* switch fail its clean check — an intermittent failure whose cause is two runs earlier. Task 7 calls this function and puts the finished argv in the dispatch payload, so no host-specific flag is ever spelled out in prose.
  - `SlotPool.switch(slot_id, commit, mode) -> dict`, the slot view. Raises `SlotError` with `.stage` set to `"git"`, `"editor"`, `"compile"` or `"probe"` so the caller can apply the right rule.
  - `SlotPool.open_editor(slot, entry) -> str`, starting `Unity -projectPath <folder>` and waiting until the MCP endpoint answers and reports this instance; returns the discovered instance id. Raises `SlotError(stage="editor")` on timeout.
  - `SlotPool.close_editor(slot) -> bool`, asking the Editor to exit through the MCP and waiting for `Temp/UnityLockfile` to disappear. Raises `SlotError(stage="editor")` if the lock survives the timeout.
  - `SlotPool.wait_for_quiet(slot, timeout) -> None`, polling `mcpforunity://editor/state` until compilation, domain reload, asset import and test running are all false. Raises `SlotError(stage="probe")` on timeout.
  - `SlotPool.park(slot_id, mode) -> dict`, moving the slot to the repo's remote default head and recording `parked_commit`. The departing mode decides the parked state, so it is passed in rather than read back from a slot row the release has already overwritten.
  - `SlotPool.clear_stale_lock(folder, alive) -> bool`, removing `Temp/UnityLockfile` only when `alive()` says the process is gone (spec §7). Called from the batch branch of `switch`, from `park` on the closed path and from `settle` in Task 6, and tested in all three branches; not the dead, unverified code it would otherwise be.
  - `SlotPool.another_editor_running(folder) -> str | None`, the cheap preflight: a Unity process on this host whose command line names a *different* project folder. Named because one Personal seat backs both it and the operator's own Editor.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_unity.py`:

```python
import tempfile
import unittest
from pathlib import Path

from agent.unity import (UnityError, batch_test_command, candidates, editor_path, other_editor_project,
                         project_version, writable_roots)


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

    def test_the_host_writable_roots_cover_where_a_batch_editor_actually_writes(self):
        """A macOS batch Editor writes the shared UPM cache, the Editor log, EditorPrefs and the licensing
        client's files, all outside the project. Under the Codex seatbelt those writes are denied and the
        failure reads as a Unity licensing or cache error (spec §8's writable roots)."""
        roots = [str(p) for p in writable_roots(system="Darwin")]
        for fragment in ("Library/Unity", "Library/Logs/Unity", "Library/Preferences",
                         "Library/Application Support/Unity"):
            self.assertTrue(any(fragment in root for root in roots), (fragment, roots))
        self.assertTrue(all("Unity" in r or "Preferences" in r for r in writable_roots(system="Windows")))

    def test_another_editor_on_a_different_project_is_seen_and_our_own_is_not(self):
        listing = (f"901 /Applications/Unity/Hub/Editor/2022.3.62f3/Unity.app/Contents/MacOS/Unity "
                   f"-projectPath /Users/x/WorkSpaces/Farm/Farm-Client\n")
        self.assertEqual(other_editor_project(self.project, system="Darwin", run=lambda: listing),
                         "/Users/x/WorkSpaces/Farm/Farm-Client")
        mine = f"901 Unity -projectPath {self.project}\n"
        self.assertIsNone(other_editor_project(self.project, system="Darwin", run=lambda: mine))
        self.assertIsNone(other_editor_project(self.project, system="Darwin", run=lambda: ""))
```

In `tests/test_slots.py`, add:

```python
INSTANCE = "slot-1@0123456789abcdef"


class FakeMcp:
    """Stands in for the Unity MCP client: records what the pool asked the Editor to do, and stands in for
    the Editor process itself by writing and removing the lock file the pool waits on."""

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

    def exit(self, slot):
        self.calls.append(("exit", slot["slot_id"]))
        self._lock(slot).unlink(missing_ok=True)
        self.open_folders.discard(slot["folder"])

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
        self.pool().ensure()
        pool = self.pool(mcp=FakeMcp())
        pool.switch("unity_slot:1", self.commit("one"), "interactive")
        pool.park("unity_slot:1", "interactive")
        pool.mcp.calls.clear()
        pool.switch("unity_slot:1", self.commit("two"), "batch")
        self.assertEqual([name for name, _ in pool.mcp.calls], ["exit"])
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


def writable_roots(system=None):
    """Where a Unity process writes outside the project. A batch Editor under the Codex seatbelt is denied
    these unless they are in its writable roots, and the denial surfaces as a licensing or package-cache
    error that reads as project corruption. Naming them here, not in the scheduler, is what keeps the
    Windows plan a configuration change (spec §8)."""
    system = system or platform.system()
    home = Path.home()
    if system == "Darwin":
        return [home / "Library" / "Unity", home / "Library" / "Logs" / "Unity",
                home / "Library" / "Preferences", home / "Library" / "Application Support" / "Unity",
                Path("/Library/Application Support/Unity")]
    if system == "Windows":
        local = Path(os.environ.get("LOCALAPPDATA", str(home / "AppData" / "Local")))
        roaming = Path(os.environ.get("APPDATA", str(home / "AppData" / "Roaming")))
        return [local / "Unity", roaming / "Unity", Path(r"C:\ProgramData\Unity")]
    return [home / ".config" / "unity3d", home / ".local" / "share" / "unity3d"]


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
```

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
    OPEN_STATES = ("idle_open", "interactive_busy")

    def switch(self, slot_id, commit, mode):
        """Spec §7's sequence, in order. The caller has already marked the slot 'switching' by acquiring it.

        The slot row was read before the acquire overwrote its state, so `was_open` comes from the state the
        ledger recorded for the slot's *Editor*, not from the transient 'switching'.
        """
        slot = self.ledger.slot(slot_id)
        if slot is None:
            raise SlotError(f"unknown slot: {slot_id}")
        folder = Path(slot["folder"])
        entry = self.entries.get(slot_id, DEFAULTS)
        was_open = bool(self.mcp is not None and self.editor_is_open(slot))
        other = self.another_editor_running(folder)
        if other:
            # One Personal seat backs this host and the operator's own Editor is routinely open on their
            # checkout. Starting a second Editor loses the licence race and reports it as something that
            # reads like project corruption, so hold the slot with a sentence a human can act on instead.
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
            return self.ledger.set_slot_state(slot_id, "batch_busy", last_switch_at=self.clock())
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
        return self.ledger.set_slot_state(slot_id, "interactive_busy", last_switch_at=self.clock())

    def editor_is_open(self, slot):
        """Unity's own marker, not the ledger's opinion: the lock file exists for as long as the Editor holds
        the folder, which is exactly what makes a second Editor on the same folder impossible."""
        return (Path(slot["folder"]) / "Temp" / "UnityLockfile").exists()

    def open_editor(self, slot, entry):
        """Start the Editor and wait until the MCP endpoint answers and names this folder's instance. The
        timeout is the cold-start figure Task 0 Step 5 measured; the returned instance id is what the worker
        pins its own MCP session with."""
        try:
            return self.mcp.start(slot, entry)
        except Exception as exc:
            raise SlotError(f"{slot['slot_id']}: the Editor did not come up: {exc}", stage="editor") from exc

    def close_editor(self, slot, timeout=None):
        """EditorApplication.Exit(0) through the MCP, then wait for Temp/UnityLockfile to disappear. Starting
        `Unity -batchmode` on a folder whose lock survives is the corruption the slot design exists to avoid."""
        timeout = self.entries.get(slot["slot_id"], DEFAULTS)["close_timeout"] if timeout is None else timeout
        try:
            self.mcp.exit(slot)
        except Exception as exc:
            raise SlotError(f"{slot['slot_id']}: the Editor refused to close: {exc}", stage="editor") from exc
        deadline = self.clock() + timeout
        while self.editor_is_open(slot):
            if self.clock() >= deadline:
                raise SlotError(f"{slot['slot_id']}: Temp/UnityLockfile survived the close", stage="editor")
            self.sleep(1.0)
        return True

    def wait_for_quiet(self, slot, timeout):
        """spec §7's 'wait for compilation'. refresh_unity returns at once, so probing straight afterwards
        normally observes is_compiling or is_domain_reload_pending and aggregates to unknown — which this plan
        routes to a permanent hold. Measured on this Mac an incremental refresh is 12 to 24 seconds."""
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

Add to `SlotPool.__init__`: `self.sleep = sleep or time.sleep` (injected so no test waits on a real clock) and `self.editor_scan = editor_scan or agent.unity.other_editor_project` (injected so no test in this suite shells out to `pgrep`; `SlotFixture.pool` passes `lambda folder: None`). Add two keys to `DEFAULTS`: `"quiet_timeout": 300` and `"close_timeout": 120`, both seeded from Task 0 Step 5's measured numbers. In `agent/unity.py`, beside `writable_roots`:

```python
def other_editor_project(folder, system=None, run=None):
    """The project path of a Unity Editor on this host that is not this slot's, or None.

    One Personal seat backs this Mac, and the operator's own Editor is routinely open on their checkout; the
    second Editor to start loses the licence race and reports it as something that reads like project
    corruption. This is the cheap preflight that turns that into a sentence."""
    system = system or platform.system()
    command = (["pgrep", "-fl", "Unity.app/Contents/MacOS/Unity"] if system == "Darwin" else
               ["pgrep", "-fl", "Unity"] if system != "Windows" else
               ["powershell", "-NoProfile", "-Command",
                "(Get-CimInstance Win32_Process -Filter \"Name='Unity.exe'\").CommandLine"])
    run = run or (lambda: subprocess.run(command, capture_output=True, text=True, timeout=5).stdout)
    try:
        out = run()
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in (out or "").splitlines():
        match = re.search(r"-projectPath\s+\"?([^\"\s]+)", line)
        if match and Path(match.group(1)).resolve() != Path(folder).resolve():
            return match.group(1)
    return None
```

with `import subprocess` added to that module. `run` is injectable so the test double never shells out.

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

**Design:** three changes to the ported code, each because FarmBot is not FarmQA. The endpoint is a constructor argument instead of a hardcoded `9090`, because this Mac's plugin defaults to `8080`. The "exactly one connected instance" rule at `farmqa_identity.py:88-90` becomes "the expected instance id is present", followed by `set_active_instance` and a re-read of `project/info` after selection — the strict rule is baked into four FarmQA call sites and would make a second slot impossible, and would also fail if any other Editor under this user connected to the same server. The expected assembly set stays `{HotUpdate, AOTScripts, Nova.Runtime, MCPForUnity.Editor}` but moves into the probe's arguments **and out of the C# probe**. The copied `editor-readiness.cs.txt` hardcodes the filter (`if (name != "HotUpdate" && name != "AOTScripts" && name != "Nova.Runtime" && name != "MCPForUnity.Editor") continue;`), so passing the set from Python alone would be theatre: the Python side can only compare against what the C# chose to return, and a different project would still need a code change — in the `.cs.txt`. So Step 3 also deletes that `continue` and lets the probe return every loaded assembly, and `collect` does the filtering. That is a two-line change to the copied file and it is what makes the Windows or Android slot genuinely configuration-only. All four assemblies exist in this project today, in `Library/ScriptAssemblies`. `verdict` is no longer the constant `BLOCKED`: the aggregate is `match` only when every check is `match`, and `unknown` is never `match` (`farmqa_request_session.py:138-139`).

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
  - `agent.slots.UnityIdentity(endpoint, probe_path)` implementing the pool's `refresh`, `probe` and `quiescent`.

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

    def __init__(self, probe_path, timeout=120, start_timeout=600, clock=time.time, sleep=time.sleep):
        self.probe_path = Path(probe_path)
        self.timeout = timeout
        self.start_timeout = start_timeout   # Task 0 Step 5's cold Editor start to a usable endpoint
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
        (Editor/Helpers/ProjectIdentityUtility.cs), but it is read, not recomputed: Task 0 Step 5 records
        whether the hash of `<folder>/Assets` matches, and the live value is authoritative either way.
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

        This is also where an unstated prerequisite becomes a named failure: the endpoint is served by a
        separate uvx process the Editor manages, not by the Editor itself, so "nothing is listening on 8080"
        must read as an editor-stage error and not as an identity mismatch.
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
                    raise SlotError(f"{slot['slot_id']}: no MCP instance after {self.start_timeout}s; is the "
                                    f"MCP for Unity server process running?", stage="editor")
                self.sleep(5.0)

    def exit(self, slot):
        """Task 0 Step 5 measures this: EditorApplication.Exit(0) through execute_code, then the pool waits
        for Temp/UnityLockfile to disappear. If the spike found the lock survives, this method terminates the
        Editor pid instead and the record says so."""
        self._client(slot).call_tool("execute_code", {"code": "UnityEditor.EditorApplication.Exit(0);"})

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

Two small additions come with it. `quiet(state)` is a new two-line helper in `agent/identity.py` beside `ready`: the same four clauses (`compilation.is_compiling`, `compilation.is_domain_reload_pending`, `assets.is_updating`, `tests.is_running`) with neither the instance comparison nor the staleness bound, because during a refresh the instance is what we are waiting to hear from; `ready` keeps both. And `agent.unity.editor_holds_project(folder, system=None, run=None)` is `other_editor_project`'s complement — same process listing, same injectable `run`, returning True when a Unity process names *this* folder. It is what makes the batch release a real check rather than a constant.

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
snapshot come over unchanged. Three things open up: the endpoint is configuration
rather than port 9090, the expected instance must be present rather than alone, and
the verdict is an aggregate over the checks rather than a constant.

Co-Authored-By: <model> <noreply@anthropic.com>"
```

---

### Task 6: The pool grants a slot on its own thread and settles it afterwards

Two-phase acquisition becomes real. An `awaiting_resource` item has been inert since Plan 1a because `Scheduler.tick` drains only `ledger.queue()`, which selects `state='queued' AND worker_pid IS NULL`. The pool now grants the oldest request, performs the switch, writes the reservation token where the worker can read it, and calls `ledger.resume`, after which the *existing* launch loop picks the item up on the next tick with no change to the queue query.

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
        self.assertEqual(("exit", "unity_slot:1"), pool.mcp.calls[-1])   # closed gracefully first
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
        granted = 0
        for kind in sorted({entry["kind"] for entry in self.entries.values()}):
            reservation = self.ledger.acquire(kind, owner=self.owner, host=self.host)
            if reservation is None:
                continue
            self._hand_over(reservation)
            granted += 1
        return granted

    def _hand_over(self, reservation):
        item_id, slot_id = reservation["item_id"], reservation["resource"]
        self.last_observation = None
        try:
            self.switch(slot_id, reservation["commit_sha"], reservation["mode"])
        except SlotError as exc:
            self._switch_failed(reservation, exc)
            return
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
git add agent/ledger.py agent/slots.py agent/service.py tests/test_slots.py tests/test_scheduler.py
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

A batch reservation adds **no** MCP server and instead widens the worker's writable roots, because a `Unity -batchmode` child writes far more than the project: inside the slot, `Library/`, `Temp/`, `Logs/` and the results XML; and outside it, on this Mac, `~/Library/Unity/cache/**` (the shared UPM and asset cache), `~/Library/Logs/Unity/Editor.log`, `~/Library/Preferences/com.unity3d.UnityEditor5.x.plist` (EditorPrefs, where MCP for Unity also keeps its `HttpUrl`), `~/Library/Application Support/Unity/**`, and the licensing client's `/Library/Application Support/Unity/Unity_lic.ulf`. `Launcher.spawn` sets `sandbox_workspace_write.writable_roots` to exactly the list it is given and inherits the real `HOME`, so under the Codex seatbelt every one of those writes is denied and the failure surfaces as a Unity licensing or cache error — the class of silent macOS failure that reads as project corruption. The list comes from `agent.unity.writable_roots(...)`, so the Windows plan supplies `%LOCALAPPDATA%\Unity` instead of adding a branch here. Task 0 Step 6 verifies the sandboxed batch run, not only the unsandboxed one.

The payload also carries the finished batch argv from `agent.unity.batch_test_command`, so the two flags that matter most — no `-quit`, no `-nographics` — are enforced where the command actually runs rather than only inside a unit test, and `-buildTarget` is not silently dropped by a skill document written in prose. The dispatch payload carries the token's *path*, never the token.

**Files:**
- Modify: `agent/scheduler.py` (`launch`)
- Modify: `agent/dispatch.py` (`dispatch_message` gains `resource=None`)
- Test: `tests/test_scheduler.py`, `tests/test_dispatch.py`, `tests/test_launcher.py`

**Interfaces:**
- Consumes: `Launcher.spawn(item_id, message, mcp_servers, budget_seconds, cwd, extra_env=None, writable=())`, unchanged; `Ledger.active_reservation(item_id)`; `Ledger.slot(slot_id)`.
- Produces:
  - `dispatch_message(..., resource=None)`. When given, the payload gains a `resource` object with `kind`, `mode`, `slot`, `folder`, `instance`, `mcp_address`, `account`, `commit`, `token_file`, `unity`, `build_target`, `batch_command` and `results_dir`. When absent the payload is byte-for-byte what it is today.
  - `Scheduler.launch` unchanged in signature and return value.
  - `agent.launcher.write_mcp_config` unchanged in signature; the scheduler hands it a per-runtime server entry rather than one shape for both writers.

- [ ] **Step 1: Write the failing tests**

In `tests/test_scheduler.py`, inside `SchedulerTests`:

```python
    def test_only_an_interactive_reservation_puts_the_unity_mcp_in_the_worker(self):
        item = self.granted_item(mode="batch")
        self.scheduler.tick()
        item_id, message, servers, _, _ = self.launcher.spawned[-1]
        self.assertEqual((item_id, servers), (item, {}))
        self.assertIn(str(self.slot_folder), self.launcher.spawn_writable)
        # Where a macOS batch Editor writes outside the project; without these the seatbelt denies the
        # licensing and package-cache writes and the run fails as if Unity were broken.
        for root in agent.unity.writable_roots():
            self.assertIn(str(root), self.launcher.spawn_writable)
        payload = json.loads(message.split("\n\n", 1)[1])
        self.assertEqual(payload["resource"]["unity"], str(self.unity_binary))
        self.assertNotIn("-quit", payload["resource"]["batch_command"])
        self.assertIn("-buildTarget", payload["resource"]["batch_command"])

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
```

`granted_item(mode=..., issue_id=ISSUE)` is a new helper on `SchedulerTests`. It extends the existing `item()` helper: it seeds an issue and a session, creates the work item with a pinned `target` (commit `"a" * 40`, `selected_at` the ISO string), claims it, calls `await_resource(..., "unity_slot", mode)`, calls `ledger.ensure_slot("unity_slot:1", kind="unity_slot", host="test", folder=self.slot_folder, mcp_address="http://127.0.0.1:8080/mcp", instance="slot-1@0123456789abcdef")`, calls `ledger.acquire("unity_slot", owner="pool", host="test")` and then `ledger.resume` — leaving the item queued with an active reservation, which is exactly the state the pool leaves behind. It sets `self.slot_folder` (a real temporary directory holding `ProjectSettings/ProjectVersion.txt`, so `editor_path` has something to read) and `self.unity_binary` (a touched file, passed as the slot entry's `unity` override, so no test looks in `/Applications`). `waiting_item(mode=...)`, used by Task 9, stops one step earlier: it never acquires, so the reservation stays `queued`.

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
                                             "token_file": "/runs/i/reservation.token", "unity": "/u/Unity",
                                             "build_target": "OSXUniversal",
                                             "batch_command": ["/u/Unity", "-batchmode"],
                                             "results_dir": "/runs/i"})
        payload = json.loads(message.split("\n\n", 1)[1])
        self.assertEqual(payload["resource"]["slot"], "unity_slot:1")
        self.assertEqual(payload["resource"]["unity"], "/u/Unity")
        self.assertNotIn("token_file", json.dumps(payload).replace('"token_file"', ""))  # path only, no value
```

Match the keyword names and the two dict shapes to whatever `tests/test_dispatch.py` already passes; the point of the test is the `resource` block, not a second copy of the existing fixture.

In `tests/test_launcher.py`, a new test — an injected server entry has to survive **both** writers, because §19's first risk is that the default runtime changes after Task 0:

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

and extend the existing writable-roots assertion so a slot folder passed through `writable` appears in the Codex `writable_roots` list exactly as the worktrees do.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -W error -m unittest discover -s tests -v -k reservation -k resource_block -k writable -k runtime_s_own_shape`
Expected: FAIL. `dispatch_message()` rejects the `resource` keyword and the scheduler still passes `{}`.

- [ ] **Step 3: Carry the resource in the dispatch payload**

In `agent/dispatch.py`, add `resource=None` to the signature and one line to the payload, after `"target"`:

```python
        "resource": resource,
```

and extend `AUTHORITY` with one sentence:

```python
    "When a resource block is present you hold that reservation for this run only: address the Editor with "
    "the instance id given, release it through the ledger CLI when you are done, and never open Unity on a "
    "task worktree."
```

- [ ] **Step 4: Build the servers and the roots from the reservation**

In `agent/scheduler.py`, inside `launch`, after `paths = self._worktrees_for(...)`:

```python
        reservation = self.ledger.active_reservation(item["id"])
        servers, slot, resource = {}, None, None
        if reservation is not None and reservation["kind"] in skill.resources:
            slot = self.ledger.slot(reservation["resource"])
            entry = self.slot_entries.get(slot["slot_id"], {})
            state_dir = self.launcher.state_dir(item["id"])
            results = Path(state_dir) / "unity-tests.xml"
            try:
                unity = agent.unity.editor_path(slot["folder"], override=entry.get("unity"))
            except agent.unity.UnityError as exc:
                # Fail the item with the list of paths tried, rather than a traceback on the scheduler thread.
                self.ledger.fail_queued(item["id"], f"no Unity editor for {slot['slot_id']}: {exc}")
                return None
            resource = {"kind": reservation["kind"], "mode": reservation["mode"], "slot": slot["slot_id"],
                        "folder": slot["folder"], "instance": slot["instance"], "account": slot["account"],
                        "mcp_address": slot["mcp_address"], "commit": reservation["commit_sha"],
                        "token_file": str(Path(state_dir) / "reservation.token"),
                        "unity": str(unity), "build_target": entry.get("build_target_argument"),
                        "batch_command": agent.unity.batch_test_command(
                            unity, slot["folder"], results=results,
                            log=Path(state_dir) / "unity-editor.log",
                            test_platform=entry.get("test_platform", "EditMode"),
                            assemblies=tuple(entry.get("test_assemblies", ())),
                            build_target=entry.get("build_target_argument")),
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

then pass `resource=resource` to `dispatch_message`, `servers` instead of `{}` to `spawn`, and extend the writable list:

```python
        extra_writable = []
        if reservation is not None and slot is not None and reservation["mode"] == "batch":
            # A Unity -batchmode child writes Library/, Temp/, Logs/ and the results XML inside the slot, and
            # the UPM cache, the Editor log, EditorPrefs and the licence files outside it. agent/unity.py owns
            # that host list so the Windows plan is a configuration change and not a branch here (spec §8).
            extra_writable.append(Path(slot["folder"]))
            extra_writable.extend(agent.unity.writable_roots())
        handle = self.launcher.spawn(item["id"], message, servers, int(skill.budget["max_hours"] * 3600), cwd=primary,
                                     extra_env={"FARMBOT_DB": str(self.db_path), "PYTHONPATH": pythonpath},
                                     writable=[Path(self.db_path).parent, *paths.values(), *clones, *extra_writable])
```

`Scheduler.__init__` gains `slot_entries=None` (a `{slot_id: entry}` map, `{}` by default), which `service.build` fills from the same `slot_entry(raw)` list it hands the pool. Import the module as `from . import unity as unity_module` — or `import agent.unity` — rather than pulling names in, so the scheduler still names no install path of its own.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -W error -m unittest discover -s tests -v -k reservation -k resource_block -k writable -k runtime_s_own_shape`
Expected: PASS. Four new tests — two in `tests/test_scheduler.py`, one in `tests/test_dispatch.py`, one in `tests/test_launcher.py` — plus whatever the substrings already match.

- [ ] **Step 6: Run the whole suite and commit**

Run: `python3 -W error -m unittest discover -s tests`
Expected: OK, 199 tests (+4).

```bash
git add agent/scheduler.py agent/dispatch.py tests/test_scheduler.py tests/test_dispatch.py tests/test_launcher.py
git commit -m "feat(scheduler): inject Unity tools from the reservation, not the manifest

skill.resources was validated and read by nothing, and launch() passed an empty MCP
map. An interactive reservation now injects exactly one server for the life of that
run; a batch reservation injects none and instead makes the slot folder writable, so
the batchmode Editor can write Library, Temp, Logs and its results file.

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
        other, other_token = self.granted_item(mode="interactive", issue_id=OTHER)
        self.run_cli("release-resource", "--item", other, "--token-file", str(other_token), "--outcome", "unclean")
        self.assertEqual(self.run_cli("slots")[0]["state"], "held")
        self.run_cli("recover-slot", "--slot", "unity_slot:1", "--reason", "operator closed Unity")
        self.assertEqual(self.run_cli("slots")[0]["state"], "idle_closed")
```

`seeded_item` gains an optional `target=None` and `SELECTED_AT` is imported from `test_ledger`, the same ISO-8601 constant every other fixture in this plan uses. `granted_item(mode=..., issue_id=ISSUE)` is a new helper on `CliTests` that seeds a pinned item, claims it, calls `await-resource`, registers `unity_slot:1` through the ledger, acquires the reservation and writes the raw token to `<state_dir>/reservation.token` at mode 0600, returning `(item_id, token_path)` — the state the pool leaves behind for a worker.

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
   `await-resource --resource unity_slot --mode batch` and exit. A fresh worker resumes once the slot has
   been switched to your pinned commit; its launch message carries a `resource` block with the slot folder,
   the pinned commit, the Unity binary, a results directory and a ready-made `batch_command`. **Run exactly
   the argv in `resource.batch_command`** — do not compose your own. It already carries this host's
   `-buildTarget` and deliberately omits `-quit` (the runner exits on its own and `-quit` races the results
   writer) and `-nographics` (the PlayMode fixtures render FairyGUI). Read `resource.results_dir`'s
   `unity-tests.xml`, not the exit code. Then `release-resource --outcome quiescent`.
5. Behaviour no test covers needs an interactive slot: `await-resource --resource unity_slot --mode
   interactive`. The fresh worker gets one MCP server named `unity`; call `set_active_instance` with the
   `resource.instance` from your launch message before anything else, because the server is shared per user
   and the selection is per MCP session. Leave the Editor in Edit Mode with nothing compiling, then
   `release-resource --outcome quiescent`; if you cannot, `--outcome unclean`, which holds the slot for an
   operator instead of handing a wedged Editor to the next worker.

You hold at most one slot at a time: release before requesting a different mode. Never open Unity on a task
worktree, never edit the slot folder, and never run a player build in a slot — slots budget an import-only
`Library/` and a player build triples it.

If your work item was created by an operator with `enqueue` rather than by a Linear delegation, its session
is local and Linear has no agent session for it: report through an issue comment and do not expect session
activities to appear.
```

- [ ] **Step 6: Rewrite the operating contract**

In `docs/operating-contract.md`: in the authority table, change `fix`'s Resources cell from `Unity slot (not yet available)` to `Unity slot (one, batch or interactive, two-phase)`. In the work-item states paragraph, replace `A waiting item has no process and holds no resource.` with `A waiting item has no process. An item in awaiting_resource holds a queued reservation; only the pool's grant turns it back into queued work.` And replace the first bullet under `## Limits in this phase` with:

```markdown
- One host at a time (the Mac since 2026-09-18; Windows follows in its own plan) and one Unity slot. Two
  items that both need Unity serialize on it in arrival order; a worker holds at most one slot and releases
  it after its own quiescence check. A failing probe holds the slot until an operator runs
  `recover-slot`. No slot is ever released on a timer. A slot runs Edit Mode and PlayMode fixtures and never
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
Expected: OK, 203 tests (+5 new, −1 replaced).

```bash
git add agent/__main__.py agent/service.py skills/fix/SKILL.md docs/operating-contract.md tests/test_cli.py tests/test_service.py
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
        self.assertEqual(self.ledger.slot("unity_slot:1")["state"], "interactive_busy")  # still held; the pool probes

    def test_stop_on_an_item_that_is_only_waiting_kills_its_queued_request(self):
        item = self.waiting_item(mode="batch")
        self.scheduler.stop(item, "Linear stop")
        self.assertEqual([r["state"] for r in self.ledger.reservations() if r["item_id"] == item], ["cancelled"])
        self.assertEqual(self.ledger.item(item)["state"], "cancelled")
```

`FakeLauncher` gains one line — `self.on_stop = None` in `__init__` and `self.on_stop and self.on_stop(item_id)` as the first statement of `stop` — which is the whole mechanism the ordering assertion needs.

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
        killed = self.launcher.stop(item_id)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -W error -m unittest discover -s tests -v -k stop -k settles`
Expected: PASS, three new tests plus the existing Stop tests.

- [ ] **Step 5: Run the whole suite and commit**

Run: `python3 -W error -m unittest discover -s tests`
Expected: OK, 206 tests (+3).

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
- Consumes: `build(config)`, which now returns a `pool`; `tests/fake_cli.py`'s `cli` mode and its `FAKE_CLI_STEPS`; the `RUNTIMES["fake"]` runtime.
- Produces: no production code. Two test classes over one shared fixture, and one ref-aware Linear stub.

**Three things the fixture has to get right**, each of which the obvious version gets wrong:

- **The shared fixture is a mixin, not a base `TestCase`.** Subclassing `EndToEndTests` would re-run its two heavyweight, timing-sensitive tests a second time under a setUp that has built a real slot worktree and a pool — duplicated work for no coverage, and two tests the totals would not account for. The setup moves into a plain `Fixture` class that does not derive from `TestCase`, and `EndToEndTests` and `TwoPhaseTests` both use it.
- **The stub has to answer per issue.** `agent.config.StubLinear.fetch_issue` ignores its argument and always returns `issue.json`, so two `enqueue` calls would observe the same issue row and the second `create_work_item` would raise `an active work item already exists for this issue`. The stub gains one line: it reads `issue-<ref>.json` when that file exists and falls back to `issue.json` otherwise, and the fixture writes one file per issue. The test then asserts the two items really do carry different `issue_id`s.
- **The proof is durable evidence, not a collaborator's call log.** A batch switch calls the MCP zero times by design (Task 4 asserts exactly that), and `park` never calls it either, so a recording double can only ever see the interactive commit — an assertion like `mcp.switched == [commit_a, commit_b, main]` is unsatisfiable and an executor would "fix" the proof rather than the system. Criterion 3 is asserted against the reservations table, `slots.parked_commit`, the audit trail and `Worktrees.head` sampled at each stage.

- [ ] **Step 1: Write the failing tests**

In `tests/test_end_to_end.py`, extract the existing `setUp` body into `class Fixture:` (a plain object with a `build_environment(self, root)` method), leave `EndToEndTests` calling it, and add a second class. The fixture gains a real slot: a bare clone, an editors root, one configured slot entry, and a `SlotPool` whose `mcp` is `RecordingMcp`. Each item gets its own `FAKE_CLI_STEPS`, selected through one script that reads `FARMBOT_ITEM_ID`.

```python
class TwoPhaseTests(unittest.TestCase, Fixture):
    def setUp(self):
        self.build_environment()   # the mixin's own method; it uses self.addCleanup, so it needs a TestCase
        self.pool = self.c.pool
        self.pool.mcp = RecordingMcp()
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

No production change is expected here. If a test fails for a reason other than its own fixture, fix the production code and say in the commit body which of Tasks 1 to 9 the gap belonged to — this task exists to find exactly those gaps.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -W error -m unittest discover -s tests -v -k TwoPhase`
Expected: PASS, two tests. The first proves the serialization, the second proves "switched to each pinned commit and parked back on main afterwards". Confirm they are two and not four: if `TwoPhaseTests` still inherits `EndToEndTests`'s test methods, the extraction into `Fixture` did not happen.

**Both orderings must pass before criterion 3 is marked done.** These two tests run batch first; `PoolTests.test_two_requests_serialize_in_the_other_order_interactive_first_then_batch` (Task 6) covers the reverse, which is the ordering that exercises the graceful close and is the steady state once the Editor stays open. Task 11 Step 4 repeats the live pair in that order.

- [ ] **Step 5: Run the whole suite and commit**

Run: `python3 -W error -m unittest discover -s tests`
Expected: OK, 208 tests (+2).

```bash
git add tests/test_end_to_end.py
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

**Why:** the offline test proves the ordering; only a live run proves that `Library/` survives a switch, that the batch Editor writes its results inside the sandbox's writable roots, and that the identity probe agrees with a real `mcpforunity://instances` read. Spec §18 asks for exactly this as the phase's live checklist, recorded as a dated report.

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

then run `python3 -m agent.service slots` again. Expected: one slot, state `idle_closed`, `parked_commit` equal to `git -C .local/repos/Farm-Client.git rev-parse origin/main`, and `.local/editors/slot-1` populated. Record `du -sh .local/editors/slot-1` and the wall clock. If `git lfs fetch` fails, record which of the two failures the error named — **credentials** for `git.kuaiwa.com` or **reachability** of the LAN — and seed the objects by copy into `.local/repos/Farm-Client.git/lfs/objects` as Task 0's spike decided. Record which path was used.

- [ ] **Step 2: Build `Library/` once, deliberately**

Run the import command from Task 0 Step 3 against `.local/editors/slot-1`, in the foreground, and record the wall clock and `du -sh .local/editors/slot-1/Library`. This is the one-time cost the operator was warned about; it is done by hand here rather than inside a scheduler tick.

- [ ] **Step 3: Drive one batch item with no webhook, on a closed slot**

The issue must be delegated to FarmBot in Linear first: `enqueue` refuses a write-capable skill otherwise, which is spec §4's rule of authority and not something the missing webhook excuses.

```bash
python3 -m agent.service enqueue --issue <a real, delegated issue uuid> --skill fix
python3 -m agent.service serve
```

Expected, in order: the item is launched, the worker calls `await-resource --mode batch` and exits, `python3 -m agent.service slots` shows the reservation `active` and the slot `batch_busy`, the fresh worker runs `resource.batch_command` inside the slot and writes `unity-tests.xml` under `.local/runs/<item>/`, and after `release-resource` the slot returns to `idle_closed` parked on main. Run the Step 6 checker and paste its output; record the wall clock of the switch and the first ten lines of the results file.

- [ ] **Step 4: Drive one interactive item on the same slot**

Nothing is opened by hand. The prerequisite is the MCP for Unity server process (operator item 5), not an Editor: confirm `lsof -nP -iTCP:8080 -sTCP:LISTEN` before starting, and confirm `python3 -m agent.service slots` still shows `idle_closed`. Then enqueue a second item whose worker asks for `--mode interactive`. Expected: the pool **starts the Editor itself**, discovers the instance and writes it to `slots.instance`, switches the slot, waits for quiet, finds zero Console errors, records an `identity_observations` row whose `aggregate` is `match`, the worker's launch message contains a `unity` MCP server and that instance id, and `release-resource --outcome quiescent` parks the slot — `idle_open`, with the Editor still running. Record the probe result verbatim, with the live instance id next to the value `sha1(<folder>/Assets)[:16]` computes and next to the `dataPath` the probe returned.

- [ ] **Step 5: Repeat the pair in the other order**

This is the ordering the batch-first rehearsal never exercises, and it is the steady state: the slot is now `idle_open` with a live Editor. Enqueue an interactive item and then a batch item. Expected: the interactive item is granted with no Editor start (the Editor is already open), and when the batch item is granted the pool closes the Editor gracefully first — `Temp/UnityLockfile` disappears before `Unity -batchmode` starts, and the batch run does not fail on a project lock. **Criterion 3 is not marked done until both orderings pass here and in the suite.**

- [ ] **Step 6: Stop a worker while it holds the slot**

With an interactive worker running, run `python3 -m agent.__main__ --db .local/agent/ledger.sqlite3 cancel --item <id> --reason "rehearsal stop"`, and time it. Expected: under five seconds, the reservation `cancel_requested`, the item `cancelled`, and on the next pool tick either a release with the slot parked or a `held` slot naming the reason. If it is held, run `recover-slot` and record that the slot returns to the pool.

- [ ] **Step 7: Check the transitions with a script, not by eye**

Every transition this task asks a human to watch is already in the ledger, so assert it instead of narrating it. Add `scripts/check-rehearsal.py` (about fifteen lines, standard library, read-only): it opens `.local/agent/ledger.sqlite3`, and for each item prints PASS or FAIL for the expected `audit` sequence (`reservation queued` → `reservation acquired` → `slot <busy state>` → `reservation released` → `slot idle_*`), the `reservations.acquired_at` ordering, `slots.parked_commit` against `git rev-parse origin/main`, the `identity_observations` aggregate, and the elapsed seconds between the `cancel requested` audit row and the item's `cancelled` row. Paste its output into the report verbatim.

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

**Spec coverage.** §7 in full for `unity_slot`: the reservation queue with FIFO, the six states and the hashed owner token (Task 2), the slot folder and its binaries (Task 3), the slot switch, the Editor's start and graceful close, the compile wait and the park (Task 4), the identity probe (Task 5), scheduling preference by mode, two-phase acquisition and the release rule (Tasks 6 and 8), tool injection as enforcement (Task 7), and Stop's ordering (Task 9). §6's pinned target, which the repository never wrote, is Task 1; §6's "when the request names a branch or PR" and the human correction of a pin are deferred in Scope with the phase that owns them. §8's slot worktrees, the batch command without `-quit`, `-nographics` or `-accept-apiupdate`, and the sandbox's writable roots — inside the slot and on the host — are Tasks 3, 4 and 7. §10's `slots`, `reservations` and `identity_observations` are Task 2; `hosts`, `build_artifacts` and `baselines` are deferred in Scope with their reasons. §17 Phase 1 criterion 3 is discharged by Task 10's two test methods and Task 6's reverse-ordering case offline, and Task 11's report live, in both orderings; criterion 2's five-second Stop is re-proved under contention in Task 9. §18's "slot pool acquisition by mode and preference, slot state machine and switch, two-phase resource acquisition" are the test names in `tests/test_slots.py`.

**Deferred by design, not gaps.** Play Mode and the logged-in session identity probe, the per-slot test account's use, `fgui_editor` and `android_device` kinds, baseline comparison of batch results, naming a ref other than the default head and retargeting a pin after the fact, player builds in a slot, and the second slot with its stdio MCP pinning. Each is named in Scope with the phase that owns it. The `qa` skill is untouched by this plan.

**Placeholders.** None. Every step names its files, shows its code and gives the command to run with the output to expect. The two places this plan deliberately does not invent an answer are Task 0's seven spike questions and the four operator-provided resources, and both are written as questions with success conditions rather than as defaults.

**Type consistency.** Seven signature changes, each checked against its existing callers. `Worktrees.ensure_clone(repo, seed_from=None)` is untouched. `_git(*args, cwd, env=GIT_ENV, timeout=600)` keeps its old behaviour for every existing call site, which all pass only `cwd`; `timeout` arrives in Task 1 and `env` in Task 3. `Ledger.await_resource(item_id, token, resource, mode)` keeps its signature and gains two refusals, so `tests/test_ledger.py:214` needs a pinned item — Task 2's fixture provides one. `Ledger.fail_queued` widens its accepted states, and its two callers in `agent/scheduler.py` pass queued items. `dispatch_message(..., resource=None)` defaults to today's payload byte for byte, and its one production caller is `Scheduler.launch`. `Scheduler.__init__` gains `slot_entries=None`, whose only production construction is `build`'s. `Receiver.__init__` gains two keyword arguments with defaults, so the only construction outside tests, in `service.build`, is updated in the same task. `SlotPool(ledger, ...)` accepts a `Ledger` or a factory, and the only production construction passes a factory. `Components` gains a trailing field, and every construction of it is `build`'s single call. `SlotPool.park` gains a required `mode`, and its only two callers are `park_idle` and the tests.

**Concurrency.** Two writers now use the ledger: the scheduler thread and the pool thread, on **two connections** — `service.build` hands the pool a factory, exactly as it already hands one to the receiver, because two threads on one `sqlite3.Connection` do not get two transactions and would commit and roll back each other's work. Given two connections, every mutation goes through `_transaction()`, which is `BEGIN IMMEDIATE`, and the two partial unique indexes are the fences that survive a crash between them; `Ledger`'s `timeout=10` is what absorbs a contended `BEGIN`. A `ReservationTests` case runs two connections into `acquire` from two threads behind a barrier and asserts exactly one grant, so the "across processes rather than by convention" claim is tested rather than argued. The pool never holds a transaction across a git command, a subprocess or an MCP call — the discipline `farmqa_identity.py:155-167` established and the reason a Stop can land at any moment. `Scheduler.tick()` gains no slow work at all, so its lock is held for the same time as today.

**Test counts.** Counts are the drift alarm, so each is a delta per file with a running total beside it, and each is confirmed by running the selection rather than by arithmetic. The suite stands at 139 before Task 0 and 208 after Task 11: +8 → 147 (Task 1), +12 → 159, +5 → 164, +15 → 179, +7 → 186, +10 new and one Phase 1a test deleted → 195, +4 → 199, +5 new and one replaced → 203, +3 → 206, +2 → 208. Two of those numbers depend on a refactor rather than on new tests: Task 10's total assumes `TwoPhaseTests` does **not** inherit `EndToEndTests`'s two test methods, and Task 8's assumes `test_await_resource_is_refused_without_slots_and_errors_are_clean` was replaced rather than kept.

## Operational checklist, owed but not code

These need the Windows host, the operator, or a decision a test cannot make, and are tracked here so they are not lost:

1. **The dedicated 公共测试服 account for slot 1.** Write it into the slot's config entry as `account`. Nothing in Phase 1 logs in; Phase 2's `qa` skill cannot start without it.
2. **Whether the service may run Unity, and how fast.** If Task 0 Step 6 found that a launchd-started batch Editor cannot acquire the Personal licence, decide between running `serve` from a login session and buying a seat. Contention is not only across hosts: the same single seat backs the operator's own Editor on `Farm-Client` on this Mac, which is why `SlotPool.switch` refuses to start a second Editor and holds the slot with a named reason instead. Step 6 also measures the import twice, foreground and through the LaunchAgent, because the installed plist sets `ProcessType=Background` and throttles CPU and disk for everything the service spawns; if the throttled figure is unacceptable, changing the plist to `Standard` is a deployment change this plan then owes, since Plan 1c installed it.
3. **App Nap.** If Task 0 Step 5 found that an unfocused Editor stalls, decide between `NSAppSleepDisabled` for the Unity bundle, a separate macOS user for the slot, and restricting the Mac slot to batch mode. The shipped mitigation steals screen focus and is not acceptable on a daily-driver Mac. This has no Windows analogue.
4. **The Windows slot's configuration**, for the Windows plan to inherit: `unity` (`Unity.exe` under one of the three probe roots in `agent/unity.py`), `build_target` (`Android` on the QA host) with its `-buildTarget` spelling, `mcp_address` (the plugin's `MCPForUnity.HttpUrl` EditorPrefs value, which lives in the registry there and in a plist here), the slot folder, and the host's own test account. No code change should be needed; if one is, the Windows plan records which module leaked a host assumption.
5. **Re-run Task 0's spike on Windows.** Import cost, LFS reachability from that host, `-runTests` exit codes under that Unity, and whether a scheduled task in Session 0 can render for PlayMode fixtures — the Windows counterpart of the launchd question.
6. **The second slot.** Spec §7 adds a slot when waiting routinely exceeds the length of a run. The `awaiting_resource` wait times are now in the ledger, so that decision is a query. It also needs the Editor RSS figure from Task 0 Step 5 against this host's 24 GB, and it is the point at which the stdio MCP transport and `UNITY_MCP_DEFAULT_INSTANCE` pinning become worth the spike §7 asks for.
7. **The webhook.** Until the tunnel delivers, every live run is driven by `python3 -m agent.service enqueue`, on issues that were delegated to FarmBot in Linear by hand, and those items report through issue comments because Linear has no agent session for them. Criterion 5's real delivery still needs the webhook; it is tracked in the 1c checklist and is not blocked by this plan.
8. **The MCP for Unity server process.** The endpoint on 8080 is a `uvx`-launched process the Editor manages, not the Editor itself, and nothing is listening today. Decide how it is started on this host — by the Editor on open, or as its own launchd agent — and write the answer into the spike record and the operating contract, because `SlotPool.open_editor`'s timeout is meaningless if nothing will ever answer.
9. **The secret in `Farm-Client/.codex/config.toml`.** Tracked in git with a plaintext bearer token in its history. Owed to whoever owns Farm-Client; FarmBot's only guarantee is that the file never becomes a source of tool configuration.

## Execution

Plan complete and saved to `docs/superpowers/plans/2026-09-19-phase1b-unity-slots.md`. Two execution options:

1. **Subagent-driven (recommended):** a fresh subagent per task, review between tasks, fast iteration. Requires the superpowers plugin enabled in this directory so `superpowers:subagent-driven-development` loads.
2. **Inline execution:** run the tasks in this session with `superpowers:executing-plans`, batch execution with checkpoints.

Task 0 comes first and its record is read by Tasks 3, 4 and 7; if Step 5 finds that App Nap stalls an unfocused Editor, stop and revise before Task 4. The Windows deployment plan is the only Phase 1 plan that follows this one.
