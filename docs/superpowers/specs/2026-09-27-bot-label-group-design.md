# The Bot label group (D18)

**Status: accepted, 2026-09-28; implemented, not yet deployed.** Current behaviour is in
[`docs/operating-contract.md`](../../operating-contract.md), which the implementation updated. The
design follows the operator's decision D18 of 2026-09-27, which amends D3 of the
[feature-workers design](2026-09-24-feature-workers-design.md) ("the feature spec" below) and,
through f (§2), the last clause of its D16; D18 c also ends the Bug-delegation shortcut of two
earlier designs (§3.1).
On 2026-09-28 the operator confirmed f, g and h and settled the questions of §10 as recommended.
Approving the design authorizes neither the Linear change nor any deployment; §7 gives their order.
"The plan" is
[`docs/superpowers/plans/2026-09-25-feature-workers-phase-a.md`](../plans/2026-09-25-feature-workers-phase-a.md),
whose "As executed" sections record the live checks cited here.

## 1. Summary

The label group 功能 becomes **Bot**, and its children name what FarmBot does with a delegated card:
**修改** starts `fix`, for a bug fix or a small code or UI change to something that exists; **UI**
starts `fgui` and **Code** starts `feature`, once an instance runs them (none does yet). A card
carries at most one. Bug, Improvement, Feature and the 部门 labels stay for people, and routing
ignores them: a card labelled only Bug no longer starts a fix when delegated. Like any delegated card
without a Bot label, it opens the read-only conversation, whose first message says how to get a fix,
and a reply such as 「修复」 there still starts one. Bug and 修改 can sit on one card, so FarmBot no
longer asks which label to remove. FarmBot never sets a Bot label itself.

## 2. Decisions

### D18 (operator, 2026-09-27; settled)

| Part | Decision |
|---|---|
| a | The team label group 功能 (single-select; children UI and Code; no open card carries it) is renamed **Bot** and gains a third child, **修改**. The group belongs to the bots: its children name the workflow FarmBot runs on a delegated card. The name is neutral because production FarmBot and the test bot TestBot both read it. |
| b | 修改 starts skill `fix` (a bug fix, or a small code or UI change to what already exists); UI starts `fgui`; Code starts `feature`. A card carries at most one. |
| c | Bug, Improvement, Feature and the 部门 group are labels for people; routing ignores them. In particular Bug alone no longer starts `fix` on delegation, because a bug can be designer-only work (config data, art) with no code change. |
| d | A delegated card with no Bot label opens the read-only conversation. A repair request in that conversation, for example 「修复」, can still start `fix`. |
| e | Bug fixes and small improvements share one workflow, `fix`. The label follows the kind and size of the work, not the card's classification. Improvement cards are a mix of code and UI tweaks. New screens, new protocol messages and new config tables are UI or Code work. |

In the feature spec, D18 replaces D3's group name, adds 修改 to its children UI and Code, and replaces
its clauses "Bug keeps `fix`; Bug plus 功能 makes FarmBot ask" (line 35); D3's matching of the parent
group stays. D16 (line 48) stands, except its "chat does not start feature work", which f replaces.

### Confirmed on 2026-09-28

The assistant proposed f, g and h on 2026-09-27; the operator confirmed each as recommended on
2026-09-28. They are part of D18.

**f. Start requests follow the Bot label.** A start request in a conversation starts the workflow
the card's Bot label names (修改 or no label: `fix`; UI: `fgui`; Code: `feature`), each only where
the instance enables that skill. FarmBot never adds a label itself. This replaces D16's "chat does
not start feature work", the feature spec's non-goal "Starting feature work from a mention or from
chat (D16)" (§3 there, line 130), and today's refusal of a first fix on a 功能 card. Mentions still
never start write work.

*Confirmed as worded in §4.4 and §4.7:* a request continues earlier write work
whatever the label now says, and "mentions never start write work" holds for routing, while a
conversation a mention opened can request a start through the card's delegation while the card is
delegated, as it can request a fix today. Reason: a start request, like a delegation, needs the card
to be delegated now (§4.4), and D18 a says the Bot child names the workflow FarmBot runs on a
delegated card; D18 d already lets a conversation start `fix`; and a card labelled during its
conversation then needs no second delegation. Until a host runs `fgui` or `feature`, f changes only
the wording of one refusal (§4.4).

**g. No transition switch: Bug alone stops starting `fix` once production runs D18.** A
transition happens anyway: until production runs D18 code, a Bug card delegated to it starts `fix`
(§7). A switch would keep sending designer-only bugs to `fix`, which D18 c exists to stop. It would
need a config key (fragile for the reason §4.1 gives), tests for both settings and a release to
remove it, and it would have to read "Bug and no Bot child", or the Bug-plus-group question returns
(§4.6). Instead, a delegation without a Bot label gets a first message saying how to get a fix
(§4.2), and the team hears of the change on the day production runs it (§6.2).

**h. The FGUI export grant covers a 修改 UI change.** FarmBot's FairyGUI CLI export rests on the
operator's standing authorization of 2026-09-21, recorded in farmgui's `AGENTS.md:38-43` ("when
FarmBot is carrying out an authorized FGUI bug fix") and in this repository
(`references/repo-map.md:96-103`; `docs/operating-contract.md:549-550`). D18 b and e send UI tweaks
that are not bugs to `fix`. *Confirmed: the operator widens the authorization to an authorized
FarmBot `fix` job, a bug fix or a small change to existing UI, leaving its validation and scope
sentences unchanged; farmgui's owners record the new wording (§7, step 3), and this repository's two
records follow it, never wider.* Reason: without it a 修改 UI change that needs
freshly exported assets has no standing grant; and the feature spec already plans to reword the same
lines for `fgui` (line 1038), so one edit can carry both.

## 3. Background

### 3.1 How the team labels cards

People label 农场 cards by kind, with Bug, Improvement or Feature, and by department, with the 部门
group (FARM-1353 reads as 部门/程序, plan line 34). The documented way to get a fix is to delegate a
Bug card (`docs/operating-contract.md:115`), a shortcut the 2026-09-17 design set
(`docs/superpowers/specs/2026-09-17-farm-linear-agent-design.md:161`) and the conversation design of
2026-09-22 kept (`docs/superpowers/specs/2026-09-22-conversation-repair-design.md:9-11`). The 功能
group, team-scoped and single-select with the children UI and Code, was made for the feature workers
(feature spec §4.1), which do not exist yet.

### 3.2 What FarmBot does with labels today

A delegation, or a reply that re-routes one (§4.3), is routed in this order:

| Delegated card | Result | Source |
|---|---|---|
| Bug and a 功能 child | asks which label to remove, adds `needs-more-info`, creates no work item | `agent/router.py:71-72`; `agent/receiver.py:259-260` |
| One 功能 child whose skill the instance runs | work item of that skill, whatever the delegation text | `agent/router.py:73-76` |
| A 功能 child the instance does not run, an unknown child, or two | conversation; the first message names the child and the instance's skills | `agent/router.py:36-43`, `:77` |
| Bug, no 功能 child, no text, `fix` enabled | `fix` work item | `agent/router.py:78-81` |
| Anything else, Bug with text included | conversation | `agent/router.py:82` |

The group and its children are constants, `FEATURE_GROUP = "功能"` and
`FEATURE_SKILLS = {"UI": "fgui", "Code": "feature"}` (`agent/router.py:7-8`), compared by exact name
with the parent name Linear returns (`:21`). Linear also returns the parent's ID
(`agent/linear_api.py:41`); FarmBot drops it and keeps names only (`:111-118`, `:395-396`). Bug is
read in two places, the question and the shortcut
`if not (text or "").strip() and "Bug" in labels and "fix" in available_skills:`
(`agent/router.py:71`, `:78`). Every event and every `request-repair` reads the card afresh
(`agent/receiver.py:211`, `:250-252`; `agent/__main__.py:412`). Labels never touch existing work:
the claim fingerprint leaves them out (`agent/ledger.py:249-251`), and a session that already had a
work item is not re-routed by its labels (`agent/receiver.py:248`). The one label FarmBot writes is
`needs-more-info` (`agent/linear_api.py:304-337`).

A conversation asks for a fix with `request-repair`. It is refused before Linear is read unless the
instance runs `fix` (`if "fix" not in running:`, `agent/__main__.py:407-410`). On a card with a 功能
child, no fix to continue and no `fgui` or `feature` job, it refuses a first fix
(`agent/__main__.py:154-167`, `:414-417`; text at `agent/router.py:46-55`). Otherwise it continues an
earlier fix from a delegation session (`w.skill='fix'`, `agent/ledger.py:1579-1584`) or creates a
first one, always `fix` (`agent/ledger.py:1640-1644`), in the recorded delegation session.

No host can run `fgui` or `feature`: the checkout's skills are `chat` and `fix`
(`tests/test_skills.py:173`), and `enabled_skills` refuses a name the checkout lacks
(`agent/skills.py:109-111`). On a host running code from #57 (the pull request that added 功能
routing, §7), a card with one 功能 child therefore opens a conversation, or gets the label question
when it also carries Bug; code before #57, which production may still run, ignores the group.

### 3.3 Evidence from Linear

A read of the 农场 team on 2026-09-27 found about 70 cards labelled Improvement; four were delegated
to FarmBot in the week before, two of which also carry Bug. No open card carries a child of 功能; the
one card that does, FARM-1390, is canceled. Today the two Bug cards start `fix` when delegated
without text and the other two open conversations; under D18 all four open conversations unless
labelled 修改.

The same day TestBot, running only `chat` and `fix`, showed today's rule on FARM-1390, labelled
功能/Code (plan lines 90-106). The delegation opened a conversation whose first message said this
instance does not run `feature`, and a later 「修复一下」 was refused by `request-repair` as feature
work, so no fix started. That refusal is what f would replace.

## 4. Design

### 4.1 The Bot group

The 农场 team's group 功能 is renamed Bot and gains the child 修改 beside UI and Code. It stays
single-select, so Linear lets a card carry one child at most (feature spec §4.1). FarmBot keeps
matching the parent group together with the child, so a standalone 修改, UI or Code label, or one in
another group, is not a Bot label (`agent/router.py:18-22`).

Matching by name is a deployment hazard: the rename reaches both bots at their next read of a card,
while code reaches them at different times (§7). D18 code therefore accepts **Bot** and **功能** as
the group's name for one release, so it routes the same before and after the rename; a later release
drops 功能. Two alternatives were considered. Pinning the group's label ID, which Linear already
returns, would survive any rename, but it needs a config key on both hosts that the loader ignores
silently when misspelled (`agent/config.py:139-140`), the ledger stores label-group entries with
exactly two keys (`agent/ledger.py:114-115`). Linear does keep label IDs through a rename: UI and
Code kept theirs through the operator's rename (checked 2026-09-28). A configurable name needs the
same edit, plus a restart of each settled service at the moment of the rename (`AGENTS.md:77-78`).

### 4.2 Routing a delegation

| Delegated card (a `created` event, or a reply that re-routes, §4.3) | Result |
|---|---|
| One Bot child whose skill the instance runs: 修改 `fix`, UI `fgui`, Code `feature` | work item of that skill, whatever the delegation text, which becomes its first session message (`agent/receiver.py:285-286`) |
| One Bot child whose skill the instance does not run, an unknown child, or two | conversation; the first message names the child, its skill and the skills the instance runs (today's text, `agent/router.py:36-43`, with Bot and 修改) |
| No Bot child, whatever its Bug, Improvement, Feature or 部门 labels | conversation; on a `created` delegation to an instance that runs `fix`, the first message says the card has no Bot label, that a reply such as 「修复」 starts a fix, and that Bot/修改 set before a later delegation starts one directly |

The last row's first message replaces the generic 「{bot} 已收到，正在查看。」
(`agent/receiver.py:33`, `:292-293`); a mention keeps the generic one, and so does a re-route
(below). It is what the team sees where a Bug card used to start a fix. The text names the
instance as every acknowledgement does (`agent/receiver.py:28-29`); `NO_BOT_LABEL` in
`agent/receiver.py` has it:

```text
{bot} 已收到。这张卡没有 Bot 标签，先以只读对话查看。需要修复或修改，请在这里回复（例如「修复」）；以后委派前先加上 Bot/修改 标签，就会直接开始处理。
```

Delegation text follows the label rule the feature spec set for UI and Code (§4.4 there), 修改
included: a Bot child starts its worker whatever the text says, and someone who only wants to talk
about a labelled card mentions FarmBot. The `fix` worker reads that message before investigating and
may answer it without changing code (`skills/fix/SKILL.md:9-12`, `:45-48`). Bug's rule that text goes
to a conversation first (`agent/router.py:78-80`) served a label that did not say what the person
wanted; a Bot child does. The operator confirmed this rule (§10, question 1).

A `created` delegation never reaches the router with text: a session carrying a comment is a mention
(`agent/receiver.py:158`, `:167-168`), and a verified artificial root is stripped of its text
(`:218-219`). Delegation text exists only as a reply that re-routes a session that never had a work
item (§4.3). That reply is already the conversation's first message, so a re-route with no Bot child
needs no instructions and keeps the generic acknowledgement.

### 4.3 Replies

Stop and a reply to an active item read no labels and do not change: Stop cancels, and a reply steers
or resumes the item (`agent/router.py:62-67`). A reply in a delegation session that never had a work
item routes again by §4.2, on the labels read for that event, with the reply as the delegation's
text, while the card is still delegated (`agent/receiver.py:245-252`). After D18 a delegation session
has no work item only when another session's work was active, so the delegation was declined or
forwarded (`agent/receiver.py:263-279`), or when its event failed after the session was recorded
(`:224`); the label-conflict case is gone (§4.6). A reply after the session's item finished opens a
new conversation whatever the labels now say (`agent/router.py:82`; `tests/test_receiver.py:932-942`).
After relabelling, ask for the work in the conversation (§4.4), or press Stop in it, then remove the
delegation and delegate the card again: while the conversation's item is queued, running or parked on
a question, a new delegation is declined or forwarded (`agent/receiver.py:263-279`), and removing the
delegation does not end that item (`agent/lifecycle.py:19-21` stops work only on a closed or archived
card).

### 4.4 Start requests in a conversation (f)

Under f, `request-repair` keeps its name, its fresh read of the card (`agent/__main__.py:411-413`) and
its fences: the conversation's claim, a real and latest message, the card open and delegated now, and
a recorded delegation session, whose target the new job takes (`agent/ledger.py:1616-1634`,
`:1643-1644`). It then continues the card's earlier write job of a delegation session if there is one,
whatever the label says now (today an earlier `fix`, `agent/ledger.py:1579-1584`). Otherwise it starts
the workflow the Bot label names, 修改 or no Bot child `fix`, UI `fgui`, Code `feature`, or refuses
because the instance does not run that skill. An unknown Bot child, or more than one, names no
workflow: a first start there is refused as today, naming the labels (`agent/router.py:48-52`).
`request-repair` is not told whether it was asked to start or to continue, so on a card with an
earlier fix from a delegation session, a stopped one included, any request continues that fix
(`agent/__main__.py:164`, `agent/ledger.py:1628`; `tests/test_repair_work.py:222-233`); the chat
skill tells the two apart (§5).

Since no host runs `fgui` or `feature` (§3.2), in the D18 release a first start on a UI or Code card
is refused in the CLI before the ledger's transition, as today's refusal is, and `fix` stays the only
skill a request can start: the `fix`-only gate before Linear is read stays
(`agent/__main__.py:407-410`), and the ledger does not change. The phase that first enables `fgui` or
`feature` moves that gate after the fresh read. The feature spec plans only continuations for that
phase: a request continues the delegation's own write skill, and a first `fgui` or `feature` job is
refused (§9.4, lines 904-906; §9.9, lines 993-994). Under f that phase must also pass the label's
skill into the ledger for a first start, which today always inserts `fix`
(`agent/ledger.py:1640-1644`). It also decides whether those skills, which start at a repository
root, can do without the conversation's summary as `prior_context`, which today reaches only a skill
that starts without one (`agent/scheduler.py:157-164`); the summary stays in `issue-context` either
way (`agent/ledger.py:2039-2051`).

FarmBot never adds, removes or changes a Bot label (feature spec §3, lines 122-123).

### 4.5 What 修改 covers

修改 covers a bug fix and a small code or UI change to something that exists, in any repository `fix`
may write (`skills/fix/skill.json:5`). It does not cover new screens, new protocol messages or new
config tables, which are UI or Code work (D18 e), nor designer-only work such as a config value or
art, which takes no Bot label (D18 c). Nothing in the code keeps a larger change out of `fix`; the
person choosing the label does. The `fix` skill therefore gains one instruction: before making a new
screen, protocol message or config table, the worker stops and finishes blocked, naming what it found
and the Bot/UI or Bot/Code label the card needs (D18 e; §10, question 2).

The `fix` skill is written for defects. Each step keeps its purpose and gains wording for a change:

| Step | Today | For a change |
|---|---|---|
| Scope | "Investigate and fix exactly one delegated Farm bug" (`skills/fix/SKILL.md:3`) | one delegated bug fix or small change |
| Start comment, posted before investigating (`skills/fix/SKILL.md:56-58`) | 「正在复现与定位问题」 (`references/comment-templates.md:11`) | 「正在定位问题或要改动的位置」, keeping 「👀 <bot_name> 已开始处理」 |
| Locate | "locate the bug" (`skills/fix/SKILL.md:35`, `:75-76`) | or the code the change touches |
| Unclear intent | "whether the contract or implementation is wrong" (`skills/fix/SKILL.md:93-94`) | or where the requested change belongs |
| Baseline run | "for baseline reproduction only" (`skills/fix/SKILL.md:167-168`) | or the state before the change |
| Red test | "Show a testable logic bug failing before the fix and passing after" (`skills/fix/SKILL.md:235-236`; `references/evidence-format.md:30-32`) | a test of the new behaviour fails on the baseline and passes on the change; a UI change gets a before-and-after check or a named gap |
| PR and delivery | "describe the observed problem" (`skills/fix/SKILL.md:319-320`); 已提交修复, 问题：<观察到的现象> (`references/comment-templates.md:21-22`) | the problem or the requested change; 已提交修复或改动, 问题或需求 |
| No change | already fixed, duplicate, does not reproduce (`skills/fix/SKILL.md:333-339`; `references/comment-templates.md:28-35`; `docs/operating-contract.md:702-704`) | also: the change is already on the target branch |
| kw_ops (the test game's GM backend) | "only for this issue's reproduction and verification" (`skills/fix/SKILL.md:28`) | "reproduction or verification", the words of the dispatch AUTHORITY (below) |
| Contract edits | "for a confirmed bug requirement" (`docs/operating-contract.md:286-287`) | for a confirmed requirement of the card, a defect or a requested change |
| FGUI export | "an authorized FGUI bug fix" (h) | as h settles |

The dispatch AUTHORITY, the launch text the Codex approval reviewer trusts (`agent/dispatch.py:5-7`),
needs no change for 修改: no sentence in it names a bug or a defect. Its limits are the issue ("A
human delegated or mentioned the issue; that is your only authority.", `agent/dispatch.py:10-11`), the
stage's repositories (`:11-12`), publishing "this issue's relevant source changes, tests and required
generated assets" (`:22-24`), and kw_ops "when this issue's reproduction or verification needs it"
(`:64-65`), which a change meets through verification. "fix SHAs" (`:55`) names the commit under
test. The one indirect limit is "A consumer-root worker follows that repository's own rules."
(`agent/dispatch.py:16`), which brings in farmgui's export grant (h). The approval reviewer never
reads farmgui's rules (§10, question 5), so, as the operator settled there, `fix`'s part of the
AUTHORITY gains a statement of the export grant h settles, and its frozen hash test
(`tests/test_dispatch.py:220-247`) changes deliberately in the same pull request.

### 4.6 Why one group removes the Bug-plus-功能 question

Today Bug and a 功能 child each name a workflow, so a card with both is ambiguous and FarmBot asks
which label to remove (`agent/router.py:71-72`). Under D18 c Bug names none; only a Bot child does,
and the single-select group allows one per card. No card can name two workflows, so there is nothing
to ask: Bug with 修改 is the ordinary pair for a bug fix, and Bug with UI or Code routes on the child.
The question (`_conflict`, `agent/router.py:29-33`) and its branch go. Were the group ever made
multi-select, a card with two children would get the explaining conversation, since a child routes
only when it is the only one (`agent/router.py:74`). The receiver's `elicit` branch
(`agent/receiver.py:305-306`) is older than the question (it is in `8e49d2d^`, before #57) and stays
unused, as it was then.

### 4.7 Mentions

A mention is never routed to write work: the delegation rows need `is_delegation`
(`agent/router.py:68`), and the receiver raises if the router ever produced such work
(`agent/receiver.py:280-282`). D18 changes nothing here, and a mention keeps the generic first
message. A conversation a mention opened can start or continue work only through the card's recorded
delegation while the card is still delegated (`agent/ledger.py:1620-1621`, `:1632-1634`;
`tests/test_repair_work.py:58-66`), and there, under f, the Bot label chooses a first start as in a
delegation's own conversation. A card never delegated to the bot gets no work this way.

## 5. Changes by component

The operating contract describes current behaviour (`docs/operating-contract.md:3`), so it changes
in the same pull request as the code.

| File | Change | Tests |
|---|---|---|
| `agent/router.py` | The group constant names Bot and, for one release, 功能 (§4.1); the child map is 修改 `fix`, UI `fgui`, Code `feature`; `feature_children` is renamed for Bot. The Bug rules (`:71-72`, `:78-81`) go with `_conflict` (`:29-33`). A `created` delegation with no Bot child returns the §4.2 message when `fix` is enabled; a re-route keeps its reply as the text, as today. `_not_run` (`:36-43`) names Bot and lists 修改, and since 修改 is not feature work its unknown-child text says 「一项工作」 and 「不会开始这项工作」 for 「一项功能工作」 and 「不会开始功能工作」. `feature_repair_refusal` (`:46-55`) becomes the refusal of §4.4 and leaves 修改 alone: with 修改 only added to today's map, `request-repair` would refuse a first fix on a 修改 card as "fix work, not a fix", and `agent/__main__.py:165` would count any earlier fix as a feature job. | `tests/test_router.py`: assertions change at `:19-20` (Bug alone opens a conversation; 修改 takes its place), `:31-32` and `:101-102` (a delegation with no Bot child carries the §4.2 message, not its own text), `:81-96`, `:103-104`, `:106-110`, `:119-128` and `:133`; `:60-72` go; new cases in §9 |
| `agent/receiver.py` | `ACK["fix"]` (`:30`) says 「正在排队处理这张修改卡」 instead of 「这个缺陷」; `PAUSED_WORK` (`:35`) drops `fix`, so a reply after undelegation reads 「暂不继续这项工作」 (`:302-304`). The §4.2 message names the instance and is posted only when this session gets the conversation, not put before the notice that forwards a message to another session's work (`:276-277`). The re-route comment (`:245-247`) loses its label example. | `tests/test_receiver.py:789-816` and `tests/test_service.py:132-144` pin the texts byte for byte; `tests/test_receiver.py:834-842`, `:860-874`, `:876-887`, `:897-930` (§9) |
| `agent/__main__.py` | `feature_card_refusal` (`:154-167`) applies §4.4: it refuses a first start on a card whose Bot children are one UI or Code child, with f's text ("… this instance does not run `feature` yet" until one does), or one unknown child or two children, with today's "not exactly one" refusal under the Bot name (`agent/router.py:50-52`); it never refuses a card whose only Bot child is 修改 or a card without a Bot child. The acknowledgement (`:425`) reads 「已排队开始或继续修改」 instead of 「…修复」. The `fix` gate (`:407-410`) stays. | `tests/test_repair_work.py:202-220` and `:252-258` (new texts; Bug with Bot/修改 gets a fix); `:222-233` and `:268-275` unchanged |
| `agent/dispatch.py` | The code comment at `:61` says "the issue's" for "a bug's". `fix`'s part of the AUTHORITY gains the export sentence h settles (§10, question 5); `chat`'s part stays as it is. | `tests/test_dispatch.py:223-247` changes deliberately: the frozen copy and its hash for `fix`, while `chat` keeps today's bytes |
| `skills/fix/SKILL.md` | The steps of §4.5 and the size instruction. | `tests/test_skills.py:143-160` pins other phrases, which stay |
| `references/comment-templates.md` | The started, delivery and no-change texts of §4.5. | `tests/test_skills.py:88-99` pins the started and delivery lines |
| `references/evidence-format.md` | The red-test rule of §4.5 (`:30-32`). | none |
| `skills/fix/skill.json` | `intents` (`:4`) becomes `["label:Bot/修改"]`; nothing reads it (`agent/skills.py:55-57`, `:81` validate and store it). | none |
| `skills/chat/SKILL.md` | "A missing Bot label alone does not require clarification or re-delegation" (`:48`); a change request such as 「改一下」 beside 「修复」 (`:41-43`); the checks and refusals of §4.4 (`:55-62`, `:66-67`), and UI and Code start requests (f); on a UI or Code card whose context shows `resumable_work`, `request-repair` only when the person asks to continue that fix, while a request for UI or Code work gets the answer §4.4's refusal gives. | none |
| `references/worker-cli.md` | "no prior fix or Bot label" (`:34`); the refusal of §4.4 (`:39-41`). | none |
| `references/repo-map.md` | "For the delegated bug or change" (`:80`); the export sentence (`:100-103`) as h settles. | none |
| `agent/monitor_static/monitor.js` | `SKILL.fix` (`:18`) reads 修改, the label people use. | `tests/test_monitor.py:138-139` checks only that every skill has a label |
| `docs/operating-contract.md` | Trigger rows `:115-120` follow §4.2 to §4.4 and §4.7, and the "declines" of `:127` then holds only for a delegation with a Bot child this instance runs (§8); the delegation paragraphs `:223-236`; "reproduction or verification" (`:206-208`); Contract edits (`:286-287`); UI fixes and changes (`:536`, `:544`); the export sentence (`:549-550`) as h settles; the no-change reasons (`:702-704`); a note in Triggers on the rename and deploy order of §7. | none |
| `README.md` | Delegate a card with a Bot label for work (`:3`); "bug fixes and small changes" (`:5`); §4.4 instead of the 功能 exception (`:8-9`). | none |
| `docs/superpowers/specs/2026-09-24-feature-workers-design.md` | This design adds the D18 row (line 50), notes on D3 and D16 (lines 35, 48) and pointers at the top of §4.1 and §4.3. When D18 lands, remove the Bug rules D18 c ends: §4.3's rows "Bug and any 功能 child" and "Bug, empty text" (lines 175, 179) and its label-fix clause (184-185), "implement the Bug-plus-功能 elicitation" in §9.2 (873), "and Bug plus 功能" in §9.11 (1012-1013) and §12's "Bug alone is unchanged; Bug plus 功能 elicits, and a re-route after the label fix starts the right skill" (1072). Add 修改 in §4.1 and §4.3, extend "D1–D17" (7), and rename 功能 to Bot in §1 (17), §4.1-§4.4 (134-193), §6.2 (411), §7.2 (683), §9.4 (905), the manifests (932), §9.11 (1012), §10 (1036) and §12 (1078). For f, also change §3's last non-goal (130), §9.4 (904-906) and §9.9 (993-994); for h, §9.11's "an authorized FGUI bug fix" (1015) and §10's farmgui row (1038). The dated records in §2.2 (line 93) and §14.1 (lines 1119-1123) stay. | none |
| `docs/superpowers/specs/2026-09-17-farm-linear-agent-design.md`, `docs/superpowers/specs/2026-09-22-conversation-repair-design.md` | That D18 c ends the Bug-delegation shortcut (§3.1): an "Approved amendment — 2026-09-27" section at the top of the first, like its lines 8-29, and one sentence after line 11 of the second. | none |
| `AGENTS.md` (recommended) | Beside the shared-workspace rules (`:69-73`): both bots read the same labels, so a label change reaches both at their next read of a card, while code reaches them at different times. | none |
| `docs/development-workflow.md` (recommended) | The same note under "What does not separate them" (`:51-65`). | none |

No change: `agent/ledger.py` (§4.4); `agent/linear_api.py` and `agent/config.py` (§4.1);
`agent/scheduler.py`; `agent/skills.py`; `agent/service.py`, whose `enqueue` checks the enabled skills
and delegation but no label (`:145-160`); `ACK["feature"]` (`agent/receiver.py:32`), whose
「这张功能卡」 names the kind of work; and `tests/test_linear_api.py` and `tests/test_ledger.py`, which
use 功能 only as a sample group name.

## 6. Changes outside FarmBot

### 6.1 Linear (operator)

Rename the group 功能 to Bot, then add the child 修改, in that order (§7). The group stays team-scoped
and single-select. No open card needs relabelling; FARM-1390 will read as Bot/Code and stays canceled.

### 6.2 What the team is told

On the day production runs D18, not before (§7):

1. The label group 功能 is now **Bot**, with 修改, UI and Code. A card carries at most one of them.
2. To have FarmBot fix a bug, or make a small change to existing code or UI, add Bot/修改 and delegate
   the card to FarmBot.
3. Bug, Improvement, Feature and 部门 are for people; FarmBot does not use them to decide what to
   start. Bug alone no longer starts a fix; Bug with 修改 is fine.
4. New screens, new protocol messages and new config tables are UI or Code, not 修改. FarmBot does not
   run those workers yet, so such a card opens a read-only conversation that says so.
5. A card delegated without a Bot label opens a read-only conversation. Reply 「修复」 there to have
   FarmBot fix it.
6. FarmBot never sets a Bot label. Changing the label after FarmBot has started does not switch the
   job: press Stop, correct the label, then remove the delegation and delegate again. A stopped fix
   stays on the card: while the card is delegated, a later request for a fix continues it.
7. Work already under way continues unchanged.
8. Delegate to FarmBot. TestBot takes only cards chosen for tests, and no card goes to both.
9. Do not move a card with a Bot label to Done or Canceled before FarmBot's delivery comment: either
   status cancels unfinished work (§10, question 4).

### 6.3 farmgui (owners)

farmgui's owners record the operator's widened authorization in `AGENTS.md:38-43` as h describes
(§7, step 3).

## 7. Deployment order, migration and rollback

Three generations of code read labels differently:

| Revision | What it reads |
|---|---|
| Before #57 | Bug and an empty text only (`git show 8e49d2d^:agent/router.py`, lines 21-25); `request-repair` has no label refusal |
| From #57 (`8e49d2d`) to `e715eb8` | the group named 功能 and the Bug rules (§3.2); TestBot runs it (`911159b`, plan line 125) |
| D18 | Bot or 功能 (§4.1), 修改, no Bug rule |

Production runs one of the first two; this repository does not record which
(`docs/development-workflow.md:5`). The running service's revision is the `revision` in the heartbeat
its `serve` writes, `service-heartbeat.json` under `local_root` (`agent/service.py:191`,
`agent/config.py:129`); code before #46, which added the heartbeat, and so before #57, writes none. A
read-only `doctor` describes the checkout it runs from (`agent/doctor.py:149`): run from production's
installation checkout, a `skills` block in its report means that checkout is at #57 or later
(`:146-164`, added by #57). The two differ, for example, after a checkout update whose redeploy the
script refused as busy (`scripts/redeploy-farmbot.ps1:63-64`); the receiver then routes with the
heartbeat's revision while workers' commands, `request-repair` among them, run the checkout's code
(`agent/scheduler.py:175-176`).

**Safe order.**

1. With the operator's go-ahead, read production's heartbeat revision and run `doctor` read-only from
   its installation checkout; report both.
2. Land farmgui's grant change (h), before any instance runs D18: D18 code states the widened grant
   in `fix`'s AUTHORITY from its first launch. It covers every fix the running code can already
   start, including a small UI change asked for in a conversation (`docs/operating-contract.md:119`),
   so it needs no D18 code.
3. Settle TestBot and move it to D18.
4. In Linear, rename 功能 to Bot, then add 修改.
5. Run the live checks of §9 on TestBot.
6. In a separately authorized release, settle production and deploy D18. The redeploy script refuses
   while any job is queued, running or waiting for a resource (`scripts/redeploy-farmbot.ps1:59-65`).
7. Tell the team (§6.2).

Do steps 4 to 7 on one day. Between steps 4 and 6 production runs its old code against the renamed
group. Either generation still starts `fix` on a Bug card delegated without text: code before #57
reads only Bug, and code from #57 finds no 功能 group and falls through to the Bug rule
(`agent/router.py:78-81`). Neither knows 修改. That window is the transition g asks about. If
production runs #57 or later, it also has the second row's exposure below during the window; the
alternative, deploying production before the Linear change, would put D18 on production before
TestBot could check 修改 live (third row below).

**State on 2026-09-28.** The operator made step 4's Linear change first: the group is named exactly
Bot, still single-select, with the children 修改, UI and Code. Steps 1 to 3 and 5 to 7 remain, and
the window above is open until production runs D18. Until then neither bot knows the group: 修改
starts nothing by itself, a Bug card with any Bot child starts `fix` when delegated without text, and
a 「修复」 in a Bot/UI or Bot/Code conversation starts a first fix, since no running code refuses it
there (second row below). No open card carried a Bot child that day. Until production runs D18, tell
the team nothing (§6.2), and put a Bot label only on the cards the operator names for §9's live
checks, delegated to TestBot alone.

**Other orders and windows.**

| Order or window | What it does |
|---|---|
| 修改 added before the rename, on a host running code from #57 (TestBot before step 2; production if #57 or later) | 功能/修改 is an unknown 功能 child there: the delegation opens a conversation saying the label maps to no feature work, 「修复」 there is refused, and with Bug the delegation asks which label to remove and adds `needs-more-info` (`agent/router.py:42-43`, `:50-52`, `:71-72`). No fix can start on that card on that host. |
| Renamed while a host runs code from #57 (TestBot if step 2 is skipped; production, if #57 or later, until step 6) | Bug with any Bot child, delegated without text, starts `fix`, and a 「修复」 in a Bot/UI or Bot/Code conversation starts a first fix, because the refusal finds no 功能 child (`agent/__main__.py:160-162`). Nothing logs it. Only cards labelled during the window are exposed. |
| Production on D18 before the Linear change | 修改 does not exist yet while Bug no longer starts `fix`, so every fix on production needs a 「修复」 reply until step 4. |
| Team told before production runs D18 | Production ignores 修改: a card with only 修改 opens a conversation and needs 「修复」; one that also has Bug starts `fix` as before. |
| Production on D18, team not told | Bug-only delegations open conversations; their first message (§4.2) says how to get a fix. |
| farmgui's grant after production runs D18 | A 修改 UI change that needs an export has no standing grant until the change lands. |

**Migration.** No schema, stored-data or config change. The ledger stores label groups as name pairs
and accepts any group name (`agent/ledger.py:108-120`); a stored snapshot keeps 功能 until the card is
next read, and nothing routes on a stored snapshot (§3.2). Existing jobs keep their skill (§3.2). An
event still pending at the deploy is routed by the new code on the card's current labels
(`agent/receiver.py:208-212`), so a Bug card delegated just before the deploy opens a conversation.

**Rollback.** Settle the service and deploy the previous revision's code and skill files together
(`docs/operating-contract.md:613-614`). Every job D18 starts is `fix` or `chat`, which every revision
runs, but a `fix` job started for a change rather than a defect then resumes under the older skill,
written for defects only (`skills/fix/SKILL.md:3`, `:235-236`, `:333-335`). The settle check does not
wait for a job parked on a question (`scripts/redeploy-farmbot.ps1:59-61`); doctor lists the parked
and blocked ones (`agent/doctor.py:84-91`). Have those finished or stopped first, and tell whoever
asked for a delivered or stopped change that a later request continues it under the older rules. Keep
the group named Bot: older code then reads no group, starts `fix` on a Bug card delegated without text
again and ignores 修改, so the team goes back to adding Bug. It also reads Bot/UI and Bot/Code as no
group, the second row's exposure for as long as the rollback lasts (`agent/router.py:78-81`;
`agent/__main__.py:160-162`), so ask the team not to delegate UI or Code cards to FarmBot meanwhile,
or restore the 功能 name as follows. Do not rename the group back to 功能 while 修改 exists, which is
the first row's order again; to restore the 功能 behaviour of code from #57, remove 修改 from every
card and from the group first.

## 8. Failure modes and edge cases

| Case | What happens |
|---|---|
| Bug-only card delegated | A conversation with the §4.2 message. The chat worker investigates or asks (`skills/chat/SKILL.md:46-48`); 「修复」 then starts `fix`, which receives the conversation's messages (`agent/ledger.py:1661-1662`) and its summary, stored on the conversation's item (`:1657-1659`) and handed over as `prior_context` (`agent/scheduler.py:157-162`). A re-routing reply that asks for a fix is the conversation's first message, and the conversation requests the fix at once (§4.4). |
| 修改 on designer-only work | `fix` starts; a wrong table value goes back to the designer (`references/repo-map.md:57-61`), art FarmBot cannot make is a question, and the delivery may be "no change". |
| 修改 work that needs a new screen, message or table | The worker stops before that part and finishes blocked, naming the Bot/UI or Bot/Code label the card needs (§4.5; §10, question 2). |
| A question as the reply that re-routes a 修改 card's delegation | `fix` starts, posts its start comment and answers first (§4.2, §4.5; §10, question 1). |
| Label changed after a job exists | The job keeps its skill (§3.2). `fix` checks no label, so a fix whose card is relabelled UI or Code continues until someone presses Stop, and a later request continues it again (§4.4). |
| Two Bot children, or an unknown one | Explaining conversation (§4.2, §4.6); a first start there is refused (§4.4). |
| Group renamed again or misspelled, such as `bot` or a trailing space | Names match exactly (`agent/router.py:21`; `agent/linear_api.py:115-117` keeps the name Linear returns): on both bots every card reads as unlabelled, every delegation opens a conversation with the §4.2 message, and a start request there starts `fix` on UI and Code cards too (§4.4). After any change to the group, repeat live check 1 of §9 before telling the team. |
| UI or Code card | Explaining conversation on every host until those workers exist; a first start there is refused (§4.4). |
| Delegation while another session's work is active | With one Bot child whose skill this instance runs: declined with a note (`agent/receiver.py:265-268`), as a Bug card is today. Otherwise, Bug-only cards included: the empty delegation is forwarded to that work as 「（无正文）」 and resumes it if it waits for input, as for a card without Bug today; the delegator reads the forwarding notice, or 「收到回复，原工作项已恢复，worker 会先读取你的回答。」 on a resume, never the §4.2 message (`:269-278`). A reply in the delegation session routes by §4.2 (§4.3). |
| Mention on a 修改 card never delegated | Conversation; a request needs a recorded delegation (`agent/ledger.py:1632-1634`). |
| Operator `enqueue --skill fix` on a UI or Code card | Runs `fix`: `enqueue` reads no label (`agent/service.py:145-160`). |
| 修改 UI change needing an FGUI export before farmgui's grant covers it | No standing grant (h); §7 lands the grant before any instance runs D18. |
| Closed card | No work item is created (`agent/ledger.py:709-710`). |

## 9. Testing

Offline, with the existing unittest patterns (temporary state, stub Linear, fake workers):

| Area | Cases |
|---|---|
| Router | 修改 starts `fix` whatever the text (§10, question 1); UI and Code start an enabled `fgui` or `feature`; each child with Bug, Improvement or a 部门 label routes on the child; Bug alone, Improvement alone and no label open a conversation with the §4.2 message, and with the generic one when `fix` is not enabled; a child the instance does not run, an unknown child and two children explain; a standalone 修改, UI or Code label, or one in another group, is not a Bot label; a group still named 功能 routes like Bot; a re-route uses the same table and, with no Bot child, keeps its reply as the text; a mention never routes to write work and keeps the generic first message. |
| Receiver | `ReceiverBase`'s default card (`tests/test_receiver.py:44`) gains Bot/修改, since its Bug label is what makes every bare delegation in that file a `fix` item; the re-route tests (`:897-930`) are rebuilt on a delegation declined because of another session's work; a Bug-only delegation while another session's item waits for input; the §4.2 message is not put before a forwarding notice, and a named instance's names it; the acknowledgements. |
| CLI | `request-repair` starts `fix` on a 修改 card and on a card without a Bot label, with or without Bug; refuses a first start on a UI or Code card with the §4.4 text, and on an unknown Bot child or two children; continues an earlier fix whatever the label; still refuses before reading Linear when `fix` is not enabled. |
| Fixtures that would pass for the wrong reason | `tests/test_service.py:55`, `tests/test_end_to_end.py:51` and tests built on `ReceiverBase`, such as `tests/test_resume_work.py:103-125` and `tests/test_receiver.py:384-400`, get their `fix` item from a Bug card and do not all assert the skill (`tests/test_end_to_end.py:122-143` does not). Without the Bug rule they pass on a conversation item; they get a Bot/修改 card and assert the skill. |
| Texts | `tests/test_skills.py:88-99` for the templates; `tests/test_dispatch.py` pins `fix`'s new AUTHORITY with its export sentence and `chat`'s unchanged bytes. |

Run the full macOS suite and the Windows CI run of the same commit. D18 changes no process,
installation or Unity code, so the Windows-host checks `AGENTS.md:99-100` asks for do not apply; CI
is not that host.

Live on TestBot, after step 4 of §7, on cards the operator names. Their comments, labels, branches and
draft PRs are real, and no card is delegated to both bots. Checks 3 and 4 use cards on which TestBot
has no earlier fix, which a request would continue (§4.4).

1. Read a Bot/修改 card: its `label_groups` is `[{"group": "Bot", "label": "修改"}]`.
2. Delegate a card labelled Bug and Bot/修改: a `fix` item and the new acknowledgement, no question
   about labels. Press Stop once both are confirmed, unless the operator wants the fix.
3. Delegate a card labelled only Bug: a conversation with the §4.2 message. Reply 「修复」: a `fix`
   item in the delegation session. Stop.
4. Delegate a Bot/Code card: the explaining conversation. Reply 「修复一下」: refused with the §4.4
   text, and no `fix` item.

After step 6, production's heartbeat revision and a read-only `doctor`; nothing else runs live there
without the operator.

## 10. Questions settled on 2026-09-28

The operator settled questions 1 to 5 as recommended and allowed the read of question 6.

1. **Reply text on a 修改 re-route.** Delegation text exists only as a reply that re-routes (§4.2),
   usually in a session declined because another session's work was active. §4.2 applies the label
   rule to that reply, so a question or 「不用了」 there starts the fix once that work has ended, and
   the fix worker answers first. The alternative keeps Bug's old rule for 修改 alone and sends such a
   reply to a conversation first. *Settled: the label rule.* The person chose the workflow by
   labelling, and one rule for three children is easier to teach; the cost is a start comment on a
   card whose delegator only replied with a question.
2. **修改 work that turns out to be UI or Code work.** When a 修改 job finds it needs a new screen,
   protocol message or config table, the worker could stop there or ask the card's owner (its
   assignee, else the person who delegated it) whether to go on. *Settled: it stops before that
   part and finishes blocked, naming what it found and the Bot/UI or Bot/Code label the card needs
   (§4.5).* No UI or Code worker runs yet, so that part stays with people; going on with the owner's
   agreement would have `fix` do work that D18 e classes as UI or Code, which would need the operator
   to amend D18 e.
3. **Designer data in a 修改 fix.** `fix` may change designer tables through `designer/configgen`
   (`references/repo-map.md:6-9`, `:34`), while D18 c keeps designer-only bugs away from `fix`.
   *Settled: leave `fix`'s write scope as it is.* D18 changes which cards reach `fix`, and a
   wrong table value already goes back to the designer (`:57-61`).
4. **The closing rule for every Bot card.** The feature spec's team rule, not to move a 功能 card to
   Done or Canceled before FarmBot's delivery comment (§4.2 there, lines 158-163), was written for UI
   and Code. *Settled: announce it for every Bot card* (§6.2, item 9), since a closing status
   cancels unfinished work on any card (`docs/operating-contract.md:124`), 修改 fixes included. Its
   other team rule, that a delegated card belongs to FarmBot until someone removes the delegation
   (D6), waits for the UI and Code workers, because the cancellation it relies on covers only their
   jobs (§9.8 there).
5. **The FGUI export grant and the approval reviewer.** The Codex approval reviewer trusts the dispatch
   AUTHORITY (`agent/dispatch.py:5-7`), which has no export sentence and defers to "that repository's
   own rules" (`:16`), yet the reviewer does not get the worker's repository `AGENTS.md`
   (`docs/operating-contract.md:144-151`). This predates D18, which gives it more cases.
   *Settled: state the export grant in `fix`'s part of the AUTHORITY in the same change as h*,
   updating its hash test deliberately (§4.5, §5).
6. **Production's revision** (§7, step 1): it decides whether production is exposed while the group is
   renamed and production is not yet on D18. *The operator allowed the read-only check on 2026-09-28;*
   its result belongs to the release that implements this design.
