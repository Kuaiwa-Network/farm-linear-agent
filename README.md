# farm-linear-agent

One Linear agent for the 农场 team. Delegate an issue to it for work, @mention it to
talk. Capabilities are added as skills on a shared identity, ledger, worker runtime and
desktop-resource locks: chat, QA, bug fix, FGUI, then whole features.

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

Open jobs awaiting answers keep today's reply behavior and the same job ID. A reply inside FarmBot's
session resumes the waiting job even without an @mention. An ordinary issue comment outside that
session does not start a worker unless it mentions FarmBot. Reopening a cancelled issue starts nothing;
an authorized continuation creates a fresh job linked to the cancelled job's recovery evidence.

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
