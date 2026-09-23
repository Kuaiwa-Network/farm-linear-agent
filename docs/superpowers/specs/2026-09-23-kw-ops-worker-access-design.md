# kw_ops access for workers

FarmBot workers may use kw_ops, the GM backend of the test game environment, through
its MCP server. Fix workers get every kw_ops tool, in every fix attempt, whether neutral
or rooted in a repository (#39). Chat workers get its query tools only. This version
covers Codex workers only (see Claude, below). The user approved this design on
2026-09-23. Three approaches were compared:

- grants in the skill manifests, with a FarmBot allowlist for read access (chosen);
- one kw_ops operator per skill;
- grants in host configuration.

QA runners are expected later and will use the same grant.

## Background

Since #37, workers no longer load a repository's own Codex configuration, so a worker
reaches a tool server only when FarmBot injects it. This is the original design's model
(the 2026-09-17 design, §5 and §7): a manifest's `mcp` lists the other tool servers a
worker gets. Reservation-bound servers such as the Unity MCP are never listed there; they
are injected only while the reservation is held, and that stays unchanged.

Every kw_ops action names a logical `server_id` returned by `gm_list_targets`. kw_ops
checks each action against the calling operator's permissions and audits it under that
operator. Every server the configured instance lists belongs to the test environment,
so FarmBot does not restrict which servers a worker uses.

## Grants

- `skills/fix/skill.json` declares `"mcp": ["kw_ops"]`: every kw_ops tool.
- `skills/chat/skill.json` declares `"mcp": ["kw_ops:read"]`: the read allowlist only.
- The skill loader accepts only grants FarmBot knows (`kw_ops` and `kw_ops:read`).
  Anything else fails at startup. A future QA runner declares its grant the same way.
- The read allowlist is a constant in FarmBot code: `gm_list_targets`,
  `gm_query_players`, `gm_player_detail`, `gm_guild_query`, `gm_time_get`,
  `gm_reward_types` and `gm_reward_catalog`. Read grants get no other tool until the
  constant lists it.

## Host configuration

An optional block in the private host profile:

```json
"kw_ops": {"url": "http://<gm-host>/mcp", "token_env": "KW_OPS_TOKEN"}
```

- `url` must be an http or https URL.
- `token_env` must be an environment variable name: uppercase letters, digits and
  underscores.
- No other keys are accepted.

The token itself never appears in a file FarmBot reads or writes. It lives in the
controller's environment: TestBot's wrapper on the Mac, and the service environment on
the Windows host. `install-launchd` writes only `PATH` and `HOME` into its plists, so a
controller started by launchd needs a wrapper that sets the variable.

The operator chooses which kw_ops operator the token belongs to. A dedicated FarmBot
operator keeps FarmBot's actions separate in kw_ops's audit log.

## Injection

`Scheduler.launch` resolves the grant from four inputs: the skill manifest, the host
configuration, the controller's environment and the runtime. It then either injects the
server or records why it did not.

**Codex.** The worker home's config gets:

```toml
[mcp_servers.kw_ops]
url = "http://<gm-host>/mcp"
bearer_token_env_var = "KW_OPS_TOKEN"
enabled_tools = ["gm_list_targets", "..."]   # read grants only
```

The token variable is also hidden from the worker's shell commands. The worker config
lists it under `shell_environment_policy.exclude`, and also sets
`features.shell_snapshot = false`. Measured on codex-cli 0.156.1, `exclude` alone does
not hide the variable, because Codex's shell snapshot re-exports it. With the snapshot
off, a kw_ops worker's commands take the controller's environment instead of a replayed
login shell.

**Claude.** Claude workers get `{"status": "unavailable"}` in this version. Since #39, fix
runs only on Codex. Claude has no per-server tool allowlist that holds under
`bypassPermissions`, so Claude cannot enforce a read grant. No Claude worker could
therefore use kw_ops.

A measurement is kept for a later version. Claude Code 2.1.229 expands `${VAR}` in
`--mcp-config` headers, so a full grant on Claude would be this `mcp.json` entry, with the
token still never written into a worker home:

```json
{"type": "http", "url": "http://<gm-host>/mcp",
 "headers": {"Authorization": "Bearer ${KW_OPS_TOKEN}"}}
```

**Environment.** Every worker currently inherits the controller's whole environment. The
token variable is removed from every worker that has no kw_ops server injected, which
includes every Claude worker. A Codex worker with kw_ops keeps the variable for the CLI's
own MCP client, hidden from its shell.

## Launch message and authority

The launch payload gains `tools.kw_ops` for skills that declare a kw_ops grant. It is one
of:

- `{"access": "full"}`
- `{"access": "read"}`
- `{"status": "unavailable", "reason": "<reason>"}`, where the reason names what is
  missing: the host configuration, the token variable, or runtime support for the
  requested access.

Skills without a kw_ops grant get no `tools.kw_ops` entry.

The dispatch AUTHORITY, which the `--approve-for-me` reviewer trusts, gains a clause that
applies when `tools.kw_ops.access` is present:

- The kw_ops MCP server is the GM backend of the test environment, and every server
  `gm_list_targets` returns is a test server.
- With `full` access the worker may use any kw_ops tool on any listed server when this
  issue's reproduction or verification needs it. With `read` access only its query tools
  are available.
- Every state-changing GM call is recorded, with server_id, tool, target and reason, in
  the worker's checkpoint handoff and its final evidence.
- The kw_ops credential belongs to the host: never read, print or store it.
- kw_ops grants no other authority.

## Failures and diagnostics

- **No configuration, no token, or a runtime that cannot enforce the access level:**
  nothing is injected, and the payload says why. The worker reports it as a
  verification gap rather than working around it.
- **kw_ops unreachable at run time:** the CLI starts without it, and the worker reports
  the gap.
- **`doctor`:** reports whether kw_ops is configured and whether its token variable is
  set, never the value.

## Security notes

- Read-only access for chat rests on FarmBot's allowlist in Codex, not on kw_ops. A worker
  that obtained the token could call write tools directly, which is accepted in the test
  environment. A read-level kw_ops operator for chat would move that enforcement into
  kw_ops, and can be added later without changing the grants.
- The token must not reach files, prompts, logs, Linear comments or pull requests. The
  offline checks below assert that it is absent from the worker home and the model input.

## Out of scope

- Server scoping.
- Per-skill kw_ops operators.
- QA runners.
- Any kw_ops access for Claude workers.
- Repository-level MCP configuration, which is no longer loaded since #37.

## Verification

**Unit tests** cover:

- manifest grant validation and host configuration validation;
- the grant resolution matrix: skill × runtime × configured × token set;
- the Codex server entry, and the unavailable entry for other runtimes;
- removing the token variable, and excluding it from shell commands;
- the payload and authority text;
- the `doctor` output.

**Measured before implementation**, offline, with a loopback kw_ops stub and model stubs:

- codex-cli 0.156.1 sends `Authorization: Bearer <token>` taken from the environment.
- With `enabled_tools = ["gm_list_targets"]`, a script in GPT-6's code mode sees only
  `tools.mcp__kw_ops__gm_list_targets`. Without it, the script also sees
  `tools.mcp__kw_ops__gm_grant_reward`.
- The shell snapshot behaviour described under Codex, above.
- Claude Code 2.1.229 expands the header from the environment.
- The #37 trust fix still holds on 0.156.1.

**Offline checks with the real CLI, after implementation**, establish that:

- a FarmBot-launched Codex worker sends the header;
- a chat worker's code-mode tools are exactly the allowlist;
- the token appears in neither the worker home, the model input, nor a shell command's
  environment.

**Windows** remains unverified until a Windows host runs a worker with kw_ops.

## Documentation

- The operating contract: its Authority section.
- The README: host configuration.
- The development workflow: TestBot's wrapper sets the token variable.
