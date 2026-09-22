"""Route lifecycle events; workers interpret natural-language intent."""
from dataclasses import dataclass

WRITE_SKILLS = ("fix", "fgui", "feature")


@dataclass(frozen=True)
class Decision:
    kind: str
    skill: str | None = None
    text: str | None = None


def route(*, action, is_delegation, text, labels, active_state, terminal_exists, available_skills):
    if action == "stop":
        return Decision("stop")
    if action == "prompted" and active_state is not None:
        if active_state == "awaiting_input":
            return Decision("resume", None, text)
        return Decision("steer", None, text)
    if is_delegation and action == "created" and not (text or "").strip():
        # Retain the explicit Bug-delegation workflow. Any actual message is
        # interpreted first, including questions and negations on a Bug issue.
        if "Bug" in labels and "fix" in available_skills:
            return Decision("work", "fix")
    return Decision("chat", "chat", text)
