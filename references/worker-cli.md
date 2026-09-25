# Worker CLI and checkpoints

Use DATABASE, ITEM_ID and STATE_DIR from your launch message. Run from `repo_root`
with its Python environment. Check the installed command before guessing a flag:

```bash
python3 -m agent --db DATABASE checkpoint --help
```

| Command | Item and authentication arguments |
| --- | --- |
| `claim` | `--item ITEM_ID --worker-id WORKER_ID`; returns the claim token |
| `fetch-issue`, `issue-context` | `--item ITEM_ID` only; no token flags |
| `renew`, `checkpoint`, `pop-inbox`, `verify-publication`, `handoff-repository`, `prepare-comment`, `post-comment`, `confirm-comment`, `activity`, `await-input`, `await-resource`, `finish` | `--item ITEM_ID --token-file STATE_DIR/token`, plus command-specific arguments from `--help` |
| `request-repair` | Read-only profile only: claim-token arguments, `--message-id LATEST_MESSAGE_ID --summary-file STATE_DIR/repair-summary.md` |
| `resume-work` | Legacy resume-only command: claim-token arguments and `--message-id LATEST_MESSAGE_ID`; cannot start a first repair |
| `memory-list`, `memory-read`, `memory-save`, `memory-forget` | Same claim-token arguments; see `references/memory.md` |
| `release-resource` | `--item ITEM_ID --token-file RESOURCE_TOKEN_FILE`, using `resource.token_file`, plus `--outcome quiescent` or `--outcome unclean` |

`fetch-issue` refreshes the ledger from Linear; `issue-context` reads the saved context.
Neither accepts `--token-file`. Tokens never belong in argv as `--token` values.
The argument table does not grant additional authority; use only your delegated item.

`issue-context.issue` is the issue as last read. Besides the bare `labels` it may carry
`label_groups` (a `{"group", "label"}` pair for each label inside a label group), `assignee` and
`creator`, and on each comment `url` (the comment's own Linear link), `parent_id` (the comment it
replies to) and, on a human comment, `author`. A person is `{"id", "name", "url"}`, never an email.
`null` means none or unknown; a missing key means the snapshot predates these fields.

`request-repair` refreshes Linear, then atomically retires read-only execution and queues
the same issue's repair. It requires recorded delegation provenance and current delegation,
but no prior fix or Bug label. It carries all current messages and the investigation summary
into `issue-context`. Success retires your token: exit immediately. A newer-message refusal
means reread the conversation before deciding again. `conversation_history` provides earlier
answers/findings across execution profiles; only current `session_messages` authorize a request.

Each `session_messages` entry, like each message in `conversation_history`, is
`{"id", "body", "author", "created_at"}`, and so is each entry of the launch message's
`user_requests`. `author` is the Linear user who wrote it, `{"id", "name", "url"}` with the full
name (`User.name`, not the `displayName` handle): the user who replied in the session, or the
person whose mention opened it. It is null when FarmBot does not know, as for messages recorded
before it kept authors. No email is recorded. `created_at` is when FarmBot received the message,
in ISO 8601 UTC.

`issue-context` also names who is responsible. `owner` is `{"person", "source"}` or null: the
issue's assignee (`"source": "assignee"`), else the person who created this item's delegation
session (`"delegator"`), else null, for example after an operator `enqueue`. `creator` is
`issue.creator`, or null when that is missing or is the owner. A comment mentions a person by
containing the person's profile `url`; the fix skill says when.

```bash
python3 -m agent --db DATABASE fetch-issue --item ITEM_ID
python3 -m agent --db DATABASE issue-context --item ITEM_ID
python3 -m agent --db DATABASE checkpoint --item ITEM_ID --token-file STATE_DIR/token --input STATE_DIR/checkpoint.json
python3 -m agent --db DATABASE await-input --item ITEM_ID --token-file STATE_DIR/token --question "Which environment should reproduce this issue?"
```

Run each mutation separately and inspect its exit status and returned JSON before
running a dependent command. A nonzero exit means the operation failed. A zero exit
still requires checking the returned state/status, including publication retry states.
On a usage error, read that command's `--help` and correct the arguments; do not try
speculative aliases. Never print a token while diagnosing a command.

## Checkpoint JSON

Save this shape as `STATE_DIR/checkpoint.json`, replacing example content with observed
facts and real paths. All five `handoff` arrays are required, including empty arrays.
Use exactly the shown entry keys. Every entry value is a nonempty string; evidence is
a single path or short string, not an array/object. `hypotheses` and `next_actions`
contain strings directly. Each array has at most 20 entries, each string at most 2,000
characters, and the entire handoff at most 12,000 serialized characters. Keep raw logs
in files. `published_prs` contains only this job's actual canonical HTTPS PR URLs.

```json
{
  "stage": "investigating",
  "handoff": {
    "facts": [
      {"claim": "Typecheck could not reach compilation because a dependency is an LFS pointer.", "evidence": "STATE_DIR/typecheck.log"}
    ],
    "hypotheses": [
      "The reported behavior may depend on server state; runtime reproduction is pending."
    ],
    "checks": [
      {"command": "tools/typecheck/hotupdate-typecheck.sh", "result": "BLOCKED: dependency hydration incomplete; no product regression established.", "evidence": "STATE_DIR/typecheck.log"}
    ],
    "repositories": [
      {"path": "WORKTREE_PATH", "branch": "ISSUE_BRANCH", "head": "FULL_HEAD_SHA"}
    ],
    "next_actions": [
      "Check the documented dependency hydration procedure, then rerun typecheck."
    ]
  },
  "published_prs": []
}
```

Before `await-input`, `await-resource` or `finish`, save the current evidence and next
steps. If `checkpoint` rejects a handoff, correct the JSON and rerun it until it
succeeds. Confirm the returned checkpoint contains the intended handoff before retiring
the claim. The CLI blocks those transitions after a rejected handoff until a valid
handoff is saved; do not remove `handoff` to bypass the repair. If saving cannot
succeed, retain the local JSON, report the exact error, and do not claim it was saved.

For a fix repository switch, save a fresh `handoff` with facts, checks, repository heads,
published PRs and next actions, then run:

```bash
python3 -m agent --db DATABASE handoff-repository --item ITEM_ID --token-file STATE_DIR/token --to Farm-Contract
```

Use the actual target repository name. The command refreshes the issue and delegation, revokes
this claim, and queues a fresh worker in the same item. Check its returned `next_root_repo`,
then exit immediately. A new worker starts only after the controller certifies this attempt's
teardown. A failed command leaves the current claim in place; inspect the error and repair it.

For a stalled Unity reservation, save that checkpoint **before**
`release-resource --outcome unclean --item ITEM_ID --token-file RESERVATION_TOKEN_FILE`.
Its `recovery_queued` response revokes the worker claim and reservation token: exit immediately.
The controller handles editor recovery and job continuation. Do not follow it with `await-input`
or ask for host intervention. A fresh worker receives the exact retried commit and can read
`issue-context.resource_recovery` for prior attempts and retained diagnostics.
