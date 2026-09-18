# Run report format

Path: `reports/<YYYY-MM-DD>-<identifier>/report.md`, committed by the worker. Raw logs stay in
`.local/runs/<item>/`.

Sections, in order: Target (repository, commit, server environment), Scope, Steps taken (commands
and their results), Evidence (paths, PR URLs, screenshots), Failures and gaps (exactly what was
not verified and why), State changes (accounts, data, files outside the PR), Next steps.

Use PASS / FAIL / BLOCKED / INCONCLUSIVE. A successful command is not a successful outcome.
Candidate lessons go under a final "Candidate lessons" heading with evidence; promotion is a PR.
