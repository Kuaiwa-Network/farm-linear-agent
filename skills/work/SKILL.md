---
name: work
description: Own one delegated issue through shared OpenSpec behavior, parallel presentation and server work, Client integration and verification. Ask humans for decisions and missing assets.
---

# General issue worker

One job owns the entire delegated issue. Bug, improvement and feature describe
the task, not your capabilities. Ignore Bot/修改, Bot/UI, Bot/Code and historical
功能 children as workflow selectors. Never ask a person to relabel or split the
issue just because it needs a new screen, protocol, scene or table.

Read the operating contract, worker CLI, repository map and the current root's
instructions. Use `execution.python` and native argument lists. Follow the
claim-token storage procedure in `references/worker-cli.md`:
on POSIX use mode 0600; on Windows keep the inherited DACL and do not change ACLs. Never share
a claim token with a presentation task.

When `tools.unity_pool.mode` is `interactive`, all Unity tests use the fixed Editor
pool. Request `unity_slot --mode interactive --commit FULL_HEAD_SHA` and exit;
after resume verify the reservation commit/instance, use MCP `run_tests` and
`get_test_job`, retain actual nonzero results and release quiescent. This host policy
overrides legacy batch directions in the capability references. A converted batch
request has not executed any tests. Never launch or close an Editor.
The launch AUTHORITY and current repository scope govern every attempt. Changing cwd or
spawning a Codex agent never extends repository permission. Revalidate actual
issue facts, named human decisions and checkpoint evidence on every continuation.
Start with `issue-context --view resume`; use the full view when stale, when a decision
references omitted text, or when a required fact is missing. Batch independent local
reads and inspect selected fields/hashes, rather than dumping entire old reports.
Before preparing delivery, validate the outcome with `finish --check-input` as documented
in the worker CLI. Reuse `verify-publication.foreign_work` for that repository; preserve
fresh publication checks and the final issue refresh. Preflight is shape validation only.

## Shared behavior first

The requested outcome and attributed human scope limits govern the whole job,
including retries and closing. Capabilities do not add requirements. Do not turn
nearby UI defects, another issue's protocol changes or a newer default branch
into extra implementation, asset questions or waiting conditions. Ask for an
asset only when the accepted outcome actually requires that asset. A person's
choice to keep scope never means waiting for an unrelated feature.

Follow the code reference's **Issue scope and consumer inputs** before generation,
on every resume and before closing. Recheck older plans that demand a full sync:
retain necessary checks and evidence, but remove incidental waits after recording
why they are not dependencies. Continue required acceptance/write-back and finish
when this issue's work and gates are satisfied. Unresolved attribution stays a
verification gap; this rule does not grant a test or publication waiver.

Begin in Farm-Contract. Read the existing OpenSpec and the issue's design inputs.
New or changed behavior, including UI-only behavior, needs a shared OpenSpec
change. Define triggers, displayed data, interactions, loading/empty/error states
and acceptance scenarios together with required protocol/config changes. Follow
Farm-Contract's own OpenSpec process and gates. Pure layout, font, art or spacing
corrections use the current behavior contract and design/visual review; they need
no invented protocol change or duplicate OpenSpec project in farmgui.

Use `../feature/SKILL.md` as the code capability reference for contract,
configuration, server, Client and closing steps. Its legacy Bot/Code intake and
prohibition on coordinating UI do not apply to this general job. Other
repository-specific restrictions, tooling, provenance and verification do apply.
Stage A may be `skipped: existing contract` only after recording the unchanged
requirements and why the issue restores or only visually changes that behavior.
Never leave unresolved behavior for a presentation task to invent.
Commit the settled contract and record its exact issue-branch HEAD in the plan
before delegating. Give tasks the scenario/file references and that commit; their
detached Farm-Contract reads borrow the owned clone's objects, so read-only
`git show COMMIT:path` can read an unmerged OpenSpec without changing checkout.

Retain stages A-G in the durable plan. Mark B/C/D skipped with explicit reasons
when no configuration/server work is required. A complex bug gets every required
stage; a small change skips unnecessary stages. Neither task label nor size adds
authority to merge, deploy or modify designer-owned values.

## Presentation and server in parallel

After A is done or the existing contract is checked, save a valid checkpoint and
delegate required presentation tasks with `delegate-task --input FILE`. JSON:

```json
{"key":"presentation-ui","capability":"fgui","instructions":"Implement the issue's named panels against the recorded shared OpenSpec and mockups; retain certified visual review/export and report missing assets."}
```

`fgui` is the reusable FairyGUI capability: source authoring, actual visual
approval, certified native export, owned Client installation, guards and Unity
package loading. `scene` covers scene/prefab/display work in Farm-Client. Give each
task exact scope, contract scenario references and dependencies. These are
controller-managed subagents of this job, not separate issues or public worker
categories. Each has its own claim, sandbox, checkpoints, receipts and run logs;
their owned repository branches belong to this job. Do not launch an independent
CLI or use a shared parent token to bypass task accounting or repository grants.
Delegate independent tasks together. If scene work depends on installed FGUI
assets, wait for FGUI delivery before creating that scene task: a task holding
Client must never wait for another task to write the same repository.

Continue required common/config/server stages while presentation runs. Check
`issue-context.tasks` and the parent inbox at every checkpoint and before long
operations. If a behavioral ruling changes, notify the affected task through an
actual human message and recheck its contract before downstream work continues.

Both branches may ask humans. Every question identifies its task key and the
person whose answer is needed. Read the actual message and use `answer-task
--task KEY --message-id ID` to forward it with its original author and timestamp
to each task it answers. Route partial answers only to relevant tasks; the task
rechecks all its outstanding questions and parks again if necessary. Never
fabricate an attributed answer, interpret silence as approval, or make people
repeat saved answers. Comments remain evidence; a session reply or bot mention
supplies the continuation. Consolidate related questions into readable rounds.

If `stage.coordination_only` is true, this attempt has no repository write roots.
Route already-saved attributed answers with `answer-task`; consume handled inbox
messages, checkpoint, then `await-tasks` and exit while presentation is unfinished.
Do not ask for the same answer again, implement changes, publish or request tests
in this attempt. Once required tasks have delivered with certified teardown,
save a current handoff checkpoint and `handoff-repository` to `Farm-Client`.
The normal server/config/presentation gates still apply. Keep the existing
candidate, baseline and reports; the coordination detour is not a new test round.

Read `human_question` as well as `pending_question`. A reply, checkpoint or task
wake does not clear that round. Once every question in your own round is resolved
by actual attributed inbox replies, use `resolve-question` with the current
question ID and those message IDs; tasks acknowledge their own rounds. Partial
answers retain the round or ask only what remains. The controller manages the
issue's `待回复` label across all jobs/tasks; never remove it yourself. Question
notices lead with the issue key and `待回复` and explain the session/mention reply
routes. See `references/worker-cli.md` for the command and upgrade behavior.

Missing artwork/assets are a normal question: name exactly what is missing,
where it is used, required dimensions/format when known, and ask for the files or
their location. Re-download and verify current uploads on resume. Never invent
final art or infer permission to use a placeholder. Ask explicitly if a temporary
placeholder would help. Only dependent work pauses; continue independent server
work. Missing tools, publisher configuration or license are named host gaps,
never authority to install/configure them yourself.

## Client join and completion

Explicitly complete or skip with reasons configuration stages B/C and server D
before entering Client. After server/config work, inspect all required presentation tasks. Use
`await-tasks` and exit when they are unfinished. Already certified tasks need no
join wait; proceed to the required integration or human decision. This releases the worker attempt
without losing source, questions or task state. The controller resumes you for
new task questions, failures or certified completion; no live polling process
waits for a human. Replies arrive at the general job and are routed with
`answer-task`. If every remaining task needs human input, use the normal grouped
`await-input` question instead, preserving all task keys and pending decisions.
The controller also wakes you for a new task question while your own human
question is pending. Consolidate the new task questions with your saved question;
the wake is not an answer. Preserve unanswered decisions and park again without
rerunning work that still depends on them.
For a failed/blocked task, inspect retained evidence and ask for the missing
decision/tool/asset when one is actually required. Own ordinary engineering
recovery instead of waiting for an unnamed engineer or asking the issue owner
to find hashes/controller commands. For missing dependency registrations already
present in unchanged Client baseline packages, direct the retained UI task to
audit source references/lifetime and use `authorize-ui-guard-repair`. That bounded
controller step preserves its entry HEAD and approval, and requires fresh exact
commit scope, both actual guards and Unity loading before delivery. Route the
actual continuation to the original task; do not create a replacement task or
reset the baseline. Other unrelated fixes do not gain write scope from this command.
A named human's retry request in this job's inbox authorizes
`retry-task --task KEY --message-id ID` after certified cleanup. Never retry a
terminal task by inventing a new key to bypass that human gate.

Parent Client implementation cannot start until required server work is done or
explicitly skipped, and all presentation tasks are delivered with certified
teardown. Check the delivered artifacts, component IDs, exported dependencies,
scene/prefab contents and task verification against the accepted behavior. A
preview, folder's existence or an unverified 'ready' claim is insufficient.

Presentation tasks may author/install Client assets before gameplay code starts;
the scheduler serializes all writes to the job's same repository. A task retains
its Client stage across human/resource waits until delivery, including an
immutable controller-selected entry HEAD for the UI-only diff checks; later
presentation tasks build on that task's committed output. Preserve the
controller-selected Client baseline and same issue branch across installation
and later code integration. Never reset that branch to main or overwrite a task's
changes. Record source/task heads and verify actual UI binding and full gameplay
behavior at the final Client HEAD. Package loading alone proves neither.

If no presentation is required, record E skipped and proceed. Finish only when
every required task has delivered, all requested work is covered, verification
limitations are explicit, and the job's relevant drafts are listed. Reuse the
existing foreign-work, destination, claim and resource checks. Stop/withdrawal
ends the whole job including its tasks and retains recovery evidence. Workers
never merge or deploy.
For Client verification, use feature stage F's reviewed baseline comparison.
Use `continue-verification` for a completed quiescent baseline's original candidate and `compare-verification`
for the retained evidence. Follow the exact token, exit and phase-reporting procedure in
[baseline comparison](../../references/test-baseline.md). The eligible verdict
permits delivery with unchanged outside-scope baseline failures recorded after the remaining required work is complete.
Use `issue_scope` to bind the actual issue acceptance and full diff to affected tests. A shared export package,
fixture/directory/loader or unknown shared-state cause alone does not make all its tests mandatory. Expand
scope only with a concrete changed behavior/dependency or observed regression. Retain matching outside-scope
failures without proving whole-package independence or diagnosing old debt; do not request resources for that.
Re-review inherited broad affected lists offline and reuse valid QA/reviews instead of repeating them.
Do not wait for a green baseline or unrelated main fixes, re-ask draft-delivery permission, or repeat a
completed unchanged pair for diagnostic-only differences; use the documented offline comparison review.
The legacy code capability's E gate becomes this verified task join; a separate
human merge of the UI draft is unnecessary for same-issue Client integration.
Keep the task's verified changes on the owned branch and include every task's
draft PR in the general delivery comment/evidence.


Service verification follows feature stage D's fixed candidate round. A queued or resumed
server lease is for its recorded commit; generic main refresh does not restart the round.
Reuse exact-candidate evidence after maintenance. Do not release/requeue or merge a newer
main just because its tip moved. Publication still verifies the configured destination,
current delegation and the round's pinned upstream ancestry; no old report verifies a new SHA.
