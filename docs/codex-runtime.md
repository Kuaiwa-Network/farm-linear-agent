# Preparing and upgrading the Codex runtime

Use a dedicated npm installation for FarmBot's Codex CLI. Each upgrade prepares
a fresh directory outside repository checkouts. `latest` selects the npm release
at installation time; the receipt records the resolved version, native executable,
PATH directories and SHA-256 of the package lock and complete package bundle.
The old installation stays available for rollback.

FarmBot still launches `codex exec` through the controller's PATH. On Windows,
select the package's native executable directory ahead of global npm shims and
desktop app directories. Preserve the package layout so the CLI can find its
matching command runner, sandbox and code-mode helpers. An npm update to the
operator's global CLI does not update this separate installation.

## Prepare a candidate

Run from a development checkout with Node.js and npm installed. The destination
must be an absolute, unused directory; preparation refuses existing directories,
repository descendants, symlink/junction ancestors and linked package files.
Choose a new destination for each attempt, including after a failed preparation.
Keep candidate directories and generated receipts in private host storage, with
permissions restricted to the operator and required system accounts.

macOS:

```bash
python3 -m agent.codex_runtime prepare \
  --directory "$HOME/.local/share/farmbot/codex/candidate-1"
python3 -m agent.codex_runtime verify \
  --receipt "$HOME/.local/share/farmbot/codex/candidate-1/codex-runtime.json"
```

Windows, using the installed/configured Python interpreter:

```powershell
$candidate = Join-Path $env:LOCALAPPDATA 'FarmBot\codex\candidate-1'
python -m agent.codex_runtime prepare --directory $candidate
python -m agent.codex_runtime verify --receipt (Join-Path $candidate 'codex-runtime.json')
```

The default is `--version latest`. Use `--version 0.162.1` to reproduce that
explicit release, or another exact version after checking its compatibility.
`--node` selects a Node executable; `--npm-cli` selects `npm-cli.js` when the host's
npm installation has a nonstandard layout. Both commands use subprocess argument
lists. npm runs through Node rather than a Windows `.cmd`/PowerShell shim.

Preparation installs `@openai/codex` from the public npm registry with lifecycle
scripts, audit, funding checks and bin links disabled. It runs the native CLI's
`--version` and requires it to match the installed package. It does not copy
authentication, load FarmBot configuration, open a ledger, contact Linear or
start a worker. Successful preparation writes `codex-runtime.json`; failed
installation retains its candidate directory; npm exit failures also retain
captured output logs for inspection. `verify` reads and hashes files without
starting any executable. It refuses changed, missing or added package files and
altered native/PATH metadata.

These checks establish package identity and local startup only. Before promotion,
rehearse the normal launcher, isolated authentication, required MCP/shell tools,
configured Windows sandbox and owned-process cleanup using independently owned
development resources. Follow the [TestBot workflow](development-workflow.md)
for any authorized live issue testing. A macOS result does not certify Windows.

## Promote and recover

Promotion remains a separately authorized host deployment. Once active jobs and
resource reservations have settled:

1. Preserve the installed supervisor/launchd definition, private environment,
   launch manifest and previous runtime directory. Verify the candidate receipt.
2. Prepend the receipt's `path_prepend` entries to the candidate controller PATH
   using the host's `os.pathsep`. Remove the previous Codex-specific entries from
   this candidate PATH; preserve other required tools and private settings.
3. For a sealed supervisor installation, replace the previous Codex bundle pins
   with the receipt's `pins`, bind the candidate environment path/hash, and
   recompute the launch-manifest hash. Leave the selected source revision,
   instance identity, configuration and all other tool pins unchanged. Run the
   supervisor's `--check` with those candidate inputs before replacing its action.
4. Complete settled supervisor replacement through
   [install-controller-supervisor.ps1](../scripts/install-controller-supervisor.ps1)
   or the host's verified deployment procedure. Preserve the regular maintenance
   pause, disabled guard task, controller lock and process-ownership checks.
   On macOS, update the installed launchd PATH during the equivalent settled
   maintenance window. A preparation receipt alone does not activate a runtime.
5. Verify the new controller identity, serving heartbeat, selected CLI path and
   version, and subsequent worker behavior. HTTP `/health` alone is insufficient.

Do not replace a running bundle in place or bypass a failed integrity check.
Retain uncertain worker/Unity recovery evidence and signal only verified owned
processes. Shared Unity Editors remain outside worker containment.

This command changes no controller schema or worker authority. Rollback restores
the previous runtime PATH and matching sealed environment/manifest/supervisor
inputs after work settles; it does not restore a ledger snapshot or undo issue
activity. Codex upgrades may have their own isolated-home format changes. Keep
those homes and receipts, and verify compatibility before reusing them with an
older CLI. Do not assume an older executable reverses those changes.
