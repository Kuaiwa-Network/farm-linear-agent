# Publication timeout recovery plan

Goal: recover transient publication verification failures without human approval gates or stale authorization.

1. Add regression tests for Linear timeout recovery, GitHub transient classification, nonretryable authorization/TLS errors, cancellation during backoff, and durable bounded job requeue.
2. Retry the entire read-only verification sequence up to three attempts (2 and 5 second waits), renewing the claim and rereading delegation and destination each time. Do not retry mutations.
3. After transient exhaustion, revoke the claim and preserve work in a durable delayed queue (60, 180, 600 seconds, maximum three job retries); after the limit, fail with an explicit infrastructure reason. Human retry resets the allowance. Return structured non-verified status, never permission to publish.
4. Update fix instructions, including treating an absent initial Unity pin as a verification gap. Run relevant tests and independent review, publish a draft PR, deploy safely, and resume FARM-1245 through the host continuation path with the original commits intact.
