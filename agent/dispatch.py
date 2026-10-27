"""Launch the single issue owner. Historical prompts are isolated for offline replay."""
from pathlib import Path
from .execution import single
from . import legacy_dispatch

# Compatibility for chat and historical state readers. New work never composes
# the old stage instructions.
SKILL_AUTHORITY = legacy_dispatch.SKILL_AUTHORITY
authority = legacy_dispatch.authority

APPROVAL_RETRY_AUTHORITY = (
    'If automatic approval review fails with "Selected model is at capacity" and explicitly says the action '
    'was not executed, save its complete returned review message verbatim in a UTF-8 file under state_dir. '
    'Checkpoint the failed action, candidate commits, reports and remaining work; settle helpers and release '
    'resources safely. Call retry-approval --item ITEM --token-file STATE_DIR/token --error-file FILE using '
    'the normal execution.python and --db DATABASE CLI prefix, then exit on retry_queued or retry_exhausted. '
    'Do not call await-input for this transient failure or spend repeated in-worker approval attempts. '
    'The host delays retries by 60, 180, then 600 seconds using the shared three-attempt capacity allowance. '
    'On resume reuse matching source and reports, recheck current requests and authority, and submit the '
    'action through the same normal automatic approval again. Publication still requires fresh '
    'verify-publication immediately before each mutation. Retry never grants approval or permits a different '
    'execution path, disabled review, changed permissions or model settings. Other denials and uncertain '
    'execution outcomes do not qualify; follow their normal handling. '
)


def dispatch_message(**arguments):
    skill_path = Path(arguments['skill_path'])
    current_chat = (arguments['item'].get('skill') == 'chat' and skill_path.parent.name == 'chat'
                    and skill_path.parent.parent.name != 'legacy')
    if single(arguments['item']) or current_chat:
        from .worker_dispatch import message, startup_instruction
        return startup_instruction(**arguments) + APPROVAL_RETRY_AUTHORITY + message(**arguments)
    return legacy_dispatch.dispatch_message(**arguments)


def review_message(*args, **kwargs):
    """Internal delivery stage has narrower, role-specific authority than work."""
    from .review_dispatch import message
    return message(*args, **kwargs)


def __getattr__(name):
    """Historical diagnostics/tests may still name archived prompt constants."""
    return getattr(legacy_dispatch, name)
