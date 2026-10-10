# Developing FarmBot alongside production

## Approved workflow

Develop on macOS while production stays on its accepted Windows revision. Live
tests use a second Linear app, **TestBot**, installed in the same Kuaiwa AI
workspace as production **FarmBot**. It works on real issues that the operator
chooses and publishes to the real repositories.

```text
Real issue in Kuaiwa AI, chosen by the operator
                  |
   @TestBot mention, or delegation from the Linear UI
                  v
TestBot on the Mac: development checkout + private development profile
                  |
                  v
Session activity on that issue; optional draft PR on its farmbot/<key> branch
                  |
          explicit review
                  v
FarmBot change merged; production upgraded by a separate release
```

This replaced a separate test workspace with manually snapshotted issues and
private sandbox repositories, abandoned on 2026-09-23. Snapshots lost the
comments, links and attachments that keep evolving on a real issue; the sandbox
repositories drifted from the code the issue described; and a result proven there
still had to be re-tested in the real workspace. Do not reintroduce a separate
workspace, manual issue snapshots or sandbox repositories.

## What separates the two bots

Current behaviour:

- Each app has its own client ID, client secret, webhook signing secret and
  endpoint. The receiver accepts an AgentSessionEvent only when `oauthClientId`,
  `appUserId` and `organizationId` all match its profile, so each bot ignores the
  other's sessions.
- Issue webhooks match the organization only. Both bots receive them for every
  issue in the workspace; they only request a fresh status read for issues that
  instance already tracks.
- An explicit `development` profile pins `expected_app_user_id` and
  `expected_organization_id`, and the app's API identity must match both and
  `expected_bot_name`. Both profiles pin the same organization; nothing requires a
  different workspace.
- A dedicated absolute `local_root`, bound by its `environment.json` marker and a
  controller lock, keeps the ledger, memory, clones, worktrees, runs, slots and logs
  apart. Each instance also needs its own port; production uses 8765.

What does not separate them:

- There is no team, project or issue allowlist; the rollout plan proposes one.
  `issue_prefix` is a publishing rule, not an admission rule. FarmBot starts work
  only on a delegation to its own app or an @mention of it, so the operator's
  choices are the test scope. A delegation Linear opened no session for counts
  too: on a card the instance already tracks, a status read that finds the card
  delegated to it again can start the work the labels name about 90 seconds
  later, in the instance's existing delegation thread on that card, whoever set
  the delegate, an automation or Linear's API included (operating contract,
  Triggers).
- Both bots use `farmbot/<lowercase-key>` branches, with `-config`, `-waivers` and
  `-followup` suffixes for Code jobs, in the same repositories.
- The development bot's comments, agent sessions, `needs-more-info` labels,
  branches and draft PRs are real and visible to the team.
- Both bots read the same labels. A label or label-group change in Linear, such
  as the rename of 功能 to Bot, reaches both at their next read of a card, while
  code reaches each bot at its own deploy.
- Checked 2026-09-23: none of the five repositories protects its default branch,
  and the Kuaiwa-Network GitHub plan offers neither branch protection nor rulesets
  for private repositories. FarmBot's publication verification (exact destination,
  private repository, write access, issue branch, never the default or a protected
  branch) is the only guard, and it is part of the code under test. Test changes
  to publication, push and worktree code offline before running them live.

## Scoping a live test

- Mention or delegate only issues you own or created for the test.
- Never delegate the same issue to both bots.
- Start a new build with an @mention on an undelegated issue. That takes the
  read-only conversation path and receives no publishing scope.
- Delegate from the Linear UI. On an explicitly authorized API delegation,
  inspect the actual agent session before requesting another. The native Windows
  TestBot trial on 2026-10-07 observes Linear opening one from API delegation;
  earlier tests did not. If no session opens, the controller's documented
  settlement still applies to a card TestBot tracks. In the @-autocomplete, pick
  TestBot, not FarmBot. See the [measured UI trial](superpowers/plans/2026-10-06-feature-workers-phase-e.md#as-executed-native-testbot-intake-and-scope-stop-2026-10-07).
- FarmBot never closes PRs or deletes remote branches. Close an unwanted draft PR
  and its branch yourself.
- A Code job on a real card reaches three repositories and waits for days between
  stages. Keep it to the stages you mean to test by saying so in the delegation text,
  for example 「只做阶段 A（合约）：开出合约草稿 PR 后停下等我，不要交接到 common。」: the
  worker finishes that stage and pauses instead of handing off. Press Stop or remove
  the delegation when the test ends; its branches and PRs stay, the suffix branches
  (`-config`, `-waivers`, `-followup`) included, until you close or delete them.

## What is available now

- Offline unittest fixtures use temporary state, local Git repositories, fake
  workers and stubbed Linear/Unity integrations.
- The service propagates the selected config file to initial and resumed workers;
  launchd installation preserves environment-selected configs as well.
- Explicit profiles verify bot/workspace identity, bind a fresh state root, hold a
  controller lock and reject fake/stub integrations for live operation.
- `issue_prefix` selects the team key accepted for publishing; it defaults to
  `FARM`, the key of the real 农场 issues a development profile tests.
- The Code worker (`feature`) is opt-in: TestBot runs it only when its profile's
  `enabled_skills` names it and `lark_cli` selects the FarmBot Feishu app through a profile or
  the explicit environment variant (Setting up TestBot). A Code job runs for days and publishes real
  draft PRs in Farm-Contract, common and farm-hive, and branches such as
  `farmbot/<key>-config` in common.
- `tests/test_feature_journey.py` drives Code jobs through the whole controller
  offline, with the fake worker, the stub Linear and local Git remotes for all five
  repositories.
- The UI worker (`fgui`) is separately opt-in and exclusive. It authors farmgui
  and posts approximate previews for named human visual approval. Actual approval
  and an explicit export request permit the configured native certified publisher,
  scoped Client assets/metadata and reasoned dependency entries, then actual guard
  and interactive Unity package-loading checks. Its `lark_cli` routes match Code's.
  `tests/test_fgui_journey.py` tests real-controller intake, corrections, explicit
  continuation, preview retries and Stop/recovery with native tools and local fixtures.
  Scripted publisher/Editor fixtures do not certify real visual judgment, licensed
  export, live app-token upload, actual Client guards or Unity acceptance. A newly
  approved TestBot UI card and separately authorized final-host release remain.
- [Mac/Windows CI](ci.md) runs the offline suite with Python 3.13 and records evidence.
- [Optional native Windows Git authentication](windows-native-git.md) records the
  restricted-worker TLS/helper-shell gap and a native callback recipe using the
  existing GitHub CLI login. It changes no service defaults or production settings.
- A development Unity slot and Windows desktop acceptance remain operational setup
  in the [rollout plan](superpowers/plans/2026-09-22-cross-platform-development-and-release.md).

Existing configs use legacy compatibility; they do not acquire the stronger explicit
live-profile requirements automatically. Do not point a development profile at
existing production data or copy its marker.

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

4. Run TestBot from that checkout on a live issue once it is set up as below.
   Offline tests remain the quick iteration loop.
5. Review and merge the FarmBot change after relevant platform checks. Windows
   process, installation and Unity behavior require Windows evidence.
6. Promote an accepted FarmBot revision to production through a separately
   authorized release. Merging a development PR does not restart production.

## Setting up TestBot

1. **Linear app.** In Kuaiwa AI, create an OAuth app whose name is exactly the
   profile's `expected_bot_name` (`TestBot`) and enable client credentials.
   FarmBot requests `read,write,app:mentionable,app:assignable` when it fetches its
   token. Enable webhooks for Agent session events and Issues. The URL is exactly
   `https://<host>/webhook`: routes are string-matched, so a trailing slash returns
   404. Requests need an HMAC-SHA256 hex signature of the raw body in
   `Linear-Signature` and a `webhookTimestamp` within 60 seconds. Linear requires a
   redirect URI even though client credentials never use one; an unused localhost
   URI is enough.
2. **Profile.** Copy the [template](../config/development.example.json) to a
   private file outside Git and outside any checkout or worktree that may be
   deleted. `client_id`, `client_secret` and `webhook_secret` are all required, so
   every config-loading command fails until they are filled. Enter them through
   hidden input; never paste them into chat, commits or logs. An agent should not
   create or edit this file with tools that report file changes back into its
   transcript, since a later secret write would be reported with the contents. Set:
   - a lowercase `instance_id` and a `port` other than production's;
   - `expected_app_user_id` and `expected_organization_id` from the app's own
     client-credentials `viewer` and `organization`, after confirming the
     organization is Kuaiwa AI and the viewer is not production FarmBot;
   - an absolute `local_root` at a stable path outside every checkout and Codex or
     Claude worktree, since state inside a worktree is removed with it;
   - `repos` naming the real Kuaiwa-Network remotes, as in the template;
   - `slots: []` until a development Unity slot exists inside `local_root`.

   Unknown top-level keys are silently ignored, so check their spelling. Inside the
   `monitor` block, the monitor refuses them.
3. **Git and GitHub.** The instance uses the host's ambient Git and `gh` login, as
   production does; the real repositories need no dedicated token. Workers inherit
   that login and its write access.
4. **Worker runtime.** A controller runs one runtime. Switching is a profile edit
   and a restart of the settled controller; the marker does not record the runtime.
   - `codex` seeds each attempt's isolated `CODEX_HOME` with `~/.codex/auth.json`.
   - `claude` gives each attempt an empty isolated `CLAUDE_CONFIG_DIR` with no
     seeded credentials, so a worker reports "Not logged in". Export a long-lived
     token from `claude setup-token` as `CLAUDE_CODE_OAUTH_TOKEN` in the
     controller's environment; worker environments are copied from it. The token
     can wrap across terminal lines, and a one-line paste saves only part of it;
     test it with an empty `CLAUDE_CONFIG_DIR` before use. Copying Keychain
     credentials or `~/.claude.json` does not work.
   - `claude` cannot run `fix`, `feature` or `fgui`: the scheduler refuses repository-staged skills under
     it (the operating contract's Authority section). A `claude` instance still
     accepts and acknowledges a `fix` delegation, and the item then fails at launch;
     a live write-worker run needs `codex`.
   - kw_ops reaches Codex workers only. Export the profile's `token_env` variable in the wrapper
     that starts `serve`, never in the profile itself.
   - `feature` and `fgui` workers read the 策划案 with lark-cli as FarmBot's own Feishu app; set it up as
     [lark-cli for feature workers](#lark-cli-for-feature-workers) describes before enabling
     either opt-in worker.
5. **Endpoint.** Run
   `cloudflared tunnel --url http://127.0.0.1:<port> --no-autoupdate --protocol http2`.
   A quick tunnel's hostname changes on every restart and FarmBot never learns it,
   so paste the new `/webhook` URL into the TestBot app after each restart.
6. **Initialize.**
   - `python3 -m agent.service doctor --config /absolute/profile.json` is read-only
     and never creates a ledger. `config_unreadable` means the profile is
     incomplete; `ledger_unreadable` is expected before the first initialization.
     Its `skills` block lists the loaded and the enabled skills;
     `enabled_skills_invalid` means `serve` would refuse the profile's
     `enabled_skills`; `skill_runtime_unsupported` means the profile's `runtime`
     cannot launch an enabled skill, as `claude` cannot launch `fix`, so each of
     that skill's jobs would fail at launch. On a profile that enables `feature` it
     also reports `tools.feature`: `feature_toolchain_incomplete` names the tools to
     install first, and `lark_cli_unconfigured` means `serve` would refuse the profile.
     Enabling `fgui` adds `tools.fgui`: the selected native Python, Git LFS,
     pinned Pillow, prepared CJK font identity and the shared bot document reader.
     Its optional `tools.fgui.export` block verifies the four explicitly selected
     native publisher hashes. It does not execute the exporter, verify a license,
     start Unity or read the design document.
   - `python3 -m agent.service seed-clones --config /absolute/profile.json --from ~/WorkSpaces/Farm`
     creates `local_root`, `.controller.lock`, `environment.json` and the bare
     clones; there is no separate init command. Local checkouts that share history
     with GitHub avoid large GitHub fetches, which have truncated on the
     development Mac. A `farm-common` checkout seeds `common`.
   - The marker records the client ID, environment, instance ID, both pinned IDs
     and the resolved `local_root`, and must match exactly. Settle them before the
     first run. Never delete or edit a marker to make a profile start.
   - Do not probe with `python3 -m agent --db … status`: worker CLI commands
     construct a `Ledger`, which creates and migrates the database.
7. **Run.** Start `python3 -m agent.service serve --config /absolute/profile.json`
   in the foreground from the development checkout, through a small wrapper that
   sets any needed environment such as the Claude token. `install-launchd` writes
   only `PATH` and `HOME` into its plists, so it would drop those variables.
   `GET /health` only proves the handler answers; an unsigned `POST /webhook`
   returning 401 `invalid signature` shows that the tunnel reaches the receiver.

   To watch TestBot from another device on the office network, set the profile's
   `monitor` block to `{"bind": "0.0.0.0", "port": 8781}` (the template's binds
   loopback only) and run
   `python3 -m agent.service monitor --config /absolute/profile.json` beside the
   controller; see the README's office status monitor section. macOS may ask
   whether Python may accept incoming connections. Edit the profile with a command
   that prints nothing from it, never with a tool that echoes file contents.

Keep the game/server test environment in mind as well. The config's
`default_server_environment` is descriptive; it does not enforce server isolation.

## lark-cli for feature workers

A `feature` or `fgui` worker reads the 策划案 with lark-cli as FarmBot's own read-only Feishu app, never with a
personal login (spec §5.4, D12). The host config names the lark-cli profile that holds the app, and
FarmBot never stores the app ID or secret.

How lark-cli 1.0.82 keeps a profile on macOS, from its source: the profile list is
`$HOME/.lark-cli/config.json`, and each secret is a file under
`$HOME/Library/Application Support/lark-cli/`, encrypted with one master key. lark-cli reads that key
from `master.key.file` beside the secrets first and otherwise from the login Keychain, where one item
serves every lark-cli store of the macOS user whatever `HOME` says. The Codex worker sandbox blocks
the Keychain. `lark-cli config keychain-downgrade`, lark-cli's own fix, copies the Keychain key into
`master.key.file`: every secret in that store, a personal `--as user` login included, then becomes
readable by every sandboxed worker on the host, `fix` and chat workers included. A fetch as bot keeps
its token in memory and skips its auth log when it cannot write it, so a worker only reads the store.
A lark-cli command that can write its config directory, as any command run outside a sandbox can,
also fetches API metadata from Feishu at startup unless `LARKSUITE_CLI_REMOTE_META=off` is set; the
commands below set it. lark-cli also takes credentials from `LARKSUITE_CLI_APP_ID`,
`LARKSUITE_CLI_APP_SECRET` and its access-token variables before any profile; FarmBot withholds
inherited values, and `LARKSUITE_CLI_PROXY_KEY`, from every worker. Only the explicit
[environment variant](#feature-only-environment-credentials) then supplies its configured bot credentials
to a Codex Code/UI document-reading worker.

Measured on TestBot's Mac on 2026-10-01, lark-cli 1.0.82 and codex-cli 0.156.1, with a dummy profile,
inside `codex sandbox -P :workspace` and with no Feishu call:

- the operator's own store: cli_version pass; config_file pass; app_resolved fail;
- a FarmBot-only lark-cli home with its own `master.key.file`, which the sandboxed command could not
  write (exit 1): cli_version pass; config_file pass; app_resolved pass; bot_identity pass; user_identity warn; identity_ready pass; endpoint_open skip; endpoint_mcp skip; a dry-run fetch as bot exited 0, and the same fetch
  with `--as user` under strict mode exited 2;
- environment credentials (`LARKSUITE_CLI_APP_ID`, `LARKSUITE_CLI_APP_SECRET`) with no store: a dry-run
  fetch as bot exited 0, and with no credentials 3.

The operator's profile and user-login counts, and absence of a file master key in the personal store,
were unchanged afterwards. The temporary dummy store was removed. This measured credential access,
not a real Feishu fetch or Windows setup.

| Option | What a sandboxed worker can read | Decision |
|---|---|---|
| A FarmBot-only lark-cli home with its own key (`lark_cli.home`) | the FarmBot app's secret only, readable by any worker on the host; only the enabled `feature`/`fgui` AUTHORITY grants its use | chosen for TestBot |
| A host account whose lark-cli store holds only the FarmBot profile (no `home`) | that account's whole store, which must never hold a personal login | for a host that runs FarmBot as an account of its own; a Windows option |
| Downgrading a store that holds a personal login | every profile in it, the personal login included | rejected, unless the operator accepts it knowingly |
| Environment credentials in the enabled Code/UI worker's shell | the FarmBot app's secret, in every command's environment | explicit `app_id`/`secret_env` variant; verify the actual worker before enabling it |
| lark-cli's sidecar auth proxy | a signing key, not the secret | not needed; a host service to supervise |
| A tenant token the controller mints | a token for about two hours | rejected: shorter than a 10-hour attempt |

Set the home up once the FarmBot app exists (spec §10), from an interactive shell, never inside a
worker:

1. Choose an absolute directory outside every checkout, `local_root`, your home directory and every
   temporary directory (FarmBot validates `local_root`, the service home and temporary directories), such as
   `/Users/Shared/farmbot-lark-cli` on macOS; `mkdir -m 700` fails if someone created it first. Give it
   its own key before any secret: lark-cli encrypts with the file key only when it finds one, and
   otherwise with the Keychain key that every store of this macOS user shares.

   ```bash
   LARK_HOME=/Users/Shared/farmbot-lark-cli
   mkdir -m 700 "$LARK_HOME"
   STORE="$LARK_HOME/Library/Application Support/lark-cli"
   mkdir -p "$STORE"
   chmod 700 "$LARK_HOME/Library" "$LARK_HOME/Library/Application Support" "$STORE"
   (umask 077 && python3 -c 'import os, sys; sys.stdout.buffer.write(os.urandom(32))' > "$STORE/master.key.file")
   ```

2. Add the profile with the app's ID, typing the secret at the hidden prompt so that it reaches no
   argument list, history or log, and allow only the bot identity:

   ```bash
   read -rs SECRET && printf '%s\n' "$SECRET" | HOME="$LARK_HOME" LARKSUITE_CLI_REMOTE_META=off LARKSUITE_CLI_NO_UPDATE_NOTIFIER=1 lark-cli profile add --name farmbot --app-id APP_ID --app-secret-stdin; unset SECRET
   HOME="$LARK_HOME" LARKSUITE_CLI_REMOTE_META=off LARKSUITE_CLI_NO_UPDATE_NOTIFIER=1 lark-cli --profile farmbot config strict-mode bot
   ```

3. Check it offline from inside the worker sandbox, from a working directory outside the home.
   `app_resolved` and `bot_identity` must pass; the output shows the app ID, so keep it out of chat
   and commits.

   ```bash
   codex sandbox -P :workspace -C "${TMPDIR:-/tmp}" -- env HOME="$LARK_HOME" LARKSUITE_CLI_REMOTE_META=off LARKSUITE_CLI_NO_UPDATE_NOTIFIER=1 lark-cli --profile farmbot doctor --offline
   ```

4. Put `"lark_cli": {"profile": "farmbot", "home": "/Users/Shared/farmbot-lark-cli"}` in the private
   profile. Enabling `feature` is a separate, operator-approved step.

If a sandboxed worker could reach a personal `--as user` login with this setup, do not enable
`feature` on that Mac until the setup is fixed or the operator has accepted and recorded that
exposure.

Never run `lark-cli config keychain-downgrade` for your own store on a FarmBot host, and never export
lark-cli credentials, or `LARKSUITE_CLI_CONFIG_DIR`, in a shell startup file: FarmBot removes the
credential variables and the config-directory override after per-worker overrides, preserving the
selected store; it cannot remove what a worker's shell sources. Diagnostic and Unity children get
the same removals, plus the configured kw_ops token. On Windows, lark-cli keeps every profile's secret in the user's registry, protected per
user with DPAPI, so a separate home isolates nothing there and FarmBot refuses one. The currently
supported Windows store-based approach uses an account whose lark-cli store holds only the
FarmBot bot profile, with no personal profiles or logins. A dedicated service account provides
separate-account isolation. The operator chose the current Windows account for the 2026-10-03
verification, with a FarmBot-only store. Local setup and offline host checks passed; actual
worker credential compatibility remains unresolved, with detailed diagnostics retained privately.
A passing offline profile check does not certify worker access or real Feishu reads. Isolated
worker/store checks and credential delivery remain pending before `feature` is enabled.
At the verified merged candidate `707ea87`, all workers withhold credential variables. The
operator subsequently authorized the feature-only environment variant implemented in
[#81](https://github.com/Kuaiwa-Network/farm-linear-agent/pull/81), merged as `e8406d5` and described
below. Actual Windows worker acceptance remains pending before live use.
The native Windows read-only toolchain inventory does not verify the store or real bot fetching;
see the [measured record and release prerequisites](superpowers/spikes/2026-10-03-native-windows-offline.md).

### Feature-only environment credentials

This originally Code-only route also serves the explicitly enabled `fgui` document
reader in Phase D. The heading remains for existing links. Fix/chat remain withheld;
extending the grant does not prepare an account, credential loader or live host.

The alternative private block is `"lark_cli": {"app_id": "cli_example", "secret_env": "FEATURE_FEISHU_SECRET"}`.
It names an app and a controller environment variable, never an inline secret. It cannot contain
`profile` or `home`. Use a distinct uppercase source name; runtime/config selectors and the kw_ops
token source are refused. Supply the value privately to the controller process through the host's
credential management. This change installs no credential loader and does not copy a Windows DPAPI
profile into worker accounts.

Only Codex `feature` and `fgui` workers receive the configured ID and secret as `LARKSUITE_CLI_APP_ID` and
`LARKSUITE_CLI_APP_SECRET`, plus forced `LARKSUITE_CLI_STRICT_MODE=bot`. The source alias is removed
from every worker, Unity run, Editor launch and diagnostic child, even after environment overrides.
Other workers retain no lark credential; user/tenant access tokens, proxy keys and config-directory
overrides are still withheld. Code/UI launches remove the auth-proxy override too. Worker prompts
contain only `tools.lark_cli: {"authentication": "environment"}` and authorize the skill's scoped reads
(`feature` also allows `sheets +workbook-info` and `sheets +cells-get` for a linked spreadsheet),
with `--as bot` and without `--profile` or `HOME`. Missing credentials are an unavailable tool;
there is no fallback to a profile. Secret values never enter the generated Codex config or process
record, and shell snapshots are disabled. Every command in the authorized document-reading worker can read
the bot secret, so the app's actual Feishu permissions remain a release check.

A Code card may link a Feishu spreadsheet as its design. The existing document-reader app needs
the read-only `sheets:spreadsheet:read` scope and access to that spreadsheet. Applying a scope and
publishing the app's permission update are operator work, separate from the bot's deployment.
The Code worker reads all tabs at one verified revision through the two scoped read commands;
it cannot search Feishu, write cells or change permissions. A screenshot of a document title alone
is not a link: an operator must supply the resolved link in the card or its conversation before intake.

`doctor` checks the source's presence in its own process and lark-cli's version, without passing the
secret to probes or reading the profile store in this mode. This establishes neither live Feishu
access nor delivery into a real worker. Native Windows isolated Codex homes explicitly select the
host's `codex_windows_sandbox`: `"elevated"` by default, or an explicitly chosen `"unelevated"`
restricted current-user backend. Verify the selected worker context separately. Unelevated mode
has weaker read and network isolation and does not isolate the user's credentials; changing
backends does not establish that the app's permissions or credential route are safe. There is no
automatic fallback to another backend or to disabled enforcement.
Retain dummy offline evidence, then verify the selected app's read permissions and one operator-chosen
document/attachment inside an actual worker before enabling feature. Generator acceptance remains
a separate check. No state schema changes; settle workers and restore a profile block before a
rollback to code that rejects this variant.

## Keeping production untouched

Do not reuse production's credentials, endpoint, port, state root or service
labels. A code change or a live test does not authorize deploying, restarting or
reconfiguring production, or changing its webhook. Do not copy the development
ledger, claim tokens, memory or configuration into production.

## Recording a test run

Real issues keep changing, so note the issue's last update you tested against.
For comparisons, record each run's FarmBot commit, actual target-code commits,
worker runtime/model, Python/Unity/MCP versions, job ID and evidence paths.
Existing memory can influence a run; record its relevant snapshot, or use a
separate disposable state root for a clean comparison.

## Using a successful fix

A development bot's fix is already a draft PR on the real issue's branch. Review
it like any other PR; FarmBot never merges, and it does not change the issue's
status or assignee. When the run exposed a FarmBot defect, fix FarmBot in its own
change. Deploying a new FarmBot revision remains a separate operation.
