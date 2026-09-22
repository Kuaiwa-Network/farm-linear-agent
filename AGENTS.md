# FarmBot development guidance

These instructions apply to agents developing this repository. FarmBot's runtime
workers receive their task instructions through `agent/dispatch.py` and `skills/`;
keep runtime authority and behavior changes in those sources and their tests.

## Read first

- [README.md](README.md): setup, commands and host diagnostics.
- [Operating contract](docs/operating-contract.md): intended current behavior and
  authority boundaries. Check the implementation when documentation disagrees.
- [Worker CLI reference](references/worker-cli.md): command authentication and
  checkpoint formats; consult when changing worker instructions or CLI behavior.
- [Development workflow](docs/development-workflow.md): approved test-workspace
  snapshot workflow, current prerequisites and explicit promotion of useful changes.
- [Development and release proposal](docs/superpowers/plans/2026-09-22-cross-platform-development-and-release.md):
  proposed Mac development and Windows release workflow. Unchecked phases are not
  implemented capabilities or authorization to execute the whole plan.

## Project map

- `agent/service.py`, `agent/config.py`: service composition, CLI and host config.
- `agent/receiver.py`, `agent/router.py`, `agent/linear_api.py`: webhook validation,
  routing and Linear communication.
- `agent/ledger.py`, `agent/lifecycle.py`, `agent/memory.py`: durable work, issue
  reconciliation and shared worker memory.
- `agent/scheduler.py`, `agent/launcher.py`, `agent/windows_job.py`,
  `agent/windows_worker_gate.py`: scheduling and owned worker processes.
- `agent/worktrees.py`, `agent/publication.py`: isolated repositories and publishing
  destination verification.
- `agent/slots.py`, `agent/unity.py`, `agent/unity_mcp.py`, `agent/identity.py`: Unity
  resource ownership, Editor discovery, MCP and verification evidence.
- `agent/__main__.py`, `agent/dispatch.py`, `skills/`, `references/`: worker-facing
  CLI, launch context and instructions.
- `agent/deploy.py`, `agent/doctor.py`: installation and diagnostics.
- `tests/`: unittest suite, fake CLI, local Git fixtures and mocked integrations.

## Development and production

- Use an isolated checkout for development. Treat the running production revision
  and its state as independent of this checkout.
- A code change or test request does not authorize production deployment, service
  restart, webhook changes, live issue mutations or production resource cleanup.
  Honor operational authorization already given for the task; do not ask again
  for routine work within that scope.
- Keep credentials and host-specific paths in private config, normally under the
  ignored `.local/` directory. Never commit secrets, claim tokens or private state.
- For authorized live testing, use dedicated test credentials, issues and publishing
  destinations, with separate ledgers, memory, clones, worktrees, slots and logs.
  A different config filename alone does not isolate these resources: set an
  explicit absolute `local_root` and verify its derived paths.
- The config loader records the selected absolute file path. Services propagate it
  to every worker attempt and installed launchd commands, overriding conflicting
  inherited `FARMBOT_CONFIG`. Keep that guarantee when changing configuration handling.
  This selects a file, not an immutable snapshot; restart a settled service after
  editing its config so controller and workers do not observe different contents.
- `runtime="fake"` and `FARMBOT_LINEAR_STUB_DIR` control separate integrations.
  Neither alone makes an arbitrary service invocation offline. Prefer the existing
  test fixtures, which also isolate repositories and Unity dependencies.
- Inspect live state through the read-only `doctor` command with the intended
  host's config. Constructing `Ledger` can create or migrate its database. Do not
  copy Mac runtime state to Windows or infer remote process ownership from local PIDs.

## Cross-platform implementation

- Support macOS development and native Windows execution from one codebase. Keep
  OS-specific process and installation behavior behind focused helpers.
- Use `pathlib`, `os.pathsep`, explicit UTF-8 for text, and subprocess argument lists.
  Test paths with spaces and Unicode. Avoid hard-coded personal paths and implicit
  assumptions about shells, separators, permissions, symlinks or POSIX signals.
- Use `sys.executable` for Python child processes. Documentation must distinguish
  macOS `python3` from the configured Windows Python executable.
- Preserve Windows Job Object containment and POSIX ownership checks. Stop only
  verified owned processes; a dead parent does not prove its descendants exited.
- Keep shared Unity Editors outside worker containment. Release slots only after
  the existing quiescence checks; preserve recovery evidence when cleanup is uncertain.
- Mac test results do not establish Windows compatibility. Changes to native
  process handling, deployment or Unity need the relevant Windows checks.

## Validation

Run commands from the repository root with a suitable Python interpreter and Git
available. Some repository/slot scenarios also require Git LFS.

```sh
# macOS: focused checks, then the full offline suite when the change warrants it
python3 -m unittest discover -s tests -p 'test_receiver.py' -v
python3 -m unittest discover -s tests -v
```

```powershell
# Windows: use the installed/configured Python interpreter
python -m unittest discover -s tests -v
python -m unittest discover -s tests -p 'test_windows_workers.py' -v
```

- Add regression coverage for changed behavior, using temporary state, fake workers,
  stub APIs and local Git remotes. Keep ordinary tests independent of production
  credentials, model services and real Unity Editors.
- The offline suite uses localhost listeners and process inspection. Distinguish
  sandbox restrictions from application failures; do not weaken checks to hide them.
- Report the commands run, results and platform skips. Do not present skipped
  Windows tests as Windows verification or an HTTP health response as full readiness.
- For documentation-only edits, check accuracy, links and whitespace; a full test
  rerun is unnecessary unless executable behavior changes.

## Change discipline

- Preserve signature/identity validation, delegation, claim fencing, configured
  publishing destinations and recovery evidence. Test changes at those boundaries.
- Follow existing standard-library and unittest patterns. Keep changes focused;
  introduce dependencies or architecture changes only when the task needs them.
- Update the operating contract when behavior changes. Keep proposals, historical
  reports and measured results distinct from current operational guarantees.
- For schema or release changes, document migration and recovery implications.
  Never assume checking out older code reverses a database migration.
