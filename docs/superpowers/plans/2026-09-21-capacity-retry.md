# Model capacity retry implementation plan

Goal: transient Codex model-capacity exits return the same authorized job to a durable delayed queue, preserving worktrees, checkpoints and model settings.

1. Add regression coverage for exact terminal capacity detection, delayed requeue before/after claim, retry limit, restart persistence, cancellation and unchanged ordinary failures.
2. Classify only the confirmed Codex terminal error. Persist retry count and next eligible launch time in the ledger; invalidate the exited claim and keep the same job/generation so reservations and checkpoints remain attached.
3. Scheduler requeues at 60, 180 and 600 seconds, reports the reason, and applies existing concurrency and fresh-delegation checks to relaunch. Never retire/delete worktrees on this path. Cleanup evidence is not fabricated or relaxed.
4. Run focused launcher/ledger/scheduler tests and inspect live failure evidence. Publish a draft PR. Stage deployment without interrupting active trials; restore FARM-1259 through the existing operator retry path while retaining its worktrees.
