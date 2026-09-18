# Farm repository map (volatile layer)

This file holds the world facts the sweep depends on: which repositories exist,
who may write where, and which generator owns which artifact. When the
architecture moves again, update THIS file; `SKILL.md` holds only the durable
process and should not need edits for repository changes. Last grounded:
2026-08-25, post protobuf-pivot (design doc
`docs/2026-08-11-protobuf-pivot-design.md` in Farm-Client).

## Access matrix

| Repository | Sweep access | Role |
| --- | --- | --- |
| Farm-Client | Read/write | Unity client: HotUpdate/AOT code, tests, generated protobuf artifacts (network + config), published FGUI descriptors |
| farmgui | Read/write | FairyGUI source (XML), source tests/contracts, authorized GUI publishing |
| farm-hive | Read/write | The Go game server (`modules/<feature>/`). The only server fix surface |
| Farm-Contract | Evidence + handoff target | Behavior contracts (`openspec/specs/`) and network proto (`proto/`). The sweep never writes here; contract changes happen in sessions rooted in this repo |
| farm-common | Evidence only | Designer-owned config tables and the `designer/configgen` Go toolchain. The sweep never edits tables or toolchain |
| farm-server | RETIRED — never a fix target | Old C++ stack, frozen at the 2026-08-11 pivot. Read it only as porting reference when an issue is explicitly a porting batch (Farm-Contract CLAUDE.md §三); never route work, worktrees, builds, or `wsl-server-build` at it |
| farm-hive-server | Out of sweep scope | Deployment/ops repo; deployment needs are recorded gaps, not sweep work |

Before editing any writable repository, read that repository's own
`AGENTS.md`/`CLAUDE.md`; its build/test commands and local rules govern the
work done there.

## Generated artifacts and their owning generators

Never hand-edit any of these; each changes only as the fresh output of its
documented generator run against a clean, manifest-valid source checkout.

| Artifact in Farm-Client | Source of truth | Regeneration |
| --- | --- | --- |
| `Assets/Scripts/HotUpdate/Proto/Network/*.pb.cs` + `NetworkMessageRegistry.g.cs` | Farm-Contract `proto/` | Unity menu **Tools/Proto/导出 Protobuf 网络协议**; contract root from EditorPrefs `Farm.NetworkProtobuf.ContractRoot` or env `FARM_CONTRACT_ROOT` |
| `Assets/Scripts/HotUpdate/Proto/Configs/*.pb.cs` and `Assets/GameRes/GameConfigs.pb/*` | farm-common designer tables via `designer/configgen` (Go) | Unity menu **Tools/Proto/导出全部 Protobuf 配置表** from a clean, manifest-valid farm-common checkout |
| `Assets/GameRes/FairyRes/<Pkg>/<Pkg>_fui.bytes` (+ atlases) | farmgui package XML source | Authorized FairyGUI Editor publish from the farmgui worktree; copy only the required fresh outputs |

Config routing rule: if the client artifact disagrees with the farm-common
table, the artifact is stale → client-owned re-export. If the artifact matches
the table and the value is still wrong, the table itself is wrong →
`config-source` handoff to the designer/owner; re-export would faithfully
reproduce the error, so do not run it.

## farm-hive specifics

Go server, module-per-feature under `modules/`. Not yet live: no
data-migration code. Gates: focused package tests plus the repository's own
build/test commands (`go build ./...`, `go vet ./...`, `go test ./...` unless
its instructions say otherwise). A message a module must send, its cache-merge
semantics, ordering, and error codes are contract questions (see below), not
free design space. No deployment target is reachable from the sweep: deploy,
restart, and end-to-end verification are recorded verification gaps, never
blockers and never justification for a client-side workaround. Never start or
kill server runtimes.

## Farm-Contract handoff discipline

Message semantics — cache-merge rules (full replacement / per-id increment /
clear sentinels), ordering dependencies, error-code user-visible behavior — are
decided in Farm-Contract's `openspec/specs/<module>.md` (客户端侧要求), never
re-derived from observed server behavior: observation pins one build, not the
contract, and enshrining it creates silent drift.

- `[CLIENT-PENDING]` items are the client side's debt to claim, and open
  questions are adjudicated — but both happen in a session rooted in
  Farm-Contract, because contract deltas land in that repo's in-flight change.
  The sweep's move is a concrete handoff to a **separate Claude Code task
  rooted in Farm-Contract**, not only a Linear comment. Its standalone prompt
  gives the resolved absolute contract root and spec path, exact spec entry/open
  decisions, and the issue's Linear identifier plus URL; it instructs the new
  task to read Farm-Contract's own `AGENTS.md`/`CLAUDE.md` and does not restate
  consumer conclusions. Order matters: keep Backlog/Todo with `needs-more-info`,
  post and read back the complete Chinese dependency comment, persist the
  checkpoint, and only then — with explicit user authority — dispatch the task
  and ledger its exact prompt plus task ID/link as `task-dispatched`, then
  continue unrelated issues. Task dispatch is not
  `handoff-complete`; only the Farm-Contract PR/write-back recorded in both the
  ledger and Linear proves completion (形如「契约回账已发：Farm-Contract PR
  #NN」). Without authority/capability, execute the main skill's complete
  six-step `authority-blocked` sequence: create no task, ask once, keep
  Backlog/Todo + `needs-more-info`, post/read back the Chinese authority
  blocker, persist the checkpoint, and continue unrelated issues.
- The `openspec` CLI resolves by cwd. Run from a consumer repo it prints
  vacuous empty results with exit code 0 (`No active changes found.`); that
  output is about the directory searched, not the contract, and is never
  evidence.
- A contract not yet landed is bridged client-side only via
  `ContractPendingException` (`Assets/Scripts/HotUpdate/Core/NetWork/PendingContract.cs`),
  never by guessing semantics.

## Environment bindings (Claude Code)

- Linear access: the Linear MCP server tools (`list_issues`, `get_issue`,
  `save_issue`, `list_comments`, `save_comment`, `list_issue_labels`,
  `list_users`, ...). There is no `linear:linear` skill in this project's
  Claude Code setup; the server id prefix is environment-specific, so
  resolve the tools by their suffix, not by a hardcoded prefix. If no Linear
  MCP tool is reachable, that is a system-wide failure: stop, do not
  substitute a saved list or a web read.
- FairyGUI publishing: no supported CLI publisher exists, and this project's
  Claude Code setup has no desktop-driving binding (the Codex copy of this
  skill binds `computer-use:computer-use` here). Treat the FairyGUI Editor
  publish as a user hand-off: prepare the farmgui XML change in its worktree,
  state exactly which packages need republishing, and record the unpublished
  descriptor as a verification gap rather than copying stale `_fui.bytes`.
- `wsl-server-build` is retired along with farm-server; never load it for
  sweep work.
