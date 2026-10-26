# farm-linear-agent

One Linear agent for the 农场 team. Delegate an issue for work; @mention it to
talk. The default profiles are `chat` and `work`. One main worker owns each bug fix or
feature across all necessary repositories, UI changes, tests and draft delivery.

The worker stays alive while waiting for Unity or server services. It tests the
candidate first; baseline testing is optional diagnosis. There are no compulsory
repository handoffs, presentation subtasks or semantic closeout approvals.
Independent issues share the configured `max_concurrent` worker limit.
The main worker chooses when
native Codex subagents help and remains responsible for integration and delivery.

Read [the operating contract](docs/operating-contract.md),
[the migration guide](docs/general-workflow.md) and
[the worker CLI](references/single-worker-cli.md). Historical jobs require
explicit settled adoption. Checking out this revision changes no running service
or Linear issue.

## Local development

Use an isolated checkout and follow [AGENTS.md](AGENTS.md). Ordinary tests use
temporary state, local Git remotes, fake workers and stub APIs, with no live
Unity or Linear access.

```powershell
# Windows: use the installed/configured Python executable.
python -m unittest discover -s tests -p "test_single_worker*.py" -v
python -m unittest discover -s tests -v
python -m unittest discover -s tests -p test_windows_workers.py -v
```

On macOS use `python3`. Native Windows results cannot be inferred from Mac
results. The CI also tests and cross-builds the fixed Go service relay.

Live testing uses a separately configured TestBot in the same real workspace,
only on operator-selected issues. Follow
[the development workflow](docs/development-workflow.md); neither a code change
nor `runtime="fake"` by itself authorizes or isolates a live test.

## Host operations

[The startup guide](docs/startup.md) covers ownership markers, state roots,
configured repositories and resources. [Runtime preparation](docs/codex-runtime.md)
covers installing a dedicated Codex runtime. Preserve the native sandbox,
automatic review, Git trust boundary and owned-process containment.

```powershell
python -m agent.service configure
python -m agent.service serve --config C:\FarmBot\config.json
python -m agent.service enqueue --config C:\FarmBot\config.json --issue FARM-1
python -m agent.service doctor --config C:\FarmBot\config.json
```

The config must select the intended instance and stable absolute `local_root`.
Worker processes inherit the selected absolute config path. Keep credentials and
host paths in private configuration. Settle and restart before changing that
configuration. New intake uses `enabled_skills: ["chat", "work"]`, or that default.
All five source repositories must be configured for write work. Optional
document/UI/server tools report their availability; unrelated work does not need
every optional tool.

Use read-only `doctor`, Monitor, heartbeat and read-only SQLite snapshots for
live inspection. Constructing a production Ledger can migrate its database.

For investigation on another Mac or PC, `export-diagnostics` creates a private
ZIP with redacted diagnostics, revision information and selected job history.
Add `--include-logs` for bounded worker log tails:

```powershell
python -m agent.service export-diagnostics --config C:\FarmBot\config.json --issue FARM-1 --include-logs --output C:\FarmBotEvidence\FARM-1.zip
```

Create the output directory first, outside the instance's state root, and choose
a new filename. On macOS use `python3` and absolute macOS paths. Review the bundle
before sharing; free-form logs can retain private content. See
[portable diagnostic exports](docs/development-workflow.md#portable-diagnostic-exports)
for contents, limits, exit codes and the local debugging workflow.

## Maintenance and rollout

For scenarios requiring both candidates online, use
[single Client/game-server verification](references/client-server-integration.md).
It atomically reserves Unity plus isolated server services for the same worker.
Idle fixed Editors may yield verified memory capacity to waiting server work;
busy Editors retain their leases. This adds no deployment authority.

A release or live restart requires operational authorization. For a planned stop:

```powershell
python -m agent.service maintenance-pause --config C:\FarmBot\config.json --reason "planned upgrade"
python -m agent.service maintenance-status --config C:\FarmBot\config.json
```

Wait for fresh `ready_to_stop: true`. Workers preserve progress and reports,
release resources safely, acknowledge maintenance and exit. Stale heartbeat,
HTTP health or a dead parent PID cannot prove quiescence. Never remove a lease,
kill an unverified process or release a busy shared Editor to force readiness.

To let selected issues finish while others cooperatively pause, use
`python -m agent.service maintenance-pause --config C:\FarmBot\config.json --reason "finish priority work" --except-issue FARM-1`
on Windows (`python3` on macOS).
Repeat `--except-issue` for a selected set. Selected claims remain active; other
workers receive maintenance in renew/resource-status and safely hand off. This is
not readiness to stop the service. Ordinary `maintenance-resume` removes this pause
without changing earlier admission restrictions or human-input waits.

After an authorized complete release, verify the actual running revision,
configuration, worker source and resource health. A selected settled historical
job can be adopted while maintenance is requested:

```powershell
python -m agent.service adopt-single-worker --config C:\FarmBot\config.json --item ITEM --reason "Continue retained candidate and reports"
python -m agent.service maintenance-resume --config C:\FarmBot\config.json --only-issue FARM-1614
```

Adoption preserves the item, source, reports and real unresolved human questions.
It does not itself resume the queue or grant an answer. Other issues stay paused
under `--only-issue`, including after the selected issue finishes. Only an explicit
ordinary `maintenance-resume` removes that restriction.

Repeat `--only-issue` to admit a selected group concurrently, for example
`maintenance-resume --config C:\FarmBot\config.json --only-issue FARM-1 --only-issue FARM-2`.
Each issue retains its own main worker, claim and worktrees. Waiting for Unity or
server services keeps that worker's seat; resources still use their existing
capacity, FIFO and quiescence checks. Unlisted issues remain paused after restart
and after every selected issue finishes.

See [migration and recovery](docs/operating-contract.md#migration-and-recovery)
before upgrading or rolling back. Old code cannot simply resume new-contract
jobs; a Git checkout does not reverse the schema or adopted job state.

## Worker cache cleanup

`serve` automatically sweeps disposable worker caches at startup and hourly,
keeping them for at least 24 hours after verified teardown. It retains logs,
reports, checkpoints, source snapshots and recovery data indefinitely. Per-host
configuration can change the grace period/interval or disable this sweep:

```json
"worker_retention": {"enabled": true, "keep_hours": 24, "sweep_seconds": 3600}
```

The fixed `STATE_DIR/uploads` cache also expires downloaded ZIPs and their
extracted files, after every attempt for the job has exited and the grace period
has passed. Only manifest-listed files whose size and SHA-256 still match are
removed. The manifest, hashes, review reports and derived images are preserved;
workers keep reports in `STATE_DIR/reports` and refetch expired inputs as needed.
Custom historical download directories are not inferred or swept.

New workers reuse item-owned `GOCACHE` after verified prior-attempt teardown;
`GOTMPDIR` and `FARMBOT_BUILD_ROOT` remain attempt-local.
Only regenerable build outputs belong there; durable evidence belongs elsewhere
under `state_dir`. Known Codex download caches and the earlier controller-managed
`server-tests/go-cache` and `server-tests/tmp` also expire. Other historical build
folders, shared caches and Unity Libraries are never guessed or swept. Active or
uncertain workers hold cleanup. Each attempt keeps a `cache-retention.json` audit.
See the [retention boundaries](docs/operating-contract.md#automatic-worker-cache-retention).
This takes effect when the changed service is deployed; editing this checkout does
not change the running instance.

## Further references

- [FARM-1693 continuation and measured startup timing](docs/diagnostics/2026-10-23-farm1693-continuation.md)
- [Go dependency preparation](docs/go-dependency-preparation.md)
- [Current resource and authority boundaries](docs/operating-contract.md#resource-ownership)

Historical material lives under `docs/legacy`, `docs/superpowers`,
`skills/legacy` and `references/legacy`. It is excluded from current worker
entry points and cannot re-enable retired procedures.
