# Silent delegation: design for FarmBot's fix

**Status: accepted, 2026-09-30; implemented on its branch (the five commits of §5), not yet merged or deployed;
the live checks of §9 have not run.** Current behaviour is in
[`docs/operating-contract.md`](../../operating-contract.md), whose Triggers table and "Withdrawn work" section
state it. The operator accepted the six decisions of §10 as recommended. Code comments cite this document as the
"silent-delegation design", with the IDs it defines. Appendix E records where the implementation departs from the
text below.

Design of 2026-09-30, for FarmBot main 7fd47a7. It merges two proposals, "own session" and "prevent and tell"; a
third, "in place", was not delivered, and its idea is taken from the first proposal's fallback. Appendix A records
what was rejected.

It extends the withdrawn-work design (`docs/superpowers/specs/2026-09-29-withdrawn-work-design.md`, "WW"
below) and keeps its principles P1-P8, claim fencing and the rule of one active item per issue.

IDs: P9-P12 are the new principles of §2; A1-A12 the paths of Part A (§4.1); S1-S23 the scenarios of §4.2,
each tested under the IDs of §8; U1-U9 the unknown Linear behaviours of §3.7; LC and AC the live checks of §9.

Every `file:line` is relative to the repository root at 7fd47a7 and was re-read for this design. Tags:
**[V]** verified in code, **[L]** measured live on the test instance on 2026-09-30, **[E]** Linear's public
documentation or schema, **[I]** inference, **[U]** unknown, with its fallback in §3.7.

The text names no card, person, host or ID, so it can go into the public repository as it is.

## 1. Problem

### 1.1 Live evidence [L]

Measured on the test instance (main 7fd47a7). Times are UTC.

1. **A delegation made in Linear's interface opens a new agent session, and sends `created`, only when no
   session of this app on the card waits for an answer** (its last activity is an elicitation). Otherwise
   Linear sends only Issue `update` webhooks: FarmBot starts nothing and the person sees nothing.
   - Blocked, three times: at 03:03 a delegation's conversation waited in its own thread; at 03:47 and 03:51
     an @mention's conversation waited in its thread.
   - Not blocked: at 04:17 a delegation opened a session while two threads were open whose last activity was
     a response. One of them had asked a question and was then closed by a response, so **a response after
     a question unblocks later delegations**.
   - Whether a thread whose last activity is a thought blocks is unknown (U2).
2. "No agent" asks whether to archive the agent's thread; archiving is optional. Archiving a thread from its
   menu also removes the delegation.
3. A delegate change sends an Issue `update`, usually within 1 s, once 59 s late.
4. `created` normally arrives within about 2 s, once 27 s late. A lost delivery was retried after 66 s with a
   fresh `webhookTimestamp` and accepted. Linear documents retries after 1 minute, 1 hour and 6 hours [E].
5. A response posted into an archived session is accepted. Linear hides Stop once a session is complete.
6. **FarmBot itself left a thread waiting.** A Stop pressed in a mention thread cancelled a fix that waited
   in its delegation thread. The reply went to the Stop's thread only, and the fix's thread kept its question
   as the last activity.
7. A delegation through the API opens no session at all.

### 1.2 Why FarmBot does nothing today [V]

- Work starts only from an AgentSessionEvent `created` (`agent/receiver.py:234-432`).
- The Issue webhook branch reads only `data.id` and asks for a status read of a tracked issue
  (`agent/receiver.py:117-131`).
- A status read that finds the card delegated only clears the undelegated mark (`agent/lifecycle.py:53-54`).
  `Ledger._clear_undelegated` (`agent/ledger.py:782-785`) returns the withdrawal flags it cleared, not the
  mark, so none of its three callers (`agent/lifecycle.py:54`, `agent/receiver.py:244-245`,
  `agent/ledger.py:1853-1856`) can see "marked undelegated, now delegated".
- The ledger records no session state. FarmBot reads none from Linear: its only session query is
  `session_has_artificial_root` (`agent/linear_api.py:334-351`).
- A Stop that reaches work in another session answers only in the Stop's thread
  (`agent/receiver.py:218-226`). `scheduler.stop` is called without `states`, so it cannot post a notice
  (`agent/scheduler.py:289-290`).

### 1.3 Which threads wait

| Kind | Cause | Seen |
|---|---|---|
| Waiting by design | A mention's or operator's conversation is kept when the delegation goes (`agent/lifecycle.py:86-89`); the delegation comes back before the confirming read (`agent/lifecycle.py:53-54`) | 03:03, 03:47, 03:51 |
| Left behind | Stop from another thread; a resume from another thread; a closing notice Linear refused; a question posted after the job was cancelled; a worker's own `activity --type elicitation`; a note skipped because the new session's acknowledgement failed | fact 6 |

Part A (§4.1) removes the second kind where it starts. Part B (§3, §4.2) handles a delegation that meets
the first kind, or any thread Part A missed.

### 1.4 Goal

- FarmBot never leaves its own thread waiting when the work there has ended or moved.
- A delegation that Linear opened no session for gets a true reaction within about 95 seconds, and starts
  the work the labels name wherever FarmBot already holds a delegation thread for the card.
- Every scenario has a decided behaviour and a unittest.
- Nothing depends on a Linear behaviour nobody has verified. Each unknown has a fallback that posts nothing
  false and cancels nothing (§3.7).
- One pull request. FarmBot's OAuth scopes do not change: a scope change revokes every token of the app [E].

Not in this change: FarmBot opening an agent session itself (decision D4, Appendix C).

## 2. Principles

P1-P8 are unchanged except P7, which P10 amends.

### P9. A thread FarmBot's work leaves gets a last word

When work ends, or is stopped or resumed from another thread, its own thread gets one activity that says so:
a `response` or `error` when the work ended, a `thought` when it goes on. A worker asks a question only
through `await-input`, which parks the job.

### P10. A closing activity is owed (amends P7)

A `response` or `error` that closes a thread and that Linear refuses is retried at most five more times,
about 31 minutes in all. It is dropped as soon as the job's state changes or newer work starts in that
thread. After the last try `doctor` lists it. P7's "one notice per cancellation" stays.

### P11. A delegation is heard once per episode

- **Episode.** A read that finds the card delegated to this app, and that started after a recorded
  undelegated mark, clears the mark and starts an episode. The read's start is the episode's `since`.
- **Heard.** The episode is heard when a delegation session of this app on the card was recorded at or
  after the mark, or a delegation `created` for the card waits behind another session's worker.
- **Settled.** An episode still unheard 90 seconds after `since` is settled once, by P12.
- **Fences.** A read that finds the card undelegated ends a waiting episode only if it started after
  `since`. A waiting episode is never restarted by a later read. This is P3's fence in both directions.

### P12. An unheard delegation is settled where the card's work is

FarmBot settles it on a fresh read of the card, which must find the card open and delegated to this app.
It takes the first of these that applies.

| Outcome | When | What happens |
|---|---|---|
| **Kept** | The card's active job has `delegation` authority and is the kind of work the labels route to | Nothing moves. Its thread gets one line: the question again if it waits, else that the work goes on. |
| **In place** | Otherwise, the active job is unclaimed or a conversation, existed before the episode, and lives in a delegation thread that Linear says is not archived | P4 with the new session equal to that thread: the job is superseded, in one transaction, by the job the labels route to, in the same thread, with its messages. |
| **Told** | Otherwise | Each of FarmBot's open threads on the card gets one note. A waiting thread gets a response that ends the wait and keeps its work answerable. A thread whose work runs gets a thought. A thread with no work that still shows open is closed. |
| **Unseen** | No open thread is found | Nothing is posted. `doctor` lists it. |

FarmBot opens no session and changes no delegate, label, status or assignee (P6). A thread whose state
FarmBot cannot read is never closed and never taken in place (P8).

### How these fit P1-P8

- **P1.** A job created in place has `delegation` authority: it is created in a recorded delegation session
  while a fresh read finds the card delegated here. No mention thread ever receives write work
  (`agent/receiver.py:390-391` is unchanged).
- **P3.** Detection is fenced by read start both ways (P11). In place supersedes only a job created before
  `since`, so nothing newer than the read is cancelled.
- **P4.** In place is P4's first branch (`agent/receiver.py:322-333`) with the same session. A claimed write
  job is never taken in place: it is told. A `created` that arrives later is P4 as today (WW H6).
- **P5.** After in place the new job is the thread's own, so a Stop there is `own`.
- **P7.** Amended by P10.
- **P8.** A failed read of the card postpones the settle. A failed or unknown session read means no in
  place and no closing of that thread.
- **Claim fencing and one active item.** Every item write goes through `Ledger.supersede`
  (`agent/ledger.py:1749-1787`), which rechecks the old job's state and now the episode in its transaction.

## 3. Detection and timing

### 3.1 The signal

`Ledger._clear_undelegated(issue_id, observed_at=None)` reads `undelegated_since` before clearing it. When a
mark was cleared and `observed_at > mark`, it opens an episode (§6.1) unless one is already waiting. Its
return value does not change. The three callers pass the start of their own read:

| Caller | Change |
|---|---|
| Lifecycle, also for launch preflights (`agent/lifecycle.py:53-54`) | `clear_undelegated(issue_id, observed_at=started)` |
| Receiver's fresh read, DT1 (`agent/receiver.py:237-245`) | `fetched_at = self.clock()` before `fetch_issue`; `clear_undelegated(issue["id"], observed_at=fetched_at)` |
| Worker `withdraw` (`agent/__main__.py:643-654`, `agent/ledger.py:1853-1856`) | the CLI takes `read_at = ledger.clock()` before `issue_status` and passes it through `Ledger.withdraw(..., read_at=)` |

`mark_undelegated(issue_id, observed_at)` (`agent/ledger.py:761-774`) also sets a waiting episode with
`since < observed_at` to `dropped`.

Which cards this covers [V]:
- A card with unfinished work is read every `reconcile_seconds`, so it is marked while undelegated. This
  includes a card whose only work is a mention's waiting conversation (checked on a temporary ledger).
- An idle card is read when its Issue webhook arrives (`agent/ledger.py:742-747`). The mark then stays
  until a read finds the delegation.

### 3.2 Heard

An episode is heard when either holds:
- `sessions` has a row with `issue_id=?`, `delegation=1`, `session_id NOT LIKE 'local-%'` and
  `created_at >= mark` (`ensure_session` runs before routing, `agent/receiver.py:259-260`, so an event that
  later failed or was deferred counts);
- `webhook_events` has a `created` row for the card that is `deferred`, or that a Stop `cancelled` at or
  after the mark (`json_extract(payload,'$.issue_id')`; both keep their payload).

### 3.3 Timing

| Quantity | Value | Basis |
|---|---|---|
| `since` | start of the read that cleared the mark: about 1 s after the delegation; at most one poll interval later when the Issue webhook is lost on a card with work | facts 3, `reconcile_seconds` |
| `created` latency | about 2 s; 27 s once; a retry after 66 s; then 1 h and 6 h | fact 4, [E] |
| **Grace** | **90 s after `since`** (`SILENT_GRACE_SECONDS`) | `since` is never earlier than the delegation, so a `created` retried 66 s after it is processed before the grace ends |
| The person sees a reaction | about 91-95 s after delegating; up to about 150 s when the Issue webhook was lost | grace, one receiver pass, one card read, at most 10 session reads |
| Look again after the work changed under a settle | 5 s | |
| Retry after a failed read | 15, 30, 60, 120, 240, then 300 s | nothing is posted meanwhile |
| The "kept" line | at most once per job per 30 minutes | an automation that flaps the delegate |
| Owed closing activity | retried after 1, 2, 4, 8 and 16 minutes, then `doctor` | P10 |

### 3.4 Where settling runs

In the receiver's loop. `Receiver.process_one` (`agent/receiver.py:484-513`) handles a Stop, then releases
deferred events, then one pending event; when no event is pending it settles one due episode.

- It is the thread that processes `created`, so a settle never interleaves with P4.
- It makes its own full read of the card, which has the labels that routing needs. The lifecycle's status
  read has none.
- `due_issue`, `finish_status_check` and the lifecycle loop do not change. The lifecycle only records the
  transition.

### 3.5 The settle

```python
def _settle_delegation(self):
    episode = self.ledger.due_episode(self.clock())           # oldest waiting row with due_at <= now
    if episode is None:
        return False
    try:
        outcome = self._settle(episode)
    except StaleRouting:                                       # the work or the episode changed under it
        self.ledger.retry_episode(episode["issue_id"], episode["since"], self.clock() + RESETTLE_SECONDS)
        outcome = "changed"
    except Exception as exc:                                   # a failed read; nothing was posted
        self.ledger.retry_episode(episode["issue_id"], episode["since"], <backoff>, error=type(exc).__name__)
        outcome = "retry"
    print(json.dumps({"event": "delegation_episode", "issue_id": episode["issue_id"], "outcome": outcome}), flush=True)
    return True
```

`_settle(episode)`:

1. **Read the card.** `fetched_at = clock()`, `fetch_issue`, `observe_issue`. This is a DT1 read.
   - Not delegated to this app, or closed or archived: the episode is `dropped`. A not-delegated read also
     asks for a status read when delegation work is active, as `agent/receiver.py:246-249` does.
   - Delegated: `clear_undelegated(issue_id, observed_at=fetched_at)`.
2. **Heard** (§3.2): the episode is `heard`. Nothing is posted.
3. **Route.** `active = active_item_for_issue(issue_id)`.
   `decision = route(action="created", is_delegation=True, text="", labels=..., label_groups=...,
   active_state=None, terminal_exists=False, available_skills=self.skills)`.
   `skill = decision.skill if decision.kind == "work" else "chat"`.
4. **Kept.** `active["authority"] == "delegation" and active["skill"] == skill`:
   `finish_episode(issue_id, since, "served", session_id)`. When it returned True, the thread is not
   `local-` and no such line was posted for this job in the last 30 minutes, post best effort an
   `elicitation` REDELEGATED_WAITING with the pending question (`awaiting_input`), else a `thought`
   REDELEGATED_RUNNING, and record audit kind `redelegated`.
5. **In place.** All of:
   - `active["state"] in UNCLAIMED or active["skill"] == "chat"` (`agent/receiver.py:41`);
   - `active["created_at"] < episode["since"]`;
   - the job's session is not `local-` and has `delegation=1`;
   - `session_state` (§3.6) answered for that session, and it is not archived.

   Then `Ledger.supersede(active["id"], ACTIVE_STATES if chat else UNCLAIMED, session_id=<same session>,
   skill=skill, authority="delegation", target=None if skill in ("chat", "feature") else session["target"],
   reason=..., episode=since)`. The supersede marks the episode `in_place` in its own transaction and raises
   `StaleRouting` when the job or the episode changed. Then post best effort a `thought`: IN_PLACE_NOTE, a
   newline, and `self._opening(decision, "")` (`agent/receiver.py:434-443`). The session's stored target is
   used as a conversation's first repair uses it (`agent/ledger.py:2040`); nothing is pinned anew.
6. **Told or unseen.** Build the targets (table below), then
   `finish_episode(issue_id, since, "told" if targets else "unseen")`. When it returned True, post each
   note. A refused `response` is owed (P10). Record audit kind `redelegated` on the active job.

Targets, over the active job's session and the card's 10 newest sessions. A `local-` session is never a
target: it is no Linear thread.

| Thread | Linear's state (§3.6) | Note |
|---|---|---|
| Holds the active job, `awaiting_input` | not archived and not `complete`/`error`, or unread | `response` SILENT_WAITING (a write job) or SILENT_WAITING_CHAT. The job stays parked. |
| Holds the active job, `awaiting_input` | `complete`, `error` or archived | none: it does not block, or nobody sees it |
| Holds the active job in `queued`, `running` or `awaiting_resource` | not archived, or unread | `thought` SILENT_BUSY |
| No active job; its `forwarded_item` is the active job, or an event of it is pending or deferred | any | none: a Stop there must keep reaching the work (P5) |
| No active job | read, not archived, any status but `complete` and `error` | `response` SILENT_ENDED |
| No active job | `complete`, `error`, archived, or unread | none |

### 3.6 Reading FarmBot's own session state

New `LinearAPI.session_state(session_id, issue_id, app_user_id)`:

```graphql
query FarmBotSessionState($id: String!) { agentSession(id: $id) { status archivedAt issue { id } appUser { id } } }
```

- It returns `{"status": <string as sent>, "archived": <bool>}`. It raises for another issue or app, as
  `session_has_artificial_root` does.
- Any status value is accepted. Linear added `stopping` on 2026-09-24 [E]. Only `complete` and `error` mean
  closed.
- It is called only while settling an unheard episode, for FarmBot's own recorded sessions on that card, at
  most 10 per episode. The first failure ends the reads of that pass; the threads not read count as unread.
- `agentSession(id)` is a public query that FarmBot already uses [V, E].

### 3.7 Unknown Linear behaviours and their fallbacks

| # | Unknown | What the design does whichever way it is |
|---|---|---|
| U1 | Whether Linear delivers a blocked delegation later, once the waiting thread has a newer activity | A `created` that arrives takes the card over under P4; one active item always. After a note this is the ideal outcome. After in place the work moves once to the new session. If Linear delivered it only when the thread completes, the delegation's work would start again after the in-place job finished; LC-1 rules this in or out before the production deploy, and the remedy is to tell instead of taking in place (one branch of §3.5). |
| U2 | Whether a thread whose last activity is a thought blocks a delegation | Detection does not depend on the cause. A thread with running work gets SILENT_BUSY. A thread with no work that reads `active` is closed by SILENT_ENDED. Live forwarding threads are left alone; if LC-2 shows they block, the follow-up of Appendix C closes them when their work ends. |
| U3 | Whether `agentSession(id)` returns `status` and `archivedAt`, also for archived sessions | A failed read: no in place, no closing of threads without work; the active job's own thread is still told from the ledger's knowledge. |
| U4 | Whether a response always turns a waiting thread complete | Seen once live (fact 1). If it did not, the next delegation is an episode again and ends as `unseen` in `doctor`. |
| U5 | How late a `created` can be | 90 s covers the measured 66 s retry. A later one is P4. |
| U6 | Whether every delegate change sends an Issue webhook | A card with unfinished work is polled. An idle card whose webhook is lost is not read, as today. |
| U7 | Whether re-posting an activity id is idempotent | Each retry of an owed activity uses a fresh id. A timeout that hid a success can give a duplicate closing response, which is harmless. |
| U8 | Whether one app's waiting thread blocks another app's delegation | Each instance reads and writes only its own recorded sessions. |
| U9 | `agentSessionCreateOnIssue`: whether it works with the current scopes, while a thread waits, and whether it sends `created` | Not used in this change (D4). |

### 3.8 What detection does not cover

- A card FarmBot never observed: its Issue webhooks are ignored, as today.
- A removal and re-delegation that no read saw as undelegated (faster than one read, or an idle card whose
  Issue webhook was lost): there is no mark, so no episode. The card is simply still delegated.

## 4. Behaviour per scenario

### 4.1 Part A: FarmBot never leaves its own thread waiting

"Own thread" is the job's `work_items.session_id`. Text names refer to §7.

| # | Path | Today [V] | New |
|---|---|---|---|
| A1 | Stop reaches work whose thread is not the Stop's: a thread that forwarded to it, the card's latest delegation thread, or a conversation's thread after a handover | The reply goes to the Stop's thread only (`agent/receiver.py:218-226`) | The stopped job's own thread also gets one `response`, STOPPED_ELSEWHERE or STOPPED_ELSEWHERE_CHAT, and its pending heartbeat is dropped in the cancelling transaction. The Stop's thread gets STOP_ELSEWHERE, which no longer claims a worker was terminated. When the work ended between the lookup and the cancel, the reply is STOP_ALREADY. |
| A2 | A message from another thread resumes a parked job (`agent/receiver.py:370-388`) | A thought in the forwarding thread only; the job's thread keeps its question | After that acknowledgement, a `thought` RESUMED_ELSEWHERE in the job's own thread, best effort. |
| A3 | A reply in a conversation's thread resumes the job it handed over to, in another thread (`agent/ledger.py:863-869`, `agent/receiver.py:414-418`) | A thought in the conversation's thread only | The same RESUMED_ELSEWHERE in the job's thread. |
| A4 | A closing activity that Linear refuses (`agent/scheduler.py:249-250`, `agent/receiver.py:226-228`, `459-460`, `502-505`, `agent/__main__.py:223-226`, `288-289`, `518-519`) | Swallowed | Owed (P10): recorded in `session_closures` and retried by the progress loop. `local-` jobs are not owed. |
| A5 | SUPERSEDED or MOVED_THREAD skipped because the new session's acknowledgement raised (`agent/receiver.py:331-332`, `366-368`) | The old thread is never told | The acknowledgement runs in `try`, the note in `finally`. |
| A6 | `await-input` posted its question, then the ledger refused the park because a cancel landed between (`agent/__main__.py:483-484`) | The question stays last | When the job is now `cancelled`, `failed`, `delivered` or `blocked`: a `response` QUESTION_WITHDRAWN or QUESTION_WITHDRAWN_CHAT in the same thread, owed on failure; then the error as before. No correction for a job that is queued or claimed again. |
| A7 | A heartbeat's question in flight crosses a closing notice (`agent/session_progress.py:138-147`) | The correction is suppressed, so the question is last | When the row is gone right after an `elicitation` was sent, the job's terminal text (「工作已停止。」) is posted once, owed on failure. |
| A8 | A worker posts `activity --type elicitation` without parking (`agent/__main__.py:458-464`); `skills/fix/SKILL.md` forbids it at `:110-112` and allows it at `:377` | Allowed | The CLI refuses it: "ask with await-input: it posts the question and parks the job". The skill line is corrected. |
| A9 | The receiver's `elicit` branch (`agent/receiver.py:419-420`): `route` never returns it | Dead code that would post a question with no work | Removed, with the `elicitation` case of `acknowledge` (`agent/receiver.py:315-316`). |
| A10 | A mention's or operator's conversation kept on undelegation; the delegation back before the confirming read | Waits by design | Unchanged. Part B settles the delegation that meets it (S1, S2, S4). |
| A11 | An unreachable issue (`agent/lifecycle.py:91`) | Silent by design | Unchanged. |
| A12 | Ends with no activity, leaving a thread active: a slot-pool failure of a waiting job, exhausted publication retries, a forwarding thread whose target ended | Nothing | Unchanged here. They matter to delegations only if U2 says an active thread blocks, and Part B then closes such a thread when a delegation meets it. Appendix C lists the follow-up. |

Paths that already end with a response or error in the job's own thread are unchanged, except that the
activity is now owed when refused: Stop in the own thread, withdrawal by a confirming read, the worker's
`withdraw`, grace expiry, closure, operator `cancel`, launch and worker failures, and `finish`.

### 4.2 Part B: a delegation Linear opened no session for

T is the thread of the card's active job.

| # | Scenario | Linear sends | FarmBot does | The person sees |
|---|---|---|---|---|
| S1 | "No agent" without archiving while a delegation's conversation waits in T; Bot/修改 added; delegated again within 60 s (03:03) | Issue updates | The removal read marks. The delegated read opens an episode. 90 s later: fetch, not heard, the labels route to `fix`, the conversation is not that, T is a delegation thread and not archived: **in place**. The conversation is superseded by a fix in T with its messages. | About 91-95 s after delegating, a thought in T: IN_PLACE_NOTE and the fix acknowledgement. The fix starts. |
| S1' | The same, 60 s or more after the removal | `created` | The confirming read already cancelled the conversation with UNDELEGATED_CHAT, so T is complete and Linear opens a session. Heard. | As today. |
| S2 | A card is delegated while an @mention's conversation waits in its mention thread M (03:47, 03:51) | Issue updates | The card was polled and marked. Episode. 90 s later the conversation is a mention's and M is no delegation thread: **told**. M gets `response` SILENT_WAITING_CHAT; the conversation stays `awaiting_input`. | The note in M. A reply there continues the conversation. "No agent" and delegating again opens a session, whose `created` takes the conversation over (WW C3). |
| S3 | A card is delegated while an @mention's conversation runs | `created`, or Issue updates only (U2) | `created`: P4 supersedes the conversation at once; heard. Silent: M gets `thought` SILENT_BUSY; nothing is cancelled. | A new session as today, or the note in M. |
| S4 | A delegation's fix waits in T; "No agent" without archiving; delegated again within 60 s; labels unchanged | Issue updates | **Kept.** T gets one `elicitation` REDELEGATED_WAITING with the fix's question. | The question again in T. An answer resumes the fix. |
| S5 | The same while the fix runs or is queued | Issue updates, or `created` (U2) | Silent: **kept**; T gets one `thought` REDELEGATED_RUNNING. `created`: P4 as today (WW C2). | One line in T, or the new session. |
| S6 | A delegation job in its delegation thread T while the labels now route to other work | Issue updates | Unclaimed job or a conversation: **in place**. A claimed write job: **told**, T gets SILENT_BUSY. | The takeover in T, or the note. |
| S7 | A thread waits in Linear but FarmBot has no work there: a thread from before this change, or one whose owed activity was given up | Issue updates | **Told.** The thread reads open and gets `response` SILENT_ENDED. If it cannot be read: **unseen**. | The note; "No agent" and delegating again then opens a session. Otherwise the operator sees the `doctor` finding. |
| S8 | `created` arrives after the settle (a retry an hour later, or U1) | `created` | P4. After a note: the parked job is superseded into the new session, and the old thread gets SUPERSEDED after the note. After in place: an unclaimed job is superseded into the new session; a claimed write job is flagged and the event deferred (WW C2). | The new session with the usual acknowledgement. |
| S9 | `created` arrives on time | `created` | The read that clears the mark opens an episode; the session row makes it heard. Nothing is posted. | As today. |
| S10 | A delegation through the API | Issue update | A tracked, marked card: an episode, settled as S1-S7 by what is on the card. With no open thread: **unseen**, nothing starts. An untracked card: ignored, as today. | A reaction only where FarmBot has a thread. |
| S11 | The delegation is removed during the grace | Issue update | A read that started after `since` drops the episode. If no read ran, the settle's own read drops it. Nothing is posted. P3 runs as today. | Nothing new. |
| S12 | The controller restarts during the grace | Linear retries | The episode is durable and `due_at` absolute: the first pass after the restart settles it. A `created` retried meanwhile makes it heard. | As S1-S10. |
| S13 | Two quick re-delegations | Issue updates, perhaps one `created` | Each undelegated read drops the waiting episode; each delegated read after a mark opens one. One settle, against the last mark. | At most one reaction per re-delegation. |
| S14 | The card is closed during the grace | Issue update | Closure cancels and posts its notices as today. The settle's read finds the card closed: dropped. | The closing responses. |
| S15 | An automation flaps the delegate (WW K8) | Issue updates | Ending undelegated: dropped. Ending delegated: **kept** for work of the same kind, one line at most every 30 minutes. | At most one line. |
| S16 | A reply arrives in T during the grace | `prompted` | The reply resumes or steers the job as today. The settle then decides on what is true: kept, in place for a conversation, or a note. | The reply's acknowledgement, then the settle's line. |
| S17 | The active job was created after `since`, for example by an @mention made after the delegation | | It is never superseded in place. Unless it is kept, its thread is told. | The note. |
| S18 | The card's work is an operator's `local-` job | Issue updates | A `local-` session is no Linear thread: no in place and no note there. Kept when it is the same kind of delegation work; else other open threads are told, or **unseen**. | Nothing, or a note in another thread. |
| S19 | T is archived while its job waits and the card is delegated: a `created` was lost twice | `created` an hour later | Work of the same kind is kept (its line lands in the archived thread). Otherwise nothing is taken in place into a thread nobody sees and T gets no note: **unseen**. The `created` then runs P4. | The new session, late. |
| S20 | The card read fails at the settle | | Nothing is posted. The settle is retried with backoff; `doctor` lists an episode more than 10 minutes overdue. | Nothing until the read works. |
| S21 | Stop in T after in place | `prompted` stop | `own`: the new job is T's. | The Stop reply. |
| S22 | The person follows a note: "No agent", then delegates | `created` | The noted thread is complete, so Linear opens a session. P4 takes over a parked job. If 60 s or more passed, P3 had cancelled the delegation's work first, and the new session's job of the same write skill continues it as its predecessor. | The new session. |
| S23 | Replay of fact 6: a fix waits in T, a mention in M forwards an answer, Stop in M | `prompted` stop | A2: T gets RESUMED_ELSEWHERE. A1: the fix is cancelled; M gets STOP_ELSEWHERE and T gets STOPPED_ELSEWHERE. | Both threads are complete; a later delegation opens a session at once. |

## 5. Code changes (one PR, five commits)

Each commit passes the offline suite. `agent/service.py` does not change: the receiver loop already calls
`process_one` (`:263-264`) and the progress loop already ticks `SessionProgress` (`:285-287`).

### Commit 1: close the threads a Stop, a resume or a failed acknowledgement leaves (A1-A3, A5, A9)

- **`agent/withdrawal.py`**: the Part A texts of §7. `NOTICE_REASONS` (`:23`) gains `stopped_elsewhere`;
  `_NOTICES` (`:24-25`) maps it to `(STOPPED_ELSEWHERE, STOPPED_ELSEWHERE_CHAT)`. STOP_ELSEWHERE (`:38`) is
  corrected. No `withdraw_reason` is ever `stopped_elsewhere`, so `_expire_withdrawals`
  (`agent/scheduler.py:549`) and `post_closing_notice` (`agent/__main__.py:213`) are unaffected.
- **`agent/scheduler.py:325-326`**: `text = notice(cancelled) if callable(notice) else notice`; `_notify` is
  called only when `text`. Today a function that returns None posts a response with no body (checked).
- **`agent/receiver.py:207-232`, `_process_stop`**:

  ```python
  STOPPABLE = (*ACTIVE_STATES, "blocked")      # what today's unconditional cancel ends
  item, where = self.ledger.stop_target(row["session_id"])
  if item is not None:
      here = row["session_id"]
      cancelled = self.scheduler.stop(
          item["id"], "Linear stop", states=STOPPABLE,
          notice=lambda job: None if job["session_id"] == here
          else withdrawal_notice(job["skill"], "stopped_elsewhere", self.bot_name))   # withdrawal.notice
      if cancelled is None:
          body = STOP_ALREADY                  # it ended between the lookup and the cancel
      elif cancelled["session_id"] == here:
          body = f"已停止 {item['identifier']} 上的工作，worker 已终止，占用的资源在静默检查后释放。"
      else:
          body = STOP_ELSEWHERE.format(identifier=item["identifier"])
  ```

  The cancelled job decides, not the job that was looked up: the cancel follows a conversation's handover
  (`agent/ledger.py:1697-1707`). `drop_progress` (`agent/scheduler.py:298`) is now true for every Stop, so
  the Stop reply is the thread's last word. With `states`, a Stop whose job already ended kills nothing
  (`agent/scheduler.py:307-308`); `_stop_cancelled` (`agent/scheduler.py:522-537`) and the reaper own those
  processes.
- **`agent/receiver.py:370-388`**: when the notice is RESUMED, after `acknowledge` call
  `self._note_resumed(delivered["item_id"], session_id)`. It posts `thought` RESUMED_ELSEWHERE to that
  job's session when it differs from `session_id` and is not `local-`; errors are swallowed.
- **`agent/receiver.py:409-418`**: keep `push_inbox`'s result; when `active["state"] == "awaiting_input"`
  and the result's state is `queued`, call `_note_resumed` the same way.
- **`agent/receiver.py:331-332` and `:366-368`**: `try: acknowledge(...)` `finally: self._close_moved(...)`.
- **`agent/receiver.py:419-420`, `:315-316`**: removed.

### Commit 2: owe a closing activity Linear refused (A4, A6-A8, P10)

- **`agent/ledger.py`**: table `session_closures` (§6.1) and
  - `owe_closure(session_id, *, issue_id=None, item_id=None, kind, body)`: nothing for a `local-` session;
    `kind` is `response` or `error`; the row replaces an older one of that session and records the job's
    current state;
  - `due_closure(now)`: the oldest row with `due_at <= now` and no `given_up_at`;
  - `closure_superseded(row)`: the job's state differs from the recorded one, or a job was created in that
    session after the row;
  - `closure_result(session_id, *, sent, error=None)`: delete when sent; else count the failure and, with
    the new count `n`, set `due_at = now + 60 * 2 ** (n - 1)`, or `given_up_at` once `n >= 6`. A row starts
    at `n = 1`, due 60 s after the first refusal.
- **`agent/scheduler.py:231-250`, `_notify`**: returns whether Linear took it. On failure, for kind
  `response` or `error`, it owes the activity on `control_ledger_factory()` when there is one, because it
  runs on the lifecycle's and the receiver's threads too.
- **`agent/receiver.py`**: `_close_moved` (`:450-460`), the Stop reply (`:226-228`; the stop row still
  becomes `uncertain`) and the event error reply (`:502-505`) owe on failure.
- **`agent/__main__.py`**:
  - `post_closing_notice` (`:209-226`) and `complete_session` (`:281-289`) take the ledger and owe on
    failure; callers at `:655`, `:670`, `:679`.
  - The request-repair acknowledgement (`:510-519`) is owed when it is a `response`.
  - `activity` (`:458-464`) refuses `--type elicitation` before anything reaches Linear (A8).
  - `await-input` (`:483-484`): A6.
- **`agent/session_progress.py`**: `tick` (`:112`) first handles one due closure: delete it when
  `closure_superseded`, else post it with a fresh activity id and record the result. A7 at `:144-146`.
- **`skills/fix/SKILL.md:376-377`**: "use `activity --type thought` for progress; a question always goes
  through `await-input`, which posts it and parks the job".
- **`agent/doctor.py`** (`:104-124`, `:345-393`): finding `unclosed_session`, read only when the table
  exists.

### Commit 3: record when a read finds a delegation back (P11)

- **`agent/ledger.py`**: table `delegation_episodes` (§6.1) and
  - `_clear_undelegated(issue_id, observed_at=None)` and `clear_undelegated(issue_id, observed_at=None)`
    (`:776-785`), as §3.1;
  - `mark_undelegated` (`:761-774`) drops a waiting episode older than the read;
  - `withdraw(..., read_at=None)` (`:1831-1863`) passes it on at `:1855`;
  - `episode(issue_id)`, `due_episode(now)`, `delegation_heard(issue_id, mark)`,
    `finish_episode(issue_id, since, state, session_id=None)` and
    `retry_episode(issue_id, since, due_at, error=None)`. The last two change only a row that is `waiting`
    with that `since`, and return whether they did.
- **`agent/withdrawal.py`**: `SILENT_GRACE_SECONDS = 90`.
- **`agent/lifecycle.py:54`**, **`agent/receiver.py:237-245`**, **`agent/__main__.py:643-654`**: as §3.1.

### Commit 4: settle a delegation Linear opened no session for (P12)

- **`agent/linear_api.py`**: `session_state` beside `session_has_artificial_root` (`:334-351`), as §3.6.
  `SCOPES` (`:16`) is unchanged. `StubLinear` (`agent/config.py:171`) gains `session_state`, which answers
  `session-state.json` of the stub directory or, without one, a complete, unarchived session.
- **`agent/ledger.py`**:
  - `supersede(..., episode=None)` (`:1749-1787`): with `episode`, the issue's episode must be `waiting`
    with that `since`, and becomes `in_place` with the session, in the same transaction; otherwise
    `StaleRouting`;
  - `sessions_for_issue(issue_id, limit)`: `session_id`, `delegation`, `created_at` and `forwarded_item` of
    the sessions that are not `local-`, newest first;
  - `noted_since(item_id, kind, since)`: whether the job has an audit row of that kind at or after `since`.
- **`agent/receiver.py`**:
  - `process_one` (`:488-491`): where it returned False for no pending event, it now returns
    `self._settle_delegation()`, called after the lock is released: the settle makes network calls, and
    `receive` needs the lock;
  - `_settle_delegation`, `_settle`, `_thread_state` (memoised per pass, None after the first failure) and
    `_tell`, as §3.5;
  - `SESSION_READS = 10`, `RESETTLE_SECONDS = 5`; `RENOTE_SECONDS = 1800` in `agent/withdrawal.py`.
- **`agent/withdrawal.py`**: the Part B texts of §7.
- **`agent/doctor.py`**: finding `silent_delegation`.

### Commit 5: documents

- **`docs/operating-contract.md`**
  - After `:136`, a new Triggers row:

    > | Delegate an issue when Linear opens no session for it (one of FarmBot's threads on the card still waits for an answer, or the delegation came through Linear's API) | about 90 s after a status read first finds the card delegated again with no delegation session, FarmBot settles it without opening a session or changing the card. Work of the kind the labels name that the delegation already has is kept, and its thread is told: a waiting job asks its question again. Other waiting or queued work, or a conversation, that lives in a delegation thread is taken over in that same thread, as a new delegation takes a card over. Otherwise each open FarmBot thread on the card gets one note: a response that ends a waiting thread, keeps its work answerable and says to choose No agent and delegate again; a thought where work runs. With no open thread nothing is posted and `doctor` lists `silent_delegation`. A delegation session that arrives later takes the card over as usual |

  - `:132`, append: "When the delegation comes back before the second read and Linear opens no session,
    see *Delegate an issue when Linear opens no session for it*."
  - `:135` (Stop), append: "When the stopped work lives in another thread, that thread also gets one
    closing response."
  - `:407-421`: replace "Polling writes only the cancellation and closing responses above" with "Polling
    writes only the cancellation and closing responses above; the receiver settles a delegation that
    opened no session (Triggers)".
  - `:838-842` (Stop): add the note in the stopped work's own thread.
  - `:850-855`, the Sessions paragraph:

    > **Sessions.** Linear opens a session for a delegation only while none of FarmBot's threads on the card waits for an answer (observed 2026-09-30), and never for a delegation through its API; it then sends only Issue updates. So FarmBot does not leave a thread waiting when the work there ends or moves: every cancel, Stop (also one pressed in another thread), takeover, move and failure ends with a response or error in the work's own thread; a job resumed from another thread gets a note in its own; a question posted after its job was cancelled is withdrawn; and a worker asks only through `await-input`. A read that finds the card delegated again after one found it not delegated starts an episode; if no delegation session is recorded within 90 s, the receiver settles it (Triggers). FarmBot reads agent-session state only then, and only for its own recorded sessions on that card, at most ten. Removing the delegation, which archiving a session in Linear's interface also does, and dismissing a session both remove the delegate, which the status reads see. If Linear lets a session be archived while the card stays delegated, its work is kept: a mention reaches it, a Stop in the mention's thread stops it, removing the delegation withdraws it, and `doctor` lists it after 7 days of waiting.

  - `:857-862`, Notices: replace "It is posted best effort and never retried" with "It is posted at once; a
    response or error Linear refuses is retried up to five more times, the last about 31 minutes later,
    unless the job's state changed or newer work started in that thread meanwhile, and `doctor` lists one
    that never went through".
  - Doctor table (`:864` on): rows for `silent_delegation` and `unclosed_session`.
  - Schema notes (`:423` on): the two tables of §6.1, and §6.3.
  - `:396-399` (CLI `cancel`): its note is owed when refused.
- **`docs/superpowers/specs/2026-09-29-withdrawn-work-design.md`**
  - The U6 row (`:215`):

    > | U6: whether a UI re-delegation always opens a new session | Settled 2026-09-30: not while one of FarmBot's threads on the card waits for an answer, and never through the API. P3's confirming read then finds the card delegated, clears the mark and cancels nothing; the silent-delegation design records that transition and settles it (P11, P12). |

  - A6 (`:242`) and C8 (`:268`): add "when Linear opens a session; see U6".
  - Appendix C: one row, "P7: a closing activity Linear refuses is retried a bounded number of times
    (silent-delegation design P10)".
- **`docs/superpowers/specs/2026-09-30-silent-delegation-design.md`**: this document.
- **`references/worker-cli.md`**: `activity` takes `thought`, `action`, `response` or `error`; a question
  goes through `await-input`.

## 6. Schema, migration and rollback

### 6.1 Schema

Both tables go into the ledger's schema script (`agent/ledger.py:413-585`), so every ledger connection
creates them.

```sql
CREATE TABLE IF NOT EXISTS delegation_episodes (
    issue_id TEXT PRIMARY KEY REFERENCES issues(id),
    since REAL NOT NULL,        -- start of the read that found the card delegated again
    mark REAL NOT NULL,         -- the undelegated mark that read cleared
    due_at REAL NOT NULL,       -- when the receiver settles it
    state TEXT NOT NULL CHECK(state IN ('waiting','heard','served','in_place','told','unseen','dropped')),
    session_id TEXT,            -- the thread that kept or took the delegation
    settled_at REAL,
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS session_closures (
    session_id TEXT PRIMARY KEY,
    issue_id TEXT, item_id TEXT, item_state TEXT,
    kind TEXT NOT NULL CHECK(kind IN ('response','error')),
    body TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 1,
    due_at REAL NOT NULL, created_at REAL NOT NULL,
    last_error TEXT, given_up_at REAL
);
```

One episode row per issue: a new episode replaces a row that is not `waiting`.

### 6.2 Migration and release

- Additive only, no backfill. Existing rows are untouched.
- A mark that older code already cleared leaves no episode, so the deploy itself settles nothing.
- Threads left waiting before the deploy are unknown to the ledger. Part B finds one through the session
  read when a delegation meets it (S7).
- Release steps: run `doctor` before and after the deploy; after it, `silent_delegation` and
  `unclosed_session` must be absent or explained.

### 6.3 Rollback

- Older code ignores both tables. A waiting episode is never settled and an owed activity never retried,
  which is today's behaviour.
- A job created in place is an ordinary job in its session.
- Under older code a Stop from another thread posts no note in the work's thread again.
- Checking out older code does not remove the tables.

### 6.4 Windows

No process code changes. A Linear Stop now passes `states`, so a job that ended between the lookup and the
cancel is not signalled by `stop`; the reaper and `_stop_cancelled` handle its processes. Run the Windows
suite, including `tests/test_windows_workers.py`, in CI.

## 7. Texts (`agent/withdrawal.py`)

`{bot}` is the instance's configured app name. A conversation's text never mentions branches or PRs.

| Name | Type | Text |
|---|---|---|
| STOPPED_ELSEWHERE | response | 这项工作已在另一个讨论串中按 Stop 停止。已推送的分支和草稿 PR 都保留。 |
| STOPPED_ELSEWHERE_CHAT | response | 这段对话已在另一个讨论串中按 Stop 结束。 |
| STOP_ELSEWHERE (corrected) | response | 已停止 {identifier} 上在另一个会话中的工作，占用的资源在静默检查后释放。 |
| RESUMED_ELSEWHERE | thought | 已在另一个讨论串收到回复，这里的工作已恢复，会先读取那条回复。 |
| QUESTION_WITHDRAWN | response | 上面的问题不用再回答：这项工作已经停止。 |
| QUESTION_WITHDRAWN_CHAT | response | 上面的问题不用再回答：这段对话已经结束。 |
| IN_PLACE_NOTE | first line of a thought | 这张卡已重新委派给 {bot}。Linear 没有为这次委派另开会话，{bot} 在这个会话里接着处理。 |
| REDELEGATED_WAITING | elicitation | 这张卡已重新委派给 {bot}。这里的工作还在等你的回答：\n{question} |
| REDELEGATED_RUNNING | thought | 这张卡已重新委派给 {bot}，这里的工作正在进行，会继续。 |
| SILENT_WAITING | response | 这张卡已委派给 {bot}，但 Linear 没有为这次委派打开会话（{bot} 的讨论串还在等回复时会这样）。这里的等待先结束，这项工作仍然保留：在这里回复可以继续；要按卡片现在的标签重新开始，请把代理改为「No agent」，再委派给 {bot}，会从已有进度接着做。 |
| SILENT_WAITING_CHAT | response | 这张卡已委派给 {bot}，但 Linear 没有为这次委派打开会话（{bot} 的讨论串还在等回复时会这样）。这里的等待先结束，对话仍然保留：在这里回复可以继续；要让 {bot} 按卡片的标签开始处理，请把代理改为「No agent」，再委派给 {bot}，这段对话会转到新的会话里。 |
| SILENT_BUSY | thought | 这张卡已委派给 {bot}，但 Linear 没有为这次委派打开会话。这里的工作会继续；要按卡片现在的标签重新开始，请等这里结束或按 Stop 之后，把代理改为「No agent」，再委派给 {bot}。 |
| SILENT_ENDED | response | 这张卡已委派给 {bot}，但 Linear 没有为这次委派打开会话。这个讨论串里已经没有进行中的工作，{bot} 现在把它结束，好让新的委派能打开会话。要开始处理，请把代理改为「No agent」，再委派给 {bot}。 |

The notes say what FarmBot saw, that no session was opened, and what to do next. They claim no cause, so
they stay true for a blocked delegation, an API delegation and a lost delivery.

Doctor findings:

- `silent_delegation`: "A read found the card delegated to this app again, no agent session followed
  within 90 s, and FarmBot found none of its threads on the card open; or the settle has been failing for
  more than 10 minutes. Most likely a delegation through Linear's API, or a thread FarmBot cannot read
  still waits. Open the card: answer or archive a waiting thread, or ask the person to choose No agent and
  delegate again." It lists an `unseen` episode of the last 7 days whose card has had no delegation
  session since, and a `waiting` one more than 600 s overdue.
- `unclosed_session`: "Linear refused this thread's closing activity six times. The thread may still show
  FarmBot waiting and block the card's next delegation. Check the session in Linear and archive it if it
  waits."

## 8. Test matrix

`R` = `tests/test_receiver.py`, `L` = `tests/test_lifecycle.py`, `G` = `tests/test_ledger.py`,
`S` = `tests/test_scheduler.py`, `P` = `tests/test_session_progress.py`, `C` = `tests/test_cli.py`,
`A` = `tests/test_linear_api.py`, `D` = `tests/test_doctor.py`, `W` = `tests/test_withdrawal.py`,
`K` = `tests/test_skills.py`.

Fixtures:
- Receiver tests of Part B use one clock for the receiver, its ledgers and the lifecycle (rebuild them with
  `clock=`, or `now = [time.time()]` as `tests/test_receiver.py:1324-1331` does): in place compares a job's
  creation with a read's start.
- Tests that assert a notice use a real `Scheduler` built from `tests/test_scheduler.py` pieces with the
  receiver's `self.api`, so `sent()` (`tests/test_receiver.py:1277-1279`) shows every post in order.
- The receiver's `Mock` API needs `session_state.return_value`, and a `Mock` scheduler's
  `stop.return_value` must be an item view: the Stop reply now reads the cancelled job's session.

### 8.1 Part A

| ID | Test | Covers | Expected |
|---|---|---|---|
| TA1 | R `test_stop_in_a_forwarding_thread_closes_the_stopped_works_own_thread` | A1, S23 | STOP_ELSEWHERE in the Stop's thread; exactly one `response` STOPPED_ELSEWHERE in the fix's thread; its progress row is gone |
| TA2 | R `test_stop_in_the_latest_delegation_thread_closes_the_works_own_thread` | A1 | the same for a claimed fix elsewhere |
| TA3 | R `test_stop_in_the_own_thread_posts_one_reply_and_no_notice` | A1 | one response, in that thread |
| TA4 | R `test_stop_whose_work_ended_meanwhile_says_it_already_stopped` | A1 | STOP_ALREADY; nothing signalled |
| TA5 | R `test_stop_that_reaches_a_handed_over_job_closes_the_jobs_thread` | A1 | STOP_ELSEWHERE in the conversation's thread; STOPPED_ELSEWHERE in the job's |
| TA6 | S `test_a_stop_notice_function_may_decline_and_the_heartbeat_still_goes` | A1 | no post for None; the progress row is dropped |
| TA7 | R `test_a_forward_that_resumes_parked_work_notes_its_own_thread` | A2 | the acknowledgement first, then `thought` RESUMED_ELSEWHERE in the job's thread |
| TA8 | R `test_a_reply_that_resumes_a_handed_over_job_notes_the_jobs_thread` | A3 | the thought in the job's thread |
| TA9 | R `test_the_old_thread_is_told_even_when_the_acknowledgement_fails` | A5 | SUPERSEDED and, for a moved conversation, MOVED_THREAD still sent; the event is `uncertain` |
| TA10 | W `NoticeTests`: `("fix", "stopped_elsewhere")`, `("chat", "stopped_elsewhere")` | A1 | the texts; the conversation's has no 分支 or PR |
| TA11 | C `test_await_input_refused_after_its_question_withdraws_the_question` | A6 | `create_activity` cancels the job as a side effect: an elicitation, then QUESTION_WITHDRAWN, then the error |
| TA12 | C `test_await_input_refused_for_work_that_goes_on_posts_no_correction` | A6 | a requeued job, and a flagged one: no correction |
| TA13 | C `test_activity_refuses_an_elicitation` | A8 | refused; nothing posted; no label added |
| TA14 | P `test_a_heartbeat_question_that_crosses_a_closing_notice_is_closed_again` | A7 | one 「工作已停止。」 response after it |
| TA15 | K `test_the_fix_skill_asks_only_through_await_input` | A8 | no "`--type elicitation` only for a question" |
| TO1 | G `test_an_owed_closing_activity_is_kept_once_per_thread_and_never_for_a_local_job` | A4 | |
| TO2 | G `test_an_owed_closing_activity_backs_off_then_is_given_up` | A4 | due after 60, 120, 240, 480 and 960 s; `given_up_at` after the sixth failure |
| TO3 | G `test_an_owed_closing_activity_is_dropped_when_newer_work_speaks_in_the_thread` | A4 | a changed job state, and a newer job |
| TO4 | P `test_a_refused_closing_response_is_retried_until_linear_takes_it` | A4 | one post per due time, each with a new activity id; none after success |
| TO5 | S `test_a_stop_notice_linear_refuses_is_owed` | A4 | `FakeAPI(fail=True)`: a `session_closures` row with the notice |
| TO6 | R `test_a_refused_move_note_stop_reply_or_error_reply_is_owed` | A4 | |
| TO7 | C `test_a_refused_closing_notice_or_final_response_is_owed` | A4 | `withdraw`, `cancel`, `finish` |
| TO8 | D `test_unclosed_session_is_a_finding` | A4 | present for a given-up row; absent on an older ledger |

### 8.2 Part B

| ID | Test | Covers | Expected |
|---|---|---|---|
| TE1 | G `test_a_delegated_read_that_clears_a_mark_opens_an_episode` | P11 | `waiting`, due `since + 90`; the cleared flags are returned as before |
| TE2 | G `test_a_read_with_no_start_or_older_than_the_mark_opens_none` | P11 | the mark is still cleared |
| TE3 | G `test_an_undelegated_read_drops_a_waiting_episode_only_if_it_began_later` | P11, S11 | both sides of the fence |
| TE4 | G `test_a_waiting_episode_is_not_reset_and_a_settled_one_is_replaced` | P11, S13 | |
| TE5 | G `test_an_episode_is_finished_once_by_the_settle_that_saw_it` | P11 | a stale `since` changes nothing |
| TE6 | G `test_heard_means_a_delegation_session_since_the_mark` | §3.2 | not a `local-` session, not a mention's, not an older one |
| TE7 | G `test_supersede_in_place_takes_the_episode_in_the_same_transaction` | P12 | `in_place` with the new job; `StaleRouting` and no change when the episode is not waiting |
| TE8 | G `test_an_older_ledger_gains_the_episode_and_closure_tables` | §6.2 | nothing is due |
| TE9 | C `test_withdraw_passes_the_start_of_its_read` | §3.1 | an episode when it clears the mark |
| TL1 | L `test_a5_...` (`:458`) extended | P11 | the delegated read opens an episode; the lifecycle posts nothing |
| TL2 | L `test_k8_...` (`:771`) extended | S15 | the episode is dropped by the third read; nothing cancelled or posted |
| TL3 | L `test_a_launch_preflight_records_the_transition_too` | §3.1 | |
| TS1 | R `test_s1_a_redelegation_with_no_session_continues_in_the_waiting_delegation_thread` | S1 | before due: nothing. After: the conversation is cancelled; a fix is queued in the same thread with its messages; `sent()` ends with one thought, IN_PLACE_NOTE and the fix acknowledgement; no SUPERSEDED; exactly one active job (the idiom of `tests/test_receiver.py:1636-1638`); the episode is `in_place` |
| TS2 | R `test_in_place_needs_a_thread_linear_says_is_not_archived` | S19, U3 | archived, and a read that raises: no supersede |
| TS3 | R `test_in_place_never_takes_work_newer_than_the_delegation` | S17 | a job created after `since` is told |
| TS4 | R `test_a_claimed_write_job_with_other_labels_is_told_and_keeps_running` | S6 | `thought` SILENT_BUSY; nothing flagged |
| TS5 | R `test_a_running_delegation_conversation_is_superseded_in_place` | S6 | its claim token is refused; the fix is queued in the same thread |
| TS6 | R `test_s2_a_waiting_mention_conversation_is_told_and_kept` | S2 | one `response` SILENT_WAITING_CHAT in the mention thread; the conversation is still `awaiting_input`; a complete older thread gets nothing; the episode is `told` |
| TS7 | R `test_after_the_note_a_reply_resumes_and_a_new_delegation_takes_over` | S22 | the reply resumes the conversation; `delegate("session-2")` then supersedes it (WW C3) and the noted thread gets SUPERSEDED |
| TS8 | R `test_s3_a_running_mention_conversation_gets_a_thought` | S3 | SILENT_BUSY; nothing cancelled |
| TS9 | R `test_s4_a_waiting_fix_of_the_same_kind_asks_again_once` | S4, S15 | one `elicitation` with the question; no supersede; a second episode within 30 minutes posts nothing |
| TS10 | R `test_s5_running_work_of_the_same_kind_is_kept_with_one_line` | S5 | one `thought` |
| TS11 | R `test_s7_an_open_thread_with_no_work_is_closed_and_an_unread_one_is_not` | S7 | `awaitingInput`: SILENT_ENDED. Complete, archived and a failed read: nothing, and the episode is `unseen` |
| TS12 | R `test_s8_a_created_after_in_place_takes_the_card_over` | S8 | P4: the in-place fix is superseded into the new session; exactly one active job |
| TS13 | R `test_s9_a_created_on_time_is_heard_whichever_read_came_first` | S9 | nothing posted past due; `heard` |
| TS14 | R `test_s10_an_api_delegation_with_no_open_thread_is_only_recorded` | S10 | `unseen`; no post |
| TS15 | R `test_s11_a_card_no_longer_delegated_at_the_settle_is_dropped` | S11 | no post; a status read is requested when delegation work is active |
| TS16 | R `test_s12_a_restart_during_the_grace_settles_once` | S12 | a new `Receiver` on the same ledger |
| TS17 | R `test_s13_two_quick_redelegations_settle_once_against_the_last_mark` | S13 | |
| TS18 | R `test_s14_a_card_closed_during_the_grace_is_dropped` | S14 | |
| TS19 | R `test_s20_a_failing_card_read_is_retried_and_posts_nothing` | S20 | the backoff; `attempts` and `error` recorded |
| TS20 | R `test_a_deferred_or_stopped_created_explains_the_episode` | §3.2 | `heard` |
| TS21 | R `test_a_refused_note_is_owed` | P10 | |
| TS22 | R `test_work_that_changed_under_the_settle_is_looked_at_again` | §3.5 | `StaleRouting`: the episode is due again 5 s later; nothing posted |
| TS23 | R `test_s16_a_reply_during_the_grace_is_handled_first` | S16 | |
| TS24 | R `test_s18_an_operators_local_job_gets_no_note` | S18 | |
| TS25 | R `test_s21_stop_after_in_place_is_the_threads_own` | S21 | |
| TN1 | A `test_session_state_reads_status_and_archive_and_accepts_unknown_values` | §3.6 | `stopping` and an unknown value pass; a mismatch raises |
| TD1 | D `test_silent_delegation_is_a_finding` | S7, S10, S20 | an `unseen` episode; an overdue `waiting` one; none once a delegation session followed; absent on an older ledger |
| TW1 | W `test_silent_delegation_texts_format_and_a_conversations_names_no_branch` | §7 | |

### 8.3 Existing tests that change

| Test | Change |
|---|---|
| `tests/test_receiver.py:175`, `:1355`, `:1566`, `:1587` | The Stop call now carries `states=` and a `notice` function: assert `call.args` and `call.kwargs["states"]` |
| `tests/test_receiver.py:1177`, `:1184`, `:1214`, `:1350` | The last activity is now the RESUMED_ELSEWHERE thought in the job's thread; assert the acknowledgement by session through `sent()` |
| `tests/test_receiver.py:1354`, `:1565` | STOP_ELSEWHERE's text (imported, so only the constant changes) |
| `tests/test_withdrawal.py:15-18` | the expected notices gain `stopped_elsewhere` |
| `tests/test_receiver.py:1284-1297` (DT1) | unchanged in outcome; `clear_undelegated` takes `observed_at` |

About 60 new tests, 3 extended ones (TA10, TL1, TL2) and 9 changed assertions: 1432 tests become about
1490. The focused modules run in seconds; the full suite takes about 4 minutes:
`env -u FARMBOT_CONFIG -u FARMBOT_LINEAR_STUB_DIR python3 -B -m unittest discover -s tests`.

## 9. Live checks on the test instance

Ground rules as WW §8.1: a throwaway 【测试】 card delegated only to the test bot; record UTC times; comments,
labels and threads are real. Implementation needs no live check. Deploying the branch to the test instance,
and each check below, need the operator's go-ahead.

### 9.1 Linear facts

None of these blocks the merge: each unknown has a fallback (§3.7). LC-1 should run before the production
deploy. LC-1 and LC-2 need no token and work with the current build.

| # | Do | Record | Settles |
|---|---|---|---|
| LC-1 | Make a delegation silent (a conversation waits; "No agent" without archiving; delegate again within 30 s). Then end the waiting thread with a response while the card stays delegated: the operator CLI `cancel` on the waiting job posts one. Wait 3 minutes. | Whether a `created` for a new session arrives with no further action | U1 |
| LC-2 | With one FarmBot thread open whose last activity is a thought (a running job, or a mention thread that forwarded), choose "No agent" without archiving and delegate again | A new session or not | U2 and the follow-up of Appendix C |
| LC-3 | `agentSession(id){status archivedAt}` on a waiting, a complete and an archived session of the test bot, by the operator with the bot's own token; or read the build's `delegation_episode` log lines during AC-1 and AC-2 | The values; whether an archived session is returned | U3 |
| LC-4 | Only for decision D4: `agentSessionCreateOnIssue` on a delegated card with no open thread, then with one thread waiting | Success or the error; whether `created` arrives; the creator; what the person sees | U9 |

### 9.2 Acceptance of the new build

After each check `doctor` must show no finding the check did not expect.

| # | Do | FarmBot must show |
|---|---|---|
| AC-1 (S1) | A delegation's conversation waits. "No agent" without archiving, add Bot/修改, delegate again within 30 s | Within about 2 minutes, in the same thread: IN_PLACE_NOTE and the fix acknowledgement. The fix starts and reads the conversation's messages. |
| AC-2 (S2) | An @mention's conversation waits. Delegate the card | The note in the mention thread within about 2 minutes. Then "No agent" and delegate: a new session, the fix or conversation with the mention's messages, and SUPERSEDED in the mention thread. |
| AC-3 (S23) | Replay fact 6 | STOPPED_ELSEWHERE in the fix's thread. A delegation afterwards opens a session at once. |
| AC-4 (S4) | A fix waits. "No agent" without archiving, delegate again within 30 s, labels unchanged | REDELEGATED_WAITING with the question in the fix's thread; no new thread; the fix still waits. |
| AC-5 (S9) | An ordinary delegation with no open thread | No extra message. |
| AC-6 (A4), optional | Drop the tunnel for 2 minutes during a cancel | The closing response lands on a retry. |

## 10. Decisions for the operator

| # | Decision | Recommended | Alternative |
|---|---|---|---|
| D1 | **In place.** When the card's unclaimed work, or a conversation, sits in a delegation thread, take the silent delegation over in that same thread (P4 with the same session) on a status read and a fresh card read, with no `created` | Yes. It is the incident's own flow (S1), needs no new Linear call, and is what a reply such as 「修复」 in that thread can already start. | Tell only: the person reads a note and must remove and re-add the delegation they just made. |
| D2 | **Kept.** Delegation work of the kind the labels name is not restarted by a silent re-delegation: a waiting job asks again, a running one goes on | Yes. A delegate flap by an automation then costs one line, not a 20-minute restart. | Strict P4: every silent re-delegation restarts the work in place. |
| D3 | **Notes that end a wait.** Where in place is impossible (an @mention thread, a thread with no work), FarmBot posts a response that ends the wait and keeps the work answerable, and reads the state of its own sessions to find such threads (at most 10 reads per unheard delegation) | Yes. A response is the only documented way to end a wait, and a note without it would leave the next delegation blocked too. | Post nothing; list the card in `doctor` only. |
| D4 | **FarmBot opens a session itself** (`agentSessionCreateOnIssue`) for the cases no thread can take: a first delegation that meets a waiting @mention thread, a card with no open thread, an API delegation | Not in this change. Run LC-4 first; if it succeeds, add it as a follow-up behind the same episode (Appendix C). Three of its behaviours are unknown, its sessions have no creator, and LC-1 may show it is not needed. | Build it now with a fallback ladder: about 3 more commits, 30 more tests and 7 live checks before merging. |
| D5 | **Grace.** 90 s after the read that sees the delegation, in every case | 90 s. It covers the measured 66 s retry. | 30 s when a session read confirms that FarmBot's own thread waits (Linear then opens nothing); faster for the person, one more rule. Revisit after AC-1. |
| D6 | **Rules outside the delegation path.** A closing activity Linear refuses is retried up to 5 times over about 31 minutes (amends P7). `activity` refuses `--type elicitation`. A Stop from another thread also posts one note in the stopped work's thread. | Yes. | Keep "never retried" and rely on the settle-time session read alone. |

## Appendix A. What was rejected, and why

From the "own session" proposal:

| Rejected | Why |
|---|---|
| Opening a session as the first step | It rests on three unverified behaviours (U9), and its fallback ladder on three more. If Linear refuses while a thread waits, it adds nothing to a note that ends the wait. If Linear delivers the blocked delegation later (U1), it gives two threads. Deferred (D4). |
| A `last_activity` record per session and a sweep that closes stranded threads | About 12 recording sites in three processes, and it posts unprompted into threads where nothing is wrong (every mention thread that forwarded, a delivered conversation). A thread that matters is found by one session read when a delegation is actually blocked. |
| An @mention "hears" a delegation FarmBot could not open a session for | It gives a mention `delegation` authority, which widens P1: a mention never starts write work. |
| Tracking an untracked card from the Issue webhook's `data.delegateId` | An unverified payload field, and without a session there is nothing FarmBot could do for such a card. |
| Answering a late `created` by comparing `agentSession.createdAt` | An unverified payload field. P4 is safe for a late `created` and already the rule (WW H6). |
| In place as a synthesized `created` run through `_decide_and_act` | Four conditionals in the receiver's most delicate function, and SUPERSEDE_SUFFIX would say the work came from another session. One `supersede` call does the same. |
| In place for a claimed write job | It restarts up to 20 minutes of work for a delegate flap, and the old worker's SUPERSEDED would name a new session that does not exist. |

From the "prevent and tell" proposal:

| Rejected | Why |
|---|---|
| "Told, never acted on" for S1 | It asks the person to undo and redo the delegation they made 90 seconds earlier, where FarmBot holds a delegation thread and a fresh read of the delegation. |
| A response that closes a delegation job's waiting thread while the job stays parked (S4) | The thread then shows complete over work that waits, and Stop is hidden there. Asking again keeps the thread true. |
| Settling in the lifecycle loop (`due_issue` selecting idle cards, `finish_status_check(next_due=)`, `_refresh(settle=False)`) | The status read has no labels, and acting must be serialised with `created`. The receiver loop gives both and leaves the status loop as it is. |
| A second Stop text for waiting work | One corrected STOP_ELSEWHERE is true for both. |

From "in place" alone (not delivered):

- An @mention thread cannot hold the delegation's write work: `agent/receiver.py:390-391`,
  `agent/ledger.py:2031-2033` and `agent/__main__.py:250` require a delegation session. That was two of the
  three live observations. It also has no answer for a stale thread or an API delegation.

Rejected by all: closing every waiting thread when the delegation goes (it contradicts WW A8 and G2);
removing and re-adding the delegate (P6); retrying without limit.

## Appendix B. Risks

| # | Risk | Mitigation |
|---|---|---|
| R1 | In place starts write work on a read, with no `created` | The thread is a recorded delegation session; a fresh read finds the card delegated; the job is older than the episode; the supersede rechecks the job and the episode in one transaction. A conversation in that thread could already start the same work. |
| R2 | Linear delivers the blocked delegation later (U1) | P4. LC-1 before the production deploy. |
| R3 | A note ends a thread whose question is still open | The question stays readable and a reply still resumes the work. Stop is hidden there, but a parked job holds no worker; "No agent", or a mention's Stop, still reaches it. |
| R4 | An automation that flaps the delegate | Ending undelegated: nothing. Ending delegated: at most one line per job per 30 minutes; work of the same kind is never restarted. |
| R5 | The settle runs network reads on the receiver's thread | At most one card read, 10 session reads and a few posts per unheard episode; pending events always go first; the first failed session read ends the reads. |
| R6 | After more than a minute of downtime, a delegation whose session did open is unheard until Linear's retry an hour later | At worst a note or an `unseen` finding. The job's thread is archived in that flow, so nothing is taken in place (S19). The `created` then runs P4. |
| R7 | A Stop racing a finish no longer signals the finished job's process | The reaper and `_stop_cancelled` do. |
| R8 | An owed activity lands up to 31 minutes late | It is dropped when the job's state changed or newer work started there. |
| R9 | Test churn | Nine assertions (§8.3). |

## Appendix C. Follow-ups, each needing its own decision

| Follow-up | Waits for |
|---|---|
| FarmBot opens a session when no thread can take the delegation (D4). It plugs into §3.5 step 6 before "told": an episode state `opened`, the session recorded with `delegation=1`, and a `created` event of its own under the key Linear's would use. | LC-4 and LC-1 |
| Closing threads that end with no activity (A12): an `error` for a slot-pool failure and for exhausted publication retries; a response in a forwarding thread when its work ends | LC-2 |
| A shorter grace when FarmBot's own thread is confirmed waiting (D5) | AC-1 |
| Reading the delegate fields of the Issue webhook, to catch a removal and re-delegation that no read saw (§3.8) | one recorded payload |

## Appendix D. What was checked for this design

Read at 7fd47a7: `agent/receiver.py`, `agent/lifecycle.py`, `agent/ledger.py`, `agent/scheduler.py`,
`agent/session_progress.py`, `agent/__main__.py`, `agent/linear_api.py`, `agent/withdrawal.py`,
`agent/router.py`, `agent/service.py`, `agent/doctor.py`, `agent/config.py`, `agent/resource_recovery.py`,
`skills/fix/SKILL.md`, `docs/operating-contract.md`, the withdrawn-work design, and the receiver,
lifecycle, scheduler, CLI, progress and withdrawal tests.

Checked by running code on a temporary ledger (no repository file changed):

- `Ledger.supersede` into the job's own session works today: the old conversation is cancelled, the fix is
  queued in the same session with the messages, a second call raises `StaleRouting`, and `stop_target`
  finds the new job as `own`.
- `Scheduler.stop` with a notice function that returns None posts a response with no body today, so the
  change at `agent/scheduler.py:325-326` is needed. With `states`, a stop of an ended job returns None and
  signals nothing.
- A card whose only work is a mention's waiting conversation is marked undelegated by the poll; the read
  that finds the delegation clears the mark, posts nothing and returns no trace of the transition; an idle
  card is read only on request.
- A delegation routes to a conversation with no Bot label, to `fix` with Bot/修改, and to a conversation
  with a Bot child this instance does not run.
- The suite has 1432 tests at 7fd47a7.

## Appendix E. Changes during implementation

Where the code departs from the text above, and why. The operating contract states the result. The branch holds
the five commits of §5, each with the fixes its review asked for, and the fixes of a final review.

| Where | Change | Why |
|---|---|---|
| A1 | A Stop in another thread that ends an operator's `local-` job posts STOPPED_ELSEWHERE as a comment on the card, and STOP_ELSEWHERE in the Stop's thread. | `stop_target` can return such a job for a Stop in the card's latest delegation thread, and every closing notice of a `local-` job is a card comment. |
| A2, A3 | RESUMED_ELSEWHERE is posted only to a job that is still active and not back in `awaiting_input` once the forwarding thread's acknowledgement returns. | The scheduler can launch the resumed job during that round trip. A note after its closing activity, or after a new question, would bury them (P9). |
| A4 | An owed row records the refusal's exception class and the card, taken from the job or from the thread's session. The result of a try belongs to the row that was tried (`owed_at`), and so does giving it up. The Stop reply is owed for the job the Stop ended or found ended, the event error reply for the thread's active job, following a conversation's handover, or for none. | `doctor` shows why a row failed. The scheduler, the receiver or a worker can owe a newer activity to the same thread during the progress loop's round trip. Each reply goes with its job's state change (P10). |
| A6 | The question is withdrawn only while its thread has no active work of its own: "no correction for a job that is queued or claimed again" also covers newer work created in that thread meanwhile. A conversation that handed over to a job in another thread still has its question withdrawn. | Otherwise 「这段对话已经结束」 closed a thread in which a takeover in place had just queued a job. |
| A7 | The terminal text is posted only when Linear took the crossing question and the thread's current job is the job that ended. | A refused question is not in the thread, and a thread with newer work must not be closed. |
| A8 | `--type elicitation` stays a parser choice, so that `activity` refuses it with the command to use; `activity --help` offers only `thought`, `action`, `response` and `error` and names `await-input`. | The refusal says what to do, and the help a worker is sent to offers nothing `activity` refuses. |
| A9 | An event whose routing decision is none of work, chat, steer or resume fails, and its thread gets the error reply. | With the `elicit` branch gone, such a decision would post nothing, record the event done and leave the person's message unanswered. |
| P10, dropping | Four rules, not two. (1) A change of the job's state drops its row in the same transaction, a given-up row too. (2) A job created in the thread after the row drops it at the next try. (3) The error a failed event was answered with, owed for no job or for an active job, goes once Linear takes the answer to a later event of the same thread: its acknowledgement, a deferred delegation's DEFER_ACK, a Stop's reply, or the later event's own error. (4) A response owed for no job, a note that the thread holds no work or a Stop's reply that found nothing to stop, is dropped at its next try while the thread reaches active work, its own or the job its messages were forwarded to; not merely because the thread is the card's latest delegation thread. | (1) Comparing the job's state at try time missed a job that left the recorded state and came back to it, and the older words followed its newer ones. (3) A message steered into a running job, or forwarded from a thread with no job, changes no state and starts no job there. (4) Such a note would say the thread has none and close it, and a Stop there must keep reaching the work (§3.5, P5). |
| P10, one last word | A later row replaces a thread's owed row and starts its tries anew, except that what closes the thread for a job that ended there is not replaced by a row owed for no job or for an active job while it still holds, that is, until newer work starts in the thread. The later row is then not owed. | Rules (3) and (4) drop such a row and leave the job's closing words. Replaced and then dropped, it left the thread with neither, and `doctor` with nothing to list. |
| P10, R8, the window | A row still owed more than 40 minutes after Linear first refused it is given up untried at its next try, and `doctor` lists it as after a sixth refusal. | The five retries take 31 minutes while the controller runs. After downtime, or a rollback and a roll forward, the older words would land hours or days after what they answer. |
| P10, `unclosed_session` | Listed for 7 days after the row was given up, and not once its job's state changed or newer work started in its thread. The hint names both ways a row is given up. | Nothing deletes a given-up row, and a finding that never clears would hide the next one. |
| P10, the progress loop | A pass that handles an owed activity, tried, dropped or given up, sends no heartbeat. | One remote call per pass keeps a pass short while Linear is down. |
| P11, §3.3 | `retry_episode` with `error` counts a failed read in `attempts`; a look-again after StaleRouting counts nothing and clears the last failure. `finish_episode` keeps `attempts` and clears the last failure. | The backoff of §3.3 is a function of the failed reads. |
| §3.2 | `delegation_heard` reads `webhook_events` only where the receiver has created it; a payload that is not JSON names no card; "a Stop cancelled at or after the mark" is a `cancelled` row completed at or after it. | A ledger no receiver opened has no such table. |
| §3.1, `withdraw` | The CLI takes the read's start before the read and passes it in every case; the ledger uses it only where the mark is cleared. | |
| §3.5, the read | A read that answers for another card is a failed read, retried with backoff. | As the lifecycle checks (P8). |
| §3.5, S14 | The full read of a card also reads `trashed`, and a trashed card reads as archived, as the status read has it (withdrawn-work design E3). | Otherwise a card deleted during the grace read as open: the settle kept or told its work, and `doctor` listed its episode for 7 days. |
| §3.5, S20, §6.1 | An episode whose card Linear keeps answering "not found" ends as `dropped`, logged `unreachable`, and nothing is posted: 3 failed reads or more, the first "not found" of the unbroken row at least 900 s old, and another call to Linear successful since (withdrawn-work design R8). Any other failure keeps the backoff. New column `delegation_episodes.unreachable_since`, also added to an episode table without it. `fetch_issue` raises a `not_found` LinearError for an issue Linear returns as null. | Retrying without limit was rejected (Appendix A): a deleted card was read every 5 minutes for the life of the ledger and listed by `doctor` as long. |
| §3.5, the table | The thread of a conversation that handed over to the card's active job is left open, as one that forwards to it. It is closed like any thread with no work once that job has ended. | A reply or a Stop there reaches that job (A3, P5), and SILENT_ENDED would not be true of it. |
| §3.5, notes | Each told note is checked again just before it is posted: the response for a waiting job is dropped when the job has left that state, SILENT_ENDED when the thread has work by then. `redelegated` is recorded on the active job only when its own thread got a note, and for a line only when Linear took it. | The session reads between building the targets and posting take seconds (P9). A refused line is tried again at the next episode, not 30 minutes later. |
| §3.5, lines | No kept or told line for work that is being withdrawn, nor for a job that has left the state the settle saw. The 30-minute limit also covers SILENT_BUSY. | "It goes on" would be false; the job has spoken in its thread since. R4 promises one line per job in 30 minutes. |
| §3.5, kept | A question asked again whose job ended while Linear took it is withdrawn with QUESTION_WITHDRAWN or QUESTION_WITHDRAWN_CHAT, owed when refused. | A6's rule, for the one other place a question is posted. |
| §3.5, outcomes | `changed` is also logged when `finish_episode` finds the episode no longer waiting; nothing is posted or retried then. | A read dropped or replaced the episode under the settle. |
| §3.5, the log line | It lists each thread the settle read, with its status and whether it is archived, null for a read Linear refused; never a body. | LC-3 reads these lines. |
| §3.6 | `session_state` also raises for an answer with no status. | The caller then knows nothing of that thread (P8). |
| §7, `silent_delegation` | "More than 600 s overdue" counts from the end of the grace, `since` + 90 s, not from `due_at`. | A failing settle moves `due_at` on each time, so the finding's own "the settle has been failing for more than 10 minutes" could never fire. |
| §7, the last paragraph | The notes do not stay true after a lost delivery: Linear did open a session then, which FarmBot has not heard of (R6), and 「Linear 没有为这次委派打开会话」 says it did not. The texts are unchanged; changing them is the operator's decision. | |
| §7, SILENT_WAITING | Its 「会从已有进度接着做」 holds only when the new delegation's job is of the waiting job's skill, which links it as predecessor. The note is posted when the labels name another skill, whose job gets the messages but not the progress. The text is unchanged; changing it is the operator's decision. | |

Left as the design has it, or as a known limit:

- The Stop reply in the job's own thread still says 「worker 已终止」, as §5 has it, although the stopped job may
  have been queued or waiting. A change of text is the operator's decision.
- A2's note follows the forwarding thread's acknowledgement. When Linear refuses that acknowledgement, the resumed
  job's thread gets no note; its worker's next activity or heartbeat follows there.
- An event error owed in a thread whose job goes on, with no later event there, is posted at its next try, after
  whatever the job said meanwhile: the message was not handled, and says so. An event error that meets the closing
  words of work that ended in its thread is not owed, and the failed message goes unreported, as before this change.
- A given-up response owed for no job stays listed by `doctor` for 7 days even if its thread reaches work later;
  a given-up row is never tried again, so nothing is posted.
- A read that returned out of order can open a second episode for one delegation: a kept job then gets at most one
  line in 30 minutes, and a thread that was told is complete and gets nothing again.
- An episode can end `told` with nothing posted: its only note was a thought the 30-minute limit held back, or every
  note was dropped just before posting. `doctor` then lists nothing, which is right for a thread told minutes ago.
- A `created` that arrives while a settle runs is pending, not heard: the settle acts, and the event is then P4, as
  S8 has it.
- The card's latest delegation thread with no job of its own is closed when Linear shows it open, although a Stop
  there reaches delegation work in another thread (P5's third branch): §3.5's table as written.
- On a host where no other call to Linear succeeds, the episode of a card that is gone keeps waiting and is listed
  by `doctor` until any call succeeds: R8's outage guard. A card the app may not read is retried without end, as its
  status reads are (withdrawn-work design E5).
