# Version targets

On an issue, use **Labels → Target version → 1.0**, then delegate it. FarmBot
selects `release/1.0` as the base in each configured source repository. The `main`
label explicitly selects `main`. With no label, FarmBot uses each repository's default branch.
Branches must exist before work begins. Labels do not create Git branches.
An incomplete label read holds work instead of assuming the default branch; issues
with more than 250 labels must have that count reduced before delegation.

For an occasional feature branch, put exactly one plain line in the description:

```text
Target branch: feature/new-market
```

That line overrides the version label and applies to every source repository.
An empty/invalid line or conflicting lines hold launch. Remove the line to use the
label. Patch releases normally share the minor-version branch: `v1.0.1` and
`v1.0.2` tags can both come from `release/1.0`. Only branch names matching
`release/<major>.<minor>` (no leading zeroes) are discovered; tags, patch-version
branches, and temporary feature branches do not generate labels.

## Configure one label sync owner

In the intended instance's private configuration, add:

```json
"version_targets": {
  "source_repo": "Farm-Client",
  "sync_labels": true,
  "sync_seconds": 300,
  "overrides": {
    "1.0": {
      "farmgui": "main"
    }
  }
}
```

`source_repo` is the configured repository whose release branches define the
picker. It defaults to `Farm-Client`. `sync_labels` defaults to false; enable it
on exactly one instance because production and TestBot share the same workspace.
Updated instances understand target selections with sync disabled. Deploy this
support to every bot that may receive version-labeled issues before using the
selector; enabling sync on one bot does not upgrade the others. Keep their
per-version repository overrides consistent. `overrides` defaults to empty and
maps a version label to explicit repository exceptions. It is optional; no missing
branch ever implicitly selects a default. In this example only `farmgui` uses
`main` for version 1.0, while the remaining repositories use `release/1.0`.

Use the normal authorized maintenance/restart procedure after editing config.
The running service checks at startup and then every five minutes. It reconciles
missing labels after a lost response, retains existing IDs and issue selections,
and marks removed branches unavailable through its managed descriptions. It never
deletes labels or modifies issue labels. Failed discovery leaves the current group
intact and retries at the next interval; failures appear in the `version_labels`
heartbeat loop. Existing human descriptions are preserved. Duplicate groups or
children and the 250-label group limit require operator reconciliation.

## Saved targets and changes during work

Before creating worktrees, FarmBot fetches the actual selected branches and records
all five repository identities, base branches, exact starting SHAs, the selection
and its timestamp in its existing ledger. Git target refs retain the objects.
The job's `branch_target` context exposes the record to the worker; normal cleanup
records separately preserve the exact working branches and candidate commits.
Retries, service restarts and continuations use these records without reselecting
the latest release or discarding progress. PR checks require the selected base.

Changing the issue target after preparation does not authorize retargeting. Restore
the original selection to continue, or use a new linked issue for the other target.
The original work remains available for an intentional backport. Ordinary release
branch advancement does not force a merge or invalidate relevant candidate tests.
A deleted or history-rewritten target stops publication until reconciled.

The additive `work_targets` table does not backfill historical jobs. Those jobs
retain default-branch behavior and need a new linked issue for an explicit target.
For rollback, settle all targeted jobs and disable sync before using older code,
which cannot enforce these targets. Retain the database, target Git refs, recovery
snapshots and reports; switching code revisions does not undo them.
