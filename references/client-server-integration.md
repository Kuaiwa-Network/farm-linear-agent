# Single Client and game-server verification

Use this when acceptance requires both candidate versions online. Independent
Client/Go tests remain separate; pure layout or server-independent work needs no
combined lease. No deployment, shared server or production operation is authorized.

After committing the clean Farm-Client and farm-hive candidates and completing
their required standalone checks, save a checkpoint and run:

```text
<selected-python> -m agent --db DATABASE await-resource --item ITEM --token-file STATE_DIR/token --resource unity_slot --mode interactive --commit FULL_CLIENT_SHA --integration-plan STATE_DIR/integration.json
```

The JSON has exactly these fields (derive the actual entrypoint and flags from
the server repository; this argv is illustrative):

```json
{"server_commit":"FULL_40_CHARACTER_SERVER_SHA","server_argv":["go","run","./cmd/server","--bind","{host}:{port}"],"scenarios":["Client action persists on Server and returns to Client"],"startup_seconds":180}
```

Only `{host}` and `{port}` placeholders are supported: loopback and a
controller-selected ephemeral port. Both clean owned commits are checked. One
interactive Unity slot plus one full server slot are reserved in one transaction.
A wait holds neither slot. Admission includes both costs and server memory floors;
the CLI refuses a host whose total budget cannot fit one pair. Stay in this worker
and poll `resource-status` while renewing.

The same worker reads `resource-status.integration`: server commit,
read-only detached source, canonical plan, loopback dependency endpoints, game
port and deadline. Existing source write grants stay as configured; the exact
snapshot is read-only. The controller creates it during preparation, outside
worker write roots; worker commands only validate it. No additional server
checkout, slot folder or clone configuration write grant is added. Multiple Clients,
multiple game nodes and human waits while holding a lease are outside this version.

Prepare a private script driving the reserved Unity instance through the
claim-bound `unity-tool`/`unity-read` CLI. It must
set the temporary game endpoint, verify expected candidate identities, execute all
declared scenarios, check actual results and return nonzero for failures, skips or
missing evidence. Restore temporary connection settings before release. Save its
argv as a JSON array in `STATE_DIR/client-command.json`, then run:

```text
<selected-python> -m agent --db DATABASE run-integration --item ITEM --token-file STATE_DIR/token --client-command STATE_DIR/client-command.json
```

This worker command starts the game server from the selected source inside the
existing worker sandbox, waits for its loopback TCP listener, then runs the Client
script while checking server liveness, claim fencing and deadline. TCP readiness
proves no application behavior; the Client script owns application handshake,
version and scenario assertions. Zero exit is not an independent audit of them.
The report records both selected SHAs, scenarios, commands, logs, timestamps,
exit result and teardown. Its hash is retained in issue audit. It replaces no
publication, baseline, coverage or review gate.

Windows children use nested non-breakaway Job Objects. POSIX children stay inside
the outer worker session, with owned groups and pinned leaders during cleanup.
Stop, claim revocation, startup failure, Client failure and deadlines stop both
command trees. Shared Unity stays outside worker containment. Uncertain cleanup
requires preserved evidence and an unclean release. Never reuse a numeric PID to
kill an unknown process. Settle Unity, checkpoint, release the single reservation
before delivery or human waits. Include results, both SHAs, report paths and the
next action in the checkpoint's `handoff`. On `worker_handoff: true`, exit
immediately: the POSIX or historical lease retired the claim. Managed Windows leases return
`keep_worker: true`; retain the claim. Both commands are registered in a sealed
reservation process scope. The host independently checks native emptiness before
Docker cleanup. Incomplete proof retains all-worker recovery and its evidence.

## Upgrade and recovery

The additive `integration_leases` table links the existing single reservation to
its canonical plan and companion slot. Existing standalone leases retain their
formats. Settle all combined leases and preserve server journals before rollback:
older code does not understand their server ownership, even though the table is
additive. Restart retains pins and journals; interrupted preparation is quarantined.
Releasing the reservation never lifts a held server half: it stays held for verified
`recover-server-slot`, while a held Editor still refuses release. Retain existing Unity
recovery/worker teardown checks. Recovery proves no acceptance scenario passed.
