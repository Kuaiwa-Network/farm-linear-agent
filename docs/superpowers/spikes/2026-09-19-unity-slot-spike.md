# Unity slot spike — Mac, 2026-09-19

Mac 2026-09-19; re-run on the Windows host in the Windows plan.

Task 0 of `docs/superpowers/plans/2026-09-19-phase1b-unity-slots.md`. The plan's own
estimates were written from the spec and from reading configuration files. Most of them
were wrong, and in the same direction: this host is far faster and far less
operator-dependent than assumed. Every number below is measured on this machine.

**Host.** Apple M4, 10 cores, 24 GB RAM, 393 GiB free. Unity 2022.3.62f3 at
`/Applications/Unity/Hub/Editor/2022.3.62f3/Unity.app/Contents/MacOS/Unity`.
Commit under test: `7d886c72ea3f49fce98b20d7ef19636bd7eabf50` (`origin/main`).

## Preconditions

| Check | Result |
|---|---|
| `git lfs version` | git-lfs/3.7.1 (darwin arm64) |
| `git.kuaiwa.com` LFS endpoint | reachable; resolves to `192.168.1.201` (LAN) |
| `credential.helper` for that host | `osxkeychain`, and a credential **is** stored |
| `uvx` | `/Users/elendil/.local/bin/uvx` |
| port 8080 before start | nothing listening |
| farmbot launchd jobs | not loaded |

## Step 1 — worktree checkout, smudge off

```
git -C <bare> fetch --prune origin                     2.5 s
GIT_LFS_SKIP_SMUDGE=1 git worktree add --detach ...    1.6 s
```

Tree 110 MB. `git lfs ls-files` counts **3,778** files — the plan expected 3,619, which
was measured at an older commit. `Assets/DOTween/DOTween.dll` begins `vers`, i.e. a
pointer, as required.

**Answer:** creating a slot's source tree is seconds, not minutes. The plan's framing of
checkout as part of a long setup was wrong.

## Step 2 — materializing the LFS binaries

```
git lfs fetch origin HEAD      36.2 s   918 MB over the LAN
git lfs checkout                5.3 s   147 MB/s
```

`DOTween.dll` then begins `MZ`. Tree grows 110 MB → 979 MB; the bare clone's LFS store
grows 176 KB → 880 MB, and that store is shared by every future slot.

No credential prompt and no failure: the fetch ran under `GIT_TERMINAL_PROMPT=0` with
stdin closed and exited 0. The `osxkeychain` helper answered non-interactively. An
earlier draft of the plan called this a blocker requiring operator setup; it is not one
on this host.

**Deviation from the plan, stated:** the rsync-seed alternative was *not* measured. It
copies ~3.6 GB of LFS *history* to obtain the same 918 MB of HEAD objects, and the
network fetch already populates the shared bare-clone store. Measuring a strictly worse
path was not worth the disk. If a future host is off the LAN, measure it then.

**Decision:** Task 3 fetches over the network by default. Failure discrimination still
matters: a credential failure prints a 401 or `Authorization` error, a reachability
failure prints connection refused or a timeout.

## Step 3 — first `Library/` import at a fresh path

```
Unity -batchmode -quit -silent-crashes -projectPath <slot> -logFile ...
real 169.27   user 220.67   sys 52.89
```

`-accept-apiupdate` was deliberately omitted — spec §8 does not list it, and the plan
flags a tree dirtied by it as a finding.

| | |
|---|---|
| Wall clock | **170 s** (spec said "tens of minutes"; plan budgeted 15 min) |
| `Library/` total | **5.1 GB** (plan estimated ~3.5 GB) |
| `Library/Artifacts` | 2.9 GB |
| `Library/PackageCache` | 1.8 GB |
| `Library/Bee` | 190 MB |
| `Library/BurstCache` | 107 MB |
| `Library/ArtifactDB` | 80 MB |
| `Library/ScriptAssemblies` | 50 MB |
| Compile errors | **0** |
| `HotUpdate.dll` | present, 7,211,008 bytes |

**Licence resolved.** `[Licensing::Client] Successfully resolved entitlement details`
appears six times and the run ends `Exiting batchmode successfully now!`. An early
`[Licensing::Module] Error: Access token is unavailable; failed to update` is benign
noise, not a failure. A batch Editor started from a shell acquires the licence.

**HybridCLR needed no provisioning.** No installer warning was logged and
`HotUpdate.dll` was produced without `HybridCLRData` being generated first, despite that
directory being gitignored and 1.9 GB in the human's checkout.

**Finding — the tree comes back dirty, and always will.** `.vscode/settings.json` is
rewritten:

```
-    "dotnet.defaultSolution": "Farm-Client.slnx",
+    "dotnet.defaultSolution": "slot-1.slnx",
```

Unity names the generated solution after the *project folder*, so every slot dirties this
file with its own folder name on every Editor run. The slot switch's "confirm tracked
files are clean" precondition would therefore fail every time. This is not
`-accept-apiupdate`; that flag was not used.

**Decision:** when Task 3 creates a slot it marks that path
`git update-index --skip-worktree .vscode/settings.json`. It is a per-folder artifact
that must never travel in a commit, and skip-worktree keeps the clean check meaningful
for everything else.

## Step 4 — the `-runTests` contract

Two runs, no `-quit` (the runner exits on its own):

| Run | `-assemblyNames` | Exit | Results XML | Contents |
|---|---|---|---|---|
| A | `HotUpdate.Tests` | **2** | 2,536,173 bytes | `result="Failed(Child)"` total 4388, passed 4362, **failed 26** |
| B | `NoSuchAssembly` | **0** | 652 bytes | `result="Passed"` total **0** |

Run A took 17 s, run B 10 s.

**Answer, and it is the most important one in this spike: the exit code must not be
trusted.** Exit 0 means "nothing ran" and exit 2 means "tests failed". A typo in an
assembly name yields a green exit code and a results file that says `Passed` over zero
tests. Task 7 must parse the XML and assert `total > 0` before treating a run as
evidence; the exit code is advisory only.

**`origin/main` is known-red: 26 failures of 4388**, across 11 classes:

```
Farm.Features.Animal.Tests.AnimalBarnConfigTests              (2)
Farm.Features.Compendium.Tests.AnimalUpgradeCostConfigTests   (3)
Farm.Features.DressUp.Tests.DressUpConfigContractTests        (1)
Farm.Features.Leaderboard.Tests.LeaderboardConfigContractTests(1)
Farm.Features.SignActivity.Tests.GrowthTaskGroupConfigTests   (1)
Farm.Features.Topup.Tests.FirstChargeHeroCropConfigTests      (1)
Farm.Tests.ConfigProtobufGeneratedGoldenTests                 (1)
Farm.Tests.FriendShelfUnlockConfigTests                       (6)
Farm.Tests.NetworkProtobufGeneratedSmokeTests                 (1)
Farm.Tests.StatInfoConfigTests                                (5)
Farm.Tests.VisitSharedRequestTests                            (4)
```

Almost all are configuration-table contract tests, which suggests the exported tables on
main are out of step with the code rather than the code being broken. The plan deferred
baseline comparison to Phase 3, but a `fix` worker in Phase 1 will meet these 26 on every
run and must not attribute them to its own change. This list is the baseline until it is
re-measured.

## Step 5 — the interactive path

| | |
|---|---|
| Editor start → `127.0.0.1:8080` answering | **16 s** |
| Editor resident memory | **2.32 GB** |
| `Temp/UnityLockfile` | created on start |
| Instance id | `slot-1@54462c1bfe7b5261` |

**The MCP server starts itself.** The plan listed "the operator confirms the server
starts" and "`execute_code` is enabled through the MCP window" as operator-provided
prerequisites. Neither is needed. Opening the Editor spawned:

```
uvx --offline --from mcpforunityserver==10.2.0 mcp-for-unity \
  --transport http --http-url http://127.0.0.1:8080 --project-scoped-tools \
  --pidfile <slot>/Library/MCPForUnity/RunState/mcp_http_8080.pid \
  --unity-instance-token 64829bcf7d0647e29bb94cc7006f8572
```

Note `--project-scoped-tools`, a per-slot pidfile under the slot's own `Library/`, and a
per-instance token. The server registered 35 tools from the plugin and exposes 48 over
MCP, `execute_code` among them and already enabled.

**Instance id source, settled.** `sha1("<slot>/Assets")[:16]` — no trailing slash —
reproduces `54462c1bfe7b5261` exactly. `sha1("<slot>")` and `sha1("<slot>/Assets/")` do
not.

**Readiness probe.** `FarmTestAgent/tests/probes/editor-readiness.cs.txt` run through
`execute_code` (which requires `action: "execute"`) returned:

```json
{"isPlaying": false, "isCompiling": false, "isUpdating": false,
 "unityVersion": "2022.3.62f3", "platform": "OSXEditor",
 "buildTarget": "StandaloneOSX",
 "dataPath": "/Users/elendil/farmbot-slot-spike/slot-1/Assets",
 "assemblies": [{"name": "HotUpdate",
                 "moduleMvid": "f35cc9e7-ffd0-41db-b8e5-251eb6691872",
                 "hasGameTestDriver": true}, ...]}
```

`mcpforunity://editor/state` reports `instance_id: slot-1@54462c1bfe7b5261`,
`is_batch_mode: false`. The probe is the plan's success condition and it passes.

**`run_tests` over MCP is asynchronous** — it returns a `job_id` and is polled with
`get_test_job`, which accepts a `wait_timeout`. It also exposes `clear_stuck` for a job
orphaned by a domain reload. This is a better fit for the interactive verify path than
the plan assumed, and it means an interactive slot need not shell out to a second Editor.

**App Nap does not stall an unfocused Editor.** Three `refresh_unity` calls with
`mode=force, scope=scripts, compile=request, wait_for_ready=true`, each preceded by a real
edit to `Assets/Scripts/HotUpdate/AssetPaths/AssetAddress.Animal.cs` so every one forced a
genuine recompile, with the Editor never fronted:

```
compile 1:  9.6 s
compile 2:  7.7 s
compile 3:  5.7 s      min 5.7  max 9.6  spread 1.7x
```

Max is 9.6 s against the plan's 120 s threshold and the spread is inside the 3x rule. No
focus was stolen. **Verdict: OK.** No `NSAppSleepDisabled`, no separate macOS user, and no
batch-only restriction is needed on this Mac.

A caution about the plan's own rule: an earlier attempt used `scope=all` without editing
anything and produced 8.3 s / 0.6 s / 3.3 s — a 13x spread that mechanically trips the
"differ by more than a factor of three" test while breaching nothing. A no-op refresh is
legitimately sub-second, so the spread rule only means anything when every sample does
comparable work. Task 4 should key its stall detection on the absolute threshold.

**Finding — `EditorApplication.Exit(0)` does not close the Editor.** Called through
`execute_code`, it returned `"exiting"` in 0.1 s and the Editor was still running two
minutes later. It was not wedged: a fresh MCP session initialized fine and `execute_code`
answered `"editmode"`. It simply ignored the request.

The fallback works and is fast:

| Action | Result |
|---|---|
| `SIGTERM` to the Editor pid | process gone in **1 s** |
| `Temp/UnityLockfile` afterwards | **still present** at +32 s — Unity never removed it |
| `Library/` after the kill | 5.1 GB, intact |
| Port 8080 afterwards | **still held**, and the stale server still answered HTTP 200 |

**Decisions for Task 4's `close_editor`:**

1. Do not rely on `EditorApplication.Exit(0)`. Send `SIGTERM` to the Editor pid and verify
   the process is gone; one second is the observed cost.
2. Remove `Temp/UnityLockfile` after confirming the pid is dead — on *every* close, not
   only after a crash. Spec §7 anticipated this for an Editor that "dies mid-run"; in fact
   it happens on an ordinary close too, so a `close_editor` that waits on the lockfile
   disappearing would hang forever.
3. Reap the MCP server process as well. It outlives the Editor, keeps port 8080 bound and
   keeps answering, so a later Editor start would meet a stale server on that port. Its pid
   is written to `<slot>/Library/MCPForUnity/RunState/mcp_http_8080.pid`, which is where the
   launcher should read it from.

## Step 6 — launchd

The FarmBot service was **not** loaded for this step. A one-shot LaunchAgent
(`com.kuaiwa.farmbot.spike`, `ProcessType=Background`, `KeepAlive=false`) ran the *same*
EditMode suite Step 4 measured, which answers both questions more cheaply than a second
cold import and leaves the operator's decision to stop the service intact. The agent was
booted out and its plist deleted afterwards.

**The licence works under launchd.** `entitlements=7`, exit code 2 exactly as foreground,
results XML written at 2,536,169 bytes against the foreground run's 2,536,173. No second
seat, no login-session requirement, and no `pgrep` preflight needed on licensing grounds.
Contention with the operator's own Editor was not exercised — none was open — so that half
of the question is still open, but it is now a smaller question.

**The Background scheduling band costs 7.5x.**

| Run | Wall clock |
|---|---|
| Foreground, cold caches (Step 4 run A) | 17 s |
| **launchd, `ProcessType=Background`** | **105 s** |
| Foreground, warm caches (control, run immediately after) | 14 s |

The control run rules out cache state: the launchd run was the *later*, warmer one and was
still 7.5x slower than a warm foreground run of identical work. This is the background
band's lowered CPU priority, timer coalescing and throttled disk I/O, inherited by
everything `serve` spawns.

**Decision:** `~/Library/LaunchAgents/com.kuaiwa.farmbot.serve.plist`, installed by Plan 1c,
must set `ProcessType` to `Standard` before any Unity work runs under the service. A 17 s
verification becoming a 105 s verification is not acceptable, and it compounds: a slot
switch, an import and a test run all pay it. This is a deployment change Plan 1b now owes,
and `agent/deploy.py` is where the plist is generated.

## Decisions

1. **LFS strategy.** Fetch over the network in Task 3 (`git lfs fetch origin HEAD` then
   `git lfs checkout`): 36 s + 5 s for 918 MB, non-interactive, credential supplied by
   `osxkeychain`. Distinguish failure modes by git-lfs's stderr — a 401 or `Authorization`
   error is credentials, connection refused or a timeout is reachability. The rsync-seed
   alternative was not measured and is not needed on this host.
2. **Import cost.** 170 s and 5.1 GB foreground for a cold `Library/` at a fresh path.
   Under launchd's Background band, expect ~7.5x unless `ProcessType` is changed to
   `Standard`. Budget 6 GB per slot; the spec's "tens of minutes" does not hold on this M4.
3. **Batch exit-code contract.** Do not trust the exit code. Exit 0 means *nothing ran* and
   exit 2 means *tests failed*. Task 7 parses the results XML and asserts `total > 0` before
   accepting a run as evidence. `main` is known-red at 26 of 4388; that set is recorded in
   Step 4 and must be treated as a baseline, not as damage caused by a fix.
4. **Instance id source.** `sha1("<slot>/Assets")[:16]`, no trailing slash. Verified against
   the live `slot-1@54462c1bfe7b5261`.
5. **App Nap.** Not a problem: 9.6 / 7.7 / 5.7 s for unfocused forced recompiles, against a
   120 s threshold, with no focus stolen. Key stall detection on the absolute threshold, not
   on the spread — a no-op refresh is legitimately sub-second and inflates the ratio.
6. **Editor RSS.** 2.32 GB with the project open in Edit Mode. On 24 GB this does not cap
   the pool at one slot; the Phase 1 slot count is one because done-criterion 3 needs
   contention to prove the queue, not because of memory.
7. **Editor start timeout.** 16 s from process start to `127.0.0.1:8080` answering. Task 4's
   `open_editor` should allow generous headroom over that — 120 s is ample. The MCP server
   starts itself as a `uvx` child with `--project-scoped-tools` and a per-slot pidfile; no
   operator action and no manual enabling of `execute_code` is required.
8. **Graceful close.** `EditorApplication.Exit(0)` through `execute_code` does **not** work —
   it returns immediately and the Editor keeps running while remaining fully responsive.
   Use `SIGTERM` on the Editor pid (gone in 1 s), then remove `Temp/UnityLockfile` by hand
   because Unity leaves it behind on an ordinary close, then reap the MCP server process
   named in `<slot>/Library/MCPForUnity/RunState/mcp_http_8080.pid`, which otherwise keeps
   port 8080 bound and answering. There is no lockfile timeout to wait on; waiting would
   hang forever.

## Still open

- Contention between a launchd batch Editor and the operator's own Editor on a different
  folder. Not exercised because none was open. Re-test before relying on `SlotPool.switch`
  granting while another Unity process is alive.
- The per-slot 公共测试服 account. Nothing in Phase 1 logs in, so it did not block this
  spike; Phase 2's `qa` skill still needs it.
- Everything in this record is a Mac measurement. Re-run on the Windows host in the Windows
  plan: import cost, LFS reachability from that host, `-runTests` exit codes under that
  Unity, the MCP transport there, and whether a scheduled task can render for PlayMode.
