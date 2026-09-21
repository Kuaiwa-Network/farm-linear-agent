# Closed and Cancelled Issue Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Stop and safely clean up FarmBot work on closed/cancelled/archived issues while retaining logs and current pause/resume behavior.

**Architecture:** Add durable cancellation/cleanup evidence to the ledger; reuse scheduler/launcher ownership and Git preservation. A small lifecycle coordinator consumes signed dirty-issue notifications and polls current Linear status. Cancelled restarts create successor jobs.

**Tech Stack:** Python 3.11+, stdlib SQLite/unittest/subprocess, Git, existing Linear GraphQL client.

**Spec:** `docs/superpowers/specs/2026-09-21-issue-job-lifecycle-design.md`.

## Global Constraints

- Keep all logs, run history, ledger records and memory snapshots. No automatic retention expiry.
- Keep `awaiting_input`, current reply-triggered resume behavior, natural-language continuation handling and current session routing.
- Cancelled jobs stay cancelled; authorized restart creates a linked successor. Paused resumption keeps its ID.
- Preserve source changes and durable refs before worktree removal; failures retain files.
- Only owned processes and FarmBot-owned worktrees are eligible for cleanup. Slot release requires quiescence.
- Reconcile every 60 seconds by default; API failures defer launch rather than inventing closure.
- Use additive migrations, real temporary SQLite/Git tests and stub Linear. Do not change live service or real issues.

## Review Focus

1. A live but unverifiable/reused PID after restart: no unrelated kill or destructive cleanup (Task 2).
2. A clean worktree contains unpushed commits, or multi-repo preservation only partly succeeds: preserve everything before removal (Task 2).
3. New worker preparation races cancellation or successor creation: no cancelled claim or old-worktree deletion race (Tasks 1–3).
4. Stale status reads and duplicate notifications: no revival of cancelled jobs or stale overwrite (Task 3).
5. Symlinked paths and unavailable status service: preserve local files and avoid unchecked launches (Tasks 2–3).

### Task 1: Durable cancellation and fresh cancelled successors

**Files:** `agent/ledger.py`, `agent/__main__.py`, `tests/test_lifecycle_ledger.py` (new), `tests/test_resume_work.py`, `tests/test_ledger.py`, `tests/test_cli.py`.

**Interfaces:**
- Keep `cancel(item_id, reason)` but make repeat cancellation idempotent and retain cleanup PID evidence.
- Add `cleanup_record(item_id) -> dict | None`, `record_cleanup(item_id, result, error=None) -> None`.
- Add `predecessor_id` to work items and expose it in item/context; cancelled `retry`/`resume_work` create a new row.
- Existing non-cancelled retry, paused replies and resource resume semantics remain unchanged.

- [ ] Write regression tests using `LedgerBase`:

```python
job = self.new_item()
token = self.ledger.claim(job['id'], worker_id='worker')['token']
self.ledger.cancel(job['id'], 'issue closed')
self.ledger.cancel(job['id'], 'duplicate close')
with self.assertRaises(LedgerError):
    self.ledger.renew(job['id'], token)
new = self.ledger.retry(job['id'], 'operator requests restart')
self.assertNotEqual(new['id'], job['id'])
self.assertEqual(new['predecessor_id'], job['id'])
self.assertEqual(self.ledger.item(job['id'])['state'], 'cancelled')
```

Also cover closed refusal, cancelled chat continuation, no token/PID/reservation copying, complete reply handoff, and unchanged paused reply behavior. Update only expectations invalidated by fresh cancelled IDs.

- [ ] Run RED: `PYTHONPATH=tests python3 -B -W error -m unittest test_lifecycle_ledger -v`.
Expected: duplicate cancellation or successor-ID assertions fail.
- [ ] Implement additive columns/cleanup table and a transaction-local cancelled-successor insertion helper. Complete the interpreter chat before inserting its successor in the same transaction; keep original cancelled row immutable. Existing one-active-item index prevents duplicate successors. Copy only checkpoint/provenance as stale evidence; initialize new execution fields from `create_work_item` defaults. Fetch current issue/delegation at CLI continuation boundaries.
- [ ] Run GREEN: `PYTHONPATH=tests python3 -B -W error -m unittest test_lifecycle_ledger test_resume_work test_ledger test_cli -v`.
Expected: all pass.
- [ ] Commit: `git add agent/ledger.py agent/__main__.py tests/test_lifecycle_ledger.py tests/test_resume_work.py tests/test_ledger.py tests/test_cli.py` then `git commit -m 'feat: retain cancelled history and create fresh successor jobs'`.

### Task 2: Preserve source before stopping and cleaning cancelled work

**Files:** `agent/worktrees.py`, `agent/scheduler.py`, `agent/launcher.py`, `tests/test_worktrees.py`, `tests/test_scheduler.py`, `tests/test_launcher.py`, `tests/test_cleanup.py` (new).

**Interfaces:**
- Add `Worktrees.preserve(item_id) -> {committed: dict[str,str], refs: dict[str,str], errors: dict[str,str]}` retaining dirty and clean HEADs.
- Add `Worktrees.remove_preserved(item_id, evidence) -> None`, validating refs/path/clean HEAD immediately before removal.
- Scheduler uses Task 1 cleanup records, invalidates claim before signalling, and defers cleanup while process/reservation ownership is unresolved.

- [ ] Add failing real-Git tests: create an unpushed clean commit, preserve/remove the worktree, verify the saved ref still resolves; repeat for dirty tracked/untracked files and detached HEAD. Multi-repo failure, path symlink or ref mismatch must retain files. Example:

```python
saved = self.trees.preserve('item-1')
self.assertEqual(saved['errors'], {})
self.trees.remove_preserved('item-1', saved)
self.assertEqual(git('rev-parse', saved['refs']['Farm-Client'], cwd=self.trees.clone_path('Farm-Client')),
                 saved['committed']['Farm-Client'])
```

Add stop-order tests that attempt a claim mutation from inside the kill callback and expect refusal. Test restart with recorded owned PID, unverified live PID, pending/held reservation, preservation failure and logs still present.
- [ ] Run RED: `PYTHONPATH=tests python3 -B -W error -m unittest test_cleanup test_worktrees -v`.
Expected: missing preservation methods or old cleanup removes files after failure.
- [ ] Implement local recovery refs for every worktree HEAD; reuse `commit_wip` identity but report errors before any removal. Reject symlinked managed directories and verify clone association. Record refs/checkpoint evidence before removing files; never push. Scheduler cancels first, stops owned worker/batch processes, checks they are gone, waits for reservation settlement, then preserves/removes. Inability to prove ownership/exit holds cleanup visibly. Same process/cleanup gates protect cancelled successors. Old terminal sweeps must not bypass these guards. No logs or snapshots are deleted.
- [ ] Run GREEN: `PYTHONPATH=tests python3 -B -W error -m unittest test_cleanup test_worktrees test_scheduler test_launcher test_slots -v`.
Expected: pass, including real subprocess cleanup assertions.
- [ ] Commit: `git add agent/worktrees.py agent/scheduler.py agent/launcher.py tests/test_worktrees.py tests/test_scheduler.py tests/test_launcher.py tests/test_cleanup.py` then `git commit -m 'fix: preserve cancelled work before safe cleanup'`.

### Task 3: Reconcile closed issues and fence launch

**Files:** `agent/lifecycle.py` (new), `agent/ledger.py`, `agent/linear_api.py`, `agent/receiver.py`, `agent/config.py`, `agent/service.py`, `agent/scheduler.py`; `tests/test_lifecycle.py` (new), `tests/test_receiver.py`, `tests/test_linear_api.py`, `tests/test_service.py`, `tests/test_scheduler.py`.

**Interfaces:**
- `LinearAPI.issue_status(issue_id) -> dict` returns ID, status name/type, archive flag, delegate ID and source update timestamp.
- Ledger methods queue a tracked issue for status refresh, select due tracked issues, apply versioned status, and record retry state.
- `Lifecycle(ledger, api, scheduler, interval=60, clock=time.time).tick() -> dict`; dedicated service connection/thread.
- Scheduler optional preflight callback defers launch on unavailable/currently unauthorized issue, outside its lock.

- [ ] Add failing real-ledger tests with fake status API: all three closed conditions cancel queued/running/input/resource-waiting jobs; open and failed reads preserve jobs; reopen leaves old jobs cancelled; stale reads cannot overwrite newer state. Signed Issue events without AgentSessionEvent identity fields are accepted for configured organization, foreign/invalid/untracked inputs are refused/ignored, duplicates are harmless. Add launch preflight unavailable and closure-during-preparation regressions.
- [ ] Run RED: `PYTHONPATH=tests python3 -B -W error -m unittest test_lifecycle test_receiver -v`.
Expected: no lifecycle coordinator and Issue events currently ignored.
- [ ] Implement the lightweight query:

```graphql
query FarmBotIssueStatus($id: String!) {
  issue(id: $id) { id updatedAt archivedAt state { name type } delegate { id } }
}
```

Persist status versions so old full-detail reads cannot undo newer closure. Issue receipt only queues a durable refresh for already-tracked IDs after HMAC/timestamp/organization checks; use current fetched status, not payload state. Periodic fair reconciliation checks unfinished and blocked jobs, records bounded backoff on failure and invokes existing scheduler stop for closure. Dedicated network loop must not block Stop. Fresh preflight status gates initial/resumed launches and stale preparation must recheck cancellation before registering/spawning. Startup runs reconciliation; shutdown joins its loop/connection. Config `reconcile_seconds` defaults 60 and rejects invalid/nonpositive values. No Linear writes in polling.
- [ ] Run GREEN: `PYTHONPATH=tests python3 -B -W error -m unittest test_lifecycle test_receiver test_linear_api test_service test_scheduler test_lifecycle_ledger -v`.
Expected: all pass, including existing reply-resume tests unchanged.
- [ ] Commit the files above with `git commit -m 'feat: cancel and reconcile work for closed Linear issues'`.

### Task 4: End-to-end verification and operating documentation

**Files:** `tests/test_lifecycle_integration.py` (new), `reports/2026-09-21-issue-job-lifecycle/report.md`, `README.md`, `docs/operating-contract.md`, current design spec, relevant chat/fix instructions for cancelled-successor recovery only.

**Interfaces:** production ledger/scheduler/lifecycle with temporary Git origins, real subprocesses and stub Linear; no alternate cancellation code in rehearsal.

- [ ] Write failing integration cases: close while worker writes source, observe claim revoked/process exit, preserve source ref, remove only worktree, retain logs; reopen starts nothing; explicit cancelled restart yields new ID with recovery evidence. A paused open job keeps its ID and current reply-triggered resume behavior.
- [ ] Run RED: `PYTHONPATH=tests python3 -B -W error -m unittest test_lifecycle_integration -v`.
Expected: any remaining wiring defects are reproduced before fixing.
- [ ] Fix only integration defects exposed, then run GREEN using the same command. Expected: pass.
- [ ] Run full suite: `python3 -B -W error -m unittest discover -s tests`. Expected: all pass, warning-free; record actual count. Run `git diff --check`; expected exit 0.
- [ ] Rewrite current operating contract/README around actual implemented closure polling, Issue webhook setup, same-ID paused resume, fresh cancelled restart, cleanup errors and recovery refs. State logs are retained. Record test evidence and live-deployment limits in report.
- [ ] Commit docs/tests, request one independent whole-branch review, fix consequential findings with RED/GREEN regressions and full suite. Follow the established push/draft-PR preference; attach PR. No merge/deploy in this implementation step.

## Self-review

Scope maps to Task 1 cancelled state/history, Task 2 safe preservation, Task 3 detection/launch fencing and Task 4 integrated evidence/docs. All five Review Focus cases have owning tests. Removed explicit-resume redesign, extra interpreter, log expiry, history compaction and snapshot pruning completely. User corrections and latest confirmation approve the narrower implementation; earlier broader plan remains only in Git history.
