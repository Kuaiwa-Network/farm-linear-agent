# Live slot rehearsal — 2026-09-20

Task 11 resumed at merged PR #5 (`328cc80`). This report supersedes the halted Step 4
status in `docs/superpowers/spikes/2026-09-20-live-rehearsal-findings.md`.
The real Unity Editor passed the identity probe, both arrival orders serialized on
slot 1, and Stop plus a launchd restart removed a running batch Editor.

## Method and scope

Used the existing slot and ledger in the original checkout; no slot was copied or
re-imported. `enqueue` checked real delegation on FARM-1230 and FARM-1238. Operator
claims requested and released the reservations; no rehearsal comments were posted.
The pool, launcher, Unity, MCP server, Git/LFS, SQLite and tests were real. The first
interactive run and reverse-order pair used `service.build()` with deliberate pool
ticks. The batch-first pair and Stop/restart checks ran under the actual
`com.kuaiwa.farmbot.serve` launchd label. Autonomous fix workers were disabled during
these checks (`max_concurrent=0`); this is live resource evidence, not a new proof of
Codex reasoning or draft-PR delivery. The earlier Step 3 already demonstrated a fresh
Codex worker consuming real batch results; criterion 5 is tracked separately.

The installed plist was still `Background` despite Task 7's renderer change. The
rehearsal used a temporary `Standard` plist, preserving the installed config and tunnel.
The actual Editor binary was Unity 2022.3.62f3 on this Mac.

## Results

| Check | Measurement |
|---|---|
| First interactive grant | 41.66 seconds; aggregate and all seven checks `match` |
| Instance | `slot-1@e7fe013d9909e41a`, matching SHA-1 of the slot's Assets path |
| Loaded source | `381a825709379fa1cab5aed7b1b405129203aa35`; clean before and after probe |
| Cache observation | sequence 3 before and after; live probe flags all idle |
| Reuse | same Editor PID 89878 for the next interactive request |
| Interactive → batch | interactive released and parked open; Editor closed before batch |
| Batch pin | `6d5b46fbe3afe2b5cec95f62665e230ab8cb3601` |
| Reverse-order batch | 23.1 seconds running Unity, 26.10 seconds through hand-over |
| Test result | exit 2, total 4414, passed 4388, failed 26; matches known-red baseline |
| Batch → interactive | second request remained awaiting_resource until batch release and park |
| CLI batch Stop | Unity gone in 0.329 seconds, after project lock existed; lock then removed |
| Forced service restart | `launchctl kickstart -k` removed batch Unity in 0.151 seconds |
| Interactive Stop | reservation released after probe in 2.445 seconds; Editor remained open |
| Final park | idle_open on `381a825709379fa1cab5aed7b1b405129203aa35`, no active reservation |

The full first observation is [identity.json](identity.json). Process commands, PIDs,
timestamps and measured timing are in [batch-stop.json](batch-stop.json),
[restart.json](restart.json) and [interactive-stop.json](interactive-stop.json).
Each batch Editor's parent in those records is the running service, not a worker.
The restart observation is specific to this Mac's launchd behavior; Python cannot
catch SIGKILL and these results do not claim otherwise. After restart the operator
cancelled the interrupted rehearsal item, and the pool settled it and parked main.

## Items and replay

| Purpose | Work item |
|---|---|
| Initial interactive identity | `67c53107-7db7-43d2-854b-7793f3bf825a` |
| Interactive first | `2274bf8f-86a2-4e66-926c-c06d8e1f2634` |
| Batch second | `b2cdd891-1268-4afb-8ee1-2bf80dbeb30d` |
| Batch first | `e3b5af71-a2ae-44c5-9e28-b209bd8d019d` |
| Interactive second | `2879a17b-58b3-413e-adea-1c6cae2d81f9` |
| Batch Stop | `cb26396e-74a4-4dff-9b2e-12a75b32b6de` |
| Restart | `4d0f7abb-1f5f-477f-8738-22233b3b2767` |
| Interactive Stop | `afa96e2e-0a03-4125-a134-9fd313314cb8` |

Run `python3 scripts/check-rehearsal.py --db <original-checkout>/.local/agent/ledger.sqlite3
--runs <original-checkout>/.local/runs` with `--pair FIRST SECOND` for each ordering,
`--item` for the first interactive identity, and `--cancelled-item` for each Stop item.
The full verbatim output is [checks.txt](checks.txt). It checks reservation/slot audit
ordering, ownership overlap, both pair orders, identity, final park and XML evidence.
Cancelled batch runs are INTERRUPTED, not fabricated successful test runs. The CLI
does not record a separate cancel-request timestamp, so process latency comes from
the external monotonic stopwatch and process observations, not invented ledger rows.

## Defects found and addressed

1. CLI cancel changed SQLite but never stopped a launcher's owned batch or worker.
   Scheduler reconciliation now kills owned processes before reaping and sweeping.
2. Default SIGTERM bypassed `serve`'s cleanup. A main-thread handler now unwinds through
   cleanup during startup and serving, then restores the previous handler.
3. Stop/shutdown could miss a batch started after their process snapshot. Spawn and
   registration now share a lock with cancellation, with a shutdown fence and a check
   of the original reservation immediately before spawning.
4. Group cleanup returned as soon as the leader died, leaving a child that ignored
   SIGTERM. It now checks the group and escalates against surviving members.
5. Independent review found cancel followed immediately by retry could hide the
   cancellation. Reconciliation also recognizes old worker ownership and outstanding
   cancellation reservations; retry waits for the old reservation to settle.

Each Python fix has a regression observed failing before the fix and passing after it.
Tests use temporary SQLite/Git and real disposable subprocesses, never Unity or the
real ledger. The baseline was 306 tests, warning-free outside the Codex sandbox. The
sandbox's loopback-bind and process-list restrictions caused the first baseline's
failures; they were not attributed to production code.

Final validation: `python3 -W error -m unittest discover -s tests` passed **331 tests**
without warnings. The 15 checker tests also cover missing transitions, wrong-item identity,
overlap, zero XML totals, and final checkout drift. Independent review found two Important
retry races; both have failing-then-passing regressions. No new minor findings remain.

## Deviations and limits

- An attempt to queue both modes on the same issue was refused by the active-item
  invariant. The live contention pairs use two separately delegated issues.
- The checker is longer than the plan's approximate 25 lines because it validates
  missing evidence and correlates slot events with reservations rather than trusting
  final states. Regression tests cover its false-positive failure modes.
- Rehearsal completion claims concern resource lifecycle only. FARM-1247 was selected
  by the operator for the separate real-delivery criterion; its worker finished blocked
  on a contract/configuration conflict, with no code change or PR. See
  `reports/2026-09-20-FARM-1247/report.md`.
- Windows, config export, player builds, multiple slots, PlayMode account identity,
  and the previously deferred sequence-based readiness condition remain out of scope.
