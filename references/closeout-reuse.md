# Closeout without repeated preparation

Use the current root's instructions and saved resume context first. Read other
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

At Contract entry inspect the existing issue branch's remote history before preparing
acceptance changes. Reconcile legitimate same-issue history once under repository
rules, without force push or clone-metadata permission changes. Main advancing alone
does not invalidate a frozen verification round. If only commit history changes with
an identical tree, retain the original report and record both commit/tree identities;
reuse only checks whose documented semantics permit it. Rerun checks dependent on
history or changed diff bases. Never relabel an old tested SHA as a new tested SHA.

Prepare the PR body, saved final checkpoint and outcome together. After required
acceptance publication, run `finish --check-ready` **before** preparing delivery.
It shares finish's local checkpoint/task/proof/closeout gates, omitting the not-yet-posted
comment. It does not fetch external proofs, certify remote readiness, publish, finish
or bypass final live checks. Fix missing fields from existing source evidence; do not
rerun tests to repair metadata. Then post/confirm delivery, add its actual action ID,
fetch the current issue and run normal finish. Update only changed links/status in the
prepared report.
