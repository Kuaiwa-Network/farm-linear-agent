# FarmBot Phase D: UI authoring and preview approval

Prepared on 2026-10-06 from the [accepted feature design](../specs/2026-09-24-feature-workers-design.md),
especially §§5.5–5.6, 7.1–7.3 and 13. This is development work, not authorization
to enable a running bot, use a live card or export UI with a licensed Editor.
The operator has authorized continuing implementation and routine verified
development merges. Phase C's controller PR #151 and measured record #152
are merged. All 1,811 offline tests pass on native development Windows and
hosted macOS/Windows, with no new skip; later real Client/Unity acceptance
remains pending. Neither merge changes a running bot.

## Entry state and scope

The supported native Client network/config/typecheck tools have landed. The
development-PC [LFS record](../spikes/2026-10-06-windows-final-tools-lfs.md)
includes a real scoped farmgui asset read, approved native upload/forced
download, and the optional endpoint-scoped worker callback. This is useful
development evidence, not proof of an arbitrary UI package's complete hydration
or of the production Windows host.

At entry, farmgui main was inspected at `5970bcf3fc42d2f1e0ae95f6a687b77953c6907c`.
Its AGENTS.md and authoring rule 12 reserved package registration for the
Editor. [farmgui #141](https://github.com/Kuaiwa-Network/farmgui/pull/141) is now
merged at `54a76b2a7110cdf8897144902f8490ed70df45bd`: authorized Bot/UI jobs may
make only validated new registrations, preserving all existing IDs/settings and
the Editor-rewrite warning. This grants no UI export. Rule 12b forbids underscores in independently packed image IDs; rule
12c requires unique eight-character lowercase alphanumeric package IDs. The
existing paid-CLI grant covers authorized fixes and small changes to existing
UI. It does not implement a new UI worker's visual/export workflow.

Phase D delivers authoring in farmgui, previews, a draft PR and an explicit
visual-approval pause. It grants no Farm-Client writes, Unity reservation,
FairyGUI CLI export, deployment or standing MCP. Phase E adds export separately.
Windows uses the selected native Python, Git and LFS, never Bash/MSYS/WSL.

## 1. Preview transport

Implement `upload-image --item --file` in the existing worker CLI. Authenticate
the claim before reading a file or requesting an upload; require an `fgui`
worker with fresh delegation and the intended configured ledger/state directory.
Use a bounded regular PNG/JPEG from that worker's state directory, rejecting
links/reparse points, path escapes, malformed content and oversized dimensions.
Keep the image immutable across validation and upload. Recheck ownership around
network work so Stop or withdrawal cannot turn a stale claim into an upload.

Use Linear's [documented file-upload flow](https://linear.app/developers/how-to-upload-a-file-to-linear):
request `fileUpload`, then PUT the validated bytes with its returned upload
headers. The app bearer token goes only to the GraphQL endpoint; signed PUT
URLs and credential-bearing headers never reach stdout, checkpoint or logs.
Refuse redirects and malformed transfer destinations/headers, use time and size
bounds, and sanitize errors. Return the unsigned Linear asset URL, hash and
dimensions only after a successful transfer and final claim check.

Tests use stub GraphQL/storage responses and temporary files: no credentials,
real upload, comments or live card. Cover PNG/JPEG, truncated data, signatures,
caps, source mutation, path/link hazards, failed/redirected transfers, hostile
headers, token isolation and cancellation before each transfer boundary. Keep
fix/chat/feature behavior unchanged. Real app-token upload acceptance remains
a separately approved TestBot check.

## 2. Reusable component preview renderer

Add a native Python entry under `skills/fgui/tools/`, using a pinned Pillow
dependency for this optional tool. Keep ordinary controller imports independent
of Pillow. Prepare dependencies explicitly in development/CI; never let a worker
install tools, fetch fonts or invent missing art during a job.

Read package/component XML and hydrated source images from explicit read roots;
write PNG and a bounded JSON evidence report only under a new private output
root. Resolve resource IDs through actual package manifests, including exported
cross-package references. Refuse traversal, reparse paths, duplicate identities,
unhydrated LFS pointers, cycles and excessive expansion. Never write a source
package or the watched `output/` directory.

Support the first useful static preview set: nested components, image/loader
resources, nine-slice scaling, placement, opacity, controller-selected visibility,
text, Button title/icon overrides, basic list layout and progress values. Resolve
an explicit or known native CJK font per OS and record its identity. Missing
fonts/art or unsupported effects are visible gaps; an approximation cannot be
presented as an Editor/runtime capture. Apply bounds to image sizes, recursive
component/node counts and aggregate pixels.

Reports name source/package/component identities, selected states, input hashes,
font/tool identity, dimensions and every approximation/gap. Side-by-side mockup
comparison uses the actual uploaded reference; measure and report deviations
instead of making up a reference image. No generated game assets are committed.

Synthetic offline fixtures cover layering, alpha, nine-slice, cross-package
resolution, controllers, Button overrides, lists/progress, Unicode/space paths,
missing art/fonts, pointers, cycles, limits and immutable sources. Inspect native
Windows PNG artifacts visually and retain hashes/reports privately. Test useful
geometry/pixels rather than mirroring helper implementation.

## 3. farmgui registration rule

Prepare a narrow farmgui guidance PR permitting validated manual registration
for an authorized `fgui` job. Preserve existing IDs; allocate only genuinely new
resources/packages with checked uniqueness and the existing packing constraints.
Update AGENTS.md and rule 12 together, retaining the Editor-rewrite warning,
lint/cycle checks, export scoping and fix behavior. Add meaningful offline
coverage if executable validation changes. No asset package or publish setting
changes merely to make the new grant exist.

## 4. Opt-in UI worker

Add `skills/fgui` and its dispatch AUTHORITY together. The manifest starts in
farmgui, writes only farmgui, reads Farm-Client main for integration context,
lists `answers`, `visual_approval` and `pr_review` gates, and has no resources
or MCP. Use the existing six-hour budget, claim renewal, isolated homes,
exclusive scheduling and opt-in enablement. Share read-only bot design-document
access through the supported profile/environment routes without exposing
credentials or changing an account/configuration.

Instructions require Bot/UI delegation, the fresh claimed card and its design
document, current upload manifest, actual art dimensions/hashes and a scoped
package/dependency list. Missing/ambiguous art is a named question; placeholders
require a recorded human answer. Hydrate only those packages/dependencies using
native LFS and verify that every needed object is materialized. Follow current
farmgui naming, Common-control reuse, safe-area, text, ID and cycle/lint rules.

Write the team's UI document with exported component identities, client
integration checklist, states and art/behavior gaps. Run native measurement,
text and cycle/lint tools, then publish only the authorized farmgui draft through
the existing destination/foreign-work checks. Render and upload clearly labeled
approximate previews with actual mockup names and measurements. Persist preview
hashes, PR head, notice IDs and named user/message approvals in the bounded plan.

Park with one visual-approval notice. Comments alone do not resume; an explicit
session continuation does. Corrections or changed art/source invalidate earlier
approval and require a new preview round. Record actual human approvals from
their author and message, never from bot comments or issue-text instructions.
During Phase D, an export request reports Phase E as unavailable rather than
running the CLI or silently delivering a Client export. A delivered authoring
result explicitly names the remaining export/runtime/human merge work.

## 5. Journeys and rollout boundary

Extend real-controller fake-worker journeys through intake/download/art gaps,
farmgui authoring, preview upload, repeated visual correction/approval, duplicate
notice avoidance, publication retry, explicit continuation, Stop and recovery.
Keep Code/fix/chat scopes and all native Windows containment tests intact. Run
focused boundaries and the full offline suite on the final executable candidate
with native Windows and hosted macOS/Windows evidence; record all skips.

No database migration is planned. Before rollback, settle/cancel new UI jobs and
their recovery work; a checkout change does not make old instructions understand
unfinished UI plans. Real card/preview acceptance, paid native FairyGUI export
and Unity loading belong to separately scoped later tests. Production enablement
and deployment require separate authorization and final-host release/recovery
verification. Phase D development does not change the running TestBot.

## As executed: preview foundations (2026-10-06)

Steps 1–2 have a separate foundation candidate: immutable PNG/JPEG validation,
claim/delegation-fenced Linear upload transport, a reusable read-only native
renderer, pinned Pillow wheels and synthetic offline regressions. No `fgui`
manifest/authority or live upload is delivered by that candidate; steps 3–5 and
Phase E remain pending. Existing worker grants and host configuration are unchanged.

Native development-PC calibration used a new detached farmgui checkout at
`5970bcf3fc42d2f1e0ae95f6a687b77953c6907c`, Python 3.13.16 and Pillow 12.3.0
in a separate development environment. Native Git LFS hydrated only four needed
source images. Common/StarBar (351×63), Notice/Marquee (933×57) and
Common/StandardBtn_S (240×83) rendered with no known gap; their PNGs were inspected
visually and source status remained clean. Chinese glyphs use the locally prepared
`msyh.ttc`, recorded by hash. Transition simulation and rich/automatic text layout
remain named approximations. No Editor or Unity was started, and this is not a
pixel comparison against an operator's mockup or production-host certification.

Real manifests exposed path-valued folder metadata, and real component geometry
required natural image sizes and loader aspect/alignment behavior. Both now have
regression coverage. Loader recursion and pixel budgets remain hard refusals,
rather than being caught as a missing-art placeholder. Every signed-upload test
uses scripted GraphQL/PUT responses; no credential or real issue is used.
The preview foundation is merged as [FarmBot #153](https://github.com/Kuaiwa-Network/farm-linear-agent/pull/153),
merge `e57408d3be979599a52298d4532816d0e2ede03b`, candidate
`11d32d8b3567196e1f391f2da83e4a6151021a3e`. Its candidate, hosted test merge
`fc0c200bde04db6089bdaf7f5da2364727a8bd29` and actual merge have identical tree
`d880587544e4551c81c9ca2a4ff3d21578f6ad1a`.

| Foundation run | Discovered | Failures/errors | Existing skips | Suite duration |
|---|---:|---:|---:|---:|
| Native development Windows | 1,854 | 0/0 | 69 | 1225.647 s |
| Hosted macOS | 1,854 | 0/0 | 42 | 651.613 s |
| Hosted Windows | 1,854 | 0/0 | 69 | 1903.995 s |

All 43 preview/transport/CLI regressions and 22 feature journeys ran on every
platform. Both Windows runs executed all seven native Job Object tests. Every
skip ID/reason equals the previous 1,811-test baseline; no skip was added.
[Hosted UTF-8 logs, versions, skip reasons and per-test durations](https://github.com/Kuaiwa-Network/farm-linear-agent/actions/runs/37484196079)
are retained by CI; native equivalents remain private. Native tools were Python
3.13.16/Git 2.54.0.windows.1/LFS 3.7.1; hosted macOS used Python 3.13.15/Git
2.55.0/LFS 3.8.0, hosted Windows Python 3.13.15/Git 2.55.0.windows.5/LFS 3.7.1.
The farmgui guidance merge and foundation merge leave running bots untouched;
these measurements do not certify the later worker candidate or production host.

## As executed: authoring worker candidate (2026-10-06)

Steps 3–5 now have an isolated development candidate: the opt-in/exclusive `fgui`
manifest and pinned dispatch authority, native execution identity, shared read-only
bot document reader, UI intake without a Client pin, visual-round instructions and
UI-document/approval templates. Defaults remain chat/fix. Only farmgui is writable;
valid UI claims cannot hand off to Farm-Client or reserve Unity. Common, Code, fix
and chat dispatch grants retain their pinned bytes. No schema or running-bot change.

Real-controller fake-worker journeys use native Python/Git and the actual renderer
and upload CLI against local origins and StubLinear. They cover art questions,
comments that do not resume, explicit attributed replies, corrections with new
source/pixel/head identities, durable notice retry across restart, distinct visual
approval/export-request events, the authoring-only stage limit, active Stop and
successor recovery of unpublished work. They test transport/orchestration and
durable attribution, not a model's judgment, real GitHub publication or live visual
acceptance. Independent publication/claim/containment boundaries remain in the suite.

Read-only development-PC doctor ran with new empty scratch roots, dummy
`doctor-only` client/webhook values, runtime Codex, explicit chat/fgui enablement
and `lark_cli.profile=farmbot`, with no Windows HOME. In the ambient shell,
`lark-cli` is absent from PATH. Selecting the previously prepared, release-integrity
checked native lark-cli for that verification process resolves that gap: Python
3.13.16, Git LFS 3.7.1, Pillow 12.3.0, `msyh.ttc` and lark-cli 1.0.82 all pass.
The font SHA-256 is `d79c55e68b1131eea0cc1c47be4f572d964f28c682e143db2ad09c1e4cb07a3f`.
The named profile exists, with zero other profiles or user logins. The prepared
probe takes 0.432 seconds; only `ledger_unreadable` remains, expected for empty
state. Both scratch roots remain empty. No production config/ledger, credentials,
app settings, account, service, Editor or Feishu authentication is touched.

This inventory checks the selected development environment, not the production
Windows host or an actual UI worker's Feishu access. The default shell still needs
an explicit prepared tool PATH at launch. Per-user Windows DPAPI is unchanged;
HOME is not credential isolation. The supported dedicated store/account route and
explicit Code/UI environment route retain their separate actual-worker/permission
acceptance requirements. Final candidate/platform measurements follow execution.

Initial worker CI found seven macOS failures at candidate
`a774370edff0fa015e939651ac0c7b9e8bdbb302` (1,868 tests, 658.793 s, no errors,
42 existing skips). Three UI journeys supplied macOS's symlinked temporary path
to the strict renderer; the fixture now uses its canonical owned source/state
root. The application path/link refusal remains intact. The monitor lacked the
new UI skill label; it now shows `UI`, with a direct label regression. Three
legacy tests still expected an uninstalled/noncontinuable UI skill and now test
the actual enabled-UI behavior and a genuinely uninstalled name. All 64 focused
journey/successor/conversation/monitor checks pass in 29.385 s, with no skips.
This correction happened on 2026-10-07 (UTC+8). Corrected full platform runs are
required before merging the worker; failed measurements and logs remain preserved.

Remaining acceptance: an operator-selected TestBot UI card with actual document/art
and app-token preview upload, human correction/approval, separately implemented
Phase E licensed native export and Client/Unity checks, then final-host release and
recovery verification. No existing parked card, unmerged game test draft or Code
job is resumed to obtain that evidence.

## As executed: verified UI authoring worker (2026-10-07)

[FarmBot #154](https://github.com/Kuaiwa-Network/farm-linear-agent/pull/154) is merged as
`1ad041327b2fb05b8855f58a50663fa99dd5f31f`. Its final executable candidate is
`011815cad769e64110bde810edbb71b148e2f3e3`; candidate, hosted merge-test
`27507a764ee7fe8a6073bda18cd6d32cb0b3b462` and actual merge share tree
`6c07071f3ee94ae2b076e0df37a9132dd237156a`. Completed development branches are removed only after
local/remote head checks, and both isolated development checkouts are clean.
The running TestBot checkout and configuration remain unchanged.

| Final worker run | Discovered | Failures/errors | Existing skips | Suite duration |
|---|---:|---:|---:|---:|
| Native development Windows | 1,869 | 0/0 | 69 | 1256.048 s |
| Hosted macOS | 1,869 | 0/0 | 42 | 682.553 s |
| Hosted Windows | 1,869 | 0/0 | 69 | 2023.806 s |

All 22 Code feature journeys, eight UI scope/toolchain checks, three UI controller
journeys and 43 preview/transport/CLI regressions run on each platform. Both
Windows runs execute all seven native Job Object tests. Every skip ID/reason
equals the preceding 1,854-test baseline: 69 on Windows, 42 on macOS. No skip or
containment/ownership exception is added. The existing native Windows skip-reason
table in the [Windows record](../spikes/2026-10-06-windows-final-tools-lfs.md)
continues to apply.

- Native development Windows: Python 3.13.16, git version 2.54.0.windows.1, git-lfs/3.7.1.
- Hosted macOS: Python 3.13.15, git version 2.55.0, git-lfs/3.8.0.
- Hosted Windows: Python 3.13.15, git version 2.55.0.windows.5, git-lfs/3.7.1.

Native verification uses the explicitly selected Python with `PYTHONUTF8=1`
before launch, a fresh passing symlink probe and the candidate's pinned offline
workflow harness. Inherited FarmBot/fake-CLI selectors and GitHub token variables
are sanitized. UTF-8 logs, every test/skip ID and reason, revision/version inventories
and per-test durations remain private locally; the
[completed hosted run](https://github.com/Kuaiwa-Network/farm-linear-agent/actions/runs/37492479493)
retains the corresponding platform evidence. This is a **development Windows PC**,
not certification of the production Windows host.

Initial candidate `a774370edff0fa015e939651ac0c7b9e8bdbb302` discovered
1,868 tests. Native Windows completed in 1257.160 s with four failures, no errors
and 69 existing skips; macOS completed in 658.793 s with seven failures, no errors
and 42 existing skips. Initial hosted Windows was superseded/cancelled; its partial
log is preserved and is not a completed suite. The actual application omission
was the monitor's missing UI label. Three legacy tests still expected UI work to
be unavailable. The three additional macOS journey failures used a symlinked
temporary-path spelling; their fixture now passes its canonical owned root to
the unchanged strict renderer. The 64 focused correction checks passed in
29.385 s with no skips before the full corrected runs above. No failure is
reclassified as a host gap or hidden behind a skip.

This completes Phase D's offline implementation: explicit Bot/UI intake, farmgui
authoring authority, native preview/upload transport, attributed visual rounds,
durable notices and Stop/recovery. The journeys use local remotes and StubLinear;
they do not establish real document access, app-token upload, model visual judgment,
paid exporter execution or Unity loading. Only farmgui is writable, and a licensed
export request still parks at the explicit Phase E `stage_limit`.

Read-only UI doctor with dummy `doctor-only` values, explicit chat/fgui enablement,
runtime Codex and a fresh empty scratch root measures Python 3.13.16, LFS 3.7.1,
Pillow 12.3.0, hashed `msyh.ttc` and prepared native lark-cli 1.0.82. The named
farmbot profile exists with zero other profiles/user logins and no Windows HOME.
The prepared probe takes 0.432 s; only the expected `ledger_unreadable` finding
remains, and scratch state stays empty. The ambient shell lacks lark-cli on PATH;
selecting the integrity-checked native tool only for the verification process
resolves that inventory gap. This does not establish real Feishu access or the
production host's launch environment. The FairyGUI executable path and paid batch-license
status remain missing operator inputs; PATH/uninstall inventory alone cannot
exclude a portable installation. No Editor, account, credentials, application
settings, production state or service is changed.

Next is the [Phase E native export plan](2026-10-06-feature-workers-phase-e.md):
measure the selected paid executable and owned Windows worker route, then implement
scoped export validation and Client installation before separately approved live
UI-card, hydrated Client/Unity and final-host release/recovery acceptance. No parked
Code card or unmerged game test draft is used as UI acceptance.

The documentation follow-up passes **75** skill/reference tests in
**0.518 seconds**, with zero failures, errors or platform skips, plus
local links/anchors, private-path/credential-pattern and whitespace checks. Only
three Markdown records change; executable code, tests and workflow are identical
to the verified merge, so no repeat full suite is required for this record.
