# Genuine native Windows Contract stage A

Measured in a Windows development test on 2026-10-06, in a separate checkout
and isolated development TestBot state on the operator's Windows machine.
Production configuration, ledger and
service were not inspected or changed. These results certify the scoped
development run, not production readiness.

## Candidate and authorized scope

FarmBot ran merged #140, `bc2ce9bf2c2a525e620196fb9351dbbc0ff1f56b`, with
Python 3.13.16, Git 2.54.0.windows.1, Git LFS 3.7.1 and the configured Codex
0.156.1 `unelevated` backend, `workspace-write`, network access enabled.
`PYTHONUTF8=1` preceded Python startup. The private wrapper sanitized inherited
selectors, verified development identity/state ownership and pinned tools.

The operator approved [FARM-1435](https://linear.app/kuaiwagames/issue/FARM-1435)
and its scope comment before delegation: resolve the existing Contract's
1201/1202/1203 blessing-ID/mail-template-ID contradiction, preserving frozen
blessing/final mail text, historical snapshots and rejection of invalid config
before charge, rewards or valid-order commit. This was a real change, not an
unchanged-source audit. No new wire declarations, economics, common/hive/client/UI
implementation or game PR merge was authorized. The human assignee was retained.

The first failed attempt and its completed cancellation remain in the
[bootstrap record](2026-10-06-windows-claim-bootstrap.md). The corrected retry
used item `2c006c7b-9ec2-4340-88a5-b3ad0289c63d` and session
`a6bf7532-b710-43df-8638-c771defac652`, one attempt, generation zero.
It started at 06:01:08.667 UTC and parked at 06:16:29.433 UTC:
**920.765 seconds** from item creation to the recorded awaiting-input state.

## Intake, source and native Git

Signed intake routed one target-free feature item. The first acknowledgement
and the acknowledgement of the approved retry message had no client target;
the message reached the running worker and was consumed. One started notice
was posted. Claim-token exclusive creation succeeded with the inherited Windows
DACL; the retry made no `icacls`/`Set-Acl` repair. All 125 recorded shell
invocations used native `pwsh.exe`. No Bash/MSYS/WSL/mxc route was used.

The worker fetched the real wiki planning document using the explicit `farmbot`
profile and `--as bot`: exit zero, 1.809 seconds, response identity `bot`,
43,431 UTF-8 JSON bytes and 42,554 extracted Markdown bytes with 30 heading
lines. The JSON SHA-256 was
`457cf574608097f223ce34448b0d3fd9099e705a5cd2bb1f85ce95841691df18`.
This run did not download an attachment or perform DOCX conversion. Those
different checks retain their earlier scoped Windows measurements in the
[signed-intake record](2026-10-03-native-windows-offline.md#windows-signed-intake-and-long-path-checkouts-2026-10-06).

Read-only snapshots of Farm-Contract, Farm-Client and farmgui were present.
The worker performed native Git revision/history reads. Its actual
`foreign-work` report completed with `status: none`, no errors and no matching
foreign PRs/branches across the five configured repositories. This replaces
the first attempt's incomplete report for this retry only.

The process environment selected the optional native callback from #141's
initial candidate, with helper SHA-256
`d96aecc5fbfae5153e3f7794de18487a9f27d08650894c5d512970d9fe4dd121`.
The existing native GitHub CLI and whitespace-free callback selector were
verified against their private receipt before starting the development service.
OpenSSL and certificate verification were explicit. Empty `credential.helper`
selection used `GIT_CONFIG_PARAMETERS`: Windows does not preserve an empty
`GIT_CONFIG_VALUE_n` as a usable environment entry. No global Git setting or
credential store was changed. Real worker remote queries, push and draft-PR
publication succeeded without the refused shell helper.

Nonzero exploratory commands were recorded: a not-yet-existing remote branch,
an empty search result and two absent optional Git identity queries. The worker
used command-local commit identity. These were not contract gate failures or
containment/credential errors.

## Contract gates and publication

Baseline Farm-Contract main was `29e6cfe1430a7c7313febc642f95db4ea6c3fb18`.
All twelve gates passed on the baseline, the changed tree and the final tree;
no gate was skipped. The worker's intermediate report is named `fix` but also
contains twelve zero exit codes. Totals below sum the recorded gate durations.

| Gate | Baseline seconds | Changed (`fix`) seconds | Final seconds | All exit codes |
|---|---:|---:|---:|---|
| buf build | 0.060 | 0.061 | 0.061 | 0 |
| buf lint | 0.089 | 0.093 | 0.123 | 0 |
| breaking | 2.854 | 2.955 | 2.964 | 0 |
| manifest | 0.071 | 0.071 | 0.104 | 0 |
| markers | 0.158 | 0.186 | 0.160 | 0 |
| message naming | 0.050 | 0.053 | 0.085 | 0 |
| proto fields | 0.094 | 0.098 | 0.093 | 0 |
| coverage | 0.087 | 0.055 | 0.088 | 0 |
| provenance | 0.114 | 0.105 | 0.106 | 0 |
| README inventory | 0.040 | 0.040 | 0.074 | 0 |
| OpenSpec config | 0.033 | 0.035 | 0.034 | 0 |
| OpenSpec validation | 0.661 | 0.676 | 0.736 | 0 |
| **Total** | **4.311** | **4.428** | **4.628** | **12/12 each** |

The registered real [Farm-Contract draft #328](https://github.com/Kuaiwa-Network/Farm-Contract/pull/328)
is open at `220e618d3cd736b6f8cc747576bca7e5a0ca413a`, based on `main`.
It changes eight scoped files: the OpenSpec gifting/mail text, project authority
row, one proto comment and its manifest hash. The wire declarations are
unchanged. The diff preserves the required snapshots and fail-closed boundary;
the new scope is attributed to the operator's actual approval comment, while
prior design decisions retain their recorded authorship. No question notice was
needed for this already-resolved narrow scope; this run does not exercise the
parked-question variant again.

All [28 Contract CI checks](https://github.com/Kuaiwa-Network/Farm-Contract/actions/runs/37422640372)
passed across Windows, macOS and Ubuntu. No game PR was merged. Independent
read-only checks found no trusted-clone problems, clean worktrees and no common
or farm-hive changes. Their heads remained `b367febe` and `6eff95c2` respectively.

## Exact stopping boundary and privacy

Confirmed notices appeared in order: `stage-B` (also skipping C), `stage-D`, then
`merge-contract`; no stage-A stage notice was posted. The merge request includes
the operator's stage-A stopping limit. The plan has A `done`, B/C/D skipped,
G pending, a `stage_limit` event and
`pause={kind: stage_limit, reason: waiting, notice: merge-contract}`.
The worker ran `await-input --reason waiting`, with no handoff to another root.

Before the separately approved cancellation check, the item was `awaiting_input`,
stage `stage-A-done`, with no worker PID or target,
and its native Job Object is empty. Heartbeat workers are zero and all six loop
error counters are zero. Read-only `doctor` shows Farm-Contract root, A done,
the waiting/stage-limit pause and draft #328. Its `attention` status contains
only two retained historical `job_failed` findings; no missing feature tools or
pending cleanup were reported. Doctor did not create a ledger.

The local scan of 477 run files, initially 12 fetched issue comments and draft-PR text
found zero occurrences of the known Feishu app ID/secret and development Linear
credentials. The claim token appeared only in its designated file, with no
public match. The existing Feishu profile remained strict bot mode with zero
personal user logins. No credentials/profile/account settings were changed.
Private UTF-8 logs, process/Job receipts, per-gate reports and the parked-session
screenshot are retained locally; private paths and source text are not published.

## Exact parked-stage-limit cancellation (2026-10-06)

The operator explicitly approved the cancellation check in this development
chat. Only TestBot's delegate was cleared through the Linear connector;
the human assignee, Backlog state, Code label and issue description were retained.
No reply or redelegation was sent to the parked session, and no successor started.

| Observation | UTC / measured result |
|---|---|
| Delegate-removal request started | 07:14:57.008 |
| Connector acknowledgement | 07:14:59.196 |
| First status read marking delegation absent | 07:14:59.926 |
| Item cancelled | 07:16:02.191 |
| One session withdrawal response created | 07:16:03.106 |
| Cleanup completed | 07:16:31.128 |
| Request to cancellation | 65.183 seconds |
| Acknowledgement to cancellation | 62.995 seconds |
| First absent-delegate read to cancellation | 62.265 seconds |

This does **not** meet the original Task 17 check 4's literal one-interval
wall-clock expectation (`reconcile_seconds=60`). The current withdrawal
contract and `Lifecycle._refresh` require two absent-delegate reads at least
one interval apart, with API/read and polling overhead. The observed delay is
consistent with that implementation, rather than a failed cancellation.
The two-read confirmation, containment and ownership checks were preserved.
The older task wording is historical; these measurements do not promise a
60-second deadline from the operator's action.

Linear displayed the exact `UNDELEGATED` response once in the selected session
and marked it **Finished**. A second issue/comment read found the same thirteen
comments, with exactly one new withdrawal response in this session and no
subsequent post. The three prior stage/merge notices remain confirmed.
The item remains cancelled after the development controller restart.

Cleanup has `done=true`, no error, no worker PID or target, and an empty owned
Job Object. The entire item worktree directory and its `.reads` directory are
absent. Each owned repository's `refs/farmbot/recovery/<item id>` resolves to
its exact pre-cleanup HEAD:

| Repository | Recovery HEAD |
|---|---|
| Farm-Contract | `220e618d3cd736b6f8cc747576bca7e5a0ca413a` |
| common | `b367febe20d6db65ebb386aa871bdb2671df9525` |
| farm-hive | `6eff95c278ccdee1e9caeed862969bd0140198f8` |

Read-only doctor reports no unfinished job or pending cleanup for this item,
and no missing feature tools. Its `attention` status still consists only of
the two previously recorded historical `job_failed` findings. The exact
six-job quiescence guard verifies two historical failures, four cancellations,
all webhook events done, every recorded native Job empty, zero cleanup errors
and all item/read worktrees removed. It does not hide or delete those failures.

Draft #328 remains open and draft at the same HEAD, with its published branch
retained. The final credential scan includes thirteen comments and still has
zero known credential or public claim-token matches across the 477 run files,
comments and PR text. The session screenshot and UTF-8 private receipts are
retained locally.

After identity and quiescence verification, only the owned development
controller was stopped. Only its `enabled_skills` changed to `chat`/`fix`;
all other config fields are identical and backups/logs were preserved. The
settled development controller restarted at `bc2ce9bf`, serving with zero
workers and zero loop errors. The existing tunnel was preserved. Production
configuration, state and service were not inspected or changed.

The documentation follow-up passed the existing 50 lifecycle tests in 2.712
seconds and 75 skill/reference tests in 0.396 seconds on native Windows
Python 3.13.16, with zero failures, errors or skips. The lifecycle checks
include the existing two-read interval and delegation-flap cases; no test,
containment rule or withdrawal behavior was changed. All five changed files
are documentation, their added local links and anchors resolve, whitespace
checks pass, and the known-credential/private-path scan has zero matches.
A full suite rerun is unnecessary for this documentation-only follow-up.

## Native callback CI follow-up

FarmBot #140's full CI ran 1,753 tests on each platform: Windows completed
without failures/errors in 1,597.412 seconds with 69 platform skips; macOS in
516.992 seconds with 19 skips. All thirteen feature journeys passed on both,
and all seven native Job Object tests passed on Windows. Hosted runners are
separate from this PC.

#141's initial full CI ran 1,759 tests. Windows reported two failing subtests
in the callback's positive username/password test, no errors and 69 skips
(1,197.479 seconds). macOS passed with 25 skips (478.299 seconds). All thirteen
journeys passed on each; all seven native Windows Job tests passed. The failed
run and its UTF-8 logs are preserved.

The CI failure reproduced locally under a UTF-8 Windows console: the .NET
Framework redirected writer inserted a BOM before `protocol`, which the dummy
CLI correctly refused. Candidate `e592c32` selects BOM-free UTF-8 at child
creation, restores the callback's input encoding, and makes response decoding
explicit. All seven focused native tests pass in 1.593 seconds, with zero skips
and no host authentication. The added regression forces the failing console
setting and checks input-encoding restoration. The final full CI passes on both platforms, as recorded below.

The updated callback's final no-model restricted probe passed every configured
remote branch read and inherited selection, and a fresh owned bare Contract
fetch of main `29e6cfe1`. It refused an unrelated valid dummy CA with exit 128
and completed in 16.055 seconds with an empty owned Job, completed reader and
unchanged runtime receipt. The tested updated helper SHA-256 is
`8714b0336221d7716e83bf12f960ebbd4dd91fd24941f47ca75e683ee277f457`.
It has not replaced the live development controller's already-selected binary.

## Final tools merge and full CI

[FarmBot #141](https://github.com/Kuaiwa-Network/farm-linear-agent/pull/141)
merged as `0f97c794901e6e4816ed8d2311dacf1da534f6a6`. The final candidate
`e592c32` and merge share tree `ce2915ebe2a0ddf22174a6182dd52fe7a0ab2067`.
The accepted [full CI run](https://github.com/Kuaiwa-Network/farm-linear-agent/actions/runs/37423421444)
tested synthetic merge `a71408616aadd9683a0170df310ad24a77108f77`.

| Hosted platform | Python | Git | Git LFS | Tests | Failures/errors | Skips | Runner elapsed seconds |
|---|---|---|---|---:|---|---:|---:|
| Windows | 3.13.15 | 2.55.0.windows.5 | 3.7.1 | 1,760 | 0 / 0 | 69 | 1,653.577 |
| macOS | 3.13.15 | 2.55.0 | 3.8.0 | 1,760 | 0 / 0 | 26 | 501.334 |

Unittest itself reports 1,651.340 seconds on Windows and 500.411 seconds on
macOS; the table includes runner/report overhead. All thirteen feature journeys
passed on both platforms. All seven native Job Object tests and all seven native
callback tests ran and passed on Windows. No native Windows check is inferred
from a macOS skip. These are hosted results, separate from the measured live
Windows development run. The failed initial CI artifacts, final UTF-8 logs,
versions, durations and every platform skip are retained.

The documentation record passed 75 skill/reference checks, zero skips, in
0.482 seconds, plus link, privacy and whitespace checks. No full offline rerun
is required for this subsequent documentation-only change.

## Remaining release prerequisites

1. The [final development tool check](2026-10-06-windows-final-tools-lfs.md)
   now verifies merged #143's unchanged code, final callback identities,
   empty-root doctor and native Git transport. Confirm the selected identities
   again as part of the separately approved production release.
2. Obtain separate operational approval for production promotion and feature
   enablement, confirming the intended Windows account/profile and release
   recovery procedure. This development acceptance does not certify that host.
3. Obtain a concrete approved real scope for later live common/config/hive stages
   and run their remaining native worker generator checks. Running-worker
   withdrawal remains unmeasured; this check cancelled a parked job with no PID.
   Phase C/client work and the later UI worker are subsequent implementation work.
4. Before Phase C, complete the actual internal-server LFS new-object upload
   and headless native Farm-Client proto export. One bounded real LFS download
   and an offline native upload/download calibration pass; the prepared 1 KiB
   real upload awaits approval in the linked final-tools record.

Current-account DPAPI access is the operator's approved development choice;
no separate HOME credential-isolation claim is made. No account was configured.
Untested generators/later live stages retain their existing pending status;
this stage-A test does not certify them. Phase C has not started.

## Full CI platform skips

The final #141 run preserves every skip below. #140 has the same 69 Windows
skips and the same macOS list except the seven callback tests. The initial
#141 run has 25 macOS skips, omitting the added UTF-8 regression; its Windows
skip map is identical to the final run. No Windows skip was added for the fix.

### windows-latest: 69 skips

| Test | Platform reason |
|---|---|
| `test_cleanup.CancellationCleanupTests.test_exited_parent_with_detached_child_holds_cleanup_after_restart` | Windows worker jobs contain children; tested in test_windows_workers |
| `test_cleanup.SelfExitedWorkerCleanupTests.test_continuation_of_a_self_exited_paused_worker_launches` | POSIX self-exit evidence; Windows workers are proved by Job Objects |
| `test_cleanup.SelfExitedWorkerCleanupTests.test_resource_fencing_certifies_a_worker_that_exited_by_itself` | POSIX self-exit evidence; Windows workers are proved by Job Objects |
| `test_cleanup.SelfExitedWorkerCleanupTests.test_resource_fencing_fails_while_the_old_handle_is_still_registered` | POSIX self-exit evidence; Windows workers are proved by Job Objects |
| `test_cleanup.SelfExitedWorkerCleanupTests.test_retirement_leaves_the_reap_of_a_registered_worker_to_poll` | POSIX self-exit evidence; Windows workers are proved by Job Objects |
| `test_config.LarkCliConfigTests.test_a_home_lies_outside_local_root_the_users_home_and_every_temporary_directory` | Windows refuses every lark_cli home |
| `test_doctor.FeatureToolchainTests.test_a_store_whose_key_is_a_file_and_that_holds_a_login_is_exposed` | the macOS store's master key file |
| `test_doctor.ProcessProbeTests.test_invalid_pids_are_never_passed_to_os_kill` | POSIX process inspection |
| `test_doctor.ProcessProbeTests.test_permission_denied_and_ps_failure_are_unknown_not_dead` | POSIX process inspection |
| `test_doctor.ProcessProbeTests.test_real_worker_is_recognized_and_reaped_worker_is_dead` | POSIX process inspection |
| `test_heartbeat.HeartbeatFileTests.test_a_fifo_or_symlink_is_unreadable_without_waiting` | FIFOs and O_NOFOLLOW symlink refusal are POSIX |
| `test_heartbeat.HeartbeatFileTests.test_write_replaces_a_symlink_or_fifo_instead_of_opening_it` | FIFOs are POSIX |
| `test_launcher.LauncherTests.test_a_fifo_the_worker_left_as_a_report_file_cannot_block_poll` | FIFOs in a directory are POSIX |
| `test_launcher.LauncherTests.test_a_kill_after_a_restart_persists_its_targets_past_a_fifo_left_for_their_temporary_file` | FIFOs in a directory are POSIX |
| `test_launcher.LauncherTests.test_a_kill_after_a_restart_refuses_a_fifo_teardown_record_before_signalling` | FIFOs in a directory are POSIX |
| `test_launcher.LauncherTests.test_a_stop_replaces_a_fifo_the_live_worker_swapped_in_for_its_teardown_record` | FIFOs in a directory are POSIX |
| `test_launcher.LauncherTests.test_cleanup_checks_refuse_a_fifo_launch_or_teardown_record_without_blocking` | FIFOs in a directory are POSIX |
| `test_launcher.LauncherTests.test_group_kill_escalates_when_only_the_child_ignores_term` | POSIX process group semantics |
| `test_launcher.LauncherTests.test_spawn_notes_an_undelivered_prompt_past_a_fifo_the_worker_left_for_the_note` | FIFOs in a directory are POSIX |
| `test_launcher.LauncherTests.test_spawn_records_the_pid_past_a_fifo_the_new_worker_put_at_its_launch_record` | FIFOs in a directory are POSIX |
| `test_launcher.LauncherTests.test_stop_does_not_block_on_a_fifo_teardown_record_and_holds_cleanup` | FIFOs in a directory are POSIX |
| `test_launcher.PosixSelfExitTeardownTests.test_a_claimed_worker_is_not_reaped_by_poll_even_without_waitid` | POSIX sessions; Windows workers are proved by Job Objects in test_windows_workers.py |
| `test_launcher.PosixSelfExitTeardownTests.test_a_failing_teardown_check_still_reports_every_exit` | POSIX sessions; Windows workers are proved by Job Objects in test_windows_workers.py |
| `test_launcher.PosixSelfExitTeardownTests.test_a_fifo_teardown_record_holds_cleanup_without_blocking_the_poll` | POSIX sessions; Windows workers are proved by Job Objects in test_windows_workers.py |
| `test_launcher.PosixSelfExitTeardownTests.test_a_fifo_that_appears_at_the_teardown_record_is_replaced_by_the_verified_one` | POSIX sessions; Windows workers are proved by Job Objects in test_windows_workers.py |
| `test_launcher.PosixSelfExitTeardownTests.test_a_group_still_reported_after_the_reap_holds_cleanup` | POSIX sessions; Windows workers are proved by Job Objects in test_windows_workers.py |
| `test_launcher.PosixSelfExitTeardownTests.test_a_member_that_cannot_be_terminated_holds_cleanup` | POSIX sessions; Windows workers are proved by Job Objects in test_windows_workers.py |
| `test_launcher.PosixSelfExitTeardownTests.test_a_process_table_that_stays_unreadable_holds_cleanup` | POSIX sessions; Windows workers are proved by Job Objects in test_windows_workers.py |
| `test_launcher.PosixSelfExitTeardownTests.test_a_reap_that_cannot_complete_is_retried` | POSIX sessions; Windows workers are proved by Job Objects in test_windows_workers.py |
| `test_launcher.PosixSelfExitTeardownTests.test_a_repeated_stop_keeps_what_an_earlier_stop_recorded` | POSIX sessions; Windows workers are proved by Job Objects in test_windows_workers.py |
| `test_launcher.PosixSelfExitTeardownTests.test_a_signalled_member_that_leaves_the_session_is_still_checked` | POSIX sessions; Windows workers are proved by Job Objects in test_windows_workers.py |
| `test_launcher.PosixSelfExitTeardownTests.test_a_teardown_record_naming_another_pid_holds_cleanup` | POSIX sessions; Windows workers are proved by Job Objects in test_windows_workers.py |
| `test_launcher.PosixSelfExitTeardownTests.test_a_verified_self_exit_keeps_an_interrupted_stops_recorded_descendants` | POSIX sessions; Windows workers are proved by Job Objects in test_windows_workers.py |
| `test_launcher.PosixSelfExitTeardownTests.test_an_unreadable_process_table_is_retried_while_the_worker_stays_unreaped` | POSIX sessions; Windows workers are proved by Job Objects in test_windows_workers.py |
| `test_launcher.PosixSelfExitTeardownTests.test_an_unverified_self_exit_supersedes_an_interrupted_stop_record` | POSIX sessions; Windows workers are proved by Job Objects in test_windows_workers.py |
| `test_launcher.PosixSelfExitTeardownTests.test_normal_exit_is_proven_quiescent` | POSIX sessions; Windows workers are proved by Job Objects in test_windows_workers.py |
| `test_launcher.PosixSelfExitTeardownTests.test_other_group_in_the_worker_session_is_terminated_and_recorded` | POSIX sessions; Windows workers are proved by Job Objects in test_windows_workers.py |
| `test_launcher.PosixSelfExitTeardownTests.test_poll_leaves_a_worker_being_killed_to_kill` | POSIX sessions; Windows workers are proved by Job Objects in test_windows_workers.py |
| `test_launcher.PosixSelfExitTeardownTests.test_same_group_survivor_is_terminated_and_recorded` | POSIX sessions; Windows workers are proved by Job Objects in test_windows_workers.py |
| `test_launcher.PosixSelfExitTeardownTests.test_setsid_descendant_escapes_the_session_check` | POSIX sessions; Windows workers are proved by Job Objects in test_windows_workers.py |
| `test_launcher.PosixSelfExitTeardownTests.test_stop_after_a_self_exit_leaves_the_reap_and_its_evidence_to_poll` | POSIX sessions; Windows workers are proved by Job Objects in test_windows_workers.py |
| `test_launcher.PosixSelfExitTeardownTests.test_stop_racing_a_self_exit_signals_the_group_even_without_waitid` | POSIX sessions; Windows workers are proved by Job Objects in test_windows_workers.py |
| `test_launcher.PosixSelfExitTeardownTests.test_stop_racing_a_self_exit_still_reaches_the_worker_group` | POSIX sessions; Windows workers are proved by Job Objects in test_windows_workers.py |
| `test_launcher.PosixSelfExitTeardownTests.test_stop_terminates_a_group_member_the_parent_walk_cannot_see` | POSIX sessions; Windows workers are proved by Job Objects in test_windows_workers.py |
| `test_launcher.PosixSelfExitTeardownTests.test_what_the_worker_left_for_the_unverified_record_is_replaced_not_opened` | POSIX sessions; Windows workers are proved by Job Objects in test_windows_workers.py |
| `test_launcher.PosixSelfExitTeardownTests.test_without_waitid_a_self_exit_keeps_holding_cleanup` | POSIX sessions; Windows workers are proved by Job Objects in test_windows_workers.py |
| `test_launcher.PosixSessionScanTests.test_a_failed_ps_exit_proves_nothing` | POSIX sessions and process groups |
| `test_launcher.PosixSessionScanTests.test_a_member_that_exits_before_getsid_is_skipped` | POSIX sessions and process groups |
| `test_launcher.PosixSessionScanTests.test_a_session_that_cannot_be_read_proves_nothing` | POSIX sessions and process groups |
| `test_launcher.PosixSessionScanTests.test_a_table_that_does_not_list_farmbot_itself_proves_nothing` | POSIX sessions and process groups |
| `test_launcher.PosixSessionScanTests.test_group_and_session_members_are_listed_but_not_the_leader_or_zombies` | POSIX sessions and process groups |
| `test_launcher.PosixSessionScanTests.test_ps_that_fails_or_times_out_proves_nothing` | POSIX sessions and process groups |
| `test_launcher.PosixSessionScanTests.test_signals_reach_the_group_and_only_members_still_in_the_session` | POSIX sessions and process groups |
| `test_launcher.PosixSessionScanTests.test_the_group_is_gone_only_when_the_kernel_says_so` | POSIX sessions and process groups |
| `test_launcher.WorkerFileReadTests.test_a_fifo_is_refused_without_waiting_for_a_writer` | FIFOs in a directory are POSIX |
| `test_launcher.WorkerFileReadTests.test_a_symlink_is_refused_even_to_a_regular_file` | Windows has no O_NOFOLLOW; making a symlink there needs a privilege |
| `test_launcher.WorkerFileWriteTests.test_a_fifo_at_the_name_is_replaced_without_waiting_for_a_reader` | FIFOs in a directory are POSIX |
| `test_launcher.WorkerFileWriteTests.test_a_symlink_at_the_name_is_replaced_and_what_it_names_is_left_alone` | making a symlink on Windows needs a privilege |
| `test_scheduler.SchedulerTests.test_a_fifo_left_for_the_batch_summary_is_no_evidence_and_cannot_stall_the_launch` | FIFOs in a directory are POSIX |
| `test_service.SignalShutdownTests.test_sigterm_during_pool_ensure_also_cleans_up_and_restores_the_handler` | POSIX service termination contract |
| `test_service.SignalShutdownTests.test_sigterm_reaps_the_batch_child_and_closes_a_running_service` | POSIX service termination contract |
| `test_slots.PoolTests.test_a_fifo_left_for_the_batch_summary_cannot_stall_the_batch_run` | FIFOs in a directory are POSIX |
| `test_slots.PoolTests.test_what_the_worker_left_at_the_token_path_is_replaced_by_the_grant` | no FIFOs in a directory, and making a symlink needs a privilege |
| `test_worktrees.ControllerGitTests.test_farmbots_git_in_a_worktree_follows_neither_of_its_pointers` | the filter here is a shell script |
| `test_worktrees.ControllerGitTests.test_farmbots_own_git_runs_no_hook_left_in_the_clone` | the hooks here are shell scripts |
| `test_worktrees.ControllerGitTests.test_within_those_parts_a_worker_commits_and_pushes_but_cannot_touch_the_config` | macOS's Seatbelt, as Codex's |
| `test_worktrees.ReadCheckoutTests.test_no_hook_fsmonitor_filter_or_setting_of_the_host_or_the_clone_runs` | the hooks, fsmonitor and filters here are shell scripts |
| `test_worktrees.ReadCheckoutTests.test_removal_never_acts_through_a_link` | POSIX permissions; Windows has the junction test |
| `test_worktrees.ReattachTests.test_no_hook_or_fsmonitor_planted_in_the_clone_runs_while_re_attaching` | the planted hooks and fsmonitor are shell scripts |

### macos-latest: 26 skips

| Test | Platform reason |
|---|---|
| `test_cleanup_recovery.CleanupRecoveryTests.test_new_attempt_with_reused_pid_is_not_covered_by_old_boot` | Windows boot proof |
| `test_cleanup_recovery.CleanupRecoveryTests.test_reused_pid_after_reboot_is_not_an_old_worker` | Windows boot proof |
| `test_cleanup_recovery.CleanupRecoveryTests.test_scheduler_preserves_then_cleans_without_killing_reused_pid` | Windows boot proof |
| `test_config.LarkCliConfigTests.test_a_home_is_refused_on_windows` | lark-cli keeps secrets per Windows user, whatever HOME says |
| `test_doctor.FeatureToolchainTests.test_windows_uses_its_running_python_and_never_probes_posix_tools` | native Windows interpreter selection |
| `test_launcher.WorkerFileReadTests.test_an_oversized_launch_record_is_refused_by_the_windows_containment_check` | the Windows containment path reads every attempt's launch record |
| `test_uploads.OutputDirectoryTests.test_a_junction_is_refused_as_out` | junctions exist on Windows only |
| `test_uploads.WindowsNameTests.test_colon_names_create_no_stream` | Windows file-name semantics are checked on Windows |
| `test_uploads.WindowsNameTests.test_reserved_device_names_are_never_opened` | Windows file-name semantics are checked on Windows |
| `test_uploads.WindowsNameTests.test_trailing_dots_and_spaces_do_not_alias` | Windows file-name semantics are checked on Windows |
| `test_windows_git_askpass.NativeGitAskpassTests.test_failed_or_malformed_lookup_never_outputs_a_credential_or_diagnostic` | native Windows credential callback |
| `test_windows_git_askpass.NativeGitAskpassTests.test_invalid_argument_count_never_starts_credential_lookup` | native Windows credential callback |
| `test_windows_git_askpass.NativeGitAskpassTests.test_password_for_another_user_is_refused_without_output` | native Windows credential callback |
| `test_windows_git_askpass.NativeGitAskpassTests.test_unavailable_or_wrong_executable_never_starts_credential_lookup` | native Windows credential callback |
| `test_windows_git_askpass.NativeGitAskpassTests.test_username_and_password_are_only_the_requested_field` | native Windows credential callback |
| `test_windows_git_askpass.NativeGitAskpassTests.test_utf8_console_does_not_add_a_bom_to_the_credential_request` | native Windows credential callback |
| `test_windows_git_askpass.NativeGitAskpassTests.test_wrong_destination_or_prompt_never_starts_credential_lookup` | native Windows credential callback |
| `test_windows_workers.WindowsWorkerTests.test_assignment_failure_never_runs_requested_command` | Windows Job Objects |
| `test_windows_workers.WindowsWorkerTests.test_later_contained_attempt_cannot_certify_legacy_attempt_with_same_pid` | Windows Job Objects |
| `test_windows_workers.WindowsWorkerTests.test_normal_exit_is_proven_quiescent` | Windows Job Objects |
| `test_windows_workers.WindowsWorkerTests.test_receiver_crash_terminates_its_worker_job` | Windows Job Objects |
| `test_windows_workers.WindowsWorkerTests.test_simultaneous_stop_and_poll_finalize_job_once` | Windows Job Objects |
| `test_windows_workers.WindowsWorkerTests.test_spontaneous_exit_reaps_child_and_preserves_unrelated_process` | Windows Job Objects |
| `test_windows_workers.WindowsWorkerTests.test_stop_is_proven_quiescent` | Windows Job Objects |
| `test_worktrees.ControllerGitTests.test_cleanup_refuses_a_junction_in_the_item_directory` | junctions are Windows' |
| `test_worktrees.ReadCheckoutTests.test_a_junction_is_refused_as_a_symlink_is` | junctions are Windows' |
