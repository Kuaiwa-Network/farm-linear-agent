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
