# Development workflow

Develop FarmBot in an isolated checkout. Runtime worker behavior belongs in
`agent/dispatch.py`, `agent/worker_dispatch.py`, the active `skills/chat` and
`skills/work`, and their tests. Follow [the operating contract](operating-contract.md).
The current registry loads only chat and work. Archived profiles are available
only to explicit offline replay; live historical jobs need settled host adoption.

## Offline checks

Use temporary ledgers, local Git remotes, fake workers and stub Linear/Unity APIs.
`runtime="fake"` and `FARMBOT_LINEAR_STUB_DIR` select separate integrations; neither
alone makes an arbitrary invocation offline. Start with affected tests, then the
full suite when behavior changes warrant it. See [CI](ci.md).

```powershell
python -m unittest discover -s tests -p 'test_single_worker*.py' -v
python -m unittest discover -s tests -v
```

Use the installed/configured Windows Python; on macOS use `python3`. Native
process, sandbox, Unity and installation changes require the relevant platform
checks. Record skips honestly. Windows success does not certify macOS, or vice versa.
Preserve path/identity/publication security checks when correcting test fixtures.

## Portable diagnostic exports

Run `export-diagnostics` on the host that owns the instance, from a checkout
containing the command, using that host's configured interpreter and an explicit
config path. It reads the existing state and never constructs `Ledger`, migrates
the database, changes config permissions, claims work or restarts the service.
Using a development checkout for this read does not require deploying it.
As with `doctor`, SQLite may create WAL/shared-memory sidecars beside a stopped
database; the logical data and schema remain unchanged.

Create a private output directory outside the instance's `local_root` and outside
your source checkout. The directory must already exist; the ZIP filename must be
new. Paths may contain spaces and Unicode. For example:

```powershell
# Windows: use the configured Python executable; substitute the intended paths.
python -m agent.service export-diagnostics --config C:\FarmBot\config.json --issue FARM-1 --include-logs --output C:\FarmBotEvidence\FARM-1.zip
```

```sh
# macOS: use the configured Python 3 interpreter and absolute host paths.
python3 -m agent.service export-diagnostics --config /absolute/private/config.json --issue FARM-1 --include-logs --output /absolute/evidence/FARM-1.zip
```

Select either `--issue` (identifier or UUID) or `--item` (one work item UUID).
An issue selection includes its latest 10 stored jobs, including terminal jobs.
Omit both for host diagnostics alone. `--include-logs` requires a selection;
without it, no worker log content is read. No live Linear request is made.

The ZIP contains UTF-8 JSON/text that can be inspected on either platform:

- `manifest.json`: schema version, UTC collection time, scope, omissions/limits,
  and SHA-256/byte count of each exported payload file.
- `host.json`: exporting checkout's full commit and dirty flag, OS, architecture,
  Python version and instance/runtime identity.
- `heartbeat.json`: validated recorded service revision, dirty flag and freshness.
  This can differ from the exporting checkout. Neither a stale heartbeat nor a
  clean Git revision proves which uncommitted sources an existing worker loaded.
- `doctor.json`: the existing read-only, **host-wide** diagnostic report; selecting
  an issue limits job details/logs, not the host report or heartbeat.
- `jobs.json`: selected job state, target and candidate commits, checkpoint summary
  and next action, plus the latest 200 audit event names/timestamps per job.
- `logs/`, when requested: tails of `stdout.log`, `stderr.log` and
  `last_message.txt` from the latest 5 attempts per selected job on this host.
- `README.md`: interpretation and local debugging instructions.

Worker log reads are capped at 256 KiB per file and the sanitized payload budget
is 8 MiB, excluding the small manifest and ZIP overhead. A cut first line is
dropped. Missing, unsafe, non-UTF-8, oversized or omitted evidence is recorded in
the manifest. Log sources must be regular files with no symlinks, junctions or
hard links. Only the fixed filenames are collected; paths in checkpoint reports
and logs are never followed. Reads occur at different times while the service
may continue running, so this is evidence rather than a consistent runtime backup.

The exporter excludes config/database copies, claim/token files, worker homes,
prompts, issue bodies, audit details, uploads, arbitrary reports and global service
logs. It redacts known configured/environment credentials, sensitive JSON fields,
common credential patterns, authentication headers, URL userinfo/query/fragment
and known state/source/home paths before writing any bundle bytes. Redaction of
free-form text cannot recognize every secret or private fact: review before
sharing, keep bundles private and do not commit them. Ordinary exception text is
not included when a source cannot be read.

The CLI prints a JSON result with the output path. Exit `0` means the requested
evidence was collected, not that the bot is healthy or tests passed. Exit `2`
means a usable **partial** bundle was written; inspect `manifest.json`. Exit `1`
means export failed. Invalid CLI syntax also uses argparse's exit `2`, without an
output result. Unknown selections fail rather than silently exporting other jobs.

On your own computer, clone FarmBot and check out the relevant revision. Obtain
any dirty-source changes separately, then translate the failure into a regression
test using the existing temporary-state fixtures. Run affected checks locally,
submit a PR and verify both platforms in CI. Do not restore the bundle as a state
root, probe recorded PIDs on your laptop, or replay real issue payloads. Native
process and Unity behavior still need checks on the appropriate host.

## Authorized live tests

Use TestBot, a second Linear app in the same Kuaiwa AI workspace, on real issues
and the real configured repositories. Separate test workspaces, copied issue
snapshots and sandbox repositories are an abandoned design. A local test or code
change does not authorize a live test, deployment, restart or webhook change.
Honor operational authorization already given for the task.

Each instance needs separate credentials, endpoint/port, ledger, memory, clones,
worktrees, slots and logs. Choose an explicit `development` profile with pinned
Linear app/workspace IDs and a fresh stable absolute `local_root` outside checkouts.
Use [host startup](startup.md) to initialize it. Never share state roots, relabel
unmarked production state, delete ownership markers or bypass the controller lock.
A different config filename alone is not isolation. Keep secrets in private
configuration; propagate its selected absolute path to every worker.

The bots share labels and repository destinations. Mention/delegate only issues
selected by the operator, never the same issue to both bots. Comments, branches,
labels and draft PRs are real. Preserve configured private destinations, branch
checks and Git trust boundaries; test publication changes offline first.

To test conversation only, mention the app without requesting implementation.
For implementation, delegate the selected issue and state the actual scope and
stop point. One main worker owns the whole fix/feature, choosing optional native
subagents only for useful independent work. There are no A–G repository stages,
mandatory UI approval rounds or automatic baseline comparison. Repository-specific
requirements and genuine unresolved human decisions still apply.

## Tools and credentials

Use native sandboxing and automatic review. Do not introduce MXC or an automatic
fallback after a denial. Prepare required native tools and optional dependencies
on the host before acceptance; the worker does not administer the host.

Feishu reads use the configured bot identity, never a personal login. Prefer a
bot-only store or the explicitly supported `lark_cli.app_id`/`secret_env` variant.
`work` gets the configured document-reader grant; chat does not. Keep personal
profiles out of any store readable by a worker. Windows DPAPI stores are per-user:
a separate HOME does not isolate them. Do not downgrade a personal Keychain store.
Verify actual app permissions and access in the chosen worker context; an offline
version/profile check is not a successful document fetch. Do not print credentials
or raw config. Missing access becomes a concrete question after available inputs
are checked, not an invitation to change identity or permissions.

## Deployment and acceptance

Use maintenance pause, wait for fresh readiness, preserve process/resource
quiescence and deploy the full tested revision with matching controller, worker
and Monitor sources. Follow [maintenance and rollout](../README.md#maintenance-and-rollout).
Never treat HTTP health or a dead parent PID as full readiness.

Adopt only an operator-selected settled historical job. Preserve its source,
reports and actual unanswered human questions. `maintenance-resume --only-issue`
keeps every other issue paused, including after the selected job finishes.
A restart, main advancing or old unrelated failures do not require another merge
or test run. Reuse reports only while relevant inputs match.

Record the actual release, runtime, candidate commits, job/attempt and evidence.
A delivered draft is not a merge, deployment or Linear Done. Review resulting PRs
normally; FarmBot workers do not merge or change issue status. Retain database
and recovery evidence: checking out old source does not reverse schema/state.
Historical experiments are records, not current instructions or authorization.
