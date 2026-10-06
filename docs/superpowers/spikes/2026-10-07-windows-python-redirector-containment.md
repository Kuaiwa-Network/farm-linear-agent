# Native Windows Python redirector containment

This is a development-PC finding, with no production installation, configuration,
ledger, service, account or credential change. It applies to the shared native
worker launcher, including fix/Code/UI; it introduces no new workflow authority.

## Measured failure

The publisher foundation's initial and fixture-corrected native full runs each
complete 1,957 tests with zero assertion failures, one cleanup error and 69 platform
skips. Durations are 1,267.368 s and 1,280.574 s. Both errors are the existing
`test_a_budget_kill_that_raises_keeps_the_live_worker_and_reports_the_others`:
temporary cleanup finds an open `stderr.log` from its second subtest. All seven
required native Job Object checks and all 11 native publisher checks run in both
suites. Ten isolated repetitions pass, as do the publisher module followed by 20
repetitions (52 tests, 11.565 s). Passing the focused case alone did not resolve the
full-suite failure; no skip or cleanup suppression is added.

The selected Python 3.13.16 UI environment's executable is a Windows venv
redirector; `sys.executable` differs from `sys._base_executable`. A separate owned
gated fixture waits for its base interpreter before assigning the redirector to
FarmBot's Job. The retained child process object is verified as the selected base
Python, and `IsProcessInJob` reports that it remains outside FarmBot's Job after
parent assignment, while already belonging to another Job. **No input handshake
is opened and no fixture CLI executes.** Closing stdin exits the gate; owned
process handles are waited and the probe's Job empties. No unknown PID is killed.
An earlier probe's attempt to attach that already-created child to the populated
Job refuses with WinError 5; its gated failure evidence is retained privately.

The [Python 3.13.16 redirector source](https://github.com/python/cpython/blob/v3.13.16/PC/venvlauncher.c)
creates its own Job and starts a base interpreter. The
[Windows Job association rules](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-assignprocesstojobobject)
describe child inheritance from the parent's current Job chain. Assignment after
startup therefore leaves a race before FarmBot's containment. This measured race
is the explanation investigated for the log-lock failure; final full verification
must confirm the correction rather than treating that inference as a pass.

## Correction

Create the selected Python gate with the documented
[`CREATE_SUSPENDED` flag](https://learn.microsoft.com/en-us/windows/win32/procthread/process-creation-flags),
assign its retained creation object to FarmBot's non-breakaway Job, then resume its
one verified initial thread before supplying the existing input handshake. This
preserves the configured Python/venv and avoids changing HOME, credentials,
permissions, accounts or the worker's native sandbox selection.

The resume helper requires a live owned Job and live process membership, matching
retained process/PID identity, one initial thread, matching retained thread owner
and exactly one suspension. It uses documented `ResumeThread` and thread-query
APIs. Unknown/denied ownership or startup state fails closed and terminates only
the owned suspended process/Job; no fallback or elevation is attempted. Existing
Job termination, descendant proof and POSIX ownership checks are retained.

Three new native checks cover deliberately delayed assignment before any gate or
redirector startup, resume failure with not-started evidence, and refusal to resume
an unassigned creation object or through a closed Job. All **10 native Job checks
pass in 1.954 s**, zero failures/errors/skips. The existing launcher module passes
**98 tests in 9.141 s**, with its 46 existing POSIX-only skips. The formerly failing
budget-kill test passes **50 repetitions in 19.087 s**, no failures/errors/skips.

Early correction checks found a prototype reference to a flag that Python's
`subprocess` does not export; the shared native helper now defines the documented
WinBase.h value before opening log streams. Two new fixture expectations are
corrected: `running()` returns a mapping, and ancestry must be read before a Job
membership snapshot while sleeping children are still being created. Those
corrections preserve every membership assertion and add no skip.

Exact-candidate full native/hosted verification follows separately. The prepared
publisher helper needs the same suspended-start sequence before its own nested
gate; real publisher/isolated-worker verification must be repeated on that final
route. Prior passing runs alone cannot certify the startup-race correction or
production-host readiness. UTF-8 logs, revision, tool versions, individual skips
and private process/fixture identities remain local.
