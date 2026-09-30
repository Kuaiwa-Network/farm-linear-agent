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

from .ledger import ACTIVE_STATES, LedgerError, StaleRouting
from .linear_api import person
from .router import WRITE_SKILLS, route
from .withdrawal import (DEFER_ACK, DEFER_STILL, DEFER_UNDELEGATED, FORWARD_PARKED_UNDELEGATED, FORWARD_WITHDRAWING,
                         MOVED_THREAD, RESUME_UNDELEGATED, RESUMED_ELSEWHERE, STOP_ALREADY, STOP_ELSEWHERE, STOP_MOVED,
                         STOP_MOVED_THREAD, SUPERSEDE_SUFFIX, SUPERSEDED, grace_seconds, notice as withdrawal_notice)
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
            else:
                body = {"moved": STOP_MOVED, "moved_thread": STOP_MOVED_THREAD,
                        "stopped": STOP_ALREADY}.get(where, "当前没有正在进行的工作可停止。")
            self._send(row["session_id"], row["activity_id"], {"type": "response", "body": body})
        except Exception as exc:
            status, error = "uncertain", type(exc).__name__
        with self.lock, self.db:
            self.db.execute("UPDATE stop_requests SET status=?,completed_at=?,error=? WHERE stop_key=?",
                            (status, self.clock(), error, row["stop_key"]))
        return True

    def _decide_and_act(self, prepared, ack_id, received_at=None):
        """received_at: when this event reached FarmBot. A pending event may be processed much later (it survives a
        restart), and the messages it adds keep that time, which dates a ruling given in one of them."""
        issue = self.api.fetch_issue(prepared["issue_id"])
        self.ledger.observe_issue(issue)
        app = self.identity["appUserId"]
        delegated = bool(app) and issue.get("delegate_id") == app
        # This fresh read of the card is a status read too (withdrawn-work design DT1, P3). Finding the delegation
        # clears the issue's undelegated mark and the flags its loss set; not finding it while the delegation's work
        # is active asks the lifecycle to read the card now, not at its next interval.
        if delegated:
            self.ledger.clear_undelegated(issue["id"])
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
        """Best effort, after the new session's acknowledgement: a superseded item's own session is told where its
        work went, `body`, SUPERSEDED for a new delegation's takeover or MOVED_THREAD for a conversation a message
        moved, or the card is, for an operator's `local-` item, which names no Linear session (design P4, P7)."""
        try:
            if str(item["session_id"]).startswith("local-"):
                self.api.create_comment(item["issue_id"], body)
            else:
                self.api.create_activity(item["session_id"], {"type": "response", "body": body})
        except Exception:
            pass

    def _note_resumed(self, item_id, session_id):
        """Best effort, after the acknowledgement in `session_id`: the parked job a message there resumed is told so
        in its own thread, whose last activity was its question, which nobody will answer there now. Nothing for a
        job of `session_id` itself, which the acknowledgement just told, or for an operator's `local-` job, which
        has no Linear thread (silent-delegation design A2, A3, P9)."""
        try:
            own = str(self.ledger.item(item_id)["session_id"])
            if own != session_id and not own.startswith("local-"):
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

    def process_one(self):
        if self._process_stop():
            return True
        self._release_deferred()
        with self.lock:
            row = self.db.execute("SELECT * FROM webhook_events WHERE status='pending' ORDER BY received_at LIMIT 1").fetchone()
            if row is None:
                return False
            self.db.execute("UPDATE webhook_events SET status='processing' WHERE event_key=?", (row["event_key"],))
            self.db.commit()
        status, error = "done", None
        try:
            self._decide_and_act(json.loads(row["payload"]), row["ack_id"], row["received_at"])
        except Deferred as deferred:
            # Acknowledged already. The event keeps its payload and waits for the old worker (design C2).
            status, error = "deferred", deferred.item_id
        except (LedgerError, RuntimeError, ValueError, KeyError, OSError, sqlite3.Error) as exc:
            status, error = "uncertain", type(exc).__name__
            try:
                self._send(row["session_id"], row["ack_id"], {"type": "error", "body": f"{self.bot_name} 处理这条消息时出错（{type(exc).__name__}），请稍后重试或联系维护者。"})
            except Exception:
                pass
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
