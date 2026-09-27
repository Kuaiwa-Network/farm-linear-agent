"""Route lifecycle events; workers interpret natural-language intent."""
from dataclasses import dataclass

WRITE_SKILLS = ("fix", "fgui", "feature")
# The team label group 功能 and the skill each child starts (spec §4.1, §4.3). Linear returns a child under its
# own short name, so a label counts only together with this parent: a standalone "UI" or "Code" label is not one.
FEATURE_GROUP = "功能"
FEATURE_SKILLS = {"UI": "fgui", "Code": "feature"}


@dataclass(frozen=True)
class Decision:
    kind: str
    skill: str | None = None
    text: str | None = None


def feature_children(label_groups):
    """The issue's 功能 children, sorted. Snapshots older than label groups have none."""
    return sorted({entry["label"] for entry in label_groups or ()
                   if isinstance(entry, dict) and entry.get("group") == FEATURE_GROUP
                   and isinstance(entry.get("label"), str) and entry["label"]})


def _named(children):
    return "、".join(f"{FEATURE_GROUP}/{child}" for child in children)


def _conflict(children, text):
    body = f"这张卡同时带有 Bug 和 {_named(children)}；请移除不适用的那个标签，然后在这里回复。"
    if (text or "").strip():
        body += "这条消息还没有处理，请在回复里再说一次。"
    return body


def _not_run(children, skill, available_skills):
    runs = "、".join(sorted(available_skills))
    if skill is not None:
        return (f"这张卡带有 {_named(children)}，由 {skill} 处理，但本实例没有启用 {skill}（本实例运行：{runs}）。"
                "先以只读对话查看，不会开始这项工作。")
    known = "，".join(f"{FEATURE_GROUP}/{child} 对应 {name}" for child, name in FEATURE_SKILLS.items())
    return (f"这张卡带有 {_named(children)}，无法对应到一项功能工作（{known}，每张卡只带一个）；"
            f"本实例运行：{runs}。先以只读对话查看，不会开始功能工作。")


def feature_repair_refusal(children, available_skills):
    """Why a conversation on a 功能 card may not start a fix, and how its work does start (spec §9.4, D16)."""
    skill = FEATURE_SKILLS.get(children[0]) if len(children) == 1 else None
    labels = _named(children)
    if skill is None:
        return (f"this issue carries {labels}, not exactly one 功能/UI or 功能/Code label; 功能 work starts only when "
                "an issue with exactly one of them is delegated, never as a fix")
    refusal = (f"this issue carries {labels}, so it is {skill} work, not a fix; {skill} work starts only when an "
               f"issue labelled {labels} is delegated")
    return refusal if skill in available_skills else refusal + f", and this instance does not run {skill} yet"


def route(*, action, is_delegation, text, labels, active_state, terminal_exists, available_skills,
          label_groups=(), reroute=False):
    """`reroute` marks a reply in a delegation session that never had a work item (D16): it routes as that
    delegation would, on the labels just fetched, with the reply as the delegation's text."""
    if action == "stop":
        return Decision("stop")
    if action == "prompted" and active_state is not None:
        if active_state == "awaiting_input":
            return Decision("resume", None, text)
        return Decision("steer", None, text)
    if is_delegation and (action == "created" or reroute):
        # Spec §4.3, in order. A 功能 label chooses the workflow, so its text never diverts it to chat (§4.4).
        children = feature_children(label_groups)
        if children and "Bug" in labels:
            return Decision("elicit", None, _conflict(children, text))
        if children:
            skill = FEATURE_SKILLS.get(children[0]) if len(children) == 1 else None
            if skill in available_skills:
                return Decision("work", skill)
            return Decision("chat", "chat", _not_run(children, skill, available_skills))
        if not (text or "").strip() and "Bug" in labels and "fix" in available_skills:
            # Retain the explicit Bug-delegation workflow. Any actual message is
            # interpreted first, including questions and negations on a Bug issue.
            return Decision("work", "fix")
    return Decision("chat", "chat", text)
