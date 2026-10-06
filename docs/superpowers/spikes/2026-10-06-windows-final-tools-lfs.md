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

## Next approval boundary and client/UI prerequisites

The real-server upload is **not executed**. A concrete script and the exact
1 KiB payload are prepared locally; the operator has been asked to approve
uploading that one object to farmgui's existing configured internal server,
then downloading and hash-checking it in the native worker context. The server
may retain the object. No Git branch, source commit, PR, production setting or
credential change is part of that proposal. The local fixture does not certify
real-server new-object upload permissions.

Before Phase C, the design's D10 checks still require that actual LFS upload
and native headless Farm-Client proto export without Unity. Later client/UI
implementation and farmgui authoring/export permission changes are subsequent
work. Scoped common/config/hive live work still needs its own real card/scope;
FARM-1346/FARM-1425 and their unmerged test drafts remain untouched. Production
promotion/feature enablement retains its separate operational approval and
final-host acceptance. These development measurements do not certify production.

The documentation follow-up passes 75 skill/reference checks in 0.417 seconds,
with no failures, errors or skips, plus added local-link/anchor, whitespace
and known-credential/private-path checks. Only four Markdown files change.
