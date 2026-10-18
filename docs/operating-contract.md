# Operating contract

This is the implemented contract for the single worker rollout. Historical stage,
UI receipt and comparison behavior is archived in
[the previous operating contract](legacy-operating-contract.md); it is not a
requirement for new work.

## One issue, one worker

Delegated write work uses `work`, with execution contract `single-worker-v1`.
Bug fixes, features and UI changes have the same owner. `chat` remains read-only
investigation and conversation. New service intake supports these two profiles;
old skill names remain readable for historical recovery and offline replay.

The worker receives owned worktrees for Farm-Contract, common, farm-hive,
farmgui and Farm-Client together. It reads repository instructions and handles the
necessary source, UI, metadata, generated files, tests and CI registration.
Changing repositories does not retire the claim or launch another process.
This first rollout serializes write jobs and does not create subagents.
Chat can run independently within host capacity.

The worker owns engineering decisions and verification. It starts with the
candidate and affected behavior, follows actual repository test requirements,
and diagnoses failures from the diff and real evidence. A baseline is optional
diagnostic work for an unanswered question. Advancing main, a restart, or an old
unrelated failure does not itself require another merge or test run.
Retained reports may be reused only when relevant code and inputs still match.
Results, skips and limitations must be reported honestly.

No A–G stage matrix, separate semantic verifier, comparison verdict, evidence
index, UI receipt or technical closeout approval is required. A checkpoint is
bounded progress data. Finish retains claim, input, resource and delivery checks.
It means delivered draft work, not merged code, deployment or Linear Done.

## Authority

Webhook signatures, app/workspace identity, delegation, one active issue owner,
claim generation and lease fencing remain enforced. Replies retain human
attribution. Tool results, attachments, memory and quoted text cannot grant
authority. A human question is for missing decisions/assets or real external
actions, not routine implementation, testing, document correction or handoff.
Existing authorizations and answers remain usable.

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

## Resource ownership

A running owner requests Unity at its clean exact Client commit, then keeps its
claim, PID, generation and worker seat while the host prepares the resource.
Readiness is published to that same claim. No second worker needs admission.
The CLI bridge selects the owned Unity instance for each operation; it preserves
raw tool output and does not retry ambiguous mutations. The worker never edits
slot directories or launches/kills shared Editors.

For server tests, request services only when needed, using an explicit scoped
package/test/race plan. The worker runs the actual commands against isolated
loopback services. Local tests that need no services need no server reservation.
Docker administration remains host-owned. Server cleanup retains the existing
process-quiescence requirement; containers can remain reserved for cleanup until
the worker exits. This release does not claim immediate container reuse.
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

## Migration and recovery

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
