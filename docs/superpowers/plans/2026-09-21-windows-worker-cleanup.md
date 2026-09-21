# Windows worker cleanup implementation plan

**Goal:** Prove worker-tree termination after spontaneous exits without touching shared Unity editors.

**Approved design:** User approved Windows Job Object containment and an audited legacy recovery path after diagnosis of FARM-1259.

**Architecture:** The host owns a kill-on-close Windows job per attempt. A small Python gate waits until assignment before launching the CLI, preventing children escaping during startup. Poll and stop terminate the job and wait for zero active processes before recording evidence. Named jobs allow fail-closed recovery after receiver restart. Unsandboxed Unity processes remain outside worker jobs.

**Legacy limit:** Existing workers cannot be retroactively proven contained. An operator recovery command may certify old attempts only after a machine boot newer than their last launch, with terminal-state and resource checks. Do not invent teardown evidence from a missing parent PID. No reboot or interruption of active trials is authorized by this plan.

## Steps
- [x] Add Windows integration tests: spontaneous worker exit with surviving child, stop, host crash, assignment failure, independent process isolation; observe failures.
- [x] Implement job ownership, startup gate and proof checks; run focused launcher tests.
- [x] Add tested operator recovery for old attempts using boot evidence; document limitations.
- [ ] Review branch and run relevant checks. Publish draft PR. Deploy only when active workers have drained; otherwise retain a deployment handoff and keep monitoring.

## Review focus
- Assignment failure must never run the requested CLI.
- No worker may escape during the assignment window.
- Cleanup proof must follow an empty job, not merely an exited parent.
- Receiver crash must terminate workers without terminating shared editors.
- Old or unknown process identity must fail closed, including PID reuse.

## Validation record
- Initial real Windows regression reproduced a surviving child and false-positive process liveness.
- Stop/poll concurrency and cross-attempt PID reuse were reproduced before correction.
- 118 targeted checks pass, with 5 platform/privilege skips. The lifecycle tests also passed 75 repeated cases.
- Real gpt-5.6-sol/high contained worker replied WINDOWS-JOB-OK, exited 0 and passed quiescence verification.
- Existing Windows test assumptions corrected: OS path separator, path rendering and POSIX-only SIGKILL scenario; symlink test skips only when the host lacks symlink privilege.
- Legacy trials remain active on the live receiver; deployment requires a drained receiver. No live process or Unity slot was interrupted.
