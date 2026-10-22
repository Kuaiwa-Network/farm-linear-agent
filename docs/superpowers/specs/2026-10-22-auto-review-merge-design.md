# Auto-review and merge: design

**Status: accepted, 2026-10-22; nothing implemented.** The operator accepted the decisions of §7 as
recommended, then the same day amended D2 and the gate for farm-hive's pin of common (§1.1, G5, G6). Current
behaviour is in
[`docs/operating-contract.md`](../../operating-contract.md): workers publish draft PRs and never merge, and people
merge.

Design of 2026-10-22 for FarmBot main e7d42a6. It adapts Anthropic's published account of how Claude Code's own
changes are reviewed (risk tiers, verified AI findings, no human approval for low-risk changes) to FarmBot's
controller and worker split. The operator settled the main rule on 2026-10-22: the process is AI end to end, and
only changes the AI judges high risk need a person.

IDs: AP1-AP9 are the principles of §2, G1-G8 the merge gate of §4.5, D1-D8 the decisions of §7. Tags: **[V]**
verified in code at e7d42a6, **[L]** measured on GitHub on 2026-10-22 over `farmbot/*` PRs created since
2026-10-08, **[I]** inference, **[U]** unknown.

The text names no card, person, host or ID, so it can go into the public repository as it is.

## 1. Problem

### 1.1 Today

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
issue's PRs itself, unless the change is high risk. A person is needed only for high-risk changes, for review
loops that do not converge and for anything the gate cannot prove.

Success means human merges per day fall to roughly the high-risk share and time to merge falls, while the revert
and reopen rate of auto-merged work stays no higher than that of human-merged work over the same period.

Out of scope: people's PRs, deployment, replacing repository CI, Linear status changes (the integration keeps
moving cards on merge), legacy staged jobs and `no_change` deliveries.

## 2. Principles

- **AP1 The change sets the tier.** Deterministic rules and the reviewer's judgment can each mark a PR high
  risk; neither can lower the other's verdict.
- **AP2 The author never reviews itself.** The reviewer is a separate worker with a fresh context and no write
  authority.
- **AP3 The controller merges; workers still never merge.** Merging is a deterministic gate over recorded
  evidence, not a worker's decision.
- **AP4 A verdict binds exact head SHAs.** Any later commit, whether from the bot, a person or a merge of main,
  voids it.
- **AP5 Only verified Important findings block.** Nits never block and never start a fix round. Findings in code
  the PR did not change are reported as pre-existing and never block.
- **AP6 Loops are bounded.** At most three review rounds per delivery; then a person decides.
- **AP7 Doubt goes to a person, never to a merge.** Checks still pending at the deadline, unreadable diffs,
  invalid reviewer output, GitHub errors and partial merges all stop auto-merge.
- **AP8 People can always stop it.** A hold label, a per-host switch and a circuit breaker that turns merging
  off after reverts.
- **AP9 Shadow before merge.** The whole path runs and records "would merge" before any merge is enabled.

## 3. Flow

```text
work delivered (generation g, draft PRs at heads S)
  -> controller queues review round r = 1
  -> review worker submits a verdict; controller validates it
       Important findings, r < 3   -> work requeued (g+1) with the findings
                                      -> new delivery at S' -> review r+1 on the diff since S
       high risk, r = 3, or upheld dispute
                                    -> needs a person: Linear and PR comments; card stays In Review
       clean and routine           -> controller marks the PRs ready -> merge gate G1-G8
                                      -> merge in dependency order -> integration moves the card
                                      -> any gate fails: wait (pending checks) or needs a person
```

**Timing.** Repository CI already runs on draft PRs; only the triage labeler skips them [L]. From the delivery
on, CI, the review and the controller's deterministic risk rules (§4.3) run at the same time; those rules need
only each PR's changed-file list. Only the merge gate waits, for a clean routine verdict and green checks on the
same heads.

## 4. Design

### 4.1 Review worker

- **Skill.** A new `review` skill with a read-only manifest (`writes: []`, `resources: []`) and a new internal
  trigger, `delivery` (`agent/skills.py` accepts only `delegation` and `mention` today [V]). It is `opt_in`, so
  each host enables it separately.
- **Shape.** The review is a root item for the issue, so it holds the issue's single active slot
  (`one_active_item_per_issue`, `agent/ledger.py:708-710` [V]) and nothing else runs on the issue meanwhile.
  How a person's message during a review is routed is left to the implementation plan; the rule is that the
  message wins and the review's verdict is void (§4.4).
- **Launch context.** The issue, its delivery comment, each PR with pinned base and head SHAs, read-only
  worktrees at those heads, the round number, earlier verdicts with the author's responses, each repository's
  `REVIEW.md` where present. The triage labeler and the hosted review bot run only on ready PRs, so they do not
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
- **Later rounds** review the diff since the last reviewed heads, together with the author's responses. They
  never repost a finding already raised, and they name a finding's class so every instance is fixed at once.
- The review worker posts nothing to GitHub or Linear itself; the controller does (§4.5, §4.6).

### 4.2 Verdict and validation

```json
{"round": 1,
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

The controller accepts a verdict only when the review's claim and generation are current, its PR set equals the
delivery's, each `head` equals the PR's current head on GitHub, every Important and Nit finding cites a path
and line inside that PR's diff, and every Important finding of the previous round has a response. A rejected
verdict is retried once; a second rejection needs a person.

### 4.3 Risk

A PR is high risk when any rule below holds; the issue is high risk when any of its PRs is.

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

- When a verdict has Important findings and r < 3, the controller requeues the delivered work job for its next
  generation, as a conversation's continuation does today (`agent/ledger.py:3301-3309` [V]). The job gets one
  inbox message from the review with the Important findings; Nits are attached for information only.
- The work skill gains one duty: answer every Important finding, either fixed (naming the commit) or disputed
  (with the reason). Disputes go back to the verifiers in the next round. "Never merge" stays.
- A required check that failed on the reviewed head counts as an Important finding of that round.
- Each fix round is a new generation, so the existing status sync moves the card back to In Progress when the
  worker starts and to In Review at its next delivery (`agent/issue_workflow.py` [V]).
- After round 3 with Important findings still open, or a dispute the verifiers uphold twice, the issue needs a
  person, with the open findings listed.
- A person's message or an issue change during the loop takes precedence. The existing routing handles it,
  generation fencing voids the pending review (AP4), and the round count restarts at the next delivery.

### 4.5 Merge gate

The controller merges the issue's PRs only when every condition holds at the moment it merges.

- **G1** Auto-merge is enabled for this host and for every repository in the delivery, the host is not in
  shadow mode, and the circuit breaker is closed.
- **G2** The issue is open, still delegated to this app and not withdrawn; the delivery is the current generation
  with no successor; no human question is unanswered; neither the card nor any PR carries the hold label (D6).
- **G3** The latest valid verdict covers the current head of every PR; no Important finding is open; every PR is
  routine; `needs_person` is false.
- **G4** Each PR is open, in a configured publishing repository, on this issue's branch, based on the default
  branch, with its head equal to the verdict's SHA (re-read just before merging), and GitHub reports it
  mergeable without conflicts. `PublicationVerifier.verify_pr` already checks repository, branch and head for
  delivered PRs (`agent/publication.py:144-176` [V]).
- **G5** The controller has marked each PR ready, because the triage labeler skips drafts [L]. Each of the
  repository's required checks (D3) then completed successfully on that head. While checks are
  pending the gate waits, up to a configured deadline (default 2 h); then the issue needs a person. A
  repository configured with no required checks relies on the worker's verification evidence, and the config
  must say so explicitly. A check that only waits for another repository's merge, such as farm-hive's
  `designer-pin-merged`, is not required; the controller verifies its condition itself (G6).
- **G6** PRs merge in dependency order (default: Farm-Contract, common, farm-hive, farmgui, Farm-Client). Each
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
- **G7** If a merge fails after earlier ones succeeded, the controller stops. It retries transient errors within
  the publication retry bounds; otherwise it reports the merged and unmerged PRs to a person. Merges across
  repositories cannot be atomic on GitHub [I].
- **G8** After merging: an audit record, a Linear comment with the tier, rounds, findings fixed and checks, and a
  PR comment with the verdict summary. Branches are kept.

**Stale base.** A PR's checks ran against main as it was then, and main may have moved since. Today's human
merges accept the same risk [L]. CI on pushes to main (Farm-Contract and common run it [L]), acceptance in
Linear and the circuit breaker detect the consequences; a merge queue is not available on the plan [L].

### 4.6 When a person is needed

The controller posts a Linear comment, in Chinese like FarmBot's other replies, and a marked PR comment, both
giving the reason, the open findings and the PR links. The PRs get a `needs-person` label and the card stays In
Review. People then merge as they do today, or ask for changes in the conversation; that starts an ordinary
continuation, whose next delivery is reviewed again.

### 4.7 Identity

The controller merges with the host's existing GitHub credential, so merges show that account as they do today
[L]. GitHub does not let an account approve its own PR, so the review is posted as a comment review, not an
approval. A dedicated machine account would make bot merges distinguishable, but it is not required (D7).

### 4.8 Records, measurement and circuit breaker

- **Schema.** Additive ledger tables: `reviews` (item, generation, round, heads, verdict, outcome) and
  `auto_merges` (issue, PR, head, merge SHA, time, tier). Older code ignores them; checking out older code does
  not remove them.
- **Visibility.** `doctor` and the monitor show the review queue and recent auto-merges.
- **Measurement**, read-only: auto versus human merges, time to merge, rounds per delivery, findings fixed versus
  disputed, reverts of auto-merged commits (a default-branch commit that reverts a recorded merge SHA) and cards
  reopened after an auto-merge.
- **Circuit breaker.** Two reverts or reopens of auto-merged work within seven days (configurable) switch the
  host to shadow mode and tell the operator. Only the operator switches it back.

### 4.9 Evaluation (later)

Past deliveries that people later fixed or reverted become review evals. A change to the reviewer's prompt or
model is kept only if it catches more of them without adding findings on PRs that needed none.

## 5. Rollout

1. **Offline.** Unit and journey tests with local Git remotes and the GitHub test double `StubGitHub`
   (`agent/publication.py:100-115` [V]). It answers only reads today, so it needs recorded write calls (mark
   ready, merge) with scripted results. Merges run against local remotes only.
2. **TestBot, shadow only**, on cards the operator chooses. A live merge reaches a real default branch, so a live
   merge check needs the operator to pick one trivial card for it.
3. **Production shadow** for about a week (around 70 human merges at today's rate). Review findings and fix
   rounds are live; merges stay human. Compare "would merge" with what people did: every PR a person closed,
   reverted or changed after review must have been high risk or had findings.
4. **Production auto-merge** for the routine tier in every repository, with the circuit breaker armed.

## 6. Change map

- `skills/review/SKILL.md`, `skills/review/skill.json`; `agent/skills.py` (trigger `delivery`);
  `agent/worker_dispatch.py` (review authority text).
- `agent/review.py` (new): verdict validation, risk rules, the gate with its pin checks, and the merge executor.
- `agent/publication.py`: mark ready, read check runs and mergeability, merge with the expected SHA.
- `agent/ledger.py`: tables, queueing a review after a delivered `work` finish, requeueing with findings.
- `agent/__main__.py` (`review-verdict`), `agent/scheduler.py` (launch reviews, poll pending checks),
  `agent/config.py` (the `auto_merge` block, with each repository's merge method, required checks and pin).
- `skills/work/SKILL.md`, `references/single-worker-cli.md`: answering review findings.
- `docs/operating-contract.md`: the controller's merge authority and its limits.
- Tests: verdict validation; each gate condition failing alone; a head that moves between check and merge; a
  partial multi-repository merge; a pin outside the common PR; a common PR that merged newer default-branch work
  after the pinned commit; a common merge that leaves the pin off common's default branch; the round cap; a
  person's message during review; the circuit breaker; shadow mode never merging.

## 7. Decisions

- **D1 Tiers.** Routine changes merge; high-risk changes go to a person. Settled by the operator on 2026-10-22,
  who added the same day that every AOT change is high risk (§4.3).
- **D2 Merge method.** Per repository. Squash, one commit per PR, by default: the operator's merges of bot PRs
  are squash in six of seven sampled [L]. common always uses a merge commit, as the seventh sample did: farm-hive
  pins a commit of the common PR, which only a merge commit makes an ancestor of common's default branch (§1.1,
  G6). Settled with the operator on 2026-10-22.
- **D3 Required checks.** Proposed from today's check names [L]: Farm-Client `dotnet-unit-tests` and
  `generated-artifact-platform-tests`; farm-hive `build`, both `native-checks` and `windows-devctl`;
  Farm-Contract and common their CI jobs; the triage labeler's `triage` where it runs. farmgui has no CI, so it
  would rely on worker evidence. farm-hive's `designer-pin-merged` is not required; the gate checks the pin
  itself (G6).
- **D4 Reviewer runtime.** Recommend the host's configured runtime, with a different model from the author's
  where the runtime allows.
- **D5 Size backstop.** Recommend 1,500 changed hand-written lines.
- **D6 Hold label.** Recommend one Linear label outside the Bot group, because routing expects exactly one Bot
  child (`agent/router.py` [V]), plus the PR label `needs-person`.
- **D7 Identity.** Keep the existing account for now.
- **D8 When to mark PRs ready.** Recommend at the gate, after a clean routine verdict. Marking them ready at
  review start would let the triage labeler and the hosted review bot feed the reviewer, but the worker's
  late-attachment check accepts only open drafts (`agent/publication.py:166` [V]), so fix rounds on ready PRs
  would need that check changed. The hosted bot's findings are rare today (§1.1).
