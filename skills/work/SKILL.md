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
The launch AUTHORITY and current repository scope govern every attempt. Changing cwd or
spawning a Codex agent never extends repository permission. Revalidate actual
issue facts, named human decisions and checkpoint evidence on every continuation.

## Shared behavior first

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
decision/tool/asset. A named human's retry request in this job's inbox authorizes
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
The legacy code capability's E gate becomes this verified task join; a separate
human merge of the UI draft is unnecessary for same-issue Client integration.
Keep the task's verified changes on the owned branch and include every task's
draft PR in the general delivery comment/evidence.
