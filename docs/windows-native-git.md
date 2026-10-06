# Optional native Windows Git authentication

The selected Codex 0.156.1 `unelevated` worker on the development PC can read the
existing GitHub CLI credential store natively, but Git's Schannel TLS path fails
with `SEC_E_NO_CREDENTIALS`. A command-local OpenSSL diagnostic retains certificate
verification, then exposes a second failure: the shell credential helper
`!gh auth git-credential` invokes `sh.exe`, whose signal-pipe creation is refused.
This is separate from generator support and from successful ordinary-host Git.

[The native askpass callback](../tools/windows_git_askpass.cs) is an optional
Windows tools component. It launches the configured native `gh.exe` directly,
reads its existing GitHub credential response in memory and returns only the
field Git requested through Git's private callback pipe. It writes no credential
file, token environment variable, global Git configuration or account setting.
Never invoke it directly to display credentials. Invalid prompts, non-HTTPS or
non-GitHub destinations, wrong ports/users, failed lookups and malformed responses
fail without credential or diagnostic output. It does not grant publication
authority: the existing configured-destination and ownership checks still apply.

## Build and select on an authorized development host

Use the configured Python with `PYTHONUTF8=1` before it starts. Choose a **new**
absolute private tools directory outside checkouts, state/worktrees and worker
write roots. Its parent must already exist. The builder uses the installed
Windows .NET Framework C# compiler, refuses an existing output/reparse path, and
preserves a private source/binary/native-GitHub-CLI receipt. It neither installs
the compiler nor authenticates GitHub.

```powershell
$env:PYTHONUTF8 = '1'
& $python -B tools/build_windows_git_askpass.py --output-directory $nativeGitTools
```

The private receipt supplies the absolute `askpass_selector` and
`gh_executable`. Validate both against its hashes before each service start.
Git's executable selector must be whitespace-free to avoid its helper-shell
path; the builder uses a byte-verified native short-path selector when available
and otherwise refuses. A build alone is not worker acceptance.

In the dedicated, sanitized **development controller's process environment**,
select `GIT_ASKPASS` from that receipt and set `FARMBOT_NATIVE_GH` to the native
GitHub executable. Use a cleared `credential.helper`, `http.sslBackend=openssl`
and explicit `http.sslVerify=true` through process-local Git configuration.
Withhold inherited Git selectors/credentials before choosing these values;
preserve hooks/fsmonitor disabling and the existing clone configuration allowlist.
Do not disable certificate checking, enable terminal prompts, repair ACLs or
fall back to a shell helper. No production configuration is changed by this recipe.

Verify read-only remote queries for every configured repository **inside the
actual selected worker**, and verify an invalid CA is refused. Record the binary
identities, effective policy, owned Job membership and empty-Job cleanup without
credentials or private paths. Each selected host/build needs its own measured
fetch/push/publication and real-worker acceptance. The optional callback changes no FarmBot defaults
and is not installed or selected by `serve` or `doctor`.

On Windows, clear the helper with a process-local `GIT_CONFIG_PARAMETERS`
entry; an empty `GIT_CONFIG_VALUE_n` disappears from the process environment
and Git rejects the incomplete selector. The measured development wrapper uses:

```powershell
$env:GIT_CONFIG_COUNT = '2'
$env:GIT_CONFIG_KEY_0 = 'http.sslBackend'
$env:GIT_CONFIG_VALUE_0 = 'openssl'
$env:GIT_CONFIG_KEY_1 = 'http.sslVerify'
$env:GIT_CONFIG_VALUE_1 = 'true'
$env:GIT_CONFIG_PARAMETERS = "'credential.helper='"
```

This is the Git transport portion of the previously sanitized development
environment, not a service-start or production recipe. The
[genuine Windows stage-A record](superpowers/spikes/2026-10-06-windows-stage-a-acceptance.md)
now measures successful real-worker remote queries, push/publication and the
requested stopping boundary using the initial callback candidate. The revised
UTF-8 callback separately passed native remote queries and a fresh Contract
fetch; its final full CI passes on both platforms, and #141 is merged.

`tests/test_windows_git_askpass.py` compiles a dummy native GitHub CLI in a
temporary path with spaces/Unicode. Its seven Windows tests use no host login;
non-Windows runs skip them with the explicit platform reason. A missing Windows
compiler fails the fixture as a host capability gap rather than passing a skip.
The credential request uses UTF-8 without a byte-order marker, including under
a UTF-8 Windows console; the callback restores its input encoding after creating
the child process. This avoids corrupting the protocol's first key through the
.NET Framework redirected writer's console-dependent default.

## Optional endpoint-scoped native LFS authentication

Git LFS can use a different server and credential store from GitHub. The
[native LFS callback](../tools/windows_lfs_askpass.cs) adds one explicitly pinned
HTTP(S) origin to the same optional build. It calls the selected existing native
`git-credential-manager.exe get` directly with prompting disabled. It uses a
server-only credential lookup, equivalent to `credential.useHttpPath=false`;
it neither discovers other accounts nor falls back to interactive login.
An existing path-specific credential requires a separate reviewed implementation.

Select the origin and existing executable together when creating a new build:

```powershell
$env:PYTHONUTF8 = '1'
& $python -B tools/build_windows_git_askpass.py --output-directory $nativeGitTools --lfs-origin $privateLfsOrigin --gcm-executable $nativeGcm
```

`$privateLfsOrigin` contains only the scheme, server and optional port from the
authorized repository's committed LFS configuration, with no username, password,
repository path, query or fragment. GitHub is refused as an LFS origin. HTTP is
supported for an explicitly selected existing internal endpoint; this does not
upgrade its transport confidentiality. Keep the endpoint private and verify it
against the authorized repository before building.

The private `askpass.lfs` companion pins that origin, the absolute GCM executable
and its SHA-256. The callback reads it beside its own executable, rejects reparse
paths and a changed GCM binary, accepts native short-path aliases by normalizing
the absolute path before validating and executing it, and checks the requested scheme/server/port and
returned identity before emitting only the requested field. Environment variables
cannot replace these LFS pins. Missing or malformed settings and lookup failures
produce no credentials or diagnostics. GitHub prompts still use the existing
GitHub callback exclusively; a failed GitHub lookup cannot use GCM credentials.
Omitting both new build options preserves the GitHub-only build.

Keep the executable, companion and receipt outside worker write roots. Validate
their recorded hashes and native CLI identities before selecting the build for a
development controller. Rebuild into a new private directory after an authorized
endpoint or GCM update; do not edit the companion to bypass a failed identity
check. The companion and receipt contain private endpoint/path metadata, never
credential values. The builder performs no lookup, authentication or credential
installation, and changes no Git, account or service setting.

Windows GCM uses this account's existing credential store. A separate HOME does
not establish account isolation. This optional tool adds no filesystem grants,
elevation or Job breakaway and is not selected automatically by FarmBot. Real
worker LFS transfer and publication acceptance are still required for each
selected account, endpoint, binary and worker policy. The
[development measurement](superpowers/spikes/2026-10-06-windows-final-tools-lfs.md)
records the earlier private callback's successful scoped 1 KiB transfer.

`tests/test_windows_lfs_askpass.py` adds dummy-GCM native tests for origin and
executable pins, quiet failures, UTF-8, server-only lookup, builder selection and
credential-store separation. The fixtures never read host credentials. Native
tests skip on non-Windows with their platform reason; origin validation runs on
both platforms. A missing Windows compiler remains a capability failure.
