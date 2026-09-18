"""Deterministic routing of Linear session events to skills (spec §4)."""
from dataclasses import dataclass

QA_WORDS = ("测试", "复现", "冒烟", "qa")
RETRY_WORDS = ("重试",)
WRITE_SKILLS = ("fix", "fgui", "feature")
ELICIT_TEXT = ("这个 issue 需要我做什么？请回复「修复」让我处理缺陷，或改为 @FarmBot 提问。"
               "没有 Bug 标签的委派我不会自动开工。")
QA_UNAVAILABLE = "我现在还不能在 qa 技能上执行游戏测试，只能回答问题；QA 会在下一阶段启用。"


@dataclass(frozen=True)
class Decision:
    kind: str
    skill: str | None = None
    text: str | None = None


def _contains(text, words):
    lowered = (text or "").lower()
    return any(word.lower() in lowered for word in words)


def route(*, action, is_delegation, text, labels, active_state, terminal_exists, available_skills):
    if action == "stop":
        return Decision("stop")
    if action == "prompted" and active_state is not None:
        if active_state == "awaiting_input":
            return Decision("resume", None, text)
        return Decision("steer", None, text)
    if action == "prompted" and terminal_exists and _contains(text, RETRY_WORDS):
        return Decision("retry")
    if is_delegation and action == "created":
        if "Bug" in labels and "fix" in available_skills:
            return Decision("work", "fix")
        return Decision("elicit", None, ELICIT_TEXT)
    if _contains(text, QA_WORDS):
        if "qa" in available_skills:
            return Decision("work", "qa")
        return Decision("chat", "chat", QA_UNAVAILABLE)
    return Decision("chat", "chat", text)
