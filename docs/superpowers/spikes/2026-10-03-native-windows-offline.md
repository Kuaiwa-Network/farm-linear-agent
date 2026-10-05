# Native Windows offline verification, 2026-10-03

The exact merged candidate `707ea87ce0e6819dff873d0928e81d6220c01e02`
passed the native offline suite after the operator authorized an elevated test
process: **1,725 tests, 1,656 passed, zero failures or errors, 69 platform skips**.
All thirteen feature journeys and all seven native Windows Job Object tests
ran and passed. The initial non-elevated diagnostic run and its symlink errors
are retained below, followed by the measured elevated rerun.

Both runs used isolated development checkouts outside the running installation
on the **production Windows host**. A read-only query observed the
`FarmBot-Receiver` scheduled task as Running; neither its configuration nor
its runtime state was read or copied. No Mac runtime state was copied.
The passing offline baseline does not establish full production readiness:
the feature-toolchain inventory found gaps, and real worker sandbox, credential
store and desktop acceptance checks remain. No application change or new skip
was made. The running service, credentials, live issues, parked jobs and
production resources were not changed.

## Environment and command

- Windows 11, build 26200, x64.
- CPython 3.13.16, x64, MSC v.1944. Python 3.13 was initially absent; the
  installed Python manager extracted an unregistered runtime into the ignored
  verification directory. The existing registered Python 3.14 was not used.
- Git `2.54.0.windows.1`; Git LFS `3.7.1` (windows amd64, Go 1.25.1).
- `PYTHONUTF8=1` and `PYTHONUNBUFFERED=1` were set before Python started.
- The pinned workflow's sanitization removed inherited `FARMBOT_*`,
  `FAKE_CLI_*`, `GH_TOKEN`, `GITHUB_TOKEN`, `GH_ENTERPRISE_TOKEN` and
  `GITHUB_ENTERPRISE_TOKEN` before discovery and child processes started.
- The test worktree was clean at the candidate revision and held no production
  config. Fixtures supplied temporary state, local Git remotes and fake
  worker/Linear/Unity integrations. Native execution ran outside the Codex
  sandbox. The initial test token was not elevated and had no symlink privilege;
  the follow-up test process used an elevated token after operator approval.

The selected interpreter executed this command from the repository root:

```powershell
python -B -m unittest discover -s tests -v
```

Initial directory symlink preflight failed with `OSError`, Windows error 1314, both
inside and outside the sandbox. The Developer Mode flag was absent. This fails
[CI's dependency gate](../../ci.md); the operator-requested full run proceeded
as diagnostic evidence with every test unchanged. Windows settings were not
modified.

## Initial non-elevated measured result

- **1,725 tests: 1,645 passed, 0 assertion failures, 6 errors, 74 skips.**
- Unittest duration: **859.699 seconds**;
  discovery and subprocess wall time: **861.688 seconds**.
- All **13 feature journeys** and all **seven native Windows Job Object tests**
  actually ran and passed; their IDs are listed below.
- The 74 skips consist of 69 existing platform skips and five additional
  capability skips from missing symlink privilege. Every skipped ID and reason
  is listed below; private temporary paths in one reason are omitted here and
  retained in the local log.
- Each of the six errors was reproduced on the same candidate by a focused
  `python -B -m unittest -v <the six IDs below>` invocation with the fixture
  module directory on `PYTHONPATH`: six tests in **0.326 seconds**, six errors,
  zero failures or skips (0.627 seconds subprocess wall time).
  Each traceback ends at `pathlib.symlink_to` / `os.symlink` with Windows error
  1314, before the affected ownership check can exercise its linked fixture.
  This is missing host capability, not evidence of an application regression;
  those ownership checks remain unverified on this token.

The complete UTF-8 log, preflight/host capability metadata, revision and version
metadata, per-test outcomes, skip reasons, and focused logs remain under the
ignored local `reports/native-windows-707ea87/` directory. They are not published
because raw tracebacks contain private host and temporary paths. The full log's
SHA-256 is `e289ee5d3d6a90d7a7e433c229c852671a1d4abd68994e5095e545c53282332d`.

The full log also contains `ConnectionAbortedError: WinError 10053` from the
test fixture `tests/test_monitor.py`'s fake receiver writing a response after
the loopback client closes its connection. Its test passed. A focused monitor
rerun reproduced the fixture traceback while **all 54 tests passed in 53.359
seconds**. That rerun finished on documentation head `cf9ecfa`, whose executable
tree is unchanged from the candidate; its log and metadata are retained too.
No exception from the production monitor was observed or inferred from this
fixture's traceback.

## Elevated follow-up: passing native offline baseline

The operator authorized an Administrator test process. A fresh local clone of
the same exact candidate was checked out detached, with its own Git metadata,
no production config and no copied runtime state. `PYTHONUTF8=1` and the same
workflow environment sanitization were applied before Python started.
The test process's elevated token passed the real directory-symlink preflight.
No Developer Mode setting, account privilege, service or credential was changed.

- Focused recheck: the six errored IDs and five capability-skipped IDs below
  all ran and passed: **11 tests in 3.639 seconds**, zero failures,
  errors or skips (4.040 seconds subprocess wall time).
- Fresh full command: `python -B -m unittest discover -s tests -v`, with the
  selected CPython 3.13.16 executable: **1,725 tests, 1,656 passed, zero failures
  or errors, 69 skips**.
- Unittest duration: **906.705 seconds**; discovery and subprocess wall
  time: **907.306 seconds**. Started `2026-10-03T04:03:35.169336+00:00` and finished
  `2026-10-03T04:18:42.518486+00:00`.
- All **13 feature journeys** and all **seven native Windows Job Object tests**
  ran and passed again, with exactly the IDs listed below.
- The 69 skipped IDs and their reasons exactly match the initial run's platform
  entries in the inventory below. The five entries under `Windows symlink
  privilege unavailable`, `creating symlinks needs a privilege on this host`
  and `symlinks unavailable` all passed in this run. The remaining skips are
  existing platform exclusions; none was added or changed.
- Windows, Python, Git and LFS versions match the initial environment above.
  No application regression was found, so no code-fix branch was needed.

UTF-8 logs, per-test results, focused evidence, revision/version metadata and
the symlink preflight are retained locally under the ignored
`reports/native-windows-707ea87-elevated/` directory. Raw logs contain private
host paths and are not published. The full log's SHA-256 is
`aed3d50af54660d8226f65314a22a616d4c09855aa64e4665ea69f3d2ba455eb`.

## Initial errors reproduced in focused testing

All six share `OSError: WinError 1314` while creating a directory symlink:

- `test_environment.EnvironmentTests.test_state_symlink_and_external_slot_are_rejected`
- `test_memory.MemorySnapshotTests.test_invalid_stored_ids_and_symlink_root_are_refused`
- `test_memory.MemorySnapshotTests.test_prune_preserves_references_and_ignores_unrelated_paths`
- `test_memory.MemorySnapshotTests.test_symlinked_retained_attempt_aborts_pruning_before_deletion`
- `test_memory.MemorySnapshotTests.test_symlinked_retained_item_aborts_pruning_before_deletion`
- `test_worktrees.WorktreeTests.test_verification_rejects_foreign_checkout_and_missing_worktree`

## Feature journeys: all passed

- `test_feature_journey.CodeJobJourneyTests.test_a_budget_kill_fails_the_job_and_retry_restarts_it_at_the_initial_root`
- `test_feature_journey.CodeJobJourneyTests.test_a_comment_during_an_attempt_is_read_and_revalidated_before_the_handoff`
- `test_feature_journey.CodeJobJourneyTests.test_a_restart_while_the_job_waits_for_config_resumes_it_where_it_was`
- `test_feature_journey.CodeJobJourneyTests.test_a_squashed_contract_merge_is_re_synced_from_the_read_only_main_checkout`
- `test_feature_journey.CodeJobJourneyTests.test_a_stage_without_work_is_skipped_and_said_so`
- `test_feature_journey.CodeJobJourneyTests.test_a_successor_selects_the_recorded_followup_before_writing_the_remaining_pin`
- `test_feature_journey.CodeJobJourneyTests.test_a_worker_saves_its_checkpoint_then_withdraws_after_confirmed_delegation_removal`
- `test_feature_journey.CodeJobJourneyTests.test_an_enqueued_code_job_pins_no_target_and_may_not_take_a_unity_slot`
- `test_feature_journey.CodeJobJourneyTests.test_config_only_work_closes_without_a_hive_branch_or_published_pin`
- `test_feature_journey.CodeJobJourneyTests.test_one_code_job_goes_from_the_contract_through_the_server_to_its_closing_steps`
- `test_feature_journey.CodeJobJourneyTests.test_removing_the_delegation_cancels_the_parked_job_and_keeps_its_branches`
- `test_feature_journey.CodeJobJourneyTests.test_stop_then_a_continuation_reattaches_the_successor_to_the_recorded_branches`
- `test_feature_journey.CodeJobJourneyTests.test_two_code_jobs_take_turns_while_a_fix_runs_beside_them`

## Native Job Object tests: all passed

- `test_windows_workers.WindowsWorkerTests.test_assignment_failure_never_runs_requested_command`
- `test_windows_workers.WindowsWorkerTests.test_later_contained_attempt_cannot_certify_legacy_attempt_with_same_pid`
- `test_windows_workers.WindowsWorkerTests.test_normal_exit_is_proven_quiescent`
- `test_windows_workers.WindowsWorkerTests.test_receiver_crash_terminates_its_worker_job`
- `test_windows_workers.WindowsWorkerTests.test_simultaneous_stop_and_poll_finalize_job_once`
- `test_windows_workers.WindowsWorkerTests.test_spontaneous_exit_reaps_child_and_preserves_unrelated_process`
- `test_windows_workers.WindowsWorkerTests.test_stop_is_proven_quiescent`

## Initial platform and capability skip inventory

This lists all 74 initial skips. The elevated run retained exactly the 69
platform entries; its five resolved capability skips are identified above.

### Windows worker jobs contain children; tested in test_windows_workers (1)

- `test_cleanup.CancellationCleanupTests.test_exited_parent_with_detached_child_holds_cleanup_after_restart`

### Windows symlink privilege unavailable (1)

- `test_cleanup.PreservationTests.test_symlinked_worktree_is_never_followed`

### POSIX self-exit evidence; Windows workers are proved by Job Objects (4)

- `test_cleanup.SelfExitedWorkerCleanupTests.test_continuation_of_a_self_exited_paused_worker_launches`
- `test_cleanup.SelfExitedWorkerCleanupTests.test_resource_fencing_certifies_a_worker_that_exited_by_itself`
- `test_cleanup.SelfExitedWorkerCleanupTests.test_resource_fencing_fails_while_the_old_handle_is_still_registered`
- `test_cleanup.SelfExitedWorkerCleanupTests.test_retirement_leaves_the_reap_of_a_registered_worker_to_poll`

### Windows refuses every lark_cli home (1)

- `test_config.LarkCliConfigTests.test_a_home_lies_outside_local_root_the_users_home_and_every_temporary_directory`

### the macOS store's master key file (1)

- `test_doctor.FeatureToolchainTests.test_a_store_whose_key_is_a_file_and_that_holds_a_login_is_exposed`

### POSIX process inspection (3)

- `test_doctor.ProcessProbeTests.test_invalid_pids_are_never_passed_to_os_kill`
- `test_doctor.ProcessProbeTests.test_permission_denied_and_ps_failure_are_unknown_not_dead`
- `test_doctor.ProcessProbeTests.test_real_worker_is_recognized_and_reaped_worker_is_dead`

### FIFOs and O_NOFOLLOW symlink refusal are POSIX (1)

- `test_heartbeat.HeartbeatFileTests.test_a_fifo_or_symlink_is_unreadable_without_waiting`

### FIFOs are POSIX (1)

- `test_heartbeat.HeartbeatFileTests.test_write_replaces_a_symlink_or_fifo_instead_of_opening_it`

### FIFOs in a directory are POSIX (12)

- `test_launcher.LauncherTests.test_a_fifo_the_worker_left_as_a_report_file_cannot_block_poll`
- `test_launcher.LauncherTests.test_a_kill_after_a_restart_persists_its_targets_past_a_fifo_left_for_their_temporary_file`
- `test_launcher.LauncherTests.test_a_kill_after_a_restart_refuses_a_fifo_teardown_record_before_signalling`
- `test_launcher.LauncherTests.test_a_stop_replaces_a_fifo_the_live_worker_swapped_in_for_its_teardown_record`
- `test_launcher.LauncherTests.test_cleanup_checks_refuse_a_fifo_launch_or_teardown_record_without_blocking`
- `test_launcher.LauncherTests.test_spawn_notes_an_undelivered_prompt_past_a_fifo_the_worker_left_for_the_note`
- `test_launcher.LauncherTests.test_spawn_records_the_pid_past_a_fifo_the_new_worker_put_at_its_launch_record`
- `test_launcher.LauncherTests.test_stop_does_not_block_on_a_fifo_teardown_record_and_holds_cleanup`
- `test_launcher.WorkerFileReadTests.test_a_fifo_is_refused_without_waiting_for_a_writer`
- `test_launcher.WorkerFileWriteTests.test_a_fifo_at_the_name_is_replaced_without_waiting_for_a_reader`
- `test_scheduler.SchedulerTests.test_a_fifo_left_for_the_batch_summary_is_no_evidence_and_cannot_stall_the_launch`
- `test_slots.PoolTests.test_a_fifo_left_for_the_batch_summary_cannot_stall_the_batch_run`

### POSIX process group semantics (1)

- `test_launcher.LauncherTests.test_group_kill_escalates_when_only_the_child_ignores_term`

### POSIX sessions; Windows workers are proved by Job Objects in test_windows_workers.py (25)

- `test_launcher.PosixSelfExitTeardownTests.test_a_claimed_worker_is_not_reaped_by_poll_even_without_waitid`
- `test_launcher.PosixSelfExitTeardownTests.test_a_failing_teardown_check_still_reports_every_exit`
- `test_launcher.PosixSelfExitTeardownTests.test_a_fifo_teardown_record_holds_cleanup_without_blocking_the_poll`
- `test_launcher.PosixSelfExitTeardownTests.test_a_fifo_that_appears_at_the_teardown_record_is_replaced_by_the_verified_one`
- `test_launcher.PosixSelfExitTeardownTests.test_a_group_still_reported_after_the_reap_holds_cleanup`
- `test_launcher.PosixSelfExitTeardownTests.test_a_member_that_cannot_be_terminated_holds_cleanup`
- `test_launcher.PosixSelfExitTeardownTests.test_a_process_table_that_stays_unreadable_holds_cleanup`
- `test_launcher.PosixSelfExitTeardownTests.test_a_reap_that_cannot_complete_is_retried`
- `test_launcher.PosixSelfExitTeardownTests.test_a_repeated_stop_keeps_what_an_earlier_stop_recorded`
- `test_launcher.PosixSelfExitTeardownTests.test_a_signalled_member_that_leaves_the_session_is_still_checked`
- `test_launcher.PosixSelfExitTeardownTests.test_a_teardown_record_naming_another_pid_holds_cleanup`
- `test_launcher.PosixSelfExitTeardownTests.test_a_verified_self_exit_keeps_an_interrupted_stops_recorded_descendants`
- `test_launcher.PosixSelfExitTeardownTests.test_an_unreadable_process_table_is_retried_while_the_worker_stays_unreaped`
- `test_launcher.PosixSelfExitTeardownTests.test_an_unverified_self_exit_supersedes_an_interrupted_stop_record`
- `test_launcher.PosixSelfExitTeardownTests.test_normal_exit_is_proven_quiescent`
- `test_launcher.PosixSelfExitTeardownTests.test_other_group_in_the_worker_session_is_terminated_and_recorded`
- `test_launcher.PosixSelfExitTeardownTests.test_poll_leaves_a_worker_being_killed_to_kill`
- `test_launcher.PosixSelfExitTeardownTests.test_same_group_survivor_is_terminated_and_recorded`
- `test_launcher.PosixSelfExitTeardownTests.test_setsid_descendant_escapes_the_session_check`
- `test_launcher.PosixSelfExitTeardownTests.test_stop_after_a_self_exit_leaves_the_reap_and_its_evidence_to_poll`
- `test_launcher.PosixSelfExitTeardownTests.test_stop_racing_a_self_exit_signals_the_group_even_without_waitid`
- `test_launcher.PosixSelfExitTeardownTests.test_stop_racing_a_self_exit_still_reaches_the_worker_group`
- `test_launcher.PosixSelfExitTeardownTests.test_stop_terminates_a_group_member_the_parent_walk_cannot_see`
- `test_launcher.PosixSelfExitTeardownTests.test_what_the_worker_left_for_the_unverified_record_is_replaced_not_opened`
- `test_launcher.PosixSelfExitTeardownTests.test_without_waitid_a_self_exit_keeps_holding_cleanup`

### POSIX sessions and process groups (8)

- `test_launcher.PosixSessionScanTests.test_a_failed_ps_exit_proves_nothing`
- `test_launcher.PosixSessionScanTests.test_a_member_that_exits_before_getsid_is_skipped`
- `test_launcher.PosixSessionScanTests.test_a_session_that_cannot_be_read_proves_nothing`
- `test_launcher.PosixSessionScanTests.test_a_table_that_does_not_list_farmbot_itself_proves_nothing`
- `test_launcher.PosixSessionScanTests.test_group_and_session_members_are_listed_but_not_the_leader_or_zombies`
- `test_launcher.PosixSessionScanTests.test_ps_that_fails_or_times_out_proves_nothing`
- `test_launcher.PosixSessionScanTests.test_signals_reach_the_group_and_only_members_still_in_the_session`
- `test_launcher.PosixSessionScanTests.test_the_group_is_gone_only_when_the_kernel_says_so`

### Windows has no O_NOFOLLOW; making a symlink there needs a privilege (1)

- `test_launcher.WorkerFileReadTests.test_a_symlink_is_refused_even_to_a_regular_file`

### making a symlink on Windows needs a privilege (1)

- `test_launcher.WorkerFileWriteTests.test_a_symlink_at_the_name_is_replaced_and_what_it_names_is_left_alone`

### POSIX service termination contract (2)

- `test_service.SignalShutdownTests.test_sigterm_during_pool_ensure_also_cleans_up_and_restores_the_handler`
- `test_service.SignalShutdownTests.test_sigterm_reaps_the_batch_child_and_closes_a_running_service`

### no FIFOs in a directory, and making a symlink needs a privilege (1)

- `test_slots.PoolTests.test_what_the_worker_left_at_the_token_path_is_replaced_by_the_grant`

### creating symlinks needs a privilege on this host (3)

- `test_uploads.ArchiveTests.test_a_link_on_the_way_to_a_member_is_never_descended`
- `test_uploads.DownloadTests.test_an_edited_manifest_cannot_lead_a_write_through_a_link_inside_the_directory`
- `test_uploads.OutputDirectoryTests.test_a_symlinked_out_is_refused`

### the filter here is a shell script (1)

- `test_worktrees.ControllerGitTests.test_farmbots_git_in_a_worktree_follows_neither_of_its_pointers`

### the hooks here are shell scripts (1)

- `test_worktrees.ControllerGitTests.test_farmbots_own_git_runs_no_hook_left_in_the_clone`

### macOS's Seatbelt, as Codex's (1)

- `test_worktrees.ControllerGitTests.test_within_those_parts_a_worker_commits_and_pushes_but_cannot_touch_the_config`

### symlinks unavailable: WinError 1314 (private temporary paths omitted) (1)

- `test_worktrees.ReadCheckoutTests.test_anything_else_at_the_path_is_replaced_and_unsafe_requests_are_refused`

### the hooks, fsmonitor and filters here are shell scripts (1)

- `test_worktrees.ReadCheckoutTests.test_no_hook_fsmonitor_filter_or_setting_of_the_host_or_the_clone_runs`

### POSIX permissions; Windows has the junction test (1)

- `test_worktrees.ReadCheckoutTests.test_removal_never_acts_through_a_link`

### the planted hooks and fsmonitor are shell scripts (1)

- `test_worktrees.ReattachTests.test_no_hook_or_fsmonitor_planted_in_the_clone_runs_while_re_attaching`

## Read-only Windows feature-toolchain inventory

After operator approval, the selected Python ran
`python -B -m agent.service doctor --config <scratch config>` from the exact
candidate. The temporary config lived outside every checkout, used only
`doctor-only` Linear values, an `offline` profile, Codex runtime, an empty
absolute state root, enabled `chat`, `fix` and `feature`, and proposed lark-cli
profile `farmbot` with no Windows `home`. No production config or ledger was
read, no Linear or Feishu request ran, and no credential setup was performed.
Doctor's probes disable update checks, downloads and telemetry. This inventory
describes the current interactive host account's PATH, not an unmeasured future
FarmBot service account or worker environment.

The first inventory completed in **4.999 seconds**, exit **2** (`incomplete`),
with `feature_toolchain_incomplete` and the expected `ledger_unreadable` for
the empty root. Feature was loaded and enabled only in this dummy config; no
dummy value appeared in the output, the state root remained empty, and the
scratch directory was removed afterwards. Go's required version uses the
default directive because no farm-hive clone was present.

After [#79](https://github.com/Kuaiwa-Network/farm-linear-agent/pull/79) merged
as `03c9833`, the operator explicitly requested this read-only check again.
The repeat started at `2026-10-03T06:24:49.151308+00:00` and completed in
**4.826 seconds**, using the same clean `707ea87` development clone and selected
Python 3.13.16. The merged record changes only documentation; it does not change
the candidate's executable tree. A fresh temporary config supplied the values
above, with no `lark_cli.home`, and the new scratch state was empty before and
after doctor. The ordinary, non-elevated host token was used; `PYTHONUTF8=1`
and the workflow's selector/token sanitization were applied before Python.
Doctor again exited **2** with `ledger_unreadable` and
`feature_toolchain_incomplete`. The expected empty-ledger finding is separate
from the measured toolchain gaps. The scratch directory was removed afterwards.
No production config or ledger was read, service started, authentication run,
account configured, credential installed or app setting changed.

Every `tools.feature` entry, version and missing-tool classification exactly
matches the first inventory. The table below therefore describes both runs.
The new full UTF-8 doctor report, sanitized inventory and raw bash lookup are
retained privately under the ignored
`reports/native-windows-toolchain-20261003-recheck/` directory. The full report's
SHA-256 is
`07fb1d0b8a67abb3a93259dd9df549eea2ca09bd55ff2990c037a59f065deef8`.

| Tool | Observed | Requirement / result |
| --- | --- | --- |
| Go | 1.24.9 | >=1.25.1; gap |
| protoc | not on PATH | 35.1; gap |
| buf | 1.73.0 | 1.72.0; gap |
| Node | 24.19.0 | >=22; passes version probe |
| openspec | not on PATH | 1.7.0; gap |
| python3 on PATH | 3.14.3 | readable version; passes version probe |
| Git LFS | 3.7.1 | readable version; passes version probe |
| bash version probe | 5.2.21 | version readable; WSL launcher first |
| sha256sum | 8.32 | readable version; passes version probe |
| mktemp | 8.32 | readable version; passes version probe |
| awk | 5.1.0 | readable version; passes version probe |
| dotnet SDK | 9.0.306 | 8.0.423; optional gap |
| lark-cli | not on PATH | unavailable; farmbot profile presence unverified |

Both `where.exe bash` and Python's actual `shutil.which("bash")` resolution put
the WSL launcher first; Git for Windows bash therefore does not precede it on
this PATH. MSYS2 is also present later in the lookup. A readable bash version
alone does not prove the native shell and generator environment the Windows
worker needs. The
`python3` alias resolves to Python 3.14.3; the offline suite explicitly used
Python 3.13.16. No generator, contract gate, Go build or real worker sandbox
acceptance was run by these version probes. The lark-cli executable was absent,
so its profile/store could not be inspected or certified. The private doctor
report, sanitized tool inventory, raw bash lookup and cleanup evidence are
retained with the elevated-run evidence.

The original documentation update passed all 73 skill/reference tests in 0.404
seconds; this follow-up passed the same 73 tests in 0.513 seconds, with no skips.
Current verification-section links, the Task 17 anchor, whitespace and
`git diff --check` passed. The record was checked against every initial error
and skip, all required journey/Job Object IDs and all 69 final skip reasons;
the measured duration and full-log hash were verified against local evidence.

## Development toolchain preparation, 2026-10-03

The operator requested the release prerequisites one at a time. The required
tool executables were prepared under the ignored development-only
`.local/verification/toolchain/` prefix. Go 1.26.6 matches the verified Mac
toolchain and exceeds farm-hive's current `go 1.25.1` directive, read at
[`e24b6cb`](https://github.com/Kuaiwa-Network/farm-hive/blob/e24b6cbc4403e19c49bf13cc7766deaa49db5184/go.mod).
The official Windows Go, protoc, buf and lark-cli artifacts passed their
upstream SHA-256 checks before extraction. Openspec was installed at exact
1.7.0 into a separate npm prefix/cache with its published integrity and resolved
dependencies retained in the local lockfile; lifecycle scripts were not run.
Nothing was installed globally.

The development process alone prepended this prefix, the selected Python 3.13
runtime and the installed Git for Windows `usr/bin` to PATH. A `python3.exe`
copy has the same hash as the selected Python 3.13.16 executable and uses the
same runtime files. Bash provenance was checked using the Git installation's
MSYS runtime marker and `uname -s`: `MSYS_NT-10.0-26200`. Its banner says
`x86_64-pc-cygwin`, which alone would not establish whether it is Git's MSYS
bash. The actual selected executable belongs to that verified Git installation;
the WSL launcher is not selected in this process.

The fresh dummy-config doctor from exact `707ea87` started at
`2026-10-03T07:04:38.945537+00:00` and completed in **7.409 seconds**, exit **2**.
The empty ledger remains expected. Every required executable version now
passes; `feature_toolchain_incomplete` names only `lark_cli`, whose binary is
present but whose proposed `farmbot` profile is absent. This check refused to
inspect an existing personal lark config; the current account had none.
Read-only profile listing reported zero other profiles and zero user logins.
No profile, secret or authentication was created.

| Tool | Prepared process result |
| --- | --- |
| Go | 1.26.6; passes >=1.25.1 |
| protoc | 35.1; passes exact pin |
| buf | 1.72.0; passes exact pin |
| Node | 24.19.0; passes >=22 |
| openspec | 1.7.0; passes exact pin |
| python3 | 3.13.16; selected runtime |
| Git LFS | 3.7.1; version probe passes |
| bash | 5.3.9; verified Git for Windows MSYS |
| sha256sum / mktemp | 8.32; version probes pass |
| awk | 5.4.0; version probe passes |
| lark-cli | 1.0.82; binary available, farmbot profile absent |
| dotnet SDK | 9.0.306; optional 8.0.423 remains absent |

Both persistent user and machine PATH values were checked unchanged. The
scratch state remained empty and was removed; the existing lark-store state
was unchanged. No production configuration/ledger was read, service restarted,
account configured or feature enabled. The original unmodified-PATH inventory
above remains valid; these results certify only the prepared development
process, not the running service or its actual worker environment.

The new UTF-8 report and metadata are retained under ignored
`reports/native-windows-toolchain-prepared-20261003/`; its report SHA-256 is
`25749e3733ad8b11158bc2a29cdbe0dc5016747d9fb7e76d3108a3700ae12bb6`.
Download digests, npm integrity/lockfile and private executable-path metadata
are retained with the ignored toolchain prefix. Sources are the official
[Go download index](https://go.dev/dl/),
[buf v1.72.0 release](https://github.com/bufbuild/buf/releases/tag/v1.72.0),
[protoc v35.1 release](https://github.com/protocolbuffers/protobuf/releases/tag/v35.1),
[lark-cli v1.0.82 release](https://github.com/larksuite/cli/releases/tag/v1.0.82)
and [openspec 1.7.0 package](https://www.npmjs.com/package/@fission-ai/openspec/v/1.7.0).

The operator then chose the **current Windows account**, citing the FarmBot
Feishu app's read-only permissions. No new account is required for this chosen
path. Its lark-cli store must remain FarmBot-only, with no personal profiles or
user logins; the empty-store measurement above satisfies only the pre-setup
check. Read-only Feishu scopes do not isolate other credentials belonging to
the same Windows user, so this choice does not establish separate-account
isolation. Actual Windows worker access to the bot's DPAPI credential remains
unmeasured.

A private local setup helper was prepared for the existing read-only FarmBot
app. Its read-only preflight passed in Windows PowerShell: the verified 1.0.82
binary matched its release archive and the current account's config/profile
store remained empty. This preflight started at
`2026-10-03T07:28:18.8645313Z`; sanitized evidence is retained under ignored
`reports/native-windows-lark-local-*/`. A separate dummy-only subprocess fixture
passed in Windows PowerShell 5.1: Unicode secret stdin, paths with spaces,
credential/config/workspace-selector removal and disabled update/metadata
flags. It accessed no lark credential store. At that point the setup itself had
**not run**: it required the
operator's local app-ID input and hidden secret prompt. The helper passes the
secret on stdin, creates only a new `farmbot` profile, sets profile strict bot
mode and checks `doctor --offline`; it saves only sanitized check names/status.
It refuses to read or replace an existing config and performs no network
authentication, personal login, app-scope change or production reconfiguration.
This preparation is not evidence of a configured profile or working Feishu
access.

The account-choice documentation passed all 73 skill/reference checks in
**0.346 seconds**, with no skips. Link, whitespace, private-path/credential
pattern and retained-evidence checks passed. The existing native suite's error,
skip and required-test inventories remain unchanged; no application fix or
full-suite rerun was needed for these documentation edits.

## Local bot profile verification, 2026-10-03

The operator completed the new `farmbot` profile under the chosen current
Windows account, with no other profiles or user logins and profile strict bot
mode. The app ID and hidden secret were entered locally. Offline profile
checks passed; endpoint checks were skipped. This setup performed no network
authentication, Feishu app-scope change or production reconfiguration.

The initial interactive helper launch inherited an incompatible PowerShell
module path, hiding `Get-FileHash`. Selecting the helper engine's built-in
modules fixed it; the original launched preflight then passed both unchanged
release hash checks. No Windows account was created.

A fresh dummy-config FarmBot doctor from exact `707ea87`, using the prepared
process-only tool prefix, started at `2026-10-03T08:10:03.537683+00:00` and
completed in **0.854 seconds**, exit **2**. `tools.feature.missing` is now **[]**:
all required versions and the `farmbot` profile pass, with zero other profiles
and zero user logins. The sole finding is the expected `ledger_unreadable` for
the empty scratch root. Optional dotnet SDK 8.0.423 remains absent. Persistent
PATH and the lark config were unchanged; the scratch state stayed empty and was
removed. UTF-8 evidence is retained under ignored
`reports/native-windows-toolchain-configured-20261003/`; report SHA-256:
`bb76ebcf1c4c475572e13bf70b30a6832e2e7dec5374e118eb41e5b058851dfd`.

These host-side checks do not certify worker access or real Feishu reads.
Additional worker compatibility checks identified a readiness gap; detailed
credential diagnostics are retained privately. A FarmBot model worker and its
isolated home have not been verified. Worker credential delivery remains a
release prerequisite. At this check's exact candidate `707ea87`, the feature-only
environment-credential variant is unimplemented; the subsequently authorized
development follow-up below records its implementation in a separate tested draft.
Authority review remains pending before live use. Windows worker generators and
full worker acceptance remain pending.

Validation of this documentation passed all **73** skill/reference tests under
the ordinary owner token in **0.349 seconds**, with no skips. An earlier run
inside the desktop sandbox recorded 33 temporary-fixture access errors
(WinError 5); its log is preserved, and the same tests passed outside that
restriction without code changes or added skips. Link/whitespace/privacy and
measured-evidence checks passed; the full native suite was not rerun for this
documentation-only update.

## Feature credential development follow-up (2026-10-03)

The operator authorized the planned feature-only environment variant as a separate
development change. Draft [FarmBot #81](https://github.com/Kuaiwa-Network/farm-linear-agent/pull/81)
implements `lark_cli.app_id`/`secret_env`, with forced bot mode only for Codex feature
workers and withholding for other workers, Unity and diagnostics. Native Windows
isolated Codex homes explicitly require the elevated sandbox. This code is not
merged or deployed; the exact merged candidate above remains `707ea87`.

On the **production Windows host, in the separate development checkout**, source
`9e7d75e586d98c317cd40429e727f24bbc250d41` passed the full offline command
`python -B -m unittest discover -s tests -v`, using the explicit Python 3.13.16
executable, `PYTHONUTF8=1`, sanitized inherited selectors and the symlink-capable
elevated test token. Result: **1,741 tests in 774.396 seconds; 1,672 passes,
zero failures/errors, 69 skips**. All thirteen feature journeys and all seven
native Job Object tests ran and passed. The skip IDs and reasons exactly match
the 69-case elevated baseline inventory above; no skips were added. Wall time
was 775.262 seconds. Git remained 2.54.0.windows.1 and Git LFS 3.7.1.

The 430 related focused tests also passed with 54 existing skips under the
ordinary token. These include its existing symlink capability skips; the full
run used the verified symlink-capable token. A dummy credential test with an
empty lark store passed both directly and through the existing elevated Codex
0.156.1 sandbox helper: bot dry run exit 0, user exit 2, no credentials exit 3.
The new doctor mode, using only dummy values and a fresh empty state root,
completed in 0.832 seconds, exit 2, with `tools.feature.missing=[]` and only the
expected `ledger_unreadable`. It read no profile store and the scratch state
remained empty. These checks performed no real Feishu authentication and do not
certify an actual model worker or its isolated home.

UTF-8 logs, revision, versions, full per-test result/skip inventories and timing
remain in ignored `reports/native-windows-feature-credentials-full/`; the full
log SHA-256 is
`1e9bcc3ef90d2e5a0d70348309df7371c942e9358124788fe082305ea5f2f0e2`.
Dummy probe and doctor evidence is retained separately. Link, whitespace and
documentation checks accompany this documentation-only follow-up. Detailed
host credential diagnostics remain private.

## Isolated Windows worker follow-up (2026-10-03; partial)

[#81](https://github.com/Kuaiwa-Network/farm-linear-agent/pull/81) merged as
`e8406d547c2663703f34b007f79f306cce35b362`. Its executable and test trees match
the measured `9e7d75e` source above. The
[merge-head CI](https://github.com/Kuaiwa-Network/farm-linear-agent/actions/runs/37115429575)
completed successfully; the earlier tested source's CI also passed.

On this production Windows host, in the separate development checkout at the
exact merged revision, an offline probe used `Launcher` to create its isolated
Codex home, environment grant and native Job Object. It substituted the real
Codex sandbox CLI for model execution, with dummy credentials and no seeded
model authentication. This checks sandbox initialization before a model worker
is attempted; it is not a full model-worker acceptance run.

The attempt started at `2026-10-03T10:15:36.058735+00:00` and stopped in
**0.329 seconds**, exit **1**, during sandbox initialization. Codex CLI 0.156.1
refused helper binaries beneath the Windows temporary directory. The generated
config selected `workspace-write` and native `windows.sandbox="elevated"`,
disabled shell snapshots and excluded the configured source alias. No credential
values appeared in that config. No sandbox child or feature tool ran, so actual
credential delivery and sandbox-child membership of the FarmBot Job Object
remain unmeasured. The parent job was recorded and settled empty.

UTF-8 logs, revision, configuration checks, duration and private scratch-location
metadata are retained under ignored
`reports/isolated-windows-worker-20261003T101536Z/`. The stderr SHA-256 is
`39133df019c3744501607c3bd219aca4b8a191c63e688ad46216c68b08081ea9`.
Scratch evidence was preserved. The next scoped check uses a stable absolute
verification root outside the checkout, followed by its native elevated sandbox
initialization. This result does not justify a sandbox downgrade. No production
FarmBot config or ledger was read, service started, live issue changed, credential
installed or Feishu call made. Windows worker generators and real bot reads
remain pending.

### Authorized native sandbox setup (2026-10-03; incomplete)

After the operator authorized verification-only native elevated sandbox setup,
the next probe used a new stable absolute scratch root outside both the checkout
and Windows temporary directory. It ran from documentation head
`2bb93ab44c6abe770de8f0535ac4788f17910f7d`; the executable and test trees were
unchanged from merged candidate `e8406d5`. This remains an isolated development
check on the **production Windows host**.

The stable-root probe started at `2026-10-03T10:32:42.114577+00:00` and stopped in
**0.279 seconds**, exit **1**, with
`orchestrator_helper_exit_nonzero: setup helper exited with status Some(1)`.
The generated config still required the elevated native sandbox, disabled shell
snapshots and excluded the source alias and credential values. No model
authentication was seeded. No sandbox child or feature tool ran; the parent
FarmBot Job Object was recorded and settled empty.

The supported [setup RPC](https://learn.chatgpt.com/docs/app-server)
was then called through a short-lived stdio app-server, without creating a
thread or model turn. The completion notification reported `success=false`
with the same helper-exit failure. Retrying Codex CLI **0.156.1** under a
verified administrator token with its matching release helpers first in the
process-only `PATH` also failed, in **0.181 seconds**. The app-server process
exited 0, which does **not** mean sandbox setup succeeded. A comparison with the
existing native CLI **0.160.0** under an administrator token failed in
**0.094 seconds** as well; the installed CLI and persistent `PATH` were not
replaced.

The official npm platform archive for 0.156.1 passed its published SHA-512
integrity check. Its CLI, sandbox setup helper, command runner and code-mode
host all matched the installed bytes. The original `PATH` selected a different
setup helper, but putting the verified matching helper directory first did not
resolve the failure. These initial checks did not establish a cause; the
read-only diagnosis below subsequently identified the ownership guard. The
results do not establish a FarmBot application regression. No containment
check was weakened, fallback sandbox selected or skip added.

A bounded read-only query of Defender, Code Integrity, AppLocker and Application
events found no entries naming the Codex executables during these attempts.
This absence does not establish that account, firewall or logon policy permits
setup; at this stage the generic helper failure had no more specific measured
cause.

UTF-8 logs, sanitized setup summaries, revision, timing, corrected component
integrity comparison and private scratch-location metadata are retained under
ignored `reports/isolated-windows-worker-20261003T103242Z/`. Stable-root probe
stderr SHA-256:
`984ed1deff49a060d8d2b393f1af6107e1021f2cdad76ad5bf575c277c007682`.
Scratch evidence was preserved; no automatic profile, credential or state
cleanup was attempted. No production FarmBot configuration or ledger was read,
FarmBot service started, live issue changed or Feishu call made.

The next step at this stage was native Codex setup diagnosis using the isolated
home's sandbox log and this measured failure. The
[official Windows troubleshooting guidance](https://learn.chatgpt.com/docs/windows/windows-sandbox)
identifies local user/group creation, firewall setup and sandbox-user logon
rights as checks when elevated setup fails; this result does not identify
which, if any, applies here. Prepare sanitized diagnostics for operator review
before sharing them; exclude sandbox secrets and private host paths. Actual
sandbox-child Job Object membership, credential delivery, Windows worker
generators and real Feishu reads remain pending.

### Read-only ownership diagnosis (2026-10-03)

Further inspection of the isolated home's daily sandbox log identified the
underlying helper failure, including the last administrator-token attempt:
`registered Core owns these sandbox accounts; helper provisioning is not permitted`.
This is an intentional ownership refusal before legacy account provisioning,
not an unexplained UAC or executable-integrity failure. Read-only Codex sandbox
installation metadata confirmed that a registered runtime exists and its
registered home differs from the fresh verification home. No identifiers,
registered-home paths or secrets are included here.

The upstream 0.156.1 setup helper enforces this refusal before writing the
structured `setup_error.json`; its top-level error is written to the daily log.
That explains why the RPC reported only helper exit 1. Upstream registered
service admission also checks the exact owner and Codex home, and authenticates
the installed package and caller image. The matching-home guard remains in
0.160.0. Merely changing a routing flag is therefore not an established
workaround for a fresh FarmBot home. This service-route conclusion comes from
source inspection and the measured home mismatch; the existing desktop home
was not used for another setup attempt.

Sanitized cause evidence is retained in the same ignored report directory as
`ownership-cause-summary.json`, along with upstream source provenance and the
local diagnostic report. The isolated daily log SHA-256 is
`a8a1669ed5364427ffc986073e666492c2733855183935bc5176b5d777f7689e`.
No sandbox account, ownership record, app setting, service or credential was
changed during this diagnosis.

The next concrete development step is to establish a supported Codex launch
integration that preserves FarmBot's per-attempt configuration isolation while
using registered native sandbox ownership, or verify the existing isolated-home
launcher in a separate native Windows environment without competing registered
ownership. Results from another environment would not certify this production
host. Preserve the ownership guard; do not copy sandbox secrets, relabel the
installation record, reuse the desktop home implicitly or downgrade containment.

### Native MXC evaluation (2026-10-03; partial)

[#82](https://github.com/Kuaiwa-Network/farm-linear-agent/pull/82) merged as
`c90deb4a3e3d997cee9ba2c660fd223a11d61065`; its
[head CI](https://github.com/Kuaiwa-Network/farm-linear-agent/actions/runs/37121682789)
passed. The following probes used that documentation revision, with executable
and test trees still identical to `e8406d5`, in the separate development
checkout on the **production Windows host**.

Codex documents a native `mxc` backend in its
[configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).
Its upstream implementation uses a process security environment without legacy
sandbox-account setup. This evaluation selected it through a probe-only CLI
override; FarmBot's generated Windows setting remains `elevated`. No production
config, registered ownership, sandbox account or persistent app setting changed.
Every attempt used a fresh isolated home, no seeded model authentication, dummy
feature credentials, `PYTHONUTF8=1` and a sanitized environment. No model or live
API was called.

The native child now starts. With an ASCII working directory in a stable scratch
root outside the checkout, explicit narrow workspace/state write grants still
failed: CLI **0.156.1** exited **1** in **2.315 seconds**, and CLI **0.160.0**
exited **1** in **2.368 seconds**. The child was observed inside FarmBot's Job
Object, its source alias and user/auth-proxy tokens were withheld, its dummy
canonical credentials and strict bot mode were delivered, and the Job Object
settled empty. Earlier Unicode-path attempts failed at the same write boundary.

A 0.160.0 minimal-read-policy probe denied relative and absolute creation,
modification of an owned seeded file, and directory creation in the granted
working directory; directory creation returned **WinError 5**. Its forbidden
sibling write was also denied. A read-only `config/read` followed by standalone
`command/exec`, without a thread or turn, confirmed the selected `mxc` backend,
named profile and exact working-directory/state write grants. The parent could
write the same working directory, while both named-profile and equivalent legacy
workspace-policy executions denied it. This RPC completed in **0.424 seconds**;
the diagnostic program's exit 0 records completion, not successful write access.
A comparison launched through the Windows consent broker under a verified
administrator token reproduced the denial in **2.274 seconds**.

Moving only the probe working directory to a fresh ignored scratch directory
inside the development checkout allowed its relative/absolute writes, seeded
file modification and directory creation; the forbidden sibling remained
blocked. The isolated home and attempt state stayed outside the checkout.
This location dependence prompted the independent PowerShell comparison below.
It did not justify relocating production state or broadening worker grants.

The completed **0.160.0** checkout-scratch probe took **5.307 seconds** and exited
**0**. Python/python3 **3.13.16**, Git **2.54.0.windows.1**, Git LFS **3.7.1**, Go
**1.26.6**, buf **1.72.0**, openspec **1.7.0** and lark-cli **1.0.82** version
probes passed after selecting each executable explicitly. Bash resolved to the
prepared **Git for Windows** executable, not WSL, but exited **0xC0000142**
([DLL initialization failure](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-erref/596a1078-e883-4972-9bbc-49e60bebca55)).
Protoc failed process creation with **WinError 623**
([illegal system DLL relocation](https://learn.microsoft.com/en-us/windows/win32/debug/system-error-codes--500-999-)).
These are unresolved native toolchain findings, not successful generator checks.
The fixture's exit 0 does not establish feature-worker readiness.

Against a fresh empty lark store, dummy bot dry run exited **0**, forced-user
dry run **2**, and missing-credential dry run **3**; the store stayed empty.
The child belonged to the FarmBot Job Object, credential-withholding assertions
passed and the job settled empty. Protected repository metadata, attempt-state
writes, cancellation, Windows worker generators and real Feishu reads remain
unverified for this backend. No FarmBot backend-selection change was made.

UTF-8 logs, revisions, versions, timings, sanitized summaries and private scratch
metadata are preserved in ignored `reports/isolated-windows-worker-*` directories.
The final checkout-scratch report is
`reports/isolated-windows-worker-20261003T130235Z/`; stdout SHA-256 is
`8af569e42222db92c159075ea7306561f484339583733dae3d876526977da69f`.
The detailed policy/RPC comparison is retained under
`reports/isolated-windows-worker-20261003T124751Z/`, and the consent-broker
comparison under `reports/isolated-windows-worker-20261003T125801Z/`.
Raw paths and diagnostics remain private.

#### Independent PowerShell comparison and image diagnosis

The operator ran the same dummy-only **0.160.0** probe from a regular PowerShell
window opened independently of Codex, under the selected current account. With
the working directory, isolated home and attempt state all outside the checkout,
it completed in **5.160 seconds**, exit **0**. All four allowed working-directory
write checks passed, the forbidden sibling was denied, the native child was
observed in the FarmBot Job Object and the job settled empty. Credential
withholding, dummy canonical delivery, strict bot mode and the empty-store
**0/2/3** dry runs passed. The earlier scratch write denial depends on the Codex
launch context; it does not establish a FarmBot write-permission regression.
The same Bash **0xC0000142** and protoc **WinError 623** failures remained.
Other version results matched the completed checkout-scratch probe above.

A focused direct comparison of the same binaries passed in **0.055 seconds**:
GNU Bash **5.3.9**, `uname -s` **MSYS_NT-10.0-26200**, and **libprotoc 35.1**,
all exit **0**. Read-only PE inspection found that protoc is x64 with relocations
stripped and no base-relocation directory; Bash and its MSYS runtime are x64 with
relocation data, but their dynamic-base/high-entropy flags are off.

A subsequent **5.258-second** MXC probe used the documented
[GetProcessMitigationPolicy](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getprocessmitigationpolicy)
API to inspect the sandboxed child. Its ASLR policy enables bottom-up allocation,
forced image relocation, high entropy and rejection of stripped images.
[Microsoft's compatibility guidance](https://learn.microsoft.com/en-us/defender-endpoint/exploit-protection-reference#force-randomization-for-images-mandatory-aslr)
states that the latter blocks binaries whose relocation information is stripped.
The pinned prebuilt protoc image is therefore incompatible with the measured
policy. ASLR is also a concrete compatibility hypothesis for Bash:
[Cygwin's process-creation documentation](https://cygwin.com/cygwin-ug-net/highlights.html)
describes its address-layout constraints. This check did not identify Bash's
failing DLL or prove that ASLR is its only failure mechanism.

No binary was patched, mitigation disabled or host exception installed. MXC is
not yet an accepted FarmBot backend. The next development step is to evaluate a
supported native runtime/toolchain combination, beginning with an ASLR-compatible
build of pinned protoc and focused Bash compatibility diagnosis. Then verify
protected metadata, attempt-state writes and cancellation before generators or
live worker acceptance. The registered-runtime ownership issue still applies
to the existing elevated backend.

The independent report is retained under ignored
`reports/isolated-windows-worker-20261003T131541Z/`; stdout SHA-256 is
`6b3a3f74c1a70d81d4b9d5aede464afecf2e8fe64ce562c9c18b624db5fe2ded`.
Direct probes and PE hashes are retained under
`reports/native-tool-images-20261003T131943Z/`, with mitigation evidence under
`reports/isolated-windows-worker-20261003T132145Z/`. All scopes remain isolated
development checks on this production Windows host; no model/Feishu access,
production configuration, ledger, service or parked job was involved.

Documentation validation passed all **73** skill/reference tests in **0.357
seconds**, with no failures, errors or skips. Changed links, whitespace and
private-path checks passed. The full offline suite was not repeated for these
documentation-only changes; the earlier measured native baseline remains above.

#### Relocatable protoc and MSYS initialization (2026-10-03; partial)

#83 merged as `8451f606ff2fbbefd07860f640a79f716ce464ee`; its
[merge-head CI](https://github.com/Kuaiwa-Network/farm-linear-agent/actions/runs/37127150756)
passed. This follow-up uses that revision, whose executable/test trees still
match `e8406d5`. It remains isolated development on the production Windows host.

A separate x64 static build of **protoc 35.1** now runs inside MXC with all four
measured ASLR flags retained. It uses the official
[protobuf 35.1 source release](https://github.com/protocolbuffers/protobuf/releases/tag/v35.1)
and its pinned **Abseil 20250512.1** dependency. Source archives were verified
against the release asset digests before extraction:

| Source | SHA-256 |
| --- | --- |
| `protobuf-35.1.zip` | `bf89df2fa0088de9c9890fbfba0076263a36c2f84847a7b54e7e32effd6201c7` |
| `abseil-cpp-20250512.1.tar.gz` | `9b7a064305e9fd94d124ffa6cc358592eb42b5da588fb4e07d09254aa40086db` |

The Abseil digest also matches the
[Bazel Central Registry record](https://github.com/bazelbuild/bazel-central-registry/blob/main/modules/abseil-cpp/20250512.1/source.json).
Existing Visual Studio 2022 tools provided **MSVC 19.44.35219.0**, Windows SDK
**10.0.26100.0** and **CMake 3.31.6-msvc6**. The globally resolved CMake 3.18.1
was not used. Source files were unchanged; dependencies were supplied locally,
with CMake fetching disabled. The verification-only build uses these options
with the upstream [Windows build instructions](https://github.com/protocolbuffers/protobuf/blob/v35.1/cmake/README.md):

```text
Generator: Visual Studio 17 2022, x64; C++17
protobuf_BUILD_TESTS=OFF
protobuf_BUILD_SHARED_LIBS=OFF
protobuf_BUILD_LIBUPB=ON
protobuf_WITH_ZLIB=OFF
protobuf_MSVC_STATIC_RUNTIME=ON
FETCHCONTENT_SOURCE_DIR_ABSL=<verified Abseil source>
FETCHCONTENT_FULLY_DISCONNECTED=ON
CMAKE_EXE_LINKER_FLAGS=/DYNAMICBASE /HIGHENTROPYVA /FIXED:NO
Build: cmake --build <build> --config Release --target protoc --parallel 4
```

The first build disabled UPB and failed with **C1083** for the required generated
`google/protobuf/descriptor.upb.h`; its **67.444-second** attempt is retained.
Enabling that upstream dependency corrected the build configuration. The
successful incremental retry took **173.930 seconds**, including **166.909
seconds** compiling/linking. This is not a clean-build timing. Its image has
dynamic-base/high-entropy flags, **23,224 bytes** of relocation data and no
stripped-relocation flag. Image SHA-256:
`71b837c0c7e9a5ac1a833150f2ef4c9d97d456c5faabc0da66388ea1d204fb5a`.
The separate image remains under ignored verification storage; no installation
or production PATH was changed.

A synthetic fixture exercises proto2 custom field options, proto3 optional/map/
oneof/nested/enum fields, a well-known timestamp import, a service and UTF-8 text.
Direct official/local compiler comparisons passed in **0.161 seconds**: all
**11 files** were byte-identical (C++, Python, C#, an imported/source-info
descriptor set, encoded bytes and decoded text). A **5.774-second** MXC probe
then passed version, generation, encoding and decoding; all 11 hashes matched
the official baseline. Writes to the read-only fixture marker and forbidden
sibling were denied, allowed scratch writes passed, and the marker was unchanged.
Job Object membership/empty settlement and dummy credential withholding/dry
runs passed. This verifies the synthetic protoc fixture, not farm-hive or
Farm-Contract generators, repository gates, worker cancellation or live access.

MSYS remains the native blocker. A **5.722-second** matrix found **0xC0000142**
for Bash version, a Bash builtin, `sh`, `uname`, `ls` and `awk`, before any
repository workload. The selected Bash is Git for Windows, not WSL. The shared
runtime reports **3.6.7-fb42d71358dd896ab324c52970f7d03f9ab0dfe5**; its SHA-256
is `13f0b0dc94588766ecfa1867f1a00061508ba1dc62f5b8e858ac59f01e358aa0`.
A **5.966-second** focused probe found that `LoadLibraryExW` for `msys-2.0.dll`
fails with **WinError 1114** inside MXC. The same load and all six direct MSYS
startup controls pass outside MXC in **0.247 seconds**, using an empty HOME and
an environment without credentials. The packaged `strace` version works, but
tracing crashes with **0xC0000005** in both contexts and yields an empty trace;
that diagnostic-tool failure does not establish a sandbox-specific cause.

A small local native debugger then captured the owned Bash child's loader
events without changing host debugging settings. The final **5.765-second**
MXC comparison observed first- and second-chance **0xC0000005** in
**`msys-2.0.dll` at RVA `0x2ece1`**, with child ASLR flags **15**. The same
debugger/Bash direct control exited **0**, with ASLR flags **0**. The debugger
changes how the exception is surfaced; ordinary launch still reports
0xC0000142. This identifies the failing module and offset, not the internal
initialization cause or proof that ASLR is its only cause. The
[matching runtime source](https://github.com/git-for-windows/msys2-runtime/blob/fb42d71358dd896ab324c52970f7d03f9ab0dfe5/winsup/cygwin/init.cc)
provides the next source/symbol diagnosis target. The owned job settled empty
after each sandbox probe, and its credential and write-denial checks passed.

Retained ignored reports:

- `reports/protoc-native-build-20261003T134937Z/` (initial configuration failure)
  and `reports/protoc-native-build-20261003T135228Z/` (successful retry).
- `reports/protoc-native-fixture-20261003T135831Z/` (direct byte comparison) and
  `reports/isolated-windows-worker-20261003T135924Z/` (sandbox generation).
  Sandbox stdout SHA-256:
  `2d1ad02f81165a50977f9002e2bebbbb6940b1f77d9bb03b725c7281d827a7c3`.
- `reports/isolated-windows-worker-20261003T135308Z/` (MSYS startup matrix),
  `reports/isolated-windows-worker-20261003T140136Z/` (DLL load), and
  `reports/msys-direct-control-20261003T140246Z/` (direct controls).
- `reports/msys-loader-direct-20261003T141200Z/` and
  `reports/isolated-windows-worker-20261003T141218Z/` (final loader comparison).
  Sandbox stdout SHA-256:
  `d9bf8de52a60ce863da4de0ea088d53336a808e8e90649b82acd0c3d84d2f75b`.

UTF-8 raw logs, tool paths and diagnostic sources remain private. No binary
patch, mitigation exception, account/app setting change or production action was
made. FarmBot still generates the elevated backend; MXC was selected only by the
verification harness and remains unaccepted. Next: diagnose the MSYS loader
fault against matching symbols/source and establish a supported native launch
combination before metadata/state-write/cancellation acceptance, Windows
repository generators or real Feishu reads.

Documentation validation passed **73** skill/reference tests in **0.396
seconds**, with no failures, errors or skips. An initial run in the Codex
execution sandbox had **33 temporary-state access errors**; the native rerun
used fresh temporary state under the development checkout. No tests or skips
were changed. Retained measurement/hash, changed-link, privacy and whitespace
checks passed. The full suite was not repeated for these documentation-only
changes; the measured native baseline remains unchanged.

#### MSYS object-directory denial (2026-10-03; partial)

This follow-up used the same separate development checkout on the **production
Windows host**, after #84 merged as `d66388267ee9e0989e6ec95a0db6f0250b6451e3`.
[Merge-head CI](https://github.com/Kuaiwa-Network/farm-linear-agent/actions/runs/37130017579)
passed. Executable/test/skill/reference trees still match `e8406d5`; Python was
**3.13.16**, with `PYTHONUTF8=1`, and the explicit native Codex CLI was **0.160.0**.
No production config, ledger or runtime state was read. All worker probes used
fresh state/home directories, dummy credentials and the previous explicit
filesystem policy; no model or live API was called.

The exact **3.6.7-4** runtime debug data was downloaded from the official
[immutable package repository revision](https://github.com/git-for-windows/pacman-repo/tree/5d5b0de653a50a198e71a84bea558256ec88402a).
The 432,528-byte `msys2-runtime-devel-3.6.7-4-x86_64.pkg.tar.xz` matched its Git
blob identity; package SHA-256 was
`a8879b916108a9422314a34ace9f25108e7fecbd1b88c945dc504e5f7c5ec63a`.
Only debug data was extracted, without installing a package. The debug file's
CRC32 **0x23a6d10d** matched the installed DLL's `.gnu_debuglink`; its SHA-256 was
`ef71739d79541e5eb731dd698fd4f89e3a4d246640e842c616a288709d25c130`.
The symbols map the fault at RVA **0x2ece1** to
`dll_list::cleanup_forkables` and its referenced global at RVA **0x283d90** to
`cygwin_shared`. The owned child's captured exception context confirms a null
RAX and a read access violation at **0xe7b4**. This is a cleanup failure after an
earlier fatal initialization error.

The diagnostic child now has distinct owned standard handles, with stderr
captured privately. Both installed and portable runtimes report the earlier
failure as **`NtCreateDirectoryObject` returning 0xC0000022 /
STATUS_ACCESS_DENIED** for their installation-specific directory under
`\BaseNamedObjects`; installation identifiers and paths are omitted here. The
[matching source](https://github.com/git-for-windows/msys2-runtime/blob/fb42d71358dd896ab324c52970f7d03f9ab0dfe5/winsup/cygwin/mm/shared.cc)
creates this directory in `get_shared_parent_dir()` and calls `api_fatal` on
failure. The later
[cleanup routine](https://github.com/git-for-windows/msys2-runtime/blob/fb42d71358dd896ab324c52970f7d03f9ab0dfe5/winsup/cygwin/forkable.cc)
reads shared state that initialization never populated. An object directory is
a Windows kernel namespace object, not a checkout filesystem directory; see
[Microsoft's API reference](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/wdm/nf-wdm-zwcreatedirectoryobject).

An independent native probe used a unique transient verification name, MSYS's
`OBJ_OPENIF` and **0x2000f** directory access mask, and an Everyone DACL granting
that mask. It never opened an MSYS/production object or changed an existing ACL.
The directory operation returned **STATUS_SUCCESS directly** and
**STATUS_ACCESS_DENIED inside MXC**. A `CreateEventW` control using a unique
`Local\` name succeeded in both contexts. Every owned handle was closed; no
object was made permanent or pre-created for a worker. The corrected direct
comparison took **0.096 seconds** and both Bash children exited **0**. An initial
diagnostic version reused one NUL handle for stdin/stdout and caused debugger
invalid-handle exceptions in both direct controls; separate handles corrected
that harness error. Those initial failures are not application regressions.

The latest official
[Git for Windows release](https://github.com/git-for-windows/git/releases/tag/v2.56.0.windows.1)
was tested separately, without host installation or PATH changes. The
59,958,024-byte `PortableGit-2.56.0-64-bit.7z.exe` matched the release API's SHA-256
`eceb5e061aa90df2f69ddd3e90f0030e1b8037a7829934bc40e4be1caa1accc1`.
The archive was extracted with the existing 7-Zip tool, never executed. Its
runtime DLL SHA-256 was
`2a89b7c31b323c42e2fb37900af087aeb27417694e630a210bc75b0369410bc7`.
Preparation/direct probes took **16.434 seconds**, reporting Git
**2.56.0.windows.1**, Bash **5.3.15(2)-release**, MSYS package **3.6.10-4** and
`uname -s` **MSYS_NT-10.0-26200**. This is native Git for Windows Bash, not WSL.

| Final contained comparison | Installed Git | Portable Git |
| --- | --- | --- |
| Git version | 2.54.0.windows.1 | 2.56.0.windows.1 |
| Git LFS version | 3.7.1 | 3.8.0 |
| MSYS package | 3.6.7-4 | 3.6.10-4 |
| Probe duration | 5.865 seconds | 5.916 seconds |
| Ordinary Bash, builtin, sh, uname, ls, awk | 0xC0000142 | 0xC0000142 |
| DLL load | WinError 1114 | WinError 1114 |
| Original captured fatal error | NtCreateDirectoryObject: 0xC0000022 | NtCreateDirectoryObject: 0xC0000022 |
| Debugger cleanup fault RVA | 0x2ece1 | 0x2ed11 |
| Separate namespace probe | 0xC0000022 | 0xC0000022 |

Both comparisons retained all four ASLR flags, passed Git/LFS and the separate
relocatable protoc **35.1** version probe, denied forbidden-sibling writes, and
passed dummy credential delivery/withholding, strict-bot dry runs **0/2/3**,
Job membership and empty settlement. Fixture exit 0 records completion, not
readiness. These findings identify the first captured MSYS startup failure;
they do not establish that fixing the namespace compatibility would resolve
every later fork/ASLR/generator issue. No mitigation or containment was weakened.

Retained ignored evidence:

- `reports/portable-git-native-20261003T144451Z/` (archive integrity and versions).
- `.local/verification/msys-symbols/summary.json` (debug-data identity/symbol map).
- `reports/msys-namespace-direct-20261003T150348Z/` (initial handle-control error)
  and `reports/msys-namespace-direct-20261003T150445Z/` (corrected direct controls).
- `reports/isolated-windows-worker-20261003T150614Z/` (installed runtime) and
  `reports/isolated-windows-worker-20261003T150620Z/` (portable runtime).
  Sandbox stdout SHA-256 values:
  `b6b9fb5ca772067e84ecde4ff85d2f26e5e8315a07866d55641ddc4a81ac9559` and
  `bdbcc84a94bb75f6f93c20f44f5e8cfbc0510fac59577e37c9d5f7ad5d7cb6ce`.
- `reports/msys-final-controls-20261003T150614Z/` (combined measurements).

Raw UTF-8 logs, debugger/probe sources, executables and host metadata remain
private. The next release step is a supported contained native Windows launch
that runs Bash: resolve the existing registered-runtime ownership blocker for
FarmBot's selected elevated backend, or establish and review a compatible
sandbox/runtime integration. MXC remains a verification-only evaluation; a Git
upgrade alone did not resolve this blocker. Repository worker generators and
real Feishu reads remain pending. No accounts, app settings, credentials,
production enablement or deployment were changed.

Documentation validation passed **73** skill/reference tests in **0.424
seconds**, with **0 failures, 0 errors and 0 skips**, using fresh temporary state
in the development checkout. Retained measurements/hashes, changed links,
privacy and whitespace checks passed. No executable behavior changed, so the
full offline suite was not repeated; its previously measured baseline still
applies.

#### Registered-runtime admission and local-object control (2026-10-04; partial)

This follow-up ran from merged #85 at
`e5946e0cb4b64dda17e3a91554416dee281bf727`, in the separate development checkout
on the **production Windows host**. The operator's local date was 2026-10-04
(Asia/Shanghai); retained UTC report timestamps below are still 2026-10-03.
[PR CI](https://github.com/Kuaiwa-Network/farm-linear-agent/actions/runs/37132528124)
and [merge-head CI](https://github.com/Kuaiwa-Network/farm-linear-agent/actions/runs/37136149475)
passed. Executable/test trees
remain unchanged from `e8406d5`; no full-suite repeat was needed for this
documentation-only checkpoint.

The latest stable upstream release checked was
[Codex 0.160.0](https://github.com/openai/codex/releases/tag/rust-v0.160.0), already
the explicit selected native CLI. A **0.284-second** read-only check examined
only the protected Codex ownership metadata and an owned `--version` child,
using a newly created verification home, Python **3.13.16** and `PYTHONUTF8=1`.
It confirmed:

- The registered Core record and completed runtime registration are present,
  with two recorded runtime accounts.
- The current Windows user's SID matches the owner. The fresh worker home does
  not match the registered home. No SID, package identifier or home path is
  included in the shareable output.
- `GetPackageFamilyName` on the owned CLI child returns **15700 /
  APPMODEL_ERROR_NO_PACKAGE**; the child exits **0**. The
  [Microsoft API reference](https://learn.microsoft.com/en-us/windows/win32/api/appmodel/nf-appmodel-getpackagefamilyname)
  identifies this result as absence of OS package identity.
- The fresh home contains only a runtime-created `tmp/` directory; auth, config
  and model state remain absent. No registered-home files, production FarmBot
  config or ledger were read, and no provisioning/service request was made.

The
[0.160.0 ownership source](https://github.com/openai/codex/blob/a956835d020762cb2b570053af06f643a11c0ecc/codex-rs/windows-sandbox-rs/src/runtime_ownership.rs)
still admits only the matching owner, home and package family. Its
[runner receipt checks](https://github.com/openai/codex/blob/a956835d020762cb2b570053af06f643a11c0ecc/codex-rs/windows-sandbox-rs/src/app_package.rs)
also require the matching Codex home. Registered service
[caller admission](https://github.com/openai/codex/blob/a956835d020762cb2b570053af06f643a11c0ecc/codex-rs/windows-sandbox-service/src/package_identity/registered.rs)
requires the installed service's package family and exact allowed caller image.
The inspected launch/provisioning interfaces carry one Codex home; a supported
separate sandbox authority-home setting was not established in those interfaces
or the checked official documentation. These are source-based integration
constraints, not a newly executed registered-service refusal. Changing a routing
flag would not satisfy the measured home/caller inputs. Preserve these guards;
do not use the interactive desktop home as a worker config/auth/state fallback.

A native object control then tested a narrower namespace operation without
granting new permissions. It created a unique `Local\` event, queried **only
that owned event's** actual object name, and requested a unique sibling directory
under the same parent, with the same **0x2000f** access mask, `OBJ_OPENIF` and
Everyone DACL as the preceding directory control. The actual namespace names
were withheld from output. Every owned event/directory handle was closed; no
existing MSYS/production object or ACL was read or changed.

| Operation | Direct control | Contained MXC control |
| --- | --- | --- |
| Unique directory under global BaseNamedObjects | STATUS_SUCCESS | STATUS_ACCESS_DENIED |
| Unique Local event | Success | Success |
| Name query on owned event | Success | Success |
| Unique directory beside owned event | STATUS_SUCCESS | STATUS_SUCCESS |
| All owned object handles closed | Yes | Yes |

The direct comparison took **0.091 seconds**, with installed and portable Bash
children exiting **0**. The contained comparison took **5.969 seconds** using
PortableGit **2.56.0**, MSYS **3.6.10-4**, the relocatable protoc **35.1** image
and the previous explicit permission profile. All four ASLR flags, allowed
scratch writes, forbidden-sibling denial, Job membership/empty settlement,
dummy credential delivery/withholding and strict-bot dry runs **0/2/3** passed.
Git/LFS and protoc still run; Bash/MSYS still fail with **0xC0000142** and the
same original directory denial. Fixture exit 0 means completion, not readiness.

The owned-event sibling result provides a concrete private-object namespace
integration direction. It does not run a modified MSYS runtime, prove fork or
repository-generator behavior, or establish MXC as an accepted FarmBot backend.
No MSYS binary/source patch, permission exception, mitigation change or
pre-created global object was used. FarmBot still selects `elevated`; the MXC
selector exists only in the ignored verification harness.

Retained ignored evidence:

- `reports/registered-home-readonly-20261003T163721Z/`: ownership booleans, CLI
  image/source hashes, child package-identity result and timing.
- `reports/msys-namespace-direct-20261003T163116Z/`: direct namespace controls.
- `reports/isolated-windows-worker-20261003T163122Z/`: contained controls,
  UTF-8 logs and preserved scratch metadata. Sandbox stdout SHA-256:
  `a145500e4fc00e863cce665d38b52270d94781637de460dcdd2b31504f1b2c60`.
- `.local/verification/codex-01600-source/`: inspected immutable upstream source.
- `.local/verification/codex-windows-support-draft.md`: sanitized upstream
  integration request, prepared locally and not submitted by this checkpoint.

Next: obtain a supported integration for isolated attempt homes with the
registered runtime, or develop and review a contained private-object MSYS
integration. The existing current Windows account already matches ownership;
this finding does not call for another account or another UAC retry. Sending the
prepared upstream request requires separate explicit operator authorization.
Native worker credentials/state writes/cancellation, real Windows repository
generators and real Feishu reads remain pending. No account, app setting,
credential, service or production state changed.

Documentation validation passed **73** skill/reference tests in **0.370
seconds**, with **0 failures, 0 errors and 0 skips**. Retained measurements and
hashes, changed links, support-draft privacy and whitespace checks passed.

#### MSYS private-namespace IPC control (2026-10-04; partial)

This continues the separate development-checkout investigation on the
**production Windows host**. #86 merged as
`6bf4919cd9c4a74bb2602dd84fa3adf196dea0f5`; its PR CI passed. The measurements
below ran at its recorded pre-merge head `4ba8fce`; the executable trees of
both revisions still match candidate `e8406d5`. No service, account, app
setting, credential or production state changed.

An existing [Microsoft MXC issue #1061](https://github.com/microsoft/mxc/issues/1061)
reports the same MSYS `NtCreateDirectoryObject` global-namespace denial.
It remains open at this check. A
[Microsoft member's response](https://github.com/microsoft/mxc/issues/1061#issuecomment-5669346845)
identifies an MSYS namespace adaptation and OS namespace virtualization as
implementation work. This is upstream context, not a fix measured on this host.
Reviewed Git for Windows runtime source at immutable commit
`81d9bd3d1c3680aa611ff2e5d1950787ffc95c6a` hard-codes the global path in
`get_shared_parent_dir()` and the session `BNOLINKS` path in
`get_session_parent_dir()` in
[shared.cc](https://github.com/git-for-windows/msys2-runtime/blob/81d9bd3d1c3680aa611ff2e5d1950787ffc95c6a/winsup/cygwin/mm/shared.cc).
Its [environment-options parser](https://github.com/git-for-windows/msys2-runtime/blob/81d9bd3d1c3680aa611ff2e5d1950787ffc95c6a/winsup/cygwin/environ.cc)
has no namespace override. This reviewed source was not rebuilt or claimed to
match the exact installed DLL's provenance. No environment-setting fix was
established by this review.

A **0.028-second** read-only build preflight of the prepared portable Git
prefix, with empty HOME and no Bash startup files, found `gcc`, `g++`, `make`,
`autoconf`, `automake` and `libtool` absent on that selected PATH. This is a
source-build toolchain gap, not an application regression or a claim about
every tool installed elsewhere on the PC. The native MSVC verification helper
does not supply an MSYS runtime build environment; a runtime prototype needs
a separate isolated GNU/MSYS build toolchain first. No installation was made.

A verification-only native helper extends the earlier directory control.
Each parent and owned child independently resolves the namespace of its own
unique `Local` event. The parent creates a unique directory there, then a
relative shared-memory section, mutex and object-manager symbolic link. The
child reopens them, maps both the section and its link, acquires the mutex,
checks the parent's marker and changes it; the parent verifies the update.
These are transient fixture objects, with every owned handle closed. The
helper neither touches an existing MSYS object/ACL nor pre-creates a global
MSYS directory. Its sibling-write control deletes only a unique fixture file
it has just created in the direct run; the contained write is denied.

The final direct comparison took **0.074 seconds**, and the contained fixture
took **5.711 seconds** with native Codex **0.160.0**, Python **3.13.16** and
`PYTHONUTF8=1`. Both controls exit **0** and all four object creations return
`STATUS_SUCCESS`. The contained native parent and child each report ASLR
flags **15** (all four flags), versus **5** directly. Both report Job membership;
the outer fixture confirms the sandbox process belongs to FarmBot's Job and
that the Job settles empty. The child's sibling-write control returns Win32
**5 / access denied** inside the sandbox, versus successful creation directly.
The existing allowed-write/forbidden-sibling and dummy credential isolation
controls also pass. This retains the explicit policy and security settings.
The first attempted fixture did not forward the new helper flag; it is not
counted as IPC verification. The corrected harness requires the requested IPC
result to be present before accepting a completed fixture.

The selected tools remain official PortableGit **2.56.0** / MSYS **3.6.10-4**
and the separate relocatable protoc **35.1**. Git, LFS, protoc, Python, Go,
buf, openspec and lark-cli version probes pass. Bash/version/builtin, sh,
uname, ls and awk still return **0xC0000142**. Strict bot-only dummy dry runs
retain exits **0 / 2 / 3** for bot / user / missing credentials. No model or
Feishu API is called; fixture exit **0** establishes control completion.
MXC is selected by the ignored evaluation harness only; Launcher-generated
config and FarmBot's product backend remain **elevated**.

Retained local evidence:

- `reports/msys-ipc-direct-20261003T232345Z/`: direct UTF-8 output and summary.
- `reports/isolated-windows-worker-20261003T232349Z/`: contained UTF-8 logs,
  summary and private scratch metadata; stdout SHA-256
  `5592a77ded6e087dca6171b57d48bc7ec1b37c3af51d28ba75664229eaecfa99`.
- Native IPC helper SHA-256
  `01d974de6d217b07cbf302c09eb792470e4f34eadf883be5f4e07319a54e5e57`;
  source SHA-256
  `63a683bf6bf83ce10c4d1cf8539a5687bf60279a407f2a7a2d87db8b75c01d33`.
- `.local/verification/msys-namespace-upstream-evidence.json` and
  `.local/verification/msys-namespace-source/`: pinned source-review context.
- `reports/msys-build-preflight-20261003T232925Z/`: the selected-prefix build-tool
  availability check, UTF-8 output and summary.

This validates more primitives for a potential private-namespace adaptation;
it does not validate actual MSYS startup, Bash pipes, MSYS fork/exec, process
signalling, simultaneous workers or repository generators. No MSYS source or
binary patch was applied. Next: build and review an actual MSYS adaptation or
obtain a supported contained runtime, then execute those acceptance cases.
The registered Codex owner/home/caller constraints from the preceding record
remain unresolved. Actual worker credentials/state writes/cancellation, real
Windows generators and Feishu/Word access remain pending. The operator deferred
publishing the prepared upstream report; no new upstream issue was posted.

Documentation validation passed **73** skill/reference tests in **0.605
seconds**, with **0 failures, 0 errors and 0 skips**. No product code, test,
skill or reference changed; a full offline-suite repeat is unnecessary for
this documentation update. Retained measurement/hash, link, privacy and
whitespace checks passed.

## Native Windows tools follow-up (2026-10-04; partial)

The operator chose native Windows tools, keeping Bash for Mac/Linux testing.
The earlier MSYS failures came from the experimental MXC evaluation; FarmBot
selects the elevated Windows backend for both fix and feature workers. Those
MXC findings do not prove that the working bug fixer, or the configured feature
backend, cannot run its tools. Further MSYS build work is deferred.

The first check reused common's existing native `designer/tools/gen-config.cmd`
launcher. An isolated checkout of `945550c84cdf15121397e8e2818c7015d3aa4124`
on this **production Windows host**, outside the running installation, passed
version and inventory but failed generation: the privately built pinned plugin
reported `protoc-gen-go.exe v1.36.8`; the preflight expected
`protoc-gen-go v1.36.8`. The new regression reproduced that failure before
the fix. This is a native generator bug, independent of MSYS.

[common PR #148](https://github.com/Kuaiwa-Network/common/pull/148)
compares the exact private executable basename and pinned version, retains the
single-line check, corrects the fake tools to match upstream, and adds focused
PowerShell Windows CI. No containment, ownership check, dependency pin or
skip was weakened. It merged on 2026-10-04 as
`e743062666acfea21ee9fcf64a5fbef8f269cdcf`; no production generator was upgraded.
At head `ba5bfa9e907462ab28f8b73b62aac3158b456525`,
[CI run 37165148969](https://github.com/Kuaiwa-Network/common/actions/runs/37165148969)
passed both complete Linux acceptance and the focused native Windows language
regression job. The first Windows run lacked protoc and could not canonicalize
the hosted Go SDK path; the CI-only follow-up supplies checksum-pinned native
protoc 35.1 and a real scratch copy of Go 1.25.1, preserving the path checks.
That focused job does not run the complete Windows producer suite.

Measured on clean candidate `289c406dd25701d297923a94e6aca3231409097a`:
CPython 3.13.16 with `PYTHONUTF8=1` set before startup, native CMD, Go 1.25.1
and protoc 35.1; Git 2.54.0.windows.1 and Git LFS 3.7.1. Inherited
FarmBot/fake-worker selectors, Feishu credentials
and GitHub token variables were removed. Go build/module caches and generated
outputs used development scratch directories; Git hooks/fsmonitor were off.
No production config, ledger or credential store was read.

| Native command through `gen-config.cmd` | Exit | Seconds |
| --- | --- | --- |
| `version` | 0 | 2.313 |
| `inventory --out ABSOLUTE_FILE` | 0 | 1.228 |
| `generate --profile farm-hive --out ABSENT_DIR` | 0 | 7.167 |
| `verify --profile farm-hive --against GENERATED_DIR` | 0 | 2.878 |
| `generate --profile farm-hive --profile unity-client --out ABSENT_DIR` | 0 | 8.342 |
| `verify --profile farm-hive --profile unity-client --against GENERATED_DIR` | 0 | 3.106 |

The sequence, including revision/status/version probes, took **25.277 seconds**.
The checkout remained clean. Bash and WSL were not invoked. This proves the
listed direct native operations, not execution inside a real FarmBot worker,
C# compilation, complete producer acceptance or release readiness.

`go vet ./internal/toolchain` passed. The native focused command
`go test -count=1 -run '^TestGenerateLanguages' ./internal/toolchain`
passed **22 top-level tests**, with **two existing explicit-fixture skips**,
in **6.686 seconds** subprocess wall time. Native generation above ran separately.

The broader `go test -count=1 ./internal/artifact ./internal/repoinfo ./internal/toolchain`
selection is not green: **169 top-level tests, 148 passed, seven failed,
14 skipped**, in **34.767 seconds**. An untouched baseline checkout repeated
the same selection in **37.019 seconds**: **168 top-level tests, 147 passed,
the same seven failures and the same skips**. The extra passing test is the
new version regression. Go subtests are not added to these top-level totals.

The seven remaining failed top-level tests are:

- `TestMaterializeModuleRequiresFreshChildOfPrivateParent`: Windows fixture/private-parent semantics need review.
- `TestMaterializeModuleCleanupPreservesReplacementIdentity`: replacement identity was not preserved.
- `TestSnapshotterRejectsUnsafeTreesFreshnessAndOverlap`: Windows rejected the fixture's newline filename before the check.
- `TestSnapshotterCleanupPreservesReplacementDestinationIdentity`: replacement marker was absent; the cleanup follow-up below identifies a fixture failure before replacement.
- `TestWindowsLauncherRejectsMalformedPreflightWithoutRunningGo`: malformed native CMD preflight behavior differs from its required exit/result.
- `TestResolveExecutableRejectsRelativeLookPathResultWithErrDotDisabled`: fixture cleanup encounters Windows read-only file semantics.
- `TestLegacyOwnershipScannerRejectsDatedActiveFiles`: Windows path matching needs review.

A first longer temporary-root run also hit two Git fixture path-length failures;
the shorter fresh system-temp run above removed those failures without changing
Git settings. The two cleanup-identity failures were the next priority; the
follow-up below resolves them without skips. No claim is made about other
Windows generator or gate paths.

All **37 skip events**, including subtests, were already present on baseline.
The ordinary native test token lacks symlink privilege; no privilege or Windows
setting was changed. The complete sanitized skip inventory is:

| Test or subtest | Reason |
| --- | --- |
| `TestReadBoundRegularRejectsIntermediateDirectoryReplacementAfterRead` | Open-directory replacement unavailable: access denied. |
| `TestLoadInputSetRejectsLinksAndInvalidModulePins/symlink_toolchain.json` | Symlink privilege unavailable on this test token. |
| `TestLoadInputSetRejectsLinksAndInvalidModulePins/symlink_go.mod` | Symlink privilege unavailable on this test token. |
| `TestLoadInputSetRejectsLinksAndInvalidModulePins/symlink_go.sum` | Symlink privilege unavailable on this test token. |
| `TestReadRejectsInvalidUTF8AndLinkedManifest/symlink` | Symlink privilege unavailable on this test token. |
| `TestProductionSourceDigestMatchesExactPackerPipeline` | Linux-only independent packer comparison; platform-neutral digest tests ran. |
| `TestBoundRootContainsDirectoryIdentitySkipsSymlinksWithoutFollowing` | Symlink privilege unavailable on this test token. |
| `TestSnapshotterRejectsUnsafeTreesFreshnessAndOverlap/symlink` | Symlink privilege unavailable on this test token. |
| `TestSnapshotterRejectsUnsafeTreesFreshnessAndOverlap/case_collision` | filesystem is case-insensitive |
| `TestValidateTreeRejectsPhysicalMutations/symlink` | Symlink privilege unavailable on this test token. |
| `TestValidateTreeRejectsPhysicalMutations/fifo` | mkfifo unavailable: exec: "mkfifo": executable file not found in %PATH% |
| `TestBuildRejectsUnsafePhysicalRootShapes/root_symlink` | Symlink privilege unavailable on this test token. |
| `TestBuildRejectsUnsafePhysicalRootShapes/directory_symlink` | Symlink privilege unavailable on this test token. |
| `TestBuildRejectsUnsafePhysicalRootShapes/case-colliding_files` | filesystem does not support case-distinct fixture names: open <private-path> |
| `TestBoundTreeWalkRejectsDirectorySymlinkSwapBeforeEnumeration` | Symlink privilege unavailable on this test token. |
| `TestGenerateLanguagesRejectsInvalidStagingBeforeRunningTools/case_collision` | filesystem is case-insensitive |
| `TestGenerateLanguagesRejectsInvalidStagingBeforeRunningTools/schema_file_symlink` | Symlink privilege unavailable on this test token. |
| `TestGenerateLanguagesRejectsInvalidStagingBeforeRunningTools/schema_root_symlink` | Symlink privilege unavailable on this test token. |
| `TestGenerateLanguagesRejectsInvalidStagingBeforeRunningTools/module_root_symlink` | Symlink privilege unavailable on this test token. |
| `TestGenerateLanguagesRejectsInvalidStagingBeforeRunningTools/output_symlink_ancestor` | Symlink privilege unavailable on this test token. |
| `TestSourceDigestRejectsUnsafeTrees/newline_name` | Windows does not permit newline path components |
| `TestSourceDigestRejectsUnsafeTrees/backslash_name` | a backslash is a Windows path separator |
| `TestSourceDigestRejectsUnsafeTrees/symlink` | Symlink privilege unavailable on this test token. |
| `TestSourceDigestRejectsUnsafeTrees/fifo` | mkfifo unavailable: exec: "mkfifo": executable file not found in %PATH% |
| `TestSourceDigestRejectsUnsafeTrees/root_symlink` | Symlink privilege unavailable on this test token. |
| `TestGenerateLanguagesRejectsMissingExtraAndMalformedOutputs/symlink_Go` | Symlink privilege unavailable on this test token. |
| `TestGenerateLanguagesProductionFixture` | Requires an explicit existing empty production fixture directory. |
| `TestGenerateLanguagesProductionSingleViews` | Requires an explicit existing empty single-view fixture directory. |
| `TestSystemRunnerCommandEnv/Unix_nonnull_empty_does_not_inherit` | Windows requires SYSTEMROOT in every explicit environment |
| `TestSystemRunnerRejectsExecutableSymlink` | Symlink privilege unavailable on this test token. |
| `TestFromEnvRejectsUnsafeRootsAndPaths` | Symlink privilege unavailable on this test token. |
| `TestProfilePathRejectsEscapesAndLinks` | Symlink privilege unavailable on this test token. |
| `TestLegacyShellGuardRejectsDatedActiveFilesThroughNeutralWrapper` | POSIX Bash legacy guard test. |
| `TestLegacyShellGuardRetainsExactHistoricalDocumentationExemptions` | POSIX Bash legacy guard test. |
| `TestUnixLauncherUsesItsCheckoutAndPreservesArguments` | Unix launcher test. |
| `TestUnixLauncherRejectsMalformedPreflightWithoutRunningGo` | Unix launcher test. |
| `TestCheckClientExportRejectsOptionLikeBaseRefBeforeGit` | Unix gate script test. |

UTF-8 logs, durations, revisions, command outcomes and SHA-256 hashes remain
in ignored local `reports/native-common-*/` directories. The final native
sequence's server-generation log hash is
`4ff5e3e83bd84c4fa9d8c5598c79a5563385162e26671401c10d4870929eeb13`;
the combined-generation log hash is
`45c0af91d960875e97372f64cf98a33565185f752ba67951ec26dddff4ea9943`.
Raw temporary paths remain local. Real Feishu access and Windows worker
acceptance remain pending.

## Native Windows cleanup follow-up (2026-10-04; partial)

GitHub confirms common #148 merged as `e743062` and the documentation-only
FarmBot #87 merged as `13cc2cd`. The next development branch starts from that
exact common merge, on this **production Windows host in the separate development
checkout**. Production configuration, its ledger, services and credentials were
not read or changed. Python 3.13.16 started with `PYTHONUTF8=1`; the same sanitized
native CMD/Go 1.25.1/protoc 35.1 environment, private caches and fresh scratch
outputs described above were used. The ordinary test token and its symlink
limitations were unchanged.

The two tests reproduced on merged common before changes. Their causes differ:

- `TestMaterializeModuleCleanupPreservesReplacementIdentity` exposed a real
  identity bug. On Windows, Go's `os.Lstat` metadata can defer file-ID lookup
  until `os.SameFile`; by then the original pathname may identify a replacement.
  [common PR #149](https://github.com/Kuaiwa-Network/common/pull/149)
  captures the directory through the existing no-follow bound-root helper and
  each document through its open handle before closing it. Six new same-byte
  replacement cases cover all three module documents, with and without an
  injected primary error; the new test failed before the fix and passes after it.
- `TestSnapshotterCleanupPreservesReplacementDestinationIdentity` failed before
  its intended replacement: Windows refuses to rename a directory while a child
  file is open. Its old missing-marker result did not prove that production
  snapshot cleanup deleted a foreign directory. The revised fixture replaces
  between copies after confirming an owned file was copied, requires the
  replacement and injected failure to occur, and preserves both the foreign
  marker and moved owned bytes. Snapshot production cleanup needed no change.

The operator merged #149 on 2026-10-04 as
`95f600827853d247cb2731535e3e1390a6f0403e`. The following measurements are from
its pre-merge source candidate; no production generator or service was deployed.

At candidate `e5c82b77f2dc43133ea6abdc240f03a4abb5b0b9`, both targeted tests and
all six new document replacement cases pass. The expanded Windows CI selection
also covers language generation, owned cleanup and cancellation: **30 top-level
tests, 28 passed, two existing explicit-fixture skips, zero failures**,
in **6.970 seconds**. Six existing capability subtest skips remain in that
selection (case-sensitive filenames and symlink creation); they are already in
the 37-event inventory above. No skip or weakened identity check was added.

The same three-package selection on the working fix ran **170 top-level tests:
151 passed, five failed, 14 skipped**, in **34.817 seconds**. It adds one passing
top-level regression and resolves the two prior failures; all **37 skip IDs**
match the earlier baseline. The remaining failed top-level tests are:

- `TestMaterializeModuleRequiresFreshChildOfPrivateParent`
- `TestSnapshotterRejectsUnsafeTreesFreshnessAndOverlap`
- `TestWindowsLauncherRejectsMalformedPreflightWithoutRunningGo`
- `TestResolveExecutableRejectsRelativeLookPathResultWithErrDotDisabled`
- `TestLegacyOwnershipScannerRejectsDatedActiveFiles`

Their measured classifications remain as listed above. Native CMD preflight
was selected next and is recorded in the following section; this earlier run
still had all five failures. Go formatting,
`go vet ./internal/artifact ./internal/repoinfo ./internal/toolchain` and
`git diff --check` passed.

The exact committed candidate repeated native CMD version/inventory, server
generate/verify and combined server/client generate/verify with exit 0 throughout
in **25.582 seconds**, including revision/status/version probes. The checkout
remained clean. Bash and WSL were not invoked. This does not certify an actual
worker sandbox, C# compilation, complete Windows acceptance or release readiness.

[CI run 37167444887](https://github.com/Kuaiwa-Network/common/actions/runs/37167444887)
passed complete Linux acceptance and the expanded focused native Windows job
at source `e5c82b7`. This does not replace the pending complete Windows suite.
The documentation follow-up passed all **73 relevant skill/reference tests**
in **0.382 seconds**, with no failures, errors or skips. Measured counts, skip
IDs, record links, privacy patterns and whitespace checks passed.

UTF-8 logs, revision, versions, duration and hashes remain local. The initial
two-test reproduction log SHA-256 is
`af01d8af37856eba1e19e138dfa9328d6703ee41d8a0796a1d4fb1fe0a2d1e86`;
the expanded passing selection's log SHA-256 is
`3c25eaa6f561921200843aecf52a2b2cb1eb213b060f7208ae595bb1ec3e11c1`;
the three-package result's log SHA-256 is
`8ab6ac34bc3cad3ea85afefe8c7b4b62c2885639a4c7484975a66ed1c40738b9`.
The new server-generation log SHA-256 is
`569e0de6198c567af557679704fcaad13d02b7625b7dbaaf491e69d1ba9a6799`;
the combined-generation log SHA-256 is
`7e6ca635e4e84990c76ab9e4dc2499bb50911d780fed51a5ab3c33cd3743df05`.
Real worker generators/gates and real Feishu access remain pending. No parked
job was resumed, and no production deployment or feature enablement occurred.

## Native Windows CMD follow-up (2026-10-04; partial)

GitHub confirms common #149 merged as
`95f600827853d247cb2731535e3e1390a6f0403e` and the documentation-only FarmBot #88
merged as `3721c003feeb292a1ae7b1d46c3d6772d8b8faa9`. This next code branch starts
from that exact common merge on the **production Windows host in the separate
development checkout**. Python 3.13.16 started with `PYTHONUTF8=1`. The selected
native CMD/Go 1.25.1/protoc 35.1 environment, inherited-selector sanitization,
private caches and fresh scratch outputs were retained. These checks used the
ordinary owner token outside the desktop app sandbox, without UAC elevation;
the token's symlink limitations are unchanged. No production configuration,
ledger, service or credential was read or changed.

The merged launcher's malformed-Git test reproduced a real CMD error: an empty
identity leaves `FARM_COMMON_COMMIT` undefined, and substring expansion then
causes a syntax-error exit 255 before the owned temporary directory is removed.
Pipe-prefixed output is skipped by the existing `for /f eol=|` reader and reaches
the same error. The original fixture shared TEMP across its cases, so the leaked
directory could also prevent a subsequent uppercase-identity case from reaching
Git. That secondary failure did not establish acceptance of uppercase identities.

[common PR #150](https://github.com/Kuaiwa-Network/common/pull/150), subsequently
merged as `c3aa16f42b50eee9ba9307ac05b91a33afdcc696`, adds an undefined-variable
guard before substring validation. The existing
single-line, exact 40-character lowercase-hex check, Go pinning and owned
nonrecursive cleanup remain intact. The fixture now isolates TEMP per case and
adds whitespace, pipe, extra-line and blank-line identities. It requires exit 2,
an empty temporary directory, no Go run and no Go version probe after invalid
Git identity. Go-version cases still require that their preflight probe ran.
No containment/ownership check was weakened and no skip was added.

The stronger regression failed before the application change for missing and
pipe-prefixed identity in **4.198 seconds**. The working fix based on `95f6008`
then produced these measured native results:

- `go test -json -count=1 -run '^TestWindowsLauncher' ./internal/repoinfo`:
  **five top-level tests passed, zero failures or skips**, in **17.683 seconds**.
- The expanded Windows CI selection (language, cleanup, cancellation and all
  launcher regressions): **35 top-level tests, 33 passed, two existing explicit
  production-fixture skips, zero failures**, in **18.553 seconds**. Its six
  existing capability subtest skips (case-sensitive names and symlink creation)
  are already in the 37-event inventory above.
- `go test -json -count=1 ./internal/artifact ./internal/repoinfo ./internal/toolchain`:
  **170 top-level tests: 152 passed, four failed, 14 skipped**, in
  **38.112 seconds**. Every one of the **37 skip IDs** matches the prior baseline.
  This selection is not the complete common suite or complete Windows acceptance.
- Go formatting, `go vet ./internal/artifact ./internal/repoinfo ./internal/toolchain`
  and whitespace checks passed. The Windows CI job now includes all native
  launcher tests; complete Linux acceptance remains required.

The remaining failed top-level tests are:

- `TestMaterializeModuleRequiresFreshChildOfPrivateParent`: the Windows
  `public_parent` fixture does not change permissions before expecting rejection;
  review the fixture and actual Windows private-parent enforcement together.
- `TestSnapshotterRejectsUnsafeTreesFreshnessAndOverlap`: the `newline_path`
  fixture cannot create its Windows-invalid filename.
- `TestResolveExecutableRejectsRelativeLookPathResultWithErrDotDisabled`:
  the readonly fixture fails during cleanup; preserve relative-lookup rejection.
- `TestLegacyOwnershipScannerRejectsDatedActiveFiles`: Windows path matching
  needs review.

At committed source `b7959f483bae7b11822e4c3483188b58684fb578`, native CMD
version/inventory, server generate/verify and combined server/client
generate/verify all exited 0 in **25.736 seconds**, including revision, status
and version probes. The checkout remained clean and Bash/WSL were not invoked.
This does not verify C# compilation, the actual worker sandbox, remaining
repository gates or real Feishu access.

The initial hosted Windows job at `b7959f4` exposed a separate path-spelling
fixture mismatch: its TEMP contains an 8.3 short alias, so a captured child CWD
did not equal the long path expected by the test. Intermediate candidate
`dd2da16920dddc5dc85e62c1ab41b186b1fe032f` canonicalizes that fixture's created
temporary inputs before use. Exact child paths, arguments, tool resolution and
environment assertions remain intact; production launcher path handling is
unchanged. The expanded selection passes again locally with the same **33
passes and eight skip events**, in **18.219 seconds**.

That hosted rerun then reached a second difference: CMD preserves an inherited
`SystemRoot` key's spelling when updating its value, while the test requires
exactly one nonempty uppercase `SYSTEMROOT` entry. An explicit mixed-case
fixture reproduced that failure locally in **4.127 seconds**. The launcher now
removes that key before recreating it from the already validated system root;
the duplicate-entry and canonical-spelling assertions remain intact. With both
corrections, the focused selection passes **33 top-level tests, two existing
fixture skips and six existing capability subtest skips**, in **17.902 seconds**.
At final source `048334d93c258680f036ffdb262adf91b26d2d8c`, the broader selection
still has **152 passed, four failed, 14 skipped**, in **36.764 seconds**, with
the same four failure IDs and all 37 skip IDs. Its native CMD smoke sequence
also passes with a clean checkout in **26.182 seconds**, without Bash or WSL.
Go vet and whitespace checks pass.
[CI run 37169189943](https://github.com/Kuaiwa-Network/common/actions/runs/37169189943)
passed complete Linux acceptance and the focused native Windows job on the
final head. This does not replace complete Windows producer acceptance. The
earlier failed jobs are not passing evidence.

The documentation follow-up passed all **73 relevant skill/reference tests**
in **0.393 seconds** (**0.514 seconds** including Python startup), with no
failures, errors or skips. Measured counts, historical skip inventory, links,
privacy patterns and whitespace checks passed.

UTF-8 logs, exact revisions, versions, durations and hashes remain local. The
stronger pre-fix regression log SHA-256 is
`099b9d56255e2d63be481be386258a336064ed9e4d78eb033c8e23deb1caf0f7`;
the five-launcher log SHA-256 is
`606154360a20b88930a7754a87f79d8b57bc505fbf337a4a9a6e42da69fb2e94`;
the expanded passing selection's log SHA-256 is
`02e5cbed378233e3814893412cf4fcdbc89bbb6daa4c6ea273acb82f0a5a3977`;
the three-package result's log SHA-256 is
`f5d0f856f9db3e941657a2df0d591ffea9d83c59de6bf6f48cee586f4253d8db`.
The server-generation log SHA-256 is
`4f1c6bbfaf4b90748f5e6bf5fcd18d3f0bc539072f7c0d7d3162034ce4741a2a`;
the combined-generation log SHA-256 is
`3713cc1a2c797f05b954c79c1d80803c0b31fde8ed79df2a604d486d23b0b1e6`.
The passing fixture-follow-up log SHA-256 is
`0fb3344e5cef4986d849e7cce6b42f92d07ea5e3a862d582c63fd8329805424e`.
The mixed-case pre-fix regression log SHA-256 is
`f3d78eefa2a02371dfec954b7cea99e0a3b3a2a45218e353accabf852124745d`;
the final focused selection's log SHA-256 is
`19d448af58aa47c755179867464729ba35524664e3f0b31374208c1b3b5aaa44`;
the final three-package log SHA-256 is
`be3662e9b43245d74bd3735f8f115597d34b10d412c8c058b8b05a8e559e008b`.
The final server-generation log SHA-256 is
`9af71079e274b2f4ce3cabee670cbb64b43237e1c224e48c1f1df22149b1dd97`;
the final combined-generation log SHA-256 is
`cef5f044e66652bc9170b6a8676593faf630b65178ad4c174c86fddf1bb1e6d0`.
The private-parent investigation selected next is recorded below. Untested Windows workers,
Feishu reads and release prerequisites remain pending; no parked job resumed.

## Native Windows parent-policy follow-up (2026-10-04; partial)

GitHub confirms FarmBot #89 merged as
`c26e4f3bb8fce0e6de76ba057075deb92ad6992d`. Common #150 was still open when this
investigation began, then the operator merged it as
`c3aa16f42b50eee9ba9307ac05b91a33afdcc696`. Its tree matches the previously
tested `048334d`. The independent parent-policy branch was rebased onto that
exact merge before broader verification. All work used the **production Windows
host in the separate development checkout**, the ordinary owner token outside
the app sandbox, Python 3.13.16 with `PYTHONUTF8=1`, native pinned Go 1.25.1 and
the previously described environment sanitization/private caches. No production
configuration, ledger, service, credential, account setting or ACL was changed.

The original `TestMaterializeModuleRequiresFreshChildOfPrivateParent/public_parent`
failure reproduced at merged `95f6008` in **1.049 seconds**. That fixture creates
a canonical temporary parent, changes it to 0755 only on Unix, then expects
rejection on both platforms. Its unchanged Windows parent is not evidence of
an unsafe ACL being accepted. The active common
[design contract](https://github.com/Kuaiwa-Network/common/blob/c3aa16f42b50eee9ba9307ac05b91a33afdcc696/docs/superpowers/specs/2026-08-09-common-owned-config-protobuf-pipeline-design.md#L195)
explicitly treats a caller-trusted output/system-temp parent as a precondition;
canonical no-follow/identity checks do not certify ownership or DACL safety.
Windows does not emulate Unix 0700 directory permissions here. The module
implementation and this fixture are unchanged by #150.

[common PR #151](https://github.com/Kuaiwa-Network/common/pull/151), subsequently
merged as `77f0056e7c3a9ef74dd68774fe616d4443a4d051`, corrects that fixture to
match the existing platform contract. Unix retains
0755-parent rejection. Windows creates a fresh module under the caller-controlled
canonical temporary parent and verifies its exact path, exact three filenames
and retained document bytes. Relative paths, noncanonical paths and existing
roots still must fail. Existing-marker validation now also fails if its evidence
was deleted or cannot be read. README states the existing Windows trust
precondition, and the focused Windows job now executes the parent-policy test.
No application behavior, containment/identity check or skip was changed.

The corrected standalone parent test passes **one top-level test and all four
subcases, zero failures or skips**, in **1.294 seconds**. At exact rebased
candidate `e56845240d55cbc7552dbeb6eb8640e5390180be`:

- The existing focused language/cleanup/launcher selection plus parent policy
  ran **36 top-level tests: 34 passed, two existing explicit production-fixture
  skips, zero failures**, in **25.475 seconds**. Six existing capability subtest
  skips remain; all eight skip IDs are already in the earlier inventory.
- `go test -json -count=1 ./internal/artifact ./internal/repoinfo ./internal/toolchain`
  ran **170 top-level tests: 153 passed, three failed, 14 skipped**, in
  **42.250 seconds**. All **37 skip IDs** match the previous CMD baseline. The
  focused and broader selections ran concurrently; these are their individual
  wall durations, not a summed suite duration.
- Go formatting, `go vet ./internal/toolchain` and whitespace checks passed.
  The diff contains a test fixture, CI coverage and README only. Native generation
  was not repeated because executable behavior and generator inputs are unchanged;
  the previous verified #150 generation evidence remains applicable.

The remaining failed top-level tests are:

- `TestResolveExecutableRejectsRelativeLookPathResultWithErrDotDisabled`:
  Windows executable-fixture cleanup; the following diagnosis corrects the
  initial readonly classification.
- `TestSnapshotterRejectsUnsafeTreesFreshnessAndOverlap`: its `newline_path`
  fixture attempts a Windows-invalid filename.
- `TestLegacyOwnershipScannerRejectsDatedActiveFiles`: Windows path matching.

[CI run 37171263841](https://github.com/Kuaiwa-Network/common/actions/runs/37171263841)
passed complete Linux acceptance and the focused native Windows job at `e568452`.
This three-package measurement does not establish complete common Windows
acceptance, a trusted service account's ACLs or actual worker readiness.

The documentation follow-up passed all **73 relevant skill/reference tests**
in **0.708 seconds** (**0.968 seconds** including Python startup), with zero
failures, errors or skips. Measured counts, all historical skip records, local
record links, the design-contract line, privacy patterns and whitespace passed.

UTF-8 logs, revisions, versions and duration metadata remain local. The original
fixture reproduction log SHA-256 is
`e25f264e928356826b23aabdf1178095e2f51001b222381474fa8777fa6340b4`;
the corrected standalone test's log SHA-256 is
`bbf6db95647fd5dfa495685ccc1de420cff5cee5d5a05a0003dec371e6cc453d`;
the expanded focused selection's log SHA-256 is
`bf14537bad0d6fd56a4e1204df2d80c1eb83da8befd13b3cd7c5adaec6c62e39`;
the three-package result's log SHA-256 is
`5a38f9aada328f32685bf8fd4c9d23b11ee106cd0496e08aa3fabf58b19f205e`.
The executable-fixture investigation selected next is recorded below, preserving
relative lookup rejection and ownership checks. Windows workers, native repository gates,
real Feishu reads and the remaining release prerequisites stay pending. No
parked job resumed and no production deployment or feature enablement occurred.

## Native Windows executable-fixture follow-up (2026-10-04; partial)

GitHub confirms common #151 merged as
`77f0056e7c3a9ef74dd68774fe616d4443a4d051` and FarmBot #90 as
`370d10b5dbc61dca0ba92052986c934c58967587`. The common merge's tree matches
tested source `e568452`; this fixture branch starts from that exact merge.
Checks used the **production Windows host in the separate development checkout**,
the ordinary owner token outside the app sandbox, Python 3.13.16 started with
`PYTHONUTF8=1`, native Go 1.25.1/protoc 35.1 and the previously described
inherited-selector sanitization, private caches and fresh scratch. No production
configuration, ledger, service, credential, account setting or ACL was changed.

`TestResolveExecutableRejectsRelativeLookPathResultWithErrDotDisabled`
reproduced its TempDir cleanup error on exact merged common in **2.918 seconds**.
The relative `LookPath` precondition and application rejection had already
passed; cleanup returned access denied. A diagnostic-only run in **3.008 seconds**
confirmed the fixture and running test executable are both writable
and share file identity. This supersedes the earlier readonly classification.
The fixture hardlinks the active test image; Windows deletion rules restrict
files held open or mapped, as documented by
[Microsoft's DeleteFileW reference](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-deletefilew).
An independent copy removes that coupling and passes cleanup.

[common PR #152](https://github.com/Kuaiwa-Network/common/pull/152)
copies the executable into a fresh fixture on every platform. It asserts a
different file identity, preserves the real relative `LookPath` result with
`GODEBUG=execerrdot=0`, requires the application to return no path and the
non-absolute-path rejection, and explicitly removes the fixture afterward.
TempDir cleanup still runs. Native Windows CI now executes this case. The
production resolver, containment/ownership checks and existing skips are unchanged.

The copied standalone case passes **one top-level test, zero failures or skips**,
in **1.239 seconds**. After strengthening the rejection assertion, the working
fix based on `77f0056` produced these measurements:

- The focused language/cleanup/launcher/parent-policy selection plus relative
  lookup ran **37 top-level tests: 35 passed, two existing explicit
  production-fixture skips, zero failures**, in **18.662 seconds**. Six existing
  capability subtest skips remain; all eight skip IDs match the prior selection.
- `go test -json -count=1 ./internal/artifact ./internal/repoinfo ./internal/toolchain`
  ran **170 top-level tests: 154 passed, two failed, 14 skipped**, in
  **36.390 seconds**. All **37 skip IDs** match the preceding parent-policy run.
  Focused and broader selections ran concurrently; their durations are measured
  separately.
- Go formatting, `go vet ./internal/toolchain` and whitespace checks passed.
  The diff contains the test fixture and CI coverage only. Generation was not
  repeated because executable behavior and generator inputs are unchanged.

Committed source `7884d9211f9d148cebb354192c33515710bb49c4` records the tested
fixture. [CI run 37173629731](https://github.com/Kuaiwa-Network/common/actions/runs/37173629731)
passed complete Linux acceptance and focused native Windows coverage at that
exact head. The remaining failed top-level tests are:

- `TestSnapshotterRejectsUnsafeTreesFreshnessAndOverlap`: its `newline_path`
  fixture attempts a filename Windows cannot create.
- `TestLegacyOwnershipScannerRejectsDatedActiveFiles`: Windows path matching.

The documentation follow-up passed all **73 relevant skill/reference tests**
in **0.376 seconds** (**0.492 seconds** including Python startup), with no
failures, errors or skips. Measured counts, historical skips, record links,
identity/writability diagnostics, privacy patterns and whitespace checks passed.

These measurements cover three packages, not complete common Windows acceptance,
actual workers or service-account readiness. UTF-8 logs, revisions, selected
versions and duration metadata remain local. The initial failure log SHA-256 is
`1e9a5cc88584d4d8b2d644c756d9fac409f6bd7a3c05f548b8773cde34ece7b1`;
the diagnostic reproduction log SHA-256 is
`798f91fb715d73de4c5657a66933a9cf8b68e892f527e824bcf78899c5a68df7`;
the passing standalone fixture log SHA-256 is
`89a4aa27527ac37aa9f519bb889a7aee77e81e95fa999b0fe069dffadf7c2632`;
the expanded focused selection log SHA-256 is
`97700554a7753872c19052e9c8798b538fb7c58ec0f384905d684357973d94cf`;
the three-package result log SHA-256 is
`213c3258f2590090feef13854e86476a0828a58bc26a29775304d2b0cfe5ccb2`.
The following newline-fixture record completes the selected Windows-invalid
filename correction, preserving unsafe-tree rejection. Native worker/gate
acceptance, Feishu reads and the remaining release prerequisites remain pending.
No parked job resumed and no production deployment
or feature enablement occurred.

## Native Windows newline-fixture follow-up (2026-10-04; partial)

GitHub confirms common #152 merged as
`e3475ea83b80334021e72f9d4c016d6cc4b75e50` and FarmBot #91 as
`6e0ceb552e9dbf021e9845f5769c3b251a60d277`. This fixture branch starts from
that exact common merge. Checks used the **production Windows host in the
separate development checkout**, the ordinary owner token outside the app
sandbox, Python 3.13.16 started with `PYTHONUTF8=1`, native Go 1.25.1/protoc 35.1
and the previously described inherited-selector sanitization, private caches and
fresh scratch. Production configuration, ledger, service, credentials, account
settings and ACLs were untouched.

`TestSnapshotterRejectsUnsafeTreesFreshnessAndOverlap` reproduced its
`newline_path` fixture-creation failure on exact merged common in **1.291 seconds**.
Windows rejects ordinary filenames containing these control characters before
the snapshotter can read them; see
[Microsoft's filename rules](https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file).
This is an invalid platform fixture, rather than measured snapshotter acceptance
of an unsafe source entry.

[common PR #153](https://github.com/Kuaiwa-Network/common/pull/153)
checks both LF and CR through the existing `validateLogicalPath` guard used by
`SnapshotSource`. On native Windows both physical filename attempts must return
`ERROR_INVALID_NAME` (123), and their source directories must remain empty.
On Unix both files are still created, the snapshotter must reject them specifically
as newline paths, and failed destinations must be absent. The shared path guard
therefore runs on Windows; a physical newline source entry cannot reach the
snapshotter through this native filesystem fixture. This is not Windows
end-to-end coverage of such an entry. The whole unsafe-tree test is now included
in focused native Windows CI. No runtime code, ownership/containment guard or
existing skip changed, and no new skip was added.

Measured native results for the working fix based on `e3475ea`:

- The corrected standalone unsafe-tree test passes **one top-level test** in
  **1.193 seconds**, with zero failures. Both LF/CR cases run and pass. The same
  two existing subtest skips remain: `symlink` (ordinary token lacks privilege)
  and `case_collision` (case-insensitive filesystem).
- The expanded language/cleanup/launcher/parent-policy/relative-lookup/unsafe-tree
  selection runs **38 top-level tests: 36 passed, two existing explicit
  production-fixture skips, zero failures**, in **18.098 seconds**. Eight existing
  capability subtest skips remain. All ten skip IDs equal the previous selection's
  eight IDs plus the unsafe-tree test's two existing IDs; none was newly introduced.
- `go test -json -count=1 ./internal/artifact ./internal/repoinfo ./internal/toolchain`
  runs **170 top-level tests: 155 passed, one failed, 14 skipped**, in
  **36.163 seconds**. All **37 skip IDs** match the preceding executable-fixture
  run and the original inventory above. Focused and broader selections overlapped;
  durations are measured separately.
- Go formatting, `go vet ./internal/artifact` and whitespace checks pass.
  The diff contains test and CI changes only. Generation was not repeated because
  executable behavior and generator inputs are unchanged.

Committed source is `8fd21f8e74b205793338740c8742b813132cd999`.
[CI run 37174882807](https://github.com/Kuaiwa-Network/common/actions/runs/37174882807)
passed complete Linux acceptance and focused native Windows coverage at that
exact head.
The sole remaining failed top-level test is
`TestLegacyOwnershipScannerRejectsDatedActiveFiles` (initially classified as
Windows path matching; the following record corrects that diagnosis), selected
as the next separate correction. These three-package results do not
establish complete common Windows acceptance, actual worker readiness or
service-account capabilities.

The documentation follow-up passes all **73 relevant skill/reference tests**
in **0.362 seconds** (**0.480 seconds** including Python startup), with zero
failures, errors or skips. Measured counts, historical skips, new record links,
privacy patterns and whitespace checks pass. UTF-8 logs, revisions, selected versions
and duration metadata remain local. The initial failure log SHA-256 is
`4e24d9400f23aa7fcf7b181d5ffe074820d7ebf94e3a56552b41e1dff88a79c2`;
the passing standalone fixture log SHA-256 is
`db397c7d38001b4b53db8d45ba210d681f04745d2fa1d0761ea9c9d7ee5908e9`;
the expanded focused selection log SHA-256 is
`f6a8fced05cbc5eb1519648886ab8d373217374dc1a388cd7f045ff43151a786`;
the three-package result log SHA-256 is
`3ca6f742b2dd34cb720bb8f601133f4da524cec2caa7757c6b2c231beda24130`.
Native worker/gate acceptance, real Feishu reads and the remaining release
prerequisites stay pending. No parked job resumed and no production deployment
or feature enablement occurred.

## Native Windows ownership-scanner follow-up (2026-10-04; partial)

GitHub confirms common #153 merged as
`7902de856c27964ad869f18761a02f27d9a965ed` and FarmBot #92 as
`1b5d59a8571d85913ca7a5bf778ef1e21328c46c`. The scanner branch starts from
that exact common merge. Checks used the **production Windows host in the
separate development checkout**, the ordinary owner token outside the app
sandbox, Python 3.13.16 started with `PYTHONUTF8=1`, native Go 1.25.1/protoc 35.1,
inherited-selector sanitization, private caches and fresh scratch. Production
configuration, ledger, service, credentials, account settings and ACLs were
untouched.

`TestLegacyOwnershipScannerRejectsDatedActiveFiles` reproduced on exact merged
common in **0.849 seconds**, with no skips. The scanner's test helper classified
scripts without recognized extensions only when Unix execute bits and a shebang
were both present. Windows does not provide those execute bits, even after
`os.Chmod(0755)`; Go documents that only the owner-writable bit controls the
Windows read-only attribute in its
[pinned Chmod reference](https://pkg.go.dev/os@go1.25.1#Chmod).
The extensionless and Markdown script fixtures therefore each matched zero of
the eight required legacy markers. This supersedes the earlier path-matching
classification; no separator correction was required.

[common PR #154](https://github.com/Kuaiwa-Network/common/pull/154)
conservatively includes every regular shebang file in the Windows test scan,
retaining the Unix execute-bit condition and ordinary historical-text exemptions.
The new `TestActiveSourceCandidateUsesHostScriptSemantics` has seven cases:
extensionless/Markdown scripts at 0755, the same two at 0644, ordinary executable
text, historical Markdown, and a recognized CMD file. Native Windows additionally
must expose no Unix execute bits in these fixtures. All dated active-file
canaries and exact historical documentation exemptions remain enforced.
Focused Windows CI now includes the actual repository scan, both existing
ownership scanner tests and the new regression. Only tests and CI changed:
runtime code, containment/ownership checks, generator inputs and existing skips
are unchanged. Fixture shebangs are read as text; Bash is not invoked or required
by these native checks.

Measured results for the working fix based on `7902de8`:

- The scanner, historical-exemption and new host-semantics tests pass **three
  top-level tests** in **1.026 seconds**, with zero failures or skips. All seven
  new regression cases run and pass.
- The expanded focused CI selection runs **42 top-level tests: 40 passed,
  two existing explicit production-fixture skips, zero failures**, in
  **18.616 seconds**. Eight existing capability subtest skips remain; all ten
  skip IDs match the previous focused selection.
- `go test -json -count=1 ./internal/artifact ./internal/repoinfo ./internal/toolchain`
  runs **171 top-level tests: 157 passed, zero failed, 14 skipped**, in
  **44.294 seconds**. The one extra top-level test is the new host-semantics
  regression; all seven previously identified failures are now resolved.
  All **37 skip IDs** match the preceding newline-fixture run and original
  inventory above. Focused and broader selections overlapped; durations are
  measured separately.
- Go formatting, `go vet ./internal/repoinfo` and whitespace checks pass.
  Native generation was not repeated because runtime behavior and generator
  inputs are unchanged.

Committed source is `66ce51a785b7dd3a015773e6a19ac4b7baad4bc7`.
[CI run 37176606613](https://github.com/Kuaiwa-Network/common/actions/runs/37176606613)
passed complete Linux acceptance and focused native Windows coverage at that
exact head. Passing these three packages does not
establish complete Windows producer acceptance, actual worker readiness or
service-account capabilities.

The wider native `go test -json -count=1 ./...` check at exact committed source
ran **560 top-level tests: 508 passed, 20 failed, 32 skipped**, in
**64.821 seconds**. There were **60 skip events**, including subtests. This is
new coverage outside the three boundary packages; those packages remain green.
The initial wrapper omitted `RUNNER_TEMP`, which two native acceptance tests
require. A focused recheck set it only to fresh local scratch, leaving host
settings untouched: **two top-level tests, one passed and one failed, zero
skips**, in **2.910 seconds**. `TestNativeWindowsNoReplacePublication` passes
its actual NTFS backend and race checks. `TestNativeWindowsGitState` gets past
its streaming/index assertions but fails its slow-filter timeout assertion:
Capture returns nil instead of a deadline error. Descendant containment is not
certified by that incomplete scenario. No full-module rerun followed this setup
correction, so the initial 20-failure count is not a post-correction count.

The ordinary token's missing symlink capability also affects definition/archive
reparse fixtures. Other measured gaps include Unix mode expectations, relative
Git-executable cleanup, Git top-level output assumptions, saved publication
identity, unsupported archive publication, archive-error classification and
compression-cleanup evidence. These remain separate investigations; the
scanner-only change does not alter their packages. No checks were weakened or
new skips added. The next focused correction is the Git resolver's relative
executable fixture, preserving fail-closed path resolution.

The documentation follow-up passes all **73 relevant skill/reference tests**
in **0.437 seconds** (**0.560 seconds** including Python startup), with zero
failures, errors or skips. UTF-8 logs, revisions, selected versions
and duration metadata remain local. The initial failure log SHA-256 is
`ff01c08fe1ee5d475d47b6c7446f772873bfe40f3cda26fb3dc343e84f2cd431`;
the passing scanner/exemption/regression log SHA-256 is
`dff4ba7240b0291e62b7e006dd6ad52ff5f64fde61a67cad1e6bc52ddadc1099`;
the expanded focused selection log SHA-256 is
`9c70f2889a041d43d8da3c2d25d7144e98e9c025d6d2bb8ffa29a016880e393b`;
the three-package result log SHA-256 is
`1b91096a64cf956eaaf9154280cd90746e3298a33050f4955bfdaa62d072871f`.
The full-module log SHA-256 is
`2d2899361d016c7d3f30e87dfe4ac1d4a30fe7255436edd7ab9b50b501a7b288`;
the native setup follow-up log SHA-256 is
`ab9382fc401062d68471f8a4d1e6fb1316d820c2fee8552193b4f401224c2ddd`.

The full-module failed top-level tests are:

- `TestFilePublicationReconciliationAndCleanupIdentity`
- `TestFilePublicationRejectsInitiallyExistingForeignTypes`
- `TestFilePublicationSuccessAndNoReplace`
- `TestNativeWindowsArchiveExtractionReparse`
- `TestNativeWindowsArchiveInputReparse`
- `TestNativeWindowsArchiveNoReplace`
- `TestNativeWindowsGitState`
- `TestNativeWindowsNoReplacePublication`
- `TestPublishCommitsExactStageIdentityAndCloseIsNilAfterPublication`
- `TestReformatFileIfSingleLineCanonicalAndIdempotent`
- `TestResolveGitRejectsRelativeLookPathResultWithErrDotDisabled`
- `TestResolveTopLevelUsesOneSanitizedBoundedGitProbe`
- `TestTypeDefsAreWired`
- `TestVerifyInstanceLocalHooks`
- `TestVerifyLimitMatrixAndSecondPassDisagreement`
- `TestVerifyProductionArchiveTwoPass`
- `TestVerifyPublicationBoundarySeams`
- `TestVerifyPublicationRootCloseDiagnosticCommitBoundary`
- `TestVerifyRejectsHostileArchiveBeforeOutput`
- `TestWriteCompressionCleanupFailuresAreRuntimeAndUnpublished`

The previous 37 boundary skip IDs are all present. The additional 23 existing
skip events exposed by this broader selection are:

| Package | Test | Reason |
| --- | --- | --- |
| `cmd/configgen` | `TestCheckConfigArtifactScript` | Unix producer shell behavior runs only on Darwin and Linux |
| `cmd/configgen` | `TestCheckConfigArtifactScriptCompileOwnershipRegression` | Unix producer shell behavior runs only on Darwin and Linux |
| `cmd/configgen` | `TestCheckConfigArtifactScriptCompileOwnershipRegressionChild` | Unix producer shell behavior runs only on Darwin and Linux |
| `cmd/configgen` | `TestCheckConfigArtifactScriptRejectsCopiedNonGitCheckoutBeforeArtifactWork` | Unix producer shell behavior runs only on Darwin and Linux |
| `cmd/configgen` | `TestConfigArtifactPipelineCleanupRejectsNamespaceReplacement` | Unix producer shell behavior runs only on Darwin and Linux |
| `cmd/configgen` | `TestConfigArtifactPipelinePackResolverBehavior` | Unix producer shell behavior runs only on Darwin and Linux |
| `cmd/configgen` | `TestConfigArtifactPipelineUploadRevalidatesExactHandoff` | Unix producer shell behavior runs only on Darwin and Linux |
| `cmd/configgen` | `TestInventoryDefaultSourceRejectsSymlinkEscape` | Ordinary token lacks symlink capability |
| `cmd/configgen` | `TestPackConfigArtifactScript` | Unix producer shell behavior runs only on Darwin and Linux |
| `cmd/configgen` | `TestPackConfigArtifactScriptRealBehaviorCallGraphAndImmutability` | Unix producer shell behavior runs only on Darwin and Linux |
| `cmd/configgen` | `TestPbOutAndClientOutMustDiffer` | Ordinary token lacks symlink capability |
| `internal/generate` | `TestVerifyPreservesReplacedPrivateWorkAndLeavesAgainstUnchanged` | Task 10 owns native Windows outputdir identity-injection coverage for held-directory replacement |
| `internal/generate` | `TestVerifyTreatsAgainstSwapAfterOpenAsRuntimeWithoutPrivateWork` | Native held-root replacement is covered by the Windows identity-injection gate |
| `internal/outputdir` | `TestClosePreservesReplacementAndAggregatesCleanupFailures` | Held no-FILE_SHARE_DELETE root prevents native replacement; Task 10 owns native injection coverage |
| `internal/outputdir` | `TestPrivateWorkClosePreservesReplacementAndReportsCleanupFailure` | Held no-FILE_SHARE_DELETE root prevents native replacement; Task 10 owns native injection coverage |
| `internal/outputdir` | `TestPublishPreservesRacedTargetsAndCleansOnlyOwnedStage/symlink` | Native Windows reparse coverage runs in native_windows_test.go |
| `internal/outputdir` | `TestPublishRefusesStageReplacementBeforeNoReplaceCall` | Held no-FILE_SHARE_DELETE stage prevents native replacement; Task 10 owns native injection coverage |
| `internal/outputdir` | `TestPublishRejectsRacedCaseFoldBasenameBeforeSyscall` | Windows case-insensitive namespace cannot create the distinct raced basename |
| `internal/outputdir` | `TestPublishUsesIdentityReconciliationAsTerminalBoundary/final_identity_commits_despite_syscall_diagnostic` | Held no-FILE_SHARE_DELETE stage prevents a test-side rename |
| `internal/outputdir` | `TestTransactionPrepareWorkCreationFailurePreservesReplacedChildAndBlocksParentCleanup` | Held no-FILE_SHARE_DELETE work root prevents a test-side replacement |
| `internal/profile` | `TestLoadRejectsNoncanonicalProfileAndSourcePaths/profile_symlink` | Ordinary token lacks symlink capability |
| `internal/profile` | `TestLoadRejectsNoncanonicalProfileAndSourcePaths/repository-root_symlink` | Ordinary token lacks symlink capability |
| `internal/profile` | `TestLoadRejectsNoncanonicalProfileAndSourcePaths/source_symlink` | Ordinary token lacks symlink capability |

Native worker/gate acceptance, real Feishu reads and the remaining release
prerequisites stay pending. No parked job resumed and no production deployment
or feature enablement occurred.

## Native Windows Git-executable-fixture follow-up (2026-10-04; partial)

GitHub confirms [common #154](https://github.com/Kuaiwa-Network/common/pull/154)
merged as `1adc87df5beae4e1e0b620562562cf26243ac85d` and
[FarmBot #93](https://github.com/Kuaiwa-Network/farm-linear-agent/pull/93)
as `1d5cbdf7483dcce970a13114535f2c2c74d466c7`. The new branch starts from that
exact common merge. Checks used the **production Windows host in the separate
development checkout**, the ordinary owner token outside the app sandbox,
Python 3.13.16 started with `PYTHONUTF8=1`, native Go 1.25.1/protoc 35.1,
inherited-selector sanitization, private caches and fresh scratch supplied as
`RUNNER_TEMP`. Production configuration, ledger, service, credentials, account
settings and ACLs were untouched.

`TestResolveGitRejectsRelativeLookPathResultWithErrDotDisabled` reproduced on
exact merged common in **2.506 seconds**, with no skips. Its real `exec.LookPath`
returned a relative executable with `execerrdot=0`, and `ResolveGit` rejected it
as intended. Only temporary-directory cleanup failed: the fixture hardlinked
the currently running Go test image. Windows prohibits deletion of a file that
is mapped as an executable; see the
[DeleteFileW reference](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-deletefilew).
This is a fixture lifetime problem, not evidence that relative-path rejection
failed.

[common PR #155](https://github.com/Kuaiwa-Network/common/pull/155)
always copies the test executable into an independent file and asserts that
source and fixture do not share file identity. The test still exercises real
relative lookup, requires an empty resolved path and the specific
non-absolute-path error, then requires explicit fixture deletion as well as
automatic temporary-directory cleanup. Focused Windows CI adds this exact test.
Only tests and CI changed; runtime containment, ownership checks, generator
inputs and existing skips are unchanged. No Bash or MSYS execution is required.

Measured results for the working fix based on `1adc87d`:

- The corrected standalone test passes **one top-level test** in **0.940
  seconds**, with zero failures or skips. Its explicit deletion and automatic
  cleanup both succeed.
- The expanded focused CI selection runs **43 top-level tests: 41 passed,
  two existing explicit production-fixture skips, zero failures**, in
  **18.421 seconds**. Eight existing capability subtest skips remain; all ten
  skip IDs match the preceding focused selection above. The added resolver test
  actually runs and passes.
- `go test -json -count=1 ./internal/gitstate` runs **17 top-level tests:
  15 passed, two failed, zero skipped**, in **10.247 seconds**. The corrected
  resolver test passes within this package. The remaining failures are
  `TestNativeWindowsGitState` and
  `TestResolveTopLevelUsesOneSanitizedBoundedGitProbe`; there are no failed
  subtests. The former still returns nil instead of a deadline error in its
  native slow-filter timeout scenario, whose fixture/runtime classification
  remains unresolved. The latter's mock supplies Windows backslashes where
  the actual Git-output contract requires an absolute forward-slash path.
  Strict output validation remains enforced. Package and focused selections
  overlapped; durations are measured separately.
- Go formatting, `go vet ./internal/gitstate` and whitespace checks pass.
  Native generation and the complete module suite were not repeated because
  runtime behavior and generator inputs are unchanged. The preceding
  560-test/20-failure module inventory is historical, not a new count for this
  candidate; no current full-module result is inferred by subtraction.

Committed source is `3f99c166650ce1bbc8a23deef195f625d826d219`.
[CI run 37177769988](https://github.com/Kuaiwa-Network/common/actions/runs/37177769988)
passed complete Linux acceptance and focused native Windows coverage at that
exact head. The documentation follow-up passes all **73 relevant
skill/reference tests** in **0.360 seconds** (**0.476 seconds** including Python
startup), with zero failures, errors or skips.
UTF-8 logs, revisions, selected versions and duration metadata remain local.
The initial failure log SHA-256 is
`879d2f5499f4cbd51d2e0ea588c962c8e0424bf20874b1db5c12904f28cbca1d`;
the passing standalone log SHA-256 is
`c20c14a34369b87116b8e1cd6b74ff6a74233e1941f85de0aa686af64eba38f8`;
the expanded focused selection log SHA-256 is
`0b5a1b45e9140c70dba5bb0962d64ba4e57ff6bcd74339ed09ab4619a1a3b056`;
the Git-state package log SHA-256 is
`86f96ccea400d54b4ad449f8180f32be6b27eef81af4e1efab4e15c210b518f6`.
The documentation-test log SHA-256 is
`fd2892da4bec0752d40c81c6441c849adbb3785e7ae29c14d3f5ee12d06449e6`.

Next is the mocked Git top-level-output fixture, preserving strict path and
single-line validation, one bounded probe and sanitized environment assertions.
The native timeout scenario, wider module gaps, actual Windows worker/gate
acceptance and real Feishu reads remain pending. No parked job resumed and no
production deployment or feature enablement occurred.

## Native Windows Git top-level-output fixture follow-up (2026-10-04; partial)

GitHub confirms [common #155](https://github.com/Kuaiwa-Network/common/pull/155)
merged as `a9bdb462469f644f691fd84e9173d0eb1941269c` and
[FarmBot #94](https://github.com/Kuaiwa-Network/farm-linear-agent/pull/94)
as `4bd6617c1a2e330ef90fb691256752b0ac75c4b0`. The new branch starts from that
exact common merge. Checks used the **production Windows host in the separate
development checkout**, the ordinary owner token outside the app sandbox,
Python 3.13.16 started with `PYTHONUTF8=1`, native Go 1.25.1/protoc 35.1,
inherited-selector sanitization, private caches and fresh `RUNNER_TEMP` scratch.
Production configuration, ledger, service, credentials, account settings and
ACLs were untouched.

`TestResolveTopLevelUsesOneSanitizedBoundedGitProbe` reproduced on exact merged
common in **0.837 seconds**, with no skips. Its mock emitted the native Windows
root with backslashes. The strict parser correctly rejected that output before
the fixture could verify the intended successful probe. The implementation's
Git-output contract requires an absolute forward-slash path on Windows and
converts it to the native path only after validation. No runtime parser change
is needed for this failure.

[common PR #156](https://github.com/Kuaiwa-Network/common/pull/156)
models stdout with `filepath.ToSlash(root)` and retains native command working
directories and expected results. The valid path includes spaces and Unicode.
Six named malformed-output cases exercise empty output, a missing newline, an
extra line, a relative path, CRLF and NUL; Windows additionally must reject
native backslashes. Each requires an empty result and its specific error
category, so an unrelated backslash rejection cannot mask newline validation.
The platform-normalizer table additionally covers a valid Windows Unicode path
and a path containing single backslashes. The existing one-probe, exact-argument
and sanitized-environment assertions remain enforced. Focused Windows CI adds
the probe and normalization tests. Only tests and CI changed; runtime validation,
containment, ownership checks, generator inputs and existing skips are unchanged.
No Bash or MSYS execution is required.

Measured results for the working fix based on `a9bdb46`:

- The corrected probe and platform normalization tests pass **two top-level
  tests** in **0.960 seconds**, with zero failures or skips. All seven native
  malformed-output rejection cases run and pass.
- The expanded focused CI selection runs **45 top-level tests: 43 passed,
  two existing explicit production-fixture skips, zero failures**, in
  **18.502 seconds**. Eight existing capability subtest skips remain; all ten
  skip IDs match the preceding focused selection above. Both added tests and
  all seven rejection cases actually run and pass.
- `go test -json -count=1 ./internal/gitstate` runs **17 top-level tests:
  16 passed, one failed, zero skipped**, in **10.468 seconds**. The corrected
  probe, normalization test and all seven rejection cases pass within this
  package. The only failure is `TestNativeWindowsGitState`: its slow-filter
  timeout scenario still returns nil instead of a deadline error. The
  fixture/runtime classification remains unresolved, and this incomplete
  scenario does not certify descendant containment. There are no failed
  subtests. Package and focused selections overlapped; durations are measured
  separately.
- Go formatting, `go vet ./internal/gitstate` and whitespace checks pass.
  Native generation and the complete module suite were not repeated because
  runtime behavior and generator inputs are unchanged. The earlier
  560-test/20-failure module inventory is historical, not a new count for this
  candidate; no current full-module result is inferred by subtraction.

Committed source is `d916b2f41eeed8c83f49ab0e2c6fb4a93beb43c3`.
[CI run 37178562032](https://github.com/Kuaiwa-Network/common/actions/runs/37178562032)
passed complete Linux acceptance and focused native Windows coverage at that
exact head.
The documentation follow-up passes all **73 relevant skill/reference tests**
in **0.364 seconds** (**0.481 seconds** including Python startup), with zero
failures, errors or skips.
UTF-8 logs, revisions, selected versions and duration metadata remain local.
The initial failure log SHA-256 is
`8d473f876af5fe03c8ef91dfdb820d4487ed77f1812f6d6fbb0f695f45f3a532`;
the passing probe/normalization log SHA-256 is
`093e8b24420a7093c61b5701dae59980df089c6818df64f89be9fd2b4e8c9acc`;
the expanded focused selection log SHA-256 is
`ee4a2f90f254b811df73b6f329766ff09f68616dd99ffc9baf2388f163e608c8`;
the Git-state package log SHA-256 is
`5bfaaaa4552253715f6048e2e28cd6c6a93c45540cbd3bea0ffaeffb258ee355`.
The documentation-test log SHA-256 is
`32f9cc88457536b445a22e4b688ffb62854ed0ca2b952b4f58a747778daf8f73`.

Next is the native Git slow-filter timeout scenario, distinguishing fixture
behavior from application timeout/containment regressions without weakening
bounded execution or adding skips. Wider module gaps, actual Windows worker/gate
acceptance and real Feishu reads remain pending. No parked job resumed and no
production deployment or feature enablement occurred.

## Native Windows Git timeout follow-up (2026-10-04; partial)

GitHub confirms [common #156](https://github.com/Kuaiwa-Network/common/pull/156)
merged as `4cf13ae721751e09506d5d9b4b1bfe73ac98c01b` and
[FarmBot #95](https://github.com/Kuaiwa-Network/farm-linear-agent/pull/95)
as `371d5fc67d4ccc532c51e11caf1b8c383781e478`. The new branch starts from that
exact common merge. Checks used the **production Windows host in the separate
development checkout**, the ordinary owner token outside the app sandbox,
Python 3.13.16 started with `PYTHONUTF8=1`, native Go 1.25.1/protoc 35.1,
Git 2.54.0.windows.1 and Git LFS 3.7.1, inherited-selector sanitization, private
caches and fresh `RUNNER_TEMP` scratch. Production configuration, ledger,
service, credentials, account settings and ACLs were untouched.

`TestNativeWindowsGitState` reproduced on exact merged common in **2.793
seconds**, with no skips: Capture returned nil instead of a deadline error.
The committed fixture contained nine bytes, but its replacement contained
30 bytes. Git can report this size change without hashing the file or invoking
the clean filter; its pinned
[Windows source](https://github.com/git-for-windows/git/blob/v2.54.0.windows.1/read-cache.c#L415-L461)
shows the early return before content comparison. Changing only the replacement
to different nine-byte content passes the actual timeout and descendant checks
in **5.829 seconds**, without skips. Together with the source, this identifies
the missed filter execution as a fixture problem; no runtime containment change
is needed for this failure.

[common PR #157](https://github.com/Kuaiwa-Network/common/pull/157)
keeps the indexed size, changes the bytes, forces mtime two seconds beyond the
baseline and asserts the same size plus changed mtime. The fixture filter is
required, so failed execution cannot silently fall back to unfiltered content.
The **750 ms deadline**, runtime-error classification, ten-second return bound,
real filter readiness marker and delayed descendant-survival marker check are
unchanged. The real Git process still runs through the Windows Job Object
coordinator, and focused Windows CI now includes this exact native scenario.
Only tests and CI changed; runtime validation, containment, ownership checks,
generator inputs and existing skips are unchanged. No external Bash generator
or new host tool prerequisite was introduced.

Measured results for the working fix based on `4cf13ae`:

- The hardened native test passes **one top-level test** in **5.937 seconds**,
  with zero failures or skips. The actual filter descendant starts, Capture
  returns the deadline runtime error, and the survival marker remains absent
  after the 2.5-second observation interval.
- `go test -json -count=1 ./internal/gitstate` passes **all 17 top-level tests**
  in **14.016 seconds**, with zero failures or skips. All previously identified
  Git-state failures are resolved for this development candidate.
- The expanded focused CI selection runs **46 top-level tests: 44 passed,
  two existing explicit production-fixture skips, zero failures**, in
  **19.117 seconds**. Eight existing capability subtest skips remain; all ten
  skip IDs match the preceding selection. The actual native Git timeout and
  descendant scenario runs and passes. Package and focused selections
  overlapped; durations are measured separately.
- Go formatting, `go vet ./internal/gitstate` and whitespace checks pass.
  Native generation was not repeated because runtime behavior and generator
  inputs are unchanged.

Committed source is `21c16033e4890ad8442c0b28c7c1317c8a6d27ce`.
[CI run 37179792174](https://github.com/Kuaiwa-Network/common/actions/runs/37179792174)
passed complete Linux acceptance and focused native Windows coverage at that
exact head, including the real Git timeout/descendant scenario on Windows.
The documentation follow-up passes all **73 relevant skill/reference tests**
in **0.393 seconds** (**0.508 seconds** including Python startup), with zero
failures, errors or skips.

With the Git-state package green, a fresh native
`go test -json -count=1 ./...` at that exact commit refreshes the wider inventory:
**560 top-level tests: 512 passed, 16 failed, 32 skipped**, in **60.612 seconds**.
There are **51 failed subtest IDs** and **60 skip events**, including subtests,
retained in the local UTF-8 log and sanitized inventory. Every skip ID matches
the prior full-module inventory and its recorded reasons above; no skips were
added. This is a measured current count, superseding the earlier 20-failure
inventory for this candidate. Both the real Git timeout/descendant test and
`TestNativeWindowsNoReplacePublication` pass. This proves the tested common
subprocess and NTFS scenarios under the ordinary token, not actual FarmBot
worker/gate acceptance or service readiness.

The remaining failed top-level tests are:

- `TestFilePublicationReconciliationAndCleanupIdentity`
- `TestFilePublicationRejectsInitiallyExistingForeignTypes`
- `TestFilePublicationSuccessAndNoReplace`
- `TestNativeWindowsArchiveExtractionReparse`
- `TestNativeWindowsArchiveInputReparse`
- `TestNativeWindowsArchiveNoReplace`
- `TestPublishCommitsExactStageIdentityAndCloseIsNilAfterPublication`
- `TestReformatFileIfSingleLineCanonicalAndIdempotent`
- `TestTypeDefsAreWired`
- `TestVerifyInstanceLocalHooks`
- `TestVerifyLimitMatrixAndSecondPassDisagreement`
- `TestVerifyProductionArchiveTwoPass`
- `TestVerifyPublicationBoundarySeams`
- `TestVerifyPublicationRootCloseDiagnosticCommitBoundary`
- `TestVerifyRejectsHostileArchiveBeforeOutput`
- `TestWriteCompressionCleanupFailuresAreRuntimeAndUnpublished`

The ordinary token's missing symlink capability still causes definition and
archive reparse fixture failures. Several archive publication/verification
failures report a not-supported operation; their source and failure stage need
focused investigation, rather than assuming an absent backend or weakening
no-replace/identity checks. The output-directory publication assertion,
Excel XML Unix-mode expectation and compression-cleanup evidence also remain
separate gaps. Complete native producer acceptance is pending.

UTF-8 logs, revisions, selected versions and duration metadata remain local.
The initial failure log SHA-256 is
`01cbd9eb3276df565556e1402a2acd6d273d32ca08db4051f9de9ad140f06ad7`;
the same-size-only experiment log SHA-256 is
`675b3a2cd25a9c3b877512a129c7115a4e77fe395683ebedd30541cf4b782c8a`;
the hardened native-test log SHA-256 is
`81cc500a21d92ec0bc45b5929077cc72868376399568f646a9e3ce861ed411e5`;
the expanded focused selection log SHA-256 is
`e2daa6d3e278b9a9c5e90d14192e8819ec856b97d82a6be412f29502ba5c17f6`;
the Git-state package log SHA-256 is
`7e62b8240a290cf50a5af3e092121ed7624543921d519a4c1d891c38de391940`;
the refreshed full-module log SHA-256 is
`9509f9cb54c831aeafb07861b2df6099702ff080560516e2411667a7f82c1208`.
The documentation-test log SHA-256 is
`3201d8d619bc0b6696f27430c480ca35eac3ee98982dd2bc35fa6946a980da88`.

Next is native archive publication, starting with its file/no-replace and
identity failures while keeping missing host symlink capability distinct.
Wider module gaps, actual Windows worker/gate acceptance and real Feishu reads
remain pending. No parked job resumed and no production deployment or feature
enablement occurred.

## Native Windows archive cleanup follow-up (2026-10-04; partial)

GitHub confirms [common #157](https://github.com/Kuaiwa-Network/common/pull/157)
merged as `34315e4158905493e1d1297b67c10fcf666322f7` and
[FarmBot #96](https://github.com/Kuaiwa-Network/farm-linear-agent/pull/96)
as `715af486a8ab45e17699d5ac9d85e2120018ab6b`. The new branch starts from that
exact common merge. Checks used the **production Windows host in the separate
development checkout**, the ordinary owner token outside the app sandbox,
Python 3.13.16 started with `PYTHONUTF8=1`, native Go 1.25.1/protoc 35.1,
inherited-selector sanitization, private caches and fresh `RUNNER_TEMP` scratch.
Production configuration, ledger, service, credentials, account settings and
ACLs were untouched.

The owned-temp cleanup case of
`TestFilePublicationReconciliationAndCleanupIdentity` reproduced on exact merged
common in **6.693 seconds**, with no skips. Both archive cleanup paths combine
`FILE_DISPOSITION_ON_CLOSE` with `POSIX_SEMANTICS`; this NTFS host rejects the
combination with a not-supported diagnostic. A new native regression before
the runtime correction fails both owned deletion cases in **1.217 seconds**;
its wrong-identity and hardlink refusal cases already pass. This is an
application cleanup bug, distinct from missing symlink privilege. It also
causes spool cleanup errors to obscure ordinary archive validation errors.

[common PR #158](https://github.com/Kuaiwa-Network/common/pull/158)
uses delete-plus-POSIX disposition through a shared helper for both the owned
unpublished file and verifier spool. As the
[Microsoft disposition reference](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntddk/ns-ntddk-_file_disposition_information_ex)
describes, the name is removed when the deleting handle closes. Parent-relative
opens, no-follow handling, identity and single-link checks remain enforced;
there is no path-based deletion fallback. No-replace publication is unchanged.
The four native regression cases cover owned temporary cleanup, owned spool
cleanup, refusal of a different expected identity, and refusal of a hardlink
alias. The spool cases exercise paths with spaces and Unicode.

After the flag correction, the ambiguous-completion fixture still fails in
**2.694 seconds**: its hardlink/remove simulation conflicts with Windows' held
file sharing protection. Windows now uses the real held-handle rename before
injecting the ambiguous diagnostic; Unix retains its link/remove simulation.
The terminal-success and final-byte assertions are unchanged. Corrected cleanup
and reconciliation pass **two top-level tests** in **1.123 seconds**, without
skips; all four native regression cases pass.

The fixture also registers `Close` cleanup for early failures or skips and
reports unexpected cleanup errors. This lets its existing unavailable-symlink
skip complete without a locked-handle temporary-directory cleanup failure.
No skip was added and no identity/containment check was weakened. Focused
Windows CI adds native cleanup, reconciliation and publication-success tests.

Measured results for the final working fix based on `34315e4`:

- `go test -json -count=1 ./internal/archive` runs **31 top-level tests:
  27 passed, four failed, zero skipped**, in **27.675 seconds**. The four
  failures are all missing-symlink-capability cases listed below. One existing
  symlink subtest is skipped. Real two-pass production-archive verification,
  hostile-input rejection, limit/error classification, publication seams and
  compression-cleanup evidence pass.
- The expanded focused CI selection runs **49 top-level tests: 47 passed,
  two existing explicit production-fixture skips, zero failures**, in
  **19.355 seconds**. The prior ten skip IDs remain; the existing archive
  symlink skip makes **11 skip events**. All four native cleanup/refusal cases
  run and pass. Package and focused selections overlapped; durations are
  measured separately.
- Go formatting, `go vet ./internal/archive` and whitespace checks pass.
  Direct native language generation was not repeated because its code and
  inputs are unaffected; actual archive publication/verification paths ran in
  the package tests.

Committed source is `08dd1249a29828fcff702af2f6c5ea10fc7e6009`.
That exact source passed complete Linux acceptance and focused native Windows coverage in
[run 37180962600](https://github.com/Kuaiwa-Network/common/actions/runs/37180962600).
The documentation follow-up passes all **73 relevant skill/reference tests**
in **0.666 seconds** (**0.912 seconds** including Python startup), with zero
failures, errors or skips.

A fresh native `go test -json -count=1 ./...` at that exact commit runs
**561 top-level tests: 522 passed, seven failed, 32 skipped**, in **63.286
seconds**. The additional top-level test is the new Windows cleanup regression.
There are two failed subtest IDs and **61 skip events**. All 60 prior skip IDs
remain, with their reasons recorded above. The additional observed event uses
an existing skip; its earlier locked-handle cleanup failure is now corrected:

| Package | Test | Reason |
| --- | --- | --- |
| `internal/archive` | `TestFilePublicationSuccessAndNoReplace/symlink` | Ordinary token lacks symlink capability; existing skip now completes after fixture handle cleanup |

The remaining failed top-level tests are:

- `TestFilePublicationRejectsInitiallyExistingForeignTypes`
- `TestNativeWindowsArchiveExtractionReparse`
- `TestNativeWindowsArchiveInputReparse`
- `TestNativeWindowsArchiveNoReplace`
- `TestPublishCommitsExactStageIdentityAndCloseIsNilAfterPublication`
- `TestReformatFileIfSingleLineCanonicalAndIdempotent`
- `TestTypeDefsAreWired`

The four archive failures and `TestTypeDefsAreWired` fail while creating symlink
fixtures under this ordinary token. The two failed subtests are
`TestFilePublicationRejectsInitiallyExistingForeignTypes/symlink` and
`TestNativeWindowsArchiveNoReplace/foreign_reparse`. They do not certify reparse
handling on a capable token; no privilege or account setting was changed.
The output-directory saved identity assertion and Excel XML Unix-mode
expectation still need focused investigation. No not-supported cleanup
diagnostic remains in the failed-test inventory. This is the measured current
result, superseding the preceding 16-failure inventory for this candidate;
complete native producer and actual FarmBot worker/gate acceptance are pending.

UTF-8 logs, revisions, selected versions and duration metadata remain local.
The initial cleanup failure log SHA-256 is
`aaf38e4d285e53cf8fdbffcb841e1c794bdaabbb008a961410a0ca715ccd007a`;
the new regression's failure log SHA-256 is
`0bbe20061579ac5765644ee4c63829b0d0f1ffc1a972454a0ee6af5b4ff92bab`;
the intermediate ambiguous-fixture result log SHA-256 is
`7111903f36e6e718a51e7caa3aef07d8fb4307e68e89378d82e7059af36f56ab`;
the passing cleanup/reconciliation log SHA-256 is
`c0e63f0ea873f17443a7213c048878397b2d07d2129fede37c90d8b2094ff20a`;
the final archive-package log SHA-256 is
`aa7c5442171b488de9901a93add97708dd8d7c856ccb7e7783c9035b7f5c460d`;
the expanded focused selection log SHA-256 is
`513353220be26094eb17c925a3f16d5970516d468f12d9134cb2c55eb29fceeb`;
the refreshed full-module log SHA-256 is
`d9f6380ab22357ab3b417e047d94e32702f64706fcf7e42f91f57811c577ab21`.
The documentation-test log SHA-256 is
`e5c68546bbc5f90331da4347ac46bf9edfaf811a6f4988a2ef8a1563253bc613`.

Next is the saved publication identity assertion, preserving owned stage/final
identity and terminal publication semantics. Host reparse capability, wider
module gaps, actual Windows worker/gate acceptance and real Feishu reads remain
pending. No parked job resumed and no production deployment or feature
enablement occurred.

## Native Windows publication identity follow-up (2026-10-04; partial)

GitHub confirms [common #158](https://github.com/Kuaiwa-Network/common/pull/158)
merged as `266dae538d0169dbcb7ad7a54ec11f8dd0a16d52` and
[FarmBot #97](https://github.com/Kuaiwa-Network/farm-linear-agent/pull/97)
as `063c0ec1790294867857eaee1592ffa0adef18d1`. This step starts from that exact
common merge on the **production Windows host in the separate development
checkout**, using the ordinary owner token outside the app sandbox, configured
Python 3.13.16 with `PYTHONUTF8=1`, native Go 1.25.1/protoc 35.1, sanitized
selectors, private caches and fresh `RUNNER_TEMP` scratch. Production
configuration, ledger, services, credentials, account settings and ACLs were
untouched.

`TestPublishCommitsExactStageIdentityAndCloseIsNilAfterPublication` fails on
the exact merge in **0.965 seconds**, without skips. Its pre-rename `os.Stat`
does not necessarily capture a physical file identity on Windows: the
[Go 1.25.1 path-stat implementation](https://raw.githubusercontent.com/golang/go/go1.25.1/src/os/stat_windows.go)
can defer that lookup. When `os.SameFile` later tries the old stage basename,
publication has already moved it. In contrast,
[handle-stat captures the identity immediately](https://raw.githubusercontent.com/golang/go/go1.25.1/src/os/types_windows.go).
This is a fixture error. The runtime already uses held-handle identities, and
the actual native publication/ownership suite passes.

[common PR #159](https://github.com/Kuaiwa-Network/common/pull/159)
captures the stage's identity from an independently opened directory handle
and closes it before publication. Final identity, complete marker bytes, the
injected post-publication close diagnostic and repeated terminal-success
assertions remain enforced. Runtime code is unchanged; no containment,
ownership, no-follow, link-count or no-replace check is weakened. No skip was
added. Focused Windows CI adds the corrected assertion, foreign BoundRoot
refusal and native no-replace suite.

Measured checks for the working correction based on `266dae5`:

- The focused identity test passes in **1.021 seconds**, without skips.
- `go test -json -count=1 ./internal/outputdir` runs **33 top-level tests:
  28 passed, zero failed, five skipped**, in **1.798 seconds**. All eight
  `TestNativeWindowsNoReplacePublication` cases run and pass: actual complete
  publication, foreign file/directory/reparse preservation, stage replacement
  refusal, cleanup replacement preservation, unsupported primitive refusal
  and post-publication close diagnostics as terminal success.
- The expanded focused CI selection runs **52 top-level tests: 50 passed,
  zero failed, two existing fixture skips**, in **18.443 seconds**. All eight
  native publication cases, the corrected saved-identity assertion and foreign
  BoundRoot refusal run and pass. Its **11 skip IDs** are exactly unchanged
  from the preceding archive cleanup selection.
- Go formatting and whitespace checks pass. Native language generation was
  not repeated: this change only corrects a test fixture and adds CI coverage.

The output-directory package has these seven existing skip events, whose
reported platform reasons remain unchanged. They are not counted as native
verification; the separate eight native publication cases above actually run.

| Test | Existing reported reason |
| --- | --- |
| `TestClosePreservesReplacementAndAggregatesCleanupFailures` | Test-side replacement blocked by held root; native injection coverage is separate |
| `TestPrivateWorkClosePreservesReplacementAndReportsCleanupFailure` | Test-side replacement blocked by held root; native injection coverage is separate |
| `TestPublishPreservesRacedTargetsAndCleansOnlyOwnedStage/symlink` | Reparse coverage belongs to the native Windows suite |
| `TestPublishRefusesStageReplacementBeforeNoReplaceCall` | Test-side replacement blocked by held stage; native injection coverage is separate |
| `TestPublishRejectsRacedCaseFoldBasenameBeforeSyscall` | Windows case-insensitive namespace cannot create the distinct raced basename |
| `TestPublishUsesIdentityReconciliationAsTerminalBoundary/final_identity_commits_despite_syscall_diagnostic` | Held stage prevents test-side rename |
| `TestTransactionPrepareWorkCreationFailurePreservesReplacedChildAndBlocksParentCleanup` | Held work root prevents test-side replacement |

Committed source is `33056482192ecde13d61168de39166446ccf8730`.
That exact source passes complete Linux acceptance and focused native Windows coverage in
[run 37184160786](https://github.com/Kuaiwa-Network/common/actions/runs/37184160786).
The documentation follow-up passes all **73 relevant skill/reference tests**
in **0.379 seconds** (**0.496 seconds** including Python startup), with zero
failures, errors or skips.

A fresh native `go test -json -count=1 ./...` at that exact commit runs
**561 top-level tests: 523 passed, six failed, 32 skipped**, in **59.162
seconds**. All **61 skip IDs** and their previously recorded reasons are
unchanged. The remaining failed top-level tests are:

- `TestFilePublicationRejectsInitiallyExistingForeignTypes`
- `TestNativeWindowsArchiveExtractionReparse`
- `TestNativeWindowsArchiveInputReparse`
- `TestNativeWindowsArchiveNoReplace`
- `TestReformatFileIfSingleLineCanonicalAndIdempotent`
- `TestTypeDefsAreWired`

The four archive failures and `TestTypeDefsAreWired` still require host symlink
capability. Their two failed subtest IDs remain
`TestFilePublicationRejectsInitiallyExistingForeignTypes/symlink` and
`TestNativeWindowsArchiveNoReplace/foreign_reparse`. The Excel XML Unix-mode
expectation remains a separate focused investigation. The saved-stage
assertion is the only difference from the previous failed-test inventory; it
passes together with all eight native publication cases. No skipped check is
claimed as production-host readiness.

UTF-8 logs, revision, selected versions and duration metadata remain local.
The exact-merge failure log SHA-256 is
`412690f649cd58f9f89bf910b7872b4905841b35f22ecf61a5f41173eed12117`;
the corrected focused identity log SHA-256 is
`58962b4659f8bb4ee4fbb8c2d4dc33393a0ba1eefff5787a2ffeb8cea2914e80`;
the output-directory package log SHA-256 is
`98148487cd12550d8df5616638391f3f34837b5b2cd02eb3a7d372c0b61d5a1e`;
the expanded focused selection log SHA-256 is
`cca3c1e8b468e50cae9cf773d0203bd5cc12bcdf6d2eceb448f4160f7e4dee8e`;
the refreshed full-module log SHA-256 is
`24f51c54fc5fa0e8f2baeb97f7364a4e6d58a8dd497f149b75f53ca0a1a8c1a2`.
The documentation-test log SHA-256 is
`cb5ff05824b1820e45e0fd20c7e8c2113e057bab2da3bdf46a1430c04c3ae796`.

Next is the Excel XML mode assertion. Host reparse capability, wider module
acceptance, actual Windows worker/gate acceptance and real Feishu reads remain
pending. No parked job resumed and no production deployment or feature
enablement occurred.

## Native Windows workbook mode follow-up (2026-10-04; partial)

GitHub confirms [common #159](https://github.com/Kuaiwa-Network/common/pull/159)
merged as `48e0a2146fd60191fa05a0a4a4d11d40157db010` and
[FarmBot #98](https://github.com/Kuaiwa-Network/farm-linear-agent/pull/98)
as `2599e5b8d42bcfeedf874edf76de29d52c7b37ee`. This step starts from that exact
common merge on the **production Windows host in the separate development
checkout**, using the ordinary owner token outside the app sandbox, configured
Python 3.13.16 with `PYTHONUTF8=1`, native Go 1.25.1/protoc 35.1, sanitized
selectors, private caches and fresh `RUNNER_TEMP` scratch. Production
configuration, ledger, services, credentials, account settings and ACLs were
untouched.

`TestReformatFileIfSingleLineCanonicalAndIdempotent` fails on the exact merge
in **0.829 seconds**, without skips, with the sanitized diagnostic
`mode=0666 want 0600`. The fixture assumes Unix access bits on Windows.
[Go's Windows mode implementation](https://raw.githubusercontent.com/golang/go/go1.25.1/src/os/types_windows.go)
reports a writable regular file as `0666`; its
[permission API uses the writable bit for the read-only attribute](https://pkg.go.dev/os#Chmod).
This is a fixture mismatch, not a rewrite permission regression.

[common PR #160](https://github.com/Kuaiwa-Network/common/pull/160)
retains the exact `0600` assertion on Unix. On Windows it captures the observed
mode before rewriting and asserts that mode is preserved afterward. Exact
canonical bytes, logical workbook equality and second-pass idempotence remain
asserted. Runtime code, source XML and generated outputs are unchanged; no
skip, containment exception or ownership relaxation was added. FileMode
preservation is not DACL validation. Focused native Windows CI adds the entire
Excel XML package.

The working correction based on `48e0a21` runs
`go test -json -count=1 ./internal/excelxml`: **15 top-level tests passed,
zero failures and zero skips**, in **0.866 seconds**. Canonicalization,
observed-mode preservation, logical workbook semantics, idempotence, parsing,
source traversal and the production-source formatting gate all actually run.
Go formatting and whitespace checks pass. Native language generation was not
repeated because its executable code and inputs are unchanged.

Committed source is `14152257593156b707031b2cc18758169a9dd3ef`.
That exact source passes complete Linux acceptance and focused native Windows coverage in
[run 37185512325](https://github.com/Kuaiwa-Network/common/actions/runs/37185512325).
The documentation follow-up passes all **73 relevant skill/reference tests**
in **0.389 seconds** (**0.506 seconds** including Python startup), with zero
failures, errors or skips.

A fresh native `go test -json -count=1 ./...` at that exact commit runs
**561 top-level tests: 524 passed, five failed, 32 skipped**, in **59.940
seconds**. All **61 skip IDs** and their previously recorded reasons are
unchanged. All 15 Excel XML tests, eight native publication cases and four
native archive cleanup/refusal cases pass. The remaining failed top-level
tests are:

- `TestFilePublicationRejectsInitiallyExistingForeignTypes`
- `TestNativeWindowsArchiveExtractionReparse`
- `TestNativeWindowsArchiveInputReparse`
- `TestNativeWindowsArchiveNoReplace`
- `TestTypeDefsAreWired`

All five fail while creating symlink fixtures because the ordinary token lacks
the required privilege. Their two failed subtest IDs remain
`TestFilePublicationRejectsInitiallyExistingForeignTypes/symlink` and
`TestNativeWindowsArchiveNoReplace/foreign_reparse`. The Excel XML assertion
is the only difference from the previous failed-test inventory; no
non-capability failure remains in this measured inventory. This does not
certify reparse handling on a capable token or complete producer readiness.
No skipped check is claimed as native verification.

UTF-8 logs, revision, selected versions and duration metadata remain local.
The exact-merge failure log SHA-256 is
`3f2f855cf3d4bac8df177470e327541deef76aa2cc9ef428eb97f0f41859f6a0`;
the passing Excel XML package log SHA-256 is
`b0ea2ddf2c44d59d7cd4c1f94c026e1c334842549430e231380779e1cf85ed88`;
the refreshed full-module log SHA-256 is
`84c280f8b23d221d0b0fa730902aeb4e10186518ce10c91b1ba45d43d2c5b851`.
The documentation-test log SHA-256 is
`3b39110f7633e200d421054586f9d440d7a55846ee280b22c7bf10c2d698efef`.

Next is the offline symlink-capability recheck on the operator-selected
current account, using a capable test token. A Windows administrator
PowerShell/UAC confirmation may be required; this is not permission to change
account or system settings. Complete producer, actual Windows worker/gate
acceptance, .NET/C# and Unity gates, real Feishu reads, the private release
scan and TestBot restoration remain pending. No parked job resumed and no
production deployment or feature enablement occurred.

## Native Windows symlink-capable token follow-up (2026-10-04; partial)

GitHub confirms common #160 merged as
`34dd6dd071fe1c101793afe5ff1124397dbbc2a2` and the documentation-only FarmBot
#99 as `5db0d67c537eca83e229352acbd842c9a8bc8071`. This check ran on the
**production Windows host in the separate development checkout**, with fresh
offline scratch and the private tool caches. It did not read production
configuration or its ledger, start a service, authenticate to Feishu, change
credentials/accounts/DACLs/app settings, or deploy/enable feature.

The operator-selected current account supplied an **elevated offline test
token** through UAC. The launcher verified the same account without disclosing
its SID and checked helper integrity before Python started. File and directory
symlink creation both passed. This does not certify the ordinary worker token
or change its previously measured missing symlink privilege. Python 3.13.16
started with `PYTHONUTF8=1`; Git 2.54.0.windows.1 and Git LFS 3.7.1 were measured.
The pinned native Go 1.25.1/protoc 35.1 setup, explicit native tool paths,
sanitized FarmBot/Lark/Git/Go selectors, disabled Git hooks/fsmonitor and fresh
short `RUNNER_TEMP` were retained. Bash/MSYS/WSL were not invoked.

On exact merged common, the five previously failing symlink cases all passed,
without skips:
`TestFilePublicationRejectsInitiallyExistingForeignTypes`,
`TestNativeWindowsArchiveExtractionReparse`,
`TestNativeWindowsArchiveInputReparse`, `TestNativeWindowsArchiveNoReplace`,
and `TestTypeDefsAreWired`. The elevated full module activated 26 formerly
skipped IDs and exposed one application regression:
`TestBoundRootContainsDirectoryIdentitySkipsSymlinksWithoutFollowing`.
Its first assertion failed with a broken-link metadata rejection, not a
missing-capability error. Windows can open the link itself with
`FILE_OPEN_REPARSE_POINT`; `bindWindowsHandle` correctly closed and rejected
its reparse metadata, but returned an untyped error that the identity-only
walk did not recognize as a no-follow link rejection.

[common PR #161](https://github.com/Kuaiwa-Network/common/pull/161) wraps only
that verified metadata rejection with the existing Windows reparse error.
The classifier's accepted native error set is unchanged; access-denied,
not-found and text-only errors remain failures. Real file/directory/broken
symlink regression cases failed before the fix. They now prove classified
rejection and handle closure using deletion of a link opened without delete
sharing. The original identity test also proves a contained directory matches,
a foreign link target does not, strict artifact walks reject links, and a
skipped link changed into a directory fails traversal. The focused native
Windows CI job now includes these cases. No skip, ownership exception or
containment relaxation was added; source XML/generated bytes are unchanged.

One preliminary focused run hit an unreliable new-test assertion that queried
an already closed numeric Windows handle, which another thread could reuse.
The corrected regression proves closure through the filesystem's delete-share
rule instead. That diagnostic run is preserved below and is not counted as
a passing application check.

Commands used `go test -json -count=1`: an exact five-case selection in
`./internal/archive ./internal/defs`, a three-case regression selection in
`./internal/artifact`, the whole `./internal/artifact` package, then `./...`.
The focused proposed-code runs recorded their modified-file hashes over the
merged base. The final full module ran at clean committed source
`1d3a68a08f300ac77bbcf9cdbbfb053752e1c2f8`; source integrity and checkout
cleanliness were verified after it. These are common generator tests, not
a rerun of FarmBot's Python controller suite.

| Check | Top-level tests | Passed | Failed | Top-level skipped | Duration |
| --- | ---: | ---: | ---: | ---: | ---: |
| Exact-merge five-case capability recheck | 5 | 5 | 0 | 0 | 1.003 seconds |
| Exact-merge elevated full module | 561 | 535 | 1 | 25 | 59.431 seconds |
| Pre-fix runtime with new regression tests | 3 | 1 | 2 | 0 | 1.247 seconds |
| Preliminary focused run; old handle assertion | 77 | 74 | 1 | 2 | 3.117 seconds |
| Corrected artifact package | 77 | 75 | 0 | 2 | 3.084 seconds |
| Committed-source elevated full module | 563 | 538 | 0 | 25 | 59.051 seconds |

The regression baseline's two failed top-level IDs were the original identity
test and `TestNativeWindowsBoundHandleRejectsReparseMetadata`, with failed
`/directory_link`, `/file_link` and `/broken_link` subtests. The preliminary
focused failure was the latter test's `/directory_link` handle assertion.
Both have zero skips. The corrected artifact package has two existing
top-level skips and eight total skip events. The final full module has
**563 top-level tests: 538 passed, zero failures, 25 skipped** and **35 skip
events**, including subtests. All 26 activated IDs pass. Its 35 remaining
skip IDs exactly match the elevated exact-merge inventory; no symlink-privilege
skip remains. All eight native publication cases, four archive cleanup/refusal
cases and 15 Excel XML tests also pass. Skipped checks are not native readiness
evidence.

The complete final measured skip inventory follows. Private paths in diagnostic
reasons are redacted; held-directory replacement limitations remain distinct
from shell-only or explicit production-fixture checks.

| Test or subtest | Measured reason |
| --- | --- |
| `TestReadBoundRegularRejectsIntermediateDirectoryReplacementAfterRead` | open-directory replacement unavailable: rename <private path/diagnostic>; access denied |
| `TestTransactionPrepareWorkCreationFailurePreservesReplacedChildAndBlocksParentCleanup` | held no-FILE_SHARE_DELETE work root prevents a test-side replacement |
| `TestCheckConfigArtifactScript` | Unix producer shell behavior runs only on Darwin and Linux |
| `TestPackConfigArtifactScript` | Unix producer shell behavior runs only on Darwin and Linux |
| `TestPublishPreservesRacedTargetsAndCleansOnlyOwnedStage/symlink` | native Windows reparse coverage runs in native_windows_test.go |
| `TestPublishRefusesStageReplacementBeforeNoReplaceCall` | held no-FILE_SHARE_DELETE stage prevents native replacement; Task 10 owns native injection coverage |
| `TestCheckConfigArtifactScriptRejectsCopiedNonGitCheckoutBeforeArtifactWork` | Unix producer shell behavior runs only on Darwin and Linux |
| `TestCheckConfigArtifactScriptCompileOwnershipRegression` | Unix producer shell behavior runs only on Darwin and Linux |
| `TestCheckConfigArtifactScriptCompileOwnershipRegressionChild` | Unix producer shell behavior runs only on Darwin and Linux |
| `TestPackConfigArtifactScriptRealBehaviorCallGraphAndImmutability` | Unix producer shell behavior runs only on Darwin and Linux |
| `TestConfigArtifactPipelinePackResolverBehavior` | Unix producer shell behavior runs only on Darwin and Linux |
| `TestConfigArtifactPipelineCleanupRejectsNamespaceReplacement` | Unix producer shell behavior runs only on Darwin and Linux |
| `TestConfigArtifactPipelineUploadRevalidatesExactHandoff` | Unix producer shell behavior runs only on Darwin and Linux |
| `TestPublishRejectsRacedCaseFoldBasenameBeforeSyscall` | Windows case-insensitive namespace cannot create the distinct raced basename |
| `TestPublishUsesIdentityReconciliationAsTerminalBoundary/final_identity_commits_despite_syscall_diagnostic` | held no-FILE_SHARE_DELETE stage prevents a test-side rename |
| `TestClosePreservesReplacementAndAggregatesCleanupFailures` | held no-FILE_SHARE_DELETE root prevents native replacement; Task 10 owns native injection coverage |
| `TestProductionSourceDigestMatchesExactPackerPipeline` | packer shell comparison requires the Linux release environment; platform-neutral SourceDigest tests still run on windows |
| `TestPrivateWorkClosePreservesReplacementAndReportsCleanupFailure` | held no-FILE_SHARE_DELETE root prevents native replacement; Task 10 owns native injection coverage |
| `TestSnapshotterRejectsUnsafeTreesFreshnessAndOverlap/case_collision` | filesystem is case-insensitive |
| `TestValidateTreeRejectsPhysicalMutations/fifo` | mkfifo unavailable: exec: "mkfifo": executable file not found in %PATH% |
| `TestBuildRejectsUnsafePhysicalRootShapes/case-colliding_files` | filesystem does not support case-distinct fixture names: open <private path/diagnostic> |
| `TestSourceDigestRejectsUnsafeTrees/newline_name` | Windows does not permit newline path components |
| `TestSourceDigestRejectsUnsafeTrees/backslash_name` | a backslash is a Windows path separator |
| `TestSourceDigestRejectsUnsafeTrees/fifo` | mkfifo unavailable: exec: "mkfifo": executable file not found in %PATH% |
| `TestGenerateLanguagesRejectsInvalidStagingBeforeRunningTools/case_collision` | filesystem is case-insensitive |
| `TestGenerateLanguagesProductionFixture` | run with -args -fixture-root <existing-empty-work-dir> |
| `TestGenerateLanguagesProductionSingleViews` | run with -args -single-view-fixture-root <existing-empty-work-dir> |
| `TestSystemRunnerCommandEnv/Unix_nonnull_empty_does_not_inherit` | Windows requires SYSTEMROOT in every explicit environment |
| `TestLegacyShellGuardRejectsDatedActiveFilesThroughNeutralWrapper` | Bash legacy guard test |
| `TestLegacyShellGuardRetainsExactHistoricalDocumentationExemptions` | Bash legacy guard test |
| `TestUnixLauncherUsesItsCheckoutAndPreservesArguments` | Unix launcher test |
| `TestUnixLauncherRejectsMalformedPreflightWithoutRunningGo` | Unix launcher test |
| `TestCheckClientExportRejectsOptionLikeBaseRefBeforeGit` | Unix gate script test |
| `TestVerifyPreservesReplacedPrivateWorkAndLeavesAgainstUnchanged` | Task 10 owns native Windows outputdir identity-injection coverage for held-directory replacement |
| `TestVerifyTreatsAgainstSwapAfterOpenAsRuntimeWithoutPrivateWork` | native held-root replacement is covered by the Windows identity-injection gate |

UTF-8 logs, revision, versions, source-integrity metadata and durations remain
local. Their SHA-256 hashes are:

- Exact-merge five-case capability recheck: `bb280e3ef687b1714e5f5d0fb15a5d93e37bf4ec56fb99fff430de3059ece887`.
- Exact-merge elevated full module: `3c9294e9253dea418429a7f2eb09b146eed16988b4a29a31f82fc7a7b4154dcc`.
- Pre-fix runtime with new regression tests: `87be13785ccf49419f350ef1f54a1b4245075324cd77c8862f02788bc31e0def`.
- Preliminary focused run; old handle assertion: `82f916a0273039910ae16d3342a9b2637aecf3bbfd7e1ba43fde19c2e3e979f0`.
- Corrected artifact package: `db4c639afeec6f56c448b58e5267328a58432a633cc04b14d88101ff7647462e`.
- Committed-source elevated full module: `a71ba97fb3fe3562dff8f85fc8971d18bec54765a2b3528beb5af690b2ec7613`.

Complete Linux acceptance and focused native Windows CI both pass at this
exact source in
[run 37188965053](https://github.com/Kuaiwa-Network/common/actions/runs/37188965053).
All 73 relevant skill/reference tests pass in 0.384 seconds (0.500 seconds
including startup). Their UTF-8 log SHA-256 is
`d0c9ec174fcb56b25e7b8ac298a9b0ee2ed33c44b9afa6c970849e60c0d7a222`.
Local links, section anchor, measured evidence, privacy and whitespace checks
pass. This documentation-only update needs no full FarmBot suite rerun or wait
for its hosted CI.

Next is review/merge of the common fix, then the pinned .NET SDK 8.0.423 and
native C# compile/production-fixture checks. A passing elevated Go module does
not establish complete producer or ordinary-token readiness. Actual native
Windows worker credential/generator/gate acceptance, Windows desktop Unity,
real Feishu planning-document/attachment reads, the private release scan and
scoped TestBot restoration remain pending. No parked job resumed; the unmerged
FARM-1425 contract/backend test drafts remain non-release provenance.

## Native Windows .NET and C# follow-up (2026-10-04; partial)

GitHub confirms common #161 merged as
`b367febe20d6db65ebb386aa871bdb2671df9525` and documentation-only FarmBot #100
as `612279cffc64811627ba4f4b2b6a711ae5c17636`. Their merged trees match the
reviewed source trees exactly; no post-merge code was left on either branch.
Both completed development branches were removed, and their verification
processes exited. The preserved local audit retains this provenance.

This next check ran on the **production Windows host in the separate
development checkout**, at exact merged common, using the **ordinary operator
token**. It read no production configuration/ledger or lark-cli credential
store, authenticated to no Feishu/Linear endpoint, started no service and
changed no account, app setting, global PATH or production deployment. Parked
jobs and their unmerged test drafts remain untouched.

The host's discoverable SDK was **9.0.306**, so it did not satisfy the exact
**8.0.423** acceptance pin. The verifier fetched Microsoft's
[official .NET 8 release metadata](https://builds.dotnet.microsoft.com/dotnet/release-metadata/8.0/releases.json)
and selected the
[8.0.423 Windows x64 ZIP](https://builds.dotnet.microsoft.com/dotnet/Sdk/8.0.423/dotnet-sdk-8.0.423-win-x64.zip).
Its 285,072,593 bytes matched the published SHA-512 before extraction:
`063fcc35c136277e6fd767c66579f3b92db22a078a7f0c7177b6af1edb2c9afae1613f6cfdc01acf7421773d9ac77f0ef73a7fd8b37f469e7e3505e5c1361ba0`.
Archive paths and duplicate/link entries were checked before unpacking into
the ignored private verification cache. Preparation took **29.437 seconds**.
This is a private development SDK, not a system installation or a production
worker configuration change.

The selected executable reported **8.0.423**. Python 3.13.16 began with
`PYTHONUTF8=1`; Go 1.25.1 `windows/amd64`, protoc 35.1, Git 2.54.0.windows.1
and Git LFS 3.7.1 were measured. The runner removed inherited FarmBot/Lark,
Git/Go, .NET/NuGet/MSBuild and credential-provider selectors, disabled Git
hooks/fsmonitor and used explicit native executable paths. A fresh short
scratch parent contained space-bearing fixture/output paths, pinned
`global.json` with roll-forward disabled, private `DOTNET_CLI_HOME`, NuGet
packages/HTTP cache and compiler object/output directories. Restore used an
explicit temporary NuGet config containing only public nuget.org and the
checked-in dependency lock. Public SDK/package downloads were preparation;
no live FarmBot integration was contacted. Builds used `--no-restore`,
disabled shared compiler/MSBuild servers, and preserved warnings-as-errors.
Bash/MSYS/WSL were not invoked.

An initial attempt failed before compilation because the verifier's temporary
NuGet config incorrectly put `<clear/>` in `packageSourceCredentials`.
NuGet treated it as an incomplete credential entry. Removing that invalid
section fixed the harness; no credentials were supplied or read. Its failed
restore took **0.912 seconds** and its UTF-8 log SHA-256 is
`e4d8ff1a1c345e4edc120e50cca6fb8e439e94657b0ec65a448c7013e3f91e99`.
It is not an application regression or passing acceptance evidence.

The corrected native sequence completed in **38.548 seconds**:

1. `go test -json -count=1 -run
   '^(TestGenerateLanguagesProductionFixture|TestGenerateLanguagesProductionSingleViews)$'
   ./internal/toolchain -args -fixture-root <fresh-empty-combined-dir>
   -single-view-fixture-root <fresh-empty-single-views-dir>` ran both previously
   opt-in production language tests. Both passed in **6.335 seconds**, with
   **zero failures and zero skips**. They compile generated Go and verify
   generated-byte stability and plugin cleanup. The client-only case removes
   Go from PATH and still generates C# successfully with the pinned protoc.
2. Copies of the checked-in `Farm.Config.Generated.Compile.csproj` and
   `packages.lock.json` were restored with `dotnet restore --locked-mode`
   and built with `dotnet build -c Release --no-restore`, setting
   `GeneratedConfigRoot` and separate private object/output directories.
   The target remains `netstandard2.0`, C# 9.0 and exact Google.Protobuf
   **3.35.1**. Combined and client-only frozen fixtures each compiled all
   **four generated C# files** with zero warnings/errors.
3. Native `designer/tools/gen-config.cmd generate --profile farm-hive
   --profile unity-client --out <fresh-absent-artifact>` generated the full
   designer artifact at the exact merged common revision in **11.019 seconds**.
   Native `verify` passed before compilation in **4.204 seconds**.
   The same locked C# project compiled all **103 generated C# files** in
   **1.397 seconds**, with zero warnings/errors.
4. The complete full artifact had **597 files**. Its independent tree hash
   stayed `10a08d41150d64a70530fe8f25450d5c9f16a8052c1de6f11f8dc304af89d769`
   through restore/build; both frozen-fixture trees also stayed unchanged.
   Every copied lock stayed byte-identical and the resolved assets contain
   Google.Protobuf 3.35.1. Native `verify` passed again after compilation in
   **3.122 seconds**. The common checkout remained clean.

All three C# builds passed with **zero warnings and zero errors**:

| Scope | Generated C# files | Restore duration | Build duration |
| --- | ---: | ---: | ---: |
| combined-fixture | 4 | 8.251 seconds | 1.373 seconds |
| client-only-fixture | 4 | 0.545 seconds | 0.982 seconds |
| actual-common | 103 | 0.555 seconds | 1.397 seconds |

UTF-8 logs, versions, revision, selected tool metadata, generated/assembly
hashes and durations remain local. Passing check log SHA-256 hashes are:

- `language-fixtures`: `e45e06673a34ac2a024c7f3d9c5f3329f5c77aea000b20f0df06cc49dfba0fbf`.
- `combined-fixture-restore`: `5068d420ebe37a6dd8b8d46a4348351cfab13f3bc44608bd3d8a1fa5c41c9090`.
- `combined-fixture-build`: `84429c06c614cb23efd774e7c5396c344706ddedfad2834c52db15596bd34bac`.
- `client-only-fixture-restore`: `d33ff5d425f841800147a8690b489c7514023a4a6134169dcb2b8cd9d8f04146`.
- `client-only-fixture-build`: `db6b21216dbed32896756dc039dd10e1d2bd013d88bab6e306c55cb7c154b614`.
- `generate-common-combined`: `a7526047342515363903896da570ca6da08df4cef966c282d50da088742897b2`.
- `verify-common-combined`: `5d699f8f7a471ad82a44882dd97fbb59e71bc246d7bd8588b1ef3840143832b2`.
- `actual-common-restore`: `1053e968286e535bf01e1bd1e949ca25a3e619b858b3f69f3504185c7673b1a0`.
- `actual-common-build`: `959f4e5217c329e573e0ddb079732c4c47f4fe5d030da490330087bb06071aa2`.
- `verify-common-after-compile`: `5d699f8f7a471ad82a44882dd97fbb59e71bc246d7bd8588b1ef3840143832b2`.

This focused sequence activated the two opt-in language tests from the prior
full-module skip inventory. That historical inventory remains unchanged in
its record; no new full module or FarmBot controller suite run is claimed.
Common source/runtime, schemas, generated checked-in outputs, dependency pins
and warnings are unchanged. No code fix or new skip was needed. All 73 relevant
skill/reference tests pass in **0.365 seconds** (0.487 seconds including
startup); their UTF-8 log SHA-256 is
`5908205fb8274412b22f98c32df98efcb9950b919875a560dae3bf6ef07ec98e`.
Measured-evidence, local-link/anchor, privacy and whitespace checks pass.
This documentation-only update needs no full-suite rerun or hosted-CI wait.

Next is actual isolated Windows worker acceptance: prove native launch,
read-only sibling access, dummy feature credential grants and descendant
containment before any real credential/read test. The separate .NET cache
does not establish that a deployed worker can select/access it. Native
Farm-Contract/farm-hive generator/gate equivalents and their worker runs,
ordinary-token symlink/ownership capability, Windows desktop Unity, real Feishu
planning-document/attachment reads, the private release scan and scoped
TestBot restoration remain pending. Complete producer/publication readiness
and production feature enablement/deployment are not certified or authorized
by this check.

## Native Windows controller credential and containment follow-up (2026-10-04; partial)

FarmBot [#101](https://github.com/Kuaiwa-Network/farm-linear-agent/pull/101)
is merged as `4149d05fe3f05ed6f56bb411c71ba27daef4cc63`. GitHub main was checked before creating
the next documentation branch; its merged tree equals the reviewed source.
The executable/test/skill/reference trees still equal the #81 candidate
`e8406d547c2663703f34b007f79f306cce35b362`. This check uses the ordinary operator
token on the **production Windows host in the separate development checkout**,
with a fresh absolute scratch root outside the checkout/installation, including
spaces and Unicode. Python is 3.13.16 with `PYTHONUTF8=1` set before launch.
No production configuration/ledger, real credential store, model authentication,
service, app settings, Windows accounts or parked work were changed or used.

The read-only registered-runtime check completed in **0.357 seconds**.
The privately selected, previously verified Codex CLI reports **0.160.0**;
its image SHA-256 remains `37762753b554982eef1c109303d1be652b6397f1479e844794353a85650199c6`. Protected installation
metadata reports a registered Core runtime, the current user as owner, two
registered accounts and a completed registration. The new worker home still
does **not** match the registered home. The owned CLI child has **no package
identity**, with Windows query status **15700**, and `--version` exits 0.
Its fresh home gains only `tmp`; no auth, configuration or model state is seeded.
No provisioning helper or service request ran. This is an admission preflight,
not a new sandbox-launch result. The retained 0.160.0 source at
`a956835d020762cb2b570053af06f643a11c0ecc` still requires registered owner/home,
installed package family and caller executable identity; standalone legacy
setup cannot replace registered-runtime accounts. The earlier measured launch
failure therefore remains unresolved. See the
[registered-runtime admission record](#registered-runtime-admission-and-local-object-control-2026-10-04-partial).
The [official Windows sandbox documentation](https://learn.chatgpt.com/docs/windows/windows-sandbox)
describes elevated mode as the preferred native sandbox. This check preserves
FarmBot's `windows.sandbox="elevated"` selection and uses no Bash, MSYS or WSL.

An independent offline **controller/gate probe passed in 0.879 seconds**.
It calls the real `Launcher.spawn`, generated isolated Codex-home configuration
and native `windows_worker_gate`, but substitutes a native Python helper for
`codex exec` and sets `seed_files={}`. **No Codex sandbox or model worker ran.**
Its result establishes controller environment delivery and Windows Job Object
containment; it does not establish sandbox filesystem/network isolation,
read-only sibling enforcement, elevated-account tool access or real credential
delivery through Codex's shell tool.

| Attempt | Duration | Measured result |
| --- | --- | --- |
| Explicit dummy feature grant | 0.547 s | Canonical dummy bot ID/secret delivered; source alias, inherited user/tenant tokens, proxy key, store override and GitHub tokens withheld; strict bot mode forced and auth proxy withheld |
| No feature grant | 0.325 s | Canonical credentials and source alias remain withheld despite hostile per-attempt overrides |
| Missing configured source | Before process creation | Refused without creating an attempt or starting a child |

Each started attempt independently observes **three owned processes** in its
Job Object: the gate, native helper and helper's sleeping descendant. Intentional
feature-helper exit 3 and explicit no-grant stop both reap all three processes,
leave an empty Job Object and pass `assert_quiescent`. A separately owned unrelated
process stays alive through both settlements, then is reaped by its own creator.
The controller has no running attempts at completion. No source alias, dummy
credential value or app ID appears in the prompt/config/process record; auth is
not seeded, shell snapshots stay disabled and the source alias stays excluded.

Inside the granted helper, native version probes select Python **3.13.16**, Git
**2.54.0.windows.1**, Git LFS **3.7.1**, the private .NET SDK **8.0.423** and
lark-cli **1.0.82** successfully. These are ordinary-token helper results,
not proof that Codex's elevated sandbox can access those tools. The no-grant
helper checks Python/Git/LFS/.NET, without exercising a feature-only lark grant.
Three lark-cli `docs +fetch --dry-run` commands use only dummy environment
credentials and a fresh empty store, with remote metadata/update checks off:
bot exits **0**, `--as user` is refused with **2**, and missing canonical secret
is refused with **5**. All three match the intended result. The store remains
empty; the dummy app ID appears in the bot dry-run output, but the secret does
not. No real planning document or Feishu endpoint is accessed.

Focused native commands use the selected Python executable explicitly with
`-B -m unittest discover -s tests -p PATTERN -v` after removing inherited
FarmBot/fake selectors, lark variables and GitHub/controller token variables:

| Pattern | Result | unittest duration | Including startup | UTF-8 log SHA-256 |
| --- | --- | --- | --- | --- |
| `test_windows_workers.py` | All **7** native Job Object tests pass; no skips/failures/errors | 0.511 s | 1.042 s | `fdf8ba1e85102430afc4736d0aa00f843671ac91c8205f3be0453373fdafe657` |
| `test_lark_cli.py` | All **8** tests pass; no skips/failures/errors | 0.010 s | 0.227 s | `a781ac06ffd1af9b8143dc24fc6ae11386d816c5ae2d3b7aa9ade4c2f236f069` |

Three incomplete local probe-harness runs are retained as evidence:
`native-worker-grants-b527b132` (0.633 s),
`native-worker-grants-396f58ac` (0.893 s) and
`native-worker-grants-3537d376` (0.865 s). The first incorrectly
required the nonsensitive dummy app ID to be absent from dry-run output. The
other two incorrectly expected a no-grant lark version command to succeed while
deliberately injecting `LARKSUITE_CLI_AUTH_PROXY`; this pinned binary refuses
that variable because the `authsidecar` build feature is absent. The final
harness checks secret absence separately and runs feature-only lark commands
only in the granted attempt, retaining all credential/containment assertions.
These were harness assumptions, not application regressions or relaxed product
tests. Each failed run's owned Job Objects and recorded helpers/descendants are
independently confirmed empty/dead afterward.

Ignored local evidence is retained in `registered-home-readonly-20261004T092410Z`,
`native-worker-grants-e1a9c046` and `native-worker-focused-2de4ab96`. Sanitized
grant-summary SHA-256 is `3b2e43ad4826182e817209cccd400c588866d7165010a7c80aa1dd66c5a6ef37`; ownership-summary
SHA-256 is `3eae9d81c5db3205ce98238def2f539af73d4eb2bd63159a77108f5e88222eb5`. Probe-source SHA-256 is
`d756708eb2780ff4facb7ba6ded2fdc1ea20f0c8008262b6bb9c21452e6b5c52`. All four completed worker stdout/stderr logs are
empty UTF-8 files with SHA-256
`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`;
private paths/PIDs, ownership values and raw host diagnostics stay local.
All **73 relevant documentation tests pass** in 0.359 seconds (0.477 seconds
including startup), without skips/failures/errors; UTF-8 log SHA-256 is
`4c1b0a26a6cef894c7dce69888475c03a25fa411c676a852a7095dc0d1b61b8f`.
Local links/anchors, measured evidence, privacy and whitespace are checked.
No FarmBot runtime,
common source, pin, containment/ownership guard or skip changed; no full offline
suite rerun or production deployment is claimed.

**Next:** complete a supported isolated-home native Codex launch integration
without bypassing registered-runtime admission, then repeat the dummy feature
grant and descendant checks through an actual Codex sandbox child. Only after
that passes should the selected real controller credential source, read-only
Feishu documents/attachments and Windows Word conversion be tested. Remaining
native Farm-Contract/farm-hive generators and repository gates, ordinary-worker
symlink/ownership capability, Windows desktop Unity, private secret scanning,
approved release provenance/CI and scoped TestBot restoration remain pending.
FARM-1346/FARM-1425 stay parked; the unmerged contract/backend test drafts are
not release evidence. No manual account or credential setup is requested by
this independent controller check.

## Native Windows Codex configuration/runtime isolation follow-up (2026-10-04; partial)

FarmBot [#102](https://github.com/Kuaiwa-Network/farm-linear-agent/pull/102)
is merged as `6293766f95b913f3294e17d87e58c27bacc2ffe0`, with the reviewed source tree unchanged.
This next check again runs on the **production Windows host in the separate
development checkout**, using Python 3.13.16 with `PYTHONUTF8=1`, fresh absolute
scratch homes outside the checkout/installation, and sanitized environments.
No production FarmBot configuration/ledger, app configuration/auth file, real
credential store, model or Feishu endpoint is accessed. No service restart,
provisioning, account/settings change or parked-job activity is performed.

The successful CLI-option check completes in **0.400 seconds**:
the configured PATH command reports **0.156.1**, while the previously verified
candidate reports **0.160.0**. All eight owned version/help processes exit 0.
Both expose `--ignore-user-config`, `--ignore-rules`, `--ephemeral`, `--config`
and `--profile`; neither exposes `--config-file` or a separate sandbox-home
override. The fresh home gains only `tmp`, with no auth/config/session state.
The [official non-interactive documentation](https://learn.chatgpt.com/docs/non-interactive-mode)
describes ignoring user config and avoiding saved rollout files; the
[environment-variable reference](https://learn.chatgpt.com/docs/config-file/environment-variables)
describes a separate SQLite-state location. These controls do not establish
separate Windows sandbox ownership. The
[configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
provides no sandbox-home setting. The
[official changelog](https://learn.chatgpt.com/docs/changelog) still lists
0.160.0 as the newest stable CLI on the checked page (released 2026-10-01).
No configured CLI is upgraded or replaced here.

A read-only installed-package check completes in **0.333 seconds**.
Windows metadata identifies one current-user Codex package, with the same
family as the protected runtime registration. The running sandbox service and
its sibling CLI are inside that OS-resolved current staged package. That CLI
reports **0.160.0**, with the same verified image SHA-256
`37762753b554982eef1c109303d1be652b6397f1479e844794353a85650199c6`, and supports the same exec isolation flags. Nevertheless,
its directly launched child has **no package identity** (Windows status
**15700**). The stored `ready_package` does **not** match the currently installed
package; querying the stored package's staged path returns **1168**. Thus a
nonempty registration receipt is not evidence that it is ready for the current
package. The app manifest declares browser-host and sandbox-runner aliases,
but no `codex.exe` CLI activation alias. Both observed aliases are present for
the current user. No alias or app activation is invoked. The protected runtime
record remains byte-for-byte unchanged throughout this check.

The retained 0.160.0 source still binds runtime admission to the owner, exact
Codex home, installed package and caller image. It passes `config.codex_home`
to native sandbox execution; supplied sandbox-state metadata changes the
permission profile, without replacing that home. See the
[registered-runtime admission evidence](#registered-runtime-admission-and-local-object-control-2026-10-04-partial).
Ignoring config, redirecting SQLite state or selecting a profile therefore
does not prove that FarmBot's fresh per-attempt home can launch against this
registered runtime. Reusing the desktop home would also require separately
proving instruction/config/state isolation; it is not adopted as an untested
bridge. **No supported isolated-home native integration has been established
on this machine.** The successful controller/helper results in #102 remain
independent; actual Codex sandbox-child grants, containment, tool access and
read-only sibling enforcement remain pending. No weaker sandbox or ownership
exception is selected to obtain acceptance.

Two incomplete verifier attempts remain local: `native-codex-options-a8631515`
(0.910 seconds) used the documentation's `sandbox windows --help` syntax.
This CLI interprets `windows --help` as the requested command, attempted a
restricted-token launch, and returned `CreateProcessAsUserW` error **2** before
creating the target process. This unintended query earns no sandbox acceptance
credit. The corrected query is `codex sandbox --help`. The package-check
attempt `packaged-runtime-cli-27f0b2ee` (0.002 seconds) stopped before launching
any child because it required the older ready-package staged lookup to succeed;
the completed check instead resolves the current installed package and reports
the mismatch explicitly. These verifier issues do not change FarmBot behavior.

UTF-8 help/version/identity logs and private metadata remain ignored locally in
`native-codex-options-044c794f` and `packaged-runtime-cli-4521cea2`. Sanitized summary SHA-256 values are
`e29c8b631d4740076c7da2ead0cb206260fd69bf5cb25e010f72a0b5e2e00394` and
`2e9058c0157feec111e8470b83a4357ca32bb5ab645b8379a1cc9b02912c5773` respectively. Successful command-log hashes
are retained per command in the first summary. Probe-source SHA-256 values are
`6f004a516958c85b4696814409f6bab7085ad3205f2620c83d62288300ec4369` and
`593074ecbb92bffcf83bbe18ee6e200e75080a5434e961b8db86f98dd60196ea`. Private paths, package identifiers, SIDs and
raw failure diagnostics are not published. All **73 relevant documentation
tests pass** without skips/failures/errors in 0.354 seconds (0.472 seconds
including startup); UTF-8 log SHA-256 is
`5072c57bb38b45ff04caaf00a07c9ea72e7c4c80a0a6df9825808ca41df4a5bd`.
Evidence hashes, local links/anchors, privacy and whitespace are checked.
Executable behavior is unchanged; no full FarmBot suite rerun
or production readiness is claimed.

**Next:** continue the independent native Farm-Contract/farm-hive generator and
repository-gate work offline. Native worker promotion still requires a supported
Codex path that preserves isolated worker configuration/state and registered
runtime admission, followed by actual sandbox-child dummy acceptance. This is
not resolved by granting the Feishu bot more permissions or creating another
Windows account. The prepared upstream report remains deferred at the operator's
request; nothing is published to OpenAI. Real Feishu/Word, ordinary-worker
capabilities, desktop Unity, private secret scanning, release provenance/CI and
scoped TestBot restoration also remain pending. Parked jobs and unmerged test
drafts remain outside this verification.

## Native Windows Farm-Contract gate inventory and manifest probe (2026-10-04; partial)

FarmBot [#103](https://github.com/Kuaiwa-Network/farm-linear-agent/pull/103)
is merged as `e54cf2b3a040af992ba48b05b5b380fdae6e87ff`. This follow-up runs
on the **production Windows host**, with the ordinary current-user token, in
a fresh separate Farm-Contract development checkout at exact default-branch
revision **`f18cbf6a4ae4a1e98dea1f99b7c8f0983769581d`**. It reads no production FarmBot configuration
or ledger and copies no Mac state. No service, account, application setting,
credential installation, live issue or Feishu operation occurs. Preparation
uses existing GitHub authentication to read the private repository; it does
not set up new credentials. An initial anonymous clone fails authentication
before the authenticated clone completes in 3.850 seconds; both private logs
are retained. That preparation failure is not an application regression.

The [authoritative gate inventory](https://github.com/Kuaiwa-Network/Farm-Contract/blob/f18cbf6a4ae4a1e98dea1f99b7c8f0983769581d/README.md#二线上协议proto)
requires twelve checks, including the breaking-waiver wrapper; bare
`buf breaking` would omit its waiver accounting and is not substituted.
CI pins buf **1.72.0** and OpenSpec **1.7.0**. Native Python
is **3.13.16**, Git **2.54.0.windows.1** and Git LFS **3.7.1**.
The existing native Node reports **v24.19.0**, while CI selects Node 22;
only OpenSpec's version query is exercised here, without claiming strict
validation or CI-equivalent Node selection. No global tool is installed or
upgraded. The source contains no tracked LFS files requiring hydration.

`PYTHONUTF8=1` is set before Python starts. Child environments are built
from a Windows OS-variable allowlist, removing inherited FarmBot, credential,
repository-selection and model-runtime selectors. Home, temporary files,
application-data directories, Buf cache and output are fresh scratch paths;
Git user/system configuration is excluded and hooks/fsmonitor are disabled.
Coverage explicitly selects the separate common checkout at merged #161,
**`b367febe20d6db65ebb386aa871bdb2671df9525`**, rather than searching for host repositories.
No Bash, MSYS or WSL process is invoked.

The native check sequence completes in **7.267 seconds**. The
following committed gate entries all exit **0**, without skips. PowerShell
variables below stand for the explicitly selected executables and private
scratch/development paths used by the verifier:

| Gate | Command | Seconds | Measured result |
| --- | --- | --- | --- |
| 1 | `& $Buf build` | 0.058 | Protocol compilation passes. |
| 2 | `& $Buf lint` | 0.088 | Lint passes with the committed configuration. |
| 8 | `& $Python -B tools/check-coverage.py --farm-common $CommonCheckout --json $ScratchCoverage` | 0.081 | All 452 registered messages accounted for; upstream drift comparison passes. |
| 11 | `& $Python -B tools/check-openspec-config.py` | 0.038 | Configuration and its built-in negative fixtures pass. |

Coverage finds **374 migrated, 59 drafted and 19 trimmed** messages. Its
committed snapshot provenance remains `farm-common@b7ae4675393b4a8d7a293261c0347d941fdfc84a`;
the explicitly selected merged common source matches that snapshot. A drafted
disposition passes this static accounting gate, without certifying that draft's
behavior or authorizing promotion. The parked test drafts remain unmerged.

A separate **read-only manifest probe** hashes all **60** `.proto` source
files and writes only scratch output. Lowercase SHA-256, two spaces, C byte
path ordering and LF reproduce both the committed Git blob and the checkout's
`MANIFEST.sha256` **byte for byte**. Manifest SHA-256 is
`d6038613a74d9d2746e026278e2472db84e4950de9a49faf4671bc9c0c297323`. This proves the measured source
bytes and canonical manifest agree; it does **not** run or certify the Bash
generator or its check entry point. All **592 tracked Farm-Contract files**
remain byte-identical, with aggregate SHA-256
`0d52f1ef4713f075d64e189d58806dcd2985157ab3b36e5d88d7df57132aebcc`; both development checkouts remain clean.

**Eight required gate entries remain pending native Windows implementation**:
3 `check-breaking-waiver.sh`, 4 `gen-manifest.sh --check`, 5 `check-markers.sh`,
6 `check-msg-naming.sh`, 7 `check-proto-fields.sh`, 9 `check-spec-provenance.sh`,
10 `check-readme-inventory.sh` and 12 `check-openspec-validate.sh`, all under
`tools/`. They are not run in this sequence and earn no pass or skip credit.
The current repository has no equivalent Python, PowerShell or CMD entry
points for them. This is a missing native workflow, without evidence of a
protocol regression. No guard, ownership rule, waiver or test is weakened.
Farm-Contract uses Buf for compilation; protoc code generation belongs to
consumer repositories and is not an extra contract prerequisite.

UTF-8 command logs, coverage JSON, scratch manifest and private preparation
metadata remain ignored locally in `native-contract-70bce7cc` and its preparation reports.
Sanitized summary SHA-256 is `ef612700b280e00c9e81acef6ddf2ff55159a1690c694da2ef89eb57f1163355`;
probe-source SHA-256 is `49bdd337489e629ec5ed15ac93f3abd53bac024b838b91a0b9474ceb052e7f90`. Per-command exit codes, durations
and log hashes are preserved in the summary. Private paths, credentials and
raw host logs are not published. All **73 relevant documentation tests pass**
without skips/failures/errors in **0.355 seconds** (0.476 seconds including
startup); UTF-8 log SHA-256 is
`0d89c1d5748258557e48413f7a28c0d94dc25d0affef8aa5c7a23350c9d4c0e0`. Evidence hashes, links,
privacy and whitespace are checked. No executable behavior changes or full
FarmBot suite rerun occurs.

**Next:** port the manifest generator first, preserving the exact canonical
bytes and adding native Windows failure cases; then address the remaining
seven wrappers and their existing negative checks. Farm-Contract's
[task boundary](https://github.com/Kuaiwa-Network/Farm-Contract/blob/f18cbf6a4ae4a1e98dea1f99b7c8f0983769581d/AGENTS.md#一本仓任务只做契约)
requires code edits in a task rooted in that repository. A self-contained
private handoff is prepared, without writing contract or consumer code here.
The supported isolated-home Codex launch, actual sandbox-child acceptance,
remaining native backend gates, real Feishu/Word, ordinary-worker capability
checks, desktop Unity, private secret scan, release provenance/CI and scoped
TestBot restoration remain release prerequisites. This host measurement
does not certify the installed production service or enable feature workers.

## Native Windows contract manifest port review (2026-10-04; candidate)

Farm-Contract [PR #319](https://github.com/Kuaiwa-Network/Farm-Contract/pull/319)
adds a standard-library Python entry point for gate 4. The reviewed candidate is
**`445212aa9d9be774f72c35767f82ac1eaea5493b`**, based on
`f18cbf6a4ae4a1e98dea1f99b7c8f0983769581d`. At this review checkpoint it is unmerged. Code changes
and their regression coverage belong to the separate Farm-Contract-rooted task;
this FarmBot change records verification only.

Review reproduces a Windows regression at initial head
`805adf6ef6fbc3fce47037ba2dcb48d5f46b266b`: `os.walk(..., followlinks=False)`
still descends into a directory junction. With one ordinary protocol file and
a real junction to an outside dummy source directory, that version incorrectly
includes both files. The corrected candidate inspects `lstat()` reparse
attributes, rejects a linked/reparse `proto/` root, and prunes linked/reparse
directories before descent. Repeating the same native probe produces one
manifest entry, excludes the junction target and preserves the outside file.
Both generator invocations exit 0 in 0.054 seconds; no skip or permission
bypass is used. The linked-root, outside-target, only-linked-input and internal
alias/cycle regression cases create real junctions on Windows, and fail if
creation fails. Linux/macOS exercise real directory and file symlinks.

Independent acceptance runs on the **production Windows host**, using the
ordinary current-user token, a separate development checkout and a fresh
export of the candidate's committed bytes. Python **3.13.16** is selected
explicitly, with `PYTHONUTF8=1` set before startup. A Windows OS-variable
allowlist excludes inherited FarmBot, model and credential selectors; homes,
application-data directories and temporary files are fresh scratch paths.
Git user/system configuration is excluded and hooks/fsmonitor are disabled.
No Bash, MSYS, WSL, production configuration/ledger, Feishu authentication,
service, account setting or deployment is involved.

| Check | Command | Seconds | Result |
| --- | --- | --- | --- |
| Native regressions | `& $Python -I -X utf8 -B tools/test-gen-manifest.py` | 2.412 (2.502 including startup) | **19 pass; zero failures, errors or skips**, including all four junction cases. |
| Existing manifest | `& $Python -I -X utf8 -B tools/gen-manifest.py --check` | 0.064 | Exit 0. |
| Default generation | `& $Python -I -X utf8 -B tools/gen-manifest.py` | 0.065 | Exit 0; canonical bytes preserved. |
| Regenerated manifest | `& $Python -I -X utf8 -B tools/gen-manifest.py --check` | 0.063 | Exit 0. |

The full export/check sequence takes **4.945 seconds**. All **60 proto files**
and all **594 exported files** remain byte-identical. Independently hashed,
committed and regenerated manifest SHA-256 is
`d6038613a74d9d2746e026278e2472db84e4950de9a49faf4671bc9c0c297323`;
exported-tree aggregate SHA-256 is
`868f028c204e663124cac8fd63373e4cd7faeb9fcec19c1367e65ea65f29dd5b`.
Acceptance uses raw committed bytes: the host checkout's Git CRLF conversion
must not be repaired by normalizing protocol source to obtain a pass.

[CI run 37201736449](https://github.com/Kuaiwa-Network/Farm-Contract/actions/runs/37201736449)
matches the exact corrected candidate and passes all four jobs: existing
Linux contract gates, plus manifest checks on Windows, Linux and macOS.
Hosted Windows runs **19 tests** in 3.564 seconds. Linux runs **21 tests** in
2.716 seconds through the native entry point and 2.471 seconds through the
shell wrapper; macOS runs **21 tests** in 1.563 and 1.682 seconds respectively.
All test invocations have zero skips. The two shell-wrapper workflow steps
are intentionally excluded on Windows; these are not native test skips or
evidence of Windows Bash acceptance.

UTF-8 native command logs and sanitized summaries are retained privately in
`native-manifest-candidate-bac77720`; the junction reports are
`native-manifest-links-cac25174` and `native-manifest-links-fa960e54`.
Generator source SHA-256 is
`77b2e004b91735c47fa947b5badd2e4fb375d71d6b91ce8880858db1286ee601`;
native regression log SHA-256 is
`455c315c56f2124b3d27a153508ee89a15f1868e29bb2fab9db5df93a43db944`;
acceptance-summary SHA-256 is
`4e59feb8ed3a58ca81a1c899b2fbdc78b26fea94e1eac67bfce0f1c97de81e3f`.
An earlier overlay verification reached 19 passing tests but stopped because
the local verifier parsed stdout while unittest wrote its summary to stderr.
That incomplete report is retained; after repairing the verifier, acceptance
uses the exact committed candidate above. This is a verifier error, separate
from the reproduced and corrected junction regression. All **73 relevant
documentation tests** pass without skips, failures or errors in **0.371 seconds**
(0.480 seconds including startup); UTF-8 log SHA-256 is
`bf02b59c520531046bb2ef130b33481a5b74822b57a97652a2ef17042d66eba5`.
Evidence hashes, links, privacy and whitespace are checked; no full FarmBot
suite rerun is needed for this documentation-only change.

**Next at this checkpoint:** review and merge the code candidate separately, then verify the
actual merged revision. Native ports remain pending for gates **3, 5, 6, 7,
9, 10 and 12**. CI's full contract-gate pass is Linux evidence; it does not
establish a full native Windows twelve-gate pass. The local Node 24/CI Node 22
difference and strict OpenSpec path remain unverified. Native farm-hive
generators/gates, actual isolated Codex worker grants and containment, real
Feishu/Word access, ordinary-worker symlink capability, desktop Unity,
private secret scan, release provenance/CI and scoped TestBot restoration
remain release prerequisites. Neither code-candidate verification nor this
documentation change certifies the installed production service or enables
feature workers. Parked jobs and unmerged test drafts remain unchanged.

## Native Windows merged contract manifest acceptance (2026-10-04)

The operator merges Farm-Contract [#319](https://github.com/Kuaiwa-Network/Farm-Contract/pull/319)
as **`332b22c00dc4a79e0cbb7ecad1f187023319259d`**. Its complete Git tree equals
reviewed source `445212aa9d9be774f72c35767f82ac1eaea5493b`; this independently
verified merge does not introduce another implementation change. FarmBot's
candidate record [#105](https://github.com/Kuaiwa-Network/farm-linear-agent/pull/105)
is merged as `5d98453c4cc3058633b41af5c842cb3f8f780477`.

On the production Windows host, the same ordinary-token, isolated development
verifier exports the **exact merged committed bytes** and completes in
**5.348 seconds**. All **19 native Windows regressions** pass without failures,
errors or skips in **2.456 seconds** (2.548 including startup). Existing-manifest
check, default generation and recheck all exit 0 in 0.065, 0.064 and 0.076
seconds respectively. All 60 proto files and all 594 exported files remain
byte-identical, with the same manifest and aggregate tree hashes as the candidate.
No source normalization, containment change, production configuration/ledger
read, credential access, Bash/MSYS/WSL invocation or service action occurs.

[Push CI run 37202202685](https://github.com/Kuaiwa-Network/Farm-Contract/actions/runs/37202202685)
matches the exact merged SHA and succeeds in all four jobs. The breaking-waiver
workflow step is restricted to pull requests and therefore excluded on this
push; it passed in the reviewed candidate's PR run above. Windows again excludes
the two Mac/Linux shell-wrapper steps. These workflow exclusions do not earn
native Windows acceptance for the other seven pending gates.

Private UTF-8 logs and summary are retained in `native-manifest-candidate-1d16a613`.
Native regression log SHA-256 is
`4cd1f7309c4eed326c667cf6242955d17fbf424065be7baff4e7ef452a7c717b`;
acceptance-summary SHA-256 is
`24aa0bccc32718f6fdabaa783d8e0a61e698bc2eeb451ac084bcbd34eb972fdf`.
All 73 relevant documentation tests pass without skips, failures or errors
in 0.329 seconds (0.434 including startup); UTF-8 log SHA-256 is
`17aa62a2b801093c13509a62960c5c24eb51ea53805f6f7cec121787d5e223d4`.
Evidence, links, privacy and whitespace are checked.

**Next:** port gate 5, the placeholder provenance-marker check, in the existing
Farm-Contract-rooted task on a separate `codex/` branch. Preserve marker detection,
backtick legends, built-in negative checks, source boundaries and unchanged
protocol/spec bytes. Gate 4 now has merged native Windows entry-point evidence;
gates 3, 5, 6, 7, 9, 10 and 12 remain pending. Full native contract/backend checks,
actual isolated workers, real Feishu/Word, ordinary-worker capabilities, Unity
and the other release prerequisites listed below remain open.

## Native Windows contract marker gate review (2026-10-04; candidate)

Farm-Contract [PR #320](https://github.com/Kuaiwa-Network/Farm-Contract/pull/320)
ports gate 5, the placeholder provenance-marker check, at exact candidate
**`26e8e4aa897243b1d816f1f18350cedf1cb1e52d`**, based on merged manifest revision
`332b22c00dc4a79e0cbb7ecad1f187023319259d`. At this review checkpoint the code
PR is unmerged and ready for review. Code and its regression coverage remain
in the Farm-Contract-rooted task; this FarmBot change records evidence only.

The native Python implementation preserves the actual prefix regex, immediate
left-backtick exemption, both built-in negative/legend selftests, case-sensitive
`.md` selection and LF-only matching/line numbering. CR, form-feed and Unicode
separators do not introduce new grep lines. It includes project, specs, changes,
archive and hidden-directory Markdown, with no source-byte normalization.
The previous shell implementation could report success after a grep read error;
the shared implementation now rejects missing/unreadable, invalid UTF-8 and
empty ordinary-Markdown input. Source roots must be ordinary directories;
linked/reparse directories and files cannot select outside input. No detection
rule, source-ownership check or test is weakened.

Independent verification runs on the **production Windows host**, with the
ordinary current-user token, explicitly selected Python **3.13.16**, Git
**2.54.0.windows.1** and Git LFS **3.7.1**, in a separate development checkout
and fresh export of committed bytes. `PYTHONUTF8=1` precedes Python startup.
Windows OS-variable allowlisting excludes inherited FarmBot, model, repository
and credential selectors; home/temp/application-data paths are fresh scratch.
Git user/system configuration is excluded and hooks/fsmonitor are disabled.
No Bash/MSYS/WSL, production configuration/ledger, Feishu authentication,
service, account setting or deployment is involved.

| Check | Command | Seconds | Result |
| --- | --- | --- | --- |
| Native regressions | `& $Python -I -X utf8 -B tools/test-check-markers.py` | 1.052 (1.139 including startup) | **23 pass; zero failures, errors or skips**. |
| Real committed repository | `& $Python -I -X utf8 -B tools/check-markers.py` | 0.151 | Exit 0, `markers OK`; **379 OpenSpec Markdown files** in scope. |

The whole export/check sequence takes **3.884 seconds**. Tests exercise actual
CLI rejection and both deliberately damaged-pattern selftests, valid and invalid
markers, legends, LF/control-character boundaries, case-sensitive filenames,
all Markdown scopes, spaces/Unicode, unrelated cwd and missing/invalid input.
Read-denied, traversal-denied and disappeared-file cases use controlled IO
faults; they do not change host ACLs or prove a particular production ACL.
Four directory-boundary cases create real Windows junctions, confirm their
type and fail if creation fails. They cover linked roots, outside targets,
only-linked input, and aliases/cycles while preserving outside dummy sentinels.
Linux/macOS add two actual file-symlink cases. All **596 exported files** remain
byte-identical, with aggregate SHA-256
`a1bc6f5034bda6779b29d674c7c5f61be4f22ecbc2111a42e57c185ea5950e5c`.
Protocol, draft protocol, OpenSpec, manifest, waiver and merged manifest-tool
bytes are unchanged from the base. Manifest SHA-256 remains
`d6038613a74d9d2746e026278e2472db84e4950de9a49faf4671bc9c0c297323`.

[CI run 37204937766](https://github.com/Kuaiwa-Network/Farm-Contract/actions/runs/37204937766)
is green for all **seven jobs**: existing Linux contract gates, three manifest
jobs and three marker jobs. Marker checkout logs confirm the exact candidate
SHA. Hosted Windows runs **23 native tests** in 1.038 seconds; Linux runs
**25 native tests** in 0.684 seconds and **25 wrapper tests** in 0.700 seconds;
macOS runs **25 native tests** in 0.684 seconds and **25 wrapper tests** in
0.892 seconds. Every invocation has zero unittest skips/failures/errors, and
all applicable real marker checks pass. Windows excludes the two shell-wrapper
steps in each marker/manifest job; those workflow exclusions are not native
test skips or evidence for the remaining Windows ports. The full contract-gate
job remains Linux evidence.

Private UTF-8 logs and sanitized summary are retained in
`native-markers-candidate-ca7b16a0`. Checker source SHA-256 is
`c4bf3182929cfa62605c39fc35604bf7a3cafb2a5d427621e1166db3191a1396`;
regression log SHA-256 is
`5ab548201cc0f225983517c6619e9312e2586eb3d3ffaa3832954f7c5c8c0cf1`;
real-repository log SHA-256 is
`86e262f5368050e41ef72e05829fab4345767bffca4a438f76693f14cb2a6751`;
acceptance-summary SHA-256 is
`0b62fc5acd4d86d1e3f30f8f00ed9415ae30281b8d3c14f163242dfe9326f062`.
Per-command durations/exits and hashes remain in that summary. Private paths,
credentials and raw host logs are not published.

All **73 relevant documentation tests** pass without skips, failures or errors
in **0.323 seconds** (0.432 including startup); UTF-8 log SHA-256 is
`a20bb6a6bac854187df5d7a670e82e6f95c04436205e8edf69cbd497eb805a2a`.
Evidence hashes, links, privacy and whitespace are checked; this documentation
change needs no full FarmBot suite rerun.

**Next at this checkpoint:** merge the reviewed code separately and verify the exact merged
revision before promoting gate 5's candidate evidence, then address gate 6's
message-naming check in the Farm-Contract-rooted task. Six other native contract
wrapper ports remain pending: **3, 6, 7, 9, 10 and 12**. Native backend paths,
actual isolated Codex workers, real Feishu/Word, ordinary-worker capabilities,
desktop Unity, private secret scan, release provenance/CI and scoped TestBot
restoration remain release prerequisites. No full Windows twelve-gate pass,
installed-service readiness or production feature enablement is claimed;
parked jobs and unmerged test drafts stay unchanged.

## Native Windows merged contract marker acceptance (2026-10-04)

The operator merges Farm-Contract [#320](https://github.com/Kuaiwa-Network/Farm-Contract/pull/320)
as **`8c7e591ee22dd8e7c47eee08254e923f891b1473`**. Its full Git tree equals
reviewed source `26e8e4aa897243b1d816f1f18350cedf1cb1e52d`, independently
verified before rechecking the exact merged export. The FarmBot candidate
record [#107](https://github.com/Kuaiwa-Network/farm-linear-agent/pull/107)
is merged as `856771dbc502ea06cd0543f69bc3c329c6ac66c6`.

On the production Windows host, the same ordinary-token development verifier
uses explicitly selected Python 3.13.16, pre-start `PYTHONUTF8=1`, sanitized
OS-variable-only environments and fresh scratch state. The export/check
sequence completes in **3.959 seconds**. All **23 native Windows tests** pass
without failures/errors/skips in **1.038 seconds** (1.128 including startup),
including all four real junction cases. The real checker returns **exit 0,
`markers OK`** in **0.152 seconds**, with all **379 OpenSpec Markdown files**
in scope. All **596 exported files** remain byte-identical, with the same
aggregate and manifest hashes as the reviewed candidate. No source-byte
normalization, production configuration/ledger read, credential access,
Bash/MSYS/WSL invocation, service action or host-setting change occurs.

[Push CI run 37206895753](https://github.com/Kuaiwa-Network/Farm-Contract/actions/runs/37206895753)
matches the exact merged SHA and succeeds in all **seven jobs**. Its PR-only
breaking-waiver step is excluded on push, retaining the reviewed PR's passing
result. Windows excludes the two shell-wrapper steps in each marker/manifest
job. These workflow exclusions do not establish native Windows acceptance
for the six other pending gates; the full contract-gate job remains Linux
evidence.

Private UTF-8 logs and sanitized summary are retained in
`native-markers-candidate-9d722b3b`. Native regression log SHA-256 is
`ba71b793bc2e33eb09ad324efb3e78e9bdad38cd953bf059bb2357799ce08dc0`;
real-repository log SHA-256 remains
`86e262f5368050e41ef72e05829fab4345767bffca4a438f76693f14cb2a6751`;
acceptance-summary SHA-256 is
`fb885af5e271f966701f38a156d80c79ef26300d864038a39c823e5fab9547b6`.
All 73 relevant documentation tests pass without skips, failures or errors
in 0.331 seconds (0.436 including startup); UTF-8 log SHA-256 is
`9be2d78b80ca12456a4851f64e218f6b56b181353ee5a313d385514a81cf342a`.
Evidence, links, privacy and whitespace are checked.

**Next:** gate 6's native message-naming port is dispatched to the existing
Farm-Contract-rooted task on a separate branch. Preserve same-file `_Ack` to
`_Req` pairing, legitimate request-only messages, top-level source scope and
coverage/read-failure refusal. Gate 5 now has merged native entry-point evidence;
gates **3, 6, 7, 9, 10 and 12**, native backend paths and actual worker/live
release prerequisites remain pending. Production feature enablement and
deployment remain outside this authorization; parked jobs and test drafts
remain unchanged.

## Native Windows contract message-naming gate review (2026-10-05; candidate)

Farm-Contract [#321](https://github.com/Kuaiwa-Network/Farm-Contract/pull/321)
ports gate 6 at committed source **`f055513b53f58be38d36cd9692c356452b62ffc5`**, based on merged
gate 5 source **`8c7e591ee22dd8e7c47eee08254e923f891b1473`**. Only the native Python checker and regressions,
POSIX wrapper, README and CI change. Protocols, OpenSpec sources and manifest
are unchanged. This is candidate acceptance; an operator-approved merge and
exact-merged-source verification remain required.

The checker preserves the one-way rule: each actual `<X>_Ack` declaration
requires `<X>_Req` in the **same file**; legitimate request-only messages remain
allowed. It selects ordinary, non-hidden, case-sensitive top-level
`proto/*.proto` files, with no recursive scan. Token recognition handles legal
multiline whitespace and comments while excluding declarations inside comments
and string literals. Missing/unreadable/invalid input, malformed lexical or
declaration structure, no selected files and zero actual Ack declarations fail
instead of earning coverage. Full protobuf syntax/type validation remains
the existing `buf build` gate. Linked/reparse roots are refused and internal
link entries are excluded; checks never normalize source bytes.

The parent independently exports the exact committed candidate on the
**production Windows host**, using a separate development checkout, fresh
scratch state, the ordinary current-user token and explicitly selected Python
**3.13.16**. `PYTHONUTF8=1` is set before Python starts; native commands use
`-I -X utf8 -B` and sanitized OS-variable-only child environments. Git is
**2.54.0.windows.1**, Git LFS **3.7.1**. No production configuration/ledger
read, credential access, Bash/MSYS/WSL invocation, service action or host-setting
change occurs. This development CLI check does not certify the actual isolated
FarmBot worker's grants, credentials or runtime.

All **34 native Windows tests** pass without failures, errors or skips in
**2.085 seconds** (2.171 including startup).
All three real owned junction cases execute: linked root refusal, selected
reparse-directory exclusion and linked-only coverage refusal. Controlled IO
faults verify read/inventory/disappeared-source failure without changing ACLs
or privileges. Regression cases also cover same-file versus cross-file
pairing, request-only messages, lexical boundaries, comments/literals,
multiline declarations, UTF-8/BOM/CRLF, top-level scope and unrelated cwd.
The three POSIX file-link cases are defined only on POSIX; they are not Windows
unittest skips or ordinary-token Windows file-symlink acceptance.

The real native checker accepts **all 60 proto files and
237 Ack declarations**, returns exit 0 in
**0.046 seconds**, and reports
`msg naming OK（237 个 _Ack 全部有配对 _Req）`. The export/check sequence takes
**4.732 seconds**. All **598 exported files** remain byte-identical;
aggregate SHA-256 is
`707038ee29ed49ff45c087a44e393daea8d55c134e870cf934cbf7fb0b4f1af3`.
Manifest SHA-256 remains
`d6038613a74d9d2746e026278e2472db84e4950de9a49faf4671bc9c0c297323`.

[CI run 37221446869](https://github.com/Kuaiwa-Network/Farm-Contract/actions/runs/37221446869)
matches the candidate head and succeeds in all **10 jobs**, including the
existing Linux contract gates and manifest/marker matrices. Naming-job logs
confirm exact-head checkout on each platform: Windows **34** native tests in
**1.833 seconds**; Linux **37** native plus **37** wrapper tests in
**1.349 / 1.468 seconds**; macOS **37** native plus **37** wrapper tests in
**1.654 / 1.980 seconds**. Each suite has zero failures/errors/skips and each
real checker reports 237 paired Ack declarations. Windows excludes both
shell-wrapper steps in each naming, marker and manifest job; these workflow
exclusions do not establish native acceptance for the other contract gates.

Private UTF-8 logs and sanitized summary are retained in `native-msg-naming-candidate-aa07dec1`.
Checker-source SHA-256 is
`170896af30248c7f58b4103a30100c8933605876d712d5d7c924e5c8d2e447c0`;
native regression log SHA-256 is
`57210b32a288cbc628574a43116aebbe22e81b5f4e9224726ea057a6ddc2adcf`;
real-repository log SHA-256 is
`e3100536fbd8d5fbd8562f46a52b6271a0e71f384950c1ddda36dd37fb7e2d4c`;
review-summary SHA-256 is
`5e4b929f070d9e6e88d148e89aa8f915fb3a95daf7e2d2e74b890a6c6a60e030`.

All 73 relevant documentation tests pass without skips, failures or errors
in 0.320 seconds (0.428 including startup); UTF-8 log SHA-256 is
`1d1f6c87b3c4db698949a7c03803c2daec7235898c774bdf842b76be5d69f46d`.
Evidence, links, privacy and whitespace are checked.

**Next:** obtain the operator's code merge for #321, verify its merged source
and applicable CI, then port gate 7 on a separate branch. Gates **3, 7, 9, 10
and 12** still need native ports; gate 6 remains candidate evidence until merged
acceptance. Native backend paths, actual isolated-worker/live access and the
remaining release prerequisites below remain pending. Production feature
enablement and deployment stay outside this authorization; parked jobs and
unmerged test drafts remain unchanged.

## Native Windows merged contract message-naming acceptance (2026-10-05)

The operator merges Farm-Contract [#321](https://github.com/Kuaiwa-Network/Farm-Contract/pull/321)
as **`831c1e1b5068168b433476923f7c106661b7b3f7`**. Its full Git tree equals reviewed source
`f055513b53f58be38d36cd9692c356452b62ffc5`, independently verified before rechecking the exact
merged export. FarmBot candidate record
[#109](https://github.com/Kuaiwa-Network/farm-linear-agent/pull/109) is merged
as `931584bae2dfad6ad80c26ad876dd11b9a069a2f`. The preceding section remains the pre-merge checkpoint.

On the production Windows host, the ordinary current-user token runs the
same development verifier against a fresh merged-source scratch export with
explicit Python 3.13.16, pre-start `PYTHONUTF8=1` and sanitized OS-variable-only
child environments. All **34 native Windows tests** pass without
failures/errors/skips in **2.276 seconds**
(2.455 including startup), including all three actual junction cases.
The real checker accepts all **60 proto files and 237 Ack declarations**,
returns exit 0 in **0.049 seconds**, and reports
`msg naming OK（237 个 _Ack 全部有配对 _Req）`.
The full export/check sequence takes **5.813 seconds**. All **598 exported
files** remain byte-identical with the reviewed candidate's aggregate,
checker-source and manifest hashes. No source-byte normalization, production
configuration/ledger read, credential access, Bash/MSYS/WSL invocation, service
action or host-setting change occurs. Actual FarmBot worker grants, runtime
and credential access remain outside this development CLI acceptance.

[Push CI run 37242538627](https://github.com/Kuaiwa-Network/Farm-Contract/actions/runs/37242538627)
matches the exact merged SHA and succeeds in all **10 jobs**. The PR-only
breaking-waiver step is excluded on push, retaining the reviewed PR's passing
result. Windows excludes the two wrapper steps in each naming, marker and
manifest job. These workflow exclusions are separate from unittest skips;
the full Linux contract-gate job does not establish native Windows acceptance
for the five other pending gates.

Private UTF-8 logs and sanitized summary are retained in `native-msg-naming-candidate-4d277295`.
Native regression log SHA-256 is
`a931d4bb7d7eb25ced6299c703ff3ad157a89a5ee9560894800dabddd8d7d5a4`;
real-repository log SHA-256 remains
`e3100536fbd8d5fbd8562f46a52b6271a0e71f384950c1ddda36dd37fb7e2d4c`;
acceptance-summary SHA-256 is
`b8b082e24550d096258b2fd01a086dbb7528d2a560f40e820b16563c835e71a3`.

All 73 relevant documentation tests pass without skips, failures or errors
in 0.659 seconds (0.786 including startup); UTF-8 log SHA-256 is
`2bf27bd6da40c8be50982eda515b4c8951653ac3971d760f6b6f51cf6fc40822`.
Evidence, links, privacy and whitespace are checked.

**Next:** gate 7 is dispatched to the existing Farm-Contract-rooted task on a
separate branch. Preserve the entire gate: stat and guild talent fields,
message/enum assertions, error-code census, README consistency and built-in
negative/positive/mutation selftests. Gate 6 now has merged native entry-point
evidence; gates **3, 7, 9, 10 and 12**, native backend paths and actual
worker/live release prerequisites remain pending. Production feature
enablement and deployment remain outside this authorization; parked jobs and
unmerged test drafts remain unchanged.

## Native Windows contract field gate review (2026-10-05; candidate)

Farm-Contract [#322](https://github.com/Kuaiwa-Network/Farm-Contract/pull/322)
ports gate 7 at committed source **`d73e91d6990b8eee8a545fcc6f283b3721f45285`**, based on merged
gate 6 source **`831c1e1b5068168b433476923f7c106661b7b3f7`**. Only the native Python checker and regressions,
POSIX wrapper, README and CI change. Protocols, OpenSpec sources and the
canonical manifest are unchanged. This is candidate acceptance; an
operator-approved merge and exact-merged-source verification remain required.

The entire gate is retained: stat's claimed-stages field and two reward
messages; guild talent/retro field numbers and existing type/cardinality
predicates; the whole-body milestone prohibition; duplicate RetroClaim tags;
two explicit first UNSPECIFIED enum zero values; the 38-message issued census;
canonical error-code comment conflicts and active 217–227 / 228–232 coverage;
three README-owned ranges and dynamic next-unallocated header/tail agreement.
The four stat negative/positive selftests run before real input; the guild
reward-id mutation runs after the candidate passes. Stat selftest/argument
failures return 2, while real input/guard/mutation failures return 1. Full
protobuf syntax/type validation remains `buf build`.

Token recognition accepts legal multiline whitespace and comment-separated
declarations without allowing comments/literals to supply them. The code scan
preserves hidden top-level `.proto` coverage and case-sensitive suffixes,
without recursion. Read failures, malformed lexical/declaration structure,
invalid UTF-8, nonordinary sources and linked/reparse roots/inputs refuse
acceptance. Source bytes are never normalized or written.

Early parent review reproduces two draft-port regressions: map and qualified
field types evade the original duplicate-tag assertion. They are fixed before
acceptance, with committed duplicate-tag negatives and distinct-tag positives,
including absolute qualified types. The original options-bearing tag-census
boundary is preserved; it is covered explicitly and is not full protobuf
validity evidence. No protocols or guard requirements are changed to obtain
a pass.

The parent independently exports the exact final committed source on the
**production Windows host**, using a separate development checkout, fresh
scratch state, the ordinary current-user token and selected Python **3.13.16**.
`PYTHONUTF8=1` is set before Python; commands use `-I -X utf8 -B` and sanitized
OS-variable-only child environments. Git is **2.54.0.windows.1**, Git LFS **3.7.1**.
All **55 native Windows tests** pass with zero failures/errors/skips in
**12.901 seconds** (12.995 including startup).
All six real owned junction cases execute, covering repository/proto roots
and stat/guild/README/code-scan inputs. Controlled IO faults test denied and
disappeared inputs without changing ACLs or privileges. Six POSIX-only actual
file-link/dangling-link/FIFO cases are defined only on POSIX; they do not
certify ordinary-token Windows file-symlink capability or constitute Windows
unittest skips.

The real native checker accepts all **60 proto files** and the required README,
returns exit 0 in **0.091 seconds**, and retains both success lines:
`proto-fields OK` and `guild-compete talent proto-fields OK`. The export/check
sequence takes **15.788 seconds**. All **600 exported files** remain byte-identical;
aggregate SHA-256 is
`fa03fed4aaf2909151c785462232dd105559ec86204ba01a03732abb7333fdf2`.
Manifest SHA-256 remains
`d6038613a74d9d2746e026278e2472db84e4950de9a49faf4671bc9c0c297323`.
No production configuration/ledger read, credential access, Bash/MSYS/WSL
invocation, service action or host-setting change occurs. Actual FarmBot
worker grants, credentials and runtime remain outside this development CLI
acceptance.

[CI run 37244166468](https://github.com/Kuaiwa-Network/Farm-Contract/actions/runs/37244166468)
matches the candidate head and succeeds in all **13 jobs**, preserving all
existing contract gates and manifest/marker/naming jobs. Field-job logs confirm
exact-head checkout: Windows **55** native tests in **15.243 seconds**;
Linux **61** native plus **61** wrapper tests in **13.881 / 13.512 seconds**;
macOS **61** native plus **61** wrapper tests in **11.896 / 12.622 seconds**.
Each suite has zero failures/errors/skips and each real checker returns both
success lines. Windows excludes the two wrapper steps in each field, naming,
marker and manifest job. These workflow exclusions do not establish native
acceptance for the other contract gates.

Private UTF-8 logs and sanitized summary are retained in `native-proto-fields-candidate-2827892b`.
Checker-source SHA-256 is
`e2309518d41dc04d5360d7047e8f145dcb25ec95ccf2dc9ba7d519df5702b8b9`;
native regression log SHA-256 is
`34dad1128d3166d7369259d0f8871bcf9abf34a575cbe897e726e5d8b42c652e`;
real-repository log SHA-256 is
`3643f69ebc44674f10fe5c117046a2003f2862ce65bdd08e59061001dffd3d56`;
review-summary SHA-256 is
`cd243c880abb4a9ea7ed4ae04aa92136c9d0f88a4a5eb83db7a1f2b0c3945680`.

All 73 relevant documentation tests pass without skips, failures or errors
in 0.554 seconds (0.685 including startup); UTF-8 log SHA-256 is
`32cee7b0992451bf8e36c1bda45e91cd25c0e6c844c80287c871eb012d89d544`.
Evidence, links, privacy and whitespace are checked.

**Next:** obtain the operator's code merge for #322 and verify its merged
source and applicable CI, then continue with gate 9. Gates **3, 9, 10 and 12**
still need native ports; gate 7 remains candidate evidence until merged
acceptance. Native backend paths, actual worker/live access and the remaining
release prerequisites below remain pending. Production feature enablement
and deployment stay outside this authorization; parked jobs and unmerged
test drafts remain unchanged.

## Native Windows merged contract field-gate acceptance (2026-10-05)

The operator merges Farm-Contract [#322](https://github.com/Kuaiwa-Network/Farm-Contract/pull/322)
as **`396cf6d4f5cc2ca66f9f115a2af2dc93ce334583`**. Its full Git tree equals reviewed source
`d73e91d6990b8eee8a545fcc6f283b3721f45285`, independently checked before rechecking the exact
merged export. FarmBot candidate record
[#111](https://github.com/Kuaiwa-Network/farm-linear-agent/pull/111) is merged
as `7765b04b17b021d32aa5a5d67897634f8e96e6ef`. The preceding candidate section remains the pre-merge
checkpoint.

On the production Windows host, the ordinary current-user token runs the
development verifier against a fresh merged-source scratch export using
explicit Python 3.13.16, pre-start `PYTHONUTF8=1` and sanitized OS-variable-only
child environments. All **55 native Windows tests** pass without
failures/errors/skips in **13.279 seconds**
(13.373 including startup), including all six actual junction cases.
The real checker accepts all **60 proto files and the required README**,
returns exit 0 in **0.092 seconds**, and reports both
`proto-fields OK` and `guild-compete talent proto-fields OK`.
The full export/check sequence takes **16.269 seconds**. All **600 exported
files** remain byte-identical, matching the reviewed candidate's aggregate,
checker-source and manifest hashes. No protocol or OpenSpec bytes change.
No production configuration/ledger read, credential access, Bash/MSYS/WSL
invocation, service action or host-setting change occurs. Actual FarmBot
worker grants, runtime and credential access remain outside this development
CLI acceptance.

[Push CI run 37245046067](https://github.com/Kuaiwa-Network/Farm-Contract/actions/runs/37245046067)
matches the exact merged SHA and succeeds in all **13 jobs**. Field-job logs
confirm exact merged checkout: Windows **55** native tests in **19.123 seconds**;
Linux **61** native plus **61** wrapper tests in **11.287 / 8.583 seconds**;
macOS **61** native plus **61** wrapper tests in **12.508 / 12.013 seconds**.
Each suite has zero failures/errors/skips; each real checker reports both
success lines. The PR-only breaking-waiver step is excluded on push,
retaining its passing reviewed-PR result. Windows excludes the two wrapper
steps in each field, naming, marker and manifest job. These workflow
exclusions are separate from unittest skips; the full Linux gate job does
not establish native Windows acceptance for the remaining gates.

Private UTF-8 logs and sanitized summary are retained in `native-proto-fields-candidate-dd6e4337`.
Native regression log SHA-256 is
`839693966c1901542b90f80200dbe233c2b150363636b2d6291e6288962dc0ea`;
real-repository log SHA-256 remains
`3643f69ebc44674f10fe5c117046a2003f2862ce65bdd08e59061001dffd3d56`;
acceptance-summary SHA-256 is
`bd6ba806b02fb29499d6b53fac285b611efdc4633501316aca9e139a0d50d60e`.

Documentation checks pass all **73 relevant tests** in **0.364 seconds**
(0.474 including startup), without failures/errors/skips. Local links,
public evidence URLs, retained log hashes, privacy and whitespace are checked.
UTF-8 documentation-test log SHA-256 is
`32f9cc88457536b445a22e4b688ffb62854ed0ca2b952b4f58a747778daf8f73`.

**Next:** gate 9 is dispatched to the existing Farm-Contract-rooted task on a
separate branch. Preserve every provenance direction and built-in selftest,
without editing protocols, specifications or the provenance table. Gate 7 now
has merged native entry-point evidence. Gates **3, 9, 10 and 12**, native
backend paths and actual worker/live release prerequisites remain pending.
Production feature enablement and deployment remain outside this authorization;
parked jobs and unmerged test drafts remain unchanged.

## Native Windows contract provenance-gate review (2026-10-05; candidate)

Farm-Contract [#323](https://github.com/Kuaiwa-Network/Farm-Contract/pull/323)
ports gate 9 at committed source **`272e06b20797b0a53282498e1bb1c0e9497cf478`**, based on merged
gate 7 source **`396cf6d4f5cc2ca66f9f115a2af2dc93ce334583`**. Only the native Python checker and regressions,
POSIX wrapper, README and CI change. Protocols, OpenSpec sources, the provenance
table and canonical manifest are unchanged. This is candidate acceptance;
an operator-approved merge and exact-merged-source verification remain required.

All four guards remain: missing registration, stale registration, nonexistent
archive references and unflattened nonhidden spec directories. The four
negative and one positive built-in selftests run before repository input;
their failure or fixture IO failure returns 2. Unsupported arguments return
2 before input/selftests; real input/guard failures return 1, and success is
exactly `spec provenance OK`. The test fixture's early junction cleanup issue
is corrected using per-alias active ownership state before acceptance.
Initial [CI run 37246856085](https://github.com/Kuaiwa-Network/Farm-Contract/actions/runs/37246856085)
at `8198885` passes Windows and the other gate jobs, but Linux/macOS each
report four failed subcases: a test-injected `scandir` fault converts cleanup's
integer directory handles to `Path`. The focused fixture fix passes those
handles to the real `scandir`, retaining every intended directory-denial
assertion. No checker guard, cleanup error or test is skipped to obtain a pass.

The TSV retains two views: Bash tab-IFS row validation collapses tabs while
coverage compares the raw first column. CR/BOM are not normalized; only
LF-terminated rows are validated, while a final unterminated row participates
in raw coverage. Duplicate registrations, opaque nonempty `inplace:` suffixes,
no-note rows and registered non-`.md` files retain their original boundaries.
The reverse scan remains nonhidden, top-level, case-sensitive `.md`; empty
existing inventories gain no new minimum-count rule. Missing/unreadable/
invalid UTF-8 and nonordinary selected inputs refuse acceptance. Unsafe
absolute/traversal/drive/UNC/ADS references and selected links/reparse roots,
files or archive ancestors are rejected before following them. No source bytes
are normalized or written, and no note or Git-commit policy is added.

The parent independently exports the exact committed source on the
**production Windows host**, using a separate development checkout, fresh
scratch state, the ordinary current-user token and explicit Python **3.13.16**.
Pre-start `PYTHONUTF8=1`, `-I -X utf8 -B` and sanitized OS-variable-only child
environments prevent inherited FarmBot selectors. All **39 native Windows
tests** pass without failures/errors/skips in **6.688 seconds**
(6.801 including startup), exercising all ten actual owned
junction fixtures across root components, repository, selected spec/table,
archive leaf/ancestor and unregistered directory boundaries. Controlled IO
faults require no ACL/privilege changes. Eight POSIX-only tests cover actual
Bash parsing/parity and file links/dangling links/FIFOs; they are defined only
on POSIX and are not Windows unittest skips or file-symlink certification.

The real native checker accepts all **34 main specification files**, returns
exit 0 in **0.080 seconds**, and reports `spec provenance OK`.
The export/check sequence takes **9.612 seconds**. All **602 exported
files** remain byte-identical; aggregate SHA-256 is
`e4a92a6b6d0aa74bbab17de256996dcc5fdf34e1c9ce061480c43135c3dd2b16`. Provenance-table SHA-256 remains
`11d5e30979a7991cbd025e1eb13adc6a6e4eb41db25cf6ec5d3700f7f5c4dd43`; manifest SHA-256 remains
`d6038613a74d9d2746e026278e2472db84e4950de9a49faf4671bc9c0c297323`. A separate parent probe passes **27 fresh fixtures**
in **1.658 seconds**, preserving all their bytes and confirming all four
directions, parser boundaries, scope, Unicode/spaces and invalid/unsafe input.
No production configuration/ledger read, credential access, Bash/MSYS/WSL
invocation, service action or host-setting change occurs. Actual FarmBot
worker grants, credentials and runtime remain outside this development CLI
acceptance.

[CI run 37247258352](https://github.com/Kuaiwa-Network/Farm-Contract/actions/runs/37247258352)
matches the candidate head and succeeds in all **16 jobs**, retaining the
other contract gates. Provenance-job logs confirm exact checkout: Windows
**39** native tests in **6.094 seconds**; Linux
**47** native plus **47** wrapper tests in **3.016 / 6.119 seconds**;
macOS **47** native plus **47** wrapper tests in **5.040 / 4.994 seconds**.
Each suite has zero failures/errors/skips and each real checker reports success.
Windows excludes the two wrapper steps in each provenance, field, naming,
marker and manifest job. These workflow exclusions are separate from unittest
skips and do not establish native acceptance for the other pending gates.

Private UTF-8 logs and sanitized summary are retained in `native-spec-provenance-candidate-dde1c295`.
Checker-source SHA-256 is `6fbf79ac758f337df4cc7b85bab71aa5859de4a9becfb78a2199675146a1a60e`;
native regression log SHA-256 is `bd77eb9b7f5af479cdad0370f3bd324731553997690af10c4c74eb23b8df2754`;
real-repository log SHA-256 is `1ec7ed86b48eea81824f2a3e5f544d56cd6ef82db4ce300ccf43b50d9d610ee2`;
review-summary SHA-256 is `2137009897c03f1e4373d482e29db6c71b0d312ec07ac4b38a4e705eb9fe505b`;
independent fixture-summary SHA-256 is `95c4dfc0ac681ab297a7709050a04c82dfd3cfb71f7f82a890817d3a73be744c`.

Documentation checks pass all **73 relevant tests** in **0.355 seconds**
(0.469 including startup), without failures/errors/skips. Local links,
public evidence URLs, retained log hashes, privacy and whitespace are checked.
UTF-8 documentation-test log SHA-256 is
`0d89c1d5748258557e48413f7a28c0d94dc25d0affef8aa5c7a23350c9d4c0e0`.

**Next:** obtain the operator's code merge for #323 and verify its merged
source and applicable CI, then continue with gate 10. Gates **3, 10 and 12**
still need native ports; gate 9 remains candidate evidence until merged
acceptance. Native backend paths, actual worker/live access and the remaining
release prerequisites below remain pending. Production feature enablement
and deployment stay outside this authorization; parked jobs and unmerged
test drafts remain unchanged.

## Native Windows merged contract provenance-gate acceptance (2026-10-05)

The operator merges Farm-Contract [#323](https://github.com/Kuaiwa-Network/Farm-Contract/pull/323)
as **`ea30dd9e49ac9e8bdace590476ab31b701fce6cf`**. Its full Git tree equals reviewed source
`272e06b20797b0a53282498e1bb1c0e9497cf478`, independently checked before rechecking the exact
merged export. FarmBot candidate record
[#113](https://github.com/Kuaiwa-Network/farm-linear-agent/pull/113) is merged
as `347b9350b30f04d2f18b3bb2c3f6de741f7c0d82`. The preceding candidate section remains the pre-merge
checkpoint.

On the production Windows host, the ordinary current-user token runs the
development verifier against a fresh merged-source scratch export using
explicit Python 3.13.16, pre-start `PYTHONUTF8=1` and sanitized OS-variable-only
child environments. All **39 native Windows tests** pass without
failures/errors/skips in **6.169 seconds**
(6.260 including startup), including all ten actual owned junction
fixtures and each of the five built-in selftest failure checks. The real
checker accepts all **34 main specification files**, returns exit 0 in
**0.103 seconds**, and reports `spec provenance OK`.
The full export/check sequence takes **9.163 seconds**. All **602 exported
files** remain byte-identical, matching the reviewed candidate's aggregate,
checker-source, provenance-table and manifest hashes. No protocol, OpenSpec
or provenance-table bytes change. The original four directions and inherited
TSV boundaries remain as documented in the candidate checkpoint.
No production configuration/ledger read, credential access, Bash/MSYS/WSL
invocation, service action or host-setting change occurs. Actual FarmBot
worker grants, runtime and credential access remain outside this development
CLI acceptance.

[Push CI run 37247730174](https://github.com/Kuaiwa-Network/Farm-Contract/actions/runs/37247730174)
matches the exact merged SHA and succeeds in all **16 jobs**. Provenance-job
logs confirm exact merged checkout: Windows **39** native tests in **6.045 seconds**;
Linux **47** native plus **47** wrapper tests in **2.934 / 6.470 seconds**;
macOS **47** native plus **47** wrapper tests in **4.538 / 4.973 seconds**.
Each suite has zero failures/errors/skips; each real checker reports success.
The PR-only breaking-waiver step is excluded on push, retaining its passing
reviewed-PR result. Windows excludes the two wrapper steps in each provenance,
field, naming, marker and manifest job. These workflow exclusions are separate
from unittest skips; the full Linux gate job does not establish native Windows
acceptance for the remaining gates.

Private UTF-8 logs and sanitized summary are retained in `native-spec-provenance-candidate-846f0477`.
Native regression log SHA-256 is
`730311a3beaab121d888dd3ef60fe53a6efa71671df26694868943e5447c230d`;
real-repository log SHA-256 remains
`1ec7ed86b48eea81824f2a3e5f544d56cd6ef82db4ce300ccf43b50d9d610ee2`;
acceptance-summary SHA-256 is
`62b46d9f4a135d14857505a5a006aa2c9a38ef54a9145202721aba68132c613f`.

Documentation checks pass all **73 relevant tests** in **0.348 seconds**
(0.458 including startup), without failures/errors/skips. Local links,
public evidence URLs, retained log hashes, privacy and whitespace are checked.
UTF-8 documentation-test log SHA-256 is
`16f96ae172cd7622da8cd5c753d648e6568f47d259731126eb123c0e2bea7334`.

**Next:** gate 10 is dispatched to the existing Farm-Contract-rooted task on a
separate branch. Preserve the actual README extractor, both startup selftests,
duplicate-row multiset comparison and both mismatch directions, without
editing protocols or their inventory rows. Gate 9 now has merged native
entry-point evidence. Gates **3, 10 and 12**, native backend paths and actual
worker/live release prerequisites remain pending. Production feature enablement
and deployment remain outside this authorization; parked jobs and unmerged
test drafts remain unchanged.

## Native Windows contract inventory-gate review (2026-10-05; candidate)

Farm-Contract [#324](https://github.com/Kuaiwa-Network/Farm-Contract/pull/324)
ports gate 10 at committed source **`474cb5f5ca19af4fcabccc292680816591ec0132`**, based on merged
gate 9 source **`ea30dd9e49ac9e8bdace590476ab31b701fce6cf`**. Only the native Python checker and regressions,
POSIX wrapper, README and CI change. Protocols, OpenSpec sources, provenance
table, manifest and the extracted README inventory sequence are unchanged.
This is candidate acceptance; an operator-approved merge and exact-merged-source
verification remain required.

Both inventory mismatch directions and both startup extractor selftests remain.
README extraction scans the entire text for the original anchored literal
table-cell prefix and lowercase ASCII filename pattern; it counts matching
rows in fences and accepts a matching prefix without a following separator.
It retains LF-only line boundaries, CRLF/EOF/BOM effects and duplicate-row
multiset comparison. Protocol selection remains nonhidden, top-level and
case-sensitive `.proto`. No extracted rows, no selected protocols and two
empty inputs fail. Missing/unreadable/invalid UTF-8/nonordinary selected inputs
and linked/reparse roots/files refuse acceptance, with exact root input names
on Windows. Out-of-scope links are not followed. Source bytes are not normalized
or written; protobuf syntax and table descriptions remain outside this gate.
Selftest/argument failures return 2; real input/guard failures return 1;
success is exactly `readme inventory OK`.

Two early fixture mistakes are corrected before final acceptance. Writing
`A.proto` beside existing `a.proto` overwrites the same Windows file rather
than creating a case variant; the regression now performs an actual owned
rename. Initial [CI run 37248910197](https://github.com/Kuaiwa-Network/Farm-Contract/actions/runs/37248910197)
at `668e6d0` reports one Ubuntu parity subcase failure because it assumed the
original unmatched `ls` glob always exits 1. The original exits **2 on Ubuntu
and 1 on macOS**; the native CLI deliberately standardizes real-input failures
to **1**. The corrected test measures the host's actual `ls` code, requires the
legacy script to match it, and separately requires native exit 1. The empty
case stays tested, and checker source is unchanged by this correction.
Private failure logs and corrections are retained by the Contract task.

The parent independently exports the exact final source on the **production
Windows host**, using a separate development checkout, fresh scratch state,
the ordinary current-user token and explicit Python **3.13.16**. Pre-start
`PYTHONUTF8=1`, `-I -X utf8 -B` and OS-variable-only child environments prevent
inherited FarmBot selectors. All **35 native Windows tests** pass without
failures/errors/skips in **2.441 seconds**
(2.532 including startup). All eight actual owned junction
fixtures execute, including selected root/file/loop refusal and ignored-scope
non-following. Controlled IO faults require no ACL/privilege changes. Eight
POSIX-only tests cover actual legacy Bash parsing/parity, file links/dangling
links and FIFOs; they are not registered on Windows and do not constitute
Windows unittest skips or file-symlink certification.

The real checker accepts **60 protocol files and 60 README rows**, returns
exit 0 in **0.036 seconds**, and reports `readme inventory OK`.
The export/check sequence takes **5.353 seconds**. All **604 exported
files** remain byte-identical; aggregate SHA-256 is
`05b5b3aa84a28c3de11a7adab3ddb50482d5e43ec5c5dadbcd9247d63cedaff5`; manifest SHA-256 remains
`d6038613a74d9d2746e026278e2472db84e4950de9a49faf4671bc9c0c297323`. The parent also verifies the full legacy Bash test
literal matches the original baseline byte-for-byte, SHA-256
`cf1cc6f625fa7c1dbf05d20cc404885d3cf322547224228e9e168f39c52f0efe`, and passes **26 extra fresh fixtures** in
**0.926 seconds** with unchanged bytes. No production configuration/ledger
read, credential access, Bash/MSYS/WSL invocation, service action or host-setting
change occurs. Actual FarmBot worker/runtime/credential acceptance remains pending.

[CI run 37249210548](https://github.com/Kuaiwa-Network/Farm-Contract/actions/runs/37249210548)
matches the final head and succeeds in all **19 jobs**. Inventory logs confirm
exact checkout: Windows **35** native tests in **2.676 seconds**; Linux **43**
native plus **43** wrapper tests in **2.723 / 3.675 seconds**; macOS **43**
native plus **43** wrapper tests in **2.442 / 2.940 seconds**. Each suite has
zero failures/errors/skips; each real checker reports success. Actual legacy
empty-glob measurements confirm Ubuntu exit 2/macOS exit 1 and native exit 1
in both native/wrapper parity suites. Windows excludes two wrapper steps in
each inventory, provenance, field, naming, marker and manifest job. These
workflow exclusions do not establish native acceptance of the remaining gates.

Private UTF-8 logs and sanitized summary are retained in `native-readme-inventory-candidate-857c5ac9`.
Checker-source SHA-256 is `16e00a8f695956321c9e81f35501d6a31df02ebedb657791fdcd852200c52dbd`;
native regression log SHA-256 is `265891f641df90c6131e9bbb0cc6976229b87cb7136a907eb44c2303d01622f2`;
real-repository log SHA-256 is `66c541dc6711646ce95243035b6864c4bbf41a7e420bb65c0ca53923ea1091e4`;
review-summary SHA-256 is `1594bac30ff1c4810e6ffa1ae0d93ddf5bb9bda128b80cdc630dbc71faf99b32`;
extra-fixture summary SHA-256 is `e1640e9dd2bf12b2b0ae194ef335d4dd16b0de20799c0303686f731655ff72d7`.

Documentation checks pass all **73 relevant tests** in **0.339 seconds**
(0.447 including startup), without failures/errors/skips. Local links,
public evidence URLs, retained log hashes, privacy and whitespace are checked.
UTF-8 documentation-test log SHA-256 is
`ac402aac8f8943024b7b27ecedfb92bcf94138e546cb8b2051baa82b4c509453`.

**Next:** obtain the operator's code merge for #324 and verify its merged source
and applicable CI, then continue with gate 12. Gates **3 and 12** still need
native ports; gate 10 remains candidate evidence until merged acceptance.
Native backend paths, actual worker/live access and the release prerequisites
below remain pending. Production feature enablement and deployment stay outside
this authorization; parked jobs and unmerged test drafts remain unchanged.

## Native Windows merged contract inventory-gate acceptance (2026-10-05)

The operator merges Farm-Contract [#324](https://github.com/Kuaiwa-Network/Farm-Contract/pull/324)
as **`5cf4c7e8a2fba37cacd212e983b7a3b2d383f9f5`**. Its full Git tree equals reviewed source
`474cb5f5ca19af4fcabccc292680816591ec0132`, independently checked before rechecking the exact
merged export. FarmBot candidate record
[#115](https://github.com/Kuaiwa-Network/farm-linear-agent/pull/115) is merged
as `5eece47b090c7878c5ae7c4c1bcac7571fbbf888`. The preceding candidate section remains the pre-merge
checkpoint, including both corrected fixture failures and 26 extra checks.

On the production Windows host, the ordinary current-user token runs the
development verifier against a fresh merged-source scratch export using
explicit Python 3.13.16, pre-start `PYTHONUTF8=1` and sanitized OS-variable-only
child environments. All **35 native Windows tests** pass without
failures/errors/skips in **2.724 seconds**
(2.815 including startup), including all eight actual owned junction
fixtures and both startup extractor selftest failure checks. The real checker
accepts **60 protocol files and 60 README rows**, returns exit 0 in
**0.035 seconds**, and reports `readme inventory OK`.
The full export/check sequence takes **5.560 seconds**. All **604 exported
files** remain byte-identical, matching the reviewed candidate's aggregate,
checker-source, README, provenance-table, legacy literal and manifest hashes.
No protocol, OpenSpec or provenance-table bytes change. Both mismatch directions,
duplicate-row multiset comparison and inherited extractor/scope boundaries remain
as documented in the candidate checkpoint. Actual ordinary-token file/directory
symlink capability is not certified by these junction fixtures.
No production configuration/ledger read, credential access, Bash/MSYS/WSL
invocation, service action or host-setting change occurs. Actual FarmBot
worker grants, runtime and credential access remain outside this development
CLI acceptance.

[Push CI run 37250183343](https://github.com/Kuaiwa-Network/Farm-Contract/actions/runs/37250183343)
matches the exact merged SHA and succeeds in all **19 jobs**. Inventory-job
logs confirm exact merged checkout: Windows **35** native tests in **3.112 seconds**;
Linux **43** native plus **43** wrapper tests in **3.153 / 3.643 seconds**;
macOS **43** native plus **43** wrapper tests in **2.938 / 3.611 seconds**.
Each suite has zero failures/errors/skips; each real checker reports success.
The PR-only breaking-waiver step is excluded on push, retaining its passing
reviewed-PR result. Windows excludes the two wrapper steps in each inventory,
provenance, field, naming, marker and manifest job. These workflow exclusions
are separate from unittest skips; the full Linux gate job does not establish
native Windows acceptance for the remaining gates.

Private UTF-8 logs and sanitized summary are retained in `native-readme-inventory-candidate-bd08628a`.
Native regression log SHA-256 is
`613449955f349326868c57430f001557be696b4575df945d027c94d9dde38dbf`;
real-repository log SHA-256 remains
`66c541dc6711646ce95243035b6864c4bbf41a7e420bb65c0ca53923ea1091e4`;
acceptance-summary SHA-256 is
`fb80bf2233fbb730eb329cc3f8525841c98e41aa22f7b694c4a78b01b2a9eb90`.

Documentation checks pass all **73 relevant tests** in **0.336 seconds**
(0.444 including startup), without failures/errors/skips. Local links,
public evidence URLs, retained log hashes, privacy and whitespace are checked.
UTF-8 documentation-test log SHA-256 is
`52f9c50c454cb24b9323386954f59cb2496d71b43f4b772e6afd3df3ffbacbe6`.

**Next:** gate 12 is dispatched to the existing Farm-Contract-rooted task on a
separate branch, preserving the actual pinned OpenSpec CLI, both startup coverage
selftests, its lower-bound coverage assertion and the existing skip_specs warning
inventory. Gate 10 now has merged native entry-point evidence. Gates **3 and 12**,
native backend paths and actual worker/live release prerequisites remain pending.
Production feature enablement and deployment remain outside this authorization;
parked jobs and unmerged test drafts remain unchanged.

## Native Windows contract OpenSpec-validation review (2026-10-05; candidate)

Farm-Contract [#325](https://github.com/Kuaiwa-Network/Farm-Contract/pull/325)
ports gate 12 at **`4e04e217dc88d8ea59f6e945e11115feb582b117`**, based on merged gate 10
`5cf4c7e8a2fba37cacd212e983b7a3b2d383f9f5`. The five changed files are the native Python checker,
regressions, POSIX wrapper, README and CI. Protocols, OpenSpec artifacts/config,
provenance table, manifest and breaking-waiver data are unchanged. Operator
code merge and exact-merged-source acceptance remain required.

The wrapper uses native Node and the installed OpenSpec JS entry point, with
the package name/version checked against the existing unique CI pin. It
delegates Markdown parsing to the real pinned CLI, invoking text
`validate --all --strict` then JSON validation. Both startup coverage selftests
remain, as does `summary.totals.items >= expected`: existing empty discovery
allows `0 >= 0`; missing roots refuse before the CLI can search a parent.
Archive/hidden/non-directory count exclusions, POSIX LF filename record counts,
Python-to-Bash integer boundaries and duplicate JSON last-key behavior remain.
There is no new item-ID reconciliation, parser or waiver ledger. The literal
`skip_specs: *true` warning retains comments/true prefixes and archive-root
metadata. Flat main spec files remain outside upstream discovery. Selected
roots/files refuse links/reparse, nonordinary/unreadable/invalid UTF-8 input;
hidden links do not expand scope. Selftest/argument errors return 2, local
input/launch errors return 1, and upstream nonzero statuses are preserved.

Parent review corrects local npm `.bin` discovery before acceptance; native
Node now locates the JS entry without executing CMD shims. A proposed
nonordinary-file fixture had used a directory named `spec.md`, which the
upstream parser accepts as a discovery folder; the corrected tests retain
that positive case. Initial [CI run 37253109505](https://github.com/Kuaiwa-Network/Farm-Contract/actions/runs/37253109505)
passes 21/22 jobs. Its single macOS failure compares temporary-directory path
spellings that refer to the same directory. Final commit changes only that
fixture to `samefile`, preserving gate input guards. Private logs also retain
a recording-script assertion that confused joined stderr progress with stdout
success, and the parent's initial scenario fixture corrected to remove the
whole scenario section: the installed parser accepts other nonfenced level-four
headings. These are fixture/recording corrections, with no added skips.

The parent independently verifies the final committed export on the
**production Windows host**, using a separate development checkout and fresh
scratch state under the ordinary current-user token. Explicit Python
**3.13.16**, native Node **v24.19.0** and OpenSpec **1.7.0** run with pre-start
`PYTHONUTF8=1`, `-I -X utf8 -B`, sanitized environments and isolated child homes,
APPDATA and XDG paths. Telemetry and background version lookup are disabled;
the CLI receives no inherited FarmBot/Feishu/runtime selectors.
All **49 native Windows tests** pass without failures/errors/skips in
**15.053 seconds** (15.149 including startup).
All **19 actual owned junction fixtures** execute, including runtime inputs;
ordinary-token file/directory symlink privilege is not certified by junctions.
Nine POSIX-only tests are not registered on Windows: three original Bash
parity cases, four file-symlink/dangling cases and two FIFO cases.

Both explicit runtime selection and actual local npm PATH discovery accept all
**50 in-flight changes**, in **0.639 / 0.636 seconds**,
with identical stdout and the two existing skip_specs entries reported. The sequence
takes **19.614 seconds**; all **606 exported files** remain byte-identical,
aggregate SHA-256 `bb0768b28959bcea988c2a1ece92e378aee7e73eb49f6ce3267bef65561eb6ec`. The embedded original
Bash literal equals the merged baseline, SHA-256 `d5c2e64422d88dcb9e09d0d6262764a810ca9c5921b042719f1fcd04682738b0`.
Ten extra fresh real-parser fixtures pass in **4.537 seconds**, without
source mutation, against the initial candidate's checker bytes, which are
identical in the final head. Manifest SHA-256 remains `d6038613a74d9d2746e026278e2472db84e4950de9a49faf4671bc9c0c297323`.
No production configuration/ledger read, credential access, Bash/MSYS/WSL
invocation, service action or app-setting change occurs. Native development
CLI acceptance does not certify an actual FarmBot worker or production release.

[CI run 37253335593](https://github.com/Kuaiwa-Network/Farm-Contract/actions/runs/37253335593)
matches the final head and passes all **22 jobs**. Each OpenSpec job confirms
exact checkout, package pin, source preservation of 606 files, zero unittest
failures/errors/skips and a real 50-change success:

- openspec-validate (macos-latest): 58 tests in 13.817s / 58 tests in 12.359s; Node v22.23.2 / OpenSpec 1.7.0.
- openspec-validate (ubuntu-latest): 58 tests in 16.811s / 58 tests in 18.456s; Node v22.23.3 / OpenSpec 1.7.0.
- openspec-validate (windows-latest): 49 tests in 26.645s; Node v22.23.3 / OpenSpec 1.7.0.

The Windows job excludes its two Bash wrapper steps; the six preceding native
gate jobs retain their existing Windows wrapper exclusions. POSIX jobs execute
both native and wrapper suites. These workflow exclusions are distinct from
unittest skips and do not certify gate 3 or backend native paths.

Private UTF-8 logs and sanitized summary are retained in `native-openspec-validate-candidate-a887aa4f`.
Checker SHA-256 is `34c49c83654b815cd4d5dd80639f8312996925c74c0ae930c4ea34b6bcb87efd`;
native test log SHA-256 is `02cd6286a25ca8b1ea0f788722cddbbe12bb510bc6eadd08085815565ce031cd`;
real check/PATH-discovery log SHA-256 is `914024e76ac25d600b8c64370a6ab91571db9ae9fff79ff5802110a42beb1171`;
acceptance-summary SHA-256 is `fa0c52ea4d168cb628b380626bd97139d9d909fd489f364f13f24b6136fdd5a2`;
extra-fixture summary SHA-256 is `9d62a5cc69e348b67e7da175d84d407257251bbb3591f6a65739ee2c0215f61a`.

Documentation checks pass all **73 relevant tests** in **0.360 seconds**
(0.470 including startup), without failures/errors/skips. Local links,
public evidence URLs, retained log hashes, privacy and whitespace are checked.
UTF-8 documentation-test log SHA-256 is
`fd2892da4bec0752d40c81c6441c849adbb3785e7ae29c14d3f5ee12d06449e6`.

**Next:** obtain the operator's code merge for #325 and verify its merged source
and applicable CI, then continue with gate 3. Native backend generators/gates,
actual isolated worker/runtime/credential and live access checks, and the
release prerequisites below remain pending. Production feature enablement and
deployment stay outside this authorization; parked jobs and unmerged test
drafts remain unchanged.

## Native Windows merged contract OpenSpec-validation acceptance (2026-10-05)

The operator merges Farm-Contract [#325](https://github.com/Kuaiwa-Network/Farm-Contract/pull/325)
as **`c6fd15902fefc154c704925eca984dbbf9cf25d1`**. Its full Git tree equals reviewed source
`4e04e217dc88d8ea59f6e945e11115feb582b117`, independently checked before testing the exact merged
export. FarmBot candidate record
[#117](https://github.com/Kuaiwa-Network/farm-linear-agent/pull/117) is merged
as `97e61e80280774c18e118eaa92eb20b6b07af76d`. The preceding candidate checkpoint retains the initial
macOS fixture failure, its canonical-CWD-only correction and ten extra actual
parser cases; the checker bytes remain identical.

On the production Windows host, the ordinary current-user token runs the
development verifier in a fresh merged-source scratch export, with explicit
Python 3.13.16, pre-start `PYTHONUTF8=1`, native Node v24.19.0 and the existing
pinned OpenSpec 1.7.0 package. Sanitized child environments exclude inherited
FarmBot and credential selectors and use owned home/temp/AppData directories.
All **49 native Windows tests** pass without failures/errors/skips in
**15.565 seconds** (15.659 including startup),
including all **19 actual owned junction fixtures**. The real upstream parser
accepts all **50 in-flight changes** with explicit native runtime selectors in
**0.626 seconds**, and with actual local npm PATH discovery in
**0.624 seconds**. Both checks return exit 0 and identical
success output. The full export/check sequence takes **20.185 seconds**.
All **606 exported files** remain byte-identical to the reviewed candidate;
checker, package entry, legacy literal, provenance and manifest hashes also match.
The upstream strict parser, both startup coverage selftests, lower-bound coverage,
literal skip_specs warning and input/process guards remain as documented.
Junction fixtures do not certify ordinary-token file/directory symlink capability.
These are development CLI checks; actual FarmBot worker grants/runtime/credentials
and real Feishu access remain pending. No production config/ledger read,
credential access, Bash/MSYS/WSL invocation, service action or host-setting change occurs.

[Push CI run 37254693409](https://github.com/Kuaiwa-Network/Farm-Contract/actions/runs/37254693409)
matches the exact merged SHA and succeeds in all **22 jobs**. OpenSpec job logs
confirm exact merged checkout and the same pinned package: Windows **49** native
tests in **25.435 seconds**; Linux **58** native plus **58** wrapper tests in
**12.733 / 12.094 seconds**; macOS **58** native plus **58** wrapper tests in
**13.959 / 13.152 seconds**. Every suite has zero failures/errors/skips. Each native
real check and both POSIX wrapper checks accept all 50 changes; all three jobs
preserve the 606-file source set. Windows/Linux use Node v22.23.3; macOS uses
v22.23.2. The PR-only breaking step is excluded on push, retaining its passing
reviewed-PR result. Windows excludes two Bash wrapper steps in each of the seven
native gate jobs; these workflow exclusions are separate from unittest skips.
The full Linux gate job does not establish native Windows gate 3 acceptance.

Private UTF-8 logs and sanitized summary are retained in `native-openspec-validate-candidate-7a9504fd`.
Native test log SHA-256 is `13c16fb19333d308592afdf4be2c878cca6cce16ce8befdcf1e2ebdf728c57fd`;
real/PATH-discovery log SHA-256 remains `914024e76ac25d600b8c64370a6ab91571db9ae9fff79ff5802110a42beb1171`;
acceptance-summary SHA-256 is
`e117cf105a4a06831f3c9e7db69f2b546759ff61015f82e0ebf24e77a948cab3`.

Documentation checks pass all **73 relevant tests** in **0.337 seconds**
(0.442 including startup), without failures/errors/skips. Local links,
public evidence URLs, retained log hashes, privacy and whitespace are checked.
UTF-8 documentation-test log SHA-256 is
`f681519694b91a1c04ccc83e3faadbe2a05e1c25a07da7d3ecd233c6446dcea0`.

**Next:** gate 3 is dispatched to the existing Farm-Contract-rooted task on a
separate branch. It must preserve both waiver mismatch directions, exact
path/rule/full-message identity, duplicate and COMPILE refusal, baseline/bootstrap
distinction and all 17 inherited criterion selftests, using native Git and the
existing pinned buf. Gate 12 now has merged native entry-point evidence. Native
backend generators/gates, actual isolated worker/runtime/credential and live
access checks, and final release prerequisites remain pending. Production feature
enablement/deployment stay outside this authorization; parked jobs and unmerged
test drafts remain unchanged.

## Native Windows contract breaking-waiver candidate (2026-10-05)

Farm-Contract [#326](https://github.com/Kuaiwa-Network/Farm-Contract/pull/326)
contains exact candidate **`c5529a883bd3bb3b1d9cdf559a730b0aa74bc42f`**, based on merged #325
`c6fd15902fefc154c704925eca984dbbf9cf25d1`. Independent review verifies exactly five changed files:
the native checker, regression suite, thin POSIX wrapper, README and CI.
No proto, proto-draft, OpenSpec, provenance, manifest, buf configuration or
`BREAKING_WAIVERS` bytes change. This is candidate evidence; the code PR is
unmerged and exact merged-source acceptance remains required.

On the production Windows host, the ordinary current-user token runs the
development verifier from a separate checkout and fresh owned scratch export.
Python **3.13.16** starts with `PYTHONUTF8=1`; native Git is **2.54.0.windows.1**,
Git LFS remains **3.7.1**, and actual buf **1.72.0** matches the single existing
CI pin. Sanitized child environments exclude inherited FarmBot, Git, buf and
credential selectors, with fresh home/temp/AppData. No production config or
ledger is read; no credentials, Bash/MSYS/WSL, service actions, app settings,
account setup or live access are used.

The exact-source commands run with the explicitly selected Python executable:
`-I -X utf8 -B tools/test-check-breaking-waiver.py` and
`-I -X utf8 -B tools/check-breaking-waiver.py BREAKING_WAIVERS origin/main`.
All **55 native Windows tests** pass in **52.407 seconds**
(52.507 including startup), with zero failures/errors/skips.
All **17 actual owned junction fixtures** execute; they do not certify ordinary
file/directory symlink privilege. All **17 inherited criterion selftests** remain
byte-faithful and run before input/process access. The real **60-proto** check
against an owned local Git baseline passes with **zero waivers / zero breaks**
in **3.480 seconds**. Native PATH discovery from an
unrelated working directory produces identical success output in
**3.384 seconds**. All **608 exported files**
remain unchanged; the full sequence takes **66.679 seconds**.

A second independent actual Git/buf sequence passes **11 breaking fixtures**
and **four baseline cases** in **11.530 seconds**. Correct portable-path
waivers for one/two deletions pass. Unrecorded deletions, stale/changed-message
entries, an extra Kick_Ntf deletion, duplicate waivers and COMPILE fail; field
type changes use complete actual buf rules/messages. Missing refs fail;
reachable empty bootstrap plus empty waivers passes; nonempty bootstrap fails;
a baseline directory named `buf.yaml` is refused rather than misclassified as
bootstrap. Each checker invocation preserves its fixture source bytes.

The original native buf diagnostics use backslashes in Windows paths. The new
adapter changes only actual diagnostic path separators to `/`; rule/message and
waiver columns remain literal, without case folding or dot-path collapse.
Duplicate identities after conversion fail, and POSIX backslashes remain literal.
Both mismatch directions, exact path/rule/full-message identity, TSV processing,
COMPILE/duplicate refusal and unknown-output/exit-code refusal remain enforced.
Git replacement objects are disabled for both preflight and buf's local clone;
external controller includes, alternates and selected reparse inputs are refused.

Development failures are retained rather than hidden by skips. Initial buf/Git
environment handling dropped an empty Git configuration value; the correction
uses a nonempty encoded helper reset. CRLF pin parsing is corrected. Owned deep
Git clone paths require process-only `core.longpaths=true`. A deep startup-TEMP
fixture initially times out; focused traces locate the block in Windows process
creation before buf starts. Startup probes at 250/258/259/260 UTF-16 units finish,
whereas 270 units times out after **8.012 seconds**, even with short child TEMP
and output paths. `LongPathsEnabled=1` was read without changing it. The checker
now refuses startup TEMP above 260 units before that unbounded API; the exact
260-unit native Git startup and deeper-directory refusal are regression-tested.
These measurements do not claim universal Windows long-path support.

Windows launches suspended children into a private kill-on-close Job before
resuming them, tracks verified members with held handles and bounds settlement.
POSIX uses an owned session. Focused tests prove another owned process survives
timeout cleanup and a dead root does not leave its owned descendant running;
all four focused checks pass in **2.129 seconds**. No containment/ownership checks
are weakened, no skips added, and cleanup touches only positively owned probes.
Actual FarmBot worker runtime/grants/containment acceptance remains separate.

[CI run 37259556317](https://github.com/Kuaiwa-Network/Farm-Contract/actions/runs/37259556317)
succeeds in **25/25 jobs**. The three new jobs check out the exact candidate:
Windows **55** tests in **58.544 seconds**; Linux **60** native plus **60** wrapper
tests in **18.829 / 18.454 seconds**; macOS **60** native plus **60** wrapper tests
in **29.045 / 25.212 seconds**. All suites have zero failures/errors/skips.
Python is 3.13.16 and buf is 1.72.0 on all three; Windows Git is 2.55.0.windows.5,
Linux/macOS Git is 2.55.0. Native real checks and both POSIX wrappers pass with
zero waivers/breaks. All three preserve the same 608-file source set.
The original full gates job runs PR merge-test commit
`64872b794fe2ddf51c4501feb41c6ad06580fc2b`, whose complete Git tree independently equals the
candidate tree `d18fbb2948db5e1631698ac86ff90f3ddc6034af`. Its breaking wrapper passes. Windows excludes
two Bash wrapper steps in each of eight gate jobs (16 workflow steps); the five
POSIX-only regression methods are defined only on POSIX. These platform
exclusions are separate from unittest skips and do not certify Windows Bash.

Private UTF-8 logs/summaries are retained in `native-breaking-waiver-candidate-27dc6c18` and
`native-breaking-waiver-fixtures-55c77a88`. Native test log SHA-256:
`807d09ba1cf7db088a25de5ba366714de60730ae1e4fdc2f6c042a230c12d7ac`;
real/PATH-discovery log SHA-256:
`0e8495a6814aed321d4f031d131cf19a83a5d57c18c4d327015f5758b27729c7`;
acceptance summary SHA-256:
`293c25797fc0fa45983c1cac638db2b711d24416d7fa9d32b3c4b33ebbcc5446`;
extra-fixture summary SHA-256:
`af0237a145951ead4bfd6324e6efc50fe8672e665fa650ddb4d7e1df2dee41bc`.
Checker SHA-256 is `4883b762048be2ab74f8d05082f83a2c98d46e00fd65b9057bc65c438a7580ed`;
the unchanged legacy literal is `3aa091c5446976a51e64c17616ba59861ef24276eab60518859a4f80286479ca`.
Native buf executable SHA-256 is `6e8f6d043e520bc81cae7b85d4cd6d93e57716a8a9842d5d18200191ee259cb5`.
CI source aggregate SHA-256 is
`e99dd3ccdf7ca58440a377cd7cdae84b5f2ce93163efad0c83885836dc007fd2`.
Retained development diagnostics and all command/CI logs have independently
checked hashes; raw paths and host logs remain private.

Documentation checks pass all **73 relevant tests** in **0.611 seconds**
(0.739 including startup), without failures/errors/skips. Links, whitespace,
privacy and retained evidence hashes are checked. UTF-8 documentation-test log
SHA-256 is `08ee39b66e37d017b29708553e6ed1a74d7fbf8688ca66d39770f68b69c0d431`.

**Next:** the operator merges code #326, then verify its exact merged source and
applicable CI. Gate 3 is the last Contract Bash wrapper port; native backend
generators/gates, producer/publication, actual isolated worker/runtime/credential
and real Feishu checks, ordinary-token symlink/desktop Unity acceptance, private
secret scan, release provenance and scoped TestBot restoration remain pending.
The UI implementer follows the required client-stage acceptance. Production
feature enablement/deployment needs separate authorization; parked jobs and
unmerged test drafts remain unchanged.

## Native Windows merged contract breaking-waiver acceptance (2026-10-05)

The operator merges Farm-Contract [#326](https://github.com/Kuaiwa-Network/Farm-Contract/pull/326)
as **`71dadaed8d111219e7170ab6712d97bbb28d8925`**. Its full Git tree
`d18fbb2948db5e1631698ac86ff90f3ddc6034af` equals reviewed source `c5529a883bd3bb3b1d9cdf559a730b0aa74bc42f`.
FarmBot candidate record [#119](https://github.com/Kuaiwa-Network/farm-linear-agent/pull/119)
is merged as `009613f2dbf46bdc0257e19f4faf3bdba109a3d9`. Its preceding checkpoint retains the
11 extra actual breaking fixtures, four baseline cases, initial failures,
corrections and measured startup TEMP limit. Checker and authority bytes match.

On the production Windows host, the ordinary current-user token tests a fresh
exact-merged-source scratch export from the separate development checkout.
The selected Python 3.13.16 starts with `PYTHONUTF8=1`; actual native Git is
2.54.0.windows.1, Git LFS is 3.7.1 and the existing pinned buf is 1.72.0.
Owned home/temp/AppData and sanitized child environments exclude inherited
FarmBot, Git, buf and credential selectors. Commands use the selected Python
executable with `-I -X utf8 -B tools/test-check-breaking-waiver.py` and
`-I -X utf8 -B tools/check-breaking-waiver.py BREAKING_WAIVERS origin/main`.
All **55 Windows tests** pass in **51.830 seconds**
(51.930 including startup), with zero failures/errors/skips.
All **17 actual owned junction fixtures** and **17 inherited criterion selftests**
execute. The real **60-proto** check against an owned local Git baseline passes
with **zero waivers / zero breaks** in **3.556 seconds**;
actual native PATH discovery from an unrelated working directory produces
identical success output in **3.497 seconds**.
All **608 exported files** remain unchanged and match the reviewed candidate.
The complete sequence takes **66.402 seconds**.

[Merged push CI run 37260999457](https://github.com/Kuaiwa-Network/Farm-Contract/actions/runs/37260999457)
checks the exact merged SHA and succeeds in **25/25 jobs**. Windows runs **55**
breaking-waiver tests in **62.469 seconds**; Linux runs **60** native plus **60**
wrapper tests in **24.346 / 22.701 seconds**; macOS runs **60** native plus **60**
wrapper tests in **22.429 / 23.664 seconds**. All suites have zero failures/errors/skips.
All three use Python 3.13.16 and buf 1.72.0; Windows Git is 2.55.0.windows.5,
Linux/macOS Git is 2.55.0. The PR-only breaking comparisons are excluded on push:
native real comparison/source-preservation plus wrapper real comparison in
each of three new jobs, and the original gates job's breaking step. They retain
the passing candidate-PR comparison evidence; push does not invent a new
protocol baseline. Windows excludes 16 Bash wrapper steps across eight jobs
(including the already-counted breaking wrapper comparison). Five POSIX-only
regression methods are defined only on POSIX. Every workflow exclusion is
separate from unittest skips. Exact merged-source real comparison and source
preservation are measured locally above.

Private UTF-8 logs/summary are retained in `native-breaking-waiver-candidate-767bb0fb`. Native test
log SHA-256 is `00ea337ca48501c16af4d6791b40911b0c6860b557a33c84edfb0aa2e8a4527a`;
real/PATH-discovery log SHA-256 is `0e8495a6814aed321d4f031d131cf19a83a5d57c18c4d327015f5758b27729c7`;
acceptance-summary SHA-256 is
`511d86bd87b01620ce43fa6f9086b6d3e430fe0f6c6197e1c095a71e7bb76aa4`.
The checker remains `4883b762048be2ab74f8d05082f83a2c98d46e00fd65b9057bc65c438a7580ed`, with unchanged legacy literal,
provenance, manifest, waiver/config and native buf hashes. Retained log hashes,
exact checkouts and platform exclusions are independently verified.

Gate 3 is the last Contract Bash wrapper port. Its two-way exact identity,
COMPILE/duplicate/output refusal, baseline/bootstrap distinction, replacement-ref
handling, input guards and owned process cleanup remain enforced. Windows
startup TEMP above 260 UTF-16 units still refuses early; the 260-unit native
startup case passes. Junction tests do not certify ordinary-token symlink
privilege, and these CLI checks do not certify actual FarmBot worker grants,
runtime/credential handling or real Feishu access. No production config/ledger
read, credential access, Bash/MSYS/WSL, service/account/app setting or deployment
action occurs; parked jobs and unmerged test drafts remain unchanged.

Documentation checks pass all **73 relevant tests** in **0.385 seconds**
(0.492 including startup), without failures/errors/skips. Links, privacy,
whitespace and retained evidence hashes are checked. UTF-8 documentation-test
log SHA-256 is `f78a13b324f83a5de910a53d43dce4f9f6b438387509616efe012dd6732a4a21`.

**Next:** inspect native backend generators/gates from farm-hive main
`e24b6cbc4403e19c49bf13cc7766deaa49db5184` in a fresh separate development checkout. Preserve
tool pins, canonical hashes, exact output sets, contract reachability and
dirty-source rejection, read-only sibling boundaries, caches, LFS and owned
containment. Backend tooling, producer/publication, actual worker/live checks,
ordinary-token symlink/desktop Unity, private secret scan, release provenance
and scoped TestBot restoration remain release prerequisites. Client-stage
acceptance still precedes UI authoring; production enablement/deployment requires
separate authorization.

## Native Windows backend local message candidate (2026-10-05)

After exact merged Farm-Contract #326 acceptance, FarmBot record #120 is merged
as `fe723d6d24a4653cfd98a647ca1ebfccea93c278`. A fresh separate farm-hive
development checkout starts from main **`e24b6cbc4403e19c49bf13cc7766deaa49db5184`** (tree
`b35a260771d5718b3d539368c763edbbe44dc31a`). Tests run on the production Windows
PC with the ordinary current-user token, in owned development scratch exports;
they do not read the production installation or certify its running worker.
Python **3.13.16** starts with `PYTHONUTF8=1`; native Git is **2.54.0.windows.1**
and Git LFS **3.7.1**. The global Go **1.24.9** is below this repository's pin.
Private preparation downloads exact native Go **1.25.1** and public protobuf
**v1.36.8**, taking **16.817 / 1.847 seconds**. Acceptance then runs offline with
prepared caches, `GOENV=off`, `GOWORK=off`, `GOTOOLCHAIN=local`,
`GOPROXY=off`, `GOSUMDB=off`, `GOFLAGS=-mod=readonly -buildvcs=false`, owned home/temp/AppData
and sanitized FarmBot/Git/authentication selectors. No private dependency fetch
or authentication occurs. Pinned protoc remains **35.1**.

**Unchanged main primitives:** actual native `go test -json ./cmd/protoreggen`
passes all **five tests**, without failures/errors/skips, in
**11.355 seconds** including startup.
Native `go run ./cmd/protoreggen` takes
**1.886 seconds**, reproducing
**59 registry files**, **58 packages** and **576 wire messages** byte for byte.
All **4,658 source files** stay unchanged; complete preparation/export/checks
take **65.406 seconds**. Direct native protoc/Go calls also reproduce
all **58 message `.pb.go` files** with existing mappings/source-relative outputs
in **2.446 seconds** (3.751 total), preserving
all 4,658 source files. This primitive probe alone does not verify a native
message entry point or either backend gate. Registry already has a native Go
entry point; its Bash CI wrapper still needs its own native gate equivalent.

**New code candidate:** farm-hive [#354](https://github.com/Kuaiwa-Network/farm-hive/pull/354)
at **`1f892e84b85bed2c5cb2022f00cb590e4218e3ef`**, tree `cfaf8e8f4cc7525e2cfc09ded91e5f2571f0e850`, adds the explicit
`gen-msg-protos.py --local` entry point, owned process helper, actual regression
suite, scoped three-platform CI and README. Exactly five files change; all old
Bash scripts, Go/module pins, protocol snapshots, manifests and generated
artifacts retain their bytes. The new entry point reads the existing sole
TARGETS table, runs four inherited branch-parser selftests, requires the declared
branch and exact pins, preflights ordinary selected inputs/outputs/caches,
refuses command shims, and disables downloads/credentials. It regenerates only
selected `.pb.go` outputs; absence of `--local` refuses full synchronization.
Windows launches suspended into a private Job; reviewed #326 runner functions
are AST-identical. Other platforms use owned POSIX sessions. Output files and
verified handles preserve bounded owned-process settlement.

On a fresh exact-commit export, all **15 actual Windows tests** pass in
**27.411 seconds**
(27.558 including startup), zero failures/errors/skips.
Commands use the selected Python executable with
`-I -X utf8 -B tools/test_gen_msg_protos.py` and
`-I -X utf8 -B gen-msg-protos.py --local --go GO_EXE --protoc PROTOC_EXE --gomodcache MODULE_CACHE --gocache BUILD_CACHE`.
Actual full CLI reproduction from an unrelated directory takes
**3.225 seconds** and preserves all
**4,662 exported files**, including all 58 canonical outputs. The full exact
export/check sequence takes **58.318 seconds**. Real fixtures cover
source/tool/plugin-cache paths with spaces, Chinese text and emoji; poisoned
inherited selectors; empty module-cache offline refusal; an actual junction;
branch/pin/input/output errors; compile failure; argument-file injection; dead
root/owned descendant settlement; and survival of another separately owned
process. Junctions do not certify ordinary-token symlink privilege.

**Measured failures and corrections:** the primitive probe initially assumed
the POSIX plugin version basename; Windows correctly reports
`protoc-gen-go.exe v1.36.8`. Correcting that strict probe expectation does not
change a pin. The initial 12-test fixture run has one error from CMD path/output
handling; canonical native CMD plus `/u` and UTF-16LE diagnostics correct it,
and the focused actual junction check passes in **0.210 seconds**. The first
hosted candidate `475e98d7` then runs 14 tests in **68.983 seconds** with
**three failures**, zero errors/skips: the official Windows protoc turns Unicode
argv paths into `??`. Linux/macOS pass that head. The successful MSVC development
image had hidden this native-tool compatibility gap. The correction retains
Unicode fixtures and uses protoc's native UTF-8 `@file`
parser, one literal argument per line in fresh owned scratch, with CR/LF/NUL
injection and file replacement refused. It changes no compiler image, locale,
host settings, tool pin, source mapping or process containment. Strengthened
emoji fixtures pass all **15 tests** locally with both the verified MSVC image
(**27.401 seconds**) and official Windows release (**28.473 seconds**), with
zero failures/errors/skips. The official image still has the separately recorded
MXC stripped-relocation limitation; ordinary-token CLI success does not settle
that future worker selection.

[Final native CI run 37264673858](https://github.com/Kuaiwa-Network/farm-hive/actions/runs/37264673858)
checks the exact final head. Windows/Linux/macOS each pass **15 tests** without
failures/errors/skips in **65.748 /
36.044 / 13.295 seconds**.
Each also runs full native CLI reproduction and preserves all **4,662 files**
with the same canonical source SHA-256 as the local exact export. All three use
Python 3.13.16, Go 1.25.1, protoc 35.1 and protobuf plugin 1.36.8. Hosted Windows
reports ANSI code page **1252** and Git **2.55.0.windows.5**; Linux/macOS Git is
**2.55.0**. No native job
or step is skipped. The workflow exercises this Python/process boundary without
repeating Go package unit tests; PRs are scoped to its surface, while mainline
pushes run fully. [Existing backend CI run 37264673845](https://github.com/Kuaiwa-Network/farm-hive/actions/runs/37264673845)
also succeeds in both `build` and `windows-devctl`, including the race suite,
snapshot/reproducibility, contract provenance, registry and remaining gates.
Its synthetic PR merge `669682cb8ab5caf98db4d76093fe3f5faa573b2e` has parents
`e24b6cb` / `1f892e8` and the same full tree as the candidate. Both exact checkout
logs are verified; none of their workflow steps is skipped. Thus all **five
applicable CI jobs** pass. The existing Go build log retains **11 conditional
skip events** (distinct from the new native suite's zero skips):

- `TestRunInZoneFailurePropagationProbe`
- `TestCorruptZSetScoreNeverSpreads/坏分数=nan`
- `TestAdaptFuncItemsHandlesClearCoin`
- `TestGuildClusterDefaultsToSingleProcessAfterBoot`
- `TestGuildHostWiring`
- `TestFriendClusterChild`
- `TestMachineTZCalendarProbeChild`
- `TestMachineTZProbeChild`
- `TestPayWakeClusterChild`
- `TestE2ERealGrantOnlyDishExists`
- `TestRealRankConfigE2ELifecycleChild`

These unchanged Go skips are recorded without claiming their skipped cases
passed. Existing CI's own private dependency/provenance fetch remains its
separate established workflow; the local and new native checks use no such
credentials or private downloads.

Private UTF-8 logs, before/after file hashes and summaries are retained in
`native-backend-registry-e94bc4d4`, `native-backend-message-primitives-b2172e10` and `native-local-msg-candidate-99df9ded`;
failed primitive/fixture/initial CI evidence is retained too. Candidate test-log
SHA-256 is `df59947a67c06d12f9a04f03fe64a62a6bcf0871c6a79da7872fd1a5f50f3036`;
CLI log SHA-256 is `10f6108163cea933d6c62c8c117705c086d3dd7278b279985b59abb4b39ca246`;
summary SHA-256 is `7143e6e6a9d8e6c6d3687252a0add8f9a38aec5aa66da3dc1a9b7275c2f9616e`.
Canonical full-source SHA-256 is `6ba485779a619f965ea275786f3afa09a565a9b44ffe113b6aa8f5316d1c2d3a`;
the unchanged 58-file output-set SHA-256 is `3947c727f1a5a6f5f766f86336282b3bb6963edfa8b055932a57229a550d509f`.
The 59-registry output-set SHA-256 is `2f29c9598a0758b1d69712ea36c55bdec4b263da2be7f09d6ee81671b1d3e9ae`.
The selected MSVC protoc image remains
`71b837c0c7e9a5ac1a833150f2ef4c9d97d456c5faabc0da66388ea1d204fb5a`; the official Windows image is
`c77b7f5125113306ecde9b328e72466e5ca805a3974dbf10b9df91a35781e89c`.
Evidence hashes, exact source, links, whitespace and privacy are checked.

Documentation checks pass all **73 relevant tests** in **0.449 seconds**
(0.557 including startup), without failures/errors/skips. UTF-8
documentation-test log SHA-256 is `a2217e7b248d4d3bebb6403aa1104c631e5dc6c35b3adcf1b2be9ea905da25d9`.

**Next:** merge the code candidate after applicable CI and accept its exact
merged source, then implement the native backend offline message snapshot/
reproducibility gate. Full contract synchronization and declared-line provenance,
registry/intra-cluster gates, designer/config generation and gates, complete
producer/publication, actual worker grants/runtime/credentials/real Feishu,
ordinary-token symlink/desktop Unity, private secret scan, release provenance
and scoped TestBot restoration remain pending. Client-stage acceptance still
precedes UI authoring. Local checks access no production config/ledger or
credentials and invoke no Bash/MSYS/WSL. No service/account/app setting,
deployment, live issue mutation or parked-job action occurs. Test drafts remain unmerged. Code merge
and exact merged-source acceptance are pending; this record proves development
CLI behavior on this PC and hosted runners.

## Next verification step

The native offline baseline is complete for the exact candidate on this host
with an elevated test token. The ordinary host token still lacks directory
symlink privilege; this result does not certify that token or the running
service account's capabilities.

Windows lark-cli protects registry credentials with per-user DPAPI. A separate
`HOME` does not isolate that store, and FarmBot rejects `lark_cli.home` on
Windows. The supported store-based approach uses a Windows account whose
lark-cli store contains only the FarmBot bot profile; a dedicated service
account provides separate-account isolation. The operator chose the current
account for this host, with its store restricted to FarmBot. Local profile setup
and strict bot mode now pass; actual worker credential validation remains
unresolved, with detailed diagnostics retained privately. The
feature-only environment-credential variant merged in #81 as `e8406d5`; the
original baseline candidate `707ea87` still withholds credential variables from
every worker. The merged variant still needs actual Windows worker acceptance
before live use. It installs no credential loader and copies no DPAPI profile
into worker accounts.

## Native Windows backend offline message gate (2026-10-05)

**Merged generator acceptance:** the operator merged farm-hive
[#354](https://github.com/Kuaiwa-Network/farm-hive/pull/354) as
**`1702356b02464ff5fa1cd6047d21e62190284845`**. Its entire tree
`cfaf8e8f4cc7525e2cfc09ded91e5f2571f0e850` equals reviewed
`1f892e84b85bed2c5cb2022f00cb590e4218e3ef`; no merge-only source change remains.
An exact merged archive passes all **15 actual native Windows tests** in
**40.287 seconds**, zero failures/errors/skips. Full
native local generation takes **3.143 seconds**, preserving all **4,662 source
files** and all **58 outputs**; the full sequence takes **78.454
seconds**. Merged [native CI](https://github.com/Kuaiwa-Network/farm-hive/actions/runs/37271192004)
passes all 15 tests on Windows/Linux/macOS in **49.977 / 30.190 / 12.437 seconds**,
with no failed or skipped native steps and the same canonical source hashes.
Merged [existing CI](https://github.com/Kuaiwa-Network/farm-hive/actions/runs/37271192014)
passes both build and Windows devctl jobs on this exact merged SHA. Its 11
existing conditional Go skip events remain visible:

- `TestRunInZoneFailurePropagationProbe`
- `TestCorruptZSetScoreNeverSpreads/坏分数=nan`
- `TestAdaptFuncItemsHandlesClearCoin`
- `TestGuildClusterDefaultsToSingleProcessAfterBoot`
- `TestGuildHostWiring`
- `TestFriendClusterChild`
- `TestMachineTZCalendarProbeChild`
- `TestMachineTZProbeChild`
- `TestPayWakeClusterChild`
- `TestE2ERealGrantOnlyDishExists`
- `TestRealRankConfigE2ELifecycleChild`

**Next code candidate:** farm-hive
[#355](https://github.com/Kuaiwa-Network/farm-hive/pull/355) at
**`29fbc9807ed8d6a0702ed9e75b605ae00d84531e`**, tree
`5b3d3561b6ba1ba5ed4a4d5b692463978f573c16`, adds the native offline
`ci/check_msg_proto.py` gate and 17 behavioral regression tests, and extends the
existing scoped native workflow/README. Exactly four files change. The old
POSIX gates/generators, tool pins, source snapshots, manifest, generated files
and owned process helper retain their bytes.

The gate independently checks one canonical lowercase 40-digit provenance
header, canonical SHA-256 rows and safe relative paths, manifest versus
Git-tracked snapshot coverage and generator TARGETS, ordinary files and actual
snapshot/output sets including untracked or ignored extras. A missing TARGETS
row cannot hide a tracked snapshot. It rejects staged changes without changing
the index, verifies selected snapshots against HEAD, and invokes the existing
native generator. Unstaged output drift can still be repaired by regeneration;
generated output bytes must then equal committed HEAD blobs. Git runs only
read-only builtins with hooks/fsmonitor, inherited config/auth, lazy fetching
and replace refs disabled; it avoids status/diff and their clean filters.
All children use the unchanged owned native process runner. Provenance **format**
does not establish upstream content or branch reachability: that separate gate
and full synchronization remain pending for native Windows.

**Measured local acceptance:** this is the production Windows PC, using the
ordinary current-user token in a separate development checkout and fresh owned
scratch, with no production configuration/ledger reads. Python **3.13.16**
starts with `PYTHONUTF8=1`; selected Git is **2.54.0.windows.1**, Git LFS
**3.7.1**, Go **1.25.1**, protoc **35.1**, protobuf plugin **1.36.8**. Existing
prepared caches are used with downloads, inherited FarmBot selectors, Go
environment/workspace configuration and authentication disabled. No Bash, WSL,
service start, host/app/account change, live issue action or deployment occurs.
Ordinary native CLI acceptance does not certify an actual FarmBot worker.

The natural TDD red runs one real gate test in **3.559 seconds**, failing because
the not-yet-implemented native entry point is absent; there are no missing
tool/capability errors. Its retained UTF-8 log SHA-256 is
`4b43cf8df071a31676ebdb4af5979242b5d12e91d156304307877d43156c4bd9`.
The working-tree green runs all 17 gate tests in **72.156 seconds**, with zero
failures/errors/skips. Exact candidate archive acceptance then passes the
unchanged **15 generator tests** in **27.570
seconds** and **17 gate tests** in **70.735
seconds**, zero failures/errors/skips. The fresh Git baseline is created locally
over the canonical exact archive, with no remotes; Git metadata is excluded
from the source hash. This is an offline comparison baseline, not release
provenance. Tests invoke the selected interpreter with
`-I -X utf8 -B tools/test_gen_msg_protos.py` and
`-I -X utf8 -B tools/test_check_msg_proto.py`. Actual full gate invocation from
an unrelated directory uses
`-I -X utf8 -B ci/check_msg_proto.py --git GIT_EXE --go GO_EXE --protoc PROTOC_EXE --gomodcache MODULE_CACHE --gocache BUILD_CACHE`.
It takes **9.792 seconds**, preserving all
**4,664 canonical files** and all **58 outputs**. Total export/Git-baseline/test/
full-gate/check duration is **176.145 seconds**. Actual Unicode/emoji
checkout paths, an owned directory junction, poisoned Git/FarmBot selectors,
snapshot comments/hash/path/provenance failures, coverage omission, ignored
extras, output repair, committed-output drift and staged index retention are
covered; no containment/ownership check is weakened and no new skip is added.

[Candidate native CI](https://github.com/Kuaiwa-Network/farm-hive/actions/runs/37273464722)
passes on exact `29fbc9807ed8d6a0702ed9e75b605ae00d84531e` on all three
platforms. Windows/Linux/macOS
pass the **15 generator tests** in **68.191 /
35.223 / 11.975
seconds** and the **17 gate tests** in **96.038 /
9.382 / 14.103 seconds**,
without failures/errors/skips. Each real full gate preserves all 4,664 files
with the same canonical source hash as local acceptance. All native jobs and
steps actually run. The existing scoped workflow adds the new gate/test paths
and does not repeat Go package tests. [Existing backend CI](https://github.com/Kuaiwa-Network/farm-hive/actions/runs/37273464730)
also passes both build and Windows devctl jobs, with every configured step
running successfully. It checks PR merge ref `776f2ed4e424e064329a991f466dce6412b16ab6`, whose
parents are merged #354 and this candidate; its entire tree equals the reviewed
candidate tree. The same 11 existing conditional Go skip events listed above
are retained; they are separate from the 32 native tests, which have no skips.
Both independent CI runs are green; no test/provenance check is bypassed.

Private UTF-8 evidence retains reports `native-local-msg-candidate-00956c28`
and `native-msg-gate-candidate-de0b045e`,
exact before/after source maps, commands, versions, durations and log hashes;
private paths and raw host logs are not committed. Digests:

- Merged #354 canonical source: `6ba485779a619f965ea275786f3afa09a565a9b44ffe113b6aa8f5316d1c2d3a`.
- Merged #354 regression log: `af4f882f27e600eddd4832a8d4deaf1890e770bf24c380bcdcc882a3c010cd3f`.
- Candidate canonical source: `c26cf21816d42d64d994247f64c75c7c7009e0c0a2e19095ecb75b89899cce82`.
- Unchanged 58 generated outputs: `3947c727f1a5a6f5f766f86336282b3bb6963edfa8b055932a57229a550d509f`.
- Native gate source: `4658eb1cb0b61ef551a10999871baaff5bfdea473ea492072d7848077631412a`.
- Unchanged owned runner: `0385e5477aa664178c9732ac40e2a8033857dc22cbb5bc73672cf8f19f6b8ebd`.
- Exact candidate generator/gate test logs: `8cc95dff942ee62ed280e003436e2ca082d1274d4c922d6cd10aeccf5d4a6897` / `0c0652b0ca86431487f5616b496041e3c58e49817db64013af968424a69c9b99`.
- Full gate log: `a95035392879455af9655fcd9090adc796554cfa83470db383299ba15db8bcdd`.
- Merged/candidate summary files: `11917822a0aa8671315d4439678a12146ac6a533e709b3e2171d4b52dfc74505` / `6fa0bdd9815af22dc18b7fd1ef044925a31703850a0a90b378aac4fe0adce6c0`.

Documentation checks pass all **73 relevant tests** in **0.654
seconds** (0.784 including startup), zero failures/errors/skips;
UTF-8 test log SHA-256 is `cf73fc40eaf0619b1d899882665cc40b6518de885fee4fd1a19b4125524ffc69`. Links, retained evidence hashes,
public-record privacy and whitespace are checked separately.

**Next:** merge the reviewed code candidate after applicable CI, verify its
exact merged revision, then provide the native backend contract-provenance/
full-synchronization and registry gate equivalents. Designer/config generation,
complete producer/publication, actual worker/tool/cache selection, real Feishu/
Word reads, Unity desktop/symlink acceptance and separately authorized release
remain prerequisites. FARM-1346/FARM-1425 stay parked; unmerged test drafts do
not establish release provenance.

## Native Windows backend local Contract provenance (2026-10-05)

**Merged offline-gate acceptance:** farm-hive
[#355](https://github.com/Kuaiwa-Network/farm-hive/pull/355) is merged as
**`fef7d64678ec53ee9a06a2ad749167e8a02b3576`**, with full tree
`5b3d3561b6ba1ba5ed4a4d5b692463978f573c16` equal to reviewed
`29fbc9807ed8d6a0702ed9e75b605ae00d84531e`. Exact merged native Windows acceptance
passes all **15 generator tests** in **28.150
seconds** and **17 offline-gate tests** in **72.074
seconds**, zero failures/errors/skips. The real gate takes
**9.785 seconds**, preserving all **4,664
canonical files** and all **58 outputs**; the full sequence is
**175.176 seconds**. Merged
[native CI](https://github.com/Kuaiwa-Network/farm-hive/actions/runs/37278094098)
passes both suites on Windows/Linux/macOS in **61.818 + 90.789 /
33.147 + 7.696 / 15.144 + 19.906 seconds** respectively, and the real full gate
preserves the same source hash. Merged
[existing CI](https://github.com/Kuaiwa-Network/farm-hive/actions/runs/37278094159)
passes both jobs on this exact SHA. All five applicable jobs and every native
step pass; the same 11 conditional Go skips recorded in the
[offline-gate record](#native-windows-backend-offline-message-gate-2026-10-05)
remain distinct from the zero-skip native suites.

**New code candidate:** farm-hive
[#356](https://github.com/Kuaiwa-Network/farm-hive/pull/356) at
**`4afc0aad778c0ab5bf3c4477cdb8e05f608ab711`**, tree
`72f40ab6c517097db21287a96a8856ad09b4ff27`, adds
`ci/check_contract_sync.py`, its 20 actual behavioral tests, scoped native CI
and README. Exactly four files change. Old POSIX entry points, module/tool
pins, protocol snapshots, manifests, generated artifacts and the owned process
helper retain their bytes. The existing API path remains POSIX; the new native
path explicitly selects a local Contract checkout and native Git, supports
ordinary clones and Git worktrees, and uses only committed upstream objects.

It validates the provenance header independently of the offline gate, requires
the declared branch without fallback, and requires the pin to name a commit
which is an ancestor of local `origin/<declared branch>`. It checks the pinned
Contract manifest's canonical rows, complete proto file set and every content
hash, then compares all tracked backend snapshots by unique basename. Missing
evidence, aliases, nonordinary Git modes, unknown/unmerged commits, duplicate
basenames and byte drift fail. Unconsumed upstream proto is reported without
failure; unrelated dirty Contract working-copy bytes do not replace committed
objects. The gate prints the observed local ref and warns that it may be stale;
fetching happens outside the gate. It uses no credentials or network, writes
neither repository, and strips inherited FarmBot/Git/authentication selectors.
Hooks/fsmonitor, lazy fetching and replace refs are disabled; all child
processes retain the unchanged owned Job/session runner. No containment or
ownership check is weakened and no skip is added.

**Measured preparation and acceptance:** the production Windows PC uses the
ordinary current-user token in separate development checkouts and fresh owned
scratch. Python **3.13.16** starts with `PYTHONUTF8=1`; selected Git is
**2.54.0.windows.1**, Git LFS **3.7.1**. This gate needs no Go/protoc or module
download. An anonymous preparation attempt fails because Farm-Contract is
private (credential prompts are disabled). A separate development clone is
then prepared using the existing GitHub login, with no credential/profile
installation or app/account setting change. Actual acceptance runs with
authentication/network disabled and reads no production configuration/ledger.
No service, live issue, Feishu access, parked job or deployment is touched.
Ordinary CLI checks do not certify an actual FarmBot worker.

The source pin remains **`5d774fa32c922f6927e01faaa8783c8b88b9f08b`**, on observed local
`origin/main` **`71dadaed8d111219e7170ab6712d97bbb28d8925`**, which matches freshly observed GitHub
main and Contract #326. The gate compares the historical pin rather than
requiring latest HEAD bytes; later docs/tool changes on main do not invalidate
the pin. Natural TDD red runs one real test in **0.745 seconds**, failing on
the absent not-yet-implemented gate; log SHA-256 is
`dff2511a8da2965f591323beef20ae924104a1259cfbd2243beeca23951bb74c`.
Working-tree green passes **20 tests in 29.172 seconds**, zero failures/errors/
skips. Exact candidate archive acceptance passes all **20 tests** in
**30.106 seconds**. A fresh local Git baseline is
created over canonical candidate archive bytes, without remotes; it is a
comparison baseline, not release provenance. Commands use selected Python with
`-I -X utf8 -B tools/test_check_contract_sync.py` and, from an unrelated cwd,
`-I -X utf8 -B ci/check_contract_sync.py --git GIT_EXE --contract CONTRACT_CHECKOUT`.
The actual full gate matches **58 snapshots** in
**2.526 seconds**, preserving all
**4,667 backend files** and **608 Contract files**, plus Contract index/config/
HEAD/packed refs and the observed branch ref. Full exact archive/baseline/test/
gate/check duration is **99.834 seconds**. Cases cover real Unicode/
emoji roots, an actual junction, Git worktree, main/nonmain lines, ancestor vs
unmerged/unknown/noncommit pins, missing/bad manifests and complete sets,
heartbeat and every snapshot path family, self-consistent backend edits that
still violate upstream bytes, poisoned selectors and read-only preservation.

[Native candidate CI](https://github.com/Kuaiwa-Network/farm-hive/actions/runs/37279905521)
passes on exact candidate SHA on Windows/Linux/macOS: **20 tests** each in
**42.762 / 4.957 /
7.862 seconds**, zero failures/errors/skips. Each
also performs the actual 58-snapshot check and preserves the same backend and
Contract source hashes as local acceptance. Every native job/step actually
runs. CI prepares full history for the declared private Contract line with
the existing read secret and disables credential persistence; the check then
sanitizes inherited authentication. PR scope is confined to provenance inputs/
tooling and main/banshu pushes run fully. No Go package tests or previous native
generator/offline suites are repeated in this workflow.
[Existing candidate CI](https://github.com/Kuaiwa-Network/farm-hive/actions/runs/37279905445)
passes both build and Windows devctl jobs, with every configured step running
successfully. Its PR merge `d5334639f03964ffa536d439b857ce1cf6d8f75c` has merged #355 and this
candidate as parents; its full tree equals the reviewed candidate tree. The
same 11 existing conditional Go skips remain recorded in the offline-gate
section linked above, separate from the zero-skip native suite. All five
applicable candidate jobs pass; no provenance/test check is bypassed.

Private UTF-8 evidence retains reports `native-msg-gate-candidate-881596e7`
and `native-contract-sync-candidate-92d6ad42`,
source maps, commands, versions, durations, preparation failure and log hashes.
Private paths, credential values and raw host logs are not committed. Digests:

- Merged #355 canonical source: `c26cf21816d42d64d994247f64c75c7c7009e0c0a2e19095ecb75b89899cce82`.
- Candidate backend/Contract sources: `ac2dc747ca9dc8ea7fca56091827880a9192fdfe765c9a2d2a6dd9c812cf40c7` / `e99dd3ccdf7ca58440a377cd7cdae84b5f2ce93163efad0c83885836dc007fd2`.
- Native provenance gate: `7a3b80f00a645a0ded83f8eb5d5b50a8ec8177c725febbe3c9246c625f4e31b2`.
- Unchanged owned runner: `0385e5477aa664178c9732ac40e2a8033857dc22cbb5bc73672cf8f19f6b8ebd`.
- Exact candidate test log: `f23f9c17d23b350f9f2bd72d4dffbbab0d2dfed9d7e3d6590311e77e30141f2e`.
- Actual provenance log: `e3c85093cd08dacc8bacb89beff9334c02c21abc84e693f88b4f3b904e4185ef`.
- Merged/candidate summaries: `9ec71a54ad698cdf235f8a56bcd871862ce3a0da7874c3f2dcbd1399750d2314` / `35bda328c8322f9edeaff26a77e8b08c5ba167c271aa45428cbb68f20f11fbc0`.

Documentation checks pass all **73 relevant tests** in **0.414
seconds** (0.522 including startup), without failures/errors/skips.
UTF-8 documentation-test log SHA-256 is `88e4f8cb171e46e3268121549fa8ae5b44622c1cc798c7c6b01192ea13b62852`. Retained hashes,
links, public-record privacy and whitespace are checked separately.

**Next:** merge the reviewed code candidate after applicable CI and verify its
exact merged revision, then finish native full snapshot synchronization and
the registry gate. The native local checkout path is the measured candidate;
the API variant remains POSIX and is not needed to run this local path.
Designer/config tooling, complete producer/publication, actual Windows
worker/tool/cache and Feishu/Word access,
Unity desktop/symlink checks and separately authorized release remain pending.
FARM-1346/FARM-1425 remain parked and their test drafts remain unmerged.

Remaining release prerequisites:

1. Use native Windows entry points and tools: configured Python 3.13.16, Git
   and Git LFS, Go respecting each repository's pin, protoc 35.1, buf 1.72.0,
   Node/openspec 1.7.0 and lark-cli. The global `python3` alias is 3.14.3 and
   must not select the verifier's interpreter. Common's native CMD generation
   now passes at the development candidates above; #148, cleanup fix #149
   and native CMD preflight fix #150 are merged. Parent-policy fixture correction
   #151 and executable-fixture correction #152 are merged. Newline-fixture
   correction #153 and scanner correction #154 are merged. The scanner's three
   boundary packages pass with all existing skips unchanged. Git-executable
   fixture correction #155 and top-level-output fixture correction #156 are
   merged, as is native Git timeout fixture correction #157; all 17 Git-state
   tests pass. Archive owned-cleanup fix #158 and publication identity fixture
   correction #159 are merged; all eight native publication cases pass.
   Workbook mode fixture correction #160 is merged; all 15 Excel XML tests
   pass without skips. The same-account elevated recheck passed all five
   symlink cases and exposed one no-follow classification regression.
   Common #161 is merged; its committed-source full native module has 538 passes,
   zero failures and 25 top-level skips (35 skip events). The ordinary worker
   token is not certified by that elevated test. On exact merged #161, the
   separate ordinary-token sequence passes both opt-in production language
   fixtures without skips and compiles the full artifact's 103 generated C#
   files with zero warnings/errors using private .NET 8.0.423. Complete
   producer/publication and actual worker tool-selection acceptance remain pending.
   Preserve the distinction between host capabilities, fixture assumptions
   and application regressions during the focused rechecks.
   .NET SDK 8.0.423 is checksum-verified in the private development cache;
   locked native C# compilation now passes. Its production worker selection
   and access remain untested; the host's global SDK is still 9.0.306.
   Keep Bash for Mac/Linux testing. The current doctor's legacy Windows Bash
   inventory is not proof that equivalent native generators/gates exist;
   update its requirements together with the verified native workflow.
2. Local FarmBot-only profile setup is complete under the operator-selected
   current Windows account. Resolve worker credential compatibility while
   preserving containment, then verify the isolated worker's actual native
   mode and credential path. Complete strict bot mode, bot
   dry runs and real planning-document/attachment reads, including Windows Word
   conversion. The operator has requested these prerequisites one at a time;
   the authorized development implementation is now merged in #81. Complete
   a supported isolated-home native launch integration: elevated initialization
   is blocked by the registered-runtime ownership incompatibility. The MXC
   MSYS finding is separate experimental evidence, not a release prerequisite
   for FarmBot's configured elevated backend; the independent PowerShell
   scratch-write comparison passed;
   merge-head CI has passed. The ordinary-token native helper now verifies the controller dummy grant
   and Job Object descendant settlement, but actual Codex sandbox-child grant
   and containment remain pending before privately supplying the selected controller
   source and verifying a real isolated worker. Real Feishu
   access remains untested here; local profile setup performed no network
   authentication.
3. Provide native Windows equivalents for the remaining Farm-Contract and
   farm-hive generators/gates, then run them in a real Windows worker: preserve
   canonical hashes, exact output sets, contract reachability and dirty-source
   rejection, read-only siblings, Go caches, Git LFS and containment. Farm-hive's
   existing `config/pb/gen.bat` still calls Bash and is not such an equivalent.
   Direct common generation does not verify these paths. Use an operator-selected
   scope without resuming parked jobs. Farm-Contract #319 is merged at `332b22c`;
   its exact merged source passes 19 local Windows tests and four CI jobs.
   Gate 5 is now merged in #320 as `8c7e591`, with 23 exact-merged-source Windows
   tests and the real marker check passing; all seven merge-head CI jobs succeed.
   Gate 6 is merged in #321 as `831c1e1`; all 34 exact-merged-source Windows
   tests and the real 237-Ack check pass, preserving all 598 exported files;
   all 10 merge-head CI jobs succeed. Gate 7 is merged in #322 as `396cf6d`;
   all 55 exact-merged-source Windows tests and the real field gate pass,
   preserving all 600 exported files; all 13 merge-head CI jobs succeed.
   Gate 9 is merged in #323 as `ea30dd9`; all 39 exact-merged-source Windows
   tests and the real 34-spec provenance check pass, preserving all 602 exported
   files; all 16 merge-head CI jobs succeed. Gate 10 is merged in #324 as
   `5cf4c7e`; all 35 exact-merged-source Windows tests and the real 60-file/60-row
   inventory check pass, preserving all 604 exported files; all 19 merge-head
   CI jobs succeed. Gate 12 is merged in #325 as `c6fd159`; all 49
   exact-merged-source Windows tests and real 50-change checks with explicit/PATH
   runtime selection pass, preserving all 606 exported files; all 22 merge-head
   CI jobs succeed. Gate 3 is merged in #326 as `71dadae`; all 55 exact-merged
   Windows tests, the real 60-proto zero-waiver check and native PATH discovery
   pass without skips, preserving all 608 exported files; all 25 merged push
   CI jobs succeed. The reviewed candidate retains 11 actual breaking fixtures
   and four baseline cases. All Contract wrapper ports now have native merged
   entry-point evidence. Backend main `e24b6cb` now reproduces 59 native Go
   registry files and 58 message outputs without drift. Farm-hive #354 is
   merged as `1702356`; all 15 exact-merged Windows tests and all five merged
   CI jobs pass, preserving all 4,662 files. #355 is merged as `fef7d64`;
   exact merged acceptance passes 15 generator and 17 offline-gate tests
   without skips and all five merged CI jobs, preserving all 4,664 files and
   58 outputs. Candidate #356 at `4afc0aa` adds native local Contract
   provenance: all 20 exact-source Windows tests and three native CI jobs pass,
   matching all 58 snapshots and preserving 4,667 backend / 608 Contract files.
   Code merge and exact merged provenance acceptance remain required. Full
   synchronization, registry gate, designer/config tooling
   and complete producer/publication remain pending.
4. Check the selected service account's symlink capability and relevant native
   ownership/process checks, then complete Windows desktop Unity acceptance.
   The elevated offline baseline does not certify the ordinary token or a
   future service account.
5. Finish the operator's private output/log/comment/PR secret scan and scoped
   TestBot restoration, preserving current ledger/history and recovery evidence.
   Require approved release provenance and applicable CI; FARM-1425's unmerged
   contract test pin currently fails backend provenance and is not release
   evidence. FARM-1346 and FARM-1425 stay parked, and their test drafts stay
   unmerged.
6. Review the completed evidence before separately authorizing production
   feature enablement or deployment. Neither is authorized or performed by this
   read-only check.
