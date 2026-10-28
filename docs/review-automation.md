# Review and merge controller

This is the implemented, opt-in review stage. The approved architecture remains in
[the design](superpowers/specs/2026-10-22-auto-review-merge-design.md). This document
distinguishes working behavior from the remaining rollout and implementation gaps.

## Current behavior

Verified single-worker delivery records the exact PR URLs, repositories, branches,
source commits and generation in the same transaction as `delivered`. Old deliveries
are not backfilled. `chat` and no-change deliveries create no review work.

The service retains its configured general-worker capacity. Review has an additional
three-slot budget, shared by repair attempts and independent read-only reviews.
Setting general capacity to eight therefore permits eleven main processes. Each
review worker has a two-helper concurrency budget; that is not a provider-wide
request limit. Review admission waits for the original author's descendant teardown.
Independent read-only reviews may overlap; repairs wait for all related reviewers
to exit. New issue continuations wait for review teardown. Native Windows Job Objects and
the existing POSIX process ownership checks are used by both launchers.

The controller constructs immutable speculative candidates in queue order:
`M+A`, `M+A+B`, `M+A+B+C`. It resolves conflicts through a separate AI repair role
before independent review. Changing or removing an earlier candidate atomically
invalidates its descendants and fences their attempts. A surviving confirmed
predecessor preserves descendant evidence. External target updates rebuild it.

CI and independent review overlap. Submitting a report does not immediately free
a slot: the worker and its descendants must finish. Once settled, GitHub CI waits
occupy no AI slot. Failed code checks and Important findings queue repairs ahead
of new reviews. Three repair cycles are allowed per PR by default; a successful
third cycle can merge. Infrastructure retries and speculative rebuilds have separate
bounded counters. Missing/running checks wait without an AI slot until the durable
`ci_timeout_seconds` deadline (24 hours by default), then hand off with a configuration
diagnostic. Cancelled, skipped, neutral and unavailable checks never pass. The
controller journals and reruns only GitHub Actions workflows verified against the
required check suite and exact SHA, with a five-minute minimum cooldown and bounded
attempts. External checks without a recoverable workflow hand off. CI infrastructure
recovery never spends a code-repair round.

A repair worker reports local test failures and skips honestly. If a local test is blocked by its environment,
it may leave that verification to a required candidate check only when the trusted policy and applicable
target-base push workflow, or configured dispatch workflow at its workflow ref, establish that the check runs
the same test on the exact candidate. Source-only checks, an evidence-only policy, a check
name alone, or a candidate's own workflow changes do not establish coverage. The repaired commit still needs
its own successful required CI and a fresh independent review. Unknown coverage and human-only decisions
remain grounds for handoff. Evidence policy version 5 fences earlier active evidence; drain active review
runs before deploying this change. Terminal handoffs are never automatically resumed.

The default polling interval is five minutes. Authority reads are cached for five
minutes during polling and repeated before promotion; failed authority reads block
writes while reconciliation and terminal reports can continue. Infrastructure
backoff survives restart and counts consecutive failures of each operation separately.
Successful metadata refresh does not erase an unresolved merge or worker failure.

Free worker slots admit eligible repairs before reviews. Within each role, the
latest observed Linear issue priority wins: urgent, high, normal, low, then no
priority; ties retain PR enrollment order. Each tick refreshes active issues before
admission, and older issue responses cannot undo a newer observed priority. Priority
changes do not preempt workers, rebuild candidates, or reorder repository merge
chains. Speculative depth, dependencies, retry delays and teardown checks still
limit eligibility. This uses existing issue metadata, with no additional migration.

Reports are claim- and snapshot-bound. Findings require causal evidence and
independent verification. Follow-up reviews must answer every prior Important
finding. Nits and pre-existing problems do not start repair cycles. Read-only
reviewers cannot publish or merge, and repairers cannot certify their own edits.
The review CLI exposes only claim, renewal and report submission. Launches withhold
GitHub token environment variables and select an isolated `gh` config directory.
Git publication restrictions also rely on the existing native sandbox and automatic
approval policy: this is not an OS-level guarantee that every host credential helper
is unreachable. Review actual credential reach during the selected-host rollout.
Each review also binds the candidate identities of every PR in that delivery.
A related repair fences in-flight reports and makes prior verdicts stale. Unchanged
repositories retain their exact-commit CI but require a fresh compatibility review;
prior Important findings remain available for explicit, independently checked responses.

Human hold labels, unresolved GitHub threads with human participation, human changes
requested, high risk, changed source heads, changed issue inputs, withdrawal and exhausted recovery stop
automatic eligibility. Human handoff is terminal `delivered / human_handoff`, with
PR/Linear reports and a yellow `待人工审查` label on the Linear issue. The issue
stays In Review. PR notifications do not create status labels. `待人工审查` means
automation has delivered to a human reviewer; `待回复` means AI is waiting for an answer.
Humans own subsequent review, edits and merge. Green checks, human approvals and
removing a hold cannot resume that run. A new explicit request uses normal intake.

The designated merge-mode controller reconciles the issue label, records ownership
before writing it and retries failures. New implementation work or issue closure
clears the managed label; an unrelated chat does not. Observe/shadow/off instances
do not write review labels. Labels `needs-person` and `待人工审查` are status output,
never eligibility gates, even if an old host config lists them in `hold_labels`.
Explicit human holds such as `hold-auto-merge` remain effective.

This change needs no database migration. Existing terminal handoffs remain terminal;
normal issue reconciliation can backfill their Linear label without reopening work.
Remove legacy PR labels only from verified handoff PRs selected for migration;
there is no global label sweep. Rollback does not undo external label changes, and
older code can reintroduce the legacy PR label/blocker behavior.

External bot review findings become independent-review input. Changed bot feedback
fences earlier reports without rebuilding the candidate or discarding same-SHA CI.
Unknown thread authorship is treated as a blocker. A high-risk verdict hands off
before CI failure can launch repairs. Drafts are marked ready only at the merge
frontier after the delivery's review and configured CI have passed.

Configured `sensitive` paths are attention hints for the independent reviewer,
not an automatic high-risk verdict. The controller supplies the matched file,
original path on renames, and rule for both the source PR and integrated candidate.
This includes paths introduced by conflict resolution or repairs. Generated files
still receive this scrutiny. A shared UI component can proceed through ordinary
review, repair, CI and merge when its actual change is routine; a path match alone
does not justify human handoff. The reviewer explains the actual behavior, affected
callers, evidence and remaining uncertainty before judging risk.

Hard gates remain for the built-in CI/deployment/agent-policy paths, configured
`aot` paths, the handwritten-diff size limit, human holds and the existing identity,
dependency and merge protections. Their Chinese handoff reasons identify the
trigger, file/rule or limit and required human action. Review and repair workers
write human-facing reasons, summaries and evidence in Simplified Chinese. A
high-risk reason states the change, concrete consequence, evidence or verification
gap, and exactly what requires a human decision or verification. Submission rejects
high-risk reasons missing the ordered `改动：`, `风险：`, `依据：`, `需人工：` sections
or Chinese risk/action explanations; the evidence still requires independent review.
Notifications
distinguish attempts from received reports, show each repository's repair count,
and do not imply that a pre-review policy gate found a defect.

This changes the meaning of existing `sensitive` configuration; inspect those
patterns before deployment. Evidence policy version 5 fences reports created with
earlier instructions. Active runs with the old policy receive terminal handoff
and require a new explicit delivery; already delivered runs stay terminal. There
is no schema migration or automatic replay. Rolling back to version 4 restores the
earlier repair guidance; version 3 restores categorical domain guidance and older releases can also restore earlier
path gates. Rollback does not undo merges, notifications or delivered handoffs.

## Exact merge backend

`git_ff` supports **merge commits**, including common's commit-ancestry pins. It
atomically advances the PR source and target refs to the already reviewed and tested
candidate. It first proves both expected tips are ancestors of that candidate.
Both updates must be nonempty, and both carry explicit expected-object leases.
The `--force-with-lease` spelling is used solely for compare-and-swap preconditions:
the controller refuses non-fast-forward history before invoking Git. There is no
unconditional force push. A source or target race rejects the entire transaction;
servers without atomic push support cannot use this backend. Workers receive none
of this merge authority.

This also publishes conflict/CI repairs on the real PR at the merge frontier,
without putting unmerged predecessors on its branch early. The exact commit and
tree remain unchanged between review, candidate CI and promotion. Protected target
branches are refused; this backend does not bypass GitHub branch policy.

The controller journals intent first, reconciles uncertain responses before retries,
and requires both target ancestry and GitHub's merged PR state before confirming
completion. GitHub may recognize the merge asynchronously. Partial repository merges
are retained and reported; the controller never automatically reverts them.

Every delivery PR must have a clean routine review and green configured evidence
before any of its PRs merge. Repository dependencies constrain merge order. Pin
rules read `KEY=SHA` files, check membership in the delivery's dependency PR before
merging anything, and verify ancestry on the dependency default branch before the
consumer lands. The independent reviewer must additionally verify that the pin
contains all of the issue's own table changes; a digest is not that proof.

## Remaining implementation and rollout limits

- Review currently targets repository default branches. Deliveries targeting a
  version/release branch use human handoff; the existing version-target publication
  checks remain in force.
- `github_queue` is a **shadow-only merge backend**. Squash and rebase remain
  configured with that backend; their native merge-group review/CI attestation and
  promotion integration are not implemented. The ordinary GitHub merge API is not
  used as a substitute because its source-head check does not fence the target.
  In merge mode, any delivery containing this backend receives a terminal human
  handoff before any repository can partially auto-merge. Shadow/observe mode can
  still evaluate it without publishing.
- One designated controller owns a repository queue. An atomic GitHub tag-ref
  creation records its instance/app and a durable random identity stored in the
  ledger. Network hostname changes do not affect it. Competing
  hosts or state roots fail closed; the existing controller root lock prevents
  duplicate processes in one state root. Automatic forwarding of deliveries from
  another independent instance to this authority is not implemented. Other instances
  can observe/review but cannot mutate this queue. Private ledgers are never shared.
- Repair workers can edit the isolated candidate, run local tests and obtain
  controller-driven GitHub CI. They cannot yet request the general worker's shared
  Unity/server resource bridge. A repair requiring those resources must report its
  verification gap and hand off; it must not start a shared Editor/service directly.
- Revert detection recognizes standard Git revert commit messages on the default
  branch. Reopen detection requires observing the issue completed and later reopened.
  Other regressions can be attributed with `review-incident`; semantic/partial
  reverts are not inferred automatically.
- The implementation is off by default. No production config, webhook, workflow,
  branch protection or service is changed by checking it out. Local Git tests do not
  establish GitHub atomic-push/PR-recognition behavior or live CI coverage. These
  require the separately authorized, selected TestBot rollout described in the design.

## Private configuration

Enable through `auto_merge`, not through a publicly selectable issue skill.
`off` preserves ordinary draft delivery. `observe` records/classifies candidates
without AI or remote writes. `shadow` adds independent reviews without remote writes
or repairs. `merge` permits candidate publication, CI dispatch, repairs, ready
conversion, reports and the supported merge backend, only on the designated instance.

An illustrative **shadow** configuration (replace the example check producer ID
with the verified App ID for the repository):

```json
{
  "max_concurrent": 8,
  "auto_merge": {
    "mode": "shadow",
    "authority": "production-instance-id",
    "workers": 3,
    "depth": 3,
    "repair_limit": 3,
    "retry_limit": 3,
    "worker_seconds": 2700,
    "poll_seconds": 300,
    "ci_timeout_seconds": 86400,
    "repositories": {
      "common": {
        "method": "merge",
        "backend": "git_ff",
        "checks": [{"name": "table-tests", "app_id": 12345}],
        "source_checks": [],
        "sensitive": ["tools/schema-migration/**"],
        "generated": [],
        "dependencies": []
      }
    }
  }
}
```

Names must match configured publishing repositories. Every repository in a delivery
needs a policy. Cyclic dependencies are rejected. `common` requires merge commits.
Farm-Client merge mode additionally requires explicit `aot` path patterns from its
trusted triage policy; generated exclusions only affect size, never path risk.
Default sensitive patterns include CI/workflow and agent policy files. Supply
repository-specific persistence, economy, build/project-setting and security paths
through `sensitive`. Trusted host config controls policy; candidate changes cannot
weaken it. The default handwritten size limit is 1,500 changed lines.

Candidate refs are `refs/heads/farmbot-review/<snapshot-id>`. Required check runs must
report the **candidate SHA**, matching both check name and trusted `app_id`. A green
original PR head or a workflow dispatch response is insufficient. Configure trusted
push workflows for these refs, or `candidate_workflow` plus `workflow_ref`: dispatch
inputs are `farmbot_candidate` and `farmbot_snapshot`. A dispatched workflow must
test that exact candidate and report checks against it, not only against the workflow
ref. `source_checks` are additional ready-PR checks (for example, triage), never a
replacement for candidate coverage. Recheck real job names and producer identities
before rollout. `evidence_only: true` is an explicit no-CI exception and requires
independent review of the delivery's retained verification evidence.

Optional pin policy, on the consumer repository:

```json
{"dependencies": ["common"],
 "pin": {"repository": "common", "path": "config/pb/toolchain.env", "key": "COMMON_COMMIT"}}
```

Use the actual key from that repository. Default merge order is Farm-Contract,
common, farm-hive, farmgui, Farm-Client among configured repositories. Explicit
`dependencies` can customize it; a pin always adds its repository as a dependency.
Do not depend on config object order. Restart a settled service after
editing config so controller and workers read the same policy. Each run hashes only
its repositories' merge/check/risk settings and applicable delivery dependencies.
Changing that evidence policy terminates the old run; tuning capacity, polling,
retry limits, hold labels or circuits does not. Hold labels are always checked live.
Adding an unrelated repository does not invalidate existing delivery evidence.

## Diagnostics and controls

`doctor` and the office monitor expose review queue states, CI waits, attempts,
repair/rebuild counts, candidates, outcomes and the persistent circuit breaker.
The monitor combines process usage with the separate general and review capacities.
Host commands use the selected private configuration:

```powershell
python -m agent.service review-status --config C:\private\farmbot.json
python -m agent.service review-pause --config C:\private\farmbot.json --reason "Investigating regressions"
python -m agent.service review-resume --config C:\private\farmbot.json --reason "Regression investigation complete"
python -m agent.service review-incident --config C:\private\farmbot.json --review-run RUN_ID --event-id EVENT_ID --incident-kind revert --reason "Confirmed regression"
```

On macOS use `python3` and the host's own config path. `review-status` is read-only.
The default circuit threshold is two distinct deliveries with confirmed auto-merges and regressions
in seven days. Duplicate signals do not count twice. Resume clears the pause, not
terminal human handoffs. Maintenance also stops new review admission and merging,
and holds drain readiness until review attempts prove quiescent. Unverifiable process
ownership retains capacity and evidence; never free slots by deleting run records.
The controller checks local worker completion and pending deliveries every five seconds,
and wakes on due recovery backoff without repeatedly polling GitHub. Remote refreshes
remain bounded by the configured poll interval or a local state transition. The monitor
distinguishes worker startup, active review, report submission and teardown; CI is the
last controller observation and may lag GitHub until the next refresh. Successful claim
receipts report CLI main entry time and elapsed milliseconds without the token. Comparing
them to tool-call time separates the period before CLI main (including scheduling, process
startup and imports) from time spent inside it; it cannot identify which earlier step was slow.
Superseded deliveries are also reported, including the inventory of any partial
merges. Confirmed merges remain eligible for incident observation after a handoff or
supersession. Incident polling processes at most ten due deliveries per tick.

Review retention follows `worker_retention` (24 hours by default). After terminal
delivery, the grace period and verified teardown of all attempts for that delivery,
it preserves candidate
objects under `refs/farmbot/review/<snapshot>` and repair work under the existing
recovery refs before removing disposable read/repair checkouts and repair branches.
Remote `farmbot-review/*` refs are deleted only with an exact-SHA lease and repository
authority. Dirty repairs are preserved first; changed remote refs, unknown processes
and pending merge intents hold cleanup. Audit records, reports and process evidence
remain; only disposable runtime caches under `runs/review` are swept. In shadow
mode local sources can retire, but existing remote refs require merge authority.
Unrelated active deliveries do not delay cleanup of terminal deliveries.

## Migration and recovery

The migration adds `review_deliveries`, `review_runs`, `review_prs`,
`review_candidates`, `review_edges`, `review_attempts`, `review_merges`,
`review_control`, `review_incidents`, `review_observations`, `review_identity`,
`review_recovery`, `review_ci_recovery` and `review_cleanup`. Existing work item
states are not converted. Claims and secrets are never stored in reports; attempt
tokens live in their private state directories and hashes in the ledger.
The candidate and attempt records include `review_inputs` bindings; upgrading an
intermediate development database adds these nullable columns, and verdicts without
the bindings cannot authorize a merge. PR rows also gain `bot_feedback`; new review
bindings include its digest. Old bindings require fresh review. Earlier development
versions' whole-config policy hashes cannot authorize current evidence and hand off.

Before rollback: stop new intake, pause review, drain both worker pools through
verified containment, reconcile every pending merge against Git and GitHub, and
retain a consistent database/state backup. Keep candidate commits, source refs,
review reports, merge journals and process teardown evidence. Older code ignores
these tables but cannot safely resume active runs. Checking out older code does not
undo the migration or remote ref changes. Do not delete/transfer the repository's
`farmbot-review-authority` tag until the old authority's queue and uncertain merges
have been reconciled by an operator. An earlier hostname-based authority marker
does not match the new durable identity and requires that explicit reconciliation;
it is never silently adopted or overwritten. Never clone the ledger into a second
live controller: it would copy the queue identity along with the private state.

Offline coverage uses temporary SQLite databases, fake APIs/workers and local Git
remotes, including spaces/Unicode Windows paths, source/target races, invalidation,
third-cycle success, CI slot release, report replay and uncertain process teardown.
For the full suite, install the pinned optional image dependency from
`requirements-ui.txt` in an isolated Python environment using its required hashes.

Focused verification from the repository root, with the configured Python executable
on Windows (`python3` on macOS):

```powershell
python -m unittest discover -s tests -p 'test_review*.py' -v
python -m unittest discover -s tests -p 'test_service.py' -v
python -m unittest discover -s tests -p 'test_publication.py' -v
```

On native Windows also run `python -m unittest discover -s tests -p
'test_windows_workers.py' -v`. The review journeys include a real contained worker
using the private claim CLI and a controller merging speculative candidates through
an actual temporary Git remote. Neither establishes live GitHub rollout readiness.

### Development validation, 2026-10-27

Measured on native Windows with Python 3.13 and an isolated environment containing
the repository's pinned Pillow dependency:

- `test_review*.py`: 59 tests passed, including real Git and contained-worker journeys.
- `test_windows_workers.py`: 10 native Windows process-containment tests passed.
- Focused service, publication, worktree, single-worker, configuration, dispatch,
  skills, heartbeat, doctor and monitor checks passed. POSIX-only cases were skipped
  where applicable; these results do not establish macOS execution compatibility.
- `python -m unittest discover -s tests -v`: 3,408 tests ran, with 69 platform skips.
  This long-lived process loaded fixtures before their Windows UTF-8, proof-return
  and optional-loop expectations were corrected. It reported 11 failures and six
  errors across 16 tests. All 16 passed when rerun against the final source. The
  whole suite was not restarted again after those fixes.
- Python compilation, monitor JavaScript syntax and `git diff --check` passed;
  local documentation links were checked.

No live Linear issues, GitHub PRs, production services or shared Editors were changed.

Before opening the draft PR, the branch was integrated with `main` at `f03370e`.
The post-integration Windows checks passed: 59 review tests, 53 service tests
(two platform skips), 276 configuration/dispatch/heartbeat/skills/monitor/single-worker/
version-target tests (three platform skips), 32 publication tests, and 10 native
Windows worker tests. Python compilation, JavaScript syntax and whitespace checks
also passed. The full offline suite was not rerun after this integration.

The subsequent Linear-priority admission change passed 65 review tests and all
14 existing priority-scheduling tests on native Windows, with no skips. The review
regressions cover every priority level, FIFO ties, live updates, stale responses,
repairs before urgent reviews, retry eligibility, non-preemption and preserved
merge order. Commands: `python -m unittest discover -s tests -p 'test_review*.py' -q`
and `python -m unittest discover -s tests -p 'test_priority_scheduling.py' -q`.

The review-feedback validation ran 287 focused tests on native Windows / Python 3.13:
284 passed and three platform-specific cases were skipped. This comprises 88 review
tests, 12 environment tests, 32 publication tests, 53 service tests (two skips), and
102 configuration, dispatch, priority, Windows-worker and worker-retention tests
(one skip). Commands used `python -m unittest discover -s tests -p PATTERN -q` with
`test_review*.py`, `test_environment.py`, `test_publication.py`, `test_service.py`,
`test_config*.py`, `test_dispatch.py`, `test_priority_scheduling.py`,
`test_windows_workers.py` and `test_worker_retention.py`; the last five patterns were
combined in one unittest suite. Compilation and `git diff --check` passed. The full
offline suite was not repeated for these fixes, and no live rollout was performed.
