# Shared Worker Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Let fresh FarmBot workers recall and maintain useful notes across issues without changing gameplay authority or sharing personal CLI memory.

**Architecture:** Store notes and concurrency metadata in the existing SQLite ledger. Publish immutable Markdown snapshots for startup recall, and use the authenticated ledger CLI for current reads and mutations. Codex and Claude receive the same recall interface while retaining isolated runtime homes.

**Tech Stack:** Python 3.11+, standard library, SQLite, Markdown, unittest; existing Codex/Claude launchers.

**Spec:** `docs/superpowers/specs/2026-09-20-shared-worker-memory-design.md` (operator approved).

## Global Constraints

- Gameplay rules belong in Farm-Contract.
- Keep the fresh, isolated CLI homes. Do not import the operator's personal memories.
- Categories are `feedback`, `lesson`, and `reference`.
- Limit titles to 120 characters, bodies to 8 KiB UTF-8 and source references to 1 KiB.
- Cap active notes at 200 for this first version.
- Create starts at revision 1. Update and forget require the revision the caller read.
- All worker operations require a live claim, including reads.
- The ledger is the durable source. The shared memory directory is a generated reading view, not a second source of truth.
- Use temporary ledgers and directories only.
- No changes to GitHub identity, model selection, QA execution, game repositories, or live service deployment are included.
- Preserve repository rules: standard library only in `agent/`; tests must not access the real ledger, network, Unity or `/Applications`; one SQLite connection per test thread; warning-free suite.
- Commit messages name the writing model in a `Co-Authored-By` trailer.

## Review Focus

- A retried create after a note has been edited or forgotten must not restore old content (Task 1).
- Concurrent create operations at the 200-note boundary must not exceed the cap (Task 1).
- Chinese text and Markdown punctuation must not bypass byte limits or corrupt the startup index (Tasks 1–2).
- A process crash or concurrent launch must not publish a partial snapshot or let cleanup remove another run's snapshot (Tasks 2–3).
- An inherited runtime setting must not reenable native memory or copy personal notes into a fresh worker (Task 4).

## Current code and execution context

Worktree: `/Users/elendil/.codex/worktrees/27c7/farm-linear-agent`.
Branch: `codex/shared-worker-memory`, based on merged main `b3d60f3`.
The approved spec was committed as `507542d`; the plan records its approval.
The live service and private runtime state remain in the original checkout. Leave them alone.

`Ledger.__init__` creates tables additively. `Ledger._transaction()` uses
`BEGIN IMMEDIATE`; `_owned(item_id, token)` verifies a running item, matching claim
and unexpired lease. `agent.__main__.parser/run` exposes worker and trusted-host
commands. `Scheduler.launch` creates worktrees and a dispatch message before
`Launcher.spawn`; the launcher then creates an isolated home and saves `prompt.md`.
`dispatch_message` puts paths and metadata in a JSON payload after an authority paragraph.
Chat currently prohibits edits; its skill needs an explicit memory-command exception.

Installed runtimes checked during planning: Codex CLI 0.154.0, Claude Code 2.1.229.
With an empty temporary `CODEX_HOME`, `codex -c features.memories=false features list`
returned exit 0 and `memories stable false`. No model run or credentials were needed.
Claude's documented disable control is `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`.
References: https://learn.chatgpt.com/docs/customization/memories and
https://code.claude.com/docs/en/memory#enable-or-disable-auto-memory.
Verify the actual child environment in tests; do not claim that this verifies the
Claude binary's internal implementation or its still-unconfigured isolated authentication.

## File responsibilities

| File | Responsibility |
|---|---|
| `agent/memory.py` (new) | Note validation, safe Markdown rendering, immutable snapshot publication and pruning |
| `agent/ledger.py` | Additive tables, authenticated transactions, revision checks, idempotency and audit |
| `agent/__main__.py` | Worker memory commands and trusted-host administration |
| `agent/scheduler.py`, `agent/dispatch.py` | Publish startup snapshot and supply recall metadata |
| `agent/launcher.py` | Disable native memory in isolated workers |
| `tests/test_memory.py` (new) | Real SQLite persistence/concurrency and snapshot behavior |
| `tests/test_memory_cli.py` (new) | Real CLI subprocess behavior and operator interface |
| `tests/test_memory_integration.py` (new) | Two fresh worker attempts, shared recall, no external writes |
| `tests/test_dispatch.py`, `tests/test_scheduler.py`, `tests/test_launcher.py` | Existing integration boundaries |
| `skills/chat/SKILL.md`, `skills/fix/SKILL.md` | Recall/save workflow and authority boundary |
| `references/memory.md` (new) | Concise command examples and rules shared by both skills |
| `docs/operating-contract.md`, original design §12, `README.md` | Current behavior, corrected ownership, operator usage |

### Task 1: Durable notes with authenticated, conflict-safe writes

**Files:** Create `agent/memory.py`, `tests/test_memory.py`; modify `agent/ledger.py`.

**Interfaces:** Add these `Ledger` methods:

```python
memory_list(item_id: str, token: str) -> list[dict]
memory_read(item_id: str, token: str, note_id: str) -> dict
memory_save(item_id: str, token: str, value: dict) -> dict
memory_forget(item_id: str, token: str, note_id: str,
              expected_revision: int, reason: str) -> dict
memory_admin(action: str, *, value: dict | None = None,
             note_id: str | None = None, expected_revision: int | None = None,
             reason: str = "") -> dict | list[dict]
memory_rows() -> list[dict]  # trusted scheduler: one SELECT of active full rows
```

Use private shared helpers under one transaction for both worker and operator paths.
Workers call `_owned` inside that transaction, including on replayed creates. Operator
identity is the literal `operator` and work-item provenance is null. No caller can
set timestamps, revisions, provenance, deletion state, or an ID on a new note.

`memory_list` returns metadata only, excluding body and source text; `memory_read`
returns one full active record. All records include `id`, `revision`, `category`,
`scope`, `title`, `created_at`, `updated_at`, `created_by_item`, `updated_by_item`,
and `actor_kind`. Full records also include `body`, `source`, `build_commit`.
Times use the existing ledger clock. `memory_rows` is not a worker CLI command.

- [x] **Write persistence and conflict tests first.** Reuse `LedgerBase` from
  `test_ledger` so all state and clocks are temporary:

```python
from test_ledger import LedgerBase
from agent.ledger import LedgerError

NOTE = {"request_id": "note-1", "title": "Check the test baseline",
        "category": "lesson", "scope": "Farm-Client",
        "body": "Compare failures against the same pinned build.",
        "source": "report:test-run", "build_commit": "a" * 40}

class MemoryLedgerTests(LedgerBase):
    def test_reopen_preserves_note_and_rejects_stale_update(self):
        item = self.new_item()
        token = self.ledger.claim(item["id"], worker_id="writer")["token"]
        saved = self.ledger.memory_save(item["id"], token, NOTE)
        reopened = self.open_ledger()
        self.assertEqual(reopened.memory_read(item["id"], token, saved["id"])["body"], NOTE["body"])
        update = {k: v for k, v in NOTE.items() if k != "request_id"}
        update.update(id=saved["id"], expected_revision=1, body="Recheck on every build.")
        self.assertEqual(reopened.memory_save(item["id"], token, update)["revision"], 2)
        with self.assertRaisesRegex(LedgerError, "revision"):
            self.ledger.memory_save(item["id"], token, update)
```

- [x] **Confirm failure:** `python3 -B -W error -m unittest discover -s tests -p test_memory.py -v`.
  Expect missing memory methods, not fixture or import failures.
- [x] **Add schema and validation.** Put validators/constants in `agent/memory.py`
  (raise `ValueError`, translated to `LedgerError` by ledger methods; no circular import).
  Add `memories` and `memory_requests` tables without altering existing work data:

```sql
CREATE TABLE IF NOT EXISTS memories (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, category TEXT NOT NULL,
 scope TEXT NOT NULL, body TEXT NOT NULL, source TEXT NOT NULL,
 build_commit TEXT, created_by_item TEXT, updated_by_item TEXT,
 actor_kind TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
 revision INTEGER NOT NULL, deleted INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS memory_requests (
 actor TEXT NOT NULL, request_id TEXT NOT NULL, payload_hash TEXT NOT NULL,
 note_id TEXT NOT NULL REFERENCES memories(id), PRIMARY KEY(actor, request_id)
);
```

  Supported scopes: `global`, `Farm-Client`, `farm-hive`, `farmgui`, `common`,
  `Farm-Contract`. Require nonblank title/body/source; reject CR/LF/control characters
  in titles. Reject unknown JSON keys and non-dict input. Check UTF-8 byte lengths
  for body/source, full lowercase 40-hex SHA when present, and positive integer
  revisions (reject bool). Request IDs are 1–80 ASCII letters/digits/`._-`.
  Update accepts a complete replacement of editable fields, plus `id` and
  `expected_revision`; it does not accept `request_id`.
- [x] **Implement transaction behavior.** Count active rows and insert within the
  same transaction. Generate UUID IDs. SHA-256 the normalized create payload
  excluding request ID; the actor key is the item ID or `operator`. Identical create
  replay returns the current note with `replayed: true`; conflicting content fails.
  If forgotten, return only `{id, revision, deleted: true, replayed: true}` and never
  restore it. Ordinary save returns the full current row plus `replayed: false`.
  Update checks ID, active state and revision before changing content. Audit only
  ID, revision, action and actor: never body, title, source or a token.
  Forget requires a nonblank reason of at most 500 characters, increments revision,
  clears title/body/source/build_commit, and keeps the tombstone and idempotency
  mapping. Treat reason as operator-facing audit metadata; warn against secrets.
- [x] **Add remaining behavioral tests.** Cancelled, expired, queued and wrong-item
  tokens fail every worker method without changing the DB. Operator actions work
  without a claim and identify the operator. Replay after update returns the edited
  content; replay after forget returns the tombstone. Another item using the same
  request ID creates its own note. Test Chinese body at/beyond the byte bound,
  forged provenance, invalid UUID, malformed category/scope and bool revisions.
  Test migration by populating issue/item/audit rows, dropping only new tables,
  reopening and verifying the old rows and state are unchanged.
- [x] **Test real concurrent connections.** With `threading.Barrier`, make two
  threads open their own `Ledger` on one temporary DB. Distinct creates both
  persist. Concurrent revision-1 updates yield one success and one conflict.
  With 199 notes, two concurrent creates yield exactly 200 active notes. Join all
  threads and surface exceptions to the main test, rather than swallowing them.
- [x] **Verify and commit:** run the focused memory and existing ledger suites;
  commit as `feat: persist shared worker memory with revision checks`.

### Task 2: Immutable, bounded Markdown reading snapshots

**Files:** Modify `agent/memory.py`, `tests/test_memory.py`.

**Interfaces:**

```python
publish_snapshot(root: Path, rows: list[dict]) -> dict
# {status: "ready", snapshot_id: str, index: absolute_path, count: int}
prune_snapshots(root: Path, runs_root: Path) -> dict
# {removed: list[str], retained: list[str]}; offline operator only
```

The scheduler supplies `Path(db_path).parent / "memory"` and `ledger.memory_rows()`.
Read all active rows in one SELECT; finish that read before filesystem work. UUID
snapshot and note IDs are the only dynamic filename components. Each published
directory contains `MEMORY.md` and `<note-id>.md` files. Publish by renaming a complete
temporary sibling directory into an unused UUID directory; never overwrite a snapshot.

- [x] **Write a failing snapshot test:**

```python
def test_snapshot_has_index_and_lazy_detail(self):
    item = self.new_item()
    token = self.ledger.claim(item["id"], worker_id="writer")["token"]
    note = self.ledger.memory_save(item["id"], token, NOTE)
    view = publish_snapshot(Path(self.tmp.name) / "memory", self.ledger.memory_rows())
    index = Path(view["index"])
    self.assertNotIn(NOTE["body"], index.read_text(encoding="utf-8"))
    self.assertIn(NOTE["body"], (index.parent / (note["id"] + ".md")).read_text(encoding="utf-8"))
```

- [x] **Confirm failure** with the focused memory suite.
- [x] **Implement rendering/publication.** Use a static recall-only warning at the
  beginning of index and topic files. Render a one-line index entry per note with
  escaped title, category, scope, revision, UTC update time and UUID filename.
  Sort by scope/title/ID. At most 200 entries and 128 KiB index bytes; fail explicitly
  if rendering violates the bound, never omit a note silently. Topic files include
  source, optional build, provenance and timestamps before the Markdown body.
  Build empty snapshots too. Ensure relative roots resolve to absolute paths.
  Create private directories/files (0700/0600 on POSIX); Windows uses platform
  permissions without POSIX assertions. Clean the current temporary directory on
  ordinary failure and re-raise the error. Reject symlinked memory roots and do not
  follow symlinks while pruning. No templated value becomes a command or include.
- [x] **Test publication semantics.** Inject an `OSError` while writing the second
  topic; no completed snapshot may appear. Two threads publishing independently
  produce complete distinct directories. Editing a snapshot never changes the
  ledger; subsequent snapshots reflect DB content. Forgetting removes a note from
  new snapshots while an old snapshot remains an explicitly historical artifact.
  Punctuation (`[]|<>`) and Chinese titles preserve one entry per line. A corrupted
  stored ID cannot escape the snapshot directory.
- [x] **Implement offline pruning.** Scan retained `<runs_root>/*/*/prompt.md`
  files, parse the existing authority-plus-JSON format, and retain every snapshot
  named by `payload.memory.index`. Old prompts without `memory` are valid. A
  malformed prompt or inaccessible runs root aborts before deleting anything.
  Prune only generated UUID directories and `.tmp-<uuid>` siblings; ignore unrelated
  files and reject symlinks. Collect/validate the full retention set before any
  deletion. The CLI in Task 3 requires a stopped service and refuses pruning while
  there are queued/running items; no pruning runs inside launch or scheduler ticks.
  A crash between snapshot publication and prompt creation leaves an unreferenced
  directory that offline maintenance can remove. Concurrent launches never prune.
- [x] **Test pruning:** retained prompt preserves its directory; unreferenced
  completed and temporary directories disappear; malformed prompt preserves all;
  old prompt without memory succeeds; symlink and unrelated directory stay untouched.
- [x] **Verify and commit:** focused memory suite; commit as
  `feat: render shared memory as immutable Markdown snapshots`.

### Task 3: Worker memory commands and host administration

**Files:** Modify `agent/__main__.py`; create `tests/test_memory_cli.py`.

**Interfaces:** Expose the four worker commands in the spec, using existing
`--item`, `--token-file` and `resolve_token`. Forget also requires `--reason`.
Expose `memory-admin list|read|save|forget|prune-snapshots` with action-specific
arguments; read needs `--id`, save needs `--input`, forget needs `--id`,
`--expected-revision`, `--reason`, and prune needs an absolute `--runs-root`.
JSON output and nonzero error handling follow current CLI conventions.

- [x] **Write subprocess tests.** Reuse `CliTests` fixtures with a new test class,
  or extract only helpers locally to avoid accidentally rerunning inherited tests.
  Seed an item and claim it, write its token into a temporary mode-0600 file, then:

```python
saved = self.run_cli("memory-save", "--item", item, "--token-file", str(token_file),
                     "--input", self.json_file("note.json", NOTE))
notes = self.run_cli("memory-list", "--item", item, "--token-file", str(token_file))
self.assertEqual([n["id"] for n in notes], [saved["id"]])
self.assertNotIn("body", notes[0])
read = self.run_cli("memory-read", "--item", item, "--token-file", str(token_file),
                   "--id", saved["id"])
self.assertEqual(read["body"], NOTE["body"])
self.assertEqual(self.calls(), [])
```

- [x] **Confirm parser failure:** `python3 -B -W error -m unittest discover -s tests -p test_memory_cli.py -v`.
- [x] **Wire commands without new Linear calls or implicit lease renewal:**

```python
if c == "memory-list":
    return ledger.memory_list(args.item, resolve_token(args))
if c == "memory-read":
    return ledger.memory_read(args.item, resolve_token(args), args.id)
if c == "memory-save":
    return ledger.memory_save(args.item, resolve_token(args), read_json(args.input))
if c == "memory-forget":
    return ledger.memory_forget(args.item, resolve_token(args), args.id,
                                args.expected_revision, args.reason)
```

  Bound memory input files to 16 KiB before JSON parsing; do not change general
  `read_json` behavior for unrelated commands. Admin uses the same validators and
  revision rules. For prune, check queued/running items, require an existing runs
  directory, and use only `Path(args.db).resolve().parent / "memory"` as prune root.
  Print a refusal explaining the stopped-service maintenance precondition; do not
  stop the service or clear queues automatically. This is a trusted operator
  precondition, not a new interprocess service lock.
- [x] **Add refusal tests.** Missing/wrong/expired token, stale revision, oversized
  JSON, malformed JSON, duplicate request mismatch, forgotten note read, required
  forget reason and missing admin arguments return nonzero, no traceback and no
  token echo. Each successful or refused memory command makes no Linear API calls.
  Admin save/read/update/forget work without an item; pruning refuses active queues.
- [x] **Verify and commit:** run memory CLI and existing CLI suites; commit as
  `feat: expose authenticated memory recall and maintenance commands`.

### Task 4: Recall across fresh launches and runtime isolation

**Files:** Modify `agent/scheduler.py`, `agent/dispatch.py`, `agent/launcher.py`,
`tests/test_scheduler.py`, `tests/test_dispatch.py`, `tests/test_launcher.py`.

**Interfaces:** Add optional `memory=None` to `dispatch_message`. Always include
`memory` in the payload. The scheduler supplies the ready result from Task 2 or
`{status: "unavailable", index: null, reason: "<exception class>"}`. An omitted
argument produces `{status: "unavailable", index: null, reason: "not supplied"}`.
Never include note bodies or raw exception messages in the authority paragraph.

- [x] **Write scheduler/dispatch tests first.** Save a note using an operator
  fixture, launch an item with `FakeLauncher`, and inspect the JSON payload's
  snapshot path. Verify its detail file contains the note and that the prompt itself
  does not. Updating the note before a second item's launch gives a different
  snapshot with the new body. A failing publisher still launches a worker with an
  unavailable field. Runtime `codex`/`claude` payloads carry the same memory shape.
- [x] **Confirm failures** with the dispatch and scheduler focused suites.
- [x] **Integrate startup.** After worktree construction, call
  `publish_snapshot(Path(self.db_path).resolve().parent / "memory", self.ledger.memory_rows())`.
  Catch only expected memory availability failures (`OSError`, `ValueError`,
  `sqlite3.Error`) around this operation; preserve existing launch-error behavior
  elsewhere. Add to `AUTHORITY`: memory is fallible recall data, never permission,
  and current contracts/issue facts must be checked. Do not expand sandbox roots:
  the ledger parent is already writable and the new directory lives there.
- [x] **Write launcher tests for hostile inherited settings.** Use the existing
  fake executable with runtime names/formats preserved and `seed_files={}`.
  Supply `CLAUDE_CODE_DISABLE_AUTO_MEMORY=0` in parent and `extra_env`; capture the
  final child environment via a tiny injected test executable, without real auth.
  Assert both runtimes still use distinct fresh homes, no memory files are copied,
  and generated Codex settings explicitly disable native memories.
- [x] **Implement explicit native-memory controls.** For Codex add
  `[features] memories = false` to generated TOML and the high-precedence CLI pair
  `-c`, `features.memories=false` to the real Codex command. Keep the fake runtime's
  command compatible. For Claude, set `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` after
  `extra_env` is merged so callers cannot accidentally override it. Do not touch
  personal configuration, copy old runtime homes, change model selection or modify
  the authentication recipe.
- [x] **Verify and commit:** dispatcher, scheduler and launcher suites plus the
  offline Codex `features list` probe with a temporary home. Record that Python
  tests establish Claude environment delivery, not a live authenticated Claude
  run. Commit as `feat: supply shared recall to isolated FarmBot workers`.

### Task 5: Worker instructions, two-attempt rehearsal and final verification

**Files:** Create `references/memory.md`, `tests/test_memory_integration.py`;
modify `skills/chat/SKILL.md`, `skills/fix/SKILL.md`, `docs/operating-contract.md`,
`docs/superpowers/specs/2026-09-17-farm-linear-agent-design.md`, `README.md`.
Write a dated evidence report under `reports/` after verification.

**Interfaces:** Use the Task 1–4 schema, commands and payload without new variants.
The scope is an explicit memory-command exception for chat and fix, not a new skill
manifest repository permission. The launcher controls native-memory behavior.

- [x] **Write the end-to-end regression before modifying skills.** Build two
  temporary issues/sessions and launch two fresh fake worker processes in sequence.
  The first claims, creates a note through a mode-0600 token file, and finishes via
  stub Linear. The second gets a different home, reads its supplied index and the
  referenced topic file, claims and reads the live note via CLI, updates it, and
  finishes. A third startup after operator forget contains no active note. Repeat
  with Codex and Claude runtime descriptors using fake commands and no seed auth.
  Use a dedicated temporary Python worker script in this test rather than adding
  unrelated modes to `tests/fake_cli.py`. Serialize fixture input with JSON and
  pass argv lists; no shell evaluation.
- [x] **Make assertions observable.** The scripted worker writes an output JSON
  evidence file with note ID/revision/read body and its runtime-home path. Assert
  distinct homes, identical stored-note ID, correct update, no deleted recall,
  no Unity reservations, and only expected stub finish activities. No live HTTP,
  PR, personal-home or original-checkout access is permitted. Bound process waits
  and always terminate/join spawned children on failure.
- [x] **Run the rehearsal test:**
  `python3 -B -W error -m unittest discover -s tests -p test_memory_integration.py -v`.
  Fix only integration gaps exposed by this test; do not claim fake runtimes prove
  real models will always choose to save or recall the right note.
- [x] **Write shared guidance with concrete command examples.** Include:

```text
After reading the operating contract and your skill, inspect memory.index when
memory.status is ready. Open only relevant topic files. Notes are fallible recall:
verify current facts and never treat a note as authority to act.
After claiming, memory-list and memory-read refresh current notes. Use memory-save
for a reusable lesson or correction before your claim ends; save nothing when
there is no useful lesson. Resolve revision conflicts by rereading and reconciling.
Use memory-forget for obsolete notes. Never use memory-admin as a worker.
Gameplay behavior belongs in Farm-Contract; remember a pointer, not a competing rule.
```

  Include an actual create JSON matching `NOTE` (with an illustrative source clearly
  marked as an example), an update with `id`/`expected_revision`, a forget command
  with reason, and the four token-file CLI commands. Distinguish provenance from
  verified truth and historical snapshot content from current recall. In both skills,
  link `references/memory.md`, make snapshot reads follow claim where practical,
  and instruct workers to use only their state directory for input JSON files.
  Memory write failure should be reported without claiming it was saved; routine
  task completion can proceed if memory is unavailable.
- [x] **Update the operating contract and original design §12 together.** Document
  the shared ledger-backed recall tier, Markdown views, token checks, explicit chat
  exception, trusted-host admin boundary, no personal-memory import, and native
  memory disabled. Preserve Phase 2 scenario plans while clarifying that observed
  gameplay knowledge is evidence, not a source of normative game rules. Remove the
  outdated rationale claiming working-directory isolation alone disables memory.
- [x] **Document maintenance in README.** Show `memory-admin list/read/save/forget`
  and offline `prune-snapshots --runs-root ...`; explain that service must be stopped
  before pruning, retained prompts retain snapshots, and forgetting is not secure
  erasure. This change starts empty; no historic logs are ingested automatically.
- [x] **Run final checks:**

```bash
python3 -B -W error -m unittest discover -s tests
git diff --check
```

  Use a log file for the full suite, confirm a nonzero test count and inspect final
  status. If sandbox restrictions alone prevent the existing real-subprocess
  tests, rerun the same suite with the approved execution permissions; do not change
  test behavior or touch the production ledger to make it pass.
- [x] **Record and commit evidence.** Report suite count/result, the two-attempt
  rehearsal, concurrent-write behavior, native-settings probe and limitations.
  Preserve the distinction between fake-runtime tests and a live model smoke test.
  Commit as `docs: define shared memory workflow and record verification`.

## Completion and review

- [x] Review the complete branch against the approved spec, especially claims that
  memory changes authority, is verified fact, or erases historical artifacts.
- [x] Verify all commits are confined to FarmBot and all temporary fixtures are
  cleaned up. No live service restart, game repository edit, or Linear post.
- [x] Report implementation/test results and branch state. Integrate via the
  user's chosen development-branch workflow; creating/merging a PR or deploying
  the service is a separate action from this plan's local implementation.

## Plan self-review

Spec coverage: purpose/authority → Tasks 4–5; data/provenance/limits/concurrency/
forgetting → Task 1; snapshots and retention → Task 2; worker/operator interfaces →
Task 3; startup/isolation/failure handling → Task 4; documentation and fresh-worker
verification → Task 5. All five review-focus conditions have explicit test cases.
No new server, external dependency, gameplay rule store or automatic historical
ingestion is introduced.
