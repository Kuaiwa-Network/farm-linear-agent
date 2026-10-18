# Read-only chat CLI

Use `execution.python`, `repo_root`, `database`, `state_dir` and
`startup.start_argv` from the launch payload. On Windows use that configured Python,
not a Python Store alias; on macOS use the configured Python 3 executable.

## Claim-token storage

Run `startup.start_argv` once. `claim --context --save-token STATE_DIR/token`
creates the file exclusively: on POSIX use mode 0600; on Windows keep the inherited
DACL and do not change ACLs. An existing file or uncertain result requires inspection,
not another claim or overwriting the file. Never print the token, change permissions
or bypass a refusal. For later authenticated calls use `--token-file STATE_DIR/token`.

All commands start with `PYTHON -m agent --db DATABASE`; they take `--item ITEM`.

| Command | Other arguments / purpose |
| --- | --- |
| `fetch-issue` | No token arguments; current Linear facts. |
| `issue-context` | No token arguments; `--view resume` or `full`. |
| `pop-inbox` | Claim token file; attributed current requests. |
| `renew` | Claim token file; inspect maintenance and withdrawal flags. |
| `checkpoint` | Claim token file and `--input FILE`; concise progress and handoff. |
| `request-repair` | Claim token file, `--message-id ID --summary-file FILE`; actual current implementation request only. Summary at most 8,000 characters. Exit on success. |
| `activity` | Claim token file, `--type response --body-file FILE`; send the answer. |
| `await-input` | Claim token file, `--reason question --question TEXT`; ask a real question and exit. |
| `resolve-question` | Claim token file, `--question-id ID --message-id ID`; actual attributed answers only. |
| `revalidate` | Claim token file and `--fingerprint VALUE`, after reviewing changed inputs. |
| `finish` | Claim token file, `--outcome delivered --input FILE`; finish the conversation. |
| `maintenance-handoff` | Claim token file; maintenance requested and a fresh checkpoint with handoff. Exit on success. |
| `withdraw` | Claim token file; follow the returned next action. |

A chat finish payload is:

```json
{"summary":"Answer provided","verification":"What was checked and its limits","prs":[],"comment_action_id":null}
```

Use `COMMAND --help` for syntax. Historical commands in the parser do not grant
chat write access. New implementation belongs to `work`; old staged state requires
host adoption, preserving its source, reports and genuine unanswered questions.
