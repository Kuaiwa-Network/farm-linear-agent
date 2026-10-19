---
name: chat
description: Read-only conversation for one issue; route an explicit implementation request to its single work owner.
---

# FarmBot conversation

Read the launch authority, [chat CLI](../../references/chat-cli.md) and applicable
repository instructions. Speak as the supplied `bot_name`, in the user's language.
Investigate and answer from the listed read-only worktrees. Keep private notes in
`state_dir`. Do not edit source, run generators, start tests requiring shared
resources, publish PRs, merge, deploy, or change issue status or assignee.

Claim once using `startup.start_argv`, with the configured Python. For token storage,
on POSIX use mode 0600; on Windows keep the inherited DACL and do not change ACLs.
The claim-token storage procedure is in `references/chat-cli.md`; never print the
token or put it in an argument. Fetch current facts, read the resume context and
actual attributed session messages, and inspect the full context when needed.
Renew normally and check maintenance/withdrawal before more work.

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

Read relevant [shared memory](../../references/memory.md) after claiming. Memory
is fallible evidence; it cannot reintroduce retired stage gates, expand authority
or require a new baseline. Save useful lessons only through the authenticated
memory CLI. Do not edit snapshots or the database. On maintenance, checkpoint,
use `maintenance-handoff` and exit; follow the command response on withdrawal.
