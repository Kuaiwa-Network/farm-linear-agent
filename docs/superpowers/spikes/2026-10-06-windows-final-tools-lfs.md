# Final native Windows tools and LFS transfer checks

Measured on 2026-10-06 in an isolated development checkout at FarmBot
`1a9b60a1e721fa48df040afd531b024c30a610e4` (merged #143), using the operator's
selected current Windows account. No production config, ledger or service was
read or changed. The development TestBot remains serving chat/fix only, with
zero workers, no pending cleanup and its prior private evidence retained.

## Candidate identity and empty-root doctor

Only six Markdown files differ from merged #141 (`0f97c794`), whose final full
CI passed 1,760 tests on both platforms. Executable code, tests and workflow
are identical. A full offline rerun is unnecessary for the documentation delta.

The first strict source-byte comparison stopped before doctor: the new Windows
checkout contains CRLF, whereas the callback build receipt names LF source.
The canonical Git blob equals the tested #141 candidate byte for byte, and
the checkout differs only by CRLF. No application code, Git setting, executable
or containment check was changed to explain that difference.

| Identity | SHA-256 |
|---|---|
| Canonical callback source / saved build source | `ba7ec68b2f7ddff24c4d43c03d334cca887ace758226a071ab6fcdbe7dd298e4` |
| Windows checkout source (CRLF) | `fec030c802a73411d20d5ce10b2e774cb816823799580174d7145644539c0d6d` |
| Final native callback executable | `8714b0336221d7716e83bf12f960ebbd4dd91fd24941f47ca75e683ee277f457` |
| Existing native GitHub CLI executable | `e98a646f524d0094fac3a6c5866ebc8fdcca0461a11f4146525a15b853e60068` |

The whitespace-free selector resolves to the exact verified executable.
`PYTHONUTF8=1` precedes Python startup and inherited selectors/credential
overrides are sanitized. Doctor uses dummy Linear values, `runtime=codex`,
the selected unelevated backend, chat/fix/feature enabled, `farmbot` profile
without HOME, and a new absolute empty scratch root outside all checkouts.

Doctor completed in **1.233 seconds**, exit **2**, `incomplete`, solely because
the empty root has no ledger (`ledger_unreadable`). It created no state, made
no live API call, and exposed no dummy values. Temporary config/root cleanup
completed. All required feature tools pass with no Bash/MSYS/WSL route:

| Tool | Measured version |
|---|---|
| Python | 3.13.16 |
| Git | 2.54.0.windows.1 |
| Git LFS | 3.7.1 |
| Go | 1.26.6; required >=1.25.1, source default for the empty root |
| protoc | 35.1 |
| buf | 1.72.0 |
| Node | 24.19.0 |
| OpenSpec | 1.7.0 |
| Optional .NET SDK | 8.0.423 |
| lark-cli | 1.0.82; farmbot present, zero other profiles/user logins |

## Final native Git callback

A fresh no-model Codex 0.156.1 unelevated worker context uses the exact
candidate gate, explicit workspace-write/network policy and verified native
Job membership. It reads all five configured GitHub repositories' branch
lists, inherited callback selection and a fresh owned Contract fetch of main
`29e6cfe1430a7c7313febc642f95db4ea6c3fb18`, all with exit zero. An unrelated
valid dummy CA is refused with exit 128. The complete probe takes **15.002
seconds**; its Job is empty, reader finished and registered runtime receipt
unchanged. No credential value is saved, displayed or put in a token variable.
The development controller's earlier callback selection remains untouched.

## Real LFS read and offline upload calibration

Tracked `.lfsconfig` from the owned development Farm-Client and farmgui clones
selects an internal HTTP LFS server, not github.com. Neither URL contains
userinfo or a query. Endpoints remain private and unchanged. GitHub callback
acceptance does not establish this separate server's transfer behavior.

One existing farmgui asset at main `5970bcf3fc42d2f1e0ae95f6a687b77953c6907c`
is selected from its committed pointer with a 64 KiB cap. Native `git lfs fetch`
downloads **15,114 bytes**, exit zero in **1.021 seconds**, exact SHA-256
`6278703207b9ec47607828d4b79a82da303a9a31bb58c7331500cc1c4c49cc28`.
The complete restricted check takes **1.862 seconds**, with verified policy,
owned child membership, empty Job, finished reader and unchanged runtime receipt.
Only standard owned worktree/Git write parts are granted. No LFS upload,
GitHub mutation, model turn or credential setup occurs.

A separate local-loopback LFS fixture then calibrates the proposed push and
download commands with a fixed **1,024-byte** synthetic text payload, containing
no credentials or game data. Its SHA-256 is
`7001b167c6e61e8db9be5568eb0f5e8a02231072e0648de8b9b7745e7bf3dc04`.
Actual Git LFS executes batch upload, byte upload, verify, batch download and
byte download; the uploaded bytes and redownloaded bytes match exactly.
Both commands exit zero in **1.317 seconds**, **2.674 seconds** overall.
Protected clone config, worktree Git pointer, attributes and LFS config remain
unchanged; clone trust, effective grants, Job quiescence and runtime receipt
checks pass. This fixture is offline apart from its local loopback listener.

## Approved real-server upload: authentication gap

The operator approved the prepared 1 KiB check with “Go” on 2026-10-06.
At **07:58:08 UTC**, native `git lfs push --object-id origin <fixed object>`
attempted that exact payload on farmgui's unchanged committed LFS endpoint.
It returned **exit 2**, **1.444 seconds** for the command and **2.411 seconds**
overall. No successful byte upload or test-object download was recorded.
The previously passing loopback transfer remains a calibration, not a real
server upload pass.

The private error says the askpass response is unavailable and credentials
for the LFS batch request were not found. A credential-free upload-batch
request independently returns **HTTP 401** with a **Basic** authentication
challenge. The configured endpoint uses HTTP. Its URL and raw private logs
are withheld from this record. The native GitHub callback deliberately
accepts only HTTPS github.com prompts, so refusing this internal destination
is correct; broadening it would risk sending GitHub credentials elsewhere.
This measures no usable internal-server write authentication in the sanitized
worker environment, not the absence of every credential on the machine.
Anonymous read success does not establish write permission.

Effective workspace-write policy and the native child's Job membership pass.
The Job is empty, reader finished, runtime registration unchanged, protected
Git files unchanged and clone trust intact. No credential, profile, account,
endpoint, service or production setting was configured or changed. The
remaining manual step is an operator-approved native Windows authentication
route with upload permission for this internal LFS server, including approval
of its transport. Credentials must be entered privately, never in this chat
or public Git history. Then rerun the exact fixed-object upload/download check;
no GitHub helper expansion or Bash/MSYS route is part of that step.

## Native headless client protocol export

A separate private fixture uses Farm-Client
`b7a07c2669f2c407f4c789d026d344bd4798273e` and clean Farm-Contract
`29e6cfe1430a7c7313febc642f95db4ea6c3fb18`, read from validated development
clones. Eleven exporter/shared-helper C# source files are copied from exact
Git blobs without edits. The existing `NetworkProtobufExporter.Export`
workflow runs under .NET SDK **8.0.423** with no-op editor services, native
protoc **35.1**, UTF-8 Python startup and paths containing spaces and Unicode.
Neither Unity nor a model turn starts. The contract snapshot is outside all
worker write roots. An initial private stub syntax typo caused one harness
CS1022 error before worker execution; correcting that fixture changed no
application source.

Google.Protobuf **3.35.1** is downloaded from its official NuGet package into
the fixture. Its netstandard2.0 DLL matches the committed LFS pointer's
**501,912 bytes** and SHA-256
`67fd69447f2e8eeb45c4c27375e4625f0bb93c22fa9774ba9795f10a15cb190f`.
Nothing is installed into the host's account or global package settings.

At **08:05:17 UTC**, the exact FarmBot #143 gate and Codex **0.156.1**
unelevated command context verify workspace-write policy with **network off**
and native child ownership before allowing execution. The measurements are:

| Check | Result |
|---|---|
| First actual exporter run | exit 0, **3.338 seconds** |
| Second actual exporter run | exit 0, **2.709 seconds** |
| Contract inputs | **60 proto files**, exact pinned clean commit |
| Generated messages and registry | **61 C# files**, **589 wire messages** |
| Two complete generations | identical filename/SHA-256 maps |
| Generated C# and registry compilation | .NET 8 build exit 0 |
| Existing metadata | preserved byte for byte; **56** metadata files retained |
| Exporter editor lifecycle | one start, stop and refresh per run |
| Worker command total / whole native probe | **9.741 / 10.277 seconds** |

The canonical sorted output-map SHA-256 is
`4b570f38507478494086a1bf98a4b5f01529fc0b8a164f2d2552d7e645650096`.
Exporter sources and all contract inputs remain unchanged, both trusted clones
remain valid, registered runtime evidence is unchanged, and the build and
worker Jobs are empty with the RPC reader finished. UTF-8 private logs,
source/output maps and sanitized result receipts are retained locally.

This establishes headless network export feasibility on native Windows for
these pinned inputs. It is a source fixture, not a client branch or delivered
feature, and it does not run the full client test suite or Unity verification.
No-op editor services do not create new `.meta` files: `Card.pb.cs`,
`Datalog.pb.cs`, `FriendGardenGifting.pb.cs`, `GuidedWechatClaim.pb.cs` and
`InviteGift.pb.cs` need metadata when Phase C installs a real export, using
sibling templates and fresh GUIDs as [§6.7](../specs/2026-09-24-feature-workers-design.md#67-stage-f-farm-client) requires. A durable supported
headless entry point and metadata installation remain Phase C implementation.
No game source, branch, commit, PR or issue was changed by this fixture.

## Remaining client/UI and release prerequisites

1. Provide approved native Windows internal-LFS write authentication and
   pass the real fixed 1 KiB upload/download. D10 LFS push is still open;
   the native headless proto feasibility check above is now measured.
2. Obtain an approved real card/scope for remaining live common/config/hive
   stages and their native worker generators. Running-worker withdrawal is
   still unmeasured; the completed cancellation check used a parked job.
3. Implement Phase C's client stages, durable headless entry point, complete
   metadata installation, client validation and closing/write-back workflow.
   Later UI authoring/export, farmgui permission changes and scoped LFS asset
   hydration remain subsequent work under the design's phase order.
4. Obtain separate production promotion/feature-enablement authorization,
   confirm the final host's selected tool/account/profile identities, and
   complete its release and recovery acceptance. These isolated development
   measurements do not certify production readiness.

FARM-1346/FARM-1425 and their unmerged drafts remain untouched. Farm-Contract
#328 remains an unmerged draft with its branch and cancellation recovery refs
preserved. The development TestBot continues chat/fix only; production config,
ledger and service remain outside this work. No new platform skip is introduced.

The documentation follow-up passes **75** skill/reference tests in **0.422
seconds**, zero failures, errors or platform skips, plus local link/anchor,
known-credential/private-path and whitespace checks. Four Markdown files change;
application code, tests and workflow remain identical to the verified candidate,
so a full offline suite rerun is unnecessary for this record-only change.
