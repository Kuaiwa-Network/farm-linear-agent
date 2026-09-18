# Live smoke test on the Mac: FarmBot against real Linear and real Codex workers

**Date:** 2026-09-18. **Host:** Mac mini, macOS 26.6.2, codex-cli 0.154.0, Python 3.13.14 (python.org build),
cloudflared 2026.9.1. **Code:** `main` at `25fee68` (Plan 1a merged) plus the fixes below, committed during the run.
**Linear:** the FarmQA OAuth app renamed to FarmBot (app user id unchanged), client credentials, webhook on a
quick tunnel. **Question answered:** does the Plan 1a code work end to end with real Linear events, real
worktrees of the Farm repositories and real `codex exec` workers?

## Result

Yes, after five code fixes and three operational steps. Every live path ran green by the end:

| Run | Trigger | Event→launch | Launch→end | Tokens | Outcome |
|---|---|---|---|---|---|
| FARM-1227 chat 1 | @farmbot mention (API comment) | 7 s | 71 s | 32k | delivered: three-sentence answer as a session response |
| FARM-1127 fix | delegation from the Linear UI | 21 s | 286 s | 65k | blocked, correctly: main already carried the fix, no PR opened |
| FARM-1227 chat 2 | mention, counting task | 6 s | 109 s | 22k | delivered: table of 1,841 files / 577k lines |
| FARM-1227 chat 3 | mention, read seven subsystems | 7 s | 167 s | 65k | delivered: responsibility and dependency table |
| FARM-1227 stop | mention, then Stop in the session | 6 s | 9 s | – | cancelled 0.97 s after the stop signal; worker and child killed |

The fix run is the most telling one. The issue duplicated FARM-1095, whose draft PR had been closed unmerged;
the worker read the background comment, checked `common` main and the client's committed protobuf, found
commit `6bfe03e2` already contained the fix, posted a blocker comment with evidence, recorded the Unity
verification gap, and opened nothing. An independent check of the clone confirmed every claim.

## Fixes made during the run

| Finding | Fix | Commit |
|---|---|---|
| Farm-Client carries 3.6 GB of LFS objects; the host's global smudge filter would download all of them on every worktree add | `GIT_LFS_SKIP_SMUDGE=1` and `GIT_TERMINAL_PROMPT=0` on every FarmBot git call | `ce77f26` |
| Codex's workspace-write sandbox allows only the cwd and temp dirs and no network; a worker must write its other worktrees, the ledger and a token file, and reach Linear and GitHub | `[sandbox_workspace_write] writable_roots` and `network_access = true` in the isolated home; `state_dir` in the launch message; FarmBot's root on the worker's `PYTHONPATH` | `dfc0bac` |
| A worktree's commits and index live in the bare clone | the item's clones are writable roots too | `6f4593b` |
| An ignored or rejected webhook left no trace | one JSON log line per delivery: status, result, type, action | `12b2c86` |
| The fix worker finished and commented on the issue but posted no session activity, so Linear kept the session active | `finish` posts the final `response`; the scheduler posts `error` for dead or never-started workers and `thought` for a requeue | `9cf8250` |

Operational steps, not code: `brew install cloudflared`; symlink `/etc/ssl/cert.pem` into python.org Python's
empty `etc/openssl/` directory (urllib failed TLS verification without it); seed the four bare clones from the
local human checkouts before the first launch (two seconds each) instead of fetching from GitHub inside the
scheduler tick.

## Facts worth keeping

- Delegating through the API (`issueUpdate.delegateId`) sets the delegate but creates no agent session and
  sends no webhook. Delegation from the Linear UI does. @mentions work from either.
- `app:assignable` is granted in the client-credentials token request; there is nothing to enable in the
  OAuth app settings. The token response's `scope` field is the proof.
- Only a `response` or `error` activity completes a Linear session; issue comments never do.
- A denied write inside the Codex sandbox fails cleanly with "Operation not permitted"; no approval
  escalation appeared with `--approve-for-me`. Codex adds a `[projects."<cwd>"] trust_level = "trusted"`
  table to the isolated `config.toml` on its own.
- The Stop path: signal received → `stop_requests` row → worker and descendant killed → item cancelled →
  confirmation response, in under one second, while the worker was still between launch and claim.
- Worker cost: 22k to 65k tokens per chat answer, 65k for the fix investigation. A quick tunnel hostname
  changes on every restart of `cloudflared`.

## Open items, all for Plan 1c

1. A `fix` item that needs no change has no outcome of its own; the worker records it as `blocked` with an
   explanation. Add a no-change outcome to the ledger, the contract and the skill.
2. Mac deployment: a launchd agent for `serve` and the tunnel, a `seed-clones` command, and the
   certificate step in the setup notes. The endpoint question was settled on 2026-09-18 and then
   deferred, since a quick tunnel is enough for testing: buy a cheap throwaway domain, put it on
   Cloudflare, run a **named** tunnel on it. Rejected with reasons, so they are not re-proposed:
   moving the company domain hiiland.com to Cloudflare (refused, it carries Feishu mail); delegating
   one subdomain to Cloudflare (subdomain zones are Enterprise, the CNAME partial setup is Business);
   花生壳 (its free tier has no HTTPS certificate and no fixed port, paid starts at ￥1299/year and
   still needs ICP 备案 plus a DNS transfer to 贝锐); Tailscale Funnel (its docs state it serves only
   `*.ts.net` names, the hostname is device-bound, and device keys expire by default); ngrok (custom
   domains are paid). A VPS running Caddy stays viable if a suitable server appears, but a
   mainland-hosted address serving a custom domain on 443 needs ICP 备案.
3. Windows: re-run the runtime spike there; the Codex sandbox roots need their Windows equivalent; port the
   supervisor script.
4. Claude runtime parity: `--add-dir` for the state dir and clones, and an isolated-auth recipe.
5. Receiver: bound the `guidance` length; catch `sqlite3` errors in event processing.
6. Delegation from the API is a documentation item: automations that delegate must go through the UI or a
   Linear-side mechanism that creates sessions.

The service and tunnel started for this test live only as long as the operator's session; nothing on the
Mac restarts them yet. Every hostname resolved from this Mac lands in the `198.18.x.x` fake-IP range, so a
local proxy client intercepts all DNS and sits in the path of every outbound connection. Confirm the tunnel
still connects with that proxy off before relying on an unattended daemon.

To run another test session: start the service, start `cloudflared tunnel --url http://127.0.0.1:8765`,
paste the new random hostname with `/webhook` into the Linear app settings, and delegate from the Linear UI.
