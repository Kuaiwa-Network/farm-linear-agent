# FarmBot Phase E: native Windows UI export

Prepared from the [accepted design](../specs/2026-09-24-feature-workers-design.md),
§§7.3–7.5, 8 and 13, and the measured [Phase D authoring work](2026-10-06-feature-workers-phase-d.md).
This is a development plan. Unchecked steps grant no production enablement,
deployment, live-card work, licensed-app setup or Unity operation.

## Entry and execution decision

At Phase E entry, Phase D's authoring worker records human visual approval and
export requests but parks at `stage_limit`. Its manifest writes farmgui alone and
has no resource/MCP. Farmgui's guidance then withholds the fix worker's standing
export grant from new UI authoring. The conditional owner grant now merges in
[#142](https://github.com/Kuaiwa-Network/farmgui/pull/142); the scoped worker route
below must still pass its final exact-candidate Windows checks before merge.

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

The scoped manifest adds only authorized FairyGUI export output, its `.meta` files
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

1. Run a separately approved TestBot UI card with actual documents/art, scoped LFS,
   real app-token preview upload, named corrections/approval and licensed export.
2. Verify hydrated Client guards and exact-commit Unity loading on the intended slot.
3. Verify final production-host tools/runtime/account/permissions, release revision
   and recovery; separately authorize production enablement/deployment.

No existing parked Code card or unmerged game test draft supplies UI acceptance.

## As executed: suspended startup and export foundations (2026-10-07)

[#161](https://github.com/Kuaiwa-Network/farm-linear-agent/pull/161) merges as
`4f94b017d7327084f35d783cfae536696259a12a`,
[#159](https://github.com/Kuaiwa-Network/farm-linear-agent/pull/159) as
`4d5669c679f8aee6069be30b8da33f49930de918`, and
[#160](https://github.com/Kuaiwa-Network/farm-linear-agent/pull/160) as
`f622a44223189932268bfae6ac0f90c128b78cee`. Their actual merge trees match
the tested candidates. These are development foundations; no running bot,
production configuration, live card, account or game test draft is changed.

The selected Windows virtual-environment Python is a redirector. Starting it
before assignment could create a base-interpreter descendant outside FarmBot's
Job; assignment is not retroactive. The launcher and publisher now create it
suspended, assign it using the retained creation handle, verify and resume its
one retained initial thread, then permit the existing gate handshake. Assignment,
resume or identity failures refuse startup and reap only verified owned processes.
No containment, ownership requirement or skip is relaxed. The initial two
publisher native full suites had one budget-kill stderr cleanup error each,
with zero assertion failures; those failures remain historical evidence.

| Candidate/run | Tests | Duration | Failures/errors | Platform skips |
|---|---:|---:|---:|---:|
| #161 development native Windows | 1,928 | 1,266.419 s | 0 / 0 | 69 |
| #161 hosted macOS | 1,928 | 654.033 s | 0 / 0 | 45 |
| #161 hosted Windows | 1,928 | 1,919.119 s | 0 / 0 | 69 |
| #159 hosted macOS | 1,961 | 609.415 s | 0 / 0 | 57 |
| #159 hosted Windows | 1,961 | 1,586.372 s | 0 / 0 | 69 |
| #160 development native Windows | 2,012 | 1,319.198 s | 0 / 0 | 69 |
| #160 hosted macOS | 2,012 | 552.612 s | 0 / 0 | 57 |
| #160 hosted Windows | 2,012 | 1,881.377 s | 0 / 0 | 69 |

Exact candidates are #161 `c1ac10e562d990df7fa022da2a4a2fa6c0b7a5be`,
#159 `de57c6a88061d739e1998bcf741c81d27fd48cdb`, and
#160 `49b86138b0a4866dfb7396e01e6025ebd3100943`.
Hosted runs [37545279577](https://github.com/Kuaiwa-Network/farm-linear-agent/actions/runs/37545279577),
[37546311907](https://github.com/Kuaiwa-Network/farm-linear-agent/actions/runs/37546311907)
and [37546463809](https://github.com/Kuaiwa-Network/farm-linear-agent/actions/runs/37546463809)
verify synthetic trees equal their candidate trees. #159's local evidence is
the #160 combined 2,012-test superset, with byte-identical publisher, validator,
installer, launcher, gate and native publisher test blobs; it is not a separate
local 1,961-test run. All ten native Job checks and twelve native publisher checks
actually run on both Windows hosts. Windows retains the established 69 skip
IDs/reasons; macOS adds three native Job and twelve native publisher skips to its
established 42. All 51 source/approval/controller-pin checks run in #160, along
with 22 Code journeys, 11 UI checks, 43 preview/transport, 34 export and 22 installer
checks. Complete UTF-8 logs, per-test duration and every skip remain private.

Development tools are Python 3.13.16, Git 2.54.0.windows.1 and Git LFS 3.7.1;
hosted Python is 3.13.15, macOS Git 2.55.0/LFS 3.8.0 and Windows Git
2.55.0.windows.5/LFS 3.7.1. Fresh symlink capability and inherited-selector/token
sanitization are verified before full runs. The corrected publisher also exports
the same 31 calibrated artifacts directly in 4.094 s and from an actual native
isolated Codex worker in 3.986 s (51.927 s whole worker). Pre-startup/pre-export
Job proofs, nested/outer Job emptiness and unchanged runtime-registration and
authentication-seed metadata are recorded. This development PC's results do not
certify production-host readiness, hydrated Client guards or Unity loading.

At those foundation candidates, prepared controller review/export/install/scope
commands retain the authoring-only manifest and use future-grant fixtures. The
actual Client dependency guard's 39 literal package entries parse unchanged; no
real Client file is written by that calibration. The later conditional owner
grant and scoped activation are recorded separately; approved live UI acceptance,
real hydrated guards and exact-commit Unity loading, followed by separately
authorized release, remain distinct prerequisites.

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

## Export inventory foundation

`agent.fgui_export` prepares the read-only immutable inventory boundary described
above. Synthetic plain v7 containers and real PNGs exercise truncation, both index
widths, long UTF-8 string overrides, dependency closure/cycles, item/sprite bounds,
missing/orphan files, links/hardlinks, mutation and independent resource limits.
It does not invoke the publisher, install Client assets or widen the UI manifest.
The measured 31-file export also calibrates actual dependency and artifact hashes,
atlas dimensions and the three inherited Common resource-name ambiguities
(`NameTag`, `btn_rukou_uibg`, `icon_beian_000`). Those are reported; they are not
duplicate package/resource IDs or silently asserted unique name lookups. Runtime
payload loading, scoped installation and approval/controller orchestration remain
pending. Full candidate verification is recorded separately after completion.

## Scoped installer foundation

`agent.fgui_install` prepares immutable changed-package-only mirror plans and a
claim/Stop-fenced apply helper with preserved before bytes and a durable private
recovery journal. Existing importer metadata remains byte-identical; complete live
sibling templates supply new folder, descriptor, atlas and sound metadata with
fresh globally checked GUIDs. It retains the watcher's deletion caps and refuses
unhydrated comparison inputs, stale plans, missing/malformed metadata, global GUID
drift, filesystem hazards and unmodeled metadata types. Unchanged dependencies and
unrelated packages are untouched. No CLI, UI manifest, licensed export grant,
Client/Unity resource or live operation is enabled by this foundation.

Read-only development-PC calibration of exact Farm-Client main
`5ca9db4f4ef4ae7a21c50c7243429a6f537a29a8` finds **10,434 metadata files and 10,434
unique GUIDs**, no duplicate GUID identity, and validates actual DefaultImporter,
TextScriptImporter, TextureImporter and AudioImporter templates in **52.217 s**.
The Client checkout stays clean. This does not install exports, run hydrated Client
guards or start Unity. The focused synthetic checks exercise full metadata/GUID
preservation, changed-only mirroring, nested/orphan cleanup, independent deletion
limits, stale input, Stop and I/O failures with retained recovery. One intermediate
22-test run had a fixture FileNotFoundError after a test method's cleanup was
placed in its neighbor; the cleanup is restored to its own method, with no
application check or skip change. Final focused/full evidence follows separately.

## Verified export foundation (2026-10-07)

[#157](https://github.com/Kuaiwa-Network/farm-linear-agent/pull/157) is merged as
`ca5b46da7305359909fdca8f53fe4b9aed2879e0`, with the same tree as candidate
`89005daab30e0a36296d14e491765e25356eb122`. Full offline verification:

| Run | Tests | Duration | Failures/errors | Existing platform skips |
|---|---:|---:|---:|---:|
| Development native Windows | 1,903 | 1,261.581 s | 0 / 0 | 69 |
| Hosted macOS | 1,903 | 603.435 s | 0 / 0 | 42 |
| Hosted Windows | 1,903 | 1,677.912 s | 0 / 0 | 69 |

All skip identities/reasons equal the established 1,869-test baseline; complete
lists, versions, UTF-8 logs and per-test durations remain private. All 34 new export
checks, 22 Code journeys, 11 UI checks and 43 preview/transport checks actually run.
All seven native Job Object tests run on both Windows hosts. Hosted synthetic
revision `7b24245b60b7ec633c3830a96bc8f74f05372131` has the candidate's exact tree;
[run 37534175956](https://github.com/Kuaiwa-Network/farm-linear-agent/actions/runs/37534175956)
passes both jobs. Native Python is 3.13.16 with Git 2.54.0.windows.1/LFS 3.7.1;
hosted Python is 3.13.15 with macOS Git 2.55.0/LFS 3.8.0 and Windows Git
2.55.0.windows.5/LFS 3.7.1. All Windows Python starts use `PYTHONUTF8=1` and the
pinned workflow's selector sanitization. This is development-PC evidence, with no
production release or UI-card acceptance claim.

## Native publisher execution foundation

`agent.fgui_publisher` prepares explicit native tool/source selection, disabled
code generation, fresh private staging and a bounded worker-run process helper.
It uses a machine-wide publisher mutex and refuses existing/competing FairyGUI
processes. Its native gate is assigned to an owned nested Job before execution;
Stop/timeout/lingering descendants terminate only that Job and retain receipts.
Success requires observed emptiness, unchanged tool/full source identities,
completion and an immutable validated inventory. It adds no CLI, UI authority,
host configuration, Client stage or Unity grant.

Early focused tests preserved two diagnostic inconsistencies (duplicate-key JSON
errors were normalized too broadly; an oversized log raised raw OSError). The
helper now retains the specific duplicate-key refusal and normalizes bounded
input errors. A repeated native nested-worker test then reproduced a real process
accounting race: the gate can be signalled just before Windows reports the final
descendant gone. The helper waits at most one second for observed emptiness;
persistent children still refuse success and are reaped. A delayed-child regression
and 20 focused repetitions of the nested-worker check pass after this correction.
No containment, ownership check or application skip is weakened.

The direct real-export calibration uses new disposable sources, exact farmgui
`54a76b2a7110cdf8897144902f8490ed70df45bd`, the same five-package dependency
closure and 682 verified LFS objects per source, hydrated from the existing native
development object cache without another download. The first probe's full-source
callback refused before startup because it used a nonempty-input hash helper for
tracked empty placeholder files. The callback now hashes verified ordinary empty
files as empty bytes; nonempty tool/project/settings/manifest checks remain.
The corrected native helper exports in **4.066 s** (**12.556 s** including tool/source
verification), with an empty owned Job and all 31 artifact hashes identical to the
earlier measurements. No source settings, watched output, service, credentials,
account, license setup, live issue, Client assets or Unity operation is changed.
The actual configured Codex 0.156.1 worker then exports in **4.057 s** in a
**44.505 s** whole attempt. The nested publisher Job is assigned before opening its
gate (at most four owned processes); both nested and outer worker Jobs empty,
observed members exit and Launcher certifies quiescence. All 31 artifact hashes
again match. Existing authentication-seed metadata and protected runtime
registration receipts remain unchanged. The first worker-fixture preparation
refused before Launcher because a private probe indexed its seed-file mapping as
a list; the corrected fresh fixture reads only its unchanged metadata. Neither
preparation failure is a license/runtime/application failure or bypass.

The focused native UI group runs **99 tests in 39.461 s**, including all 32 new
publisher checks and 11 actual native containment/concurrency checks, with zero
failures/errors/skips. Another 75 skill/reference tests pass in 0.456 s. Final
exact-candidate full offline evidence follows separately; these helper
calibrations do not enable the Phase E worker route or establish production-host
readiness. Attributed UI export/Client orchestration, hydrated guards and actual
Unity loading remain pending.

The initial hosted macOS full run on candidate
`118362abe371c6d5b342b2e7ba7a86463475e060` runs 1,957 tests in 649.564 s,
with 21 failures, 9 errors and 53 platform skips (the established 42 plus 11 new
native-Windows-only publisher checks). Its new publisher fixtures used the
unresolved macOS temporary path, whose ancestor is a system symlink. The strict
application link refusal is correct. Fixture setup and its per-subtest temporary
attempts now resolve their canonical root, preserving all link/reparse/hardlink
refusals and every native test. Raw UTF-8 evidence remains private; final corrected
candidate verification follows. No application or process-containment code changes
for this fixture repair.

## Verified Client installation foundation (2026-10-07)

[#158](https://github.com/Kuaiwa-Network/farm-linear-agent/pull/158) is merged as
`c52513aa84f3ebf414bb62a71b6b7a6740286e8c`, retaining candidate
`fe759f75e550c40f9f746bbbbbb3159c1031c5a6`'s exact tested tree.

| Run | Tests | Duration | Failures/errors | Existing platform skips |
|---|---:|---:|---:|---:|
| Development native Windows | 1,925 | 1,274.302 s | 0 / 0 | 69 |
| Hosted macOS | 1,925 | 548.647 s | 0 / 0 | 42 |
| Hosted Windows | 1,925 | 1,946.267 s | 0 / 0 | 69 |

All skip IDs/reasons equal the established baseline. All 22 installer regressions,
34 export checks, 22 Code journeys, 11 UI checks and 43 preview/transport checks
run; all seven native Job Object tests run on both Windows hosts. Hosted synthetic
commit `e94fe254eb6a28d60c76b615a695e588442ddc5b` has the exact candidate tree;
[run 37535985656](https://github.com/Kuaiwa-Network/farm-linear-agent/actions/runs/37535985656)
passes both jobs. Python/Git/LFS selections and UTF-8/environment sanitization are
the same as the export-foundation table above. This remains development/offline
evidence; real Client installation, hydrated guards and Unity loading are pending.

## Prepared approval, source identity and Client pin

The shared controller Client pin now recognizes staged Code/UI jobs without
changing the delivered UI manifest or authority. The Code-only compatibility
methods retain their existing skill restriction. Future UI Client entry selects
main once after the authoring attempt's quiescence and retains its exact issue
branch/baseline through retries. The prepared approval helper binds real human
messages/comments and timestamps to the unchanged full-source/review-image/round
identity, including canonical same-issue messages from retired attempts; it never
treats a plan or worker-written handoff as a human answer. Actual intent still
needs the scoped worker's interpretation of the referenced message.

The owned-source helper validates full committed index/file identity using trusted
controller Git, native CRLF normalization and raw LFS pointers without executing
clean filters. The actual source diff includes changed shared packages, leaves
unrelated dependency exports private and retains non-package changes as evidence.
Missing base history, deleted package manifests and non-package-only diffs refuse
automatic export. No CLI, durable export receipt or Client/Unity grant is added.

Development-PC calibration of exact farmgui
`54a76b2a7110cdf8897144902f8490ed70df45bd` in a new disposable owned checkout
checks **5,443 tracked files**, **682 LFS objects / 72,570,302 bytes** hydrated from
the existing cache, and completes a full source snapshot in **98.970 s**. A second
snapshot is identical; the original development source remains clean. The first
private preparation tried fetching all historical refs from the partial clone and
refused on unavailable historical objects. A fresh local shallow fixture fetches
only the exact measured source tree; no existing promisor settings or application
ownership checks are changed. Raw logs and private paths remain local.

Focused source checks cover actual local Git, Unicode/spaces, empty placeholders,
CRLF, changed/index/untracked input, unknown filters, scoped/unselected pointers,
materialized LFS mismatch, links/hardlinks, budgets and concurrent mutations;
diff checks include shared changes and cross-package rename paths. An early test
fixture attempted overwriting Git's native hidden `.git` file and refused with
PermissionError; the final fixture supplies the same corrupt-pointer read to the
real ownership checker without changing native file attributes or adding a skip.
Final candidate verification follows separately. Functional export/receipt/Client
orchestration, the farmgui owner's export grant and real UI-card/Unity acceptance
remain pending.

## Publisher suspended-start correction

The initial and fixture-corrected development-PC Windows full runs each execute
1,957 tests with zero assertion failures, one cleanup error and 69 existing
platform skips, in 1,267.368 s and 1,280.574 s. Both errors are the existing
launcher budget-kill test retaining a second worker's stderr log during temporary
cleanup. All seven required native Job tests and all 11 then-current native
publisher tests actually run. Focused repetitions alone do not establish a pass.

The [separate native startup investigation](../spikes/2026-10-07-windows-python-redirector-containment.md)
measures a selected Python venv redirector starting its base interpreter before
parent Job assignment. Its fixture never opens the gate or executes a CLI. The
shared launcher correction creates Python suspended, assigns the owned creation
object and verifies/resumes its initial thread before startup and the handshake.
The prepared publisher now uses the same sequence for its nested gate and requires
separate assignment-before-startup and assignment-before-export evidence before
accepting an inventory. A deliberately delayed assignment test proves no gate
startup before assignment; a refusal test rejects missing startup evidence even
when exit, quiescence and pre-export assignment otherwise pass.

This correction adds one native publisher regression (33 publisher checks, 12
native). Exact-candidate full suites and fresh real publisher/isolated-worker
calibrations follow separately. Earlier passing process/export measurements do
not certify the corrected route. No new skip, containment relaxation, alternate
shell, runtime permission repair, live issue or production change is introduced.

Fresh native calibration of the corrected code uses two new disposable sources
at the same exact farmgui commit and all 682 scoped cached LFS objects per source.
The direct publisher passes in **4.094 s** (**24.982 s** including verification).
The actual isolated Codex 0.156.1 worker passes in **51.927 s**, including a
**3.986 s** publisher run. Both prove assignment before Python startup and export;
all 31 artifact hashes match the earlier measurements. Both nested and outer Jobs
empty, observed worker members exit, and no attempt remains. Existing
authentication-seed metadata and protected runtime registration receipts remain
unchanged. Native unelevated execution needs no permission repair or alternate
shell. This is developer helper evidence, with no worker authority expansion,
actual Client install, Unity acceptance or production readiness claim.

## As executed: durable receipts and scoped Client route (2026-10-07)

[#162](https://github.com/Kuaiwa-Network/farm-linear-agent/pull/162) merges as
`bf9a61995ca88086a239dd3e058cd701573890d4`, with the exact tested tree of candidate
`4303702a46fa06ee3f96622ddf143ea9bba6e721`. Its manifest remains authoring-only; the
scoped guard/loading/delivery activation follows separately.

| Run | Tests | Duration | Failures/errors | Platform skips |
|---|---:|---:|---:|---:|
| native-final | 2073 | 1519.764 s | 0 / 0 | 69 |
| ci-macos | 2073 | 673.645 s | 0 / 0 | 57 |
| ci-windows | 2073 | 2196.510 s | 0 / 0 | 69 |

All 61 new boundary checks run on all three hosts; all ten native Job and twelve
native publisher checks run on both Windows hosts. Every platform skip ID/reason
equals #160: 69 Windows, 57 macOS. The measured discovery total is 2,073; the
private initial estimate counted two preview CLI additions twice and was corrected
without changing the tested candidate. Hosted synthetic commit
`6343b573ba7be543951089dea8ba49d50985c6a2` has the exact candidate tree, and
[run 37550685083](https://github.com/Kuaiwa-Network/farm-linear-agent/actions/runs/37550685083)
passes both jobs. Versions, UTF-8/environment sanitization and fresh symlink checks
follow the foundation record above; complete UTF-8 logs, per-test timings and all
skips stay private. The actual Client guard parser calibration preserves all 39
literal entries unchanged.

These are development-PC/offline results. They do not certify production-host
readiness, a real UI card, actual Client guards or Unity loading. No running bot,
production configuration/state, live issue, credentials, app preferences or
license setup is changed.

## Verified scoped UI activation and delivery verification

The scoped manifest now grants farmgui and Farm-Client, initially farmgui, plus
`unity_slot`. Actual attempt write roots remain exactly the active stage, and
detached Client main remains read-only. The pinned dispatch authority and worker
instructions require actual controller review/export/install identities, fresh
named human events, all changed/shared packages, immutable Client baseline and
exact scope, actual hydrated guards, reserved package loading and both verified
drafts. A missing configured native publisher still parks at a named stage limit.
No direct paid CLI, arbitrary MCP, gameplay/bindings, kw_ops, Bash/MSYS/WSL or
production operation is granted.

The new guard command uses native suspended Job assignment, fresh bounded private
TRX/logs, fixed filters, claim/Stop/tool/source fences and only the two actual
Client methods. Skipped, foreign, incomplete or Inconclusive is pending. The
reserved Unity command uses the existing pinned MCP identity route and fixed
probe. Imported descriptors must hash to committed Client bytes; every selected
package and declared atlas/sound/misc asset must load. Already registered package
IDs/names/path aliases refuse before registry mutation. Only probe-owned wrappers
are removed, with imported Unity assets retained for other users of the Editor.
This package-loading proof does not establish designed-panel rendering, gameplay
behavior or human visual/runtime acceptance.

Delivery requires current trusted receipt/installation/guard/loading records,
resource release and exactly the actual source/Client drafts at their unchanged
heads, verified through the real publication API and owned Git boundary. The CLI
injects a current controller delivery identity; a plan or finish paragraph cannot
fabricate success. Existing no-change investigations carry no PR and do not
certify a UI package. No schema migration is added; retain journals/audit records
and settle in-flight UI work before an authorized rollback.

Focused native development checks pass 10 new guard tests (three actual native
Job checks), eight loading tests and eight delivery tests, plus 71 existing CLI,
229 ledger, 75 skill, 25 dispatch and 11 authoring UI checks. Actual durable
handoffs, local Git, immutable export/install records, real CLI parsing/dispatch,
reserved ledger state and confirmed stub delivery comments are exercised.
Publisher processes, guard TRX, MCP loading and GitHub metadata in these fixtures
remain scripted; they do not certify a real UI card, paid export, actual Client
guards or Unity execution. The additional native guard tests are explicit macOS
platform skips and must actually run on both Windows hosts.

During integration, the first delivery implementation called the publication
verifier with the wrong API shape, hidden by a permissive mock. It now uses the
actual signature; a real owned-Git/scripted-metadata verifier regression catches
foreign or changed draft heads. Fixture configuration is corrected for the CLI's
real configured-repository check. The authoring journey's stage assertion remains
farmgui only, despite the wider manifest. No scope, ownership, containment or
skip is weakened. Final exact-candidate full verification is recorded below.

Farmgui's conditional owner grant merges as
`1193f21008dea66823c721a0818bf6690c33376b` in
[#142](https://github.com/Kuaiwa-Network/farmgui/pull/142); its two documentation
files preserve the standing fix grant. Native registration/resource/ID/cycle lint
and its relative link/whitespace checks pass. No actual source package, Client
asset, game draft, live card, bot configuration or production state changes.

## As executed: verified scoped UI delivery route (2026-10-07)

[#163](https://github.com/Kuaiwa-Network/farm-linear-agent/pull/163) merges as
`06234234b6084b6ae7340158a3679bd8c5a805e6`, retaining the exact tested tree of candidate
`e61bed6064bb437b36457eff248c7e9859217c5f`. Its source/Client grant is opt-in, and actual
attempt roots remain scoped to one active repository. No default host enablement,
running TestBot, production configuration/state, real issue or game draft changes.

| Run | Tests | Duration | Failures/errors | Platform skips |
|---|---:|---:|---:|---:|
| native-final | 2099 | 1670.912 s | 0 / 0 | 69 |
| ci-macos | 2099 | 776.105 s | 0 / 0 | 60 |
| ci-windows | 2099 | 2250.091 s | 0 / 0 | 69 |

Hosted synthetic commit `5a65a36a1dfaf36089a79d959236f7be6a9b215f` has the exact
candidate tree. [Run 37554351140](https://github.com/Kuaiwa-Network/farm-linear-agent/actions/runs/37554351140)
passes both jobs. All 26 new boundary tests run on both Windows hosts, including
all three native guard containment checks; all ten shared native Job and twelve
native publisher checks also run. macOS runs 23 portable additions and skips only
the three new native guard tests in addition to its 57 established skips. Every
established skip ID/reason is unchanged: Windows retains exactly 69; macOS totals
60. The new skips are:

- `test_fgui_guards.NativeGuardProcessTests.test_selected_python_redirector_runs_only_after_assignment_and_drains_job`: native Windows UI guard Job Objects.
- `test_fgui_guards.NativeGuardProcessTests.test_stop_before_resume_never_starts_process`: native Windows UI guard Job Objects.
- `test_fgui_guards.NativeGuardProcessTests.test_timeout_reaps_owned_python_tree_and_retains_logs`: native Windows UI guard Job Objects.

All 22 Code journeys, 11 authoring UI checks, 45 preview/transport checks, 34
export checks, 22 installer checks and all 61 receipt/scope additions remain
actual checks. Development Python/Git/LFS are 3.13.16, 2.54.0.windows.1 and 3.7.1;
hosted Python is 3.13.15, macOS Git/LFS 2.55.0/3.8.0 and Windows Git/LFS
2.55.0.windows.5/3.7.1. Fresh native symlink checks, UTF-8 and inherited
selector/token sanitization precede full discovery. Complete UTF-8 logs, every
skip ID/reason, selected revisions and per-test timings remain private.

This is development-PC/offline evidence. Publisher, actual guard-result and MCP
fixtures remain scripted, and package loading alone cannot establish target-panel
rendering, gameplay or human visual/runtime acceptance. Approved live UI work with
actual documents/art, app-token upload, named corrections/approval/export, hydrated
Client guards and exact-commit Unity loading remains required. Final production
host tool/runtime/permission checks and enablement/deployment need separate
operational authorization; another checkout or PC does not certify that host.

The runtime implementation and owner export grant are complete and verified
offline. No existing parked Code card, unmerged game test draft, source preview
or fixture result supplies the remaining live UI acceptance. No schema migration
or automatic credential/profile/state cleanup is introduced. Preserve audit and
interrupted installer evidence; settle in-flight UI work before an authorized
rollback. This follow-up changes only this verification record: its code blobs
remain byte-identical to the independently verified runtime candidate.

## As executed: native TestBot intake and scope stop (2026-10-07)

The operator approves one development Windows UI trial on
[FARM-1408 — 上架UI](https://linear.app/kuaiwagames/issue/FARM-1408/上架ui).
The intended TestBot is settled before its update: six historical jobs are terminal
(two failed, four cancelled), with no workers, pending webhook/cleanup, reservations
or live recorded Job members. Its explicit development identity, unchanged owned
state root and configured publishing destinations are verified; its existing
private config is backed up. TestBot runs the exact verified runtime tree at
`70716fc53a9cf8d2111cb2233b254a2b4155cfec`, using the prepared native Python/Pillow
environment and enabling chat/fix/fgui; Code remains disabled. No production
config/state/service, credentials, license, account or app settings are changed.

Sanitized UI doctor finds no missing tools: Python 3.13.16, Pillow 12.3.0,
Git LFS 3.7.1, the prepared CJK font and existing bot-only lark-cli 1.0.82 profile
(no other profiles or user logins). All four explicitly selected native publisher
hashes match. This neither executes the publisher nor certifies its license.
There is still no configured development Unity slot; real Client/loading
acceptance remains pending. The two established failed-job findings are retained.

The selected card receives Bot/UI, delegation to TestBot and a human-attributed
trial-scope comment with the project's existing planning-file link. A real TestBot
agent session and its signed webhook start the native authoring worker. The worker
and all three observed descendants belong to its named Windows Job. Doctor's
ordinary PID probe reports `platform_not_supported` on Windows; the separate native
Job query supplies containment evidence without weakening any ownership check.

The worker's real `download-uploads` retrieves and verifies both selected inputs:

| Input | Bytes | SHA-256 | Dimensions |
|---|---:|---|---|
| 餐厅经营_系统策划案.md | 43592 | `d4537007ed3866da2cae4896eb063ebc2ecf827ad5f661e53d8c3596fe1bf7b3` | — |
| image.png | 636957 | `7140b8004d67be153e5721edb5e3007e588c4319372f346bc81c3e896baeef95` | 503 × 862 |

Foreign-work inspection finds no competing issue publication. The source worktree
is clean at farmgui main `1193f21008dea66823c721a0818bf6690c33376b`; read-only Client
context is `5ca9db4f4ef4ae7a21c50c7243429a6f537a29a8`. The screenshot's marked
“还没有可上架的菜肴，先去制作吧” is `emptyFoodText`, created by
`assets/Scripts/HotUpdate/Features/Shelf/RestaurantViewCookingPrompt.cs`, with
explicit brown `Color(0.35f, 0.23f, 0.18f)`. There is no corresponding source text
node in farmgui's `CakeShelf/RestaurantView`. A source-only edit cannot fulfill
this request, and the UI Client grant does not permit a C# color patch.

The worker posts a durable scope question and parks in `awaiting_input` /
`scope_question`. Its worker PID clears, its recorded Job is empty, the heartbeat
has no workers and there is no pending cleanup. No source change, preview,
app-token image upload, PR, licensed export, Client install, actual Client guard
or Unity probe is produced. This demonstrates real native intake and an effective
scope stop, not live UI delivery or production readiness. A small runtime color
fix belongs to the existing bug fixer (`Bot/修改`); the worker's suggested separate
Code task is not necessary for this defect. No label change or separate repair
is performed without the operator's next decision.

This trial also reveals a changed provider behavior: the authorized API delegation
itself opens a real Linear agent session. Relying on the old “API creates no
session” documentation, the development agent explicitly opens a second session.
The controller cancels/supersedes the earlier queued UI item, settles its cleanup
and runs exactly one successor; both session threads retain the visible transfer
record. Inspect the actual session before another creation request; this observation
does not guarantee every API delegation opens one on every workspace or app.

The next proposed bounded trial is
[FARM-1396 — 礼包花圃ui](https://linear.app/kuaiwagames/issue/FARM-1396/礼包花圃ui),
item 1 only. Read-only source inspection finds the actual authored text nodes:
`Activities/Panels/GiftGardenView.xml` has `pagePlaqueText` (“本页礼包”), and
`Activities/Components/GiftGardenPageBuyBtn.xml` has `title` (“整页购买”). The
current authoring rules already identify the latter's 160 × 40 frame / 42-point
text clipping. The issue has an uploaded target artwork and runtime reference
screenshots and is Todo, undelegated and assigned to the approving operator.
Its second effect-layering item is outside this proposed first trial. This is a
proposal only: FARM-1396 has not been relabeled, delegated, commented on or worked.
Switching the selected trial and withdrawing the parked FARM-1408 UI attempt need
the operator's decision. Existing parked Code cards and game test drafts remain
untouched. Actual source preview/upload/visual approval/export, hydrated Client
guards, exact-commit Unity loading and final-host release checks remain required.

## As executed: settled withdrawal and bounded text trial (2026-10-07)

The operator approves withdrawing the parked FARM-1408 UI attempt and selecting
[FARM-1396 — 礼包花圃ui](https://linear.app/kuaiwagames/issue/FARM-1396/礼包花圃ui),
item 1 only. FARM-1408 returns to its original Todo state and Improvement label,
with no delegate. The controller's normal withdrawal settles both UI items as
cancelled, removes their owned worktrees/read checkouts and clears all recorded
native Job members. There are no workers, queued reservations or pending cleanup
at this fence. Historical failure records remain preserved; no runtime color
repair, manual ledger change or production cleanup occurs.

FARM-1396 receives Bot/UI and delegation to the intended development TestBot,
retaining its Bug label, Todo state, assignee and original description. The named
operator's scope comment permits only the existing “本页礼包” and “整页购买” text
correction using this card's original art/screenshots. Item 2, effect layering,
remains pending. This operational approval does not approve a future visual
round or request export, and partial item-1 delivery cannot close the whole card.

The API delegation itself opens exactly one observed TestBot root session; no
additional session-creation mutation is sent. Its signed intake starts one native
authoring attempt at farmgui main `1193f21008dea66823c721a0818bf6690c33376b`.
The worker and all three observed descendants are members of its named Windows
Job. The runtime checkout remains frozen at
`70716fc53a9cf8d2111cb2233b254a2b4155cfec`; configuration, ownership and production
remain unchanged. The actual download manifest and downloaded bytes verify:

| Input | Bytes | SHA-256 | Dimensions |
|---|---:|---|---|
| 弹窗005.png | 2651212 | `3cfff1280936059eea7488ede4cdc6b551ea28454bef39390a89034585170f18` | 1080 × 1920 |
| image.png | 694794 | `7437b0bf4ec20e8dfe92eb4d78ade4478cd5e85d8ef38b6c54978dea5ad99b3e` | 501 × 781 |
| image-2.png | 428855 | `688a2422fe9b4d515e1d343ca13188e2c1f831da4cff169f1ea3273f6d4360b5` | 367 × 562 |

The third image belongs to the deferred item; downloading it does not expand the
approved scope. The real TestBot start notice explicitly retains that deferral.
Read-only Windows inventory finds the registered Unity 2022.3.62f3 executable
(file version 2022.3.62.9860879), matching Client's required Editor revision.
No Editor is running at inventory time and no development Unity slot is yet
configured. Presence/version alone proves neither license, project import, MCP
identity nor loading acceptance.

The actual worker commits only the two Activities XML text layouts and
`docs/farm-1396-gift-garden-ui.md`, source head
`070c0f82bce1798bf817256e1ec5db6b528531f6`. Native text measurement/checks pass;
IDs, component names, artwork and binding/gameplay scope remain unchanged.
It generates an approximate static preview and the original-art comparison.
The resumed worker verifies publication and opens
[farmgui draft #143](https://github.com/Kuaiwa-Network/farmgui/pull/143) at that exact
source HEAD. Two real app-token PNG uploads succeed. The controller records
review `82b81f5d6de74f0d9684e4f17a87c1fa`, round 1, matching source/draft identity,
changed package Activities (`fvyctcfd`) and the actual comparison image:
4,056,354 bytes, SHA-256
`cf61527c9a09bf37284ad13f5584b2962d85a02e607f324063e45a5c33e6a54f`.
Its selected export closure is Activities, Common, CommonFx, ItemIcons,
PlayerCustomize and UILangTex. The confirmed `visual-1` notice names that current
image and source HEAD. These are actual source/publication/upload/review results,
not human visual or export acceptance.

The sanitized development wrapper's missing LFS filter registration makes its
hydrated dependency originals appear dirty. The operator requests a checkpoint
and pause before publication; both resulting worker attempts settle with empty
native Jobs, preserving the source commit. The controller is stopped only after
fresh quiescence and owned process/listener checks. All 1,031 original files
(188,942,783 bytes) independently match the committed LFS SHA-256/size. A verified
native zero-argument content filter and explicit verified-path index refresh
restore clean status without changing HEAD, the indexed tree or original bytes.
The [native filter record](../spikes/2026-10-07-windows-native-lfs-filter.md)
preserves both recovery refusals and distinguishes this host gap from source
damage. It adds no Bash/MSYS/WSL/MXC, global/clone configuration or worker grants.

The same frozen development runtime restarts with the verified optional filter;
only its process environment changes. A named operator reply explicitly resumes
the existing FARM-1396 item/session, retaining item 2 as pending and withholding
visual approval/export. The resumed worker and all observed descendants belong
to its named native Job. It then parks in `awaiting_input` / `visual_approval`;
the PID clears and every recorded Job is empty, with no pending webhook/cleanup,
reservation or heartbeat worker. No export record or Client draft exists.
The comparison explicitly labels static placeholders/system-font differences
and unsupported effects/relations/transitions; it is neither FairyGUI nor Unity
output. Both requested phrases are visible in this approximation, but runtime
clipping must still be checked after export. Item 2 remains pending.

The optional native filter's first full local run exposes a UTF-8 BOM regression
and slow binary failure formatting, recorded separately. The corrected adapter
passes 15 focused and 38 sequence checks and is selected only after a fresh
settled-development restart fence. Its corrected full runs then pass 2,114 tests
on the development PC (1680.382 seconds, 69 skips), hosted macOS (741.446 seconds,
73 skips) and hosted Windows (2216.710 seconds, 69 skips), all with zero
failures/errors and verified equal candidate trees. Both Windows runs execute
all thirteen native filter checks; established platform skip IDs/reasons are
unchanged. PR #167 merges at `7604de5dfb7f5889f0f0613196e3a1a26f308a92` with
that tested tree. The initial incomplete run remains separate evidence.

### Actual round-1 approval and refused export certification

At 06:24:04.670 UTC, 马张力 posts actual reply
`575cf37e-a5e5-4eb4-b084-f6fa8a17ae5f` under the existing session root, explicitly
approving round 1 and requesting export. Its signed session event becomes actual
stored message 8 with the same named author and body. The fourth native attempt
consumes it and saves separate `visual_approved` and `export_requested` events
bound to the unchanged review/head/source digest/preview hash. No duplicate
approval or session is sent.

The selected native publisher completes six approved/dependency packages and
50 private artifacts, with exit zero, proved assignment before startup/export
and an empty owned Job. Read-only revalidation confirms unchanged staged bytes,
source head and complete file-hash map. The controller nevertheless refuses
`UI export receipt cannot certify stale source or uncertain process ownership`:
the publisher hashes the complete `{head, files}` identity while approval and
controller receipt checks hash `files` and bind the full commit separately.
This is a controller integration regression, not an absent license, missing host
capability or stale approval. Helper staging is retained for diagnosis and is
never substituted for a certified receipt.

The confirmed `export-stage-limit-1` notice is
`04a569cd-4fab-4e72-bb21-5985a0b9dd79`. The same item/session parks in
`awaiting_input` / `export_stage_limit`, its PID clears, all recorded native Jobs
are empty, and there are no pending webhooks, cleanup, reservations or heartbeat
workers. No controller export record, Client handoff/change or Unity result exists.
The approved round is preserved while a separate controller correction receives
regression verification; a fresh controlled export must follow. Actual Client
guards, exact-commit Unity loading, runtime UI QA and separately authorized
production-host release checks remain pending. Item 2 remains pending and no
game draft is merged.

### Corrected receipt and actual Client handoff

[PR #169](https://github.com/Kuaiwa-Network/farm-linear-agent/pull/169) makes the
native publisher use the same canonical file-map digest as the approved review
and immutable controller receipt. The full source commit remains a separate
exact-match authority field. Before/after source checks, current input/claim,
delegation/Stop checks and native Job ownership are preserved. A regression
executes the real publisher validation through the controller receipt boundary;
only the licensed native process is mocked in this offline check. Against the
old publisher it reproduces the exact live refused-receipt error. All 100 focused
publisher/workflow/record/approval/source checks pass in 82.329 seconds with no
failures, errors or skips, including all twelve native publisher checks.

The corrected candidate `39140414f1f9be510bce5735079e8f18aa110f6b` completes a
fresh full offline discovery on the native development PC and both hosted
platforms. Each invocation starts with Python UTF-8 and sanitized inherited
selectors/credentials; complete UTF-8 logs, revisions, versions, per-test timings
and every skip ID/reason are retained privately:

| Host | Tests | Duration | Failures / errors | Skips |
|---|---:|---:|---:|---:|
| Native Windows development PC | 2115 | 1709.707 s | 0 / 0 | 69 |
| Hosted macOS | 2115 | 727.443 s | 0 / 0 | 73 |
| Hosted Windows | 2115 | 2337.487 s | 0 / 0 | 69 |

The [hosted run](https://github.com/Kuaiwa-Network/farm-linear-agent/actions/runs/37583390863)
identifies synthetic revision `740797cea6f85324794b9012984c821830d28859`. Its tree,
the candidate tree and merged tree all equal
`8b314ea711a90e44b08683e9f1f50f873274659c`. PR #169 merges as
`5a23e700683c4039329447c6acc85cf4e50a44e2`; its verified topic branch is retired.
Every established skip ID/reason is unchanged from the corrected #167 runs.
Both Windows runs execute all thirteen native filter, ten shared Job, twelve
publisher and three guard-process checks. The new publisher-to-controller
receipt regression runs on all three hosts. Development Python/Git/LFS are
3.13.16 / 2.54.0.windows.1 / 3.7.1; hosted Python is 3.13.15, with macOS Git/LFS
2.55.0 / 3.8.0 and Windows Git/LFS 2.55.0.windows.5 / 3.7.1. No new skip or
weakened check obtains this pass.

After the focused checks, a fresh settled-development fence verifies cleared
worker PIDs, empty recorded Jobs and no pending webhook/cleanup, reservations
or heartbeat workers. Exact executable/command, current owner, creation time,
ancestry and the sole development listener identify the controller being stopped.
Only that development controller restarts with the frozen corrected candidate;
the private config, native filter and original approval/source identity remain
unchanged. The full suite completes afterward as recorded above. This is a
development-PC trial, not production deployment or production-host certification.

At 06:54:19.337 UTC, the named operator's operational continuation comment
`a3936c6c-0ae4-4928-97c5-08f5605c7f2a` resumes the same item/session for a fresh
controlled export. It retains item 1, already approved round 1 and the explicit
export request; it does not approve another preview. The fifth native attempt
produces controller receipt `0507c57b9c3742579446775e06dbd0a7`, receipt SHA-256
`c76829249415ff3eaff931523526f2223eb1cc96b9f8597f84033e420c3078fa`.
Read-only revalidation verifies its immutable audit checksum, original approval
message 8, unchanged review/source head/digest, all six packages and 50 staged
artifacts, exit zero, assignment before startup/export and an empty owned Job.
The earlier refused staging is retained separately and never installed.

The same job hands off to Farm-Client. The controller pins actual Client baseline
`5ca9db4f4ef4ae7a21c50c7243429a6f537a29a8` and launches the sixth native worker.
Its scoped Activities LFS pull cannot obtain credentials for Client's separate
LFS endpoint through the selected GitHub-only native callback. Fifteen Activities
files remain canonical LFS pointers; `install-ui` refuses before writing. There
is no installation receipt, Client commit/draft, actual dependency/orphan guard
result or Unity result. Source draft #143 and the certified export remain intact.

The confirmed `client-lfs-stage-limit-1` notice is
`9474f507-1403-45b5-9a80-2e827fd9aa15` at 07:12:50.598 UTC. The job parks in
`awaiting_input` / `client_lfs_stage_limit`, rooted in Farm-Client. A fresh
read-only snapshot finds all worker PIDs clear, all recorded native Jobs empty
and no pending webhook/cleanup, reservations, heartbeat workers or other
unfinished jobs. The selected Client target is retained. The older source-stage
quiescence helper's requirement for a null target does not apply to this Client
handoff; its refusal is not evidence of a live worker or permission to clear the
target. Historical jobs, approval and export evidence are preserved.

### Measured Client access and remaining acceptance

Read-only inspection of Client's committed `.lfsconfig` finds a separate HTTP
LFS endpoint without embedded credentials. Windows Credential Manager has an
entry for one of its standard target names; presence alone does not establish
validity or availability to the selected callback. No credential values are
printed or saved. Existing native Git Credential Manager 2.7.3 refuses a bounded
noninteractive retrieval because that endpoint uses unencrypted HTTP. These
findings distinguish the host's transport/authentication route from an
application regression. No helper setting, endpoint, credential or unsafe-remote
override is changed.

Two explicit development LFS caches are checked against all fifteen scoped
pointer hashes/sizes: the independent Client development checkout's shared Git
directory, verified inside the development parent, and the owned TestBot Client
clone, verified through its clone/config boundary. Neither contains those
objects. No object is copied, no remote request is made by this cache inventory,
and no production state/cache is read. A team-supported secure Client LFS access
route is now required before the existing job can resume its certified install.

Native Unity readiness remains a separate host gap: the configured development
slot count is zero, the required 2022.3.62f3 executable exists, no Editor is
running and native uv 0.12.14 is present. One existing MCP listener has no verified
development ownership/identity; it is not queried or used as this job's evidence.
No Editor, account, license or app setting is configured by this inspection.

The remaining prerequisites are:

- Establish the team's supported secure Client LFS route on this account, then
  resume the same scoped job and verify actual hydrated inputs.
- Complete controller-certified scoped Client install/metadata, commit/draft and
  the actual dependency/orphan guards at that exact candidate.
- Prepare a dedicated development Unity slot, verify its MCP/Editor identity and
  run reserved exact-commit package loading, followed by human runtime UI QA.
- Verify actual Feishu document reads when a trial requires them; this uploaded-art
  card does not exercise that integration. Finish the remaining broader live
  Code/closing/write-back journeys before claiming the whole feature release.
- Perform separately authorized final production-host tools/runtime/permissions,
  release and recovery checks before production enablement/deployment.

Item 2, parked Code cards and unmerged game test drafts remain pending. Game draft
#143 is not merged. This documentation update changes no executable behavior and
does not require another full suite run.
