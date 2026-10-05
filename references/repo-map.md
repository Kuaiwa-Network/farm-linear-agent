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
| Farm-Contract | Read/write for `fix` and `feature` in their own worktree | Behavior contracts (`openspec/specs/`) and network proto (`proto/`); follow repo instructions and record confirmed decisions |
| common (`Kuaiwa-Network/common`, local checkout often `farm-common`) | Read/write for `fix`: designer-owned config tables corrected at their source and regenerated through `designer/configgen`, as draft PRs; for `feature`: the definition layer, its regenerated inventory and count constants only | Designer-owned config tables and the `designer/configgen` Go toolchain |
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
For the delegated bug or change, resolve
uncertain behaviour by asking in Linear with `await-input` (which adds `needs-more-info`).
Once the human decision is clear, update the relevant contract first, then the affected client,
server and configuration sources. Link draft PRs and record dependencies and the decision's
source. Do not invent `DECIDED` attribution, merge, deploy, or open another task just to edit
this contract. Missing generators remain explicit verification gaps or blockers.

## Code worker (`feature`): Farm-Contract

### Native Windows commands

For a Windows feature attempt (`execution.platform == "win32"`), use the
absolute `execution.python` from dispatch. Set `PYTHONUTF8=1` before starting
Python and use `-X utf8 -B`; use that interpreter for every `python3` example
and the FarmBot ledger CLI. The Bash snippets below are macOS/Linux examples.
Windows uses the native entry points in this table, with the same stage,
provenance, gate and publication requirements. Never fall back to Bash, Git
Bash, MSYS or WSL when a native entry point/tool/cache is missing; record the
exact missing prerequisite and pause if the stage cannot proceed.

| Repository operation | Native Windows entry point |
| --- | --- |
| Contract gates 3–7, 9, 10 and 12 | Selected Python runs the matching `tools/check-breaking-waiver.py`, `gen-manifest.py --check`, `check-markers.py`, `check-msg-naming.py`, `check-proto-fields.py`, `check-spec-provenance.py`, `check-readme-inventory.py` and `check-openspec-validate.py`; gate 3 retains `BREAKING_WAIVERS origin/main` arguments |
| Contract gates 1, 2, 8 and 11 | Native `buf build`/`buf lint`, selected Python `tools/check-coverage.py` and `tools/check-openspec-config.py` |
| Contract manifest regeneration | Selected Python `tools/gen-manifest.py` |
| Common inventory/generate/verify | `designer\tools\gen-config.cmd` with the same flags and absolute paths as `gen-config.sh` |
| Backend full protocol synchronization | Selected Python `gen-msg-protos.py --contract ABSOLUTE_CONTRACT --git ABSOLUTE_GIT --go ABSOLUTE_GO --protoc ABSOLUTE_PROTOC --gomodcache PREPARED_MODULE_CACHE --gocache OWNED_BUILD_CACHE` |
| Backend registry generation | Selected Python `gen-registry.py --go ABSOLUTE_GO --gomodcache PREPARED_MODULE_CACHE --gocache OWNED_BUILD_CACHE` |
| Backend protocol/registry gates | Selected Python `ci/check_msg_proto.py` and `ci/check_proto_registry.py` with explicit Git/Go/prepared-cache flags; the message gate also needs protoc |
| Backend Contract provenance | Selected Python `ci/check_contract_sync.py --contract ABSOLUTE_CONTRACT --git ABSOLUTE_GIT` |
| Backend designer digest before publication | Selected Python `config/pb/designer-digest.py --common ABSOLUTE_CONFIG_CHECKOUT --git ABSOLUTE_GIT --commit FULL_CONFIG_SHA` |
| Backend config generation | Selected Python `config/pb/gen.py --common ABSOLUTE_COMMON --git ABSOLUTE_GIT --go ABSOLUTE_GO --protoc ABSOLUTE_PROTOC --gomodcache PREPARED_MODULE_CACHE --gocache OWNED_BUILD_CACHE` |
| Backend independent designer/config gates | Selected Python `ci/check_designer_pin.py --common ABSOLUTE_COMMON --git ABSOLUTE_GIT`, `ci/check_pb_manifest.py --git ABSOLUTE_GIT`, and `ci/check_config_pb.py` with common/Git/Go/protoc/prepared-cache flags |

Select ordinary absolute native executables from the already prepared host
toolchain and check the repository's actual pins; use the tools' own `--help`
for flags. Module caches must already contain the pinned dependencies and be
readable by the worker; a writable build cache belongs under STATE_DIR. Do not
copy private host paths into PRs, install tools, expose credentials, change
account/app settings or grant writes to shared caches to make a check pass.
Common's launcher selects pinned Go/Git from the process PATH; set up only that
worker process's environment, retaining its repository sanitization.
Set `GOPROXY=off` and `GOSUMDB=off` for the native verification processes;
missing prepared dependencies must fail without downloading or authenticating.
Common's source-digest Go tests also run natively. The complete producer acceptance
script and release publisher remain on supported macOS/Linux/Jenkins; a Windows
worker names that acceptance as not run locally and requires its applicable CI.
It never runs the release publisher or Jenkins itself.

The native digest reads committed blobs without extraction; retain the pending
64-zero archive-hash placeholder until actual publication. The native config
generator stages outputs before publication and preserves them on generation
failure; it has no legacy `--cache` option. `--source` and the local `gen.bat`
adapter remain UNPINNED and cannot substitute for the stage's pinned generation.
Actual sandbox/cache access remains a host acceptance prerequisite.

Stage A's root, and the closing steps' waiver removal. Follow the repository's own rules by path: `README.md` §一
(the gap-first loop: three inputs, candidates and costs, confidence tiers, the client half of the gap list,
scenario markers, testable scenarios and 验收) and §二 (the twelve local gates and how to install their tools),
`openspec/config.yaml` (its context, `rules.proposal`, `rules.specs`, `rules.tasks` and `operations`),
`AGENTS.md` and `CLAUDE.md` (a handoff cites scenario numbers and never restates rulings),
`tools/check-breaking-waiver.sh` (the waiver file's two-way rule) and `.github/workflows/ci.yaml`.

- The twelve gates, in `README.md` §二's order: `buf build`, `buf lint`,
  `bash tools/check-breaking-waiver.sh BREAKING_WAIVERS origin/main` (after `git fetch origin main`),
  `bash tools/gen-manifest.sh --check`, `bash tools/check-markers.sh`, `bash tools/check-msg-naming.sh`,
  `bash tools/check-proto-fields.sh`, `python3 tools/check-coverage.py`, `bash tools/check-spec-provenance.sh`,
  `bash tools/check-readme-inventory.sh`, `python3 tools/check-openspec-config.py` and
  `bash tools/check-openspec-validate.sh`. `ci.yaml` pins buf and openspec (1.72.0 and 1.7.0 on 2026-09-28); read
  the pins there each time, because another buf version can judge a breaking change the other way. CI runs gate ③
  on pull requests only.
- After changing `proto/`: `bash tools/gen-manifest.sh`, then the README file-inventory row (gate ⑩). Package and
  message names feed the msgId hash and never change; field numbers are never reused.
- Gate ③ reads `BREAKING_WAIVERS` both ways: an unrecorded break is red, and so is a recorded one that no longer
  exists. The lines a change adds therefore go stale when it merges, and every Farm-Contract PR stays red until
  someone removes them; FarmBot's closing steps do, on `farmbot/<key>-waivers`.
- Where FarmBot differs from the repository's own sessions: the Code card is the batch's Linear issue and FarmBot
  creates none; its `handoff-repository` is the 交棒; a ruling stands only when a named person answers (no
  high-confidence or three-working-day defaults); and the client half's 现状 evidence comes from the read-only
  checkouts of Farm-Client's and farmgui's default branches that FarmBot passes as `reads`, each command given with
  the commit read.

## Code worker (`feature`): farm-common definition layer

Stage B's root, and stage C's. Follow the repository's own rules by path: `designer/CLAUDE.md` (the source-digest
test after any change under `designer/china/source`; a new column goes where 策划 place it; `code`-style symbol
names are persistent player-data field names), `README.md` (generate and verify),
`designer/tools/check-config-artifact.sh` (the artifact counts and the sites that repeat them),
`designer/china/client-required-fields.txt` and `designer/tools/ssml_reader.py`.

- The definition layer (feature-workers design §6.3, D13), under `designer/china/source`: the `_table.xml/` sheets,
  `_convert.xml` (export registration), `_enum.xml` with `_protoenum.xml` (every enum in both), and, for code-side
  vocabulary, `_func.xml/`, `_context.xml/`, `_sbinary.xml/`, `_event.xml/` and `_struct.xml/`. Precedent commits
  in farm-common: `6044e5c` (a client-only config's `_func`, `_context` and `_sbinary` entries, with its count
  constants in two Go tests and two tool scripts), `e41c918` and `8164942` (event and context vocabulary), and
  `9644c39` then `7db8dbe` (an enum registered in one half only broke encoding until the second).
- Data files, data rows, data values and global-key values are 策划's, never FarmBot's. A declared default fills
  every blank cell, so FarmBot declares only type-neutral defaults.
- Edit SpreadsheetML by targeted text replacement: spreadsheet tools and XML libraries rewrite whole files. Read
  cells with `designer/tools/ssml_reader.py`, read-only, which resolves columns by `ss:Index` as the production
  readers do. It is a Python module with no command line: import it from `designer/tools` and use `read_text`,
  `worksheet_body` and `header_columns` (the last keeps empty and repeated header names, so it can tell "exactly
  one column").
- `bash designer/tools/gen-config.sh inventory --out ABSOLUTE_PATH` rewrites
  `designer/china/client-export-inventory.tsv`; `generate --out` and `verify --against` take absolute paths outside
  the checkout, each `--out` a fresh directory that does not exist yet. The launcher changes directory, so a
  relative path lands under `designer/configgen`.
- `designer/tools/check-config-artifact.sh` needs dotnet SDK exactly 8.0.423, Go 1.25.1 and protoc 35.1 on macOS or
  Linux, and refuses other platforms.
- Leave `designer/configgen/profiles/farm-hive.json` alone: farm-hive generates the tables its own `TABLES` lists,
  and a profile change moves all eight count sites.

## Code worker (`feature`): farm-hive sync, registry and designer pin

Stage D's root, and the closing steps' re-sync and pin. Follow the repository's own rules by path: `CLAUDE.md` (iron
rules 4, 5 and 10), `README.md` (生成链, 新配表的落点清单 and its gates list), `gen-msg-protos.sh` (its header: the
three provenance marks and why both protocol gates refuse them), `gen-registry.sh`, `cmd/protoreggen/main.go`,
`config/pb/gen.sh` (its header: modes and exit codes), `config/pb/lib-designer-source.sh`,
`config/pb/toolchain.env`, `ci/*.sh` and `.github/workflows/ci.yaml`.

- Protocol sync: `bash gen-msg-protos.sh` copies every `TARGETS` proto from `FARM_CONTRACT` (default
  `../Farm-Contract`, which in a FarmBot job is the item's own Farm-Contract worktree) and rewrites
  `ci/msg-proto-manifest.sha256` with the contract commit. A commit that is not on Farm-Contract's default branch is
  marked `-unreachable`, a checkout with uncommitted proto changes `-dirty`; `ci/check_msg_proto.sh` and
  `ci/check_contract_sync.sh` refuse both, so they stay red until the re-sync after the contract merges. The
  re-sync reads the merged contract from FarmBot's read-only checkout of Farm-Contract's default branch, only once
  that checkout contains the merge commit.
- Registration: a new contract `.proto` needs a `TARGETS` line, a blank import and a `targets` entry in
  `cmd/protoreggen/main.go`, and wider gate pathspecs when its directory level is new; `bash gen-registry.sh`
  regenerates the registries; each new client message needs a handler and a `config/cs_handler_census.txt` row.
- The designer pin, as farm-hive main has it on 2026-09-28 (follow farm-hive's own instructions if they have
  changed, and say in the PR which mechanism was used): `config/pb/toolchain.env` holds `DESIGNER_SOURCE_VERSION`
  (`<committer date>.<short hash>` of the branch tip common's `designer-source.pipeline` publishes),
  `DESIGNER_SOURCE_ARCHIVE_SHA256` (only a publish produces it) and `DESIGNER_SOURCE_DIGEST` (`ds_content_digest` in
  `config/pb/lib-designer-source.sh`, over a directory whose `source/` holds `designer/china/source`).
  `bash config/pb/gen.sh --common CHECKOUT --cache DIR` materializes the pinned commit from a local farm-common
  checkout and checks the digest without the archive checksum or the file server. Copy the published values from the
  pipeline's output, never from a guess. The header of common's `designer-source.pipeline` says farm-hive consumes
  only `config-artifact.pipeline`'s archive; farm-hive main does not use that archive, and its own files decide.
- Generation deletes its outputs first. After a failure restore only the generated files, `git checkout --
  'config/pb/*.proto' 'config/pb/*.pb' 'config/pb/*.pb.txt' 'config/pb/*.pb.go' config/pb/artifacts.sha256`,
  never the whole directory, which also reverts `toolchain.env` and the scripts. A new table also needs its `TABLES`
  entry and the README's new-table checklist.
- Local gates, in `ci.yaml`'s build-job order, that need neither the file server nor a GitHub token:
  `bash ci/check_designer_pin.sh --gate --common CHECKOUT --cache DIR` (CI's first gate, which there downloads the
  archive), `go vet ./...`, `go build ./...`, `go test -race -timeout 300s ./...` with `DESIGNER_SOURCE_CACHE` set
  (a narrow-type test needs it), `bash ci/check_pb_manifest.sh`, `bash ci/check_config_pb.sh` with
  `FARM_COMMON_DIR` and `DESIGNER_SOURCE_CACHE` once `config/pb` is committed (it needs a clean status), then, after
  the two protocol gates, `bash ci/check_proto_registry.sh` and the later `ci/check_*.sh` steps. CI's
  service-backed variants (a MongoDB replica set, Redis, etcd and NATS) are not available to a worker.
- The published pin: the pipeline prints the three values at the end of its run. Its short hash can be longer than a
  local `git rev-parse --short`; then `gen.sh` runs again, so that the provenance line of
  `config/pb/artifacts.sha256` matches (`ci/check_pb_manifest.sh`).
- Jenkins branches in common: `farmbot/<key>-config` at the config commit, and `farmbot/<key>-config-<n>` (n from 2)
  when a re-pin names a commit that does not descend from the one already pushed; neither ever carries a commit of
  FarmBot's own, and neither is ever force-pushed.

## Code worker (`feature`): expected CI on its PRs

From the feature-workers design §6.9. A PR body carries FarmBot's local results; FarmBot never changes CI.

| Repository | Check | Expected | Why |
|---|---|---|---|
| farm-hive | designer pin gate (`ci/check_designer_pin.sh --gate`) | red until a human runs the publish and FarmBot writes the published values | the gate downloads the archive; an unpublished version fails, and the steps after it do not run, build and tests included |
| farm-hive | `ci/check_msg_proto.sh`, `ci/check_contract_sync.sh` | red until the contract merges and FarmBot pushes the re-sync | both refuse an `-unreachable` pin |
| farm-hive | every later gate | not run, rather than red, until the re-sync is pushed | CI stops at the first failing step |
| Farm-Contract | the twelve gates | green when FarmBot ran all twelve locally with CI's pinned buf and openspec; otherwise the PR names those not run | the same scripts as CI; a gate ③ failure caused by another change's stale waivers on main is reported to that change's owner |
| common | config artifact acceptance | green only when `designer/tools/check-config-artifact.sh` passed locally; otherwise the PR says it was not verified | beyond generation the job runs gofmt, vet, `go test` with the production acceptance test, an inventory comparison, two generations, exact artifact counts, a visibility check and a C# compile |

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
  During an authorized FarmBot fix job, a UI bug fix or a small change to
  existing UI (widened on 2026-09-28; farmgui's `AGENTS.md` records it before
  any instance runs that revision), FarmBot may export the affected packages
  directly without another permission request or human GUI handoff. This
  applies to both Codex and Claude Code workers and supersedes the earlier
  unpaid-license handoff and separate export-approval guidance.
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
