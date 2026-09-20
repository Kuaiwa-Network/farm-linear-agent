# Shared worker memory verification

Implementation branch: `codex/shared-worker-memory`, based on main `b3d60f3`.
The live service was not restarted or changed. The new memory store starts empty.

## Result

FarmBot owns shared recall in its existing SQLite ledger. Fresh Codex and Claude
workers receive immutable Markdown indexes and topic files while keeping their
isolated runtime homes. Authenticated CLI commands read, save, correct and forget
notes. Revisions prevent lost updates; item-scoped request IDs prevent duplicate
creates and cannot resurrect a forgotten note. Gameplay rules remain in Farm-Contract.

## Evidence

- Baseline: 347 tests passed, warning-free, in 82.155 seconds.
- Task 1: new persistence/claim/concurrency tests failed on missing memory methods,
  then passed; 80 memory and existing ledger tests passed together.
- Task 2: publication/retention tests failed on missing snapshot functions, then
  passed; 14 memory tests passed, including partial-write failure and two publishers.
- Task 3: four subprocess CLI tests failed on unavailable commands, then passed;
  21 memory and existing CLI tests passed together. Memory operations made no Linear
  calls and did not extend claim leases.
- Task 4: dispatch/scheduler/native-memory tests failed on missing integration, then
  passed; 64 dispatch, scheduler and launcher tests passed together.
- Fresh-process rehearsal: six scripted workers, three each using the real Codex
  and Claude runtime descriptors with fake executable/auth-free substitutions.
  Save → recall/update in a new home → forget → empty recall in a third home passed
  with real SQLite and CLI subprocesses. Only expected stub Linear response
  activities occurred; no Unity reservation, real issue update or PR was created.
- Full suite: `python3 -B -W error -m unittest discover -s tests` passed **370 tests**
  in **86.938 seconds**. `git diff --check` passed.
- Installed Codex CLI 0.154.0 accepted `features.memories=false` in a fresh temporary
  home; its feature listing reported native memory disabled. Launcher tests verify
  the Claude disable environment variable reaches the child even when inherited
  environment and extra overrides try to enable it.

An initial sandboxed launcher-suite run failed two existing process-inspection tests
and emitted subprocess cleanup warnings. The same 64 tests passed with the
process-inspection permissions used for the baseline; no production code change
was made to disguise that environmental failure.

## Limits

This proves storage, claim checks, snapshot publication and runtime wiring, not
that a language model will always select the right lesson to save or recall. No
live model smoke run or authenticated Claude worker was used. Claude's isolated
authentication setup remains outside this change. Native memory is disabled; no
personal memories or historic logs were ingested.

Notes are attributed, fallible recall, not verified gameplay facts or authority.
The host administration command follows the repository's trusted-operator
convention and is not a security boundary against direct SQLite access. Snapshot
pruning requires a stopped service and the correct absolute runs directory.
Forgetting clears current recall but not backups, retained historical snapshots,
or context already read by a running worker.
