"""Small CLI acknowledgements; stored progress and lifecycle gates stay intact."""
from .execution import single


RECEIPT_COMMANDS = ('renew', 'checkpoint', 'revalidate')
# Omit only known context payloads. Keep all other fields, including new control,
# recovery or refusal information, rather than silently dropping unknown keys.
CONTEXT_FIELDS = ('checkpoint', 'evidence', 'target', 'branch_target', 'task_request')


def response(args, result):
    """Project the committed result without any fallible post-commit reads."""
    if (args.command not in RECEIPT_COMMANDS or not isinstance(result, dict) or not single(result)):
        return result
    if args.command == 'checkpoint':
        result = {**result, 'checkpoint_saved': True}
    if getattr(args, 'view', 'compact') == 'full':
        return result
    omitted = [key for key in CONTEXT_FIELDS if result.get(key)]
    receipt = {key: value for key, value in result.items() if key not in omitted}
    receipt['response_view'] = {
        'name': 'compact', 'command': args.command,
        'omitted_fields': omitted,
        'read_context': f"issue-context --item {result['id']} --view resume",
        'read_full_item': f"issue-context --item {result['id']} --view work-item",
    }
    return receipt
