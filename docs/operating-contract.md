# FarmBot operating contract

Current behaviour implemented in this repository. Closure cleanup requires deployment of this revision. Rewritten in place whenever behaviour changes; the
design rationale lives in `docs/superpowers/specs/`.

## Host configuration

File selection is explicit `--config`, then `FARMBOT_CONFIG`, then the checkout's
default `.local/agent/config.json`. The loader expands `~` and captures an absolute
path. A service built from that loaded config passes its path to every worker
attempt, including resumes, overriding a conflicting inherited `FARMBOT_CONFIG`
without changing the host process's environment. The launchd installation command
also embeds the selected absolute path when selection came from the environment
or default. Directly constructed in-memory configurations have no source file;
test fixtures must still supply their own worker environment.

The host loader secures the config's permissions on POSIX. Worker CLI and API
consumers read it without changing permissions, so a selected config can live
outside worker-writable state directories without granting write access to it.

This pins file selection, not file contents. Restart a settled service after editing
the file so the controller and its workers load the same settings. State location
still comes from `local_root`; configure an absolute path for each installation.
These rules do not by themselves restrict credentials, repositories or live issues.

Codex workers default to `gpt-6-sol` at `xhigh` reasoning. Private
`codex_workers` entries override either setting per skill; unspecified settings
retain the FarmBot default. The selected settings are written into each new or
resumed worker's isolated Codex home. Claude workers do not use these settings.

Private `enabled_skills` lists the skills an instance runs, from those in its checkout's `skills/`;
without the key every one runs except an opt-in skill, one whose `skill.json` sets
`"opt_in": true`, which runs only where the list names it. A checkout that ships an opt-in skill
therefore starts nothing new until an operator names it. The list must include `chat`, the
conversation every other route falls back to, and `chat` cannot be opt-in. A name the checkout
lacks, or an enabled skill the dispatch AUTHORITY does not cover, is a configuration error: `serve`
and `enqueue` stop, and `doctor` reports `enabled_skills_invalid`. The receiver routes only to
enabled skills, and `enqueue`, `request-repair` and `resume-work` refuse the others; `enqueue` says
when the refused skill is opt-in. A queued item whose skill is loaded but not enabled fails before
launch with an error activity naming 重试; `retry` or a requested continuation brings it back once
the skill is enabled. An item whose skill the checkout lacks, which only a rollback leaves, stays
queued as before. `doctor` reports `skills` with the loaded and enabled names, so an opt-in skill no
list names shows as loaded and not enabled, and `skill_runtime_unsupported` for enabled skills the
configured `runtime` cannot launch (see Authority). Restart a settled service after changing the
list; an older revision ignores the key and runs every skill. An older revision also refuses a
manifest with `opt_in` or `exclusive`, keys it does not know, so a rollback deploys the older code
and skill files together, as always.

Explicit profiles select `environment` (`development`, `production`, or `offline`)
and a lowercase `instance_id`. Existing configs default to `legacy` for compatibility.
Live profiles require `expected_bot_name`, pinned `expected_app_user_id` and
`expected_organization_id`, and a dedicated absolute `local_root`. API identity must
match all configured fields. Live profiles reject the fake runtime and Linear stub
selector; offline profiles require both. Offline mode still needs local repository
and Unity fixtures and is not a network sandbox.

`serve` and `seed-clones` acquire a nonblocking OS file lock on the root before
building state. Explicit profiles bind a fresh root using `environment.json` with
nonsecret identity fields. Mismatched, corrupt or unmarked existing runtime state is
refused; it is never automatically adopted. State/Unity paths, including default slot
folders, must resolve inside the root. Shutdown retains the lock until controller
threads finish. Maintenance and worker CLI checks reject incompatible ownership
before opening a ledger; worker CLI additionally checks the selected profile's DB.
Config-free legacy CLI fixtures remain supported for unmarked databases.

These checks prevent accidental profile mixing; filesystem permissions and credentials
remain the security boundary. Do not delete the marker or downgrade code to bypass it.
Migrate an existing production root only as a separately planned, backed-up operation.
The marker adds no database migration, and legacy production settings are unchanged.

`issue_prefix` defaults to `FARM`, which a development profile testing real 农场 issues
keeps; a profile serving another team uses that team's key. Writable worktrees
and publication verification share this policy and use `farmbot/<lowercase-key>`
with an optional suffix. An unrelated suggested Linear branch becomes the canonical
branch. Repository identity, private/write permission, protected-branch and exact
PR verification still apply. The prefix is a publishing rule, not a team admission rule.
The pinned app/workspace IDs select which app's sessions an instance accepts; within
the workspace, only UI delegation or an @mention of that app starts work. No team,
project or issue allowlist exists, so a development bot sharing the production
workspace is scoped by the operator's choice of issues.

Explicit launchd profiles use `com.kuaiwa.farmbot.<environment>.<instance_id>` labels;
legacy labels remain unchanged. Installation writes plists but does not load them.

The HTTP receiver binds the loopback address without reverse-DNS lookup. Host DNS
availability is not required to start the local listener.

## Triggers

Linear text names the instance by its configured `expected_bot_name` (default `FarmBot`): the
receiver's acknowledgements and error activities, launch-failure notices, and the worker comments
rendered from `references/comment-templates.md`, whose `<bot_name>` workers fill from the launch
message's `bot_name`. A development profile whose app is TestBot therefore speaks as TestBot in the
shared workspace. The tables below use the production name. Git commit identity, launchd labels,
the ledger marker and the `/health` body are unchanged.

Issue detail reads retry a timed-out Linear request up to three times before the receiver reports
an error activity. The retry applies to the current read page only; GraphQL mutations are never
replayed after a lost response. A prompt that still fails must be retried in Linear.

Linear signs upload URLs (`uploads.linear.app`) with a query string that changes between reads. An
issue read removes that query string and any fragment, and leaves the Markdown, JSON, HTML (an
entity such as `&quot;` right after the URL included) or sentence around the URL as written.
Earlier revisions removed everything from the first `?` or `#` after an upload URL up to the next
whitespace or `)`, such as the rest of a `<linear-image>` block or of a sentence that went on after
a question mark. Deploying this revision therefore changes, once, the fingerprint of every tracked
issue whose description or comments lost text that way, at the issue's next full read (a worker's
`fetch-issue` or an agent-session event; status checks do not count). Every unfinished job on such
an issue whose claim is taken before that read then requeues at `finish` (its next attempt redoes
the work and posts its start comment again), has its repository handoff refused and cannot
register a PR that Linear attached before its checkpoint, unless its worker re-reads the issue and
revalidates. That includes a job that is only queued, waiting for a resource or between repository
stages at the deploy: nothing re-reads the issue before a claim, so its own first `fetch-issue`
stores the new text. Settle or cancel unfinished jobs on affected issues before deploying, or
accept one requeue and a repeated start comment for each. Rolling back changes those fingerprints
back once, with the same effect. Another app's comments, which earlier revisions counted as a
person's, are a bot's from this revision, so an issue another app commented on changes fingerprint
once in the same way, and back again on a rollback.

| You do | FarmBot does |
|---|---|
| Assign (delegate) an issue labelled Bot/修改 to @FarmBot | starts a `fix` work item when this instance runs `fix`, whatever else the card carries and whatever text comes with it (otherwise the read-only conversation that says so); first activity within 10 s; posts 「👀 <bot_name> 已开始处理」 (「👀 FarmBot 已开始处理」 in production) once the worker claims |
| Delegate an issue labelled Bot/UI or Bot/Code | starts `fgui` or `feature` when this instance enables it (neither exists yet); a `feature` session gets no Farm-Client target, and no activity in it, nor the reply to a mention forwarded to its job, carries a target line; otherwise, and for an unknown Bot child or two, the read-only conversation, whose first activity says what this instance runs |
| Delegate an issue without a Bot label, whatever its Bug, Improvement, Feature or 部门 labels | starts the read-only conversation; on an instance that runs `fix`, its first activity says the card has no Bot label, that a reply such as 「修复」 starts a fix, and that Bot/修改 set before delegating starts one directly. It investigates, answers or clarifies intent. A standalone `修改`, `UI` or `Code` label outside the group routes like any other label |
| Reply in a delegation session that never had a work item, for example after another session's work declined it | while the issue is still delegated to FarmBot, routes again on its current labels with your reply as the delegation's text: a Bot child whose skill this instance runs starts its worker; otherwise, the read-only conversation |
| Ask for a fix, a change or the card's feature in a conversation (a reply, or @FarmBot) | when the issue has recorded delegation and is still delegated to FarmBot, continues the delegation's earlier `fix` or `feature` job whatever the label now says, on an instance that runs its skill; with no earlier job, starts the workflow the Bot label names on an instance that runs it, `fix` with Bot/修改 or no Bot label and `feature` with Bot/Code; with Bot/UI, Bot children that name no workflow, or a workflow this instance does not run, the conversation says why nothing starts |
| @FarmBot in a comment or the session | interprets intent in read-only execution; a mention never starts write work itself |
| Reply in a session while a worker runs | the text reaches the worker at its next checkpoint |
| Reply to a FarmBot question | the parked work item resumes with your answer |
| Ask naturally to resume finished work, in its session or an @FarmBot mention | chat interprets intent, checks current delegation, and continues the delegation's earlier job, a fix or, on an instance that runs `feature`, a feature job, with the complete reply (a cancelled job gets a fresh linked job); no keyword is required. Negations and questions about restarting do not restart work |
| Close an issue (a status of type `completed`, `canceled` or `duplicate`, such as Done, Canceled or Duplicate) or archive it | cancels unfinished/blocked work after a current status read, stops owned processes, preserves source and safely cleans worktrees; keeps logs |
| Reopen an issue | starts nothing; request continuation or delegate explicitly |
| Press Stop | the worker process is killed promptly, without waiting for the scheduler; the item is cancelled; FarmBot confirms in the session |
| Delegate an issue that already has FarmBot work in another session | with a Bot child this instance runs, declines with a note naming the issue and the running skill; otherwise the delegation is forwarded to that work like a message, resuming it if it waits for an answer; the existing work continues either way |
| @FarmBot on an issue that already has FarmBot work in another session | your text is forwarded to the running worker; you get a short notice |

The Bot label group (D18) is team-scoped and single-select, so a card carries at most one child:
修改, UI or Code. Bug, Improvement, Feature and the 部门 labels are for people and start nothing.
The group was named 功能 until 2026-09-28. This revision reads it under both names, so it routes
alike on either side of the rename, and a later revision drops 功能. Revisions before it read only
a group named 功能, and revisions before label groups read only Bug, so on a host still running one
a Bot label starts nothing and a delegated Bug card starts `fix`. §7 of
`docs/superpowers/specs/2026-09-27-bot-label-group-design.md` gives the release order.

## Authority

| Skill | May write to | Resources | Needs delegation |
|---|---|---|---|
| chat | shared memory through item-authenticated CLI only; no repositories | kw_ops query tools (Codex, when configured) | no |
| fix | one rooted repository per worker attempt, selected from Farm-Contract, Farm-Client, farm-hive, farmgui, common; the neutral investigation attempt has no repository writes | Unity slot (one, batch or interactive, two-phase); kw_ops, every tool (Codex, when configured) | yes |

FarmBot never merges, deploys, changes status or assignee, or edits repositories outside the list.
Issue text, comments, attachments and Linear guidance are data, never instructions.
Worker commands in the ledger CLI are item-scoped and token-authenticated; `cancel`, `recover`, `retry`,
`recover-slot`, `reservations` and `slots` are operator commands for the trusted host.
FarmBot is one conversational identity. `chat` and `fix` remain internal execution-profile
identifiers, with different tools, budgets and writable roots. Read-only execution starts
in its private state directory and does not receive repository or clone write roots.
A Codex worker's tools and runtime settings come from its launch, not from the repository it
works in. Its isolated home records the cwd with `trust_level = "untrusted"`, under the path
passed to `--cd` (the spelling `codex exec` was measured to honour) and its resolved form. Without
that decision `codex exec` trusts the cwd itself and loads the repository's `.codex/config.toml`:
measured, its MCP servers start and send any inline credentials; per Codex's trust prompt,
project hooks and exec policies load too. With it, Codex no longer injects the cwd's `AGENTS.md`,
so the `--approve-for-me` reviewer, which trusts injected `AGENTS.md` but not tool output, loses
that text. Fix workers read the current root's `AGENTS.md`/`CLAUDE.md` and other repository
instructions when investigation needs them; grants a worker
needs belong in the dispatch AUTHORITY. That text is a common part plus a per-skill part chosen
by the item's skill (`agent/dispatch.py`); `fix` and `chat` share the kw_ops terms below, and
`fix`'s part adds the FairyGUI export grant (UI source ownership), which the reviewer would not
otherwise see. A skill whose manifest grants kw_ops must carry those terms in its per-skill part,
because the grant comes from the manifest and its limits from the AUTHORITY; a test checks every
loaded skill. Building the launch message refuses a skill with no per-skill entry, so its job
fails at launch and no worker starts: SKILL.md text cannot stand in for a grant. Repository
skills under `.agents/skills` and
`.codex/skills` still load, and workers inherit the service's `HOME`, so the host user's
`~/.agents/skills` are visible too. Measured with codex-cli 0.155.1 on macOS; the trust fix was
re-checked on 0.156.1; Windows is unverified.
A Claude worker's settings and MCP servers also come from its launch, whatever its cwd. It runs
with `--setting-sources user`, so of the user, project and local settings it reads only the user
settings in its isolated `CLAUDE_CONFIG_DIR`, where FarmBot seeds none, and with
`--strict-mcp-config`, so only the injected `mcp.json` supplies MCP servers. `claude -p` skips
the workspace trust dialog. Without the first flag it loads the cwd's `.claude/settings.json` and
`.claude/settings.local.json`: measured, their `apiKeyHelper` ran, their hooks ran (`SessionStart`
and `UserPromptSubmit` before the first model request, tool hooks around a tool call), and their
`env` reached the worker and its tools, so an `ANTHROPIC_BASE_URL` they set received the worker's
requests and OAuth token. A repository worktree's settings loaded, and so did a
`.claude/settings.json` in a job's state directory: the chat cwd, which the worker can write and
every attempt of the job shares. Without the second flag, a repository `.mcp.json` server that
the repository's own settings approved started. The first flag also stops Claude injecting the
cwd's `CLAUDE.md` (with its `@` imports, `CLAUDE.local.md`, `.claude/CLAUDE.md` and
`.claude/rules`) and loading the cwd's `.claude` skills, agents and commands and the skills and
agents of `--add-dir` directories; FarmBot's skills reach workers by path. Settings in `--add-dir`
directories and the service user's `~/.claude` did not load with or without the flag. Measured
with Claude Code 2.1.229 on macOS; Windows is unverified.

kw_ops, the GM backend of the test game environment, is a standing tool grant that a skill
manifest declares in `mcp`. `kw_ops` gives fix workers every tool, and `kw_ops:read` gives chat
workers the query tools listed in `agent/kw_ops.py`, as the server's `enabled_tools`; the skill
loader rejects any other `mcp` entry at startup. The private host profile's `kw_ops` block names
the URL and `token_env`, the environment variable that holds the token. The name cannot be one
FarmBot sets for workers, such as `FARMBOT_DB` or `CODEX_HOME`. The token stays in the
controller's environment, which a Codex worker with kw_ops inherits, and the worker's CLI reads it
by name (`bearer_token_env_var`). That worker's isolated home lists the variable under
`shell_environment_policy.exclude` and sets `features.shell_snapshot = false`, because codex-cli
0.156.1 re-exports excluded variables from its shell snapshot (measured on macOS; Windows is
unverified). Every other worker has the variable removed from its environment. Claude workers get
no kw_ops. The URL must use HTTPS, except HTTP on loopback; an existing remote HTTP configuration
must be changed before restarting on this revision. FarmBot learns the variable's name only
from the block, so add the block and the variable together, and remove them together: a variable
set without the block reaches every worker.

`tools.kw_ops` in the launch payload states the access, `full` or `read`. When kw_ops is not
configured, its variable is unset or blank, or the runtime is unsupported, it says why instead and
nothing is injected. Codex waits about a second for kw_ops before the worker's first model
request, and a worker whose kw_ops has not started by then still runs without it: a refused
connection logs an error in the worker's stderr, and a slow or silent kw_ops logs nothing
(measured with codex-cli 0.156.1 on macOS; Windows is unverified). In both cases the payload still
states the access, and the worker reports the missing kw_ops as a verification gap when the issue
needs it. Configure kw_ops only when every target it lists is a test server, using a kw_ops
operator whose permissions cover only test servers; FarmBot does not scope servers. The kw_ops
terms of the fix and chat AUTHORITY, which tell workers that every listed server is a test server,
limit full access to the issue's reproduction or verification, and require every state-changing
call to be recorded.
Read-only access rests on FarmBot's allowlist, not on kw_ops.

The token variable must be set only in the controller's environment, in the wrapper that starts
`serve`, never in a shell startup file such as `~/.zshenv`: the removal and the exclusion apply
only to the environment a worker inherits, and worker shells may source startup files. On Windows,
provide it only in the controller service's process environment, not as a persistent user or
machine environment variable, which lives in the registry where same-user processes can likely
read it (unverified). FarmBot starts batch and interactive Unity Editors with a copy of the
controller's environment that excludes the configured token variable while retaining other
variables needed for licensing. `doctor` reports `tools.kw_ops` with `configured` and, for a
configured host, `token_env` and
`token_set_in_doctor_environment`, which reflects doctor's own environment, not the running
controller's.

An active repair can answer questions directly. Free-text intent is interpreted by the
current worker; QA/retry words do not dispatch work by themselves. Only a child of the Bot
label group chooses a workflow: Bot/修改 starts `fix`, Bot/UI `fgui` and Bot/Code `feature`,
whatever text accompanies the delegation, and that text is its first session message. A
delegation without a Bot label opens the read-only conversation. Earlier revisions started
`fix` on a Bug card delegated without text; D18 ended that shortcut, because a bug can be
designer-only work that needs no code change.

`request-repair` checks a fresh Linear snapshot, a live read-only claim, the latest session message
and a recorded delegation session on the same issue. It atomically retires that claim and either
continues the delegation's earlier job or creates the first job the card's Bot label names under the
recorded delegation: a first fix takes that session's target, and a first `feature` job none. The
earlier job is the latest `fix` or `feature` job of a delegation session on the issue, this
conversation's session first (`resumable_work` in `issue-context`); an `fgui` job is not continued
from a conversation. A request continues that job whatever the card's label now says, and only on an
instance that runs its skill: elsewhere `request-repair` and `resume-work` refuse, and no other
skill's job starts in its place. A mention alone grants no new authority. The card's Bot label
decides what a request may start when there is no earlier job (D18 f), on an instance that runs it:
with Bot/修改 or no Bot label, `fix`; with Bot/Code, `feature`. On a card with Bot/UI it refuses a
first job, saying that `fgui` work starts when the labelled issue is delegated or, when this
instance does not run `fgui`, that it does not yet; on a Bot/Code card where this instance does not
run `feature` it says that; an unknown Bot child or two name no workflow and are refused too. Both
commands are refused before Linear is read on an instance that runs neither `fix` nor `feature`; one
that runs `feature` but not `fix` reads the card, then refuses a first fix. The acknowledgement in
the session reads 「已排队开始或继续修改…」 for a fix and 「已排队开始或继续这项工作…」 for other work. FarmBot never sets a
Bot label; the one label it writes is `needs-more-info`. `resume-work` remains a resume-only
compatibility command. Cancelled jobs stay cancelled and receive a fresh successor ID; other
terminal jobs keep their ID. A job a conversation starts or continues begins at its initial root,
which for a fix is the neutral investigation. The launch includes one bounded `prior_context`
summary from the investigator or the current fix checkpoint; replies, questions and the full prior
findings remain available in `issue-context`. Both are recall that the new worker must verify. A
`feature` job starts rooted in Farm-Contract, so its launch carries its own checkpoint handoff, not
the conversation's summary, which it reads in `issue-context.conversation_history`. Late messages
and Stop from a source conversation follow its active handoff. Merely observing changed issue
text/comments does not start work. Historical context is recall, not a current request.

Repository stages follow the skill manifest. A skill whose `skill.json` sets `"staged": true` writes
one repository per worker attempt, its current root: the item's `root_repo` or, while that is unset,
the manifest's `initial_root`. Without an `initial_root`, an attempt with no recorded root is
neutral: it runs in the private state directory and writes no repository. The loader refuses a
manifest key it does not know, so a misspelled stage key cannot load as that key's default. `fix` is
staged with no initial root, so a fix begins neutral with read access to all five worktrees. The
pinned Farm-Client target supplies a Unity baseline, not the investigation root. To edit or use
repository-specific skills, the worker saves a current checkpoint and calls `handoff-repository --to
REPO`, where REPO is in the manifest's `writes` and is not the current root. The CLI verifies the
manifest's stage rule, the configured host ledger and repositories, and fresh Linear delegation,
then revokes the claim. The controller stops the old worker, requires process-tree teardown evidence
and rechecks REPO against the manifest before switching `root_repo` and launching a fresh Codex
worker rooted at REPO; until then the handoff stays pending. The same item, Linear session, branch,
checkpoint and PR history continue. The new worker can write and verify publication only for its
root repository; other worktrees remain read-only. Changing cwd in one worker does not switch
instructions or write authority. `retry`, a chat-requested continuation and the successor of a
cancelled job start again with no recorded root, so the next attempt begins at the initial root,
which for a fix is the neutral investigation and for a skill with an `initial_root` that repository.
Staged skills require Codex's explicit `workspace-write` sandbox; the Claude fallback has no
equivalent repository write boundary and is refused for them. A `claude` host still starts with a
staged skill enabled and queues its jobs; each fails at launch with an error activity, and `doctor`
reports `skill_runtime_unsupported` naming the runtime and those skills. A Contract-root worker
follows Farm-Contract's OpenSpec instructions and its Superpowers restriction. Consumer workers use
their own repository rules.

A change to the issue during an attempt (title, description, attachments, or a comment that is
neither a bot's nor FarmBot's own, as People says) refuses that attempt's repository handoff and
the registration of a PR Linear has already attached, and makes `finish` requeue the item for a
fresh worker. A worker that has read the change in `issue-context` can call `revalidate` with
`issue-context.fingerprint`, the fingerprint of the snapshot it read: the ledger accepts only the
fingerprint it currently stores, moves the claim onto it and records both fingerprints in `audit`.
The handoff saved before must be saved again, and a later change still refuses the handoff and
requeues `finish`. A PR that Linear attached before the claim or its last re-read is issue input:
only a claim that revalidated since may register it as this job's output, and only if nothing else
changed. The additive `work_items.revalidated_fingerprint` column records a revalidated claim;
older code ignores it.

A checkpoint may also carry a validated `plan` for work that spans stages and days (keys and bounds in
`references/worker-cli.md`). The ledger carries it forward when a checkpoint omits it, shows it in
`issue-context`, and hands the nearest predecessor's plan to a successor as `recovery.plan`. It is recall
that the worker verifies, never authority, and it is not a handoff. Older code keeps a `plan` key only
until its next checkpoint that omits it, and never validates one.

A fix worker may update Farm-Contract in its Contract-root attempt for a confirmed requirement of
the issue, a defect or a requested change, before the affected implementation. Uncertain behaviour
or missing information is a question in Linear via `await-input`, which adds `needs-more-info`,
emits the elicitation and parks the item. A reply resumes it; insufficient answers lead to another
question. The label is not automatically removed just because a reply arrived. Every question
elicitation adds it, including chat and intake. `await-input --reason waiting` parks the same way
without the label, for a pause that waits on a human step elsewhere rather than on an answer; the
checkpoint records the reason as `pending_reason` beside `pending_question`. `await-input` refuses
while the item holds or awaits a Unity reservation. Status and assignee remain unchanged. Contract
access does not bypass generator requirements or add Unity export tools.

Existing hosts must add `Farm-Contract` to their private `repos` configuration and seed its bare clone
before enabling this fix manifest. New configurations include its GitHub remote by default.
The additive `root_repo` and `next_root_repo` columns preserve existing ledger rows. Settle
running fix workers before deploying this behavior; a retry with no root starts at neutral
investigation. Do not roll back during a pending repository handoff: older code cannot honor
its process-teardown fence or stage publishing restriction.

CLI `cancel` revokes the claim immediately; the next scheduler tick stops owned worker/batch
processes. Linear Stop and closure reconciliation revoke the claim before signalling. A pending
batch launch is fenced. A cancelled job never becomes queued again: explicit authorized `retry`,
`resume-work` or `request-repair` creates a fresh linked job, and so does delegating the issue again
when the latest job of the skill it routes to (any write skill) was cancelled. The linked job waits
for predecessor cleanup and reservation settlement, and reads its predecessors' plan and notices in
`issue-context.recovery`. Source recovery is stale evidence to inspect against current code and PR
state.

Enable **Issue** webhooks alongside AgentSessionEvent using the same endpoint and signing secret.
Issue events require the configured organization, valid signature and timestamp; they only request
fresh status for already-tracked issues. Their payload state never cancels work directly. A separate
loop checks unfinished/blocked issues every `reconcile_seconds` (default 60); missed webhooks are
covered by polling. With many issues, network latency can extend that interval. Status reads use
source timestamps so older snapshots cannot undo a newer closure. An issue is closed when it is
archived or its workflow-state type is `completed`, `canceled` or `duplicate`; Linear gives Duplicate
a type of its own rather than `canceled`. Other types, including `started` review and acceptance
statuses, leave work running. Before every launch, a fresh status/delegation check must succeed and
find the issue open. Errors defer launch with bounded retry delay; losing delegation
prevents new write workers from launching. Polling makes no Linear writes.

Cleanup records the old PID and preserves dirty tracked/non-ignored untracked source as local WIP
commits. Every repository HEAD, including clean unpublished commits, gets a durable
`refs/farmbot/recovery/<job-id>` ref in its bare clone. Ref/HEAD/path checks must pass before removing
worktrees. A live unverifiable PID, surviving descendants, unsettled reservation or Git failure keeps
files and records a cleanup error. A dead parent alone does not prove detached children exited: an
attempt without verified teardown evidence (including an interrupted launch or older attempt) also
holds cleanup for operator investigation. Teardown evidence comes from a Stop or budget kill, from an
empty Windows Job Object, or on macOS/POSIX from the reap of a worker that exited by itself. A POSIX
worker leads its own session and process group, whose IDs both equal its PID and stay reserved until
FarmBot reaps it. Before that reap FarmBot terminates and records each live member of the session that
its `ps` snapshot shows, after a Stop or budget kill as well as a self-exit. For a self-exit it writes
evidence only after a fresh snapshot shows the session empty and, once the worker is reaped, the kernel
reports no process in its group. A snapshot is not atomic: a member of another process group in the
session that forks and exits between the listing and its session lookup can be missed, and a Stop
persists its sweep only in its final record. A member that leaves the session after being signalled,
and any descendant an earlier, interrupted Stop of the same attempt recorded, stays in that evidence and
holds cleanup while alive; such a pid is recorded but never signalled again. An unreadable process
table is retried for up to a minute first; an unreadable or foreign earlier record holds cleanup. A child that called `setsid()` has left the worker's session and escapes
this check; that is the POSIX limit of the proof, and it is not rare. Claude Code 2.1.280 was observed
starting each Bash tool shell in its own session, so processes a `claude` worker's commands leave running
are likely outside it; other versions and Codex are unmeasured. Stop also signals descendants it can still
find through their parents; a self-exit proof cannot. The Stop sweep is skipped when the process table
cannot be read or `os.waitid` is missing, and Stop then records its evidence as before. Self-exit evidence needs `os.waitid`, which CPython provides on macOS from 3.13. Under an older
interpreter, and for a worker that exited while FarmBot was not running or before this check existed,
there is no evidence and cleanup still holds.
An error while FarmBot processes one worker's exit or budget kill is logged as a `worker_poll_error` event
and does not discard the exits already collected for other workers. That worker stays tracked, and
keeps its concurrency slot, until a later scheduler pass completes its processing; the error itself
records no teardown evidence. A Unity failover fence fails, and is retried, while the revoked worker is
still tracked, so a same-ID successor never launches beside it.
FarmBot reads the files it keeps in a worker's writable state directory only as regular files and without
waiting. These are the worker's reports, its launch and teardown records, its slot reservation token and the
batch run summary. A FIFO, a device, a record over 1 MiB, or on POSIX a symlink in place of the file, is
unreadable. An unreadable launch or teardown record holds cleanup, and an unreadable reservation token holds
its slot. An unreadable report leaves that exit's message empty and its failure unclassified. An unreadable
batch summary reaches the next worker as a verification gap. Of `stderr.log`, only the last 4 KiB is read.
Once a worker could have reached the directory, FarmBot writes each of these files as a new file, then
renames it over the old name. Whatever the worker left at that name, a FIFO or a symlink included, is
replaced, never opened or written through. This does not extend to a worker that replaces a directory on the
path, such as its run directory, with a symlink.
These guards apply to every terminal retirement path. The scheduler retries pending cleanup. Slots still require the
existing quiescence probe; held slots enter controller-owned recovery. No log, run report, ledger history or
memory snapshot is removed by closure cleanup. Every worker attempt gets its own log directory.

Inspect `cleanup_pending` and `issue_status_errors` with `python3 -m agent.service status`, or the
ledger CLI's `status`. `issue-context --item JOB_ID` exposes that job's `cleanup` and, for successors,
`recovery` evidence. Inspect saved source with `git --git-dir .local/repos/REPO.git show
refs/farmbot/recovery/JOB_ID`; apply selected commits only after reviewing current requirements and
repository history. Cleanup does not push, merge, close PRs, or undo already-issued external requests.

SIGTERM to the service runs batch-process cleanup during startup or normal serving. On this Mac,
the earlier live `launchctl kickstart -k` rehearsal also removed the batch Editor; this is not a promise
that Python cleanup executes after SIGKILL. Reservation release still requires a quiescence probe.

## Automatic Unity resource recovery

A failing release or stalled interactive test quarantines its slot and requests controller
recovery. `awaiting_resource` with stage `waiting_for_recovery` is an infrastructure wait;
`awaiting_input` remains exclusively a wait on a human: a question or, with `--reason waiting`, a
human step elsewhere. This supersedes the original
operator-only slot recovery policy. Workers checkpoint, release `unclean`, and exit immediately.
The release revokes both their claim and reservation token. Never request a human to operate
Unity or add `needs-more-info` for this condition.

After certifying the worker's process tree has stopped, the controller detaches the old
reservation and queues its exact commit and mode. A healthy second slot may resume that job
while an independent service loop repairs the first. Only configured local slot editors may
be stopped; uncertain process inspection, surviving processes and identity mismatches keep
the slot quarantined. After proving the Editor has exited, recovery may archive changes confined
to `Assets/*.meta`: tracked changes under a dedicated Git recovery ref, newly created metadata
under private recovery evidence. It checks the source snapshot again before restoring those
files from the held commit, then returns the clean slot closed. A failed archive or any other
dirty source keeps the slot quarantined. The failed verification is still a gap; an exact-commit
retry consumes the existing preparation or execution budget, so a commit that repeatedly makes
Unity rewrite metadata ends in an explicit job failure without holding slots indefinitely. A
shared MCP broker is never terminated by this recovery path. Captured diagnostics remain under
`.local/agent/resource-recovery/`.

The controller observes test progress, not merely the active flag: 180 seconds without progress
or continuously unavailable inspection triggers recovery. Repair requests a cooperative Play Mode
stop and verifies teardown before restarting the editor. Startup, compilation, commit and loaded
assembly identity must pass before availability is restored. An interrupted run remains a
verification gap. Stop and issue closure prevent job continuation throughout recovery.

Unrelated Unity project folders may coexist with the pool. Every editor MCP call resolves the
configured project from the current instance listing and rejects a conflicting recorded instance.
Process ownership, project locks and the full grant identity probe still apply. Closing the last
pool editor does not reap its broker while an unrelated editor is present.

There are two automatic execution retries per job, three separate preparation retries, and three
attempts per slot recovery, with 60/180-second
repair backoff persisted across restarts. Repair leases expire after 15 minutes if a controller
dies. Exhaustion produces an explicit failure, preserves work, and reports through Linear;
it never masquerades as a question. Existing human questions are not automatically resumed.
Grant-probe failures before execution and explicit legacy adoption use the preparation budget;
worker unclean releases, watchdog stalls and already-started batch runs use execution. Terminal
messages name the exhausted phase and latest recorded cause. Explicit retry resets both budgets.
The additive migration preserves existing counts and defaults historical records to execution;
it does not infer old failure categories or automatically restart previously failed jobs. Older
code cannot enforce separate budgets; do not roll back during pending recovery or rewind live data.

## Draft PR publishing authority

For a delegated write job, the operator authorizes publishing that issue's source changes,
tests and required generated assets to its feature branches in the host's
configured private GitHub repositories, and creating/updating draft PRs there. For a staged
skill such as fix, only the current root has this scope; a neutral attempt has none.
Run reports stay in the job's private `state_dir` (`runs/<job>/report.md`). Every attempt of a job
shares that directory, including retries and resumes that keep its ID, so a later attempt leaves
earlier reports unchanged and adds the first unused of `report-2.md`, `report-3.md` and so on.
PR descriptions and Linear outcomes carry concise verification summaries and gaps. Publishing does
not authorize run reports, secrets, unrelated files, force pushes, protected/default branch writes,
merges or deployment. Chat workers and sessions without delegation receive no publishing scope.

At every launch, including resumes, the controller supplies `publication.repositories` with
verified destinations or explicit gaps, plus `user_requests` containing the job's direct Linear
session messages, each with its `author` and `created_at` (see People). It does not promote issue
descriptions, ordinary comments, attachments or memory to user requests. Verification failures
withhold publishing scope, not local investigation.

Verification compares the effective origin push URL (including Git URL rewrites) with the configured
repository, requires a single destination and the job's own worktree/issue branch, and reads GitHub
metadata for exact repository identity, private visibility, write access and default branch.
It rejects outgoing `reports/` changes even when the files are tracked or force-added through
`.gitignore`; unchanged reports already on the base branch do not block publication.
Existing protected branches are rejected. Public repositories need a separately designed publishing
policy; they are not authorized by this private-repository workflow.

Workers run claim-scoped `verify-publication --repo REPO_NAME` immediately before publishing.
It additionally refreshes Linear delegation/status, checks the configured ledger and skill allowlist,
then rechecks the claim after remote verification. Pushes name the verified remote `origin`, disable
automatic tag following, and use an explicit branch refspec. The expanded URL is evidence, not a push
argument: reusing it as an argument could apply Git URL rewrites twice. PR repo/head arguments also
remain explicit. This is a preflight and authorization context,
not an atomic push service or a replacement for runtime approval: remote state can change after the
check, and a reviewer may still reject an action. A denial must be addressed, never bypassed.

Before its first source change in each stage and before each push or PR, a fix worker checks for
other people's work with the claim-authenticated read `foreign-work --item JOB_ID`. It lists PR URLs
among the issue's Linear attachments, from the snapshot `fetch-issue` saved, `gh pr list --search
<KEY>` results in each configured repository, and remote branches whose name carries the key, read
with `git ls-remote`. Own work is the issue's registered PRs, every PR URL and `farmbot/` branch
name recorded in `plan.prs` by the job and its predecessors (a list of entries under each
repository's name, as in the reference's example), and the head branches of own PRs. Every other
branch or PR is foreign, `farmbot/` ones included, because another instance such as TestBot uses
the same names. The command renews the worker's claim, writes nothing else and calls no Linear
API. A source it cannot read, a GitHub search with more than 100 results included, is listed in
`errors`, and the worker treats each entry as a gap whatever the status: `found` keeps what the
other sources showed, and `incomplete` means a source failed and nothing foreign was found, never
that nothing exists. When anything is foreign, the worker posts a `foreign_work` notice and asks
with `await-input`, unless a current session message already answered. `verify-publication` adds
the same report for its repository as `foreign_work`, or `unavailable` when the check itself
failed, as evidence only: a hard publication gate would need a stored acknowledgement, so
publication is not refused on it.

## Unity verification commits

The issue target remains the immutable baseline for the job. A `feature` job has none (Phase B plan,
P6): the receiver pins no Farm-Client commit in a session whose delegation starts it, nor on any
later event in that session or for a mention in another session that it forwards to the job, and no
acknowledgement of those events has a target line; a first `feature` job a conversation starts takes
none either. `feature` has no client stage yet, so nothing in it uses a target; Phase C decides what
its client stage pins. A write worker can request `await-resource --resource unity_slot --mode batch
--commit FULL_SHA` (or `--mode interactive`) to verify the current clean HEAD of its own Farm-Client
worktree. The CLI authenticates the claim, checks the configured host/checkout and commit, then the
ledger rechecks ownership before queuing. For a fix, this selection requires a Farm-Client-rooted
worker. A neutral fix worker may request the original baseline without `--commit`; other rooted fix
workers cannot request Unity. Omitting `--commit` retains baseline behaviour. Dirty files,
abbreviated SHAs, refs, another checkout's commit and read-only chat requests cannot select a fix
revision.

Each reservation records its exact commit independently of the baseline. The slot loads that
commit and the resumed worker sees it as `resource.commit`. Batch summaries additionally carry
`commit_sha` and `reservation_id`; XML, Editor logs and a summary are retained in
`runs/<job>/unity/<reservation-id>/`. The job-root summary points to the latest run. A baseline
run cannot establish that a later fix works; reports must name the tested commit and any gaps.

## Linear uploads

Any worker may download the claimed issue's uploads with the claim-authenticated
`download-uploads --item ITEM_ID --out DIR [--url URL ...]`. Its sources are the
`uploads.linear.app` URLs in the issue's description and human comments, in Markdown, bare or
`<linear-image>` form; GraphQL `attachments` are linked resources such as PRs, not uploads.
Without `--url` it takes every upload there, and each `--url` must be one of them, unsigned as
`issue-context` shows it. It refreshes the issue in the ledger as `fetch-issue` does. `DIR` must be
an absolute path without `..` that is not a symlink, junction or other reparse point; it is created
if missing, once every `--url` is checked. The command runs in the worker's own process and
sandbox. The skill names a directory of the worker's own under its state directory
(`STATE_DIR/inputs/linear`), which no controller step reads or writes; the state directory's own
top level is the controller's.

Workers may hold the Linear app's secret (feature-workers design, D11): the worker CLI builds its
Linear client from the host config, as every Linear-facing CLI command already does. Workers use it
only through FarmBot's CLI commands for the claimed item and never fetch `uploads.linear.app`
another way. The fix skill and the worker CLI reference state this rule; the dispatch AUTHORITY
does not mention uploads. A download sends the app's bearer token as an unredirected header, over
HTTPS to `uploads.linear.app` only, refuses every redirect (the file must come from that origin
itself; the refusal names only the host the redirect pointed at, which is what the operator records
if Linear redirects to signed storage), and gives up after 30 seconds without data or 10 minutes for
one file, however slowly it trickles. No output, manifest, error text or log carries the token or a
signed URL; a failure is named by its kind, and a token-endpoint or local storage failure as such.

Limits apply per run: 100 downloads, 256 MiB for one file and 1 GiB in all; archives of at most
2,000 entries; 1 GiB extracted in all, each member expanding to at most 100 times its compressed
size or 1 MiB, whichever is more. A failed download, including a web page returned for a file not
named `.html` or `.htm`, is recorded in the manifest and the run goes on. Missing art or an unreadable upload
is a question for the issue, never a guess.

`DIR/manifest.json` lists every file: its sources (`description` or `comment:<id>`), unsigned URL
path, local name, sha256, size, content type, PNG or JPEG pixel size and, for a member of a `.zip`
upload, the archive and the member's stored name. An upload whose files still match the manifest is
not fetched again, and a directory holding another issue's manifest is refused. Local names come
from a `<linear-image>` title or Markdown link text, else from the URL path; Windows-forbidden and
unprintable characters become `_`, device names get a `_` prefix, and names that collide ignoring
case are numbered. A file in `DIR` that the manifest does not list is never overwritten. Archive
members are extracted under a directory named after the archive, never through a link. A member is
refused for an absolute, drive-relative or `..` path in either separator style, a link, a Windows
device name, a trailing dot or space, a `:`, a name that collides with another ignoring case, or a
name that is neither UTF-8 nor GBK. A member name is UTF-8 when its UTF-8 flag or an Info-ZIP
Unicode Path field says so; otherwise strict UTF-8 is tried first, because macOS Archive Utility
omits the flag, then GBK. Refused members are listed with the reason, decoded legacy names keep
their raw bytes, and nested archives are not extracted. The Windows cases (junctions, device names,
trailing dots, streams) have tests that run only on Windows.

## UI source ownership

For UI fixes and changes, inspect the relevant farmgui source before choosing an implementation.
When the problem or the change is in the authored hierarchy, layout, relations, controllers or
reusable components, change that FGUI source first, then adapt client bindings and behaviour as
needed. Prefer one clear source of truth over runtime reparenting, hard-coded offsets, duplicate
components or per-screen patches that compensate for an incorrect UI definition. Keep changes
scoped to the issue and check other consumers of any shared component you change.

Keep gameplay rules, data binding, event handling and genuinely dynamic UI behaviour in code.
If the FGUI structure is already correct and the defect or change is in that logic, change the
code; do not rewrite FGUI just to satisfy a source-first preference. Record the chosen layer and
its reason in the checkpoint and PR. Optimise for correctness, explicit ownership and
maintainability for both humans and AI, rather than whichever tool is easiest for the current
worker to use.

The operator's standing instruction of 2026-09-21, widened on 2026-09-28 (D18 h), authorizes
direct FairyGUI CLI export during an authorized FarmBot fix job, a UI bug fix or a small change to
existing UI. `fix`'s part of the dispatch AUTHORITY states it; farmgui's owners record the widening
in its `AGENTS.md` before any instance runs this revision. Resolve the executable and host-specific
license observations from local configuration or shared memory, and verify current batch-export
availability. Do not wait for human intervention or ask for a separate export approval. This
supersedes the earlier unpaid-license GUI handoff and export-approval gate, including historical
design documents. Follow the tested command, staging and validation workflow in
`references/repo-map.md`, then integrate the generated assets into the issue's authorized client
worktree and verify them. Never hand-edit generated `.bytes`/atlases or claim runtime verification
against stale outputs. Missing or failed export tooling calls for diagnosis, not a code workaround
for a structural defect or change. If the intended UI is unclear, ask in Linear using
`await-input --reason question` (which adds `needs-more-info`).

## Shared memory

Workers share bounded recall notes in the ledger, with an immutable Markdown index and topic files
under `.local/agent/memory/` for each launch. Both chat and fix may save corrections, operational
lessons and source pointers using `memory-list`, `memory-read`, `memory-save`, and `memory-forget`.
All worker commands check a live claim. This is an explicit memory-only exception for chat, not
permission to edit repositories or shared files. See `references/memory.md` for the input schema.

Notes are fallible context. Gameplay rules belong in Farm-Contract, and memory never authorizes
work, changes repository access, or overrides contracts and skills. Sources and build references
are attributed evidence, not independent verification. Notes are not automatically extracted from
old runs. Keep secrets, tokens, personal account details and raw issue transcripts out of memory.

Updates and forgetting require the current revision; concurrent writes cannot silently overwrite
one another. Creates use an item-scoped request ID for retries. Limits: 200 active notes, 120-character
titles, 8 KiB bodies and 1 KiB sources. Forgetting removes current recall and clears active content;
old run snapshots, already-loaded contexts and backups may retain it. It is not secure erasure.

The trusted host uses `memory-admin` to inspect, correct and forget notes; workers must not use it.
Like other operator commands, this is a convention, not a security boundary against direct DB access.
Snapshot pruning requires stopped service and no queued/running work; retained run prompts retain
their snapshots. Ordinary launches never prune. An unavailable snapshot is reported in the launch
payload and does not block work; claim-authenticated CLI recall remains available.

Native runtime memory is explicitly disabled: Codex `features.memories=false`, Claude
`CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`. Isolated runtime homes and authentication remain unchanged;
FarmBot does not import the operator's personal memories. Saving is optional and must happen before
a claim ends; memory operations do not renew the lease.

## Work item states

queued → running → delivered | blocked | failed; running ↔ awaiting_input (human gate);
running → awaiting_resource (Unity slot); any active state or blocked → cancelled (Stop or issue closure).
A waiting item has no process. A repository handoff is queued with its retired worker PID retained;
it cannot be claimed or launched until the controller certifies teardown and clears that PID.
An item in awaiting_resource holds a queued reservation; only the pool's grant turns
it back into queued work. A launched worker must claim its item within 10 minutes or it is stopped and the item fails. A confirmed terminal Codex model-capacity error returns the same queued or running item to the queue after 60, 180, then 600 seconds, with at most three automatic retries. The old claim is revoked; worktrees, checkpoints, model settings and reservations are retained. Retry timing is durable and all normal concurrency/delegation checks still apply. Cancelled, completed, waiting and deliberately stopped work is not automatically retried. Other pre-claim exits fail the item; a worker that dies with an expired lease requeues the item once for a fresh worker. A queued item whose skill is loaded but not in `enabled_skills` fails before launch.
The Linear session follows the item: `finish` posts the final response that completes the session (a chat
answer is its own response); a worker that dies or never starts leaves an error activity naming 重试 as the
way back, and a requeue leaves a thought.

The host posts session progress every ten minutes for queued, running and resource-waiting
work. It reports the recorded state and checkpoint age without extending the worker's lease
or claiming new results. Awaiting-input, terminal and synthetic local sessions receive no
regular heartbeat. Timing and pending activity IDs survive restarts; a failed send retries
after sixty seconds. A state change while an activity is in flight is followed by a durable
correction so completed or waiting sessions do not remain active. Reporting uses its own
loop and connection; slow Linear requests do not hold the scheduler lock.

At service startup the progress publisher creates the additive `session_progress` table;
existing work-item rows are not rewritten. Pending sends and their retry timing remain
in that table across restarts. Rolling back to older code stops periodic reporting and
leaves the table unused; it does not reverse or delete saved work. Deploy or roll back
the host code and worker skill files together after the service has been settled.

## People

An issue read keeps the group of each label that belongs to a label group (`label_groups`,
beside the bare `labels`), the assignee, the creator and, for each comment, its own Linear link, the
comment it replies to and, for a human comment, its author. The receiver also records each agent
session's creator (Linear's `agentSession.creator`, unset when automation or an agent started the
session) and each session message's author: the user who wrote a session reply, or the creator of
the mention session whose comment opened it. A person is always the Linear user's ID, name and
profile URL (`{id, name, url}`), never an email. A user without a UUID or an `https://linear.app/`
profile URL is stored as null, as is one whose name is blank, longer than 256 characters, holds a
control, format (a bidirectional mark or an invisible character, other than the joiners emoji
sequences use), private-use or line-separator character, or contains an email address, and so is a
comment link outside `https://linear.app/`. An app user, which Linear marks with `User.app`
(FarmBot itself, another FarmBot instance, Codex or Linear's integration user), is never a person
in an issue read: its comments are a bot's, with no author, and it is never the issue's assignee
or creator. A webhook names its users without `User.app`, so a session creator or message author
is judged by the other rules only. None of this is issue input: a claim covers the title, the
description, attachments other than the job's own PRs, and the IDs and bodies of the comments that
are neither a bot's nor FarmBot's own, so reassigning or relabelling an issue neither requeues its
work nor refuses a handoff.

Issue snapshots stored before this revision lack the issue-read fields above, which reads as
unknown, and older revisions ignore those fields.
`issue-context` shows each message's author and the time FarmBot received it (`created_at`, ISO
8601 UTC; an event processed later, for example after a restart, keeps its arrival time) on
`session_messages` and on `conversation_history` messages, and a chat's messages keep both when a
repair takes them over. The nullable `sessions.creator_json` and `inbox.author_json` columns are
added when a ledger opens. Existing rows are not rewritten: they read as unknown (null), as do
operator-enqueued sessions, and their messages keep the time they were stored, which for a copy an
older revision's repair made is that repair's time. Older code ignores the columns, so rolling back
keeps working; what it records meanwhile names nobody.

`issue-context` also gives workers an `owner`: the issue's assignee, else the human who delegated
the issue most recently (the creator of its latest delegation session in Linear, even for an item
started under an earlier one; an operator `enqueue` delegates to nobody, so its `local-` session is
skipped), else nobody (an issue only ever enqueued, or a delegation whose creator is unknown). Its
`creator` is the issue's creator when that is a person other than the owner. Fix workers record a
human ruling as `[DECIDED:<Linear user name>@<date>]` under the full Linear name (`User.name`) of
the author of the comment or session message that gave it, dated by the calendar date of its
`created_at` in UTC+8, the team's time zone, and linked to the comment's own `url`; only for a
comment without one do they build the link from the issue URL and the comment id. They attribute
no ruling to anyone else or to a comment a FarmBot instance posted, record none that nobody gave
and write no silent-consent default. A blocker, a delivery's request to review and merge, a
question notice and an `await-input` question mention the owner by profile URL, which Linear
renders as a mention; without an owner nobody is mentioned in the owner's place. A question for
策划 also mentions the creator. FarmBot asks the owner to merge, cannot enforce who does, and
never merges. One live check on 2026-09-27 found that a profile URL in a comment posted with the
app's token renders as a mention and notifies the person (spec §14.1); reliability beyond that
one observation is unmeasured.

## Comments

Chinese, concise, one marker line `[farmbot:<id>]` appended by the ledger. Kinds: started (once
per claimed issue input and generation, so a job requeued after the issue changed posts it
again), blocker, delivery. Templates: `references/comment-templates.md`; `<bot_name>` there is the
instance's `expected_bot_name`, passed to workers as `bot_name`, and `<owner.person.url>` is the
owner's profile URL from `issue-context` (see People).

Notices are a second family, for comments a job may repeat: `question` (a grouped question round),
`waiting` (a pause on a human step elsewhere) and `foreign_work` (other people's branches or PRs).
The ledger keeps one per work item and worker-chosen request id rather than per claimed input, so a
retried attempt of the same item reuses it and a new round needs a new request id; a successor of
cancelled work starts with none and reads its predecessors' under `recovery.notices`. `post-notice`
reconciles the marker against live comments before creating one, and creates none on an issue that
has left scope. The bodies of FarmBot's own comments and notices never count as issue input. Notices live in the additive `notices` table; a rollback leaves it unused and
any unposted notice unsent.

## Resource execution limits

- Each configured host owns its Unity slots. Items that need Unity queue for a free slot;
  a worker holds at most one slot and releases
  it after its own quiescence check with `release-resource --outcome quiescent`, or `--outcome unclean` to
  hand it to controller recovery. **A worker never starts a Unity process**: the Editor does not work inside a
  worker's sandbox, so FarmBot performs a batch run itself, outside that sandbox, between the request and
  the worker that reads its results — one grant is one run. A failing probe, and an unclean release, hold
  the slot until controller repair verifies it healthy. No slot is ever released on a timer. A slot runs Edit Mode
  and PlayMode fixtures and never a player build: the budget is an import-only `Library/`, and a player
  build adds several GB of `Bee` and `BuildCache` to it. The receiver and the tunnel run as launchd agents
  and restart at login; while the host config names no named tunnel, the public hostname changes whenever
  the tunnel restarts and must be re-entered in Linear, and until it is, work is created with
  `python3 -m agent.service enqueue --issue <id> --skill fix`. An enqueued item has a local session that
  Linear does not know about, so it reports through issue comments and posts no session activities;
  `enqueue` still refuses a write-capable skill on an issue that is not delegated to FarmBot now, because
  the rule of authority is not what the missing webhook excuses. It also refuses a skill the instance does
  not run (`enabled_skills`). `python3 -m agent.service slots` is the
  operator's view of the pool: slot states, parked commits and the open reservations behind them.
- A fix that finds nothing to change (already fixed, duplicate, does not reproduce, or the requested
  change is already on the target branch) delivers with an empty PR list and a `no_change` reason,
  and FarmBot's session response says 无需改动. Blocked stays for work that a human must unblock.
- Delegation must come from the Linear UI. Setting the delegate through the API creates no agent session,
  so FarmBot never hears about it.
- Two concurrent workers. Run-time budget, lease and renewal cadence are per skill, from its `skill.json`
  (`max_hours`, `lease_seconds`, `renew_minutes`); the launcher records the lease on the work item and the
  launch message tells the worker its own numbers.
- Worker runtime: Codex CLI (`codex exec --approve-for-me`, with `sandbox_mode = "workspace-write"` in its isolated home and automatic approval review), one isolated `CODEX_HOME` per work item seeded with `auth.json`; Claude Code remains a chat fallback pending an equivalent fix sandbox and isolated-auth recipe. Details: `docs/superpowers/spikes/2026-09-18-runtime-spike.md`.
- Receiver: HMAC-SHA256 and 60 s timestamp window. AgentSessionEvent matches client, app user and organization; Issue events match organization. Each delivery the receiver rules on, signed or not, adds one JSON line to the service log with its status, result, `type` and `action`, never the body; `type` and `action` appear only as words of 1 to 40 ASCII letters, anything else as null.

## Publication transport recovery

Publication verification retries only transient read/authentication transport errors, never
push/PR mutations. Each of three attempts (2/5 second delays) renews the claim and fetches fresh
delegation, destination and branch evidence. After temporary exhaustion, the host revokes the
claim and durably queues the same job after 60/180/600 seconds, preserving checkpoints and files.
Only `status: verified` authorizes publication. `retry_queued` and `retry_exhausted` require worker
exit; the latter records a failed infrastructure attempt after the three delayed retries.
Permission/destination mismatches and certificate errors remain fail-closed and do not requeue.
Missing initial Unity target pins remain recorded verification gaps, not publication blockers.
Publication retries reuse preserved worktrees without an origin fetch. Their allowance counts
launched attempts; a failed host Linear delegation preflight keeps the job queued under the
existing lifecycle backoff without consuming another worker attempt.

## Status monitor

`python3 -m agent.service monitor` serves a read-only Chinese status page and `/api/status` JSON for one
instance, as a separate process on the host running `serve`. It binds the config's `monitor.bind`
(default `127.0.0.1`) and `monitor.port` (default 8780, never the receiver's port). It serves only `GET`
and `HEAD`, answering other methods with 405, and only when the `Host` header is an IP literal,
`localhost` or a configured `monitor.hostnames` entry; other hosts get 421, and a request without a
`Host` header gets 400. There is no authentication. Exposing it beyond loopback is an explicit
configuration choice, and anyone who can reach it can read issue identifiers, titles, job, worker and
slot states and PR links. Only the monitor and `install-launchd` validate the `monitor` block; `serve`
and workers neither read nor validate it. `install-launchd` writes a monitor agent only for a non-empty
block that passes that validation, and never removes one. That agent restarts an exited monitor at most
once a minute (`ThrottleInterval` 60), so a monitor that keeps refusing to start adds one line a minute
to its log.

At startup the command prints one JSON line, `monitor_ready`, once it listens. Any startup problem
instead makes it exit 1 with one `monitor_failed` JSON line: an unreadable config, an ownership
mismatch, a `monitor` block it cannot use, a port equal to the receiver's or already in use, or any other
error.

After startup, a status build that fails for a reason other than an unreadable ledger (shown as 未知)
answers `/api/status` with 500 and the security headers every reply carries. As a good build does, it
answers every request for the next two seconds without another build, so the status is built at most
once every two seconds however many pages poll; it replaces the last good build, which is never served
again. The first failure of each run of failures prints one `status_failed` JSON line. The page counts
the 500 as a failed poll and shows 连接中断, although the monitor is running. The `status_failed` line
tells this apart from a monitor the page cannot reach, which leaves no such line. A client that
disconnects before or during its reply is not logged, by the monitor or by the receiver.

The page accepts only a status document of `schema_version` 1 and keeps no other body. Any other body is
a failed poll, and one of another version, from a monitor upgraded under an open tab, makes the banner
read 监控已更新，请刷新页面。 and the header and tab title read 需要刷新. The page never reloads itself. If
rendering a document throws, the header and tab title read 页面显示出错 and the content is dimmed until a
render succeeds, so the page never keeps showing an earlier verdict.

The monitor opens the ledger with SQLite `mode=ro` and `query_only`, never constructs `Ledger` and never
takes the controller lock. It writes no FarmBot file or ledger row; SQLite can leave its `-wal`/`-shm`
side files beside a ledger whose service is stopped, which is why the monitor runs as the same account as
`serve`. It signals and inspects no processes, and makes no request other than
`GET http://127.0.0.1:<port>/health` with proxies disabled, following no redirect. Only a 200 counts as
answering; a 3xx, any other status and a failed request count as not answering. The JSON is an allowlist.
It carries a job's stage name, clamped to 120 characters, but no checkpoint contents, descriptions,
comments, questions, inbox or worker messages, evidence prose, logs, paths, PIDs, tokens, config values
other than the instance's environment, ID, bot name and host, or raw stored errors. Older ledgers without
optional tables or columns are read without migration.

`serve` writes `<local_root>/service-heartbeat.json` by replacement:
- when it starts, with phase `starting`, before slot preparation;
- every five seconds, with phase `serving` once its loops run;
- on clean shutdown, after its loops have stopped, with phase `stopped`.

The heartbeat records each loop's work timing and consecutive errors, the revision, in-memory webhook
outcome counts, and the worker processes the launcher is managing, as start and budget-deadline times
only. A failed write is skipped and never affects serving. The first failure of each run of failed writes
prints one `heartbeat_error` JSON line naming the exception class, and the final `stopped` beat, which no
later beat repairs, is retried once after 0.1 seconds. The monitor reads the file as untrusted data (a
regular file, at most 64 KiB, validated) and treats it as stale once it is 60 seconds old. It judges
loops as idle, busy, stalled or erroring only from a fresh heartbeat that is not `stopped`; a stale or
stopped heartbeat's loops show only their last recorded times, in a neutral tone. A read refused with
`PermissionError`, as on Windows when it collides with the replace, is retried once after 0.1 seconds. A
worker able to write the state root could forge the heartbeat, so `/health` remains an independent
signal.

A revision that writes no heartbeat never replaces the file. After rolling back to such a revision,
delete `<local_root>/service-heartbeat.json` once the newer `serve` has stopped. Otherwise its last
heartbeat stays, and while the older `serve` answers `/health` the page shows 需要关注
(`heartbeat_stale`) indefinitely.
With no heartbeat file, a healthy `/health` still shows 需要关注 (`heartbeat_missing`): it cannot distinguish
an older service revision from a failed initial heartbeat write. An older revision's loop rows show
此版本未提供.

A running job's worker is shown with the first state that applies:

| State | When |
|---|---|
| 租约已过期 | The job's lease has run out |
| 未被服务跟踪 | A fresh `serving` heartbeat lists no worker for the job, more than 30 seconds after its claim |
| 续约逾期 | More than 1.5 times its skill's `renew_minutes` have passed since its last lease renewal or claim |
| worker 状态未知 | No fresh serving heartbeat can confirm tracking, or the claim is within its first 30 seconds and the heartbeat has not listed the worker yet |
| worker 运行中 | A fresh serving heartbeat lists the worker, and none of the above applies |

Expired, untracked and overdue states raise attention; unknown is neutral.

The verdict is the first that applies:

| Verdict | Meaning |
|---|---|
| 未知 | The ledger cannot be read |
| 已停止 | A stopped heartbeat and no `/health` |
| 正在启动 | A fresh heartbeat with phase `starting` |
| 无响应 | No `/health` and no fresh heartbeat |
| 需要关注 | A half-alive service, erroring or overlong loops, recent webhook rejections, held or orphaned slots, expired leases, overdue or untracked workers, cleanup still pending 10 minutes or more after a job finished, repeated Linear status failures, or a reservation cancellation unsettled for 5 minutes or more |
| 正常 | None of the above |

A verdict is evidence about these checks only, not proof that Linear, the tunnel or Unity work.

Without a heartbeat time to use, the monitor dates a `/health` failure from the first status build that
saw it. If more than 30 seconds pass between builds, so that no page was polling, the next failure starts
a new date: a failure seen long ago never dates a new one.

`scripts/redeploy-farmbot.ps1` stops the Windows receiver with `Process.Kill()`, so no `stopped`
heartbeat is written and Python cleanup does not run. During a redeploy the page shows 需要关注
(`receiver_unreachable`) while the last heartbeat is still fresh, then 正在启动 once the new `serve`
writes its `starting` heartbeat, then its usual verdict, normally 正常. If 60 seconds pass without a
heartbeat in between, it shows 无响应 until the `starting` heartbeat arrives. It never shows 已停止: only a
clean shutdown writes the `stopped` heartbeat. A clean shutdown closes the receiver first and writes
`serving` heartbeats until its loops have drained, which can take minutes, so it shows 需要关注
(`receiver_unreachable`) for that whole time and then 已停止. Under launchd, `launchctl bootout` sends
SIGKILL once the job's exit timeout has passed. The plists do not set it, so the system default applies.
A drain longer than that ends without a `stopped` heartbeat, so the page shows 无响应 instead of 已停止
once the last heartbeat is 60 seconds old.
