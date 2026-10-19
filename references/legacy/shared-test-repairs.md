# Reusing diagnosed shared test repairs

Before spending a new diagnostic round or stopping on unresolved fixture attribution,
run `shared-test-repairs --item ITEM --token-file STATE_DIR/token --candidate
STATE_DIR/original-full-candidate.json`. The report must belong to this item's
settled verification pair. The command searches controller-recorded successful
test-only repairs, without changing this comparison's verdict or requesting Unity.

`matching_diagnosed_repair` means the failed leaf signature, original fixture blob,
reviewed dependency blobs and engine/toolchain identities match. Review the current
issue's complete diff for a new interaction with the diagnosed mechanism. If still
applicable, reuse the original causal investigation; do not require capturing an
unavailable historical frame or repeating the same experiment on every issue.
`review_required` lists concrete signature/source/dependency/toolchain differences
or a missing dependency review. These records are leads, not verified applicability.
No match means normal bounded diagnosis, not an automatic retry or baseline rerun.

First decide whether repair is required for this business issue at all. Confirmed
unrelated defects use [independent maintenance](shared-test-findings.md) and do not
delay delivery. The lookup also returns `maintenance_findings` from prior causal
reviews; bind reuse to the current scope and reports.

Only when repair is required or separately authorized, port the matching test
fixture delta from the two named Git commits. Never
cherry-pick another issue's entire feature change or merge moving main. Preserve the
original failure reports and diagnostic budgets; record the reused audit ID,
current-diff review and source identities in the existing repair review. A fixture
repair needs fresh whole-fixture results on this candidate (plus necessary
predecessor/cleanup regressions), as described in worker-cli.md. Historical passes
do not certify the new candidate. Required issue tests, resource ownership,
comparison and publication checks remain in force. Other unresolved failures block.

Successful `compare-verification --repair-original ...` automatically records the
diagnosis and repair in an immutable controller audit event. Add `shared_dependencies`
to the `repair` object: a list of exact repository-relative source/resource paths
that determine the failure mechanism, excluding the changed fixture files. Review
the actual causal dependencies, including relevant globals, adapters and engine
wrappers; directory similarity is insufficient. The controller pins their Git blobs
and requires them unchanged by the repair. Include configuration/resource paths when
they affect the mechanism. An incomplete review cannot justify reuse. Without this
list the repair remains discoverable, but needs dependency review before reuse.

This reuses a diagnosed mechanism and its narrow fix, not a historical passing
verdict or an assertion that the old hardware/timing state is known. The immutable
event stores test identities, not claim tokens or full private configuration. No
schema migration, new acceptance question, cross-repository handoff or external
comment is needed. Older releases ignore the additional audit events. Existing
pre-upgrade repairs can be indexed by an operator after revalidating their original
recorded pair, source delta and successful composite comparison; copying an
unverified report or a memory note is insufficient.
