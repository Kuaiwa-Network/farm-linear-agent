# Conversation Repair Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement inline, with a final independent review.

**Goal:** Let one FarmBot conversation answer questions or enter authorized repair execution without re-delegation.

**Architecture:** Extend the existing atomic resume handoff to first repairs. Keep
execution profiles and controller authority; remove semantic keyword routing. Persist
conversation context and publish host-owned session progress.

**Tech Stack:** Python standard library, SQLite, unittest, existing Linear API client.

**Spec:** `docs/superpowers/specs/2026-09-22-conversation-repair-design.md`

## Global Constraints

- Preserve existing job IDs and command compatibility for resumed non-cancelled fixes.
- Cancelled fixes require a successor and the existing cleanup gate.
- No additional coordinating model, external dependency, or production mutation.
- Read-only workers cannot directly modify source or publish changes.

## Review Focus

- A new message or Stop racing the mode switch must prevent use of stale intent.
- A mention on an undelegated issue cannot grant repair authority.
- A reply after a completed answer must retain the previous question/findings.
- The fresh repair worker must receive the repair budget and writable worktrees.
- Heartbeats must survive restart without reviving awaiting-input/completed sessions.

## Task 1: Conversation-to-repair transition

Files: `agent/ledger.py`, `agent/__main__.py`, `agent/router.py`,
`tests/test_repair_work.py`, `tests/test_cli.py`, `tests/test_router.py`, `tests/test_receiver.py`.

- [x] Add failing real-ledger and receiver tests: non-Bug delegation → question → 修复
  queues the first fix; assert original target, messages, summary and old-token retirement.
- [x] Cover revoked delegation, missing delegation provenance, foreign/stale message,
  Stop/expired claim, rollback, duplicates, prior fix recovery and late inbox forwarding.
- [x] Add `request_repair(item_id, token, message_id, app_user_id, summary)` and CLI
  `request-repair --item ID --token-file PATH --message-id N --summary-file PATH`.
- [x] Share the atomic handoff with legacy resume-work; include durable conversation
  history and request summaries in issue-context. Keep historical context non-authoritative.
- [x] Route non-Bug delegations and all free-text intent to the conversational profile;
  retain empty Bug-delegation dispatch and active-worker steering.
- [x] Run focused routing, receiver, CLI and handoff tests; verify GREEN.

## Task 2: Worker instructions and execution integration

Files: `skills/chat/SKILL.md`, `skills/fix/SKILL.md`, `references/worker-cli.md`,
`docs/operating-contract.md`, `tests/test_scheduler.py`.

- [x] Exercise a handoff through the scheduler and inspect resulting profile, budget,
  context and workspace setup; add regression tests before implementation adjustments.
- [x] Explain both profiles as the same agent; document intent interpretation, summary
  transfer, request-repair, legacy command behavior and answering while fixing.
- [x] Update operating contract and historical design amendments.

## Task 3: Durable session progress

Files: `agent/session_progress.py`, `agent/service.py`, `tests/test_session_progress.py`.

- [x] Add failing tests with real SQLite and a recording API: ten-minute cadence,
  restart, retries, waiting/terminal/local exclusions, no lease renewal.
- [x] Add a host progress publisher and independent service loop; report only recorded
  work state and checkpoint timing, keeping networking outside scheduler locks.
- [x] Run focused tests and the full offline suite with PYTHONUTF8=1; investigate failures.
- [x] Independently review the complete diff, fix material findings, verify and commit.
