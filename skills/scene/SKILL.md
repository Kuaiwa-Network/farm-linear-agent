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

The parent declares exact `scope.assets` paths and `scope.validation` (`source`
or `unity`); their necessary `.meta` files are included by the controller. For a
legacy task with no scope, ask the parent to use `authorize-scene-scope` while
you are parked at `scene-scope-missing` with `await-input --reason waiting`.
This is an engineering coordination gap, not a new visual approval.
Record `depends_on` for required sibling artifacts; a dependency on FGUI includes
its certified parent installation. Never hold Client waiting for another writer.

For an outside prerequisite, commit and checkpoint your asset edits, release
resources, then `yield-presentation --input FILE` with `reason` and exact `paths`.
Exit. The controller waits for quiescence before the parent may write; the parent
commits only those repair paths and calls `resume-presentation`, preserving your
original entry and invalidating only affected evidence. Do not alter inherited
parent repair files or create replacement tasks to bypass a lock.

Retain the parent's `plan.ui.validation` decision and reasons, actual baseline,
named targets, allowed changes and preservation limits in this task's plan and on
resume. Independently choose sufficient behavior/source, visual or pixel checks
from acceptance criteria, actual changes and visual regression risk, reassessing
changed requirements/dependencies. Pixel comparison needs relevant references and
comparable rendering; honor explicit precision requirements and retain evidence
gaps. Choose routine methods without asking humans. Preserve unrelated fonts,
outlines, layout and shared styles. Approximate preview differences alone are not
defects; stop visual iteration after required checks pass. All normal resource
and delivery checks still apply.

Missing art/assets or unclear layout are grouped questions identifying your task
key and required decider. Ask for actual files/their location with known format
and dimension requirements. Checkpoint and use `await-input`, then exit. Do not
invent art, infer placeholder approval or ask for previously recorded answers
again. The parent continues independent server work and routes actual human
answers back to this task with their attribution. Re-read uploads and decisions
on resume, and park again for unanswered questions.

Commit and verify the actual scene/prefab dependencies and visible behavior;
request Unity only through an exact-commit reserved slot and release through
quiescence checks. If `scope.validation` is `unity`, run `verify-scene-assets`
in that reservation before release. It proves local asset loading, not final
gameplay or visual acceptance. Report actual checks and gaps. New tasks leave
Client publication to the parent and finish with `prs: []`; legacy tasks retain
their verified Client draft requirement. Save evidence and finish only this presentation
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
