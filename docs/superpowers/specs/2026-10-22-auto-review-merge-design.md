# Auto-review and merge: design

**Status: approved, amended 2026-10-27; opt-in implementation in development.** See
[`review-automation.md`](../../review-automation.md) for implemented behavior, remaining limits and validation.
The operator approved the speculative integration
queue described below after settling human handoff, conflict resolution, CI waiting and worker capacity.
This supersedes the earlier proposal to serialize the entire review/CI cycle for each target branch.
The original acceptance was on 2026-10-22, including D2 and the common pin rules. Current behaviour is in
[`docs/operating-contract.md`](../../operating-contract.md): workers publish draft PRs and never merge;
the optional controller merge stage is disabled by default. Production behavior is unchanged.

The original design was written against FarmBot main e7d42a6. The October 27 amendment adapts it to the
single-worker architecture and the operator's concurrency requirements. The design draws on
[Anthropic's account of code review](https://claude.dev/blog/solving-code-review-at-anthropic/).
Routine work is reviewed, repaired and merged automatically. High-risk work and exhausted automatic recovery
are delivered to humans, who review and merge; FarmBot does not wait for their approval and then merge for them.

IDs: AP1-AP12 are the principles of §2, G1-G8 the merge gate of §4.5, D1-D14 the decisions of §7. Tags: **[V]**
verified in code at e7d42a6, **[L]** measured on GitHub on 2026-10-22 over `farmbot/*` PRs created since
2026-10-08, **[I]** inference, **[U]** unknown. Those historical measurements and code references have not been
remeasured for this amendment. The normative sections below describe the approved future system, not live guarantees.

The text names no card, person, host or ID, so it can go into the public repository as it is.

## 1. Problem

### 1.1 Observed baseline, 2026-10-22

- **Volume.** In two weeks FarmBot opened 215 PRs in the five repositories and 158 merged. One person merged 88%
  of them. Median time from opening to merge was 3.2 h, p90 30 h [L].
- **Path.** FarmBot publishes a draft; a person marks it ready; the PR triage labeler (Farm-Client and farm-hive
  only) and a hosted AI review bot run on the ready PR; the person merges [L].
- **Thin review signal.** Of 58 merged PRs sampled in Farm-Client, farm-hive and Farm-Contract, 5 drew inline
  findings from the review bot, and 6 got any commit after being marked ready [L]. The merge is mostly a click
  after checks pass.
- **Multi-repository issues.** 56 of 100 issues touched two or more repositories, so one issue usually means
  several PRs that must land together [L].
- **Cross-repository pin.** farm-hive keeps no copy of the designers' config tables. Its
  `config/pb/toolchain.env` pins one common commit and a digest of that commit's tables, and farm-hive's CI and
  release pipeline build from that commit, read from common's Git history. farm-hive's separate check
  `designer-pin-merged` stays red until the pinned commit is on common's default branch, and turns green when
  rerun after common merges. The pin file says common uses true merge commits, so a PR's commits become
  ancestors of its default branch [L]. In a traced bot delivery, farm-hive pinned the first commit of the
  issue's common PR. The PR then merged newer default-branch work, so its head's tables differed from the pin,
  but only by other people's changes; the PR's own one-line table change was in the pin. People merged common
  with a merge commit, the pin check turned green two minutes later, and farm-hive merged after it [L].
- **No enforcement from GitHub.** The organisation's plan offers no branch protection, required checks or merge
  queue for these private repositories [L], and the default branches are unprotected (AGENTS.md). Whatever gate
  FarmBot applies is the only one.
- **Workers may not merge.** The dispatch text forbids it (`agent/worker_dispatch.py:150`) and the work skill
  repeats it (`skills/work/SKILL.md`) [V]. The ban rests on instructions and the runtime's approval review: the
  worker publishes with credentials that could also merge [I].
- **Detection after merge.** Linear's GitHub integration moves a merged card to the team's acceptance state,
  where people accept or reopen it (live check of 2026-09-25). That remains the detection layer.

### 1.2 Goal

FarmBot reviews each delivery with an independent reviewer, fixes the findings that matter and merges the
issue's PRs itself, unless the change is high risk. Human intervention is a completed handoff: record
`delivered` with the reason and outstanding evidence, leave the card In Review, and let humans review and merge.
Unresolved defects after bounded repairs, ambiguous conflicts and exhausted recovery also take this path.
Slow or queued GitHub CI alone remains a durable wait, not a defect or a human handoff.

Success means human merges per day fall to roughly the high-risk share and time to merge falls, while the revert
and reopen rate of auto-merged work stays no higher than that of human-merged work over the same period.

Out of scope: people's PRs, deployment, replacing repository CI, new Linear status policy (the existing
controller transitions and GitHub integration remain), legacy staged jobs and `no_change` deliveries.

## 2. Principles

- **AP1 The change sets the tier.** Deterministic rules and the reviewer's judgment can each mark a PR high
  risk; neither can lower the other's verdict.
- **AP2 The author never reviews itself.** The reviewer is a separate worker with a fresh context and no write
  authority.
- **AP3 The controller merges; workers still never merge.** Merging is a deterministic gate over recorded
  evidence, not a worker's decision.
- **AP4 A verdict binds an integration snapshot.** Record the PR heads, actual base, ordered predecessor
  snapshots, conflict-resolution commits, candidate commit/tree, relevant inputs and policy version. A changed
  input invalidates affected evidence, even if this PR's original head did not change.
- **AP5 Only verified Important findings block.** Nits never block and never start a fix round. Findings in code
  the change did not cause are reported as pre-existing and never block. A regression may manifest on an
  unchanged line; blocking findings must establish causation, not merely cite a changed line.
- **AP6 Repairs are bounded.** Allow up to three AI repair cycles, each followed by validation. A successful
  final cycle can merge. Hand off only if blocking problems remain, not because a counter reached three.
- **AP7 Missing evidence never permits a merge.** CI still running stays pending. Unreadable diffs, invalid
  output, transport errors and unknown merge outcomes get bounded recovery or reconciliation, then a delivered
  human handoff if unresolved. Never infer success from a timeout or blindly repeat an ambiguous merge.
- **AP8 People can always stop it.** A hold label, a per-host switch and a circuit breaker that turns merging
  off after reverts.
- **AP9 Shadow before merge.** The whole path runs and records "would merge" before any merge is enabled.
- **AP10 Parallel preparation, ordered merges.** Speculate on `main+A`, `main+A+B`, `main+A+B+C`; resolve each
  candidate's conflicts before its final review. Merge in queue order. Upstream changes invalidate descendants.
- **AP11 Separate worker capacity from queue state.** Preserve the existing eight worker slots and add three
  review-stage slots. CI waits release AI capacity while retaining durable queue and dependency state.
- **AP12 Human handoff ends automation.** Humans review, change and merge handed-off PRs. A human approval or
  completed CI cannot reactivate auto-merge. Only an explicit new work request can start a new continuation.

## 3. Flow

```text
verified implementation delivery (generation g, draft PR heads S)
  -> early risk screening
       high risk                 -> delivered: human handoff -> humans review and merge
       routine                   -> join ordered repository/target-branch queue
  -> prepare speculative integrated candidate, resolving conflicts with AI
  -> run GitHub candidate CI and independent AI review concurrently
       code/check defects        -> AI repair -> new candidate -> tests and independent review
       CI still running          -> park review work; controller watches GitHub
       changed predecessor       -> invalidate dependent candidates and rebuild
       high risk / unresolved    -> delivered: human handoff -> humans review and merge
       clean, green, current     -> wait for predecessors to land
  -> controller verifies G1-G8 against actual base and candidate provenance
  -> merge in order, reconcile actual result, record delivered auto-merge outcome
```

**Timing.** PR creation, early risk screening and ordinary PR CI can run concurrently. A final correctness
review starts only after its candidate has integrated its declared predecessors and resolved conflicts. Review
can overlap GitHub CI on that immutable candidate. Original PR CI is not automatically evidence for a combined
candidate. Section 4.10 defines candidate CI and section 4.11 defines worker scheduling.

The implementation owner's existing verified `delivered` event is the internal review trigger. The review
lifecycle is separate: its terminal outcome is `delivered`, with disposition `auto_merged` or `human_handoff`.
An AI process exiting during a CI wait is not a terminal review outcome.

## 4. Design

### 4.1 Review worker

- **Skill.** A new `review` skill with a read-only manifest (`writes: []`, `resources: []`) and a new internal
  trigger, `delivery` (`agent/skills.py` accepts only `delegation` and `mention` today [V]). It is `opt_in`, so
  each host enables it separately.
- **Shape.** A durable review run belongs to the verified implementation item and generation. Its read-only
  review attempts and write-authorized repair attempts run sequentially for that issue; it must not introduce
  a second concurrent issue writer. The controller fences attempts, waits for verified teardown and preserves
  current ownership/resource checks when transferring between roles. A parked review run retains its queue
  state without a running process. Human changes supersede its verdict and merge eligibility (§4.4).
- **Launch context.** The issue, its delivery comment, each PR with pinned base and head SHAs, read-only
  worktrees at the integrated candidate snapshots, repair count, earlier verdicts with AI repair responses,
  and each repository's approved `REVIEW.md` where present. Policy is loaded from the trusted configuration/base,
  not a PR's proposed policy changes. The triage labeler and the hosted review bot run only on ready PRs, so they do not
  run before the review (D8).
- **Process**, following the source article:
  1. *Intent.* Read what the issue asked and what the delivery claims. For a bug fix, check that the change
     addresses the cause, not the symptom.
  2. *Hunt.* Several independent finders (native subagents) read each diff against its whole repository and
     against the issue's other PRs: contract, server and client agree, and generated outputs were regenerated.
     When farm-hive changes its common pin, the pinned commit must contain every table change the common PR
     makes on top of the default branch; the pin's digest proves only the pinned commit's own content.
  3. *Verify.* Each candidate goes to at least two verifiers that did not find it. It survives only with a
     majority and file and line evidence. Style, diagnostic and cleanup-only findings are dropped.
  4. *Tag* each surviving finding Important, Nit or Pre-existing.
  5. *Assess risk* per PR (§4.3).
  6. *Submit* the verdict with `review-verdict --input FILE`, authenticated like the other worker commands,
     then finish.
- **Later rounds** review changed inputs and affected interactions together with the AI repair responses.
  They never repost a finding already raised, and name a finding's class so every instance is fixed at once.
  A changed predecessor or resolution can require broader review than the PR's latest source diff.
- The review worker posts nothing to GitHub or Linear itself; the controller does (§4.5, §4.6).

### 4.2 Verdict and validation

```json
{"review_run": "RUN_ID", "snapshot": "CONTROLLER_SNAPSHOT_ID", "repair_cycles": 0,
 "prs": [{"url": "https://github.com/OWNER/REPO/pull/1", "head": "FULL_SHA",
          "risk": "routine", "reasons": ["why this tier"]}],
 "findings": [{"id": "F1", "pr": "https://github.com/OWNER/REPO/pull/1", "path": "src/a.cs", "line": 42,
               "severity": "important", "class": "unchecked null reward", "claim": "...",
               "evidence": "...", "votes": "2/3"}],
 "responses": [{"finding": "F0", "status": "fixed", "note": "..."}],
 "summary": "...", "needs_person": false, "needs_person_reason": ""}
```

`risk` is `routine` or `high`; `severity` is `important`, `nit` or `preexisting`; a response's `status` is
`fixed`, `dispute_upheld` or `dispute_rejected`.

The snapshot ID names controller-recorded immutable provenance; a worker cannot mint its own eligible snapshot.
It includes each repository, target branch, source head, actual base, ordered predecessors and their heads,
candidate commit/tree, resolution commits, dependency inputs, policy version and CI identities. Review reports
record the changed files and causal evidence for each finding; affected unchanged code is valid evidence.

The controller accepts a verdict only when the claim, generation and snapshot are current, its PR set equals
the verified delivery's, each source head still matches GitHub, and every prior blocking finding has a response.
Independent verification must substantiate a finding; a vote count alone is not proof. A rejected malformed
verdict is retried once, then handed off if still unusable. Ordinary stale snapshots are rebuilt, not accepted
or misreported as clean.

### 4.3 Risk

A PR is high risk when any rule below holds; the issue is high risk when any of its PRs is.
Assess both the PR's own changes and its integration/conflict-resolution changes, including interactions with
its predecessors. Reassess risk after repairs. Excluding generated files from a size count does not exclude
them from semantic or security review, and candidate content cannot change the policy used to classify itself.

**Deterministic rules**, per repository in config:

- A path on the repository's always-person list. Defaults: `.github/**` and CI scripts everywhere; build and
  project settings in the client; cluster and persistence code in the server; anything the repository's
  `REVIEW.md` lists.
- **AOT code in the client, whatever the size of the change.** This is hand-written runtime code outside the
  client's hot-update assembly. It compiles into the native binary and cannot be hot-updated, so a bug there
  needs a full client rebuild and resubmission. Editor-only code, tests, generated protocol code and hot-loaded
  data are not AOT. The path list mirrors the client's triage labeler and lives in config; the controller
  applies it itself, because the labeler skips drafts.
- A triage-labeler label meaning lead review, an economy path or AOT code (`aot-no-hotfix`). Where a repository runs
  the labeler, the gate waits for its check on the head SHA after marking the PR ready (G5); a high-risk label
  then sends the issue to a person.
- A hand-written diff above a configured size (D5); generated outputs are excluded from the count.

**Reviewer judgment** marks high: economy changes (grants, costs, settlement, purchases), save data and
migrations, protocol changes that are not purely additive, cross-node behaviour, security and permissions,
changes a revert commit cannot undo, such as data migrations, architecture others will copy, and any change
whose fit with the issue the reviewer cannot confirm.

Everything else is **routine**: most UI and copy work, config-table edits the issue asked for, additive features
and bug fixes confined to one feature.

### 4.4 Fix rounds

- The review stage coordinates all conflict, CI and review repairs. "Author fixes findings" means an AI repair
  attempt, not a request for the human to fix routine defects. Reuse the work owner's retained context and
  existing claim/publication protections, but account for this attempt in the review-stage pool (§4.11).
- Only one role may write an issue's branch at a time. Finish and verify teardown of the repair attempt before
  an independent, read-only reviewer checks its candidate. No worker merges a PR.
- The AI answers every Important finding: fixed with commit/evidence, or disputed with a reason that an
  independent verifier assesses. Nits do not start a repair cycle. A required check exposing a code defect
  enters the same repair process; a provider outage uses infrastructure recovery instead.
- Initial integration resolves ordinary merge conflicts before final review. Each subsequent repair cycle
  includes edits followed by affected tests and independent review. The default is three cycles; passing on
  cycle three is eligible to merge. Persist counts across worker/controller restarts. New candidate IDs or CI
  runs cannot reset a defect's repair budget.
- GitHub queue/run time is not a repair attempt. Predecessor invalidation without a new defect does not consume
  the defect repair budget either. Bound integration churn and infrastructure retries separately; report and
  hand off work that cannot converge rather than allowing an endless rebase/review loop.
- Actual worker continuations retain generation/input fencing and the existing issue status transitions.
  Internal candidate rebuilds are controller state changes, not fabricated human requests or approvals.
- A person's new work request, changed issue inputs, hold or withdrawal takes precedence and invalidates
  pending merge eligibility. Preserve previous findings and counters when the underlying work is unchanged.
  After human handoff, a new explicit request starts an ordinary continuation; no automatic return edge exists.

### 4.5 Merge gate

The controller merges the issue's PRs only when every condition holds at the moment it merges.

- **G1** Auto-merge is enabled for this host and for every repository in the delivery, the host is not in
  shadow mode, and the circuit breaker is closed.
- **G2** The issue is open, still delegated to this app and not withdrawn; the delivery is the current generation
  with no successor; no human question is unanswered; neither the card nor any PR carries the hold label (D6).
- **G3** The latest valid verdict covers the source heads and integrated snapshots of every PR in the delivery;
  no Important finding is open; every PR is routine; `needs_person` is false. No handoff is terminally recorded
  for this run. A human approval never substitutes for this gate or revives a handed-off run.
- **G4** Each PR is open, in a configured publishing repository, on this issue's branch, based on the default
  branch, with its current head bound to the validated snapshot, and GitHub reports it mergeable without
  conflicts. The candidate must be at the merge frontier, with all predecessors confirmed merged and the
  actual resulting content covered by its evidence (§4.10). Re-read the source head and actual base before
  submission. `PublicationVerifier.verify_pr` supplies existing destination/ownership checks, not proof of
  speculative integration or current-base correctness.
- **G5** The controller has marked each PR ready, because the triage labeler skips drafts [L]. Each of the
  repository's required checks (D3) then completed successfully for their declared source/candidate inputs,
  from the configured check producers. Missing, cancelled, skipped or stale required evidence is not a pass.
  While GitHub reports queued or running, persist a CI wait and release the AI slot. A warning deadline may
  flag unusually slow CI, but elapsed time alone does not deliver it to humans or count as a repair. A
  repository configured with no required checks relies on the worker's verification evidence, and the config
  must say so explicitly. A check that only waits for another repository's merge, such as farm-hive's
  `designer-pin-merged`, is not required; the controller verifies its condition itself (G6).
- **G6** PRs merge in dependency order (default: Farm-Contract, common, farm-hive, farmgui, Farm-Client), while
  respecting each repository's queue order. Detect dependency cycles before admission instead of acquiring
  locks in opposite orders. Each
  merge call passes the expected head SHA, which GitHub's merge endpoint accepts so that a moved head refuses the
  merge, and uses the repository's configured method (D2). When a farm-hive PR changes the common pin:
  - before merging anything, the controller checks that the pinned commit is one of the commits of the issue's
    common PR, or is already on common's default branch when the issue has no common PR. Otherwise the issue
    needs a person. Whether the pin carries all of the PR's own table changes is the reviewer's check (§4.1),
    because a plain comparison of tables fails once the PR has merged newer default-branch work (§1.1);
  - after merging common and before merging farm-hive, it confirms through GitHub that the pinned commit is now
    an ancestor of common's default branch.

  It neither reruns nor waits for `designer-pin-merged`. That check's result on the PR stays red from before
  common merged; its run on every push to farm-hive's default branch confirms the pin again after the merge [L].
- **G7** Journal the intended snapshot and merge method before the call. If the result is ambiguous, reconcile
  GitHub state before retrying. An accepted/enqueued request is not proof of a completed merge. If a merge
  fails after earlier ones succeeded, stop dependent merges; bound retries for confirmed transient failures,
  then deliver an exact merged/unmerged inventory and recovery evidence to humans. Do not automatically
  revert earlier merges. Merges across repositories cannot be atomic on GitHub [I].
- **G8** After merging: an audit record, a Linear comment with the tier, rounds, findings fixed and checks, and a
  PR comment with the verdict summary. Branches are kept.

**Stale base.** The original proposal accepted an old base; this amendment removes that assumption. Head-SHA
fencing alone does not fence a target-branch update. Validate the actual base and integrated result, invalidate
stale evidence, and prove the final merge mechanism cannot silently land an untested result (§4.10).

### 4.6 When a person is needed

The controller posts a Linear comment, in Chinese like FarmBot's other replies, and a marked PR comment, both
giving the reason, the open findings and the PR links. The PRs get a `needs-person` label and the card stays In
Review. Persist the terminal review outcome `delivered`, disposition `human_handoff`, with all unresolved
findings, conflicts, failed/missing checks, reviewed commits and any partial merges. Release its automatic
queue eligibility and active attempts. This is completed bot work, not `blocked` or an approval wait.

People review, make further changes and merge. Their approval, a later green CI run, removal of a comment or
elapsed time cannot automatically resume this run or merge its PRs. An explicit new work request can start a
new continuation. Rebuild speculative successors without the handed-off PR; genuine dependents wait until
the dependency is actually merged or the requirement is explicitly changed. Unrelated work can continue.

### 4.7 Identity

The controller merges with the host's existing GitHub credential, so merges show that account as they do today
[L]. GitHub does not let an account approve its own PR, so the review is posted as a comment review, not an
approval. A dedicated machine account would make bot merges distinguishable, but it is not required (D7).
Keep merge credentials out of the read-only reviewer. A separate identity alone does not enforce the author's
merge prohibition: implementation must inspect actual credential reach and preserve or strengthen the
controller publication boundary. Do not execute untrusted candidate scripts in a credentialed merge executor.

### 4.8 Records, measurement and circuit breaker

- **Schema.** Additive durable records for review runs, role-specific attempts, repair responses, candidate
  snapshots, queue order/ownership, predecessor edges, CI waits and merge intents/results. Record `auto_merges`
  with issue, PR, source/candidate/merge identities, tier and time. Distinguish pending CI, stale candidates,
  rebuilding, merge submitted, merged and terminal human handoff. Table names are implementation choices.
- **Recovery.** Reconcile pending merges, candidate validity, remote CI and controller ownership after restart
  before admitting work. Persist pool accounting and attempt fences. Older code cannot safely resume these
  active runs merely because tables are additive. Settle workers and merge intents before rollback; retain a
  consistent state backup, audit and candidate provenance. Checking out older code does not undo migration.
- **Visibility.** `doctor` and the monitor show both worker pools, queued/parked review runs, candidate
  dependencies, CI waiting time, invalidation reasons, handoffs and recent auto-merges.
- **Measurement**, read-only: auto versus human merges, time to merge, repair cycles per delivery, findings fixed versus
  disputed, reverts of auto-merged commits (a default-branch commit that reverts a recorded merge SHA) and cards
  reopened after an auto-merge. Also measure reviewer utilization, same-branch merge throughput, speculative
  rebuilds, discarded CI minutes and time waiting for repair capacity. Speculation is a throughput tradeoff,
  not a promise that no integration work repeats.
- **Circuit breaker.** Two reverts or reopens of auto-merged work within seven days (configurable) switch the
  host to shadow mode and tell the operator. Only the operator switches it back.

### 4.9 Evaluation

Past deliveries that people later fixed or reverted become review evals. A change to the reviewer's prompt or
model is evaluated for regression recall, false findings and repair convergence before adoption.
Create the initial evaluation set before enabling live merges. Include clean changes, realistic conflict
resolutions, security regressions and faults that manifest in unchanged consumers.

### 4.10 Speculative integration queue

Use one authoritative queue per configured repository and target branch, shared across all instances that
can merge there. Separate per-host queues are insufficient when TestBot and production share repositories.
This does not mean sharing their private ledgers, credentials or resource roots: use a designated queue
authority or a proven shared coordination service with per-instance ownership and fencing.

Start with a configurable look-ahead depth of three. For a queue A, B, C at base M:

| Candidate | Integrated content | May be reviewed/tested concurrently? | May merge? |
| --- | --- | --- | --- |
| A | M + A | Yes, once conflict-free | When all gates pass |
| B | M + A + B | Yes, once conflict-free | After A lands and evidence is still valid |
| C | M + A + B + C | Yes, once conflict-free | After A and B land and evidence is still valid |

These are immutable speculative snapshots, not promises that predecessors will merge. Keep unmerged
predecessor code in controller-owned candidate refs/worktrees, separate from the original issue branches, so
a human cannot accidentally merge another issue merely by merging B's original PR. Each resolution is
attributed to the affected issue and anchored to its exact input snapshot. Only the controller can publish
candidate refs into a narrow configured namespace; ordinary worker publication grants remain issue-scoped.

The repair role resolves conflicts first. Reviewers then assess each PR's incremental change in the full
integrated context and relevant interactions. Candidate CI and review may overlap. Do not review a checkout
being edited by a repair worker; fork a new immutable snapshot for every mutation.

If A is repaired, withdrawn, reordered or handed to humans, invalidate dependent B/C candidates, verdicts and
CI associations before any merge can be submitted. Rebuild them from the new ordered prefix. A late green CI
event for the old snapshot remains historical evidence and cannot revive it. Keep independent branches of
the dependency graph eligible. Rebuild preparation gets lower priority than work that can unblock the merge
frontier; limit look-ahead so repeatedly failing A does not create unlimited speculative work.

**GitHub CI.** GitHub runs the workflows and controls their execution time. FarmBot observes authenticated
events and periodically reconciles actual run/check state, so missing or duplicate events cannot lose work.
CI failures produce a code repair or a bounded infrastructure retry; queued/running work parks. The controller
cannot substitute its own green status for an absent required result.

Existing PR workflows may test only a PR head or its ordinary merge ref, not M+A+B. Before enabling speculation,
configure trusted workflows to check out and report on the exact candidate snapshot, using native merge-group
events where available or explicitly supported candidate refs/dispatch inputs. Retain repository secrets and
workflow permission boundaries. Record the tested checkout and relevant input versions, check producer,
workflow/run/check identities, attempts and conclusions. Do not reuse CI by matching a display name alone.

**Promotion.** After A lands, reconcile its actual merge result and reconstruct B's proposed result against
the actual base. Squash/rebase may change commit identities even when source trees agree; a common merge
commit may preserve ancestry. Do not assume identical files imply identical verification inputs: version
generation, history-dependent checks, dependency pins and build inputs may differ. Materializing a conflict
resolution onto B's original branch also changes its source head. Persist and validate that mapping; rerun
affected tests and review whenever prior evidence does not cover the materialized candidate. Never silently
attach another candidate's green checks to a new head. The default is to rebuild/revalidate when uncertain.

Before live rollout, choose and test a merge backend that preserves these guarantees for each repository's
configured merge method. Native queue availability and protections must be rechecked; the October 22 plan
measurement is historical. A controller lock coordinates participating bots, but cannot fence an outside
writer on an unprotected default branch. A pre-merge read plus an expected PR head does not close that race.
Prove server-side enforcement or an equivalent atomic publication mechanism before claiming that the exact
validated result is guaranteed to land. Keep a repository in shadow while this requirement is unresolved;
do not silently weaken it to the original stale-base policy.

### 4.11 Worker capacity and ownership

- Keep the existing eight general implementation/chat worker slots; add three review-stage slots. These are
  separate admission budgets, for at most eleven main worker processes. Review helpers must have an explicit
  bounded budget; eleven main processes does not imply only eleven provider requests including subagents.
- The additional pool runs integration/repair attempts and independent review attempts, with distinct
  contexts, permissions and claims. A repairer cannot certify its own edits. It does not require three
  permanently running reviewer processes or one permanent process per PR.
- Save the verdict and release the AI slot while GitHub CI runs. The durable review run, queue position and
  dependency edges remain. Other eligible speculative candidates in the same repository, or candidates in
  other repositories, may use the free slot. The approved design is not the earlier fully serial pipeline.
- If all three slots are busy when CI fails, queue its repair with priority over new speculative reviews;
  do not steal implementation capacity or assume an immediate worker. Bound attempt duration and safely
  checkpoint at normal boundaries so completed or yielded work makes capacity available.
- Keep one current writer per issue and preserve native process containment, claim fencing, source write
  roots and resource quiescence. Reviewers read source and reports without owning Unity/server slots. A repair
  that needs runtime verification uses the existing resource admission and release protocol, not a bypass.
- Before rollout, validate host memory, model concurrency and shared resource capacity for the combined
  budget. This design changes no running host configuration by itself.

## 5. Rollout

1. **Offline.** Unit and journey tests with temporary ledgers, local Git remotes and fake GitHub CI/events.
   Extend `StubGitHub` to record and script writes, pagination, delays and ambiguous outcomes. Exercise the
   merge backend with local remotes before touching a real default branch.
2. **Evaluation and TestBot shadow.** Evaluate historical failures and clean controls; use TestBot only on
   operator-selected real issues with its isolated development profile. Observation-only shadow runs do not
   modify PRs, start repairs or merge. Candidate-ref writes and real CI dispatches are a separate enabled
   capability in the authorized test scope, not implied by the word shadow.
3. **Production shadow.** Start with about a week and enough representative deliveries to assess false
   findings, missed failures, speculative rebuild cost and throughput. Observe first; enable automatic repairs
   separately. Compare would-merge decisions against what people found and changed, investigating differences
   rather than treating every human edit as a bug the reviewer should have predicted.
4. **Selected live merge.** With operational authorization, validate a chosen trivial delivery and a small
   same-repository sequence. Confirm CI checked the integrated snapshots, the controller merged in order and
   an upstream repair invalidated descendants. Prove restart/ambiguity handling and the target-branch boundary.
5. **Gradual automation.** Enable routine categories per repository only after its checks, workflow triggers,
   merge backend and recovery path are verified. Keep high-risk/manual work delivered to humans, retain the
   circuit breaker and expand based on measured results. Approval of this design does not change production
   configuration, deploy code, restart services or choose live issues.

## 6. Change map

- `skills/review/SKILL.md`, `skills/review/skill.json`; `agent/skills.py` (internal delivery trigger);
  `agent/dispatch.py`, `agent/worker_dispatch.py` and service/runtime allowlists (role-specific review and repair
  authority, while keeping current chat/work intake and single issue ownership).
- `agent/review.py` (proposed): verdict validation, risk rules and finding lifecycle.
  `agent/review_queue.py` (proposed): immutable candidates, predecessor invalidation and scheduling.
  `agent/review_merge.py` (proposed): deterministic gate, pin checks and journaled merge execution.
- `agent/publication.py`, `agent/worktrees.py`: exact destination checks, narrowly scoped candidate refs,
  integrated snapshots and GitHub operations. Keep clone config/hooks outside worker write grants.
- `agent/ledger.py`: additive run/candidate/attempt/merge records, delivery trigger, repair continuation and
  terminal human handoff. Couple state transitions to existing generation and publication ownership fences.
- `agent/__main__.py` (`review-verdict` and role-authenticated input), `agent/scheduler.py` (separate budgets and
  priorities), `agent/service.py`/`agent/lifecycle.py` (CI reconciliation, restart and queue ownership),
  `agent/config.py` (pool sizes, review policy, checks, candidate CI, merge methods and dependency rules).
- `agent/doctor.py`, `agent/monitor.py`, `agent/monitor_view.py`: read-only queue, pool, CI and handoff visibility.
- `skills/work/SKILL.md`, `references/single-worker-cli.md`: answering review findings.
- `docs/operating-contract.md`: the controller's merge authority and its limits.
- Tests: every gate failing alone; partial multi-repository merges; pin membership, complete own table changes
  and ancestry; ready-PR repairs; terminal handoff; a successful last repair cycle; changed human inputs; holds,
  withdrawal and circuit breaker; observation-only shadow with zero writes. Section 8 adds queue-specific cases.

## 7. Decisions

- **D1 Tiers and handoff.** Routine verified changes merge automatically. High-risk changes go to humans;
  every client AOT change remains high risk. The October 27 clarification makes this a terminal delivered
  handoff, with humans reviewing and merging rather than approving a later bot merge.
- **D2 Merge method.** Per repository. Squash, one commit per PR, by default: the operator's merges of bot PRs
  are squash in six of seven sampled [L]. common always uses a merge commit, as the seventh sample did: farm-hive
  pins a commit of the common PR, which only a merge commit makes an ancestor of common's default branch (§1.1,
  G6). Settled with the operator on 2026-10-22.
- **D3 Required checks.** Proposed from the October 22 check names [L]: Farm-Client `dotnet-unit-tests` and
  `generated-artifact-platform-tests`; farm-hive `build`, both `native-checks` and `windows-devctl`;
  Farm-Contract and common their CI jobs; the triage labeler's `triage` where it runs. farmgui has no CI, so it
  would rely on worker evidence. farm-hive's `designer-pin-merged` is not required; the gate checks the pin
  itself (G6). Reconfirm check names, producer identity and candidate coverage before rollout. Any no-CI
  exception must explicitly list adequate verification evidence and limitations; absence of CI is not a pass.
- **D4 Reviewer runtime.** Recommend the host's configured runtime, with a different model from the author's
  where the runtime allows.
- **D5 Size backstop.** Recommend 1,500 changed hand-written lines.
- **D6 Hold label.** Recommend one Linear label outside the Bot group, because routing expects exactly one Bot
  child (`agent/router.py` [V]), plus the PR label `needs-person`.
- **D7 Identity.** Keep the existing account for now.
- **D8 When to mark PRs ready.** Keep ready conversion at the gate, after a clean routine verdict. Run
  deterministic risk checks earlier, then wait for any ready-only required checks. Test a repair after ready
  conversion explicitly: current single-worker delivery verification accepts open ready PRs; historical
  draft-only assumptions are not an excuse to bypass publication checks or recreate unrelated PRs.
- **D9 Concurrency.** Keep eight existing implementation/general worker slots and add three review-stage slots,
  rather than splitting eight into six plus two. Up to eleven main workers; validate actual host/runtime
  capacity before rollout. Repairs and independent reviews use separate contexts within the additional pool.
- **D10 Speculation.** Approve parallel integration/review/CI for M+A, M+A+B and M+A+B+C, with ordered merges.
  This replaces the discussed fully serial final-review pipeline. Conflicts are resolved before each final
  review. Earlier changes can require rebuilding later candidates; that cost is accepted for throughput.
- **D11 CI waits.** GitHub runs CI. Park AI work and release its worker slot while waiting; retain durable
  queue state. No exclusive reviewer-slot reservation for a waiting PR. The controller merges a still-valid
  green candidate without needing an AI worker, or queues a repair when required.
- **D12 Repair limit.** Default to three AI repair cycles followed by validation. Successful repair never
  requires human intervention solely because the limit was reached. Unresolved problems cause delivered
  handoff. CI duration is not a repair failure.
- **D13 End of automation.** Human handoff is `delivered`, keeps the card In Review and disables that run's
  automatic merge. Humans review and merge. No automatic return from their review to an AI repair loop;
  only an explicit new work request starts another continuation.
- **D14 Integrated evidence.** Queue/candidate provenance and actual-base validation replace the original
  stale-base assumption. Ordinary PR CI and a matching head alone do not certify a speculative candidate.

## 8. Implementation sequence and acceptance

Implement in an isolated development checkout. The following are reviewable increments, not a production
deployment schedule. Keep the feature off by default until the rollout gates above are met.

1. **Policy, lifecycle and migration.** Define immutable snapshot and terminal outcome schemas; record review
   eligibility atomically with verified delivery. Add separate admission budgets and restart-safe state.
   Acceptance: eight general workers do not prevent review admission; at most three main review-stage
   attempts run; stale generations cannot submit verdicts; a handoff cannot be revived by a green check or
   human approval; rollback prerequisites are documented and tested.
2. **Read-only review and AI repairs.** Add the opt-in review skill, authenticated verdicts and a separately
   authorized repair path. Keep the writer/reviewer distinction and owned-process teardown. Acceptance:
   concrete findings, no self-certification, no nits causing repair loops, no concurrent writers, independent
   verification after repair, and successful cycle three proceeding without human intervention.
3. **Queue and speculative candidates.** Establish the shared repository/target authority, dependency order,
   candidate refs, immutable worktrees and full provenance. Acceptance: three same-repository candidates can
   be prepared concurrently; modifying/removing A invalidates B/C atomically; independent candidates remain
   usable; human handoff removes automatic eligibility; cyclic dependencies cannot deadlock controllers.
4. **GitHub CI adapter.** Support exact candidate workflows, authenticated events and periodic reconciliation.
   Acceptance: PR-head-only success cannot clear a combined candidate; late/duplicate/out-of-order events are
   idempotent; cancelled/skipped/missing required results do not pass; long-running CI parks without handoff;
   waiting work occupies no AI slot; failed CI queues prioritized repair without exceeding either pool.
5. **Promotion and merge executor.** Implement the selected per-repository backend and all G1-G8 guards.
   Acceptance: expected source heads and actual-base/result coverage; squash/rebase identity changes;
   history-dependent verification; controller restart before/after the merge call; lost responses; external
   base updates during submission; confirmed versus merely enqueued merges; common pin ancestry; partial
   cross-repository outcomes. A discovered race must be fixed or keep the affected backend in shadow.
6. **Visibility, evaluation and rollout controls.** Add read-only diagnostics, pool/queue/CI monitoring, audit,
   metrics and persistent circuit breakers. Acceptance: shadow cannot merge; observation-only mode cannot
   write; handoff reports are complete; reverts/reopens are attributed without double-counting the same event;
   representative evals and the authorized TestBot sequence establish readiness before production enablement.

Run focused unittest modules for each increment, then the full offline suite when integration warrants it.
Use temporary state, fake workers, stub APIs and local Git remotes. Verify Windows process/path behavior on
Windows and macOS behavior on macOS; report platform skips. Preserve existing signature, identity, claim,
delegation, Git trust, destination and Unity/server resource boundaries throughout.
