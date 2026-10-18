# FarmBot host startup

Use the intended instance's absolute config path and its installed Python. This
guide applies to a configured host and to every subsequent start; it does not
replace release maintenance when changing a running installation.

## Configure the host once

1. Select the instance's Linear identity, absolute state root, repository remotes
   and enabled skills in its private config. Use separate roots for FarmBot and
   TestBot. Follow the [development workflow](development-workflow.md) for a new
   live development instance; existing production ownership remains intact.
2. Install the required native tools and authenticate the host's GitHub CLI.
   Configure [Go dependency preparation](go-dependency-preparation.md) with
   absolute Go/Git/gh paths, the Go executable SHA256, separate cache/preparation
   roots outside worker state, permitted repositories/modules and private
   prefixes. Tools must also be outside worker state. On Windows, make tools and
   module sources readable by sandbox workers and keep cache writes host-only.
   Preparation logs/staging remain private. Startup does not install tools,
   select a Go version, log in, change permissions or invent missing config.
3. Configure Unity `slots` with the repository, build target, native Editor
   selection and MCP endpoint/account required by this host. Retain the existing
   slot folders and ownership/recovery evidence. Native Windows interactive
   Editors need the configured user's logged-in desktop session. Capacity and
   [resource admission](../README.md#server-test-slots-and-heavy-resource-admission)
   determine which Editors can run together.
4. Seed owned repository clones ahead of the first start, with Git LFS available
   for Unity projects. Keep controller clone configuration and publication
   destinations within the [operating contract](operating-contract.md#authority).

For Editors that open automatically on each startup and stay open between jobs,
also configure `"unity_pool": {"editors": 2}` (or another count up to the number
of configured slots). The first N `slots` are selected and opened sequentially.
Budget memory for every retained Editor plus any server capacity. Memory
admission can defer some starts while a healthy Editor serves work. This mode
uses interactive worker MCP tests; older queued Unity batch requests convert to
interactive without counting as executed tests. Activate this policy only after
active Unity work settles and projects outside the selected count are closed.
See [resource ownership and recovery](operating-contract.md#resource-ownership).
Without `unity_pool`, the existing on-demand batch/interactive mode remains.

For example, from the accepted source checkout:

```powershell
# Windows: substitute this instance's configured paths.
$farmbotPython = 'D:\FarmBot\tools\python.exe'
$farmbotConfig = 'D:\FarmBot\config.json'
& $farmbotPython -X utf8 -B -m agent.service seed-clones --config $farmbotConfig --from 'D:\FarmSources'
```

```sh
# macOS: use the Python installation selected for this instance.
python3 -B -m agent.service seed-clones --config /absolute/private-config.json --from /absolute/FarmSources
```

An existing nonempty Go cache without an ownership marker requires the host
operator to inspect it and explicitly run `python -B -m agent.go_dependencies
--config <absolute-config> --adopt-existing` (macOS: `python3`). An ownership
marker for another instance or state root is refused, including during explicit
adoption. Do not remove or rewrite it to make startup succeed.

## Each startup

Start only the installed supervisor or the foreground controller for this
instance. When a supervisor already owns it, use that installation; its config,
environment, interpreter and release hashes must still match. Replace pinned
inputs only through settled maintenance. The Windows installation procedure is
in the [README](../README.md#run).

For a configured foreground instance, from its accepted source checkout:

```powershell
& $farmbotPython -X utf8 -B -m agent.service serve --config $farmbotConfig
```

```sh
python3 -B -m agent.service serve --config /absolute/private-config.json
```

Every `serve` startup performs these steps automatically:

| Component | Startup behavior | Work after startup |
| --- | --- | --- |
| Instance | Check the selected profile and acquire its controller lock before stateful startup | Preserve claims, queued work, recovery evidence and worker config-file selection |
| Go preparation, when configured | Verify native tools and the pinned Go digest; create and mark missing/empty cache and preparation directories; verify existing markers against this instance and state root | Service the worker's exact committed dependency requests; reuse a complete cache or download and verify missing modules through the host loop |
| Unity slots | Register selected slots, prepare missing owned checkouts and check LFS hydration; preserve existing parked commits and busy folders; warm selected Editors sequentially when `unity_pool` is configured | Retain fixed-pool Editors between jobs, or open on demand in legacy mode; verify each reserved candidate and preserve quiescence/recovery checks |
| Server-test pool, when configured | Check the local Docker backend, pinned images and recorded pool ownership | Create isolated services for admitted server reservations |

Go startup never adopts nonempty unmarked storage or changes another instance's
marker. Invalid ownership, linked paths, missing tools or a changed Go digest
stop startup before Linear communication or ledger construction. Without a
`go_dependencies` block, that optional facility stays disabled.

Startup prepares the dependency facility; dependency downloads remain tied to a
worker's clean committed `go.mod`/`go.sum`. It does not download every repository's
latest dependencies at boot. Workers remain offline and use private build/temp
directories. A pending preparation request needs no human reply. A failed one
retains diagnostics for the specific host correction and bounded retry.

Unity startup always prepares the selected slot folders. With `unity_pool`, it
also opens and verifies the selected Editors sequentially under memory admission,
then retains them between jobs. Without that block, Editors start for admitted
reservations. Each grant still verifies the actual project and reserved commit.
A slot-preparation failure is reported as
`slot_pool_unavailable`; the receiver can still run. Server pool failures are
similarly reported as `server_pool_unavailable`. These signals require attention
even if HTTP health responds.

Persistent Editor startup failures use the existing bounded preparation recovery
lane; a healthy selected Editor can continue serving the queue. Startup does not
reset held slots or clear uncertain process ownership to make the pool ready.

## Check readiness and handle failures

Run the read-only diagnostics with the same config and configured interpreter:

```powershell
& $farmbotPython -X utf8 -B -m agent.service doctor --config $farmbotConfig
```

```sh
python3 -B -m agent.service doctor --config /absolute/private-config.json
```

For configured Go preparation, `tools.go_dependencies.ready` checks ownership
markers, regular native tools and the Go digest. Doctor does not initialize
missing directories or download modules. Before the first startup, fresh storage
can therefore report `go_dependency_preparation_unready`; `serve` initializes it
after checking the tools. After startup, the heartbeat must show `serving` with
fresh controller loops, including `go_dependencies` when configured. Review the
slot and server-pool findings and startup logs as well.

This readiness check does not certify every module version, worker sandbox
access, Unity gameplay or server integration. The exact worker request receipt
and the worker's actual verification gates provide that evidence.

For changed config/tools, first settle work and use the installed supervisor's
maintenance procedure to replace its pins and restart. For foreign/unmarked
state, linked paths, missing LFS or uncertain process/resource cleanup, retain
the evidence and correct the named host prerequisite. Never clear markers,
reservations or locks to bypass a refusal. Resume a waiting issue only after the
correction has reached its restarted controller and a fresh worker.

## Release and rollback

This startup change adds no ledger schema migration. Preserve Go ownership
markers, cached modules and preparation diagnostics across releases. Older code
requires the same roots to have been initialized explicitly before startup; the
existing `agent.go_dependencies` command still supports that workflow. Settled
maintenance and supervisor pin replacement remain necessary for a release or
configuration change.
