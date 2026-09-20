# Handoff — Plan 1b execution, 2026-09-20

Written because the Claude Code session that executed Tasks 1-4 is out of budget. Everything
needed to resume is here or in git; nothing important is left in that conversation.

## Read these, in this order

1. `docs/superpowers/specs/2026-09-17-farm-linear-agent-design.md` — the spec. **It is the
   binding authority.** The plan is only its argument; where they disagree, the spec wins.
   §5 §6 §7 §8 §10 §17 §18 bind this plan. §7 is the heart of it.
2. `docs/superpowers/plans/2026-09-19-phase1b-unity-slots.md` — the plan (~4500 lines).
3. `docs/superpowers/spikes/2026-09-19-unity-slot-spike.md` — **measured facts about this Mac.**
   Read the "Decisions" list and the sandbox addendum before touching Tasks 7, 9 or 11.
4. This file.

## State

**Updated by Codex on 2026-09-20.** PR #5 is merged (`328cc80`). Task 11's live
resource rehearsal now passes. See `reports/2026-09-20-slot-rehearsal/report.md`
and its machine-checked evidence, which supersede the previously halted Step 4.

- Tasks 1-10, 12 and 13 were already complete; none was repeated.
- Task 11: live identity aggregate `match`, both serialization orders, same-Editor
  reuse, 4414-test batch run with the known 26 failures, CLI batch Stop in 0.329 s,
  interactive probe/release in 2.445 s, and `launchctl kickstart -k` batch cleanup in
  0.151 s. Both process checks were repeated after Unity acquired its project lock.
- Stop/restart rehearsal exposed Python gaps. This continuation fixes CLI process
  reconciliation, SIGTERM cleanup, late batch spawn races, group escalation, and
  cancel-then-retry races, with failing-then-passing real-subprocess regressions.
- `scripts/check-rehearsal.py` now checks audit ordering, overlapping ownership,
  identity, batch XML, both arrival orders, and actual HEAD/parked commit against main.
- Phase 1 criterion 5: the operator selected **FARM-1247** (task 410 completes after
  unlocking one table instead of two), delegated it in Linear, updated the tunnel URL,
  then re-delegated. The real webhook created work item
  `9440b065-87da-4494-8dc0-95713c6c0ef1`, session
  `9641da61-238d-4ff2-9470-36e094854a95`. Delivery is still in progress.

### Runtime locations

Code/evidence for this continuation live in the Codex worktree. The original checkout
at `/Users/elendil/WorkSpaces/Farm/farm-linear-agent` still owns `.local/` (real ledger,
clones, runs and slot). Do not create or import a second slot in this worktree.

The resource rehearsal used operator claims and deliberately disabled autonomous
workers. It did not post comments or claim a new Codex delivery. Raw Unity logs and
XML remain under the original checkout's `.local/runs/<item>/`; committed evidence
contains the identity observation, process timing and checker output.

The failed quick tunnel was restarted. The current URL is
`https://release-montgomery-incomplete-suzuki.trycloudflare.com/webhook` and the
operator confirmed it was pasted into Linear; real delivery to the receiver is verified.
A later tunnel restart changes that URL again.

## A trap that will bite you

**`.superpowers/` is git-ignored** (`.gitignore:6`). The execution workspace at
`.superpowers/sdd/2026-09-19-phase1b-unity-slots/` holds the ledger, every task brief, every
implementer report and every review diff — and **none of it is in git**. A `git clean -fdx`
destroys all of it. Everything load-bearing from it has been copied into this file; the briefs
and reports are still on disk if you want the detail.

**A real 6.1 GB Unity slot exists at `.local/editors/slot-1`**, detached at `7d886c72e`, with its
`Library/` already imported (170 s of work). `.local/` is also git-ignored. Nothing may touch it —
not code under test, not tests. Tasks build throwaway repositories in temp directories. Task 3
and Task 4 were both verified to leave it untouched.

## The execution loop

Each task: extract the task's text into a brief → dispatch a fresh implementer with the brief
path only (never the whole plan, never conversation history) → it writes tests FIRST, confirms
they fail for the right reason, implements, runs the whole suite, self-reviews, commits, and
writes a report file → generate a diff of the task's range → dispatch a reviewer with brief +
report + diff → it returns TWO verdicts, spec compliance and task quality → any Critical or
Important finding opens a fix round (resume the same implementer; it keeps its context) → the
fix round ends with a SCOPED re-review of the fix diff only → record completion in the ledger.

Rules that earned their place here:
- **Never fix findings yourself.** Send them back to the implementer; controller fixes skip review.
- **Minor findings never enter the fix loop.** They go to the deferred list below.
- **A finding that contradicts the plan is a ruling, not a stall.** Decide it with the spec as
  the authority, write down what it costs if wrong, and keep going.
- **Mutation checks must run `python3 -B`** (or `PYTHONDONTWRITEBYTECODE=1`). A same-size
  mutation reverted within one second lets CPython reuse a stale `.pyc` and gives a false result
  in EITHER direction. This actually happened during Task 2 and produced a false failure.
- Every task so far has contained at least one real defect. Budget for a fix round each time.

## Global constraints

- Python 3.11+, **standard library only** in `agent/`.
- Suite green AND warning-free under `python3 -W error`; see the latest report for the current count.
- `unittest`'s `-k` is a plain substring with **no boolean operators**. `-k "a or b"` matches
  nothing, prints `NO TESTS RAN` and exits 5 — which silently satisfies an "expected: FAIL" gate.
  Repeated `-k` flags are ORed. Always run a selection and count what it really matches.
- Tests close every server they start (`addCleanup(server.server_close)` beside `shutdown`), or
  `ResourceWarning` fails the suite under `-W error`.
- No test may start Unity, touch `/Applications`, reach the network, or open the real ledger.
  Unity, git-lfs and the Unity MCP are **injected collaborators**. Tests MAY shell out to real
  `git` and use real temporary SQLite and real subprocesses.
- **One `sqlite3.Connection` per thread.** `Ledger._transaction()` is a bare `BEGIN IMMEDIATE`
  on `isolation_level=None`; two threads on one connection do not get two transactions. Invariants
  are enforced by SQLite partial unique indexes, not by convention.
- Slot git runs **with** LFS smudge (`SLOT_ENV` sets `GIT_LFS_SKIP_SMUDGE=0`); task worktrees keep
  it at `1`. Mixing them gives Unity pointer files and a failure that reads as project corruption.
- A slot is always detached at a full 40-hex commit.
- **No slot is released on a timer.** A release follows a passing quiescence probe; a failing
  probe HOLDS the slot for the operator's `recover-slot`.
- **A worker never starts a Unity process, in either mode** (see the sandbox finding below).
- Commit messages end with a `Co-Authored-By:` trailer naming the model that wrote the commit.

## Measured facts that the design depends on

From the Task 0 spike on this Mac (2026-09-19). These are why parts of the plan look odd; do not
"simplify" them back.

- **A Unity batch run inside the Codex `workspace-write` seatbelt HANGS** — killed at 25 minutes,
  0.0% CPU, log frozen at 2,289 bytes, no results file, and **zero file-permission denials**. The
  same command unsandboxed exits 2 in 19 s. The cause is a denied Mach lookup for
  `com.apple.hiservices-xpcservice`, which `sandbox_workspace_write` cannot grant, so no
  `writable_roots` entry fixes it. Hence: the **launcher** runs the Editor, outside the seatbelt,
  and hands the worker the results file. `codex exec --approve-for-me` uses that sandbox, so this
  applies to every worker FarmBot spawns.
- **`-runTests` exit codes are inverted from intuition.** Exit 0 means *nothing ran*; exit 2 means
  *tests failed*. A typo in `-assemblyNames` yields exit 0 and a results file saying `Passed` over
  zero tests. Parse the XML and assert `total > 0`; the exit code is advisory only.
- **`origin/main` is known-red: 26 failures of 4388**, almost all config-table contract tests. A
  fix worker meets them on every run and must not blame its own change.
- **`EditorApplication.Exit(0)` does not close the Editor.** It returns in 0.1 s and the Editor
  keeps running, fully responsive. `SIGTERM` works — gone in 1 s.
- **Unity leaves `Temp/UnityLockfile` behind on an ordinary close** (still present at +32 s). So
  it is NOT a liveness signal, and anything that waits for it to vanish hangs forever. The
  launcher removes it after confirming the pid is gone.
- **The MCP server outlives the Editor**, keeps port 8080 bound and still answers HTTP 200. Its
  pid is at `<slot>/Library/MCPForUnity/RunState/mcp_http_8080.pid`. Reap it.
- Editor start to a usable MCP endpoint: **16 s**. Open Editor: **2.32 GB** resident.
- Instance id is `sha1("<slot>/Assets")[:16]`, no trailing slash — verified by exact match.
- Cold `Library/` import: **170 s**, 5.1 GB. Slot source tree: ~45 s. Total ~3.5 min, ~6 GB.
- `ProcessType=Background` in the serve plist costs **7.5x** (14 s warm foreground vs 105 s under
  launchd). `agent/deploy.py:29` still emits `Background`; Task 7 is where it becomes `Standard`.
- Unity's generated solution is named after the project folder, so **every Editor run rewrites
  `.vscode/settings.json`** to `slot-<n>.slnx`. Slot creation marks it `skip-worktree`.
- LFS over `git.kuaiwa.com` works non-interactively via `osxkeychain` (36 s for 918 MB). It is on
  the LAN at `192.168.1.201`, so an off-LAN or VPN-routed session gets pointer files.

## Rulings I made on your behalf

Seven decisions I took rather than stopping to ask. Each names what it costs if it was wrong, so
you can undo any of them cheaply.

**1. Branch, not a git worktree.** `.local/` is git-ignored and holds FarmBot's bare clones
(876 MB of LFS objects) and the already-built 6.1 GB slot. A fresh worktree would have neither, so
Tasks 3+ would re-import for nothing. *Cost:* commits land on a branch in the shared checkout
rather than an isolated directory.

**2. Opus for every implementer and reviewer**, against the skill's advice to economise, because
the human rejected a cheaper model mix on an earlier plan. *Cost:* higher spend per task.

**3. Task 1 — ruled against the brief on the pin echo.** The brief mandated echoing the pin only
on the `work` and `chat` branches. But the pin is stored before routing and the echo was guarded
on `target is None`, so a session whose first event is `elicit`/`steer` wrote the pin silently and
suppressed the echo forever after — including on the event that creates the work. The pin would
never be announced. Spec §6 requires it be announced "so a human can correct it before work
starts", and the spec binds over the plan. *Cost:* one extra line of zh-CN for session kinds that
arguably did not need it.

**4. Task 1 — put a Minor finding into the fix round** instead of deferring it, against the rule
that minors never enter the loop. Two assertions, the round was open anyway, and the test would
otherwise have passed with the whole feature deleted. *Cost:* two lines of scope.

**5. Task 2 — carried the pin-refusal defect into the dispatch as a ruling.** The brief has
`await_resource` refuse an item with no pinned commit but never says to update the three existing
callers. I ruled the refusal load-bearing (a slot cannot switch to a commit that does not exist)
and required the callers be updated. *Cost:* none observed; both fixtures took a pin cleanly.

**6. Task 3 — accepted an unlabelled re-raise.** `add_slot` re-raises unlabelled when
`_transfer_kind` returns `None`, rather than defaulting to "(reachability)". `git lfs fetch` only
ever fails as a transfer, but `git worktree add` also fails locally (bad ref, path already
registered), and labelling those reachability would send an operator to the network for a local
fault. A reviewer built a table from real git messages confirming 401 and unresolvable-host still
get labelled. *Cost:* a rare transfer failure could reach the operator unlabelled.

**7. Task 4 — two rulings on the slot switch.** (a) `park` must not hand `clear_stale_lock` a
`lambda: False`; spec §7 says the lock is removed "only after confirming the process is gone", and
asserting rather than asking would delete a live lock and risk two Editors on one folder. (b) The
broad `except Exception` clauses may keep HOLDING the slot — releasing one in an unknown state is
worse — but must not label a FarmBot bug as a Unity `probe` failure, because `recover-slot` is how
an operator learns which side misbehaved. *Cost:* one extra process check per park; one extra
error class through the probe path.

## Deferred minor findings

None blocks execution. The final whole-branch review triages which must be fixed before merge.

- **Task 1** — `requested_ref` is the literal "default", not the resolved branch name.
- **Task 1** — `resolve_commit` still leaks `TimeoutExpired`, left for Task 4.
- **Task 1** — the pin-failure sentence promises Task 2 behaviour that has not landed.
- **Task 1** — `Ledger.create_work_item` stores `json.dumps(target)` unvalidated, so the Interfaces claim that `checked_target` is "the only way a target enters the ledger" is not literally true. No hole today — the receiver passes an already-validated session view.
- **Task 1** — an ack followed by a later raise inside `_decide_and_act` can still produce a second `_send` on the same `ack_id` from `process_one`'s handler. Pre-existing, untouched by this task.
- **Task 1** — a cached `remote_head` failure surfaces as `WorktreeError` where the first call raised `TimeoutExpired`. No caller distinguishes them today; flagged for Task 4.
- **Task 2** — a broken invariant surfaces as raw `sqlite3.IntegrityError` rather than `LedgerError` if Task 3's `park_idle` frees a slot still holding an active reservation. Flag for Task 3.
- **Task 2** — the 6-line cancel CASE UPDATE is verbatim in both `cancel_reservations` and `cancel`; brief-mandated to avoid nesting `_transaction()`, but a helper would remove the copy.
- **Task 2** — `assertTrue(token)` in the settlement-listing test asserts nothing about the behaviour under test.
- **Task 2** — `reservations.attempts` is schema-only — written by nothing, exposed by `_reservation_view`. Brief-specified; dead surface until a later task defines it.
- **Task 2** — the mode preference is a sort, not a filter, so an interactive request still takes a closed slot when no open one is free. Correct per §7 as worded, but uncovered.
- **Task 3** — `checkout_commit` dead and untested in this commit; `Paths.editors` consumed by nothing; `slot_entry`'s two SlotError branches untested; the "each of the four parts" test exercises only part three; an `idle_open` record keeps a possibly stale `parked_commit`; spec amendment lines exceed that file's wrap width.
- **Task 3** — a MISSING git-lfs binary is a local fault but its message matches TRANSFER_FAILURE and is labelled "(reachability)" — the misdirection the deviation argues against.
- **Task 3** — the same 401 still surfaces unlabelled on `checkout_commit` (which Task 4's switch uses, also smudge-on) and on `ensure_clone`'s `git clone`, which on a fresh host fails before `add_slot`'s try. Carry into Task 4.

## What is left

1. Finish Phase 1 criterion 5 on FARM-1247: a real code change, draft PR and delivery
   comment. Inspect the work item above before creating anything; the webhook worked.
2. Windows deployment remains last by the user's explicit ordering; no host assigned.
3. Config export (`-executeMethod`) remains Phase 3+, not part of this continuation.
4. Historical deferred minors below remain recorded; this continuation addressed
   important lifecycle defects found by live rehearsal and independent review.

## If you continue in Codex rather than Claude Code

Nothing here depends on Claude Code. The plan, the spec, the spike and this file are all in git;
the briefs and reports are on disk under `.superpowers/` (untracked — do not `git clean -fdx`).

Two adjustments worth making: Codex has no `superpowers:subagent-driven-development` skill, so the
loop described above has to be followed by hand or approximated; and if you run the tasks yourself
rather than dispatching implementers, keep the review step anyway — every task in this plan so far
has contained at least one real defect, and several were found only by mutation-testing a test that
looked fine.

**Three hard-won rules from the 2026-09-20 session, worth carrying over:**

- **Green tests on the Unity boundary are evidence about Python only.** The suite fakes MCP and
  never executes C#. Two defects reached a live Editor after passing three document reviews,
  per-task reviews and adversarial mutation testing. Do not treat a green suite as proof that
  anything works against Unity.
- **Do not add a gate condition you cannot verify live.** That is exactly how both Step 4 defects
  arrived. A `sequence`-based liveness check was proposed during Task 13's review and deliberately
  deferred for this reason; the rationale is recorded in the findings write-up.
- **Write blast radius by grepping, not from memory.** The Task 13 brief claimed `ready()` had two
  call sites. It has three, and the third was the interactive *release* predicate with the same
  failure mode. And never mutate a working tree while a reviewer is reading it — from the outside it
  is indistinguishable from a silent revert, and it cost one false CRITICAL.

**Codex-specific gotchas already measured** (detail in the spike): `codex exec --approve-for-me` uses
`sandbox_workspace_write`, and **Unity hangs inside that seatbelt** on a denied Mach lookup for
`com.apple.hiservices-xpcservice`. Unity must be launched unsandboxed. The launchd job must carry
`ProcessType=Standard` — `Background` measured 7.5x slower for a Unity batch run (105 s against
14 s) because a job's scheduling band is inherited by everything it spawns; this is already fixed at
`agent/deploy.py:33`.
