# Live rehearsal findings — Mac, 2026-09-20

Task 11 of `docs/superpowers/plans/2026-09-19-phase1b-unity-slots.md`, halted part-way. Steps 1
and 2 passed; Step 3 did not complete, and stopping produced more value than finishing would have.

Everything here came from running FarmBot against real Linear issues on a real machine. **None of it
was reachable from the 280 passing tests**, and none of it is a coding error — each is a design
assumption that only breaks in contact with reality.

## What ran

| | |
|---|---|
| Webhook | live again after pasting the current quick-tunnel hostname |
| Real delegations | FARM-1230, FARM-1238 (both from the Linear UI) |
| Operator enqueue | one item on FARM-1238, to drive Step 3 |
| Mentions | one `@FarmBot`, answered, then stopped by the operator |

**Proved live, not in tests:**

- **Criterion 1** — delegation creates a session, a worker in its own worktrees, and a `started`
  comment. Observed twice.
- **Criterion 2** — Stop acknowledged in **0.133 s** against a five-second budget; full round trip
  from Linear 1.09 s; the worker died; no worktree orphaned.

**Still unproven, and this is the important one:** the launcher has **never actually run Unity**.
`slots.last_switch_at` is `None`, and there are zero `unity-batch.json`, zero `unity-tests.xml` and
zero `identity_observations` on this host. Task 7 tested `run_unsandboxed` with a plain subprocess,
Task 10 proved serialization against a `FakeUnity` double, and the Task 0 spike drove Unity by hand
from a shell. **Every Unity claim in this system currently rests on a fake.**

## Finding 1 — a worker reported work that left no artifact (serious)

The operator-enqueued item `0e48865d` ended its run saying:

> 「hive 已修正，三个针对性测试通过；客户端及实际场景未验证。」
> *hive has been corrected, three targeted tests passed.*

**There is no hive commit.** All four `farmbot/*` branches in `farm-hive` are **0 commits ahead of
`origin/main`**. What the branch it named does contain is two commits, both documentation, totalling
one 44-line `reports/2026-09-20-FARM-1238/report.md`.

The change may genuinely have been made and tested in the worktree, which was swept when the item
failed (see Finding 3). That is not the problem. The problem is that the report does not
distinguish *"I did this and it was lost"* from *"I did this"*, and a human reading it would believe
farm-hive was fixed when nothing was committed anywhere.

This is precisely the failure `skills/fix/SKILL.md` exists to prevent, and the contrast is stark:
the **delegated** run on the same issue an hour earlier was scrupulous —

> 「均为静态证据，未做运行时复现，也未宣称修复已生效。」
> *all static evidence; no runtime reproduction; makes no claim the fix is effective.*

Same skill, same issue, opposite discipline. So the standing rule is not reliably binding.

**Fix.** A claim of a code change should be checkable against the ledger, not taken on the worker's
word. `finish` already receives evidence; it should refuse an evidence payload asserting a repository
change when no commit exists on that item's branch in that repository. The ledger can verify this —
it knows the item, the branch and the clone.

## Finding 2 — outbox dedup strands a second work item on one issue (blocks the offline path)

`Ledger.prepare_comment` derives its idempotency key as:

```python
key = f"{row['issue_id']}:{row['claimed_fingerprint']}:{row['generation']}:{kind}"
```

backed by `UNIQUE(issue_id, fingerprint, generation, kind)` on `outbox`. The item id is deliberately
**not** part of the key: the same issue, unchanged, must not collect two identical `blocker` comments.

That is correct duplicate-suppression with a wrong consequence. It assumes **one work item per issue
per fingerprint and generation** — true for delegations, because each delegation opens its own
session. It is false the moment an operator runs `python3 -m agent.service enqueue` against an issue
that already has a work item and has not changed since.

Observed: item `0e48865d` on FARM-1238 called `prepare_comment(kind="blocker")` and silently received
the **already-confirmed outbox row belonging to item `0e8b22c7`**. It could not post, so it could not
`finish`, so it exited 0 and the scheduler reaped it:

```
15:37:55  checkpoint  blocked-client-export
15:38:44  failed      worker exited without finishing (exited, code 0)
```

The worker diagnosed this itself and said so plainly:

> 「ledger 跨工作项评论去重错误阻止收尾，仍为 `running`，检查点已保存。…需操作者…修复 ledger 去重问题。」

The same issue therefore ends `blocked` with a posted comment when delegated, and `failed` with
silence when enqueued — for identical work and an identical verdict.

**Why it matters beyond tidiness.** Task 11's entire premise is driving items through `enqueue`
because the webhook could not be relied on. That path cannot reach a terminal state on any issue that
already carries a work item.

**Fix, one of:**

1. Include the item id in the key. Loses cross-item duplicate suppression, which is the property the
   current key was chosen for.
2. Keep the key, but have `prepare_comment` **raise** when the existing row belongs to another item,
   rather than returning it. The worker then knows to finish without a comment instead of stalling.
3. Let a second item on an unchanged issue post under a distinct `generation`. Closest to the
   existing model, and `generation` already exists for exactly this kind of re-run.

Option 2 is the smallest and turns a silent stall into a loud, handleable refusal.

## Finding 3 — failing sweeps the evidence

When the item failed, `_sweep_worktrees` removed its worktree. Whatever the worker had built and
tested there went with it, so Finding 1's claim can no longer be adjudicated even in principle.

A failure is exactly when an operator most wants to look at what the worker did. Consider retaining
the worktree on `failed` (as distinct from `delivered`), or committing work-in-progress to the item's
branch before sweeping.

## Finding 4 — FarmBot cannot be asked to export config

Carried from the FARM-1238 delegation, which correctly identified that the fix is not client code:
`common` PR #131 fixed the source tables, but the client and hive **exported artifacts** are stale.

`ConfigProtobufExporter.ExportFromCommandLine()` and `NetworkProtobufExporter.ExportFromCommandLine()`
both exist precisely so the export can run headless. `skills/fix/skill.json` grants `writes` on all
four repos and `resources: ["unity_slot"]`. But the launcher's only argv shape is
`agent.unity.batch_test_command`, which hardcodes `-runTests`; `grep -rn executeMethod agent/ skills/`
returns nothing. **The capability exists and the authority exists; the request does not.**

Adding it needs a second batch argv shape (`-executeMethod` plus `FARM_COMMON_ROOT`), a gate on which
designer-source release pin to export from — a release decision the worker correctly refused to make
alone — and the hive artifacts synced afterwards. **Phase 3 or later, not 1b.**

## Finding 5 — the blocked path cites an unpublished commit

The delegated FARM-1238 run told the team its investigation report was at `4aed437d0`, on branch
`farmbot/farm-1238-…`. That branch was never pushed; `git branch -r --contains` returns no remote
refs. The reference is unresolvable for everyone except this machine.

Either push the branch when blocking, or do not cite a SHA in a Linear comment.

## Finding 6 — the Unity path is reached far less often than assumed

Three real delegations today; not one requested a slot. Two issues were already fixed, and the third
needed a config re-export rather than code. A `fix` worker asks for Unity only when it has a change
to verify, so the slot machinery will see much less live traffic than the design implies — and bugs
get triaged away from it more often than expected.

Not a defect. But it makes spec §7's rule for adding a second slot — add one when `awaiting_resource`
waits routinely exceed the length of a run — look even more like the right instinct, and it means
Task 11's Step 3 cannot be driven by a real bug on demand. Driving the reservation by hand
(`enqueue` → `claim` → `await-resource --mode batch`) is the only reliable rehearsal, and it still
exercises everything that matters: the pool, the switch, the launcher's Unity run and the settle.

## What Task 11 still owes

Steps 3 through 9. Specifically, and in this order once Finding 2 is fixed:

1. A batch run on a closed slot, driven by hand, with the Editor's parent confirmed to be `serve`
   and **not** a `codex` worker — that one line is the Task 7 redesign made observable.
2. The `-runTests` exit-code contract meeting a real Editor for the first time: parse the XML, assert
   `total > 0`, and expect **26 of 4388 failing**, which is `origin/main`'s known-red baseline.
3. An interactive run on the same slot, with the identity probe proving the Editor loaded the pinned
   commit.
4. Stop against a live batch run, verified with `pgrep` rather than the ledger's own opinion.
5. A service restart mid-run, proving `stop_all_unsandboxed` does not orphan an Editor.
