# Withdrawn work: design for FarmBot's undelegation fix

**Status: accepted, 2026-09-29; being implemented, not yet merged or deployed.** Current behaviour is in
[`docs/operating-contract.md`](../../operating-contract.md), which the implementation updates. On
2026-09-29 the operator settled the five questions of §9 as recommended. Code comments cite this
document as the "withdrawn-work design", with the IDs it defines. Appendix C records where the
implementation departs from the text below.

The scenario inventory (`scenarios.md`), the critique (`critique.md`) and the notes on Linear's
behaviour (`linear-behaviour.md`) that this design cites were working notes and are not in the
repository. §4 restates each scenario it decides, and Appendix A each critique finding's resolution.

IDs: P1-P8 are the principles of §2; A1-K9 the scenarios of §4, each tested under its ID in §6; X1-X4
the concurrency tests of §6; U and LC the unknowns and live checks of §3 and §8. R1 (the status reads
withdraw work, P2 and P3), R8 (an unreachable issue: three not-found reads over at least 15 minutes
while other calls succeed, E3) and R10 (honest forwarding notices, §7.1) name remedies from the working
notes, as do D6-D10 and G19. D16 is the
[feature-workers design](2026-09-24-feature-workers-design.md)'s decision that a reply re-routes.

Design of 2026-09-29, based on FarmBot main at 6fc2b1f. Every `file:line` is relative to the repository
root at that revision and was re-read for this design. Tags: **[V]** verified in code, **[E]** Linear's
public docs or schema, **[I]** inference, **[U]** unknown, settled by a live check in §8.

The text names no card, person, host, path or ID, so it can go into the public repository as it is.

## 1. Problem

### 1.1 The incident

1. A person delegated a card with no Bot label. FarmBot opened a read-only conversation (`chat`), which
   asked a question and parked in `awaiting_input`.
2. The person removed the delegation. Linear archived the waiting session.
3. The person labelled the card and delegated it again. Linear opened a new session. FarmBot answered
   "已有进行中的工作（chat），请在原会话继续" and started nothing.
4. Every reply in the new session got the same refusal. An operator had to cancel the item by hand.

### 1.2 Why it happened [V]

- **Losing the delegation never cancels anything on a deployed host.**
  - `_cancel_undelegated` acts only on skills with an initial root (`agent/lifecycle.py:43-45`).
  - Neither `skills/chat/skill.json` nor `skills/fix/skill.json` has one.
  - Tests and the contract pin this: `tests/test_lifecycle.py:133-144`, `docs/operating-contract.md:132`.
- **One active item per issue** (`agent/ledger.py:422-424`). While the parked chat was active:
  - a new delegation that routes to work is refused (`agent/receiver.py:287-291`);
  - every reply in the new session re-routes into the same refusal (`agent/receiver.py:238-243`).
- **FarmBot reads no session state**, so it cannot know that the old session was archived.
- **Stop works per session** (`agent/receiver.py:195`). An archived session cannot be stopped.

### 1.3 The same mechanisms strand more than chat

- A waiting or queued `fix` is never cancelled.
  - A queued `fix` is read from Linear on every scheduler tick (about once a second) without end.
  - A `fix` that is granted the Unity slot keeps the host's only slot.
- A worker can park itself after undelegation: `await-input` checks no delegation (`agent/__main__.py:418-428`).
  - The fix SKILL contradicts itself on this: `skills/fix/SKILL.md:268-269` against `:326-327`.
- Continuations can land in a session that has ended (`agent/ledger.py:1748-1757`).

`scenarios.md` lists every scenario (rows A1-J6), and `critique.md` adds findings 1.1-5.3.

### 1.4 Goal

- Every scenario has a decided behaviour and a unittest.
- This explicitly includes undelegation where Linear leaves the session open.
- Nothing here depends on a Linear behaviour nobody has verified.
- The change is one pull request.

## 2. Principles

### P1. Authority is recorded once, when an item is created

A new column, `work_items.authority`, takes one of three values.

| Authority | Given to |
|---|---|
| `delegation` | every write item (fix, feature, fgui) wherever it is created; a chat created by a delegation `created` event or a D16 re-route while the card is delegated to this app; a chat that replaces a delegation-authority chat (P4) while the card is still delegated |
| `mention` | a chat created by an @mention, or by a person's message while the card is not delegated to this app |
| `operator` | a chat created by `agent.service enqueue`. This is keyed on the operator path, not on `sessions.delegation`, which `enqueue` sets from the live delegate (`agent/service.py:176-177`). |

Authority is never re-derived. It changes in one case only: a person answers a parked
delegation-authority chat while the card is not delegated. That chat then resumes as `mention`, because
the person is talking to FarmBot without a delegation. This keeps the answer from being cancelled a
minute later.

### P2. Withdrawal: what it means per state

Work is **withdrawn** in three cases:
- the delegation that authorised it is gone, confirmed by two reads (P3);
- a newer delegation session takes the card over (P4);
- the issue is unreachable (R8).

Withdrawal never touches `mention` or `operator` chats, except when the issue is unreachable.

| State | On withdrawal |
|---|---|
| `queued`: never launched, retry-delayed, launched but unclaimed, or with a handoff pending | Cancel now through `scheduler.stop(states=…)`. Today's reservation handling applies (`agent/ledger.py:1545-1553`), and `stop` kills a spawned or retiring worker (`agent/scheduler.py:313-316`). |
| `awaiting_input` | Cancel now. |
| `awaiting_resource`, with the reservation queued, active or mid-switch | Cancel now. A queued reservation is cancelled; an active one becomes `cancel_requested` and the pool probes and releases it (`agent/ledger.py:1366-1368`). |
| `running` (claimed) | Do not revoke the claim. Set a withdrawal flag with a deadline (grace = 2 × the skill's `renew_minutes`: 20 min for fix, 10 min for chat). The worker sees the flag, saves a checkpoint and runs the new `withdraw` command, which cancels its own item. At the deadline the controller cancels and kills it the way Stop does. |
| `blocked` | Unchanged. It holds no active slot. Closure still cancels it. |
| terminal, or waiting only for cleanup | Nothing to do. |

### P3. A read cancels only what it has seen twice, and only what existed before it

- The first read that finds the delegate is not this app records `issue_checks.undelegated_since`.
- A later read acts only when it also finds no delegation and started at least `reconcile_seconds`
  (default 60) after that first observation.
- Any read that finds the delegation clears the mark, and clears any `undelegated` withdrawal flag.
- A read never cancels or flags an item created after the read started. This resolves the race in
  critique 1.1.

### P4. A new delegation owns the conversation

A delegation `created` event, or a D16 re-route, arrives in a new session S2 while item X is active in
another session. S2 takes over the conversation.
- **X unclaimed, or X is a chat:** X is **superseded** in one ledger transaction.
  - X is cancelled.
  - The item S2 routes to is created in S2.
  - X's messages are copied into it.
  - A write item of the same skill links X as its predecessor.
- **X a claimed write item:** X is withdrawn (P2). S2's event waits until X ends, then runs.
- An item's `session_id` never changes, so there is no "adopt" (critique 2.8).

### P5. Stop reaches the work the session talked to

A Stop in session S stops the first of these that exists:
1. S's own active item;
2. the item S's messages were forwarded to;
3. the issue's active item, when S is the issue's latest delegation session.

### P6. Always kept, never done

**Always kept:**
- pushed branches and draft PRs (cleanup preserves source as recovery refs, `agent/scheduler.py:482-494`);
- checkpoints and plans, reached through the successor's `predecessor_id` lineage;
- inbox messages, copied on supersede and otherwise visible in `conversation_history`
  (`agent/ledger.py:2131-2143`);
- audit rows and `job_cleanup` evidence;
- Linear comments and labels, including `needs-more-info`.

**Never done:**
- close a PR or delete a branch;
- set itself back as the delegate;
- remove a label;
- change the status or the assignee.

### P7. Notices

- A cancellation posts **one** notice.
  - A chat's notice never mentions branches.
- The notice goes to the item's own session, best effort.
  - A `local-` item's notice is an issue comment (`agent/scheduler.py:244-245`).
- In the same transaction, any pending heartbeat send for the item is dropped. That prevents a second
  "工作已停止。" response.
- A notice is never retried in a loop.
- Posting into an archived session may fail [U, LC10]. The failure is swallowed, as `_notify` already
  does (`agent/scheduler.py:248-249`).

### P8. Unknown never cancels

- An error, a null session or an unclassified GraphQL failure never counts as withdrawal.
- Only "not found" or `trashed`, repeated over a window while other calls succeed, counts as an
  unreachable issue (R8).

## 3. Detection

| Signal | Source | Latency | Use |
|---|---|---|---|
| Issue `update` webhook, then a status read | `agent/receiver.py:97-111` | about 1 s, for an issue FarmBot tracks; whether Linear sends one for a delegate change is U4 | first observation, or confirmation (P3) |
| Status poll | `Lifecycle.tick` (`agent/lifecycle.py:48-50`) | every `reconcile_seconds` while the issue has an active or blocked item (`agent/ledger.py:690-695`) | first observation and confirmation; closure; R8 |
| Launch preflight | `Lifecycle.preflight` (`agent/lifecycle.py:52-58`) | before a launch; **now at most once per interval** while the issue is marked undelegated or failing | launch gate; observation |
| The receiver's fresh read (DT1) | `agent/receiver.py:212` | at each AgentSessionEvent | asks for an immediate status read when not delegated; clears the mark when delegated; `can_resume`; the authority of new chats |
| A new delegation `created` in another session | receiver | at the event | P4. This covers remove-then-redelegate even when no read saw the gap (A6), whether or not Linear archived the old session. |
| Stop signal | `agent/receiver.py:130-131` | at the event | P5 |
| Worker `fetch-issue` | `agent/__main__.py:337-342` | at the worker's checks | the worker withdraws; asks for a status read (it never cancels anything itself, critique 2.10) |
| Worker `await-input` | CLI | at the call | a fresh status read refuses a delegation-authority pause on an undelegated card |

### How FarmBot learns that a session was archived

It does not read session state in this change, and it does not need to.

- **Archival with delegate removal (the incident):** the status reads see the removal.
- **Dismissal:** it "removes the agent as delegate" [E, `AgentSession.dismissedAt` in the schema], so the
  reads see it.
- **Re-delegation:** it arrives as a `created` event in a new session, which triggers P4.

### How FarmBot learns of undelegation that leaves the session open

The same reads detect it. Covered cases:
- removal through the API or an automation;
- removal while the item sits in a mention session;
- any UI path that leaves the session open.

The notice is posted into the still-open session, and every later reply or Stop there has a defined
outcome (§4, row A2).

### Archival while the card stays delegated (B1)

Whether Linear allows this at all is U, settled by LC8. FarmBot cannot see it. The item is kept, and
every escape still works:
- a mention answers it (P4 for a chat, forwarding for a write item);
- a Stop in that mention session reaches it (P5);
- removing the delegation cancels it (P2);
- the doctor lists items parked for more than 7 days.

If LC8 shows B1 happens, a follow-up reads `agentSession(id){archivedAt dismissedAt}` for parked items.
It treats only a non-null `archivedAt` or `dismissedAt` as ended, and a null result or an error as
unknown (critique 2.6).

### Safe fallbacks where Linear's behaviour is unknown

| Unknown | Fallback |
|---|---|
| U4: whether a delegate change sends an Issue webhook or bumps `updatedAt` | The poll observes within one interval. Decisions use each read's own result and its start time (P3), never the stored snapshot, so equal-version overwrites (`agent/ledger.py:672`) cannot cancel new work. `request-repair` checks the fresh read (J4). |
| U6: whether a UI re-delegation always opens a new session | If no session opens (API re-delegation), P3's confirming read finds the card delegated, clears the mark, and nothing is cancelled. |
| LC10: whether posting into an archived session fails | Best effort, no retry (P7). A cancelled fix has already announced its branches on the card through its started, blocker and delivery comments and PR attachments, so no extra comment is posted. |
| U7: a deleted issue | `trashed: true` is treated as archived, which is closure. "Not found" counts toward R8. |
| U11: `webhookTimestamp` on retried deliveries | Unchanged. Listed in §8. |

### Linear's rate limit

- An OAuth app gets 5,000 requests per hour per app user. Over the limit Linear returns HTTP 400 with
  `RATELIMITED` [E, linear.app/developers/rate-limiting, read 2026-09-29].
- Preflight used to re-read an undelegated issue on every tick (G3). Now it waits for the issue's next
  due read.
- A `RATELIMITED` or authentication error is transient. It is never counted toward R8.

## 4. Behaviour per scenario

IDs follow `scenarios.md`; K1-K9 are the critique's additions. "R1" means P2 and P3 applied by the
status reads, and "supersede" means P4. Tests are named in §6.

### A. Delegation withdrawn or moved

| # | Scenario | New behaviour |
|---|---|---|
| A1 | UI removal archives the session while a delegation chat waits (**the incident**) | The first read marks the issue. The confirming read, 60-120 s after the removal, cancels the chat and posts UNDELEGATED_CHAT best effort. The card is free. A re-delegation before that point is C1. |
| A2 | **Removal leaves the session open** (API, automation, mention-session item, any non-archiving path) | Same cancellation, and the response lands in the open session. **A later reply there:** the item is terminal, so a `mention` chat starts and answers. **Stop there:** "这里的工作已经停止。" **During the confirmation window:** a reply to a waiting fix is saved and told the work is stopping; a reply to a waiting chat resumes it as `mention`. |
| A3 | Moved to another agent | As A1/A2. A running write worker is flagged, and `verify-publication` refuses to publish meanwhile (`agent/__main__.py:516-518`). |
| A4 | Moved to the other FarmBot instance | As A3. Rated M (critique 1.2): `verify-publication` and `foreign-work` already guard every push. |
| A5 | Assignee changed | Nothing: FarmBot reads only the delegate. LC12 checks whether "No assignee" clears it. |
| A6 | Removed and re-added between two reads, or while FarmBot was down | The new `created` supersedes (C1/C2); no read is needed. With an API re-add, no session opens and nothing is cancelled (§3). |
| A7 | Session dismissed | The delegate is removed [E], so as A1. |
| A8 | A mention-session chat waits when the delegation goes | Kept: `mention` authority. |
| A9 | A chat worker is running | Flagged. `await-input` refuses, so the chat answers and finishes. After 10 min the controller cancels and kills it. |
| A10 | A fix or feature worker is running, with or without an interactive slot | Flagged. `verify-publication`, `handoff-repository`, `await-input` and `await-resource` refuse with "delegation withdrawn". The worker checkpoints and runs `withdraw`: the item is cancelled, its lineage is kept, and UNDELEGATED is posted. Otherwise the controller stops it at 20 min. |
| A11 | Delegation removed between the receiver's read and item creation | The item is newer than the read that marked the issue, so the next confirming read cancels it. Preflight refuses its launch meanwhile. |

### B. Session ended or idle while the delegation stays

| # | Scenario | New behaviour |
|---|---|---|
| B1 | Session archived, card still delegated (only if LC8 shows this is possible) | Kept. The escapes in §3 apply, and the doctor lists items parked for more than 7 days. |
| B2 | Session goes stale | Nothing: another activity recovers it [E]. |
| B3 | A reply after FarmBot's `response` | Unchanged. |

### C. Re-delegation, second sessions and duplicates

| # | Scenario | New behaviour |
|---|---|---|
| C1 | A new delegation S2 while X waits unclaimed in S1 (**the incident's second half**) | Supersede in one transaction (`Ledger.supersede`). S2 is acknowledged first, with the note "此前在另一个会话中的工作已转到这里继续。" Then S1 gets SUPERSEDED, best effort. No refusal text remains. |
| C2 | A new delegation while X runs in S1 | **X a chat:** superseded at once; the next tick kills it. **X a write item:** flagged `superseded`. S2 gets DEFER_ACK, and S2's event is `deferred` until X is no longer running (at most grace + 5 min). It then runs as a normal delegation, and `create_work_item` links X. |
| C3 | A delegation while a mention chat waits or runs | Supersede (D6). The chat's messages go to the new item. |
| C4 | A delegation while an operator's `local-` item exists | As C1/C2. The note to the `local-` item goes as an issue comment. |
| C5 | A mention in M while X is active elsewhere | **X a chat, unclaimed:** supersede into M, keeping X's authority while the card is delegated. **X a chat, running:** steer, as today. **X a write item:** forward, as today, with an honest notice (R10) and `sessions.forwarded_item` recorded. |
| C6 | A successor waits for its predecessor's cleanup | Rule unchanged. The heartbeat says what it is waiting for, and the doctor lists `cleanup_pending` (it already does). |
| C7 | A continuation or `retry` after the delegation session ended | `_cancelled_successor` records the latest non-`local-` delegation session, which is the newest open one after a re-delegation. A `local-` predecessor keeps its session. |
| C8 | Two delegation sessions with no removal between them (U6) | As C1/C2. |
| C9 | Marked Duplicate | Closure, plus one CLOSED notice per active item. |

### D. Stop

| # | Scenario | New behaviour |
|---|---|---|
| D1 | Stop in the item's own session | Unchanged. |
| D2 | Stop in S2 or M while the work lives in another session | P5. The reply names the other session's work. Stop in a superseded S1: "这里的工作已转到新的委派会话；要停止，请在那个会话里按 Stop。" |
| D3 | The Stop reply fails | Unchanged. |

### E. Issue closed, removed or out of reach

| # | Scenario | New behaviour |
|---|---|---|
| E1 | Done or Canceled | Closure as today. **New:** one CLOSED or CLOSED_CHAT response for each *active* item; blocked items are cancelled silently. |
| E2 | Archived | As E1. |
| E3 | Deleted | `trashed` → archived → E1. A read returning null or not-found starts R8: 3 reads over at least 15 min, while other calls succeed, cancel everything unclaimed and silently; claimed items are flagged. |
| E4 | Moved to another team | Out of this change. The doctor flags an active item whose identifier lies outside `issue_prefix`. |
| E5 | The app loses team access | A forbidden error is not "not found": nothing is cancelled, and the doctor lists the status errors (existing). |
| E6 | The app is revoked, or rate limited | Transient: back off only. Never R8. |
| E7 | Reopened | Unchanged. |

### F. Item states when the delegation goes

| # | State | New behaviour |
|---|---|---|
| F1 | queued fix | R1 cancels it. Preflight reads at most once per interval (G3). |
| F2 | queued delegation chat | Preflight refuses the launch while the card is not delegated, and R1 cancels it. |
| F3 | retry-delayed | R1 cancels. |
| F4 | launched, not yet claimed | R1 cancels. `stop` kills the spawned worker, and its claim then fails (`agent/ledger.py:872-873`). |
| F5 | awaiting_input, question | R1 cancels. The label stays (D8). |
| F6 | awaiting_input, `waiting` notice | R1 cancels. |
| F7 | awaiting_resource, reservation queued | R1 cancels; the reservation is `cancelled`. |
| F8 | granted: queued with an active reservation | R1 cancels. The reservation becomes `cancel_requested`, and the pool settles the slot (G4 closed). |
| F9 | mid-switch or mid-batch | R1 cancels. `resume` then refuses, and the slot goes back (`agent/slots.py:270-283`). |
| F10 | handoff pending teardown | R1 cancels. `cancel` clears the handoff, and `stop` kills the retiring worker. |
| F11 | mid-handoff | The CLI refuses. The SKILL says to withdraw. |
| F12 | cleanup pending | Nothing. |
| F13 | blocked | Unchanged (G19 accepted, L). |
| F14 | `finish` after publication while flagged | `delivered` is accepted. `blocked` is recorded as `cancelled`, so a re-delegation continues it (critique 2.5). |

### G. Per skill

| # | Skill | New behaviour |
|---|---|---|
| G1 | chat, `delegation` | P2 in every state. |
| G2 | chat, `mention` or `operator` | Kept on undelegation. Closure and R8 cancel it. |
| G3 | fix | P2. |
| G4 | feature | P2. The rule no longer depends on `initial_root`. |
| G5 | fgui | P2, because it is a write skill. |

### H. Lost deliveries and downtime

| # | Scenario | New behaviour |
|---|---|---|
| H1 | The Issue webhook is lost | The poll observes and confirms. |
| H2 | The `created` event is lost | Out of scope (App. B). The person delegates again. |
| H3 | A `created` event marked `uncertain` | Out of scope (App. B). |
| H4 | A reply is lost | The doctor lists items parked for more than 7 days. |
| H5 | The controller is down during the removal | At restart the first poll marks and the next confirms. |
| H6 | A late `created` | P4 as usual. |
| H7 | A retry keeps its timestamp | Out of scope. Live check. |

### I. Operator actions

| # | Scenario | New behaviour |
|---|---|---|
| I1 | `enqueue`, then undelegation | A write item (`delegation`) is cancelled with an issue comment. A chat (`operator`) is kept. |
| I2 | Operator `cancel` | Posts one OPERATOR note: in the session, or as a comment for `local-`. Pending heartbeat sends are dropped. |
| I3 | `retry` of a cancelled item | Session as C7. |

### J. Worker-side and races

| # | Scenario | New behaviour |
|---|---|---|
| J1 | A worker parks after undelegation | `await-input` does a live read for a delegation-authority item. `Ledger.await_input` refuses a flagged item. R1 on every confirming read is the backstop. |
| J2 | The fix SKILL contradicts itself | Resolved: withdraw, never `await-input`. |
| J3 | The chat SKILL has no rule for undelegation | New rule. |
| J4 | The stored delegate is stale in `request-repair` | The CLI passes its fresh read into `_repair_work`. |
| J5 | Supersede or forward races a cancel | Up to two re-routes on `StaleRouting`. |
| J6 | The app identity is unknown | Preflight refuses delegation-authority launches. |

### K. Critique additions

| # | Scenario | New behaviour |
|---|---|---|
| K1 | Linear re-sends `created` with a reused session id | Out of scope, with a live check. Fallback: a reply there starts a chat, which can request the fix. |
| K2 | Deploy-time sweep | Existing rows: `authority` NULL reads as `delegation` for write skills and `mention` for chat. Release steps are in §5.3. |
| K3 | Blocked, then re-delegated | Withdrawal ends in `cancelled`, so the lineage holds. |
| K4 | A label is removed from a delegated card | Not a stop signal. Contract text only. |
| K5 | The conversation across a cancel and a new chat | `conversation_history` already carries the messages. |
| K6 | A `local-` chat blocks a delegation | Supersede. |
| K7 | Rate-limited or unauthenticated app | Transient. The preflight throttle applies. |
| K8 | The delegate flaps (automation) | P3 debounce. |
| K9 | A stale lifecycle read races a re-delegation | P3's read-start fence. |

## 5. Code changes (one PR, six commits)

The commits are ordered so that each one passes the offline suite.

### 5.1 By commit

**Commit 1: schema, authority, texts**

- **`agent/ledger.py`**
  - Additive columns in the migration list (`:546-560`):
    - `work_items.authority TEXT`
    - `work_items.withdraw_deadline REAL`
    - `work_items.withdraw_reason TEXT`
    - `issue_checks.undelegated_since REAL`
    - `issue_checks.unreachable_since REAL`
    - `sessions.forwarded_item TEXT`
  - `AUTHORITIES = ("delegation", "mention", "operator")`.
  - `_authority(row)` returns the stored value, or, when it is NULL, `"delegation"` for a skill in
    `WRITE_SKILLS` and `"mention"` otherwise.
  - `_view` (`:596-606`) adds `authority` (resolved), `withdraw_deadline` and `withdraw_reason`.
    `issue_context.coordination` (`:2102-2103`) adds `authority` and `withdrawn`.
  - `create_work_item(..., authority=None)` validates the value. When it is None: `delegation` for write
    skills, `mention` for chat.
  - Its body moves into `_insert_item(issue, session_id, skill, target, authority)`, which assumes the
    caller holds the transaction. The predecessor rule at `:758-761` is unchanged.
- **New `agent/withdrawal.py`**
  - Every notice text in §7.1.
  - `notice(skill, reason, bot)` picks the text: `reason` is one of `undelegated`, `superseded`, `closed`
    or `operator`; the chat variant has no branch wording.
  - `grace_seconds(manifest)` returns `2 * budget["renew_minutes"] * 60`, or 1200 when the manifest is
    unknown.
- **`agent/service.py` `enqueue`** (`:187`) passes `authority="delegation"` for write skills and
  `"operator"` for chat.

**Commit 2: cancel hygiene, supersede, withdrawal in the ledger**

- **`Ledger._cancel_row(row, reason, *, drop_progress)`** holds the body of `cancel` at `:1541-1555`.
  With `drop_progress`, it also runs `DELETE FROM session_progress WHERE item_id=?` when that table
  exists.
- **`cancel(item_id, reason, *, states=None, drop_progress=False)`**.
- **`class StaleRouting(LedgerError)`**.
- **`supersede(expected_id, expected_states, *, session_id, skill, target, authority, text, author, received_at, reason)`**
  runs in one `BEGIN IMMEDIATE`:
  1. Re-read X. Raise `StaleRouting` if X is not in `expected_states`.
  2. `_cancel_row(X, reason, drop_progress=True)`.
  3. `_insert_item(...)`. Its predecessor rule links X when the skill is the same write skill.
  4. Copy every inbox row of X, keeping author and time, as `_repair_work` does (`:1742-1743`).
  5. Push `text` when it is non-empty.
  6. Audit both items.
  7. Return the pair `(cancelled, created)`.
- **`flag_withdrawal(item_id, reason, deadline)`** sets the flag only when the item is `running` and has no
  flag yet. It returns True when it set the flag, and raises `StaleRouting` when the item is not running.
- **`clear_withdrawals(issue_id)`** clears flags whose reason is `undelegated`.
- **`withdraw(item_id, token, *, delegated, closed)`**:
  - requires the running claim (`_owned`), and one of: a flag is set, `delegated` is False, or `closed`
    is True;
  - runs `_cancel_row(drop_progress=True)` and audits `withdrawn`;
  - returns the view and the reason: the flag's reason, else `undelegated` or `closed`.
- **Refusals of a flagged item** with "delegation withdrawn: save a checkpoint, then run withdraw and
  exit": `await_input` (`:1192`), `await_resource` (`:1222`), `handoff_repository` (`:1114`).
- **`finish`** (`:1981-2048`): for a flagged item, the outcome `blocked` becomes `cancelled` through
  `_cancel_row`, with no requeue.
- **`queue()`** (`:796-799`) adds `AND withdraw_deadline IS NULL`.
- **`mark_undelegated(issue_id, observed_at)`** sets `undelegated_since=COALESCE(undelegated_since, ?)` and
  returns the stored value.
- **`clear_undelegated(issue_id)`** sets it to NULL and calls `clear_withdrawals`.
- **`finish_status_check(..., unreachable=False)`** sets `unreachable_since` when it is still NULL, or
  clears it.
- **`_repair_work(..., delegate_id=_STORED)`** (`:1697-1699`) checks a passed fresh `delegate_id` instead of
  the stored one. Its new item gets `authority="delegation"`.
- **`_cancelled_successor(previous, reason)`** (`:1748-1757`):
  - session: the predecessor's, when that is `local-`; otherwise `_owner_delegation(issue)["session_id"]`,
    falling back to the predecessor's;
  - `authority="delegation"`.
- **`record_forward(session_id, item_id)`**.
- **`stop_target(session_id)`** returns `(item, where)`. `where` is `own`, `forwarded`, `latest_delegation`,
  `moved` (the session's last item was superseded), `stopped` (it was cancelled) or `none`.

**Commit 3: lifecycle and scheduler**

`agent/lifecycle.py` replaces `_cancel_undelegated`. `UNDELEGATED_STATES` stays as the unclaimed set,
because tests import it.

- **`refresh(issue_id)`**:
  1. `started = self.clock()`, then `raw = api.issue_status(...)` and `current = apply_issue_status(raw)`.
  2. If the issue is closed, run `_close(issue_id)`.
  3. Elif the card is not delegated, or `app_user_id` is None:
     - `since = mark_undelegated(issue_id, started)`;
     - if `app_user_id` is set and `started - since >= self.interval`, run
       `_withdraw(issue_id, started, "undelegated")`.
  4. Else run `clear_undelegated(issue_id)`.
  5. On a `LinearError` of kind `not_found`, run `finish_status_check(..., unreachable=True)`, then
     `_withdraw(issue_id, started, "unreachable", everyone=True)` when all of these hold:
     - `failures >= 3`;
     - `now - unreachable_since >= 900`;
     - `getattr(api, "last_success_at", 0) > unreachable_since`.

     Every other error keeps today's backoff.
- **`_close(issue_id)`**: for each item that is not finished:
  - an active item: `scheduler.stop(id, reason, states=ACTIVE_STATES, notice=notice(skill, "closed"))`;
  - a blocked item: `scheduler.stop(id, reason)`, with no notice.
- **`_withdraw(issue_id, before, reason, everyone=False)`** skips an item when `created_at >= before`, or,
  unless `everyone`, when `authority != "delegation"`. Then:
  - an unclaimed state: `scheduler.stop(id, …, states=UNDELEGATED_STATES, notice=…)`, with no notice for
    `unreachable`;
  - `running`: `ledger.flag_withdrawal(id, reason, now + grace_seconds(skill))`;
  - blocked, with `everyone`: `scheduler.stop(id, reason)`.
- **`preflight(item)`**:
  - Return False without a read when the check is not due yet (`due_at > now`) and either
    `check["error"]`, or `check["undelegated_since"]` is set and the item has `delegation` authority.
  - Otherwise refresh, then admit only when the issue is open and either `authority != "delegation"` or
    `app_user_id` is set and equals the delegate.

`agent/scheduler.py`:
- **`stop(..., notice=None)`** passes `drop_progress=notice is not None` to `cancel`.
- **`_expire_withdrawals()`** runs first in `tick`. For each running item whose `withdraw_deadline <= now`:
  `ledger.cancel(id, "withdrawal grace expired", states=("running",), drop_progress=True)`, then
  `_notify(id, "response", notice(skill, reason))`. `_stop_cancelled` then kills it.

**Commit 4: receiver**

`agent/receiver.py`, in `_decide_and_act`:

- **After `observe_issue`:**
  - `delegated = app_user_id and delegate_id == app_user_id`.
  - When delegated, run `clear_undelegated`.
  - When not delegated and the issue has an active delegation-authority item, run
    `request_status_check`.
- **`owns = is_delegation and delegated and (action == "created" or reroute)`**.
- **Replace `:287-302`**, for the case where `elsewhere` is in another session:
  - **`owns`:**
    - X unclaimed, or X a chat: `supersede`. Authority: `delegation`. Acknowledge S2 with the usual
      ACK (or NO_BOT_LABEL) plus the SUPERSEDED suffix. Then call `_close_moved(X)`, best effort.
    - X a claimed write item: `flag_withdrawal(X, "superseded", deadline)`, acknowledge DEFER_ACK, and
      raise `Deferred(X.id)`. When the payload already carries `"deferred": true`, acknowledge
      DEFER_STILL instead and return.
  - **Not `owns`, X a chat that is unclaimed:** `supersede` into this session.
    - Authority: `delegation` when X's authority is `delegation` and the card is delegated; otherwise
      `mention`.
    - Acknowledge with ACK chat, then `_close_moved(X)`.
  - **Otherwise:**
    - `push_inbox` as today, with `resume_waiting` = delegated-or-chat and X not flagged;
    - `record_forward(session_id, X)`;
    - the notice per R10 (§7.1).
- **Resume branch (`:324-329`):**
  - For a chat: `can_resume` is as before. When the chat's authority is `delegation` and the card is not
    delegated, set it to `mention` in the same `push_inbox` transaction (new keyword
    `demote_to_mention`).
  - For a write item that is not delegated: the reply is saved, and the text is RESUME_UNDELEGATED.
- **New item creation** passes `authority`:
  - a work decision: `delegation`;
  - a chat: `delegation` when `is_delegation` and delegated, else `mention`.
- **`_decide_and_act` retries** the routing and act step at most twice on `StaleRouting`, or on
  `push_inbox`'s "cannot steer a terminal work item" (J5).
- **`process_one`:**
  - starts with `_release_deferred()`: a `deferred` row whose item is no longer running, or whose
    `withdraw_deadline + 300` has passed, becomes `pending` with a fresh `ack_id`, and its payload gets
    `"deferred": true`;
  - on `Deferred`, stores status `deferred`, keeps the payload and sets `error` to the item id.
- **`_receive_stop` (`:179-180`)** cancels rows with status `pending` or `deferred`.
- **`_process_stop`** uses `ledger.stop_target(session_id)`, with these replies:
  - `own`: as today;
  - `forwarded` or `latest_delegation`: STOP_ELSEWHERE;
  - `moved`: STOP_MOVED;
  - `stopped`: STOP_ALREADY;
  - `none`: as today.
- **`_close_moved(item)`**: SUPERSEDED as a session response, or as an issue comment for a `local-` item.
  Errors are swallowed.

**Commit 5: worker CLI, Linear API, progress**

`agent/__main__.py`:
- **`fetch-issue`:** adds `"withdrawn": bool(item["withdraw_deadline"])`. When not delegated, it calls
  `ledger.request_status_check(issue_id)`.
- **`await-input`:** for a delegation-authority item, a fresh `api.issue_status`. It refuses when the card
  is not delegated or is closed, before anything reaches Linear. The ledger refuses a flagged item.
- **New `withdraw --item --token-file`:**
  1. `renew`.
  2. `api.issue_status`.
  3. `ledger.withdraw(delegated=…, closed=…)`.
  4. Post `notice(skill, reason)`, best effort: a session response, or an issue comment for `local-`.
- **`verify-publication`** (`:505-508`) **and `handoff-repository`** (`:370-371`): refuse a flagged item.
- **`request-repair` / `resume-work`** (`:446-449`): pass `delegate_id=current["delegate_id"]`.
- **`cancel`** (`:586-587`): `drop_progress=True`, then the OPERATOR notice, best effort.
- **parser:** `cmd("withdraw", "--item", token=True)`.

`agent/linear_api.py`:
- **`class LinearError(RuntimeError)`** with `kind` in `not_found`, `forbidden`, `ratelimited`, `auth` or
  `rejected`. The message stays "Linear GraphQL rejected the request".
- **`graphql`** (`:235-250`):
  - HTTP 400 whose body holds `RATELIMITED` → `ratelimited`;
  - GraphQL `errors[].extensions.code`: `RATELIMITED` → `ratelimited`, `FORBIDDEN` → `forbidden`,
    `AUTHENTICATION_ERROR` → `auth`;
  - a message matching "not found" → `not_found`;
  - anything else → `rejected`.
  - On success it sets `self.last_success_at = time.time()`.
  - The exact codes are U. LC7 records them, and an unrecognised code falls to `rejected`, which never
    cancels anything.
- **`issue_status`** (`:339-347`): the query adds `trashed`; `archived = archivedAt is not None or trashed is True`;
  a null issue raises `LinearError("not_found")`.

`agent/session_progress.py`:
- **`_content`** (`:19-43`):
  - queued, in order: retry delay (existing); predecessor cleanup pending; a status-check error; the
    issue marked undelegated; the existing text;
  - running and flagged: "工作正在保存进度并停止。"
- **New column `failures INTEGER NOT NULL DEFAULT 0`.** A failed send is next due after
  `min(interval, retry_seconds * 2**failures)` (`:100-103`). A success resets it.

**Commit 6: skills, doctor, docs**

- The skill and reference texts in §7.2 and §7.3.
- `agent/dispatch.py` AUTHORITY (`:58`) gains one sentence (§7.3). The approval reviewer reads only the
  dispatch.
- **`agent/doctor.py` `_snapshot` and `diagnose`**: new findings. Each is read only when its column
  exists.

  | Finding | Condition |
  |---|---|
  | `undelegated_work` | active delegation-authority items on an issue whose `undelegated_since` is more than 3 intervals old |
  | `withdrawal_overdue` | a running item whose `withdraw_deadline` passed more than 300 s ago |
  | `long_parked` | `awaiting_input` for more than 7 days |
  | `deferred_delegation` | a `webhook_events` row with status `deferred` older than 45 min |
  | `outside_prefix` | an active item whose identifier is outside `issue_prefix` |
  | `stored_undelegated` | the stored delegate is not `expected_app_user_id`, when the config pins one |

- The contract and design edits in §7.4.

### 5.2 Existing tests that change, and why

| Test | Change |
|---|---|
| `tests/test_lifecycle.py:90-94` | Still passes. Extend: a second preflight inside the interval makes no read (G3). |
| `tests/test_lifecycle.py:114-131` | Needs a confirming read, 60 s later: P3. |
| `tests/test_lifecycle.py:133-144` | Rewritten. The claimed feature is flagged, not cancelled; the parked fix and the queued delegation chat are cancelled; a mention chat is kept. The old test pins the bug. |
| `tests/test_lifecycle.py:163-168` | Two reads. |
| `tests/test_lifecycle.py:30-48` | Extend with the CLOSED notices (D10). |
| `tests/test_receiver.py:194-201` | Declined → superseded, and the new fix links its predecessor. |
| `tests/test_receiver.py:816-841` | The resume text becomes RESUME_UNDELEGATED. |
| `tests/test_receiver.py:964-976` | Forwarded-and-resumed → the waiting conversation is superseded into the delegation session. |
| `tests/test_receiver.py:978-989`, `:1071-1083` | The refusal becomes a supersede; the "reply re-routes" part moves to a deferred-then-still-running case. |
| `tests/test_receiver.py:1003-1011` | Reworked the same way. The D16 re-route itself is unchanged. |
| `tests/test_receiver.py:1022-1030` | Text. |
| `tests/test_resume_work.py:129-138` | The waiting mention chat is superseded into the answering session, not resumed in place. |
| `tests/test_linear_api.py:136-143` | The query holds `trashed`. |
| `tests/test_cli.py:797` | `fetch-issue` also prints `withdrawn`. |
| `tests/test_ledger.py` | Exact-dict comparisons of item views, if any, gain three keys [U until run]. |

### 5.3 Schema, migration and recovery

- **Additive columns only.**
  - `work_items.authority` is NULL on existing rows and reads as K2 says.
  - `session_progress.failures` is added by `SessionProgress.__init__` through `ALTER TABLE`, when missing.
- **Release steps:**
  1. Before deploying, run `doctor`. With `expected_app_user_id` pinned it lists `stored_undelegated` items.
  2. Decide on each one. After the deploy, a pre-existing write item on an undelegated card is cancelled
     at the first confirming read, and its session gets UNDELEGATED. Pre-existing chats are kept and
     listed.
- **Rollback:**
  - Older code ignores the new columns.
  - A `webhook_events` row in status `deferred` is never processed by older code. Its session re-routes
    on the person's next reply (D16), or the operator sets it to `pending` by hand.
  - A flagged worker keeps running under older code until `verify-publication` refuses it.
  - Checking out older code does not undo the columns.
- **Windows:** no new process code. Kills go through `launcher.stop` and `_stop_cancelled`, as for Stop
  and closure, but they now happen in more situations. Run the Windows suite, including
  `tests/test_windows_workers.py`, on the Windows runner. A killed `git` that leaves an `index.lock`
  (critique 5.1, [I]) shows as `cleanup_pending`, which the doctor already reports.

## 6. Test matrix

- Style: `LifecycleTests` reuses `SchedulerTests.setUp` (`tests/test_lifecycle.py:21-28`).
  `BotRoutingReceiverTests` helpers are in `tests/test_receiver.py:852-880`. `LedgerBase` and
  `SessionProgressTests` are as in the existing files.
- `L` = `tests/test_lifecycle.py`, `R` = `tests/test_receiver.py`, `S` = `tests/test_scheduler.py`,
  `G` = `tests/test_ledger.py`, `C` = `tests/test_cli.py`, `P` = `tests/test_session_progress.py`,
  `A` = `tests/test_linear_api.py`, `D` = `tests/test_doctor.py`.
- "Confirm" means: `refresh` with `delegate_id=None`, then `self.now += 60`, then `refresh` again.

### A. Delegation withdrawn or moved

| ID | Test | Setup | Expected |
|---|---|---|---|
| A1 | L `test_a1_waiting_delegation_chat_is_cancelled_after_a_confirmed_removal` | A delegation chat in awaiting_input. One refresh, then confirm. | After the first read: still waiting, `undelegated_since` set, no activity. After the second: cancelled; exactly one `response` UNDELEGATED_CHAT; no `session_progress` row. |
| A2a | L `test_a2_removal_that_leaves_the_session_open_posts_there_once` | A parked fix. Confirm, then a third refresh. | Cancelled; one UNDELEGATED in its session; the third read posts nothing. |
| A2b | R `test_a2_reply_in_the_open_session_after_cancel_starts_a_mention_chat` | A cancelled delegation item in session-1; the card is undelegated; a `prompted` reply. | A new chat in session-1 with `authority=mention`; the ACK chat. |
| A2c | R `test_a2_stop_in_the_open_session_after_cancel_says_it_already_stopped` | As A2b; Stop. | Reply STOP_ALREADY; `scheduler.stop` not called. |
| A2d | R `test_a2_reply_to_a_waiting_chat_during_the_window_resumes_it_as_mention` | A waiting delegation chat; the card is undelegated; a reply. | Queued; `authority=mention`; a later confirm leaves it alone. |
| A3 | L `test_a3_delegation_moved_to_another_app_is_withdrawal` | As A1, with `delegate_id=uuid4()`. | As A1. |
| A5 | L `test_a5_a_read_that_finds_the_delegation_clears_the_mark` | One undelegated read, then a delegated read, then an undelegated read 60 s later. | Nothing cancelled; `undelegated_since` equals the third read's start. |
| A6 | R `test_a6_redelegation_supersedes_without_any_status_read` | A waiting chat in session-0; a Bot/修改 delegation in session-1; the lifecycle never runs. | Chat cancelled; a fix queued in session-1 with the chat's messages; FIX_ACK plus the suffix; then SUPERSEDED in session-0. |
| A8 | L `test_a8_mention_chat_is_kept_when_the_delegation_goes` | A waiting mention chat; confirm. | Still waiting; no activity. |
| A9 | L `test_a9_running_chat_is_flagged_then_stopped_after_ten_minutes` | A claimed delegation chat; confirm; `now += 600`; `scheduler.tick()`. | Flagged at confirmation with a deadline 600 s ahead; cancelled at the tick; one UNDELEGATED_CHAT; the launcher stopped it. |
| A10a | C `test_a10_flagged_fix_refuses_publication_pause_resource_and_handoff` | A claimed fix; `flag_withdrawal`. | `verify-publication`, `await-input`, `await-resource` and `handoff-repository` each fail with "delegation withdrawn". |
| A10b | C `test_a10_withdraw_cancels_the_claim_and_posts_once` | A flagged fix, after a checkpoint. | `withdraw` → cancelled; the token is refused afterwards; one UNDELEGATED. |
| A10c | C `test_a10_withdraw_refuses_while_still_delegated_and_unflagged` | A claimed fix; the stub card is delegated. | Refused. |
| A11 | L `test_a11_item_created_after_the_read_started_is_not_cancelled` | Mark the issue at t0; patch `issue_status` to create a fix during the confirming read. | The new fix stays queued; the older chat is cancelled. |

### B. Session ended or idle

| ID | Test | Setup | Expected |
|---|---|---|---|
| B1 | R `test_b1_mention_reaches_a_fix_parked_in_an_unreachable_session_and_stop_follows_it` | A parked fix in session-1, card delegated; a mention in session-9; Stop in session-9. | The mention is forwarded and resumes the fix; `forwarded_item` is recorded; Stop stops the fix with STOP_ELSEWHERE in session-9. |

### C. Re-delegation, second sessions and duplicates

| ID | Test | Setup | Expected |
|---|---|---|---|
| C1 | R `test_c1_new_delegation_supersedes_a_waiting_item_in_one_transaction` | A waiting fix in session-0; a delegation in session-1. | Old item cancelled; new fix with `predecessor_id` set; inbox copied in order with authors; S2 acknowledged before S1's SUPERSEDED. |
| C2a | R `test_c2_new_delegation_defers_behind_a_claimed_fix_then_continues_it` | A claimed fix in session-0; a delegation in session-1; the old worker runs `withdraw`; `process_one`. | First: flagged `superseded`, DEFER_ACK, event `deferred`. After the withdrawal: a fix in session-1 linked to the old one; a fresh ack id. |
| C2b | R `test_c2_deferred_event_reports_once_if_the_old_worker_is_still_running` | As C2a; `now` past deadline + 300 s with the item still running. | DEFER_STILL; no item; event `done`. |
| C2c | R `test_c2_running_chat_is_superseded_at_once` | A claimed mention chat; a Bot/修改 delegation. | Chat cancelled (the claim token is refused); fix queued. |
| C3 | R `test_c3_delegation_outranks_a_waiting_mention_chat` | A waiting mention chat; a delegation. | As C1, with no predecessor (chat → fix). |
| C4 | R `test_c4_local_item_is_superseded_with_an_issue_comment` | A queued `local-` fix; a delegation. | One comment SUPERSEDED; new fix linked. |
| C5a | R `test_c5_mention_supersedes_a_waiting_chat_into_its_own_session` | A waiting delegation chat; the card delegated; a mention in session-9. | New chat in session-9 with `authority=delegation`; old one cancelled. |
| C5b | R `test_c5_mention_to_a_parked_undelegated_fix_says_it_will_not_continue` | A parked fix; the card undelegated; a mention. | Saved; FORWARD_PARKED_UNDELEGATED; still `awaiting_input`. |
| C6 | P `test_c6_queued_successor_says_it_waits_for_predecessor_cleanup` | A linked successor whose predecessor cleanup is not done. | The heartbeat body is the predecessor text. |
| C7 | G `test_c7_cancelled_successor_records_the_latest_delegation_session` | A cancelled fix in session-1; a newer delegation session-2; `retry`. | Successor in session-2. A `local-` predecessor keeps its session. |
| C9 | L `test_c9_duplicate_closure_posts_one_closing_response_per_active_item` | An active chat plus a blocked fix; `status_type=duplicate`. | Both cancelled; exactly one CLOSED_CHAT; nothing for the blocked item. |

### D. Stop

| ID | Test | Setup | Expected |
|---|---|---|---|
| D2a | R `test_d2_stop_in_the_latest_delegation_session_stops_work_elsewhere` | A deferred session-1; X claimed in session-0; Stop in session-1. | X stopped; the deferred event cancelled; STOP_ELSEWHERE. |
| D2b | R `test_d2_stop_in_a_superseded_session_points_to_the_new_one` | After C1, Stop in session-0. | STOP_MOVED; nothing stopped. |

### E. Issue closed, removed or out of reach

| ID | Test | Setup | Expected |
|---|---|---|---|
| E1 | L `test_e1_closure_notices_only_active_items` | Extends `:30`. | Active items get one CLOSED or CLOSED_CHAT each. |
| E3a | A `test_e3_trashed_issue_reads_as_archived` | A status response with `trashed: true`. | `archived` is True. |
| E3b | L `test_e3_not_found_three_times_over_fifteen_minutes_cancels_silently` | `issue_status` raises `LinearError("not_found")`; `api.last_success_at` advances. | Cancelled after the third failure, once ≥ 900 s have passed; no activity. |
| E5 | L `test_e5_forbidden_or_rate_limited_never_counts_as_unreachable` | Kinds `forbidden`, `ratelimited` and `auth`. | Nothing cancelled; backoff as today. |
| E6 | A `test_e6_ratelimited_http_400_is_classified_transient` | An HTTP 400 whose body holds `RATELIMITED`. | `LinearError.kind == "ratelimited"`. |
| E4 | D `test_e4_item_outside_issue_prefix_is_a_finding` | A stored identifier outside the prefix. | `outside_prefix`. |

### F. Item states when the delegation goes

| ID | Test | Setup | Expected |
|---|---|---|---|
| F1 | L `test_f1_undelegated_queued_fix_is_read_once_per_interval_then_cancelled` | A queued fix; 30 scheduler ticks over 59 s. | One status read; the confirming poll at +60 s cancels it. |
| F2 | L `test_f2_queued_delegation_chat_is_not_launched_while_undelegated` | A queued delegation chat; preflight. | False; a mention chat on the same card is admitted. |
| F3 | L `test_f3_retry_delayed_item_is_cancelled` | `retry_not_before` in the future. | Cancelled. |
| F4 | L `test_f4_launched_unclaimed_item_is_cancelled_and_its_worker_stopped` | `set_worker`, no claim. | Cancelled; the launcher stopped it; a later claim fails. |
| F5/F6 | L `test_f5_f6_parked_for_a_question_or_a_wait_is_cancelled_and_the_label_stays` | Park with `question` and with `waiting`. | Cancelled; `needs_more_info` never undone. |
| F7 | L `test_f7_queued_reservation_is_cancelled` | `await_resource`. | Reservation `cancelled`. |
| F8 | L `test_f8_granted_slot_is_released_through_settlement` | Reservation acquired; item resumed to queued. | Item cancelled; reservation `cancel_requested`; listed by `reservations_to_settle`. |
| F9 | `tests/test_slots.py` `test_f9_withdrawal_mid_switch_returns_the_slot` | Cancel inside `switch`, via R1. | `resume` refuses; the slot is given back. |
| F10 | L `test_f10_pending_handoff_is_cancelled_and_its_retiring_worker_stopped` | Handoff pending. | `next_root_repo` None; the launcher stopped it. |
| F11 | C `test_f11_handoff_refuses_on_undelegated_card` | Existing refusal. | Unchanged (regression). |
| F13 | L `test_f13_blocked_item_is_untouched_by_undelegation` | Blocked fix; confirm. | Still blocked. |
| F14 | G `test_f14_flagged_blocked_finish_is_recorded_cancelled_and_continued` | Flagged fix finishes `blocked`; then a delegation. | Cancelled; the new fix links it. |

### G. Per skill

| ID | Test | Setup | Expected |
|---|---|---|---|
| G1-G5 | L `test_g_rule_follows_authority_not_initial_root` | Feature (fixture), fix, fgui (a fixture manifest), delegation chat, mention chat, operator chat; all queued; confirm. | The first four are cancelled; the two chats with other authority are kept. |

### H. Lost deliveries and downtime

| ID | Test | Setup | Expected |
|---|---|---|---|
| H1 | L `test_h1_poll_alone_confirms_without_any_webhook` | No request; two ticks 60 s apart. | Cancelled. |
| H4 | D `test_h4_long_parked_item_is_a_finding` | `awaiting_input`, `updated_at` 8 days ago. | `long_parked`. |

### I. Operator actions

| ID | Test | Setup | Expected |
|---|---|---|---|
| I1 | L `test_i1_enqueued_write_item_is_withdrawn_by_comment_and_operator_chat_kept` | A `local-` fix and a `local-` chat (authority from `enqueue`). | Fix cancelled with one comment; chat kept. |
| I2 | C `test_i2_operator_cancel_posts_one_note_and_drops_pending_progress` | A pending `session_progress` content. | One OPERATOR response; the row is gone. |
| I3 | G `test_i3_retry_of_cancelled_item_uses_latest_delegation_session` | As C7, through `retry`. | As C7. |

### J. Worker-side and races

| ID | Test | Setup | Expected |
|---|---|---|---|
| J1 | C `test_j1_await_input_refuses_on_an_undelegated_card_for_delegation_authority` | Delegation chat; the stub card is undelegated. | Refused; no label added and no activity posted. A mention chat is allowed. |
| J2/J3 | `tests/test_skills.py` `test_j2_j3_skill_texts_withdraw_instead_of_pausing` | Read SKILL.md. | fix: names `withdraw` and not "revoked delegation" under await-input. chat: holds the undelegated rule. |
| J4 | `tests/test_repair_work.py` `test_j4_request_repair_uses_the_fresh_delegate` | Stored delegate APP; the fresh stub read says None. | Refused. |
| J5 | R `test_j5_forward_that_meets_a_terminal_item_reroutes_to_a_new_chat` | Patch `push_inbox` to cancel X first. | A new chat in the mention session; the event is `done`. |
| J6 | L `test_j6_unknown_identity_refuses_delegation_launches` | `app_user_id=None`. | Preflight False for a fix; True for a mention chat. |

### K. Critique additions

| ID | Test | Setup | Expected |
|---|---|---|---|
| K2 | G `test_k2_migration_reads_null_authority_by_skill` | Old-schema rows. | Write → `delegation`, chat → `mention`. |
| K3 | = F14 | | |
| K5 | G `test_k5_new_chat_sees_the_cancelled_chats_messages_in_history` | Cancel a chat; create a new chat. | `conversation_history` holds the messages. |
| K7 | = E5/E6 | | |
| K8 | L `test_k8_flap_shorter_than_an_interval_cancels_nothing` | Undelegated at t, delegated at t+30, undelegated at t+61. | Nothing cancelled. |
| K9 | = A11 | | |

### Concurrency (critique 5.2)

| ID | Test | Setup | Expected |
|---|---|---|---|
| X1 | R `test_supersede_race_with_request_repair_reroutes_once` | `request_repair` commits between routing and supersede. | Exactly one active item; no `uncertain`. |
| X2 | G `test_await_input_after_cancel_is_refused` | Cancel, then `await_input` with the old token. | LedgerError. |
| X3 | P `test_cancel_with_notice_leaves_no_second_response` | A pending heartbeat content; `stop(notice)`. | The next ticks send nothing. |
| X4 | P `test_failing_sends_back_off_to_the_interval` | `create_activity` raises. | Due after 60, 120, 240, 480, then 600 s. |

## 7. Operating contract and skill text

### 7.1 Notice texts (`agent/withdrawal.py`)

`{bot}` is the configured app name.

| Name | Text |
|---|---|
| UNDELEGATED (write) | 这张卡已不再委派给 {bot}，这项工作已取消。已推送的分支和草稿 PR 都保留，接手的人可以在上面继续；重新委派给 {bot} 时会从已有进度接着做。 (unchanged) |
| UNDELEGATED_CHAT | 这张卡已不再委派给 {bot}，这段对话已结束。需要继续时，重新委派给 {bot}，或在评论里 @{bot}。 |
| SUPERSEDED | 这张卡有了新的委派会话，这里的工作已转到那里继续。 |
| SUPERSEDE_SUFFIX | 此前在另一个会话中的工作已转到这里继续。 |
| DEFER_ACK | {bot} 已收到委派。这张卡上一次的 {skill} 工作正在保存进度并停止（最长约 {minutes} 分钟），停止后会在这里接着处理。 |
| DEFER_STILL | 上一次的工作还没有停止。稍后在这里回复任意内容即可开始。 |
| CLOSED (write) | 这张卡已关闭或归档，{bot} 已停止这里的工作。已推送的分支和草稿 PR 都保留。 |
| CLOSED_CHAT | 这张卡已关闭或归档，这段对话已结束。 |
| OPERATOR (write) | 维护者已停止这项工作。已推送的分支和草稿 PR 都保留。 |
| OPERATOR_CHAT | 维护者已结束这段对话。 |
| RESUME_UNDELEGATED | 已保存回复；这张卡已不再委派给 {bot}，这项工作即将停止。重新委派给 {bot} 会从已有进度接着做。 |
| FORWARD_PARKED_UNDELEGATED | 已保存你的消息；这张卡已不再委派给 {bot}，{skill} 工作不会继续。重新委派后会读取这条消息。 |
| FORWARD_WITHDRAWING | 已保存你的消息；这张卡上的工作正在停止。 |
| STOP_ELSEWHERE | 已停止 {identifier} 上在另一个会话中进行的工作，worker 已终止，占用的资源在静默检查后释放。 |
| STOP_MOVED | 这里的工作已转到新的委派会话；要停止，请在那个会话里按 Stop。 |
| STOP_ALREADY | 这里的工作已经停止。 |
| Heartbeat: predecessor | 工作已排队，正在等待上一次工作的清理完成。 |
| Heartbeat: status error | 工作已保留；暂时读不到这张卡的状态，读取恢复后继续。 |
| Heartbeat: undelegated | 这张卡已不再委派，这项工作即将停止。 |
| Heartbeat: withdrawing | 工作正在保存进度并停止。 |

### 7.2 Skills

**`skills/fix/SKILL.md:268-269`.** Replace the sentence "if the issue was archived, closed or re-delegated
away, stop publication and finish blocked" with:

> If `fetch-issue` prints `delegated: false` or `withdrawn: true`, or any command refuses with
> `delegation withdrawn`, publish nothing and ask nothing. Save a checkpoint whose `plan.prs` records
> every branch and PR you pushed, then run `withdraw` and exit. `withdraw` ends the job as cancelled, so
> a later delegation continues from your plan. If `withdraw` refuses because the card is delegated
> again, continue.

**`skills/fix/SKILL.md:326-327`.** Remove "revoked delegation" from the `await-input` list, and add:

> revoked delegation is never a question; follow the withdrawal rule above.

**`skills/chat/SKILL.md`, after step 3** (`:34`):

> If `fetch-issue` prints `delegated: false` or `withdrawn: true` and `issue-context.coordination.authority`
> is `delegation`, do not ask a question: `await-input` will refuse. Answer what you can in one response,
> say the card is no longer delegated to `bot_name` and how to delegate it again, and finish delivered.
> With `mention` or `operator` authority, delegation does not matter to you.

Keep `:77-78` as it is.

**`references/worker-cli.md:20-22`.** Add `withdrawn` to the `fetch-issue` output. Add a row for
`withdraw --item ITEM_ID --token-file FILE`: "ends your claim as cancelled after the delegation was removed
or superseded; save a checkpoint first; exit after it succeeds."

### 7.3 Dispatch

Append to AUTHORITY (`agent/dispatch.py:58`):

> If fetch-issue reports delegated false or withdrawn true, or a ledger command refuses with 'delegation
> withdrawn', publish nothing and ask nothing: save a checkpoint, run the ledger CLI's withdraw command
> and exit.

### 7.4 Operating contract and design spec

**`docs/operating-contract.md`:**

- **`:132`**, replacing the row:

  > | Remove FarmBot's delegation, delegate the card to another app, or dismiss FarmBot's session | two status reads at least `reconcile_seconds` apart (the first is usually within a second of the change) confirm it; the issue's work that the delegation authorised then stops: a queued or waiting job is cancelled with one response in its session (an issue comment for an operator-enqueued job), and a running worker is told to save its progress and stop, and is stopped after twice its renew interval. Branches, draft PRs and recovery refs stay, and delegating the card again continues from them. Conversations started by an @mention or by an operator are not affected. Nothing is cancelled for work created after the read began, or when the delegation comes back before the second read |

- **`:135`**, replacing the row:

  > | Delegate an issue that already has FarmBot work in another session | the new delegation takes the card over: waiting or queued work and any conversation move to the new session (the old job is cancelled and continued by a linked job with its messages; the old session gets one note); a running write worker is told to save and stop, and the new session starts once it has |

- **`:136`**, replacing the row:

  > | @FarmBot on an issue that already has FarmBot work in another session | a waiting conversation moves to your thread and answers there; otherwise your text is forwarded to that work, and the notice says whether it was resumed, will be read at the next checkpoint, or will not continue because the card is no longer delegated |

- **`:134`**, adding:

  > a Stop in a session that forwarded a message to the work, or in the card's latest delegation session, stops the card's work wherever it runs

- **`:125` and `:129`:** unchanged, except that a reply to a parked delegation conversation after the
  delegation was removed resumes it as a conversation that needs no delegation.
- **`:131`:** adding "and posts one closing response in the session of each job it cancels while
  active".
- **New row:**

  > | Remove a Bot label from a delegated card | nothing; only removing the delegation or Stop stops work |

- **`:392`, "CLI `cancel`":** adding "and posts one note in the job's session".
- **`:409-413`:** replace the delegation sentences with the rule above. Add: "a launch preflight on an
  issue marked undelegated or failing waits for the issue's next due read, so a held-back job reads
  Linear at most once per interval". Replace "Polling makes no other Linear write…" with "Polling writes
  only the cancellation and closing responses above".
- **`:709-711`:** state machine:

  > queued, awaiting_input or awaiting_resource → cancelled when the delegation that authorised the job is withdrawn, it is superseded by a newer delegation session, or the issue is unreachable; running → cancelled by the worker's `withdraw`, or by the controller when the withdrawal grace ends.

- **A new "Withdrawn work" section:** authority (P1), P3, P4, P5, the doctor findings, and R8's thresholds.
  It also states that FarmBot reads no session archive state, and why.
- **Schema notes:** §5.3.

**`docs/superpowers/specs/2026-09-24-feature-workers-design.md`:**
- **§9.8 (`:961-968`):** "saves a checkpoint and finishes blocked" becomes "saves a checkpoint and runs
  `withdraw`", and the lifecycle rule follows P2.
- **`:160-161`:** keep. The team rule becomes true for fix too.

## 8. Live checks on the test instance

### 8.1 Ground rules

- Use a throwaway 【测试】 card in the farm team, delegated only to TestBot.
- Record the UTC time of each action.
- The controller log has no timestamps: note its line count before each action, and read the new lines
  after it.
- A GraphQL read is run by the operator with TestBot's own token. The base query is in
  `linear-behaviour.md`.
- Comments, labels and branches created here are real.

### 8.2 Part 1: Linear facts, before merging

These need no new code, and run in this order (critique 5.3).

| # | A person does in Linear | Record | Settles |
|---|---|---|---|
| LC-U4 | On a card TestBot tracks, choose "No agent", then delegate to TestBot again. | Accepted `Issue update` lines in the log after each step; the issue's `updatedAt` before and after, from a read. | Latency only, now (§3). |
| LC-U6 | After LC-U4, open the card's threads. | A new thread each time? The same session id? A `created` log line each time? | P4's trigger, K1. |
| LC-1 | Run the base query on the first thread of LC-U4. | `archivedAt`, `dismissedAt`, `status`; whether `agentSession(id)` still returns it. | The follow-up session read (B1). |
| LC-10 | The operator posts one `thought` into the archived session with TestBot's token. | Success, or the full error. | P7's best effort. |
| LC-8 | Open every menu of a fresh TestBot thread; try each action (Stop, dismiss, archive, delete or resolve the thread) on a fresh session. | The delegate afterwards and the lifecycle fields. | Whether B1 exists. |
| LC-5 | On a delegated card, @TestBot in a new comment, then choose "No agent". | Is the mention thread archived? | A2's frequency. |
| LC-6 | With a session open, clear the delegate through the API. | The session's fields; the webhooks. | A2's frequency. |
| LC-7 | Move a throwaway card to Canceled, archive it, then delete it. Run the status query with `trashed` after each step. | The query is accepted; the result or error body for the deleted card. | E3, the `LinearError` codes. |
| LC-12 | Change the assignee, then set "No assignee". | The delegate afterwards. | A5. |

### 8.3 Part 2: acceptance of the new build

The branch must be deployed on the test instance, which needs the operator's go-ahead. After each
check, `doctor` must show no finding the check did not expect.

| # | A person does | FarmBot must show |
|---|---|---|
| AC-1 (A1) | Delegate an unlabelled card with a vague request; wait for the question; choose "No agent"; wait 3 min. | Doctor: no active item. The old thread shows UNDELEGATED_CHAT if Linear accepted it. |
| AC-2 (C1) | Repeat AC-1's first two steps; choose "No agent", add Bot/修改 and delegate again within 30 s. | The new thread: FIX_ACK plus SUPERSEDE_SUFFIX within 10 s. The fix starts. Its first worker reads the chat's messages (issue-context). No "已有进行中的工作" anywhere. |
| AC-3 (A2) | Make the session stay open: the LC-5 or LC-6 path, whichever left it open. Then reply in the old thread, then press Stop there. | UNDELEGATED_CHAT in that thread within 2 min. The reply gets a fresh chat answer saying how to delegate again. Stop answers STOP_ALREADY. |
| AC-4 (F5) | Delegate a Bot/修改 card and get the fix to ask a question; choose "No agent". | UNDELEGATED within 2 min. The item is cancelled. Cleanup done. |
| AC-5 (C3) | @TestBot a question on a card, then delegate the card with Bot/修改 while the chat is waiting. | The chat is cancelled; the fix starts in the delegation thread with the question's text; the mention thread gets SUPERSEDED. |
| AC-6 (D2) | With a fix waiting in the delegation thread, @TestBot an answer in a new comment (forwarded), then press Stop in that comment's thread. | STOP_ELSEWHERE there; the fix is cancelled. |
| AC-7 (E1) | With a chat waiting, move the card to Canceled. | CLOSED_CHAT in its thread within a minute. |
| AC-8 (I2) | The operator runs `cancel` on a waiting chat. | OPERATOR_CHAT in its thread. |
| AC-9 (A10) | Optional, and only if the operator accepts a real branch: while a fix runs, choose "No agent". | Within 20 min: UNDELEGATED; doctor shows no running item; `withdraw` or the grace expiry is in the audit. Delegating again starts a fix whose issue-context `recovery` holds the old plan. |

## 9. Open questions for the operator

1. **Grace for a claimed worker.** Twice its renew interval (fix 20 min, chat 10 min) before the
   controller stops it. Or should FarmBot stop it at once, accepting that a PR created after its last
   checkpoint is later reported as foreign (critique 2.4)?
2. **Confirmation delay.** Waiting work is cancelled only after a second read, one `reconcile_seconds`
   (60 s) later. A re-delegation is never delayed by this. Is 1-2 minutes acceptable?
3. **New Linear writes.** This change adds one closing response when a card is closed (D10), and one note
   when the operator cancels (D9). Both are visible to people. Keep them?
4. **Where an answer lands.** An @mention that answers a waiting conversation moves it to the mention's
   thread, instead of resuming it in its old thread. Accept this change in behaviour?
5. **Conversations already stranded at deploy time.** They are kept and listed by the doctor for manual
   cancelling, not cancelled automatically. Accept?

## Appendix A. Critique findings

| Finding | Resolution |
|---|---|
| 1.1 U4 decides correctness | **Accepted.** P3 fences by read start and uses each read's own result; J4 uses the fresh read. U4 is still the first live check. |
| 1.2 A4 overstated | **Accepted.** Rated M; no special immediate kill for another agent. |
| 1.3 G3 is H because of the rate limit | **Accepted.** Preflight throttle, `RATELIMITED` treated as transient, limit verified. |
| 1.4 Contract list incomplete | **Accepted.** §7.4 covers `:125`, `:129`, `:131`, `:132`, `:134`, `:135`, `:136`, `:392`, `:409-413`, `:709-711`. |
| 1.5 Operator authority | **Accepted.** Set by `enqueue`. |
| 1.6 C8 is not a withdrawal; no launch `delegated` field | **Accepted.** `fetch-issue` stays the source; `authority` is stable, so it goes in issue-context. |
| 2.1 Unstable authority | **Accepted.** Stored at creation. The one change (demotion on a person's reply while undelegated) is an explicit event, not re-derivation. |
| 2.2 Debounce | **Accepted, for every skill.** Chat too, for one rule; P4 frees the card at once on re-delegation anyway. |
| 2.3 R8 mass cancel | **Accepted.** Per-issue `not_found` or `trashed` only, other calls must succeed, claimed items are flagged. |
| 2.4 D1 loses evidence | **Accepted.** Flag, grace, worker `withdraw`, then stop. |
| 2.5 Finish blocked breaks the lineage | **Accepted.** `withdraw` cancels, and a flagged blocked finish is recorded as cancelled. |
| 2.6 Definition of "ended"; D5 | **Accepted in principle.** Session reads are deferred (§3); the rule is written down for the follow-up. B1 keeps the item. |
| 2.7 One transaction | **Accepted.** `Ledger.supersede`, re-route on `StaleRouting`. |
| 2.8 Adopt is hazardous | **Accepted.** Supersede only; inbox copied. |
| 2.9 No kills before the ack | **Accepted.** The receiver writes the ledger only, acknowledges S2, then notes S1. |
| 2.10 CLI must not run R1 | **Accepted.** It only requests a status check. |
| 2.11 Double responses, endless retries, missing close | **Accepted.** `drop_progress`, backoff, SUPERSEDED to S1. |
| 2.12 Forwarding into a chat strands | **Accepted for waiting chats.** For running chats, steering is kept: killing one discards its work, and the risk (B1 during a run) may not exist. |
| 3.1 Session id reuse | **Out of this change.** Live check LC-U6; fallback in K1. |
| 3.2 Deploy sweep | **Accepted.** K2 and §5.3. |
| 3.3 Blocked, then re-delegated | **Accepted** via 2.5. Blocked for other reasons: unchanged. |
| 3.4 Label removal | **Accepted.** Contract row. |
| 3.5 Conversation lost | **Rejected.** `conversation_history` already includes every item's messages and pending question (`agent/ledger.py:2131-2143`); test K5. |
| 3.6 Local chat blocks | **Accepted.** Supersede. |
| 3.7 Rate-limited or unauthenticated app | **Accepted.** E5/E6/K7. |
| 4 Unverified Linear behaviour | **Accepted.** No DT2 in this change; LC10 best effort; DT3 not needed, because P4 triggers on the event itself and P3 handles races; DT4 not subscribed. |
| 5.1 Windows | **Accepted.** §5.3. |
| 5.2 Tests | **Accepted.** X1-X4, K2, F14. |
| 5.3 Order of live checks | **Accepted.** §8.2. |

## Appendix B. Out of scope, with reasons

| Item | Reason |
|---|---|
| Reading session state (DT2) | Nothing in this design needs it. It waits for LC-8 and LC-1. |
| Inbox-notification webhooks (DT4) | A Linear config change for each app. A hint at most, since reads already detect removal. |
| H2 (lost `created`) | Delivery reliability, not withdrawal; a follow-up. |
| H3 (`uncertain` `created`) | Delivery reliability, not withdrawal; a follow-up. |
| H7 (retry timestamps) | Delivery reliability, not withdrawal; a follow-up. |
| E4 detection | Moving to another team needs a team read in the status query. The doctor finding covers visibility. |
| G19 polling of blocked-only issues | L, pre-existing. |
| D7 automatic expiry of long pauses | The doctor's `long_parked` first. |

## Appendix C. Changes during implementation

Where the code departs from the text above, and why. The operating contract states the result.

| Where | Change | Why |
|---|---|---|
| §5.1 commit 1, `create_work_item` | A write item accepts only `delegation` authority. | P1 gives every write item that authority; nothing else may create one. |
| §5.1 commit 2, `retry` and a continuation a conversation requests | They clear a withdrawal flag on the item they queue again. | `queue()` never launches flagged work, so the job would never run. |
| §5.1 commit 2, `_cancelled_successor` | Only a write job's successor moves to the latest delegation session with `delegation` authority. A conversation's successor keeps its session and its authority. | Giving every successor delegation authority would turn a mention's conversation into the delegation's. |
| §5.1 commit 2, `withdraw` | Without a flag or closure, a read that finds the card not delegated withdraws only delegation-authority work. A mention or operator conversation is refused and continues. | P2, A8 and G2. The dispatch text of §7.3 reaches every worker, so the ledger is the fence. |
| §5.1 commit 2, `withdraw` | When the caller's read finds the card delegated and open, `withdraw` clears the issue's `undelegated_since` and its `undelegated` flags, commits, and refuses, so the worker continues. A `superseded` or `unreachable` flag still ends the work. | P3: any read that finds the delegation clears the mark and those flags. Otherwise a flap longer than one interval, or an API re-delegation (U6), would end work that nothing restarts. |
| §5.1 commit 2, `stop_target` | `latest_delegation` returns only delegation-authority work. | A Stop in an old delegation session must not reach a conversation that a mention or the operator started later. A2 expects STOP_ALREADY there. |
| §5.1 commit 3, `LinearError` | The class, with its `kind`, is defined in `agent/linear_api.py` with commit 3; commit 5 makes `graphql` and `issue_status` raise it. | The lifecycle's not-found rule (R8) reads the kind before the client classifies any error. Until commit 5 no read is `not_found`, so nothing is withdrawn as unreachable. |
| §5.1 commit 4, `push_inbox` | A push to an item that has ended raises `StaleRouting`, a `LedgerError` with the same message, and the receiver routes again on that class. | The receiver's re-route (J5) then matches an error class, not message text. Callers that catch `LedgerError` are unchanged. |
| §5.1 commit 4, `_release_deferred` | A deferred event is released when the old item no longer runs, when its deadline is 300 s past, or when it runs with no withdrawal flag at all. | A running item without a flag has no deadline to wait for; the event then answers DEFER_STILL, and a later reply re-routes it (D16). |
| §5.1 commit 4, forwarding notice | FORWARD_PARKED_UNDELEGATED answers a message forwarded to any delegation-authority work while the card is not delegated here, not only to parked work, unless the work was resumed or is already withdrawing. | Such work is withdrawn at the next confirming read whatever its state, so "will not continue" is the honest notice (R10). |
| §5.1 commit 5, `SessionProgress.tick`, done with commit 2's review | No correction is sent once the item's progress row is gone. | A notice's `drop_progress` can commit while a heartbeat send is in flight, and the correction would be a second response (P7). |
| §5.3, rollback | Under older code, a worker flagged `undelegated` is refused at `verify-publication` while the card stays undelegated. A worker flagged `superseded` may publish, because the card is still delegated. | Older `verify-publication` reads the delegate, not the flag. |
