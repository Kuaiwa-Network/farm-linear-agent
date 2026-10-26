# Worker reasoning policy

FarmBot selects the reasoning effort when launching a Codex worker attempt:

| Worker contract | Default effort | Intended use |
| --- | --- | --- |
| Current `chat` | `medium` | Read-only conversation, status, explanation and investigation |
| Current `single-worker-v1` work owner | `high` | Implementation, diagnosis and verification across owned repositories |
| Historical/other profiles | `xhigh` | Preserve the existing offline replay policy |

These are conservative starting defaults, not a prediction of task difficulty.
Required investigation, tests, authority checks and automatic approval review
remain the same. The model policy remains `latest-sol`; explicit model pins and
the existing supported-effort catalog selection retain their behavior.

The private host's `codex_workers` settings take precedence per skill and per
field. For example, `{"work":{"reasoning_effort":"xhigh"}}` retains extra-high
work effort, and `{"chat":{"model":"gpt-6.1-sol"}}` pins the model while using
the new chat effort default. `{"fix": ...}` configures historical fix workers,
not the current `work` owner. An explicit `xhigh` value is never silently reduced.
Each override applies to every subsequent attempt of that skill.

For an authorized rollout, inspect existing chat/work effort pins. To adopt the
defaults, remove just the relevant `reasoning_effort` keys or set the desired
values explicitly, preserving other configuration. Difficult or sensitive work
can retain an operator-selected `xhigh` override. This release does not change
production config or choose effort from issue prose, labels, priorities or retries.

Selection is per attempt, including ordinary resumes. It does not switch during
polling, tool calls or task phases, and it introduces no restart or escalation
protocol. Continue the authorized work at the selected effort. Native helpers
retain the runtime's inheritance; this policy supplies no helper override or
permission to create extra helpers merely to change effort. Workers must not edit
model/configuration settings or bypass a refusal to obtain another effort level.

Compare comparable completed tasks after an authorized trial: correctness and
review findings, retries/rework, elapsed time, uncached/cached input separately,
output and reasoning usage. Lower effort is not proof of lower total task cost.
No paid-model or live TestBot quality evaluation is implied by offline tests.

No database migration is needed. Settle workers before authorized config/release
changes; existing attempts keep their launch settings. Restoring explicit `xhigh`
chat/work pins restores the previous effort behavior on subsequent attempts.
Rolling back source alone also restores old defaults but preserves explicit pins.

The [official configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
documents `model_reasoning_effort`; supported levels depend on the selected model.
