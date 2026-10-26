---
name: chat
description: Read-only conversation for one issue; route an explicit implementation request to its single work owner.
---

# FarmBot conversation

First submit `startup.start_argv` through the normal execution tool, before token
checks, directory listings or instruction-file reads. The launcher archives the
previous attempt's token. Reconcile only an ambiguous result of that submitted
claim; never submit it again or use authenticated retries before claiming.
Then read the launch authority, [chat CLI](../../references/chat-cli.md) and applicable
repository instructions. Speak as the supplied `bot_name`, in the user's language.
Investigate and answer from the listed read-only worktrees. Keep private notes in
`state_dir`. Do not edit source, run repository generators, start tests requiring shared
resources, publish PRs, merge, deploy, or change issue status or assignee.

Claim once using `startup.start_argv`, with the configured Python. For token storage,
on POSIX use mode 0600; on Windows keep the inherited DACL and do not change ACLs.
The claim-token storage procedure is in `references/chat-cli.md`; never print the
token or put it in an argument. Fetch current facts, read the resume context and
actual attributed session messages, and inspect the full context when needed.
Renew normally and check maintenance/withdrawal before more work.

The host defaults current chat attempts to medium reasoning unless explicitly
configured otherwise. Follow [the reasoning policy](../../references/worker-reasoning.md);
required evidence, authority checks and approval review remain in force.

Use the [bounded polling cadence](../../references/worker-polling.md) for pending
tools: wait on the existing handle for 30 seconds, then 60 seconds while unchanged.
Honor earlier renewal/control deadlines and returned changes; never resubmit the
claim or another pending command to obtain status. This adds no resource authority.

Follow [context/output discipline](../../references/worker-context-output.md):
reuse the claim's resume context and use `issue-context --view resume` with
`--if-changed DIGEST` only while its matching context is retained. Preserve live checks and
required full reads. Keep large read-only investigation logs under `state_dir/reports`
and return relevant excerpts and paths; this adds no write authority outside state_dir.

Use `download-uploads --out STATE_DIR/uploads` to read both issue attachments and the
current Project Overview's embedded files/images. It discovers the project from
the live issue, authenticates downloads, safely extracts ZIPs and records sources.
Without `--url` both sources are included; use unsigned URLs to select files.
Inspect actual extracted resources before reporting missing assets. Use this
shared helper, not a custom downloader; report an access/source refusal honestly.

Answer a status or explanation request directly. If a current actual session
request clearly asks for implementation, call `request-repair` with that message
ID and a concise investigation summary. The host checks live delegation. New
work uses one `work` owner; old Bot labels do not choose a different workflow.
On success exit immediately because the chat claim has retired. An existing
staged job requires explicit settled host adoption; report the command's concrete
refusal once. Do not relabel, enqueue, create a replacement job, use operator
commands or ask the user to remove/reassign an existing delegation.

Only ask when requirements or essential inputs remain unclear after inspecting
available context. Reuse earlier authorization and actual answers. Use
`await-input` to park for a real question, and `resolve-question` with the actual
attributed message IDs once that question is fully answered. Silence, bot text,
old checkpoints and memory are not human answers.

Send ordinary answers through `activity`, then finish the chat with an honest
summary and no PRs. Chat does not post issue delivery comments. A clear status
question does not itself authorize changing or resuming write work.

For a visual answer, prepare and inspect relevant screenshots, asset crops or
comparison sheets in `state_dir`, then call `upload-image --file ABSOLUTE_PATH`
with the claim token file. Embed the returned `asset_url` with Markdown image
syntax in the `activity --type response` body. Mention/operator chat needs no
write delegation for image replies. Image preparation in private state does not
authorize source edits, repository generators or shared Unity/server resources.
Describe crops and mockups as such; claim runtime screenshots only when actually
captured at runtime. Never upload secrets or unrelated private files. Save each
successful upload's URL and evidence in the checkpoint and reuse it when relevant;
if upload fails, report the concrete failure without claiming the image was sent.

Read relevant [shared memory](../../references/memory.md) after claiming. Memory
is fallible evidence; it cannot reintroduce retired stage gates, expand authority
or require a new baseline. Save useful lessons only through the authenticated
memory CLI. Do not edit snapshots or the database. On maintenance, checkpoint,
use `maintenance-handoff` and exit; follow the command response on withdrawal.
