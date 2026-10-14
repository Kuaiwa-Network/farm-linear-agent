# Comparing existing Unity failures

Full suites still run. A draft may retain unrelated failures only when complete comparable baseline and
candidate evidence proves no new failures, the issue's affected tests pass, and all other required checks pass.
This is a draft-delivery verdict, never a claim that the full suite passed or permission to merge/deploy.

From the owned Client stage, save the candidate SHA and checkpoint, then request:

```text
<selected-python> -m agent --db DATABASE await-resource --item ITEM --token-file STATE_DIR/token --resource unity_slot --mode interactive --commit CANDIDATE --baseline
```

The controller validates the clean owned candidate, branch, clone, current remote-main ancestry and claim,
then reserves actual remote main. It does not reset source or the immutable target. The resource carries
`verification_role=baseline` and `candidate_commit`. Run the full identical selection on `resource.commit`,
save complete results, checkpoint and release. Wait for controller settlement before requesting the candidate
normally at its unchanged clean SHA. Prefer the same slot for the pair; an environment mismatch cannot waive
a failure. Do not edit a slot or operate its Editor processes. A gap/stall is never a completed baseline.

Keep each run in a private JSON file with this shape (all identities come from observed evidence):

```json
{
  "commit": "40-character-lowercase-SHA",
  "reservation_id": "actual-reservation-id",
  "job_id": "actual-test-job-id",
  "completed": true,
  "mode": "PlayMode",
  "selection": {"assembly_names": ["HotUpdate.PlayTests"]},
  "environment": {
    "host": "observed-host", "slot": "unity_slot:1",
    "unity_version": "observed-version", "platform": "observed-build-target",
    "test_framework_version": "observed-version",
    "config_digest": "SHA256-of-all-used-config-inputs",
    "package_digest": "SHA256-of-Unity-package-lock-and-used-FairyGUI-assets"
  },
  "summary": {"total": 1, "passed": 1, "failed": 0, "skipped": 0},
  "tests": [{"fullName": "Exact.Parameterized.TestIdentity", "state": "Passed", "message": "", "stackTrace": ""}]
}
```

Hash the sorted relative-path/content-hash inventory, with identical scope for the pair. Retain the inventory,
raw result and tool/instance identities alongside each normalized run. Git/LFS content identity inventories
may be used when each reserved checkout's hydrated bytes are independently verified against those objects.
Record additional environment inputs
(account, test settings, server/stub selection, relevant environment variables) when used; the environment
objects must match exactly. Never copy credentials into evidence. The tests array contains every leaf outcome,
not just failures. Parse counts from actual results, not discovery progress or exit codes. Failed-job MCP polls
that hide results are gaps until the same finished job's actual complete result is retrieved read-only. Use
bounded pages or a compact projection of every leaf's fullName/state/message/stackTrace plus the actual summary
when a transport cannot return the full verbose result. Validate counts and unique identities after assembly.
Once complete results and input identity are saved, checkpoint and release promptly; compare offline after
controller settlement, rather than holding the shared Editor while writing the diagnosis.

Scope JSON contains exact `affected_tests`, all Git `changed_paths`, and `unrelated_failures`, mapping each
candidate failure name to a nonempty `reason` and `evidence` reference. Review source dependencies and the
actual diff; absence of a failing test filename from the diff alone does not prove the failure is unrelated.
Include the issue's entire affected fixture, not one passing test. Failures whose stack points into a changed
path, affected failures, changed failure signatures, new/changed skips, removed tests and mismatched input
identity are blocked. New tests must pass. Known failures require the same state/message/failure site.
Failure messages retain path/URL differences; only line endings and outer whitespace are normalized.
Stack normalization ignores checkout paths, C# line shifts, IL offsets and assembly IDs.

Run the read-only comparison using the selected Windows Python or macOS `python3`:

```text
<selected-python> -m agent.test_baseline --baseline STATE_DIR/baseline.json --candidate STATE_DIR/candidate.json --scope STATE_DIR/comparison-scope.json
```

Exit 0 permits considering a draft; 1 blocks; 2 reports invalid/missing evidence. Save the full output in
`client.verification.comparison` with both evidence paths, SHAs, scope and unchanged input identities. Validate
the candidate still equals the verified clean HEAD before the normal fresh delegation, foreign-work and
`verify-publication` checks. Any source/input change requires a new pair. If the verdict is
`passed_with_existing_failures`, the draft lists every retained failure and skip and explicitly states the full
suite remains failed. Do not weaken assertions, suppress logs, skip failing tests or repair other issues.

No database migration is introduced. Baseline reservation identity lives in controller audit records; older
code can read the ledger but does not dispatch the comparison role or allow this draft verdict. Settle active
comparison reservations before rolling back. Running services use their installed revision until separately
deployed; editing a development checkout does not update worker authority.
