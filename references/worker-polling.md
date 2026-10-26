# Bounded worker polling

Current chat and work workers use this cadence when a submitted operation is
still running or a read-only status is unchanged. It changes how often the model
checks, not command timeouts, startup recovery, claim authority or resource ownership.

## Pending tool or command

Submit the operation once. If the execution tool returns a running cell, process
session or other wait handle, use that handle's documented wait operation. Request
`yield_time_ms=30000` initially; after an unchanged/no-output result, request
`yield_time_ms=60000` on subsequent waits. Use the equivalent duration for other
wait APIs and honor a lower supported tool limit. Never invent a wait argument
for a tool that does not support one. Prefer its completion notification when available.

A wait may return early with output or completion. Read that result immediately;
do not add a sleep after it has completed. Reset the next wait to 30 seconds when
there is meaningful new progress. A timestamp, heartbeat or repeated "still running"
notice alone does not reset the backoff. Avoid routine one- or ten-second polls;
a concrete earlier deadline or urgent control check can require a shorter wait.

Do not resubmit a command or test launch to learn whether it finished. A pending
startup claim is the same call: wait for its result, with no second claim and no
authenticated commands before it succeeds. Startup deadlines and the host's
capacity/retry rules remain authoritative. Reconcile ambiguous results normally.

## Separate status reads

After an initial `resource-status`, dependency status or test-job read says it is
still pending, wait 30 seconds before another read, then 60 seconds between
unchanged reads. Reset to 30 seconds after meaningful progress or a state change;
act immediately on readiness, completion or failure. Do independent authorized
work during the interval when useful. Helpers follow the same wait cadence;
only the main worker manages FarmBot claims and shared resources.

## Deadlines and control handling

After claiming, track the last successful renewal and the next `renew_minutes`
deadline and lease expiry. Before waiting, shorten the wait or renew when due so
the cadence cannot carry the worker past either deadline. A successful
`resource-status` already renews; do not add a duplicate `renew` solely because
that status is unchanged. While another operation runs, use a separate normal
renewal when needed. Respect operation timeouts and earlier control deadlines.

Inspect every returned result for errors, maintenance, cancellation, withdrawal
and new requests. Handle them immediately using the existing CLI and resource
rules. When only a pending tool handle is returning, continue the normal renewal
and inbox checks after claiming; a quiet tool is not evidence of no new requests.
Never hide these checks in a long blocking sleep or an unattended polling loop.
Claim refusal, retired claims, handoff and recovery responses retain their existing
stop/exit instructions; backoff does not authorize further authenticated work.

Unchanged status alone is not a test failure or permission to rerun tests, recover
resources, change models, bypass approval or administer the host. Preserve pending
operations and recovery evidence until their actual result is known.
