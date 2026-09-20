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

**Resolved 2026-09-20 — see "Step 3 result" at the end of this document.** The text below records what was true when the rehearsal was halted.

**Was unproven, and this was the important one:** the launcher had **never actually run Unity**.
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


---

# Step 3 result — the launcher runs Unity (2026-09-20)

Recorded after PR #4 unblocked the `enqueue` path. **Everything in this system that rested on
`FakeUnity` is now proved against a real Editor.**

Driven by hand, because three real delegations produced zero slot requests (Finding 6): service
stopped, `enqueue` then `claim` then `await-resource --mode batch`, then the service restarted and
the pool did the rest unassisted.

```
16:47:29  awaiting_resource  needs unity_slot:batch
16:47:58  reservation        acquired            <- pool granted
16:50:17  queued             unity_slot:1 acquired (batch)
16:50:45  worker             pid 46405 on mac    <- FRESH worker, not the operator's claim
16:51:52  deduplicated       started             <- PR #4's fix, in production
16:54:11  reservation        released
16:55:25  prepare_comment    delivery
          delivered
```

| Claim | Evidence |
|---|---|
| Two-phase acquisition | the asking claim exited; a fresh worker (pid 46405) resumed |
| The slot switch | `7d886c72e` to `6d5b46fbe`; `last_switch_at` no longer `None` |
| **The launcher runs Unity, the worker never does** | Unity pid 46016, **parent 45779 = `serve`** |
| The batch run produces real evidence | 2.55 MB `unity-tests.xml`, 834 KB editor log, 130.9 s |
| The exit-code contract | exit **2** with `total=4414` recorded as `"state": "ran"`, not `"gap"` |
| Handover | the fresh worker's `prompt.md` carried `batch_result` with `"state": "ran"` |
| No worker started Unity | violation grep over every worker `stdout.log` came back clean |
| The slot returns clean | `idle_closed`, parked `6d5b46fbe`, 0 reservations, 0 dirty files |

**The known-red baseline matched exactly.** Task 0 measured **26 failures of 4388** on `7d886c72e`.
This run on `6d5b46fbe` gives **26 of 4414** — twenty-six tests were added in between, all passing,
and the same twenty-six still fail. That is not a number you get by coincidence, and it is the
strongest single indication the run was genuine.

**`identity_observations` remains 0, correctly.** The identity probe runs on *interactive* switches;
a batch run needs no MCP at all, which is why `agent.unity.batch_test_command` carries no Editor
path. Step 4 is what exercises the probe.

**A check that looked wrong and was not.** `borrowed_comments()` reported 0 while the audit trail
showed `deduplicated started`. That is deliberate — its docstring says a `started` marker states no
conclusion and is not listed, and the trail keeps it either way. Correct behaviour, not a gap.

## Still owed from Task 11

Steps 5 through 9, and the second half of Step 4. See "Step 4 result" below.


---

# Step 4 result — the interactive path, halted (2026-09-20)

Two real defects, both in the identity probe, both invisible to the test suite. **One is fixed. One
needs a design decision.** The slot was `held` on both attempts, which is spec §7 working correctly —
a failing probe holds for the operator's `recover-slot` rather than releasing in an unknown state.

Proved along the way, and worth keeping:

- **The sha1 instance rule holds in production.** `sha1("<slot>/Assets")[:16]` computed
  `e7fe013d9909e41a`, matching the live instance id exactly. `discover_instance` — the method Task 5
  found could never have worked as briefed, because it matched a `path` field the HTTP transport
  never emits — works against a real server.
- **The MCP server self-starts**, as a `uvx` child of the Editor, no operator action, port 8080 up
  within about a minute of the Editor starting.
- **The failure path is sound.** Held rather than released; reservation preserved; no orphan;
  `recover-slot` returned the slot clean both times.

## Defect 1 — the probe read `.Location` on a dynamic assembly (FIXED)

```
Runtime error: The invoked member is not supported in a dynamic module.
NotSupportedException at System.Reflection.Emit.AssemblyBuilder.get_Location()
                     at MCPDynamicCode.Execute()
```

`execute_code` compiles the probe into a **dynamic assembly**. The probe enumerates
`AppDomain.CurrentDomain.GetAssemblies()` and reads `.Location` on each — and reaches the
`MCPDynamicCode` assembly it just created, where `Location` throws. Every check then returned
`unknown` and the slot was held.

FarmQA never hit this because its probe filtered first:

```csharp
if (name != "HotUpdate" && name != "AOTScripts" && name != "Nova.Runtime" && name != "MCPForUnity.Editor")
    continue;
```

Task 5 removed that filter deliberately and for good reasons — pushing project-specific names out of
C# and into Python config, so another project or platform is a configuration change rather than an
edit to the probe. Two reviewers approved it. **The reasoning was right and the result was broken**,
because the unfiltered loop now reaches the dynamic assembly. There are 270 loaded assemblies in this
project; FarmQA touched four.

Fixed by skipping dynamic modules before reading `Location`. Verified against a live Editor: the
probe returns 270 assemblies with `HotUpdate`'s `moduleMvid` and `hasGameTestDriver: true`.

**No test can cover this.** The suite substitutes a fake MCP that returns canned JSON and never
executes C#. The fix is therefore committed without one, which is stated here rather than hidden.

## Defect 2 — `ready()` misreads a change-triggered snapshot (FIXED, Task 13)

> Resolved on branch `task-13-readiness-gate` by direction 1 below, minus the reordering it assumed:
> `collect()` keeps FarmQA's order, because `editor_ready` already ANDs the probe's live flags and only
> the freshness clause had to go. `staleness.is_stale` and `advice.ready_for_tools` went with it — both
> are the server's own arithmetic on this same timestamp, so both were circular. See `identity.ready`.

As first written, `agent/identity.py:61` required the editor state to be recent (the clause is gone as of
Task 13, 2026-09-20 — it no longer appears anywhere in `agent/`; the line number below is the pre-fix file):

```python
and 0 <= now_ms-observed <= 10000
```

But `observed_at_unix_ms` is not a heartbeat. `EditorStateCache.cs` subscribes to
`EditorApplication.update` and then **deliberately skips rebuilding when nothing changed**:

```
:303   // This avoids the expensive BuildSnapshot() call entirely when nothing changed.
:348   // No state change - skip the expensive BuildSnapshot entirely.
```

So the field means *when the state last changed*. A stable idle Editor legitimately keeps an old
timestamp — and the more reliably idle it is, the more certainly the gate fails. Measured: age
climbed past **117 seconds** with `sequence` frozen at **3**, while `is_compiling`,
`is_domain_reload_pending`, `is_updating` and `is_running` were all `False` — an Editor that was
demonstrably idle and demonstrably usable, since `execute_code` worked throughout.

`advice.ready_for_tools` goes `False` for the same reason, which makes it circular rather than
independent evidence.

**Nothing a client can call forces a rebuild.** `refresh_unity` (0.5 s, returned fine),
`execute_code` and `manage_editor` all left `sequence` at 3.

This gate came from FarmQA, where it presumably held because that tool drove the Editor continuously.
FarmBot's Editor sits idle between operations, which is exactly when the assumption breaks.

**Two candidate directions**, both design changes deserving a review loop rather than a patch:

1. **Derive readiness from the probe's own output.** `execute_code` returns live `isCompiling`,
   `isUpdating` and `isPlaying`, and it demonstrably executes on the main thread. This makes the gate
   independent of the cache's rebuild policy, at the cost of reordering `collect()` so the probe runs
   before the gate rather than after it.
2. **Find a force-rebuild path.** `ForceUpdate(reason)` exists inside `EditorStateCache` and
   `playModeStateChanged` calls it, but no MCP tool appears to expose it. If one does, the gate stays
   as written.

Direction 1 looks stronger: it removes a dependency on a third-party cache's optimisation policy,
which is the kind of coupling that broke here in the first place.

### Deferred: a `sequence`-based liveness signal (2026-09-20, Task 13 review)

Task 13's review proposed replacing what the freshness bound approximated with a comparison of
`sequence` across `collect()`'s two `editor/state` reads. **Deferred, not adopted**, for two reasons.

The obvious form of it is backwards. Requiring `before['sequence'] != after['sequence']` would demand
that the Editor's tracked state *move* during the probe window — but a correctly idle Editor is exactly
the one whose `sequence` does not move (measured here: frozen at 3 for 117 s), so that condition
re-creates the defect this section records, in a form that no longer even mentions a clock. The useful
form is the opposite: `before['sequence'] == after['sequence']`, proving the tracked state did **not**
change across the probe, which is the Editor-side analogue of `collect()`'s existing `source_stable`
check on the checkout.

Even in that form it stays deferred, because adding a gate condition that has never been checked against
a live Editor is precisely how both defects on this page arrived. `collect()` already stores both
snapshots in the ledger row as evidence, so the next live re-run of Step 4 produces the `sequence` data
needed to decide this on measurements instead of reasoning. Revisit it then.

## What both defects have in common

**The test harness is structurally incapable of catching either.** It substitutes a fake MCP that
returns canned JSON, so no test executes C# or exercises a change-triggered snapshot. Three
document-review passes, per-task reviews and adversarial mutation testing all approved an identity
probe that could not work against a real Editor.

That is the argument for this rehearsal existing, made concrete twice in one step.
