---
name: work
description: One worker owns a delegated bug fix or feature through implementation, verification and draft delivery.
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
CI registration yourself. Change directories directly. Do not hand off between
repositories or create subagents in this first rollout. Use existing repository
tools; do not expand a small issue into unrelated cleanup.

Read [the concise CLI guide](../../references/single-worker-cli.md). Claim once
using `startup.start_argv`; it saves the token privately. Use `execution.python`
with argument arrays and `--token-file`, never put a token in logs. Renew normally,
and before expensive operations. Post concise progress when something changes.

Test the candidate and its affected behavior first. Follow real repository test
requirements. Decide whether a failure is related using the actual diff, failure
details and retained evidence. Run a baseline only if a concrete unanswered
question requires it. Preserve raw outcomes and explain unrelated failures in
the delivery; they do not create an automatic whole-suite repair project.
Never turn missing results into a pass. Reuse reports when candidate and relevant
inputs still match. Maintenance, a diagnostic run, renamed tests or main moving
does not alone justify another full run or merge.

For Unity, request a slot at the clean committed candidate, stay in this worker,
and poll `resource-status`. When ready, use `unity-tools` to discover schemas and
`unity-tool`/`unity-read` to work with the reserved instance. Collect actual test
results and needed UI observations in that same reservation. Reconcile a lost
test-start response before retrying. Leave tests finished, Edit Mode idle and
compilation quiet before releasing. Never operate shared Editor processes yourself.
If a resource is held for recovery, preserve reports, checkpoint and exit as
instructed by the resource response; do not repeatedly poll or start another test.
If maintenance arrives while a request is still queued, cancel that request;
if preparation has begun, wait for readiness, settle it and release safely.

Use ordinary local commands for tests that need no shared service. Request
`server_slot` only for tests that need its isolated services, specifying packages,
tests and race selection. Use the returned loopback environment in your own
subprocesses; stop them before release. Do not touch live services or administer
Docker. Server containers remain owned by the host and are recycled only after
it can establish process quiescence.

For FairyGUI, edit the affected packages and use the repository's preview and
validation tools. Request human visual review only when the task/repository
requires it. `publish-ui` exports explicit package names/IDs with the configured
native publisher into private staging. Install the required outputs in your
owned Client, preserve valid GUIDs, inspect the diff and test bindings/behavior.
No separate UI task, source PR approval or controller receipt is required.
Missing art is a concrete human question; do not invent final assets.

Keep source commits, candidate SHAs, report paths, failures and next action in a
concise checkpoint. Reports belong in `state_dir`, never source commits. A human
question parks only when a real decision/input is missing. Read and acknowledge
the actual attributed replies; routine technical repair needs no new permission.

Review the final diff and required verification yourself. Use `verify-publication`
before pushing each exact issue branch or creating/updating its draft PR. Publish
a clear delivery with the PRs, behavior tested and limitations; then `finish`.
FarmBot verifies ownership, destination, live claim, resource release and the
confirmed delivery comment, not a second semantic acceptance matrix. Do not
merge PRs, deploy or change Linear status.

On maintenance, collect in-flight work, stop your services, release resources,
checkpoint a current `handoff`, and `maintenance-handoff` then exit. On withdrawal,
publish nothing and follow `withdraw`. Recovery reuses source and evidence.
