# Feature workers Phase C: client stages

Prepared on 2026-10-06 as the next development work after the native Windows
Phase B checks. This plan describes work to implement; it does not assert that
client stages exist, authorize a live card, or promote/enable any running bot.
The operator has authorized continuing development and routine verified merges.
The selected Stage A card remains cancelled and its game draft remains unmerged.

## Entry evidence and boundaries

- Phase B has native Windows Stage A/Feishu/Git acceptance and a parked-job
  cancellation record. Later real common/config/hive generator journeys and
  cancellation during an active worker remain release checks.
- D10 network proto feasibility passes on Farm-Client
  `b7a07c2669f2c407f4c789d026d344bd4798273e` and Farm-Contract
  `29e6cfe1430a7c7313febc642f95db4ea6c3fb18`: two headless exports,
  60 proto inputs, 61 identical C# outputs, 589 messages and .NET compilation.
  Fifty-six existing metadata files are preserved; five new outputs need metadata.
- The approved 1 KiB native LFS push/forced download passes with the current
  account's existing GCM login. The supported optional callback independently
  passes transfer and a GitHub read; its code PR #147 is merged after the full native and both-platform suites pass.
- Implement §§6.6–6.8 of the [accepted feature design](../specs/2026-09-24-feature-workers-design.md). Windows runs native tools
  through explicit Python/dotnet/protoc executables, without Bash/MSYS/WSL.
  macOS may retain the existing shell entry points. No Unity is used to generate
  protobuf/config artifacts; Unity resources serve verification only.
- Preserve opt-in/exclusive feature scheduling, one writable repository per
  attempt, fresh worker homes, owned Jobs/POSIX ownership, CLI identity/claim
  fences, readonly sibling snapshots, clone trust and configured publication.
  No worker may merge game PRs, run Jenkins, alter designer values or start an
  Editor directly. FarmBot development PR merges do not change those grants.

### Delivered entry tooling (2026-10-06)

Farm-Client #1421/#1422 are merged with tested/merge trees equal. The complete
metadata transaction and supported network CLI pass native Windows full unit
checks, hosted macOS/Windows checks, and two real native exports at the pinned
contract commit. All 61 C# outputs have full metadata, including five new GUIDs;
both 122-file maps match, compilation passes and a wrong pin preserves outputs.
The [measured record](../spikes/2026-10-06-windows-final-tools-lfs.md#supported-client-metadata-and-headless-network-export)
contains exact commits, durations, skips and evidence boundaries. Headless config
integration and native full client typecheck remain next in step 1. Steps 2–5
are still implementation/acceptance work; no running bot is enabled or deployed.

## 1. Supported headless client export tools

Work in an isolated Farm-Client development checkout after reading its current
AGENTS/CLAUDE guidance and existing exporter tests. Add a .NET 8 command entry
that links the actual network exporter, validators, process runner and generated
directory transaction. Supply explicit roots/protoc; do not reproduce generation
logic or select Editor preferences/dialogs. The no-op editor adapter must throw
if a preference/dialog is unexpectedly used. Build intermediates stay in a
private owned output directory; use hydrated, hash-checked protobuf dependencies.

Extend the existing replacement transaction narrowly so a headless installation
can create missing `.meta` files in its staged replacement before swapping the
directory. Preserve existing metadata byte for byte, including GUIDs; use a
validated sibling MonoImporter template and a fresh GUID for each new C# asset;
remove orphan assets/metadata together. Reject invalid templates, duplicate
GUIDs and reparse paths. An error before commit preserves the whole old output;
an install failure rolls it back and retains recovery evidence if rollback fails.
Do not append metadata after an already committed asset replacement.

Add the analogous config entry using the existing config exporter/installer:
verify the artifact's exact common commit/digest/table set; map `.pb` to
`.pb.bytes`, replace both generated directories together, preserve old GUIDs,
create full metadata with the correct sibling importer and remove orphans.
Keep table registry/count validation. Reword client exporter guidance to permit
these supported headless workflows, with provenance and the same verification.

Regression tests cover unchanged output, new/deleted/renamed assets, full metadata,
bad manifests/tools/templates, no-editor behavior and multi-directory rollback.
Run real pinned network/config exports twice in native worker fixtures, compare
complete maps and compile readers/registry. Record input refs/tool identities,
durations and owned-Job cleanup. No generated game assets are committed by a
tooling acceptance fixture. Native typechecking also needs a supported Windows
entry point; retain the read-only Unity-opened checkout prerequisite and report
that capability explicitly rather than bypassing it.

## 2. Controller scope and verification target

Extend the feature manifest's writes with Farm-Client, gates with `ui_ready`, and
resources with `unity_slot`. Listing a resource permits a later request; it does
not reserve Unity during contract/common/hive stages. Keep stage A's Farm-Client
and farmgui snapshots read-only. Do not eagerly seed the new client issue branch
alongside A's existing contract/common/hive worktrees: first prepare it on entry
to the client stage, from that stage's latest verified main. Recovery of an
already recorded client branch reattaches it instead of resetting it. Retain
the existing server-stage sibling worktrees and read behavior.

Feature sessions currently have no pinned target, and `await-resource` refuses
an item without one. Add a controller-validated stage-local Farm-Client baseline
when the client stage starts, using the trusted clone/latest-main branch setup.
Do not retrofit an issue-start client pin or accept a worker-supplied unverified
target. Session routing and fix targets stay unchanged. An explicit committed
verification HEAD must belong to the item's owned client branch, pass existing
Git validation and be recorded separately from that baseline in its reservation.
Check ownership again after Git reads. Retries/recovery preserve the actual
recorded client branch and target rather than silently selecting newer main.

Tests cover lazy client creation, client main advancing during server work,
requests before client handoff, stale claims, wrong roots/branches,
uncommitted or unknown HEADs, cancellation during Git validation, recovery and
held/unclean Unity cleanup. Shared Editors remain outside worker containment.

## 3. UI-ready pause and client instructions

Update runtime authority in `agent/dispatch.py`, feature instructions and repo
map together. Stage E asks once if the recorded change has UI; name its packages,
components and owner, park under `ui_ready`, and verify actual default-branch
farmgui component/client exports on an explicit resume. Missing exports cause a
new scoped request; existing package presence alone is not human confirmation.
Without UI, mark E skipped with its reason and proceed. Honour stage limits and
withdrawal throughout, including no-server/no-config paths.

Stage F starts from the controller's latest client baseline, exports network
protos from the clean committed issue contract snapshot and config from the
verified common artifact, then implements the client requirements. Commit LFS
pointers through the existing clean filter, using the verified optional native
callback/environment on the selected development host. Open a client draft with
exact unmerged input/provenance, unrelated designer changes, local checks and
merge preconditions. Run native typecheck, .NET tests and reservation-based Unity
verification at the owned committed client HEAD. Missing capabilities stop the
stage with measured evidence; no success-by-skip or broader grants.

## 4. Closing and contract write-back

Preserve user-triggered merge verification, waiver cleanup and server pin checks.
After the contract merges, re-export client protos against the same verified
main commit used by hive's re-sync and push the recorded client draft update.
Support merge and squash histories explicitly; never infer main provenance from
an unmerged issue SHA. Retry-safe plan entries and notice IDs prevent duplicate
requests/publications. The client PR still waits for its merge preconditions.

Add the exact `farmbot/<key>-writeback` Farm-Contract suffix/role to publication
validation and its tests, with the same destination/branch ownership checks.
Run write-back in a fresh Contract-rooted attempt from main: update scenario
acceptance links, archive using the native OpenSpec route, retain client/pending
tail sections and decision counts, update provenance and run all twelve gates.
Return to the issue branch before a sibling consumer reads it. Deliver every
draft/verification/remaining human merge step; workers continue to never merge.

## 5. Offline journeys, rollout and later UI work

Extend the real-controller fake-worker journeys through E/F/G: UI and no-UI,
client-only/no-server, config repin, merge/squash re-sync, write-back, continuation,
stage limits, failed exports, parked and running-worker withdrawal, and Unity
quiescence/recovery. Keep every existing Phase B journey and fix/chat isolation
meaningfully covered. Run focused boundary tests then the full offline suite
once per final executable candidate on native Windows and macOS/CI; retain all
skips and native Job execution evidence. Documentation-only follow-ups need
links/whitespace/relevant reference checks, not another full suite.

No database migration is planned; if implementation needs one, document it and
test forward/recovery behavior before landing it. Roll back instruction/manifest
changes only after jobs/resources settle; checkout alone does not reverse state.

Real later-stage acceptance requires a concrete operator-approved card/scope,
the intended tool/account/profile identities, prepared caches and a verified
private optional Git/LFS build. Keep live operations separate from offline
development. Production promotion and feature enablement require their own
authorization and final-host release/recovery acceptance.

Phase D follows with the fgui authoring worker, scoped LFS hydration, preview
renderer/image upload and human visual approval. Phase E follows with licensed
native Windows FairyGUI export, complete metadata/package installation and the
client export draft. Neither phase is implemented by this plan, and no current
production/farmgui permissions change merely by recording it.

The [native Windows record](../spikes/2026-10-06-windows-final-tools-lfs.md) and
[current operating contract](../../operating-contract.md) govern the measured
entry state; unchecked work above remains implementation and acceptance work.
