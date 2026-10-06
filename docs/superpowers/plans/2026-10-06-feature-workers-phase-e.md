# FarmBot Phase E: native Windows UI export

Prepared from the [accepted design](../specs/2026-09-24-feature-workers-design.md),
§§7.3–7.5, 8 and 13, and the measured [Phase D authoring work](2026-10-06-feature-workers-phase-d.md).
This is a development plan. Unchecked steps grant no production enablement,
deployment, live-card work, licensed-app setup or Unity operation.

## Entry and execution decision

Phase D's authoring worker records human visual approval and export requests but
parks at `stage_limit`. Its manifest writes farmgui alone and has no resource/MCP.
The current farmgui guidance also withholds the fix worker's standing export grant
from new UI authoring. None of those fences should be relaxed before this stage's
implementation and relevant Windows checks.

The initial PATH/uninstall inventory missed the per-user `FairyGUI-Editor.exe`
installation. The development-PC native and actual isolated-worker checks below
now measure successful batch exports with its existing authorization. The selected
path and raw host evidence remain private. No production config, credentials,
Editor preferences, license store, Mac state or production ledger is inspected.
The existing worker-run route succeeds here; a controller-run publisher is not
required by this measurement. Other hosts need their own execution check.

After selecting the actual executable, verify version/hash and current batch
availability with a bounded isolated development export. Use a disposable farmgui
checkout, explicitly selected scoped packages/dependencies, fully hydrated inputs
and fresh private staging/log paths. Do not use the watched `output/` directory,
start a watcher/Unity/service, activate a license or change app settings. Record
exit and every required package's completion, source/status changes including
`.objs`, fresh output identities/inventory/hashes and duration. Preserve failures.

Then test the same selected executable in the configured native Windows worker
context, with the established owned process/Job Object and isolated runtime home.
No Bash/MSYS/WSL, account change, credential copying, disabled containment or
automatic fallback. If it cannot execute there, stop and document the actual
failure before choosing a controller-run publisher; the Unity design is a
precedent, not an implemented FairyGUI execution grant. Do not introduce that
architecture from an unmeasured assumption.

## Package and artifact boundaries

Determine changed packages from the actual farmgui issue PR source diff, including
changed shared packages. Record the reviewed farmgui commit and package manifest
IDs; a changed PR head/source/art/state invalidates the old visual/export evidence.
Hydrate changed packages and their required dependencies with native scoped LFS.
Unchanged dependencies may be exported privately to validate the set but must not
be committed into Client as this job's output.

Validate a fresh bounded immutable export snapshot before Client installation:

- Each selected package has a descriptor with its expected identity/name, actual
  dependencies and only safe relative artifact names. Reject LFS pointers,
  malformed/truncated layouts, unmodeled disk-bearing item types and duplicate
  identities. Match the actual Client `UIPackage` reader and existing FGUI guards.
- Require exact descriptor-declared atlas/sound/misc inventory; missing files and
  orphan files refuse acceptance. Inspect actual container compression/version
  support instead of assuming a publish setting or exit code proves compatibility.
- Refuse traversal, links/reparse points, hardlinks, unexpected package directories,
  source changes during reads and excessive file/byte counts. Preserve source,
  descriptor, output and tool hashes in private bounded evidence.

Native process execution needs its own timeout, concurrency exclusion and Stop/
withdrawal fencing. Completion text alone does not establish process quiescence.
Retain private staging after uncertain cleanup; never kill an unverified process
or erase recovery evidence to obtain a pass.

## Client handoff and installation

The future manifest adds only authorized FairyGUI export output, its `.meta` files
and justified dependency-guard entries to the Client-rooted stage. It grants no
Client gameplay/UI binding code; that remains the Code worker's task.

The first Client handoff must select and persist a trusted main baseline and exact
issue branch through controller-owned state, extending the measured Code-stage
mechanism to UI without allowing a worker-written target to replace it. Retries
must retain that baseline/branch. The UI session stays unpinned at authoring intake.
Unity verification requires an explicit clean committed owned Client HEAD.

Mirror only changed packages into `Assets/GameRes/FairyRes/<Pkg>/`, following the
actual `scripts/watch-publish.ps1` semantics: stale artifacts and their orphan
metadata leave those package directories, existing file/folder GUIDs remain, and
new artifacts/folders get complete native importer metadata from validated sibling
templates with unique fresh GUIDs. Leave every unrelated package/root untouched.
Snapshot and validate all planned reads/writes/deletes before mutation; preserve
an interrupted installation's evidence rather than claiming directory atomicity.

A changed shared package can carry main's unrelated binary drift. Compare/export
provenance and describe that difference in the PR; request a named human ruling
where its scope is ambiguous. Never hand-edit generated descriptors or atlases.

## Verification, drafts and delivery

Run the actual native Client FGUI orphan/dependency guards against hydrated bytes.
An Inconclusive LFS-pointer result is pending, never a pass. New dependency edges
need `_allowed` reasons in the same authorized draft. New packages retain the
required Common dependency. Commit the exact candidate before requesting the
existing Unity slot machinery; record the tested commit and actual loading result.
Typecheck, descriptor validation and dotnet guard success do not establish Unity
loading or visual runtime acceptance.

Open only the issue's Farm-Client draft through the existing publication/foreign
work boundaries, naming exported farmgui commit, changed packages, approvals and
export request by actual user/message/time, and remaining human merges. After
source review changes, regenerate and reverify rather than reusing stale staging.
The UI worker still never merges, deploys, runs Jenkins or changes CI. Delivery
names both drafts and the human merge steps; Code's UI-ready gate checks the
separate merged farmgui/Client provenance. Comments alone do not resume delivered
work or approve changed art.

Offline coverage should use temporary state/local origins, scripted publisher
processes and fake Unity: identity/inventory/metadata hazards, cancellation across
publisher/install boundaries, approval staleness, Client pin/branch fencing,
publication retries, shared drift and recovery. Run the final executable candidate
on native Windows and hosted macOS/Windows, including all native Job Object tests
and every platform skip. Real paid exporter/worker/Unity acceptance remains measured
separately and cannot be inferred from those fixtures.

## Remaining release prerequisites

Phase D implementation and exact-candidate native/hosted offline verification
are complete; the [measured record](2026-10-06-feature-workers-phase-d.md#as-executed-verified-ui-authoring-worker-2026-10-07)
distinguishes that result from live acceptance. Remaining prerequisites:

1. Implement the reviewed native worker-run export route using the measured
   executable selection, fresh staging, timeout/concurrency and Stop fences below;
   the isolated development-PC feasibility checks are complete.
2. Implement/export-test the scoped package validator, Client installation, UI
   approval/export boundary, controller pin, draft/delivery and recovery behavior.
3. Run a separately approved TestBot UI card with actual documents/art, scoped LFS,
   real app-token preview upload, named corrections/approval and licensed export.
4. Verify hydrated Client guards and exact-commit Unity loading on the intended slot.
5. Verify final production-host tools/runtime/account/permissions, release revision
   and recovery; separately authorize production enablement/deployment.

No existing parked Code card or unmerged game test draft supplies UI acceptance.

## As executed: native publisher and actual worker route (2026-10-07)

This is the **development Windows PC**. These probes establish batch execution
feasibility, not production-host readiness, real UI-card acceptance, licensed-app
setup, Client installation or Unity loading. No service, live card, account,
credentials, app settings, license activation or production state is changed.

The per-user installation directory is labelled 6.1.4; executable metadata instead
reports Unity engine product version `2022.3.5f1c1 (8357566ed26e)` and file version
`2022.3.5.8607574`, so those fields are not an independently verified FairyGUI
application version. The selected executable SHA-256 is
`e077ce509ac23ee5f704b1be51cda025132ba564ba58b2f2ba8686645f8b9862`.
It is Authenticode unsigned; no download or vendor-integrity claim is made.
The executable and three core DLL hashes remain unchanged across the direct run.
The [official publishing reference](https://www.fairygui.com/docs/editor/publish/)
specifies batch mode, explicit project/package/output/log arguments and an
executable-directory or PATH launch. The probes use its executable directory,
native argument lists and private staging outside the watched source `output/`.

Source is exact farmgui main
`54a76b2a7110cdf8897144902f8490ed70df45bd` ([#141](https://github.com/Kuaiwa-Network/farmgui/pull/141)).
The inspected Notice dependency closure is exactly Common, CommonFx, Notice,
PlayerCustomize and UILangTex. Detached disposable development checkouts use scoped
native Git LFS hydration: **682 objects, 72,570,302 bytes**, all sizes/SHA-256 checked
against pointer identities, exit 0 in **4.612 s** and clean tracked status. The
initial private preparation cap of 500 objects refused this measured inventory
before download; after reviewing the closure, the bounded probe allowed at most
750 objects/200 MiB. No containment, ownership or application skip is changed.

| Execution probe | Export exit | Export duration | Whole worker attempt | Output |
|---|---:|---:|---:|---|
| Direct native publisher | 0 | 5.124 s | — | 31 artifacts |
| Configured native Codex worker | 0 | 3.864 s | 28.591 s | Same 31 names and SHA-256 hashes |

Each run records all five package completion entries and produces five descriptors,
24 fully decoded atlas images and two sounds. Descriptors have the expected
package IDs/names, **version 7 and compression flag false**, despite the project's
global `compressDesc` setting. Settings are unchanged. Complete descriptor/item
inventory acceptance remains the implementation below, not a conclusion from
publisher text or exit alone. Tracked source hashes remain identical; only ignored
`.objs` state is created in disposable sources, and no watched output is created.

The direct process uses the existing native gate handshake, Job assignment before
exporter launch and a 180-second bound; at most four owned processes are observed
and the Job empties. The actual-worker probe uses FarmBot's current Launcher at
`005be34a5532bb296634edc56b63a627d21d8d08`, selected Python 3.13.16 with
`PYTHONUTF8=1` before launch and native Codex **0.156.1** (SHA-256
`70bcb05f9bf1a4e7306edd0cd1b57d02af3267ad02a34b26f45c8c4bb20a3301`).
Its ordinary isolated Codex home, workspace-write scope and explicitly selected
unelevated Windows backend are verified. The publisher is observed inside that
worker's owned Job, all observed members exit, Launcher verifies quiescence and
no attempt remains. Existing authentication-seed metadata and protected runtime
registration receipts remain unchanged; their contents/values are not reported.
No Bash/MSYS/WSL, elevation, permission repair or fallback is used.

The first actual-worker probe lasted **26.358 s** and refused before publisher
startup: its clean-source check failed because the fixture's empty global Git
configuration omitted installed LFS clean filters. All 682 inputs still matched
their pinned hashes. A fresh alternate-index focused reproduction reports 682
changes without that filter, zero with explicit trusted per-command
`filter.lfs.process=git-lfs filter-process` / `filter.lfs.required=true`, and zero
with the normal environment. Hooks/fsmonitor remain off and the clean-source check
is retained. The corrected fresh worker fixture passes as above. This was a probe
configuration error, not an application regression, missing license, approval
rejection or weakened containment. Both attempts and the focused proof remain
in private UTF-8 evidence with versions, hashes and durations.

Next: implement immutable descriptor/inventory validation and scoped Client
installation, then the UI export authority/checkpoint/Client/verification journeys.
Real app-token preview upload, attributed live approval/export, hydrated Client
guards, exact-commit Unity loading and separately authorized final-host release
remain pending.

Documentation validation: 75 skill/reference tests pass in 0.757 s,
zero failures/errors/skips; local links/anchors, privacy patterns and whitespace
pass. Only two Markdown records change, so no repeat full suite is required.
