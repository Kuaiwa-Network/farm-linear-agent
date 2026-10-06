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
credentials or private paths. Fetch/push/publication and a real worker still need
their own measured acceptance. The optional callback changes no FarmBot defaults
and is not installed or selected by `serve` or `doctor`.

`tests/test_windows_git_askpass.py` compiles a dummy native GitHub CLI in a
temporary path with spaces/Unicode. Its six Windows tests use no host login;
non-Windows runs skip them with the explicit platform reason. A missing Windows
compiler fails the fixture as a host capability gap rather than passing a skip.
