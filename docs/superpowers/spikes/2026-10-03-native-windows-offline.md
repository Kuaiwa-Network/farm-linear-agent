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

Remaining release prerequisites:

1. The development prefix now passes required executable probes. Prepare and
   verify the selected Windows account's toolchain: Go at least 1.25.1 (then
   check the actual farm-hive directive), protoc 35.1, buf 1.72.0, openspec 1.7.0,
   lark-cli and native Git for Windows bash before WSL. Verify `python3`,
   coreutils and awk in the eventual worker environment; the current `python3`
   global alias is 3.14.3 while the prepared prefix selects Python 3.13.16. Dotnet
   SDK 8.0.423 is an optional later config-artifact requirement.
   The separate relocatable protoc 35.1 build now passes the MXC synthetic
   generation fixture; native MSYS startup is blocked by an object-directory
   access denial inside the MXC evaluation, including the latest tested portable
   Git. Neither
   direct version probes nor this fixture certify the repository generators.
2. Local FarmBot-only profile setup is complete under the operator-selected
   current Windows account. Resolve worker credential compatibility while
   preserving containment, then verify the isolated worker's actual native
   mode and credential path. Complete strict bot mode, bot
   dry runs and real planning-document/attachment reads, including Windows Word
   conversion. The operator has requested these prerequisites one at a time;
   the authorized development implementation is now merged in #81. Complete
   a supported isolated-home native launch integration: elevated initialization
   is blocked by the registered-runtime ownership incompatibility, while the
   MXC evaluation above still denies the MSYS shared-object directory, although the
   independent PowerShell scratch-write comparison passed;
   merge-head CI has passed. Then verify the dummy feature credential grant and
   sandbox-child containment before privately supplying the selected controller
   source and verifying a real isolated worker. Real Feishu
   access remains untested here; local profile setup performed no network
   authentication.
3. Run generators and repository gates in a real Windows worker sandbox: native
   bash/contract gates, byte-identical generated outputs, protoc with read-only
   siblings, Go builds/module caches and Git LFS. Version probes do not verify
   these paths. Use an operator-selected scope without resuming parked jobs.
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
