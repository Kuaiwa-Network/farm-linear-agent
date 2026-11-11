# Version targets

People create the choices in Linear's workspace **Target version** label group.
On an issue, select a label such as `v1.2.3`, then delegate it. New version labels
must use `vMAJOR.MINOR.PATCH`, with three nonnegative numeric components and no
leading zeroes (except `0` itself). FarmBot uses the exact name as the base branch
in each configured source repository: `v1.2.3` selects `v1.2.3`. Bare `1.2.3`,
`v1.2`, arbitrary branch names, prerelease suffixes and uppercase `V` are rejected.
The special `main` choice remains supported.

This is FarmBot intake validation, not a restriction on Linear's label editor.
People can still create invalid labels; selecting one holds new work before any
branch lookup or creation. A valid-looking but unintended number cannot be detected:
selecting `v1.2.4` authorizes that version even if a person meant `v1.2.3`.

When new work starts, an existing target branch is reused. A missing version branch
is created from that repository's current default branch, after destination and
issue-authority checks. Selecting a label alone creates nothing. The `main` label
explicitly selects an existing `main`. Without a label, each repository's default
branch is selected.
An incomplete label read holds work instead of assuming the default branch; issues
with more than 250 labels must have that count reduced before delegation.

For an occasional feature branch, put exactly one plain line in the description:

```text
Target branch: feature/new-market
```

That line overrides the version label and applies to every source repository.
An empty/invalid line or conflicting lines hold launch. Remove the line to use the
label. An explicit `Target branch:` must already exist in every source repository.
Git branches and tags do not generate label choices.

## Configure one label sync owner

In the intended instance's private configuration, add:

```json
"version_targets": {
  "sync_labels": true,
  "sync_seconds": 300,
  "overrides": {
    "v1.0.0": {
      "farmgui": "main"
    }
  }
}
```

`sync_labels` defaults to false; enable it on exactly one instance because production and TestBot share the same workspace.
Updated instances understand target selections with sync disabled. Deploy this
support to every bot that may receive version-labeled issues before using the
selector; enabling sync on one bot does not upgrade the others. Keep their
per-version repository overrides consistent. `overrides` defaults to empty and
maps a version label to explicit repository exceptions, which must already exist.
In this example only `farmgui` uses `main` for version v1.0.0, while the remaining
repositories use `v1.0.0`. Legacy override keys remain readable for config compatibility
but cannot authorize new selections in unsupported formats.
The old `source_repo` setting remains accepted for config
compatibility but is ignored: no repository defines the picker.

Use the normal authorized maintenance/restart procedure after editing config.
The running service checks Linear at startup and then every five minutes. It ensures
the group and `main` choice exist, retains human-created choices, and updates only
FarmBot-managed descriptions to explain the exact branch name and creation behavior.
Old managed "Unavailable" descriptions are repaired. It never reads Git, discovers
version choices, deletes labels or modifies issue labels. Failed reconciliation
retries at the next interval; failures appear in the `version_labels`
heartbeat loop. Existing human descriptions are preserved. Duplicate groups or
children and the 250-label group limit require operator reconciliation.
Unsupported existing labels are retained; FarmBot-managed descriptions mark them
unsupported for new work. Sync never creates bare version choices or renames them.

## Saved targets and changes during work

Before creating worktrees, FarmBot resolves every repository and validates any
required creation destinations. Each new branch is an atomic create-if-absent:
another creator's branch is reused, never overwritten or advanced. Creation uses
the controller's verified private repository and current issue authority; workers
still only publish their own issue branches. Creation attempts and confirmations
are audited. A partial failure preserves already-created branches for retry;
there is no transaction spanning remote repositories and no automatic deletion.

FarmBot fetches the actual selected branches and records
all five repository identities, base branches, exact starting SHAs, the selection
and its timestamp in its existing ledger. Git target refs retain the objects.
The job's `branch_target` context exposes the record to the worker; normal cleanup
records separately preserve the exact working branches and candidate commits.
Retries, service restarts and continuations use these records without reselecting
a different starting commit or discarding progress. PR checks require the selected base.

Changing the issue target after preparation does not authorize retargeting. Restore
the original selection to continue, or use a new linked issue for the other target.
The original work remains available for an intentional backport. Ordinary target
branch advancement does not force a merge or invalidate relevant candidate tests.
A deleted or history-rewritten saved target stops publication until reconciled;
it is not automatically recreated.

Existing saved targets, including bare version selections and earlier `release/1.0`
mappings, stay unchanged. Retries and same-issue continuations may retain only that
exact saved selection; this does not authorize new bare version selections.
Fresh jobs now require `vMAJOR.MINOR.PATCH` (or `main`). Set explicit repository overrides if new
work should continue using an older prefixed branch. No branches are renamed.

The additive `work_targets` table does not backfill historical jobs. Those jobs
retain default-branch behavior and need a new linked issue for an explicit target.
This update adds no database migration. For rollback, settle targeted jobs and
disable sync before using older code, which may reject the new label names or map
fresh jobs to prefixed branches. Retain the database, target Git refs, recovery
snapshots and reports; switching code revisions does not undo them.
