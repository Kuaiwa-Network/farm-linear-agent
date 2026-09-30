"""Verified Linear agent-session webhooks -> ledger work items (spec §3, §4, §15)."""
from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
import os
import re
import socket
from socketserver import TCPServer
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .ledger import ACTIVE_STATES, TERMINAL_STATUS_TYPES, LedgerError, StaleRouting
from .lifecycle import UNREACHABLE_READS, UNREACHABLE_SECONDS
from .linear_api import LinearError, person
from .router import WRITE_SKILLS, route
from .withdrawal import (DEFER_ACK, DEFER_STILL, DEFER_UNDELEGATED, FORWARD_PARKED_UNDELEGATED, FORWARD_WITHDRAWING,
                         IN_PLACE_NOTE, MOVED_THREAD, REDELEGATED_RUNNING, REDELEGATED_WAITING, RENOTE_SECONDS,
                         RESUME_UNDELEGATED, RESUMED_ELSEWHERE, SILENT_BUSY, SILENT_ENDED, SILENT_WAITING,
                         SILENT_WAITING_CHAT, STOP_ALREADY, STOP_ELSEWHERE, STOP_MOVED, STOP_MOVED_THREAD,
                         SUPERSEDE_SUFFIX, SUPERSEDED, grace_seconds, notice as withdrawal_notice, question_withdrawn)
from .worktrees import WorktreeError

MAX_BODY = 1024 * 1024
_PLAIN_WORD = re.compile(r"[A-Za-z]{1,40}")
TARGET_REPO = "Farm-Client"
PIN_TIMEOUT = 8
# {bot} is the instance's configured Linear app name: a development instance shares the workspace with
# production, and an acknowledgement in the wrong name gets the wrong bot stopped.
ACK = {"fix": "{bot} 已收到委派，正在排队处理这张修改卡。进展和草稿 PR 会更新在这里。",
       "fgui": "{bot} 已收到委派，正在排队处理这张 UI 卡。进展、预览和草稿 PR 会更新在这里。",
       "feature": "{bot} 已收到委派，正在排队处理这张功能卡。进展、问题和草稿 PR 会更新在这里。",
       "chat": "{bot} 已收到，正在查看。", "qa": "{bot} 已收到测试请求，正在排队。"}
# D18: the first message of a delegation whose card has no Bot label, where a Bug card used to start a fix.
NO_BOT_LABEL = ("{bot} 已收到。这张卡没有 Bot 标签，先以只读对话查看。需要修复或修改，请在这里回复（例如「修复」）；"
                "以后委派前先加上 Bot/修改 标签，就会直接开始处理。")
# The states of work no worker holds, which a newer session takes over at once (withdrawn-work design P4).
UNCLAIMED = ("queued", "awaiting_input", "awaiting_resource")
# The states of work a Linear Stop ends: every state a cancel accepts. A job that finished or was cancelled between
# the Stop's lookup and its cancel is not one, and the Stop then says so (silent-delegation design A1).
STOPPABLE = (*ACTIVE_STATES, "blocked")
# A delegation deferred behind a withdrawing worker waits this long past the worker's deadline, by which the
# controller has stopped it, before its event reports that the worker has not stopped (design C2).
DEFER_SLACK = 300
# An event whose routing met work that changed meanwhile is routed again at most this many times (design J5).
REROUTES = 2
# Settling a delegation Linear opened no session for (silent-delegation design P12, §3.3-§3.6). A settle reads the
# state of at most SESSION_READS of FarmBot's own threads on the card. One that found the work or the episode changed
# under it looks again RESETTLE_SECONDS later; one whose read of the card failed is tried again after 15, 30, 60, 120
# and 240 seconds, then every 300, until the card is out of reach by the lifecycle's rule (withdrawn-work design R8).
SESSION_READS = 10
RESETTLE_SECONDS = 5
SETTLE_RETRY_SECONDS = 15
SETTLE_RETRY_MAX = 300
# What Linear shows for a thread a response or an error closed. Linear adds status values (`stopping` came on
# 2026-09-24), so every other value, also one nobody knows yet, is an open thread (§3.6).
CLOSED_STATUSES = ("complete", "error")
IN_PLACE_REASON = "a delegation Linear opened no session for took the work over in its own thread"
FORWARDED = "该 issue 正在处理中，你的消息已转给正在处理的 worker。"
RESUMED = "收到回复，原工作项已恢复，worker 会先读取你的回答。"


class Deferred(Exception):
    """A new delegation waits for the claimed worker of item `item_id` to withdraw (design P4, C2)."""

    def __init__(self, item_id):
        super().__init__(item_id)
        self.item_id = item_id


class Receiver:
    def __init__(self, db_path, secret, identity, api, ledger_factory, skills, scheduler, clock=time.time,
                 worktrees=None, default_server_environment="公共测试服", bot_name="FarmBot"):
        self.secret = secret.encode()
        self.identity = identity
        self.api = api
        self.ledger = ledger_factory()
        self.skills = set(skills)
        self.scheduler = scheduler
        self.clock = clock
        self.worktrees = worktrees
        self.default_server_environment = default_server_environment
        self.bot_name = bot_name
        self.lock = threading.Lock()
        self.db = sqlite3.connect(db_path, check_same_thread=False, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE IF NOT EXISTS webhook_events (
            event_key TEXT PRIMARY KEY, session_id TEXT NOT NULL, ack_id TEXT NOT NULL, status TEXT NOT NULL,
            payload TEXT, received_at REAL NOT NULL, completed_at REAL, error TEXT)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS stop_requests (
            stop_key TEXT PRIMARY KEY, session_id TEXT NOT NULL, activity_id TEXT NOT NULL, status TEXT NOT NULL,
            received_at REAL NOT NULL, completed_at REAL, error TEXT)""")
        self.db.execute("UPDATE webhook_events SET status='uncertain', error='InterruptedProcessing' WHERE status='processing'")
        self.db.commit()
        if str(db_path) != ":memory:" and os.name != "nt":
            os.chmod(db_path, 0o600)

    def close(self):
        with self.lock:
            self.db.close()
            self.ledger.close()

    @staticmethod
    def _source_time(event):
        source = event.get("agentActivity") if event["action"] == "prompted" else event["agentSession"]
        value = source.get("createdAt") if isinstance(source, dict) else None
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.timestamp() * 1000 if parsed.tzinfo else None
        except ValueError:
            return None

    def receive(self, raw, signature, now_ms=None):
        expected = hmac.new(self.secret, raw, hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(expected, signature):
            return 401, "invalid signature"
        try:
            event = json.loads(raw)
        except (ValueError, UnicodeError, RecursionError):  # RecursionError: nested too deeply to parse
            return 400, "invalid json"
        if not isinstance(event, dict):
            return 400, "invalid event"
        timestamp = event.get("webhookTimestamp")
        now_ms = self.clock() * 1000 if now_ms is None else now_ms
        if type(timestamp) not in (int, float) or not math.isfinite(timestamp) or abs(timestamp - now_ms) > 60_000:
            return 401, "invalid timestamp"
        if event.get("type") == "Issue":
            if event.get("organizationId") != self.identity["organizationId"]:
                return 403, "identity mismatch"
            if event.get("action") not in ("create", "update", "remove"):
                return 200, "ignored"
            data = event.get("data")
            try:
                with self.lock, self.db:
                    issue_id = data.get("id") if isinstance(data, dict) else None
                    issue_id = str(uuid.UUID(issue_id))
                    accepted = self.db.execute("UPDATE issue_checks SET requested=1,due_at=0 WHERE issue_id=?",
                                               (issue_id,)).rowcount
            except (ValueError, TypeError, AttributeError):
                return 400, "invalid issue"
            return 200, "accepted" if accepted else "ignored"
        if event.get("type") != "AgentSessionEvent":
            return 200, "ignored"
        if any(event.get(k) != v for k, v in self.identity.items()):
            return 403, "identity mismatch"
        action = event.get("action")
        if action not in ("created", "prompted"):
            return 200, "ignored"
        session = event.get("agentSession")
        session_id = session.get("id") if isinstance(session, dict) else None
        if not isinstance(session_id, str) or not session_id or len(session_id) > 128:
            return 400, "missing session"
        event_id = session_id
        if action == "prompted":
            activity = event.get("agentActivity")
            if not isinstance(activity, dict) or not activity.get("id"):
                return 400, "missing prompt"
            if activity.get("agentSessionId", session_id) != session_id:
                return 403, "activity session mismatch"
            if activity.get("signal") == "stop":
                return self._receive_stop(event)
            content = activity.get("content")
            if not isinstance(content, dict) or content.get("type") != "prompt":
                return 200, "ignored"
            event_id = activity["id"]
        try:
            prepared = self._prepare(event)
        except ValueError:
            return 400, "invalid prompt"
        key = f"{self.identity['organizationId']}:{action}:{event_id}"
        with self.lock, self.db:
            inserted = self.db.execute("INSERT OR IGNORE INTO webhook_events VALUES (?,?,?,'pending',?,?,NULL,NULL)",
                                       (key, session_id, str(uuid.uuid4()), json.dumps(prepared), self.clock())).rowcount
        return 200, "accepted" if inserted else "duplicate"

    def _prepare(self, event):
        session = event["agentSession"]
        issue = session.get("issue") or {}
        issue_id = issue.get("id")
        if not isinstance(issue_id, str) or not issue_id:
            raise ValueError("session without issue")
        # People only as the shared person shape: Linear's webhook users also carry an email and an avatar, which
        # FarmBot never keeps. Linear leaves `creator` unset when automation or an agent started the session.
        creator = _person(session.get("creator"))
        if event["action"] == "prompted":
            text = event["agentActivity"]["content"].get("body") or ""
            author = _person(event["agentActivity"].get("user"))
        else:
            text = (session.get("sourceComment") or session.get("comment") or {}).get("body") or ""
            author = creator  # whoever opened the session wrote the comment that opened it
        if not isinstance(text, str) or len(text) > 32000:
            raise ValueError("oversized prompt")
        guidance = event.get("guidance")
        guidance = guidance if isinstance(guidance, str) else json.dumps(guidance, ensure_ascii=False) if guidance else ""
        if len(guidance) > 32000:
            raise ValueError("oversized guidance")
        return {"action": event["action"], "session_id": session["id"], "issue_id": issue_id, "text": text,
                "is_mention": bool(session.get("comment") or session.get("commentId")
                                   or session.get("sourceComment") or session.get("sourceCommentId")),
                "guidance": guidance, "creator": creator, "author": author}

    def _receive_stop(self, event):
        session_id = event["agentSession"]["id"]
        stop_key = f"{self.identity['organizationId']}:stop:{event['agentActivity']['id']}"
        with self.lock, self.db:
            inserted = self.db.execute("INSERT OR IGNORE INTO stop_requests VALUES (?,?,?,'pending',?,NULL,NULL)",
                                       (stop_key, session_id, str(uuid.uuid4()), self.clock())).rowcount
            if inserted:
                # A delegation deferred behind another session's worker goes with the Stop too (design D2).
                self.db.execute("UPDATE webhook_events SET status='cancelled',completed_at=? WHERE session_id=? "
                                "AND status IN ('pending','deferred')", (self.clock(), session_id))
        return 200, "stop received" if inserted else "duplicate"

    def _send(self, session_id, activity_id, content):
        self.api.create_activity(session_id, content, activity_id=activity_id)

    def _process_stop(self):
        with self.lock:
            row = self.db.execute("SELECT * FROM stop_requests WHERE status='pending' ORDER BY received_at LIMIT 1").fetchone()
            if row is None:
                return False
            self.db.execute("UPDATE stop_requests SET status='processing' WHERE stop_key=?", (row["stop_key"],))
            self.db.commit()
        status, error = "done", None
        body = stopped = None
        try:
            # The session's own work, else the work its messages were forwarded to, else, from the card's latest
            # delegation session, the delegation's work wherever it runs (design P5).
            item, where = self.ledger.stop_target(row["session_id"])
            if item is not None:
                here = row["session_id"]
                # The job the cancel ends decides what is said, not the job that was looked up: the cancel follows a
                # conversation's handover to a job in another thread. A job that lives in another thread than the
                # Stop's gets one closing response there, so its thread does not keep a question as its last
                # activity; this reply is the last word of the Stop's own thread, and the job's pending heartbeat
                # goes with the cancel either way (silent-delegation design A1, P9).
                cancelled = self.scheduler.stop(
                    item["id"], "Linear stop", states=STOPPABLE,
                    notice=lambda job: None if job["session_id"] == here
                    else withdrawal_notice(job["skill"], "stopped_elsewhere", self.bot_name))
                if cancelled is None:
                    body = STOP_ALREADY  # it ended between the lookup and the cancel: nothing was stopped
                elif cancelled["session_id"] == here:
                    body = f"已停止 {item['identifier']} 上的工作，worker 已终止，占用的资源在静默检查后释放。"
                else:
                    body = STOP_ELSEWHERE.format(identifier=item["identifier"])
                stopped = cancelled or item
            else:
                body = {"moved": STOP_MOVED, "moved_thread": STOP_MOVED_THREAD,
                        "stopped": STOP_ALREADY}.get(where, "当前没有正在进行的工作可停止。")
            self._send(row["session_id"], row["activity_id"], {"type": "response", "body": body})
            self._answered(row["session_id"])
        except Exception as exc:
            status, error = "uncertain", type(exc).__name__
            if body is not None:
                # The reply is what closes the Stop's thread, so one Linear refused is owed to it, for the job the
                # Stop ended or found ended (silent-delegation design A4, P10). The Stop itself stays uncertain.
                self._owe(row["session_id"], "response", body, exc, job=stopped)
        with self.lock, self.db:
            self.db.execute("UPDATE stop_requests SET status=?,completed_at=?,error=? WHERE stop_key=?",
                            (status, self.clock(), error, row["stop_key"]))
        return True

    def _decide_and_act(self, prepared, ack_id, received_at=None):
        """received_at: when this event reached FarmBot. A pending event may be processed much later (it survives a
        restart), and the messages it adds keep that time, which dates a ruling given in one of them."""
        fetched_at = self.clock()
        issue = self.api.fetch_issue(prepared["issue_id"])
        self.ledger.observe_issue(issue)
        app = self.identity["appUserId"]
        delegated = bool(app) and issue.get("delegate_id") == app
        # This fresh read of the card is a status read too (withdrawn-work design DT1, P3). Finding the delegation
        # clears the issue's undelegated mark and the flags its loss set, and records from the read's start that the
        # delegation is back (silent-delegation design P11); not finding it while the delegation's work is active
        # asks the lifecycle to read the card now, not at its next interval.
        if delegated:
            self.ledger.clear_undelegated(issue["id"], observed_at=fetched_at)
        else:
            current = self.ledger.active_item_for_issue(issue["id"])
            if current is not None and current["authority"] == "delegation":
                self.ledger.request_status_check(issue["id"])
        session = self.ledger.session(prepared["session_id"])
        if session is None and prepared["action"] == "created" and prepared.get("is_mention") and delegated:
            # Delegations may include Linear's synthetic thread comment. Verify
            # its origin off the webhook ACK path before granting write authority.
            if self.api.session_has_artificial_root(prepared["session_id"], issue["id"], app):
                prepared = {**prepared, "is_mention": False, "text": ""}
        is_delegation = (session["delegation"] if session else
                         prepared["action"] == "created" and not prepared.get("is_mention") and delegated)
        # .get: an event the previous revision accepted, still pending at upgrade, carries neither person.
        session = self.ledger.ensure_session(prepared["session_id"], issue["id"], is_delegation, prepared["guidance"],
                                             creator=prepared.get("creator"))
        author = prepared.get("author")
        session_id = prepared["session_id"]
        text = prepared["text"]

        def routing():
            """This session's work, the router's decision, the issue's work in any session, whether the event
            concerns feature work, and whether it re-routes a delegation (D16)."""
            active = self.ledger.active_item_for_session(session_id)
            history = self.ledger.items_for_session(session_id)
            # D16: a reply in a delegation session that never had a work item (another session's work declined or
            # took its delegation) routes again on the labels fetched above. Only while the issue is still delegated
            # to this app: routing is what grants write work.
            reroute = (prepared["action"] == "prompted" and is_delegation and active is None and not history
                       and delegated)
            decision = route(action=prepared["action"], is_delegation=is_delegation, text=text,
                             labels=issue["labels"], active_state=active["state"] if active else None,
                             terminal_exists=bool(history) and active is None, available_skills=self.skills,
                             label_groups=issue.get("label_groups") or (), reroute=reroute)
            elsewhere = self.ledger.active_item_for_issue(issue["id"])
            # Plan P6: the Farm-Client target is a fix's reproduction baseline. A session whose delegation starts
            # feature work gets none, declined or not, and no later event in it adds one, such as a reply that
            # steers or resumes that work; nor does a mention in another session that is forwarded to a feature
            # job. No acknowledgement of such an event carries a target line.
            forwarded = decision.kind == "chat" and elsewhere is not None and elsewhere["session_id"] != session_id
            feature_work = ((decision.kind == "work" and decision.skill == "feature")
                            or any(entry["skill"] == "feature" for entry in history)
                            or (forwarded and elsewhere["skill"] == "feature"))
            return active, decision, elsewhere, feature_work, reroute

        routed = routing()
        pin = ""
        if session.get("target") is None and self.worktrees is not None and not routed[3]:
            try:
                commit = self.worktrees.remote_head(TARGET_REPO, timeout=PIN_TIMEOUT)
                session = self.ledger.set_session_target(prepared["session_id"], {
                    "repository": TARGET_REPO, "requested_ref": "default", "commit_sha": commit,
                    "server_environment": self.default_server_environment,
                    "selected_at": datetime.now(timezone.utc).isoformat()})
                pin = f"\n目标已锁定：{TARGET_REPO}@{commit[:7]}（{self.default_server_environment}）。"
            except (WorktreeError, subprocess.TimeoutExpired, OSError):
                # An origin we cannot reach must not stop the session; the item runs unpinned and any Unity
                # rung will refuse it, which is a recorded gap rather than a silent wrong-commit run. Only
                # that failure is absorbed: a LedgerError here is a fault in our own store, and reporting it
                # as an unreachable origin would hide it from a human and from the event's own status, so it
                # is left to process_one, which marks the event uncertain and says so in the session.
                pin = "\n暂时无法锁定客户端提交，本次将不做 Unity 验证。"
            # The ls-remote can take seconds, in which work can start, end or move: act on what is true once it
            # returns, as before plan P6 needed the decision first.
            routed = routing()

        def acknowledge(kind, body):
            """One event, one activity — and the pin rides in whichever branch sends it. Echoing only from the
            work and chat branches would let a session that first steers or resumes store its pin in silence
            and never announce it, because every later event sees a target that is no longer None (spec §6).
            It is a thought, or a response that starts nothing: a question is asked only by a worker, through
            `await-input`, which parks its job (silent-delegation design A9, P9)."""
            self._send(session_id, ack_id, {"type": kind, "body": body + pin})

        def take_over(decision, elsewhere, feature_work):
            """This delegation session owns the card, whose work is `elsewhere`, in another session (design P4)."""
            skill = decision.skill if decision.kind == "work" else "chat"
            if elsewhere["state"] in UNCLAIMED or elsewhere["skill"] == "chat":
                # Unclaimed work, or any conversation, is cancelled and continued here in one transaction, with its
                # messages. The receiver only writes the ledger: the scheduler's next tick kills a conversation's
                # worker, after this acknowledgement, which Linear wants within seconds (critique 2.9).
                self.ledger.supersede(elsewhere["id"], ACTIVE_STATES if elsewhere["skill"] == "chat" else UNCLAIMED,
                                      session_id=session_id, skill=skill,
                                      reason="a new delegation session took the card over",
                                      target=None if feature_work or skill == "chat" else session.get("target"),
                                      authority="delegation", text=text, author=author, received_at=received_at)
                # The takeover is done whatever becomes of this acknowledgement, so the old thread is told even
                # when Linear refuses it (silent-delegation design A5).
                try:
                    acknowledge("thought", self._opening(decision, text) + "\n" + SUPERSEDE_SUFFIX)
                finally:
                    self._close_moved(elsewhere, SUPERSEDED)
                return
            # A claimed write worker saves its progress and withdraws, or the controller stops it at its deadline;
            # this event waits for that, then runs as the delegation it is (design C2).
            grace = self._grace(elsewhere["skill"])
            self.ledger.flag_withdrawal(elsewhere["id"], "superseded", self.clock() + grace)
            if prepared.get("deferred"):
                acknowledge("response", DEFER_STILL)
                return
            acknowledge("thought", DEFER_ACK.format(bot=self.bot_name, skill=elsewhere["skill"],
                                                    minutes=round(grace / 60)))
            raise Deferred(elsewhere["id"])

        def act(active, decision, elsewhere, feature_work, reroute):
            if prepared.get("deferred") and not delegated:
                # The delegation this event waited behind another session's worker to run was removed meanwhile. Its
                # session still records a delegation, but nothing authorises its work any more, and the person asked
                # for nothing since: the event answers once and starts nothing (design C2, as D16 needs `delegated`).
                acknowledge("response", DEFER_UNDELEGATED.format(bot=self.bot_name))
                return
            # A delegation's own event, while the card is delegated here, owns the card (design P4).
            owns = is_delegation and delegated and (prepared["action"] == "created" or reroute)
            if elsewhere is not None and elsewhere["session_id"] != session_id and decision.kind in ("work", "chat"):
                if owns:
                    return take_over(decision, elsewhere, feature_work)
                if elsewhere["skill"] == "chat" and elsewhere["state"] in UNCLAIMED:
                    # A waiting conversation moves to the thread that answers it (design C5, 2.12), keeping the
                    # delegation's authority only while the card is still delegated here. No new delegation took it,
                    # so the old thread is told only that it moved.
                    authority = "delegation" if elsewhere["authority"] == "delegation" and delegated else "mention"
                    self.ledger.supersede(elsewhere["id"], UNCLAIMED, session_id=session_id, skill="chat",
                                          reason="a person's message moved the conversation to another session",
                                          authority=authority, text=text, author=author, received_at=received_at,
                                          takeover=False)
                    try:
                        acknowledge("thought", self._opening(decision, text) if decision.kind == "chat"
                                    else ACK["chat"].format(bot=self.bot_name))
                    finally:
                        self._close_moved(elsewhere, MOVED_THREAD)  # as a takeover's old thread (design A5)
                    return
                # Forwarded to work that stays where it is, and a Stop here now reaches it (design P5). The notice
                # says what becomes of the message (design R10).
                flagged = elsewhere["withdraw_deadline"] is not None
                resume = (delegated or elsewhere["skill"] == "chat") and not flagged
                delivered = self.ledger.push_inbox(elsewhere["id"], text or "（无正文）", resume_waiting=resume,
                                                   author=author, received_at=received_at)
                self.ledger.record_forward(session_id, delivered["item_id"])
                resumed = not flagged and elsewhere["state"] == "awaiting_input" and delivered["state"] == "queued"
                if flagged:
                    notice = FORWARD_WITHDRAWING
                elif resumed:
                    notice = RESUMED
                elif not delegated and elsewhere["authority"] == "delegation":
                    notice = FORWARD_PARKED_UNDELEGATED.format(bot=self.bot_name, skill=elsewhere["skill"])
                else:
                    notice = FORWARDED
                if decision.text and decision.text != text:
                    notice = decision.text + "\n" + notice
                acknowledge("thought", notice)
                if resumed:
                    self._note_resumed(delivered["item_id"], session_id)
                return
            if decision.kind == "work":
                if decision.skill in WRITE_SKILLS and not is_delegation:
                    raise RuntimeError("router produced write work from a mention")
                item = self.ledger.create_work_item(issue_id=issue["id"], session_id=session_id, skill=decision.skill,
                                                    target=None if feature_work else session.get("target"),
                                                    authority="delegation")
                if text:
                    self.ledger.push_inbox(item["id"], text, author=author, received_at=received_at)
                acknowledge("thought", self._opening(decision, text))
            elif decision.kind == "chat":
                # Design P1: a conversation a delegation opens while the card is delegated here is the delegation's;
                # any other is a mention's, which no withdrawal of the delegation touches.
                item = self.ledger.create_work_item(issue_id=issue["id"], session_id=session_id, skill="chat",
                                                    authority="delegation" if is_delegation and delegated else "mention")
                if text:
                    self.ledger.push_inbox(item["id"], text, author=author, received_at=received_at)
                acknowledge("thought", self._opening(decision, text))
            elif decision.kind == "steer":
                self.ledger.push_inbox(active["id"], decision.text, author=author, received_at=received_at)
                acknowledge("thought", "已转给正在处理的 worker，会在下一次检查点读取。")
            elif decision.kind == "resume":
                # `active` is this session's work, or the job its conversation handed over to, which lives and asked
                # its question in another thread (Ledger.active_item_for_session).
                if active["skill"] == "chat":
                    # An answer on a card no longer delegated here goes on without the delegation (design P1, A2).
                    delivered = self.ledger.push_inbox(active["id"], decision.text, resume_waiting=True, author=author,
                                                       received_at=received_at, demote_to_mention=not delegated)
                    acknowledge("thought", "收到回复，继续处理。")
                else:
                    delivered = self.ledger.push_inbox(active["id"], decision.text, resume_waiting=delegated,
                                                       author=author, received_at=received_at)
                    acknowledge("thought", "收到回复，继续处理。" if delegated
                                else RESUME_UNDELEGATED.format(bot=self.bot_name))
                if active["state"] == "awaiting_input" and delivered["state"] == "queued":
                    self._note_resumed(delivered["item_id"], session_id)

        for attempt in range(REROUTES + 1):
            if routed[3]:
                pin = ""  # feature work gets no target line, however it came to be routed
            try:
                return act(*routed)
            except StaleRouting:
                # The work this event was routed to ended, moved or was claimed meanwhile (a worker's request for a
                # repair, a Stop, another event): route it again on what is true now (design J5).
                if attempt == REROUTES:
                    raise
                routed = routing()

    def _opening(self, decision, text):
        """The acknowledgement of new work: its job's, or a conversation's: the first message of a card without a Bot
        label, the router's explanation, or the plain one."""
        if decision.kind == "work":
            return ACK.get(decision.skill, ACK["chat"]).format(bot=self.bot_name)
        if decision.unlabelled:
            return NO_BOT_LABEL.format(bot=self.bot_name)
        if decision.text and decision.text != text:
            return decision.text
        return ACK["chat"].format(bot=self.bot_name)

    def _grace(self, skill):
        """The withdrawal grace of a claimed worker of `skill`, from the scheduler's loaded manifests (design P2)."""
        manifests = getattr(self.scheduler, "skills", None)
        return grace_seconds(manifests.get(skill) if isinstance(manifests, dict) else None)

    def _close_moved(self, item, body):
        """After the new session's acknowledgement: a superseded item's own session is told where its work went,
        `body`, SUPERSEDED for a new delegation's takeover or MOVED_THREAD for a conversation a message moved, or the
        card is, for an operator's `local-` item, which names no Linear session (design P4, P7). The note closes the
        old thread, so one Linear refuses is owed to it (silent-delegation design A4)."""
        try:
            if str(item["session_id"]).startswith("local-"):
                self.api.create_comment(item["issue_id"], body)
            else:
                self.api.create_activity(item["session_id"], {"type": "response", "body": body})
        except Exception as exc:
            self._owe(item["session_id"], "response", body, exc, job=item)

    def _owe(self, session_id, kind, body, exc, *, job=None, issue_id=None):
        """Linear refused the response or error that closes the thread `session_id`: it is owed to the thread, and the
        progress loop posts it again, unless `job`, the work it closes the thread for, or newer work in the thread
        speaks there first (silent-delegation design P10). Best effort, as the post was; an operator's `local-` job
        is owed nothing."""
        try:
            self.ledger.owe_closure(str(session_id), issue_id=job["issue_id"] if job else issue_id,
                                    item_id=job["id"] if job else None, kind=kind, body=body,
                                    error=type(exc).__name__)
        except Exception:
            pass

    def _answered(self, session_id):
        """Linear took the answer to an event of the thread `session_id`: its acknowledgement, a Stop's reply, or the
        error it failed with. The error still owed there for an older event would follow that answer, and ask for a
        message again that has gone through since, so it goes. The event need not have moved any job: a message
        steered into a running job, or forwarded from a thread that has no job of its own. What the thread is owed
        for work that ended in it stays (silent-delegation design P10). Best effort, as owing the error was."""
        try:
            self.ledger.drop_event_error(str(session_id))
        except Exception:
            pass

    def _note_resumed(self, item_id, session_id):
        """Best effort, after the acknowledgement in `session_id`: the parked job a message there resumed is told so
        in its own thread, whose last activity was its question, which nobody will answer there now. Nothing for a
        job of `session_id` itself, which the acknowledgement just told, or for an operator's `local-` job, which
        has no Linear thread (silent-delegation design A2, A3, P9).

        The note says the work goes on and will read that message first. The scheduler may launch the job during the
        acknowledgement's round trip, so the note is checked against the job as it stands afterwards: a job that has
        ended meanwhile, its thread closed by an error or a notice, or that has read the message and asked again,
        gets none."""
        try:
            item = self.ledger.item(item_id)
            own = str(item["session_id"])
            goes_on = item["state"] in ACTIVE_STATES and item["state"] != "awaiting_input"
            if goes_on and own != session_id and not own.startswith("local-"):
                self.api.create_activity(own, {"type": "thought", "body": RESUMED_ELSEWHERE})
        except Exception:
            pass

    def _release_deferred(self):
        """A delegation deferred behind another session's claimed worker goes back to the queue once that worker no
        longer runs, or once its deadline is DEFER_SLACK past and it still does (design C2). Its payload then says it
        was deferred, and a fresh activity id lets it answer in its session again: the first carried DEFER_ACK."""
        with self.lock:
            rows = self.db.execute("SELECT event_key,payload,error FROM webhook_events WHERE status='deferred' "
                                   "ORDER BY received_at").fetchall()
        now = self.clock()
        for row in rows:
            try:
                item = self.ledger.item(row["error"])
            except LedgerError:
                item = None
            if (item is not None and item["state"] == "running" and item["withdraw_deadline"] is not None
                    and now < item["withdraw_deadline"] + DEFER_SLACK):
                continue
            payload = {**json.loads(row["payload"]), "deferred": True}
            with self.lock, self.db:
                self.db.execute("UPDATE webhook_events SET status='pending',ack_id=?,payload=?,error=NULL "
                                "WHERE event_key=? AND status='deferred'",
                                (str(uuid.uuid4()), json.dumps(payload), row["event_key"]))

    def _settle_delegation(self):
        """Settle one delegation Linear opened no session for, once its grace has ended: the waiting episode longest
        due (silent-delegation design P11, P12, §3.4). It runs in the receiver's loop, the one thread that handles
        `created`, so a settle never interleaves with a new session's takeover, and only when no event is pending.

        A settle that found the work or the episode changed under it looks again shortly. One whose read failed has
        posted nothing; it is tried again later, and the failure is counted and named for doctor, unless the card
        is out of reach, which ends the episode (`unreachable`). Each settle writes one line to the service log: the
        outcome and what Linear said of each thread it read, never a body. Returns whether an episode was due."""
        episode = self.ledger.due_episode(self.clock())
        if episode is None:
            return False
        reads = {}
        try:
            outcome = self._settle(episode, reads)
        except StaleRouting:
            self.ledger.retry_episode(episode["issue_id"], episode["since"], self.clock() + RESETTLE_SECONDS)
            outcome = "changed"
        except Exception as exc:
            gone = isinstance(exc, LinearError) and exc.kind == "not_found"
            delay = min(SETTLE_RETRY_MAX, SETTLE_RETRY_SECONDS * 2 ** min(episode["attempts"], 8))
            self.ledger.retry_episode(episode["issue_id"], episode["since"], self.clock() + delay,
                                      error=type(exc).__name__, unreachable=gone)
            outcome = "unreachable" if gone and self._out_of_reach(episode) else "retry"
        threads = [{"session_id": session_id, "status": state and state["status"],
                    "archived": state and state["archived"]} for session_id, state in reads.items()]
        print(json.dumps({"event": "delegation_episode", "issue_id": episode["issue_id"], "outcome": outcome,
                          "threads": threads}), flush=True)
        return True

    def _out_of_reach(self, episode):
        """Whether `episode`, whose read Linear just answered with "not found", was dropped because its card is out
        of reach, by the rule that withdraws such a card's work (withdrawn-work design R8, Lifecycle._unreachable):
        enough failed reads, "not found" for long enough, and not explained by an outage of Linear itself. The card's
        sessions went with it, so nothing is posted; without this the settle would read a card that is gone for as
        long as the ledger lives, and doctor would list it for as long. Only the episode this settle read is dropped:
        one a read ended or replaced meanwhile is left as it is."""
        current = self.ledger.episode(episode["issue_id"])
        first = current["unreachable_since"]
        return (first is not None and current["attempts"] >= UNREACHABLE_READS
                and self.clock() - first >= UNREACHABLE_SECONDS
                and (getattr(self.api, "last_success_at", None) or 0) > first
                and self.ledger.drop_episode(episode["issue_id"], episode["since"]))

    def _settle(self, episode, reads):
        """Settle `episode` where the card's work is, on a fresh read of the card (silent-delegation design P12, §3.5),
        and return what became of it: `dropped`, the card is closed or no longer delegated to this app; `heard`, Linear
        did open a session; `kept`, `in_place`, `told` or `unseen`, the first of P12's outcomes that applies; or
        `changed`, another read ended the episode meanwhile. `reads` collects what Linear said of each thread.

        FarmBot opens no session and changes nothing on the card (P6). Nothing is posted before the episode is ended
        as the one this settle read, so a settle acts once; StaleRouting from the takeover means the job or the
        episode changed under it. A failed read of the card raises, and nothing was posted (P8)."""
        issue_id, since = episode["issue_id"], episode["since"]
        fetched_at = self.clock()
        issue = self.api.fetch_issue(issue_id)
        if issue.get("id") != issue_id:
            raise ValueError("the read of the card answers for a different issue")
        self.ledger.observe_issue(issue)
        app = self.identity["appUserId"]
        delegated = bool(app) and issue.get("delegate_id") == app
        # This read is a status read too, as each of the receiver's is (withdrawn-work design DT1): finding the
        # delegation clears the mark, which opens no second episode while this one waits; not finding it while the
        # delegation's work is active asks the lifecycle to read the card now.
        if delegated:
            self.ledger.clear_undelegated(issue_id, observed_at=fetched_at)
        active = self.ledger.active_item_for_issue(issue_id)
        if not delegated and active is not None and active["authority"] == "delegation":
            self.ledger.request_status_check(issue_id)
        if not delegated or issue["archived"] or issue["status_type"] in TERMINAL_STATUS_TYPES:
            return "dropped" if self.ledger.finish_episode(issue_id, since, "dropped") else "changed"
        if self.ledger.delegation_heard(issue_id, episode["mark"]):
            return "heard" if self.ledger.finish_episode(issue_id, since, "heard") else "changed"
        # The work a delegation of this card starts now, as a `created` would route it.
        decision = route(action="created", is_delegation=True, text="", labels=issue["labels"], active_state=None,
                         terminal_exists=False, available_skills=self.skills,
                         label_groups=issue.get("label_groups") or ())
        skill = decision.skill if decision.kind == "work" else "chat"
        if active is not None and active["authority"] == "delegation" and active["skill"] == skill:
            return self._keep(episode, active)
        thread = str(active["session_id"]) if active is not None else None
        if thread is not None and not thread.startswith("local-"):
            # In place: P4 with the new session equal to the job's own. Only unclaimed work or a conversation, as a
            # new session takes over at once; only a job older than the read that found the delegation (P3); only in
            # a delegation thread, which alone may hold the delegation's work (P1); and only in a thread Linear says
            # is not archived, so that the work never moves where nobody sees it, nor on a state nobody read (P8).
            conversation = active["skill"] == "chat"
            session = self.ledger.session(thread)
            if ((conversation or active["state"] in UNCLAIMED) and active["created_at"] < since
                    and session is not None and session["delegation"]):
                state = self._thread_state(reads, thread, issue_id)
                if state is not None and not state["archived"]:
                    # The thread's stored target, as a conversation's first repair takes it: nothing is pinned anew.
                    self.ledger.supersede(active["id"], ACTIVE_STATES if conversation else UNCLAIMED,
                                          session_id=thread, skill=skill, reason=IN_PLACE_REASON,
                                          target=None if skill in ("chat", "feature") else session["target"],
                                          authority="delegation", episode=since)
                    self._say(thread, {"type": "thought", "body": IN_PLACE_NOTE.format(bot=self.bot_name) + "\n"
                                       + self._opening(decision, "")})
                    return "in_place"
        return self._tell(episode, active, reads)

    def _thread_state(self, reads, session_id, issue_id):
        """What Linear says of the thread `session_id`, {"status", "archived"}, or None when it is not known. One
        settle asks once per thread and for SESSION_READS threads at most, and its first refused read ends its reads:
        every thread after that is unknown, and an unknown thread is never closed nor taken in place (design §3.6,
        P8). `reads` keeps this settle's answers, None for the refused one."""
        if session_id not in reads:
            if None in reads.values() or len(reads) >= SESSION_READS:
                return None
            try:
                reads[session_id] = self.api.session_state(session_id, issue_id, self.identity["appUserId"])
            except Exception:
                reads[session_id] = None
        return reads[session_id]

    def _say(self, session_id, content):
        """Post `content` in the thread `session_id`, best effort: the refusal, or None when Linear took it."""
        try:
            self.api.create_activity(session_id, content)
        except Exception as exc:
            return exc
        return None

    def _line(self, job, content, outcome):
        """Post the line of a settle that ends nothing, a thought or a question asked again, in the thread of `job`,
        best effort, and record it in the job's audit trail. Not for an operator's `local-` job, which has no Linear
        thread; not for work that is being withdrawn, of which "it goes on" would be false; not for a job that has
        left the state the line was written for, which has spoken in its thread since (design P9); and at most once
        per job in RENOTE_SECONDS, so that an automation that flaps the delegate costs one line (§3.3). Returns
        whether Linear took it."""
        thread = str(job["session_id"])
        if thread.startswith("local-") or job["withdraw_deadline"] is not None:
            return False
        if self.ledger.item(job["id"])["state"] != job["state"]:
            return False
        if self.ledger.noted_since(job["id"], "redelegated", self.clock() - RENOTE_SECONDS):
            return False
        if self._say(thread, content) is not None:
            return False
        self.ledger.note(job["id"], "redelegated", outcome, {"type": content["type"]})
        return True

    def _keep(self, episode, active):
        """Kept (design P12, D2): `active` is delegation work of the kind the labels route to, so nothing moves. Its
        thread gets one line: a waiting job asks its question again, which keeps a waiting thread over waiting work,
        and any other says that the work goes on."""
        thread = str(active["session_id"])
        if not self.ledger.finish_episode(episode["issue_id"], episode["since"], "served", thread):
            return "changed"
        try:
            if active["state"] != "awaiting_input":
                self._line(active, {"type": "thought", "body": REDELEGATED_RUNNING.format(bot=self.bot_name)}, "kept")
            elif self._line(active, {"type": "elicitation", "body": REDELEGATED_WAITING.format(
                    bot=self.bot_name, question=active["checkpoint"].get("pending_question") or "")}, "kept"):
                # The job may have ended while Linear took the question: a closure, an operator's cancel. Its thread
                # would keep a question nobody reads an answer to, so the question is withdrawn, as `await-input`
                # withdraws one (design A6, P9).
                ended = self.ledger.item(active["id"])
                if ended["state"] not in ACTIVE_STATES:
                    body = question_withdrawn(ended["skill"])
                    refused = self._say(thread, {"type": "response", "body": body})
                    if refused is not None:
                        self._owe(thread, "response", body, refused, job=ended)
        except Exception:
            pass  # the line is best effort, and the episode is settled
        return "kept"

    def _event_waits(self, session_id):
        """Whether an event of the thread `session_id` is still to be handled: pending, or deferred behind another
        session's worker."""
        with self.lock:
            return self.db.execute("SELECT 1 FROM webhook_events WHERE session_id=? "
                                   "AND status IN ('pending','deferred') LIMIT 1", (session_id,)).fetchone() is not None

    def _reaches_work(self, session_id):
        """Whether the thread `session_id` has active work a Stop or a reply there reaches as its own: a job that
        lives in it, or the job its conversation handed over to, which lives in another thread."""
        return self.ledger.active_item_for_session(session_id) is not None

    def _tell(self, episode, active, reads):
        """Told, or unseen (design P12, D3, §3.5): the delegation can neither be kept nor taken over in place, so each
        of FarmBot's open threads on the card gets one note, and with no such thread nothing is posted and doctor
        lists the episode. The threads are that of `active`, the card's active job, and the card's newest ones; an
        operator's `local-` session is no thread.

        - The thread of a waiting job gets a response, which ends the wait that kept Linear from opening a session;
          the job stays parked and answerable. Not when Linear says the thread is closed or archived: it then blocks
          nothing, or nobody sees it.
        - The thread of a job that goes on gets a thought, unless it is archived.
        - A thread with no job of its own that Linear shows as open is closed by a response. Not while its messages
          are forwarded to the active job, nor while its conversation has handed over to a job that is still active,
          nor while an event of it is still to be handled: a Stop there must keep reaching the work (P5). Not when
          its state could not be read (P8).
        A refused response is owed to its thread (P10)."""
        issue_id = episode["issue_id"]
        own = str(active["session_id"]) if active is not None else None
        notes = []
        if own is not None and not own.startswith("local-") and active["withdraw_deadline"] is None:
            state = self._thread_state(reads, own, issue_id)
            if active["state"] == "awaiting_input":
                if state is None or not (state["archived"] or state["status"] in CLOSED_STATUSES):
                    text = SILENT_WAITING if active["skill"] in WRITE_SKILLS else SILENT_WAITING_CHAT
                    notes.append((own, "response", text.format(bot=self.bot_name)))
            elif state is None or not state["archived"]:
                notes.append((own, "thought", SILENT_BUSY.format(bot=self.bot_name)))
        for row in self.ledger.sessions_for_issue(issue_id, SESSION_READS):
            thread = row["session_id"]
            if (self._reaches_work(thread) or (active is not None and row["forwarded_item"] == active["id"])
                    or self._event_waits(thread)):
                continue  # the job's own thread, seen above, or a thread that still reaches the work or has an event
            state = self._thread_state(reads, thread, issue_id)
            if state is not None and not state["archived"] and state["status"] not in CLOSED_STATUSES:
                notes.append((thread, "response", SILENT_ENDED.format(bot=self.bot_name)))
        outcome = "told" if notes else "unseen"
        if not self.ledger.finish_episode(issue_id, episode["since"], outcome):
            return "changed"
        for thread, kind, body in notes:
            try:
                self._note(thread, kind, body, active if thread == own else None)
            except Exception:
                pass  # each note is best effort, and the episode is settled
        return outcome

    def _note(self, thread, kind, body, job):
        """Post one note of a told episode in `thread`: for `job`, the card's active job, in its own thread, or for
        no job in a thread that had none. The note is for the thread as the settle found it (design P9): a job that
        has left its state since has spoken there itself, and a thread that has work by now is not told it has
        none."""
        if kind == "thought":
            self._line(job, {"type": kind, "body": body}, "told")
            return
        if job is not None:
            if self.ledger.item(job["id"])["state"] != job["state"]:
                return
        elif self._reaches_work(thread):
            return
        refused = self._say(thread, {"type": kind, "body": body})
        if refused is not None:
            self._owe(thread, kind, body, refused, job=job)  # a thread with no job is owed for its card
        if job is not None:
            self.ledger.note(job["id"], "redelegated", "told", {"type": kind})

    def process_one(self):
        """One step of the receiver's loop: a Stop, else one pending event, else, with no event pending, one
        delegation whose grace has ended with no session (silent-delegation design §3.4). Returns whether it did
        any of them."""
        if self._process_stop():
            return True
        self._release_deferred()
        with self.lock:
            row = self.db.execute("SELECT * FROM webhook_events WHERE status='pending' ORDER BY received_at LIMIT 1").fetchone()
            if row is not None:
                self.db.execute("UPDATE webhook_events SET status='processing' WHERE event_key=?", (row["event_key"],))
                self.db.commit()
        if row is None:
            # Outside the lock: a settle reads Linear, and `receive` needs the lock to accept a webhook meanwhile.
            return self._settle_delegation()
        status, error = "done", None
        answered = True  # Linear took this event's answer in its thread: an acknowledgement, or the event's error
        try:
            self._decide_and_act(json.loads(row["payload"]), row["ack_id"], row["received_at"])
        except Deferred as deferred:
            # Acknowledged already. The event keeps its payload and waits for the old worker (design C2).
            status, error = "deferred", deferred.item_id
        except (LedgerError, RuntimeError, ValueError, KeyError, OSError, sqlite3.Error) as exc:
            status, error = "uncertain", type(exc).__name__
            body = f"{self.bot_name} 处理这条消息时出错（{type(exc).__name__}），请稍后重试或联系维护者。"
            try:
                self._send(row["session_id"], row["ack_id"], {"type": "error", "body": body})
            except Exception as refused:
                # Owed to the thread, for its job as it stands: a later message that moves the job on is acknowledged
                # there, and this older error must not follow that; nor the answer to any later event of the thread
                # (_answered), which may move no job at all (silent-delegation design A4, P10).
                answered = False
                try:
                    job = self.ledger.active_item_for_session(row["session_id"])
                    issue_id = json.loads(row["payload"]).get("issue_id")
                except Exception:
                    job = issue_id = None
                self._owe(row["session_id"], "error", body, refused, job=job, issue_id=issue_id)
        if answered:
            self._answered(row["session_id"])
        with self.lock, self.db:
            if status == "deferred":
                self.db.execute("UPDATE webhook_events SET status=?,error=? WHERE event_key=?",
                                (status, error, row["event_key"]))
            else:
                self.db.execute("UPDATE webhook_events SET status=?,completed_at=?,error=?,payload=NULL WHERE event_key=?",
                                (status, self.clock(), error, row["event_key"]))
        return True

    def results(self):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT event_key,session_id,status,received_at,completed_at,error FROM webhook_events ORDER BY received_at")]


class ExclusiveServer(ThreadingHTTPServer):
    """No port sharing on Windows, no reverse-DNS lookup at bind, and no traceback for a client that went away;
    the receiver and the status monitor use it.

    HTTPServer adds a reverse-DNS lookup after binding; a slow host resolver must not gate startup."""
    allow_reuse_address = os.name != "nt"
    daemon_threads = True

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address

    def handle_error(self, request, client_address):
        # A client that resets or closes its connection before or during the reply: a poll the status page aborted,
        # a phone that left the Wi-Fi, or a monitor probe that gave up while serve was bound but not yet accepting,
        # answered once serve_forever starts. That is no fault of the server, and socketserver would print a
        # traceback for each such client, to a log launchd never rotates. Any other error keeps that traceback.
        if isinstance(sys.exc_info()[1], ConnectionError):
            return
        super().handle_error(request, client_address)


def _person(value):
    """A webhook user as the shared person shape, or None: anything but an object names nobody."""
    return person(value) if isinstance(value, dict) else None


def _plain_word(value):
    """value if it is a plain word of 1 to 40 ASCII letters, else None. The delivery log line takes type and action
    from a body that may be unsigned, and logging any other value would let whoever reaches the endpoint add
    megabytes to the service log with each request."""
    return value if isinstance(value, str) and _PLAIN_WORD.fullmatch(value) else None


def make_server(receiver, port=8765):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def respond(self, status, message):
            data = json.dumps({"status": message}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def answer_webhook(self, kind, status, message):
            heartbeat = getattr(self.server, "heartbeat", None)
            if heartbeat is not None:
                try:
                    heartbeat.webhook(kind, status, message)
                except Exception:
                    pass  # counting feeds the status page and must never change a webhook's answer
            self.respond(status, message)

        def do_GET(self):
            self.respond(200, "FarmBot ready") if self.path == "/health" else self.respond(404, "not found")

        def do_POST(self):
            if self.path != "/webhook":
                return self.respond(404, "not found")
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return self.answer_webhook(None, 400, "invalid length")
            if length <= 0 or length > MAX_BODY:
                return self.answer_webhook(None, 413, "invalid body size")
            self.connection.settimeout(3)
            try:
                raw = self.rfile.read(length)
                if len(raw) != length:
                    return self.answer_webhook(None, 400, "incomplete body")
                status, message = self.server.receiver.receive(raw, self.headers.get("Linear-Signature"))
            except TimeoutError:
                return self.answer_webhook(None, 408, "body timeout")
            except Exception:
                return self.answer_webhook(None, 500, "receiver error")
            # One line per delivery so an ignored or rejected event is visible in the service log; never the
            # body, and its type and action only as plain words, since unsigned deliveries are logged too.
            # Written before the response, so a caller holding its reply knows the line exists.
            try:
                event = json.loads(raw)
                kind = _plain_word(event.get("type")) if isinstance(event, dict) else None
                action = _plain_word(event.get("action")) if isinstance(event, dict) else None
            except (ValueError, UnicodeError, RecursionError):  # also an unsigned body nested too deeply to parse
                kind = action = None
            print(json.dumps({"event": "webhook", "status": status, "result": message, "type": kind, "action": action}), flush=True)
            self.answer_webhook(kind, status, message)

    server = ExclusiveServer(("127.0.0.1", port), Handler)
    server.receiver = receiver
    server.heartbeat = None  # a serving process installs its Heartbeat here (service._serve)
    return server
