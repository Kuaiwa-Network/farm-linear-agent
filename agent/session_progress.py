"""Durable host-owned Linear progress; reporting never extends a worker lease."""
import json
from uuid import uuid4

from .withdrawal import (HEARTBEAT_PREDECESSOR, HEARTBEAT_STATUS_ERROR, HEARTBEAT_UNDELEGATED,
                         HEARTBEAT_WITHDRAWING)


class SessionProgress:
    def __init__(self, ledger, api, *, interval=600, retry_seconds=60):
        self.ledger, self.api = ledger, api
        self.interval, self.retry_seconds = interval, retry_seconds
        self.db = ledger.connection
        self.db.execute("""CREATE TABLE IF NOT EXISTS session_progress (
            item_id TEXT PRIMARY KEY REFERENCES work_items(id), due_at REAL NOT NULL,
            activity_id TEXT, content TEXT, status_key TEXT, last_error TEXT,
            failures INTEGER NOT NULL DEFAULT 0)""")
        # Failed sends to the item's session since its last successful one, which space the retries out
        # (withdrawn-work design X4). Added to a table an older revision created; its rows start at zero.
        if "failures" not in {row[1] for row in self.db.execute("PRAGMA table_info(session_progress)")}:
            self.db.execute("ALTER TABLE session_progress ADD COLUMN failures INTEGER NOT NULL DEFAULT 0")

    @staticmethod
    def _eligible(item):
        return item["state"] in ("queued", "running", "awaiting_resource")

    def _queued_body(self, item):
        """What holds a queued job back: the first of a retry delay, its predecessor's cleanup, a failing status read
        and a card no longer delegated to this app, which only work the delegation authorised depends on
        (withdrawn-work design C6, §5.1 commit 5); else it waits for an execution resource."""
        if item["retry_not_before"] > self.ledger.clock():
            return "工作已保留，正在等待重试。"
        if item["predecessor_id"]:
            cleanup = self.ledger.cleanup_record(item["predecessor_id"])
            if not cleanup or not cleanup["done"]:
                return HEARTBEAT_PREDECESSOR
        check = self.ledger.status_check(item["issue_id"]) or {}
        if check.get("error"):
            return HEARTBEAT_STATUS_ERROR
        if check.get("undelegated_since") is not None and item["authority"] == "delegation":
            return HEARTBEAT_UNDELEGATED
        return "工作仍在排队，等待可用的执行资源。"

    def _content(self, item):
        state = item["state"]
        if state == "queued":
            body = self._queued_body(item)
        elif state == "awaiting_resource":
            body = "工作已保留，正在等待 Unity 验证资源。"
        elif state == "running" and item["withdraw_deadline"] is not None:
            body = HEARTBEAT_WITHDRAWING  # its worker saves its progress and runs `withdraw` (design P2)
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
        """The new send's activity id, or None when the item's row is gone: a cancellation that posted its own
        closing notice dropped it, and nothing may follow that notice."""
        activity_id = str(uuid4())
        cursor = self.db.execute("UPDATE session_progress SET activity_id=?,content=?,status_key=?,due_at=?,last_error=NULL WHERE item_id=?",
                                 (activity_id, json.dumps(content, ensure_ascii=False), status_key,
                                  self.ledger.clock() + self.retry_seconds, item_id))
        return activity_id if cursor.rowcount else None

    def _send(self, item_id, session_id, content, activity_id):
        # No database or scheduler lock spans the remote request. Stop and worker
        # completion remain free to proceed while Linear is slow or unavailable.
        try:
            self.api.create_activity(session_id, content, activity_id=activity_id)
        except Exception as exc:
            # Each failure in a row doubles the wait before the same activity is tried again, up to the interval:
            # a session Linear keeps refusing is not written to once a minute without end (design X4, P7).
            self.db.execute("""UPDATE session_progress SET last_error=?,failures=failures+1,
                due_at=?+MIN(?,?*(1<<MIN(failures,20))) WHERE item_id=? AND activity_id=?""",
                            (type(exc).__name__, self.ledger.clock(), self.interval, self.retry_seconds,
                             item_id, activity_id))
            return False
        return True

    def queue_current(self, item_id):
        """Correct an external status send that raced a newer job transition."""
        with self.ledger._transaction():
            item, status_key = self._current(item_id)
            self.db.execute('INSERT OR IGNORE INTO session_progress(item_id,due_at) VALUES(?,0)', (item_id,))
            self._reserve(item_id, self._content(item), status_key)
            self.db.execute('UPDATE session_progress SET due_at=0 WHERE item_id=?', (item_id,))

    def _post_owed(self, now):
        """Try one closing activity Linear refused earlier and that is due again (silent-delegation design P10): drop
        it unposted when its job, or newer work in its thread, has spoken there since; give it up untried when its
        tries have outlived their window, the controller having been down meanwhile, so that it never lands hours
        after the words it answers; else post it under a fresh activity id, since nobody knows whether Linear takes
        the same id twice (U7), and record the result. Whether one was due."""
        owed = self.ledger.due_closure(now)
        if owed is None:
            return False
        session_id, owed_at = owed["session_id"], owed["created_at"]
        if self.ledger.closure_superseded(owed):
            self.ledger.drop_closure(session_id, owed_at=owed_at)
            return True
        if now - owed_at > self.ledger.CLOSURE_WINDOW_SECONDS:
            self.ledger.give_up_closure(session_id, owed_at=owed_at)
            return True
        try:
            self.api.create_activity(session_id, {"type": owed["kind"], "body": owed["body"]}, activity_id=str(uuid4()))
        except Exception as exc:
            self.ledger.closure_result(session_id, sent=False, error=type(exc).__name__, owed_at=owed_at)
        else:
            self.ledger.closure_result(session_id, sent=True, owed_at=owed_at)
        return True

    def _close_again(self, item, content):
        """A heartbeat's question has just landed in the thread of `item`, whose row a cancel dropped meanwhile with
        a closing notice of its own: the question followed that notice and would be the thread's last activity, with
        no correction pending. `content`, the ended job's terminal text, closes the thread again, once; owed when
        Linear refuses it (silent-delegation design A7)."""
        try:
            self.api.create_activity(item["session_id"], content, activity_id=str(uuid4()))
        except Exception as exc:
            self.ledger.owe_closure(item["session_id"], issue_id=item["issue_id"], item_id=item["id"],
                                    kind=content["type"], body=content["body"], error=type(exc).__name__)

    def tick(self):
        now = self.ledger.clock()
        if self._post_owed(now):
            return True
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
                asked = sent and content["type"] == "elicitation"
                item, status_key = current, current_key
                content = self._content(item)
                activity_id = self._reserve(item_id, content, status_key)
                if activity_id is None:
                    # The row is gone: a cancel dropped it with a closing notice of its own, and nothing may follow that
                    # notice. A question that landed behind it has, though, and the thread is closed again: with the
                    # ended job's terminal text, never over newer work in its thread, which `item` would then be.
                    if asked and content["type"] in ("response", "error"):
                        self._close_again(item, content)
                    break
                continue
            if sent:
                self.db.execute("UPDATE session_progress SET due_at=?,activity_id=NULL,content=NULL,status_key=NULL,last_error=NULL,failures=0 WHERE item_id=? AND activity_id=?",
                                (self.ledger.clock() + self.interval, item_id, activity_id))
            break
        return True
