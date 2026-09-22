# Developing FarmBot alongside production

## Approved workflow

Develop on macOS while production stays on its accepted Windows revision. Use a
separate Linear test workspace and a separate test bot for live experiments. Real
feature requests and bug reports become test cases through a deliberate snapshot
copy; they do not need to be artificial examples.

```text
Production issue + selected source revision
                  |
          manual snapshot copy
                  v
Independent issue in the test workspace
                  |
       FarmBot Dev + sandbox repositories
                  v
Evidence and optional sandbox draft PR
                  |
       explicit review and promotion
                  v
Normal source PR linked to the production issue
```

There is no ongoing synchronization. Test comments, replies, Stop actions,
delegation and status changes remain in the test workspace. A snapshot's original
issue URL provides provenance, not permission for a worker to modify that issue.
Start with manual copies; automated issue import is deferred.

## What is available now

- Offline unittest fixtures use temporary state, local Git repositories, fake
  workers and stubbed Linear/Unity integrations.
- The service propagates the selected config file to initial and resumed workers;
  launchd installation preserves environment-selected configs as well.
- Live test-bot provisioning, configurable app identity, test-team publishing
  policy, enforced environment ownership and Mac/Windows CI remain work in the
  [rollout plan](superpowers/plans/2026-09-22-cross-platform-development-and-release.md).

Do not treat config propagation alone as completion of live environment isolation.
Current identity validation still expects `FarmBot`, and publication validation
still expects `FARM-*` issue identifiers. The planned `FarmBot Dev` / `FBTEST-*`
setup needs those prerequisites before its first live run.

## Daily code changes

1. Start a feature branch in an isolated checkout of this repository.
2. Add a regression for the changed behavior and run focused offline tests.
3. Run the full suite before publishing a behavior change. On macOS:

   ```sh
   python3 -m unittest discover -s tests -p 'test_config_propagation.py' -v
   python3 -m unittest discover -s tests -v
   ```

   On Windows, use the configured Python interpreter, for example:

   ```powershell
   python -m unittest discover -s tests -v
   python -m unittest discover -s tests -p 'test_windows_workers.py' -v
   ```

4. Use a live test issue only after the test installation meets the prerequisites
   below. Offline tests remain the quick iteration loop.
5. Review and merge the FarmBot change after relevant platform checks. Windows
   process, installation and Unity behavior require Windows evidence.
6. Promote an accepted FarmBot revision to production through a separately
   authorized release. Merging a development PR does not restart production.

## Live test installation prerequisites

Use a distinct app identity, client credentials, webhook signing secret and public
endpoint for the test workspace. Pin and verify its expected organization/app IDs.
Keep production credentials out of the worker environment. A future importer needs
separate production read credentials and test write credentials; the running test
worker should not receive the production reader's credentials.

Each installation needs an absolute state root and separate ledger, memory, run
logs, bare clones, worktrees and Unity slots. Give concurrent instances distinct
receiver/MCP ports and service names. Do not share production Unity folders or copy
Mac runtime state to Windows. Existing launchd labels are fixed, so two installations
for the same user are not yet supported by that installer.

Map logical repository names to private sandbox copies of the real source. Include
the relevant Git LFS assets, Unity version/build modules and test dependencies. Git
and GitHub CLI credentials used by development should have write access only to
the intended sandbox destinations. Publishing checks must accept the test team's
issue-key policy without weakening destination or protected-branch verification.

Keep the game/server test environment separate as well. The config's
`default_server_environment` is descriptive; it does not enforce server isolation.

## Turning a real issue into a test case

Create a fresh issue manually in the test workspace. Preserve the relevant facts
in its description and record where they came from. Copy attachments only when
appropriate for the test workspace; production-only links may not be readable
there. Record unavailable evidence rather than silently omitting it.

Use this description template:

```text
Source issue: original workspace URL and identifier
Snapshot captured at: UTC timestamp
Source issue last updated at: timestamp, if known
Purpose of this run: behavior or regression being evaluated

Request / observed bug:
Expected behavior:
Reproduction steps:
Relevant discussion (with source author/time where useful):
Attachments copied:
Evidence unavailable in the test workspace:

Target source: repository identity + branch/reference + full commit SHA
Other relevant repository revisions:
Test server/account/data requirements:
```

The copied issue gets its own ID, comments and lifecycle. Review the snapshot before
delegating it from the test workspace's UI. Source comments remain evidence; they
are not new commands to the test worker. Keep the selected source revision in the
case record and verify the run's actual target matches it: recording a SHA in prose
does not itself pin FarmBot's execution target.

For reproducible comparisons, retain the snapshot and record each run's FarmBot
commit, actual target-code commits, worker runtime/model, Python/Unity/MCP versions,
test data assumptions, job ID and evidence paths. Use a fresh test issue when you
need a fresh lifecycle. Existing memory can influence a run, so record its relevant
snapshot or use a separate disposable test state root for a clean comparison.

## Using a successful experimental fix

First review its code and evidence in the sandbox. If useful, explicitly authorize
promotion: apply the reviewed commits to a normal feature branch based on current
source, resolve differences and rerun the required checks. Open a normal PR linked
to the original production issue, explaining which evidence came from the test run.

Do not copy the test ledger, claim tokens, memory, host configuration or sandbox
credentials into production. Do not automatically change the production issue's
status, post test chatter there or merge either PR. Promotion of a gameplay fix and
deployment of a new FarmBot revision are separate operations.
