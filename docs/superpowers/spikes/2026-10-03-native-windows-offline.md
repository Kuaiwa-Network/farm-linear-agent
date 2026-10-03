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

## Bot profile setup and elevated sandbox probe, 2026-10-03

The operator entered the existing bot app's ID and secret in a local hidden
prompt under the chosen current Windows account. Setup started at
`2026-10-03T07:49:14.5713834Z` and finished at
`2026-10-03T07:49:59.4310713Z`. The resulting store contains only `farmbot`, no
other profiles or user logins, and profile strict bot mode. The local offline
doctor reported `config_file`, `app_resolved`, `bot_identity` and
`identity_ready` passed, `user_identity` warned for the absent user login, and
both endpoints were skipped. No network authentication or Feishu app-scope
change was performed. The initial interactive helper launch had inherited
PowerShell 7's module path into Windows PowerShell 5.1, hiding `Get-FileHash`;
selecting the helper engine's built-in modules fixed it. The original launched
preflight then passed both unchanged hash checks. No account was created.

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

The installed Codex commands differ: FarmBot's PATH selection is the 0.156.1
command wrapper, while the separately selected native executable is 0.160.0.
An initial native-executable doctor-only probe was superseded for credential
access purposes by the PATH-selected 0.156.1 probe below. No CLI was upgraded.
The current Codex config uses its existing elevated native Windows sandbox;
the probe used `:workspace`, included managed requirements and left network
access disabled. It used fresh scratch files and the exact candidate's
`child_environment` withholding, with no model run, service invocation,
production config/ledger read or credential variables supplied.

| Check | Ordinary setup account | Existing elevated Codex sandbox |
| --- | --- | --- |
| Probe duration | 0.227 seconds | 0.847 seconds |
| Token user matches setup account | yes | no |
| Profile strict bot mode | passes | passes |
| lark `doctor --offline` | exit 0, bot identity passes | exit 0, bot identity passes |
| Bot-only registry entry found | yes | no; WinError 2 |
| DPAPI decrypt yields nonempty secret | yes | unavailable; no registry entry |

The comparison began at `2026-10-03T08:01:37.992892+00:00`; its sandbox probe
began at `2026-10-03T08:01:38.220689+00:00`. The read-only DPAPI probe followed
the pinned CLI's registry/value/entropy format, checked only the new FarmBot
profile and emitted booleans/error codes. Decrypted bytes were zeroed without
being copied, decoded, hashed, printed, saved or exported. Sanitized evidence
is retained under ignored `reports/native-windows-lark-sandbox-20261003-080137/`;
the host and sandbox output hashes are respectively
`7ec19a4dd3cda6d1c9dd75d0cf0bb4aac18259dbba81a6e1a6cd0600942b9ce2` and
`2730a7bd64393f27a16ead5ee1e0f2475f05711a30b591c6189ddfb100244718`.

An offline doctor pass is insufficient evidence of secret access here. The
pinned CLI's [Windows keychain backend](https://github.com/larksuite/cli/blob/v1.0.82/internal/keychain/keychain_windows.go)
returns an empty secret without an error when its registry value is absent.
In [strict bot mode](https://github.com/larksuite/cli/blob/v1.0.82/internal/credential/default_provider.go),
supported identity flags are set; the [offline bot diagnostic](https://github.com/larksuite/cli/blob/v1.0.82/internal/identitydiag/diagnostics.go)
then does not require a nonempty secret. This explains the passing doctor
alongside the missing secret. The [native sandbox documentation](https://learn.chatgpt.com/docs/windows/windows-sandbox)
describes the dedicated sandbox users used by elevated mode.

This measures the existing elevated sandbox helper, **not** a launched FarmBot
model worker or its isolated `CODEX_HOME`. Their native sandbox selection and
end-to-end credential path still need verification. The observed elevated path
cannot use the setup account's per-user DPAPI store as-is. Changing to another
FarmBot host account would still require checking the sandbox user's access;
the operator's current-account choice remains in place. No sandbox downgrade,
credential copy to a sandbox account or feature-only environment variant was
used to obtain a pass. That variant remains unimplemented and is a separate
development/authority decision before any live worker use. Real Feishu reads,
Windows generators and full worker acceptance remain pending.

Validation of this documentation passed all **73** skill/reference tests under
the ordinary owner token in **0.347 seconds**, with no skips. An earlier run
inside the desktop sandbox recorded 33 temporary-fixture access errors
(WinError 5); its log is preserved, and the same tests passed outside that
restriction without code changes or added skips. Link/whitespace/privacy and
measured-evidence checks passed; the full native suite was not rerun for this
documentation-only update.

The preparation record passed all 73 skill/reference checks in 0.369 seconds,
with no skips. Its links, whitespace, privacy scan, measured report hash and
artifact/version evidence were checked; application code and the existing
native-suite inventory remain unchanged.

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
account for this host, with its store restricted to FarmBot. Local profile setup,
strict bot mode and host DPAPI access now pass. The existing elevated sandbox
cannot read its registry secret; isolated worker/store checks remain pending. The
feature-only environment-credential variant is **not implemented**: the
current code withholds credential variables from every worker. That variant
would need a separate implementation and authority review, not a config edit.

Remaining release prerequisites:

1. The development prefix now passes required executable probes. Prepare and
   verify the selected Windows account's toolchain: Go at least 1.25.1 (then
   check the actual farm-hive directive), protoc 35.1, buf 1.72.0, openspec 1.7.0,
   lark-cli and native Git for Windows bash before WSL. Verify `python3`,
   coreutils and awk in the eventual worker environment; the current `python3`
   global alias is 3.14.3 while the prepared prefix selects Python 3.13.16. Dotnet
   SDK 8.0.423 is an optional later config-artifact requirement.
2. Local FarmBot-only profile setup is complete under the operator-selected
   current Windows account. Resolve the measured elevated-sandbox credential
   gap while preserving containment, then verify the isolated worker's actual
   native mode and DPAPI/credential path. Complete strict bot mode, bot
   dry runs and real planning-document/attachment reads, including Windows Word
   conversion. The operator has requested these prerequisites one at a time;
   the next credential-delivery development choice is pending. Real Feishu
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
