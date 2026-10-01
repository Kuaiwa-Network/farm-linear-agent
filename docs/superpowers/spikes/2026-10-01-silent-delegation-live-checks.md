# Silent-delegation continuation, 2026-10-01

This is a measured test record and a release preparation note. It does not authorize a production
deployment. The candidate is `3525b937e01743c0bd4eff144f481607ba9470a5` (#74), not the local
feature-worker continuation. Live tests use the operator-selected disposable card and TestBot's
existing isolated development instance. Production has not been changed.

## Evidence available

- Claude's final code review completed on October 1, 00:28–02:48 UTC+8. #74 merged at 07:12.
  The interrupted work was live acceptance, not an unfinished hours-long code review.
- [PR CI](https://github.com/Kuaiwa-Network/farm-linear-agent/actions/runs/36762535097) passed
  on macOS and Windows. Windows ran 1,594 tests, with 67 platform skips and all seven required
  native worker-containment tests executed. The CI checkout `95d8cd88967a8ddffc1b01def6df7925c4df7e80`
  and the candidate have the identical Git tree `b7bb3c474cce18386b6b0f7a9547e4d76fdd90db`.
- [Merged-main CI](https://github.com/Kuaiwa-Network/farm-linear-agent/actions/runs/36789888976)
  passed on macOS; its Windows job reached the 20-minute limit before completing. Its partial
  log is not a passing test result. The passing PR result covers identical tracked source.
- Hosted CI does not establish real Windows desktop, worker-authentication, installation or
  Unity readiness. No such acceptance was performed in this continuation.

## Live checks

Times below are UTC, as requested by the [test protocol](../specs/2026-09-30-silent-delegation-design.md#9-live-checks-on-the-test-instance).
The detailed instance-specific IDs and observations are saved privately under `.local/continuations/`.

| Check | Measured result |
|---|---|
| AC-5 | Ordinary UI delegation at 01:27:28 opened one session with the normal acknowledgement and no silent-delegation note. |
| AC-1 | No agent at 01:43:37; Bot/修改 added; re-delegation at 01:44:06. At 01:45:41 the existing thread received the in-place note and fix acknowledgement. The conversation was replaced by exactly one fix in that thread, which subsequently waited for input. This proves takeover; no substantive human fix request was provided to verify rich-message carryover. |
| AC-4 | A first 49-second setup reposted the pending question. A measured 18.032-second repeat explicitly observed undelegation before re-delegation and settled at 03:35:15.688. Job ID, thread, generation zero and awaiting-input state were preserved; no new worker or thread. The repeat notice was suppressed by the specified 30-minute rate limit. A separate 5.4-second UI flap was coalesced before the controller observed undelegation and is not acceptance evidence. |
| LC-1 | After that measured setup, operator cancellation closed the in-place fix at 03:36:00.262 while the card remained delegated. Through 03:39:33.835, no delayed new session or duplicate job appeared, and no closing response was owed. The earlier conversation-only observation also saw none, but its 30-second setup was not measured. |
| AC-2 | Partial result, not a pass. The waiting mention received the note at 03:48:06.971, about 95 seconds after delegation, and its UI status became Finished while the conversation stayed parked and answerable. No agent/re-delegation at 03:49:39 opened no new session. After archiving the earlier completed delegation thread, a second repeat at 03:52:41 still opened none through 03:54:42 (121.8 seconds), with only the completed mention unarchived. Both episodes settled unseen. Archiving the completed mention at 03:55:10, then delegating at 03:55:25, did open a new fix session and carried the test prompt into its inbox; this extra step is separate from acceptance. |
| LC-2 | With only the new delegation thread unarchived, its latest activity was the normal thought acknowledgement and its worker was running. No agent/re-delegation completed in 23.137 seconds at 03:59:20.100. No new session appeared through 04:01:42.392 (142.3 seconds). The controller kept the same fix, settling served at 04:00:51.056. |
| AC-3 | Stop portion passed; full check not passed. At 04:02:54.710 a new mention forwarded an answer to the waiting fix; the original thread received RESUMED_ELSEWHERE. Stop was pressed in the forwarding thread at 04:03:31.428, cancelling the fix at 04:03:32.331. The original thread visibly received STOPPED_ELSEWHERE and the forwarding thread STOP_ELSEWHERE; both showed Finished. A 22.424-second No agent/re-delegation completed at 04:04:35.291, but no new session appeared through 04:08:58.858 (263.6 seconds), and the episode settled unseen. |
| LC-3 | Raw session-state controller log lines were not recorded. No custom bot-token API query was made. |
| LC-4 | Deferred own-session-creation follow-up; outside this change's acceptance. |
| AC-6 | Optional tunnel-outage check not run. |

Doctor after AC-1, AC-4 and LC-1 showed the same four pre-existing findings as the baseline and
no new findings. AC-2's unsuccessful follow-up produced the expected `silent_delegation` warning
for the test card. Those unrelated jobs and recovery state were left intact.

The AC-2 and AC-3 results contradict the acceptance assumption that ending waiting threads with responses
is sufficient for the next UI delegation to open a session. The controller's fallback executed, but
the person could still follow its instruction and have no work start. The previously planned
own-session-creation follow-up (LC-4) is therefore relevant; it was not tested or implemented here.
Do not waive the failed acceptance criterion or describe the archive workaround as the expected flow.

The disposable card was cancelled at 04:09:22.855 and its TestBot delegation removed at 04:10:54.355.
All five work items are cancelled. Final doctor showed no active test job, worker, slot, reservation or
owed closing response. It retains one expected `silent_delegation` warning for the unsuccessful episode,
in addition to the four baseline findings; the recorded episode was not deleted to make diagnostics green.

## Local guidance correction

The four silent-delegation notes now add a fallback to archive this bot's **completed** sessions if
UI re-delegation still opens none. The no-work note no longer promises that its closing response will
let Linear open a session. Doctor offers the same fallback. Running and needs-input sessions are not
the suggested archive targets. This corrects guidance only; it does not implement automatic session
creation or turn the failed acceptance criteria into passes. TestBot still serves `3525b93` and has
not received this local correction.

The existing notice regression first failed for all four missing fallback texts. After correction,
the focused offline commands passed on macOS with Python 3.13.14:

```sh
env -u FARMBOT_CONFIG -u FARMBOT_LINEAR_STUB_DIR python3 -B -m unittest discover -s tests -p 'test_withdrawal.py' -v
env -u FARMBOT_CONFIG -u FARMBOT_LINEAR_STUB_DIR python3 -B -m unittest discover -s tests -p 'test_doctor.py' -v
env -u FARMBOT_CONFIG -u FARMBOT_LINEAR_STUB_DIR python3 -B -m unittest discover -s tests -p 'test_receiver.py' -v
```

8, 46 and 196 tests respectively: 250 passed, no skips. The initial sandboxed doctor run failed its
real-process probe and the receiver run had 14 denied localhost-listener errors; the unchanged tests
passed with the required host access. No checks were weakened. `git diff --check` and local documentation
links were checked. A full suite rerun was unnecessary for notice and diagnostic text changes. These
are Mac results, not Windows verification of the local correction.

## Windows production runbook prepared for the operator

**Hold promotion: full AC-2 and AC-3 did not pass.** This runbook is for the
existing `FarmBot-Receiver` Windows installation using `scripts/redeploy-farmbot.ps1`. It is not
an installation or state-migration procedure. A different scheduled-task layout needs its own
verified procedure. The actual Windows host, installed revision, interpreter and config have not
been inspected here.

1. Record the existing task's working directory, Python executable, selected config, absolute state
   root and current Git revision on Windows. Use the installed interpreter for every command.
   Confirm the checkout is clean and the selected config identifies production FarmBot and its
   existing ledger. Do not copy the Mac config, state or ownership marker.
2. Run `python -m agent.service doctor --config <absolute-production-config>` with that interpreter
   before changing source; save the JSON privately as a baseline. Review parked work, active
   workers, reservations, slots, recovery evidence and owed session closures. Let work settle.
   The redeploy helper checks queued/running/resource-waiting jobs and worker PIDs, but does not
   reject every parked state or every diagnostic finding. Any remaining parked work or recovery
   finding requires an explicit compatibility decision, not an automatic cancel or cleanup.
3. Back up the ledger with SQLite's backup API, rather than copying only its main file while WAL
   writes may exist. Preserve the private config, memory, worktree/ref inventory and recovery
   evidence using the host's existing private backup procedure. Store the backup outside the
   checkout. Record the old revision and candidate. Keep the deployment window free of new
   operator delegations; there is no implemented general drain command. The helper repeats its
   busy checks immediately before restarting.
4. In the verified installation checkout, fetch the repository, select the exact candidate with
   `git switch --detach 3525b937e01743c0bd4eff144f481607ba9470a5`, and verify `git rev-parse HEAD`.
   Do this only after work is settled, because workers and controller use files in the checkout.
   Preserve private ignored files and the existing production configuration. This release does
   not require enabling the unfinished `feature` skill or changing the webhook endpoint.
5. Run `.\scripts\redeploy-farmbot.ps1 -CheckOnly`. After the operator accepts the recorded live
   results, backup and compatibility decision, run `.\scripts\redeploy-farmbot.ps1` from that
   checkout. It verifies receiver ownership, stops that receiver and waits for the existing
   scheduled supervisor to restart it. It neither fetches nor chooses a revision. Do not substitute
   an arbitrary PID kill or remove controller/ownership markers.
6. A new receiver PID and health 200 only establish the helper's restart check. Run doctor again
   with the same explicit config and interpreter; compare findings with the baseline. Verify
   the service heartbeat reports the candidate revision and advancing serving timestamps, the
   intended identity/state paths, normal webhook delivery and scheduling, and relevant Unity
   readiness. Any live production smoke test requires a separately selected issue and authorization.
7. Keep the old revision and backup until the release is accepted. #74 adds durable session-closure
   and delegation-episode tables, session forwarding and issue-read metadata. Earlier intervening
   changes may add other columns, depending on the installed revision. Starting the candidate can
   migrate the ledger. Checking out old code does not undo that migration or external comments,
   sessions, branches and PRs. Stop and reconcile before any rollback that needs a data restore;
   code-only rollback is not verified by this runbook.

For source behavior and helper limits, see [README](../../../README.md#run),
[the operating contract](../../operating-contract.md), and
[the release proposal](../plans/2026-09-22-cross-platform-development-and-release.md).
Unchecked rollout phases remain proposals.
