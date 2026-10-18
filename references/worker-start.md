# Start or continue one worker

New single-owner work starts with [the concise CLI](single-worker-cli.md).
The stage navigation below documents archived launches and read-only chat;
it does not add stage handoffs or read requirements to `single-worker-v1`.

The launch AUTHORITY, current repository rules and current human decisions still
govern the attempt. `startup` is a navigation hint, never permission or a result.

Read `startup.read_together` in one batch, including the current root's AGENTS.md
(and applicable inherited instructions). On continuation, read the current phase
of the capability guide, not every other repository/stage. Missing files or
unclear rules require inspection; do not silently omit a mandatory instruction.
For a fresh intake, read the operating contract and repository map as usual.

Then execute `startup.start_argv` as an argument list using the configured
Python and ledger from dispatch (`claim --context --save-token STATE_DIR/token`).
It exclusively creates the configured item token file with mode 0600 on POSIX
and inherited DACL on Windows, refusing existing files, links and other paths.
It returns `token_file`, never the secret, plus the same
resume view as `issue-context --view resume`, without a second CLI invocation.
This is stored context, not a fresh Linear read. No custom storage script or
whole CLI-reference read is needed for this command. A storage/claim failure
requires stopping; preserve the file and do not retry an uncertain claim.
For older launches without that command, capture plain claim stdout privately: the
top-level `token` is secret. Create `STATE_DIR/token` with
`os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)` and write only that
token using `os.fdopen(..., "w", encoding="utf-8")`. Never overwrite an existing
file/link, print the token, change its Windows inherited DACL or repair permissions.
Stop on storage failure. Inspect only nonsecret claim fields and `context`.
The full storage procedure remains in `worker-cli.md#claim-token-storage`.

Honor `maintenance_requested` before doing work. Fetch the current issue through
`fetch-issue`, and compare its returned fingerprint with `context.fingerprint`.
If the live read changed it, reload `issue-context --view resume` and revalidate
against the actual new requirements before reusing decisions or reports. Preserve
real human questions and read the full issue context when
the resume view says stale or a required decision is missing. Do not call
`issue-context` again merely to obtain the same resume view. A failed/rejected
claim grants nothing. Do not repeatedly claim after an uncertain response.

For a validation/publication/acceptance continuation, batch independent reads of
the retained evidence index, clean source identities and exact next-step rules.
Verify the index once; review the actual relevant results. Do not recreate reports
or normalize the same completed comparison again when its inputs still match.
See `closeout-reuse.md`. Complete fresh delegation/publication checks where
required; startup data never caches that authority.
