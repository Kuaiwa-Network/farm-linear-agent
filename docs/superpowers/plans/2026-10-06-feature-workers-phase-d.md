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

farmgui main is inspected at `5970bcf3fc42d2f1e0ae95f6a687b77953c6907c`.
Its AGENTS.md and authoring rule 12 still reserve package registration for the
Editor. Rule 12b forbids underscores in independently packed image IDs; rule
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
The final foundation revision and complete native/hosted suite results are recorded
after execution; earlier Client-stage measurements do not certify this new code.
