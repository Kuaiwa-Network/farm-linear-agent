---
name: scene
description: Reusable scene, prefab and display capability for a delegated general job.
---

Read the parent job's task request, settled shared OpenSpec and current
Farm-Client instructions. This is presentation work within that job, not a
separately labelled issue. Follow the worker CLI, current write scope and the
existing claim, publication, foreign-work and resource rules. Use
`execution.python` with native argument lists.
Follow the claim-token storage procedure in `references/worker-cli.md`:
on POSIX use mode 0600; on Windows keep the inherited DACL and do not change ACLs. Store only
your own claim, never the parent's token.

Author only the task's scene/prefab/display assets and required resource
references. Preserve Unity metadata identities. Do not implement unrelated
gameplay/server code, export FairyGUI, merge, deploy or configure the host. New
or changed behavior requires the parent to settle the shared OpenSpec first;
pure appearance corrections follow the existing requirements and actual design
references. No duplicate OpenSpec project is needed in the Client repository.

Missing art/assets or unclear layout are grouped questions identifying your task
key and required decider. Ask for actual files/their location with known format
and dimension requirements. Checkpoint and use `await-input`, then exit. Do not
invent art, infer placeholder approval or ask for previously recorded answers
again. The parent continues independent server work and routes actual human
answers back to this task with their attribution. Re-read uploads and decisions
on resume, and park again for unanswered questions.

Commit and verify the actual scene/prefab dependencies and visible behavior;
request Unity only through an exact-commit reserved slot and release through
quiescence checks. Report actual checks and gaps. Publish this job's Client draft
through `verify-publication`. Save evidence and finish only this presentation
task; the parent integrates gameplay later on the same owned issue branch. Stop
or withdrawal of the parent revokes your work and preserves recovery evidence.

Read `issue-context.parent_job` and `task.instructions`; check the exact contract
references the parent recorded. Use read-only Git in `reads.Farm-Contract` to
inspect that exact commit, including `git show COMMIT:path` for an unmerged
OpenSpec. Never change the read checkout or assume its default-branch files
already contain the parent's change. Report task progress as thoughts and retain any
visual-review notices. Do not post a separate started/blocker/delivery outcome
comment or close the issue's shared conversation. Task `finish` JSON carries
`summary`, `verification` and its verified `prs`, or an explicit `no_change`
reason; the general job posts the final outcome comment.
The controller rechecks clean owned Client HEAD and actual draft provenance at
finish. Commit all intended changes first; a no-change claim must have no tracked
or untracked changes and zero diff from this task's immutable entry baseline.
