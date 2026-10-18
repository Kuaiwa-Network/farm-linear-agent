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

## Maintenance and rollout

A release or live restart requires operational authorization. For a planned stop:

```powershell
python -m agent.service maintenance-pause --config C:\FarmBot\config.json --reason "planned upgrade"
python -m agent.service maintenance-status --config C:\FarmBot\config.json
```

Wait for fresh `ready_to_stop: true`. Workers preserve progress and reports,
release resources safely, acknowledge maintenance and exit. Stale heartbeat,
HTTP health or a dead parent PID cannot prove quiescence. Never remove a lease,
kill an unverified process or release a busy shared Editor to force readiness.

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

## Further references

- [Go dependency preparation](docs/go-dependency-preparation.md)
- [Current resource and authority boundaries](docs/operating-contract.md#resource-ownership)

Historical material lives under `docs/legacy`, `docs/superpowers`,
`skills/legacy` and `references/legacy`. It is excluded from current worker
entry points and cannot re-enable retired procedures.
