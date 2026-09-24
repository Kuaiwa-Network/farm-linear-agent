# Office status monitor

A read-only web page that shows the 农场 team what FarmBot is doing and whether it is
running. The user approved this design section by section on 2026-09-24. It is a
proposal until implemented; the operating contract describes current behaviour.

## Decisions

| Question | Decision |
|---|---|
| Who looks at it | Everyone on the team, from their own devices |
| Reach and login | Office network only; no login and no domain. The LAN is the boundary |
| Instance | Production FarmBot on Windows first. The code is cross-platform, so TestBot on the Mac is the live test bed |
| Architecture | A separate, read-only monitor process on its own LAN port, beside `serve` |
| Language and detail | Chinese UI, matching FarmBot's Linear voice. Issue titles are shown |
| Controls | None. With no login, anyone on the LAN can open the page, so it cannot stop, retry or cancel anything |

Rejected alternatives:

- **Public or tunnelled access.** The Cloudflare tunnel forwards the whole receiver port,
  so a page served there is on the internet at a hostname that changes with every quick
  tunnel restart. A login through Cloudflare Access would need the deferred domain.
- **A monitor built into `serve`.** When FarmBot dies, the page dies with it, so it cannot
  say "down since 14:02". A page bug would share production's process. It could not
  appear until production is upgraded and restarted, and there is no drain command yet.
- **A periodic static HTML snapshot.** The data is minutes old, something still has to
  serve or share the file, and "down" shows only as an old timestamp.

## Scope

In scope: the monitor command and page, a heartbeat written by `serve`, webhook arrival
counts in the receiver, a `monitor` config block, a macOS launchd agent, Windows
installation instructions, tests and documentation.

Out of scope, possible follow-ups: alerts (for example posting to a team chat when
FarmBot goes down), controls, login or internet access, one page for several instances,
cloudflared probes or the current quick-tunnel URL, and history charts.

## Architecture

```text
Production host (Windows first; macOS for TestBot)
+-----------------------------------------------------------------------+
| serve (existing; receiver on 127.0.0.1:<port>, the port the tunnel     |
|        forwards)                                                      |
|   + heartbeat thread: every 5 s replaces                              |
|     <local_root>/service-heartbeat.json with phase, loop timings,     |
|     revision and webhook counts                                       |
|                                                                       |
| monitor (new process; <bind>:<monitor port>, never tunnelled)         |
|   reads  ledger: read-only SQLite snapshot, never migrates            |
|          heartbeat: bounded read, regular files only                  |
|          GET http://127.0.0.1:<port>/health: 2 s timeout, no proxy    |
|   serves GET /, /monitor.js, /monitor.css: static page               |
|          GET /api/status: JSON, built at most once every 2 s          |
+-----------------------------------------------------------------------+
            ^  office LAN: http://<host>:<monitor port>/
     teammates' browsers poll /api/status every 5 s
```

The monitor must run on the same host as `serve`: it probes `127.0.0.1` and reads that
host's files. It needs no Linear, GitHub or model credentials and makes no external
request.

### Components

| Unit | Responsibility |
|---|---|
| `agent/readonly_db.py` | `snapshot_connection(path)`: opens the ledger with `mode=ro`, sets `PRAGMA query_only=ON` and begins one read transaction. `doctor` moves to it unchanged in behaviour |
| `agent/heartbeat.py` | `Heartbeat` records loop iterations, phase and webhook outcomes in memory under a lock. Also writes and reads the heartbeat file, validates it, and reads the best-effort source revision |
| `agent/monitor_view.py` | `build_status(...)`: the ledger path, opened only through `snapshot_connection`, plus a heartbeat result, a `/health` result, instance fields and the clock, become the status JSON. It holds the verdict and attention rules and does no other I/O |
| `agent/monitor.py` | The `monitor` command: config and ownership checks, the HTTP server, the `/health` probe and the snapshot cache |
| `agent/monitor_static/` | `index.html`, `monitor.css` and `monitor.js`: vanilla JS with no external requests, loaded into memory when the monitor starts |

Changes to existing code:

- `agent/service.py`: a `monitor` subcommand. `_serve` starts a heartbeat thread before
  `pool.ensure()`, records each guarded loop's iterations, and writes the stopped
  heartbeat on shutdown.
- `agent/receiver.py`: the `/webhook` handler records each POST outcome. A failure to
  record is swallowed and never changes the response. The receiver's exclusive-bind
  server class moves to module level so the monitor can reuse it.
- `agent/config.py`: the `monitor` block and `Paths.heartbeat`.
- `agent/deploy.py`: `install-launchd` writes a third agent when a `monitor` block exists.

## Configuration

```json
"monitor": {"bind": "0.0.0.0", "port": 8780, "hostnames": ["farmbot-host.local"]}
```

| Key | Default | Rule |
|---|---|---|
| `bind` | `127.0.0.1` | An IPv4 literal; no DNS lookup at startup. Exposing the page on the LAN means explicitly setting `0.0.0.0` or the host's LAN address |
| `port` | `8780` | An integer from 1 to 65535, different from the receiver `port`, so the tunnel never carries the monitor |
| `hostnames` | `[]` | At most 16 lowercase DNS names that the `Host` header may carry besides IP literals and `localhost` |

Unknown keys inside the block are rejected. A misspelled key would otherwise silently
keep loopback or make every request fail the host check. `serve` and workers never read
the block. Editing it requires restarting only the monitor, and the shared-config
restart rule does not apply to it. Production's older revision must still be checked
to ignore unknown top-level keys before the block is added; see Rollout.

## Heartbeat

`serve` writes `<local_root>/service-heartbeat.json` (`Paths.heartbeat`):

- When `_serve` begins, with phase `starting`. The heartbeat thread starts before
  `pool.ensure()`, which can take minutes while slots are prepared.
- Every 5 seconds from then on. The phase becomes `serving` once the loop threads have
  started.
- On clean shutdown, after the loop threads have joined, with phase `stopped` and
  `stopped_at`.

Until the first beat, which comes after `build()` has verified the Linear identity and
opened the ledger (normally seconds), the file still holds the previous run's heartbeat.
A restart therefore briefly shows the previous 已停止 before 正在启动.

Each write goes through the existing replace-on-write helper (`_write_worker_file`). A
failed write, such as a Windows sharing violation while the monitor reads the file, is
skipped until the next beat. A writer failure never stops serving.

The file sits in the state root beside the controller lock and ownership marker. Codex
workers are not given write access there, but a Claude-runtime worker has no OS
sandbox. The monitor therefore treats the file as untrusted display data. It reads it
with the bounded regular-file reader (`_read_worker_file`, capped at 64 KiB) and
validates every field. An invalid file is reported as `unreadable`, never as healthy.
The `/health` probe remains an independent signal.

Format (`schema_version` 1). Nothing in it is secret, and it records no PID:

```json
{"schema_version": 1, "phase": "serving", "started_at": 0.0, "written_at": 0.0, "stopped_at": null,
 "revision": "a3f9f77c1d2e", "dirty": false, "runtime": "codex",
 "loops": {"schedule": {"started_at": 0.0, "finished_at": 0.0, "consecutive_errors": 0,
                        "error_type": null, "error_at": null}},
 "webhooks": {"last_at": 0.0, "last_type": "Issue", "last_rejected_at": null,
              "counts": {"accepted": 0, "duplicate": 0, "ignored": 0, "rejected": 0,
                         "malformed": 0, "failed": 0}}}
```

- `loops` has one entry per guarded loop: `receive`, `schedule`, `pool`, `lifecycle`,
  `progress` and `resource_recovery`. An iteration is recorded around each loop's work.
  The pause between iterations is not part of it, so a loop that sleeps between ticks is
  not reported as busy. `consecutive_errors` resets after a successful iteration. `error_type` is an exception class name, as in the service log.
- `revision` is the first 12 characters of `git rev-parse HEAD` for the checkout running
  `serve`, read once at startup with a 5 s timeout. `dirty` is true when tracked files
  differ from it. Both are `null` when Git is unavailable or the directory is not a
  checkout.
- Webhook outcomes are classified by the handler's result:
  - `accepted`: 200 accepted or stop received.
  - `duplicate` and `ignored`: 200 with those results.
  - `rejected`: 401 or 403, from the signature, timestamp or identity checks.
  - `malformed`: 400, 408 or 413.
  - `failed`: 500.

  Every `/webhook` POST is counted, including early size and timeout rejections. Counts
  reset when the service restarts.

## Status snapshot

`/api/status` is assembled field by field from the list below and nothing else. Rows
are never serialized wholesale. `issues.metadata` holds full descriptions and comments,
so only `identifier`, `title` and `url` are extracted from it, with `json_extract`.
Timestamps are host epoch seconds with a top-level `generated_at`. The page computes
ages from `generated_at` plus the time elapsed since the fetch, so a viewer's clock
skew cannot distort them.

| Section | Fields | Source |
|---|---|---|
| top level | `schema_version`, `generated_at`, `verdict`, `verdict_since` | computed |
| `instance` | `environment`, `instance_id`, `bot_name`, `host` | config |
| `monitor` | `revision`, `dirty` | the monitor's own checkout |
| `service.health` | `ok`, `status`, `latency_ms`, `error_type`, `checked_at` | `/health` probe |
| `service.heartbeat` | `state` (`fresh`, `stale`, `missing` or `unreadable`), `phase`, `written_at`, `started_at`, `stopped_at`, `revision`, `dirty`, `runtime` | heartbeat |
| `service.loops` | per loop: `name`, `state` (`idle`, `busy`, `stalled` or `erroring`), `started_at`, `finished_at`, `consecutive_errors`, `error_type`, `error_at` | heartbeat |
| `service.webhooks` | `last_at`, `last_type`, `last_rejected_at`, `counts` | heartbeat |
| `service.agent_event_at` | newest `webhook_events.received_at` | ledger |
| `service.linear` | `last_ok_at` (newest `issue_checks.checked_at` with no error), `failing_issues` | ledger |
| `counts` | `running`, `queued`, `awaiting_input`, `awaiting_resource` | ledger |
| `active[]` | `identifier`, `title`, `url`, `skill`, `state`, `display_state`, `stage`, `repo`, `created_at`, `state_since`, `checkpoint_at`, `retry_at`, `queue_position`, `prs[]` | ledger |
| `slots[]` | `slot_id`, `kind`, `state`, `commit`, `holder`, `mode`, `recovery` (`state`, `attempts`, `max_attempts`) | ledger |
| `unity_queue` | number of queued Unity reservations | ledger |
| `recent[]` | `identifier`, `title`, `url`, `skill`, `outcome`, `finished_at`, `retried`, `prs[]` | ledger |
| `attention[]` | `code`, `subject`, `since`, `count` | computed |
| `ledger` | `ok`, `error_type`, `missing_optional[]` | computed |

Derivations:

- `display_state` refines `state`:
  - `queued` splits into `launching` (worker spawned, not yet claimed), `switching_repo`
    (a pending repository handoff), `retry_wait` (`retry_not_before` in the future,
    exposed as `retry_at`), and otherwise `queued`, with `queue_position` in the
    scheduler's own `priority, created_at, id` order.
  - `awaiting_resource` becomes `waiting_for_recovery` when the stage says so, and
    otherwise carries its Unity reservation queue position.
- `state_since` and `checkpoint_at` come from the newest `audit` row whose kind is the
  current state, or `checkpoint`. They use one grouped query over the active items,
  because `audit` has no index. `updated_at` is not used: lease renewal rewrites it. A
  new item's first audit row is `create`, not `queued`, so `state_since` falls back to
  `created_at`. `checkpoint_at` is `null` before the first checkpoint.
- `slots[].kind` is the resource kind (`unity_slot`). `holder` and `mode` (`batch` or
  `interactive`) come from the slot's active or `cancel_requested` reservation, and are
  `null` when the slot is free. `commit` is the first 7 characters of `parked_commit`.
- `recent` holds terminal items updated in the last 7 days, newest first, at most 30:
  - `outcome` is `no_change` for a delivery whose evidence carries `no_change`, and
    otherwise the terminal state.
  - `retried` is true when an item names this one as `predecessor_id`.
- `prs` come from `published_prs` for the job's issue, labelled `<repo>#<number>`. Only
  URLs matching `https://github.com/<owner>/<repo>/pull/<n>` are emitted. Draft status
  is not recorded, so it is not claimed.
- `url` is emitted only when it starts with `https://linear.app/`. Otherwise it is
  `null`.
- Strings are clamped: titles to 200 characters and stages to 120. Error types must look
  like a Python class name, otherwise `Error` is shown.

Never emitted: config values beyond the instance fields; credentials, tokens or token
hashes; issue descriptions, comments or labels; checkpoints, evidence prose, pending
questions, inbox messages and worker last messages; logs and run files; raw stored
errors; PIDs, host paths and command lines; memory notes. Deep diagnostics stay with
`doctor` on the host.

Schema tolerance: each snapshot reads `sqlite_master` and `PRAGMA table_info` first. Only
`work_items` (`id`, `issue_id`, `skill`, `state`, `stage`, `created_at`, `updated_at`)
and `issues` (`id`, `metadata`) are required. Every other table or newer column is
optional. The section or derivation that needs it is skipped, and the table is listed in
`ledger.missing_optional`, so the page can show 此版本未提供. This lets the monitor read
the older ledger of production's accepted revision without migrating it.

## Verdict and attention

Rules are evaluated in order. The heartbeat is `fresh` when `written_at` is less than
60 seconds old; it is written every 5 seconds.

| # | Condition | Verdict | `verdict_since` |
|---|---|---|---|
| 1 | Ledger missing or its required tables unreadable | `unknown` | — |
| 2 | Heartbeat phase `stopped` and `/health` failing | `stopped` | `stopped_at` |
| 3 | Fresh heartbeat with phase `starting` | `starting` | `started_at` |
| 4 | `/health` failing and heartbeat `stale`, `missing` or `unreadable` | `unresponsive` | heartbeat `written_at`, else the monitor's first observed failure |
| 5 | `/health` failing, heartbeat fresh | `attention` (`receiver_unreachable`) | first observed failure |
| 6 | `/health` answering, heartbeat present but not a fresh `serving` or `starting` beat | `attention` (`heartbeat_stale`, or `heartbeat_unreadable` for an invalid file) | `written_at` |
| 7 | Any attention item | `attention` | earliest item |
| 8 | Otherwise | `ok` | — |

A missing heartbeat with a healthy `/health` is not an attention item: it is what an
older production revision looks like, and its loop rows read 此版本未提供.

Attention items, with named thresholds:

| Code | Condition |
|---|---|
| `slot_held` | A slot in state `held`, with its open recovery's attempts |
| `slot_without_reservation` | A busy slot with no active reservation |
| `lease_expired` | A running item whose lease has expired |
| `cleanup_pending` | A `job_cleanup` row not done for more than 10 minutes |
| `issue_status_error` | An issue whose status read has failed 3 or more times in a row |
| `reservation_cancel_pending` | A reservation in `cancel_requested` for more than 5 minutes |
| `loop_erroring` | A loop with 3 or more consecutive errors |
| `loop_stalled` | A loop busy longer than its limit: receive 2 min, lifecycle and progress 10 min, schedule and resource_recovery 30 min, pool 90 min |
| `webhook_rejected` | A rejected webhook in the last 15 minutes. A continuous secret mismatch keeps it raised, while a stray scanner clears |
| `receiver_unreachable`, `heartbeat_stale`, `heartbeat_unreadable` | Rules 5 and 6 above |

Failed and blocked jobs appear in `recent` with their outcome. They are not attention
items. Quiet webhook periods, such as nights and weekends, are informational only.

## HTTP surface and page

- Routes: `GET` or `HEAD` for `/`, `/monitor.js`, `/monitor.css` and `/api/status`.
  Other paths return 404. Other methods return 405 with `Allow: GET, HEAD`. No endpoint
  reads a query string or a body.
- Host check: the `Host` header, without its port, must be an IP literal, `localhost` or
  a configured hostname, compared case-insensitively. Otherwise the response is 421,
  and a missing header gets 400. This blocks DNS-rebinding reads from a malicious site
  a teammate visits.
- Headers on every response:
  - `Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self';
    connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none';
    frame-ancestors 'none'`
  - `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`,
    `X-Frame-Options: DENY` and `Cache-Control: no-store`
  - an explicit UTF-8 content type
- Server: the receiver's exclusive-bind class, with `SO_EXCLUSIVEADDRUSE` on Windows and
  no reverse-DNS lookup. Daemon request threads have a 5 s socket timeout, and request
  logging is suppressed. One JSON line is printed at startup (`monitor_ready`) and on a
  fatal error.
- Cache: the status JSON is built at most once every 2 seconds, however many teammates
  are watching. Requests during a build wait for it.
- `/health` probe: `http://127.0.0.1:<port>/health` with a 2 s timeout, through a
  `urllib` opener with an empty `ProxyHandler`, so a desktop proxy's environment
  variables cannot intercept it. Only a 200 counts as answering.
- Page:
  - `lang="zh-CN"`, a responsive layout for phones, and light and dark themes.
  - Every value is rendered with `textContent`, never `innerHTML`. Links are created only
    for `https://linear.app/` and `https://github.com/` URLs. The label maps for
    verdicts, states, skills, outcomes, loops and attention codes live in `monitor.js`;
    the JSON stays language-neutral.
  - The page polls every 5 seconds. When a poll fails, it keeps the last good data
    dimmed, shows a banner that the monitor connection has been lost since the first
    failure, and the header and tab title read 连接中断 instead of the last verdict. When
    the ledger cannot be read, the work sections keep their last good data, labelled
    工作数据停留在 HH:MM.
  - A footer states that the page is read-only and that actions happen in Linear.

## Failure behaviour

- A transient ledger error, such as a busy timeout or a mid-migration schema, is
  reported in `ledger.error_type`. The page keeps the last good sections, and each poll
  retries.
- The monitor refuses to start, exiting non-zero with one JSON line, for any of these:
  an unreadable config, an ownership marker that does not match the config (the same
  read-only `check_ownership` other maintenance commands use), a `bind` that is not an
  IPv4 literal, a port equal to the receiver's, or a port already in use. It never
  touches `.controller.lock`, since even a brief acquisition could make a starting
  `serve` fail.
- launchd `KeepAlive` restarts the monitor on the Mac. On Windows, the scheduled task's
  restart-on-failure setting does; that behaviour must be verified on the host.
- The monitor keeps no durable state. After a restart, `unresponsive` is anchored to the
  heartbeat's `written_at`, so "since" survives.

## Deployment

- **macOS:** `install-launchd` adds `<prefix>.monitor` beside `serve` and `tunnel` when
  the config has a `monitor` block. It runs `python -u -m agent.service monitor --config
  <absolute path>`, logs in the same directory, and loads nothing by itself.
- **Windows:** the README documents two steps the operator performs with administrator
  rights:
  - an inbound firewall rule for the monitor port, limited to the Private profile and
    `LocalSubnet`;
  - a scheduled task that starts at logon as the same user as `serve`, runs the
    configured Python with the same arguments, appends output to a log file, and
    restarts on failure.
- **TestBot:** during live testing it runs in a terminal tab with a `monitor` block on
  port 8781, since TestBot's receiver uses 8766.

## Rollout

Each step needs its own authorization. Merging deploys nothing.

1. Pull request with the tests below, reviewed and merged. The repository is public, so
   commits and PR text carry no hostnames or LAN addresses.
2. Live on the Mac with TestBot:
   - Bind the monitor to the LAN on port 8781 and open it from a phone on office Wi-Fi.
   - Stop the controller and see 无响应 within about a minute. Restart it and see 正常.
   - @TestBot on an issue the operator chooses, and watch the job appear and progress.
3. Production on Windows, as an operator decision:
   1. Place the merged revision in its own directory. It runs only `monitor`; production
      `serve` stays on its accepted revision.
   2. Confirm that production's revision ignores unknown top-level config keys.
   3. Make sure the production config has an absolute `local_root`, because the default
      is the checkout running the command. If it has none, add exactly the path
      production already uses. From the monitor directory, `doctor --config` must then
      report production's actual ledger path.
   4. Add the `monitor` block and validate the edited file with `doctor`, since a JSON
      error would break every new worker's config load. Production needs no restart.
   5. Create the firewall rule and the scheduled task.
   6. From another office machine, check that the page loads and its jobs match
      `doctor`.
   7. Loop heartbeats appear after production's next normal upgrade.
4. Documentation:
   - the README section;
   - an operating contract "Status monitor" section: read-only, what it shows and omits,
     opt-in LAN exposure, no authentication, and not proof of anything beyond its
     checks;
   - AGENTS.md project map entries and the development workflow's TestBot monitor port;
   - `config/development.example.json` with a loopback `monitor` block.

## Verification

Offline tests use standard-library unittest, written test-first. Fixture ledgers are
built in temporary directories: tests may construct `Ledger`, the monitor code may not.

- `tests/test_monitor_view.py`:
  - Job fields: every `display_state`, `queue_position`, `retry_at`, `state_since` from
    audit and not `updated_at`, PR labels and URL allowlists, and the recent window and
    cap.
  - Resources and history: `no_change` outcomes, `retried`, slots with holders and
    recoveries, and `unity_queue`.
  - Rules: the attention thresholds and the verdict table on a fake clock.
  - Older ledgers with optional tables and columns removed.
  - Redaction: tokens, host paths, raw errors, descriptions, comments, checkpoints and
    pending questions seeded into fixtures never appear in the JSON.
- `tests/test_heartbeat.py`:
  - Recording, and the `consecutive_errors` reset.
  - The payload schema and validation of hostile content: wrong types, huge numbers,
    extra keys and odd error types.
  - File states: fresh, stale, stopped, missing, corrupt, oversized, and on POSIX a FIFO
    or symlink.
  - Replace-on-write, and a simulated `PermissionError` while writing.
  - Revision reading inside and outside a Git checkout.
- `tests/test_monitor.py`, with a real server on `127.0.0.1` port 0:
  - Responses: every route, 404, 405, the 421 host check, and the security headers.
  - The two-second cache under concurrent requests.
  - Read-only proof: ledger bytes and mtime unchanged, and no `-journal` created.
  - `/health` answering, failing (500), timing out and refused, including with proxy
    environment variables set.
  - Startup refusals.
  - A static check that `monitor.js` contains no `innerHTML`, `outerHTML`,
    `insertAdjacentHTML`, `eval`, `Function(` or `document.write`.
- Existing suites:
  - `serve` writes `starting`, every loop and `stopped`, and a failing heartbeat write
    never stops serving (`test_service.py`).
  - Every webhook outcome is counted, and a failing recorder never changes a response
    (`test_receiver.py`).
  - `monitor` config validation.
  - The monitor launchd agent only when configured (`test_deploy.py`).
  - `doctor` output unchanged after moving to `readonly_db`.
- The full offline suite runs on macOS. CI runs it on macOS and Windows with Python
  3.13. Windows CI is test evidence, not production acceptance, and the production
  steps above need a check on the Windows host.

## Risks and limitations

- There is no authentication. Anyone on the office network, including guest Wi-Fi if it
  shares the subnet, can read issue identifiers, titles and job states.
- A worker that can write the state root can forge the heartbeat. `/health` stays
  independent, and the page never offers controls whose use a forgery could trigger.
- `/health` proves only that the receiver's handler answers. The page is not proof of
  Linear, tunnel or Unity health beyond the signals it lists.
- Webhook counts are in memory and reset at restart. Issue events are not stored in the
  ledger, so an older revision without the heartbeat shows only the time of the last
  agent-session event.
- Loop limits are initial estimates. They raise attention, never `unresponsive`, and
  can be tuned after observing production.
