"""Launch the single issue owner. Historical prompts are isolated for offline replay."""
from pathlib import Path
from .execution import single
from . import legacy_dispatch

# Compatibility for chat and historical state readers. New work never composes
# the old stage instructions.
SKILL_AUTHORITY = legacy_dispatch.SKILL_AUTHORITY
authority = legacy_dispatch.authority


def dispatch_message(**arguments):
    skill_path = Path(arguments['skill_path'])
    current_chat = (arguments['item'].get('skill') == 'chat' and skill_path.parent.name == 'chat'
                    and skill_path.parent.parent.name != 'legacy')
    if single(arguments['item']) or current_chat:
        from .worker_dispatch import message
        return message(**arguments)
    return legacy_dispatch.dispatch_message(**arguments)


def __getattr__(name):
    """Historical diagnostics/tests may still name archived prompt constants."""
    return getattr(legacy_dispatch, name)
