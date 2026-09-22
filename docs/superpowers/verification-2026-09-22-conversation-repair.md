# Conversation/repair verification

Workspace: Codex worktree `61d1/farm-linear-agent`, based on `5463f28`.
Runtime: Windows, Python 3.14, `PYTHONUTF8=1`. No production state or Linear issue was changed.

## Behavioral coverage

- Replayed FARM-1261: non-Bug delegation, clarification, `修复`, first repair queued.
- Fresh delegation/scope checks, delegation provenance, latest actual message, foreign
  message/token, expiry, Stop during Linear refresh, repeated transition, late inbox.
- Prior cancelled repair receives a recovery-linked successor; legacy resume remains scoped.
- Earlier answers, pending questions and investigation findings survive execution changes.
- Scheduler gives read-only runs no source/clone write roots and gives repairs five
  configured writable repositories, the 8-hour budget and 45-minute renewable lease.
- Durable ten-minute progress, one-minute retry, restart, waiting/resource/local states,
  and state corrections after in-flight Stop, questions and replacement work.

Independent review identified a Stop/handoff race. A real-ledger regression reproduced
it: selection of chat before handoff followed by Stop left the queued fix active. Fixed
by resolving the destination inside cancellation's transaction and signalling both ends.
The regression was observed RED then GREEN. Empty synthetic intake messages were also
removed from new conversations and refused by the repair transition.

Final focused verification after those fixes: **236 tests: 234 passed, 2 platform skips**
across routing, receiver, repair/resume, CLI, ledger, scheduler, progress and service wiring.
The pre-review full rerun had 565 tests and exactly the ten baseline failures listed below.

Final full command: `python -m unittest discover -s tests -v` with `PYTHONUTF8=1`.
Result: **567 tests, 549 passed, 5 failures, 5 errors, 8 skips** in 279.863 seconds.
The failure/error names exactly match the unmodified baseline; there are no additional
failures. The 28 added tests all passed. Logs are retained locally in
`.local/verification/conversation-repair-final.log` and
`.local/verification/conversation-repair-focused.log`. Git's staged whitespace check passed.

## Baseline failures present before implementation

The unmodified baseline ran **539 tests: 5 failures, 5 errors, 8 skips**. These were:

| Test | Existing Windows issue |
|---|---|
| `test_dispatch.DispatchTests.test_message_is_self_contained_and_carries_no_issue_prose` | POSIX `/repo` expectation vs Windows `\\repo` path |
| `test_doctor.DoctorTests.test_cli_is_json_and_does_not_change_config_permissions_or_ledger_contents` | POSIX `0640` mode assertion |
| `test_end_to_end.EndToEndTests.test_delegated_bug_runs_a_worker_that_comments_and_finishes_blocked` | Cleanup assertion expects unverified teardown, but Windows Job Object teardown is verified |
| `test_slots.PoolTests.test_a_queued_request_is_granted_switched_and_resumed_for_a_fresh_worker` | POSIX token mode assertion |
| `test_unity.UnityTests.test_the_editor_is_found_per_host_from_the_project_version` | POSIX path separator assertion for Mac editor path |
| `test_memory.MemorySnapshotTests.test_invalid_stored_ids_and_symlink_root_are_refused` | Windows error 1314: symlink privilege unavailable |
| `test_memory.MemorySnapshotTests.test_prune_preserves_references_and_ignores_unrelated_paths` | Windows error 1314: symlink privilege unavailable |
| `test_memory.MemorySnapshotTests.test_symlinked_retained_attempt_aborts_pruning_before_deletion` | Windows error 1314: symlink privilege unavailable |
| `test_memory.MemorySnapshotTests.test_symlinked_retained_item_aborts_pruning_before_deletion` | Windows error 1314: symlink privilege unavailable |
| `test_worktrees.WorktreeTests.test_verification_rejects_foreign_checkout_and_missing_worktree` | Windows error 1314: symlink privilege unavailable |

Some existing HTTP test teardown threads also emit Windows socket-close warnings.
These baseline conditions are recorded rather than represented as successful tests.

## Implementation decisions

- Retained the empty Bug-delegation shortcut from the approved design; free-text
  requests receive model interpretation before any mode transition.
- Kept `chat`/`fix` IDs for saved-job and host configuration compatibility. They name
  execution profiles, not separate conversational agents.
- A first repair from a mention uses the existing recorded delegation session and its
  target. Live delegation is checked again; the originating conversation can still
  steer or stop the destination. Its acknowledgment identifies where progress appears.
- Added context through ledger queries and immutable handoff evidence, avoiding a
  separate conversation manager or schema migration for existing jobs.

No live model replay, Unity test, production deployment or restart is claimed. Offline
tests validate the controller boundaries and context flow; intent interpretation still
depends on the worker following the updated instructions.
