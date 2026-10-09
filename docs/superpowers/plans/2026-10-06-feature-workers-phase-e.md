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

Implementation and exact-candidate native/hosted offline verification are complete.
The fresh FARM-1396 item-1 trial below now completes actual licensed export, scoped
Client installation, hydrated guards, reserved exact-commit Unity loading,
quiescent release and two-draft delivery on the development Windows PC. Both
game PRs are now human-merged, with the tested changed files preserved; the
operator-confirmed screenshot passes scoped item-1 visual QA. Native scoped
Feishu wiki/document access also passes below, with explicit content limits. Historical
pending checkpoints below describe their state when recorded. Remaining gates:

1. Complete the operator-selected document-driven FARM-1104 UI trial below.
   Actual native UI-worker intake of the full leaderboard R4 DOCX and seven
   uploads is measured. Round 1 has the operator's actual combined approval and
   a successful controller-certified export. Native restoration of all 92 required
   Client originals and current source-main history is independently verified.
   Controller-certified Client installation, the single-file candidate scope
   and both actual native guards are independently verified. The later successful
   continuation below now completes exact-commit Unity loading, quiescent release,
   controller-certified two-draft delivery and normal task cleanup. Source
   [farmgui #150](https://github.com/Kuaiwa-Network/farmgui/pull/150) and Client
   [#1448](https://github.com/Kuaiwa-Network/Farm-Client/pull/1448) remain review
   drafts. Real dynamic panel QA still needs the existing development Client;
   package-loading evidence and the approximate source renderer do not establish
   actual runtime layout or visibility behavior.
   FARM-1396 item 2 and whole-card acceptance remain outside this trial.
2. Separately scoped real Code later-stage acceptance: Common/config/hive and native
   generators, running-worker withdrawal, Client/closing Unity tests and the real
   Contract gates, merge/squash resynchronization, write-back and archive. The
   [Phase C plan](2026-10-06-feature-workers-phase-c.md) distinguishes implemented
   offline behavior from this pending live journey. The approved FARM-1419 native
   Windows journey has started below; later-stage acceptance is still pending,
   and parked cards stay parked. Its Contract corrections are now merged, and
   the operator has merged farm-hive #367. Its three-file generated protocol
   follow-up #377 is now merged with the exact reviewed tree. Remaining UI-ready,
   Client/closing Unity, consumer resynchronization, write-back and archive
   acceptance stay pending; full game-suite and hosted CI gaps remain explicit.
   The operator now confirms that FARM-1419 art/UI is unfinished; its worker
   parks at `ui_ready` with no Client stage accepted. Deferred designer archive
   publication remains outside the approved scope.
   External runtime interruption and development webhook/recovery state are
   recorded separately. Game-repository CI failures below do not establish a
   FarmBot implementation defect or replace these live acceptance gates.
3. Intended production-host tools, licensed exporter, native runtime, selected
   account/profile/permissions, Git/LFS, dependency restore/cache, cold Editor
   startup and release/recovery acceptance, followed by separate authorization for
   production enablement and deployment. Development-PC evidence is not that
   certification; the measured cold-import deadline gap remains explicit.

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

## As executed: operator-confirmed local Gitea route (2026-10-07)

The operator clarifies that the existing separate LFS service is local Gitea
over HTTP. FarmBot does not require an HTTPS migration. The earlier refusal is
Git Credential Manager's default unsafe-remote policy, combined with the private
development wrapper selecting only the GitHub callback. A normal GitHub commit
push does not test Gitea LFS access; downloading or publishing new LFS objects
uses that separate service, including when the existing bug fixer needs them.

A bounded noninteractive lookup for only that operator-confirmed origin, using
the existing native GCM executable and Windows store, finds both existing
credential fields in 0.160 seconds. Credential values are neither saved nor
printed, and no credential/profile/account or global/clone setting is configured.
The already-tested `build_windows_git_askpass.py` dual-callback route is built in
a new private tools directory outside worker writes. Its adjacent private
settings pin the exact HTTP origin and native GCM executable hash. GitHub retains
its existing gh route and cannot fall back to Gitea; unrelated destinations are
refused. Three wrong-destination probes and all eighteen native LFS callback
checks pass (3.804 seconds, zero failures/errors/skips).

The development process receives URL-scoped `credential.<origin>.allowUnsafeRemotes`,
`provider` and `allowWindowsAuth` selections for only that origin. No global
`GCM_ALLOW_UNSAFE_REMOTES` environment override is selected. Inherited GCM
overrides are withheld, interactivity and secret tracing are disabled, and the
helper/settings/source/gh/GCM hashes are rechecked before selection. This reuses
existing native tools and runtime code; it does not introduce Bash/MSYS/WSL/MXC,
credential setup, a new account or a production setting.
The [GCM configuration reference](https://github.com/git-ecosystem/git-credential-manager/blob/main/docs/configuration.md)
documents URL-specific credential policy and its default HTTP refusal.

A fresh Client-stage read-only fence verifies the retained exact baseline and
branch, clean Client status, immutable export/original approval, owned clones,
cleared worker PIDs, empty Jobs and no pending webhook/cleanup, reservations or
heartbeat workers. Exact old controller executable/command, owner, creation time
and sole development listener are checked before stopping it. Only that settled
development controller restarts with the selected dual callback. The frozen
runtime revision and private config remain unchanged; the new process's
owner/ancestry/listener match and health returns 200. This response proves only
that its HTTP handler answers.

At 07:54:12.936 UTC, the named operator's continuation comment
`51f922c7-dce5-490a-93b3-923fdf52fc4c` resumes only the existing item-1 Client stage
and existing certified receipt. The seventh native worker and all three observed
descendants are members of its named Job. Actual LFS hydration, certified Client
install/guards and development Unity acceptance are measured separately below;
credential availability or a callback fixture cannot certify them.

The actual scoped LFS pull succeeds in 2.558 seconds through the existing local
Gitea route. The first installer invocation refuses a dirty baseline; a later
invocation passes the same clean-baseline gate and records controller installation
`0c4a302d81ce4036b0f399588d5449de`, SHA-256
`8754ffb3641862130df501207cf05bc6461b720770730dd20ada19d9078754a2`.
It installs only Activities: 31 files including sixteen metadata files. A
separate read-only check rehashes the installed inventory and revalidates the
global metadata GUID digest against that immutable receipt. No first-install,
ownership or metadata check is bypassed. The committed Client candidate also
passes the controller's complete scope check; actual guards/publication and
Unity loading are separate outcomes.

### Actual Client draft and NuGet preparation

The worker publishes and records Client draft
[#1425](https://github.com/Kuaiwa-Network/Farm-Client/pull/1425), head
`e6c48321bf0b2d32262594075618c5371e439b58`, beside the unchanged source draft
[#143](https://github.com/Kuaiwa-Network/farmgui/pull/143). Both are independently
checked open and draft at those exact heads. Neither game PR is merged.

The first `verify-ui-guards` invocation stops after 111.018 seconds during
NuGet restore: five `NU1301` diagnostics, no TRX and neither guard executed.
The job parks in `awaiting_input` / `client_guard_stage_limit`. A fresh read-only
snapshot finds cleared worker PIDs, empty recorded Jobs and no pending
webhook/cleanup, reservations, heartbeat workers or other unfinished job.
The immutable source/export/installation receipts and pinned Client target
remain intact. This is a host dependency-access gap, not a measured guard pass
or an established UI regression. The
[NuGet diagnostic reference](https://learn.microsoft.com/en-us/nuget/reference/errors-and-warnings/nu1301)
describes this restore-stage source failure.

A separate native Python HTTPS read obtains the valid public NuGet index with
HTTP 200 in 1.115 seconds; it does not prove the worker's .NET transport works.
All five pinned direct Client packages are present and SHA-512-valid in the
earlier isolated development cache. Eleven public package archives, including
the cached dependencies, are independently checked and copied into a new owned
job run directory. A fresh native contained restore of the actual Client unit
project, using only that local feed and package cache, succeeds in 2.345 seconds.
Its Job is assigned before startup and empty on completion. No credentials,
global NuGet/TLS setting or tracked Client file changes. This preparation does
not write a controller guard receipt or substitute for executing both tests.

At 08:28:20.347 UTC, named operator comment
`fcf3f476-e472-4344-bbee-ecafa005d108` resumes this exact item-1 candidate and
receipt, selecting the owned cache through `NUGET_PACKAGES` for the verification
process. It requires the actual guarded CLI run, preserves both drafts and the
original round-1 approval, and retains the dedicated Unity-slot gap. No service
restart or configuration change is needed for this cache selection.

### Actual guards and pre-existing MonthlyPass failure

The resumed actual guarded CLI invocation completes after 94.295 seconds with a
complete two-test TRX: `FguiOrphanAtlasGuardTests` passes, while
`FguiDependencyGuardTests` fails on `MonthlyPass -> CommonFx`. Neither test is
skipped or Inconclusive; there are no compiler error diagnostics. No passing
controller guard receipt is recorded, and Unity loading cannot advance.

Read-only comparison proves the MonthlyPass descriptor's LFS pointer is identical
at pinned baseline `5ca9db4f4ef4ae7a21c50c7243429a6f537a29a8` and Client candidate
`e6c48321bf0b2d32262594075618c5371e439b58`. The materialized 22,535 bytes match its
committed SHA-256 `0665c0aa4c261ce678e4fb72da816fbe9165d3873819c5c755f2df026f6c7e13`.
The MonthlyPass guard entry is unchanged (`Common` only); the candidate's guard
diff changes only Activities and its reason comment.

A separate native contained focused negative control compiles the exact baseline
guard source and root-discovery helper against only that verified unchanged
MonthlyPass descriptor. Its actual test reproduces the same failure. This is a
focused baseline reproduction, not a full baseline Client suite or release
evidence. Actual source inspection finds one existing CommonFx movieclip in
`MonthlyPass/Components/MonthlyPassRewardCell.xml`, component name `n4`, resource
`dy4c1`. No source, asset, guard entry or game PR is changed by this diagnosis.

The failure is pre-existing in the pinned Client inputs and belongs outside the
approved Activities-only trial. A proposed separate Client follow-up would
review and document that existing structural dependency in the MonthlyPass
guard entry, leaving the descriptor and UI behavior unchanged. It is not applied
to the current trial or treated as a passing result; operator scope approval and
ordinary game draft review are required before that follow-up. The current source
and Client drafts, certified export/install receipts and original visual approval
remain preserved.

Remaining release prerequisites are now:

- Resolve the independent MonthlyPass dependency-guard failure in its own
  reviewed Client follow-up, then run both actual guards against the resulting
  certified trial candidate without skips, Inconclusive results or bypasses.
- Configure an owned development Unity slot and verify its native Editor/MCP
  identity; run reserved exact-commit package loading, then human runtime UI QA.
- Exercise actual Feishu document reads on a suitable scoped trial and finish
  the remaining broader Code/closing/write-back acceptance journeys.
- Complete separately authorized final production-host tools/runtime/permissions,
  release and recovery checks before production enablement or deployment.

This is a development PC result. Production readiness, whole-card completion,
item 2 and game PR merges are not certified or authorized by these measurements.

## As executed: independent MonthlyPass guard follow-up (2026-10-07)

After the separate follow-up is proposed, the operator says to continue. The
Client's current GitHub main is checked before preparing a separate development
checkout and `codex/monthlypass-existing-commonfx` branch from
`5ca9db4f4ef4ae7a21c50c7243429a6f537a29a8`. Root `AGENTS.md` directs reading the
Client's complete `CLAUDE.md`; there are no nested test instructions. The parked
TestBot worktree and original trial's source/assets/receipts are untouched.

The independent Client change modifies only
`tests/Farm.Tests.Unit/FguiDependencyGuardTests.cs`: record `CommonFx` beside
`Common` for MonthlyPass, with a concrete reason identifying the existing
`MonthlyPassRewardCell` movieclip `dy4c1` / `n4` and its package lifetime. Guard
implementation and every other entry remain unchanged. The descriptor and
source are not republished. This records the baseline's existing structural
dependency; it is not a new asset or a runtime UI behavior change.

The new checkout's scoped native LFS pull fetches objects but reports that
checkout is skipped because no persistent LFS registration exists. No install,
global/clone Git setting or shell workaround is selected. Each materialized file
is checked against its committed pointer's SHA-256 and size: 694 files,
596,934,071 bytes. After a filtered diff proves no content change, a NUL-delimited
explicit verified-path index stat refresh preserves the indexed tree and HEAD;
native-filtered status is clean. These are this separate development checkout's
own cache and files, not production state.

Candidate `47ee09f1caf91409974aa4dd276322b6242ac172` runs both actual repository
guards from the complete Client unit project, using the prepared job-local public
package cache. All 64 package descriptors contain materialized FGUI data.

| Native Windows check | Measured outcome |
|---|---|
| Restore from verified public development feed | Exit 0, 0.928 seconds |
| Actual dependency and orphan-atlas guards | 2 passed, 6.232 seconds |
| Failed/error/skipped/Inconclusive results | 0 / 0 / 0 / 0 |
| Owned native processes | Job assigned before startup; empty on completion |
| Scoped diff and whitespace | One test file only; checks pass |

Complete UTF-8 logs, passing TRX hash, pointer inventory and process measurements
remain private. The prior exact-baseline negative control reproduces the failure;
this candidate's actual hydrated positive run passes. Hosted Client CI uses LFS
pointers, so its skipped FGUI results cannot replace this native evidence.

The independent game change is published as draft
[#1426](https://github.com/Kuaiwa-Network/Farm-Client/pull/1426), at that exact head
and baseline. No game PR is merged. Original Client draft
[#1425](https://github.com/Kuaiwa-Network/Farm-Client/pull/1425) and source draft
[#143](https://github.com/Kuaiwa-Network/farmgui/pull/143) remain intact. A fresh
read-only development snapshot confirms the original job remains parked with no
worker PIDs, pending webhooks/cleanup, reservations or heartbeat workers, and all
recorded Jobs empty.

This independent pass does not create a guard receipt for the parked Activities
job. Its pinned baseline still lacks the MonthlyPass entry. The controller's
`dependency_guard_change` permits edits only for the certified export's changed
packages, and `assert_installed` binds installation to the selected baseline;
inserting this unrelated entry into that job or replacing its target would fail
those checks. Human review/merge of the independent game follow-up must precede
planning verification from a corrected baseline. Existing drafts and historical
proofs must be preserved through that continuation; no reset, ledger relabel or
passing-proof substitution is performed.

Remaining acceptance is the original trial's complete controller-certified guard
run from corrected inputs, owned native Unity/MCP slot and exact-commit package
loading, human runtime visual QA, real Feishu document reads and the broader
Code/closing/write-back journeys. Production-host release/recovery checks and
separate enablement/deployment authorization remain required. These are measured
development-PC results, not production-host certification.

## As executed: corrected-baseline trial preparation and native Sol client (2026-10-07)

Client follow-up [#1426](https://github.com/Kuaiwa-Network/Farm-Client/pull/1426)
is independently checked merged, at `e9988b2d64a5fa5765496d37ec777492df66a278`.
The original Activities trial remains pinned to its earlier Client baseline;
that merge does not repin the item or give it a passing controller guard receipt.

Named operator comment `892906a8-8753-4c32-9030-5d1fbfd562c4`, at
12:43:29.781 UTC, requests only truthful blocker settlement of the obsolete-baseline
attempt. The worker posts the real blocker and finishes `blocked` through the normal
claim/checkpoint/confirmed-comment path. Its first finish is refused by the existing
comment/input fence; a fresh issue read and normal completion settle it without
loosening that fence. The real card remains open and item 2 remains pending.

A subsequent read-only snapshot verifies completed cleanup, unchanged target,
cleared worker PIDs, empty recorded Jobs and zero unfinished jobs, pending webhooks,
cleanup, reservations or heartbeat workers. The exact historical source head
`070c0f82bce1798bf817256e1ec5db6b528531f6` and Client head
`e6c48321bf0b2d32262594075618c5371e439b58` are retained in controller recovery refs
and immutable recovery-history snapshots. The historical export staging bytes are
rehashed against the original controller receipt; the installation receipt hash
is unchanged. Both original game drafts remain open and draft. No guard pass,
baseline replacement or borrowed approval is recorded.

Preparing an independent same-card run exposes a scheduler branch gap. When the
old run retains the canonical branch, source authoring receives a fresh issue
branch, but first Client entry still requests the old canonical name and is
correctly refused by the existing-branch guard. The real-Git regression fails
on the original code with that refusal (one test, 3.672 seconds). FarmBot
[#174](https://github.com/Kuaiwa-Network/farm-linear-agent/pull/174) carries the
validated plan's actual farmgui branch into first Client entry. An existing Client
pin remains authoritative; foreign/reserved source branches still refuse before
selection. It changes no baseline, ownership, scope or containment guard.

The native focused run passes 528 offline tests in 362.307 seconds, with zero
failures/errors and seven existing platform skips. Fixtures cover preserved older
source/Client branches, corrected trusted main, later main movement, immutable
existing pins and invalid source branches, including space/Unicode paths.
The frozen candidate is `a7c335bf64ad5c8954142622b79f26baea333f62`, tree
`95359602161662075282b62b2111000f603ad62d`. Hosted full suites pass on both
platforms: Windows 2,126 tests in
2,442.709 seconds with 69 existing platform skips; macOS 2,126 tests in
821.120 seconds with 73 existing platform skips. Both have zero failures/errors,
all 13 feature journeys and all 13 Client-stage tests run, and Windows executes
all 10 native Job Object tests. Every skip ID/reason matches the preceding
verified candidate. The hosted merge trees match the frozen tree. PR #174 is
merged as `e573f322a3b4d005070cb16d3cff0a3bf0abc974`, with that same tested tree.
Logs, Python/Git/LFS versions and every platform skip remain
private; those offline results do not certify this live trial.

After the settled development controller's exact executable, command, creation,
owner and sole development listener are checked, it switches to that frozen
development candidate. Private configuration and state ownership remain unchanged;
no production installation or configuration is selected. Read-only doctor reports
no missing UI tools, ready native export, the intended `gpt-6.1-sol` / `xhigh`
model selection and zero configured Unity slots. Historical failed/blocked jobs
remain findings. HTTP health 200 verifies only the development HTTP handler.

Named operator comment `67158fe3-e9e1-47c7-859c-a4bb659bdeea`, at
13:03:40.213 UTC, scopes a separate fresh item-1 preview and requires its own
approval/export request. A real TestBot session is created once, with a durable
attempt marker; its signed webhook creates independent item
`1f245123-4892-4246-a059-5313e844c685`, with no predecessor or Client intake pin.

### Actual model startup refusal and current native client

The first fresh attempt selects `gpt-6.1-sol` / `xhigh`, but exits before any task
command with HTTP 400: that model is unsupported for the selected Codex ChatGPT
client. Its Job is empty and it records no preview, review, export or installation.
The configured source catalog is from CLI 0.160.0 and lists GPT-6.1 Sol; the pinned
bot CLI 0.156.1 refreshes its own catalog without that model. This measured client
version mismatch is distinct from an application guard failure or a proven lack
of account access. The earlier offline model/config tests do not establish a live
model start. [OpenAI Docs](https://learn.chatgpt.com/docs/models) states that model
availability depends on client, sign-in method and rollout.

The already installed native CLI 0.160.0 has a valid Authenticode signature and
SHA-256 `37762753b554982eef1c109303d1be652b6397f1479e844794353a85650199c6`.
A separate contained model-only probe, with an empty work directory and no task
tools, returns `MODEL_READY` using GPT-6.1 Sol / xhigh in 16.273 seconds, exit 0.
Actual worker Job membership is checked, the Job is empty at completion and the
work directory is unchanged. No credential, account, global CLI setting or
installation is added.

Fresh quiescence/preservation and exact development-controller identity checks
precede selecting that signed installed CLI through the development process
environment. Frozen FarmBot source and private configuration remain unchanged;
new controller owner/ancestry/listener and HTTP health are verified. The normal
operator `retry` command advances only the fresh startup-failed item's generation
to 1. It does not resume the original blocked trial or reuse historical review
authority. Its second actual worker config retains GPT-6.1 Sol / xhigh; observed
worker/descendants are members of its native Job. That worker claims the item
and reads its real source/uploads. It then
parks normally because its foreign-work scan finds the merged Client #1426
and the new session has no direct continue/stop/adopt answer. Its source is
clean, it has no preview/review/export/install records, its PID clears and its
Job is empty. The worker posts actual notice `foreign-work-1`; this is a normal
human-choice gate, not a toolchain failure.

The operator's instruction to continue is relayed as named session reply
`32d10b30-47e5-49b5-b678-21f54fb9ca5c`, at 13:29:40.589 UTC. It chooses
the independent item-1 draft/preview, acknowledges merged #1426 as later Client
baseline context and preserves both historical drafts. It explicitly supplies
neither visual approval nor an export request. The signed continuation resumes
the same fresh item normally; its next attempt again selects GPT-6.1 Sol / xhigh
and has verified worker/observed-descendant Job membership. At this checkpoint,
preview/review completion remains pending and is not inferred from the
model-only probe or the foreign-work choice.

The latest-Sol policy reads configured-source metadata, but a catalog produced by
a newer desktop client cannot certify an older pinned bot CLI. Model promotion
requires checking the selected native client and a bounded real model start;
desktop updates alone do not upgrade that pinned runtime. Future client/model
availability remains a release check.

Remaining release prerequisites: the fresh trial's actual preview approval and
export, installation and complete controller-certified guards from corrected
Client inputs; an owned native development Unity/MCP slot, reserved exact-commit
package loading and human runtime UI QA; scoped actual Feishu reads and broader
Code/closing/write-back journeys; separately authorized production-host tool,
runtime, permissions, release/recovery checks and enablement/deployment. These are
development TestBot measurements, not production readiness or whole-card completion.

## As executed: fresh independent item-1 visual round (2026-10-07)

The named continuation recorded above is consumed as current session request 13.
The worker retains the independent item and its actual source branch, without
adopting historical drafts or using their approval. Its committed source head is
`9bbfa75acfd9a860947cf5c5d1ca840b53aafb72`, directly based on verified farmgui
main `1193f21008dea66823c721a0818bf6690c33376b`.

The source diff contains exactly two Activities XML files and the item-1 UI
document. `GiftGardenView/pagePlaqueText` receives a larger two-line text area;
`GiftGardenPageBuyBtn/title` receives a larger single-line area. Logical font size
42 and stable node names remain. Shrink behavior, artwork-matched text/stroke
colors and centered button geometry are recorded in the UI document. No package
ID, registration, image, publishing setting or consumer code changes. The worker
checks current references, cycles, IDs and original art against their hashes.

Actual mockup text measurements are recorded with their limits. The plaque-style
check changes from failing on the original source to passing on the new source.
Purchase text fill/stroke colors match the mockup measurement; the full purchase
stroke boundary cannot be measured cleanly because its mask overlaps the basket
and scan boundary. The runtime cause of the original missing plaque line remains
unverified. These source checks do not establish a runtime fix.

The native source renderer uses SourceHanSansSC-Bold. The full 1080x1920 panel
reports four unsupported effect categories: group/grayscale/partial-fill,
rotation/pivot/skew/blend transforms, sibling-target relations and `gearLook`.
Its separate 180x185 purchase-button preview renders with no reported gaps and
one approximation. The composite labels approximate previews beside actual
uploaded references, and records glyph/geometry deviations. It is not an Editor
capture or pixel/runtime acceptance.

Fresh source draft [farmgui #144](https://github.com/Kuaiwa-Network/farmgui/pull/144)
is verified open/draft at that exact head. The controller records round 1,
review `898a303fceb74f9da17836255ef021ea`, source digest
`97103d1c3cd0c063c368ac28988780aeea4e0eff6a3d49379ffc726677c46e63` and changed
package map `Activities: fvyctcfd`. Its actual uploaded 2200x2040 composite binds
SHA-256 `f4ba0110c9a8541771dea63ccb372a98ef486701aa8a90d55d26d5139a2adfac`;
the supplemental 1500x1000 close-up binds
`203d502d86f77770f2cf3ddbd24c68f94374cc233c1a9b3eca97a46a3d841f29`.
Both private PNGs are rehashed against the actual upload records. Preview files,
raw host logs and signed asset URLs remain private.

The confirmed `visual-1` notice, comment
`8c5613f4-a8af-4001-b008-20d4cd263f3b` at 14:03:49.569 UTC, names that exact
head and uploaded composite. A subsequent 14:06:41 UTC read-only snapshot finds
the same independent item parked at `awaiting_input/visual_review`,
`pause.kind=visual_approval`, no worker PID and an empty native Job. No human
approval/export events, Client target, certified export or installation exist at
that checkpoint. The old source #143 and Client #1425 remain open/draft at their
preserved heads. Normal controller evidence is used throughout.

Fresh named visual approval and an explicit export request remain required for
this unchanged round. No historical approval or controller export/install receipt
is reused. Subsequent acceptance still requires the fresh controller-certified
export, scoped Client installation and complete actual guards from corrected
inputs; an owned native development Unity/MCP slot and reserved exact-commit
loading; human runtime UI QA; scoped actual Feishu reads and broader Code/closing/
write-back journeys; separately authorized production-host release/recovery
checks and enablement/deployment. The real card remains open, item 2 remains
pending, and these are development TestBot results.

## As executed: fresh item-1 approval, export and Client handoff (2026-10-07 UTC)

The operator explicitly approves the fresh round and requests export. Named
session reply `99b7ce33-0339-444c-8387-43780a278232`, at 15:42:23.031 UTC,
binds source draft #144's unchanged head, review, source digest, composite hash
and Activities package identity recorded above. It accepts the approximate
preview's documented limits; runtime visual acceptance and item 2 remain pending.
Linear's signed continuation is stored as session message 14, with the same
named human author. Linear expands the issue and historical Client PR references
into links. Those exact link-only changes and terminal whitespace are checked
against the original reply; the controller's approval/request hashes match the
actual canonical stored message. No historical approval is reused.

The resumed worker uses the signed native Codex 0.160.0 with
`gpt-6.1-sol` / `xhigh`. Worker and observed descendants belong to their native
Job. Normal `export-ui` completes in 5.621 seconds, exit 0, with publisher Job
assignment before startup/export and an empty Job at completion. Controller
receipt `a052dbe4aa1a4787a2f9240b90aa0064` binds round 1 and source head
`9bbfa75acfd9a860947cf5c5d1ca840b53aafb72`; its immutable SHA-256 is
`9f0c66839f33364b696f815b72dc8a4de99036e5f4f9c2a353c840f5a1875214`.
All 50 retained artifacts across six exported dependency packages are rehashed
against the controller inventory. Only changed Activities is authorized for
Client installation.

The normal controller handoff pins Client baseline
`e9988b2d64a5fa5765496d37ec777492df66a278`, the actual merge of corrected
dependency-guard [Client #1426](https://github.com/Kuaiwa-Network/Farm-Client/pull/1426),
once at 15:57:07.443855 UTC. It carries the validated fresh source branch
`farmbot/farm-1396-1f245123-4892-4246-a059-5313e844c685-2`, exercising the
merged #174 handoff correction. The independent item enters a new Client
attempt normally; the historical blocked item and its drafts remain preserved.
Existing pins are not reset to a later main.

### Measured installation interruption and settled development recovery

The first `install-ui` refuses unhydrated selected-package inputs. Native LFS
pull/checkout leave those baseline files as pointers on this host; direct native
smudge returns bytes matching their committed OIDs and sizes. After scoped
hydration and validated Git stat refresh, the owned baseline is clean without
changing its index tree, branch or commit. No credential, global Git setting or
trusted clone configuration is repaired to obtain this result.

A subsequent normal installation applies the single changed
`Activities/Activities_fui.bytes` descriptor, then reports
`PublicationUnavailable: GitHub verification timed out` before recording its
success certificate. Another controller attempt also reports that timeout.
The helper's complete journal records one write and no deletes/directory changes;
it retains 31 original backups, totaling 27,514,274 bytes, all matching their
journal hashes. The new descriptor is 66,742 bytes, SHA-256
`4d7382c86561647d3fdb377d21aa8a0ff62b7906d0d27fe2843f82730a938969`, matching
the certified staging inventory. A complete helper journal is not a controller
installation record. No guard or Unity proof is recorded.

The worker parks normally at `awaiting_input/client_installation_gap`, with
`pause.kind=stage_limit`, no worker PID and an empty native Job. The controller
preserves the uncertified write and recovery evidence. Four focused native
offline tests of dirty-first-install refusal and interrupted-write preservation
pass in 12.478 seconds, with zero failures/errors/skips. An initial test harness
imports the old checkout and produces four import errors; its logs are retained
separately. The corrected fresh interpreter verifies current source imports.
Those harness errors are not application regressions or live guard results.

Once GitHub reads work again, source #144 is reverified open/draft at its unchanged
head. Settled operator recovery verifies the owned clone/worktree, sole changed
file, original Git/LFS identity, current certified export bytes and empty worker
Jobs. It restores only the development descriptor to its actual pinned baseline:
66,779 bytes, SHA-256
`25b8a80f04dad313720e579335811f21f630be950fca3bf4136d1ee30f95181f`.
The owned Client becomes clean at the same baseline and branch. Before/after
descriptor bytes, every original journal/backup and the failure logs remain
private and intact; private configuration and ledger records are unchanged.
No journal is promoted to a success certificate and no source approval changes.

Named session reply `cb3754cf-70c0-44ab-863a-03a47a1d1501`, at
16:28:27.666 UTC, requests a normal same-item installation/guard retry from that
verified clean baseline. It preserves the fresh approved round, receipt and
historical drafts. This continuation supplies no Unity startup, app-settings,
credential, game-merge or production grant. A new controller installation record
and actual complete guard results remain required.

The normal resumed worker subsequently records fresh installation
`07e4aa8ee7084d7db7473e95d245498a`, immutable SHA-256
`4352aa1aef8c0e49b1add17917ddbd2b38117a65c19df2ad18f25caed2d17e7e`.
The read-only observer verifies that checksum and its binding to the same fresh
export, source head, baseline and branch. It certifies only Activities: 31 files,
including 16 metadata files. The failed journal remains separate and intact;
normal installation, rather than manual promotion of its journal, supplies the
actual success record. Exact committed scope and complete actual guards are
still required at this checkpoint.

The worker commits Client candidate
`218ef7a805b072b3b87cb1b0b4f207e2a1901b12`. Controller scope verification
accepts exactly two changed files: the Activities descriptor and that package's
literal dependency-guard entry. Read-only checks revalidate all installed bytes
and the global metadata GUID inventory against the immutable installation.

The first actual guard command fails during public NuGet restore with `NU1301`;
no test or TRX executes in that attempt. A separate read-only public feed request
returns HTTP 200, identifying a selected-process dependency-access gap rather
than an executed application guard failure. The worker verifies 11 existing
cache archives against package versions, SHA-512 and ZIP integrity, and selects
that existing cache only for the controller subprocess. Independent read-only
checks revalidate all 11 archives, totaling 23,422,747 bytes. No dependency,
credential or persistent NuGet/app setting is installed or changed.

The subsequent normal `verify-ui-guards` records both actual repository methods
as Passed: `FguiDependencyGuardTests.No_unsanctioned_published_dependency_edges`
and `FguiOrphanAtlasGuardTests.Package_dirs_hold_exactly_the_files_their_descriptors_declare`.
The native run exits 0 in 12.041 seconds, with suspended-before-startup Job
assignment and an empty Job at completion. Complete retained TRX has exactly
two executed/passed results and zero failures, errors, skips or Inconclusive
results; SHA-256 is
`701856677b54cb880b3cdf56a3711c46ab8febfd8e77ec1a63bc3c58781bf7c3`.
Controller guard proof SHA-256
`79eb31c8fecdda512efbf4095b5e3e60c1e0a93f2aaab76208a5111ad5e9688d`
binds that exact candidate, source head, installation and fresh export receipt.
The actual TRX bytes/results and immutable proof checksum are independently
revalidated. Earlier restore and installation failures remain private measured
evidence; their results are not promoted into this successful run.

At 16:57:25 UTC, a read-only snapshot finds the same item normally parked at
`awaiting_resource/unity_resource`, with no worker PID and an empty native Job.
Interactive reservation `53069eda-dd9f-49cd-aa8d-bc140882c26d` is queued for exact
candidate `218ef7a805b072b3b87cb1b0b4f207e2a1901b12`, with no resource assigned.
No Unity loading proof exists. The current development instance still has zero
configured slots, and the app-preference decision below remains pending.

### Native Unity preparation findings, without starting an Editor

Read-only host checks find Unity 2022.3.62f3 installed, matching the Client project,
and no running Unity Editor. Default port 8080 belongs to an unrelated Java
process; it is neither contacted as MCP nor stopped. Existing selected Unity MCP
settings already use local HTTP port 9090 with auto-start enabled, and that port
has no listener. Development configuration still has zero Unity slots.

The Client's pinned MCP package is commit
`30d22075093d1d35dfb0091c1c7550e9ad948577`. Five relevant downloaded source blobs
are verified against that exact Git tree. Its
[startup registration helper](https://github.com/CoplayDev/unity-mcp/blob/30d22075093d1d35dfb0091c1c7550e9ad948577/MCPForUnity/Editor/Services/StartupConfigRewrite.cs)
can rewrite installed client configurations when auto-registration is enabled;
the current account has neither an explicit disable nor a configuration lock.
A private plan proposes an owned development Client slot on existing port 9090
and disabling `MCPForUnity.AutoRegisterEnabled` first. That preference applies
to the Windows account's Editors, so the operator's earlier no-app-settings-change
boundary requires an explicit decision. The plan is not executed: no preference,
slot configuration, Editor, MCP connection, account or credential is changed.

Remaining release prerequisites are the app-preference decision and an owned
native Unity/MCP slot, reserved exact-commit loading and human runtime UI QA; scoped real Feishu
reads and broader Code/closing/write-back journeys; separately authorized
production-host tool/runtime/permissions and dependency restore/cache readiness,
release/recovery checks and deployment.
These are development TestBot results. Item 2 and whole-card acceptance remain
pending, and production readiness is not certified.

## As executed (2026-10-07 UTC / 2026-10-08 Windows local; Unity preparation)

The operator authorizes completing the remaining development checks, including
the pending account-wide Unity MCP auto-registration decision. The selected
`MCPForUnity.AutoRegisterEnabled` preference is explicitly disabled before
starting an Editor. Other selected transport preferences already use local HTTP
port 9090; they are not changed. This prevents the pinned plugin's interactive
startup helper from rewriting installed client configurations. The previous
preference state and development configuration are retained privately.

The settled development TestBot alone is restarted after verifying its executable,
creation identity, owner, listener and empty worker Jobs. A separate prepared
runtime selector keeps frozen source `a7c335bf64ad5c8954142622b79f26baea333f62`,
signed native Codex 0.160.0 and `gpt-6.1-sol` / `xhigh`. The only host-config change
is one owned development Client Unity slot with the installed matching
2022.3.62f3 Editor and existing local MCP port. An initial private restart helper
fails at a Windows file-replacement API parameter before applying configuration;
the original config and backup remain intact. A fenced continuation uses native
literal-path file replacement and verifies the new config and process ancestry.
This helper failure is not a Client or FarmBot application regression.

Normal slot preparation hydrates the Client and assigns the existing exact-commit
reservation for `218ef7a805b072b3b87cb1b0b4f207e2a1901b12`. Unity opens the owned
project and starts its first Library import. Compilation reports two distinct
`CS0234` errors in `NetworkProtobufExporterTests`: `Farm.Tools.NetworkProtobuf`
is outside Unity's Assets compilation, and the test assembly lacks a direct
reference to `Farm.GeneratedArtifacts.Editor`. The metadata implementation is
present in that separate Editor assembly; its source is not missing. Both
affected test inputs are byte-identical at pinned baseline
`e9988b2d64a5fa5765496d37ec777492df66a278` and the UI candidate. They are not
introduced by the two-file UI change.

MCP does not become usable within the controller's existing startup deadline.
The normal controller fences resource ownership, preserves recovery diagnostics
and holds the slot. The job remains at `awaiting_resource/waiting_for_recovery`
with no worker PID; every prior worker Job remains empty. Automatic setup/editor
repair retains its persisted budgets. No timer releases the hold, no successful
loading proof is manufactured, and the pinned Client target is not changed.
Private Editor logs and actual Unity compiler response inputs are retained.

The three normal Editor repair attempts subsequently exhaust with the same MCP
startup timeout. The controller marks this item
`failed/verification-infrastructure-failed`; terminal cleanup completes without
error and preserves committed work and recovery records. No worker remains.
After verifying exhausted repairs, no active reservation, exact project path,
installed executable hash, controller ancestry, process creation and account
ownership, operator maintenance closes only the owned development Editor.
Its final log is retained, the selected MCP port is free, and the slot remains
held. No ledger write clears the hold or resets repair budgets. A failed trial is
not presented as successful UI delivery.

An independent Client development checkout starts from the verified current main,
also `e9988b2d64a5fa5765496d37ec777492df66a278`, on
`codex/unity-network-exporter-test-compile`.
[Client #1427](https://github.com/Kuaiwa-Network/Farm-Client/pull/1427) is an open
draft at `3cf42c00dd3c97037cdf94c440c4786d0a6ba2dc`. It adds the missing direct
assembly reference and a test-only import-free adapter for Unity. The standalone
gate continues exercising the actual CLI adapter. The metadata test executes on
both routes, and a new shared regression verifies that preferences and dialogs
remain unavailable. No test is removed or skipped and exporter runtime behavior
is unchanged.

Native Windows validation replays the actual Unity 2022.3.62f3 compiler response
file: the unchanged baseline reproduces both `CS0234` errors in 0.517 seconds;
the corrected test assembly compiles with zero errors in 0.577 seconds. This is
actual compiler validation, not an executed Unity EditMode suite or a UI loading
certificate. Focused standalone network-exporter, generated-artifact and headless
argument tests pass 162 executed tests in 12.735 seconds, with zero failures or
errors. Five existing POSIX process tests are `NotExecuted` on Windows:
`TimeoutBoundsPartialOutputOfChattyProcess`,
`RunDrainsBothFullPipesAndBoundsOutput`,
`RunPropagatesExplicitEnvironmentOverride`,
`TimeoutKillsEntireUnixProcessTree` and `RunPreservesLinuxExecutablePathToken`.
All compiler/test processes are suspended before native Job assignment and all
Jobs are empty at completion. The new adapter-boundary test passes; retained TRX
SHA-256 is `f38416018200e1ce87d5138c939f3d6f12a704525211712be1cb9e804e338646`.
The draft's exact head also has successful hosted standalone unit tests,
generated-artifact platform tests on Windows and macOS, and PR-triage script
tests. Those checks do not run the live Unity loading gate.

The independent fix does not alter the current UI candidate, certified export,
installation, guards, immutable Client pin or historical drafts. Game review and
merge remain human decisions. Even a later main merge does not silently repin this
item: subsequent UI verification must start from an explicitly selected corrected
baseline with the required fresh approvals. Actual reserved Unity/MCP loading,
human visual/runtime QA and final two-draft delivery remain pending. Scoped
Feishu integration and broader Code/closing/write-back journeys, plus separately
authorized production-host release/recovery checks and deployment, remain release
prerequisites. These are isolated development TestBot results on this Windows PC.

## As executed (2026-10-08 Windows local; corrected Unity baseline)

The operator merges [Client #1427](https://github.com/Kuaiwa-Network/Farm-Client/pull/1427).
Read-only GitHub checks confirm merge
`07bba0faf92faa986ba544373fe9ad31d3a85a64` and successful standalone unit and
Windows/macOS generated-artifact platform checks. This supplies the independent
exporter test compilation correction described above; it does not modify or
repin any historical UI trial.

Before development-slot maintenance, the controller's executable, creation
identity, account ownership, selected listener and development configuration
are verified. All historical jobs are terminal, cleanup is complete, worker
Jobs are empty, and no active reservation or pending repair remains. There is
no running Editor or selected MCP listener. Normal operator `recover-slot`
clears the exhausted slot hold through the supported controller command.
Historical repair records and immutable Client targets remain unchanged.

The owned development slot is fetched and checked out at the verified corrected
main, `07bba0faf92faa986ba544373fe9ad31d3a85a64`, after clone/config and worktree
identity checks. It is tracked-clean and fully hydrated, with no remaining LFS
pointers. The installed matching Unity 2022.3.62f3 Editor opens this project.
Its first import exceeds the existing 120-second MCP startup deadline; that
timeout and private log are preserved. No deadline, containment or successful
job result is changed to conceal the cold-import limit. The already started
owned Editor continues importing; a second Editor is not launched.

After import, the existing local MCP endpoint becomes available. A separate
read-only readiness check verifies the same owned Editor and project, waits for
quiescence, finds zero Console errors and runs the actual C# identity probe.
The aggregate result is `match`: exact corrected commit, stable clean source,
`StandaloneWindows64`, play mode off and loaded `HotUpdate`, `AOTScripts`,
`Nova.Runtime` and `MCPForUnity.Editor` assemblies. This check takes 6.378 seconds.
It is measured native Windows host readiness, not a reserved UI package-loading
certificate or runtime visual acceptance. All historical job targets are
independently verified unchanged. Only the owned development slot is prepared;
production resources and unrelated listeners remain untouched.

Named operator scope comment `ae7072e4-0842-417a-9154-6f4a7c4ea9ce`, at
23:12:47.887 UTC on 2026-10-07, requests a fresh independent FARM-1396 item-1
trial. Historical [source #144](https://github.com/Kuaiwa-Network/farmgui/pull/144)
at `9bbfa75acfd9a860947cf5c5d1ca840b53aafb72` supplies the exact approved XML
design reference. Existing drafts, failed jobs, receipts and recovery evidence
are preserved. The new trial must publish its own source draft and approximate
preview, then obtain explicit approval of that fresh round and an export request.
An older approval or receipt cannot certify the new trial.

The normal development TestBot session `d5178014-5aaa-4eef-8f21-975a0268c464`
creates independent item `c2765bc0-d61c-4883-9797-29f673e09620`. Read-only
observation confirms the running source worker selects signed native Codex
0.160.0 with `gpt-6.1-sol` / `xhigh`; the worker and all observed descendants
belong to its native Windows Job. No historical item is resumed or repinned.

Fresh visual approval, certified export/install/guards, reserved exact-commit
Unity loading, quiescent release and final source/Client draft delivery remain
pending at this checkpoint. Human runtime UI QA, item 2 and whole-card acceptance
also remain pending. Scoped real Feishu reads, broader Code/closing/write-back
journeys and separately authorized production-host dependency, runtime,
permissions, release/recovery checks and deployment remain release prerequisites.
These measurements come from the isolated development TestBot on this Windows
PC; they do not certify a production installation.

### Fresh corrected-baseline source draft and visual review

The new worker first parks on its normal foreign-work collaboration check after
verifying merged Client #1426/#1427. The existing operator authorization is
relayed into this fresh session as comment
`66098957-018e-4be7-a16d-45c25f674c91`, at 23:24:40.573 UTC on 2026-10-07.
This is an explicit continuation on the independent branch using #144's design
reference, not visual approval or an export request. The webhook resumes the
same new item normally; the previous attempt's native Job is empty.

[Source #145](https://github.com/Kuaiwa-Network/farmgui/pull/145) is an open
draft at `5e47cb0830243954214355d9d526e0b19c1cdc30`, based on verified farmgui
main `1193f21008dea66823c721a0818bf6690c33376b`. Independent read-only checks
compare committed Git blobs and confirm both changed XML files exactly match
historical #144. The only other changed file is the item-1 UI document. Package
IDs, images, registration paths and publisher configuration remain unchanged.
The full owned clean source snapshot, including hydrated selected dependencies,
matches controller source digest
`f1f1aca92a3ad8d063a1646b1bbacd7510e27f25df7923824f88f84cfb01139e`.

The worker passes package cycle/export-reference, uniqueness, registration and
targeted text checks, and validates the committed hashes/sizes of 1,503
materialized resources. Initial ad hoc inventory parsing failures are retained;
the successful native inventory and independent controller snapshot supply the
actual source evidence. A reported image mismatch is separately checked against
its raw committed LFS pointer: the materialized PNG has the expected 3,159,257
bytes and matching SHA-256. No credential, Git configuration, source identity or
containment check is weakened.

Fresh controller review `49d905d86ab64e57bab91c7ff4a9cb7d` binds round 1 to
that exact head and digest. It records changed map `Activities: fvyctcfd`;
the unchanged selected dependency closure also contains Common, ItemIcons,
CommonFx, PlayerCustomize and UILangTex. Actual confirmed visual notice
`d393234e-76f1-4866-8f86-f356b0d05046` names the review's uploaded comparison
and full source head. The retained 2208-by-2060 comparison PNG has SHA-256
`0032a1cbb0662085a86a1a96e62c5c747556ccc556eb7b1799121500f89e4fdc`.
The supplemental 1920-by-1360 three-column label comparison has SHA-256
`97fbf95f7859a8961985da7e52dd00c5291277a1a16ccedce9debd4fd90c2c41`.
Both actual PNGs are inspected. The first PNG PUT fails; a subsequent normal
controlled upload succeeds, and only the actual successful assets are bound.

The fresh approximate preview shows both target labels completely. Fill ink
extends beyond the original art by 5/6/6/4 pixels (left/right/top/bottom) for
the plaque and 13/10/6/1 pixels for the purchase label. Fill/stroke color distance
for the purchase label is zero. Full white-stroke extent measurement still
fails because it overlaps basket/flower art and touches the scan boundary; that
gap remains explicit. The original plaque ink already fits inside the old
source box, so the reported disappearing second line's runtime cause remains
unverified. Neither approximate text check proves native clipping behavior.

The full-view renderer returns gap/exit 2. Unsupported or approximate groups,
gray/fill effects, rotation/pivot, sibling relations, gearLook, automatic text,
instance properties and animation remain documented. Static crops, rewards,
prices, search/back text and Hub footer differ from the art; the project default
font also differs from the original drawing. These are retained preview limits,
not passing FairyGUI Editor/Unity rendering or item-2 acceptance.

The same independent item is now normally parked at
`awaiting_input/visual_review`, with `pause.kind="visual_approval"`, no worker
PID and all attempt Jobs empty. Independent checks revalidate the actual draft
head, exact reference XML, owned source digest, PNG bytes and confirmed notice.
Fresh named visual approval and an explicit export request remain required.
No new export receipt, Client target/install, guard result or reserved loading
proof exists. Historical evidence and drafts remain intact. After approval,
normal certified export, corrected-baseline Client handoff/install, actual
guards, reserved exact-commit loading and quiescent release must precede final
two-draft delivery. Human runtime UI QA, scoped Feishu reads, broader release
journeys and separately authorized production-host release/recovery/deployment
remain pending as described above; item 2 and whole-card acceptance remain open.

### Fresh approval, certified export and scoped Client installation

The operator explicitly approves this fresh item-1 round and requests export.
The relay into the current session is comment
`11d17ec8-16e2-4b72-93b8-7e5aff33f241`, at 00:17:08.154 UTC on 2026-10-08,
by the named human operator. The actual canonical session inbox message is 17.
Independent checks match its author, time and body, accounting for Linear's
ordinary automatic Markdown links only when comparing the relay. Authority
hashes use the actual canonical inbox body. The approval binds the exact source
head, review, source digest and comparison PNG recorded above, acknowledges the
approximate preview gaps, and requests the normal corrected-baseline Client
journey. It does not supply runtime visual acceptance or item-2 authorization.

The normal native export produces receipt
`c237c3e5152043ae9e56f9bfa5ec27ac`, with SHA-256
`d68b5e4fda41094c0f6c01dea4c4367da81bac6c670a7cafb2bfbb37903587e4`.
All six selected package identities and all 50 staged artifacts are independently
rehash-verified against the controller snapshot and approval/source bindings.
The actual publisher exits zero in 6.130 seconds, is assigned to its native Job
before startup and export, and leaves that Job empty. End-to-end controller
verification also repeats the owned full-source, remote and authority checks;
6.130 seconds measures the publisher process, not the whole command.

The normal handoff persists corrected Client baseline
`07bba0faf92faa986ba544373fe9ad31d3a85a64` and the new independent issue branch.
A preparation check detects an unhydrated Activities atlas after an initial LFS
command; native hydration then succeeds. That failure remains private evidence,
not a passing guard or an application regression. The certified installer
accepts only Activities: 31 files including 16 metadata files, retaining existing
GUIDs. Installation `01860ca3115c4ebc82f21679cdf2442f` has SHA-256
`141974964333bccf09d1b9d01df17a18638191496d29ec98731fded29d03fdca`.
Historical targets, receipts and drafts remain unchanged. Guards, reserved Unity
loading and final delivery are recorded separately below only after their actual
results and normal resource release are independently verified.

### Fresh native guards and exact-commit loading

The clean scoped Client candidate is
`20a86e15744f7ac1ddf61780ee7adcc529d2f416`. Its diff contains only the Activities
descriptor and the justified Activities dependency-guard entry. Independent
checks revalidate the installed artifact bytes and global GUID inventory against
the immutable installation certificate. Both actual hydrated native guards pass:
`No_unsanctioned_published_dependency_edges` and
`Package_dirs_hold_exactly_the_files_their_descriptors_declare`. There are two
executed passing tests, zero failures and zero skips, in 7.478 seconds.

Retained TRX SHA-256 is
`3dc62341c5aea09092467e604608bc98db7ebe08fe693116610375c0d974389d`;
the complete two-test identities, counters and outcomes are independently checked.
Guard certificate SHA-256 is
`90cd09d6d9cf5da6ee55ce5d8cbb5202285f87a202db4315076cfc4464e15f8b`.
The unchanged native guard runner suspends before Job assignment and verifies
zero exit and an empty owned Job before recording its bounded process result.
The observer initially expects a Job name that this record format does not
retain; it is corrected to the actual process schema while retaining checksum,
TRX, exit and pre-startup/empty-Job result checks. This is a private diagnostic
checker correction, not an application change or relaxed guard.

The first slot grant returns an unknown identity observation, so the controller
refuses handoff, releases that reservation and holds the slot with recovery
evidence. Normal setup recovery `590da248-0e0c-43dd-900e-da1181247b3a` succeeds
on its first persisted repair attempt. A subsequent identity observation matches
and reservation `c2ee67f1-cdfd-483e-8156-d39174820053` is granted at the same
exact candidate. No operator reset, target change, relaxed deadline or manual
success record replaces the failed observation.

The normal `verify-ui-loading` route then produces actual Unity certificate
SHA-256 `f93fedff26c8c19be6e1225eb02df77b0b9c33f65a5cd455375f0f834645adaf`.
It binds the same receipt, installation, source head and exact committed Client
candidate. Before/after Editor and source identity checks match, all descriptors
match the selected slot's actual bytes, and the fixed C# probe verifies every
selected package and dependency:

| Package | ID | Package items | Disk assets |
|---|---|---:|---:|
| Activities | `fvyctcfd` | 172 | 14 |
| Common | `cmcommon` | 504 | 23 |
| CommonFx | `xjwxiayo` | 5 | 1 |
| ItemIcons | `ii0items` | 173 | 4 |
| PlayerCustomize | `i34morsw` | 134 | 1 |
| UILangTex | `82y1zwra` | 16 | 1 |

These are 1,004 actual package items and 44 disk assets. The probe removes only
registrations it created and records `cleanup_complete=true`; the immutable
controller checksum and complete six-package results are independently checked.
The exact-commit reservation is normally released after quiescence. This is
actual native Unity package loading, not just a descriptor parse, dotnet pass or
host-readiness probe. It does not establish target-panel appearance, the original
second-line clipping cause, item 2 or human runtime UI acceptance.

### Fresh terminal delivery and remaining release gates

Independent terminal verification at 2026-10-08 01:19:25 UTC confirms normal `delivered`
state with no worker PID and both current open drafts at their certified heads:
[farmgui #145](https://github.com/Kuaiwa-Network/farmgui/pull/145) at
`5e47cb0830243954214355d9d526e0b19c1cdc30`, and
[Client #1428](https://github.com/Kuaiwa-Network/Farm-Client/pull/1428) at
`20a86e15744f7ac1ddf61780ee7adcc529d2f416`. The controller delivery proof binds
the fresh receipt, canonical approval/export request and matching guard/Unity
certificates. Confirmed actual delivery comment `84be7e04-c060-45b5-9a5b-bb83d54a9e08` names
both drafts; its outbox identity, marker and actual remote body are independently
matched, accounting for ordinary automatic links only when comparing text.
The real issue is independently verified open. Game review and merges remain
human decisions.

Normal terminal cleanup completes with no error. Both committed heads are
independently verified in retained controller recovery refs and snapshots before
the owned job worktrees are removed. All five attempt Jobs are empty; there is no
active/queued reservation or pending repair. The shared development Editor remains
normally idle/open, parked back at corrected main
`07bba0faf92faa986ba544373fe9ad31d3a85a64`. The failed first grant and recovered
setup record remain preserved. Historical failed trials, immutable targets,
receipts, installation journals, recovery records and drafts remain intact.
Frozen development runtime and selected development configuration hashes are
unchanged throughout this fresh trial.

This completes the scoped native Windows UI export-to-delivery trial. Human
runtime visual QA, item 2 and whole-card acceptance are not certified; the real
issue remains open. The remaining release prerequisites at the top are still
pending: real scoped Feishu reads, later Code/closing acceptance and running-worker
withdrawal, plus intended production-host preparation/recovery and separately
authorized enablement/deployment. In particular, this warm development slot does
not resolve or conceal the earlier first-import 120-second deadline gap. Neither
parked FARM-1346 nor FARM-1425 is resumed, and no production deployment, service,
configuration, credentials or account is changed.

### Operator-supplied runtime visual check and Client merge (2026-10-08 Windows local)

The operator supplies a 351-by-773 runtime screenshot, then explicitly confirms
in this chat that it comes from the updated Client containing #1428. The original
545,240-byte PNG is retained privately with SHA-256
`24d7cd0c0666a63d5f0b31f6b4600911c013bf5e36f1e90bef3f6007fd409e72`.
No screenshot, private path or device/account metadata is published in this record.

Inspection against the original art confirms the wooden plaque displays both
lines, `本页` and `礼包`, completely. The purchase label displays all four
characters of `整页购买`, with no obvious clipping of the text or white outline
at the supplied resolution. This passes the scoped item-1 captured visual check.
The screenshot's build attribution is the operator's explicit confirmation; the
image itself does not independently identify a commit or execution platform.
The earlier full-stroke measurement and approximate-renderer gaps remain
historical evidence and are not rewritten as automated passes. This check does
not claim pixel-perfect whole-panel matching, all resolutions, item 2 or
whole-card acceptance.

Read-only GitHub checks confirm
[Client #1428](https://github.com/Kuaiwa-Network/Farm-Client/pull/1428) merged as
`6baa6999d15ce5ffc86139668b67542124437160`; current Client main is the same merge
at this checkpoint. The Activities descriptor and dependency-guard Git blob
identities match tested candidate `20a86e15744f7ac1ddf61780ee7adcc529d2f416`
exactly at both the merge and current main. The prior certified native export,
guards, exact-commit loading and terminal recovery results remain bound to their
original candidate, not silently repinned to this merge.

[Source #145](https://github.com/Kuaiwa-Network/farmgui/pull/145) remains an open
draft at `5e47cb0830243954214355d9d526e0b19c1cdc30`. Its human review/merge is
the immediate remaining provenance gate. Scoped real Feishu document access,
live Code/closing and running-worker withdrawal acceptance, and intended
production-host startup/dependency/recovery checks plus separate deployment
authorization remain pending as listed above. No game PR is merged by this
verification, no delivered job or parked card is resumed, and no production
configuration, runtime, credentials, account or service is changed.

### Both human game merges and scoped native Feishu reads (2026-10-08 Windows local)

Read-only verification at 2026-10-08 02:27:22 UTC confirms
[farmgui #145](https://github.com/Kuaiwa-Network/farmgui/pull/145) merged as
`6fa24eb22052010b6243900a04dca767c3859065`; source main equals that merge.
[Client #1428](https://github.com/Kuaiwa-Network/Farm-Client/pull/1428) remains
merged as `6baa6999d15ce5ffc86139668b67542124437160`, also current Client main.
Both PR head identities equal their originally certified candidates.

The three changed source files and two changed Client files have identical Git
blob identities at their tested candidate, merge and current main. Client's
entire candidate/merge/main trees are equal. Source trees differ: the complete
candidate-to-merge comparison contains 21 changed paths, including rename
origins, all confined to the unrelated, unselected `assets/Cards/` package.
The selected Activities package and all five exported dependency packages, plus
nonpackage inputs, are unchanged. This is scoped input equivalence, not whole
source-tree equality or a new export certificate for main. The original source
digest, approval, export receipt, installation, guards, Unity loading and delivery
proof remain bound to their tested candidates. No delivered job is resumed and
no source/export evidence is silently repinned.

Together with the operator-confirmed captured visual check above, this completes
the scoped FARM-1396 item-1 human merge/visual follow-up. The real card is not
closed; item 2 and whole-card acceptance remain pending. No Code job's UI-ready
state is advanced: its explicit human reply and refreshed component/export
checks remain required when that independently scoped job reaches stage E.

Under the earlier autonomous read-only reference-selection grant, existing UI
cards supply linked wiki references. The selected native Codex **0.160.0**
`command/exec` route uses fresh empty Codex homes and the existing current-account
`farmbot` profile with lark-cli **1.0.82**, strict bot mode, explicit `--as bot`,
no Windows HOME override and sanitized inherited credential/config selectors.
The process is created suspended, assigned to its owned non-breakaway Job before
startup and released only after membership is verified. Effective workspace-write
roots, unelevated Windows policy and network permission are checked before each
bounded read. No model turn, new FarmBot service or credential setup occurs.

| Existing UI reference | Read | Command seconds | Entire run seconds | UTF-8 response bytes |
|---|---|---:|---:|---:|
| FARM-885 | Wiki resolution | 1.969 | 2.388 | 665 |
| FARM-846 | Wiki resolution | 3.554 | 3.951 | 666 |
| FARM-846 | Docx Markdown fetch | 2.326 | 2.752 | 229 |
| FARM-1102 | Wiki resolution | 2.069 | 2.488 | 663 |
| FARM-1102 | Docx Markdown fetch | 2.511 | 2.941 | 189 |

All five actual commands exit 0; their complete API envelopes independently
report `ok=true` and `identity=bot`. Total measured run duration is **14.520
seconds**, excluding selection and inspection. Each read has verified child Job
membership, empty-Job cleanup, dead child and settled RPC reader. Profile-config
metadata and registered runtime receipt remain unchanged, and no Codex auth file
is seeded. UTF-8 responses, RPC logs and their SHA-256 identities remain private.

The first linked wiki resolves to `sheet`; no unsupported sheet command is run.
The two docx fetches return only headings: respectively 51 UTF-8 Markdown bytes /
two nonempty lines / one heading, and 14 bytes / one line / one heading. Their
content hashes are retained privately. They demonstrate scoped bot document
access, not substantive task requirements, complete visual designs or a real
document-driven UI worker journey. The initial version-field assumption and
docx-only selection fixture stop locally before their fetch; these fixture
failures remain preserved. Correcting the private fixture changes no application
code, credential source, permission or containment check.

Remaining release work is the concrete scope/content selection and live journeys
listed above, followed by intended production-host startup/dependency/recovery
acceptance and separate enablement/deployment authorization. This is still the
development Windows PC. No issue is mutated, no parked card or historical draft
is resumed, and no production configuration, state, runtime, service, credentials,
account or app settings are changed.

### Substantive design read and proposed next Code scope (2026-10-08 Windows local)

Read-only current Code-card discovery finds FARM-1419 as the open card assigned
to the operator outside the two parked cards. It remains Backlog and undelegated.
Its earlier TestBot grant explicitly limited work to stage A; that read-only
analysis completed and the delegation was withdrawn. A generic development
continuation is not recorded as permission to widen that named live grant.
FARM-1419 is the proposed next card, not a newly started or approved Code job.

Using its existing linked reference under the autonomous read-only selection
grant, native Codex 0.160.0 and lark-cli 1.0.82 resolve the wiki and fetch the
full planning document as the bot. The independently checked API envelopes
both report `ok=true` and `identity=bot`. The fetch returns **43,431 UTF-8 JSON
bytes**, document revision **236**, and **42,552 UTF-8 Markdown bytes / 30
headings**, including substantive planning and UI sections. This advances the
earlier heading-only access observation; it does not establish a new UI worker
journey, current designer-source completeness or acceptance of all historical
rules over later named Contract decisions.

| Read | Command seconds | Entire run seconds | Response bytes |
|---|---:|---:|---:|
| Wiki resolution | 1.964 | 2.373 | 680 |
| Full docx Markdown fetch | 2.111 | 2.531 | 43,431 |

Both commands exit 0, with **4.904 seconds** total measured run duration.
The same suspended startup, owned non-breakaway Job, fresh empty Codex home,
effective native policy and exact-root checks apply. Child membership,
empty-Job cleanup, dead child, settled RPC reader and unchanged profile metadata
and registered runtime receipt are independently verified. Responses, complete
document content, linked URLs, source hashes and UTF-8 logs remain private.
No model turn, service start, credential setup or live issue mutation occurs.

Pinned read-only source inspection identifies the existing gifting Contract
and checks the associated follow-up
[Farm-Contract #328](https://github.com/Kuaiwa-Network/Farm-Contract/pull/328).
It remains an open draft at `220e618d3cd736b6f8cc747576bca7e5a0ca413a`, based on
current Contract main `29e6cfe1430a7c7313febc642f95db4ea6c3fb18`, and GitHub reports
it mergeable and clean. All **28** displayed hosted checks pass. The underlying
[workflow run](https://github.com/Kuaiwa-Network/Farm-Contract/actions/runs/37422640372)
is independently matched to that exact head and successful completion; the gates
cover native Windows, macOS and Linux. The reviewed diff corrects the selected
blessing/mail-template interpretation, preserves invalid-config refusal and
frozen historical text, and changes no wire field, message or number. This is a
Contract change requiring its human review/merge, not a FarmBot documentation PR.

A bounded open-PR scan of the five configured repositories returns 8, 3, 7, 8
and 4 PRs respectively, without hitting its 100-result cap. The related open
follow-up identified by that scan is #328. The linked persistence dependency
[hive-jelly #12](https://github.com/Kuaiwa-Network/hive-jelly/pull/12) is already
merged as `424c70d860721cae54c7f49a271de0603cdd7b4e`; that state check does not
certify its consumer pin or current backend acceptance. Preliminary discovery
does not replace a worker's required fresh foreign-work/publication checks.

The concrete proposed continuation is FARM-1419's remaining Code workflow, after
the corrected Contract main is verified and the operator grants the later-stage
scope. Ordinary human design/configuration, UI-ready and game-merge gates remain;
workers draft only, never merge game PRs or publish configuration. No other issue
is started and no designer value, player data or production integration is changed.
FARM-1346 and FARM-1425 remain parked. The existing development runtime/profile
is not changed by this preflight; production-host certification and separately
authorized enablement/deployment remain pending.

### Approved native Windows Code intake (2026-10-08 Windows local)

The operator explicitly approves FARM-1419's remaining Code workflow in this chat,
expanding this card's historical stage-A-only trial. The authenticated operator's
Linear comment `5c25aa58-ef88-4cc7-88f8-017b0d8edfee` records that scope at
2026-10-08 03:07:00 UTC. This is a live acceptance test on the development Windows
PC: real issue sessions, changes and draft PRs are possible. Ordinary human
design/configuration, UI-ready and game-merge gates remain. Code does not author
or export farmgui; no other issue, designer-value mutation, configuration
publication, player-data operation or production change is authorized.

Read-only verification confirms
[Farm-Contract #328](https://github.com/Kuaiwa-Network/Farm-Contract/pull/328)
merged at 2026-10-08 02:57:55 UTC as
`a9b444337721125b75067d635a4b0f816ee60cc3`. Its tested head remains
`220e618d3cd736b6f8cc747576bca7e5a0ca413a`, with all 28 hosted checks successful.
The complete tested-head, merge and current-main trees are independently equal
at this checkpoint. Historical certificates and candidate pins are preserved.

Before the settled development restart, all 11 existing jobs are terminal:
three failed, six cancelled, one blocked and one delivered. All 25 recorded
native worker Jobs are empty; worker PIDs, pending webhook events, pending
cleanup, open reservations and heartbeat workers are absent. Historical failures
remain recorded. No production ledger is inspected and no Mac state is copied.

The existing frozen runtime `a7c335bf64ad5c8954142622b79f26baea333f62` remains
unchanged. Only development configuration gains `feature` alongside `chat`,
`fix` and `fgui`, and drops its obsolete feature-specific GPT-6 override to
inherit the already approved `latest-sol` / `xhigh` policy. The old private
configuration, wrappers, process receipt and logs are retained; new wrappers
pin the same source, signed native Codex 0.160.0 and new configuration hash.
Only the verified owned development controller is stopped. Its replacement's
interpreter, process creation identity, account ownership, bootstrap ancestry
and port are checked. The shared development Unity Editor remains outside worker
containment; no account, credential or app-setting change occurs.

After restart, read-only doctor reports no missing feature or UI tools, verifies
the native publisher and resolves the next Code model to `gpt-6.1-sol` with
`xhigh`. Its remaining findings are the three historical failed jobs and one
historical blocked job. HTTP health is 200, the heartbeat is fresh and serving,
and all six controller loops have zero consecutive errors; readiness is not
inferred from health alone.

Only [FARM-1419](https://linear.app/kuaiwagames/issue/FARM-1419) is delegated to
TestBot. API delegation opens no new session in the bounded before/after read,
so one durably recorded, real issue session is created through Linear's API:
`e542954f-2f67-445f-abd0-da1b57cf9560`. The signed webhook creates native
Windows work item `b50b3c8e-7488-4e52-b6a2-6ef04809d2b6` with skill `feature`.
Its worker claims the item and runs at intake, with no Client target yet.
The actual isolated worker configuration confirms `gpt-6.1-sol` / `xhigh`;
the root and every observed descendant belong to its owned native Job. This
establishes real native intake and launch, not completed downstream acceptance.
The actual feature worker then saves successful wiki and full-docx responses,
both independently checked as `ok=true` and `identity=bot`. The document is
revision 236 with 42,552 UTF-8 Markdown bytes and 30 headings; its content hash
equals the prior read-only probe. These are new worker-owned response files,
not replayed probe responses. Native Code document intake is therefore measured;
the separate UI-worker document journey and downstream Code gates remain pending.
The subsequent checkpoint enters `contract` with stages A through G pending;
no game PR has been published at that checkpoint. Normal human gates remain.
No Bash/MSYS/WSL/MXC route is selected. FARM-1346 and FARM-1425 remain parked;
production enablement and deployment remain separately authorized release work.

### Contract draft and external runtime interruption (2026-10-08 Windows local)

The native Code worker publishes
[Farm-Contract #329](https://github.com/Kuaiwa-Network/Farm-Contract/pull/329)
at `8d7d26d280be8877b7987c7bf28414f334324f4b`, based on merged #328
`a9b444337721125b75067d635a4b0f816ee60cc3`. Only the existing gifting change's
`tasks.md` changes: current ledger handoffs, MailInfo Client export declarations,
the human UI dependencies and the complete 29-Scenario consumer acceptance map.
No gameplay ruling, wire field, message, designer data or economic value changes.
The candidate is clean; all twelve final native Windows gates are independently
matched to this committed HEAD and exit 0. All 28 hosted checks also succeed
at this unchanged PR head. It remains an open draft requiring human review/merge;
downstream implementation and game acceptance are pending.

The initial baseline report retains an additional failed invocation of
`tools/check-msgid.py` (exit 2, missing file). README actually documents the
non-gate helper under the remaining-protocol-drafts change. A separate native
host focused run uses that documented entry point on the proto and proto-draft
directories: 79 proto files, exit 0, 0.057 seconds and unchanged inputs. The
failed worker invocation remains preserved. This corrects an invocation error,
not an application regression or missing standard gate; it neither adds a skip
nor replaces worker-context or downstream verification. The public draft body
is sanitized to remove a host-root line and distinguish these measured results;
the private original is retained and the draft head stays unchanged.

Across an external desktop-package change, the selected Codex 0.160.0 file
disappears. The recorded development controller, old TestBot proxy and shared
development Editor are absent, and the existing worker Job is empty while its
ledger row still says running. At the first read the heartbeat is 500.33 seconds
old and the development receiver has no listener. These observations establish
an interrupted run; they do not establish the exact process-termination cause.
The committed draft, original session, checkpoint, claim history and all earlier
evidence remain intact. All 26 recorded worker Jobs are independently empty;
the interrupted root is dead, all five owned clone checks pass, and no reservation
is open. Production configuration/state is not inspected or copied.

The installed replacement is signed Codex **0.162.0-alpha.2**, SHA-256
`3553cd6e7df5a093d8cb8301cd8088a57e0971aba71ddbe0e67f7f44a15cdf68`.
Its prerelease status is explicit. Separate contained checks precede development
recovery: the actual `gpt-6.1-sol` / `xhigh` model-only probe exits 0 with the exact
readiness response in 11.265 seconds, leaves its work directory unchanged and
settles its owned Job. A new command-only Feishu fetch checks suspended startup,
membership and effective native unelevated workspace/network policy; the full API
envelope independently reports `ok=true`, `identity=bot` and document revision
236. It returns 43,431 bytes in 1.795 command seconds / 2.174 whole-run seconds,
with empty-Job cleanup, dead child, settled reader, unchanged profile metadata
and unchanged registered-runtime receipt. No Codex auth is seeded for that fetch.
These limited compatibility results do not certify production use of this CLI
or a resumed downstream feature stage. The 0.160 measurements are not repinned.

New private wrappers pin the same frozen FarmBot source, unchanged development
configuration and verified replacement CLI. Read-only Job/ledger observation and
Git/documentation tooling no longer require the removed historical executable;
runtime launches retain their own version/hash checks. The development controller
is restarted with verified interpreter, owner, ancestry and listener. Health is
200, the serving heartbeat is fresh and all six loops have zero consecutive errors.
Recovery uses the existing controller mechanism after the original 45-minute
claim lease expires; no lease, token, checkpoint or ledger row is manually changed.
The interrupted row remains running at this checkpoint, so recovery completion
and a new actual worker attempt are not claimed.

The selected old development proxy is confirmed absent; no unrelated proxy is
changed. A replacement pinned native quick tunnel forwards only to the verified
development receiver and its external health response is 200. Its new webhook
URL is retained in a private local operator file. Linear app settings are not
changed: the operator must update only TestBot's development webhook endpoint
before incoming signed replies are considered restored. No replacement issue
session or synthetic webhook is created.

Remaining steps are that manual webhook update, normal claim-fenced recovery and
human Contract draft review/merge, followed by the pending Code stages, separately
selected document-driven UI trial and intended production-host release/recovery
acceptance. Desktop-dependent executable selection and development process
lifetime require explicit release planning; a healthy replacement proxy is not
an end-to-end webhook or production readiness certificate. FARM-1346/FARM-1425
remain parked, and production deployment, settings, credentials and accounts
remain unchanged.

### Claim-fenced Windows Code resumption (2026-10-08 Windows local)

The operator confirms in this chat that only TestBot development's webhook URL
has been updated to the replacement endpoint. The URL remains private. This is
an operator confirmation of the app-setting step, not a measured new signed
delivery: at the post-recovery observation, this session still has only its
original completed inbound event. A genuine subsequent session event must prove
the replacement route; no synthetic event or replacement session is created.

The original claim lease expires at 2026-10-08 04:14:22.539 UTC. Read-only audit
and process observation then confirm the controller's normal recovery requeues
the same work item because its worker is gone. A new native attempt claims it
at 04:15:53.132 UTC. Its predecessor Job is empty; the new root and all five
observed descendants belong to the new owned Job. The isolated worker settings
remain `gpt-6.1-sol` / `xhigh`. No lease, token, checkpoint or ledger row is
manually edited. The original session, Contract checkpoint and exact
[Farm-Contract #329](https://github.com/Kuaiwa-Network/Farm-Contract/pull/329)
head `8d7d26d280be8877b7987c7bf28414f334324f4b` are retained.

The development configuration and frozen runtime remain unchanged. The serving
heartbeat is fresh and all six controller loops report zero consecutive errors;
no cleanup error is present. This establishes normal claim-fenced recovery and
a newly claimed native worker at `contract`, not completion of stage A or the
remaining feature acceptance. The replacement CLI's prerelease qualification
and production-host limitations recorded above still apply. Contract #329
remains an open draft with human review/merge required by the operator's approved
scope. Common/configuration, server, the explicit UI-ready gate, Client and
closing/recovery verification remain pending. FARM-1346/FARM-1425 stay parked;
production deployment and production resource changes remain unauthorized.

### Human Contract merge and restored signed delivery (2026-10-08 Windows local)

The operator reports the merge of
[Farm-Contract #329](https://github.com/Kuaiwa-Network/Farm-Contract/pull/329).
Independent current GitHub reads verify its unchanged tested head
`8d7d26d280be8877b7987c7bf28414f334324f4b`, actual merge
`4476168c2c63a639f67d7533f319522157c09949` and merge time 04:20:20 UTC.
At this checkpoint the tested, merged and current-main trees are equal, and
all 28 hosted checks remain successful. Only the gifting `tasks.md` changed.
No game PR is merged by TestBot or this development agent.

The authenticated operator's reply `5027a292-2cc7-4a66-8ed7-618de03441fd`
transcribes that chat report in the existing TestBot thread. The development
receiver records a real `AgentSessionEvent` / `prompted` response as 200 accepted;
this path validates the webhook signature. Read-only ledger observation confirms
the event is processed, the report reaches the original work item's inbox with
the expected human author, and the worker consumes it. The existing session now
has its original completed event and this completed prompt. Thus the replacement
route has measured signed delivery, not only HTTP health or an operator's
endpoint-update confirmation. No synthetic event or replacement session is used.

The worker records the human merge, updates the Contract PR entry to merged and
marks `stages.A` done. The controller completes the repository handoff to
`common`: both Contract-attempt Jobs are empty, and a fresh Common-rooted native
worker launches. Its root and all five observed descendants belong to its owned
Job, with `gpt-6.1-sol` / `xhigh`. At this observation it has not yet claimed the
queued item; the subsequent read confirms it claims the original item in the
Common root. `stages.B` through `G` remain pending. This establishes a settled
repository handoff, fresh launch and claim, not Common generation or later acceptance.
The serving heartbeat is fresh and all six controller loops have zero consecutive
errors. Development configuration and the frozen FarmBot runtime are unchanged.

Ordinary named configuration input/publication, UI-ready, consumer verification
and game-merge gates still apply. The separately selected document-driven UI
journey and intended production-host release/recovery checks remain pending.
FARM-1346/FARM-1425 remain parked; production deployment, credentials, accounts,
player data and production resources are unchanged.

### Native Windows Common declarations and configuration gate (2026-10-08 Windows local)

The Common-rooted worker completes stage B and publishes the draft
[common #162](https://github.com/Kuaiwa-Network/common/pull/162) at
`936f50c8c3e20cd28c76445a7dc2789f1cb02f7c`, based on
`5c4329da7c54e0d39c091ab75ce26a00ae0c007c`. Independent diff review confirms
five changed files: MailInfo's existing export registration becomes `all`, its
generated inventory table row changes accordingly, and three required artifact
count sites update. The four existing fields are `id:uint32`, `title:string`,
`content:string` and `paramList:string[]`; no designer data row or global value
changes. The owned clone trust check passes and the committed worktree is clean.

Retained UTF-8 worker logs and JSON results establish these native Windows checks:

| Check | Measured result | Seconds |
|---|---|---:|
| Source-digest tests | Exit 0, with the four existing platform skips below | 13.950 |
| `gen-config.cmd inventory` | Exit 0; only the MailInfo table export row differs | 16.062 |
| Clean-commit double-profile generation | Exit 0; manifest commit equals the candidate, `dirty=false` | 10.545 |
| Generated artifact verification | Exit 0 | 3.560 |
| Production counts, repeatability and projection equality | Initial exit 1; focused same-head retry with a shorter private temporary parent exits 0 | 7.222 / 39.504 |

The clean artifact contains 600 manifest entries: server 188 and Client 412.
Independent physical counts match 46 server data/debug files, 48 server
schema/Go files, 102 Client data/debug files and 104 Client schema/C# files.
MailInfo's four Client fields are present. An earlier dirty-input generation
also succeeds but is retained separately, not used as the clean candidate proof.

All native source-digest platform skips are recorded by name:
`TestProductionSourceDigestMatchesExactPackerPipeline`,
`TestSourceDigestRejectsUnsafeTrees/newline_name`,
`TestSourceDigestRejectsUnsafeTrees/backslash_name` and
`TestSourceDigestRejectsUnsafeTrees/fifo`. The first covers the Linux shell
packer; the remaining cases use names or filesystem objects unsupported by the
Windows test path. No new skip is added. Native production-count execution has
no skip or empty-test warning. The failed attempt reports native Go startup's
"The directory name is invalid." The shorter-temporary-parent retry runs the
same committed test successfully; the exact underlying host/path limit is not
established. Both logs remain preserved. No assertion or containment check is
weakened to obtain the focused pass.

The macOS/Linux-only `check-config-artifact.sh` is not run on this Windows PC.
Current GitHub verification at the unchanged candidate confirms both existing
checks pass: `linux-acceptance` and `windows-language-regression`. The Linux
result supplies the applicable hosted acceptance evidence; it does not turn
the skipped local shell integration into native Windows execution. These are
development-PC results, not certification of the production installation or
its selected paths and tools.

The real `config-needed` comment `36a41a33-1713-4973-9e5c-0cf686d4df12`
is delivered to FARM-1419. It requests human naming review/merge and a named
configuration commit or branch containing the declarations and valid designer
data. The observed job is `awaiting_input` at `config_ready`, with A/B done and
C through G pending. All three attempts for this item have empty native Jobs;
the worker PID is cleared, the heartbeat is fresh and all six loops have zero
consecutive errors. The original session and checkpoint remain intact.
The declaration HEAD is not self-approved as a configuration or production pin.

Read-only dependency checks also find the tracked designer-source reports
[FARM-1436](https://linear.app/kuaiwagames/issue/FARM-1436) and
[FARM-1440](https://linear.app/kuaiwagames/issue/FARM-1440) still in Backlog,
and [FARM-1437](https://linear.app/kuaiwagames/issue/FARM-1437) awaiting acceptance.
These are dependency status observations, not current-run backend test results;
none of those cards is started or changed. Configuration-source verification,
farm-hive/Client scenario acceptance, named UI readiness, closing/recovery and
intended production-host qualification remain. No Jenkins/configuration
publication, production deployment, account, credential or production resource
change occurs. FARM-1346/FARM-1425 remain parked.

### Named Common source and native configuration verification (2026-10-08 Windows local)

The operator reports "merged. Configuration ready on common main" in this chat.
Independent current GitHub reads verify
[common #162](https://github.com/Kuaiwa-Network/common/pull/162) merged at
05:57:06 UTC, with unchanged tested head
`936f50c8c3e20cd28c76445a7dc2789f1cb02f7c` and actual merge
`93b0d17793381b9acbb4be23759978bf788654d6`. The tested and merged trees are
equal; both hosted checks remain successful. The authenticated operator's
existing-thread reply `5eadedbb-d973-408f-befc-dfc65c2d9dfe`, created at
06:02:40.045 UTC, faithfully relays that named `main` source. A real signed
prompt is accepted and processed into the original work item's inbox, with the
expected human author. No synthetic webhook, new session or manual ledger edit
is used.

The fresh Common worker resolves `main` to the full merge SHA above. Its root
and all observed descendants belong to its native Windows Job Object; the
three predecessor Jobs are empty. The isolated model remains `gpt-6.1-sol` /
`xhigh`. Its retained SpreadsheetML observations establish the exact four
declared mail headers, no new undeclared mail headers, nonempty mail templates
1201/1202/1203 with the `name` parameter, store 26 and the existing
`voucher_Intimacy` row. The existing typo in 1202's internal designer name is
reported, left unchanged and not asserted as an intended design value.

Earlier local-clone command attempts fail with Git revision/remote diagnostics.
The native worker then creates its own detached checkout using a private Git
repository borrowing the trusted Common clone's object directory. Independent
reads verify all eight recorded native steps pass, the alternates path names
that exact trusted object directory, HEAD is the selected full SHA and status
is clean. The borrowed clone's configuration and hooks remain outside worker
write authority. An independent development scratch probe also performs a
native local shared clone and detached checkout successfully in 4.769 seconds,
without remote access or live-state writes. The failed worker command logs are
retained; the precise cause of those earlier command failures is not
established, and the independent probe does not certify the failed route.
No Bash/MSYS/WSL fallback or weakened ownership/containment check is used.

Retained UTF-8 result logs establish the following checks at the selected SHA:

| Native configuration check | Result | Seconds |
|---|---|---:|
| `gen-config.cmd generate --profile unity-client` | Exit 0 | 9.195 |
| `gen-config.cmd generate --profile farm-hive --profile unity-client` | Exit 0 | 10.206 |
| Client-only artifact verification | Exit 0 | 3.341 |
| Combined artifact verification | Exit 0 | 3.309 |
| Source-digest tests at the selected SHA | Exit 0, four existing platform skips | 1.386 |

The four generator/verifier checks record no platform skip. Both manifests identify
`93b0d17793381b9acbb4be23759978bf788654d6` with `dirty=false`; they contain
412 Client-only and 600 combined artifacts. Independent reads check every
artifact's size and SHA-256 and the generated Client MailInfo fields `id`,
`title`, `content` and `paramList`; the Client projections are byte-identical.
The source-digest rerun records exactly
`TestProductionSourceDigestMatchesExactPackerPipeline`,
`TestSourceDigestRejectsUnsafeTrees/newline_name`,
`TestSourceDigestRejectsUnsafeTrees/backslash_name` and
`TestSourceDigestRejectsUnsafeTrees/fifo` as skipped. Their platform reasons
remain those recorded in stage B; no new skip is added. This is measured configuration generation
on the development PC, not production-host qualification, backend acceptance
or a published configuration archive. The earlier source-digest platform skips
remain the separate stage-B findings above.

The worker records the named source event, `config.ref=main`, the full resolved
SHA, `config.pin=local`, expected version `2026-10-08.93b0d17` and the source
branch [farmbot/farm-1419-config](https://github.com/Kuaiwa-Network/common/tree/farmbot/farm-1419-config).
An independent GitHub ref read verifies that branch points to exactly the
selected SHA without an additional commit. Stage-C notice
`b1637350-d942-4780-a73f-b75ffe051bf7` is posted at 06:26:02.248 UTC. A/B/C
are done, the source report is consumed, and the controller completes the
handoff of the original item to `farm-hive`. At that observation the item is
queued and all four predecessor Jobs are empty. The fresh heartbeat and six
zero-error loops are controller health evidence, not backend acceptance.

No Jenkins job or configuration archive is published. The actual published
version, content digest and archive checksum remain a human closing gate;
the shorter expected-version hash is not substituted for those values.
Backend consumer/scenario checks, named UI readiness, Client and closing
verification remain pending. The separately selected document-driven UI
journey and intended production-host qualification also remain pending,
including the prerelease CLI and cold Unity startup findings above. The frozen
TestBot runtime, development configuration, production installation and parked
FARM-1346/FARM-1425 are unchanged.

### Native backend prerequisites and resumed stage D (2026-10-08 Windows local)

The original FARM-1419 item is claimed in the trusted `farm-hive` worktree.
The worker refreshes the backend baseline to
`66b352c7527c820abd25c33ef149656a5edfafb4` and rereads the actual Feishu
document, revision 236. The observed content digest remains
`a05a1317e3c106ab5c3fc06751ae7c3b3cfb03415dd5e5bd0eca60f4e660c79b`.
The verified Contract source remains merge
`4476168c2c63a639f67d7533f319522157c09949`; the named Common source remains
`93b0d17793381b9acbb4be23759978bf788654d6`. Read-only source checks and
retained UTF-8 logs establish the first native backend attempt:

| Native backend check | Result | Seconds |
|---|---|---:|
| Protocol synchronization | Exit 0; 60 packages | 12.708 |
| Registry generation | Exit 0; 61 outputs | 12.753 |
| Protocol gate | Exit 0 | 11.447 |
| Contract provenance gate | Exit 0 | 2.970 |
| Registry gate | Exit 0 | 6.575 |
| Designer-source digest | Exit 0 | 10.708 |
| Candidate designer-pin calculation | Exit 0 | 11.615 |
| Configuration generation | Exit 1; prepared configgen dependency unavailable with `GOPROXY=off` | 12.895 |
| Dependency diagnostic | Exit 1; module lookup disabled | 0.047 |
| Candidate configuration manifest | Exit 1; retained outputs do not match the candidate source pin | 0.155 |
| Candidate configuration reproduction | Exit 1; same source/output mismatch | 0.388 |
| Restored prior configuration manifest | Exit 0 | 0.512 |
| Vet / build | Exit 1 / 1; missing hive-jelly and cache-write boundary, before compilation | 37.913 / 37.905 |
| Race suite | Exit 2; CGO disabled, no tests run | 0.054 |

An earlier native protocol-snapshot command also fails to resolve its local
`origin/main` reference; the worker preserves that log and succeeds with an
owned snapshot. These failures remain failures, not platform skips or backend
acceptance. No new skip or waiver is introduced. Failed configuration generation
does not replace the existing outputs or their pin: the old configuration
manifest and inputs are restored and verified. Local commit
`8a1f4211572a2c30eb1950519e8e989236becb4c` changes protocol provenance only;
the trusted worktree is clean, it is not pushed and no backend PR exists at
this checkpoint. All 29 mapped business scenarios remain unexecuted, and the
four gifting handlers remain unimplemented in the observed source. The prior
FARM-1436/FARM-1440 reports do not establish a current-run data regression.

Notice `2db52ad1-2008-4fd6-a659-f9ff9b2de039`, posted at 06:43:26.076 UTC,
requests the native prepared dependencies and CGO compiler. The controller
parks the same item at `server` / `answers`, clears its worker PID, and all
five attempt Jobs are empty. No worker downloads a tool or dependency, changes
authentication, weakens containment or substitutes Bash/MSYS/WSL/MXC.

Within the standing development-host preparation scope, the development agent
prepares a new ignored scratch cache, leaving the runtime-selected caches and
actual backend dependency files unchanged. The exact existing module pins are:

- `github.com/Kuaiwa-Network/common/designer/configgen`:
  `v0.0.0-20260916145836-6e697505d651`.
- `github.com/Kuaiwa-Network/hive-jelly`:
  `v0.0.0-20260929021510-b9d2278b90b3`.
- `github.com/Kuaiwa-Network/hive`:
  `v0.0.0-20260922032331-927a2319f67d`.

Configgen is restored from a previously prepared local module archive and its
committed Go checksums are verified. The initial hive-jelly Git fetch is refused
by native askpass outside its trusted directory; that refusal and policy are
retained. Existing GitHub API access reads the exact private source commits
instead, without credential setup. Canonical module ZIP and `go.mod` checksums
for both private modules match the committed `go.sum` entries. The initially
missing pinned `golang.org/x/mod` dependency and public transitive metadata are
prepared at their selected versions, with checksum verification; no dependency
version is upgraded. The first incomplete graph check is retained separately.
The broader scratch preparation adds 35 checksum lines; that temporary file is
preserved and the original scratch dependency files are restored before the
final checks. With those original files and `GOPROXY=off`, `GOSUMDB=off`,
`GOTOOLCHAIN=local` and read-only module mode, all three final checks pass:
the 124-entry module graph in 0.059 seconds, configgen command build resolution
in 0.162 seconds and `go mod verify` in 0.678 seconds. This prepares inputs for
the worker; it does not certify regenerated backend outputs or business tests.

No native CGO compiler was available on the selected PATH. A standalone
[Winlibs native Windows release](https://github.com/brechtsanders/winlibs_mingw/releases/tag/16.2.0posix-14.0.0-ucrt-r2)
is prepared only in ignored development scratch: GCC 16.2.0, MinGW-w64 14.0.0,
target `x86_64-w64-mingw32`. Its 273,613,326-byte archive matches both the
GitHub release digest and the published SHA-256
`d5dbafc4a170e762ca6143151ec918fb9e2c72736fb14cd704abebc6bdd5276a`.
The native compiler hash is
`206f9fc067234f48ae077550658dae06bd75d99110c1b3166887b7090cbd7fa0`.
The `libsynchronization.a` runtime check required by the
[Go Windows race-detector guidance](https://go.dev/doc/articles/race_detector)
succeeds. No global PATH, application setting, registry, account, service or
production installation changes.

An independent native smoke probe uses the verified backend process runner,
which starts suspended, assigns the process to its owned Windows Job before
resuming it and verifies settlement. With network access disabled, a real CGO
call and synchronized Go test pass under `go test -race -count=1 -v .` in
10.641 seconds. A separate deliberately racing program run with `go run -race`
returns exit 1 and reports `DATA RACE` in 2.096 seconds. Both Jobs settle.
This positive/negative probe establishes compiler and detector operation on
this development PC; it does not substitute for the actual farm-hive race suite.

Private `host-native-backend-readiness.json` supplies the prepared paths and
identities in the selected item's state directory. The authenticated original
thread reply `8ed64467-c8c0-4df6-bff0-d1bd61ba3ed4`, created at
07:14:45.666 UTC, explicitly identifies a Codex development-host measurement,
not a new human design, configuration-publication or UI-ready decision. Real
signed delivery is accepted and processed into the original inbox, and the
worker consumes it. The same item/session resumes at `server`, with a fresh
native Job and `gpt-6.1-sol` / `xhigh`. A snapshot-ancestry diagnostic includes
an unrelated process whose creation predates the owned root; held native
process handles distinguish it from the current worker. All observed processes
created for the current attempt are members of its owned Job. Cleanup authority
remains Job membership, never snapshot PID ancestry. The five predecessor Jobs
are empty, the heartbeat is fresh and all six loops have zero consecutive errors.

At this checkpoint stage D implementation, regenerated configuration, actual
build/vet/race and business scenarios remain pending, followed by human UI
readiness, Client and closing verification. Configuration publication and game
PR merges retain their named human gates. The separately selected document-driven
UI journey, prerelease CLI qualification, cold Unity startup and intended
production-host release/recovery checks remain pending. These are development-PC
measurements. Production deployment is unauthorized; the frozen TestBot source,
development configuration and parked FARM-1346/FARM-1425 are unchanged.

### Resumed native backend artifacts and failed race suite (2026-10-08 Windows local)

The resumed worker independently verifies the prepared module cache and refreshes
its backend baseline to `883bb1f4b5035ce7c77d899176d934d80ab3cfca`.
Native configuration generation now succeeds without downloading dependencies.
It commits the regenerated consumer outputs and local source pin at
`72899f35f313aa85eab1e237623c42e96c23a153`. Independent trusted-worktree reads
verify every one of the 368 committed artifact hashes, the selected source
version `2026-10-08.93b0d17` and a clean worktree. This proves the consumer
artifacts, not completion of the gifting implementation or configuration publication.

The retained UTF-8 results distinguish successful checks and failed attempts:

| Resumed native check | Result | Seconds |
|---|---|---:|
| Prepared module-cache verification | Exit 0 | 0.826 |
| Exact designer-source digest | Exit 0 | 11.343 |
| Configuration generation | Exit 0 | 29.129 |
| Build / vet | Exit 0 / 0 | 17.302 / 8.530 |
| Committed configuration manifest | Exit 0 | 1.154 |
| Local designer-pin check | Exit 0 | 23.508 |
| Configuration reproduction, first attempt | Exit 1; native read-only Git command fails | 76.685 |
| Focused configuration diagnostic / final gate | Exit 0 / 0; all 368 artifacts match committed bytes | 50.342 / 37.569 |
| Cross-call lint | Exit 0 | 14.290 |
| Owner document / API / store gates | Exit 0 / 0 / 0 | 0.504 / 0.275 / 0.285 |
| Contract provenance / message gates | Exit 0 / 0 | 3.199 / 11.754 |
| Actual backend race suite | Exit 1; owned Job settled | 367.738 |
| Focused food-configuration recheck | Exit 1 | 27.981 |

The successful native reproduction reruns retain the same committed head and
unchanged implementation. The failed first attempt remains preserved; the exact
cause of its read-only Git failure is not established. No containment or source
check is bypassed. Build/vet results at the refreshed baseline are separate from
later implementation acceptance and do not certify code that has not yet been written.

The actual Go race suite runs with the prepared native compiler. Its JSON stream
reports 10,244 passing, 41 failing and 359 skipped test/subtest outcomes; these
counts include parent and nested outcomes and are not 10,644 independent tests.
There are 39 failed package outcomes, with named test failures reported in eight
packages. The remaining failed package outcomes cannot be called passing or
skipped. The log includes a native test-executable startup failure reporting
"not a valid Win32 application". No `WARNING: DATA RACE` is present, but failed
and incomplete execution does not establish a clean race suite. The retained
UTF-8 JSON log has SHA-256
`474557c559b3940661224794412d4e01f4708b46c36452c4664aca44b2715817`.
An independently parsed private report preserves every failed package, named
failure, skip name and reason. No new skip is added and no failed outcome is
rewritten as an unavailable-host pass.

Measured failure categories include tests invoking Linux shell scripts, native
Windows timezone and socket assumptions, an `.invalid` DNS assumption that does
not hold on this host, actor-call timeouts and a designer-data/test expectation
mismatch. The shell-test failures are retained; Bash/MSYS/WSL is not installed
or enabled to make them pass. Failed actor, socket and executable checks remain
under focused investigation rather than being labeled application regressions
or harmless host limitations without evidence. Linux/service-backed CI remains
necessary evidence; these Windows results do not replace it.

At the selected Common commit, the actual Food sheet's `20005` row leaves
"首次制作奖励" empty. The existing tests `TestE2ERealFoodFirstRewardAdapted`
and `TestE2ERealFoodMakeFlow` expect `COIN(1)` times 5, and their focused recheck
fails. This establishes a current input/test-expectation conflict; it does not
establish the intended designer value or a gifting regression. The agent does
not invent that value, edit designer/global data, weaken assertions or approve
a waiver. The prior FARM-1436/FARM-1440 reports remain separate historical
observations, and those cards are not started or changed.

Most recorded skips concern unavailable Mongo/Redis-backed integration fixtures;
the full skip inventory remains in the private parsed report. Skipped service
coverage, helper-probe skips and any configuration-dependent skip remain
untested. No service, credentials or environment settings are created to turn
them into an apparent pass. Actual gifting scenario coverage, backend CI and
stage-D acceptance remain pending.

The existing-thread developer report `41c5aa33-8cc4-4093-8003-36e6b8ba38af`,
created at 07:44:17.528 UTC, preserves the failed suite and recommends continuing
only the already-authorized gifting work independent of those baseline issues.
It explicitly grants no new human design, waiver, publication, UI-ready, game
merge or production approval. Failed checks remain acceptance blockers; no
next-stage handoff is certified by this report. At this observation the same
selected item remains running in its owned native Job, with its five prior
Jobs empty and no backend PR recorded in its plan. Named human gates, Client,
closing and intended production-host qualification remain pending. These are
development-PC measurements; the frozen runtime, development configuration,
production installation and parked FARM-1346/FARM-1425 remain unchanged.

### Committed mail milestone and independent native check (2026-10-08 Windows local)

A later serial baseline diagnostic at
`72899f35f313aa85eab1e237623c42e96c23a153` also fails: exit 1 in
1,457.373 seconds, with its owned Job settled. Its JSON stream reports
16,899 passing, 46 failing and 1,290 skipped test/subtest outcomes across
12 failed packages. Parent and nested outcomes are included; these are not
independent test counts, and the earlier incomplete parallel execution has a
different outcome inventory. Every reported failed package, named failure and
skip is retained privately with the UTF-8 log. This diagnostic does not turn
the backend baseline into a pass or establish that all failures are harmless
Windows limitations. The unresolved designer-data expectation and platform,
actor and service-backed checks remain acceptance blockers.

The worker develops the garden-gift mail changes with failing and passing
focused tests, then applies and checks them in the actual backend worktree.
It commits this milestone at
`e9e2869eb949e7c88d6a40e9b1fe6c4a4234fbe7`. The changes add an explicit
persistent garden-gift category, frozen mail text, order association and
sequence checks, retention and claim/delete protections. Staging mail into
an isolated recipient owner does not itself complete the multi-player
database transaction or prove rollback and idempotency of purchase/send.
Temporary overlay results are development evidence, not verification of the
committed candidate.

An independent check uses a new separate development checkout frozen at that
exact mail commit. It uses the prepared native Go 1.25.1 and verified Windows
compiler, its own build cache and temporary directories, disabled module
network access and the owned Windows Job runner. The local trusted-object
clone and exact checkout pass in 0.184 and 2.161 seconds. The command
`go test -race -json -count=1 ./modules/mail` exits 0 in 26.054 seconds,
with its Job settled. It reports 127 passing, zero failing and one skipped
test/subtest outcome. All eight `TestGardenGift...` tests actually run and
pass, covering persistent category, corrupt attachments, read-before-delete,
category boundaries, frozen input, retention/reload, duplicate rejection and
invalid association. The tested frozen checkout remains clean.

The single skip is `TestEnqueueFiniteIdemClassification/mongo`: the existing
fixture reports that `MONGO_URI` and `MONGO_REQUIRED` are unset. The first
independent wrapper reports an assertion failure because it required zero
skips across the whole package, despite Go exiting 0. That original receipt
is preserved. A separate observation classifies the skip, verifies that all
eight new gift tests passed without skips and checks the frozen checkout;
it does not alter tests, repeat the suite or certify database integration.
The UTF-8 Go JSON log has SHA-256
`5b693ae7d54764fd1095088948e51290a76d1d1e0b6e56ea05cc25855125918d`.

The previously recorded developer report is now observed consumed in the
original item's inbox. The same item/session remains in stage D while the
worker continues persistence and purchase/send transaction implementation.
The four gifting handlers, complete 29-scenario coverage, real database
integration, backend CI and stage-D acceptance remain pending. No backend PR
is recorded in its plan at this checkpoint. Human UI readiness, configuration
publication and game PR merges retain their gates, followed by Client and
closing verification. These remain development-PC measurements; production
qualification and deployment are not certified, and no production, runtime
configuration, account or parked-issue change is made.

### Gift transaction foundation and integration review (2026-10-08 Windows local)

The worker commits the transaction foundation at
`aca44391a071ebf7af6f4fb737e80cb01700f3a7`. It adds memory and Mongo storage
boundaries, durable request/payload receipts, recipient creation sequences,
relationship checks and player revision guards. The Mongo implementation uses
one session for the order, both players and intimacy. This source review and
passing memory tests do not prove actual Mongo execution, server wiring or
the complete economic transaction.

The retained native worker checks include build and vet exits 0 in 18.677 and
14.874 seconds. The first cross-call invocation exits 1 in 0.054 seconds with
a path error; its corrected invocation exits 0 in 7.318 seconds. The store
race check passes in 9.807 seconds before commit and 9.627 seconds at the
committed foundation. Pre-commit result labels identify the prior HEAD, not
an immutable snapshot of the changed files.

Independent verification freezes a separate development checkout at the exact
foundation commit. With prepared native Go/compiler inputs, network access
disabled, private scratch and an owned Windows Job, it runs
`go test -race -json -count=1 ./modules/gardengift ./modules/friend/friendstore ./internal/playerrevision`.
Go exits 0 in 16.649 seconds and its Job settles. The JSON stream reports
218 passing, zero failing and 92 skipped test/subtest outcomes. Every test in
the new `gardengift` package runs and passes, including memory rollback,
replay, competing qualification/spend, old-writer fencing and corrupt sequence
checks. All 92 test skips report unset `MONGO_URI`; their names and reasons
are preserved privately. Real database integration remains untested.

The helper-only `internal/playerrevision` package has no test files. Go reports
a package skip for that reason, separately from the 92 test skips. The initial
independent wrapper fails its assertion that all three package outcomes must
be `pass`, despite Go exiting 0. Its original receipt is retained. A separate
observation classifies this result and verifies the tested checkout is clean;
no application test, skip or containment check changes and no suite rerun is
used to obtain a different result. The UTF-8 Go JSON log has SHA-256
`0e5be9b91cb9fde53e554b132bb7e993095fee981491ca3e288e0f775e84f8b9`.

Static integration review identifies a compatibility requirement before the
new store replaces the player store. Existing `invitegift` code calls the
persist pool's `MonotonicUpdate` RPC. The pinned framework rejects a store
without `persist.MonotonicUpdater`; ordinary `MergedUpdate` is not an allowed
fallback. At this foundation commit, the new memory and Mongo stores lack
that optional interface. The genuine developer report
`f03e588a-c5d4-45fb-b081-c98516eb9bfd`, created at 09:00:25.213 UTC in the
original thread, requests preservation and regression coverage of those
existing atomic max/set semantics during server wiring. It also requests
an explicit migration and recovery plan for existing player records: the
new revision/sequence fields are only initialized on newly inserted records.
The report supplies technical findings, not new human design or merge authority.
No migration, field repair, epoch reclamation or production data change is run.

A read-only comparison also confirms the food conflict crosses the configuration
update: backend main `feee46cbb1336efeb1019638638e11fd7bd76ff0` still contains
`COIN(1)` times 5 for Food `20005`, while the selected Common-derived candidate
has an empty first reward. The existing assertions match the old consumer
data. The intended designer value remains under clarification; the agent
does not change either value or weaken the tests.

The same selected item/session remains in stage D, continuing shop effects and
actor integration. The new foundation alone does not certify four handlers,
all 29 business scenarios, real Mongo coverage, backend CI or stage-D acceptance.
Named human publication, UI-ready and game merge gates, Client/closing checks
and production qualification remain pending. These are development measurements;
the frozen runtime, configuration, production installation and parked issues
remain unchanged.

### Gift core and player-host checks; missing statistic mappings (2026-10-08 Windows local)

The worker commits real shop-factory staging at
`f6a926d9b1f871c77faf2ea8ad290901aef9acb6`, then the gift-effect core at
`afe6c0e4cd4a049ec367838d2f50b963cb366b71`. At
`fc1475ec675be341b13280c91a74a031a3dbcbc2`, it restores the player store's
existing monotonic-write surface and adds malformed-update and no-automatic-repair
regressions. The earlier integration review therefore identifies a gap that is
addressed in this later candidate, not an unresolved missing method at that head.
The original failed regression results are retained. These changes do not
perform player-data migration or establish production readiness.

The shared player-host integration is committed on the recorded
`farmbot/farm-1419-followup` branch at
`6fd4919277ee05bf671f379fe8c61eeca0141f4f`. It binds ordinary writes to the
loaded player revision, refreshes authoritative state without reclaiming the
login epoch and discards old turns when their writes are fenced. An authoritative
read runs before each guarded turn; the reload callback is used when the
revision changes. Server assembly and the callback's affected-component scope
still require final verification. Neither branch has a backend PR recorded at
this checkpoint; game review and merges retain their human gate.

The first host fixture run fails compilation; the next run reports two rejected
gift transactions and a secondary actor timeout. The corrected fixture's focused
race check exits 0 in 6.796 seconds, and the full user-package race check exits 0
in 8.300 seconds. Vet exits 0 in 0.549 seconds. The first host cross-call check
fails in 10.245 seconds; its corrected check passes in 9.547 seconds. Failed
attempts remain preserved, with no new skip or weakened transaction validation.

Three independent native Windows race checks freeze separate development
checkouts at their exact commits, with sanitized child environments, module
network access disabled and owned Jobs that settle. Later checks reuse only an
earlier independent build cache; `-count=1` still runs the tests. The verified
checkouts remain clean:

| Exact candidate | Packages checked | Passing / failing / skipped test/subtest outcomes | Race seconds |
|---|---|---|---:|
| `f6a926d9b1f871c77faf2ea8ad290901aef9acb6` | shop, gardengift, friendstore, playerrevision | 365 / 0 / 92 | 53.885 |
| `fc1475ec675be341b13280c91a74a031a3dbcbc2` | above, mail and invitegift | 567 / 0 / 93 | 11.041 |
| `6fd4919277ee05bf671f379fe8c61eeca0141f4f` | above and user | 708 / 0 / 94 | 11.775 |

All recorded test skips report unset `MONGO_URI`; their exact names and reasons
are retained privately. The helper-only `playerrevision` package separately has
no test files and is not standalone execution coverage. Source hashes, UTF-8
logs and original receipts are preserved. The latest race JSON log has SHA-256
`55ed3eb4e761bff86d752c33ce04c0c6c41e9c7e996082e9bd06cc7b199c0f1b`.
Passing these focused checks does not replace real Mongo, full backend CI,
four-handler assembly or the complete 29-scenario acceptance.

The authenticated TestBot notice `a612b039-d95d-4f42-b4d4-3964321cab65`, created
at 09:52:48.820 UTC in the original thread, identifies missing authoritative
statistic mappings. The frozen contract requires VIP growth and first-top-up
progress to advance by `N * 100` cents. The selected configuration's
`event_after_recharge` / GlobalFuncInfo `4005` only advances cumulative recharge
through STAT `9001`. The existing VIP projection reads `privilege.expires.1`,
an expiry timestamp; first-top-up eligibility flags do not identify the required
growth/progress event chain. The worker asks the owner and designer for the
authoritative fields, units and entry points, or the missing named configuration
or contract definitions. It does not infer monetary progress from expiry,
invent thresholds or reopen the already-decided cents unit.

Independent implementation continues while those mappings and the Food `20005`
reward value remain under clarification. Stage D remains pending; the three
statistics, complete business coverage, server assembly and real service-backed
acceptance are not certified. Named publication, UI-ready and game merge gates,
Client/closing, schema migration/recovery planning and production qualification
remain pending. The runtime and development configuration remain frozen; no
production, credential, account or parked-issue changes are made.

### Handler wiring and login-order regression (2026-10-08 Windows local)

The same selected worker continues stage D with uncommitted handler and server
wiring changes on top of `6fd4919277ee05bf671f379fe8c61eeca0141f4f`.
These worker results measure mutable development source. A result file's HEAD
label is not a frozen candidate identity, and the earlier independent checks
do not cover this new batch.

The real handler actor race check exits 0 in 13.390 seconds. The first
production-module list check exits 1 in 8.913 seconds because the new module
is absent from the declared end-to-end harness list. Adding it to that harness
retains the list comparison and passes the focused check in 8.946 seconds.
The new frontend/config/mail selection then reports 24 passing, zero failing
and zero skipped test/subtest outcomes in 26.113 seconds, including the four
typed request handlers exercised through real user-owner turns and memory
stores. Build and vet exit 0 in 4.441 and 1.908 seconds. These checks do not
establish the complete 29-scenario acceptance or real Mongo execution.

The first existing-login regression selection exits 1 in 7.231 seconds.
`TestLoginDuplicateKicksOld`, `TestReloginReadsBack` and
`TestNewModulesLoadedOnRealBootPath` each receive `NewMail_Ntf` where the
established login sequence expects `CloseFriendTeamSelf_Ntf`. The new module
is moved after its already-started dependencies in the harness, preserving
the existing login assertions and registration order. The corrected selection
reports five passing, zero failing and zero skipped outcomes in 9.436 seconds.
All original failure receipts and UTF-8 logs are retained privately; no skip
or relaxed assertion is used to get a pass.

Independent comparison freezes a separate development checkout at the previous
exact commit `6fd4919277ee05bf671f379fe8c61eeca0141f4f` and runs
`go test -race -json -count=1 -run '^(TestLoginDuplicateKicksOld|TestReloginReadsBack|TestNewModulesLoadedOnRealBootPath)$' ./server`.
It uses sanitized native Windows child inputs, offline module resolution and
an owned Job that settles. All three tests pass with zero failures and zero
skips in 31.022 seconds, and the frozen checkout remains clean. This confirms
the previous candidate passes those checks; it does not independently certify
the mutable correction. Its UTF-8 Go JSON log has SHA-256
`b5d1b61058823f62f078dfaa9a90e93527fe73cfe1b0d4d1700198bc68eb9aad`.

Server registration currently leaves gift economics unavailable until the
transaction service and guarded-store/progress wiring are installed. Stage D
remains pending. Exact-commit verification of the new batch, authoritative
VIP-growth/first-top-up mappings, the Food `20005` decision, complete scenario
coverage, real database/service CI and schema migration/recovery planning
remain release prerequisites. Human configuration publication, UI-ready and
game PR merges, Client/closing and production-host qualification retain their
gates. These measurements are from the development PC; the active runtime,
production, credentials and parked issues are unchanged.

### Operator reward correction and transaction-scanner maintenance (2026-10-08 Windows local)

The operator confirms that gifting consumes vouchers and should reuse the
existing VIP-growth and first-top-up handling. This is a behavior clarification,
not a newly supplied field mapping or proof that the selected Go implementation
already has the required integration. The worker must locate and verify the
existing path rather than invent new counters or ask the operator to identify
implementation symbols without first inspecting the code.

The latest operator correction, original-thread comment
`7af3565b-1334-40f4-9c30-42386010ea59`, supersedes the earlier interpretation that
Food `20005` must permanently have no first reward: first rewards are driven by
configuration. Commit `5cc7a2cd83804744313d8fb891bd29856cedc860` deletes the
obsolete fixed `COIN(1) * 5` assertions and redundant real-row reward test. It
changes neither designer configuration nor reward implementation. Existing
Food fixtures cover configured rewards, empty rewards and repeated creation.
The Food decision is resolved; checking the actual gifting progress path remains
implementation work.

Independent verification freezes a separate clean development checkout at
that exact commit and runs
`go test -p 1 -race -timeout 300s -json -count=1 -run '^(TestE2ERealFoodMakeFlow|TestClaimableFirstRewardMatrix|TestMakeFirstRewardOncePerID|TestMakeFirstRewardFailureSkipsFlagNotMake)$' ./modules/food ./config`.
Native Windows, Go 1.25.1, the pinned native compiler and original offline module
pins produce 10 passing, zero failing and zero skipped test/subtest outcomes
in 48.652 seconds. All four named top-level tests run. The owned Job settles,
the checkout stays clean and every tracked-file fingerprint stays unchanged.
The UTF-8 Go JSON log has SHA-256
`32717ed3f3c1d8be88998ff46b9ee998bfc450d0ee2bb8f57804d972d7f06135`.
The worker's full Food package check (437 pass outcomes) and focused
config/recharge/gift selection (32 pass outcomes) exit 0 in 67.770 and
60.701 seconds respectively, with no failures or skips. These are worker
receipts, not additional independent coverage or a full config package run.

The published backend drafts are [farm-hive #366](https://github.com/Kuaiwa-Network/farm-hive/pull/366)
at `fc1475ec675be341b13280c91a74a031a3dbcbc2` and
[#367](https://github.com/Kuaiwa-Network/farm-hive/pull/367) at the Food correction
above. A separate development checkout freezes the previously published
follow-up `cea5b2a93717018d85f9cecacc32584db40523b2` for CI maintenance. The initial
regression check exits 1 in 39.557 seconds: actual gift transaction entry points
are absent from the admission list, and identically typed driver-handle
comparisons are incorrectly classified as erasure. Existing nil comparison
support is preserved.

The independently prepared [CI-only draft #368](https://github.com/Kuaiwa-Network/farm-hive/pull/368),
commit `4bbe377a3afc5fc6d6370d6e173d563130bbeac2`, targets the selected follow-up
branch and changes nine CI/scanner/fixture files. It admits only the actual
`Transact` and `AppendGardenGift` transaction entry points and allows `==`/`!=`
when both operands have exactly the same driver-handle static type. Comparisons
through `any`, a local interface or a variable shadowing `nil` remain rejected;
application code, module pins, configuration and the active worker worktree are
untouched.

The full scanner package with `-race` passes 11/0/0 test/subtest outcomes in
22.203 seconds. The native scanner executable's `--selftest` exits 0 in
5.139 seconds, preserving exact 0/1/2 exit-code fixtures. Three native degradation
injections deliberately permit erased comparisons, append an unused admission,
and remove the two new admissions. They produce the intended assertion failures
in 3.250, 5.459 and 5.530 seconds; no compile failure is substituted for a
meaningful rejection. Every tracked source file is restored byte-for-byte after
each injection. The full scanner rerun passes 11/0/0 in 19.646 seconds, with
UTF-8 log SHA-256
`779a303fca6bb669c82370706e9d24dde445e2a4767405ca22954a9fea16b8f7`.
Native owned Jobs retain kill-on-close containment, use an 8 GiB memory limit
verified by API readback and settle. No Bash, MSYS, WSL or MXC is used; the
canonical Bash degradation harness remains unrun on this Windows host.

The actual hosted failure is earlier than the transaction gate. The newly
read #367 run `37773237390` at `5cc7a2cd` and #368 run `37773520038` at
`4bbe377a` both fail downloading the selected designer archive with HTTP 404.
Both select provisional version `2026-10-08.93b0d17`; neither reaches business
CI. Earlier #366 and #367 runs fail at the same download step. Merging Common
`93b0d17793381b9acbb4be23759978bf788654d6` does not publish an archive. The
configuration publisher must provide the actual published version, content
digest and archive SHA-256. No Jenkins/configuration publication or weakened
provenance check is performed. Source-level cross-platform checks that pass do
not certify the complete backend CI.

Stage D remains pending: verified voucher progress integration, complete
29-scenario coverage, required real-service execution and successful backend
CI are still needed. The farm-hive development contract describes a new project
without legacy production data and forbids invented historical-data repairs;
this verification adds no legacy game-data migration requirement. FarmBot's
own state recovery and host qualification remain separate. Named configuration
publication, UI-ready, game PR human merges, Client/closing and production-host
qualification retain their gates. These results measure the development PC,
not production readiness. The running runtime and private configuration remain
frozen; no production, credential/account or parked-issue changes are made.

### Configured Food reward guard and recharge source investigation (2026-10-08 Windows local)

The operator's latest instruction, relayed in original-thread comment
`1e9adf40-846d-4abe-82ed-7b8e021985f7`, is to continue without addressing the
Common configuration package now. Implementation and local native verification
that do not depend on that published archive continue. The recorded archive
404, publication/provenance and incomplete hosted backend CI stay pending;
this instruction does not certify them or authorize configuration publication,
UI-ready, game PR merges or production changes.

Deleting the obsolete fixed Food reward assertion also removed
`TestE2ERealFoodFirstRewardAdapted`, which F15 still named. Deliberately dropping
first-reward adaptation then left the ordinary real cooking test passing in
11.460 seconds: that remaining selection did not guard the configuration
column. The separate [maintenance draft #369](https://github.com/Kuaiwa-Network/farm-hive/pull/369),
exact commit `7dacf95a67e0b55b1b60419317c562b104148ad2`, restores the named
adapter test with expectations decoded from the original embedded Food
configuration. An independent absent/empty/nonempty input protects the adapter
even if all real configured rewards are empty. Empty rewards remain valid;
neither Food `20005` nor a five-coin reward is fixed by the test. F15 declares
this adapter guard, and the cooking test retains its ordinary flow coverage.
The three-file diff contains only tests, the F15 declaration and its design
record. Application code, designer configuration and module pins are unchanged.
The active worker tree is untouched; #369 and transaction maintenance #368
remain drafts for the existing human game-PR integration gate.

Independent exact-commit checks use native Windows, Go 1.25.1, the pinned
native compiler, original offline module pins and `-race`. The final declared
F15 baseline has 3 passing, zero failing and zero skipped test/subtest outcomes
in 6.703 seconds. Applying F15's existing literal replacement from
`adaptRowReward(r.GetFirstReward().GetRewards())` to `adaptRowReward(nil)`
produces assertion failures in both named subtests and their parent: 0/3/0
in 5.623 seconds. A compile failure is not substituted for a meaningful
rejection. Every tracked source file is restored byte-for-byte, then the
configured reward, cooking, empty-reward and repeat-reward selection passes
13/0/0 in 11.723 seconds. Its UTF-8 Go JSON log has SHA-256
`e22048defa08a1b11d8613c2f8fd09e260c81f8e46a30f3d4f3b873ef7f7eaa9`;
the checkout stays clean. All owned Windows Jobs settle, retain kill-on-close
and have the 8 GiB memory guard verified by API readback. No Bash, WSL, MSYS or
MXC is used. The canonical Bash degradation harness remains unrun on this host.

The first native compile exits 1 after 21.783 seconds because the host's
temporary volume lacks space; no test assertion runs. The initial UTF-8
failure log and receipt are preserved. Fresh, separately owned temporary and
compilation-cache directories on an available volume resolve that host limit.
No production cleanup, global environment change or live worker/runtime/cache
mutation is performed. This is a development-PC result, not production-host
qualification. Verified maintainer report `567ded7f-b227-4df6-b4aa-12bbce58812f`
delivers the exact draft and measurements in the original selected thread;
it is not a human approval or CI waiver.

Read-only source investigation locates the existing C++ first-recharge
producer at [ShopControl::recordRechargeOnDeliver](https://github.com/Kuaiwa-Network/farm-server/blob/d790916f8d9b2a62263395bbea5b4afed15c68c4/source/inks/shop/ShopControl.cpp#L65),
freezing farm-server main `d790916f8d9b2a62263395bbea5b4afed15c68c4` and checking
Git blob identities. It records the amount in fen under recharge total,
first/last time, dispatches the shared after-recharge event and dispatches
first-recharge only once, reserving the session flag before events. The selected
published Go candidate already has `RecordComp` but only dispatches the
after-recharge event. Maintainer report `d79eefb6-b643-4fda-862b-1dcb09a8de8f`
provides this concrete source and existing Go component to TestBot, preserving
actor/revision, atomicity and idempotency. It does not authorize copying the
old asynchronous static-write implementation. The worker is implementing
the shared first-recharge path; mutable-source checks do not certify a frozen
published candidate.

The already fetched original Feishu document remains revision 236, 42,552
Markdown bytes, SHA-256
`a05a1317e3c106ab5c3fc06751ae7c3b3cfb03415dd5e5bd0eca60f4e660c79b`.
Its payment rule requires sender cumulative recharge, VIP growth and first
recharge to behave identically to self-purchase, with no corresponding receiver
progress. VIP source investigation continues: the inspected client privilege
projection exposes monthly-card benefits/expiry, which cannot establish an
amount-growth consumer. Neither absence nor completion of that consumer is
inferred from the partial inspection; no guessed monetary field is introduced.

Stage D and release prerequisites remain pending: verified complete voucher
progress integration, complete 29-scenario coverage, required real services,
successful backend CI after named archive publication, human game-PR
integration and UI-ready, Client/closing and production-host qualification.
The development contract's new-project/no-legacy-production-data assumption
is unchanged; this source investigation adds no invented historical game-data
migration requirement. Production, private runtime/configuration, credentials,
accounts and parked issues are unchanged.

### Frozen first-recharge and combined maintenance checks (2026-10-08 Windows local)

The selected [backend follow-up #367](https://github.com/Kuaiwa-Network/farm-hive/pull/367)
is published at `f834799e54d113b341a2d1a6576b4d5a07a4fd6c` and remains a draft.
It adds shared recharge total/first/last records and first-recharge dispatch
before reentrant events, with voucher purchase, paid frozen delivery and gifting
using the existing Go record component. The gift regression exercises configured
Activity 13/first-charge state and sign activation on the sender, no matching
receiver progress, retained first time after reload, replay and overflow rollback.
It also contains the delayed-mail retention and multi-item/frozen-blessing fixes.
These changes do not establish complete VIP-growth handling or release readiness.

Independent verification freezes a fresh separate development checkout at that
exact published commit and runs
`go test -p 1 -race -timeout 300s -json -count=1 -run '^(TestRechargeRecordsPrecedeReentrantEvents|TestBuyAndPaidDeliveryShareFirstRechargeAfterReload|TestBuyRechargeRecordOverflowPrecedesCost|TestRealGardenGift.*|TestLoginDuplicateKicksOld|TestReloginReadsBack|TestNewModulesLoadedOnRealBootPath)$' ./modules/shop ./config ./server`.
Native Windows, Go 1.25.1, the pinned native compiler and original offline
module pins produce 35 passing, zero failing and zero skipped test/subtest
outcomes across 18 top-level tests in 64.670 seconds. The eight explicitly
selected first-recharge, rollback and login tests all run. The UTF-8 Go JSON
log has SHA-256
`8acf2c443856dd7b275514e20f150236ca0201ce0428e07b43ef9e4a9618553e`.
The checkout stays clean and every tracked-file fingerprint stays unchanged.
This is a local-store regression selection, not real Mongo execution or the
complete 29-scenario acceptance.

A separate, private development branch combines that frozen application
candidate with the full verified commit ranges of maintenance drafts
[#368](https://github.com/Kuaiwa-Network/farm-hive/pull/368) and
[#369](https://github.com/Kuaiwa-Network/farm-hive/pull/369).
The local combined head is `985dd69bc40eddc862b78918af89f0740c0a576e`.
Its 12-file difference contains CI/scanner/fixtures, the configured Food reward
test and Food design record only; application code, module pins and generated
configuration match `f834799e`. No game PR is merged, the private branch is not
published, and the active worker tree is untouched.

On this combination, the full `./ci/cmd/txncallsites` package with native `-race`
passes 11/0/0 in 28.633 seconds; its Go JSON log SHA-256 is
`c3df48d3adb106869d53c87ad8e8529233156ebd320161d38d1390dfaa371722`.
The preceding shared recharge/gift/login selection plus
`TestE2ERealFoodFirstRewardAdapted` passes 38/0/0 across 19 top-level tests
in 62.524 seconds, with log SHA-256
`38f6c27b880f2e82c39b964c011421d0a53d3de1f0eca7e8e0f5341f91b3904a`.
Tracked fingerprints remain unchanged and the checkout stays clean. All native
owned Jobs settle and retain the verified 8 GiB memory guard. These combined
checks add no claim that the canonical Bash harness or hosted backend CI ran.

The first private combination attempt selected only the final commit of the
two-commit Food draft, omitting its prerequisite test commit and producing a
documentation cherry-pick conflict before tests. This was a verification-helper
preparation error, not an application regression. The conflict evidence is
preserved, the identified private cherry-pick is aborted to a clean state,
and the corrected fresh combination applies both Food commits before the
measurements above. No active source or production state is changed.

Review identifies an additional direction correction in `f834799e`: its
historical-player/recharge-history release-input branch, test and documentation
conflict with farm-hive's explicit new-project/no-production-data development
premise. The measured selection includes that historical-input test; its pass
does not approve the extra release condition. Verified maintainer notice
`4765c32c-8e82-4834-b32c-504c8345426d` requests removal of the invented historical
compatibility requirement while preserving overflow, corrupt-record,
idempotency, actor/revision and atomic rollback checks. Correction and
exact-commit revalidation remain pending; no historical player data is read,
backfilled or cleaned.

Pinned accepted Contract #329 merge
`4476168c2c63a639f67d7533f319522157c09949` requires amount-based VIP growth in
the gifting scenario. Its existing privilege specification, the inspected
native Go consumers/configuration, legacy privilege implementation and client
projection instead describe monthly-card validity/benefits. The recorded
`dim_vip_level` value is a 0/1 active-privilege value, not monetary progress.
The operator is asked to resolve this concrete requirement difference; no
growth field is guessed and no requirement is silently removed.

The selected original approval `5c25aa58-ef88-4cc7-88f8-017b0d8edfee` explicitly
retains human game-PR merging. The prepared maintenance combination is now
reviewable, and approval to integrate only #368/#369 into the feature draft
branch is requested separately. VIP clarification, that integration approval,
the source direction correction and exact-candidate checks are pending.
Common archive publication remains deferred as instructed; its provenance and
hosted-CI release gate remains open. Real-service acceptance, complete scenario
coverage, UI-ready, Client/closing, game main merges and production-host
qualification remain separate release prerequisites. The running runtime,
private configuration, credentials/accounts, production and parked issues
remain unchanged.

The subsequent new-project correction is committed as
`b1cac0369ed1ff06d12bae9519db8356a1f5a14e`. Read-only review confirms removal
of the historical recharge-statistics compatibility branch and extra
historical-data release conditions. Its replacement regression corrupts the
permanent recharge total/first/last records with a nonzero deadline and proves
rejection before any committed gift effect. Overflow, reentrant first-recharge,
reload, replay, actor/revision and complete local-store rollback checks remain.
This closes the specific source-direction correction described above.

A fresh independent checkout freezes that exact correction and reruns the
same shared recharge/gift/login command. All 18 top-level tests run, producing
38/0/0 test/subtest outcomes in 65.177 seconds. The Go JSON log
SHA-256 is `9be0f044eb138920fe0a3ae2f72047f92d056d5a4e704cb80137386cb369bb2d`. A fresh private
combination of this corrected application and the same complete maintenance
ranges has head `f3bcb4fb4528999a1aa1c755bd93d8b4d3884410`: its full
transaction-scanner race check passes 11/0/0 in
25.624 seconds, and the shared regression plus
configured Food guard passes 41/0/0 in 63.605 seconds.
The latter Go JSON log SHA-256 is
`f9019317d6e4357883412f2cf80ba63495f656f3181874ee61f74c05d1393447`. Both fresh checkouts
stay clean, all tracked fingerprints remain unchanged, and the owned native
Jobs settle with the verified 8 GiB memory guard. Application code, module pins
and generated configuration in the combination match the corrected candidate.

The earlier measurements remain historical. These new results close the
covered local regressions after the direction correction; they do not resolve
VIP's amount-growth requirement, authorize game-PR integration, supply real
Mongo/complete-scenario evidence, publish the deferred archive or certify hosted
CI/UI/Client/production-host readiness. Those gates remain pending.

### Approved maintenance integration and withdrawn interpretation (2026-10-08)

The operator replies in the Codex conversation: "1. 沿用月卡行为 2. 批准".
The maintainer then expands the first answer into a monthly-card validity and
benefits rule. That expansion is a maintainer interpretation, not an operator
decision about monthly-card duration. It is withdrawn by the operator's
2026-10-09 terminology correction recorded below: "VIP 成长" in this gift
scenario means sender cumulative recharge, using the same statistic rather
than an additional VIP money-experience or level counter. Shared first-charge
handling, units, once-only accounting, atomicity and rollback remain required.
Original-thread comment `0c87abe9-652b-4cfa-a2e1-e757d9457cfe`, posted through
the operator's Linear identity on 2026-10-08 at 13:46:18 UTC, contains the
maintainer's expanded interpretation as well as the actual replies. Its
monthly-card expansion and conversation message 34 are retained as history,
but are not evidence of an additional operator design ruling. The Contract
follow-up remains subject to the ordinary human game-main merge gate.

The second answer explicitly approves merging only maintenance
[#368](https://github.com/Kuaiwa-Network/farm-hive/pull/368) and
[#369](https://github.com/Kuaiwa-Network/farm-hive/pull/369) into
`farmbot/farm-1419-followup`. Before integration, the selected TestBot worker
has exited, its owned Jobs are empty and its issue checkout is clean. Fresh
GitHub checks confirm the exact previously reviewed PR heads, complete nine-
and three-file scopes, feature-draft base and actual remote branch head
`b1cac0369ed1ff06d12bae9519db8356a1f5a14e`. The complete two-commit Food scope
is included. The maintenance drafts are marked ready and squash-merged with
exact-head matching: #368 becomes
`a907ca33b4140ea2ba14b29fd1604a3bdbcbb31c`, then #369 becomes
`aee99258a2de5b72edaba38399d42b8567173975`.

The resulting feature-draft Git tree is
`05c3f2edb134e208e39bad588391fc6ac14d670e`, exactly matching the independently
tested private combination `f3bcb4fb4528999a1aa1c755bd93d8b4d3884410` recorded
above. A full tree comparison finds no difference. Its twelve-file difference
from `b1cac036` contains only the reviewed CI/scanner/fixture, Food test and
design-record maintenance; application code, module pins and generated
configuration remain identical. The related native Windows measurements are
11/0/0 scanner outcomes in 25.624 seconds and 41/0/0 recharge/gift/login/F15
outcomes in 63.605 seconds. Those commands ran on the private combination,
not on the new GitHub squash SHA; full tree equality links the measurement to
the integrated content without inventing a rerun.

Both maintenance PRs are verified MERGED. Backend
[#367](https://github.com/Kuaiwa-Network/farm-hive/pull/367) remains OPEN/draft
at the integrated head, and farm-hive main remains
`feee46cbb1336efeb1019638638e11fd7bd76ff0`. No game main PR is merged. Neither
the active worker checkout nor its checkpoint/ledger is manually edited.
The original TestBot session receives the integration report and the then-
recorded interpretation and is observed queued through its normal controller.

This development-PC result closes the two maintenance integration decisions.
The accompanying VIP interpretation is superseded by the 2026-10-09 correction
below; this result does not certify production-host readiness.
Real-service/Mongo execution, complete 29-scenario acceptance and full failure
attribution, current feature UI-ready and client integration, configuration
archive/provenance/hosted backend CI, game main merges and production release
qualification remain separate unfinished gates. The operator's instruction
to defer the Common package remains in force. No Jenkins/config publication,
Feishu writes, credentials/accounts or app settings, production operation or
parked issue is changed by this integration.

### Unresolved native failures compared with upstream main (2026-10-08)

After approved maintenance integration, independent fresh development
checkouts compare exact feature candidate
`aee99258a2de5b72edaba38399d42b8567173975` with freshly verified farm-hive main
`feee46cbb1336efeb1019638638e11fd7bd76ff0`. Both use native Windows, Go 1.25.1,
the pinned native compiler and original offline dependency versions, with
`go test -p 1 -race -timeout 120s -json -count=1` and the same explicit nine-test
selection across seven packages. All nine top-level tests actually run in
each checkout. Dependency downloads are disabled; the tests still perform
their prescribed local/network diagnostic probes. No real or shared Mongo,
production service or player database is used.

| Exact input | Passing | Failing | Skipped | Duration |
|---|---:|---:|---:|---:|
| Integrated feature `aee99258` | 1 | 8 | 0 | 57.343 s |
| Upstream main `feee46cb` | 1 | 8 | 0 | 56.044 s |

The failure sets are identical:

- `internal/mongotest`: `TestLayerHintNamesTheLayer`; the `.invalid` DNS-negative
  precondition unexpectedly resolves on this host.
- `internal/tzprobe`: `TestRunInZoneReflectsRealTimezone`; the child does not
  observe the requested `TZ` offset on native Windows.
- `loginsvr`: `TestBootFailureLeavesCluster` and `TestBootFailsWhenPortBusy`;
  the occupied-port setup does not make startup return the expected error.
- `modules/guildcompete`: `TestComputeSeasonTimesIndependentOfMachineTimezone`;
  the child processes observe only one local-timezone offset.
- `msgrisk/wordlib`: `TestWriteReadCacheRoundTripOnDisk` reports mode 666
  against the POSIX 600 expectation, and
  `TestWriteCacheIsAtomicForConcurrentReaders` fails its concurrent rename.
- `nodeconf`: `TestProbeAnyReachableSharesOneDeadline`; its supposed blackhole
  target returns immediately, so the timeout precondition is not demonstrated.

The six failing packages have no file difference from that upstream main to
the integrated feature candidate. These failures are reproduced on existing
main, not new failure evidence attributable to this feature change. Host
preconditions, fixture portability and application behavior still need separate
diagnosis and any necessary native fixes; this comparison grants no exemption.
In particular, neither startup failure, permissions nor atomic-cache checks
is weakened, and no skip is added.

`modules/friend.TestFriendRemoteReceiverUsesDBAndRevision` passes in both
focused selections. That result does not resolve its original whole-suite
timeout or prove timing stability. The preserved original serial result remains
16,899 passing, 46 failing and 1,290 skipped outcomes; these two focused checks
do not replace that suite or attribute all its failures and skips.

The candidate UTF-8 Go JSON log SHA-256 is
`02e4eff6deaee7ab8e1bab22915c8de7b8008c445cd8c07d0b7e46b5db86fd14`;
the upstream log SHA-256 is
`6779f2d702b0d3b7ff1d663f71ab41dad0a83b3c3e0dfbdd04892da62effd8f4`.
Both checkouts remain clean, every tracked-file fingerprint is unchanged,
and owned Windows Jobs settle with the verified 8 GiB memory guard. Verified
original-thread reports `d9b85aef-b1da-4d32-8638-b7f2c34eb748` and
`4555e002-4a81-43b4-be4d-3d0530c25e2d` give the TestBot this evidence.

The normal controller subsequently hands this same selected issue back to a
fresh Contract worker for the maintainer's then-recorded monthly-card text
correction, whose interpretation is withdrawn on 2026-10-09 below. Its
checkpoint records integrated backend head `aee99258`; the backend issue
checkout is restored cleanly. This observed handoff is not proof that a new
Contract PR or its human main-merge gate has completed. The earlier Common
package deferral and all real-service, full-scenario, UI/client and production
qualification gates remain in force.

### Historical monthly-card draft checks; interpretation withdrawn (2026-10-08)

These measurements apply to the historical draft below. The maintainer's
monthly-card interpretation is withdrawn on 2026-10-09; passing structural
checks did not make that interpretation an operator design decision.

The original selected TestBot session resumes under the then-recorded
interpretation and adopts the approved backend maintenance head
`aee99258a2de5b72edaba38399d42b8567173975`. It restores its backend issue
checkout cleanly and uses the normal controller handoff to a fresh worker
rooted in Farm-Contract. Neither the maintainer nor a backend worker manually
changes the ledger/checkpoint or writes into another active repository root.

The Contract worker fetches actual current main
`acdc15286d79f8d09a3e9183b3f94ea0c150f18e`, including unrelated changes that
have already merged upstream, and prepares exact commit
`a6d898b8181aa6effb3d743e1e7db1e4abd54e9b` on the selected issue branch.
The focused [Contract draft #331](https://github.com/Kuaiwa-Network/Farm-Contract/pull/331)
changes only the existing gifting proposal, design, tasks and gifting delta.
That historical draft uses original-thread comment
`0c87abe9-652b-4cfa-a2e1-e757d9457cfe` to express the maintainer's monthly-card
interpretation and retains shared sender cumulative/first-recharge accounting
at 100 fen per voucher, atomic rollback and order idempotency. The comment's
expanded interpretation was incorrectly attributed as an operator ruling;
that attribution and wording are corrected on 2026-10-09 below. No protocol
field, configuration declaration or economic value is added. All 29 scenario
names and their count are unchanged.

All twelve required native Windows Contract gates return zero at the exact
commit: buf build/lint, breaking-waiver, manifest, markers, message naming,
proto fields, coverage, provenance, README inventory, OpenSpec configuration
and validation. Buf is 1.72.0 and OpenSpec is 1.7.0. Whole-repository OpenSpec
validation reports 52 passed and zero failed. Original UTF-8 per-gate logs,
durations and SHA-256 values are retained privately, and the maintainer
independently verifies every reported log hash, unchanged source fingerprints,
the exact published head/base, four-file scope and sanitized public diff/body.

At the review snapshot, #331 is OPEN/draft; 23 hosted checks are SUCCESS and
five IN_PROGRESS. Those hosted statuses are a measured snapshot, not a claim
that every CI check passed. Contract main merge is still pending the selected
issue's explicit human game-main merge gate: the preceding approval allowed
only backend maintenance #368/#369 into the feature draft. This Contract
draft was published for review and is subsequently corrected below; it does
not establish that
backend stage D, UI/client integration, complete scenario acceptance or
production release qualification has finished. Common publication remains
deferred, and all previously measured Windows failures remain recorded.

### Shared-store wiring candidate independently verified (2026-10-08)

The selected TestBot resumes backend work and updates
[draft #367](https://github.com/Kuaiwa-Network/farm-hive/pull/367) to exact
candidate `1ad3e57630d335c83e1d0afe84444f8e517a9256`. Its 15-file increment from
approved maintenance head `aee99258` binds the ordinary player and gift paths
to the same revision-guarded store, validates distinct receipt/chat collections,
drains admitted gift I/O before module/Mongo shutdown, and checks existing
monthly-card validity through real configuration. Original maintenance merges
remain ancestors; module pins and generated inputs are not changed in this
increment. The Mongo commit/replay/stale-write/late-rollback case compiles but
does not execute its assertions without a replica set.

On this development Windows PC, a separate fresh checkout freezes the exact
published candidate. Independent verification uses native Go 1.25.1, the
previously pinned native GCC/CGO compiler, original offline dependency versions,
`GOWORK=off`, `-mod=readonly`, and `go test -p 1 -race -timeout 120s -json
-count=1`. All service selectors are absent; no shared or player database is
selected. This is development evidence, not production-host certification.

| Independent selection | Passing outcomes | Failing | Skipped | Duration |
|---|---:|---:|---:|---:|
| Complete `./server` package | 275 | 0 | 10 | 92.295 s |
| Exact published gift/config/store/host/mail/chat/F15 selection | 114 | 0 | 0 | 54.769 s |

Counts include subtests. Both owned Windows Jobs settle with the verified 8 GiB
memory guard; all tracked-file hashes remain unchanged and the checkout stays
clean. The UTF-8 Go JSON SHA-256 values are
`72f4768c8062e1f58c39eea98a4f714e9ce685677f05addd8606eb9f7752c388`
and `6925dc455dddba101bb84d52379a3626f87545a2e4e820b0c1f1690cb0c1de5c`.
These new independent results are separate from the worker's same-candidate
275/0/10 in 60.419 s, 114/0/0 in 48.691 s and scanner 11/0/0 in 23.337 s.
The maintainer checks the worker's six published test/build/vet/crosscall log
hashes and exact SHA; its whole-repository build/vet and existing CI callname
crosscall selection return zero. No full-repository test pass is claimed.

Every skip in the independent server run is preserved:

- `TestGuildClusterDefaultsToSingleProcessAfterBoot`
- `TestGuildHostWiring`
- `TestMongoStoreVariant`
- `TestFriendClusterChild`
- `TestFriendClusterRealDispatch`
- `TestGardenGiftMongoBootCommitsReplaysAndRollsBackLateFailure`
- `TestPayWakeClusterChild`
- `TestPayWakeRealDispatch`
- `TestGateChainEndToEnd`
- `TestGuildClusterEnabledIsWiredThroughBoot`

Two entries are private subprocess helpers. The two guild-default/host entries
skip because the shared memory boot fixture does not install the guild host;
the remaining six entries lack real Mongo, Redis or cluster inputs. None is
credited as successful Mongo/cluster acceptance.
The worker's first full-server check at `0e4bed24` fails the unchanged factory
configuration inventory assertion because the new order collection is missing
from the template. It fixes the template at `f49ad35f`; the original 274/1/9
result remains retained. No skip or containment/ownership check is relaxed.

The worker maps all 29 original Contract scenarios to concrete delta,
Requirement/Scenario names, input SHAs, tests and missing acceptance portions.
The maintainer independently verifies all 30 distinct referenced test receipts
against the actual passing JSON events and log hashes at this exact consumer
commit. Every row remains `PARTIAL_LOCAL_PASS`; this mapping does not execute
the outstanding Mongo concurrency, unknown commit/replay, real cross-node or
client/UI behavior. The private JSON mapping SHA-256 is
`04f165731b64f18f59a8220e11e3900695e9c2708b915c3d08fff538932abd4a`.

Fresh inspection of [same-candidate main CI](https://github.com/Kuaiwa-Network/farm-hive/actions/runs/37800749740)
confirms the build fails at the designer pin gate with HTTP 404; it does not
reach the downstream business checks. Four native generation/provenance
workflows succeed. The operator's Common publication deferral continues;
merge of the Common source is not publication or a CI waiver. This draft still
includes unmerged #366 and exceeds the repository's approximately 3,000-line
handwritten-diff review limit, so formal splitting and review remain required
before game-main merge.

The worker settles with the issue checkout clean, no active worker, all owned
Jobs empty, and stage D pending at the closing-input gate. A dedicated,
disposable Mongo replica set, Redis, etcd and NATS environment is still required
for the remaining native Windows acceptance. None of those service executables
or Docker/Podman is found on the selected sanitized native-tool PATH; this is a
PATH capability observation, not a search of production settings. No service,
account, credentials, app settings, workflow rerun or production data is changed.

At this snapshot, [Contract #331](https://github.com/Kuaiwa-Network/Farm-Contract/pull/331)
is still OPEN/draft at `a6d898b8`, with all 28 hosted checks successful. Its
previously measured OpenSpec validation excludes the two unchanged existing
`skip_specs` entries `login-milestone2` and `remaining-protocol-drafts`; 52/52
does not claim coverage of those exclusions. The selected issue's human
game-main merge gate remains pending. Existing Windows baseline failures,
complete scenario acceptance, Common publication/provenance/hosted CI,
UI-ready/client integration, review splitting and production-host qualification
all retain their individual outstanding status.

### Operator terminology correction published (2026-10-09)

The operator asks "vip金额成长是啥 她意思应该就是累计充值吧" and, after
the maintainer explains the proposed terminology correction, explicitly says
"纠正吧". Verified [original-thread source](https://linear.app/kuaiwagames/issue/FARM-1419/好友花圃赠礼开发#comment-d103d57a)
`d103d57a-5f44-4c1b-b906-e24e9e16ee19` relays these actual replies through
the operator's Linear identity on 2026-10-09 local time. The correction is:

- "VIP 成长" here means the sender's cumulative recharge statistic. It uses
  the same record, without duplicate booking or a separate money-experience
  or level counter.
- Gift spending reuses the shared self-purchase recharge path and existing
  first-charge determination, at N times 100 fen for N spent vouchers, once
  per order. The recipient receives no corresponding recharge progress.
- Cumulative accounting does not imply that an ordinary gift opens or renews
  a monthly card. Existing monthly-card goods/reward configuration is not
  changed. The maintainer's former duration/benefits expansion is withdrawn,
  not recast as a separate operator design decision.
- Shared atomic commit, whole-order rollback, retry idempotency and no fake
  channel callback remain required. Original P1/P-06 and revision 236 history
  are retained, not reassigned to a new respondent.

Fresh GitHub inspection confirms the existing
[Contract draft #331](https://github.com/Kuaiwa-Network/Farm-Contract/pull/331)
is OPEN/draft at `a6d898b8181aa6effb3d743e1e7db1e4abd54e9b` before writing.
An independent development checkout prepares a documentation-only correction
on `codex/farm-1419-cumulative-contract-correction`, then fast-forwards the
existing `farmbot/farm-1419` PR branch to
`5416d3576a2901f2a960badab0d26ef08a24758b`. The base remains
`acdc15286d79f8d09a3e9183b3f94ea0c150f18e` and the tree is
`a94f46d960e600b9dbff555a5350f0360c7bdea3`. The title and body are corrected
around cumulative recharge; the old commit and logs remain historical.
Only proposal, design, tasks and the gifting delta change. Protocol, manifest,
tools, configuration and economic values are unchanged; all 29 scenario names
and their count remain identical to the accepted baseline.

On this development Windows PC, all twelve existing README native Contract
commands pass on that exact committed head. Tools are Python 3.13.16,
Git 2.54.0.windows.1, Buf 1.72.0, Node v24.19.0 and OpenSpec 1.7.0; the Buf and
OpenSpec pins are checked against the candidate workflow. Child environments
omit inherited live selectors/authentication and use owned scratch/cache/temp
directories. Native subprocess trees are assigned to owned Windows Jobs before
execution and are verified settled with the 8 GiB guard; Bash/WSL/MXC is not
used. Tracked-file fingerprints stay unchanged and the checkout stays clean.
The existing commands are `buf build`, `buf lint`, followed by
`python -I -X utf8 -B` with `check-breaking-waiver.py BREAKING_WAIVERS
origin/main`, `gen-manifest.py --check`, and the remaining
Python tools named below under `tools/`. The selected Python executable is
used explicitly; private executable and checkout paths are not published.

| Gate | Tool | Exit | Duration | UTF-8 log SHA-256 |
|---:|---|---:|---:|---|
| 1 | `buf build` | 0 | 0.069 s | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| 2 | `buf lint` | 0 | 0.100 s | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| 3 | `check-breaking-waiver.py` | 0 | 3.475 s | `0e8495a6814aed321d4f031d131cf19a83a5d57c18c4d327015f5758b27729c7` |
| 4 | `gen-manifest.py` | 0 | 0.091 s | `506f1aa0ec1d18720a29348621195bcfbe90bb90c4e921e386620b95e6fbf8a6` |
| 5 | `check-markers.py` | 0 | 0.173 s | `86e262f5368050e41ef72e05829fab4345767bffca4a438f76693f14cb2a6751` |
| 6 | `check-msg-naming.py` | 0 | 0.069 s | `462ed55d2b62cb3bf2091cfbfb161acb3d60be89529302fef1a56be9d300f664` |
| 7 | `check-proto-fields.py` | 0 | 0.122 s | `3643f69ebc44674f10fe5c117046a2003f2862ce65bdd08e59061001dffd3d56` |
| 8 | `check-coverage.py` | 0 | 0.079 s | `29786146a45182143693f9e01561f4ba3c2278a65fa94f922a2b8426bf1a23d0` |
| 9 | `check-spec-provenance.py` | 0 | 0.111 s | `1ec7ed86b48eea81824f2a3e5f544d56cd6ef82db4ce300ccf43b50d9d610ee2` |
| 10 | `check-readme-inventory.py` | 0 | 0.058 s | `66c541dc6711646ce95243035b6864c4bbf41a7e420bb65c0ca53923ea1091e4` |
| 11 | `check-openspec-config.py` | 0 | 0.059 s | `c10edd5b26399bc9eeb0f7ac597e2269cbeb789f21b7be5d00eeed2b513ec5a2` |
| 12 | `check-openspec-validate.py` | 0 | 0.701 s | `d1776026e76cbbea7db21155d4c9027c3f43264c625d1cf0f87b6087d3401183` |

Total gate duration is 5.229 seconds. OpenSpec reports 52 passed / 0
failed; unchanged existing `skip_specs` exclusions `login-milestone2` and
`remaining-protocol-drafts` are not covered. Coverage gate A uses the committed
`cmd-registry.snapshot.tsv` as its mandatory universe; optional `farm-common`
is absent, so its drift comparison does not run. No exclusion or skip is added
to pass this correction. Initial gate 3 rejects the temporary clone's Git
object alternates. The maintainer copies all objects into that independent
clone, removes only its owned alternates file, passes `git fsck --full`, verifies
the identical commit/tree, and reruns all twelve commands. The original failure
and first three gate logs are retained; no trust rule is weakened.

Verified original-thread delivery `334ce5d4-a7b4-4294-ad8f-a96a4c0152c1`
supplies the TestBot the exact corrected candidate and asks it to verify the
consumer implementation and update its binding/map through the normal process.
The earlier 29-row mapping and 275/0/10 plus 114/0/0 backend measurements refer
to old Contract `a6d898b8` and consumer `1ad3e576`; they remain unchanged and
do not automatically certify a new Contract/consumer pair. No active worker
checkout, ledger or checkpoint is manually edited. The selected worker is
observed running its backend continuation; this documentation does not claim
that its new candidate or focused check has passed.

#331 remains OPEN/draft and is not merged into game main. Its former 28
successful hosted checks apply to the old head, not this new commit. Complete
scenario/real-service acceptance, the selected issue's human game-main merge
gate, formal review splitting, deferred Common archive/provenance/complete
backend CI, UI-ready/client integration and production-host release
qualification remain individually pending. This correction changes neither
runtime nor app settings, credentials/accounts, service state, production data
or parked issues. Development-PC results do not certify production readiness.


### Contract merge and local Docker real-service acceptance (2026-10-09)

This is the same development Windows PC, not the production Windows host.
The operator reports #331 merged, then explicitly requests local Docker for
Mongo, Redis, etcd and NATS. The former missing local-service capability is
addressed below; earlier skip and failure measurements remain historical.

Fresh GitHub inspection verifies [Contract #331](https://github.com/Kuaiwa-Network/Farm-Contract/pull/331)
merged at 2026-10-08 23:01:40 UTC: head
`5416d3576a2901f2a960badab0d26ef08a24758b`, merge
`fa924fd2f44cf68606ebc61e6ebd32b9b3399cb0`, tree
`a94f46d960e600b9dbff555a5350f0360c7bdea3`. The merged tree matches the
previously verified twelve-gate candidate; only the four recorded documents
change. No new wire/config value or scenario is implied by the merge.

[Backend #367](https://github.com/Kuaiwa-Network/farm-hive/pull/367) is still
OPEN/draft at `15ea9ff29bc355062952ace0ec63c85d0b61b7aa`. Relative to the
previous `1ad3e57630d335c83e1d0afe84444f8e517a9256`, only its gifting design
document changes. An independent, no-hardlink/no-alternates Windows clone
reruns the eight selected config/shop recharge tests with offline dependencies:
11 passing test/subtest outcomes, zero failures or skips, 64.102 s. UTF-8
stdout SHA-256 is
`6f9cd165584fcf97d91b075b63759ff562413dd780db87080c260b8fea5f9d37`.
The worker's separate same-head 11/0/0 in 13.795 s and its verified log hash
`c4f8ba2a2247b4471174a52fcaa6b006b2e762c47ff1c6310d40a9b555ebf717`
remain distinct observations. The eight tests cover actual configuration
recharge, first charge, order replay, overflow/corrupt records, shared buy/paid
delivery and reentrant events. The worker's current 29-row mapping remains
`FULL_SCENARIO_ACCEPTANCE_PENDING`, SHA-256
`fc7c742be94a09e655a88eb91fb2c2a07cc63f144f0ac6e3fe208ce8f814171a`.
Those partial measurements do not sign off all scenarios.

The host is Windows 11 Home x64 build 26200, with 31.8 GiB physical RAM,
existing WSL 2.6.1/WSL2 and an active hypervisor. Docker was absent before
this step. Following the [official Windows installation documentation](https://docs.docker.com/desktop/setup/install/windows-install/),
Docker Desktop 4.94.0 build 241994 is installed per-user using the WSL2
backend. Its official 606.1 MiB installer downloads in 42.069 s; published
SHA-256 `a9814e31049d66156477a86614e83365669677733014ec72f74229623ff3890a`
matches and Authenticode reports Valid with Docker Inc as publisher. Install
returns zero in 23.399 s without elevation, credentials or account setup.
Program files and both actual Docker virtual disks are on the selected
development data drive; the system drive has only approximately 1 GiB free
after installation, an ongoing host-capacity gap. No unrelated files are
deleted. Docker engine 29.8.2 is Linux; its service containers use WSL2, while
FarmBot, Go 1.25.1/CGO race and all test children execute natively on Windows.
No Bash, MSYS or MXC worker path is introduced.

The private disposable Compose project uses an explicit engine pipe and an
empty owned Docker credential configuration. All four containers, the private
bridge and four named volumes have checked project/issue/owner labels.
Published bindings are actually verified as 127.0.0.1 only on fresh ports;
there are no host bind mounts, Docker-socket mounts or privileged containers.
Memory limits total 2,560 MiB, CPU/log limits are set, and restart is disabled.
No Docker login, insecure registry setting or production configuration/data
is used. Private paths, ownership IDs, logs and controller state stay local.

| Service | Measured version | Source identity |
|---|---|---|
| Mongo replica set `rs0` | 7.0.40 | official image `sha256:b6421fd6d1c5ded6377b397d8983e2f82e2100dc5123332dcfda2065a472be5b` |
| Redis | 7.0.15 | official image `sha256:352c1fdadc91926edda08f45aeb3f27f37194c2f14101229c0523a11195c96e3` |
| etcd | 3.5.11 | pinned official archive `e256885e753dc99001335e099d3c2eb8cf21a865a087ee4d7e3665752ae5929a`; binary `fb240485b480e5d91d1504a4c8a78e4a1fe214e64a028acae63bd7a550040399` |
| NATS | 2.10.9 | pinned official archive `0b251614eb2ad18ab499493ee76ede75db4fa60bbdca175aff70a72971e50b19`; binary `7ff038d44a3d889134acf4e1e5fbc0041972a8f01fc0cb84db74ba25caa3a425` |

Redis follows the candidate CI acceptance pin 7.0.15, rather than changing
the separate dev-compose 7.2 pin. etcd/NATS images contain the checksum-
verified pinned binaries. Mongo's internal and external port match so its
loopback replica-set member is reachable from Windows. Actual version and
protocol probes pass for all four services; the native `go run ./cmd/devmongo`
initializer confirms replica-set primary selection in 2.551 s.

Setup failures are retained locally: the first engine probe times out during
initial boot; the first mount assertion incorrectly treats named-volume
bindings as host mounts; official images initially create two anonymous data
volumes; an internal Docker network suppresses host port forwarding. The
maintainer checks actual mount types/labels, replaces only this project's
two anonymous volumes with owned named volumes, and recreates only its own
containers on a normal private bridge with verified loopback publications.
No global prune, safety-check waiver or application skip is used.

Each native selection uses the configured Go executable explicitly:
`go test -p 1 -race -timeout 180s -json -count=1 -run '<selection>' ./server`.
Go environment is `GOENV=off`, `GOWORK=off`, `GOTOOLCHAIN=local`,
`GOFLAGS=-mod=readonly -buildvcs=false`, `GOPROXY=off`, `GOSUMDB=off`,
`CGO_ENABLED=1`; selected compiler/dependency/cache/temp inputs are owned.
`PYTHONUTF8=1` is set before Python. Inherited live selectors/authentication
are omitted, the four service inputs point only at the owned loopback
containers, and Mongo/Redis/cluster required flags are all 1. Native process
trees are assigned before execution to owned kill-on-close Windows Jobs with
the verified 8 GiB guard and are checked settled. Docker infrastructure is
owned independently by its exact IDs/labels. Source fingerprints and clean
checkouts are verified; the selected TestBot runtime is not restarted.

The six service tests actually run: `TestGardenGiftMongoBootCommitsReplaysAndRollsBackLateFailure`,
`TestFriendClusterRealDispatch`, `TestPayWakeRealDispatch`,
`TestGateChainEndToEnd`, `TestGuildClusterEnabledIsWiredThroughBoot`, and
`TestMongoStoreVariant`. The original candidate passes five top-level tests,
including gift transaction commit/replay/stale-write/late-failure rollback.
G5 fails four subtests: `G5_roster_starts_empty`,
`G5_roster_warn_did_not_recur`, `offline_convergence`, `shutdown_order`.
The first three count the Windows stdout/file mirror twice; the fourth
directly sends SIGTERM, unsupported on Windows. Current upstream main
`d4a0acbe8981a812a6173b8e60003323e385e5a9`, frozen independently, reproduces
the identical failures under the same owned services. Its G5 source and Go
module inputs are byte-identical to the feature candidate.

[Test-harness repair #371](https://github.com/Kuaiwa-Network/farm-hive/pull/371)
is OPEN/draft at `f602a3a8e206e29a1a09be074e1d657de5855025`, based on
that exact main. It changes two test files and the existing design document;
production behavior/configuration is unchanged. An inherited private parent
pipe requests the existing `srv.Stop()`; EOF/invalid commands fail, each
child is waited once, and failed/forced cleanup fails the test. The original
pre-stop open-node, login, once-only offline release and removal-before-exit
assertions remain. Counting uses persisted structured files, preserving raw
failure diagnostics and genuinely repeated events. Two added logging tests
first fail; fixed G5, three regression tests and two existing budget checks
then pass. No platform skip, weaker assertion or new CI gate is introduced.

An additional independent clone combines original consumer `15ea9ff2` with
only #371's test/docs commit, giving local verification commit
`e1635662b7b1b442cd6add27ec87ad65055f878b`. It runs all six service tests
plus those regression/budget checks: 11 top-level tests, 18 passing
test/subtest outcomes, zero failures or skips. This combined result does not
relabel the original unmodified `15ea9ff2` as passing G5 or imply that #371
has merged.

| Measurement | Pass / fail / skip outcomes | Duration | UTF-8 stdout SHA-256 |
|---|---|---:|---|
| Original `15ea9ff2`, six real-service tests | 8 / 5 / 0 | 63.908 s | `f52cb285a978a4be1cb1188e97201ff781da095bb3ecfc588bdc43f959b35cbf` |
| Upstream `d4a0acbe`, complete G5 baseline | 3 / 5 / 0 | 86.856 s | `ca285216b37144fd37dc2beadfa92cc6b568ef8ea3d1fe77bba542b1fcb5bafd` |
| Two added logging regressions, before repair | 0 / 2 / 0 | 7.351 s | `050fbc4d27bc82a8a1d3d5d473cc199906b434a872eb6e5c93ae9dad67412c47` |
| Main plus final repair, G5/regression/budget | 13 / 0 / 0 | 13.751 s | `c439c7c2a6e86ae9b4b194f5cdfd6f75d8430957ecd62edac3f4a3b964486911` |
| Feature plus repair, six services/regression/budget | 18 / 0 / 0 | 52.962 s | `4e9e213b5d65a48cf3e3cc336e85c9e5680e3c85e335c01d90d212d928f032ad` |

The five failure outcomes in the first two rows represent four failing G5
subtests and their failing parent, not five independent application faults.
All selections have zero skips; prior full-suite platform/dependency skips
and eight independently reproduced Windows baseline failures are retained,
not cleared by these focused runs. Every stderr log is empty, SHA-256
`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.
Measured host tools are Python 3.13.16, Git 2.54.0.windows.1 and Git LFS
3.7.1 (native Windows amd64, Go 1.25.1).

Fresh [#371 CI](https://github.com/Kuaiwa-Network/farm-hive/actions/runs/37859968354)
reports `windows-devctl` FAILURE before any step/test starts. Its original
GitHub check annotation says recent account payments failed or the spending
limit needs adjustment. This is an externally measured CI-account blocker,
not an executed Windows test failure; it is neither waived nor hidden by
the passing local test result. An account/organization administrator must
resolve Billing & plans before this hosted Windows gate can run. No billing,
account or app setting is changed by the maintainer. The other build is still
in progress at the snapshot; its success is not asserted. Common publication
remains deferred, and the earlier designer-package 404/downstream CI gap
remains separate.

Verified original-thread notice
`41f8fdd8-2099-4fd6-9969-93142fd7556a` supplies the merge identity, exact
original/combined results, #371 and the CI blocker to the selected TestBot
session, with only owned loopback service inputs for continued authorized
testing. Body/parent/operator identity are read back and checked. No worker
checkout, ledger or checkpoint is manually edited. The four labelled test
containers/volumes remain available for this selected task; they are not
production services, and future cleanup must verify the same owner/IDs.

Release prerequisites still outstanding are complete current 29-scenario
acceptance (including Mongo concurrency, unknown commit/replay, retention,
multi-item/reload and client/UI portions), repaired hosted CI execution and
Common archive/provenance, #371 integration and formal split/review of the
large backend draft including #366, selected game-main human merge gates,
UI-ready/client integration and separate production-host qualification.
These development-PC results do not certify production readiness. No
production service/configuration, credential/account, player data, parked
issue, publication job or webhook/app setting is changed.


### Data-drive gift-surface rerun and completed CI snapshot (2026-10-09)

After the preceding record, the selected TestBot resumes its normal
continuation. Its `hive1431-gift-surfaces` check at exact consumer `15ea9ff2`
fails compilation in 24.615 s because the system drive runs out of space:
`compile: writing output ... There is not enough space on the disk`.
No test assertion runs (0 pass / 0 fail / 0 skip test outcomes); the failing
package/build event is not an application regression or a successful test.
Its original UTF-8 log is retained, SHA-256
`2bdb18bfb6de30f4e37e1cd795c777f87ac61b39ee4133a3fcb9bb0044c31f78`.
The checkout is clean, source fingerprints unchanged and owned Job settled.

The maintainer independently repeats the same eight-package selection at
`15ea9ff29bc355062952ace0ec63c85d0b61b7aa` using only owned data-drive
command-local TMP/TEMP/GOCACHE, fixed offline dependencies and the four
owned Docker service inputs with required flags set. The selected command is
`go test -p 1 -race -json -count=1 -timeout 300s -run
'Gift|GardenGift|Monotonic|Revision' ./modules/gardengift
./modules/friendgardengifting ./modules/shop ./modules/mail
./modules/friendchat ./modules/friend/friendstore ./user ./config`.
Package build concurrency is reduced with `-p 1`; test assertions and internal
concurrency are unchanged. Result: **113 passed / 0 failed / 0 skipped**
test/subtest outcomes across 76 top-level tests, 56.514 s; all eight packages
pass. UTF-8 stdout SHA-256 is
`3457553ab5d9421f6fb17a41286e56f398c5648eed737c315960b60345e93c1c`,
stderr is empty with the previously recorded empty-log hash. The native
8 GiB owned Job settles, files are unchanged and the checkout remains clean.
This is the original candidate, without #371's test-harness overlay. The
selection includes memory/unit fixtures; service inputs being available does
not make every passing case a real Mongo transaction or complete all 29
scenarios. The earlier combined six-service 18/0/0 remains separate.

Final checks confirm all four retained containers' actual protocol health,
Mongo primary, exact ownership, named volumes and loopback-only published
bindings. A first final `mongosh` probe omits the custom test port and fails
at its default port; the corrected explicit-port probe passes. That probe
mistake is retained and does not count as an application test failure. The
system drive has approximately 0.7 GiB free; only the maintainer's newly
created CI log is copied, hash-checked and removed from its own workspace to
preserve it on the owned data drive. No production/shared files or caches
are removed and no running configuration/account is changed.

[#371 CI](https://github.com/Kuaiwa-Network/farm-hive/actions/runs/37859968354)
now completes: **build SUCCESS**, **windows-devctl FAILURE without starting**.
The verified GitHub billing/spending-limit annotation still requires an
account or organization administrator. Build-log SHA-256 is
`7ac5ac8f363a3b9ee54e57364563aa4066c2eb9096461fcad1bd4e1e2bc883af`.
Build success is not an assertion that the hosted Windows gate executed.
The earlier #367 designer-package 404 applies to its separate feature pin
and is not cleared by the #371 main-based build. No gate is waived and #371
remains a separate draft pending integration; no game-main merge is made.

Verified selected-thread notices `b63cd300-7e2e-4e02-ae93-6fa2d4a7a6d0`
and `201621b0-8180-4935-aa84-287f16f1e095` supply the disk classification,
command-local data-drive recommendation, completed CI snapshot and exact
113/0/0 result. The TestBot continues through its normal controller, without
manual worker-tree, ledger or checkpoint edits. Complete current scenario
acceptance, deferred Common publication/provenance, hosted Windows CI,
review splitting and game-main gates, client/UI and production-host
qualification remain independently outstanding.


### Merged G5 repair, native repeats and Mongo business scope (2026-10-09)

This remains the development Windows PC, not the production Windows host.
Fresh GitHub inspection confirms [farm-hive #371](https://github.com/Kuaiwa-Network/farm-hive/pull/371)
merged at 2026-10-09 00:56:14 UTC: merge
`739354a053889f59c1e2d52e792958244c881a69`, parent
`d4a0acbe8981a812a6173b8e60003323e385e5a9`, tree
`9362b62927eef29ce3a77c1de7767300802c728a`. The merge tree exactly matches
the previously tested head `f602a3a8e206e29a1a09be074e1d657de5855025`.
Only the two recorded gate-chain test files and existing design record differ.
The remote maintainer branch is already absent after merge; its matching local
branch is removed after verified adoption. The independent checkout is clean.

Original-thread reply `51dd4849-fab0-4ab5-8476-049b545e5466` relays the
operator's actual "merged, continue" instruction and targeted #371 adoption.
The selected TestBot adopts it normally as
`51ed3a3a63d28d6db485b194ffa814845a35e962`, directly after `15ea9ff2`.
Read-only clone-trust checks confirm exactly the approved three-file patch;
tree `31ee98d22950c96464f1c085a11a41eba036bc2f` matches the maintainer's
previous independently tested `e1635662` overlay. No worker checkout, ledger,
checkpoint, runtime configuration or controller is manually changed.

The worker repeats native Windows Go 1.25.1/CGO race checks at that exact
adopted SHA with offline dependencies, owned data-drive inputs, required
service flags and the four labelled loopback Docker services. Logs are strict
UTF-8, hashes and actual JSON test events are independently rechecked;
source fingerprints stay identical, checkouts stay clean and the owned 8 GiB
Job settles. These are separate observations from the maintainer's earlier
overlay runs:

| Selection | Actual test/subtest outcomes | Actual top-level tests | Duration | UTF-8 log SHA-256 |
|---|---|---|---|---|
| service/G5 and regression selection, `./server` | 15 pass / 0 fail / 0 skip | 8 | 158.171 s | `fc7ce98c5d1e5543bd9a3c73b0fd6203209e8a5b5995da05eae1b23d657e810a` |
| gifting/revision plus budget selection, nine packages | 118 pass / 0 fail / 0 skip | 81 | 70.144 s | `f5750c6d3cf2afe321110fecb18f5f61503b890a777dc12cf5c57874a9baf644` |

The first selection actually executes `TestMongoStoreVariant`,
`TestFriendClusterRealDispatch`, `TestPayWakeRealDispatch`,
`TestGuildClusterEnabledIsWiredThroughBoot`, complete `TestGateChainEndToEnd`
with seven subtests, and the three new gate-chain regressions. Its regex also
contains three unmatched names; no execution is inferred from regex text.
The second selection actually runs
`TestGardenGiftMongoBootCommitsReplaysAndRollsBackLateFailure`,
`TestE2EPositiveWaitsUseBudget`, `TestE2EWaitTablesCoverEveryWaitHelper`,
the two gift wiring checks and the previous eight-package gifting selection.
The six intended service tests and two real budget checks therefore run across
the two selections. The second command is `go test -p 1 -race -json -count=1
-timeout 300s -run 'Gift|GardenGift|Monotonic|Revision|^TestE2EPositiveWaitsUseBudget$|^TestE2EWaitTablesCoverEveryWaitHelper$'
./modules/gardengift ./modules/friendgardengifting ./modules/shop
./modules/mail ./modules/friendchat ./modules/friend/friendstore ./user
./config ./server`.

The existing `TestGardenGiftMongoBootCommitsReplaysAndRollsBackLateFailure`
constructs the actual shared Mongo store and checks transaction storage with
synthetic economic effects. Its name does not establish execution of full
server `Boot` or actual `Service.Gift`/configured recharge/reward/mail business.
That distinction remains in the current scenario gaps. The new committed-config
business fixtures below explicitly call actual `Service.Gift` using an actual
MongoStore and independent per-process/per-sequence test databases; they do
not replace complete server/network/client acceptance.

The first new seven-test-file candidate is
`98cff93a3310cd90ef3970a792c38c397aa37882`. Its config-package compile fails
because a refactored handler test retains an unused `gardengift` import.
Worker result is 0 pass / 0 fail / 0 skip test outcomes in 1.221 s, UTF-8 log
SHA-256 `228583d7fd61d9537697f8cd78ab46bb73bbd128b4ae71e59c8ab5d85e3ffcac`.
The maintainer independently repeats the nine-package selection: 62 passing
test/subtest outcomes in the other packages, zero failing/skipped test
outcomes, but config compilation fails and the overall exit code is 1;
93.885 s, stdout SHA-256
`9f799b2373613ebab9b3404ae25325d996160c91e4f97fa750dd6414512ec753`.
Empty stderr has the previously recorded hash. Both original failures remain;
an overall failed build is not reported as a passing selection.

After the normal import fix `a8701348768d8946c9199f27105136bf55e8f290`,
the worker's actual Mongo business selection reports 72 pass / 2 fail / 0 skip,
29.055 s, log SHA-256
`9e05d151d53e9ef64d914bb1c32dcbf1706823b179114e1d09faf79503674a1f`.
The maintainer independently repeats the focused
`TestRealGardenGiftMongoConcurrentOrdinaryPurchase`: 1 pass / 2 fail / 0 skip,
11.039 s, stdout SHA-256
`4fdbfd5819046f96b5a5c120bd3c052369532512bbf3490b803c9dead25dfcb8`.
The failing subtest expects an ordinary COIN voucher self-purchase to add
recharge as well as the gift. The original committed 84001/84002 rows have
COIN(voucher) costs; existing `shop.Buy` prepares recharge accounting for
RECHARGE costs. [The approved gifting scenario](https://github.com/Kuaiwa-Network/Farm-Contract/blob/fa924fd2f44cf68606ebc61e6ebd32b9b3399cb0/openspec/changes/friend-garden-gifting/specs/friend-garden-gifting/spec.md#L44-L47)
specifies the gift's statistics, while [the recorded P1 decision](https://github.com/Kuaiwa-Network/Farm-Contract/blob/fa924fd2f44cf68606ebc61e6ebd32b9b3399cb0/openspec/changes/friend-garden-gifting/proposal.md#L84)
explicitly calls the extra statistics a gifting exception. Reusing the shared
accounting function does not authorize changing ordinary COIN self-purchases.

The worker initially adds that ordinary shop-26 behavior in `6c345558`.
It is not accepted as an application-regression repair or attributed to the
operator's wording correction. Verified original-thread replies
`2f4bc84e-2ca7-473a-a4d7-c3684ec3790a` and
`1d027b80-238d-4e4a-86c9-b7d73845231b` supply source evidence and require
normal-flow withdrawal of that behavior/design attribution, strict separate
gift/ordinary accounting expectations, and retention of actual wallet,
qualification, old-write, atomicity and replay checks. The real Mongo fixture
work and all earlier failures are preserved; no skip or economic assertion
waiver is authorized. Later corrected-candidate results must be recorded at
their own precise source SHA.

Merge does not clear hosted CI. Merged-main checks at `739354a0` are rejected
before execution by the freshly verified GitHub recent-payment/spending-limit
annotation, including the native generator/provenance jobs on all three host
platforms. No Billing/account/app setting is changed or workflow rerun.
The preceding #371 build SUCCESS and #206 Mac/Windows offline CI SUCCESS are
separate earlier jobs; they do not prove these rejected checks executed.
Common archive publication stays explicitly deferred; the feature designer
package 404 and full wire/provenance sync remain independently pending.

Remaining release prerequisites are correctly scoped real Mongo business and
concurrency coverage, true unknown-commit/transport replay, actual gift chat
cross-node/offline recovery and complete 29-scenario acceptance; hosted CI and
deferred Common/provenance; formal split/review of #366/#367 and the game-main
human gates; UI-ready/client work and separate production-host qualification.
The owned Docker services remain available for the selected task. No parked
issue, production service/player data, credential, account, app or webhook
setting changes in this step.


### Actual Mongo business, cross-node delivery and corrected accounting (2026-10-09)

These measurements are from the same development Windows PC, not the production
Windows host. The four previously owned Docker services remain isolated on
loopback. Go 1.25.1/CGO race and test child processes run natively on Windows;
Docker uses WSL2 for Linux dependency containers. Python is explicitly 3.13.16
with `PYTHONUTF8=1`; dependency downloads remain disabled, required service flags
are set, source fingerprints and actual JSON outcomes are checked, and every
owned 8 GiB Job is settled. Private paths, credentials and raw logs stay local.

The selected TestBot consumes both scope-correction replies and normally commits
`7097486b0ba5efa25ca51d71cffe50606414f181`. `modules/shop/buy.go` is again byte
identical to the approved `51ed3a3a` source. Ordinary shop-26 COIN self-purchases
retain their original behavior; the gift uses the approved recharge exception.
The real Mongo concurrency test still requires both 58+18 voucher costs, balance
924, gift cumulative recharge 58 and recharge record 5800, plus replay with no
additional effects. The unsupported ordinary-self-buy recharge-overflow test is
withdrawn with that unsupported behavior; approved gift overflow, first-charge,
qualification, stale-write and atomic rollback assertions remain. Design
attribution is corrected. The rejected `6c345558` expansion and prior failing
tests remain in history; a green result at that source is not accepted as the
product specification.

The real Mongo fixtures call actual `Service.Gift` with committed configuration,
loaded component Owners and MongoStore, rather than just synthetic transaction
effects. They use independently named per-process/per-sequence databases. Cases
include gift versus self-buy/other voucher spend, qualified competing gifts,
multiple-item rollback after late relation failure, permanent recharge record
overflow/malformed rejection, configured first-charge and recipient exclusion,
101-item retention/reload and frozen content. Two further replay cases place an
owned loopback proxy before the disposable Mongo service: after a genuine
successful server commit, it drops the reply or returns one
`UnknownTransactionCommitResult`. Driver retry must keep the same durable order,
one debit/reward/recharge and no repeated frames. No Mongo failpoint, server
configuration change or production data is used.

Actual cross-node gift delivery needs a bounded typed committed-order wake. The
new internal RPC carries only a 64-hex order identity; it uses the existing bounded
friend dispatcher and authoritative `center_node` routing. The receiver requires
the current admitted live actor and a guarded owner turn, then reloads committed
state. It does not accept economic payloads, arbitrary directive names, allocate
an offline recipient or bypass the revision/commit boundary. Regression cases
cover invalid identities, retired/replaced/wrong recipients and receive admission.
The integration test starts two actual native gameBoot processes with the real
Mongo/Redis/etcd/NATS services and tests online increment, offline durable receipt,
relogin full recovery and absence of cold-login incremental presentation. Its
gateHub substitutes the gate wire and installs the two exact test placement
leases. This is server integration evidence, not real Unity/client/UI acceptance.

All initial cross-node failures remain: missing test relation-sequence store at
`a553b142` (0/1/0, 10.530 s,
`903e21d45e38b3bedc6e78ce1cf07c89a53442cc98ff86ab16deb9727b88c76f`),
then missing online increment at `a1c3bc81` (0/1/0, 13.767 s,
`66f839d5644a38e2f78ae4903e72b1d1b9be6c5625a7220c0cf6868eb5190601`)
and diagnostic-only `403f37d1` (0/1/0, 13.762 s,
`e94ce16a9273b70d66964ee51aa017954d530988b449392f77c5f3963d1ecadb`).
The fixture lacked the gate placement needed by the production routing path;
`545e35c5` supplies those owned test leases and verifies a real routed frame before
the gift, without extending the wait budget or removing notification assertions.
That exact selection passes 1/0/0 in 9.972 s,
`f663ee18382bdba1d6388dee37a8061b8b90789de67b1b88c503e53058d7cecb`.

The maintainer independently freezes corrected `7097486b`; the following are
separate from the worker's own results. Counts include actual test/subtest
outcomes, never inferred regex matches. Every selection has zero failures and
zero skips, empty stderr, a clean checkout and unchanged source fingerprints.

| Exact candidate / independent selection | Pass / fail / skip | Duration | UTF-8 stdout SHA-256 |
|---|---|---|---|
| `7097486b`, nine business/notification packages, CGO race | 197 / 0 / 0 | 93.969 s | `d0b01bbc3a389126f4fd556008a969108a2786843e58453b918635fb60e3626c` |
| `7097486b`, six real-service tests, complete G5, gift cross-node, three regressions and two budget gates | 19 / 0 / 0; all 12 specified top-level tests run | 22.966 s | `c02c83fac3852bf9ccca3e834925985789a476ac98c13d61fec4dda059e9c4ae` |
| `2c8c4ad1`, the three newly Mongo-expanded hotload/chat tests | 9 / 0 / 0 | 17.434 s | `0ef979fabec4e3ddb591c5c27e7116f4c4621329ac4df7b0cbc4d62ceb269e6a` |

The first independent command is `go test -p 1 -race -json -count=1 -timeout
300s -run 'Gift|GardenGift|Monotonic|Revision|FriendProtocolCensus|FriendReceive|FriendExact|FriendRoute|FriendSession|Recharge|FirstRecharge'
./modules/gardengift ./modules/friendgardengifting ./modules/shop ./modules/mail
./modules/friendchat ./modules/friend/friendstore ./user ./session ./config`.
The second uses an anchored alternation of the twelve actually recorded server
test names, including all six intended service tests, complete G5 and
`TestGardenGiftClusterRealOnlineAndOfflineRecovery`; it starts only after the
worker's same-PID service selection has settled. The added-test selection is
anchored to `TestRealGardenGiftHotloadInvalidPriceRejectsNewGiftButKeepsCommittedReceipt`,
`TestGardenGiftChatDedupSurvivesHistoryAndConversationDeletion` and
`TestGardenGiftChatConcurrentReplayAppendsOnce`, in config and friendchat. All three
run their memory and actual Mongo variants. The last commit changes only those
two test files; the independent 709 measurements remain attributed to 709.

Full native protocol synchronization commits the merged Contract provenance in
`0eb99cc0`: all 60 consumed snapshots already match
`fa924fd2f44cf68606ebc61e6ebd32b9b3399cb0`, so only the manifest pin changes.
After a fresh independent fetch of Contract main, corrected 709 passes native
snapshot/generated-code reproduction (11.698 s,
`0e130b7a450b301c2dfed4634b2a8ba7dc6f4aa5704a763a9ccd9bbb189fb36a`),
provenance (2.770 s,
`8ca376a590bc8214152d55814be92ef6d9b82d85bb03d4d2012805aeec18d7ef`)
and all 61 registry output bytes (5.234 s,
`8444ad837929550cc614b8fa17a543ac47900d87510d9faad804444ca03a9106`).
The upstream `redeem_code.proto` remains explicitly unconsumed, a non-error NOTE;
no invented target or partial synchronization is used to suppress it.

The worker's first protocol-gate attempt at `0eb99cc0` has exit 1 at registry
`WinError 5` after the snapshot/provenance checks succeed: 18.461 s,
`f70b19f6514fd455cd97b57d8f198d654fa1f1dc0819845c52f13da4557336df`.
It executes zero test/subtest outcomes; the failure is preserved and does not
establish a product assertion failure. The registry passes independently at the
same 0eb source; subsequent worker retries at corrected 709 and final 2c8 also
pass. No skip substitutes for the gate, and no cause is claimed merely from a
successful retry. The earlier 0eb business result 229/0/0 remains historical
because it still contained the rejected ordinary self-purchase expansion.

Final worker candidate is `2c8c4ad14eb85ac12f4daefaad70627989f94431`, tree
`ee71cac47a8f069699ccc3fe5dbc4a13c6121990`. Its own fully recounted native results
are 239/0/0 across ten selected business/service packages (121 actual top-level
tests), 99.794 s, log
`3481d66638eb2a414fae45bad5508abd9a4300199089478f2987aa3bf86f50f7`;
native protocol gates exit 0, 19.934 s,
`9623b566466fff1eea24a493af58d2d414eafdc01bfce01a8e902279bcf88dc1`;
and build/vet, transaction scanner and internal-cluster reproduction exit 0,
65.846 s, log
`38ed9cb48c957a015ff095492a4d51dd1b6d679e0a0e7ea38fc80294aac497ca`.
The latter includes 11 passing scanner test/subtest outcomes, and the existing
25 scanner self-test cases remain distinct from that count. No result is moved
to an untested integration tree. An additional standalone cluster regeneration
at the same 2c8 source initially exits 1 before assertions because Go cannot
create its work directory (Access is denied): 0.444 s, zero test outcomes,
`e459d0c196c25891e459d897b85f38d54c14a91e1741084955fdb999e492ed8c`.
The retained retry exits 0 and reproduces all nine internal cluster protocols
in 0.890 s, `e5215f71523bdf9932303bbdc18d866899ceb90fe50a32d6b57230210a793171`.
Both attempts leave the checkout clean and source fingerprints unchanged;
neither is counted as a Go test pass or a product assertion failure. Original-thread reply
`aa607794-6388-4933-80e0-acbdb8ee8fb1` delivers the independent 709 observations
and current-main drift to the selected session through normal routing; no active
worker checkout, ledger or checkpoint is manually modified.

Fresh GitHub API plus Git remote-ref inspection observes farm-hive main advancing
to `acff471877b9454a8ddae571c37fa0e139035580` by #350, with approved #371's
`739354a0` as its first parent. These tests bind the specified feature candidates,
not an integration with that further main. Normal foreign-work review and fresh
candidate validation remain necessary for any integration. #366/#367 remain
drafts with their formal split/review and human game-main merge gates; this record
does not authorize those merges.

Remaining release prerequisites are the complete 29-scenario acceptance mapping
including real client/UI presentation; formal code review/splitting and any
latest-main integration; required hosted CI after the recorded Billing rejection
and the explicitly deferred Common/designer publication dependency; UI-ready/client
completion; and separate production-host qualification plus explicit release
authorization. These native service results close measured server-test gaps but
do not waive the older whole-suite failures/skips or establish production readiness.
The independent Docker services remain available to the selected task. No parked
issue, production deployment/service/player data, credential/account or app/webhook
setting changes in this work.


### Full Windows suite attempts and native fixture corrections (2026-10-09)

These are measurements on the development Windows PC, not the production host.
The earlier Python offline suite and these backend Go tests are different suites.
This backend run uses native Go 1.25.1, CGO/race, Python 3.13.16 with
`PYTHONUTF8=1`, Git 2.54.0.windows.1 and Git LFS 3.7.1. The four owned Docker
dependencies remain loopback-only, independently labelled, bounded and without
host-directory binds; Linux service containers use Docker's WSL2 engine. Native
test processes retain suspended assign-before-resume, kill-on-close ownership
and the 8 GiB Job limit. Dependency downloads are disabled, required service
flags remain enabled, UTF-8 logs/hashes and source fingerprints are retained
privately, and every attempted Job below settles. No credentials or private
host paths are published.

The frozen `2c8c4ad14eb85ac12f4daefaad70627989f94431` full command is
`go test -p 1 -race -json -count=1 -timeout 300s ./...`, with a separate 900-second
owned-Job ceiling. It is **incomplete and failed**, not an all-green suite.
The local committed Common pin gate passes in 10.802 s (hash
`454a332080b2d61d36fd53b3103f8bb5b3de9f31c840704b1c1a2c022def2b0f`);
the explicitly deferred archive is not certified by that source check.
The first run unintentionally lets existing tests discover inherited Bash/WSL;
it cannot establish an entirely native toolchain. Subsequent batches and focused
checks use an explicit native-only executable search path on which `bash`, `sh`
and `wsl` do not resolve. No Bash/WSL installation or global setting change is
used to obtain a pass.

| Frozen 2c8 attempt | Pass / fail / test skip | Duration | UTF-8 stdout SHA-256 |
|---|---|---|---|
| Initial full command; outer timeout | 5302 / 44 / 5 | 900.197 s | `d3f3aaabadc8d0da1057863a2814c03f32124d947c98d1e26d8f9ad71a1ce9b4` |
| Remaining native batch 1 | 937 / 0 / 0 | 51.665 s | `39529b2fb2566964ce40944ab9406881f415e098eec0e1f4e1e26f350f194e52` |
| Remaining native batch 2 | 1290 / 1 / 0 | 89.143 s | `dff03eec957cb0ff68d272820488ab4f8e60a4d424e7b05a5cd1a054c2ab4c4f` |
| Remaining native batch 3 | 561 / 0 / 0 | 44.968 s | `0d90df5c12f30372a427584a28eaaca3f40fe1bd9fb5d34fecb5a1ec546fb97c` |
| Remaining native batch 4 | 4595 / 2 / 2 | 227.060 s | `04924de809d9a71c7f52bc83a0fd8cfd42c5ccb2eb4ded83b699392481ab7d13` |
| Remaining native batch 5 | 1093 / 0 / 0 | 44.041 s | `6741c5c563fb4fab22ffb175f42365cfe8d527e51c63c99fd66716aa65712c0c` |
| Remaining native batch 6 | 756 / 0 / 0 | 42.476 s | `a29076a02994cc0f664ba388befa8b81dd082f5ed8a844b56c69c292b9d46ccd` |
| Remaining native batch 7 | 827 / 0 / 0 | 38.409 s | `d6049c7e1ea8b45c8beec311034d033c4c90545d8195b124a75bdb35a0c48203` |
| Remaining native batch 8 | 626 / 0 / 0 | 37.739 s | `e2bcce8fde1bb95c09abe2135c70859500196726d57f1d6fe38cf746a122868a` |
| Remaining native batch 9 | 824 / 2 / 0 | 40.218 s | `986ff5384bd896da971de0878aac449a8dd91f00e2a604cf83f047ffb2e35a02` |
| Remaining native batch 10 | 414 / 1 / 0 | 57.814 s | `17aba11db89640309522b76ff2876c2c70f0a3145427cebf36a1121ec921f09f` |
| Remaining native batch 11 | 171 / 0 / 0 | 14.446 s | `9e0931b8da97844aacb9817bad7c80e427164bd5ec946ca6ea7a43b41bf21c3b` |
| Separate complete server package | 292 / 0 / 4 | 69.783 s | `1b50d8b53c31c655365546efcf6ceeade4102eb93d34948673a40c9d01515156` |

The initial run reaches package outcomes for 67 of 191 listed packages; the
remaining 123 packages are attempted in eleven sequential native batches,
followed by server. The eleven batches take 691.029 s, with 12,094 passing,
six failing and two skipped test/subtest outcomes. Initial package events are
35 pass, seven fail and 25 no-test-file skips; those package skips are distinct
from test skips. Packages and tests overlap across attempts, so these counts
must not be added as a unique whole-suite total. Resource-aborted packages leave
tests without terminal outcomes and later tests unvisited. An attempted package
and a zero failed-test counter do not prove that its process succeeded.

At 2c8, the following test/subtest failures are actually emitted; original logs
remain intact, including parent failures and the later-corrected cases.

| Package | Emitted failed test/subtest names |
|---|---|
| `arch` | `TestStartClusterDepsDefaultsPublishURL`, `TestClientCodesTraceToNamedConstants`, `TestImplementedCodesAppearInProtoSnapshot` |
| `cmd/devtest` | `TestExecutePreservesOutputAndExit`, `TestExecuteLongOutputAndSuccess`, `TestPreflightOrchestration/build_failure`, `TestPreflightOrchestration/success`, `TestPreflightOrchestration/test_failure`, `TestPreflightOrchestration/default_off`, `TestPreflightOrchestration` |
| `config` | `TestContentDigestImplementationsAgree`, `TestDesignerNoNarrowTypeOverflow`, `TestGardenGiftDelayedDeliveryDoesNotResurrectEvictedMail/mongo`, `TestGardenGiftDelayedDeliveryDoesNotResurrectEvictedMail`, `TestRealGardenGiftTwoSendersCompeteForOneQualification/mongo`, `TestRealGardenGiftTwoSendersCompeteForOneQualification` |
| `internal/mongotest` | `TestLayerHintNamesTheLayer` |
| `internal/tzprobe` | `TestRunInZoneReflectsRealTimezone` |
| `loginsvr` | `TestBootFailureLeavesCluster`, `TestBootFailsWhenPortBusy` |
| `modules/closefriendteam` | `TestCreateHappyPath`, `TestCreateRejectionOrder`, `TestCreateNonceThreeBranches`, `TestCreateDoCostFailureKeepsTeam`, `TestCreateNonceRejectionOrderCrossCells`, `TestCreateKeepsClaimAndAttemptWhenOutcomeUnknown`, `TestNonceRetryAfterOwnDisbandCreatesNewTeam`, `TestNonceShortCircuitNeedsTeamStillMine`, `TestCreateRiskIsTwoPhaseAndAfterMoney`, `TestAttemptReconcileJudgesByMembershipNotLeadership`, `TestQueuedWakeDuringCreateCannotStealTheClaim`, `TestReconciledCreateChargesExactlyOnce`, `TestLoginReconcilesPendingCreateAttempt`, `TestInviteHappyPath`, `TestInviteRejectionOrder/参赛中不挡邀请（裁决_R25：161_只钉踢/退/散）`, `TestInviteRejectionOrder/目标存在但不是密友_157_/_无档案_2`, `TestInviteRejectionOrder/目标不在线_158（含_notifier_未装配的_fail-closed）`, `TestInviteRejectionOrder/目标已在团_159`, `TestInviteRejectionOrder/重复单据_160`, `TestInviteRejectionOrder/团满_156（在途邀请占名额）`, `TestInviteRejectionOrder`, `TestInviteDuplicateBeatsFull`, `TestCloseFriendCheckSurvivesHighWaterMark`, `TestRecommendFirstStage/满员团照样能推（不占名额）` |
| `modules/friend` | `TestFriendRemoteReceiverUsesDBAndRevision` |
| `modules/guild/guildstore` | `TestEveryWithTransactionCallsitePassesMajorityTransactionOptions` |
| `modules/guildcompete` | `TestComputeSeasonTimesIndependentOfMachineTimezone` |
| `msgrisk/wordlib` | `TestWriteReadCacheRoundTripOnDisk`, `TestWriteCacheIsAtomicForConcurrentReaders` |
| `nodeconf` | `TestProbeAnyReachableSharesOneDeadline` |

Every test skip across those attempts is listed below. Child-only helper skips
are not missing top-level acceptance, while configuration/platform conditions
remain explicit limitations; none is counted as a native test pass.

| Package / skipped test | Measured reason |
|---|---|
| `config` / `TestAdaptFuncItemsHandlesClearCoin` | Generated FuncItem.b_clearcoin is not published/bumped/regenerated. |
| `config` / `TestE2ERealGrantOnlyDishExists` | Committed dish/recipe rows are one-to-one; no grant-only dish exists. |
| `config` / `TestRealRankConfigE2ELifecycleChild` | Child-only witness, executed through its parent lifecycle test. |
| `internal/tzprobe` / `TestRunInZoneFailurePropagationProbe` | Child-only failure-propagation probe. |
| `modules/chat/chatstore` / `TestCorruptZSetScoreNeverSpreads/坏分数=nan` | Redis 7.0.15 rejects the NaN sorted-set score at insertion. |
| `modules/guildcompete` / `TestMachineTZCalendarProbeChild` | Child-only calendar probe. |
| `modules/guildcompete` / `TestMachineTZProbeChild` | Child-only season probe. |
| `server` / `TestGuildClusterDefaultsToSingleProcessAfterBoot` | Shared memory boot does not install a guild host. |
| `server` / `TestGuildHostWiring` | Shared enabled list excludes guild; the explicit guild-enabled wiring case runs. |
| `server` / `TestFriendClusterChild` | Private child helper of the actual friend cluster test. |
| `server` / `TestPayWakeClusterChild` | Private child helper of the actual payment-wake cluster test. |

Direct gift-related failures receive normal TestBot corrections, without edits
to its live checkout, ledger or checkpoint. `015fac94` introduces closed named
ACK mappings for 0–5/default FAIL and synchronizes the earlier config observer's
append/reset/snapshot, which had raced with subsequent actual Mongo gift tests.
It retains race checking and the economic/replay assertions. `14c0bb50` corrects
the strict typed transaction gate to admit the actual literal
`options.Transaction().SetReadConcern(readconcern.Snapshot()).SetWriteConcern(writeconcern.Majority())`.
Snapshot and Majority remain unchanged in application transactions. The gate
still checks the real third argument, driver/package/type identity and callsite
census and rejects locals, helpers, decoys, fake constructors/setters, repeated
overrides, weaker write concern and wrong read concern. Three valid and nineteen
invalid compiling fixtures produce 24 test/subtest outcomes with their parents.

The maintainer independently checks corrected behavior on its own clean frozen
sources. Counts below are separate overlapping selections, not a suite total.

| Independent source / selection | Pass / fail / skip | Duration | UTF-8 stdout SHA-256 |
|---|---|---|---|
| `015fac94` / observer-fixed-business-independent | 208 / 0 / 0 | 114.209 s | `f0c72cb1e4b273105a570e1f9f2c8b7ae9c04a275fbbfad95a2750912aad1996` |
| `015fac94` / observer-fixed-boot-independent | 6 / 0 / 0 | 16.449 s | `14cba3929c8ce985e5031813d27c9f84d3ce4b042de598209cd028804716dd70` |
| `015fac94` / observer-fixed-code-audit-independent | 2 / 1 / 0 | 22.427 s | `70e8405e397089c3d679316b730571451bbc9699155b3a681401a1cfaceb5cdc` |
| `015fac94` / actual two-node gift | 1 / 0 / 0 | 8.220 s | `6ebcfe8129039d8ac0c202d0e168c892fd9b8c951de5b5bb023d3aa007b69295` |
| `14c0bb50` / strict Snapshot+Majority fixtures | 24 / 0 / 0 | 31.915 s | `409901ae9dd59b44a709c4afc39e0799930c48250acd8ad478de9059b09c856c` |

The original full closefriendteam run times out at the 300-second package limit
after many actor query failures. A new isolated process passes CreateHappyPath
at both 2c8 and pre-feature merged `739354a0`, but two preceding real store-panic
tests poison the baseline pool. Native recovery logging tries an uninitialized
TextLog writer even though TestMain installed zap. A small native probe observes
default logging panic versus initialized structured logging success. The
maintainer's separate [farm-hive #373](https://github.com/Kuaiwa-Network/farm-hive/pull/373)
changes only three test files (21 added lines), initializes both relation test
hosts' existing structured facade and requires the next actual relation query
to succeed after an injected store panic. Removing the fix makes that new
regression fail; full friend/closefriendteam packages then pass. Merge
`ce3afdfa17b42b790bc25ed416c45d918b607b59` has the exact tested head's tree
`d94017a2f87e5590c6e339aafe8bbab7ddbd2623`. Its temporary branches are removed.

| Separate maintenance check | Pass / fail / skip | Duration | UTF-8 stdout SHA-256 |
|---|---|---|---|
| #373 / native-panic-fixtures-red | 0 / 1 / 0 | 38.651 s | `c21873cf1695df9358b34bc6855334e46623d609373d429c7ad770cb2d2c1f7a` |
| #373 / native-panic-fixtures-full | 464 / 0 / 0 | 32.355 s | `f08ee3940575293debc2431b49ad7663da6dfc1cd8a885ec3ab6f04fb3f6a0c1` |

TestBot normally adopts those exact three files as
`791bdd6d63f81926e4c4340f1fac36c41d5a5904`; the maintainer verifies their bytes
against merged #373 and recounts the actual UTF-8 worker logs, hashes and frozen
source readbacks. At that source the service selection is 249/0/0 in 115.912 s
(`1d77475f130a467807be44519ce9a71e6d8ed107ef5932674a329b78c075fea8`),
complete relation packages 464/0/0 in 37.679 s
(`f0c3bb53e8bb99632ebae8a8a632aa0a2ed7909cc119112831b0b58c877e19a7`),
transaction gate 24/0/0 in 16.361 s
(`449274643ad08a8f31eeff37d18ada8d8177d94aad1490aba04f0b50a8e34778`),
and build/vet/pin/scanner 11/0/0 in 72.100 s
(`7fa5fad536192974bfb0129fd369ded6dd62bc1678b8ac7b413a9860ecd9fbff`).
Three full native protocol/registry gates and the existing native crosscall
invocation pass. Passing that older callname selection does not establish that
all newly introduced yielding entry points are covered.

The baseline comparisons reproduce the same .invalid DNS, Windows TZ and two
held-port failures at 2c8 and 739 (each selection 1/4/0). The host resolves the
reserved .invalid name to an address rather than failing lookup; no global DNS
change is made. Native Go does not use inherited TZ as those probes assume;
global timezone/time.Local is not changed. Windows IPv4/wildcard bind semantics
do not produce the held-port collision those fixtures assume. A separate 739
selection reproduces both wordlib cache disk/atomic-reader failures, nodeconf's
shared probe deadline failure and guildcompete's timezone failure (0/4/0,
32.973 s, `33be379c0a122b2560ebdded4c4305af585aec36b561691c0b7f5d71be8212d5`).
Those are measured pre-feature failures, not a waiver or an assertion that they
are harmless. Existing Bash fixtures and a hardcoded python3 fixture also fail
on the native-only path. Common-source cache/archive prerequisites remain
separate from these platform assertions.

Initial arch, guild and guildstore runs terminate with ThreadSanitizer Windows
allocation error 1455. The C drive then has approximately 0.23 GiB free. After
the operator reports freeing space, measurements show approximately 27.7 GiB
free and 10.7 GiB available virtual memory. Reattempted complete packages at 791
still have the following outcomes; guild/guildstore fail at process level even
though their emitted failed-test counters are zero. This disproves treating
disk cleanup alone as sufficient. No sanitizer, memory cap or containment
check is disabled, and no unsupported sole-cause attribution is made.

| Native 791 resource follow-up | Pass / fail / skip | Duration | UTF-8 stdout SHA-256 |
|---|---|---|---|
| resource-restored-arch | 574 / 5 / 0 | 91.750 s | `54f571a8a4f7692de85d924d8cae8acab92a280516f7b7b9ab1e8406521e364b` |
| resource-restored-guild | 605 / 0 / 0 | 45.744 s | `817499195c996f7bea25b7d1f5fd3cfdb5ccf9e4aa65dd6d2b7e5a1f00ca3b3b` |
| resource-restored-guildstore | 228 / 0 / 0 | 46.253 s | `b6102cfd8bc074507c529a2d08e457d10e1521677413c91135e28a092c7b2bf7` |
| resource-case-guild | 1 / 0 / 0 | 9.173 s | `198a2d0130b125bc48001c9948acb3b4265ba143823d243a5cf0396d9c083637` |
| resource-case-guildstore | 1 / 0 / 0 | 21.147 s | `b9c69c061a0c9ddacd245a54fb405a61d79d4559d116987b37aafa7e9d0a0478` |
| native-new-arch-baseline | 1 / 2 / 0 | 6.490 s | `34ba0471067958cc6b9190456c81be18f1d5cb49c0dd5586dea3eff1b6ebda07` |

The complete arch package now reaches previously unvisited gates and has five
failed tests: TestPinHTTPFetchRejectsShaMismatch and
TestStartClusterDepsDefaultsPublishURL still invoke Bash;
TestGoTestConsolePackageFailurePreservesPendingOutput hardcodes python3;
TestImplementedCodesAppearInProtoSnapshot rejects the gift Contract's prose
code heading; and TestScannedPackagesCoverEveryYieldingPackage identifies
friendgardengifting and user as newly yielding packages missing coverage.
The last coverage gate passes on 739 under the same native environment; the
two additional executable-name fixtures fail there as well (1/2/0, 6.490 s).

The two interrupted resource tests each pass in a new bounded process:
TestGuildCompeteTalentBonusReadsFrozenSnapshotOnly and
TestMongoNearLimitDocRejectsGrowthAndOnlyStoreLayerCanShrink. Their passing
selections do not replace a complete package run. Unvisited guildstore cases
are further attempted in fresh bounded native groups, with the failed complete
run preserved. The catalog is only a private test-selection diagnostic, not a
new repository roster or CI gate.

| Frozen 791 guildstore tail selection | Pass / fail / skip | Duration | UTF-8 stdout SHA-256 |
|---|---|---|---|
| resource-guildstore-tail-1 | 12 / 0 / 0 | 6.229 s | `aa2b71fe2ba2bdab0822eedb742a63c7275ec16322e853920819c1d1c30cb362` |
| resource-guildstore-tail-2 | 12 / 0 / 0 | 5.959 s | `4e5a00916f2998535024660470edf5518b03b88a8cf272afac77db3c8067cc7b` |
| resource-guildstore-tail-3 | 12 / 0 / 0 | 5.896 s | `99528643fbb602fd6a00a78c94d4b5df43a39049553123a463794d31896523fb` |
| resource-guildstore-tail-4 | 42 / 0 / 0 | 8.931 s | `7eef92904b352e5b079d1f82ad123c6acda30d67929f63e71d71dda999dd3ccc` |
| resource-guildstore-tail-5 | 32 / 0 / 0 | 11.140 s | `d01f477e35ba06c2a0bcafc6f2f3e0d475425f8a999c7dd1aed5b0e328aa2402` |
| resource-guildstore-tail-6 | 14 / 0 / 0 | 6.168 s | `41bb70dffec7b310011a2cb919eeadab41421840777cb2550819f86c4e64b067` |

A guild tail attempt emits five passing tests, then the existing
TestRecommend_PoolSaturatedByFullGuilds fails with a nil TextLog writer while
starting the actual OpsWorker timer. Later selected cases do not run, and the
diagnostic selection assertion stops; this is not a green twelve-case batch.
The independent maintenance [farm-hive #374](https://github.com/Kuaiwa-Network/farm-hive/pull/374)
adds only the same initialized-facade selection and one explanatory comment
in guild TestMain (two lines, one test file). Its existing real recommendation
test supplies the red control; a broader recommendation/OpsWorker/resume/GM/
archive/reconcile/log selection passes after the fix. No timer behavior,
production log setting or assertion changes. Merge
`b6a1c71a670d01fe1c039ff92596af8aeaa21747` matches tested head
`2ee49b94e1ae980eda6815dbb79558fe794e82d5`, tree
`bdc02da0e464a4604a304180b8248f1fa4f5f9ed`; temporary branches are removed.
The normal original-thread reply requests narrowly scoped worker adoption,
not automatic integration of unrelated main changes.

| #374 independent existing-behavior control | Pass / fail / skip | Duration | UTF-8 stdout SHA-256 |
|---|---|---|---|
| native-guild-logging-red | 0 / 1 / 0 | 24.548 s | `c8204fa17d18c8c28fbd3a01e9d9c890a0f45f2d950952f8e56a448413d1f690` |
| native-guild-logging-green | 86 / 0 / 0 | 18.400 s | `73b3269565deffb32dd4c75725d2efe66b94ad3995f616deee7960aee82ea8b9` |

The GitHub observation at published 791 confirms #367 is still OPEN/draft.
The build [original job](https://github.com/Kuaiwa-Network/farm-hive/actions/runs/37878706393/job/113653142314)
executes and fails obtaining the deferred designer archive with HTTP 404 before
business CI. All thirteen Windows/native failed checks have actual Billing/
payment/spending-limit rejection annotations and zero executed steps; triage
is skipped. They are not application-test failures or passing native CI.
The administrator request remains pending; no billing setting, workflow dispatch
or archive publication is performed. Unrelated external #372 remains OPEN and
is neither adopted nor merged by this task.

Normal original-thread replies now scope two remaining technical corrections:
upstream Contract comment formatting only (canonical code heading and N=NAME
rows; exactly 0–5, no WAIT, no fields/IDs/behavior changes), followed by full
native consumer generation/provenance; and actual crosscall coverage of the new
yielding entries without exclusions/debt/skip. The initial `88dd920c` coverage
commit adds the two packages, but its three-gate selection is 2/1/0 because
CI/local/native callnames still need RefreshPlayerState and ReloadCommittedState.
88 is subsequently published as a draft; its older extended lint pass does not
close that consistency failure. Further
worker corrections and protocol follow-up results must bind their own final
SHA; 791 measurements are not transferred to later commits.

Remaining release prerequisites are: corrected upstream protocol and full
consumer provenance; complete yielding-entry coverage with actual native lint;
unresolved native baseline helpers/cache/timezone/network fixtures and a
complete bounded suite result; required hosted CI after administrator Billing
repair and the operator's separately deferred designer publication decision;
formal splitting/review/latest-main integration and human game-main merge of
#366/#367; real client/UI completion and the unmeasured parts of all 29 scenes;
and separate production-host qualification plus explicit release authorization.
The disk-space intervention is completed, while sanitizer whole-package limits
remain measured. These records and small test-host maintenance merges do not
enable production feature mode, certify production readiness, authorize Common
publication or resume parked FARM-1346/FARM-1425. No production deployment,
service restart, player-state cleanup, credential/account or app/webhook change.


### Reviewed callname maintenance candidate (2026-10-09)

TestBot normally adopts #374 as
`69222ceab3887805f58d8a8c4c1be910a88b8b54`, with the exact merged guild TestMain
bytes verified independently. Its focused timer/recommendation selection is
8/0/0 in 12.337 s, log
`a83008975994dc867b66498a82fd6f0308bcc10a13ca636576c50f189fe684bf`.
The combined strict code/crosscall gates are 4/2/0 in 24.533 s: protocol comment
format and canonical callname coverage remain failures at that source. Other
earlier measurement SHAs remain attributed to their actual sources.

The worker's fixed instructions prohibit CI/workflow edits. The maintainer
reviews the concrete four-literal proposal and independently implements it in
`codex/gift-yield-callnames`, outside the active worker tree. The four existing
lines in local/CI commands and msgrisk/playerinfo degradation anchors retain
all old names and add only RefreshPlayerState and ReloadCommittedState. No
skip, new gate, debt entry, permission or workflow behavior change. The final
maintenance source is `fce2a1e6f92415da2dbe9b5c20879c64f0830214`, tree
`18453d20c927bc4cc6bcf334651ea872636b94aa`, on exact normally adopted 692.

| Owned native maintenance selection | Pass / fail / skip | Duration | UTF-8 stdout SHA-256 |
|---|---|---|---|
| `8a77c7f3` / gift-callnames-ci-red | 0 / 1 / 0 | 5.153 s | `f735e36be1c27cbb0274e56369a543192be989ca90a4663fbc83c58791c237a8` |
| `8a77c7f3` / gift-callnames-local-red | 0 / 1 / 0 | 2.914 s | `64202889cb3b83da84771eafcc2d88dfdbbd288987bebf66c5331121d43b083d` |
| `fce2a1e6` / gift-callnames-final-g3-green | 3 / 0 / 0 | 10.555 s | `7a70e51a63c530313c5fb8fc79c0cd17d01ea4dbad335ce16f761b523f6cf15d` |
| `fce2a1e6` / gift-callnames-final-canonical-native-lint | 0 / 0 / 0 | 4.799 s | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |

The two red controls delete the actual CI/local command parameter lines; the
existing relevant source gates must each fail, then exact original bytes are
restored after the owned Job settles. Final G3/package/local checks pass 3/0/0
and the real full-tree native crosscalllint reads the complete canonical
arguments, with exit 0. Zero test outcomes on the lint command are not a no-op
test claim. Exact changed anchor bytes, clean source and unchanged fingerprints
are verified; the Bash degradation harness is not executed or represented as
passed on Windows.

The focused [farm-hive #375](https://github.com/Kuaiwa-Network/farm-hive/pull/375)
targets `farmbot/farm-1419-followup`, not game main, and is OPEN at publication.
It is held until the running hive worker normally finishes its checkpoint and
hands off to Contract; the maintainer must reverify worker settlement, exact
base/head/files/tree and unchanged main before merging this maintenance into
the feature draft. Original-thread reply
`754e6e19-b96b-4a2b-83ed-6d387fd4b06d` delivers those exact source/proof details
and the normal handoff sequence, without editing active checkout or ledger.
The protocol follow-up, hosted Billing/archive conditions, complete suite,
formal review/splitting/human game-main merge and real client/UI/production-host
prerequisites stay pending. A later merged/adopted candidate needs its own
verification; this prepared correction is not reported as already integrated.


### Native guild path correction and yielding-entry integration (2026-10-09)

These measurements continue on the development Windows PC after the completed
disk intervention. They do not qualify the production host. The same pinned
native Go 1.25.1/CGO race, Python 3.13.16 (`PYTHONUTF8=1`), native Git/LFS and
owned loopback Docker dependencies are used. Bash, sh and wsl do not resolve
on the test search path. Suspended assign-before-resume, kill-on-close and the
8 GiB Job cap remain unchanged. Every recorded root test Job settles; source
fingerprints remain fixed during each invocation. Private logs/state and
credentials remain local.

After the normal hive worker exits, the read-only development observer confirms
the selected job's actual root repository is Farm-Contract and its prior Jobs
are empty. Stage and repository are checked separately during handoff.
The tested [farm-hive #375](https://github.com/Kuaiwa-Network/farm-hive/pull/375)
is squash-merged only into `farmbot/farm-1419-followup`, at
`24b43fb8071f71ef59082cb956c718bd887b9d24`, tree
`18453d20c927bc4cc6bcf334651ea872636b94aa`. The exact reviewed base is
`69222ceab3887805f58d8a8c4c1be910a88b8b54`, head
`fce2a1e6f92415da2dbe9b5c20879c64f0830214`. #367 stays OPEN/draft, and main
is verified unchanged by this feature-branch maintenance merge. The temporary
maintenance branches are removed; the worker receives the merge through a
normal verified reply, preserving its prohibition on editing CI/workflows.

Only four existing callnames literals change: CI, the local crosscall command
and the two existing degradation anchors. RefreshPlayerState and
ReloadCommittedState are appended to all prior names. Removing each actual
CI/local parameter line produces its expected red control; restored G3/local
consistency checks give 3/0/0, and the canonical whole-tree native crosscalllint
passes. The final head's checks are 10.555 s (hash
`7a70e51a63c530313c5fb8fc79c0cd17d01ea4dbad335ce16f761b523f6cf15d`)
and 4.799 s (empty-output SHA-256
`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`).
No exclusion, debt or skip is added. These native controls do not claim that
the Bash degradation batch was executed.

The independent fce2 guild selection then reaches 874 passing, one failing and
zero skipped test/subtest outcomes in twelve groups, 125.727 s; later groups
are not attempted. Its TestGuildNameWritersInvalidatePanels failure is also
reproduced on pre-feature main 739354a0 and current b6a1c71. The existing
`/guildstore/` exclusion sees backslash filenames on Windows, incorrectly
counting the Store implementation as a second business writer. This is a
baseline test-host path defect, not a gift application regression.

[farm-hive #376](https://github.com/Kuaiwa-Network/farm-hive/pull/376) changes
one existing test helper (two added lines, one removed): callback filenames
pass through filepath.ToSlash. Typed AST loading, directory scope, writer
uniqueness and cache-invalidation checks remain intact. A compiling extra
business writer outside guildstore still fails the uniqueness assertion; it
is restored before all four existing callback consumers pass. No application
code or production configuration changes. Merge
`6548725eea5cb3a663b001bae19bdbb967e3153e` exactly matches tested head
`47c0831a4033b101e7fc78c3d8b86abac239e36b`, tree
`d74b18a2af307c5fad87cbcbc05da74fd9dba1dc`. Temporary public maintenance
branches are removed, and normal worker adoption is requested.

| Independent source-gate control | Pass / fail / skip | Duration | UTF-8 stdout SHA-256 |
|---|---|---|---|
| Pre-feature 739354a0 natural red | 0 / 1 / 0 | 13.956 s | `92195c58a61b90cbfc1d912ee8717d7feb7fdd451dc4104a14537268374b46c2` |
| guild-source-paths-native-red | 0 / 1 / 0 | 28.158 s | `92991da959e86d2dced767efebc63fa478ecea6a8c00c67a1c1bd16c8e73e24b` |
| guild-source-paths-second-writer-red | 0 / 1 / 0 | 13.655 s | `ce1dd8fc00ca25f07d3b28fffec440d6dbe002d8fe87607978c7b2023e673877` |
| guild-source-paths-all-consumers-green | 144 / 0 / 0 | 15.014 s | `830535404f5de2391e763cba254afef85c3a92103c21c1f1228e0655e456e199` |

The independent guild rerun uses a clean local verification-only commit
`f73c26a8d23dd3d6c416333142a278e97a54beb2`, tree `ac3e61800e575f2283fbdc903f87e89e2ceccc91`. Its feature parent is the
actual #375 merge, and the sole added file change is byte-verified against
the merged #376 test helper. This source is not the live worker's checkout,
and these results must not be relabelled as measurements of a later worker SHA.
A premature helper launch before the preparation receipt existed exits before
starting any Go test; after preparation settles, the actual measured run starts.
No active worker source, ledger or checkpoint is edited.

| Frozen independent guild group | Pass / fail / skip | Duration | UTF-8 stdout SHA-256 |
|---|---|---|---|
| Group 1 | 58 / 0 / 0 | 6.210 s | `61b255a8adecf7cded2a5829d74da080ed4adbec4f4194f2e9efae3d539f62e5` |
| Group 2 | 57 / 0 / 0 | 6.171 s | `62f848feb24827480f524efa347b18fc84ecd04cc146b637ef80abe78988a358` |
| Group 3 | 48 / 0 / 0 | 10.827 s | `1cb340cf60808676076c1c5f8f6ba7c1fd7330490c3e4462fd70b1185f5dd0d0` |
| Group 4 | 71 / 0 / 0 | 12.682 s | `4d5c9577885d4d001d0cc43e63269dfd3767fa82f2a3f659cd8799e56eaa2f02` |
| Group 5 | 68 / 0 / 0 | 14.878 s | `9239ef6f8088e868b8a35ec6a3ea520cf7f94ed4a008150bd168e38a91ee96cd` |
| Group 6 | 82 / 0 / 0 | 6.086 s | `0080823db849e905966205c7dc9b21506002f3aadeff9344d0b596bdbd1980f3` |
| Group 7 | 222 / 0 / 0 | 14.045 s | `d658e2fe5072e63e37dcced26d9e8ad48fc569ac1d200e07eb558078442b0600` |
| Group 8 | 53 / 0 / 0 | 10.071 s | `d4b22249b1d0defb3c2c054d1ea4731397648cf49754c7ddf7dab5749b0bda0d` |
| Group 9 | 56 / 0 / 0 | 12.814 s | `0d42a6dd2ed24ddd9e50fd054c6258e5374a4eb02c165abe608ae0d3d2d81091` |
| Group 10 | 48 / 0 / 0 | 9.386 s | `8a845415d12b9b867d3394ac15595ab2c369b02e78f695d26388d5e619bd799d` |
| Group 11 | 55 / 0 / 0 | 10.918 s | `09a6ca937f971f567ad17e16f5fec23a0408785d7e5b84e5c92bc2590bd1f51a` |
| Group 12 | 57 / 0 / 0 | 9.599 s | `7447883239a8000092587c15af82260133ae0b8957e1386442057fa4f4c7ccba` |
| Group 13 | 48 / 0 / 0 | 6.138 s | `f41b0842490656f22b8c63839018b061eba9431dc55e79d02de4f003eb562b9e` |
| Group 14 | 48 / 0 / 0 | 6.474 s | `f899eef222f90bee7da1c960d781bb756285f8e35f37d0099d8f6132b5525542` |
| Group 15 | 69 / 0 / 0 | 6.746 s | `d7ee65bbb07009d3687e33a563eb0ebb630832c6d891af9f5a07971f17e21850` |
| Group 16 | 71 / 0 / 0 | 6.085 s | `f1fc01c3225f1af8129179884c0f0c63743e70ba932fb5096eea955784fe9d9a` |
| Group 17 | 51 / 0 / 0 | 9.474 s | `7ce664b0e14c22a3f14dce293160b740a553f2b0264d052b04f884d353e5d754` |
| Group 18 | 50 / 0 / 0 | 6.159 s | `eeb58305d53a11f2ff66167662b049c438ba189a4554ddeefc25be3c84503093` |
| Group 19 | 65 / 0 / 0 | 11.006 s | `496034529c378687b76fd5d1fb3de08db9c9f2fe9ae9b678a1a7003330277b4b` |
| Group 20 | 30 / 0 / 0 | 6.419 s | `13072e9b145f42506769d908e05c19e295cec6be15bd4a5101c275b9324c3ddc` |

Measured run: 1307 pass / 0 fail / 0 skip, 187.666 s; completed=true. All 929 actually listed top-level guild cases run in fresh groups; test/subtest counts overlap earlier selections and are not added to them. A fresh-process grouped pass, when present, is distinct from a complete
single-process package pass. The earlier whole-package sanitizer error 1455,
failed suite attempts, baseline failures and all platform/test skips remain
recorded. No memory-limit or containment change is made to obtain a pass.

The selected TestBot continues its normal upstream comment-format-only work
and fresh consumer generation/provenance. Its measurements must bind the
resulting exact SHA. Public hosted CI is still a separate prerequisite: the
earlier actual Billing rejection/zero-step evidence remains pending an
administrator response; local maintenance merges do not repair that account.
The deferred designer archive remains outside this task's publication scope.
Formal split/review and human game-main merge gates for #366/#367, remaining
real client/UI scenes and production-host qualification plus explicit release
authorization remain. No production service restart/deploy, credential/account
setup, player-state cleanup, app/webhook edit or parked-issue resumption occurs.


The fresh Contract worker publishes the separate
[Farm-Contract #332](https://github.com/Kuaiwa-Network/Farm-Contract/pull/332).
Independent review verifies exactly the approved code-comment block and its
generated manifest row; no fields/message names/IDs/0-5 meanings or WAIT rule
change, and the original 29 scenarios remain byte-identical. On clean head
`4f4e40611978a36963e5f17ad9fda9e2b4a7acb0`, base
`6d594d1f74e8cc04563d84b0a33288a3fee199c1`, all twelve existing native
Contract gates pass independently in 4.310 s. Buf is 1.72.0, OpenSpec 1.7.0,
Node 24.19.0 and Python 3.13.16; OpenSpec is 53 passed / 0 failed, breaking
diagnostics/waivers are 0/0, and optional Common drift remains unmeasured.
Existing skip_specs declarations are unchanged. Each Job settles without Bash,
MSYS or WSL and tracked-source fingerprints stay unchanged.

Under the operator's standing authority for small maintenance PRs, #332 is
merged at `3699c93c51b83e6d962518c5d7571e51d33f18fc`, exact tree
`b305b8026abd51172a159ede86882a9ec7c8b920`. The worker business branch is
preserved. A normal original-thread reply reports the real merge and requests
normal checkpoint/fresh hive handoff, full native generation/provenance, scoped
#376 adoption with #375 retained, and checks bound to the resulting exact head.
This completes the upstream format correction, not consumer integration or
Stage D, hosted CI, game-main, client/UI or production acceptance.


### Current release gates and post-merge protocol candidate (2026-10-09)

The remaining prerequisites above are three acceptance groups, not a count of
missing FarmBot features: finish the selected real Code later-stage journey,
complete a separately selected document-driven UI journey, then qualify the
intended production Windows host and obtain deployment authorization. The
earlier screenshot-driven UI acceptance and implemented offline behavior remain
recorded; the substantive document read alone does not certify a UI worker
journey. There is no measured release date or percentage-complete claim.

At 2026-10-09 04:47:15 UTC, FarmBot's own latest main is
`605748ba9e2bbe334c1b90294c924cfb4db862c0`, the merge of
[FarmBot #210](https://github.com/Kuaiwa-Network/farm-linear-agent/pull/210).
Git comparison against the active development runtime
`a7c335bf64ad5c8954142622b79f26baea333f62` shows no changes under agent, tests,
skills, scripts or .github. This comparison does not restart or deploy anything.

| FarmBot #210 hosted offline check | Status at observation | Conclusion |
|---|---|---|
| Offline / macos-latest / Python 3.13 | COMPLETED | SUCCESS |
| Offline / windows-latest / Python 3.13 | IN_PROGRESS | pending |

These are FarmBot's checks. The independently inspected game-repository
Billing/zero-step rejections and the deferred designer archive 404 remain
separate findings. They must not be presented as a Billing failure of FarmBot's
own Windows check, or as new missing FarmBot implementation. No rerun or account
setting change is performed.

The operator's direct message identifies the human merge of
[farm-hive #367](https://github.com/Kuaiwa-Network/farm-hive/pull/367).
Fresh GitHub/Git verification binds it to merged main
`4965825f17d7be3da1e894ee27cea51a9eb0222c`, tree `fa1095b738c7c46cce5107d516bbeaa1a846b427`,
parent `6548725eea5cb3a663b001bae19bdbb967e3153e`; its public reviewed
head is `24b43fb8071f71ef59082cb956c718bd887b9d24`. The follow-up branch
was deleted by that merge. A fetch including the now-deleted branch fails;
fetching main alone succeeds, and ls-remote confirms the branch is absent.
This is a branch-lifecycle finding, not an authentication failure. The old
[farm-hive #366](https://github.com/Kuaiwa-Network/farm-hive/pull/366) draft
is still open at the observation; it is not blindly merged or closed.

The #367 merge retains the earlier gift protocol comment snapshot. A normal
original-thread reply reports the actual merge and deleted branch, requests
preserving generated work and normal main-based review/checkpoint handling,
and does not edit the worker's source, checkpoint, ledger or publishing target.
The worker prepares a clean main-based candidate on
`farmbot/farm-1419-followup-2`, head `49982e3427132dc5a2c0b4b8bb233270cceecc31`, tree `aace01078cdc7b7aee7e0bd136a92fd6c88018cb`.
Its sole parent is the actual #367 merge. Exactly three files differ: the
generated gift protocol, generated protocol manifest and gift design document
provenance. The generated files are byte-identical to the independently tested
0cafeff7 consumer candidate. Full native regeneration on the new worker head
returns exit 0 in 10.954 s with unchanged fingerprints, clean source and a
settled owned Job. Its UTF-8 log SHA-256 is independently verified as
`4b1ae5797dd55f4f18eb65c4034d757f5eb3a0743c4a446d492e7b051c9338e3`. It consumes the merged
[Farm-Contract #332](https://github.com/Kuaiwa-Network/Farm-Contract/pull/332)
source; no protocol wire fields or business behavior change.

The new exact candidate is frozen in a separate owned development checkout.
The earlier 0cafeff7 checks are not relabelled as measurements of merged main.
Independent native checks of the new head use the unchanged Go 1.25.1/CGO
race setup, Python 3.13.16 with PYTHONUTF8=1, pinned dependencies and native Git.
Bash, sh and wsl do not resolve. Suspended assign-before-resume, kill-on-close
and the 8 GiB Job cap are preserved; every Job settles, with clean source and
unchanged fingerprints.

| Independent post-merge candidate check | Pass / fail / skip | Duration | UTF-8 stdout SHA-256 |
|---|---|---|---|
| Six existing strict architecture gates | 6 / 0 / 0 | 27.179 s | `55834906f321af18f916e9d8b8ece6a84a807fc5fb2b123f50e4810c09ee5d84` |
| Canonical whole-tree native crosscalllint | exit 0 (not unittest counts) | 14.365 s | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |

At this checkpoint the new candidate's publication/review/merge and later
Client/Unity, write-back and archive steps remain pending. No complete game
suite pass is claimed. Earlier failed attempts, sanitizer error 1455 and all
test/platform skips remain recorded; grouped selections do not certify one
whole-package process or the production host. Private credentials, host paths,
logs and state remain local. No production deploy/restart, account or app
configuration, deferred archive publication or parked-issue resumption occurs.


### Exact protocol merge and read-only UI inputs (2026-10-09)

The selected worker normally parks at its closing checkpoint and exits; the
read-only development observer verifies that its owned Jobs are empty before
maintenance publication. Under the operator's standing authorization for small
reviewed corrections, [farm-hive #377](https://github.com/Kuaiwa-Network/farm-hive/pull/377)
is squash-merged at `dab1fb3745af7fbcd75469d41a860d5313474720`. Its sole parent is
`4965825f17d7be3da1e894ee27cea51a9eb0222c`, and the actual merged tree `aace01078cdc7b7aee7e0bd136a92fd6c88018cb`
exactly matches independently tested head `49982e3427132dc5a2c0b4b8bb233270cceecc31`.
The three-file diff changes protocol comments, the generated manifest/source
pin and design provenance. The protocol's non-comment lines are identical.
No business code, wire field, scenario, runtime or production setting changes.
The remote `farmbot/farm-1419-followup-2` branch remains present at observation.

The previous section binds independent six-gate/native-lint checks to the
reviewed head. The following additional worker results are independently
verified against their original UTF-8 log hashes, clean source, unchanged
fingerprints and settled 8 GiB Jobs on that same head. They are not relabelled
as new runs on the squash SHA or summed into a whole-suite pass.

| Source-bound native worker selection | Pass / fail / skip | Duration | UTF-8 log SHA-256 |
|---|---|---|---|
| main-gift-services | 249 / 0 / 0 | 135.560 s | `14faeaf501468902467aa72bce892d2eb4502c36db06745532412a9a894c5527` |
| main-transaction-gates | 24 / 0 / 0 | 19.562 s | `45f8aa8eb1dd07c8c0f3badc908b8f610dabe861cdd56246f51a9bcb1838c5ab` |
| main-guild-path-gates | 2 / 0 / 0 | 15.379 s | `bf6395a52122491b1c361c50fb5f8dae779d6d0d2082221e7f519893a74a1295` |
| main-build-gates | 11 / 0 / 0 | 89.926 s | `90a56ce97bfcb99d85010020c722d257c3226bac72e4fd88522bd5d678b23e90` |

A verified normal original-thread reply reports the actual merge and requests
normal main/source checks before subsequent work. It preserves the merged-PR
review rule, Common publication deferral and all UI-ready/client/human gates.
No active source, ledger, checkpoint or issue branch is manually rewritten.
The older #366 draft is not blindly closed or merged.

At 2026-10-09 13:32:51 +08:00, both macOS/Python 3.13 and Windows/Python 3.13
hosted offline checks have completed SUCCESS on each of
[FarmBot #210](https://github.com/Kuaiwa-Network/farm-linear-agent/pull/210) and
[FarmBot #211](https://github.com/Kuaiwa-Network/farm-linear-agent/pull/211).
The latter merged main is `55cebefddaeae2252fcb3cb81aa7c19a2728ca37`. Its agent/tests/skills/scripts/.github
paths remain identical to the active a7c335bf development runtime. This closes
the previously observed in-progress Bot CI state; it does not deploy that main.

Game CI remains separate: #377 has seven Billing-rejected checks with zero
executed steps, while build executes thirteen steps and fails at the designer
pin gate on the deferred archive's HTTP 404/exit 2, before downstream Go checks.
The independently preserved build failure log SHA-256 is
`086a64d08205af7ad37ecdda118eb5acd6cb1879666de1a7505b36070af3712d`.
Neither failure is treated as a passing game gate. No Actions rerun/dispatch,
Billing change, Jenkins or Common archive publication is performed.

Existing owned read checkouts are inspected without refresh, hydration or
mutation. Their heads match the current GitHub defaults: farmgui
`86cd4640616a3434006fe87379060d7bb874746c` and Farm-Client
`9f0eb670ddd5ce79535adf532ff80ef30e773efe`. All six previously named source descriptors
are present with matching package entries: GiftGardenView, MailPanel,
MailContentPopup, ChatGiftMsgItem, UnlockPopup and RewardPopup. The Activities,
Mail, Chat and Common client descriptor exports are still LFS pointers in this
specific read checkout. This is a representation/readiness finding, not proof
that Git LFS or real exports are unavailable on the host. It does not certify
their binary identities, dependencies or UI behavior. The three-step gifting
popup/success-state component identities still need their named UI-owner
confirmation. Existing components and the earlier FARM-1396 screenshot approval
are not inferred to approve FARM-1419's UI-ready gate.

Descriptor-only availability is then resolved without changing those read
checkouts. Mail and Chat are hash-verified in the existing development LFS
cache and pass the bounded v7 package parser. Activities and Common are absent
from that particular cache and do not match the two inspected retained export
receipts; this is not reported as global host unavailability. Using the already
approved origin-scoped native Git/GCM route, their two committed descriptors
are fetched into a new owned isolated bare repository/cache in 2.780 s. Each
native helper Job settles under the unchanged 8 GiB cap. No new credentials,
host settings, production resources or existing checkout files are configured.

The initial local helper imports an older checkout's agent package and stops
at the parser after the successful fetch. Parsing resumes in an explicitly
selected current-runtime child, without a second download. Original logs and
hashes remain preserved; this is a verification-helper import defect, not an
application or LFS-transfer regression. All four byte streams match the
committed pointer hash/size and expected source package ID and pass v7 parsing:

| Package | Descriptor bytes | Package ID | Committed pointer SHA-256 |
|---|---|---|---|
| Activities | 66787 | `fvyctcfd` | `6c0c22fc5a6b968c2765c3dabd8ea026471095df4971b58f76befee41d7c47cd` |
| Chat | 24997 | `gnqvkylv` | `742a57f448adaaa8e7e40101d8795ee0bb7354e7524b00ba4065fb99186af363` |
| Common | 146430 | `cmcommon` | `5bf06b43e0da172944e3423c1977622c7d60501b4bf7c4b54b53f62dd52dbfe4` |
| Mail | 6348 | `83fhzknh` | `9dd50e428800265615eed7c3746d1b4a342090862532249e437228bf9b9de59d` |

This verifies descriptor format/package identity and declared dependency
inventory only. The full atlases/dependency assets, component payload behavior,
current-task human UI-ready confirmation and new popup IDs remain unaccepted.
Existing read checkout files are still pointers; they are not silently hydrated
or used to bypass controller checks. The resumed Code worker reports no new
source diff, confirms the actual merged main and preserves D/E/F/G pending at
its normal closing checkpoint, with owned Jobs empty. Its fresh main CI report
has sixteen Billing-zero-step jobs, distinct from #377's seven PR jobs; deferred
archive publication and full game acceptance stay pending. A concrete question
about current gifting UI completion is presented to the operator; no UI approval
or role/scope change is inferred from the earlier screenshot trial.

Remaining release groups stay unchanged: the selected Code later-stage journey,
a separately scoped substantive-document UI worker journey, and intended
production-host startup/exit/recovery qualification followed by explicit
deployment authorization. Historical failures, platform/test skips, sanitizer
error 1455 and interrupted full-suite attempts remain recorded. No release date,
whole-suite pass, UI-ready acceptance or production certification is claimed.


### Human UI dependency and new document-driven scope (2026-10-09)

The operator confirms that [FARM-1419](https://linear.app/kuaiwagames/issue/FARM-1419)
art/UI is unfinished. Normal attributed reply `f1fb15dc-7d5f-46f9-9b40-7c6a98466002` in the
existing development thread conveys that input, with exact remote read-back.
The real worker acknowledges it and parks at `ui_ready`; its worker process is
absent, recorded owned Jobs are empty and the issue worktree is clean. Completed
Contract/service work is retained. Client, closing Unity, resynchronization,
write-back and archive remain pending. Same-named source components or parsed
package descriptors are not a replacement for the missing human/UI delivery.
No service restart, production change or state cleanup occurs.

Read-only candidate checks reject automatic continuation of already implemented
work. The operator then approves a new, narrow
[FARM-1104](https://linear.app/kuaiwagames/issue/FARM-1104) UI trial: place the VIP
badge immediately after the nickname in leaderboard list rows and the self row,
using existing art. Titles, podium layout, gameplay, Contract/server work and
Common configuration remain outside that scope; it is not whole-card acceptance.
Current owned read checkouts match farmgui main
`86cd4640616a3434006fe87379060d7bb874746c` and Farm-Client main
`9f0eb670ddd5ce79535adf532ff80ef30e773efe`. Existing
[Client #1254](https://github.com/Kuaiwa-Network/Farm-Client/pull/1254) is merged;
pre-intake checks find no open matching issue PR/branch, current delegate or
development job. Existing main still uses a fixed list-badge position, while
the card's later human feedback requests adjacency to the nickname.

The issue's original wiki link resolves to a short directory document, not R4.
Native read-only resolution identifies the actual attached
`排行榜_系统策划案R4.docx`; its direct wiki link is supplied in the new human scope
comment so the worker need not browse unrelated wiki nodes. Root verification
uses the existing `farmbot` bot profile and creates no credentials or profile.
All commands start suspended, bind to an owned 8 GiB Job before resume and settle
with no descendants left. No inherited FarmBot selector or credential override,
separate Windows HOME, Bash, MSYS or WSL route is used. Profile-file metadata
remains unchanged. UTF-8 payloads and hashes remain private.

| Native candidate read | Exit | Duration | UTF-8 stdout SHA-256 |
|---|---:|---:|---|
| directory fetch | 0 | 2.889 s | `01d048b9b201bcc0334565c9088a2a4871c53791bbd171e0dddaba7582cba7e4` |
| wiki | 0 | 1.931 s | `f735b814ff85dee32b9613d2b0f6c30aabf1d9ff7906e67b79f2dbc1df6f3170` |
| nodes | 0 | 2.024 s | `68962606597e61e7c1b029b8da9a01bb1a86f15c68309fd483a403d408ec3b1d` |
| download | 0 | 3.397 s | `aaa57260e0bd5a2d63ce26e9b48a33ce761f16ab864e7952a944cedff6f5522b` |

The original DOCX is 1,229,520 bytes, SHA-256
`2fa928fb96ca667c694b674c85a8047f8891f7d93d2bf079e85aca7a7ef7ba58`. Bounded OOXML body/table reading yields
217 nonempty paragraphs, 13 tables and 14,914 UTF-8 bytes, SHA-256
`cd17e04ff8df80ee6ff0400ef5da7ede2273dcd7c22cfe99d256fd6c04c5f651`. This confirms substantive text, not image/layout fidelity
or intake by the actual UI worker. The archive is unchanged; no ZIP extraction,
Office install or credentials/configuration change is needed. An initial helper
preflight refuses a PATH exposing `wsl.exe` before any Feishu command starts;
the rerun selects only native executable directories and preserves that refusal
as helper evidence, not an application regression.

Human scope comment `0684528c-839b-4a7d-87d6-36f012c2d4af` and the existing Bot/UI label authorize
only this selected card. API delegation creates exactly one real TestBot session
and one `fgui` work item through the existing signed webhook; no duplicate
session or synthetic event is created. The existing development runtime/profile
is unchanged. A subsequent real-worker observation verifies substantive
document/upload intake below. Source authoring, new visual approval, explicit
export request, certified Client handoff and Unity evidence for this scope remain
pending at this observation. Earlier FARM-1396 evidence is not
relabelled as this new trial. FARM-1346 and FARM-1425 stay parked, deferred Common
publication stays deferred, and production-host acceptance remains separate.


The actual native `fgui` worker now performs the wiki resolution and bot download
itself. Its private original DOCX matches the independently read R4 file above,
including its 1,229,520-byte size and SHA-256; bounded re-reading confirms
all 217 nonempty paragraphs and 13 tables. Its actual saved text is
15,129 bytes, SHA-256
`9f0e5f8d41fb245d6aa2f0e8592ceedac0ccc4e3479522f562095ae4ac799171`. Every nonempty original paragraph is present.
The worker preserves a different blank-line/newline representation, so this text
is not claimed byte-identical to the root verifier's conversion. Document images
and rich layout are not certified by those text checks.

Its real `download-uploads` manifest records seven successful artifacts: six PNG
images and one MP4, with no download failures. Independent reads verify every
recorded size/SHA-256 and unchanged file metadata. The original manifest SHA-256
is `04f7f2f16fe2728ad269c12d62038791426a73c00a0e5e1e010e3bc76f998293`. Those inputs remain private and are not committed
or silently counted as visual approval. The worker is measured using
`gpt-6.1-sol`/`xhigh`; its root and every observed descendant belong to the owned
native Job, and the development controller reports zero loop errors. This now
establishes substantive linked-document intake in a real UI worker. It does not
complete authoring, the new visual round, export/Client delivery or production
acceptance. Any later source, art or PR-head change invalidates prior round proof.


### Document-driven leaderboard source and visual round 1 (2026-10-09)

The selected native Windows TestBot UI job advances from verified R4/upload
intake to authoring. [farmgui #150](https://github.com/Kuaiwa-Network/farmgui/pull/150) is an open draft at
`f6dbab2572e70bc2a150a305deb9b22a2d2234e2`, tree `94044413279fee79063ee2ae11eb0662989a4c7b`, based on
`86cd4640616a3434006fe87379060d7bb874746c`. Independent native Git/GitHub reads verify exactly
two changed files: `assets/Leaderboard/Items/LeaderboardRankItem.xml` and
`docs/farm-1104-leaderboard-vip-ui.md`. Whitespace checks pass. No image content,
package registration, stable child ID/name or Client C# is changed; title/podium
and whole-card work stay outside the human-approved scope.

The component retains the existing font size and VIP art, moves both owner-state
nickname origins left by 18 source pixels and relates the badge to the nickname's
right edge and vertical middle. The badge uses the existing 41-by-44 image with
an 8-pixel logical gap. Existing `SetVip`/`KeepRankAboveName` bindings are retained.
Worker evidence reports passing package-cycle/export/ID lint, text color/ink-box
checks, unchanged child identities and referenced registrations. Sixteen static
geometry cases cover other/self, short/six-CJK/12-wide-ASCII and VIP/non-VIP.
The widest printable ASCII case has an estimated 7-pixel visible-ink gap to the
right label. This depends on the checked font and the current input-code fallback
limits; actual GlobalInfo values, special characters, Unity glyphs and pooled
row reuse remain unverified. These are static estimates, not runtime acceptance.

The two actual native source renders report `status=gap`: sibling-target
relations are unsupported. Automatic/rich text remains approximate and
transitions are not simulated. No missing-image gap remains. The private
2,200-by-2,740 comparison sheet places the actual uploaded references beside
those two source renders and labels its 16-case geometry illustration separately.
It explicitly identifies the self badge's unexpanded relation and does not claim
to be a FairyGUI Editor or Unity capture. The initial pointer-image preview,
helper argument/path/geometry refusals, commit identity omission and checkpoint
helper correction are preserved privately; later success does not erase them
or relabel these helper failures as application regressions.

Read-only development-ledger inspection verifies the actual earlier image
transfer and exactly one controller `fgui_review_created` audit record:

| Round identity | Measured value |
|---|---|
| Round / controller review ID | 1 / `290eceb764804d9db9a7539fabc8a748` |
| Source digest | `fcde79edda4bfaf97413688079c53446d6e941b88f88808f44144af9f5a2d246` |
| Review image SHA-256 | `5e7ced85694b4b720db66c4fc08b08f1e6db9f8bb1a97fbe5a34726e4d27d191` |
| Actually changed package | Leaderboard / `k8r6dxmj` |
| Selected dependency closure | Common, CommonFx, Leaderboard, PlayerCustomize, UILangTex |
| Confirmed normal visual notice | `a31de05a-cece-4ca7-a862-4d41e4f25a1c` |

The uploaded image hash/size/dimensions, private retained image, exact source
HEAD and controller record agree. The worker posts its durable `visual-1`
notice, parks at `visual_approval` and exits; independent observation finds no
worker and empty owned Jobs, with zero controller loop errors. No export audit
record exists. Scope approval is not approval of this new round. Named approval
of the unchanged preview/source plus an explicit export request is the next
human step. Source changes invalidate this round; the draft is not merged to
bypass that requirement.

This is development-PC evidence. The actual licensed export, scoped Client
installation, guards, exact-commit Unity loading, dynamic UI QA and delivery for
this new trial remain pending. FARM-1419 remains at its art-dependent `ui_ready`
gate with completed backend work retained; FARM-1346/FARM-1425 stay parked.
Deferred Common publication and intended production-host qualification remain
separate. No production deployment/restart, credential, profile or app setting
change occurs.
### Combined visual confirmation and measured Client destination (2026-10-09)

The operator clarifies that normal confirmation of the current visual round
includes export and scoped Client continuation. The worker notice now names
Farm-Client, the validated same-issue branch and changed package paths before
asking for that one reply. An explicit visual-only approval or hold leaves export
pending. The two provenance events remain distinct and may cite the same actual
human message and timestamp; no controller text heuristic, schema migration,
publication bypass or broader write grant is introduced. An older notice needs
an actual combined request or clarification. Runtime instructions and the visual
notice template are updated together; the running TestBot revision is not edited
or restarted by this change.

For FARM-1104 round 1, the operator's `approved` reply is independently read back
as human comment `42a97f89-bfc4-49ac-9476-354f836058ae` in the existing TestBot
thread. The subsequent actual combined clarification is
`d7d32e43-cb97-4a43-81b6-8144532f49ed`; no second session or ledger edit is used.
Read-only native development-state inspection measures one successful controller
export receipt, `3545fe52a0c244e39cdd0d1fdfc0c4f7`, SHA-256
`6eadebd81070188fd1df7cb7a24a18249a54c828f710abef013dff1520d34d0e`.
It retains the exact round-1 source/preview identities above. Actual private
staging matches the immutable five-package dependency closure; only Leaderboard
is changed and eligible for installation. The native publisher exits zero with
assignment before startup/export and an empty owned Job.

The controller then pins Farm-Client baseline
`54756872aadfb4619e9ec0055e56d36caa4d2570` and branch `farmbot/farm-1104`.
Independent trusted Git reads verify the actual owned Client worktree and branch.
At this observation its HEAD still equals the baseline, with no Client diff or
installation-success record yet. The intended destination is
`Assets/GameRes/FairyRes/Leaderboard` inside that worktree. Client installation,
guards, exact-commit Unity loading and actual dynamic panel QA remain pending;
this development PC observation does not certify production-host readiness.

### Native cached Client input restoration (2026-10-09)

The same FARM-1104 Client attempt retains its certified export and fixed baseline
but parks at `stage_limit` before installation. Its actual waiting notice is
`4f84fc5a-f99d-417f-90a0-b12ebf5bd94b`. `install-ui` refuses unhydrated selected
inputs. The worker's scoped LFS commands exit zero while still leaving pointers;
their diagnostic says Git LFS is not installed for that repository despite the
supplied required native process filter. This message is retained as a finding,
not treated as proof that the host lacks Git LFS or that credentials are missing.
No installation, Client candidate, guards, Unity reservation or Client draft is
recorded. The worker exits and all its owned Jobs settle.

Independent read-only inspection verifies the owned Client branch, unchanged
baseline/index and clean Git status. Across selected packages/dependencies and
the actual guard descriptors, 92 committed LFS files still contain pointers,
including 64 descriptors. All 92 corresponding local cache objects have the
correct SHA-256 and size; none is missing. This inspection uses no network.
An older private bootstrap also refuses because its historical Codex package
path was removed. The Git/LFS-only diagnostic avoids selecting Codex and reuses
the existing independently verified native tools; no runtime or host setting
is changed to bypass that stale diagnostic helper.

The first offline force-checkout probe passes only after changing pointer file
stats. A new real-Git clean-pointer regression exposes that direct force checkout
can still leave matching-stat pointer files unchanged. The incomplete probe and
failing regression are preserved; no skip or application guard is weakened.
The complete method uses native `checkout-index` with a fresh private `--prefix`
and UTF-8 NUL path input. Instructions require original OID/size proof and the
unchanged disk pointer plus live claim before scoped replacement; the offline
fixture verifies byte identities and preserves Head/indexed tree. A scoped index
stat refresh follows. The independent probe ends with clean status.
The real native regression also preserves a separate genuine XML edit and covers
binary bytes and paths with spaces/Unicode without a helper shell, network or
host login. Paid export outputs still pass through `install-ui`.

The selected native Windows Python 3.13.16/Git 2.54.0 run passes 123 focused
filter/UI-instruction/dispatch/skill tests in 4.503 seconds with zero failures,
errors or skips after the complete-method correction. Worker instructions, CLI
reference and current contract now require staged byte proof and scoped
replacement rather than relying on exit zero. No filter binary, controller
implementation, schema, account, credential, clone/host configuration or service
is changed. The existing TestBot receives the verified continuation in its
original thread as `c63a3876-15a5-4c95-a72d-2fa00a0ebd62`; it is a continuation
of the unchanged approved round, not a second session or repeated export.
Actual Client installation/guards/loading and dynamic panel QA remain pending.

[The complete #215 CI run](https://github.com/Kuaiwa-Network/farm-linear-agent/actions/runs/37903019651)
now succeeds on both Mac and Windows for the one-confirmation change. That is
separate from this new hydration regression and from production-host qualification.


### Actual native Client originals and source-history recovery (2026-10-09)

The existing FARM-1104 TestBot job resumes the unchanged approved round through
its original session and normal repository handoffs. Independent read-only
inspection at 2026-10-09T09:01:06+00:00 verifies all 92 previously inventoried Client
originals against their committed LFS OID and size. Every actual file matches,
with zero remaining pointers and zero differences from those baseline bytes.
The owned branch remains `farmbot/farm-1104`, with HEAD equal to its fixed Client
baseline `54756872aadfb4619e9ec0055e56d36caa4d2570`. This inspection uses no network and does not
modify live state. It measures the worker's actual restoration, separately from
the earlier offline fixture and from certified installation of paid outputs.

The next `install-ui` attempt refuses before installation because current
verified farmgui main `32f65d5d229c88404b621cf063eae3c5b8b8273c` is not yet available
as a local commit. Its bounded source Git read cannot compute the trusted merge
base; no installation recovery entries or Client changes are created. The
controller does not fetch into a read-only source repository from a Client-rooted
attempt. The worker saves its evidence and uses a normal handoff to a fresh
farmgui-rooted attempt to fetch that history. This preserves repository authority
and does not require a host configuration change or weakened source check.

At 2026-10-09T09:03:45+00:00, independent source validation succeeds against that current
verified main. The reviewed source HEAD remains `f6dbab2572e70bc2a150a305deb9b22a2d2234e2`,
merge base `86cd4640616a3434006fe87379060d7bb874746c`, with exactly the same component/UI-document
diff and only Leaderboard / `k8r6dxmj` changed. The source draft, human approval,
preview and retained export receipt remain unchanged. The active worker and all
observed descendants remain in its owned native Windows Job; prior Jobs are empty
and the development controller has zero loop errors. This observation does not
claim a memory limit on the runtime worker's Job.

The read-only controller-record checkpoint at 2026-10-09T09:08:35+00:00 still has exactly
one export and no completed installation, guard, Unity-loading or delivery record.
Those stages and actual dynamic panel QA remain pending. No second session,
repeat visual approval, direct export invocation, manual Client-output copy,
ledger edit or source-round change is used to bypass either refusal. FARM-1419
continues waiting for art/UI; parked cards are not resumed. These are measurements
on the development Windows PC, not qualification of the intended production host.
Production deployment/restart, credentials, profiles and app settings are unchanged.


### Leaderboard Client candidate and owned native package preparation (2026-10-09)

The unchanged FARM-1104 round proceeds through the original TestBot job to a
successful controller-certified installation. Read-only native inspection verifies
installation `89e52676b94b4cb09c74cfe686e72228`, checksum
`9fe9870e9b548dab431b9c2fe019566549a97f1b23bcbb6b3adae5c97267702b`, bound to retained export receipt
`3545fe52a0c244e39cdd0d1fdfc0c4f7` and reviewed source
`f6dbab2572e70bc2a150a305deb9b22a2d2234e2`. The actual seven-file Leaderboard
inventory matches that installation proof. Only Leaderboard is installed; its
atlases and existing Unity metadata are preserved.

The Client branch `farmbot/farm-1104` retains baseline
`54756872aadfb4619e9ec0055e56d36caa4d2570` and now has candidate
`827757aa1775b59bc1c88ddf9969e07a8a4e5b6e`. Actual controller `verify-ui` scope evidence and
independent Git reads agree on exactly one changed file,
`Assets/GameRes/FairyRes/Leaderboard/Leaderboard_fui.bytes`. The working tree is
clean. No gameplay, View bindings, other package or guard implementation changes.

The initial `verify-ui-guards` attempt stops during native dotnet project restore
with `NU1301` against the public NuGet service index. It creates no complete TRX
and neither of the two real guard tests runs. This is an unresolved dependency
restore finding, not a failing product assertion, skipped test or successful guard.
Its actual waiting notice is `43f485fe-bb41-4a04-a8fa-88358119b4b8`. The worker
saves the candidate/installation and exits; all its observed owned Jobs are empty.
No Unity reservation or Client draft is created at that checkpoint.

Host preparation then independently verifies 11 existing public NuGet archives:
their recorded SHA-512, size and ZIP integrity all match, including the exact
five direct dependencies of this committed Client project. Using the already
pinned native SDK, the host creates a fresh per-card private feed and package
cache under the existing task state root. A separate temporary probe project has
the same `net8.0` target and exact PackageReference declarations. Native restore
uses only an explicit local feed/config and exits zero in 1.9
seconds; all 11 restored archives match. The host's separate native Job has an
8-GiB limit and settles. This prepares dependencies; it does not run or certify
the actual guards, and does not assert that the model worker's Job has that limit.

No credential, user/global NuGet setting, SDK, service configuration, tracked
Client file or live ledger is changed by that preparation. The prepared cache is
already inside this card's existing state write root, requiring no wider grant.
The normal continuation in the original thread is
`af13bbf8-dfeb-46a2-88f2-ab5410186774`, read back as the actual operator. It directs
the resumed worker to verify the package/SDK identities and use command-local
`NUGET_PACKAGES` and private cache/temp selectors for the unchanged controller
CLI. [NuGet documents the process-local package-cache selector](https://learn.microsoft.com/en-us/nuget/consume-packages/managing-the-global-packages-and-cache-folders).
No test-command substitution, skip, source-failure suppression or fabricated TRX
is used. The same two real guards and exact-candidate Unity loading remain required.

[The full #216 CI run](https://github.com/Kuaiwa-Network/farm-linear-agent/actions/runs/37907469578)
now passes on both Mac and Windows for the native cached-checkout regression.
This is separate from the live Client guard/cache preparation and actual panel QA.
The renewed worker remains on the same candidate and retained source round;
the next actual guard, loading, clean-release and draft-delivery results are pending
at this checkpoint. These are development-PC results, not production-host
qualification or authorization to deploy. FARM-1419 still waits for art/UI and
the parked cards are not resumed.


### Actual native guards passed; Unity preparation failed (2026-10-09)

The original worker subsequently completes the unchanged controller guard command
against Client candidate `827757aa1775b59bc1c88ddf9969e07a8a4e5b6e` using the prepared
exact-version cache. Both real tests run and pass:

- `FguiDependencyGuardTests.No_unsanctioned_published_dependency_edges`
- `FguiOrphanAtlasGuardTests.Package_dirs_hold_exactly_the_files_their_descriptors_declare`

The complete TRX records two total/executed/passed tests and zero failures, errors,
skips or inconclusive outcomes. Native process duration is 12.463 seconds, exit
zero, with Job assignment before startup and the owned Job empty after completion.
Independent read-only inspection verifies the controller record checksum
`b328c87c4e5fde3c0cbfcbed08664ea97037ac055d70a2fb71222fcc7a4bde8f`, actual TRX SHA-256
`773ffa3a3080b4a20ef3865b701f3e602e8bc30fcb1c7cd8c1b07ad10c171f5c`, exact candidate,
installation/source binding and pinned native SDK. The earlier `NU1301` restore
gap is resolved for this actual guard run.

The controller then attempts interactive Unity preparation for that same candidate.
The first reservation is released after Editor startup cannot find its MCP instance
within 120 seconds. The ordinary failure-return path parks the folder on current
Client main `5f76b77955d7d97ebac91b46b4c6b1575d809033`; the next preparation attempt
finds tracked changes and fails. The job ends `failed` at
`ui-client-guards-verified`. Both reservations are released and all observed owned
worker Jobs are empty. No Unity loading or delivery certificate exists and no Client
draft has been created.

Later read-only inspection finds the configured Editor alive and its exact slot
connected to MCP. It also finds two modified atlas metadata files: Activities
changes a GUID, and PlayerCustomize changes importer platform entries. ProjectSettings
and Packages have no tracked diff. The Editor log includes an asset-database mtime
mismatch during the preparation interval, then late server-ready/session-connected
messages. The generic dirty-slot wording that says a human edited the folder does
not establish who produced these changes. A late MCP connection does not certify
the candidate: the slot is now on main, not the requested candidate. No metadata
is discarded, no Editor is stopped, no runtime is edited/restarted and no retry is
forced during this inspection.

The controller's normal task-worktree retirement preserves the candidate under
both the item recovery ref/history and `farmbot/farm-1104`. Independent trusted
read-only Git verifies the exact candidate still changes only the Leaderboard
descriptor against its pinned baseline; the committed LFS OID and retained object
match the certified installation. Immutable installation, guard and TRX records
remain available. This is preserved work plus an unresolved Unity preparation gap,
not successful end-to-end UI delivery. The next prerequisite is safe slot recovery
and exact-candidate Unity verification; neither reapproval nor re-export of this
unchanged round is required. These remain development-PC measurements.


### Fenced recovery of the failed UI startup (2026-10-09)

[FarmBot #219](https://github.com/Kuaiwa-Network/farm-linear-agent/pull/219)
fixes the Editor preparation failure-return path exposed above: it now quarantines
the slot at the requested commit and enters existing bounded controller recovery,
instead of force-checking out main beneath an importer that may still be running.
Preparation and execution budgets remain separate; batch-runner wiring failures
retain their existing behavior. There is no timeout increase, new skip, identity
bypass or schema change. Three new regressions fail on the old implementation and
pass after the fix, including Stop during startup, which never resumes cancelled
work. Native Windows checks run 126 tests: 124 pass, zero errors/failures,
and the following two existing platform skips:

- `test_slots.PoolTests.test_a_fifo_left_for_the_batch_summary_cannot_stall_the_batch_run`:
  `FIFOs in a directory are POSIX`.
- `test_slots.PoolTests.test_what_the_worker_left_at_the_token_path_is_replaced_by_the_grant`:
  `no FIFOs in a directory, and making a symlink needs a privilege`.

All ten native Windows Job Object tests actually run and pass. The related
slot/multi-slot/recovery/UI checks take 352.562 seconds. The host's separate
8-GiB verification Job settles; this does not assert a memory cap on model workers.
UTF-8 logs, failing regressions and exact revision/tree are retained privately.
#219 merges as `bb54cd060588493b900eb1203672fdabab362873` with the verified tested tree
`23024c19f823df0ca0cbd0054b01c33bf196e8ec`. Full cross-platform CI is still pending at merge and is tracked
separately from these focused native results.

Before requesting recovery of the existing development slot, read-only inspection
verifies its configured folder, clone/worktree trust, exact source snapshot, two
eligible atlas metadata changes, Editor executable and current-account ownership.
All 50 recorded native worker Jobs are empty; no active worker, queued/running/
resource-waiting item, open reservation, pending recovery or cleanup exists.
The host requests the existing controller recovery API, with no forced release,
direct row edit, manual source reset or direct Editor operation. This request
does not resume the failed job. The controller completes recovery
`314eefee-5cb8-424c-962f-14837308b2e5` in two bounded repair attempts. It preserves the two
changed metadata files under immutable archive commit
`a8f425893f7a7d17b4ddd8649604d8b00ee7ac94`, verifies the Editor has exited and leaves the
same main commit `5f76b77955d7d97ebac91b46b4c6b1575d809033` clean and `idle_closed`.
Independent read-only checks verify the manifest, recovery ref, exact archived
path set and parent commit; the failed item and all original candidate/guard
evidence stay preserved.

A fresh development runtime is prepared outside every task worktree from the
verified merged #219 tree. It copies only repository source, with no private
configuration, credential or runtime-state copy. Read-only doctor reports no
missing feature/UI tools and native export readiness; existing historical
failed/blocked-job findings remain. After a fresh whole-instance settlement fence,
only the verified owned TestBot development controller restarts. Its interpreter,
process creation identity, current-account ownership, bootstrap ancestry and
listener are checked. Configuration and all external state roots remain unchanged;
the already selected signed native CLI remains `codex-cli 0.162.0-alpha.2` and the
model policy remains `latest-sol` / `xhigh`, resolving to `gpt-6.1-sol` at this
checkpoint. The healthy serving heartbeat has zero loop errors. Neither a healthy
heartbeat nor the restored main slot certifies candidate Unity loading.

The normal operator-authored continuation in the original thread is
`98ad292a-0168-4642-beff-bc0e709d778c`, independently read back with the original
operator identity. It requests normal continuation of retained round 1 and
candidate `827757aa1775b59bc1c88ddf9969e07a8a4e5b6e`, with no new visual approval,
export or source change. A read-only conversation worker receives it in the
original session under a verified native Job, still using `gpt-6.1-sol` / `xhigh`.
The conversation completes its normal `request-repair` continuation and exits
with its native Job empty. The original UI work item is queued again under the
same item/session, with the saved source draft, candidate, target and checkpoint
retained; its next native worker is verified contained and uses the same model.
Exact-candidate loading, clean release, Client draft, two-draft delivery and dynamic
panel QA remain pending at this checkpoint. The
other cards remain parked and production is unchanged. These are development-PC
measurements, not production-host qualification or deployment authorization.


The #219 tested-head Mac CI completes successfully at 2026-10-09 10:47:15 UTC;
the Windows job also completes successfully at 11:16:20 UTC. Its
[full CI run](https://github.com/Kuaiwa-Network/farm-linear-agent/actions/runs/37918315612)
is distinct from the focused native result and the actual UI trial.

During normal UI continuation, a source comparison finds that the reattached
checkout still contains the committed LFS pointer for
`assets/Common/Atlas/bg/icon_bg_90.png`. Independent read-only checks verify the
cached original against that committed pointer's SHA-256 and size. The worker
restores the original input; a subsequent independent check verifies its actual
bytes, unchanged reviewed source head and clean working-tree status using the
already pinned native Git/LFS filter. The host does not edit the active worker's
checkout. This resolves the observed input-materialization gap without a source
change, new review or repeat export; the remaining controller source/receipt
checks and Client/Unity continuation still have to complete.

### Reviewed byte identity after worktree reattachment (2026-10-09)

The resumed worker restores 694 selected source originals using the pinned native
fresh-prefix checkout, verifying committed LFS OIDs/sizes and retaining its
restoration evidence. Independent read-only source validation accepts the owned
clean source commit and hydrated selection, but its complete 5,504-file raw-byte
digest is `13bc4df5c0ccf0879c4195a6e5e42dc275319fb483baef316a65fca2a4b07c28`,
different from the review/export digest
`fcde79edda4bfaf97413688079c53446d6e941b88f88808f44144af9f5a2d246`.
The actual normal Client handoff refuses with
`UI source or authority differs from the controller export`. The worker preserves
all evidence, posts notice `08a315f5-f5aa-46b7-b6ad-28703f9f24b9`, and exits
`awaiting_input` at `ui-source-identity-stage-limit`; all its native Jobs are empty.
No new Unity reservation, Client draft or delivery certificate is produced.
Git cleanliness alone does not establish the original raw-byte review identity.

Read-only diagnosis isolates the difference to checkout newline conversion in
the two changed text files. The reviewed `LeaderboardRankItem.xml` has 3,018
bytes and SHA-256
`1679155ab44c900a41f97e0371cc3d19ac8d95455f3e3ccee3a0dc31eaee7f31`:
lines 20, 21, 36, 37 and 43 use LF; the other line endings use CRLF. The rebuilt
checkout has all CRLF. Its approved raw bytes are reconstructed from the unchanged
committed blob and independently matched to that retained review hash/size.
The other file, `docs/farm-1104-leaderboard-vip-ui.md`, has original LF bytes,
6,373 bytes and SHA-256
`9e18e4223a0b93e0867a44844f309c23348631f507413b0d846cfe572faef61a`;
these equal its committed blob. Substituting only these two verified original
hashes into the otherwise unchanged full file map exactly reproduces the original
approved/exported digest. This is a virtual diagnostic proof until actual source
restoration and normal controller verification complete.

The host writes only new private prepared copies and a manifest under this card's
existing state root. It does not edit the live checkout, claim, ledger,
configuration, Git filters/attributes or export. The operator-authored continuation
`d0185c71-f048-4b6b-92ee-19ad0d7bf8bc` is read back with the original operator and
thread identity. It authorizes the normally claimed farmgui worker to verify and
back up current bytes, restore only these exact approved inputs within its existing
write scope, and require the unchanged full source/export checks and Client handoff
to pass. No changed source or weaker identity check is authorized. The original
UI item queues normally again under the same session, source draft, candidate and
certificate identities. Actual restored source identity, Client rehydration,
exact-candidate Unity loading, clean release, two-draft delivery and dynamic panel
QA remain pending at this checkpoint. Production remains unchanged.

### Exact reviewed inputs restored; normal Client handoff passed (2026-10-09)

The normally claimed farmgui worker verifies the prepared files and current
identities, preserves previous bytes, and restores only the two proven reviewed
text inputs. The ordinary controller Client handoff actually passes and the same
item advances to `ui-client-handoff` with `root_repo=Farm-Client`. The source
attempt exits and its native Job is empty; the subsequent Client worker is
contained and retains the same source draft, round, receipt, installation and
candidate `827757aa1775b59bc1c88ddf9969e07a8a4e5b6e`. There is no new source commit,
approval or export.

Independent read-only `owned_source` validation now verifies all 5,504 actual
tracked-file hashes, clean committed/index identity and the hydrated selected
inputs. The actual complete digest is again
`fcde79edda4bfaf97413688079c53446d6e941b88f88808f44144af9f5a2d246`, exactly matching
the original controller review and immutable export record. The earlier virtual
proof is now confirmed by restored files and successful normal controller entry.

The UI instructions also require private exact-byte retention of authored
non-LFS source/UI-document files before parking or leaving farmgui. They explain
how a reattached checkout can change raw newlines, require unchanged reviewed
identities and original-byte proof for scoped restoration, and preserve the
existing controller refusal for a changed digest. This adds no controller API,
schema migration, permission grant or source-normalization exemption. A real Git
fixture with a Unicode/spaced XML path checks mixed reviewed newlines, native
checkout conversion, unchanged commit/index, different complete digest, and exact
restoration. Its initial fixture/stat-refresh failure is corrected; no application
identity check or old test is weakened. The related source/workflow/skill/dispatch
checks run 145 tests on native Windows: all pass, zero failures/errors/skips,
78.453 seconds. UTF-8 logs and exact file identities are retained privately. The
separate host verification Job has an 8-GiB limit and settles; model worker memory
limits are not inferred from it. Live service/runtime configuration is unchanged
by this instruction follow-up.

The current Client attempt still needs its committed original inputs restored,
then exact-candidate Unity loading, clean release, Client draft and two-draft
delivery. Dynamic panel QA remains separate. These are development-PC results;
production qualification and deployment are still pending.

### Actual reserved package loading and release passed (2026-10-09)

The Client worker restores 92 committed original inputs through the existing
scoped native process-filter route. The certified seven-file installation,
source review/export identities and candidate
`827757aa1775b59bc1c88ddf9969e07a8a4e5b6e` remain unchanged. Both actual hydrated
guards pass again in 12.092 seconds, with no failed, skipped or Inconclusive
result. Their controller checksum is
`ec4d69a7bc058280534bf929c1ece0b630f1a28f904a0556291246b967dbee19`;
the complete TRX checksum is
`bd6c56d8d31835f3cb44e70deb3fb9c6f3d78dcafa467097a57158123265619c`.

The first normal loading call refuses because the UI gate compares the active
candidate to `slots.parked_commit`, which still names the last idle main park.
Independent read-only inspection verifies that the actual owned slot HEAD is
already the exact candidate. Normal worker unclean release and controller
execution recovery preserve the candidate and repair the slot at that same
commit. This makes the historical park field equal the requested candidate on
the next reservation; it does not fix the faulty first-use check.

Reservation `c3c52cac-e5de-4e08-a5fb-60c6236ec551` then produces the actual
controller loading checksum
`c81c4056df949f22f714297199b3781302902ce696da497283b5c1ae7c802ef2`.
Matched before/after Editor/source identity and actual descriptor hashes bind
the successful load of Common, CommonFx, Leaderboard, PlayerCustomize and
UILangTex to the original installation, source and candidate. Probe-owned
package cleanup is verified. The normal CLI's quiescence gate passes;
the reservation is released with `worker reported quiescent`, and the pool
returns to `idle_open`. These results come from the unchanged development
runtime at `bb54cd060588493b900eb1203672fdabab362873`, not the following
application fix or a production deployment. Client publication and two-draft
delivery still need their actual proofs at this checkpoint. Package loading
does not establish dynamic target-panel QA.

### Same-round two-draft delivery completed (2026-10-09)

The normal worker publishes [Farm-Client #1448](https://github.com/Kuaiwa-Network/Farm-Client/pull/1448)
at the exact tested candidate and retains [farmgui #150](https://github.com/Kuaiwa-Network/farmgui/pull/150)
at the unchanged reviewed source commit. Both are actual open drafts on
`farmbot/farm-1104`. Controller delivery
`b6dfa570a9624d38887be428ecfa9e4f` binds that exact pair to the original receipt,
approval/request, current guard and actual loading identities. Normal
`finish --outcome delivered` passes; the same original UI work item becomes
`delivered` and its worker exits.

Independent read-only verification confirms the delivery ID in the terminal
evidence, generation, exact PR pair and source/candidate pins, canonical guard
and loading checksums, five loaded packages, complete probe cleanup, and the
actual reservation's quiescent release. All 12 UI attempt native Jobs are empty;
there is no open UI reservation or pending UI recovery. Normal controller task
cleanup completes and removes the temporary task worktrees. The trusted clone
retains the candidate branch, whose diff still contains only
`Assets/GameRes/FairyRes/Leaderboard/Leaderboard_fui.bytes` against the selected
baseline. The shared Editor remains in an idle-open pool slot; no host process
or state is manually cleared to obtain delivery.

The selected small UI trial has completed certified source approval, native
export, scoped Client installation, hydrated guards, exact-commit package
loading, clean resource release and two-draft delivery on this development PC.
The actual game panel still requires dynamic nickname/VIP visibility, relation
placement and pooled-row checks, including the bottom self row. Neither game PR
is merged by this verification. FARM-1419 remains pending its art/UI work, and
production-host qualification and separately authorized deployment remain release
prerequisites.

### Active UI slot identity uses the actual owned commit (2026-10-09)

The application fix keeps `parked_commit` as historical idle-park evidence.
At every UI reservation fence it instead validates the configured Client slot's
owned clone/worktree entry and reads the actual HEAD through explicit Git
directory/common-directory/work-tree selectors. Hooks, fsmonitor, LFS filters,
replacement objects, lazy fetch and optional locks are disabled for this bounded
read. An unavailable read, different commit or borrowed Git pointer refuses
loading. Active exact-commit reservations, live claims, before/after Editor and
source identity, package hashes, cleanup, Stop and delivery checks remain in force.
There is no schema migration, new write grant or manual live-row correction.

The regression uses real owned local Git repositories and the normal pool grant,
switch, identity record, reservation-token file and resume path, with a separate
Unicode/spaced Editor folder. The last idle park remains the baseline while the
actual slot switches to the candidate. Before the fix the normal loading case
reproduces the committed-slot refusal in 11.736 seconds. New refusal cases cover
a changed actual HEAD even when the historical field names the candidate,
another owned worktree's Git pointer, commit drift during loading and a timed-out
commit read. Existing unknown identity, Stop, package/result and source-drift
refusals remain covered.

Native Windows validation covers 138 distinct relevant cases. The initial
137-case run records two fixture errors (547.733 seconds); normal pool grants
return the secret token through their existing file, and Git for Windows protects
the hidden `.git` fixture file. After correcting token handling and adding the
timeout case, the 138-case run has one remaining hidden-file fixture error
(533.070 seconds). The fixture then uses an existing-file handle and restores
its original pointer/permissions. The complete changed UI group reruns all
12 cases successfully in 111.075 seconds, with zero failures/errors/skips and
unchanged application source. The other 126 cases have 124 passes and only these
two existing platform skips:

- `test_slots.PoolTests.test_a_fifo_left_for_the_batch_summary_cannot_stall_the_batch_run`:
  `FIFOs in a directory are POSIX`.
- `test_slots.PoolTests.test_what_the_worker_left_at_the_token_path_is_replaced_by_the_grant`:
  `no FIFOs in a directory, and making a symlink needs a privilege`.

All ten native Windows Job Object tests actually run and pass. No application
check or existing test is weakened, and no skip is added. Python is 3.13.16,
Git is 2.54.0.windows.1, and `PYTHONUTF8=1` is set before each explicit Python
launch. UTF-8 logs, every initial fixture error, durations, source identities
and process settlement are retained privately. The separate host verification
Job has a measured 8-GiB limit and settles; this does not assert a model-worker
memory limit. Documentation checks, links and whitespace are also validated.
These tests change no active runtime or configuration.

The predecessor exact-input follow-up #221 has now completed its full pinned
[Mac/Windows CI](https://github.com/Kuaiwa-Network/farm-linear-agent/actions/runs/37926740183)
successfully. The current application's full CI is tracked separately. The
successful live UI delivery above used the earlier runtime, and does not
substitute for first-use verification of this fix or production-host qualification.


### Development controller activated after settled UI delivery (2026-10-09)

Application fix [#222](https://github.com/Kuaiwa-Network/farm-linear-agent/pull/222)
merges as `b3c630bf20b48304e2a8faac2d8ca840ba2046c5`, with the exact tested
tree `6a105544a6cd56155a701f8eda191bbb7b009721`. A new isolated frozen development
runtime contains only that repository revision; no private configuration,
credentials or state are copied into it. Its read-only preflight verifies the
unchanged development profile, native CLI 0.162.0-alpha.2, ready native publisher,
no missing feature/UI tools, and `gpt-6.1-sol` with `xhigh`.

Before activation, the fresh whole-instance fence verifies all 56 historical
native worker Jobs empty, no active/queued worker, pending webhook, open
reservation, pending recovery or incomplete cleanup. The actual owned idle-open
slot has clean source and a quiescent Editor at its historical park. The restart
revalidates the old development controller's executable, command, creation time,
owner, ancestry and receiver port, retains its process handle, and terminates only
that verified controller. The shared idle Editor is preserved. Private restart,
process and rollback evidence remain intact.

Independent read-only checks verify the new controller identity/ancestry,
unchanged configuration, exact merged revision/tree, fresh serving heartbeat,
HTTP health 200 and zero consecutive errors in all six service loops. The same
whole-instance/idle-slot fence passes again after startup. Health 200 alone is
not claimed as readiness: these results are scoped development-runtime evidence.
The already delivered UI trial is not repeated, and no new approval/export,
game PR merge, production deployment or production configuration change occurs.

The application's pinned [full CI](https://github.com/Kuaiwa-Network/farm-linear-agent/actions/runs/37932931889)
is still running at this activation checkpoint; the native focused coverage above
is not represented as a full-suite result. The operator selects the existing
development Client for visual acceptance; no additional acceptance checkout is
created. The exact delivered Client branch still needs actual game-panel checks.
Actual dynamic game-panel acceptance,
FARM-1419 art/UI closure, intended production-host qualification and separately
authorized production deployment remain release prerequisites.
