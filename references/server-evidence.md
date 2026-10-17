# Retained server evidence

Select acceptance from the fixed source diff and issue requirements, as stage D describes.
Keep the original raw reports, their hashes, tested commits, toolchain, service mode,
environment, package/test selection, timeout and skip outcomes. An incomplete run is
not a passing full suite; complete disjoint package reports can establish coverage,
not erase failures or turn several reservations into one green run.

For each retained failure, distinguish:

- Issue-related: the assertion covers changed behavior or a necessary registration.
  Fix it on the issue branch and verify the correction. Never treat a missing legitimate
  transaction registration as unrelated debt or disable its exact-set gate.
- Matched baseline failure: an actually executed comparable baseline has the same
  per-test failure. Preserve both report identities; counts alone do not establish this.
- Confirmed independent defect: the exact failing assertion has a concrete causal chain
  outside this issue, supported by immutable source/input identities and observed logs.
  Review the complete actual diff, directly affected consumers and changed shared test
  helpers. Explain why each relevant change cannot reach the failing condition. For an
  unsupported platform path, prove that the actual run used that platform and that the
  unchanged path produces the observed failure before the changed behavior executes.
  For a source-only gate, identify all source/fixture/toolchain inputs it reads. A file
  name, unchanged test alone, old issue verdict or plausible explanation is insufficient.
- Unresolved: preserve the specific missing evidence. Use the existing bounded diagnosis
  budget, never reset it on maintenance/resume or repeatedly rerun unchanged full suites.

Save the causal review and hashed evidence references in the private run directory and
link them from the handoff/evidence index. Confirmed independent defects remain failed
tests, separately disclosed for maintenance; do not call them baseline-matched without an
executed comparable baseline. They need not block scoped draft delivery when required
issue behavior and checks are satisfied. Any unresolved or issue-affecting failure still
blocks; this grants no security, provenance, publication or required-test waiver and is
not a Unity compare-verification verdict.

A registration-only follow-up commit does not relabel old reports as tests of the new
SHA. Record the exact intervening diff, confirm production code, test code and relevant
inputs are identical, reuse only the unchanged behavior evidence with that justification,
and execute the affected registration gate and its intended negative case at the new
clean candidate. Changed behavior, test harness, dependencies or inputs invalidate the
corresponding reuse. Keep the original upstream base; do not merge moving main or request
a fresh full baseline just to produce a newer SHA. Publication must still pass the
controller's ancestry and destination checks; retained evidence is not publishing authority.
