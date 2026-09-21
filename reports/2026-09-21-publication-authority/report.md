# Verified draft PR publishing authority

## Problem and behavior

FARM-1248's farmgui push was rejected as an unverified external destination even
after the user requested a PR. Other jobs received inconsistent approval decisions
from the same repository allowlist. Dispatch contained local worktree paths but no
verified destination evidence or explicit, job-scoped publishing authorization.

Every delegated write-worker launch now includes verified private GitHub destinations,
actual issue branches and the operator's authorization to publish the issue's source,
tests, required generated assets and verification report as draft PRs. Direct session
requests are carried separately across resumes; ordinary issue comments and memory
are not promoted to authorization. Chat and undelegated jobs get no publishing scope.

The read-only verifier checks worktree/clone ownership, the issue branch, the effective
origin push destination, exact GitHub owner/repository, private visibility, write
permission and default branch. Existing protected branches are refused. Missing,
malformed or failed verification produces an explicit gap while investigation can
continue locally. Public repositories are outside this private-repository policy.

`verify-publication --item ... --token-file ... --repo ...` repeats the checks before
publication, refreshes Linear delegation/status, checks the skill allowlist and host
ledger, and rechecks the claim after GitHub reads to catch concurrent cancellation.
The worker pushes via verified `origin`, an explicit branch refspec and
`--no-follow-tags`; PR commands use the full verified repository URL.

## Verification

- Clean baseline: 456 tests passed with warnings treated as errors.
- Initial integrated implementation: 469 tests passed with warnings treated as errors.
- Final gate after review fixes: `python3 -B -W error -m unittest discover -s tests`
  passed **472 tests in 103.316 seconds**, warning-free. `git diff --check` passed.
- Regression tests exercise real temporary Git repositories and SQLite, with GitHub
  metadata injected: changed/multiple/rewritten push URLs, foreign clones, private
  visibility/write access, wrong/protected branches, read-only and undelegated jobs,
  resumed requests, host-ledger mismatch and cancellation during verification.
- Live read-only checks verified all five configured repositories on FARM-1248's
  worktrees: Farm-Client, farm-hive, farmgui, common and Farm-Contract. All matched
  their configured private GitHub repositories and had write access. No pushes or
  PR mutations were performed during these checks.

## Review and fixes

Independent review found three issues, reproduced before correction:

1. Reusing an expanded push URL could apply Git rewrites a second time. The worker
   now pushes by the verified remote name. The reviewer independently confirmed
   with a dry run and disabled HTTPS transport that this avoids the second rewrite.
2. Retained predecessor branches can cause a successor's branch to gain an item-ID
   suffix. Verification now inspects each actual worktree branch.
3. Malformed GitHub metadata could crash launch. Field-shape checks now withhold
   publishing scope instead. All 11 verifier tests passed on scoped re-review.

Worker-instruction simulation followed the preflight and exact issue-branch/draft-PR
flow, preserved local work on a changed destination, and did not bypass review on
rejection. No consequential findings remain from the scoped re-review.

## Limits and deployment

This supplies verified evidence and the user-approved publishing scope; it is not an
atomic publishing service or a replacement for automatic approval. Remote config,
issue status or GitHub branch rules can change after a check. Approval can still
reject an action, and the worker must address the stated gap rather than bypass it.
Matching rules on a not-yet-created branch remain subject to GitHub enforcement.

The running service has not been changed. Merge and deploy this revision to provide
the new context to future worker attempts. No model, concurrency or Unity settings
were changed.

API references: [GitHub repository metadata](https://docs.github.com/en/rest/repos/repos#get-a-repository)
and [branch metadata](https://docs.github.com/en/rest/branches/branches#get-a-branch).
