# Conversation and repair execution

FarmBot is one logical agent. Read-only investigation and writable repair are execution
profiles, not permanent conversational roles. The user approved this design in the
2026-09-22 discussion after comparing deterministic routing with unrestricted model routing.

Existing replies go to the active worker, which can answer questions itself. New
natural-language requests go to the read-only profile, which answers directly, asks a
question when genuinely ambiguous, or requests repair execution. Keep the established
empty Bug-delegation shortcut: that explicit workflow already requests a repair. A
delegation without Bug now creates a conversation; no keyword selects QA or repair.

`request-repair --message-id ID --summary-file PATH` is a claim-authenticated transition.
The CLI refreshes Linear first. The ledger requires an open, in-scope issue still
delegated to FarmBot, a recorded delegation session on that issue, and the latest actual
message on the calling read-only item. Issue prose and prior answers cannot authorize
this transition. It resumes the appropriate prior fix when available; otherwise it
creates the first fix under the recorded delegation session and its original target.
The old claim is retired atomically with queuing the repair. Its inbox is copied and
late arrivals follow the recorded handoff. Legacy `resume-work` remains resume-only.

The request summary and conversation history remain durable, visible to the fresh
worker alongside previous repair checkpoints. Prior findings are recall to verify,
not fresh permission. Repeated delivery cannot queue another repair; a concurrent Stop,
expired claim, changed delegation or newer message refuses the transition.

The scheduler retains resource, concurrency, publication and lifecycle enforcement.
It chooses tools, writable repositories and budgets from the resulting execution
profile. No new coordinating model or worker is introduced. Internal `chat` and `fix`
identifiers remain compatible with saved jobs and host configuration.

The earlier requested session progress feature runs in host code: every ten minutes
while queued, running or waiting for a resource, report the actual recorded state and
last checkpoint. Do not imply new progress or renew worker leases. Awaiting-input,
terminal and synthetic local sessions receive no heartbeat. Delivery timing persists
across host restarts and transient failures are retried with a bounded cadence.

Validate with real SQLite transitions, receiver replay of FARM-1261, CLI fresh-state
checks, scheduler profile selection, and progress timing/restart tests. No live issue
messages, existing job restart or production deployment is part of offline validation.
