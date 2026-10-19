# Single worker CLI

This guide applies to `single-worker-v1`. Use the launch payload's absolute
`repo_root`, `execution.python`, `database`, `state_dir` and owned worktrees.
Invoke Python with an argument array from repo_root. On Windows use the supplied
Python executable; on macOS use the configured Python 3 interpreter.

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
| `download-uploads` | `--out STATE_DIR/uploads`, optional repeatable `--url UNSIGNED_ISSUE_URL`: authenticated download of this issue's attachments, with a local manifest. Reuse intact files; never fetch private upload URLs anonymously. |
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
| `prepare-comment` | `--kind started\|blocker\|delivery --body-file FILE`; returns an outbox action ID. |
| `post-comment` | `--action-id ID`: idempotent outbox send; confirms the actual remote comment. |
| `finish` | `--outcome delivered\|blocked --input FILE`; optionally `--check-input` for shape or `--check-ready` for local readiness. |
| `maintenance-handoff` | Fresh checkpoint with `handoff`, no open resource; requires maintenance requested. Then exit. |
| `withdraw` | Checkpoint and exit when delegation is withdrawn; publish nothing further. |

Use `COMMAND --help` for exact syntax. The large parser retains historical
commands for old state inspection and offline replay; repository handoff,
presentation subtasks, mandatory baseline comparison, UI receipts and semantic
closing checks are refused for this contract.

A typical checkpoint is ordinary JSON:

```json
{"summary":"Fix implemented; affected tests passed","candidates":{"Farm-Client":"FULL_SHA"},"reports":["ABSOLUTE_PRIVATE_REPORT_PATH"],"handoff":"Review diff and publish the draft PR."}
```

A delivered finish uses:

```json
{"summary":"What changed","verification":"Actual checks, results and remaining limitations","prs":["https://github.com/OWNER/REPO/pull/123"],"comment_action_id":"CONFIRMED_DELIVERY_ACTION"}
```

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
