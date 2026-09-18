# Runtime spike: headless worker CLI flags, auth, MCP injection, kill behaviour

**Machine:** Mac (Darwin 25.6.0, macOS 26.6.2, arm64, hostname `Elendils-Mac-mini.local`). **To be re-run on the Windows host in Phase 1c** — the Windows host was not available for this spike, and at least one finding below (Claude Code auth storage) is expected to differ on Windows.

**Runtime versions:** `codex` = codex-cli 0.154.0 (`/usr/local/bin/codex`). `claude` = Claude Code 2.1.229 (`/Users/elendil/.local/bin/claude`). `python3` = Python 3.13.14.

**Repo/branch:** `farm-linear-agent`, branch `phase1a-core`. All scratch files under `/tmp/farmbot-spike/`; nothing outside the record committed.

## Summary of the one material deviation from the brief

The brief's Step 1 command includes `--ask-for-approval never`. That flag does not exist in codex-cli 0.154.0 (`error: unexpected argument '--ask-for-approval' found`) — this CLI version replaced the old approval-policy flag. `codex exec` defaults to `approval: never` on its own, so plain shell tasks (Step 1) work without any approval flag. However, **MCP tool calls are rejected under `approval: never`** with the literal agent-reported message `MCP tool call requires approval, but approval policy is never`. The fix that works in this version is `--approve-for-me` (routes approval through automatic review, and implies `sandbox: workspace-write` itself — it cannot be combined with an explicit `--sandbox` flag). `--approve-for-me` was verified to work for both the plain task and the MCP task, so it is the one flag Phase 1a's launcher should use in place of the brief's assumed `--sandbox workspace-write --ask-for-approval never`.

---

## Codex (codex-cli 0.154.0)

### Step-by-step log

| Step | Command (env + args) | Result | Wall-clock |
|---|---|---|---|
| 1a | `CODEX_HOME=<isolated> codex exec --cd <work> --sandbox workspace-write --ask-for-approval never --skip-git-repo-check --output-last-message last.txt "Read README.md…"` (brief's literal command) | FAIL — CLI parse error: `unexpected argument '--ask-for-approval' found`. Flag doesn't exist in 0.154.0. | 1s |
| 1b | Same, minus `--ask-for-approval never`, fresh isolated `CODEX_HOME` (no auth seeded yet) | FAIL — banner shows `approval: never` (default), but repeated `401 Unauthorized` from `wss://api.openai.com/v1/responses` and the HTTPS fallback; `last.txt` never created; exit=1 | 23s (5 retries then gave up) |
| 1c | Same command, after `cp ~/.codex/auth.json <isolated CODEX_HOME>/auth.json` | PASS — exit=0; `last.txt` = exactly `hello` (5 bytes, confirmed via `od -c`, no trailing newline) | 19s |
| 2a | Step 1c's command with prompt `"Call the tool named probe and reply with exactly its output."`, `[mcp_servers.probe]` added to isolated `config.toml`, `--sandbox workspace-write` (no approval flag, i.e. default `approval: never`) | FAIL — exit=0 but `last.txt` = the agent's own error text: `MCP tool call requires approval, but approval policy is never` | 22s |
| 2b | Same prompt, `--approve-for-me` + `--sandbox workspace-write` together | FAIL — CLI parse error: `the argument '--sandbox <SANDBOX_MODE>' cannot be used with '--approve-for-me'` | 0s |
| 2c | Same prompt, `--approve-for-me` alone (no `--sandbox`) | PASS — exit=0; `last.txt` = exactly `probe-ok`; banner shows `approval: on-request`, `sandbox: workspace-write [workdir, /tmp, $TMPDIR]` | 25s |
| 1d (sanity) | Step 1's plain README prompt re-run with `--approve-for-me` alone | PASS — exit=0; `last.txt` = `hello` | 16s |
| 3 | `codex exec --cd <work> --skip-git-repo-check --approve-for-me "Run the shell command: sleep 120"`, backgrounded; PID noted; wait 5s; `SIGTERM` top PID; wait 5s; check | See kill sequence below | ~10s to observe cascade |

### Auth inheritance

Isolated `CODEX_HOME` does **not** inherit auth from `~/.codex` (confirmed by the 401s in row 1b). Copying `~/.codex/auth.json` (600 bytes... actually 4129 bytes, mode 600) into the isolated `CODEX_HOME` and rerunning fixed it (row 1c). No other file was copied or needed. File contents were never printed, quoted, or committed — only the filename and pass/fail outcome are recorded here. Note: `CODEX_HOME` is not solely an auth directory — codex populates it with a full app-state tree (sqlite databases, `goals_1.sqlite`, `logs_2.sqlite`, session dirs, etc.) on first run even before any auth succeeds; the launcher should budget disk/cleanup accordingly per work item.

### MCP injection result

Works, but **only** with `--approve-for-me` (see deviation note above). Writing `[mcp_servers.probe]` into the isolated home's `config.toml` and pointing `command`/`args` at a local `python3` script is sufficient — codex auto-starts every MCP server declared in `config.toml` for the session (confirmed: the probe server process was running even during the unrelated Step 3 kill test, because it was still declared in `config.toml`), not only when the model decides to call it.

### Final-message capture

`--output-last-message <file>` reliably captures exactly the final agent text with no extra whitespace/newline in both the plain-task case (`hello`) and the MCP case (`probe-ok`).

### Kill sequence that worked

Process tree for Step 3: shell → PID A (`node` wrapper, e.g. 50253) → PID B (rust `codex` binary, child of A, e.g. 50255) → children of B: the declared MCP server (python3), an internal `codex-code-mode-host` helper, and the actual sandboxed shell-exec child running `sleep 120` (e.g. PID 50472).

- A single `SIGTERM` to the top-level PID (A) killed A, B, the MCP helper, and `codex-code-mode-host` within 5 seconds. **No `SIGKILL` was needed** for that part of the tree.
- The `sleep 120` child **survived** that `SIGTERM` — `ps` showed it reparented to PID 1 with `STAT=SNs` (its own new session/process group; codex's sandboxing detaches the actual shell-exec child into a new session, presumably so a sandbox-exec/seatbelt wrapper can't kill the parent by mistake — but this also means the parent's exit doesn't reap it).
- A **second, separate `SIGTERM` sent directly to the orphaned `sleep` PID** killed it immediately. No `SIGKILL` was needed there either.
- **Conclusion for the launcher:** killing the top PID alone is not sufficient. The launcher must snapshot the descendant PID tree before killing (or otherwise independently track the sandboxed shell-exec child's PID), send `SIGTERM` to the top PID, then verify and separately `SIGTERM` any surviving descendant. `SIGKILL` was not required anywhere in this test, but the launcher should still keep it as a fallback for a process that ignores `SIGTERM`.
- PIDs killed in this test: 50253 (→ cascaded 50255, 50329, 50471), then 50472 directly. All confirmed dead by final sweep; no stray `codex`, `python3`, or `sleep` processes remained.

---

## Claude Code (Claude Code 2.1.229)

### Auth storage discovery (before running anything)

`~/.claude/.credentials.json` **does not exist** on this Mac. `security find-generic-password -s "Claude Code-credentials"` confirms this installation stores its OAuth credential in the **macOS Keychain**, not a flat file. This matters because the fallback specified in the task dispatch instructions ("copy `~/.claude/.credentials.json`") has no source file to copy on this machine.

### Step-by-step log

| Step | Command | Result | Wall-clock |
|---|---|---|---|
| 1 (isolated) | `CLAUDE_CONFIG_DIR=<isolated> claude -p --output-format json --permission-mode bypassPermissions "Read README.md and reply with exactly its contents."` | FAIL — `is_error:true`, `result:"Not logged in · Please run /login"` | <1s (duration_ms in the low tens) |
| 2 (isolated, literal brief command) | `CLAUDE_CONFIG_DIR=<isolated> claude -p --output-format json --permission-mode bypassPermissions --mcp-config mcp.json --strict-mcp-config "Call the tool named probe…"` | FAIL — identical auth gate: `is_error:true`, `result:"Not logged in · Please run /login"`, `duration_ms:20` — the MCP server was never reached | 0s |
| 3 (isolated) | Same isolated env, prompt `"Run the shell command: sleep 120"`, backgrounded | FAIL — same auth error in ~2s; no child process ever spawned; nothing to kill (confirmed empirically, not assumed) | 2s |
| 2-supp (default `~/.claude`, **not isolated** — run only to determine whether the MCP mechanism itself works) | Same command, no `CLAUDE_CONFIG_DIR` override | PASS — `is_error:false`, `result:"probe-ok"`, `duration_ms:10749` | 12s |
| 3-supp attempt 1 (default `~/.claude`) | Same kill-test prompt, no isolation | Process **exited on its own** at `duration_ms:19655` before any signal was sent — Claude Code's own Bash-tool policy auto-backgrounds long foreground `sleep` calls; the model's final text said "foreground sleeps are blocked in this harness… I'll report back when it exits in about two minutes," but since `-p` is one-shot there is no later turn to report back on. The whole process tree (parent + backgrounded `sleep`) was gone moments later — auto-reaped, no stray left, no manual kill needed. | ~20s |
| 3-supp attempt 2 (default `~/.claude`) | Same prompt, tight polling to catch parent + child both alive | PASS (genuine kill test) — caught the real `sleep` child (PID 51093) alive at ~8s while the parent `claude` PID (50959) was still alive; sent `SIGTERM` to the parent immediately. 5s later **both parent and child were gone**. Result file shows `is_error:true`, `terminal_reason:"aborted_streaming"`. | ~13s total (kill confirmed within 5s of signal) |

### Auth inheritance

Does **not** inherit into isolated `CLAUDE_CONFIG_DIR` on this Mac (rows 1–3, isolated). The fallback specified in the task dispatch instructions (copy `.credentials.json`) could not be attempted because that file does not exist here — this install uses the macOS Keychain instead (see discovery above). Extracting the Keychain secret into a synthesized credentials file was deliberately **not attempted**: it would mean guessing an undocumented on-disk JSON schema rather than performing the literal, pre-authorized file copy the task dispatch specified, and the task instructions were explicit about not guessing. Per the resolution rule for an unfixable non-interactive auth failure, this is recorded as **FAIL for isolation** and the spike continued using the already-authenticated default config directory, clearly labeled "supplementary," purely to determine whether the MCP and kill mechanics work in principle. **This auth-seeding gap must be re-tested on the Windows host in Phase 1c**, where credential storage may be file-based (matching the brief's assumption) rather than Keychain-based.

### MCP injection result

The mechanism itself (`--mcp-config <file> --strict-mcp-config`) works correctly and returns exactly `probe-ok` — confirmed in the supplementary (non-isolated, authenticated) run. The isolated run never reached the MCP server because the auth gate fires first. This is an auth/isolation problem, not an MCP-mechanism problem.

### Final-message capture

`--output-format json`'s `result` field held exactly `probe-ok` with no extra text (supplementary run). For the plain task, the isolated failure runs returned `result:"Not logged in · Please run /login"` in the same field — the field is used for both success and error text, distinguished by `is_error`.

### Kill sequence that worked

Process tree (supplementary attempt 2): shell → `claude -p` process (e.g. PID 50959) → intermediate shell → `sleep 120` (e.g. PID 51093, same process group as its immediate parent, no new-session detachment observed here unlike codex).

- A single `SIGTERM` to the top-level `claude -p` PID killed **both** the parent and the `sleep` child within 5 seconds. **No `SIGKILL` was needed**, and no orphan was left.
- Caveat discovered along the way (attempt 1): Claude Code's own Bash tool refuses to run a long foreground `sleep` and silently diverts it to its internal background-task mechanism instead of blocking or erroring. In one-shot `-p` mode this means the CLI process can exit "successfully" (or with a claim of async progress) well before a long-running command actually finishes. **This has a launcher implication beyond kill behaviour**: process exit must not be treated as proof the requested work finished if the transcript indicates a backgrounded/async action — a genuinely long foreground command run through Claude Code's own Bash tool may not run to completion the way it would under Codex's shell-exec.
- PIDs involved across both kill-test attempts: 50676 (isolated, died instantly on auth failure — nothing to kill), 50788/50857/50860 (attempt 1, self-exited/auto-reaped, no signal sent), 50959/51093 (attempt 2, `SIGTERM` sent to 50959, both confirmed dead 5s later). All confirmed dead by final sweep; no stray `claude` or `sleep` processes remained.

---

## Decision

**Codex is the Phase 1a default runtime.** It is the only one of the two verified end-to-end in true isolation on the tested machine — isolated auth (via a one-file copy of `auth.json`), MCP injection (via `--approve-for-me`, not the brief's assumed flags), and kill behaviour (via a two-step targeted `SIGTERM`, no `SIGKILL` needed) all have a proven, reproducible recipe here. Claude Code's MCP injection and kill behaviour are both sound (and its kill behaviour is arguably simpler — one `SIGTERM` reaps the whole tree, versus codex's two-step reap) once authenticated, but this Mac's Claude Code install stores auth in the macOS Keychain rather than a copyable file, so isolated-home auth-seeding for Claude Code has no proven recipe yet. Re-run this spike on the Windows host in Phase 1c: if Windows' Claude Code credential storage is file-based (as the brief originally assumed), Claude Code's simpler kill behaviour may make it the better default there, but Phase 1a should not default to a runtime whose isolated-auth path is unverified on any machine tested so far.
