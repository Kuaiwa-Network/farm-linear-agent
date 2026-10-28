"""Issue-level human attention, independent of worker execution state."""
import hashlib

REPLY_LABEL = "待回复"
REVIEW_LABEL = "待人工审查"
LEGACY_REVIEW_LABEL = "needs-person"
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


def review_label_state(ledger, issue_id):
    """The latest implementation generation owns the issue's review status."""
    if not ledger.connection.execute('SELECT 1 FROM review_runs r JOIN work_items w ON w.id=r.item_id '
                                     'WHERE w.issue_id=? LIMIT 1', (issue_id,)).fetchone():
        return None
    item = ledger.connection.execute("SELECT id,generation,state FROM work_items WHERE issue_id=? "
        "AND parent_id IS NULL AND skill='work' ORDER BY created_at DESC,rowid DESC LIMIT 1", (issue_id,)).fetchone()
    if item is None:
        return None
    run = ledger.connection.execute('SELECT id,state,disposition FROM review_runs WHERE item_id=? AND generation=?',
                                    (item['id'], item['generation'])).fetchone()
    issue = ledger.issue(issue_id)
    waiting = bool(run and item['state'] == 'delivered' and run['state'] in ('delivered', 'superseded')
                   and run['disposition'] in ('human_handoff', 'superseded') and not issue['archived']
                   and issue['status_type'] not in ('completed', 'canceled', 'duplicate'))
    return (item['id'], item['generation'], run['id'] if run else None, waiting)


def reconcile_review_label(ledger, api, issue_id):
    state = review_label_state(ledger, issue_id)
    if state is None:
        return True
    owned = ledger.connection.execute("SELECT 1 FROM audit a JOIN work_items w ON w.id=a.item_id "
        "WHERE w.issue_id=? AND a.kind='review_label_managed' LIMIT 1", (issue_id,)).fetchone()
    if not state[-1] and not owned:
        return True  # Never sweep an unrelated human's same-named label.
    if not owned:
        # Persist ownership before the write, including a lost API response.
        ledger.note(state[0], 'review_label_managed', 'owns only the issue review-status label ' + REVIEW_LABEL)
    try:
        return api.awaiting_review(issue_id, state[-1],
                                   still_current=lambda: review_label_state(ledger, issue_id) == state)
    except Exception:
        # Retry even after delivery/closure, preserving the lifecycle's polling
        # delay instead of turning a label outage into a busy status-read loop.
        ledger.connection.execute('UPDATE issue_checks SET requested=1 WHERE issue_id=?', (issue_id,))
        raise


def question_notice(ledger, item, body, bot_name, *, comment_thread=False):
    context = ledger.issue_context(item["id"])
    person = (context.get("owner") or {}).get("person")
    mention = (person or {}).get("url", "")
    heading = f"🔴 {item['identifier']} 待回复"
    if item.get("task_key"):
        heading += f" [{item['task_key']}]"
    instructions = (f"请直接在 {bot_name} 的会话中回复，或在本 issue 评论中 @{bot_name} 并写明问题编号和答案。"
                    "@提及会新开 Linear 会话，答案会转给原工作项；直接回复原会话可保持同一会话。"
                    "普通评论不自动唤醒暂停任务；部分答复只解决已回答的问题。")
    if comment_thread:
        instructions = ("请直接回复这条问题评论，写明问题编号和答案。各负责人可以分别回答，不必由一人汇总。"
                        f"等大家回答完毕后，由任意一人 @{bot_name} 告知可以检查并继续；"
                        "届时会一并读取本评论串的答复并保留答复人，普通回复不会自动唤醒任务。"
                        "也可回复原会话要求检查。部分答复只解决已回答的问题；"
                        "尚未回答的问题会继续等待。")
    parts = [heading]
    if mention and mention not in body:
        parts.append(mention)
    return "\n\n".join([*parts, body, instructions])
