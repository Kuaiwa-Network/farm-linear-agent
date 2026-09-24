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
  only on UI delegation or an @mention of its own app, so the operator's choices
  are the test scope.
- Both bots use `farmbot/<lowercase-key>` branches in the same repositories.
- The development bot's comments, agent sessions, `needs-more-info` labels,
  branches and draft PRs are real and visible to the team.
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
- Delegate from the Linear UI; delegation set through the API creates no agent
  session. In the @-autocomplete, pick TestBot, not FarmBot.
- FarmBot never closes PRs or deletes remote branches. Close an unwanted draft PR
  and its branch yourself.

## What is available now

- Offline unittest fixtures use temporary state, local Git repositories, fake
  workers and stubbed Linear/Unity integrations.
- The service propagates the selected config file to initial and resumed workers;
  launchd installation preserves environment-selected configs as well.
- Explicit profiles verify bot/workspace identity, bind a fresh state root, hold a
  controller lock and reject fake/stub integrations for live operation.
- `issue_prefix` selects the team key accepted for publishing; it defaults to
  `FARM`, the key of the real 农场 issues a development profile tests.
- [Mac/Windows CI](ci.md) runs the offline suite with Python 3.13 and records evidence.
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
   - kw_ops reaches Codex workers only. Export the profile's `token_env` variable in the wrapper
     that starts `serve`, never in the profile itself.
5. **Endpoint.** Run
   `cloudflared tunnel --url http://127.0.0.1:<port> --no-autoupdate --protocol http2`.
   A quick tunnel's hostname changes on every restart and FarmBot never learns it,
   so paste the new `/webhook` URL into the TestBot app after each restart.
6. **Initialize.**
   - `python3 -m agent.service doctor --config /absolute/profile.json` is read-only
     and never creates a ledger. `config_unreadable` means the profile is
     incomplete; `ledger_unreadable` is expected before the first initialization.
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
