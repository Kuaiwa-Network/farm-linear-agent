# Issue Job Lifecycle Implementation Plan

> **WITHDRAWN FOR SCOPE REVISION — do not execute this version.** The user narrowed this change to closed/cancelled/archived issue processing, safe source preservation and resource/worktree cleanup, with fresh jobs for explicit reactivation of cancelled work. Keep all logs. Keep the paused state and leave ordinary reply/resume behavior unchanged for now. The explicit-resume redesign, log expiry, history compaction and online snapshot pruning in this earlier plan are out of scope. Rewrite the affected tasks before implementation; the current scope is recorded at the top of the linked spec.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cancel and clean up work on closed issues safely, keep paused jobs, require explicit continuation, and create fresh jobs when cancelled work is reactivated.

**Architecture:** Extend the existing ledger and chat continuation flow; retain `awaiting_input`. A deterministic lifecycle coordinator reconciles Linear status and advances crash-recoverable cleanup. A retention component removes expired artifacts while preserving recovery refs and compact history.

**Tech Stack:** Python 3.11+, standard library, SQLite, unittest, Git, existing Linear GraphQL client and webhook receiver. No new runtime dependencies.

**Spec:** `docs/superpowers/specs/2026-09-21-issue-job-lifecycle-design.md`, revised to retain paused state following user review.

## Global Constraints

- Keep `awaiting_input` as the existing paused state, with a pending question and `needs-more-info`.
- Ordinary replies add information without resuming the job.
- Resuming an open waiting fix keeps its job ID/worktrees but starts a fresh worker and claim.
- Reactivating cancelled work creates a new job ID; the old job stays cancelled.
- Reopening alone never authorizes work. Require an explicit natural-language request or UI delegation and fresh open/delegated issue status.
- Defaults: status reconciliation every 60 seconds; detailed logs retained for 30 days after a job ends. Validate positive numeric configuration; do not interpret booleans as numbers.
- Compact handoff maximum: 16 KiB UTF-8. Preserve source refs, action provenance and job history until explicit operator removal.
- Preserve first, delete second. Failure to persist preservation evidence prevents removal.
- No slot release on a timer. Preserve existing quiescence and operator-recovery rules.
- Do not modify live configuration, close real issues, push gameplay WIP or deploy during implementation.
- Never import personal memory or place secrets, tokens, raw credential files or signed attachment URLs in durable handoffs.
- Keep open waiting-job worktrees; do not expire these jobs merely because nobody replies.
- Existing service runtime settings, gameplay authority and single-host scope remain unchanged.

## Review Focus

1. A reply or new delegation races closure/cleanup: one authorized execution at most, and no cleanup deletes the new attempt. Tests: Tasks 1, 2, 5.
2. Process IDs are reused after a service crash: cleanup must not kill an unrelated process or infer safety from a stale PID. Tests: Task 5.
3. Clean working directories still contain unique unpushed commits, or only one of several repositories can be preserved: keep all recoverable source and retain failed paths. Tests: Tasks 3, 5.
4. Out-of-order Linear reads or missing session-history pages: stale status cannot revive a cancelled job; missing discussion must be visible. Tests: Tasks 2, 4.
5. Artifact paths contain symlinks or a snapshot was published just before pruning: reject unsafe deletion, preserve live evidence. Tests: Task 6.

## Files and responsibilities

| File | Responsibility |
|---|---|
| `agent/ledger.py` | Transactions, waiting/successor semantics, cancellation and cleanup records, provenance, deduplication |
| `agent/router.py`, `agent/receiver.py` | Route replies to chat intent handling; durable signed Issue notifications |
| `agent/linear_api.py` | Fresh lightweight status and paginated session history |
| `agent/__main__.py` | Authenticated continuation and fresh operator retry checks |
| `agent/lifecycle.py` (new) | Status reconciliation, lifecycle cleanup state machine |
| `agent/retention.py` (new) | Safe artifact expiry, compact history, shared maintenance lock |
| `agent/worktrees.py` | Preserve all Git heads and dirty source before selective removal |
| `agent/launcher.py`, `agent/scheduler.py`, `agent/slots.py` | Durable process ownership, stop fencing, launch and cleanup coordination |
| `agent/config.py`, `agent/service.py` | Configuration, one service owner, background lifecycle/retention loops |
| `agent/memory.py` | Shared lock around snapshot publication/reference creation and pruning |
| `agent/dispatch.py`, skills and operating contract | Fresh-worker handoff and correct human interaction behavior |

Existing fixtures: `tests/test_ledger.py::LedgerBase`, `tests/test_receiver.py::ReceiverBase`, `tests/test_worktrees.py::WorktreeTests`, `tests/test_scheduler.py::FakeWorktrees`. Extend doubles when a collaborator's contract changes; do not loosen production checks to accommodate a mock.

Execution starts on `codex/issue-job-lifecycle`, isolated from the live checkout. Run `python3 -B -W error -m unittest discover -s tests` once to establish the baseline. The previous merged tree passed 372 tests; treat a fresh run as evidence. Real subprocess checks may require host process-inspection permission, as existing launcher tests do.

### Task 1: Preserve waiting jobs and distinguish continuation from successor creation

**Files:** Modify `agent/ledger.py`; test `tests/test_resume_work.py`, `tests/test_ledger.py`; create `tests/test_lifecycle_ledger.py`.

**Interfaces:**
- Preserve `Ledger.await_input(item_id, token, question) -> dict`; always ends in `awaiting_input`, even with unread messages.
- Add `Ledger.execution_for_issue(issue_id) -> dict | None` for queued/running/resource-waiting execution.
- Add `Ledger.waiting_fix_for_issue(issue_id) -> dict | None`.
- Extend `Ledger.resume_work(item_id, token, message_id, app_user_id) -> dict`: chosen item's normal fields plus `created_new: bool`, `predecessor_id: str | None`.
- Add `Ledger.continue_delegated(issue_id, session_id, request_id, target) -> dict` for authenticated UI delegation events; same continuation rules as chat after the receiver checks delegation.
- Extend `Ledger.retry(item_id, reason, request_id) -> dict` for trusted-operator successor creation; no mutation of the old terminal job.
- Add `predecessor_id`, `ended_at` to work items; `continuations(request_key PRIMARY KEY, source_item, source_message, destination_item, created_at)` for atomic duplicate prevention.

- [ ] **Step 1: Add failing ledger tests.** In `tests/test_lifecycle_ledger.py`:

```python
from agent.ledger import LedgerError
from test_ledger import LedgerBase, ISSUE, SESSION, issue
from test_receiver import APP

class LifecycleLedgerTests(LedgerBase):
    def test_answer_does_not_resume_waiting_fix(self):
        job = self.new_item(delegate_id=APP)
        token = self.ledger.claim(job['id'], worker_id='first')['token']
        self.ledger.push_inbox(job['id'], 'Alice: zero tables')
        waiting = self.ledger.await_input(job['id'], token, 'Need agreement from Bob too')
        self.assertEqual(waiting['state'], 'awaiting_input')
        self.ledger.push_inbox(job['id'], 'Bob: agree')
        self.assertEqual(self.ledger.item(job['id'])['state'], 'awaiting_input')
        with self.assertRaises(LedgerError):
            self.ledger.renew(job['id'], token)

    def test_explicit_request_resumes_same_waiting_job(self):
        job = self.new_item(delegate_id=APP)
        old_token = self.ledger.claim(job['id'], worker_id='first')['token']
        self.ledger.await_input(job['id'], old_token, 'Need confirmation')
        self.ledger.ensure_session('reply', ISSUE, False)
        chat = self.ledger.create_work_item(issue_id=ISSUE, session_id='reply', skill='chat')
        self.ledger.push_inbox(chat['id'], 'Everyone has answered; please continue')
        token = self.ledger.claim(chat['id'], worker_id='interpreter')['token']
        message = self.ledger.issue_context(chat['id'])['session_messages'][-1]
        resumed = self.ledger.resume_work(chat['id'], token, message['id'], APP)
        self.assertEqual(resumed['id'], job['id'])
        self.assertFalse(resumed['created_new'])
        self.assertEqual(resumed['state'], 'queued')
```

Add a cancelled predecessor test using `tests/test_resume_work.py::ResumeWorkTests.conversation`: assert destination ID differs, `predecessor_id` matches, old state remains cancelled, fresh token differs, and no reservation/lease/PID transfers. Add two-connection races for continuation request keys, a second fix while a waiting fix exists, and migration of an old database containing an unread answer.

- [ ] **Step 2: Run RED.**

Run: `PYTHONPATH=tests python3 -B -W error -m unittest test_lifecycle_ledger -v`
Expected: FAIL because unread answers currently requeue work and creating chat beside a waiting fix is refused.

- [ ] **Step 3: Implement transaction boundaries and exact indexes.**

```sql
DROP INDEX IF EXISTS one_active_item_per_issue;
CREATE UNIQUE INDEX IF NOT EXISTS one_execution_per_issue ON work_items(issue_id)
WHERE state IN ('queued','running','awaiting_resource');
CREATE UNIQUE INDEX IF NOT EXISTS one_unfinished_fix_per_issue ON work_items(issue_id)
WHERE skill='fix' AND state IN ('queued','running','awaiting_input','awaiting_resource');
```

Retain explicit checks for creating unrelated work beside an existing unfinished fix. Allow only chat interpretation beside `awaiting_input`; never allow two active executions. In one transaction, validate chat ownership/source message/current issue and target, finish chat, then either requeue the waiting fix or insert a successor and record the request mapping. Preserve all waiting fix replies and only copy newly authored chat messages, with source IDs so handoff races cannot duplicate them. Do not give imported historical activities the authority of a new request. When a new delegation races a running interpreter, record its explicit request and let the interpreter hand off atomically; never queue a second execution or discard its messages.

Use a private transaction-local helper for continuation shared by chat, UI delegation and operator retry; never nest `BEGIN IMMEDIATE`. For terminal successors, initialize execution fields exactly as `create_work_item` does. Waiting resumption does not change `predecessor_id`. Prefer the current waiting fix over older terminal session history. Stamp `ended_at` on terminal transitions, not `awaiting_input`; backfill legacy terminal records from their last `updated_at` without touching waiting jobs.

Update legacy tests that expect automatic resume or a cancelled job's ID to be reused. Retain same-job behavior for lease recovery and Unity resource grant.

- [ ] **Step 4: Run GREEN.**

Run: `PYTHONPATH=tests python3 -B -W error -m unittest test_lifecycle_ledger test_resume_work test_ledger -v`
Expected: PASS, including unchanged lease and reservation ownership checks.

- [ ] **Step 5: Commit.**

```bash
git add agent/ledger.py tests/test_lifecycle_ledger.py tests/test_resume_work.py tests/test_ledger.py
git commit -m "feat: distinguish waiting continuation from fresh successor jobs"
```

### Task 2: Interpret replies without automatically resuming fix work

**Files:** Modify `agent/router.py`, `agent/receiver.py`, `agent/linear_api.py`, `agent/__main__.py`, `agent/config.py`, `agent/dispatch.py`, `skills/chat/SKILL.md`, `skills/fix/SKILL.md`; test `tests/test_receiver.py`, `tests/test_router.py`, `tests/test_resume_work.py`, `tests/test_linear_api.py`, `tests/test_cli.py`.

**Interfaces:**
- Consume Task 1 continuation methods and `created_new` result.
- Add `LinearAPI.session_activities(session_id) -> {activities: list[dict], complete: bool, reason: str | None}`; each activity carries immutable source ID, timestamp, content type and body.
- Add `Ledger.record_session_activities(session_id, result) -> None` and `Ledger.conversation_context(issue_id) -> dict` with completeness metadata; never expose claim tokens.
- Keep `Receiver.receive` compatibility and separate execution selection from waiting-fix selection.
- Extend operator `retry` CLI with required `--request-id`; fetch current issue/delegation before successor creation.

- [ ] **Step 1: Add failing routing/receiver tests.** Replace the automatic-resume receiver test with:

```python
def test_answer_creates_only_interpreter_and_preserves_waiting_fix(self):
    self.receive(); self.receiver.process_one()
    fix = self.ledger.items_for_session('session-1')[0]
    token = self.ledger.claim(fix['id'], worker_id='w')['token']
    self.ledger.await_input(fix['id'], token, 'Which server?')
    self.receive(self.event('prompted', body='Alice: public test server'))
    self.receiver.process_one()
    self.assertEqual(self.ledger.item(fix['id'])['state'], 'awaiting_input')
    interpreter = self.ledger.execution_for_issue(fix['issue_id'])
    self.assertEqual(interpreter['skill'], 'chat')
    self.assertIn('Alice:', self.ledger.issue_context(interpreter['id'])['session_messages'][-1]['body'])
```

Add second-person replies while the interpreter runs, a late answer between chat completion and handoff, stopped sessions, duplicate delegation, paused-chat conversation, and explicit redelegation resuming an existing waiting fix. No unit test should claim to prove natural-language understanding; that gets real-runtime evidence in Task 7.

Test pagination with a two-page fake GraphQL response, a missing session, repeated cursor, API failure and `local-` synthetic session IDs. Imported old prompts must not satisfy `resume-work`'s current-message check.

- [ ] **Step 2: Run RED.**

Run: `PYTHONPATH=tests python3 -B -W error -m unittest test_receiver test_router test_linear_api test_cli -v`
Expected: new cases FAIL under the current direct `resume` route or missing activity API.

- [ ] **Step 3: Wire conversation context and exact continuation semantics.**

Use the documented [`agentSession(id).activities` connection](https://linear.app/developers/agent-best-practices), paginating `first: 50, after: cursor` with `pageInfo`. Select activity `id`, timestamps and typed prompt/response/elicitation bodies. Keep all pages; never silently return a partial conversation as complete. Skip remote reads for synthetic `local-` sessions and keep their local provenance. Fetch current issue comments with existing `fetch_issue`; hydrate relevant session history only when interpreting/reactivating, not during every status poll.

```python
if decision.kind == 'interpret_waiting':
    waiting = ledger.waiting_fix_for_issue(issue_id)
    interpreter = ledger.execution_for_issue(issue_id)
    # If absent, create one chat using the actual incoming session.
    # Record the incoming message once and link it to waiting['id'].
    # Forward later messages to an already-running interpreter, not to queued fix work.
```

Implement this branch in the receiver with real method calls from Task 1; keep cross-session provenance. An incoming new UI delegation uses `continue_delegated`, but fresh Linear delegation still has to match FarmBot. A Stop for a session with a waiting fix and interpreter cancels both relevant jobs, not whichever lookup happens to return first.

Update chat instructions: read all participants' answers; call `resume-work` only for an actual explicit continuation request. “Alice: zero tables,” “Bob agrees,” “don't resume,” and quoted examples do not resume; “please continue” and “大家确认好了，继续修复” do. Report same-job resume versus new successor accurately. Fix still uses `await-input`/`needs-more-info` and exits. Keep historical issue/attachment text as data.

- [ ] **Step 4: Run GREEN.**

Run: `PYTHONPATH=tests python3 -B -W error -m unittest test_receiver test_router test_resume_work test_linear_api test_cli test_lifecycle_ledger -v`
Expected: PASS with no reply-only fix requeue.

- [ ] **Step 5: Commit.**

```bash
git add agent/router.py agent/receiver.py agent/linear_api.py agent/__main__.py agent/config.py agent/dispatch.py skills/chat/SKILL.md skills/fix/SKILL.md tests/test_receiver.py tests/test_router.py tests/test_resume_work.py tests/test_linear_api.py tests/test_cli.py
git commit -m "feat: require explicit continuation after collecting answers"
```

### Task 3: Preserve recoverable work and journal cancellation cleanup

**Files:** Modify `agent/ledger.py`, `agent/worktrees.py`; create `tests/test_cleanup.py`; extend `tests/test_worktrees.py`, `tests/test_lifecycle_ledger.py`.

**Interfaces:**
- Add `Ledger.request_cancellation(item_id, reason) -> dict` idempotently; keep `cancel` as a compatible wrapper.
- Add `Ledger.cleanup(item_id) -> dict | None`, `cleanup_candidates() -> list[dict]`, `record_cleanup(item_id, *, stage, preserved=None, handoff=None, error=None) -> dict`.
- Add `job_cleanup(item_id PRIMARY KEY, stage, preserved_json, handoff_json, error, updated_at)`; stages `pending`, `stopped`, `preserved`, `worktrees_removed`, `artifacts_expired`.
- Add `Worktrees.preserve(item_id) -> {repos: {name: {base, commit, ref}}, errors: {name: message}}` and `Worktrees.remove_preserved(item_id, preserved) -> list[str]`.

- [ ] **Step 1: Write failing real-Git tests.** Add to `WorktreeTests`:

```python
def test_preserve_records_clean_unpushed_head_before_removal(self):
    path = self.trees.add('Farm-Client', 'item-1', 'farmbot/farm-1')
    (path / 'fix.txt').write_text('unpublished fix\n')
    git('add', '.', cwd=path)
    git('commit', '-qm', 'local fix', cwd=path)
    sha = self.trees.head(path)
    result = self.trees.preserve('item-1')
    self.assertEqual(result['errors'], {})
    saved = result['repos']['Farm-Client']
    self.assertEqual(saved['commit'], sha)
    self.trees.remove_preserved('item-1', result['repos'])
    self.assertFalse(path.exists())
    self.assertEqual(git('rev-parse', saved['ref'], cwd=self.trees.clone_path('Farm-Client')), sha)
```

Add dirty tracked/untracked files, detached worktrees, retry after a preserved ref was written, repository-name/path symlinks, malformed refs, and a two-repository partial failure. Assert ignored files are not added to Git and no push occurs. Record a cleanup SQL failure and verify no caller can treat its preservation as durably complete. Add symlinked manifest and corrupt-manifest tests; both retain all source paths.

- [ ] **Step 2: Run RED.**

Run: `PYTHONPATH=tests python3 -B -W error -m unittest test_worktrees test_cleanup test_lifecycle_ledger -v`
Expected: new preservation/cleanup APIs missing or current cancelled transition erases needed ownership evidence.

- [ ] **Step 3: Implement durable preservation before removal.**

For each validated FarmBot worktree, record its current HEAD even when clean. Commit changed tracked/non-ignored untracked files using existing `WIP_IDENTITY`, then create `refs/farmbot/recovery/<item-uuid>` in that repository's bare clone. Save the base commit when the worktree is first created in an atomically written `worktrees/<item>/.farmbot-worktrees.json`, keyed by repository, so later handoffs can distinguish the patch from its base. Update managed-directory traversal to recognize only that metadata file and configured repository directories; reject other entries rather than treating files as repositories. Existing worktrees without base metadata retain HEAD/ref and explicitly mark base as unknown; never invent a default-branch base.

```python
saved = trees.preserve(item_id)
ledger.record_cleanup(item_id, stage='preserved', preserved=saved['repos'], handoff=handoff)
# The lifecycle caller may remove only entries durably recorded above.
# Any saved['errors'] are recorded and retried; affected worktrees stay intact.
```

`remove_preserved` revalidates containment, repo mapping, ref resolution and unchanged HEAD/dirty state immediately before removing each worktree. A change since preservation aborts that path. Never follow a symlink out of managed roots. Keep reusable clone/slot folders untouched. Do not use `shutil.rmtree` as a substitute for failed Git removal.

Cancellation records state/token invalidation and resource cancellation atomically. Leave cleanup evidence and process ownership available even though the public work item no longer exposes a usable claim. Repeated cancellation does not change `ended_at` or postpone retention. `blocked` jobs may become cancelled on closure; delivered history remains delivered.

- [ ] **Step 4: Run GREEN.**

Run: `PYTHONPATH=tests python3 -B -W error -m unittest test_worktrees test_cleanup test_lifecycle_ledger test_ledger -v`
Expected: PASS; unique commits survive worktree removal and preservation failures retain files.

- [ ] **Step 5: Commit.**

```bash
git add agent/ledger.py agent/worktrees.py tests/test_cleanup.py tests/test_worktrees.py tests/test_lifecycle_ledger.py
git commit -m "feat: preserve job changes before durable cancellation cleanup"
```

### Task 4: Detect closure through webhooks and bounded status reconciliation

**Files:** Create `agent/lifecycle.py`, `tests/test_lifecycle.py`, `tests/test_config.py`; modify `agent/receiver.py`, `agent/linear_api.py`, `agent/ledger.py`, `agent/config.py`, `agent/service.py`; extend `tests/test_receiver.py`, `tests/test_linear_api.py`, `tests/test_service.py`. `tests/test_config.py` is new; existing configuration coverage also lives in `tests/test_service.py`.

**Interfaces:**
- Add `LinearAPI.issue_status(issue_id) -> dict` with `id`, `status`, `status_type`, `archived`, `delegate_id`, `updated_at` (Linear's `updatedAt`, normalized timestamp).
- Add `Ledger.observe_status(status) -> {applied: bool, closed: bool}`; only update already tracked issues and reject older source timestamps.
- Add `Ledger.lifecycle_issues() -> list[str]` for issues with active/waiting/blocked work or unfinished cleanup.
- Add `Ledger.queue_status_check(issue_id) -> None`, `status_checks_due(now, limit=20) -> list[str]`, `record_status_check(issue_id, *, success, next_check_at, error=None) -> None` backed by `issue_lifecycle` metadata.
- Add `Lifecycle(ledger, api, scheduler, *, reconcile_seconds=60, clock=time.time)` with `reconcile_one() -> bool`, `check_before_launch(item) -> bool`, and `close_issue(issue_id, reason) -> list[str]`.
- Add config `reconcile_seconds: int = 60` and pass a dedicated connection/client to the service's lifecycle thread.

- [ ] **Step 1: Write failing webhook and reconciliation tests.** In `ReceiverBase` descendants:

```python
def test_issue_update_uses_issue_envelope_not_agent_identity_fields(self):
    self.receive(); self.receiver.process_one()
    event = {'type': 'Issue', 'action': 'update', 'organizationId': 'org',
             'webhookTimestamp': 100_000, 'createdAt': '2026-09-21T00:00:00Z',
             'data': {'id': ISSUE, 'updatedAt': '2026-09-21T00:00:00Z'}}
    self.assertEqual(self.receive(event), (200, 'accepted'))
    self.assertEqual(self.receive(event), (200, 'duplicate'))
```

Create `tests/test_config.py` with a `unittest.TestCase` that constructs `Config` using nonsecret fixture credentials and checks defaults plus invalid `reconcile_seconds` values `0`, `-1`, `True`, and `"60"`; add retention validation in Task 6. Add signed foreign-organization events, malformed IDs, unknown issues, delayed events following reopening, and a separate webhook secret when configured through a workspace webhook. Keep the existing session envelope's app/client checks unchanged.

In `tests/test_lifecycle.py`, create a real temporary ledger using `LedgerBase` and inject `Mock` API/scheduler:

```python
def test_poll_catches_closure_while_waiting(self):
    from unittest.mock import Mock
    from agent.lifecycle import Lifecycle
    job = self.new_item()
    token = self.ledger.claim(job['id'], worker_id='w')['token']
    self.ledger.await_input(job['id'], token, 'Need a decision')
    api = Mock()
    api.issue_status.return_value = {'id': ISSUE, 'status': 'Done',
        'status_type': 'completed', 'archived': False, 'delegate_id': None,
        'updated_at': self.now}
    scheduler = Mock()
    scheduler.stop.side_effect = lambda item, reason: self.ledger.request_cancellation(item, reason)
    lifecycle = Lifecycle(self.ledger, api, scheduler, clock=lambda: self.now)
    self.ledger.queue_status_check(ISSUE)
    self.assertTrue(lifecycle.reconcile_one())
    self.assertEqual(self.ledger.item(job['id'])['state'], 'cancelled')
```

Parameterize completed/canceled/archive, queued/running/input-waiting/resource-waiting/blocked, and API failure. Test a newer open status followed by an older closed read; never apply the older read. A job already cancelled while the issue was closed remains cancelled after reopening. Preflight failure must not spawn.

- [ ] **Step 2: Run RED.**

Run: `PYTHONPATH=tests python3 -B -W error -m unittest test_lifecycle test_receiver test_linear_api test_config test_service -v`
Expected: missing lifecycle/status APIs and Issue events currently ignored.

- [ ] **Step 3: Implement lightweight reads and durable dirty-issue scheduling.**

```graphql
query FarmBotIssueStatus($id: String!) {
  issue(id: $id) {
    id updatedAt archivedAt state { name type } delegate { id }
  }
}
```

Normalize timestamps strictly; legacy fixtures may omit source versions only on the full `fetch_issue` path. Add `updatedAt` there too. Stale full-detail reads must not overwrite more recent lifecycle status. A missing/deleted/inaccessible issue or malformed response creates a visible retry error, not fabricated closure or permission to launch.

Follow the [documented Issue webhook envelope and authentication](https://linear.app/developers/webhooks). Issue webhook intake persists a deduplicated dirty-issue notification using `(organization, type, issue ID, source updatedAt/action)` plus a payload hash when no update version exists. Signed remove notifications invalidate eligibility pending a fresh read; never start work because an issue disappeared. A signed close event plus successful current closed read leads to cancellation. Webhook receipt returns within five seconds without network/Git/model calls. Use constant-time signature comparison and organization allowlisting; separately configured signing secrets must be explicitly supplied, not guessed from agent credentials.

`reconcile_one` performs at most one bounded network read per call outside scheduler/SQLite locks. On success, store fresh status, invoke `scheduler.stop` for all cancellable jobs on a closed issue, and set the next check to now + 60. On failures use 5, 10, 20, 40, 60 second bounded backoff. An update notification brings its check forward. Fair ordering prevents one failing issue from starving others. `check_before_launch` uses a new status read and requires open status plus delegation for write skills; it defers on failure and records why. Do not freeze Stop behind this network call.

Start the lifecycle loop with its own ledger connection (same pattern as pool), run once at startup, and stop/join it on service shutdown. Add latest status-check time/error to operator status output. Do not introduce any Linear writes or status changes in the reconciler.

- [ ] **Step 4: Run GREEN.**

Run: `PYTHONPATH=tests python3 -B -W error -m unittest test_lifecycle test_receiver test_linear_api test_config test_service test_lifecycle_ledger -v`
Expected: PASS; missing webhooks are recovered and stale observations do not revive work.

- [ ] **Step 5: Commit.**

```bash
git add agent/lifecycle.py agent/receiver.py agent/linear_api.py agent/ledger.py agent/config.py agent/service.py tests/test_lifecycle.py tests/test_receiver.py tests/test_linear_api.py tests/test_config.py tests/test_service.py
git commit -m "feat: reconcile issue closure and cancellation"
```

### Task 5: Stop owned processes before cleanup and gate resumption safely

**Files:** Modify `agent/lifecycle.py`, `agent/ledger.py`, `agent/launcher.py`, `agent/scheduler.py`, `agent/slots.py`, `agent/dispatch.py`, `agent/service.py`; extend `tests/test_cleanup.py`, `tests/test_scheduler.py`, `tests/test_launcher.py`, `tests/test_slots.py`, `tests/test_resume_work.py`.

**Interfaces:**
- Add `Ledger.register_process(item_id, *, pid, kind, identity) -> str`, `processes(item_id) -> list[dict]`, `retire_process(process_id) -> None`. `kind` is `worker` or `batch`; identity records host, creation marker and owned launch/run path.
- Add `Launcher.stop_owned(item_id, processes) -> bool` which proves owned processes are gone, not merely that a signal was sent.
- Add `Lifecycle.cleanup_one() -> bool` advancing Task 3 cleanup records; `Lifecycle.can_launch(item) -> bool` checks status, predecessor cleanup and current reservations.
- Add optional `process_registry` collaborator to `Launcher` for production, retaining standalone test use. Registry callbacks record before ownership is forgotten.
- Add `recovery` to dispatch payload: compact predecessor handoff, repository refs/base/commit and `revalidation_required: true`; never include stale tokens.

- [ ] **Step 1: Add failing cancellation ordering and crash tests.** Extend scheduler doubles with an observed ordering list:

```python
def stop_observer(item_id, *args, **kwargs):
    self.assertEqual(self.ledger.item(item_id)['state'], 'cancelled')
    with self.assertRaises(LedgerError):
        self.ledger.renew(item_id, old_token)
    return True
```

Install this as a worker stop side effect in the existing scheduler fixture and call `scheduler.stop`. It fails because cancellation currently happens after killing. Add real subprocess cases for a worker and its descendants, a batch process, process already exited, reused/unowned PID and service restart with persisted ownership. A fake returned PID alone is never proof that killing is safe.

Add cleanup tests that inject a failure after each durable stage (`pending`, `stopped`, `preserved`, `worktrees_removed`) and reconstruct the coordinator. Assert cancelled worktrees remain if termination, preservation, ledger commit or quiescence fails. Add a waiting fix plus chat interpreter on the same issue: closing it cancels both, while ordinary reply only leaves the fix waiting.

- [ ] **Step 2: Run RED.**

Run: `PYTHONPATH=tests python3 -B -W error -m unittest test_cleanup test_scheduler test_launcher test_slots test_resume_work -v`
Expected: ordering/process registry and crash-recovery tests fail on current best-effort cleanup.

- [ ] **Step 3: Make stop/launch ownership durable and cleanup restartable.**

```python
# Scheduler.stop, before any potentially blocking operation:
ledger.request_cancellation(item_id, reason)
launcher.stop_unsandboxed(item_id)
launcher.stop(item_id)
# Preserve process records until exit is proved; cleanup runs outside this fast path.
```

Record worker identity at spawn and batch identity in `run_unsandboxed` under its existing cancellation lock. Spawn/registration gaps need a durable launch intent: if registration fails, kill/reap the just-spawned child and retain evidence; startup recovery checks any unresolved intent against OS process ownership rather than assuming no process exists. The owning handle/process group and stable identity must match before a kill. If identity is unverifiable, hold cleanup and report the exact gap. Reused PIDs must not be signalled.

Refactor scheduler launch so fresh network status checks and Git preparation occur outside the scheduler's long-held lock. Recheck cancellation under the spawn fence immediately before Popen; cancellation during preparation prevents spawn, and cancellation immediately after spawn still sees the registered child. Apply the existing batch cancellation fence too. Never let a cancelled claim return from `claim`, `renew`, memory mutations or publication CLI calls as valid.

`cleanup_one` uses Task 3 preservation/removal only after process exit and settled resource ownership. Build the 16 KiB handoff deterministically from the last checkpoint, question, Git refs and PR links, truncating prose explicitly rather than identifiers. Store preservation results before deleting each eligible repository. Record missing/partial evidence in the handoff. Replace unconditional `_retire`/`_sweep_worktrees` removal for ended work with cleanup scheduling; waiting jobs remain untouched. Stop cleanup failures must never convert cancellation to delivery.

A waiting job can resume only after its old worker exited and prior resource ownership is settled. A terminal successor can queue while predecessor cleanup is pending but cannot launch until safe; report the wait in status. New worktree paths expose saved refs for inspection; do not auto-apply unreviewed patches. Keep original PR links and let the new worker verify whether work already merged.

- [ ] **Step 4: Run GREEN.**

Run: `PYTHONPATH=tests python3 -B -W error -m unittest test_cleanup test_scheduler test_launcher test_slots test_resume_work test_lifecycle -v`
Expected: PASS; the real-process tests verify descendants are gone before removal.

- [ ] **Step 5: Commit.**

```bash
git add agent/lifecycle.py agent/ledger.py agent/launcher.py agent/scheduler.py agent/slots.py agent/dispatch.py agent/service.py tests/test_cleanup.py tests/test_scheduler.py tests/test_launcher.py tests/test_slots.py tests/test_resume_work.py
git commit -m "fix: fence cancelled jobs and clean up only after safe preservation"
```

### Task 6: Bound retained artifacts without deleting active recovery evidence

**Files:** Create `agent/retention.py`, `tests/test_retention.py`; modify `agent/config.py`, `agent/service.py`, `agent/ledger.py`, `agent/memory.py`, `agent/scheduler.py`, `agent/__main__.py`; extend `tests/test_memory.py`, `tests/test_config.py`, `tests/test_service.py`.

**Interfaces:**
- Add config `retention_days: int = 30`.
- Add `ServiceLock(path)` context manager with an OS-released exclusive file lock (`fcntl` on Unix, `msvcrt` on Windows); refuse a second service for the same ledger regardless of port.
- Add `MaintenanceGuard` with `publication()` and `maintenance()` context managers sharing a process-local RLock. One service lock makes this sufficient for supported online service ownership.
- Add `Retention(ledger, runs_root, memory_root, *, guard, retention_days=30, clock=time.time)` with `tick() -> dict` counts/errors and `expire_one(item_id) -> dict`.
- Add `Ledger.expirable_jobs(before) -> list[dict]`, `compact_job(item_id) -> None`; only terminal, safely cleaned jobs qualify.
- Extend `prune_snapshots(root, runs_root, *, retained_indexes=())` if needed to preserve explicit in-flight references; ordinary callers keep current behavior. Offline admin maintenance must acquire the service lock and refuse a running service, not rely on documentation alone.

- [ ] **Step 1: Write failing retention tests with real temporary directories/fake time.**

```python
from pathlib import Path
import tempfile, unittest
from agent.retention import ServiceLock

class ServiceOwnershipTests(unittest.TestCase):
    def test_second_service_cannot_own_same_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / 'service.lock'
            with ServiceLock(lock):
                with self.assertRaises(RuntimeError):
                    with ServiceLock(lock):
                        self.fail('second owner entered')
            with ServiceLock(lock):
                pass
```

Use `LedgerBase` to create a cancelled job with `ended_at = self.now`, record cleanup through `worktrees_removed`, and create a real run prompt/log. With fake clock at day 29, files remain; at day 30 they expire while job ID/state/predecessor/ref/handoff remain. Repeat the call and assert no duplicate cleanup. Add waiting jobs, active interpreters, unresolved process ownership, held reservations and preservation failures; none expire.

Add symlinked item/attempt/prompt, unreadable directory, unrelated files, and incomplete handoff fixtures. Add a two-thread barrier test where snapshot publication completes but prompt creation has not: maintenance must wait until the reference is written. A terminated service releases its OS lock; a stale lock-file pathname alone does not prevent restart.

- [ ] **Step 2: Run RED.**

Run: `PYTHONPATH=tests python3 -B -W error -m unittest test_retention test_memory test_config test_service -v`
Expected: missing retention/ownership APIs; current snapshot prune is not coordinated online.

- [ ] **Step 3: Implement bounded, conservative cleanup.**

Acquire the service lock before constructing mutable runtime components and hold it through shutdown. Share `MaintenanceGuard` between scheduler launch and retention. Hold publication protection from snapshot creation until the complete run prompt is durably present; release it on launch failure after cleanup of the unreferenced view. Do not hold this lock during a model run or network/Git preparation.

```python
with guard.maintenance():
    # Recheck terminal state, ended_at, process records, cleanup stage and reservations.
    # Validate every selected managed path before deleting any of them.
    # Preserve compact recovery data in SQLite before removing eligible run directories.
    result = prune_snapshots(memory_root, runs_root)
```

Remove retired claim/auth copies once no live worker/resource needs them, even before log expiry. Preserve reservation tokens until the pool completes release. Apply 30-day expiry to detailed run files only for terminal jobs with successful cleanup; service-owned logs outside per-job runs are not in scope. Do not use modification times of arbitrary files as authorization to delete. Bound each tick to one job and run at most once per minute so it cannot monopolize the service.

Compact duplicated issue descriptions and old inbox bodies only when no active/waiting job or in-flight interpreter needs them and complete recovery/provenance is saved. Keep immutable source IDs, latest pending question, compact handoff, request/delivery deduplication keys, PR references, action identities and required publication evidence. If an older local-only conversation cannot be reconstructed, preserve its bounded handoff and explicitly record that raw history expired; never assert full recovery. Do not delete foreign-key parents or issue metadata fields required for current routing. Hydration on the next explicit request fetches fresh issue/session history through Task 2.

After expired run removal, prune only unreferenced generated snapshots using the existing conservative complete scan. Validation/scan errors leave data in place and are surfaced in operator status. Never prune WIP refs, backups, clone objects, Unity slot folders or active shared-memory notes. Offline CLI pruning must refuse while the same ledger service lock is owned.

- [ ] **Step 4: Run GREEN.**

Run: `PYTHONPATH=tests python3 -B -W error -m unittest test_retention test_memory test_memory_cli test_config test_service test_cleanup -v`
Expected: PASS, including publication barriers and no deletion on incomplete retention scans.

- [ ] **Step 5: Commit.**

```bash
git add agent/retention.py agent/config.py agent/service.py agent/ledger.py agent/memory.py agent/scheduler.py agent/__main__.py tests/test_retention.py tests/test_memory.py tests/test_config.py tests/test_service.py
git commit -m "feat: expire ended-job artifacts while retaining recovery history"
```

### Task 7: Rehearse the lifecycle and document the deployed-behavior boundary

**Files:** Create `tests/test_lifecycle_integration.py`, `scripts/rehearse-lifecycle.py`, `reports/2026-09-21-issue-job-lifecycle/report.md`; modify `README.md`, `docs/operating-contract.md`, `docs/superpowers/specs/2026-09-17-farm-linear-agent-design.md`, `skills/chat/SKILL.md`, `skills/fix/SKILL.md`.

**Interfaces:**
- Consume production scheduler/lifecycle/retention APIs from Tasks 1–6; no alternate cancellation implementation in tests.
- `scripts/rehearse-lifecycle.py --runtime codex|fake --output PATH` uses an isolated temporary ledger, synthetic local Git origins, stub Linear, and disposable worker homes. Default `fake`; real model runs require explicit runtime selection.
- Emit a JSON result with case name, old/new item IDs, terminal states, preserved refs, process-exit evidence, resource result, retention counts and limitations. Never include token/auth contents.

- [ ] **Step 1: Write the integration tests before the harness.**

```python
import importlib.util
from pathlib import Path
import tempfile, unittest

class LifecycleIntegrationTests(unittest.TestCase):
    def test_rehearsal_uses_real_processes_and_preserves_cancelled_history(self):
        script = Path(__file__).resolve().parents[1] / 'scripts' / 'rehearse-lifecycle.py'
        spec = importlib.util.spec_from_file_location('lifecycle_rehearsal', script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            report = module.run(runtime='fake', output=Path(directory))
        self.assertTrue(report['passed'])
        self.assertEqual(report['linear'], 'stub')
        self.assertTrue(report['cases']['waiting_resume']['same_job'])
        self.assertTrue(report['cases']['cancelled_successor']['new_job'])
        self.assertTrue(report['cases']['running_close']['processes_gone'])
```

- [ ] **Step 2: Run RED.**

Run: `PYTHONPATH=tests python3 -B -W error -m unittest test_lifecycle_integration -v`
Expected: missing rehearsal module/API.

- [ ] **Step 3: Build the reproducible subprocess rehearsal.**

Implement `run(*, runtime, output) -> dict`. Use fresh private homes, real ledger CLI claims and actual child processes. Stub Linear status and activities; use local Git repositories instead of gameplay repos. Fake worker scripts are created inside the rehearsal output with `sys.executable` descriptors, not by editing product runtime descriptors globally.

Exercise: two ordinary answers leave a fix waiting; explicit continuation resumes the same item; closure while waiting cleans its worktree after preservation; closure while running terminates child processes before removal; reopen alone starts nothing; explicit continuation after cancellation creates a new item with a linked recovery ref; fake-clock expiry removes eligible logs while keeping compact history. Inject failure at each cleanup boundary and rerun. Assert zero real Linear calls and no real Unity launch.

The real Codex variant checks language interpretation separately using current chat skill and stub session activities: ordinary answer, second person's agreement, negated restart, quoted restart and explicit continuation in English/Chinese. Record actual old/new IDs, not just model statements. Remove copied auth files after workers exit. Do not claim Claude execution or live webhook delivery from this offline/stub rehearsal.

- [ ] **Step 4: Run GREEN and full verification.**

Run: `PYTHONPATH=tests python3 -B -W error -m unittest test_lifecycle_integration -v`
Expected: PASS with fresh processes and temporary real Git/SQLite.

Run: `python3 -B -W error -m unittest discover -s tests`
Expected: all tests PASS with warnings treated as errors. Record actual count and elapsed time; do not invent a count from this plan.

Run: `python3 -B scripts/rehearse-lifecycle.py --runtime codex --output /private/tmp/farmbot-lifecycle-live`
Expected: explicit-continuation cases change job state as specified; answer/negation/quotation cases do not resume fix work. If authentication is unavailable, record that exact gap and still complete offline coverage; do not silently substitute fake results.

Run: `git diff --check`
Expected: exit 0 and no whitespace errors.

- [ ] **Step 5: Write current behavior and evidence.** Update trigger/state tables to distinguish answer collection, same-ID waiting resume, fresh-ID cancelled continuation, issue closure, Stop and retention. Document 60-second reconciliation/30-day retention configuration and health/status output, local WIP-ref recovery, preserved-history limits, cleanup-error recovery and webhook subscription setup. Explain that ordinary Issue webhooks require their correct signing configuration; reconciliation works when subscription is unavailable. Keep scopes/status/assignee unchanged.

Record verification in the report, including no deployment performed and tests using stub Linear. Preserve any known gaps explicitly. Update original architecture lifecycle sections without rewriting multi-host plans.

- [ ] **Step 6: Commit.**

```bash
git add tests/test_lifecycle_integration.py scripts/rehearse-lifecycle.py reports/2026-09-21-issue-job-lifecycle/report.md README.md docs/operating-contract.md docs/superpowers/specs/2026-09-17-farm-linear-agent-design.md skills/chat/SKILL.md skills/fix/SKILL.md
git commit -m "test: rehearse closure cleanup and explicit job continuation"
```

## Completion and delivery

- [ ] Confirm each spec section maps to Tasks 1–7; preserve `awaiting_input` throughout.
- [ ] Follow the selected execution skill's final independent review and fix requirements; focus on the five failure classes above.
- [ ] Record review rulings and actual test evidence; fix consequential findings with failing-then-passing regressions.
- [ ] Push a feature branch and create/attach a draft PR under the user's established preference. Do not merge or deploy as part of implementation without the integration instruction.
- [ ] For later deployment: back up ledger, stop idle service, migrate, restart, validate local/public health, enable/verify Issue subscription with the user-authorized access available, and use a designated test issue for live webhook/closure testing.

## Plan self-review

Spec coverage: waiting and successor semantics (1–2), all-participant context (2), current issue/closure detection (4), cancellation races and ownership (3–5), safe preservation (3,5), retention/snapshot safety (6), migration and documentation (1,6,7), end-to-end evidence (7). All five Review Focus items have concrete tests assigned above.

The only deployment-specific dependency is workspace webhook configuration and a designated live test issue. Implementation and stub rehearsals do not depend on changing either. No product code, live state or webhook settings were changed while writing this plan.
