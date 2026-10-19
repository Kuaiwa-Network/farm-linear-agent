# General workflow

New work uses [one issue owner](operating-contract.md) for bug fixes, features
and UI changes. The main worker has the necessary owned repositories together
and continues through implementation, candidate testing and draft delivery.
There are no compulsory repository handoffs, presentation subtasks or baseline
comparison rounds. The main worker may use native Codex subagents for useful
independent work and remains responsible for integration, verification and
delivery. These helpers do not create separate FarmBot jobs or claims.

The worker follows [work/SKILL.md](../skills/work/SKILL.md) and
[the concise CLI guide](../references/single-worker-cli.md). Repository-specific
instructions and actual human decisions still apply. Ordinary technical repairs
and document updates within the requested scope need no repeated permission.

Current service profiles are `chat` and `work`; historical labels do not choose
a different implementation workflow. One write job runs at a time for this
rollout. Chat remains available. Configure optional integrations only when the
chosen work needs them.

Migration is explicit: drain the old instance, preserve state and evidence,
adopt only a settled selected job, and resume only that issue. See
[the migration and rollback contract](operating-contract.md#migration-and-recovery).
No old job, child task or production reservation is silently converted or erased.
