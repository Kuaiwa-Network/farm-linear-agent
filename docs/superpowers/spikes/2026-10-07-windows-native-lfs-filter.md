# Native Windows LFS content-filter verification

Measured on the development Windows PC on 2026-10-07. This records the optional
native filter in [FarmBot #167](https://github.com/Kuaiwa-Network/farm-linear-agent/pull/167),
initial candidate `423a207369070838003ba7687e8006ff80a81026` and corrected candidate
`8abb53677b82bb04eea93e2f28ea7ee6a0cd3a70`, based on
`06f732d5f6a2d182704c49445bba0925a6c88322`. The running development TestBot keeps
its frozen runtime checkout at `70716fc53a9cf8d2111cb2233b254a2b4155cfec`.
This PC's results do not certify the production Windows host.

## Measured host gap and implementation

The sanitized development launch environment intentionally withholds global Git
configuration. This also removes LFS content-filter registration. The actual
FARM-1396 authoring worker hydrates its scoped original images successfully, but
Git then lists those correct bytes as unstaged modifications. A sampled file's
SHA-256 and size match its committed LFS pointer exactly, and an explicitly
selected native filter produces that same pointer. Authentication and content
filter registration are separate requirements; no damaged source image is found.

[Git's command launcher](https://github.com/git/git/blob/v2.54.0/run-command.c)
uses its helper shell for filter strings containing whitespace or shell
characters, including `~` in a short path. An ordinary executable-plus-arguments
filter command therefore cannot satisfy this trial's native Windows requirement.
The optional adapter instead has a zero-argument, verified absolute selector and
starts only the existing hash-pinned `git-lfs.exe filter-process` through native
process APIs. It forwards binary streams, preserves the child exit code and keeps
the child in the caller's Job. Private settings pin the selected executable and
hash; malformed settings, reparse paths and changed binaries refuse quietly.

The builder creates a new private tools directory outside checkout, state and
worker write roots. It neither installs credentials nor changes accounts,
application settings, global/clone Git configuration or service defaults. The
development wrapper validates source/helper/LFS/companion identities before each
start and appends only `filter.lfs.process` and `filter.lfs.required=true` to its
sanitized process-local Git configuration. Hooks/fsmonitor disabling, verified
TLS, native credential callbacks, ownership and publication checks remain.
The [selection reference](../../windows-native-git.md#optional-native-lfs-content-filter)
describes the optional tools component; it is not automatically selected by
`serve` or `doctor`, and read-only checkouts retain disabled-filter overrides.

## Offline verification

All 14 focused checks pass on native Windows in 3.587 seconds, with zero failures,
errors or skips: two portable selector checks and twelve native adapter checks.
Coverage includes fragmented arbitrary binary data, child failure propagation,
invalid arguments/settings/pins/reparse paths, spaces/Unicode, builder refusal,
suspended-start Job assignment/drain, and actual local Git clean/cached smudge.
The Git fixture preserves a genuine XML edit and the indexed tree, and its trace
contains no helper-shell invocation. Missing compiler/Git LFS is a host failure,
not a passing skip. The initial focused fixture used a Python constant unavailable
on Windows; it is corrected to the native CREATE_SUSPENDED value before the final
focused run, without weakening startup containment.

The exact frozen full candidate is attempted once locally using Python 3.13.16 with
`PYTHONUTF8=1` before startup, a fresh passing native symlink check and the pinned
offline workflow's selector/token sanitization. Complete UTF-8 logs, per-test
durations, revisions/versions and every skip ID/reason are retained privately.
The [hosted run](https://github.com/Kuaiwa-Network/farm-linear-agent/actions/runs/37575260023)
retains platform artifacts. The local attempt is incomplete, as recorded below.

Hosted macOS has completed 2,113 tests in 675.261 seconds with zero failures/errors
and 72 platform skips. Its synthetic commit
`e7f02b3e35e8c96656eee831ee838920b87069b4` has the exact candidate tree
`a914d89ee86c31a939bc46d0dc3ccfa7aea1862e`. All 60 established skip IDs/reasons are
unchanged; the twelve additional skips are the native adapter tests, each with
reason `native Windows Git LFS filter adapter`. Both portable additions run.
Hosted Windows completion remains pending at this draft's preparation time;
no skipped native check is claimed as Windows evidence.

The local full run reports a failure in
`test_actual_git_clean_and_stat_refresh_preserve_source_with_no_helper_shell`,
then stalls in `test_fragmented_binary_stream_is_preserved_without_text_conversion`.
There are 1,570 completed test-status lines and no completed result/timing summary.
The isolated Git regression subsequently passes in 2.297 seconds. This does not
explain or waive the full-run failure. After more than five minutes without log
progress, read-only inspection finds only the Python redirector/interpreter and
no live test descendants. Their executable, exact test command, creation time,
current owner and ancestry are checked before stopping only those interpreters.
The incomplete UTF-8 log and interruption identities are retained privately;
neither development TestBot nor production is stopped by this diagnostic action.
No full-suite pass or final native release claim is made from this interrupted
attempt. The bounded diagnostics resolve its cause below.

### UTF-8 binary correction

A contained diagnostic sequence reproduces the failures after the native
callback tests select a UTF-8 console. The .NET Framework redirected stdin writer
captures that encoding when the child starts and emits a three-byte UTF-8 BOM
before raw BaseStream forwarding. The echo fixture receives 179,210 bytes instead
of 179,207, starting with `efbbbf`; Git rejects the corrupted filter handshake.
This is an adapter regression, not missing host capability. The apparent stall
is unittest's quadratic difflib formatting of a large repetitive binary mismatch,
as shown by the diagnostic traceback; no native filter child remains hung.
The diagnostic outer Job is assigned before Python startup and drained on timeout.

The corrected adapter selects BOM-free UTF-8 only while creating redirected
stdin, then restores the caller's complete input encoding before raw forwarding.
It changes no child authority or containment. The new console-mode regression
checks exact binary equality and encoding restoration; it fails against the
preserved old source. Binary mismatch diagnostics now compare the same exact
bytes but report bounded sizes/hashes instead of building a huge textual diff.
No check or skip is weakened.

All 15 corrected focused checks pass in 3.610 seconds, zero failures/errors/skips.
The previously failing callback/filter sequence also passes all 38 checks in
10.586 seconds, with no timeout and an empty owned Job. Its old-source negative
control fails as expected in 0.708 seconds. The corrected candidate is frozen
for a fresh full native run; at this record's initial preparation its
[new hosted run](https://github.com/Kuaiwa-Network/farm-linear-agent/actions/runs/37578787871)
is also pending. The old hosted Windows run is superseded/cancelled, retaining
the completed initial macOS artifact; it is not Windows pass evidence. PR #167
remains draft until the corrected full native and hosted checks complete.

### Completed corrected verification

The frozen corrected candidate completes the pinned full offline suite on the
native development PC and both hosted platforms, with complete UTF-8 logs,
per-test timings, versions and every platform skip retained:

| Host | Tests | Duration | Failures / errors | Skips |
|---|---:|---:|---:|---:|
| Native Windows development PC | 2114 | 1680.382 s | 0 / 0 | 69 |
| Hosted macOS | 2114 | 741.446 s | 0 / 0 | 73 |
| Hosted Windows | 2114 | 2216.710 s | 0 / 0 | 69 |

Both hosted artifacts identify synthetic revision
`06b105d6a5dff7904651d1c0e3ffea93b54cc7be`, whose tree equals the candidate tree
`6ca48e35e8025b7e4f922d8f941e1b75dde116a3`. Both Windows runs execute all thirteen
native adapter checks, ten shared Windows Job checks, twelve native publisher
checks and three native guard-process checks. All 69 established Windows skip
IDs/reasons are unchanged. macOS retains its 60 established skips and adds only
the thirteen native adapter checks; both portable filter checks run everywhere.
The initial incomplete/failing run remains separate evidence.

[PR #167](https://github.com/Kuaiwa-Network/farm-linear-agent/pull/167) merges as
`7604de5dfb7f5889f0f0613196e3a1a26f308a92`; its tree exactly matches the tested
candidate. The verified topic branch is retired. The running development core
remains frozen at `70716fc53a9cf8d2111cb2233b254a2b4155cfec`; no production
deployment or game PR merge is performed.

## Actual development source recovery

The operator approves preserving the parked FARM-1396 work, repairing the
development process environment and resuming its existing session. Before stopping
the owned development controller, a fresh read-only fence verifies the selected
item is awaiting input, all its native Jobs are empty, and there are no workers,
pending webhooks, cleanup or reservations. The sole development listener, process
executable/command, creation time and current owner match the private receipt.
Only that development controller is stopped; its tunnel and production remain.

The first recovery attempt refuses a guessed two-package allowlist before any
index mutation. Read-only inventory establishes the actual hydrated closure:

| Package | Original files |
|---|---:|
| Activities | 180 |
| Common | 378 |
| CommonFx | 153 |
| ItemIcons | 170 |
| PlantIcons | 1 |
| PlantImages | 2 |
| PlayerCustomize | 132 |
| UILangTex | 15 |

All 1,031 files are ordinary tracked blobs with canonical committed LFS pointers;
every materialized SHA-256 and byte count matches, totaling 188,942,783 bytes.
There are no untracked files, unrelated directories or uncommitted text changes.
Only the two approved Activities XML files and their issue document are committed,
at `070c0f82bce1798bf817256e1ec5db6b528531f6`. Package dependency hydration adds no
application change or new worker authority.

A pathless `update-index --really-refresh` retains cached pointer sizes: status
still shows 1,031 modifications even though the native-filtered diff is empty.
The recovery therefore supplies only the independently verified paths through
NUL-delimited stdin, matching the explicit-path offline regression. The
[Git update-index reference](https://git-scm.com/docs/git-update-index)
distinguishes stat refresh from updates to named paths. Afterward native-filtered
status is clean, HEAD and the indexed tree are unchanged, and every original is
rehashed successfully. No expanded image replaces a Git pointer; no ignore,
assume-unchanged, skip-worktree, attributes, hook or configuration bypass is used.

The same frozen development runtime starts with the verified native adapter,
matching executable/owner/launch ancestry and its sole development listener;
health returns 200. That response proves only the handler answers. A real named
operator reply in the existing Linear session explicitly resumes item 1, without
approving an unseen preview or requesting export. Actual worker/publication
outcome is recorded with the
[bounded UI trial](../plans/2026-10-06-feature-workers-phase-e.md).

After resolving the console regression, another fresh empty-Job/read-only fence
precedes replacing only the settled development controller's selected private
helper with a newly built, hash-verified corrected adapter. Its frozen core/config,
state, ownership, source commit and preview identity remain unchanged. The new
controller's executable/owner/ancestry/listener are verified and health returns
200. A forced sample clean-filter hash still equals its committed pointer, and
source status remains clean. This is development selection, not production
activation or a completed full-suite release claim.

## Remaining release checks

The native filter's corrected full local/hosted verification is complete. The
bounded UI trial also measures a real source draft, app-token uploads and
controller-verified current-source review. A real named session reply approves
that unchanged round and requests export. Native publishing completes in private
staging, but the controller refuses its receipt because the publisher and
approval use different source-digest formats. The failed certification is an
application regression; it neither invalidates the verified native filter nor
authorizes using uncertified staging. The separate controller correction and a
fresh certified export remain pending. Scoped Client install/metadata,
hydrated dependency/orphan guards and reserved
exact-commit Unity package loading remain separate checks. Human runtime UI QA
is broader than package loading. Production-host tool/runtime/account/permission
and release/recovery checks, plus separate enablement/deployment authorization,
remain required. No production operation or game PR merge is performed here.
