# Test environment implementation

Status: user authorized implementation and test-resource provisioning after PR #23.
This executes the approved development workflow; production deployment remains separate.

## Design and order

1. Add explicit `legacy`, `development`, `production`, and `offline` environments.
   Preserve existing configs as legacy. Explicit live configs require absolute state
   roots outside the checkout default, an instance ID and pinned Linear app/workspace
   IDs. Refuse fake workers and stub selectors for live profiles. Offline profiles
   require both fake workers and Linear stubs, and remain dependent on isolated fixtures.
2. Bind a fresh state root to nonsecret profile identity using an ownership marker.
   Never adopt existing unmarked runtime state automatically. Hold an OS-backed lock
   for a controller's lifetime; reject contention before constructing a ledger.
   Reject a legacy profile attempting to reuse a marked root. Validate derived state
   and explicit Unity folders remain inside the selected root for explicit profiles.
3. Share configured issue-key policy between scheduler and publication verification.
   Default to FARM; development uses FBTEST. Preserve exact destination, private repo,
   protected branch, delegation, claim and PR checks.
4. Namespace launchd labels for explicit instances. Render only; do not start jobs as
   an installation side effect. Provide a private local development config template.
5. Run offline CI on macOS and Windows with Python 3.13, Git LFS, evidence artifacts,
   and mandatory native Windows worker tests. Fix platform-dependent fixture assertions
   without weakening the behavior under test.
6. Provision a separate Linear workspace/app and private sandbox destinations, then
   configure and inspect the local installation before starting an authorized live run.
   Authentication, security grants and binding terms may require the user's interaction.

## Verification

- Regress identity mismatch with unchanged ledger/state; fake/stub rejection before API calls.
- Exercise same-root lock contention and release in real child processes, including
  paths with spaces and Unicode. Check changed profile, malformed markers and unmarked
  existing state are refused without rewriting evidence.
- Confirm independent roots can run concurrently and a failed startup releases its lock.
- Regress FBTEST publishing and canonical branch selection with real local Git fixtures.
- Run focused tests, then the full suite; publish CI and inspect both platform results.
- Keep external account blockers and Windows Unity acceptance distinct from offline results.

## Operational limits

Config paths pin selection, not file contents. A marker prevents accidental reuse; it
does not replace OS account permissions or Git credentials restricted to sandbox repos.
Existing production installations need an explicit, backed-up migration before adopting
an explicit production profile. No database migration or production restart is authorized
by this implementation. Windows installation/Unity acceptance need a reachable test host.
