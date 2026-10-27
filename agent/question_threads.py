"""Durable question comments and attributed replies for the current issue owner."""
import hashlib
import json
from uuid import uuid4

from .attention import QUESTION_KEY, question_notice, questions


def prepare(ledger, item_id, token, body, bot_name):
    """Reserve the round before posting. Changed text gets a durable reply in the original thread."""
    from .ledger import _text
    _text(body, 'question')
    with ledger._transaction():
        row = ledger._owned(item_id, token)
        ledger._refuse_withdrawn(row)
        ledger.require_valid_checkpoint(item_id, token)
        ledger.require_no_reservation(item_id)
        checkpoint = json.loads(row['checkpoint'])
        question = ledger.human_question(item_id)
        if question is None:
            after = ledger.connection.execute('SELECT COALESCE(MAX(id),0) FROM inbox '
                'WHERE item_id=? AND consumed_at IS NOT NULL', (item_id,)).fetchone()[0]
            question = {'id': str(uuid4()), 'body': body, 'after_message_id': after}
        elif question['id'].startswith('legacy-'):
            question['id'] = str(uuid4())  # Freeze the legacy reply floor before another park.
        question.setdefault('comment_request_id', 'question-' + hashlib.sha256(question['id'].encode()).hexdigest()[:32])
        request_ids = [question['comment_request_id'], *question.get('comment_followup_request_ids', [])]
        if ledger.notice(item_id, request_ids[0]) is None:
            rendered = question_notice(ledger, ledger._view(row), question['body'], bot_name, comment_thread=True)
            ledger._prepare_notice(row, 'question', request_ids[0], rendered)
        if body.strip().replace('\r\n', '\n') != question['body'].strip().replace('\r\n', '\n'):
            # Chain revisions so a retry reuses its notice, while A -> B -> A is a real new clarification.
            request_id = 'question-followup-' + hashlib.sha256(
                (request_ids[-1] + '\n' + body).encode()).hexdigest()[:40]
            rendered = question_notice(ledger, ledger._view(row), body, bot_name, comment_thread=True)
            ledger._prepare_notice(row, 'question', request_id, rendered)
            request_ids.append(request_id)
            question['comment_followup_request_ids'] = request_ids[1:]
            question['body'] = body
        checkpoint[QUESTION_KEY] = question
        checkpoint['pending_question'] = question['body']
        checkpoint['pending_reason'] = 'question'
        ledger.connection.execute('UPDATE work_items SET checkpoint=?,updated_at=? WHERE id=?',
                                  (json.dumps(checkpoint, ensure_ascii=False), ledger.clock(), item_id))
        return [ledger.notice(item_id, request_id) for request_id in request_ids]


def point_to_question(ledger, api, item_id, token, notice, bot_name):
    """Keep one session pointer per revision, with a saved ID for ambiguous sends."""
    from .ledger import LedgerError
    with ledger._transaction():
        row = ledger._owned(item_id, token)
        ledger._refuse_withdrawn(row)
        if str(row['session_id']).startswith('local-'):
            return
        checkpoint = json.loads(row['checkpoint'])
        question = checkpoint.get(QUESTION_KEY)
        if not question or notice['request_id'] != (
                question.get('comment_followup_request_ids') or [question.get('comment_request_id')])[-1]:
            raise LedgerError('question round changed before session pointer')
        pointer = question.get('comment_activity')
        if not pointer or pointer['request_id'] != notice['request_id']:
            url = ledger.issue(row['issue_id'])['url'] + '#comment-' + notice['remote_id']
            prefix = '剩余问题已补充到原' if question.get('comment_followup_request_ids') else '问题已发布到'
            pointer = {'id': str(uuid4()), 'request_id': notice['request_id'], 'sent': False,
                       'body': f'{prefix} [issue 评论]({url})。各负责人可分别回复问题评论；'
                               f'大家答完后，由任意一人 @{bot_name} 告知检查并继续，无需汇总到会话。'}
            question['comment_activity'] = pointer
            ledger.connection.execute('UPDATE work_items SET checkpoint=?,updated_at=? WHERE id=?',
                (json.dumps(checkpoint, ensure_ascii=False), ledger.clock(), item_id))
        if pointer['sent']:
            return
    api.create_activity(row['session_id'], {'type': 'elicitation', 'body': pointer['body']},
                        activity_id=pointer['id'])
    with ledger._transaction():
        row = ledger._owned(item_id, token)
        checkpoint = json.loads(row['checkpoint'])
        current = checkpoint.get(QUESTION_KEY)
        if current and (current.get('comment_activity') or {}).get('id') == pointer['id']:
            current['comment_activity']['sent'] = True
            ledger.connection.execute('UPDATE work_items SET checkpoint=?,updated_at=? WHERE id=?',
                (json.dumps(checkpoint, ensure_ascii=False), ledger.clock(), item_id))


def _in_thread(comment, root_id, comments):
    parent, seen = comment.get('parent_id'), set()
    while parent and parent not in seen:
        if parent == root_id:
            return True
        seen.add(parent)
        parent = comments.get(parent, {}).get('parent_id')
    return False


def collect_replies(ledger, api, issue_id, *, item_id=None, issue=None):
    """Collect answers on explicit human contact; the receiver alone decides whether to resume."""
    from .ledger import ACTIVE_STATES, TERMINAL_STATUS_TYPES, _in_scope
    targets = [q for q in questions(ledger, issue_id) if q.get('comment_request_id')
               and (item_id is None or q['item_id'] == item_id)]
    targets = [(q, ledger.notice(q['item_id'], q['comment_request_id'])) for q in targets]
    targets = [(q, notice) for q, notice in targets if notice and notice['remote_id']]
    if not targets:
        return
    raw = api.fetch_issue(issue_id) if issue is None else issue
    if raw['id'] != issue_id:
        raise ValueError('question reply response identifies a different issue')
    ledger.observe_issue(raw)  # Validate every comment and its author before using it as input.
    with ledger._transaction():
        issue = ledger.issue(issue_id)
        if (not _in_scope(issue) or issue['archived'] or issue['status_type'] in TERMINAL_STATUS_TYPES
                or raw['archived'] or raw['status_type'] in TERMINAL_STATUS_TYPES):
            return
        comments = {c['id']: c for c in issue['comments']}
        for expected, notice in targets:
            row = ledger._row(expected['item_id'])
            question = ledger.human_question(row['id'])
            if (row['state'] not in ACTIVE_STATES or row['withdraw_deadline'] is not None
                    or not question or question['id'] != expected['id']):
                continue
            if row['authority'] == 'delegation' and (not api.app_user_id
                    or issue.get('delegate_id') != api.app_user_id or raw.get('delegate_id') != api.app_user_id):
                continue
            versions = question.setdefault('comment_reply_versions', {})
            received = False
            for comment in sorted(comments.values(), key=lambda c: (ledger._version(c['updated_at']), c['id'])):
                if (comment['author_kind'] != 'human' or not comment.get('author') or not comment['body'].strip()
                        or not _in_thread(comment, notice['remote_id'], comments)):
                    continue
                version = ledger._version(comment['updated_at'])
                digest = hashlib.sha256(json.dumps([comment['body'], comment['author']], sort_keys=True,
                                                  ensure_ascii=False).encode()).hexdigest()
                previous = versions.get(comment['id'])
                if previous and (version < previous['at'] or
                                 (version == previous['at'] and digest in previous['hashes'])):
                    continue
                source = comment.get('url') or comment['id']
                body = f"Reply to question {question['id']} ({source}):\n\n{comment['body']}"
                cursor = ledger.connection.execute('INSERT INTO inbox(item_id,body,author_json,created_at) VALUES(?,?,?,?)',
                    (row['id'], body, json.dumps(comment['author'], ensure_ascii=False), ledger.clock()))
                versions[comment['id']] = {'at': version, 'hashes': (
                    previous['hashes'] + [digest] if previous and version == previous['at'] else [digest])}
                ledger._audit(row['id'], 'question_comment_reply', 'attributed reply in question thread',
                    {'question_id': question['id'], 'comment_id': comment['id'], 'message_id': cursor.lastrowid,
                     'updated_at': comment['updated_at']})
                received = True
            if received:
                checkpoint = json.loads(row['checkpoint'])
                checkpoint[QUESTION_KEY] = question
                ledger.connection.execute('UPDATE work_items SET checkpoint=?,updated_at=? WHERE id=?',
                    (json.dumps(checkpoint, ensure_ascii=False), ledger.clock(), row['id']))
