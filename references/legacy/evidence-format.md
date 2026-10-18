# Run report format

Path: `STATE_DIR/report.md`, where `STATE_DIR` is the job's private state directory from the worker's
launch message. Retries and resumes of the job share it: when an earlier attempt's report is already
there, leave earlier reports unchanged and write the first unused `STATE_DIR/report-2.md`,
`report-3.md`, …, naming the report it supersedes. Keep reports and raw logs in `STATE_DIR`; do not
commit or publish run reports in a repository. Summarize the checks, tested commits and gaps in the
draft PR description and Linear outcome.

A continuation report contains only changed decisions, new results and remaining work;
reference previous reports and `plan.evidence_index` instead of copying their evidence.
Update the current attempt's prepared report as links/status change. Keep canonical
test reports immutable; see [closeout reuse](closeout-reuse.md).

Sections, in order: Target (repository, commit, server environment), Scope, Steps taken (commands
and their results), Evidence (paths, PR URLs, screenshots), Failures and gaps (exactly what was
not verified and why), State changes (accounts, data, files outside the PR), Next steps.

Use PASS / FAIL / BLOCKED / INCONCLUSIVE. A successful command is not a successful outcome.
Candidate lessons go under a final "Candidate lessons" heading with evidence; save reusable ones
through the memory CLI in `references/memory.md`.

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
setup, run the intended check before claiming the behavior passed. Validate the candidate
at the issue's required scope. When a before/after reproduction is needed to establish a
fix or resolve attribution, record the relevant assertion under comparable conditions;
a baseline build failure followed by a candidate build success alone is insufficient.
Reuse valid observations and use the necessary focused selection, not two full suites
by default. UI work follows its recorded validation decision and required evidence.

Describe failures as empirically baseline-matched only with per-case baseline evidence: record baseline
and fix SHAs, the same mode, selection and environment, and compare test names and failure
signatures. Historical counts, another issue's run and memory notes are leads, not proof
that a current failure is unrelated. Without a matched baseline, retain the actual failure
and use the skill's reviewed [candidate-first assessment](candidate-verification.md) or
[server causal evidence](server-evidence.md) when applicable. Distinguish a proven
independent defect or reviewed unchanged-source finding from empirical baseline matching;
say `baseline_executed=false` when no baseline ran. Otherwise state "observed failure;
attribution unresolved". Acceptance failures and evidence gaps still block the applicable
required checks. Report mode-specific totals separately.

Draft delivery reports, PRs and comments must name remaining gaps and their impact on
confidence. A delivered outcome records submitted work; it does not turn incomplete
verification into a pass. Record a Unity stall's observed state and recovery separately
from its cause; recovery after an Editor reload alone establishes no root cause.
