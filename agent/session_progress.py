"""Durable host-owned Linear progress; reporting never extends a worker lease."""
import json
from uuid import uuid4


class SessionProgress:
    def __init__(self, ledger, api, *, interval=600, retry_seconds=60):
        self.ledger, self.api = ledger, api
        self.interval, self.retry_seconds = interval, retry_seconds
        self.db = ledger.connection
        self.db.execute("""CREATE TABLE IF NOT EXISTS session_progress (
            item_id TEXT PRIMARY KEY REFERENCES work_items(id), due_at REAL NOT NULL,
            activity_id TEXT, content TEXT, status_key TEXT, last_error TEXT)""")

    @staticmethod
    def _eligible(item):
        return item["state"] in ("queued", "running", "awaiting_resource")

    def _content(self, item):
        state = item["state"]
        if state == "queued":
            body = ("工作已保留，正在等待重试。" if item["retry_not_before"] > self.ledger.clock()
                    else "工作仍在排队，等待可用的执行资源。")
        elif state == "awaiting_resource":
            body = "工作已保留，正在等待 Unity 验证资源。"
        elif state == "running":
            body = f"工作仍在处理中；已记录阶段：{item['stage'][:120]}。"
            checkpoint = self.db.execute("SELECT max(created_at) FROM audit WHERE item_id=? AND kind='checkpoint'",
                                         (item["id"],)).fetchone()[0]
            body += (f"最近保存检查点距今 {max(0, int((self.ledger.clock() - checkpoint) / 60))} 分钟。"
                     if checkpoint is not None else "尚未保存新的检查点。")
        elif state == "awaiting_input":
            return {"type": "elicitation", "body": item["checkpoint"].get("pending_question") or "正在等待你的回复。"}
        else:
            # A heartbeat can cross a worker's final activity in flight. Restore
            # the actual session state instead of leaving completed work active.
            body = {"delivered": "工作已完成。", "cancelled": "工作已停止。",
                    "failed": "工作执行失败；详情见上一条错误报告。", "blocked": "工作遇到阻碍，等待处理。"}[state]
            summary = item["evidence"].get("summary")
            if summary:
                body += "\n" + summary
            return {"type": "error" if state == "failed" else "response", "body": body}
        return {"type": "thought", "body": body}

    def _current(self, item_id):
        item = self.ledger.item(item_id)
        active = self.ledger.active_item_for_session(item["session_id"])
        # Do not let an old item's pending response close a successor conversation.
        # A cross-session handoff does complete the source session, however.
        if active and active["session_id"] == item["session_id"]:
            item = active
        return item, f"{item['id']}:{item['state']}:{item['generation']}"

    def _reserve(self, item_id, content, status_key):
        activity_id = str(uuid4())
        self.db.execute("UPDATE session_progress SET activity_id=?,content=?,status_key=?,due_at=?,last_error=NULL WHERE item_id=?",
                        (activity_id, json.dumps(content, ensure_ascii=False), status_key,
                         self.ledger.clock() + self.retry_seconds, item_id))
        return activity_id

    def _send(self, item_id, session_id, content, activity_id):
        # No database or scheduler lock spans the remote request. Stop and worker
        # completion remain free to proceed while Linear is slow or unavailable.
        try:
            self.api.create_activity(session_id, content, activity_id=activity_id)
        except Exception as exc:
            self.db.execute("UPDATE session_progress SET last_error=? WHERE item_id=?",
                            (type(exc).__name__, item_id))
            return False
        return True

    def tick(self):
        now = self.ledger.clock()
        with self.ledger._transaction():
            self.db.execute("""INSERT OR IGNORE INTO session_progress(item_id,due_at)
                SELECT id,created_at+? FROM work_items
                WHERE state IN ('queued','running','awaiting_resource') AND session_id NOT LIKE 'local-%'""",
                (self.interval,))
            row = self.db.execute("""SELECT p.* FROM session_progress p JOIN work_items w ON w.id=p.item_id
                WHERE p.due_at<=? AND (p.content IS NOT NULL OR w.state IN ('queued','running','awaiting_resource'))
                ORDER BY p.due_at,w.created_at LIMIT 1""", (now,)).fetchone()
            if row is None:
                return False
            item_id = row["item_id"]
            item, status_key = self._current(item_id)
            content = json.loads(row["content"]) if row["content"] else None
            # Reevaluate pending sends after restarts/state changes. In particular,
            # never retry a stale thought after a question, Stop or completion.
            if content is None or row["status_key"] != status_key:
                content = self._content(item)
                activity_id = self._reserve(item_id, content, status_key)
            else:
                activity_id = row["activity_id"]
                self.db.execute("UPDATE session_progress SET due_at=? WHERE item_id=?",
                                (now + self.retry_seconds, item_id))
        # Correct a racing state change promptly. Further changes are durable for
        # the next tick, so a flapping job cannot monopolize this reporting loop.
        for _ in range(2):
            sent = self._send(item_id, item["session_id"], content, activity_id)
            current, current_key = self._current(item_id)
            if current_key != status_key:
                item, status_key = current, current_key
                content = self._content(item)
                activity_id = self._reserve(item_id, content, status_key)
                continue
            if sent:
                self.db.execute("UPDATE session_progress SET due_at=?,activity_id=NULL,content=NULL,status_key=NULL,last_error=NULL WHERE item_id=?",
                                (self.ledger.clock() + self.interval, item_id))
            break
        return True
