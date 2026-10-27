# Prepare, run, collect and deliver

This is execution guidance for the current issue owner, not another approval,
checkpoint schema or scheduler gate. Keep the actual repository requirements,
claim/control checks, resource isolation and publication authority.

## Prepare before reserving

Keep the assigned `GOCACHE` and inherited `GOFLAGS`: configured hosts seed private
compilation caches across issues and use `-trimpath` for checkout-independent keys.
Do not clear or replace caches to obtain fresh test evidence; `-count=1` executes
tests while reusing valid compilation. A cache import receipt is not a test pass.

For final verification, settle source, generated inputs and related fixture migrations first. Run cheap
affected checks that need no shared service, including available native typechecks.
Prepare the exact test selection/argv, private report paths, relevant UI states
and cleanup procedure before `await-resource`. Have the clean candidate ready to
execute when the reservation becomes ready. Do not reserve a slot merely to hold
capacity while implementing code, writing a QA helper or drafting PR descriptions.
No new manifest or human approval is required for this preparation.
Exploratory reservations remain available for a concrete diagnostic question with
a prepared bounded procedure; completed implementation is not a prerequisite for
reproducing a bug.

For Unity, reuse the project's working tests, bootstrap and QA helpers whenever
they cover the scenario. Prepare any necessary task-specific procedure against
the actual project APIs; typecheck it offline where supported. Do not assume a
common reflection interface across unrelated config readers. Prefer small,
observable steps with explicit completion and cleanup to a new long-running
helper whose state cannot be reconciled after a lost response.

Native tool discovery and runtime observations still require the ready owned
reservation. Discover and retain the relevant actual schemas once per unchanged
instance/tool set. For new/changed QA helpers, first check the runtime prerequisites,
bootstrap and required screen dimensions, then exercise one small relevant step
before a larger QA procedure. Reuse established procedures when inputs match;
an offline compile is not native runtime acceptance. Record and restore
temporary state through supported owned-instance operations; never edit the shared
slot or administer the Editor process. Preserve required tall-screen/SafeArea and
state coverage; a known portrait setup is not a universal substitute.

## Use the reservation for execution

On readiness, run the prepared checks promptly. Reconcile pending handles and
ambiguous mutations; never launch again just to obtain output. Collect exact
results and failure details while ownership still permits access. Diagnose a new
setup failure from that evidence before another attempt; do not blindly rerun a
suite. If correction requires source edits or substantial helper development,
settle owned work, save recovery evidence and release normally before preparing
the corrected candidate. An uncertain cleanup still follows the existing held/
unclean recovery path, never a forced release or an invented cancellation path.

Checkpoint actual results, candidate/input identity, report paths and next action.
Release promptly after verified quiescence, before PR prose, unrelated analysis
or remote CI waits. For server/combined leases obey `keep_worker` versus
`worker_handoff`; release itself does not certify a test pass. Reuse matching
evidence and rerun only checks affected by actual corrections.

Independent implementation, local checks and preparing delivery text can overlap
pending tests when permissions, source freezing and resource ownership permit it.
Helpers get a bounded assignment and return a concise completion/blocker handoff:
changed paths/commit, results, limitations and private evidence paths. Report a
concrete blocker or conflicting input promptly; avoid repeated unchanged status
messages or sending whole logs back and forth. The owner alone handles claims,
shared resources, publication and finish. Settle helpers before releasing anything
they use or finishing, and retain the existing polling/control cadence.

## Finish in one delivery sequence

Prepare the affected repositories' PR descriptions and verification summaries
together while checks run, marking results pending until observed. After results
settle, review the final diffs, reconcile changed inputs and update those drafts
with actual outcomes and limitations. Keep original failures as evidence.

Publish each final commit and PR description with fresh `verify-publication`
immediately before each push or PR create/update; batching preparation does not
reuse authorization across operations. Preserve concurrent changes and qualify
which inputs retained tests cover. Avoid cosmetic publish/edit cycles when the
facts have not changed. Refresh issue facts/inbox, handle actual new requests and
run `finish --check-ready` on the final PR heads. Post the prepared delivery through
the idempotent outbox, confirm it and call `finish` promptly. Do not add another
review round or repeat checks merely because delivery preparation took time.
Changed authority, inputs, PR heads or failures still require normal reconciliation;
readiness is not a reusable permission or a delivered state.
