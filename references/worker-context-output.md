# Context and output discipline

Read the resume context returned by the successful startup claim before further
work. It already contains the current requirements, human/unknown comments,
attributed requests, questions and handoff. Do not immediately issue another
context read for the same information. The exact claim-first procedure is unchanged.

## Reuse context with an explicit change check

For a needed later read, prefer `issue-context --item ITEM --view resume`, with
the normal Python and database prefix. The response's `context_view.digest`
identifies the complete stored context, including evidence omitted from the
resume projection. Keep that response in memory or a private UTF-8 JSON file.

Only while that matching context is retained, use:

```text
issue-context --item ITEM --view resume --if-changed DIGEST
```

If nothing changed, `context_view.unchanged` is true and the command returns a
small receipt with coordination, fingerprint and inbox count. Reuse the retained
context; the absent sections are not empty or deleted. If anything changed, the
command returns the resume context with a new digest and `unchanged: false`.
Inspect the changes and replace the retained copy. Do not use an issue fingerprint
as the digest: requests, question state, recovery and Project Overview changes
can matter even when that fingerprint is unchanged.

This checks stored context, not live Linear state. Keep required `fetch-issue`
reads after new requests and before delivery, normal renewal/control/inbox checks,
revalidation of changed inputs and fresh publication checks. A digest is neither
a claim token nor evidence that work/tests are still valid. It introduces no
reason to poll context more often; use the [polling cadence](worker-polling.md).

If the retained response is missing, truncated or no longer available after
compaction, read resume without `--if-changed`. Read `--view full` whenever
`requires_full_view` is true, a decision refers to omitted text, or a needed fact
is missing. Inspect complete relevant human requests and unresolved questions;
never treat an omitted field or an unchanged receipt as permission or a passed check.

Read all applicable repository/skill instructions completely before the work
they govern. Reuse them only while their contents and scope are unchanged;
changing branches, revisions, directories or instructions may require another
read. Use focused searches and relevant source ranges for investigation instead
of repeated whole-file, whole-repository or full-context dumps.

## Keep large output available without repeating it in the conversation

For `single-worker-v1`, CLI `renew`, `checkpoint` and `revalidate` return compact
receipts by default. They omit non-empty checkpoint/evidence, target/branch-target
and task-request payloads; null/empty values stay inline. `response_view.omitted_fields`
names the omissions and `read_context` identifies the read-only resume command. `checkpoint_saved: true`
acknowledges the normal checkpoint write, not a test pass or approval. The database
retains the complete data. The CLI opts into control signals collected inside the
write transaction, from the same snapshot as the work item. A signal-read failure
rolls back the write; response formatting performs no database reads after commit.
Internal Ledger callers retain their previous output unless they opt into controls.

Read state, generation, lease expiry, authority, maintenance/withdrawal and recovery
fields in either view. Active `human_question` text and IDs are retained in full,
including the legacy consumed-message floor used by ordinary question reads.
`inbox_pending` counts unread messages without consuming them; `requires_issue_review`
reports a stored fingerprint difference/requeue request without revalidating it.
Handle those signals through the normal inbox/context/control workflow. Receipts
are acknowledgements, not cached permission, live Linear reads or complete context.
Recovery/questions and unknown new control fields are not truncated to meet a size
budget. Revalidation retains `claimed_fingerprint` and `previous_fingerprint`.

Choose `--view full` before running the command when its full work-item result is
needed; it includes the same control signals and checkpoint acknowledgement as
compact output. After a compact response, read `issue-context --view resume` for context.
To retrieve the complete stored work item/checkpoint, use the receipt's
`read_full_item`: `issue-context --view work-item` with the item ID. This reads only
the work item, without loading issue context or conversation history. It does not
renew, write or consume input; `--if-changed` remains resume-only. Do not repeat a checkpoint/
revalidation merely to see omitted data.
Historical staged jobs retain their previous response shape. Resource-status,
startup, other lifecycle commands, errors and raw Unity results are unchanged.

For builds, tests and other potentially large outputs, capture full stdout/stderr
under `state_dir/reports` using explicit UTF-8 and preserve the actual command's
exit code. Use a distinct report path so earlier evidence is not overwritten.
Return the command, exit code, result counts/status, relevant error
excerpts and absolute report path. Inspect the saved report before claiming a
pass; a short tail may omit an earlier failure. Preserve the native tool's process
ownership, pending handle, timeout and teardown behavior. Use the configured
Python and argument arrays, not a shell wrapper that masks exit status or detaches work.

Start routine text reads around 2,000 output tokens when the tool supports an
output limit; do not invent unsupported arguments. This is a display budget, not
a hard evidence limit. Read additional ranges or raise it when needed. Avoid
large default output allowances and repeated unchanged log dumps. If a tool's
output is truncated, use its retained result or the saved file and explicitly
identify anything still unavailable. Never rerun a test, build, claim, inbox pop
or other operation merely to recover output.

Do not blindly cap startup, lifecycle or control responses. Inspect claim outcome,
current requests, maintenance/withdrawal/cancellation flags, refusal/exit instructions,
failure details and required approval evidence in full. Keep complete approval-review
capacity errors for `retry-approval`. An output budget cannot waive these checks.
Use token-file commands and keep credentials/auth files out of reports and excerpts.
Inspect images with the image tools; use file references instead of printing base64
or converting image bytes into text-token estimates. Helpers return concise findings
with paths and verification evidence; they retain their existing authority limits.
