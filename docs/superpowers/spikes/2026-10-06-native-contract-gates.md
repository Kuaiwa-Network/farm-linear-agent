# Native Windows contract audit (2026-10-06)

This is an authorized, unchanged-source audit of FARM-1346 through the real
Windows TestBot service and Codex worker. It runs on the **production Windows
host**, using a separate development checkout and fresh isolated TestBot state.
It does not inspect or change the production installation, config, ledger or
service, and does not certify production readiness.

The FarmBot service is pinned to `0c4ef5ee240141b26382964e2b433ef8b8f202e0`.
The audited Farm-Contract source is
`71dadaed8d111219e7170ab6712d97bbb28d8925`. The worker's local `origin/main`
has the same head, but its read-only refresh fails with Schannel
`SEC_E_NO_CREDENTIALS`. A separate read-only GitHub API check confirms the remote
main is still that exact commit. This does not turn the failed worker fetch into
a successful fetch.

The selected Python is **3.13.16**, with `PYTHONUTF8=1` set before launch and
explicit `-I -X utf8 -B` arguments. Git is **2.54.0.windows.1** and Git LFS
**3.7.1**. Native buf **1.72.0** and OpenSpec package **1.7.0** match the contract
CI pins; OpenSpec uses the selected native Node runtime. The configured Codex
CLI is **0.156.1**, using the explicitly selected `unelevated` sandbox. Actual
Job queries observe the recorded gate inside the worker's owned Job. Execution
logs record **84 native `pwsh.exe` executions**; no Bash/MSYS/WSL is used. The
driver puts TMP/TEMP and buf cache inside its already authorized private audit
directory. No tool install, credential/profile change, elevation, ACL repair or
write-root expansion occurs.

## Twelve gate results

All twelve commands execute once, in README order. **Nine return zero; gates 3,
9 and 12 fail.** No platform skip is added or used. The table records process
durations, not the full model-worker or service duration; their sum is **1.541
seconds**. Python rows use the selected executable explicitly, followed by
`-I -X utf8 -B tools/<entrypoint>` and the arguments shown.

| Gate | Native command / Python entrypoint | Exit | Seconds | Actual coverage |
| --- | --- | --- | --- | --- |
| 1 | `buf build` | 0 | 0.091 | Current proto compiles. |
| 2 | `buf lint` | 0 | 0.115 | Current proto style passes. |
| 3 | `check-breaking-waiver.py BREAKING_WAIVERS origin/main` | 1 | 0.055 | Blocked: the read-only snapshot has object alternates; the same-head owned-worktree retry fails creating its temporary directory. |
| 4 | `gen-manifest.py --check` | 0 | 0.072 | Current proto matches the committed manifest. |
| 5 | `check-markers.py` | 0 | 0.816 | Unfilled source templates are absent; this does not approve existing UNREVIEWED markers. |
| 6 | `check-msg-naming.py` | 0 | 0.052 | 237 Ack messages have paired Req messages. |
| 7 | `check-proto-fields.py` | 0 | 0.095 | Current pinned fields, messages and code positions pass. |
| 8 | `check-coverage.py` | 0 | 0.053 | Committed snapshot covers 452/452 entries, including 59 draft entries; no optional farm-common drift check runs. |
| 9 | `check-spec-provenance.py` | 2 | 0.040 | Blocked during startup self-tests; repository provenance is not validated. |
| 10 | `check-readme-inventory.py` | 0 | 0.037 | The inventory matches the current proto directory. |
| 11 | `check-openspec-config.py` | 0 | 0.033 | Current OpenSpec rules keys pass. |
| 12 | `check-openspec-validate.py` | 1 | 0.082 | Blocked while preparing the isolated runtime; upstream validation of 50 in-flight changes does not run. |

Gate 3's first failure is the intentional `Git controller object alternates are
refused` check. Its read-only Git snapshot is not an accepted input for that
gate; this guard must remain. The worker retries against its actual owned,
ordinary writable worktree at the same source head. That retry also exits 1:
`WinError 5` while creating a `breaking-waiver-*` temporary directory. Its
reported duration is **0.171 seconds**. No real delta is introduced, so even a
successful same-head breaking check would not verify a new protocol change.

Gates 9 and 12 similarly fail with `WinError 5` for `provenance-selftest-*` and
`openspec-validate-runtime-*`. The actual source wrappers all use Python's
standard `tempfile.TemporaryDirectory`. Earlier contained native measurements
on this host found that Python 3.13 `0700` directory creation replaces inherited
Windows permissions: ordinary file operations work, but access to those newly
created directories fails. Default inherited-permission directory creation
passes in the same granted parent, while protected sibling/controller writes
remain denied. Those results reproduced with configured and packaged Codex
versions. The current logs are consistent with that measured failure. They do
not show a wrong gate argument, a missing pinned tool or a failed contract
assertion.

The next fix belongs in Farm-Contract tooling: use owned native temporary
workspaces that inherit the granted Windows parent permissions, preserve POSIX
`0700`, refuse links/reparse points and verify identity during cleanup. The
backend's already tested `tools/native_temp.py` provides a reference. The three
wrappers need focused regression coverage and actual restricted-worker reruns;
all twelve need a new run after the fix. Preserve object-alternates refusal,
signature/ownership/containment checks and failed evidence. Do not substitute
Bash, elevate, repair ACLs, patch Python's standard library or add skips to pass.
No Farm-Contract source is modified by this documentation record.

## Settlement and remaining acceptance

The worker posts one audit result and parks in `awaiting_input`, stage
`contract-audit`, with no target, no worker PID and no published PR. Independent
Git status checks find Farm-Contract, common and farm-hive clean. Farm-Contract
HEAD matches the audit baseline. Removing only TestBot's delegate preserves
FARM-1346's human owner, Todo status, labels, comments and earlier drafts. Linear
shows the cancellation response and a complete, unarchived session. Read-only
verification finds the native Job empty, owned writable/read worktrees removed,
no cleanup error, and the retained Farm-Contract recovery commit and tree equal
to the unchanged baseline. Other repositories' recovery heads also match the
pre-cleanup measurements.

A local scan of **445 private attempt files** and the fetched issue/comments
finds **zero known Feishu app ID/secret or TestBot Linear credential matches**.
Only the selected bot secret is resolved in memory for that local comparison;
no value is printed or persisted. The current-account `farmbot` profile remains
strict bot mode with zero personal user logins and no HOME override.

After exact process-identity and quiescence checks, only the development
controller is stopped using native `Stop-Process`, its feature opt-in is removed,
and the settled controller restarts on the same merged FarmBot revision with
`enabled_skills: ["chat", "fix"]`. Its heartbeat shows serving, zero workers and
zero errors in all six loops. Doctor retains only the two historical checkout
`job_failed` findings and no pending cleanup. The audit logs, profile backup,
recovery refs and tunnel are preserved. No production restart occurs.

Task 17 remains partial. Finish the tooling fix and actual restricted-worker
gate rerun first. Then use human-attributed scope/design answers and a genuine
delta for changed-contract gates, a real draft PR and the exact `waiting` /
`stage_limit` pause. G2/G3/G4/H1 remain unanswered; this audit does not approve
them. The production installation's release verification and promotion require
their separate prerequisites and operational authorization. FARM-1425 and its
existing test drafts remain outside this audit. Phase C's client implementation
and the later UI implementer are not started.

## Preserved UTF-8 evidence

Raw logs remain private because they contain local host paths. SHA-256 values
identify the measured evidence without publishing those paths or host logs.
Successful buf commands intentionally have empty output files; their identical
empty-file hash accompanies distinct command/exit/duration records in the
summary.

- Driver: `7f0f66f0c30fb6796ebf3998e458387fe2ae2e9df026a707442ff4aba8dda867`.
- Summary: `efbf0271b7c0cf6c04b9716eec5edda6117e1ae588314a7da613721dcb43a1a7`.
- Focused gate-3 retry log: `3918f5e492ccd1d641d50e8150bb5f5e00aeba42caf321c934565df00f4ccd04`.

| Private log basename | SHA-256 |
| --- | --- |
| gate-01.log | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| gate-02.log | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| gate-03.log | `87fd4d8f8b48e37e723fdc4c6b51317529a68d853a78a27d5b1972f7d9cfd134` |
| gate-04.log | `46af877702a674d45bf655922c3a59ed135e8b8cba739a6880652fe05cd68811` |
| gate-05.log | `0cc69a025a5eb4a852488ad605e30f4a15caf8c7ac7a20bfbc02a117b85222c7` |
| gate-06.log | `59bde64288b5da9c1e14de311b67e7c6bdc56b0171d69e28b1f0f783baafa19a` |
| gate-07.log | `77757b0cd9d7b1c5a0bd58c3e20857c58cca55c344846b953f0dfa663666dffb` |
| gate-08.log | `b2a5e9a1305b60418eac9df555b4ff4d05613f4ac91f87898db5d4be64bcb15c` |
| gate-09.log | `dc540f52d9ac8bf02445cc65a8d87f118d77cf77f80149e866637e8793839eba` |
| gate-10.log | `9eec67a170cb6d05740676eb8bb20774d862ce5b01ce33686e4bbd2d7be5c8b1` |
| gate-11.log | `cd7f969b794f47cb469c57dd04388c3b14206899d694a887f3b44d21d0d1c111` |
| gate-12.log | `6e852a60c9410ae8ca2d6f9be4612c7ab0ede3e3392626dc1605900ab94906dd` |

Documentation validation uses the selected Python 3.13.16: `test_skills`
passes **73 tests**, zero failures/errors/skips, in **0.450 seconds**.
The private UTF-8 test log SHA-256 is `2f6ccba8b87eae41290adcdc42ba819b70734c89946e5ec992ea91b38596f3e1`.
Added relative links/anchors, whitespace and public-diff privacy checks
pass. This record changes Markdown only; no full offline rerun is needed.

## Candidate temporary-workspace fix and remaining native gap (2026-10-06)

The subsequent tooling-only [Farm-Contract PR #327](https://github.com/Kuaiwa-Network/Farm-Contract/pull/327)
is **draft and unmerged**. Its measured head is
`a8f332fe2611e745a38ebb2f9021a10e90a91135`, tree
`f29e0416764eff09def696090830a27fce1840ec`, against unchanged main
`71dadaed8d111219e7170ab6712d97bbb28d8925`. No proto, OpenSpec or manifest
content changes. This follow-up runs in separate disposable development
checkouts on the same **production Windows host**, without accessing its
production installation, config, ledger or service. It is offline tooling
verification; it does not resume the withdrawn TestBot issue.

The candidate uses a Contract-owned temporary-workspace helper: Windows
creation inherits the already granted parent DACL; POSIX retains `0700`.
Exclusive creation, ordinary ancestor/reparse refusal, pinned identity checks
and conservative cleanup preserve ownership. Failed bodies and uncertain
cleanup retain evidence. The three wrappers load the helper from an explicit
sibling path under isolated Python. Existing gate criteria, startup self-tests,
object-alternates refusal and process containment remain.

Python **3.13.16**, Git **2.54.0.windows.1**, LFS **3.7.1**, buf **1.72.0** and
OpenSpec **1.7.0** remain the selected native tools. `PYTHONUTF8=1` precedes
explicit Python `-I -X utf8 -B`; gate shells are native `pwsh.exe`.
Native focused commands are `python -I -X utf8 -B tools/test-native-temp.py`,
`tools/test-check-spec-provenance.py`, `tools/test-check-openspec-validate.py`
and `tools/test-check-breaking-waiver.py` (each latter script also follows the
explicit selected Python and the same flags). They pass **19, 39, 49 and 55
tests** respectively outside the sandbox, **162 total**, with zero runtime
skips. Existing Windows exclusions for POSIX-only fixtures remain; these
results do not turn excluded cases into Windows coverage. All **28** Windows,
Linux and macOS [CI checks](https://github.com/Kuaiwa-Network/Farm-Contract/actions/runs/37413616291)
complete successfully at the recorded head.

### Actual contained results

Fresh ordinary Git checkouts use the exact candidate source and immutable main
baseline. All twelve gates pass outside the sandbox. Both actual offline
`workspace-write`, **unelevated** workers execute all twelve, but only **11/12
pass**: configured Codex **0.156.1** and packaged **0.160.0** both fail gate 3.
Gates 9 and 12 now reach and pass their real assertions/upstream validation.
The following durations include each native shell/command; they are not model,
service or full worker durations.

| Gate | Ordinary exit / seconds | Configured 0.156.1 exit / seconds | Packaged 0.160.0 exit / seconds |
| --- | --- | --- | --- |
| 1 | 0 / 0.637 | 0 / 0.478 | 0 / 0.489 |
| 2 | 0 / 0.641 | 0 / 0.512 | 0 / 0.520 |
| 3 | 0 / 4.884 | 1 / 4.345 | 1 / 3.239 |
| 4 | 0 / 0.606 | 0 / 0.548 | 0 / 0.497 |
| 5 | 0 / 0.686 | 0 / 0.604 | 0 / 0.573 |
| 6 | 0 / 0.580 | 0 / 0.466 | 0 / 0.481 |
| 7 | 0 / 0.622 | 0 / 0.555 | 0 / 0.514 |
| 8 | 0 / 0.584 | 0 / 0.482 | 0 / 0.475 |
| 9 | 0 / 0.619 | 0 / 0.497 | 0 / 0.501 |
| 10 | 0 / 0.579 | 0 / 0.453 | 0 / 0.461 |
| 11 | 0 / 0.569 | 0 / 0.448 | 0 / 0.456 |
| 12 | 0 / 1.189 | 0 / 1.083 | 0 / 1.080 |

Gate 3 now creates and accesses its owned temporary directory, then buf's
`.git#branch=<verified-commit>` input invokes Git's local transport. Git's
internal `sh.exe` fails to create its signal pipe with `WinError 5`; buf's
unexpected exit is correctly refused as a transport failure, not interpreted
as no breaking change. Short fixture paths and buf caches inside already
granted TEMP do not resolve it. The initial cache-location failure and earlier
fixture Git-path/separator setup failures remain in private logs; corrected
runs supersede those setup errors without erasing them. No Bash script, WSL
route, elevation, ACL repair, write-root expansion, stdlib monkeypatch or added
skip is used to obtain a pass. Git's internal shell dependency still blocks the
current gate-3 transport in the native sandbox.

| Regression command | Configured exit / seconds | Packaged exit / seconds | Measured result |
| --- | --- | --- | --- |
| `test-native-temp.py` | 0 / 0.701 | 0 / 0.717 | 19 pass in both. |
| `test-check-spec-provenance.py` | 0 / 8.615 | 0 / 8.253 | 39 pass in both. |
| `test-check-openspec-validate.py` | 0 / 17.422 | 1 / 17.293 | 49 pass configured; packaged has one fixture-directory rename WinError 5. |
| `test-check-breaking-waiver.py` | 1 / 46.929 | 1 / 45.776 | 55 run in each; ten failures each in real Git/buf transport cases. |

The packaged OpenSpec fixture rename failure is retained as unresolved
comparison evidence; passing gate 12 does not erase it or certify that CLI
version's whole regression suite. The configured runtime passes the helper,
provenance and OpenSpec regressions. Both breaking suites remain failed.

Both workers keep exactly three predeclared write roots and disable networking;
no authentication or model turn starts. Job queries observe all **16/16** gate
and regression shells, plus the child and descendant, in the owned native Job.
After settlement the Job is empty and the observed process identities are
dead. Protected sibling write handles are denied and sentinel/source bytes
remain unchanged. Job settlement reports no cleanup error. Failed breaking
workspaces, five intentional provenance-selftest failure workspaces per
attempt, caches and the packaged rename uncertainty remain preserved; no
uncertain tree is recursively cleaned.

### Evidence and next implementation step

Commands, exits, durations and separate UTF-8 stdout/stderr SHA-256 records
remain private. These hashes identify the final comparisons without publishing
raw logs, account identities or local paths:

| Evidence | SHA-256 |
| --- | --- |
| Completion | `e1f1fb104890919d128f45485143645cdf4357b3e60b8315a03831fbc83973fb` |
| Evidence index | `2e4748cae639838fbbdb60e95afb122c9a8174170361513632cb308d73ec1a31` |
| Ordinary native summary | `b63d6f58221cec18d24db94f140fe00b0fdc7f81f9d8faf058ab57ff0d2e9f07` |
| Configured short-path summary | `bd7317865daf03f548f30ef52bf690b10632c84f5f22c99b23fca45c2a3706b5` |
| Packaged comparison summary | `d6e60a1ab68b224399258491e3ad80e35307143a215c1ff54191ca6b56dadea8` |
| Configured gate-3 stderr | `408217edc5f10dd5f22a106b83d78d1f54f067e6625f6a1795fabf7ac28cc6d4` |
| Packaged gate-3 stderr | `ce20b5df06d932d5777ebe15600d68c983867ae3098b279a363ff58a92052f01` |
| Configured breaking-regression stderr | `8b548bcbc9a260340ca6d11be7349cfe4c8371ba406ff367e08503c283fd26d7` |
| Packaged OpenSpec-regression stderr | `0c25f52c7807d3d589828064af56277add304e4a642a088c92aab94a5a1ce413` |

The proposed native fix is to materialize the already validated immutable Git
baseline blobs in an owned temporary workspace, then give buf a directory or
verified image input. The [official buf CLI](https://buf.build/docs/reference/cli/buf/breaking/)
accepts those `--against` formats. This proposal has not been implemented or
accepted. It must preserve exact baseline/configuration semantics, Git input
guards and waiver criteria, and demonstrate real compatible/incompatible
fixtures in the accepted configured native worker. The packaged fixture rename
failure also needs focused investigation and an explicit disposition.

Do not merge #327 on CI success alone. Complete the native gate-3 fix, affected
regressions and all twelve contained gates first. Genuine changed-contract
end-to-end acceptance, human-attributed G2/G3/G4/H1 answers, a real draft PR and
the exact `waiting` / `stage_limit` pause remain pending. The settled TestBot
remains chat/fix only. FARM-1425 and game test drafts stay parked. Production
release verification/promotion needs its separate prerequisites and operational
authorization; Phase C and the UI implementer remain unstarted.

Candidate record validation: selected Python 3.13.16 `test_skills` passes
**73 tests**, zero failures/errors/skips, in **0.496 seconds**.
The private UTF-8 test log SHA-256 is `6c77dbcd2fe956856c01c2727c63db162ec5da4badde8dce3e97eb0c6018bf0e`.
Added relative links/anchors, public-diff path/identity/credential-pattern checks
and whitespace pass. This follow-up changes only Markdown; it does not rerun
the full FarmBot suite or convert Contract CI into restricted-worker acceptance.
