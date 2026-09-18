"""Verified Linear agent-session webhooks -> ledger work items (spec §3, §4, §15)."""
from datetime import datetime
import hashlib
import hmac
import json
import math
import os
import socket
import sqlite3
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .ledger import LedgerError
from .router import WRITE_SKILLS, route

MAX_BODY = 1024 * 1024
ACK = {"fix": "FarmBot 已收到委派，正在排队处理这个缺陷。进展和草稿 PR 会更新在这里。",
       "chat": "FarmBot 已收到，正在查看。", "qa": "FarmBot 已收到测试请求，正在排队。"}


class Receiver:
    def __init__(self, db_path, secret, identity, api, ledger_factory, skills, scheduler, clock=time.time):
        self.secret = secret.encode()
        self.identity = identity
        self.api = api
        self.ledger = ledger_factory()
        self.skills = set(skills)
        self.scheduler = scheduler
        self.clock = clock
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
            text = (session.get("comment") or {}).get("body") or ""
        if not isinstance(text, str) or len(text) > 32000:
            raise ValueError("oversized prompt")
        guidance = event.get("guidance")
        guidance = guidance if isinstance(guidance, str) else json.dumps(guidance, ensure_ascii=False) if guidance else ""
        if len(guidance) > 32000:
            raise ValueError("oversized guidance")
        return {"action": event["action"], "session_id": session["id"], "issue_id": issue_id, "text": text,
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
        is_delegation = (session["delegation"] if session else
                         prepared["action"] == "created" and issue.get("delegate_id") == self.identity["appUserId"])
        self.ledger.ensure_session(prepared["session_id"], issue["id"], is_delegation, prepared["guidance"])
        active = self.ledger.active_item_for_session(prepared["session_id"])
        history = self.ledger.items_for_session(prepared["session_id"])
        decision = route(action=prepared["action"], is_delegation=is_delegation, text=prepared["text"], labels=issue["labels"],
                         active_state=active["state"] if active else None, terminal_exists=bool(history) and active is None,
                         available_skills=self.skills)
        session_id = prepared["session_id"]
        elsewhere = self.ledger.active_item_for_issue(issue["id"])
        if elsewhere is not None and elsewhere["session_id"] != session_id:
            if decision.kind == "work":
                self._send(session_id, ack_id, {"type": "response", "body":
                    f"{issue['identifier']} 已有进行中的工作（{elsewhere['skill']}），请在原会话继续，或等它完成后再委派。"})
                return
            if decision.kind == "chat":
                self.ledger.push_inbox(elsewhere["id"], prepared["text"] or "（无正文）")
                notice = "该 issue 正在处理中，你的消息已转给正在处理的 worker。"
                if decision.text and decision.text != prepared["text"]:
                    notice = decision.text + "\n" + notice
                self._send(session_id, ack_id, {"type": "thought", "body": notice})
                return
        if decision.kind == "work":
            if decision.skill in WRITE_SKILLS and not is_delegation:
                raise RuntimeError("router produced write work from a mention")
            item = self.ledger.create_work_item(issue_id=issue["id"], session_id=session_id, skill=decision.skill,
                                                target=(session or {}).get("target"))
            if prepared["text"]:
                self.ledger.push_inbox(item["id"], prepared["text"])
            self._send(session_id, ack_id, {"type": "thought", "body": ACK.get(decision.skill, ACK["chat"])})
        elif decision.kind == "chat":
            item = self.ledger.create_work_item(issue_id=issue["id"], session_id=session_id, skill="chat")
            self.ledger.push_inbox(item["id"], prepared["text"] or "（无正文）")
            body = decision.text if decision.text and decision.text != prepared["text"] else ACK["chat"]
            self._send(session_id, ack_id, {"type": "thought", "body": body})
        elif decision.kind == "steer":
            self.ledger.push_inbox(active["id"], decision.text)
            self._send(session_id, ack_id, {"type": "thought", "body": "已转给正在处理的 worker，会在下一次检查点读取。"})
        elif decision.kind == "resume":
            self.ledger.push_inbox(active["id"], decision.text)
            self.ledger.resume(active["id"], "human answered in session")
            self._send(session_id, ack_id, {"type": "thought", "body": "收到回复，继续处理。"})
        elif decision.kind == "retry":
            self.ledger.retry(history[-1]["id"], "human asked 重试 in session")
            self._send(session_id, ack_id, {"type": "thought", "body": "已重新排队。"})
        elif decision.kind == "elicit":
            self._send(session_id, ack_id, {"type": "elicitation", "body": decision.text})

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
                self._send(row["session_id"], row["ack_id"], {"type": "error", "body": f"FarmBot 处理这条消息时出错（{type(exc).__name__}），请稍后重试或联系维护者。"})
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
            super().server_bind()

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
