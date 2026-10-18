# Internal FGUI source task

This boundary applies when `task.parent_id` is present. Use the source-authoring,
named visual approval and certified native export procedures in `SKILL.md`.
Keep the original task identity, source entry, reviewed preview, attributed
approval and export receipt across resumes. Export only the required package
closure and explain actual dependency changes. No extra confirmation is needed
to carry out work already authorized by the current applicable decisions.

Your deliverable is the specified farmgui source and certified export. The parent
installs into Client, handles relevant metadata and dependency registration,
implements business behavior and performs final candidate verification and QA.
Do not repair Client metadata, create a Client draft or reserve Unity for a new
source task. A package's exported bytes are not proof of final functionality.

Once source is committed, its draft is verified and the approved export receipt
is retained in `plan.ui.receipt_id`, call `verify-ui-source`. Save its
`delivery_id` as `ui_source_delivery_id` and its `receipt_id` in the finish JSON,
with `source_only: true`, a summary, actual verification and `prs: [SOURCE_PR]`.
Then finish this task and exit. The parent cannot consume the artifact before
the controller certifies your process teardown. Issue delivery is the parent's
responsibility; do not post a separate final issue outcome.

Existing internal tasks already rooted in Client can use the same handoff when
Client is clean and unchanged from their original entry and no installation
journal needs recovery. The controller proves this; do not change/reset files
or claim no-change to pass it. A partly applied or completed legacy installation
retains its old installation/recovery protocol and evidence. Preserve and report
the exact unsupported recovery gap instead of re-exporting, re-asking visual
approval or silently replacing the candidate.

A new attempt after Stop can still be a legacy continuation: the controller
records `presentation_legacy_client_recovery` when its predecessor retains an
unfinished Client entry. Follow the retained installation recovery protocol in
that case, including original baseline, journal, guards and loading checks.
Changing the child key never hides that retained Client work.
