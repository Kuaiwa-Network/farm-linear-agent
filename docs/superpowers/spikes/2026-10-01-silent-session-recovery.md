# Silent session recovery, October 1

The earlier [live acceptance round](2026-10-01-silent-delegation-live-checks.md) found that completed,
unarchived TestBot threads could prevent UI re-delegation from opening a new session. Waiting-work
preservation and cross-thread Stop worked; the fresh-session portions of AC-2 and AC-3 failed.
This change implements the D4 follow-up from the [design](../specs/2026-09-30-silent-delegation-design.md).

## Implementation

After the existing 90-second grace, an open card still delegated to this app keeps an existing delegation
job of the same skill, or takes over eligible work in its own delegation thread. Otherwise the receiver
opens an issue session through `agentSessionCreateOnIssue` and routes its creation through the existing
receiver. A waiting mention is not promoted into write authority: the new delegation session takes over
its messages. A claimed write worker remains fenced and the new session waits for withdrawal.

The public creation input has no caller-supplied idempotency key. The receiver writes a `session_openings`
row before the network call, with a unique non-secret issue-link marker. It sends one mutation per
episode. An interrupted response is recovered by finding one matching own session on the issue;
missing, malformed, archived or ambiguous results do not authorize another mutation. If a crash prevented
the request from reaching Linear, the operator must remove and re-add the delegation after the controller
observes its removal. Existing manual guidance remains the fallback for an explicit `success=false`.

The confirmed session queues a normal `created` event under Linear's event key. A five-second wait lets
the signed webhook supply guidance first. Duplicate delivery enriches session metadata but starts no
second job. Synthetic routing reads the card again and starts nothing after withdrawal, closure or a
new delegation episode. Stop arriving during creation cancels the pending creation.

The additive table preserves existing episode rows, job authority, private state and schema evidence.
Rollback leaves the table and any already-created remote session in place; it does not reverse a live job.
No production service, webhook, credential or publishing destination is changed by this development work.

## API compatibility probe

The selected disposable TestBot card was reopened and delegated only to TestBot. At approximately
06:09 UTC, `agentSessionCreateOnIssue` succeeded with existing completed threads still unarchived.
The resulting session had no human creator and no source comment. Linear delivered a normal `created`
webhook and the release receiver created exactly one delegation fix job, preserving its predecessor.
The typed `externalLinks` response recovered the same session using the issue-link marker.

The initial probe used the deprecated JSON `externalUrls` response, which Linear serialized as a JSON
string. This made local validation reject an already-created session. The mutation was not repeated;
inspection recovered that session and the client now uses typed `externalLinks { label url }` instead.
The probe job was cancelled and the card returned to Canceled with no delegate.

Sources: [Linear agent interaction](https://linear.app/developers/agent-interaction) and
[official GraphQL schema](https://github.com/linear/linear/blob/master/packages/sdk/src/schema.graphql).

## Validation

Validation results and the complete receiver acceptance round are recorded below after execution.
