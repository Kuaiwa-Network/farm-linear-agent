# Development workflow

Develop FarmBot in an isolated checkout. Runtime worker behavior belongs in
`agent/dispatch.py`, `agent/worker_dispatch.py`, the active `skills/chat` and
`skills/work`, and their tests. Follow [the operating contract](operating-contract.md).
The current registry loads only chat and work. Archived profiles are available
only to explicit offline replay; live historical jobs need settled host adoption.

## Offline checks

Use temporary ledgers, local Git remotes, fake workers and stub Linear/Unity APIs.
`runtime="fake"` and `FARMBOT_LINEAR_STUB_DIR` select separate integrations; neither
alone makes an arbitrary invocation offline. Start with affected tests, then the
full suite when behavior changes warrant it. See [CI](ci.md).

```powershell
python -m unittest discover -s tests -p 'test_single_worker*.py' -v
python -m unittest discover -s tests -v
```

Use the installed/configured Windows Python; on macOS use `python3`. Native
process, sandbox, Unity and installation changes require the relevant platform
checks. Record skips honestly. Windows success does not certify macOS, or vice versa.
Preserve path/identity/publication security checks when correcting test fixtures.

## Authorized live tests

Use TestBot, a second Linear app in the same Kuaiwa AI workspace, on real issues
and the real configured repositories. Separate test workspaces, copied issue
snapshots and sandbox repositories are an abandoned design. A local test or code
change does not authorize a live test, deployment, restart or webhook change.
Honor operational authorization already given for the task.

Each instance needs separate credentials, endpoint/port, ledger, memory, clones,
worktrees, slots and logs. Choose an explicit `development` profile with pinned
Linear app/workspace IDs and a fresh stable absolute `local_root` outside checkouts.
Use [host startup](startup.md) to initialize it. Never share state roots, relabel
unmarked production state, delete ownership markers or bypass the controller lock.
A different config filename alone is not isolation. Keep secrets in private
configuration; propagate its selected absolute path to every worker.

The bots share labels and repository destinations. Mention/delegate only issues
selected by the operator, never the same issue to both bots. Comments, branches,
labels and draft PRs are real. Preserve configured private destinations, branch
checks and Git trust boundaries; test publication changes offline first.

To test conversation only, mention the app without requesting implementation.
For implementation, delegate the selected issue and state the actual scope and
stop point. One main worker owns the whole fix/feature, choosing optional native
subagents only for useful independent work. There are no A–G repository stages,
mandatory UI approval rounds or automatic baseline comparison. Repository-specific
requirements and genuine unresolved human decisions still apply.

## Tools and credentials

Use native sandboxing and automatic review. Do not introduce MXC or an automatic
fallback after a denial. Prepare required native tools and optional dependencies
on the host before acceptance; the worker does not administer the host.

Feishu reads use the configured bot identity, never a personal login. Prefer a
bot-only store or the explicitly supported `lark_cli.app_id`/`secret_env` variant.
`work` gets the configured document-reader grant; chat does not. Keep personal
profiles out of any store readable by a worker. Windows DPAPI stores are per-user:
a separate HOME does not isolate them. Do not downgrade a personal Keychain store.
Verify actual app permissions and access in the chosen worker context; an offline
version/profile check is not a successful document fetch. Do not print credentials
or raw config. Missing access becomes a concrete question after available inputs
are checked, not an invitation to change identity or permissions.

## Deployment and acceptance

Use maintenance pause, wait for fresh readiness, preserve process/resource
quiescence and deploy the full tested revision with matching controller, worker
and Monitor sources. Follow [maintenance and rollout](../README.md#maintenance-and-rollout).
Never treat HTTP health or a dead parent PID as full readiness.

Adopt only an operator-selected settled historical job. Preserve its source,
reports and actual unanswered human questions. `maintenance-resume --only-issue`
keeps every other issue paused, including after the selected job finishes.
A restart, main advancing or old unrelated failures do not require another merge
or test run. Reuse reports only while relevant inputs match.

Record the actual release, runtime, candidate commits, job/attempt and evidence.
A delivered draft is not a merge, deployment or Linear Done. Review resulting PRs
normally; FarmBot workers do not merge or change issue status. Retain database
and recovery evidence: checking out old source does not reverse schema/state.
Historical experiments are records, not current instructions or authorization.
