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

The final Mac command was:

```sh
env -u FARMBOT_CONFIG -u FARMBOT_LINEAR_STUB_DIR python3 -B -m unittest discover -s tests -v
```

It passed **1,618 tests in 228.561 seconds**, with **17 Windows-only skips**. Focused coverage includes
20 own-session recovery scenarios, 58 API tests and 196 receiver tests. The initial API tests failed before
the methods existed; two later regression tests reproduced the late-webhook and intercepted-reply bugs
before their corrections. `git diff --check` passed. The full suite needs localhost listeners and process
inspection; it ran with the host access required by those offline fixtures.

Windows was not executed on this Mac. Hosted Windows CI follows the recovery branch's pull request;
its result is separate from Windows desktop service/Unity acceptance. The original release's previous
Windows result does not establish compatibility for this change.

## Integrated TestBot acceptance

TestBot temporarily served clean implementation commit `fbf4bc5`. Its existing development profile,
state root, app identity, endpoint, skills and publishing destinations were preserved. Only the chosen
disposable silent-delegation card was exercised. The original clean `3525b93` release was restored after
cleanup; production was not changed.

| Check | Measured result (UTC) |
|---|---|
| Completed threads, no active work | API delegation was observed at 06:43:36.701. After the grace, the receiver recorded one opening at 06:45:07.553, confirmed its session at 06:45:10.417, and queued one delegation fix at 06:45:16.410. No completed thread was archived. |
| Waiting mention handover | A real `@TestBot` comment was posted through the Linear connector at 06:47:27.768 while undelegated. Its mention chat reached `awaiting_input` with a pending question at 06:49:57.470. API delegation was observed at 06:50:55.310. One new opening at 06:52:26.171 superseded the chat and queued one delegation fix at 06:52:31.901. The exact test prompt was present in the new inbox; the mention session remained `delegation=0`. |
| Duplicate-session watch | The final fix was operator-cancelled by 06:54:46; the card stayed delegated through 06:58:48, more than four minutes. There were still exactly two opening attempts, nine historical jobs and nine sessions: no extra job/session, active work or owed closure. |
| Cleanup | The card returned to Canceled with no delegate. All nine test jobs were cancelled, no test PR was published, and no active test worker/resource or owed closure remained. The original four unrelated diagnostic findings were preserved. |

Linear delivered the real `created` webhooks as duplicates of the queued creation keys; each was handled
once. Both new sessions had no human creator. This verifies LC-4's no-open-thread and waiting-thread
cases, and the recovery behavior missing from the previous AC-2 and post-cancellation check.

The Mac locked during the run. The Linear connector's real rich mention and read-only runtime evidence
allowed the handover check to finish. The UI display and native cross-thread Stop were not re-exercised
in this run; the earlier live run checked cross-thread Stop, and this change adds offline coverage for
Stop arriving during creation, webhook duplication, lost responses/restarts, stale episodes, closed cards,
claimed-worker withdrawal and normal replies after creation.
