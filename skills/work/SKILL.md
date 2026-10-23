---
name: work
description: One main worker owns a delegated bug fix or feature, using native subagents when useful.
---

# Issue owner

Own this issue through completion, as in a normal Codex session. Bug fixes,
features and UI work use the same workflow. Read the actual issue, existing
answers, attachments and each affected repository's instructions. Make a short
plan when useful; there is no required A–G stage plan or extra proposal approval.
Honor approval genuinely required by repository instructions, using existing
authorization rather than asking again for routine edits.

Use all necessary owned worktrees in the launch context. Implement the required
source, UI assets, Client metadata, server changes, tests, generated files and
CI registration. Change directories directly. Use existing repository tools;
do not expand a small issue into unrelated cleanup.

Use native Codex subagents at your discretion when independent work benefits
from delegation. Small or tightly dependent work can stay with you. For example,
independent UI and server changes can run in parallel; Client integration waits
for the UI outputs it needs. Give helpers a clear scope, owned paths, repository
instructions and relevant decisions. Coordinate conflicting writes yourself.
You own integration, verification and delivery. Helpers respect the same
permissions and report results or blockers to you; only you manage the FarmBot
claim, shared resources, human questions, publication and finish. Native helpers
do not use the retired controller-managed presentation task workflow. Settle
helpers before parking, releasing resources they use, handoff or finish, and
save their completed work in your checkpoint for recovery.

Make `startup.start_argv` your first execution-tool call, before directory listings,
token checks or instruction-file reads. The launcher archives the previous attempt's
token; an old checkpoint is not a current claim. Only reconcile token state after
your submitted claim has an ambiguous outcome, without submitting another claim.
Unclaimed startup timeout recovery belongs to the controller; authenticated
checkpoint/retry commands require a successful claim.
Claim once first using `startup.start_argv`; it saves the token privately and
returns the resume context. Then read [the concise CLI guide](../../references/single-worker-cli.md)
and relevant repository instructions. Use `execution.python`
with argument arrays and `--token-file`, never put a token in logs. Renew normally,
and before expensive operations. Post concise progress when something changes.
Download issue attachments and the current Project Overview's embedded files/images
with `download-uploads --out STATE_DIR/uploads`, just as chat does. The helper discovers the
project from the live issue, handles authentication, safe local names and ZIP
extraction, and records sources. Without `--url` both sources are included; use
unsigned URLs to select files. Reuse intact local files and inspect actual resources
before reporting missing assets. Do not replace this helper with a custom downloader.

Before changing contract behavior or protocol semantics, review each decision
against approved requirements, existing decisions, affected implementations and
repository rules. Include newly discovered edge cases, compatibility and failure
behavior. A feature's approved requirements settle only what they actually cover;
they do not make every subsequent choice high confidence. Code is evidence of
current behavior, not approval of a new business rule. Ordinary field naming and
editorial corrections with no semantic change need no new decision round.

| Classification | Evidence and action |
| --- | --- |
| Already decided | Cite the exact approved requirement or attributed answer and its scope; proceed without asking again. |
| High confidence | State the decision, cite evidence and explain why it follows directly with no unresolved tradeoff; publish and proceed without a reply. |
| Medium confidence | Describe the real tradeoff and a reasonable alternative, give a recommendation and costs; require an attributed answer before dependent edits. |
| Low confidence | Explain missing/conflicting evidence or unresolved intent, values or experience; give concrete options and costs, a recommendation and room for another answer; require an attributed answer before dependent edits. |

Publish a concise Linear decision summary before its dependent contract or
implementation edits. When every decision is already decided or high confidence
and no explicit approval is required, use `activity --type thought --body-file FILE`
and continue automatically. Say that no reply is required; do not call `await-input`
for acknowledgment. Keep each decision's evidence and reasoning beside its rating.
Your high-confidence assessment is not a person's approval: do not invent an
approver, answer or `[DECIDED]` attribution for it.

If medium/low decisions or required approvals remain, complete independent clear
work first, publishing its decided/high subset before editing. Do not begin
dependent contract or implementation changes. Then checkpoint, settle helpers,
release resources, and use `await-input --reason question` for the unresolved
items. Group questions by responsible role, low before medium, with evidence,
recommendations, alternatives and costs; reference already-posted decisions.
Do not leave an unanswered question only in `activity` or a PR. The worker exits
after parking and resumes on a reply. Silence, elapsed time (including three
working days) and a general instruction to develop do not settle these questions.
On resume resolve only the items actually answered by attributed replies.
`await-input --reason question` posts the questions in the issue's comment area;
the session contains a pointer to that comment. Give questions stable numbers and
invite each responsible person to reply directly under the question comment.
No @mention or consolidated session answer is required. FarmBot checks outstanding
question threads on the normal lifecycle pass, so replies may take about a minute
to appear. Session replies and @mentions in other comments still work.

Maintain an answered/unanswered list with actual inbox message IDs and authors
in the checkpoint. A reply can answer just one question; retain earlier answers
across resumes and never ask someone to collect everyone else's answers. Complete
only work supported by the answers so far, then checkpoint and use `await-input`
again for the remaining questions. This reuses the existing question comment and
round. Call `resolve-question` only after all questions in that round are answered,
citing the relevant message IDs from all respondents. Resolve that round before
opening a new one; do not silently replace its questions with different decisions.
Explicit repository approval requirements apply at every confidence level;
reuse an existing approval only within its actual scope.

List missing assets, formal configuration values and access separately from
decision confidence. A high-confidence plan to defer an input does not provide
or approve it, authorize invented values, or establish complete delivery.
Keep decisions, evidence, posted summary references, unanswered items and actual
answers in the checkpoint. Reuse unchanged decisions on resume; publish only
new or changed entries, and assess new gaps before dependent work.

Test the candidate and its affected behavior first. Follow real repository test
requirements. Decide whether a failure is related using the actual diff, failure
details and retained evidence. Run a baseline only if a concrete unanswered
question requires it. Preserve raw outcomes and explain unrelated failures in
the delivery; they do not create an automatic whole-suite repair project.
Never turn missing results into a pass. Reuse reports when candidate and relevant
inputs still match. Maintenance, a diagnostic run, renamed tests or main moving
does not alone justify another full run or merge.
Finalize generated output and test inputs before the final verification run.
An exploratory result may be reused only if those inputs still match. Check the
repository's native toolchain before running a bundled executable: fresh worktrees
may contain an LFS pointer instead of the binary. Preserve version/schema checks.

For Unity, request a slot at the clean committed candidate, stay in this worker,
and poll `resource-status`. When ready, use `unity-tools` to discover schemas and
`unity-tool`/`unity-read` to work with the reserved instance. Collect actual test
results and needed UI observations in that same reservation. Reconcile a lost
test-start response before retrying. Leave tests finished, Edit Mode idle and
compilation quiet before releasing. Never operate shared Editor processes yourself.
If a terminal `get_test_job` response has no result, use `unity-test-result` with
the exact job ID while still owning the reservation. It reads retained results
without rerunning tests; unavailable or truncated evidence must be stated honestly.
Save the report and release safely before PR drafting or remote CI waiting.
Refresh the checkpoint when results are collected/released and before delivery:
state the actual results, retained reports and next action, replacing old waiting
text. Resource readiness/release alone is never evidence that tests passed.
If a resource is held for recovery, preserve reports, checkpoint and exit as
instructed by the resource response; do not repeatedly poll or start another test.
If maintenance arrives while a request is still queued, cancel that request;
if preparation has begun, wait for readiness, settle it and release safely.

When `recovery.candidate_repair` reports importer metadata changes, inspect the
retained metadata evidence and recovery Git ref. New `.meta` files and changes
to existing `.meta` files are source inputs. Preserve existing valid GUIDs, review
import settings and check asset references if a GUID changed. Integrate the
appropriate metadata into your owned Client worktree, commit it and request
Unity at that new clean SHA. The host can recover a held Editor from this new
request even after its old baseline exhausted repair. Never edit the shared slot,
ignore metadata changes or reset the recovery counter. Recovery grants no test
pass; verify the corrected candidate before delivery.

Use ordinary local commands for tests that need no shared service. Request
`server_slot` only for tests that need its isolated services, specifying packages,
tests and race selection. Use the returned loopback environment in your own
subprocesses; stop them before release. Do not touch live services or administer
Docker. Server containers remain owned by the host and are recycled only after
it can establish process quiescence.
Before releasing server services (including a combined lease), checkpoint the
actual results, candidate SHAs, report paths and a `handoff` describing the next
action. Successful release returns `worker_handoff: true` and retires the claim.
Exit immediately, including during maintenance. The controller verifies all
attempts exited, cleans the services and automatically resumes the same issue
and saved progress. Do not renew, finish or request another resource after that
response. Ordinary Unity-only releases keep the worker alive.

If acceptance requires Client and Server candidates online together, follow
[single Client/game-server verification](../../references/client-server-integration.md).
Request its atomic pair and stay in this worker. Use the pinned server source and
reserved Unity instance, exercise actual scenarios and retain both SHAs/results.
Independent single-side checks need no pair. Release safely before delivery or
human waits; missing evidence never becomes a pass.

For FairyGUI, edit the affected packages and use the repository's preview and
validation tools. Request human visual review only when the task/repository
requires it. `publish-ui` exports explicit package names/IDs with the configured
native publisher into private staging. Install the required outputs in your
owned Client, preserve valid GUIDs, inspect the diff and test bindings/behavior.
No separate UI task, source PR approval or controller receipt is required.
Missing art is a concrete human question; do not invent final assets.

For copy-only work, first distinguish editable text from lettering baked into an
image or authored art. Changing the words does not authorize replacing image
lettering with Text, cropping/masking/hiding it, or overlaying substitute text.
Use suitable approved replacement assets if available. Otherwise preserve the
original presentation, complete independent clear edits, and ask for the asset
or an explicit decision on the visual alternative. List deferred art wording
honestly; retaining the PNG file while hiding its lettering is not preserving art.

Dynamic text means a fixed authored sentence with replaceable parts, for example
`七日登录可领取{grade}品{crop}`. Keep that template in the owning visible text
field and fill its variables. A generic dialog/toast accepting an entire message
is a different existing API: keep feature copy with the caller or its existing
resources. Do not add hidden Text nodes to shared components as a business copy
store, or migrate unrelated strings just to satisfy a template rule. Review
changed node types, image crops/visibility, overlays and shared-component
responsibilities alongside tests; readable text and passing tests cannot approve
a new visual design.

Keep source commits, candidate SHAs, report paths, failures and next action in a
concise checkpoint. Reports belong in `state_dir`, never source commits. A human
question parks only when a real decision/input is missing. Follow the contract
decision review above: publish already-decided/high decisions and continue;
ask about medium/low decisions, explicitly required approval, or missing
essential assets/access after checking available inputs. Explain the blocker,
options and recommendation via
`await-input --reason question` after saving progress and releasing resources.
Normal resource queueing is not a human question. Read and acknowledge the actual
attributed replies; routine technical repair needs no new permission.

If automatic approval review reports `Selected model is at capacity` and explicitly
says the action was not executed, save the complete returned review message verbatim
as UTF-8 under `state_dir`. Checkpoint the failed action, commits, reports and next
step; settle helpers and release resources safely. Run `retry-approval --error-file FILE`
with the normal database, item and claim arguments, then exit on `retry_queued` or
`retry_exhausted`. Use this durable retry immediately instead of repeated approval
calls or `await-input`. On resume reuse matching reports, recheck current authority,
and retry through normal automatic approval, including fresh `verify-publication`
before any publication. Never bypass review, change permissions/models, or treat a
safety denial or uncertain execution outcome as this capacity failure.

Review the final diff and required verification yourself. Use `verify-publication`
before pushing each exact issue branch or creating/updating its draft PR. Push the
final commit and run `finish --check-ready` before posting the delivery. Publish
a clear delivery with the PRs, behavior tested and limitations; then `finish`.
Successful `finish` registers its verified PR list as this issue's own output for
continuations. A checkpoint `prs` list alone is not publication ownership.
FarmBot verifies ownership, destination, live claim, resource release, that each
listed PR is this issue's open or merged PR at your worktree's current HEAD, and
the confirmed delivery comment, not a second semantic acceptance matrix. Do not
merge PRs, deploy or change Linear status.

On maintenance, collect in-flight work, stop your services, release resources,
checkpoint a current `handoff`, and `maintenance-handoff` then exit. On withdrawal,
publish nothing and follow `withdraw`. Recovery reuses source and evidence.
