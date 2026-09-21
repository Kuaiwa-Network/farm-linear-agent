# Closed and cancelled issue cleanup

Status: implemented and tested; independent review and deployment pending.

## Scope

When a tracked Linear issue is completed, cancelled or archived, cancel FarmBot work
whether queued, running, waiting for input or waiting for Unity. Stop owned processes,
preserve unfinished changes, then safely remove task worktrees. Keep all logs, run
history, ledger records and memory snapshots. No automatic retention expiry.

Keep `awaiting_input`, current reply-triggered resume behavior, natural-language
continuation handling and current session routing. Ordinary comments outside the
agent session remain inert unless they mention FarmBot. This change does not add an
explicit-resume gate to paused jobs or create an extra chat interpreter beside them.

Cancelled jobs stay cancelled. An authorized explicit restart of cancelled work
creates a new job linked to the previous job's checkpoint and preserved source refs.
Reopening alone never starts work. Resuming a paused, open job keeps its ID and
worktrees, as today. Non-cancelled terminal retry behavior is unchanged.

## Detection and authority

Accept signed Issue update notifications for the configured organization alongside
AgentSessionEvent, validating each envelope's own identity fields. Webhooks only
queue a status check for already-tracked issues; current Linear status determines
whether work is cancelled. Do not trust a delayed payload as current state.

A dedicated loop reconciles tracked unfinished issues at a configurable 60-second
default interval. Use lightweight status/delegation reads, bounded retries and fair
scheduling. Network failures defer action and remain visible; they never imply
closure. Source timestamps prevent older reads from replacing newer status.

Check current status before launch/restart. API unavailability defers launch. A
confirmed closed issue cancels its jobs; lost delegation prevents write work from
launching. Stop and cleanup must not wait for a network call holding scheduler locks.
No change to Linear status, assignee, scopes or automatic delegation.

## Cancellation and cleanup

Cancel and revoke the claim before signalling processes. Persist the last known
owned PID so a service restart does not erase cleanup evidence. Stop the tracked
worker and task-owned batch process. An unverified live PID must not be killed or
assumed safe; retain the worktrees and report the cleanup gap. Slots still require
existing quiescence checks; a failed check holds the slot for operator recovery.

Cleanup is repeatable. Record its result/errors in the ledger. Do not remove a
worktree while the old worker, descendants or resource handover may still touch it.
Preserve dirty tracked/non-ignored untracked source in local Git commits, and retain
refs for clean heads too because they may contain unpushed commits. Record repository
commit/ref evidence durably before deleting any worktree. No automatic pushes.
A preservation or validation failure keeps files and retries later. Paths must remain
inside FarmBot-owned roots and must not follow directory symlinks.

Preserve the existing checkpoint, question and PR references; force-stopped workers
cannot be expected to produce a new prose summary. New jobs see old evidence as
stale and verify current issue/code/PR state before applying saved work. Cancelled
successors cannot launch before predecessor cleanup and reservation settlement.
Published messages/PRs and already-issued external requests cannot be undone.

## Components and migration

Extend ledger cancellation/successor and cleanup metadata, receiver Issue intake,
Linear lightweight reads, scheduler stop/cleanup and service reconciliation. Reuse
existing worktree and launcher mechanisms with focused safeguards. Keep runtime
models, shared memory and multi-host plans unchanged. Additive database migration;
existing paused jobs and their unread answers are not rewritten.

Issue webhook subscription must be enabled on deployment; polling catches missed or
unavailable notifications. Do not silently change workspace configuration. Implementation
uses temporary SQLite/Git and stub Linear. Live service stays unchanged until merged
and deployment is authorized.

## Verification

Test all issue closure types across queued/running/input/resource-waiting jobs,
missed/duplicate/delayed webhooks, failed status reads, claim revocation before kill,
process restart/ownership gaps, preservation failure, clean unpushed and dirty commits,
symlink protection, same-ID paused resume, fresh-ID cancelled restart, and logs retained.
Use real subprocess and Git rehearsals; run the full warnings-as-errors suite and an
independent whole-branch review.

References: [Linear webhooks](https://linear.app/developers/webhooks),
[agent-session triggers](https://linear.app/developers/agent-interaction).
