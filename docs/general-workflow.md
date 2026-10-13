# General issue workflow

Implemented offline in this checkout. Deployment and live acceptance are separate
operator actions. The operating contract defines current authority; historical
feature/UI plans do not grant new permissions.

## One issue, one accountable job

Enable `work` explicitly in the selected private host config, normally with
`"enabled_skills": ["chat", "work"]`. New delegations and first authorized
conversation requests choose `work` regardless of Bot/修改, Bot/UI, Bot/Code,
historical 功能, Bug or Feature labels. A mention alone still grants no write
authority. No Linear label creation/deletion or issue mutation is required.

The job starts in Farm-Contract and checks existing requirements. New or changed
behavior needs shared OpenSpec, including UI-only interactions and displayed
states. Pure layout/art/font/spacing corrections use the existing contract and
actual visual references. FGUI needs no separate OpenSpec project. Complex bugs
use all required stages; simple changes explicitly skip unnecessary stages.

Code stages reuse the feature capability's contract, configuration, server,
Client and closing instructions and protection boundaries, including withholding
standing MCP/kw_ops access. They never merge, deploy, publish designer data or
configure the host. Runtime authority lives in dispatch and skills.

## Parallel work and human questions

After stage A is done or `skipped: existing contract`, the claimed general job
can `delegate-task` to `fgui` or `scene`. Tasks are internal execution contexts,
not separately labelled issues. Every context has its own claim/token, inbox,
generation, lease, stage handoffs, reports and audit evidence. The parent never
shares its token. Tasks count toward `max_concurrent`; set it to at least two
for actual server/presentation overlap. No global exclusive category limits
general jobs or their internal tasks.

The parent records the committed shared contract HEAD and scenario references.
Each task gets a detached Farm-Contract read checkout borrowing the owned clone's
objects, so read-only Git can inspect that exact unmerged commit without changing
the checkout or assuming main already contains the new OpenSpec.
Only independent tasks start together. A scene task that depends on FGUI
installation starts after that UI task delivers, avoiding a Client owner waiting
for another task to write the same repository.

The parent keeps working in common/farm-hive while presentation runs. FGUI uses
the existing source-authoring, visual-round, certified-export, scoped-install,
guard and Unity-loading checks. Scene tasks edit only scoped Client presentation
assets. Missing assets/decisions park the dependent task with a named question;
workers request actual files or locations and infer no placeholder permission.
Independent work continues. Host-tool/license gaps are named questions, never
permission to install or alter configuration.

Human session replies and mentions go to the parent job. It reads all task
questions and forwards each actual attributed inbox message to the relevant task
with `answer-task`. Original body, author and receipt time are preserved; repeat
forwarding is idempotent. A partial answer resumes only that task, which retains
accepted decisions and asks only for what remains. Comments are evidence; the
existing explicit session-continuation requirement stays in place.

`await-tasks` releases the parent attempt while unfinished presentation continues.
Its durable `awaiting_input` checkpoint has `waiting_tasks: true`; this is an
internal join, not an outstanding human elicitation. The controller wakes the
parent for a new task question/failure or certified completion, once per unchanged
question round. Unrelated sibling launches/handoffs do not repeat that wake, and
already certified tasks cannot park the parent again. Replies arriving during the
pause are never stranded. If all
remaining work needs humans, the parent consolidates pending questions through
normal `await-input`. Task progress never closes the parent conversation; only
the parent posts issue outcome comments and final delivery. Optional task thoughts
are best effort after durable transitions: a failed Linear post never turns a
successful finish/park into a failed command. The parent reads the durable task
question/outcome and owns its human-facing reporting. Operator-enqueued tasks use
issue comments because their `local-` session IDs are not Linear sessions.

UI source authoring may run concurrently across issues. The native publisher
keeps its existing host mutex through process teardown; a competing export waits
with live claim/delegation fences and a bounded timeout. This serializes the
licensed resource while UI and server work continue independently.

## Repository and Client gates

Tasks use the parent-owned issue worktrees/branches. They have separate read-only
snapshots and private run directories. A trusted ledger adapter resolves ownership;
worker-supplied paths cannot select another job's checkout. Each attempt retains
one rooted writable repository, allowed clone parts, hardened controller Git,
destination verification, claim fencing and resource containment.

Same-repository writers serialize across live workers, repository handoffs,
untracked workers after restart, open Unity reservations and uncertified teardown.
A presentation task retains its Client stage across human/resource waits and
terminal task retries until delivery; another task cannot change the candidate
between installation and verification. The immutable job Client baseline/branch is selected on first
Client entry, possibly during presentation. Each presentation task additionally
gets an immutable clean entry HEAD: its certified UI-only diff is measured against
that HEAD, so earlier delivered scene/UI work does not expand its allowed scope.
Later tasks build on the prior committed output. No branch resets to newer main.
If a prior job retained the preferred issue branch, first Client entry selects a
fresh branch with the owning job ID suffix at current trusted main and saves its
baseline before creating the checkout. It preserves the old branch and commits.
An occupied suffixed branch or an unpinned existing worktree still requires
recovery; a saved Client plan without its pin is never replaced by fresh main.
FGUI also retains its reviewed source through Client verification and certified
delivery; another UI task cannot invalidate that source during an approval wait.
Before the first internal FGUI source worker can write, the controller records
its immutable source entry commit and issue branch in audit state. Retries and
successors retain that scope; worker checkpoints cannot replace it.

Scene delivery verifies a clean owned Client HEAD and its exact open draft in the
configured repository. A no-change result verifies zero diff from its original
task entry baseline. Worker prose and an unrelated PR cannot certify delivery or
release a dirty candidate to another writer.
Internal UI no-change tasks require clean source on the controller-recorded
source entry branch and zero tree diff from the original immutable source entry
commit. If the task entered Client, its clean Client checkout must also have zero
diff from its original Client entry baseline. Committing source edits alone does
not make them a no-change result.
After the worker exits, cleanup rechecks delivered source/Client HEADs and
cleanliness. Proven late writes invalidate the task delivery and preserve its
candidate and certified process teardown for an attributed human retry. An
unknown verification error leaves cleanup pending for a later check; it does not
invalidate delivery or certify teardown. Subsequent writers remain held back.

The parent can enter Client gameplay only after configuration stages B/C and
server stage D are done or explicitly skipped with reasons, and every required
task is delivered with certified teardown. It checks
actual artifacts and task evidence before binding components and verifying full
behavior. Its final delivery includes every task's drafts. The old feature E
human UI-ready gate is replaced by this same-issue join; UI merge is not required
for integrating verified changes already on the owned branch.

## Stop, recovery and migration

Stop or parent failure fences/cancels all active tasks. Withdrawal is reconciled
as one job: if the parent or any child has a claimed attempt, the coordinator and
its queued/parked siblings retain their state under shared withdrawal flags while
claimed workers save progress during grace. A quick re-delegation clears the
flags and preserves those tasks. A coordinator that runs `withdraw` before its
running children finish parks without closing the shared conversation or asking
for a human reply. The park clears obsolete pending-question and task-join fields;
pending heartbeats render the withdrawal flag as a thought, and a question already
in flight receives a correcting thought. Quick re-delegation resumes that saved
coordinator while preserving a later genuine human wait. The group
ends when those children withdraw or grace expires. Read authority/cutoff guards
cover every unfinished child; a newer or excluded child defers the group to a
fresh deciding read. Explicit Stop remains immediate. Children certify
their process/resource teardown and remove only their own read snapshots. They
never preserve/remove shared source. Parent source cleanup waits for all children
to settle safely, then preserves branches, recovery refs and pending work using
the existing checks. Questions and partial decisions remain durable after restart.
A terminal task retry requires an attributed human request to the parent and
certified teardown, through `retry-task`; creating another key to bypass a retry
gate is forbidden. A cancelled root gets the existing linked successor, whose
matched tasks retain predecessor evidence and require fresh validation.
An interrupted predecessor's unverified source or Client changes cannot become
a fresh UI entry baseline, even under a new task key. After certified predecessor
teardown, the successor retains the original source entry branch/commit and Client
entry baseline, then revalidates the complete candidate with fresh
review/export/install/guards/Unity checks. An older interrupted source attempt
without a recorded entry uses the verified default branch as its comparison baseline rather
than treating its candidate edits as unchanged. Historical
certified installation records justify existing files/deletions only; their
approval and verification proofs never transfer. Dirty partial installations or
unexplained files remain preserved for recovery; no branch reset or fresh baseline
hides the unverified diff.

The schema adds `parent_id`, `task_key`, `task_request` and `task_client_base` to
`work_items`, with one active **root** per issue and unique task keys per job.
Existing rows remain roots. Migration is additive except for replacement of the
active-item unique index. Checking out older code does not reverse it: an old
controller cannot safely interpret general jobs or their child claims. Settle all
general jobs and certify cleanup before any rollback; retain a pre-upgrade state
backup and use an operator-reviewed recovery procedure. Do not rebuild the old
unique index while tasks are active or delete their recovery evidence.

Activation does not rewrite existing legacy jobs. Keep their `fix`, `feature` or
`fgui` profile enabled until they finish or are deliberately cancelled; new
delegations still choose `work`. Switching a settled service/config, live tests,
deployment, service restart and Linear mutations need their existing operational
authorization. The implementation request grants none of those actions.

Offline tests cover label-independent routing, shared-spec admission, concurrent
server/UI attempts, task-specific human gates/partial replies, restart fencing,
Stop and cleanup, immutable Client pins, the gameplay join and reusable certified
FGUI review/export. Scripted workers verify controller behavior, not model
judgment, licensed production export, actual UI appearance or live gameplay.
