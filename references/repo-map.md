# Farm repository map (volatile layer)

FarmBot note: this map is inherited from the Farm-Client sweep skill. Repository names and
eligible repositories for FarmBot come from each skill's `skill.json` manifest; actual write
authority comes from the current worker's `stage.write_repositories`. The "Sweep access" column
below describes repository roles, not permission for every fix attempt. In particular the configuration repository is
`common` (GitHub `Kuaiwa-Network/common`, often checked out locally as `farm-common`), and the
`fix` skill may change designer tables there through the documented `designer/configgen`
toolchain, as draft PRs. Generated artifacts are still never hand-edited.

Current `common/README.md` documents a headless producer:
`bash designer/tools/gen-config.sh generate --profile farm-hive --profile unity-client --out /absolute/absent/artifact`
and `verify` against that artifact. Read the checked-out README and toolchain pins before using it.
This can validate/generate both profiles without starting Unity. Consumer import is a separate step:
follow the consumer repo's documented importer and do not replace generated client files by hand.
Do not infer that all configuration fixes require Unity executeMethod merely from the older menu
description below. Report the specific missing consumer step if one remains unavailable.

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
| farmgui | Read/write | FairyGUI source (XML), source tests/contracts, authorized paid-CLI publishing |
| farm-hive | Read/write | The Go game server (`modules/<feature>/`). The only server fix surface |
| Farm-Contract | Read/write for `fix` in its own worktree | Behavior contracts (`openspec/specs/`) and network proto (`proto/`); follow repo instructions and record confirmed decisions |
| common (`Kuaiwa-Network/common`, local checkout often `farm-common`) | Read/write for `fix`: designer-owned config tables corrected at their source and regenerated through `designer/configgen`, as draft PRs | Designer-owned config tables and the `designer/configgen` Go toolchain |
| farm-server | RETIRED — never a fix target | Old C++ stack, frozen at the 2026-08-11 pivot. Read it only as porting reference when an issue is explicitly a porting batch (Farm-Contract CLAUDE.md §三); never route work, worktrees, builds, or `wsl-server-build` at it |
| farm-hive-server | Out of sweep scope | Deployment/ops repo; deployment needs are recorded gaps, not sweep work |

Before editing any writable repository, read that repository's own
`AGENTS.md`/`CLAUDE.md`; its build/test commands and local rules govern the
work done there.

## Generated artifacts and their owning generators

For UI changes, follow **UI source ownership** in `docs/operating-contract.md`: correct authored
structure in farmgui before adapting client code; keep behaviour and data logic in code. An
unavailable publisher is a handoff requirement, not a reason to patch around the UI structure.

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

## Farm-Contract workflow for FarmBot

The fix manifest includes a readable Farm-Contract worktree. Switch with `handoff-repository`
to start a fresh worker rooted there before editing or invoking its OpenSpec process; cwd matters.
Contract-root workers must follow Contract's restrictions on Superpowers and consumer code.
For the delegated bug, resolve
uncertain behaviour by asking in Linear with `await-input` (which adds `needs-more-info`).
Once the human decision is clear, update the relevant contract first, then the affected client,
server and configuration sources. Link draft PRs and record dependencies and the decision's
source. Do not invent `DECIDED` attribution, merge, deploy, or open another task just to edit
this contract. Missing generators remain explicit verification gaps or blockers.

## Environment bindings (Claude Code)

- Linear access: the Linear MCP server tools (`list_issues`, `get_issue`,
  `save_issue`, `list_comments`, `save_comment`, `list_issue_labels`,
  `list_users`, ...). There is no `linear:linear` skill in this project's
  Claude Code setup; the server id prefix is environment-specific, so
  resolve the tools by their suffix, not by a hardcoded prefix. If no Linear
  MCP tool is reachable, that is a system-wide failure: stop, do not
  substitute a saved list or a web read.
- FairyGUI publishing (operator instruction, 2026-09-21): resolve the editor
  executable from host configuration or a host-specific shared-memory note,
  then verify that it exists and has an active paid license for batch export.
  Keep installation paths and license observations out of versioned guidance.
  During an authorized FGUI bug fix, FarmBot may export the
  affected packages directly without another permission request or human GUI
  handoff. This applies to both Codex and Claude Code workers and supersedes the
  earlier unpaid-license handoff and separate export-approval guidance.
  Run `-batchmode -p <job-worktree/FGUIProject.fairy> -b <comma-separated-packages>
  -o <absolute-job-staging-directory> -logFile <absolute-log-file>` with a timeout
  and only one publisher per project/output directory. `-o` is literal and does
  not expand `{publish_file_name}`. Verify exit, completion logs, fresh descriptor
  identities, dependencies, atlas inventory and hashes; stage validated outputs
  into the authorized client worktree's package directories, preserving `.meta`
  GUIDs, then run the relevant Unity checks. Never hand-edit generated files or
  substitute stale outputs. A failed export is a technical blocker to diagnose,
  not a reason to request routine export permission. Use another host only after
  verifying its paid-license activation. This authority covers local export and
  issue-scoped client integration; existing PR, merge and deployment rules remain.
- `wsl-server-build` is retired along with farm-server; never load it for
  sweep work.
