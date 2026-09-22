# Offline CI

[The workflow](../.github/workflows/tests.yml) runs on pull requests, pushes to
`main`, and manual dispatch. Both `macos-latest` and `windows-latest` run Python
3.13. This is the initial CI baseline; a passing Mac job does not establish
Windows compatibility. Hosted runner images and the Python patch can change, so
retain the versions recorded with each candidate's test evidence.

The suite uses the standard library, temporary state, local Git repositories,
fake workers and fake Unity/MCP. Git and Git LFS must be on `PATH`; the dependency
step checks the hosted image's installed versions and fails if either is absent.
It also checks directory symlink creation because symlink rejection tests protect
filesystem ownership boundaries. Missing privilege is a runner setup failure.

The workflow requests only `contents: read`, does not reference secrets, and
disables checkout credential persistence. It does not install or authenticate
worker CLIs, launch a real Unity Editor, invoke live Linear, or deploy a service.
GitHub's checkout, Python setup and artifact upload steps use the network; the
test fixtures themselves are offline apart from local loopback listeners.
Before discovery, the test step removes inherited `FARMBOT_*` and `FAKE_CLI_*`
selectors and GitHub token environment variables. Fixtures supply their own
settings. This is not a network sandbox or a standalone safe runner for a host
with production credentials; unexpected external access is not blocked by this
workflow. Keep these jobs on the fresh GitHub-hosted runners specified here.

Native Windows Job Object tests must execute in the Windows job. POSIX-only tests
retain their existing skip reasons. Keep both operating-system results when one
fails; the matrix deliberately has `fail-fast: false`. Do not hide Windows
failures behind broad skips or treat a headless CI job as desktop Unity acceptance.

Test reports are uploaded for 14 days even when tests fail. They contain only
offline test output and version/result metadata, not `.local` state or live
configuration. Investigate failures from the exact candidate revision and record
platform skips. Require both jobs in branch protection only after the first
successful Windows and Mac baseline; adding this file does not configure remote
branch protection or establish that baseline.

For changes to native process handling, installation or Unity, the separate
Windows acceptance scenarios in the
[development and release plan](superpowers/plans/2026-09-22-cross-platform-development-and-release.md)
remain required before production promotion.
