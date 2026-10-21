# Operating contract

This is the implemented contract for the single worker rollout. Historical stage,
UI receipt and comparison behavior is archived in
[the previous operating contract](legacy/operating-contract.md); it is not a
requirement for new work.

## Issue workflow status

The controller moves a delegated issue to **In Progress** after successfully starting
its first issue-work worker, and to **In Review** after the root job passes its
existing delivery checks and records `delivered`. This covers the current single-worker `work` owner and retained legacy root write jobs. Queue admission, chat replies, internal
presentation-task completion, blocked/failed/cancelled outcomes and a completion
preflight do not move the card to review. A finish that detects changed requirements
and requeues work does not request review either.

Each transition resolves the named `started` state in that issue's own team; review
and progress are never selected by type alone. The team must already have unambiguous
states named `In Progress` and `In Review` (case-insensitive); missing/ambiguous states
are reported as sync failures, and no state is created. Before writing only `stateId`,
the controller rechecks that the issue is open, unarchived and still delegated to this
app, and that the local transition has not been superseded. Workers retain no direct
status-changing authority. Assignee and other issue fields are untouched.

Transitions are saved atomically with worker registration and successful delivery
in the additive `issue_workflow_updates` table. Linear failures are audited and
retried by lifecycle reconciliation, including after delivery and controller restart,
without failing workers or blocking launch preflight. A confirmed transition is not
continually reapplied; another attempt in the same generation preserves later human
status choices. A new generation or delivery requests its own transition. Obsolete,
closed and withdrawn requests are skipped. Existing historical jobs are not backfilled.
Older releases ignore this table, so settle pending updates before rollback; checking
out older code neither reverses Linear status changes nor removes saved requests.

## One issue, one main worker

Delegated write work uses `work`, with execution contract `single-worker-v1`.
Bug fixes, features and UI changes have the same owner. `chat` remains read-only
investigation and conversation. New service intake supports these two profiles;
old skill names remain readable for historical recovery and offline replay.
Only `skills/chat` and `skills/work` are loaded by the current registry. Retired
profiles and procedures live under `skills/legacy` and `references/legacy`, and
are loaded only for explicit offline replay. Chat has a separate current launch
authority and concise CLI; it does not inherit the old staged worker prompt.
Current chat/work intake does not preselect a client reproduction target or append
the legacy target/environment line to its acknowledgement. This also applies to
mentions and replies on chat-only hosts; no remote client-head lookup is needed.
Existing recorded targets remain intact, and offline legacy replay retains its
target selection. No schema migration or historical activity rewrite is required.
An authorized implementation request routes to the work owner. Retained staged
jobs still require explicit settled adoption; chat cannot silently restart them.

Chat may prepare relevant visual evidence in its private `state_dir`, upload it
through `upload-image`, and embed the returned asset URL in a session response.
This includes asset crops and comparison sheets, which must be labelled accurately
rather than presented as runtime screenshots. Source worktrees remain read-only.
The command validates a bounded, fully decoded single-frame PNG/JPEG from the
item's own state directory, requires the configured ledger and enabled chat
profile, and fences transfers with the live claim and withdrawal checks. It reads
the current issue before uploading: mention/operator chat needs an open issue,
while delegation-authorized chat still requires delegation to the app. Uploads
are audited as `chat_image_uploaded`; they create no UI approval receipt or issue
delivery comment. The worker embeds the URL through its ordinary `activity`
response and retains successful upload evidence for reuse. Pinned Pillow in the
worker Python remains required; no schema migration or new host permissions are
needed. Existing sessions gain this ability only after the updated code and
instructions are deployed and their worker resumes.

The worker receives owned worktrees for Farm-Contract, common, farm-hive,
farmgui and Farm-Client together. It reads repository instructions and handles the
necessary source, UI, metadata, generated files, tests and CI registration.
Changing repositories does not retire the claim or launch another process.
Independent issue owners and chat share the host's `max_concurrent` limit. Each
issue keeps its own worktrees and claim; a resource wait retains its worker seat.
Historical staged writers cannot overlap current owners. Native Codex subagent tools are enabled in the isolated worker config;
the main worker chooses when to delegate independent work. It supplies scope,
owned paths, repository instructions and relevant decisions, coordinates shared
writes and dependencies, and integrates and verifies the results. Helpers follow
the same permissions; only the main worker manages FarmBot claims, shared
resources, human questions, publication and finish. Helpers are not controller
jobs and do not restore durable presentation tasks or repository handoffs.
The main worker settles helpers before parking, releasing resources they use,
handoff or finish, and checkpoints their completed work for recovery.

The worker owns engineering decisions and verification. It starts with the
candidate and affected behavior, follows actual repository test requirements,
and diagnoses failures from the diff and real evidence. A baseline is optional
diagnostic work for an unanswered question. Advancing main, a restart, or an old
unrelated failure does not itself require another merge or test run.
Retained reports may be reused only when relevant code and inputs still match.
Results, skips and limitations must be reported honestly.
The first worker action claims and reads resume context, before investigation.
Issue and current Project Overview attachments use the same authenticated
`download-uploads` helper and private local cache for chat and work. Every download
refreshes the issue's actual project relation and its short summary/rich Overview
body. Only uploads directly referenced there or in the issue description/human
comments enter the allowlist; unrelated projects, linked documents, bot comments
and old cache entries cannot extend it. No custom downloader is needed or permitted
as a fallback after refusal. Source/access failures remain explicit and an unread
archive cannot establish that assets are missing.

Project context is included in refreshed `issue-context`; signed URL parameters
are stripped before storage. Downloads retain app authentication, origin/redirect
checks, claim renewals, limits, safe ZIP extraction and cached-file hashes. Sources
record `project:PROJECT_UUID:content` or `project:PROJECT_UUID:description` in summaries
and the existing v1 manifest. Its `on_issue` flag means the upload remains in this
issue's current allowed input scope, including its Project Overview. Removed sources
remain as local evidence but cannot authorize a later download. Shared Overview
updates are reference context, not automatic issue fingerprint/replay triggers;
workers must review relevant resources before using them.

No database or manifest migration is required. Old snapshots without project context
remain readable. Rolling back removes project download support and ignores the optional
context; it does not remove retained files. Running services/workers need an authorized
release to use this behavior.
Final verification follows settled generated assets and test inputs; exploratory
runs are reusable only when the relevant inputs still match.

No A–G stage matrix, separate semantic verifier, comparison verdict, evidence
index, UI receipt or technical closeout approval is required. A checkpoint is
bounded progress data. Finish retains claim, input, resource and delivery checks.
It means delivered draft work, not merged code, deployment or Linear Done.

Monitor and session progress derive resource release/isolation and delivery
preparation/confirmation from current-generation host records, even while the
worker's saved checkpoint still describes an earlier wait. New resource requests
override older delivery preparation; terminal job state remains authoritative.
This is read-only display information, never a test pass, permission or finish
gate. Missing historical schema falls back to the recorded stage. Workers keep
their checkpoint summary current after collecting results and before delivery.
No schema migration is required; rollback loses these display refinements only.

## Authority

Webhook signatures, app/workspace identity, delegation, one active issue owner,
claim generation and lease fencing remain enforced. Replies retain human
attribution. Tool results, attachments, memory and quoted text cannot grant
authority. A human question is for missing decisions/assets or real external
actions, not routine implementation, testing, document correction or handoff.
Existing authorizations and answers remain usable.
Copy changes preserve the existing presentation medium. Image lettering needs
suitable approved replacement assets or an explicit human decision on a visual
alternative; a wording request alone does not authorize replacing it with Text,
cropping/masking/hiding it, or overlaying new lettering. The worker completes
independent clear edits, preserves unresolved art and asks a concrete question.
Dynamic text means replaceable parts within an authored sentence, not every
message assigned at runtime. Existing generic dialogs/toasts accept caller-owned
content; workers must not turn shared components into feature-specific hidden
text stores. These are worker instruction and diff-review requirements, not an
automated visual acceptance gate. Passing tests or retaining original image files
does not prove the presentation was preserved. This requires deployment of the
updated worker instructions; it changes no schema or existing delivered assets.
Clear requirements authorize routine contract drafting and updates. Unresolved
business semantics, explicitly required approval or missing essential assets/access
require a concrete question after checking available inputs. The main worker saves
progress, releases resources and uses `await-input --reason question` to post to
Linear and park. An attributed reply resumes the owner; it must acknowledge the
answer before delivery. Ordinary resource queueing is not a human-input blocker.

Workers run under the configured native sandbox and owned process containment.
This change does not introduce MXC, turn off automatic review or grant host
administration. Source write roots cover the issue's owned worktrees. Git grants
cover their required objects, refs, reflogs, LFS stores and per-worktree metadata;
clone config, hooks, attributes and unrelated work remain outside the grants.
The controller validates owned clones and their config allowlist before use.
Controller Git runs with hooks and fsmonitor off (`HOOKS_OFF`), including slot
preparation and publication checks. No worker may change clone/global settings
to broaden permissions.

Publication requires a fresh check of the exact configured private repository,
issue branch, remote and current delegation. Workers may create/update draft PRs
on those verified branches. They cannot push protected/default branches, force
push, merge, deploy, change Linear status or assume a test reply permits these.
Main need not move into the candidate merely because publication is later.
Confirmed delivery outbox records remain required. Real unresolved human
questions and changed issue inputs still fence completion.

## Approval-review capacity recovery

When automatic approval review returns the complete `Selected model is at capacity`
failure explicitly confirming that the action was not executed, a current worker
saves that message and its failed action, commits, reports and next step. It settles
helpers, releases resources through the normal quiescence checks, and calls the
authenticated `retry-approval --error-file FILE` command instead of `await-input`.
Only that complete diagnostic qualifies (including Codex's original tool-error
wrapper; whitespace/layout differences are accepted). Safety denials, quoted fragments,
other errors and uncertain execution outcomes retain their normal handling.

The command retires the claim and durably queues the same job after 60, 180, then
600 seconds, sharing the existing three-retry model-capacity allowance. Exhaustion
records an infrastructure failure with work preserved, without creating a human
question. It cannot resume a stopped, waiting, expired, withdrawn or closed job.
Open resource reservations and rejected checkpoints must be settled first.
Scheduler admission still requires verified worker teardown, current delegation,
maintenance/concurrency checks and normal publication verification. Capacity and
publication retries reuse retained worktrees/read checkouts without refreshing their
Git bases. Matching reports and candidates are reused; a retry grants no test pass.

Every resumed action goes through normal automatic approval again. No approval,
model, sandbox, permission or publishing-destination checks are relaxed. This path
does not execute the failed action itself or reinterpret an existing human question.
It uses existing `capacity_retries` and `retry_not_before` fields; no schema migration
is required. Older code understands the queued state and counters but lacks the new
worker command/instructions; settle workers before changing releases. Existing
`awaiting_input` jobs require an explicitly authorized resume.

## Resource ownership

A running owner requests Unity at its clean exact Client commit, then keeps its
claim, PID, generation and worker seat while the host prepares the resource.
Readiness is published to that same claim. No second worker needs admission.
The CLI bridge selects the owned Unity instance for each operation; it preserves
raw tool output and does not retry ambiguous mutations. The worker never edits
slot directories or launches/kills shared Editors.
`unity-test-result` reads a bounded retained report for an exact test job in the
owned ready instance, including failed runs hidden by the plugin's ordinary job
response. It rejects tests predating that reservation and rechecks ownership after
collection. It does not launch tests, release resources or certify acceptance.
Missing results stay unavailable; truncation is explicit. Keep reports private and
release a quiet Editor before PR prose or remote CI waiting. No database migration
or plugin/cache modification is involved; unsupported plugin APIs fail explicitly.

For server tests, request services only when needed, using an explicit scoped
package/test/race plan. The worker runs the actual commands against isolated
loopback services. Local tests that need no services need no server reservation.
Docker administration remains host-owned. Server cleanup retains the existing
process-quiescence requirement; containers can remain reserved for cleanup until
the worker exits. This release does not claim immediate container reuse.
Released or cancelled reservations from an older worker generation can settle
after a finish/requeue handoff, including after controller restart. Cleanup still
verifies the lease's exact Docker identities and ownership and waits for all
recorded worker attempts, including a running successor. It preserves the cleaned
journal without changing the successor's claim or reservation. Open reservations
with mismatched generations and reservations ahead of the current generation
remain held. No schema migration is needed; already-held slots still require
guarded `recover-server-slot` after the controller stops.
The existing one-open-reservation-per-job limit is retained; simultaneous Unity
and managed server reservations are not introduced in this first rollout.

Resource release still requires correct reservation authentication. The host
checks Editor/owned-process quiescence before reuse, retains uncertain state and
recovery evidence, and stops only verified owned processes. Windows Job Objects
and POSIX ownership checks remain intact. Shared Editors stay outside worker
containment. Resource recovery returns the worker to review retained evidence;
it does not automatically enqueue the same test again. Retry limits remain.

FairyGUI `publish-ui` uses the configured pinned native publisher, explicit
package names/IDs, owned clean source, fresh staging, source revalidation,
publisher mutex and owned process containment. The main worker installs the
necessary outputs in its Client and checks GUIDs, bindings and behavior itself.
There is no parallel controller-managed UI task in this rollout.

Monitor distinguishes a live worker waiting for resources, project preparation,
resource readiness and ordinary work. Readiness, stage names and released slots
are never test success. Capacity details remain allowlisted public data.

### Corrected Unity inputs after failed baseline recovery

Unity may generate missing `.meta` files or rewrite existing importer metadata.
The host preserves tracked changes in a recovery Git ref and new files in the
private recovery evidence before restoring source. The worker reviews those
changes, preserves valid GUIDs and checks references when identities change,
then commits the appropriate inputs in its owned Client worktree.

For a detached candidate recovery whose baseline has failed, a new queued Unity
request from the same issue's live current worker can replace the old baseline.
The host rechecks the owned clean exact HEAD, current generation, lease, host,
withdrawal, maintenance and admission scope. Cancelled, stale or dirty requests
cannot provide recovery input. The successor pins the new SHA before becoming
pending, retains the old journal as `superseded`, and gets at most three repair
attempts. Resubmitting a previously selected SHA for the same failure does not
reset its budget. A fresh post-import source/Editor identity check still gates
reuse; the worker reservation is granted separately through normal admission.

The original metadata evidence remains available across successors. Repair never
resumes a stopped or waiting job or certifies tests. An explicitly authorized host
operation during maintenance may select the original issue's clean corrected HEAD
to recover an already exhausted slot without reviving its cancelled request.
Both old and new recovery locks fence this transition and any immediate repair.

No schema migration is required. The successor uses existing recovery and audit
tables plus its private baseline plan. Before rollback, settle pending successor
repairs: older code cannot preserve metadata from the previous baseline when a
successor switches to its corrected target. Keep all journals and metadata refs.

### Unity reload and reconnect

Preparation waits for an existing import/reload to reconnect before establishing
the Console boundary. It archives prior Console entries, clears them once and
requests compilation with `wait_for_ready=false`: the host owns the bounded wait,
not an in-Editor callback that can disappear during domain reload. An idle sample
predating the request is insufficient. A completed compilation or domain reload
at or after the request must be observed before Console and source/Editor identity
checks can certify readiness.

A lost refresh response is reconciled using read-only observations, without
replaying the refresh or clearing the Console again. Missing completion evidence,
compiler errors, dirty source or mismatched identity still refuse handoff. MCP
failures retain the operation and a bounded machine error code without echoing
arbitrary response messages. A first setup recovery waits the configured quiet
deadline for an existing Editor to reconnect before escalating to an owned-process
restart; it does not treat one missing instance sample as proof of a dead Editor.
No schema migration or Unity package/cache edit is required.

## Automatic worker cache retention

`serve` runs a separate `worker_retention` loop. By default it starts a backlog
pass at startup and another pass one hour after the preceding pass completes.
The optional private configuration block is
`"worker_retention": {"enabled": true, "keep_hours": 24, "sweep_seconds": 3600}`.
The two durations must be positive finite numbers; `enabled` must be a boolean.
Disabling it leaves the existing immediate Windows executable retirement intact.

Only these regenerable paths are eligible inside a worker attempt:

- `home/cache/remote_plugin_catalog`, `home/cache/codex_apps_tools` and
  `home/plugins/cache`: downloaded Codex catalogs, tool descriptions and plugin bundles.
- `disposable/go-cache`, `disposable/go-tmp` and `disposable/build`: attempt-local
  Go compiler caches/temporary files and disposable build outputs.

The launcher always supplies absolute `GOCACHE`, `GOTMPDIR` and
`FARMBOT_BUILD_ROOT` paths under that attempt. Workers keep these paths instead
of creating additional job-level caches. An external build-output layout is used
only where the repository supports it; Client tests requiring in-tree `bin/obj`
keep that layout, whose existing worktree retirement remains unchanged. Logs,
results XML/TRX, screenshots, reports, source snapshots, checkpoints and anything
needed for recovery must stay outside disposable paths under `state_dir`.
Credentials, home settings, session history/databases, claims, process evidence,
ledger history, memory and recovery refs are never retention targets.

For older jobs the sweep also recognizes exactly the earlier controller-managed
`<runs>/<item>/server-tests/go-cache` and `server-tests/tmp` paths. Every recorded
attempt in the job must have verified teardown; shared job caches additionally
wait until the newest teardown is older than the grace period. It does not infer
other historical worker scratch/source directories from names or extensions.
Global Go/NuGet/Unity/Docker caches, module sources, Unity slots, maintenance
archives and arbitrary build folders are outside this sweep.

The entire job must have no tracked active worker and every recorded attempt
must have valid teardown evidence. Windows requires matching Job identity,
recorded empty descendants and a fresh native empty-Job query. POSIX requires
matching `posix_session` identity, `exited`/`empty` proof, empty recorded descendants,
an absent leader and fresh readable empty-session/group checks. Older or
uncertified attempts are held; a dead parent is insufficient. The existing POSIX
limit for unobserved descendants that created new sessions still applies. Each
attempt's teardown and each file's last modification/metadata-change time must
be older than the grace period. Windows creation time serves as its second file
age check. Recent files remain for a later pass.

Each batch removes at most 500 regular, singly linked files. Reparse points,
symlinks, special files and uncertain evidence are refused. Windows pins directory
ancestors/evidence and deletes through exact locked file handles; POSIX traverses
without following links and unlinks relative to opened directory descriptors.
Only empty cache subdirectories are removed. A launcher lock serializes batches
with fresh worker creation, including same-job resumes. Retention errors hold
further removal and never prevent ordinary reaping or resource settlement.
Shutdown cancels between entries and joins the loop; maintenance readiness
includes an in-flight retention batch.

Before deletion, an atomic `cache-retention.json` receipt records policy, allowed
roots, time and process/teardown hashes. It is updated with batch status/counts
and cumulative file/byte totals; an interrupted `started` receipt means the last
batch's final count is unknown. Partial failures retain their counts and sanitized
reason. The service emits `worker_cache_retention` events for removed data and
held attempts, and includes the loop in its heartbeat. No ledger schema or state
adoption/migration is required, including for a selected legacy instance. Existing
profile ownership and controller locking still apply. Rollback stops the new
sweep but cannot restore regenerable data already deleted; preserved source and
evidence remain available for rebuild/recovery.

## Cooperative maintenance pause

Maintenance blocks new worker/resource admission. Existing workers finish or
collect an in-flight operation, cancel still-queued requests, stop their owned
test processes and release acquired resources safely. They checkpoint current
SHAs, report paths and the next action, then acknowledge maintenance and exit.
The controller must prove process/resource quiescence before reporting ready.
A stale heartbeat or HTTP health response cannot establish safe shutdown.

Maintenance survives restart until explicit resume. An `--only-issue` admission
restriction also survives restart and completion; no other issue resumes
automatically. Resuming reuses retained worktrees and reports. If cleanup
removed a checkout, controller-owned recovery refs retain unpublished commits
and dirty work snapshots; a successor follows the same issue's certified
predecessor chain, never silently starts over at newer main.

The host may repeat `maintenance-resume --only-issue` for 1 to 32 distinct known
issues. Selection is atomic and refuses live workers or acquired resources outside
the group. Claim, worker launch, both resource queues and Monitor use the same
durable issue IDs. Monitor reports the complete selected list and excludes held
issues from queue positions. Selection does not answer a human question, adopt a
historical job, or change its candidate.

## Migration and recovery

### Combined Client/game-server verification

Acceptance requiring both candidates online may request one combined lease through
`await-resource --resource unity_slot --mode interactive --commit CLIENT_SHA --integration-plan FILE`.
The same worker keeps its claim and seat while waiting; no second worker or stage
handoff is created. One Unity and one full server slot are acquired together under
shared admission. Waiting holds neither resource. Both owned clean SHAs are checked;
the server source snapshot is detached and read-only. Pools start only dependency
services and prepare the owned Editor. Game code and Client automation run inside
worker containment through `run-integration`, with bounded startup/lease deadlines.
TCP readiness is only transport readiness. The worker must assert application
versions and actual scenarios; reports replace no repository test gate.

Fixed idle Editors yield only when waiting server work needs memory. Acquisition
is fenced before close, and ownership/quiescence checks remain mandatory. Busy or
uncertain Editors are never reclaimed. Warmup waits behind server admission, then
resumes when budget permits. A combined request retains or warms exactly one Editor;
surplus idle Editors may yield. Insufficient total pair budget is refused by the CLI.

The additive `integration_leases` table links one reservation to its plan and
companion slot, preserving the one-open-reservation-per-item fence. Recovery retains
both pins; uncertain setup holds resources and journals. The controller creates the
read-only server snapshot during preparation; worker commands only validate it. A
release never lifts a held server half, which waits for verified `recover-server-slot`,
and Unity settlement continues meanwhile. Server cleanup still
requires verified teardown of all worker attempts, including after release while
the continuing worker lives. Settle pairs and preserve journals before rollback:
older code cannot interpret companion ownership. See [the reference](../references/client-server-integration.md).

### Existing execution contracts

The schema adds `work_items.execution_contract`. Existing rows remain
`staged-v1`; fresh work rows are `single-worker-v1`. Historical write jobs do not
launch on the new production path or acquire new resources. Their state,
reports, reservations and audit history are preserved.

For a separately authorized rollout, drain the old service, verify quiescence,
back up its state, and use an explicit `enabled_skills: ["chat", "work"]`
configuration (the same pair is the new default). Keep all required repositories
configured. Missing optional document-reader configuration is reported only when
needed; it does not prevent unrelated bug work from starting.

While maintenance is requested, the host-only
`python -m agent.service adopt-single-worker --config CONFIG --item ITEM --reason TEXT`
converts a settled top-level job explicitly. It checks no open reservation,
certified worker teardown and settled/cleaned historical child tasks. It keeps
the same item, retained candidate, reports, audit history and unresolved human
question. Adoption does not answer that question, resume maintenance or admit
other issues. Old technical waiting gates are archived for the owner to review,
without demanding another approval. Resume only the chosen issue after release readiness is verified.
Use the installed Python executable on Windows, `python3` on macOS.

Old modules remain for reading old state and running historical regression
fixtures. Service construction allows legacy execution replay only with the fake
runtime; there is no production replay CLI flag. Historical commands cannot
reintroduce staged execution for a new-contract job.

Checking out older code does not undo schema or adopted-job changes. Rollback
requires another settled maintenance stop and a state/release recovery plan;
never start old code against new-contract active jobs. A backup must be restored
as one consistent instance, with its ownership marker and resource evidence.
No task state, resource, worktree or report is deleted merely by this migration.
Multi-issue admission adds no schema migration; single-issue audit records remain
compatible. Older releases reject a multi-issue scope rather than admit everything.
Before rolling back to one, settle maintenance and explicitly select a supported
single issue (or restore unrestricted admission only with operator authorization).

## Development and validation

Use an isolated checkout; production revision and state are independent. Keep
credentials and host paths in ignored private configuration. The selected
absolute config path is propagated to every worker; settle and restart before
changing it. Diagnostics use read-only doctor/SQLite snapshots, not a constructed
production Ledger.

Local regression fixtures use temporary state, local Git remotes, fake workers
and stub Linear/Unity/services. They verify flow and safety boundaries but do
not certify business behavior, model autonomy or a real Unity run.
Native Windows process checks must run on Windows. Follow
[the development workflow](development-workflow.md) for a separately authorized
TestBot test on an operator-selected real issue. No deployment or live test is
implied by editing this repository.
