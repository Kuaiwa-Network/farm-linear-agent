"""The texts of withdrawn work and the grace a claimed worker has to stop (withdrawn-work design §7.1, P2:
docs/superpowers/specs/2026-09-29-withdrawn-work-design.md).

Work is withdrawn when the delegation that authorised it is gone, when a newer delegation session takes the card
over, or when the issue is out of reach. `{bot}` is the instance's configured Linear app name. A conversation's
text never mentions branches: a chat pushes none.
"""
from .router import WRITE_SKILLS

# The one session response a cancelled job gets, per reason. UNDELEGATED is the text the lifecycle already posted
# for a staged job before this change.
UNDELEGATED = ('这张卡已不再委派给 {bot}，这项工作已取消。已推送的分支和草稿 PR 都保留，接手的人可以在上面继续；'
               '重新委派给 {bot} 时会从已有进度接着做。')
UNDELEGATED_CHAT = '这张卡已不再委派给 {bot}，这段对话已结束。需要继续时，重新委派给 {bot}，或在评论里 @{bot}。'
SUPERSEDED = '这张卡有了新的委派会话，这里的工作已转到那里继续。'
# A waiting conversation a person's message moved to the thread it was written in, which no new delegation opened
# (design C5): the old thread, or for an operator's conversation the card, is told without naming a delegation.
MOVED_THREAD = '这段对话已转到另一个讨论串继续。'
CLOSED = '这张卡已关闭或归档，{bot} 已停止这里的工作。已推送的分支和草稿 PR 都保留。'
CLOSED_CHAT = '这张卡已关闭或归档，这段对话已结束。'
OPERATOR = '维护者已停止这项工作。已推送的分支和草稿 PR 都保留。'
OPERATOR_CHAT = '维护者已结束这段对话。'
NOTICE_REASONS = ('undelegated', 'superseded', 'closed', 'operator')
_NOTICES = {'undelegated': (UNDELEGATED, UNDELEGATED_CHAT), 'superseded': (SUPERSEDED, SUPERSEDED),
            'closed': (CLOSED, CLOSED_CHAT), 'operator': (OPERATOR, OPERATOR_CHAT)}

# What the new delegation session is told when it takes the card over, or waits for a claimed worker to stop.
SUPERSEDE_SUFFIX = '此前在另一个会话中的工作已转到这里继续。'
DEFER_ACK = '{bot} 已收到委派。这张卡上一次的 {skill} 工作正在保存进度并停止（最长约 {minutes} 分钟），停止后会在这里接着处理。'
DEFER_STILL = '上一次的工作还没有停止。稍后在这里回复任意内容即可开始。'
# A deferred delegation whose card was no longer delegated here when its turn came: it starts nothing.
DEFER_UNDELEGATED = '这张卡已不再委派给 {bot}，这次委派的工作不会开始。需要时重新委派给 {bot}，或在评论里 @{bot}。'

# Replies, forwarded messages and Stop while or after work is withdrawn.
RESUME_UNDELEGATED = '已保存回复；这张卡已不再委派给 {bot}，这项工作即将停止。重新委派给 {bot} 会从已有进度接着做。'
FORWARD_PARKED_UNDELEGATED = '已保存你的消息；这张卡已不再委派给 {bot}，{skill} 工作不会继续。重新委派后会读取这条消息。'
FORWARD_WITHDRAWING = '已保存你的消息；这张卡上的工作正在停止。'
STOP_ELSEWHERE = '已停止 {identifier} 上在另一个会话中进行的工作，worker 已终止，占用的资源在静默检查后释放。'
STOP_MOVED = '这里的工作已转到新的委派会话；要停止，请在那个会话里按 Stop。'
STOP_MOVED_THREAD = '这段对话已转到另一个讨论串继续；要停止，请在那个讨论串里按 Stop。'
STOP_ALREADY = '这里的工作已经停止。'

# Session heartbeats for a job that waits or stops for one of these reasons.
HEARTBEAT_PREDECESSOR = '工作已排队，正在等待上一次工作的清理完成。'
HEARTBEAT_STATUS_ERROR = '工作已保留；暂时读不到这张卡的状态，读取恢复后继续。'
HEARTBEAT_UNDELEGATED = '这张卡已不再委派，这项工作即将停止。'
HEARTBEAT_WITHDRAWING = '工作正在保存进度并停止。'

# The grace of a worker whose manifest is unknown: twice fix's renew interval.
DEFAULT_GRACE_SECONDS = 1200


def notice(skill, reason, bot):
    """The one response a job of `skill` gets when it is cancelled for `reason`, one of NOTICE_REASONS. A job that
    became unreachable gets none, so ValueError says there is no text for it."""
    if reason not in _NOTICES:
        raise ValueError(f'no notice for reason {reason!r}')
    write, chat = _NOTICES[reason]
    return (write if skill in WRITE_SKILLS else chat).format(bot=bot)


def grace_seconds(manifest):
    """How long a claimed worker has to save its progress and run `withdraw` before the controller stops it: twice
    its skill's renew interval, so a worker that renews on time has checked in at least once meanwhile."""
    if manifest is None:
        return DEFAULT_GRACE_SECONDS
    return 2 * manifest.budget['renew_minutes'] * 60
