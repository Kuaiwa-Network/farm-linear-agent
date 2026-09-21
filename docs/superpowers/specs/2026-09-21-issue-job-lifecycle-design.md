# Issue closure, explicit continuation, and job retention

Status: approved with the user’s correction to retain `awaiting_input`; implementation planning.

## Intent and agreed behavior

FarmBot must stop work when its Linear issue is completed, cancelled, or archived,
and must not retain abandoned working directories indefinitely. A question may need
answers from several people. Replies supply information; only an explicit human
continuation request or a new UI delegation authorizes another fix job.

Reactivating cancelled work creates a new work item ID, fresh claim, fresh worker
process and fresh execution state. The previous cancelled job remains cancelled.
Its compact handoff and preserved changes are evidence, not continuing authority.
Resuming an open job in `awaiting_input` keeps that job’s ID and existing worktrees,
but creates a fresh worker and claim. A new worker does not require a new job.

Keep `awaiting_input` as the existing paused state, with a pending question and
`needs-more-info`. Ordinary replies add information without resuming the job.
Silence does not cancel an open issue. Resource waits remain a separate scheduling
state: this change does not remove `awaiting_resource` either.

Assumptions proposed here: reconcile issue status every 60 seconds, and retain detailed
logs for 30 days after a job ends. Both are configurable positive values. A compact
history and references to preserved source changes remain until explicit operator
removal. This is retention cleanup, not secure erasure or backup deletion.

## Approach

Use the current receiver, deterministic scheduler, ledger and chat interpreter.
Add a small lifecycle/retention component to coordinate status reconciliation and
idempotent cleanup; keep file operations outside ledger transactions.

Alternatives considered:

- Webhooks alone are smaller but leave abandoned jobs after missed deliveries.
- Expiring every question after a timer avoids long waits but discards the team's
  ability to answer later and confuses silence with cancellation.
- Webhooks plus reconciliation, explicit continuation, and compact retained history
  meet the approved behavior without an always-running model coordinator.

## State and continuation

The normal job path is `queued -> running -> delivered | blocked | failed | cancelled`.
`running -> awaiting_input -> queued` requires an explicit continuation request for
the final transition; a reply alone cannot cause it.
`awaiting_resource` still yields the worker while the pool prepares a local slot.
Infrastructure recovery within a still-authorized job may retain that job's ID;
human reactivation of an ended job always creates a successor.

`await-input` posts the question, adds the label, saves the question/handoff, retires
the claim, and enters `awaiting_input`. An answer racing this transition is retained;
it does not turn the job back into queued work without explicit continuation intent.

Messages on inactive work go through the existing bounded chat flow. A short-lived
chat worker can interpret natural-language intent and answer questions, but ordinary
answers must not create or restart a fix worker. This preserves natural-language
continuation without keyword matching. Negations, quoted requests, hypothetical
questions, and incomplete discussion do not authorize continuation. Multiple people
can contribute; nobody's reply is implicitly a final sign-off. An explicit request
starts a fresh worker that may ask again if material ambiguity remains.

Extend `resume-work` to distinguish waiting work from ended work. Waiting work is
requeued with the same ID; ended work gets an atomic successor. Retain the CLI name
and return the chosen item plus whether a successor was created. Require a live owned
chat claim, an actual source message, the same issue, prior UI delegation, and a freshly
fetched open issue still delegated to FarmBot. Complete the chat and requeue the waiting
job or insert the successor in one transaction. Record `predecessor_id` for successors,
requesting message provenance, and a stable request key so redelivery or retry cannot
create duplicate executions.

Allow one bounded chat interpreter alongside one waiting fix job, while still allowing
at most one queued/running/resource-waiting execution per issue and one unfinished fix
job per issue. Change the ledger uniqueness indexes and receiver selection together;
do not simply remove the current one-active-item constraint. Messages received during
chat handoff are forwarded exactly once to the selected job. If a waiting chat itself
needs interpretation, a fresh worker may inspect the new reply in that chat; this is
conversation handling, not authorization to resume a fix.

The successor gets no old PID, token, lease, reservation, generation counter or
pending executable action. Link the previous handoff, saved Git refs and PRs; mark
their contents as stale until reverified. Refresh issue details, all comment pages,
and relevant agent-session activities before interpreting the request. Preserve
local message provenance where Linear does not provide a complete recoverable copy;
missing history is reported rather than invented. Session identity may be reused.
New UI delegation explicitly resumes a waiting fix, or creates a new job linked to
the most recent relevant ended job when no waiting fix exists.

## Closure detection and cancellation

Treat Linear state types `completed` and `canceled`, and an archive timestamp, as
closed for execution. Status names are team-specific and are not used as rules.

Accept signed Issue change webhooks for the configured organization in addition to
AgentSessionEvent. The ordinary Issue payload is a different envelope; do not require
agent-session-only identity fields on it. Authenticate, validate and durably record
the notification before acknowledging; use a fresh issue read to establish current
status rather than allowing delayed notifications to overwrite newer state. Only
issues already tracked by FarmBot are eligible for lifecycle handling. An open-state
notification never authorizes work.

A separate bounded reconciliation loop scans tracked issues with active jobs or
unfinished lifecycle cleanup. It also checks open issues with waiting or blocked jobs
so closure while waiting is discovered. Failures are retried with backoff; an API error alone is
not evidence of cancellation. Reconciliation must not block Stop or resource cleanup.
Recheck current status/delegation before initial launch and every human reactivation.
An unavailable preflight check defers the launch without creating a model process.

When a current issue is closed, cancel queued/running/resource-waiting/input-waiting
jobs and mark unresolved blocked jobs cancelled. Do not rewrite delivered historical
results.
Explicit Stop and operator cancellation use the same cancellation implementation.
Persist the cancellation and retire the claim before stopping owned processes. Record
process ownership separately until termination is verified, so clearing a claim does
not lose the ability to stop its process after a service crash.

Fence new batch launches, kill the task-owned worker and batch process group, then
let the slot pool perform its existing quiescence check. Failure to prove safety
holds the slot for operator recovery. Never release a slot just because time passed,
and never kill another job's process or editor. A successor cannot launch against a
predecessor whose processes or reservations have not finished cleanup.

Cancellation is repeatable and survives crashes between state change, termination,
preservation and removal. Further claim-authenticated mutations are refused after
cancellation. Already-issued external requests and already-published PRs/messages
cannot be undone; retain their references. This design does not promise an atomic
transaction spanning Linear, GitHub and local process termination.

Reopening only changes eligibility. A later explicit continuation request or UI
delegation creates a successor; it never reopens an old job or silently restarts it.

## Preservation and cleanup

Cleanup has persisted progress/error metadata distinct from execution status.
Run it for ended jobs. An open job in `awaiting_input` retains its handoff and
worktrees for explicit resumption; closure changes it to cancelled and makes it
eligible for cleanup. Do not remove a waiting job’s worktrees merely because it asked
a question. Once a job ends, the saved handoff and source changes replace the need
to keep its working directories mounted.

1. Confirm owned processes have exited and dependent resource cleanup is safe.
2. Preserve changed tracked and non-ignored untracked source files in local Git
   commits/refs, without pushing. Record each repository's ref, base and commit in
   the ledger, including clean heads containing earlier unpushed commits.
3. Save a bounded handoff (at most 16 KiB) with remaining work, pending question,
   source refs and existing PR links. Forced cancellation uses the last checkpoint
   plus deterministic Git evidence; it cannot promise an LLM-written final summary.
4. Remove only FarmBot-owned task worktrees whose preservation succeeded. A commit,
   ref, path-validation or ledger-write failure keeps affected files and records an
   actionable cleanup failure. Never repeat the current best-effort-commit followed
   by unconditional removal behavior for these paths.
5. Once no process needs them, remove retired runtime credential/token copies.
   Keep detailed logs for the configured retention period, then remove eligible
   run directories and compact redundant local issue/message prose while retaining
   handoff, provenance, deduplication keys and externally published action identities.

Do not delete local WIP refs containing unique changes as part of log retention.
Do not touch user checkouts, Unity slot folders, shared clones, published PRs or
shared agent-memory notes. Archived snapshots of shared memory are generated data:
prune only after their last retained run reference is gone, coordinating publication
and pruning so a freshly published but not yet attached snapshot cannot be deleted.
Reuse the existing conservative retention scan: malformed paths, symlinks or an
incomplete scan prevent deletion rather than guessing. The single service instance
must serialize maintenance with launch publication; unsupported concurrent services
must be refused. Operator offline pruning remains available.

Successors use new worktree paths. Fetch current remote state and expose preserved
changes through recorded refs; do not blindly reset a branch or replay an old patch
over newer work. The new worker checks whether changes are already merged, useful,
conflicting or obsolete before applying them. Missing history does not prevent a
fresh investigation, but must not be reported as restored progress.

## Migration and operational boundaries

Use additive schema changes where possible. Preserve existing `awaiting_input` rows,
checkpoints, replies and source files. Apply explicit-continuation semantics to them;
do not requeue them merely because they already have an unread answer.
Run cleanup only after the new preservation path is installed and verified. Existing
terminal jobs are never automatically requeued by migration. Operator `retry` of an
ended job adopts successor semantics too; lease recovery remains distinct.

The operating contract, chat/fix skills, CLI help and tests must describe the same
semantics. Update the original design's lifecycle sections. Shared-memory authority,
gameplay contracts, default model settings, GitHub identity and multi-host support
are unchanged. No reminders or automatic cancellation due solely to silence.

Issue-webhook subscription is a deployment prerequisite, not assumed to exist from
the current AgentSessionEvent subscription. Reconciliation provides a fallback while
configuration is pending. Document actual webhook delivery and polling health. Do
not broaden OAuth scopes or change workspace settings silently.

## Verification

- Multiple answers and a reply racing `await-input` do not resume a fix.
- Explicit English/Chinese continuation resumes a waiting job with the same ID and
  a fresh claim, or creates exactly one successor to ended work. Negation, quotation
  and ordinary answers do neither. Closed or undelegated issues refuse continuation.
- Old cancelled/blocked/delivered jobs retain history; successor claims and resource
  tokens are new. Reopening alone is inert; re-delegation is deduplicated.
- Closure/cancellation/archive while queued, running, awaiting input or a resource, or blocked
  stops further authorized work. Include a live worker, a batch process and races
  between Stop, grant, launch, reply and successor creation.
- Duplicate, delayed, foreign-organization and invalidly signed Issue notifications
  cannot corrupt state. Missed notifications are caught by reconciliation; API errors
  do not mark issues closed or start unchecked jobs.
- Dirty, clean-but-unpushed, detached and multi-repository work survive cleanup.
  Failed preservation prevents deletion. Restart at every cleanup stage remains safe.
- Retention uses fake clocks and temporary files; active work and failed cleanup are
  excluded. Snapshot publication/pruning races and malformed/symlinked paths retain
  data safely. Compact history and unique WIP refs survive log expiry.
- Run the full warning-as-error suite and a real subprocess rehearsal with stub Linear
  before PR delivery. Production closure tests require a designated test issue;
  never close or cancel an unrelated issue to exercise this code.

## References

Repository evidence: `agent/ledger.py` (`await_input`, `resume_work`),
`agent/receiver.py`, `agent/scheduler.py`, `agent/worktrees.py`, and `agent/memory.py`.
The current implementation resumes the original item and does not implement this design.

Linear documents Issue change notifications and webhook authentication in
[Webhooks](https://linear.app/developers/webhooks), and reconstructing conversations
from session activities in
[Interaction best practices](https://linear.app/developers/agent-best-practices).
