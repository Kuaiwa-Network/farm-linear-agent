# FARM-1247 — end-to-end attempt, blocked

The operator selected [FARM-1247](https://linear.app/kuaiwagames/issue/FARM-1247)
for Phase 1's real-delivery criterion. The issue says task 410, “累计解锁2个桌子”,
completed after only one table was unlocked.

The previous quick tunnel was running but repeatedly reported `Unauthorized: Tunnel
not found`. It was restarted; the operator pasted the new webhook URL into Linear
and re-delegated through the UI. The genuine webhook created work item
`9440b065-87da-4494-8dc0-95713c6c0ef1`, session
`9641da61-238d-4ff2-9470-36e094854a95`. A fresh Codex worker claimed the item,
posted the started marker, investigated in its own worktrees, checkpointed evidence,
posted a blocker, and finished the ledger item as `blocked`.

## Finding and limit

The worker's static inspection at common `ce74548` found all three shelf rows have
DefaultUnlock false, while statistic 3004 (`unlockListFoodCount`) has initial value
and minimum 1. Global function 5001 adds 1 on first unlock. Task 410's target is 2.
Thus the source configuration explains how the first explicit unlock reaches 2.
The extracted rows and arithmetic are [source-evidence.json](source-evidence.json).
This is source evidence, not a reproduction on the public test server.

Farm-Contract `openspec/specs/shelf.md` §3 still specifies one initially unlocked
table (`DECIDED:Rosetta@2026-09-02`), conflicting with the current source tables.
The fix skill requires a worker to report such a conflict and finish blocked;
it gives the worker no Farm-Contract worktree or authority to change that contract.

No code or generated configuration was changed, no PR was opened, and no runtime
verification was claimed. **Phase 1 criterion 5 remains unproved.** This run proves
the real webhook-to-worker-to-blocker path, not delivery of a fix.

## Reviewable outcome

[FarmBot's blocker comment](https://linear.app/kuaiwagames/issue/FARM-1247#comment-14186678-72ee-4749-8649-9a3469624749)
records the exact conflict and proposed next steps in Chinese. The issue's status
and assignee were not changed. The operator was asked to choose the intended initial
table count before retrying. If zero is intended, the contract must first be corrected,
then statistic 3004's initial/minimum values and exported configuration must follow
the approved release path. If one is intended, clarify and restore the default-table
configuration instead. The config-export capability remains deferred in Plan 1b.

Raw worker logs, checkpoint and outcome remain in the original checkout at
`.local/runs/9440b065-87da-4494-8dc0-95713c6c0ef1/`. This report was recorded by the
rehearsal controller from the ledger, worker output and evidence file after the
worker finished.
