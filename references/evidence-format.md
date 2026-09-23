# Run report format

Path: `STATE_DIR/report.md`, where `STATE_DIR` is the private run directory in the worker's launch
message. Keep the report and raw logs there; do not commit or publish run reports in a repository.
Summarize the checks, tested commits and gaps in the draft PR description and Linear outcome.

Sections, in order: Target (repository, commit, server environment), Scope, Steps taken (commands
and their results), Evidence (paths, PR URLs, screenshots), Failures and gaps (exactly what was
not verified and why), State changes (accounts, data, files outside the PR), Next steps.

Use PASS / FAIL / BLOCKED / INCONCLUSIVE. A successful command is not a successful outcome.
Candidate lessons go under a final "Candidate lessons" heading with evidence; promotion is a PR.

For each check, record the full tested commit SHA, command or test selection, test mode,
environment (including relevant Unity version and dependency state), result and artifact path.
Separate assertions established by evidence from hypotheses about their cause.

| Result | Required evidence |
| --- | --- |
| PASS | The intended check actually ran and its relevant assertions passed. State the coverage; a targeted pass is not a full-suite pass. |
| FAIL | The intended check ran and failed. Name the individual test/assertion and observed failure. Attribution may still be unresolved. |
| BLOCKED | A prerequisite prevented the intended check: missing or LFS-pointer dependencies, setup/typecheck failure, or compilation failure before the intended test. Record the prerequisite error and the resulting verification gap. |
| INCONCLUSIVE | No usable result: stalled/timeout run, missing or unreadable XML, or zero tests. Record the verification gap and available logs. |

A prerequisite error is not a failing product assertion. After correcting hydration or
setup, run the intended check before claiming red/green. For a testable logic fix, show
the relevant assertion failing on the baseline and passing on the fix under comparable
conditions; a baseline build failure followed by a fix build success alone is insufficient.

Classify failures as pre-existing only with per-case baseline evidence: record baseline
and fix SHAs, the same mode, selection and environment, and compare test names and failure
signatures. Historical counts, another issue's run and memory notes are leads, not proof
that a current failure is unrelated. Without a matched baseline, state "observed failure;
attribution unresolved". Report mode-specific totals separately.

Draft delivery reports, PRs and comments must name remaining gaps and their impact on
confidence. A delivered outcome records submitted work; it does not turn incomplete
verification into a pass. Record a Unity stall's observed state and recovery separately
from its cause; recovery after an Editor reload alone establishes no root cause.
