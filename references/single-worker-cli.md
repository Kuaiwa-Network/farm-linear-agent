# Single worker CLI

This guide applies to `single-worker-v1`. Use the launch payload's absolute
`repo_root`, `execution.python`, `database`, `state_dir` and owned worktrees.
Invoke Python with an argument array from repo_root. On Windows use the supplied
Python executable; on macOS use the configured Python 3 interpreter.

The first execution-tool call is `startup.start_argv`, before any directory listing,
token check or instruction-file read. The launcher archives prior attempt tokens;
old checkpoints do not imply an active claim. Startup retries belong to the host,
with unchanged sandbox/approval checks and a ten-minute deadline per attempt.
Start with `startup.start_argv`: claim once, save the token exclusively at
`state_dir/token`, and read the returned resume context. If that call has an
ambiguous outcome, inspect the process/claim result; never blindly claim again.
Use `--db DATABASE` before the command and `--item ITEM --token-file STATE_DIR/token`
for authenticated commands. The token itself must never appear in logs.

The main worker may use native Codex subagents for bounded work within the same
permissions. Helpers report to the main worker; they do not claim this issue or
call its lifecycle/resource/publication commands. The main worker coordinates
shared resources and human questions, integrates results, settles helpers before
parking or finish, and checkpoints their work. No FarmBot child task is created.

| Command | Additional arguments and purpose |
| --- | --- |
| `fetch-issue` | No token arguments. Read live issue facts before delivery or after a new request. |
| `issue-context` | No token arguments; `--view resume` or `full` includes history, questions, reports and inputs. |
| `download-uploads` | `--out STATE_DIR/uploads`, optional repeatable `--url UNSIGNED_UPLOAD_URL`: authenticated download of issue and current Project Overview attachments, with a local manifest. Reuse intact files; never fetch private upload URLs anonymously. |
| `renew` | Read maintenance/withdrawal flags; keep the current claim alive. |
| `checkpoint` | `--input FILE`: bounded JSON progress, candidate SHAs, report paths and next action. |
| `pop-inbox` | Read attributed requests. Reuse actual existing answers. |
| `revalidate` | `--fingerprint VALUE`, only after reviewing changed inputs. |
| `await-input` | `--question TEXT --reason question\|waiting`: save a checkpoint and release resources first; then exit. |
| `resolve-question` | `--question-id ID --message-id ID` (repeatable), actual attributed replies only. |
| `await-resource` | `--resource unity_slot --mode interactive --commit SHA`, or `server_slot --mode batch --commit SHA --server-test-plan FILE`. Stay in this worker. |
| `run-integration` | `--client-command FILE`: JSON argv exercising reserved Unity against one isolated game server. Request an atomic pair with `await-resource --resource unity_slot --mode interactive --commit CLIENT_SHA --integration-plan FILE`; see [combined verification](client-server-integration.md). |
| `resource-status` | Same claim. Returns queued/preparing/ready/held/settled state, precise capacity reason, commit and reservation token-file path. |
| `cancel-resource` | `--reservation ID`: cancels only this claim's still-queued request. An acquired resource must settle normally. |
| `unity-tools` | List actual MCP tool schemas after Unity is ready. |
| `unity-tool` | `--name NAME --input FILE`: JSON object arguments, bound to the reserved instance; raw MCP result. |
| `unity-test-result` | `--job-id ID`: read a retained test report in the owned instance, including failed jobs whose normal response has `result=null`. Save stdout privately. No test launch or release. |
| `unity-read` | `--uri URI`: read a resource from the reserved Unity instance. |
| `release-resource` | **Use the reservation token file returned by resource-status**, not the claim token. `--outcome quiescent\|unclean`. |
| `publish-ui` | `--packages FILE`: JSON package name/ID map; native export into private staging, no Client installation. |
| `prepare-go-dependencies` | `--repo REPO --module MODULE --commit SHA`; inspect `go-dependencies --request-id ID`. |
| `verify-publication` | `--repo REPO`: fresh exact destination/branch/private-repository check before publishing. |
| `retry-approval` | `--error-file FILE`: complete UTF-8 approval-review capacity error that explicitly confirms no action executed. Checkpoint, settle helpers and release resources first; then exit on `retry_queued` or `retry_exhausted`. |
| `prepare-comment` | `--kind started\|blocker\|delivery --body-file FILE`; returns an outbox action ID. |
| `post-comment` | `--action-id ID`: idempotent outbox send; confirms the actual remote comment. |
| `finish` | `--outcome delivered\|blocked --input FILE`; optionally `--check-input` for shape or `--check-ready` for readiness, including the listed PRs. |
| `maintenance-handoff` | Fresh checkpoint with `handoff`, no open resource; requires maintenance requested. Then exit. |
| `withdraw` | Checkpoint and exit when delegation is withdrawn; publish nothing further. |

Use `COMMAND --help` for exact syntax. The large parser retains historical
commands for old state inspection and offline replay; repository handoff,
presentation subtasks, mandatory baseline comparison, UI receipts and semantic
closing checks are refused for this contract.

Keep downloads in `STATE_DIR/uploads`. ZIPs and unchanged extracted files expire
after every worker attempt for the item has fully exited and the retention grace
period has passed (24 hours by default). The manifest and hashes remain; rerun
`download-uploads` to fetch expired inputs. Save review reports and derived images
under `STATE_DIR/reports`, outside the download cache.

Chat and work share `download-uploads`. It refreshes the issue's project relation
and reads uploads directly embedded/linked in the issue description, human comments
and current Project Overview (short summary and rich body). No `--url` downloads
both sources; repeat unsigned `--url` to select files. Read refreshed `issue.project`
in `issue-context`: `description` is the summary and `content` is the Overview body.
The manifest and summary record `project:PROJECT_UUID:content`/`description` sources;
ZIPs use the existing safe extraction and cache. Other project/document links do
not expand the download scope. Do not write a custom downloader after a refusal.

For `retry-approval`, save the complete returned message starting with
`Automatic approval review failed: Selected model is at capacity.` including its
explicit not-executed explanation, or Codex's original `exec_command failed: CreateProcess`
error wrapper. Save it verbatim without additional commentary. The command only
retires this claim and schedules another attempt; it never executes or approves
the action. It shares the model-capacity limit of three retries at 60, 180 and 600
seconds. Stop using the old token and exit for either result. On resume recheck
requests/authority and run normal approval again; publication also needs a fresh
`verify-publication`. Other denials or ambiguous execution outcomes do not qualify.

A typical checkpoint is ordinary JSON:

```json
{"summary":"Fix implemented; affected tests passed","candidates":{"Farm-Client":"FULL_SHA"},"reports":["ABSOLUTE_PRIVATE_REPORT_PATH"],"handoff":"Review diff and publish the draft PR."}
```

A delivered finish uses:

```json
{"summary":"What changed","verification":"Actual checks, results and remaining limitations","prs":["https://github.com/OWNER/REPO/pull/123"],"comment_action_id":"CONFIRMED_DELIVERY_ACTION"}
```

Each listed PR is checked on GitHub: this issue's branch in its owned worktree,
head at that worktree's current HEAD, and open (draft or marked ready) or merged.
Push the final commit, then run `finish --check-ready` before posting the
delivery comment, so a wrong number or stale head is fixed before anyone reads it.
Successful delivery registers these verified PRs as the issue's own output for
later continuations and foreign-work checks. `--check-ready` does not register
ownership. A checkpoint `prs` list alone does not register or authorize a PR.

Use `no_change` with a concrete reason instead of PRs when no code change is
needed. A finish is FarmBot delivery, not a merge, deployment or Linear Done.

Server plans contain exactly `packages` (module-local paths), `tests` (exact
Test names, or an empty list for all selected package tests) and `race` (boolean),
for example `{"packages":["./breeding"],"tests":[],"race":false}`. Request services
only when needed. Test commands still run in the worker. Resource
readiness never certifies a test result. A lost Unity mutation response must be
reconciled by observing the Editor/test job, not automatically retried.

`unity-test-result` requires the same ready reservation and a test started after
its acquisition. It returns the exact job ID, reservation and candidate commit,
terminal counts and up to 50 non-passing test details with explicit truncation
flags. Missing/evicted/in-flight results remain unavailable, never a pass.
Use normal `get_test_job` for progress. If details are capped, inspect the relevant
retained details before attributing failures; do not infer that omitted tests passed.
Collect reports while the reservation is owned, then release a quiet Editor
before drafting PR prose or waiting for remote CI. Release still requires the
host's quiescence check.

For queued resources during maintenance, cancel and checkpoint. For acquired
resources, collect in-flight results, stop owned test processes, settle and
release. The host still checks Editor/process quiescence before reuse. Server
containers are recycled only after the host can establish process quiescence.
