# Native Windows claim bootstrap and Git transport

Measured on the development Windows PC on 2026-10-06, using the configured
Codex 0.156.1 `unelevated` backend, `workspace-write`, network access enabled,
Python 3.13.16 and Git 2.54.0.windows.1. This is not production-host readiness.
No production configuration, ledger, service or credentials were changed.

## Approved real Contract check

The operator approved [FARM-1435](https://linear.app/kuaiwagames/issue/FARM-1435)
for Task 17's stage-A-only check. Its changed scope resolves the existing gifting
Contract contradiction: 1201/1202/1203 are both blessing IDs and mail template
IDs, preserving frozen server text, historical snapshots and fail-closed config
validation. No wire fields, economics, client/server implementation, later-stage
handoff or game PR merge was authorized. The approved scope was recorded on the
issue before adding Code and delegating to development TestBot. Its human
assignee was preserved.

The first attempt ran on FarmBot `0c4ef5ee`, against Farm-Contract `29e6cfe1`.
It claimed successfully and wrote its token successfully. The skill's unqualified
"mode 0600" wording led it to run `icacls` and then `Set-Acl` on Windows; both
permission changes failed. A subsequent claim-authenticated `foreign-work`
command succeeded, so this is not evidence of a token-read or credential failure.
All eight declared write roots included the intended item state directory.
The stage-A limit was present in the launch context; a later message was received
but not consumed before the stop.

`foreign-work` returned `incomplete`: GitHub PR searches succeeded, while all
five repositories returned `remote_branches: git ls-remote failed`. No complete
foreign-work absence was established. The operator-authorized test was stopped
before a saved plan, genuine delta, contract gate run, notice or draft PR. Stage-A
delivery and the `waiting`/`stage_limit` pause remain unverified on this card.

The selected job is cancelled, its native Job Object is empty, and its worktrees
and read checkouts are removed. Cleanup finished without errors and retained
recovery evidence. TestBot delegation was cleared; the human assignee and Code
label remain. All five development jobs are inactive, all recorded native Jobs
are empty, all webhooks are done and heartbeat workers are zero. The verified
quiescent development controller was stopped; its tunnel was preserved.
`enabled_skills` was restored to `chat`/`fix`, with all other development config
fields unchanged. Production was untouched.

## Correct token storage

All three claiming skills now point to the shared
[storage procedure](../../../references/worker-cli.md#claim-token-storage):
exclusive creation of an ordinary UTF-8 token file in the owned state directory,
mode 0600 on POSIX, and the inherited DACL on Windows. Existing files/links and
storage errors stop the worker. No ACL repair, ownership change, elevation or
writable-root expansion is authorized. Windows mode bits do not establish
isolation from other users of the service account.

`python -B -m unittest discover -s tests -p test_skills.py -v` ran 75 tests, all
passed, zero skips, in 0.392 seconds. The two added checks cover the cross-platform
storage instructions and refusal of stale tokens/permission repair. An initial
module-form invocation produced two fixture import errors; repository-root
discovery, the documented invocation, resolved them without source changes.

A fresh no-model native command probe with two owned roots created and read a
dummy token using `os.open(..., O_CREAT | O_EXCL | O_WRONLY, 0o600)` and refused a
second creation without changing its bytes. It performed no ACL mutation. The
effective sandbox policy and native child Job membership were verified; cleanup
left the Job empty, the reader finished, and the registered runtime receipt
unchanged. This proves the storage primitive works in the tested runtime; a
fresh real worker still needs to follow the corrected instruction.

## Separate Git transport gap

Read-only `git ls-remote` on the approved Contract remote succeeds in the
sanitized ordinary controller environment. The same read inside the actual
configured restricted worker fails with exit 128 and Schannel
`SEC_E_NO_CREDENTIALS`.

A command-local OpenSSL diagnostic gets past that TLS failure, then fails to
authenticate: Git's shell credential helper attempts `sh.exe`, which fails to
create a signal pipe with Win32 error 5. Explicitly selecting the existing
`gh auth git-credential` helper gives the same result. Direct native
`gh auth git-credential get` succeeds inside the worker and returns credential
fields; no credential values were displayed or persisted. This separates the
existing credential lookup from Git's TLS/helper-shell path. It does not establish
successful restricted Git fetch, push or publication.

The final probe completed in 2.492 seconds, with effective policy and Job ownership
verified, no model turn, no ACL mutation or credential installation, an empty Job
after cleanup and an unchanged runtime receipt. Private UTF-8 logs and sanitized
summaries are retained locally. No Bash/MSYS generator fallback, certificate-check
disablement, production change or account setup was used.

## Remaining acceptance

1. Resolve and verify native Windows authenticated Git transport inside the
   selected worker, retaining certificate verification and credential privacy.
2. Retry the already-approved FARM-1435 scope through genuine Contract changes,
   all twelve native gates, a registered real draft PR and `merge-contract`.
3. Verify stage A `done`, a `stage_limit` event/pause and
   `await-input --reason waiting`, with no common/hive/client/UI handoff.
4. Perform the separately authorized parked-job delegation-removal check, verify
   its single session response, cancellation, cleanup and retained draft/recovery
   evidence. The early cancellation above does not substitute for this check.
5. Complete release-candidate verification and obtain separate production
   promotion/feature-enablement authorization. Phase C remains unstarted.
