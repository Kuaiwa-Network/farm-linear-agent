# Native Windows offline verification, 2026-10-03

The exact merged candidate `707ea87ce0e6819dff873d0928e81d6220c01e02`
was tested once in an isolated development worktree outside the running
installation on the **production Windows host**. A read-only query observed
the `FarmBot-Receiver` scheduled task as Running; neither its configuration nor
its runtime state was read or copied. No Mac runtime state was copied.

This run does **not** establish production readiness: the required directory
symlink capability is missing from the test token. No application change or new
skip was made to obtain a pass. The running service, credentials, live issues,
parked jobs and production resources were not changed.

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
  sandbox; the test token was not elevated and had no symlink privilege.

The selected interpreter executed this command from the repository root:

```powershell
python -B -m unittest discover -s tests -v
```

Directory symlink preflight failed with `OSError`, Windows error 1314, both
inside and outside the sandbox. The Developer Mode flag was absent. This fails
[CI's dependency gate](../../ci.md); the operator-requested full run proceeded
as diagnostic evidence with every test unchanged. Windows settings were not
modified.

## Measured result

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

## Errors reproduced in focused testing

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

## Every platform and capability skip

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

## Next verification step

Provide symlink capability to the isolated test process, then verify directory
symlink creation and rerun the six errors and five capability-skipped cases.
A successful full offline baseline on the exact candidate remains pending;
the diagnostic run above is not a passing release gate. Enabling Developer
Mode or changing account privileges is an operator action outside this run.

Windows feature-toolchain doctor, generators/gates in a real worker sandbox,
lark-cli store selection and bot fetching, desktop Unity acceptance, and any
production deployment remain separately scoped work. FARM-1346 and FARM-1425
stay parked; their unmerged test drafts are unaffected.
