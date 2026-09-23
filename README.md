# farm-linear-agent

One Linear agent for the 农场 team. Delegate an issue to it for work, @mention it to
talk. Capabilities are added as skills on a shared identity, ledger, worker runtime and
desktop-resource locks: chat, QA, bug fix, FGUI, then whole features.

FarmBot keeps one conversation across read-only investigation and writable repair execution.
An authorized “fix it” reply can start the first repair or resume previous work without
re-delegating. The host carries messages and findings across the change of execution profile
and posts recorded session progress every ten minutes while work is active or queued.

Start with the [design spec](docs/superpowers/specs/2026-09-17-farm-linear-agent-design.md).
It records the decisions, the architecture, the porting map from the two prototypes it
replaces (`FarmTestAgent` and `BugAgent`), and the phased plan.

## Run

```bash
python3 -m unittest discover -s tests -v          # all offline tests
python3 -m agent.service configure                 # once per host; writes .local/agent/config.json
python3 -m agent.service serve                     # receiver on 127.0.0.1:8765 plus scheduler
python3 -m agent.service enqueue --issue FARM-1    # create work with no webhook; still needs delegation
python3 -m agent.service slots                     # the pool: slot states, parked commits, open reservations
python3 -m agent.service doctor                    # read-only JSON diagnostics for this host
python3 -m agent --db .local/agent/ledger.sqlite3 status
```

Before the first `serve` on a new host: point the Linear app's webhook at a tunnel to port 8765
(`cloudflared tunnel --url http://127.0.0.1:8765`), run `python3 -m agent.service seed-clones --from ~/WorkSpaces/Farm`
so the bare clones exist before the first launch instead of being fetched inside a scheduler tick, and on a
python.org Python make sure `etc/openssl/cert.pem` exists (symlink `/etc/ssl/cert.pem`). Delegate from the
Linear UI; delegating through the API creates no agent session. Live record:
`docs/superpowers/spikes/2026-09-18-mac-live-smoke.md`.

To keep FarmBot running across reboots on macOS:

```bash
python3 -m agent.service install-launchd
```

Run it from your normal interactive shell: a launchd job inherits only `/usr/bin:/bin:/usr/sbin:/sbin`,
which contains no `codex`, `gh` or `cloudflared`, so the command captures the PATH of the shell that ran it
and writes that into both agents. It refuses to write anything when the configured runtime or `cloudflared`
is not on that PATH. It writes two launchd agents and prints the `launchctl bootstrap` lines to load them.
Logs land in `.local/agent/logs/`. With no `tunnel` key in the host config it runs a quick tunnel, whose hostname
changes at every restart and must be pasted into the Linear app settings again; set
`"tunnel": {"name": "<tunnel>"}` once a named Cloudflare tunnel exists and the hostname stops moving.

Behaviour: `docs/operating-contract.md`. Plans: `docs/superpowers/plans/`.

For Mac development alongside Windows production, follow the
[development workflow](docs/development-workflow.md): offline tests first, then live
tests with the FarmBot Dev app in the same Linear workspace, on real issues you choose
and the real repositories.
Start an explicit development profile from [this template](config/development.example.json).
See [CI](docs/ci.md) for the Python 3.13 Mac/Windows checks and evidence artifacts.
`serve --config` propagates the selected absolute config file to all worker attempts.
An installed launchd service also preserves configuration selected through
`FARMBOT_CONFIG`. Use an absolute `local_root`; changing the config filename alone
does not move its state. Restart a settled service after editing configuration.

For a host-specific Codex model override, add `codex_workers` to private
`.local/agent/config.json` and restart the drained receiver:

```json
"codex_workers": {
  "fix": {"model": "gpt-5.6-sol", "reasoning_effort": "high"}
}
```

Each new or resumed `fix` worker receives these settings in its isolated Codex
home. Skills without an entry keep the runtime default; Claude workers are
unaffected. The model must be available to the host's account. This setting does
not change `max_concurrent` or the number of configured Unity slots.

## AI/operator diagnostics

Run `python3 -m agent.service doctor --config /absolute/path/to/config.json` on the
host you want to inspect. Omit `--config` to use `FARMBOT_CONFIG` or the checkout's
`.local/agent/config.json`. The command prints one JSON report (`schema_version: 1`)
with job/issue IDs, state and stage, leases, worker process checks, Unity slots,
open reservations, pending cleanup, stored Linear status failures, and log paths.
`findings` have stable codes, evidence and inspection hints so an AI can locate
the relevant logs without scanning the entire ledger.
`local_root` defaults to the checkout running the command, even when `--config`
points elsewhere; set an absolute `local_root` when inspecting from another checkout.

Exit codes are **0** (`ok`: no problems detected by these checks), **1** (`attention`:
findings need inspection), and **2** (`incomplete`: a config, ledger, log or process
check could not be completed). Incomplete takes precedence, retaining other findings.
Jobs waiting for answers are normal. Cleanup and reservation cancellation may still
be in progress; a finding is a reason to inspect, not an instruction to kill or retry.
Counts include all historical jobs; job detail includes active jobs, failed/blocked
jobs without successors, and jobs with pending cleanup. Retained run files are listed
without reading their contents; credentials, claim tokens, issue prose, raw stored
errors and process command lines are omitted. Detailed stored errors remain in
`issue_checks.error` and `job_cleanup.error` in the ledger.

This is a one-shot diagnostic snapshot, not a monitor or proof of overall service
health. It does not probe the receiver, scheduler loop, tunnel, Linear, or Unity
editor health. It checks recorded worker PIDs on the configured host using POSIX
process inspection; foreign-host PIDs, Windows, denied inspection and unverified
process ownership are reported as unknown. A live PID does not prove progress.
Run it per machine; do not use another machine's config to probe local PIDs.
No jobs, configuration permissions, logs or resource assignments are modified;
the ledger is opened read-only without creating or migrating it.
An older ledger reports `incomplete` and lists missing lifecycle schema entries;
upgrading the running service applies its normal migrations separately.

## Issue closure and cancelled work

Enable Issue webhooks alongside agent-session events on the configured Linear endpoint. Completed,
canceled and archived issues cancel FarmBot's unfinished/blocked jobs. A separate status poll catches
missed notifications; `reconcile_seconds` in private config defaults to 60. Failed status reads defer
new launches. No Linear status or assignment is changed by reconciliation.

Owned processes stop before cleanup. Unfinished source and clean unpublished commits survive in local
`refs/farmbot/recovery/<job-id>` refs. Unverified processes, unsettled slots and preservation failures
hold cleanup; `python3 -m agent.service status` shows `cleanup_pending` and `issue_status_errors`.
All run logs, ledger history and memory snapshots remain. See the [operating contract](docs/operating-contract.md)
for source recovery and deployment details.

Retries of the same job restore missing worktrees from its saved recovery commit. Cleanup also
retains immutable `refs/farmbot/recovery-history/<job-id>/<sha>` snapshots, listed in the cleanup
manifest, so later attempts cannot hide earlier work by replacing the latest recovery pointer.

Worker CLI examples and the exact handoff format are in [worker CLI guidance](references/worker-cli.md).
A rejected handoff must be successfully saved before the job can await input/resources or finish.
If Linear attaches a job's PR before its checkpoint, the CLI can reconcile it after verifying the
exact open draft, repository, branch and HEAD and proving that no other issue input changed.

Open jobs awaiting answers keep today's reply behavior and the same job ID. A reply inside FarmBot's
session resumes the waiting job even without an @mention. An ordinary issue comment outside that
session does not start a worker unless it mentions FarmBot. Reopening a cancelled issue starts nothing;
an authorized continuation creates a fresh job linked to the cancelled job's recovery evidence.

## Automatic Unity recovery

An unclean Unity release or 180 seconds of observed test stagnation quarantines that slot
and puts the job in `awaiting_resource` / `waiting_for_recovery`. The controller revokes the
old worker and resource tokens, proves process teardown, and queues the exact same commit
for verification, allowing another healthy slot to serve it. A separate recovery loop
preserves diagnostics, requests Play Mode stop, restarts only the affected configured editor,
and verifies identity, compilation and readiness before returning the slot to the pool.

Two automatic execution retries, three separate slot-preparation retries, and three editor
repair attempts are persisted in SQLite. A failed grant or explicit legacy adoption does not
consume the execution budget. Exhaustion reports its phase and latest cause. Repair
attempts back off 60 then 180 seconds. An interrupted repair lease expires after 15 minutes.
Repeated stalls or exhaustion of every configured slot produce an explicit infrastructure
failure with saved work and diagnostics. Genuine questions remain `awaiting_input`.
Workers must checkpoint before `release-resource --outcome unclean`, then exit; no host
operation or “continue verification” reply is required. Diagnostics live under
`.local/agent/resource-recovery/` and in `issue-context.resource_recovery`.

Other Unity projects may remain open. Each MCP call verifies the configured project against
the live instance listing before selecting it; unrelated editors and shared brokers remain untouched.

The ledger migration adds recovery tables and retry-classification columns. Existing recovery
counts are retained, with old records classified as execution; historical causes are not guessed.
Explicit retry clears both per-job budgets. Preserve these tables and their
diagnostics across upgrades. Older code cannot service a pending recovery or its
revoked claims; rolling code back does not restore those claims. Resume with a
compatible controller, and never rewind the ledger after new external actions.

## Windows worker cleanup

Windows worker attempts run in a host-owned Job Object with kill-on-close enabled.
A startup gate prevents the CLI from creating children before containment is established.
Normal exit, failure, timeout and Stop all drain the job before cleanup is recorded;
receiver termination also closes the job. Shared Unity editors are launched separately
by the slot pool and are not members of a worker job. The worker PID in the ledger is
the gate process; the Codex/Claude CLI is its child.

Old attempts created before containment may retain an unverified-descendant warning.
A missing parent PID is insufficient evidence to remove their worktrees. After a planned
machine restart, the trusted host operator can record boot evidence with:

```powershell
python -m agent.service recover-worker-cleanup --item ITEM_ID --reason "Planned restart completed"
```

This command refuses active jobs, unsettled reservations, another host, or a boot older
than any launch/attempt record. It records an audit entry; the scheduler still preserves
source and verifies cleanup normally. It neither restarts the machine nor retries the job.
Do not edit `process.json`, `killed.json` or cleanup records to bypass missing evidence.

## Shared worker memory

FarmBot starts with an empty memory store. Workers may save reusable corrections,
operational lessons and source pointers across issues. Gameplay rules stay in
Farm-Contract. Memory is recall data and never grants permission to act. See
[worker memory guidance](references/memory.md) for the JSON format and commands.

The ledger is authoritative; `.local/agent/memory/<snapshot>/MEMORY.md` and topic
files are generated historical reading views. Edit notes using the CLI, not those
files. Worker calls require a live item claim. Host administration requires no claim:

```bash
python3 -m agent --db .local/agent/ledger.sqlite3 memory-admin list
python3 -m agent --db .local/agent/ledger.sqlite3 memory-admin read --id NOTE_ID
python3 -m agent --db .local/agent/ledger.sqlite3 memory-admin save --input note.json
python3 -m agent --db .local/agent/ledger.sqlite3 memory-admin forget --id NOTE_ID --expected-revision 2 --reason "Obsolete observation"
```

Create input requires `request_id`; update input requires `id` and the current
`expected_revision`. Both use the same validated content format. Admin commands
are trusted-host operations, never a worker fallback. Do not store secrets in
notes or forget reasons. Forget clears current content but does not erase old
snapshots, backups, or a running worker's already-loaded context.

For snapshot maintenance, stop the service first and settle queued/running work.
Use the actual absolute runs directory belonging to this ledger:

```bash
python3 -m agent --db .local/agent/ledger.sqlite3 memory-admin prune-snapshots --runs-root /absolute/farm-linear-agent/.local/runs
```

Retained run prompts preserve their referenced snapshots. Only unreferenced generated
snapshot directories and abandoned staging directories are removed. Malformed prompts
or symlinked retained-run directories abort pruning before deletion. The command does
not stop the service for you; the stopped-service requirement is an operator precondition. No snapshots are pruned during
ordinary worker launches. Native Codex/Claude memory is disabled in workers, and no
personal memory or historic run logs are automatically imported.

## Model capacity retries

A Codex worker that exits with the terminal “Selected model is at capacity” error
returns to the same job's queue after 60, 180, then 600 seconds (at most three
automatic retries). Retry timing survives service restarts. The host preserves the
worktrees and checkpoint, revokes the old claim, and uses the same model settings.
The usual concurrency limit and fresh delegation checks still apply. Other errors,
stopped workers and completed/cancelled jobs do not trigger this retry policy.
After exhaustion the job fails with an explanation; an explicit operator retry
starts a new retry allowance. Requeueing does not certify process cleanup or remove
worktrees; existing retirement and Unity reservation safety checks remain in force.

## Publication transport retries

`verify-publication` retries temporary Linear/GitHub transport failures three times, waiting
2 and 5 seconds between attempts. Every attempt rechecks the live claim, delegation and exact
publishing destination. No push, PR creation or other mutation is retried by this mechanism.
After those attempts fail, the same job is queued after 60, 180, then 600 seconds (at most three
job retries), preserving its worktrees and checkpoint and retiring its old claim. The command
returns `retry_queued`, never `verified`; the worker must exit. Exhaustion records an explicit
infrastructure failure (`retry_exhausted`). An operator/chat retry resets the allowance.
Authorization, destination and certificate validation failures are not treated as outages.
Publication retries reuse existing worktrees without fetching from origin. The allowance counts
launched worker attempts: if the host cannot refresh Linear delegation before launch, the job
stays queued under the existing lifecycle backoff until that check recovers.
