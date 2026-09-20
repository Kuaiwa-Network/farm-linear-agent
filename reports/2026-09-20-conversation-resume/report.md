# Conversation continuation — 2026-09-20

The operator approved extending fix workers to Farm-Contract, asking unresolved questions
in Linear, and resuming from ordinary language in the original session or an @FarmBot
mention. A subsequent instruction requires `needs-more-info` whenever FarmBot asks for
clarification. These decisions supersede the older contract-handoff-only boundary.

## Behavior

- The existing chat worker interprets intent in context. No keyword list determines restart.
  Its item-scoped `resume-work` command verifies a live chat claim, an actual received message,
  a fresh open/delegated issue snapshot, and prior delegated fix work on that same issue.
- The original session prefers its own fix. A new mention can select the latest prior fix on
  the issue. Chat completion, fix requeue and copying the complete conversation are atomic.
  A receiver that selected chat just before handoff follows its recorded destination, preserving
  concurrent replies. An answer arriving while a question is posted is not stranded.
- Read-only observation of changed issue text/comments no longer automatically retries blocked
  work. Negations and questions about restarting reach the model without causing a retry.
- `await-input`, direct elicitation activities and intake elicitation add `needs-more-info`.
  The API reuses an applicable team/workspace label or creates a team label if absent, then
  uses additive `issueAddLabel`, preserving other labels. Label removal is not automatic.
- Fix workers get Farm-Contract's isolated worktree and bare clone in their writable roots.
  They read its local instructions and use its openspec workflow from that cwd. The explicitly
  approved cross-repo workflow supersedes only the older separate-session-only restriction.
  Confirmed decisions can produce linked draft contract and implementation PRs; unclear intent
  parks for a Linear answer. Merge/deploy/status/assignee authority is unchanged.

## Verification

Final validation: `python3 -W error -m unittest discover -s tests` passed **347 tests**
in 81.921 seconds, warning-free.

Regression tests exercised real SQLite ownership and handoff state, fresh delegation via the CLI,
full conversation preservation, same-session selection, concurrent inbox delivery, and the
answer-before-park race. API tests cover additive labeling, idempotence and failed confirmation.
Scheduler and local end-to-end fixtures include the new Farm-Contract worktree. New regressions
were observed failing before their fixes.

An independent reviewer found the concurrent-reply and wrong-session selection bugs; both were
fixed with regression tests. The undelegated-chat clarification edge was also corrected.

Actual isolated Codex chat workers ran against temporary ledgers and a stub Linear API:

| Input intent | Observed result |
|---|---|
| “The blocker is sorted … Please pick this back up” | Prior fix queued; decision copied; chat delivered |
| “先别重新开工 …” | Fix stayed cancelled; chat answered only |
| Question about which server | Fix stayed cancelled; chat answered from the recorded target |
| Ask to clarify whether initial count is zero or one, without restarting | `needs_more_info` then elicitation; chat awaiting_input; fix stayed cancelled |

These are live model/CLI checks with fake external effects, not proof of live Linear labeling
or delivery of FARM-1247. The smoke script never launched a resumed fix and removed its temporary
auth copies after workers exited. Sanitized results are in `smoke-results.json`.

Linear's current [agent documentation](https://linear.app/developers/agent-interaction) and
[official schema](https://raw.githubusercontent.com/linear/linear/refs/heads/master/packages/sdk/src/schema.graphql)
were checked for comment-backed sessions, elicitation and additive label mutation shapes.

## Integration and outstanding work

The original installed service remains on its earlier checkout. Its private `repos` configuration
now includes `Farm-Contract: https://github.com/Kuaiwa-Network/Farm-Contract.git`, and its isolated
bare clone has been seeded using the existing Worktrees implementation and the same-remote human
checkout as a read-only seed. Credentials and other config were preserved. Do not create a second
Unity slot or replace the original ledger. New behavior becomes live after branch integration
and service restart.

FARM-1247's operator decision is zero initially unlocked tables, counting only explicit unlocks.
Its prior run is still blocked and has no game-code PR. After integration/configuration, the new
worker can correct the contract and affected sources itself. The previous blanket assumption
that config export requires Unity was too broad: common's README documents a headless producer
for both profiles. Consumer import must still follow its own current instructions; no generated
files may be hand-edited. Phase 1 criterion 5 is still outstanding.
