# Start or continue one worker

The launch AUTHORITY, current repository rules and current human decisions still
govern the attempt. `startup` is a navigation hint, never permission or a result.

Read `startup.read_together` in one batch, including the current root's AGENTS.md
(and applicable inherited instructions). On continuation, read the current phase
of the capability guide, not every other repository/stage. Missing files or
unclear rules require inspection; do not silently omit a mandatory instruction.
For a fresh intake, read the operating contract and repository map as usual.

Then run `claim --item ITEM --worker-id WORKER --context` using the configured
Python and ledger from dispatch. It returns the usual claim fields plus the same
resume view as `issue-context --view resume`, without a second CLI invocation.
This is stored context, not a fresh Linear read. Capture stdout privately: the
top-level `token` is secret. Create `STATE_DIR/token` with
`os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)` and write only that
token using `os.fdopen(..., "w", encoding="utf-8")`. Never overwrite an existing
file/link, print the token, change its Windows inherited DACL or repair permissions.
Stop on storage failure. Inspect only nonsecret claim fields and `context`.
The full storage procedure remains in `worker-cli.md#claim-token-storage`.

Honor `maintenance_requested` before doing work. Fetch the current issue through
`fetch-issue`, preserve real human questions and read the full issue context when
the resume view says stale or a required decision is missing. Do not call
`issue-context` again merely to obtain the same resume view. A failed/rejected
claim grants nothing. Do not repeatedly claim after an uncertain response.

For a validation/publication/acceptance continuation, batch independent reads of
the retained evidence index, clean source identities and exact next-step rules.
Verify the index once; review the actual relevant results. Do not recreate reports
or normalize the same completed comparison again when its inputs still match.
See `closeout-reuse.md`. Complete fresh delegation/publication checks where
required; startup data never caches that authority.
