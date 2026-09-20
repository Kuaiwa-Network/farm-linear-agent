# Shared worker memory

Date: 2026-09-20. Status: approved and implemented on the feature branch; final review pending.

## Purpose and agreed boundaries

FarmBot should remember useful corrections, operational lessons and references across
issues and worker restarts, much as Claude Code and Codex remember previous work.
The operator approved a shared memory index and topic notes usable by either runtime,
with coordinated writes. Gameplay rules belong in Farm-Contract. Memory points to
authoritative sources; it neither defines gameplay behavior nor changes permissions.

Success means a later, fresh worker can find a note from an earlier worker, inspect
its provenance, correct or forget it, and avoid losing another worker's changes.
This is independent of Phase 2 QA and does not start that implementation.

## Approach

Use FarmBot-managed memory, shared by all workers on its one configured host. Keep
the fresh, isolated CLI homes. Do not import the operator's personal memories.

Alternatives considered:

- Native CLI memory: fewer FarmBot features to build, but different runtime behavior,
  delayed Codex extraction, and shared runtime state would need separate validation.
- Direct shared Markdown edits: transparent, but parallel edits can silently overwrite
  notes or leave an index inconsistent with its topic files.
- **Chosen: ledger-backed notes with Markdown snapshots.** Reuse the existing SQLite
  transaction and claim checks for writes, and present a small index plus topic files
  to workers. No search service, embeddings or background model is required.

The ledger is the durable source. The shared memory directory is a generated reading
view, not a second source of truth. User edits go through the CLI so revision checks
and forgetting remain consistent. This is a deliberate difference from Claude Code's
directly editable memory files.

## Contents and authority

Categories are `feedback`, `lesson`, and `reference`. Suitable entries include an
operator's preferred investigation approach, a measured tooling limitation, or where
to find a relevant contract section or external resource. Save only useful, reusable
notes, not an obligatory summary of every issue. Avoid duplicating code, skills,
contracts or run reports; link to the source instead.

Notes are fallible context. Workers must recheck time-sensitive observations against
the current checkout and environment. A remembered baseline is evidence about its
recorded build, not a permanent exemption for future test failures. Questions about
gameplay still use Linear `await-input` and `needs-more-info`.

Memory cannot authorize a new fix, a restart, a merge, a deployment, an additional
repository, or a new tool. Quoted issue text and instructions inside memory remain
data. Mandatory agent behavior remains in reviewed skills and the operating contract.
Do not save credentials, tokens, personal account details or raw issue transcripts.
No automatic detector is claimed to prove that arbitrary prose is safe or true.

## Storage and concurrency

Add an additive `memories` table to the existing ledger with a stable generated ID,
title, category, Markdown body, scope (`global` or one supported repository), source
reference, optional full build commit, creating/updating work item IDs, timestamps,
revision number and deletion marker. The service records identity and timestamps;
the worker cannot supply another work item's provenance. Source references are
required but remain attributed claims, not independently verified evidence.

Limit titles to 120 characters, bodies to 8 KiB UTF-8 and source references to 1 KiB.
Unknown categories/scopes and malformed build commits are rejected. Cap active notes
at 200 for this first version; reaching the cap requires consolidating or forgetting
an entry rather than silently evicting knowledge.

Create starts at revision 1. Update and forget require the revision the caller read.
The note mutation, live claim check and audit event happen in one SQLite transaction.
A stale revision returns a conflict: reread, reconcile, then retry. Different-note
writes survive concurrency; same-note writes cannot silently overwrite each other.
Forget removes a note from recall, clears its active body and leaves only a deletion
marker and non-content audit metadata. This is not secure erasure of SQLite backups
or already-launched workers' contexts.

Use the directory beside the ledger, `memory/`, for generated Markdown snapshots.
Each snapshot is immutable and identified by a generated ID. Build its bounded
`MEMORY.md` index and one topic file per active note from a single database read
snapshot, then publish the directory only when complete. No shared mutable index
is overwritten during concurrent launches. IDs, not titles or supplied paths, form
filenames. Snapshot content is UTF-8 text and never executed.

The index contains title, category, scope, revision, update date and topic filename;
full bodies load on demand. Launch receives the completed snapshot's absolute path.
Existing snapshot files are not authoritative and cannot be edited to update memory.
Snapshot cleanup follows run retention: retain snapshots referenced by retained runs;
delete unreferenced completed snapshots during host maintenance. Failed unpublished
temporary snapshots can be removed on the next snapshot build.

## Worker and operator interface

Extend `python3 -m agent --db DATABASE` with:

- `memory-list --item ITEM --token-file TOKEN`: current active-note metadata.
- `memory-read --item ITEM --token-file TOKEN --id ID`: current body and provenance.
- `memory-save --item ITEM --token-file TOKEN --input NOTE.json`: create, or update
  with `id` and `expected_revision`. New notes have no caller-supplied ID.
- `memory-forget --item ITEM --token-file TOKEN --id ID --expected-revision N`:
  forget an obsolete note. Record a required reason.

All worker operations require a live claim, including reads. Chat and fix may both
maintain memory as an explicit exception to chat's prohibition on repository edits.
The exception permits these commands only; it does not permit editing shared files
or other workers' state. Save before `finish`, `await-input` or `resume-work` retires
the claim. A successful retry of an uncertain create uses a caller request ID scoped
to the work item, so it returns the original note rather than creating a duplicate;
reuse with different content is rejected.

Provide a host-only `memory-admin` command with list, read, save and forget actions
for inspection and correction without a work-item claim. Like existing operator
commands, this is a trusted-host convention, not protection against a process that
already has direct access to the ledger. Workers are explicitly forbidden to use it.

Dispatch includes the memory index path and its recall-only status. Both worker
skills read the index after the operating contract and skill instructions, then read
only relevant topic files. For current changes during a long run, use the CLI to
refresh rather than trusting an old snapshot. Save lessons when useful; absence of
a useful lesson is not an error and never blocks delivery.

Snapshot publication failure produces an explicit unavailable-memory field in the
launch message; ordinary work can continue and CLI reads remain available. Database
or write failures report failure, never a successful save. Native auto-memory is
explicitly disabled in generated runtime configuration using settings supported by
the installed CLI; verify the settings before implementation. FarmBot's recall path
is the same regardless of which runtime launches the worker.

## Integration and documentation

Expected integration points: `agent/ledger.py` for schema/transactions, a focused
`agent/memory.py` for validation/rendering, `agent/__main__.py` for commands,
`agent/scheduler.py` and `agent/dispatch.py` for startup snapshots, and
`agent/launcher.py` for explicit native-memory settings. Update chat/fix skills,
the operating contract, and usage documentation in the implementation PR.

Update the original design's Memory section to reflect this approved boundary when
implemented: operational recall is FarmBot-managed; gameplay rules remain in
Farm-Contract. Phase 2 observations/scenarios remain future work and must not be
described as an alternative source of gameplay rules.

Do not seed notes automatically from historic logs in this change. They contain
stale assumptions and require selection. No changes to GitHub identity, model
selection, QA execution, game repositories, or live service deployment are included.

## Verification

Use temporary ledgers and directories only. Add regressions for persistence across
fresh launches; Codex/Claude dispatch parity; relevant on-demand recall; concurrent
different-note writes and stale same-note conflicts; idempotent creates; revoked or
expired claims; bounded inputs and generated filenames; forgetting and regenerated
snapshots; failed snapshot publication; operator correction; and isolation from the
operator's runtime home. Verify migration of an existing ledger without losing work.

Run the warning-free Python suite. Exercise save in one worker attempt and recall
in a fresh attempt against stub Linear, with no real issue messages or Unity launch.
Report native CLI configuration verification separately from Python test results.
