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
initial inference that a new login was needed was premature. The follow-up
below finds an existing usable login under the account's actual Git credential
lookup policy and completes the approved transfer. This first failure proves
only the isolated GitHub-only callback cannot authenticate this LFS server;
it does not establish a host credential gap.

## Existing native Windows login: real LFS transfer passes

The operator questioned whether Git itself could upload LFS. Read-only host
configuration inspection identifies the already configured `manager` helper
and native Git Credential Manager **2.7.3**. No credential setting is added.
The initial noninteractive direct lookup includes the repository path and
requires prompting. Host Git's actual `credential.useHttpPath=false` policy
omits that path; the matching server-only lookup returns an existing username
and password successfully. Values are consumed in memory, never printed or
saved by the probe. Credential availability therefore depends on matching the
configured lookup key; the failed path-qualified lookup was not proof that
this account lacked the LFS login.

A private native C# askpass fixture calls the verified `git-credential-manager.exe`
directly with `get`, prompting disabled, and a UTF-8 credential-protocol request
matching that host policy. It accepts only the configured LFS scheme, host and
port, checks any returned origin fields, bounds output and emits only Git's
requested field. Three wrong-destination/prompt checks return failure with no
stdout/stderr, including github.com and an unrelated internal-server substitute.
The production/development GitHub callback is neither changed nor broadened.

| Native identity | SHA-256 |
|---|---|
| Existing Git Credential Manager executable | `b7f0e61535b7bab81ea11126ecf1e7ad4486426df69921a78a680dc40bae2c12` |
| Private endpoint-scoped native callback | `ae083c02df93abff152487b2927a9eaee28277cc8b4eb2f2bb6a24fc79a6e1e9` |

At **08:29:01 UTC**, the same approved **1,024-byte** object and unchanged
committed internal LFS endpoint are tested in a fresh native Codex **0.156.1**
unelevated command context at FarmBot #143. `git lfs push --object-id` exits
**0**. After removing only the exact known local cache object, `git lfs smudge`
redownloads it and exits **0**, with exactly **1,024 bytes**, SHA-256
`7001b167c6e61e8db9be5568eb0f5e8a02231072e0648de8b9b7745e7bf3dc04`,
and the restored cache matching. The two commands take **2.985 seconds**;
the complete native probe takes **3.995 seconds**.

Workspace-write/network grants, native Job membership, empty Job, finished
reader, unchanged registered runtime receipt, unchanged protected Git files
and clone trust all pass. No model turn, login, credential setup, account,
profile, Git setting, endpoint, service or game GitHub branch/commit/PR is
changed. The approved synthetic object may remain on the internal server;
no server-side deletion was attempted. Private UTF-8 logs and sanitized receipts
are retained. No Bash/MSYS/WSL route or credential value is part of the record.

D10's real native LFS push/download feasibility check now passes for this
selected account, endpoint, tool versions and scoped private callback. A
durable endpoint-scoped native credential route still belongs to client-stage
implementation before real client worker publication; this probe has not
configured one on a running controller or certified production readiness.

## Supported optional native LFS callback

The follow-up implements the optional endpoint-scoped route in
[the native build tool](../../../tools/build_windows_git_askpass.py), with
[selection and credential boundaries](../../windows-native-git.md). The
GitHub-only default remains unchanged. Selecting the two new LFS build options
pins one non-GitHub HTTP(S) origin and the existing native GCM executable/hash
in a private companion outside worker write roots. LFS pins cannot be replaced
by environment variables; GCM runs directly, noninteractively, with server-only
lookup. No credential installation or running-controller selection occurs.

At FarmBot implementation commit `3fdef24afb5c1904b628a8f9a3f56f3924396f6b`,
the actual supported builder creates a fresh private optional build. Its
executable SHA-256 is
`cf93297cfd88bfc37c3c0e0715d0c5edbcdac216cd7694833b53891e6baee29a`;
GCM remains the exact existing binary measured above. The whitespace-free
selector and companion hash are checked privately. Three invalid destination/
prompt checks fail with no stdout/stderr and no credential setup.

At **09:23:30 UTC**, a fresh Codex **0.156.1** native unelevated command context
uses that build and the exact approved object/unchanged internal endpoint.
LFS push and forced download both exit **0**; the **1,024-byte** download and
restored cache match the approved SHA-256. Commands take **2.590 seconds** and
the complete probe **3.586 seconds**. At **09:24:59 UTC**, a separate native
read-only GitHub query through the same build exits **0**, returning **31**
branch heads in **2.052 seconds**, **2.991 seconds** for the complete probe.
GitHub credentials remain on the GitHub route, separate from LFS credentials.

Both probes verify explicit policy, child membership in the owned native Job,
empty-Job cleanup, finished readers, unchanged registered runtime evidence,
unchanged protected Git files and trusted clones. No model turn, new payload,
game branch/commit/PR change, credential/account setup or production access
occurs. At **09:24:58 UTC**, the settled development TestBot still serves
chat/fix only, zero workers, zero loop errors and no pending cleanup. Its two
historical failures and four cancelled jobs remain recorded.

This validates the supported optional tool on this development PC. Selecting
it for a later real client worker and completing that worker's publication
acceptance remain pending; this is not production-host certification.

The first full validation at `3fdef24` passes **1,776 tests in 919.191 seconds**
on this development Windows PC, zero failures/errors, **69** existing platform
skips; all thirteen journeys, seven native Job tests and 23 callback/origin
checks execute. macOS CI passes **1,776 tests in 435.036 seconds**, **40**
Windows-only skips. Hosted Windows CI at the matching PR merge tree runs
**1,776 tests in 1,927.118 seconds**, with **17 failing subtest/test events**,
zero errors and **69** skips. The new LFS helper refuses before starting GCM;
other native Job tests and feature journeys run. These are preserved failures,
not a passing Windows CI result, and #147 remains unmerged at this point.

A focused local reproduction pins the same dummy executable through its
native 8.3 directory alias: the old helper refuses it. .NET Framework expands
that alias in `Path.GetFullPath`, so comparing the resulting text to the input
incorrectly rejects a valid absolute pinned file. The fix normalizes the path
and uses that same result for reparse checks, name/hash validation and execution;
it still refuses reparse pins, drive/root-relative paths, changed binaries and
wrong origins. New native regressions cover the alias and those refusal
boundaries without host credentials or new skips. The final candidate's full
validation remains required; no running controller selection changes.

### Final native acceptance and full offline validation

The 8.3-alias fix is built with the actual supported builder at final commit
`39a02ab56f2deaea22f66e0f3a8581bca0fc6d74`. The executable SHA-256 is
`19400f7bd0fd824633219ee7f8d4e5ac38b5e9c1e7364d67448a09681cd7c06f`;
the existing GCM binary remains unchanged. At **10:09:52 UTC**, the same approved
1,024-byte object passes native LFS push and forced smudge download again, exact
size/hash and restored cache, exit 0/0, **2.587 seconds** for commands and
**3.559 seconds** overall. The server can acknowledge the already uploaded
object; this repeat proves successful commands and verified forced download,
not an assertion that fresh upload bytes were transmitted. At **10:09:56 UTC**,
the same build's native GitHub read returns 31 heads, exit 0, **2.086 seconds**
for the command and **3.060 seconds** overall. Both checks preserve policy, owned
Job membership/empty cleanup, finished readers, runtime registration, protected
Git files and clone trust. No new payload, credentials/settings, model turn,
game publication or production action occurs.

The local Windows suite ran once at implementation commit `39a02ab56f2deaea22f66e0f3a8581bca0fc6d74`,
with the configured Python **3.13.16**, Git **2.54.0.windows.1** and LFS **3.7.1**.
`PYTHONUTF8=1` precedes Python startup; inherited FarmBot/fake-worker/Git/token
selectors are withheld. The recorded runner uses `-B`, unittest discovery of
`tests` and verbosity 2, retains UTF-8 output, all test timings, revision and
every skip id/reason privately, and makes no production access. A fresh temporary
directory symlink check passes on this development PC before the suite.

| Run | Tests | Duration including discovery | Failures/errors | Platform skips |
|---|---:|---:|---:|---:|
| Native development Windows | 1,778 | 911.161 s | 0/0 | 69 |
| Hosted macOS CI | 1,778 | 515.262 s | 0/0 | 42 |
| Hosted Windows CI | 1,778 | 2008.452 s | 0/0 | 69 |

| Run | Python | Git | Git LFS |
|---|---|---|---|
| Native development Windows | 3.13.16 | 2.54.0.windows.1 | 3.7.1 |
| Hosted macOS CI | 3.13.15 | 2.55.0 | 3.8.0 |
| Hosted Windows CI | 3.13.15 | 2.55.0.windows.5 | 3.7.1 |

Both Windows runs actually execute **all 13 feature journeys, all seven native
Job Object tests and all 25 callback/origin checks**, without a skip in those
groups. The new callback introduces **no Windows skip**. macOS skips the sixteen
new native LFS tests with the explicit Windows platform reason and runs the two
portable origin tests; its other 26 native Windows skips are existing ones.
The focused native callback/skill-reference run passes **100 tests in 5.476 s**,
zero failures/errors/skips. Local links, whitespace and known credentials,
private paths and the private LFS endpoint are checked without exposing values.

The [full CI run](https://github.com/Kuaiwa-Network/farm-linear-agent/actions/runs/37447798856)
tests PR merge ref `c6de9d043e32593a32c79f056e852ac9cde5cd9f`. Its tree equals
the final local implementation/PR head
`39a02ab56f2deaea22f66e0f3a8581bca0fc6d74`. [PR #147](https://github.com/Kuaiwa-Network/farm-linear-agent/pull/147)
merges as `efd3f03472311cb48d9a1ab3492384065d128dce`, with the candidate and
actual merge trees equal. CI artifacts retain both hosted tool inventories, full UTF-8 logs, skip
ids/reasons and timings. Local private receipts retain the independent host run
and native transfer checks. Hosted results do not certify production readiness.

Every native Windows platform-skip reason is recorded below; the private
summary additionally preserves all **69 exact test ids**. These existing skips
include platform/fixture reasons mentioning privilege even though this run's
independent symlink capability check passes; no skip condition was changed.

| Existing skip reason | Count |
|---|---:|
| FIFOs and O_NOFOLLOW symlink refusal are POSIX | 1 |
| FIFOs are POSIX | 1 |
| FIFOs in a directory are POSIX | 12 |
| POSIX permissions; Windows has the junction test | 1 |
| POSIX process group semantics | 1 |
| POSIX process inspection | 3 |
| POSIX self-exit evidence; Windows workers are proved by Job Objects | 4 |
| POSIX service termination contract | 2 |
| POSIX sessions and process groups | 8 |
| POSIX sessions; Windows workers are proved by Job Objects in test_windows_workers.py | 25 |
| Windows has no O_NOFOLLOW; making a symlink there needs a privilege | 1 |
| Windows refuses every lark_cli home | 1 |
| Windows worker jobs contain children; tested in test_windows_workers | 1 |
| macOS's Seatbelt, as Codex's | 1 |
| making a symlink on Windows needs a privilege | 1 |
| no FIFOs in a directory, and making a symlink needs a privilege | 1 |
| the filter here is a shell script | 1 |
| the hooks here are shell scripts | 1 |
| the hooks, fsmonitor and filters here are shell scripts | 1 |
| the macOS store's master key file | 1 |
| the planted hooks and fsmonitor are shell scripts | 1 |

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

## Supported client metadata and headless network export

The feasibility fixture above is superseded by delivered Farm-Client tooling:
[metadata PR #1421](https://github.com/Kuaiwa-Network/Farm-Client/pull/1421)
merges as `79e6c1c68e2480d2f5877d1840b0373f22f0a952`; its tested final head is
`1547fba46fbaf00c1d9cf75088e602f52553e367`.
[Headless network PR #1422](https://github.com/Kuaiwa-Network/Farm-Client/pull/1422)
merges as `e5ade51ee564563a1260136b954c7be4fbc208b6`; its final head is
`00151d3aab67d2987894004bba565a599c78d23c`. Each merge tree equals its tested
candidate. Development tooling merges do not change the running FarmBot.

The shared generated-directory transaction creates missing metadata inside
prepared replacements before installing assets, preserves existing metadata
byte for byte, and rolls assets and metadata back together. Complete validated
MonoImporter/TextScriptImporter sibling templates supply fresh unique GUIDs;
invalid templates, duplicate GUIDs and reparse paths are refused. Existing
Editor callers retain their defaults. The delivered .NET 8 network command
links the actual exporter, validates an explicit clean contract commit before
generation, and throws on unexpected Editor preferences/dialogs. Its protobuf
DLL is checked against the committed LFS hash. No Unity Editor starts.

Native development Windows full unit validation records these results. The
ordinary checkout deliberately leaves unrelated game assets as LFS pointers;
all existing skip IDs/reasons and UTF-8 logs remain in private receipts.

| Client candidate | Discovered | Passed | Existing skips | Failures/errors | Test command duration |
|---|---:|---:|---:|---:|---:|
| #1421 final metadata implementation | 6,503 | 6,427 | 76 | 0/0 | 19.935 s |
| #1422 network implementation | 6,513 | 6,437 | 76 | 0/0 | 64.101 s |

Four pre-existing Windows exporter failures were reproduced against the clean
baseline: shell-fixture argument/output behavior and native path expectations.
The metadata PR replaces the Windows fixture with a directly compiled native
executable and canonicalizes returned/expected paths. It adds no skip or weaker
containment check. An earlier harness output location and CRLF-sensitive source
assertions were corrected in the private fixture/ephemeral CI setup; these were
test setup findings, not additional application regressions. macOS's system
directory aliases are avoided by using physical test-work directories rather
than weakening reparse checks.

At **11:14:13 UTC**, the actual delivered CLI is tested in a fresh Codex
**0.156.1** unelevated native command context with the FarmBot #148 gate,
**network off**, explicit write roots and owned Windows Jobs. CLI/source
candidate `7c1fc6fd7cf97218dece30c9afbea148c5b87416` has the same executable
source as final #1422; the later commit changes CI only. The clean read-only
contract input remains `29e6cfe1430a7c7313febc642f95db4ea6c3fb18`.

| Delivered command check | Measured result |
|---|---|
| First / second real export | exit 0/0; 3.370 / 2.836 s |
| Inputs and output | 60 proto inputs; 589 messages; 61 C# and 61 full metadata files |
| Complete output maps | all 122 asset/metadata files identical across runs |
| Metadata identity | 56 existing files byte-identical; five new files; GUIDs unique |
| Generated C# and registry compilation | exit 0 |
| Wrong explicit contract pin | exit 1; all outputs unchanged |
| Worker commands / complete probe | 12.466 / 13.001 s |

All copied tool sources and contract inputs remain unchanged. Native child
ownership, effective policy, empty build/worker Jobs, finished RPC reader,
clone trust and registered runtime evidence pass. The fixture starts neither
Unity nor a model turn and configures no credentials or service. It commits no
generated game assets. It tests the delivered network command, separately from
the tooling PR publication.

[Final #1422 CI](https://github.com/Kuaiwa-Network/Farm-Client/actions/runs/37455684974)
passes the Linux full suite (**6,441 passed, one existing skip**, 22.2365 s),
native Windows focused suite (**235 passed, one existing skip**, 19.1054 s),
and macOS focused suite (**238 passed, one existing skip**, 10.6980 s). Both
platform jobs build the actual delivered CLI and run its help command; review
and triage checks pass. Host discovery varies with fixture dependencies, so
these counts are not presented as identical to the private native full run.

The network/metadata delivery above completes the first part of Phase C step 1.
The following section records the subsequent config and full typecheck delivery;
controller stages and later live acceptance remain pending. These development-PC
results do not certify the production Windows host.

This record-only follow-up passes 75 skill/reference checks in 0.456 seconds,
zero failures/errors/skips, plus added local links/anchors, whitespace and
known-secret/private-path checks. Only two Markdown files change; executable
code, tests and CI remain identical, so another full offline run is unnecessary.

## Supported headless config and native full client typecheck

[Config PR #1423](https://github.com/Kuaiwa-Network/Farm-Client/pull/1423)
merges as `99d00cf8037923a0a96ecd7bed522228b39332c1`; final candidate
`1009fd8a4c5f2e20e2c989c460099d100e4e91b6` differs from its tested executable
head `1ff969fb48207edc14715123e933e153751fb40d` only in guidance.
[Native typecheck PR #1424](https://github.com/Kuaiwa-Network/Farm-Client/pull/1424)
merges as `5ca9db4f4ef4ae7a21c50c7243429a6f537a29a8`; final candidate is
`58ad936d9fed1d19eed86693e7f9eee7cca8d947`. Its executable code matches tested
`e37fa3359bf4707c63b85d980c90319aa360d8b2`; the last commit clarifies only
platform guidance. Both merge trees equal their
verified candidates; completed development branches are removed with head
checks. Neither delivery changes the running FarmBot or generated game assets.

The config command links the actual exporter, validator and transactional
installer. It requires explicit absolute client/common roots, a clean full
common commit and the verified artifact's full source digest. Provenance is
checked before preparing replacements. It preserves old metadata, creates
complete TextScriptImporter/MonoImporter metadata inside the two staged
directories and swaps or rolls them back together. Preferences/dialogs are
refused. Windows uses the existing native common command, never Bash/MSYS/WSL.

The full native client unit run discovers **6,528 tests: 6,452 passed, 76 existing
skips, zero failures/errors**, in **19.132 s**. All skip IDs/reasons, UTF-8 logs
and the meaningful initial missing-entry-point failure remain private. There
is no added skip. Focused config validation passes 242 of 248 discovered tests
with six existing skips in 11.810 s. The full suite's code matches the final
config candidate; its last guidance commit needs no repeated full run.

At **11:52:14 UTC**, the delivered config command passes in a fresh native
Codex **0.156.1** unelevated, network-off, no-model command context with the
FarmBot #149 worker gate. The common input is the clean read-only
`b367febe20d6db65ebb386aa871bdb2671df9525`; the generated artifact's verified
source digest is
`d189a438d6c5a51a89079e485442af490f8e4cd9775fcdda92fbe1a4c8d95764`.
The tools are .NET SDK **8.0.423**, protoc **35.1** and the prepared native
Go **1.25.1** (binary SHA-256
`a6450f128e096d51d24f786e0e72eca13d6b9c48f9a2bd1cc0de4913e53f566a`).
Prepared modules/tool bytes are read-only; compilation caches are fresh and
owned, with module network access disabled.

| Delivered config check | Measured native Windows result |
|---|---|
| First / second real export | exit 0/0; 15.676 / 8.949 s |
| Outputs | 101 tables/data files, 103 C# files, 204 complete metadata files |
| Complete maps / identity | all 408 files identical; old metadata byte-identical; unique GUIDs |
| Wrong common commit / source digest | exit 1/1; 7.031 / 6.923 s; all outputs unchanged |
| Real generated types/readers/registry | compilation exit 0; all 101 tables load |
| Commands / complete probe | 43.738 / 44.402 s |

The initial private harness used an unsupported dotnet build flag and omitted
two actual existing reader dependencies. Those harness faults and outputs are
retained; the corrected fixture uses locked restore and the actual source
files. They required no application relaxation. Final clone trust, source and
tool immutability, native child ownership, empty Jobs, finished RPC reader and
runtime registration checks pass. Unity, a model turn, credential setup and
production configuration/ledger reads are absent.

[Final #1423 CI](https://github.com/Kuaiwa-Network/Farm-Client/actions/runs/37459099449)
passes Linux full tests (**6,456 passed, one existing skip**, 21.9883 s),
macOS focused tests (**253 passed, one existing skip**, 7.3657 s), and
Windows focused tests (**250 passed, one existing skip**, 15.9279 s).
Both delivered export commands build and enter on the platform jobs. Host
fixture discovery differs from the private full suite; these counts are
reported separately.

The native Python typecheck requires explicit client/reference/new output
roots and a native dotnet executable. It borrows matching Unity **2022.3.62f3**,
C# **9** / .NET Standard **2.1** references read-only and rebuilds source lists
from the selected current client. Production compiles first; both test
assemblies use this run's fresh production DLL. Fresh SDK projects use literal
compiler properties and validated managed references/analyzers; unsupported
imports, conditions, unhydrated DLLs, reparse paths and changed inputs fail.
Directory.Build.props/targets imports are disabled on the command line before
SDK evaluation. Feed-free restore, private outputs and disabled build servers
keep the borrowed checkout unchanged. A partial DLL from a failed build cannot
make a later test build pass.

At **12:16:38 UTC**, the actual final typecheck passes in the same native
network-off/no-model worker configuration. Its tool SHA-256 is
`8ca8e08fc7535c5eb3142da8f42185d54681849f1820f44ed5c2f6b6b205830d`.

| Assembly | Current source files | Managed references / analyzers | Exit / errors / warnings | Build duration |
|---|---:|---:|---:|---:|
| HotUpdate | 1,429 | 253 / 2 | 0 / 0 / 113 | 4.989 s |
| HotUpdate.Tests | 365 | 227 / 2 | 0 / 0 / 0 | 1.659 s |
| HotUpdate.PlayTests | 180 | 224 / 2 | 0 / 0 / 7 | 1.701 s |

The build phase takes **9.562 s**, command **10.397 s** and complete probe
**12.557 s**. Source/reference/tool hashes and borrowed Unity project files
remain unchanged; effective worker policy, owned native child, empty Job,
finished reader and runtime registration pass. No Unity Editor starts. These
results compile the test assemblies; they do not execute EditMode/PlayMode
tests or certify a Unity resource slot.

Nine meaningful native Python boundary regressions pass locally in **0.337 s**
with no skips. [Final #1424 CI](https://github.com/Kuaiwa-Network/Farm-Client/actions/runs/37463839934)
passes Linux full tests (**6,456 passed, one existing skip**), macOS focused
tests (**253 passed, one existing skip**) and Windows focused tests (**250
passed, one existing skip**). Its nine Python tests pass on macOS in **0.063 s**
and Windows in **0.336 s**, without skips; both export commands build and enter.
Linux also passes four WeChat and seven WebGL platform tests. All local and CI
logs, revision/tool identities and initial probe evidence remain private.

This completes Phase C step 1 on this development PC: supported complete
metadata, native network/config entry points and full native typecheck.
Controller client stages, UI-ready gating, committed-HEAD Unity verification,
closing/write-back and real client publication remain implementation and
acceptance work. Results on this PC do not certify the production Windows host.

## Remaining client/UI and release prerequisites

1. D10's native real LFS push/download and headless proto feasibility checks
   now pass, and the supported optional endpoint-scoped LFS callback passes
   native transfer and GitHub-read acceptance. Select a verified private build
   for an authorized real client worker and complete its publication acceptance;
   preserve GitHub destination checks, credentials and owned Git write roots.
2. Obtain an approved real card/scope for remaining live common/config/hive
   stages and their native worker generators. Running-worker withdrawal is
   still unmeasured; the completed cancellation check used a parked job.
3. Implement Phase C's controller client stages, UI-ready gate, reservation-based
   Unity validation and closing/write-back workflow. Step 1's supported network,
   config, complete metadata and native full typecheck tools are delivered in
   client #1421–#1424; their measured development-PC results are recorded above.
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

The existing-login documentation follow-up passes **75** skill/reference tests
in **0.399 seconds**, zero failures, errors or platform skips, plus local
link/anchor, whitespace and known-credential/private-path checks. Four Markdown
files change; no executable behavior is changed by this record.
