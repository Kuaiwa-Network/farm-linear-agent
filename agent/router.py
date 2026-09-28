"""Route lifecycle events; workers interpret natural-language intent."""
from dataclasses import dataclass

WRITE_SKILLS = ("fix", "fgui", "feature")
# The team label group Bot and the skill each child starts (D18; feature spec §4.1, §4.3). Linear returns a child
# under its own short name, so a label counts only together with this parent: a standalone "UI" or "Code" label is
# not one. Bug, Improvement, Feature and the other labels for people never route. The group was named 功能 before
# D18: this release accepts both names, so a host routes alike on either side of the rename in Linear, and a later
# release drops 功能 (Bot label group design §4.1).
BOT_GROUP = "Bot"
BOT_GROUPS = (BOT_GROUP, "功能")
BOT_SKILLS = {"修改": "fix", "UI": "fgui", "Code": "feature"}
# The write skills whose delegated jobs a request in a conversation continues, whatever the card's label now says
# (spec §9.4). fgui joins when its phase lands.
CONVERSATION_SKILLS = ("fix", "feature")


@dataclass(frozen=True)
class Decision:
    kind: str
    skill: str | None = None
    text: str | None = None
    # A delegation of a card with no Bot child: its first message says how to get a fix (D18, design §4.2).
    unlabelled: bool = False


def bot_children(label_groups):
    """The issue's Bot children, sorted. Snapshots older than label groups have none."""
    return sorted({entry["label"] for entry in label_groups or ()
                   if isinstance(entry, dict) and entry.get("group") in BOT_GROUPS
                   and isinstance(entry.get("label"), str) and entry["label"]})


def _named(children):
    return "、".join(f"{BOT_GROUP}/{child}" for child in children)


def _not_run(children, skill, available_skills):
    runs = "、".join(sorted(available_skills))
    if skill is not None:
        return (f"这张卡带有 {_named(children)}，由 {skill} 处理，但本实例没有启用 {skill}（本实例运行：{runs}）。"
                "先以只读对话查看，不会开始这项工作。")
    known = "，".join(f"{BOT_GROUP}/{child} 对应 {name}" for child, name in BOT_SKILLS.items())
    return (f"这张卡带有 {_named(children)}，无法对应到一项工作（{known}，每张卡只带一个）；"
            f"本实例运行：{runs}。先以只读对话查看，不会开始这项工作。")


def start_refusal(children, available_skills):
    """Why a start request in a conversation starts nothing here, or None when it may start or continue `fix`.

    D18 f (design §4.4): the card's Bot label chooses the workflow. No Bot child, or only 修改, is `fix`. A first
    `fgui` or `feature` job cannot be started from a conversation in this release, and an unknown child or two
    children name no workflow."""
    skill = BOT_SKILLS.get(children[0]) if len(children) == 1 else None
    if not children or skill == "fix":
        return None
    labels = _named(children)
    if skill is None:
        return (f"this issue carries {labels}, not exactly one of {BOT_GROUP}/修改, {BOT_GROUP}/UI or "
                f"{BOT_GROUP}/Code, so it names no workflow; correct the label, then ask again")
    if skill not in available_skills:
        return (f"this issue carries {labels}, so it is {skill} work, not a fix, and this instance does not run "
                f"{skill} yet")
    return (f"this issue carries {labels}, so it is {skill} work, not a fix; {skill} work starts only when an "
            f"issue labelled {labels} is delegated")


def continuation_refusal(skill):
    """Why a request in a conversation continues nothing here: the delegation's own earlier job is `skill` work,
    which this instance does not run, and a request never starts another skill's job in its place (spec §9.4)."""
    return f"this issue's earlier {skill} job continues only on an instance that runs {skill}, and this one does not"


def route(*, action, is_delegation, text, labels, active_state, terminal_exists, available_skills,
          label_groups=(), reroute=False):
    """`reroute` marks a reply in a delegation session that never had a work item (D16): it routes as that
    delegation would, on the labels just fetched, with the reply as the delegation's text. `labels`, the bare
    names, no longer route (D18 c); only a Bot child of `label_groups` does."""
    if action == "stop":
        return Decision("stop")
    if action == "prompted" and active_state is not None:
        if active_state == "awaiting_input":
            return Decision("resume", None, text)
        return Decision("steer", None, text)
    if is_delegation and (action == "created" or reroute):
        # A Bot child chooses the workflow, so the delegation's text never diverts it to chat (spec §4.4, D18).
        children = bot_children(label_groups)
        if children:
            skill = BOT_SKILLS.get(children[0]) if len(children) == 1 else None
            if skill in available_skills:
                return Decision("work", skill)
            return Decision("chat", "chat", _not_run(children, skill, available_skills))
        if action == "created" and "fix" in available_skills:
            # No Bot label: a conversation whose first message says how to get a fix. A re-route's reply is
            # already that conversation's first message, so it keeps the ordinary acknowledgement.
            return Decision("chat", "chat", text, unlabelled=True)
    return Decision("chat", "chat", text)
