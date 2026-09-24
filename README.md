# farm-linear-agent

One Linear agent for the 农场 team. Delegate an issue to it for work, @mention it to
talk. Capabilities are added as skills on a shared identity, ledger, worker runtime and
desktop-resource locks: chat, QA, bug fix, FGUI, then whole features.

FarmBot keeps one conversation across read-only investigation and writable repair execution.
An authorized “fix it” reply can start the first repair or resume previous work without
re-delegating. The host carries messages and findings across the change of execution profile
and posts recorded session progress every ten minutes while work is active or queued.
Fix work starts with read-only investigation, then uses a fresh Codex worker rooted in each
repository it needs to change. The controller keeps one Linear work item across those switches.

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

On the installed Windows production host, reload the revision already checked out
after work has settled with one PowerShell command:

```powershell
.\scripts\redeploy-farmbot.ps1
```

The helper checks the production ledger for active work and reservations, verifies
the receiver process belongs to this installation, then lets its scheduled
supervisor restart it. It waits for a new receiver PID and HTTP health 200.
`-CheckOnly` performs the checks without restarting. The helper does not fetch
or select a Git revision; update the checkout to a tested commit before using it
for a code release.

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
and writes that into every agent it writes. It refuses to write anything when the configured runtime or
`cloudflared` is not on that PATH. It writes the serve and tunnel agents, and a monitor agent when the
config has a non-empty `monitor` block, and prints the `launchctl bootstrap` lines to load them.
Logs land in `<local_root>/agent/logs/` (`.local/agent/logs/` by default), the monitor agent's included.
With no `tunnel` key in the host config it runs a quick tunnel, whose hostname
changes at every restart and must be pasted into the Linear app settings again; set
`"tunnel": {"name": "<tunnel>"}` once a named Cloudflare tunnel exists and the hostname stops moving.

Behaviour: `docs/operating-contract.md`. Plans: `docs/superpowers/plans/`.

For Mac development alongside Windows production, follow the
[development workflow](docs/development-workflow.md): offline tests first, then live
tests with the TestBot app in the same Linear workspace, on real issues you choose
and the real repositories.
Start an explicit development profile from [this template](config/development.example.json).
See [CI](docs/ci.md) for the Python 3.13 Mac/Windows checks and evidence artifacts.
`serve --config` propagates the selected absolute config file to all worker attempts.
An installed launchd service also preserves configuration selected through
`FARMBOT_CONFIG`. Use an absolute `local_root`; changing the config filename alone
does not move its state. Restart a settled service after editing configuration.

Codex `chat` and `fix` workers default to `gpt-6-sol` with `xhigh` reasoning.
For a host-specific override, add `codex_workers` to private
`.local/agent/config.json` and restart the drained receiver:

```json
"codex_workers": {
  "fix": {"reasoning_effort": "high"}
}
```

Each new or resumed worker receives the default settings, merged with any
per-skill override, in its isolated Codex home. Claude workers are unaffected.
The model must be available to the host's account. This setting does
not change `max_concurrent` or the number of configured Unity slots.

To give workers kw_ops, the test environment's GM backend, add its location to the private config
and put the token only in the controller's environment:

```json
"kw_ops": {"url": "https://<gm-host>/mcp", "token_env": "KW_OPS_TOKEN"}
```

Use HTTPS for a remote kw_ops server; HTTP is accepted only for loopback addresses.
An existing remote HTTP URL must be changed to HTTPS before restarting on this revision.
Configure kw_ops only when every target it lists is a test server, using a kw_ops operator whose
permissions cover only test servers; FarmBot does not scope servers. Export the variable in the
wrapper that starts `serve`, never in a shell startup file such as `~/.zshenv`: FarmBot removes or
excludes it only from the environment a worker inherits, and worker shells may source startup
files. `install-launchd` writes only `PATH` and `HOME` into its plists, so it cannot carry the
variable; never add the token to a plist's `EnvironmentVariables`, where workers can read it.
Instead, start a kw_ops controller through that wrapper yourself, as the
[development workflow](docs/development-workflow.md) does for TestBot. Codex fix workers then get
every kw_ops tool and chat workers its query tools; Claude workers get none. FarmBot learns the
variable's name only from the block, so add the block and the export together, and remove them
together: an export without the block reaches every worker. `doctor` shows whether kw_ops is
configured and whether the variable is set in doctor's own environment.

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

## Office status monitor

A read-only web page for the team, served by a separate process beside `serve` on the same host:

```bash
python3 -m agent.service monitor --config /absolute/path/to/config.json
```

It listens on `127.0.0.1:8780` unless the host config's `monitor` block says otherwise, so exposing it on
the office network is explicit:

```json
"monitor": {"bind": "0.0.0.0", "port": 8780, "hostnames": ["farmbot-host.local"]}
```

`bind` is an IPv4 address, not a hostname. `port` must differ from the receiver's `port`, so the
Cloudflare tunnel, which forwards only the receiver, never carries the monitor. Requests must name the
host by IP address, `localhost` or one of `hostnames`, which lists at most 16 lowercase DNS names. Only
the monitor and `install-launchd` read this block. `serve` and workers neither read nor validate it, so a
mistake in its keys or values stops only the monitor, which exits with a `monitor_failed` line naming the
problem. A JSON syntax error still breaks every config load, so check the edited file with `doctor`.
After changing the block, restart only the monitor. Teammates open `http://<host>:8780/`.

The page is in Chinese, refreshes every five seconds and cannot stop, retry or change anything. It shows
the service verdict, work in progress with Linear and PR links, Unity slots, attention items and the last
seven days of results. Each running job also shows its worker:

- worker 运行中: none of the states below applies.
- 续约逾期: more than 1.5 renewal intervals have passed without a renewal (15 minutes for a fix, 7.5 for
  a chat).
- 未被服务跟踪: `serve` is serving but manages no worker for the job, more than 30 seconds after its
  claim.
- 租约已过期: the lease has run out.

It never shows issue descriptions, comments, checkpoints, questions, logs, paths, PIDs, tokens or raw
errors; use `doctor` on the host for detail. There is no login: anyone who can reach the port can read
issue identifiers, titles and job states.

The monitor reads the ledger read-only, probes the receiver's `/health` on loopback and reads
`<local_root>/service-heartbeat.json`. `serve` rewrites that file every five seconds with its phase, loop
timings, revision, webhook counts, and the start and deadline times of the workers it manages (never
PIDs), so the page still reports a stopped or wedged service. A service revision that writes no heartbeat
shows its loop rows as 此版本未提供. `/health` proves only that the receiver answers; the page is not proof
of Linear, tunnel or Unity health beyond the signals it lists.

Reading a ledger while `serve` is stopped can leave SQLite's `-wal` and `-shm` side files beside it; the
ledger itself is never modified. Run the monitor as the same account as `serve`, so those files stay
writable by the service.

`install-launchd` adds a third agent for the monitor when the config has a non-empty `monitor` block. It
writes no agent at all if the monitor would refuse that block. It never deletes a plist, so removing the
block, or setting it to `{}`, and reinstalling leaves an installed monitor agent in place. To remove it,
run `launchctl bootout gui/$(id -u)/<label>` and delete `~/Library/LaunchAgents/<label>.plist`. The label
is `com.kuaiwa.farmbot.monitor` for a legacy install, and
`com.kuaiwa.farmbot.<environment>.<instance_id>.monitor` for an explicit profile.

On the Windows production host, use the Python and checkout that the `FarmBot-Receiver` task uses. Run
the commands below from that checkout; they show the Python as `C:\Path\To\python.exe` and the checkout
as `C:\FarmBot`.

1. Update the checkout, and run `.\scripts\redeploy-farmbot.ps1` as usual, so `serve` writes the
   heartbeat.
2. Add the `monitor` block with a LAN `bind` to the host config, and check the edited file with `doctor`.
   `serve` needs no restart for it.

   ```powershell
   & "C:\Path\To\python.exe" -m agent.service doctor --config "C:\FarmBot\.local\agent\config.json"
   ```

3. Run the monitor once in a PowerShell console, check for its `monitor_ready` line, and leave it
   running until step 5.

   ```powershell
   & "C:\Path\To\python.exe" -u -m agent.service monitor --config "C:\FarmBot\.local\agent\config.json"
   ```

4. In an elevated PowerShell, as any administrator, allow the monitor's port on the office network and
   register a task that starts the monitor at the logon of the account that runs `FarmBot-Receiver`. The
   block reads that account from the `FarmBot-Receiver` task and gives the new task an explicit principal
   for it.

   ```powershell
   New-NetFirewallRule -DisplayName "FarmBot monitor" -Direction Inbound -Protocol TCP -LocalPort 8780 `
     -Profile Private -RemoteAddress LocalSubnet -Action Allow
   $account = (Get-ScheduledTask -TaskName "FarmBot-Receiver").Principal.UserId
   $principal = New-ScheduledTaskPrincipal -UserId $account -LogonType Interactive
   New-Item -ItemType Directory -Force -Path "C:\FarmBot\.local\agent\logs"
   $action = New-ScheduledTaskAction -Execute "cmd.exe" -WorkingDirectory "C:\FarmBot" `
     -Argument '/d /c ""C:\Path\To\python.exe" -u -m agent.service monitor --config "C:\FarmBot\.local\agent\config.json" >> "C:\FarmBot\.local\agent\logs\monitor.log" 2>&1"'
   $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 `
     -RestartInterval (New-TimeSpan -Minutes 1)
   Register-ScheduledTask -TaskName "FarmBot monitor" -Action $action -Principal $principal `
     -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $account) -Settings $settings
   ```

   The task appends the monitor's output to `C:\FarmBot\.local\agent\logs\monitor.log`, so its
   `monitor_ready`, `monitor_failed` and `status_failed` lines are kept when Task Scheduler runs it. The
   log stays small: request logging is off, and the monitor prints one line at startup and one where each
   run of failed status builds starts, though a client that drops its connection before the reply can
   add a traceback. `cmd /c` exits with Python's exit code, so restart-on-failure still applies.

5. Nothing starts the task before the next logon. Stop the console run from step 3 with Ctrl+C, then
   run `Start-ScheduledTask -TaskName "FarmBot monitor"`.

The firewall rule needs the office network classified as Private. Check restart-on-failure, the task's
account and its log on the host once. `redeploy-farmbot.ps1` does not restart the monitor; restart its
task after updating the checkout.

The helper stops the receiver with `Process.Kill()`, so `serve` writes no `stopped` heartbeat and its
Python cleanup does not run. During a later redeploy the page therefore shows 需要关注
(`receiver_unreachable`) while the last heartbeat is still fresh, then 正在启动 once the new `serve` writes
its `starting` heartbeat, then its usual verdict, normally 正常. If 60 seconds pass without a heartbeat
before the new one, it shows 无响应 in between. It never shows 已停止.

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

## macOS worker cleanup

Each worker leads its own POSIX session and process group. Before FarmBot reaps an exited worker, it
terminates any remaining member of that session it can see in the process table. For a worker that
exited by itself, it records `killed.json` with `posix_session` only once the session and group are
empty. If it cannot verify this, it writes `teardown-unverified.json` and cleanup stays pending.
Children that called `setsid()` leave the session and are not covered. Claude Code 2.1.280 was observed
starting its Bash tool shells that way, so treat processes a `claude` worker leaves running as uncovered.
The check needs Python 3.13 or later on macOS, for `os.waitid`. Attempts that exited under an older
interpreter, before this check existed, or while FarmBot was stopped keep their pending cleanup.
`recover-worker-cleanup` is Windows-only; on macOS, inspect the attempt's run directory (`process.json`,
`killed.json`, `teardown-unverified.json`, and `killed.superseded.json`, an earlier `killed.json` set aside
when a later check could not verify the attempt) and the job's `issue-context` cleanup evidence before
deciding how to proceed.

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
