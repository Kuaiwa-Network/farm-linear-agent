"""Small CLI acknowledgements; stored progress and lifecycle gates stay intact."""
from .attention import question_from
from .execution import single


RECEIPT_COMMANDS = ('renew', 'checkpoint', 'revalidate')
# Omit only known context payloads. Keep all other fields, including new control,
# recovery or refusal information, rather than silently dropping unknown keys.
CONTEXT_FIELDS = ('checkpoint', 'evidence', 'target', 'branch_target', 'task_request')


def response(args, result, ledger):
    """Project successful single-owner output only, after normal command checks."""
    if (args.command not in RECEIPT_COMMANDS or getattr(args, 'view', 'compact') == 'full'
            or not isinstance(result, dict) or not single(result)):
        return result
    receipt = {key: value for key, value in result.items() if key not in CONTEXT_FIELDS}
    receipt['response_view'] = {
        'name': 'compact', 'command': args.command,
        'omitted_fields': [key for key in CONTEXT_FIELDS if key in result],
        'read_context': f"issue-context --item {result['id']} --view resume",
        'read_full_item': f"issue-context --item {result['id']} --view full --include-work-item",
    }
    # Acknowledging a write must not repeat its entire progress/evidence payload.
    # Active questions are an exception: preserve their complete text and IDs.
    receipt['human_question'] = question_from(result)
    receipt['inbox_pending'] = ledger.connection.execute(
        'SELECT count(*) FROM inbox WHERE item_id=? AND consumed_at IS NULL',
        (result['id'],)).fetchone()[0]
    row = ledger._row(result['id'])
    receipt['requires_issue_review'] = bool(row['requeue_requested'] or
        ledger._issue_row(row['issue_id'])['fingerprint'] != row['claimed_fingerprint'])
    if args.command == 'checkpoint':
        receipt['checkpoint_saved'] = True
    return receipt
