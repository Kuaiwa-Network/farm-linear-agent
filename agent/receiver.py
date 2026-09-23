"""Verified Linear agent-session webhooks -> ledger work items (spec §3, §4, §15)."""
from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
import os
import socket
from socketserver import TCPServer
import sqlite3
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .ledger import LedgerError
from .router import WRITE_SKILLS, route
from .worktrees import WorktreeError

MAX_BODY = 1024 * 1024
TARGET_REPO = "Farm-Client"
PIN_TIMEOUT = 8
# {bot} is the instance's configured Linear app name: a development instance shares the workspace with
# production, and an acknowledgement in the wrong name gets the wrong bot stopped.
ACK = {"fix": "{bot} 已收到委派，正在排队处理这个缺陷。进展和草稿 PR 会更新在这里。",
       "chat": "{bot} 已收到，正在查看。", "qa": "{bot} 已收到测试请求，正在排队。"}


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
        except (ValueError, UnicodeError):
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
        if event["action"] == "prompted":
            text = event["agentActivity"]["content"].get("body") or ""
        else:
            text = (session.get("sourceComment") or session.get("comment") or {}).get("body") or ""
        if not isinstance(text, str) or len(text) > 32000:
            raise ValueError("oversized prompt")
        guidance = event.get("guidance")
        guidance = guidance if isinstance(guidance, str) else json.dumps(guidance, ensure_ascii=False) if guidance else ""
        if len(guidance) > 32000:
            raise ValueError("oversized guidance")
        return {"action": event["action"], "session_id": session["id"], "issue_id": issue_id, "text": text,
                "is_mention": bool(session.get("comment") or session.get("commentId")
                                   or session.get("sourceComment") or session.get("sourceCommentId")),
                "guidance": guidance}

    def _receive_stop(self, event):
        session_id = event["agentSession"]["id"]
        stop_key = f"{self.identity['organizationId']}:stop:{event['agentActivity']['id']}"
        with self.lock, self.db:
            inserted = self.db.execute("INSERT OR IGNORE INTO stop_requests VALUES (?,?,?,'pending',?,NULL,NULL)",
                                       (stop_key, session_id, str(uuid.uuid4()), self.clock())).rowcount
            if inserted:
                self.db.execute("UPDATE webhook_events SET status='cancelled',completed_at=? WHERE session_id=? AND status='pending'",
                                (self.clock(), session_id))
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
            item = self.ledger.active_item_for_session(row["session_id"])
            if item is not None:
                self.scheduler.stop(item["id"], "Linear stop")
                body = f"已停止 {item['identifier']} 上的工作，worker 已终止，占用的资源在静默检查后释放。"
            else:
                body = "当前没有正在进行的工作可停止。"
            self._send(row["session_id"], row["activity_id"], {"type": "response", "body": body})
        except Exception as exc:
            status, error = "uncertain", type(exc).__name__
        with self.lock, self.db:
            self.db.execute("UPDATE stop_requests SET status=?,completed_at=?,error=? WHERE stop_key=?",
                            (status, self.clock(), error, row["stop_key"]))
        return True

    def _decide_and_act(self, prepared, ack_id):
        issue = self.api.fetch_issue(prepared["issue_id"])
        self.ledger.observe_issue(issue)
        session = self.ledger.session(prepared["session_id"])
        if (session is None and prepared["action"] == "created" and prepared.get("is_mention")
                and issue.get("delegate_id") == self.identity["appUserId"]):
            # Delegations may include Linear's synthetic thread comment. Verify
            # its origin off the webhook ACK path before granting write authority.
            if self.api.session_has_artificial_root(prepared["session_id"], issue["id"], self.identity["appUserId"]):
                prepared = {**prepared, "is_mention": False, "text": ""}
        is_delegation = (session["delegation"] if session else
                         prepared["action"] == "created" and not prepared.get("is_mention")
                         and issue.get("delegate_id") == self.identity["appUserId"])
        session = self.ledger.ensure_session(prepared["session_id"], issue["id"], is_delegation, prepared["guidance"])
        pin = ""
        if session.get("target") is None and self.worktrees is not None:
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
        active = self.ledger.active_item_for_session(prepared["session_id"])
        history = self.ledger.items_for_session(prepared["session_id"])
        decision = route(action=prepared["action"], is_delegation=is_delegation, text=prepared["text"], labels=issue["labels"],
                         active_state=active["state"] if active else None, terminal_exists=bool(history) and active is None,
                         available_skills=self.skills, bot_name=self.bot_name)
        session_id = prepared["session_id"]

        def acknowledge(kind, body):
            """One event, one activity — and the pin rides in whichever branch sends it. Echoing only from the
            work and chat branches would let a session that first elicits or steers store its pin in silence
            and never announce it, because every later event sees a target that is no longer None (spec §6)."""
            if kind == "elicitation":
                self.api.needs_more_info(issue["id"])
            self._send(session_id, ack_id, {"type": kind, "body": body + pin})

        elsewhere = self.ledger.active_item_for_issue(issue["id"])
        if elsewhere is not None and elsewhere["session_id"] != session_id:
            if decision.kind == "work":
                acknowledge("response", f"{issue['identifier']} 已有进行中的工作（{elsewhere['skill']}），"
                                        "请在原会话继续，或等它完成后再委派。")
                return
            if decision.kind == "chat":
                can_resume = elsewhere["skill"] == "chat" or issue.get("delegate_id") == self.identity["appUserId"]
                delivered = self.ledger.push_inbox(elsewhere["id"], prepared["text"] or "（无正文）", resume_waiting=can_resume)
                notice = "该 issue 正在处理中，你的消息已转给正在处理的 worker。"
                if elsewhere["state"] == "awaiting_input" and delivered["state"] == "queued":
                    notice = "收到回复，原工作项已恢复，worker 会先读取你的回答。"
                if decision.text and decision.text != prepared["text"]:
                    notice = decision.text + "\n" + notice
                acknowledge("thought", notice)
                return
        if decision.kind == "work":
            if decision.skill in WRITE_SKILLS and not is_delegation:
                raise RuntimeError("router produced write work from a mention")
            item = self.ledger.create_work_item(issue_id=issue["id"], session_id=session_id, skill=decision.skill,
                                                target=(session or {}).get("target"))
            if prepared["text"]:
                self.ledger.push_inbox(item["id"], prepared["text"])
            acknowledge("thought", ACK.get(decision.skill, ACK["chat"]).format(bot=self.bot_name))
        elif decision.kind == "chat":
            item = self.ledger.create_work_item(issue_id=issue["id"], session_id=session_id, skill="chat")
            self.ledger.push_inbox(item["id"], prepared["text"] or "（无正文）")
            body = (decision.text if decision.text and decision.text != prepared["text"]
                    else ACK["chat"].format(bot=self.bot_name))
            acknowledge("thought", body)
        elif decision.kind == "steer":
            self.ledger.push_inbox(active["id"], decision.text)
            acknowledge("thought", "已转给正在处理的 worker，会在下一次检查点读取。")
        elif decision.kind == "resume":
            can_resume = active["skill"] == "chat" or issue.get("delegate_id") == self.identity["appUserId"]
            self.ledger.push_inbox(active["id"], decision.text, resume_waiting=can_resume)
            acknowledge("thought", "收到回复，继续处理。" if can_resume
                        else f"已保存回复；issue 已不再委派给 {self.bot_name}，暂不继续修复。")
        elif decision.kind == "elicit":
            acknowledge("elicitation", decision.text)

    def process_one(self):
        if self._process_stop():
            return True
        with self.lock:
            row = self.db.execute("SELECT * FROM webhook_events WHERE status='pending' ORDER BY received_at LIMIT 1").fetchone()
            if row is None:
                return False
            self.db.execute("UPDATE webhook_events SET status='processing' WHERE event_key=?", (row["event_key"],))
            self.db.commit()
        status, error = "done", None
        try:
            self._decide_and_act(json.loads(row["payload"]), row["ack_id"])
        except (LedgerError, RuntimeError, ValueError, KeyError, OSError, sqlite3.Error) as exc:
            status, error = "uncertain", type(exc).__name__
            try:
                self._send(row["session_id"], row["ack_id"], {"type": "error", "body": f"{self.bot_name} 处理这条消息时出错（{type(exc).__name__}），请稍后重试或联系维护者。"})
            except Exception:
                pass
        with self.lock, self.db:
            self.db.execute("UPDATE webhook_events SET status=?,completed_at=?,error=?,payload=NULL WHERE event_key=?",
                            (status, self.clock(), error, row["event_key"]))
        return True

    def results(self):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT event_key,session_id,status,received_at,completed_at,error FROM webhook_events ORDER BY received_at")]


def make_server(receiver, port=8765):
    class ExclusiveServer(ThreadingHTTPServer):
        allow_reuse_address = os.name != "nt"

        def server_bind(self):
            if os.name == "nt":
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            # HTTPServer adds a reverse-DNS lookup after binding. This listener is
            # explicitly loopback-only; a slow host resolver must not gate startup.
            TCPServer.server_bind(self)
            self.server_name, self.server_port = self.server_address

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

        def do_GET(self):
            self.respond(200, "FarmBot ready") if self.path == "/health" else self.respond(404, "not found")

        def do_POST(self):
            if self.path != "/webhook":
                return self.respond(404, "not found")
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return self.respond(400, "invalid length")
            if length <= 0 or length > MAX_BODY:
                return self.respond(413, "invalid body size")
            self.connection.settimeout(3)
            try:
                raw = self.rfile.read(length)
                if len(raw) != length:
                    return self.respond(400, "incomplete body")
                status, message = self.server.receiver.receive(raw, self.headers.get("Linear-Signature"))
            except TimeoutError:
                return self.respond(408, "body timeout")
            except Exception:
                return self.respond(500, "receiver error")
            # One line per delivery so an ignored or rejected event is visible in the service log; never the
            # body. Written before the response, so a caller holding its reply knows the line exists.
            try:
                event = json.loads(raw)
                kind = event.get("type") if isinstance(event, dict) else None
                action = event.get("action") if isinstance(event, dict) else None
            except (ValueError, UnicodeError):
                kind = action = None
            print(json.dumps({"event": "webhook", "status": status, "result": message, "type": kind, "action": action}), flush=True)
            self.respond(status, message)

    server = ExclusiveServer(("127.0.0.1", port), Handler)
    server.receiver = receiver
    server.daemon_threads = True
    return server
