# FARM-1693 continuation and startup investigation

Measured on 2026-10-23 from retained, read-only production evidence. This report
describes a development change, not a deployment or a new production guarantee.
No service, issue, reservation, worker or production cache was changed for this
investigation. Runtime logs and credentials remain private.

## First execution latency

The inspected attempt is `20261023T111029Z-92c172fea3d0`. Times below are Taipei
time (UTC+08:00); session events are stored in UTC. The event labels matter:
neither submitting a tool call nor receiving its output measures shell startup.

| Observation | Local timestamp / duration | Evidence |
| --- | --- | --- |
| Worker registration recorded | 19:10:29.876957 | Ledger audit timestamp |
| First execution tool submitted | 19:10:43.765 | Session response-item event |
| Tool yielded a running cell | 19:11:15.024 | Same call's output; subsequent waits tracked by cell ID |
| Claim transition to running recorded | 19:16:58.929357 | Ledger audit timestamp, not an OS process-start timestamp |
| Final matching tool output received | 19:16:59.033 | Matching wait output |
| Submission-to-output interval | 375.268 seconds | Difference between session timestamps |
| Reported command execution duration | 0.9391429 seconds | Nested execution tool's `wall_time_seconds` |
| Outside the reported command duration | 374.328857 seconds | Difference; not a measured review or sandbox duration |

The worker had already launched and submitted its claim command, so this interval
cannot be explained as waiting in FarmBot's worker-admission queue. The retained
evidence does not separately timestamp automatic review, tool dispatch, sandbox
initialization, shell entry and result delivery. It therefore does **not** establish
which of those components caused the delay. The fix does not claim to have removed
this delay or to make a 480-second cold build fast.

The new read-only extractor prints allowlisted timestamps and durations, never
prompts, command arguments, command output, claim tokens or session credentials:

```powershell
# Windows: use the configured Python executable, against a retained attempt.
python -m agent.startup_diagnostics --attempt C:\FarmBotState\runs\ITEM\ATTEMPT
```

On macOS use `python3` and the intended host's absolute retained attempt path;
do not copy live state between hosts. This extractor does not construct a Ledger.
It follows yielded cell IDs and ignores unrelated waits. Missing durations remain
unknown instead of becoming guessed shell-start times.

Future attempts add `preparation_started_at` and `process_started_at` to the
launch record. The latter is sampled after process creation, not provided by the
kernel. Successful `claim --save-token` adds `worker_claim_timing` audit fields:
`command_entered_at` marks entry to the Python claim helper after CLI/ledger setup,
and `claim_completed_at` follows private token-file flush. Those fields narrow
FarmBot-owned preparation/claim time; they still do not instrument external review
or sandbox stages.

Codex's documented telemetry distinguishes tool decisions from tool results and
includes decision source and result duration. Correlating those events with the
launch, session and claim timestamps would be the next diagnostic step when such
events are already available. Enabling a new exporter or changing runtime settings
is a separate authorized host operation; tool-result telemetry can include output
snippets. No telemetry or execution-review configuration was changed here.
See [official Codex advanced configuration](https://learn.chatgpt.com/docs/config-file/config-advanced).

## Development changes and their boundaries

- **Continuation scope:** issue context and launch instructions distinguish the
  latest attributed message from historical checkpoints. Before new managed tests
  or delivery, the worker records which message it reviewed and its current scope.
  For conflict-only work it resolves actual conflicts, validates affected areas,
  and attributes failures to the merge before expanding repairs. A status question
  does not automatically replace the preceding explicit engineering request.
  History, approvals and evidence remain available; old TODOs are not new authority.
- **Service release:** new Windows server/combined leases register each command
  before spawning it in a nested, non-breakaway Job Object. Authenticated release
  seals that scope after native emptiness checks and preserves the main claim/PID/
  generation. Pool cleanup repeats the check and existing Docker/Unity ownership
  checks. POSIX and historical leases retain whole-worker handoff. Incomplete or
  uncertain proof retains the existing held/recovery path.
- **Compilation cache:** the same item reuses its Go compiler cache only after all
  prior attempts are proved quiescent. Other items, temporary directories and
  arbitrary build outputs remain separate. Retention keeps its teardown and grace
  checks and preserves reports. Go's content-addressed compilation cache validates
  source/toolchain/options; external C libraries have documented invalidation
  limits. `-count=1` still forces a fresh test result, and `-run` still compiles the
  selected packages. Explicit affected-package selection is necessary; broad
  `./...` plus a narrow test name is not a package-build filter.
  See [the Go command's build and test caching documentation](https://pkg.go.dev/cmd/go#hdr-Build_and_test_caching).
- **Priority pause:** `maintenance-pause --except-issue FARM-1693` requests other
  workers to checkpoint, settle resources and yield while preserving the selected
  worker. Resource-status polling carries maintenance, withdrawal and inbox signals.
  Ordinary resume restores the earlier admission restriction and preserves human
  input waits and pending teardown. Priority mode never reports permission to stop
  the controller. No timer or completion automation is installed.

The process-scope tables are additive. Deploy matching controller, CLI and worker
instructions only after the normal settled pause. Before rollback, settle scoped
commands, services and workers and retain the database/journals. Older code does
not reverse the schema addition and cannot safely supervise active new scopes.
See the [operating contract](../operating-contract.md#server-service-release) and
[worker CLI reference](../../references/single-worker-cli.md).

## Development validation

Original validation base: `0b43ac4926c6d58f0279edaa6f26bee1abfd3d14`. Validation ran on native
Windows with Python 3.13.15 in an isolated worktree and virtual environment.
Pillow 12.3.0 was installed from the repository's hash-pinned `requirements-ui.txt`.
The first attempt with the system interpreter lacked Pillow and was interrupted;
it is not counted as a completed suite.

The full offline discovery was split by sorted test module into four processes,
matching the module-based strategy in [CI](../../.github/workflows/tests.yml).
Each used temporary fixtures and stripped inherited FarmBot/fake-worker/GitHub
credential environment variables. Local commands were:

```powershell
.\.local\continuation-venv\Scripts\python.exe -X utf8 .local/run-offline-shard.py 0 4
.\.local\continuation-venv\Scripts\python.exe -X utf8 .local/run-offline-shard.py 1 4
.\.local\continuation-venv\Scripts\python.exe -X utf8 .local/run-offline-shard.py 2 4
.\.local\continuation-venv\Scripts\python.exe -X utf8 .local/run-offline-shard.py 3 4
```

The local runner and detailed logs/JSON results remain in ignored
`.local/validation` (runner in `.local/`). Normal unsharded discovery is
`python -m unittest discover -s tests -v` with the same prepared dependencies.

| Full-suite shard | Cases | Failures | Errors | Platform/capability skips |
| --- | ---: | ---: | ---: | ---: |
| 0 | 767 | 3 | 0 | 2 |
| 1 | 967 | 1 | 0 | 16 |
| 2 | 621 | 0 | 0 | 47 |
| 3 | 1,021 | 0 | 0 | 4 |
| Total | 3,376 | 4 | 0 | 69 |

The four failures identified old fixtures: two asserted the former cache wording,
and two resumed/delivered without recording the newly reviewed request scope.
The fixtures now follow the current contract, including preserving an unanswered
human decision despite recording scope. After correction these commands passed:

```powershell
.\.local\continuation-venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -p test_single_worker.py -v
.\.local\continuation-venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -p test_single_worker_journey.py -v
```

Those reruns covered 35 and 3 cases respectively, with no failures or skips.
The full suite was not repeated after those fixture corrections. Six additional
regression cases were added during review, bringing discovery at that base to 3,382.
The final grouped regression run of `test_continuation_controls.py`,
`test_server_test_runtime.py`, `test_startup_diagnostics.py`,
`test_worker_cache_reuse.py` and `test_windows_workers.py` passed all **34 cases**,
with no failures, errors or skips. It exercised actual native child processes,
including a nested service Job inside a live outer worker Job.

Focused reruns also passed: maintenance (33), monitor projection (52), combined
Client/server (29), fixed Editor pool with fake Editors (30), and the combined
resource CLI journey (1). These used `python -X utf8 -m unittest discover -s tests
-p MODULE.py -v`; the CLI journey additionally used `-k combined_request`.
`node --check agent/monitor_static/monitor.js`, local Markdown link checks, and
`git diff --check` passed.

The 69 skips cover POSIX sessions/groups/FIFOs/permissions and shell fixtures,
macOS-specific storage/Seatbelt behavior, and platform-specific link checks.
They are not macOS verification. No real Unity Editor, live issue mutation,
production service operation, deployment or cold-build performance benchmark was
performed. Initial adoption of the new cache layout may still compile cold.

## PR preparation

The change was rebased onto `main` at
`3f0e1a68b0a927615045c9bdad41a145e5de7de8`, which already includes the prerequisite
question-thread and Docker-recovery changes and the new queued-priority refresh.
The rebase was conflict-free; `git range-diff` confirmed an unchanged patch before
this validation note was added.

On that base, a grouped native Windows rerun passed **81 cases**, with no failures,
errors or skips: `test_priority_scheduling.py`, `test_continuation_controls.py`,
`test_maintenance.py`, `test_server_test_runtime.py`, `test_startup_diagnostics.py`,
`test_worker_cache_reuse.py` and `test_windows_workers.py`. Each module can be rerun
with the same `python -X utf8 -m unittest discover -s tests -p MODULE.py -v` command.
The grouped runner used the same environment filtering as the full-suite runner;
results remain in ignored `.local/validation/pr-base-regressions.log` and `.json`.
The full suite was not repeated on this newer base.

## Release review corrections

CI run `39963478845` on PR #343's initial head completed all 3,396 cases on
both platforms. The four Windows shards passed with 69 platform/capability skips,
including all ten native Windows worker tests. macOS reported 32 failures,
108 errors and 105 skips. Most failures followed worker startup errors: the new
cache check rejected the system `/var` alias above the host-selected runs root.
The cache now checks links/junctions inside the item and cache directories before
creation or reuse, while accepting host-controlled ancestors. Direct item paths
are enforced, and regression tests cover host aliases and each owned link boundary.

The combined-resource CLI fixture also incorrectly expected immediate POSIX
service settlement while its all-worker quiescence callback still returned false.
It now verifies that settlement remains blocked until that proof is supplied;
the runtime's POSIX whole-worker requirement is unchanged. These findings blocked
deployment of the initial head. Subsequent checks belong to the corrected PR head;
the earlier green Windows run does not certify that newer revision.
After these corrections, a native Windows run of cache reuse, cache retention,
Windows worker containment and the combined-resource CLI journey passed all
41 cases with no failures, errors or skips. Private results are retained as
`.local/validation/review-fix.log` and `.json`.
