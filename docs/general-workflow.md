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
Client and closing instructions and protection boundaries. General jobs retain
configured kw_ops access. They never merge, deploy, publish designer data or
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
question round. Replies arriving during the pause are never stranded. If all
remaining work needs humans, the parent consolidates pending questions through
normal `await-input`. Task progress never closes the parent conversation; only
the parent posts issue outcome comments and final delivery.

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
FGUI also retains its reviewed source through Client verification and certified
delivery; another UI task cannot invalidate that source during an approval wait.

The parent can enter Client gameplay only after stage D is done or explicitly
skipped and every required task is delivered with certified teardown. It checks
actual artifacts and task evidence before binding components and verifying full
behavior. Its final delivery includes every task's drafts. The old feature E
human UI-ready gate is replaced by this same-issue join; UI merge is not required
for integrating verified changes already on the owned branch.

## Stop, recovery and migration

Stop, withdrawal or parent failure fences/cancels all active tasks. Children certify
their process/resource teardown and remove only their own read snapshots. They
never preserve/remove shared source. Parent source cleanup waits for all children
to settle safely, then preserves branches, recovery refs and pending work using
the existing checks. Questions and partial decisions remain durable after restart.
A terminal task retry requires an attributed human request to the parent and
certified teardown, through `retry-task`; creating another key to bypass a retry
gate is forbidden. A cancelled root gets the existing linked successor, whose
matched tasks retain predecessor evidence and require fresh validation.
An interrupted predecessor's unverified Client changes cannot become a fresh UI
entry baseline, even under a new task key. Preserve the candidate and ask for
recovery/revalidation; never hide that diff or erase it to make a new task start.

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
