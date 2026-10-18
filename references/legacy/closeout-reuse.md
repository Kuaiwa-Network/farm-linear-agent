# Closeout without repeated preparation

After actual candidate tests and required live QA, release Unity through the normal quiescent
path and keep the same worker for offline closeout. Assess all retained suites with one
`verification-closeout --item ITEM --token-file STATE_DIR/token --input STATE_DIR/closeout.json`:

```json
{"runs":[
  {"candidate":"candidate-edit.json","scope":"edit-scope.json"},
  {"candidate":"candidate-play.json","scope":"play-scope.json"}
]}
```

Use `baseline` per entry for an actual recorded pair, and `repair_original` only for an existing
certified repair. Paths are relative to this item's state directory (absolute paths inside it also
work). The command checks one to six retained suites, writes content-addressed verdict files and
returns one immutable evidence index. It does not normalize/relabel original runs, run Unity, release
resources, waive remaining gates or finish the job. Any blocked suite leaves the overall result and
Monitor blocked even if another suite passes. Diagnose from the retained evidence; rerun this offline
command after correcting a review, not the baseline tests. Reuse its index path and verdicts in the
compact checkpoint instead of separate per-suite comparison/index/summary tool turns.

Prepare the final checkpoint, finish input and delivery text together after required acceptance
and publication gates. Use `finish --check-ready --checkpoint FINAL_CHECKPOINT --input OUTCOME`
with the normal item/claim/outcome arguments. A successful preflight permits preparing delivery;
confirmed delivery and the fresh authorization checks in actual `finish` are still mandatory.

Use `worker-start.md`, the current root's instructions and saved resume context first. Read other
repository rules when entering that repository; do not recursively reread every
checkout's instructions at each continuation. Missing decisions and stale context
still require the complete source. A report index is navigation, not approval.

After a test/comparison result is complete, select its original immutable reports
under `STATE_DIR`. Run the worker CLI with ordinary item and claim-token arguments:

```text
evidence-index --item ITEM --token-file TOKEN --input INDEX_INPUT.json
evidence-index --item ITEM --token-file TOKEN --verify INDEX_PATH
closing-preflight --item ITEM --token-file TOKEN
finish --item ITEM --token-file TOKEN --outcome delivered --input OUTCOME.json --check-ready
```

The index input names reviewed full commit SHAs and selected reports:

```json
{
  "pins": {"candidate": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
  "reports": [{"path": "candidate-result.json", "purpose": "candidate tests", "summary": "7 passed"}]
}
```

Use actual baseline, candidate and relevant consumer/source pins, never this example
SHA. The command hashes files without changing them and returns an immutable index
path. Store it in `plan.evidence_index` and one handoff fact. Keep plans below 16,000
characters and handoffs below 12,000; detailed findings belong in the original report.
A continuation verifies the index once, then checks current clean repository pins,
issue scope, relevant inputs and required gates. Hashes prove file integrity only;
supplied pins, summaries and relevance remain worker-reviewed claims.
`requires_issue_review` requires reviewing changed issue context before reuse.
Changed inputs invalidate affected evidence; missing or changed files need diagnosis,
not silently regenerated passes. Never reuse publication authorization.

Reuse existing normalized comparisons and original test reports when their exact
inputs match. Do not copy reports, rebuild equivalent indexes, dump complete JSON
repeatedly or launch another worker just to summarize them. Read selected fields for
the next decision. An attempt's final report contains only new decisions, links and
remaining work, referencing original reports/index.

Before a **docs-only Contract acceptance** handoff, save completed/skipped stages
A–F and reviewed consumer inputs, including `base_commit`, `source_commit`,
`consumer_commit` and evidence. Run `closing-preflight` while still in the consumer
worker. It catches missing identities before losing that context; it does not require
the not-yet-published acceptance PR or waive unfinished consumer work.

Release Unity after required verification and quiescence, then compare and publish
offline in the same worker. Hand off only for a required write in another repository.
Use ordinary `checkpoint --input ...` for Contract progress; `verification-progress`
is for the claimed Client verification workflow.

At Contract entry inspect the existing issue branch's remote history **and fetched
default-branch SHA before editing or running the Contract gates**. Record both SHAs
and check whether that main SHA is an ancestor of the owned issue HEAD. Reconcile
legitimate same-issue history once under repository rules. If required Contract gates
compare against a newer main, inspect its delta and integrate that pinned main into
the Contract issue branch once before the gate run; do not first run gates on a
known stale branch and rediscover new main protocol files as deletions. Retain merge
conflicts or actual issue protocol changes as concrete work, never waive their checks.
No force push or clone-metadata permission changes. Main advancing alone
does not invalidate a frozen verification round. If only commit history changes with
an identical tree, retain the original report and record both commit/tree identities;
reuse only checks whose documented semantics permit it. Rerun checks dependent on
history or changed diff bases. Never relabel an old tested SHA as a new tested SHA.
Run all required Contract gates at the resulting HEAD. Do not keep chasing a moving
main tip after that run solely for freshness. Contract-only synchronization does not
require consumer generation, a Client merge, baseline testing or changed inputs when
this issue retained sufficient consumer inputs; actual required input changes do.

Prepare a compact closeout packet before handoff: evidence-index path, tested source
pins, original comparison/QA paths, completed stages, existing PR URLs and the exact
remaining Contract action. The receiving worker verifies it and adds only its new
decisions. Reuse the prepared PR body/outcome/checkpoint files for publication and
delivery, updating actual URLs and confirmations rather than rebuilding equivalent
copies after each step. Do not treat an index or packet as a completion verdict.

Pass the saved packet as `--checkpoint FILE` to `handoff-repository`,
`await-resource` or `continue-verification` to save it and perform the handoff
in one invocation. It runs the ordinary checkpoint gates first, then all ordinary
handoff/resource gates. If the second step refuses, the checkpoint remains saved
and the current claim/resource remains owned; diagnose the refusal. A successful
handoff retires the claim: exit immediately, with no final report rewrite or wait
for the next reservation. The controller still verifies descendant teardown and
Editor quiescence before switching. It never trusts worker-reported release alone.

`verify-publication` returns `push_argv` using the verified remote name and explicit
issue ref, plus `execution_hint`. On Windows use normal automatic approval for that
exact push outside the restricted shell on its first attempt: MSYS hooks can fail
to create signal pipes there. Keep hooks and approval enabled; a denial stops work.
Never disable hooks, add broader roots or force-push to work around it. On other
platforms keep the ordinary execution policy. Refresh verification before each push;
the hint neither executes a push nor supplies approval.

Prepare the PR body, saved final checkpoint and outcome together. After required
acceptance publication, run `finish --check-ready` **before** preparing delivery.
It shares finish's local checkpoint/task/proof/closeout gates, omitting the not-yet-posted
comment. It does not fetch external proofs, certify remote readiness, publish, finish
or bypass final live checks. Fix missing fields from existing source evidence; do not
rerun tests to repair metadata. Then post/confirm delivery, add its actual action ID,
fetch the current issue and run normal finish. Update only changed links/status in the
prepared report.
