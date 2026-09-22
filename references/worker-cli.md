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
| `renew`, `checkpoint`, `pop-inbox`, `verify-publication`, `prepare-comment`, `post-comment`, `confirm-comment`, `activity`, `await-input`, `await-resource`, `finish` | `--item ITEM_ID --token-file STATE_DIR/token`, plus command-specific arguments from `--help` |
| `memory-list`, `memory-read`, `memory-save`, `memory-forget` | Same claim-token arguments; see `references/memory.md` |
| `release-resource` | `--item ITEM_ID --token-file RESOURCE_TOKEN_FILE`, using `resource.token_file`, plus `--outcome quiescent` or `--outcome unclean` |

`fetch-issue` refreshes the ledger from Linear; `issue-context` reads the saved context.
Neither accepts `--token-file`. Tokens never belong in argv as `--token` values.
The argument table does not grant additional authority; use only your delegated item.

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
