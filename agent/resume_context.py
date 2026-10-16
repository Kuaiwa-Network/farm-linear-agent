"""A navigable resume view; complete evidence remains available in issue-context."""
import copy


def resume_view(context):
    """Keep current authority/decisions intact, index repeated historical evidence.

    This is a read projection, never a new fingerprint or an acceptance verdict.
    Unknown authors are retained in full, just like humans.
    """
    result = copy.deepcopy(context)
    omitted = []
    for index, comment in enumerate(result.get('issue', {}).get('comments', [])):
        if comment.get('author_kind') == 'bot' and 'body' in comment:
            comment.pop('body')
            comment['body_omitted'] = True
            omitted.append(f'issue.comments[{index}].body')
    for index, entry in enumerate(result.get('conversation_history', [])):
        if entry.get('handoff') is not None:
            entry.pop('handoff')
            entry['handoff_available'] = True
            omitted.append(f'conversation_history[{index}].handoff')
    for group in ('tasks', 'predecessor_tasks'):
        for index, task in enumerate(result.get(group, [])):
            if task.get('state') == 'delivered' and task.get('checkpoint'):
                task.pop('checkpoint')
                task['checkpoint_available'] = True
                omitted.append(f'{group}[{index}].checkpoint')
    result['context_view'] = {
        'name': 'resume', 'omitted_paths': omitted,
        'complete_view': 'issue-context --item ITEM --view full',
        'requires_full_view': bool((result.get('handoff') or {}).get('stale')),
        'guidance': ('Read full context when the handoff is stale, a decision refers to omitted bot text, '
                     'or a needed task/history fact is missing. No omitted field is a passed check. '
                     'Current requirements, human/unknown comments, messages and questions are retained.')}
    return result
