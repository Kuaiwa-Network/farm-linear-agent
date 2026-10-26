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

Unlaunched jobs start by the latest observed Linear priority: urgent, high,
normal, low, then no priority. Ties use the original job creation time and ID.
Issue update webhooks request a fresh read; the regular lifecycle status read
and full issue reads also refresh queued priorities. Older versioned responses
cannot undo a newer priority. Changes take effect after a successful read and
the next admission pass, subject to maintenance, retry delays and worker capacity.
A priority change during launch preflight defers admission to a new ordered pass.
Already launched workers are not preempted, and Unity/server reservation order
is unchanged. Priority alone does not restart work or invalidate its inputs.

This uses existing issue metadata and work-item columns, with no schema migration.
Existing queued jobs refresh on their next successful issue read. Rollback retains
the last saved queue priorities but stops synchronizing later Linear changes.

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
Latest explicit human requests define the current turn, including conflict-only
continuations. Historical unfinished acceptance lists are retained evidence, not
new obligations. The router preserves attributed inbox requests when steering or
resuming the same owner; `current_request` highlights the latest one separately
from historical handoffs. Before new resource tests or delivered finish, an owner
with new attributed input records `request_scope` in its existing checkpoint:
`through_message_id` and a bounded summary of the interpreted scope. Consuming the
inbox, maintenance or restart does not acknowledge it. The host checks freshness,
not semantic correctness; scope interpretation and failure attribution remain the
worker's responsibility. Status questions alone do not erase the latest explicit
work request. Conflict-only work checks actual conflicts, required source/generated
dependencies and affected validation, classifies each failure, and leaves unrelated
historical failures reported without reopening whole-feature acceptance.

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

### Worker startup and unattended queues

Current chat/work launches put the exact claim argv before other guidance. The
first execution-tool call submits that claim before token checks, file reads or
investigation. The launcher archives the previous attempt's token; saved work is
not a current claim. Only an ambiguous result of a submitted claim needs claim
reconciliation. Sandbox, automatic review, delegation and publication checks stay
in force. Prompt ordering does not guarantee tool-service response latency.

The startup watchdog remains 600 seconds per launched attempt; queued work does
not consume that deadline. Unclaimed timeout, exit or lost-process recovery first
fences the exact PID/generation against late claims, then proves all attempts
quiescent using the existing native ownership checks. Uncertain teardown stays
fenced. Three automatic retries wait 60, 180 and 600 seconds; the fourth failed
attempt is terminal and retains normal preservation evidence. Checkpoints, source
and existing reservation generations survive retries. Retry counts persist across
claims and restarts, independently of model-capacity/publication allowances; an
explicit operator retry resets the startup allowance. Running-worker failures,
launch-configuration failures, cancellation and human-input waits are not startup
retries. Admission still refreshes delegation and honors maintenance and scope.

Two startup failures on a host within 15 minutes start a durable admission cooldown
of 180 seconds. Further failures extend it to 360, then at most 600 seconds. Existing
workers continue. After cooldown and settlement of unclaimed workers, one new
worker probes startup; only its successful claim restores ordinary admission.
A failed probe extends cooldown. Cancelled probes cannot permanently stop the
queue. Failures, retries and restored admission are retained in the host audit.

The additive migration creates `worker_startup_recovery`, `worker_startup_control`
and a zero-default `work_items.startup_retries` counter. Back up settled state before
deployment. Older code ignores these fences and controls: rollback requires verified
worker teardown and settlement of pending startup recovery first, followed by an
explicit admission decision. Checking out old code does not reverse this migration.

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
Before changing contract behavior or protocol semantics, the worker reviews each
decision against approved requirements, existing decisions, affected implementations
and repository instructions. New edge cases, compatibility and failure behavior
need their own assessment; approval of a feature settles only the covered choices.
Already-decided entries cite an exact approved requirement or attributed answer.
High confidence means a direct consequence of cited evidence without an unresolved
tradeoff; medium means a real tradeoff with a reasonable alternative; low means
missing/conflicting evidence or unresolved intent, values or experience.

The worker publishes each decision, classification, evidence and reason in a concise
Linear summary before dependent edits. If all entries are already decided/high
confidence and no explicit approval is required, `activity --type thought` publishes
the summary and work continues without a reply or an input wait. The worker's
assessment is never recorded as a person's approval. Ordinary naming and editorial
corrections without semantic changes need no new round.

Medium/low decisions and explicit approval requirements need attributed answers
before dependent contract or implementation changes. The worker first publishes
and completes independent decided/high work, then checkpoints, settles helpers,
releases resources and uses `await-input --reason question` to ask and park. Questions
are grouped by responsible role, low before medium, with recommendations, alternatives,
costs and evidence. An attributed reply resumes the owner; it must acknowledge only
the actual answers before delivery. Silence, elapsed time and general development
authorization do not resolve these questions. Required repository approvals apply
at every confidence level; existing approvals remain usable within their scope.

Missing assets, formal configuration values and access are listed separately:
confidence in deferring an input does not supply it or establish complete delivery.
Check available inputs before asking. Checkpoints retain decisions, evidence,
posted summary references and actual answers; unchanged entries are reused on
resume and only new or changed decisions need publication. Ordinary resource
queueing is not a human-input blocker.

The current work owner posts human questions in an issue comment through the
durable notice outbox; its session carries a pointer. Each teammate can reply
directly under that comment without collecting a combined session answer. After
everyone is finished, any teammate @mentions the bot to request review and
continuation. A human mention or session reply imports accumulated answers in
that outstanding thread before the normal inbox resume. Ordinary replies do not
wake work, and there is no periodic question-thread polling. The import retains
reply authors and comment references. Repeated
reads are idempotent; a newer edit is delivered as another attributed message.
Bots, unknown authors, unrelated comment threads, resolved rounds and terminal
jobs cannot wake the owner through this path. Current issue scope, delegation,
withdrawal, maintenance and launch checks still apply.

Partial replies preserve the question ID, original comment, reply floor and
earlier answers across checkpoint/resume/park. The worker records which questions
remain, and resolves the round only when all have actual answers; receiving a
comment is not automatic semantic approval. Session replies and comment @mentions
remain supported. Existing session-only questions stay answerable that way; an
owner re-asking one under this release gives it a comment thread. Chat and staged
historical jobs keep their existing path. No database migration is required:
thread and reply references use the existing checkpoint, notices, inbox and audit.
Older releases do not import these thread answers; rollback preserves the question
but answers then need to be sent in a session reply or @mention until restored.

This review is a worker instruction and reporting requirement, not an automated
semantic confidence judge. Existing question/claim/delivery fences remain in force;
there is no new CLI, schema or migration. It takes effect for workers launched with
the updated instructions after an authorized release. Rollback removes this guidance
but does not answer or discard existing human questions.

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

A delivered finish of the current owner also checks every PR it lists on GitHub
before the card can move to In Review. Each must be in a configured publishing
repository, on this issue's branch in the owned worktree, based on the default
branch, with its head at that worktree's current HEAD. It may be an open draft,
an open PR a person marked ready for review, or a PR already merged; a PR closed
without merging, or a number GitHub does not know, is refused.
`finish --check-ready` runs the same check so the worker finds a wrong number or
an unpushed final commit before posting the delivery comment; `--check-input`
stays shape-only. Transient GitHub failures get the publication check's bounded
retries. `no_change` deliveries list no PR and are unaffected. Legacy staged
jobs keep their own delivery checks. No schema migration is involved; older
code simply finishes without this check.

A successful single-worker delivery atomically registers its verified `prs` in
the existing `published_prs` table. Workers need not repeat that list under a
special checkpoint key. Preflight, refused delivery and changed-input requeue
register nothing. Registered output attachments are excluded from the issue's
input fingerprint and remain owned after a restart or continuation; other PRs
still appear in the foreign-work report. Arbitrary checkpoint `prs` or historical
delivery prose is not a substitute for current publication verification.

## Worker polling cadence

Current chat/work launch authority and skills instruct workers to wait on an
existing tool handle for 30 seconds initially and 60 seconds after unchanged or
empty output, within the tool's supported limit. Separate resource, dependency
and test-status reads use the same intervals. Meaningful progress resets the
interval to 30 seconds; repeated timestamps or heartbeat notices do not.
Completion, errors and control changes are handled as soon as they are returned.

Earlier renewal, lease, operation and control deadlines take precedence. After
claiming, workers continue normal renewal/inbox checks and inspect maintenance,
cancellation and withdrawal flags. Resource status already renews the claim.
Pending startup claims are waited on, never resubmitted; authenticated commands
still require a successful claim. Helpers retain no claim/resource authority.
The [polling guide](../references/worker-polling.md) describes these boundaries.

This is worker instruction guidance, not a host-enforced timer or a guarantee of
token savings. It adds no CLI, schema, permissions or background polling process;
startup watchdogs, retry delays and resource quiescence checks are unchanged.
It takes effect for workers receiving the updated launch instructions after an
authorized release. Rollback removes the guidance without migrating state.

## Worker context and output

Current chat/work instructions reuse the resume context returned by startup claim
and prefer `issue-context --view resume` for later reads. Resume responses include
`context_view.digest`, a SHA-256 digest of the complete stored context, including
evidence omitted from that projection. With `--view resume --if-changed DIGEST`,
an exact match returns an explicit unchanged receipt with coordination, fingerprint
and inbox count; any change returns the normal resume projection and a new digest.
The digest is distinct from the issue input fingerprint and covers requests,
questions, recovery, project content and historical evidence as well.

Conditional reads require the worker to retain the matching context. Missing or
truncated context requires an unconditional read; stale handoffs and omitted
facts still require full evidence. Default/explicit full views are unchanged.
The comparison reads stored state and does not fetch Linear, consume inbox messages,
renew a claim, check maintenance, revalidate inputs or approve work. Existing live
authority and control checks remain required.

Workers keep large stdout/stderr in private UTF-8 reports, preserve exit status,
and return focused summaries/excerpts with report paths. Routine text reads start
around 2,000 output tokens where supported and expand when needed. Required
instructions, human requests, control flags, failures and approval evidence are
not subject to a hard truncation cap. See [the guide](../references/worker-context-output.md).

The conditional CLI response is deterministic; reuse and output sizing are worker
guidance whose production token savings still require measurement. No context is
deleted and there is no database/config migration. An authorized release is needed
for these changes to reach workers. On rollback, older code ignores the added
response metadata but does not accept `--if-changed`; use unconditional reads with
the matching older worker instructions.

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
process-quiescence requirement; new leases use reservation-scoped command proofs
to retain the main worker. Historical leases require full worker teardown.

An unavailable read-only Docker identity probe at server startup is retried for
up to three minutes, allowing the host watchdog to restart Docker. No Docker
mutation is replayed. Standalone reservations retain their original claim, ports,
identity and startup deadline in `waiting_for_docker` and retry on later pool
ticks while the same owner remains eligible and maintenance is off. Cancellation,
withdrawal, changed generation and expiry fence readiness. Cleanup waits for both
worker quiescence and the original daemon to return; a failed identity probe no
longer permanently holds the slot. Cold-start pool prerequisites are retried by
the service loop. Changed identities, ownership conflicts, corrupt journals and
ambiguous resource mutations still require guarded investigation. Combined
preparation gets the bounded identity-probe retry; exhausted combined preparation
retains its existing recovery path.

The optional Windows Docker watchdog is installed separately from the controller.
Its Windows task checks once a minute and at user logon. It starts only a pinned
host task when the Docker backend is absent, with bounded restart backoff, and
verifies the original data location and daemon identity. It never kills a live
backend, cleans resources or changes issue state. An unhealthy live backend is
reported for investigation. Desktop Docker remains a logged-in-user service.
Private status, restart events and the host launcher's exit records are retained.
See [Docker watchdog operations](docker-watchdog.md) for installation and pause.

No database migration is involved. Settle pending `waiting_for_docker` journals
before rolling back: older releases do not retry that pre-start journal state.

Released or cancelled reservations from an older worker generation can settle
after a finish/requeue handoff, including after controller restart. Cleanup still
verifies the lease's exact Docker identities and ownership and requires the sealed command scope to be empty, or all
recorded worker attempts to have exited for historical/uncertain leases. It preserves the cleaned
journal without changing the successor's claim or reservation. Open reservations
with mismatched generations and reservations ahead of the current generation
remain held. Already-held slots still require
guarded `recover-server-slot` after the controller stops.
Server setup now journals the verified startup Docker identity before its next
Docker call, including combined leases. A failed identity probe therefore retains
the lease needed for normal recovery; controlled preparation errors retain their
specific diagnostic in the audit.

For older standalone reservations with no journal, explicit host recovery requires
a stopped current-generation worker, verified teardown of all attempts, the most
recent settled slot journal's matching local daemon/context identity, and fresh
absence checks for every expected Docker name and ownership/reservation label.
It only releases the reservation when all checks pass; it never deletes discovered
resources, invents a token, or substitutes this path for a corrupt journal. Missing
historical evidence, changed identity, combined leases and uncertain resources stay
held. A separate private absence receipt is retained before the fenced ledger
update. Recovery does not resume the issue or certify tests. No schema migration is
needed; older code can read the released state but cannot perform this recovery.
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

Source Git normalization uses batches of at most 128 non-LFS files and 8 MiB
(an individually bounded larger file runs alone). LFS pointer identity and
materialized-byte checks still cover every LFS file. Each hash batch has a
60-second limit and one automatic retry after a timeout, within a five-minute
normalization budget. Other Git failures and source mismatches are not retried.
Every attempt rechecks clone/worktree trust, and final commit, index, file stamps
and untracked-file checks still reject source changes across batches or retries.
On Windows each Git hash process starts suspended in an owned Job Object;
timeout cleanup drains its launcher and descendants before retry. Uncertain
cleanup refuses continuation. This retries read-only validation, never a native
export. No schema migration or new worker authority is required; deployment is
still an explicit host operation, and existing parked issues need continuation.

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
- `disposable/go-cache` (historical), `disposable/go-tmp` and `disposable/build`:
  attempt-local Go temporary files and disposable build outputs.

The launcher supplies absolute `GOCACHE` at the same item's
`compiler-cache-v1/go-cache`, reusing Go's content-addressed compilation cache only
after every previous attempt is proved quiescent. It supplies attempt-local
`GOTMPDIR` and `FARMBOT_BUILD_ROOT`; arbitrary binaries/build trees are not silently
reused. Item roots never share caches. Go keys compilation by source/toolchain/flags;
service tests retain `-count=1`, so a warm compile cache is no test-result pass.
Native/cgo external libraries still require the toolchain's documented invalidation.
Historical per-attempt caches stay on their existing retention path; the first
launch adopting the item cache can still compile cold. No old build tree is
automatically promoted into the new cache.
Host-controlled path aliases above an item (such as macOS `/var`) remain valid;
links or junctions in the item directory or either cache directory are refused
before cache reuse or creation.
Workers keep these paths instead of creating additional caches. An external build-output layout is used
only where the repository supports it; Client tests requiring in-tree `bin/obj`
keep that layout, whose existing worktree retirement remains unchanged. Logs,
results XML/TRX, screenshots, reports, source snapshots, checkpoints and anything
needed for recovery must stay outside disposable paths under `state_dir`.
Credentials, home settings, session history/databases, claims, process evidence,
ledger history, memory and recovery refs are never retention targets.

The sweep recognizes `compiler-cache-v1/go-cache` and exactly the earlier controller-managed
`<runs>/<item>/server-tests/go-cache` and `server-tests/tmp` paths. Every recorded
attempt in the job must have verified teardown; shared job caches additionally
wait until the newest teardown is older than the grace period. It does not infer
other historical worker scratch/source directories from names or extensions.
Global Go/NuGet/Unity/Docker caches, module sources, Unity slots, maintenance
archives and arbitrary build folders are outside this sweep.

The fixed job-level `STATE_DIR/uploads` download cache is also eligible after
the newest verified attempt teardown ages past the same grace period. Cleanup
reads and pins its bounded v1 `manifest.json`, selecting only downloaded ZIPs
and their recorded extracted members. Member paths must belong to the recorded
archive's extraction directory. Each file must still match its recorded size
and SHA-256 and pass the existing file-age, regular-file and link checks before
deletion. Ambiguous/unsafe manifests hold cleanup; missing manifests grant no
deletion authority. Custom historical download directories are never guessed.

The manifest, resource inventory, hashes and sources remain unchanged. Unlisted
files, changed files, direct non-ZIP attachments, reports and derived comparison
images remain; workers save reports/images in `STATE_DIR/reports`. Empty extraction
directories may remain. `download-uploads` can refetch expired archives and
re-extract members using the preserved manifest and current source allowlist.
The ordinary retention receipt includes the manifest SHA-256 and candidate count
before deletion; batches share the same file budget and job/resume lock as caches.
Existing canonical upload caches qualify without schema or manifest migration.
Rollback stops future ZIP expiry but cannot restore already deleted cache bytes;
they require an authorized refetch while the Linear source remains accessible.

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

## Server service release

New single-worker server and combined reservations create a claim/generation-bound
process scope. `run-server-tests --module MODULE` runs the exact package/test/race
plan from the owned clean server worktree; `run-integration` registers both command
trees in the same scope. These are worker-side commands under the existing sandbox
and automatic review. Controllers never execute worker-supplied commands. Managed
status responses omit usable service environments/ports; only scoped commands get
them. Do not start ad hoc service clients or reuse old endpoints.

Commands accept at most 256 nonempty literal arguments without NUL, with a total
limit of 30,000 UTF-16 units including Windows quoting and the terminating NUL.
Long exact Go test filters are allowed within that bound. Server plans validate
their generated command before admission; execution checks again with the actual
configured executable path. Oversized selections are refused with a specific
command-limit error, without silently broadening or dropping tests.

Argument validation precedes the `starting` record and process creation. A rejected
command therefore creates no uncertain process entry. The server runner may report
teardown complete only when the reservation's existing command proofs are also
quiescent; the run still fails and supplies no test verdict. Once creation is
attempted, incomplete identity or teardown evidence continues to require recovery.
This command-validation change adds no schema migration and does not clear existing
held resources or restart failed jobs. Older code reinstates its per-argument limit.

Every accepted command records `starting` before creation, then its native identity. Windows
uses a nested, non-breakaway Job assigned while the child is suspended. POSIX uses
an owned group within the worker session and pins its leader through cleanup.
Because that group cannot contain children moving to another group, POSIX retains
the existing whole-worker handoff and session proof; it does not use the fast
release path. Observed escaped descendants retain uncertain evidence. Shared
Editors remain outside worker containment. The documented outer POSIX limitation
for unobserved descendants creating new sessions is unchanged.

On Windows, after saving results and next action in a current checkpoint, release
seals the process scope atomically with releasing the reservation. It requires all
registered commands to have successful teardown and fresh native emptiness checks.
It returns `keep_worker: true`, retaining the main claim, PID and generation. New
commands cannot enter a sealed scope. The server pool repeats checks for that exact
released reservation and validates Docker identity/ownership before cleanup. Unity
quiet-state checks remain independent and mandatory for combined leases.

Missing/incomplete process evidence, a lost runner, failed native queries or an
unclean release retain recovery evidence and the existing all-worker teardown path.
POSIX and historical reservations without a process scope return `worker_handoff: true`
and retire the claim; the owner exits and resumes only after all-attempt teardown
and service cleanup. Cancellation/withdrawal never becomes an automatic resume.
Held slots still require the existing guarded recovery. Closing a parent, setting
an empty flag, or releasing the reservation alone is not quiescence.

The additive `resource_process_scopes` and `resource_commands` tables preserve
claim fences, identities and completed/uncertain runs. Existing grants are not
upgraded in place. Deploy matching controller, CLI and instructions after a full
settled pause. Before rollback, settle all scopes/services and all worker attempts;
older workers can disclose endpoints and do not register commands, so mixing them
with active managed scopes is unsupported. Keep journals and the database backup;
checking out old code does not reverse a migration.

## Cooperative maintenance pause

`maintenance-pause --except-issue FARM-1` (repeatable for a selected set) requests
cooperative pause for other issues while the selected claim stays active. Queued
workers/resources are filtered by this same durable exception set; selected
workers can keep or request resources within existing capacity. `resource-status`
renews and returns maintenance, withdrawal and inbox signals in every state,
including none/queued/preparing. Workers act on those signals before more work.
This priority mode always reports `ready_to_stop: false`; it is not a deployment
pause. A different existing maintenance scope cannot silently be overwritten.
Ordinary `maintenance-resume` ends priority mode while retaining any earlier
admission restriction, pending teardown and human-input waits. No completion timer
or automatic resume is created. A subsequent explicit ordinary resume can clear
an older admission restriction as before. Exceptions are saved in the audit with
the maintenance request ID; old code conservatively treats this as full pause.
Settle priority mode and workers before release/rollback.

Ordinary maintenance blocks new worker/resource admission. Existing workers finish or
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

Preservation also records each worktree's exact branch in the controller's
`job_cleanup.result.branches`. A single-worker continuation restores that branch
at its recovery commit, including an original collision suffix, instead of
allocating another suffix. The branch must belong to the same issue, be free of
another checkout, and still match the saved commit; a missing local branch can
be recreated, but a moved branch or missing recovery ref requires reconciliation.
No fetch, reset, history rewrite or automatic main merge is part of restoration.
Cleanup checks both HEAD and branch before removing the saved worktree.

Each cleanup retry refreshes branch records for repositories successfully preserved
on that pass. A detached HEAD clears that repository's stale branch name; failed
preservation and repositories already removed by an earlier partial cleanup retain
their prior branch evidence. Commits, recovery refs and historical snapshots remain
recorded across retries. Existing pending cleanup can recover on its normal sweep
after an authorized release, without editing the ledger or resetting a checkout.
This changes no schema or record format; rollback loses this stale-branch repair.

These records add no schema migration. Older releases ignore the optional branch
map and do not register PRs at finish, so rolling back loses these guarantees.
Historical cleanup without a branch map retains its prior recovery behavior;
already-created suffix branches and unregistered historical PRs are not silently
reassigned from names or checkpoint text. Recover those with explicit host
reconciliation and publication checks, preserving every candidate and report.

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
requires verified reservation-command teardown for managed Windows scopes, or all
worker attempts for POSIX, historical and uncertain scopes. Settle pairs and preserve journals before rollback:
older code cannot interpret companion ownership. See [the reference](../references/client-server-integration.md).

The combined hand-over's thread-local server helper inherits the main pool's
startup-verified Docker identity. Starting services rechecks that identity before
creating resources; it never adopts a changed daemon or endpoint. For historical
combined startup journals that lack this identity, `recover-server-slot` can retire
the hold only after Unity has settled the pair, the current-generation worker and
all its attempts are quiescent, and a prior settled controller journal anchors a
fresh proof that no matching Docker resources exist. The recovery archives the
original journal and a separate proof, preserves the issue checkpoint, and performs
no Docker cleanup. Ambiguous identity, existing resources, changed ownership or an
unsettled pair retain the hold. No schema migration is required.

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

`export-diagnostics --config FILE --output NEW.zip` creates portable, private
evidence using the existing read-only doctor and SQLite snapshot readers. It
does not modify configuration, migrate a ledger, contact Linear, start workers
or change resource ownership. Its output is a new ZIP outside `local_root`;
read-only SQLite may also create WAL/shared-memory sidecars for a stopped ledger.
An optional issue/item selection includes bounded history, even for terminal
jobs; worker log tails additionally require `--include-logs`. Doctor and heartbeat
remain host-wide. The exporter distinguishes its checkout revision from the
service revision recorded in the heartbeat and records missing/truncated evidence.

Only allowlisted log filenames from selected local jobs are read, with regular
file, link/reparse-point, identity and size checks. No config, database, credential
store, token file, upload or arbitrary checkpoint/report path is copied. Redaction
occurs before writing; it covers known secrets and common credential formats but
does not make arbitrary log prose safe for public sharing. Bundles remain private
and require review before sharing. They are diagnostic evidence, not replayable
state, process ownership proof or live-test authorization. This command requires
no schema migration; removing it leaves saved ZIPs untouched. See the
[export workflow](development-workflow.md#portable-diagnostic-exports) for limits
and partial-result exit codes.

Local regression fixtures use temporary state, local Git remotes, fake workers
and stub Linear/Unity/services. They verify flow and safety boundaries but do
not certify business behavior, model autonomy or a real Unity run.
Native Windows process checks must run on Windows. Follow
[the development workflow](development-workflow.md) for a separately authorized
TestBot test on an operator-selected real issue. No deployment or live test is
implied by editing this repository.
