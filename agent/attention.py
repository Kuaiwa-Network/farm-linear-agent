"""Issue-level human attention, independent of worker execution state."""
import hashlib

REPLY_LABEL = "待回复"
QUESTION_KEY = "human_question"


def question_from(item):
    checkpoint = item["checkpoint"]
    if QUESTION_KEY in checkpoint:
        return checkpoint[QUESTION_KEY]
    # Existing parked jobs remain answerable after an upgrade. No schema change.
    body = checkpoint.get("pending_question")
    if body and checkpoint.get("pending_reason") != "waiting":
        digest = hashlib.sha256((item["id"] + body).encode("utf-8")).hexdigest()
        return {"id": "legacy-" + digest, "body": body, "after_message_id": 0}
    return None


def questions(ledger, issue_id):
    from .ledger import ACTIVE_STATES
    found = []
    for row in ledger.connection.execute("SELECT * FROM work_items WHERE issue_id=?", (issue_id,)):
        if row["state"] not in ACTIVE_STATES or row["withdraw_deadline"] is not None:
            continue
        if row["parent_id"]:
            parent = ledger._row(row["parent_id"])
            if parent["state"] not in ACTIVE_STATES or parent["withdraw_deadline"] is not None:
                continue
        item = ledger._view(row)
        if question := ledger.human_question(item["id"]):
            found.append({"item_id": item["id"], "task_key": item["task_key"], **question})
    return found


def mark_reply_label(ledger, item_id):
    if not ledger.connection.execute("SELECT 1 FROM audit WHERE item_id=? AND kind='reply_label_managed'",
                                     (item_id,)).fetchone():
        ledger.note(item_id, "reply_label_managed", "owns only the issue's 待回复 signal")


def reconcile_reply_label(ledger, api, issue_id):
    pending = questions(ledger, issue_id)
    owned = ledger.connection.execute("SELECT 1 FROM audit a JOIN work_items w ON w.id=a.item_id "
        "WHERE w.issue_id=? AND a.kind='reply_label_managed' LIMIT 1", (issue_id,)).fetchone()
    # Never sweep a human's same-named flag from an old tracked issue on upgrade.
    if not pending and not owned:
        return
    if api.awaiting_reply(issue_id, bool(pending)) is not False and pending and not owned:
        mark_reply_label(ledger, pending[0]["item_id"])


def question_notice(ledger, item, body, bot_name):
    context = ledger.issue_context(item["id"])
    person = (context.get("owner") or {}).get("person")
    mention = (person or {}).get("url", "")
    heading = f"🔴 {item['identifier']} 待回复"
    if item.get("task_key"):
        heading += f" [{item['task_key']}]"
    instructions = (f"请直接在 {bot_name} 的会话中回复，或在本 issue 评论中 @{bot_name} 并写明问题编号和答案。"
                    "普通评论不自动唤醒暂停任务；部分答复只解决已回答的问题。")
    parts = [heading]
    if mention and mention not in body:
        parts.append(mention)
    return "\n\n".join([*parts, body, instructions])
