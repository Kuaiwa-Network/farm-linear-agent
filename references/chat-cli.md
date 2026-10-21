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
| `download-uploads` | Claim token file, `--out STATE_DIR/uploads`, optional repeatable `--url UNSIGNED_ISSUE_URL`; authenticated issue attachments for read-only investigation. |
| `upload-image` | Claim token file, `--file ABSOLUTE_PATH`; upload an inspected PNG/JPEG from this item's `state_dir` for a visual answer. Returns `asset_url`, SHA-256, size, content type and dimensions. |
| `pop-inbox` | Claim token file; attributed current requests. |
| `renew` | Claim token file; inspect maintenance and withdrawal flags. |
| `retry-approval` | Claim token file, `--error-file FILE`; complete not-executed capacity review message. Checkpoint first; exit on `retry_queued` or `retry_exhausted`. A retry grants no additional authority. |
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

To include an uploaded image, put `![Asset comparison](RETURNED_ASSET_URL)` in the
UTF-8 response file and send it with `activity --type response --body-file FILE`.
Upload alone does not post a reply. Keep the returned evidence in the checkpoint
and reuse a successful URL instead of uploading again just to retry the response.
Only single-frame PNG/JPEG files are supported: at most 20 MiB, 8192 pixels per
side and 16 million pixels total, with a matching portable filename. Files must
be ordinary files inside this item's state directory, without links or traversal.
The configured worker Python needs the pinned Pillow in `requirements-ui.txt`;
missing validation support is a host setup error, not permission to bypass it.
The command checks the configured ledger, active claim, enabled chat profile and
fresh open issue. Mention/operator chat needs no delegation; delegation-authorized
chat still requires the app's delegation. Withdrawal and cancellation fence the
upload. This grants no source, PR, issue-field or shared-resource write authority.

Use `COMMAND --help` for syntax. Historical commands in the parser do not grant
chat write access. New implementation belongs to `work`; old staged state requires
host adoption, preserving its source, reports and genuine unanswered questions.
