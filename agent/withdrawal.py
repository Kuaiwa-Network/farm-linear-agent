"""The texts of withdrawn work and the grace a claimed worker has to stop (withdrawn-work design §7.1, P2:
docs/superpowers/specs/2026-09-29-withdrawn-work-design.md).

Work is withdrawn when the delegation that authorised it is gone, when a newer delegation session takes the card
over, or when the issue is out of reach. `{bot}` is the instance's configured Linear app name. A conversation's
text never mentions branches: a chat pushes none.

Also the last word a thread gets when its work is stopped or resumed from another thread, or ends after it asked
a question, and what a thread is told when a delegation came for which Linear opened no session (silent-delegation
design §4.1, §4.2, §7: docs/superpowers/specs/2026-09-30-silent-delegation-design.md).
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
# A Stop pressed in another thread ended the job: a thread that forwarded to it, the card's latest delegation thread,
# or a conversation's thread after its handover. The job's own thread gets this, so that it is not left waiting on a
# question nobody will answer there (silent-delegation design A1, P9). It is a notice, never a withdrawal reason.
STOPPED_ELSEWHERE = '这项工作已在另一个讨论串中按 Stop 停止。已推送的分支和草稿 PR 都保留。'
STOPPED_ELSEWHERE_CHAT = '这段对话已在另一个讨论串中按 Stop 结束。'
NOTICE_REASONS = ('undelegated', 'superseded', 'closed', 'operator', 'stopped_elsewhere')
_NOTICES = {'undelegated': (UNDELEGATED, UNDELEGATED_CHAT), 'superseded': (SUPERSEDED, SUPERSEDED),
            'closed': (CLOSED, CLOSED_CHAT), 'operator': (OPERATOR, OPERATOR_CHAT),
            'stopped_elsewhere': (STOPPED_ELSEWHERE, STOPPED_ELSEWHERE_CHAT)}

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
# What a job's own thread is told, as a thought, when a message in another thread resumed it: its last activity there
# was the question, which has now been answered elsewhere (silent-delegation design A2, A3).
RESUMED_ELSEWHERE = '已在另一个讨论串收到回复，这里的工作已恢复，会先读取那条回复。'
# The Stop's own thread, when the work it stopped lives in another. It claims no terminated worker: the job may
# have been queued or waiting, with none.
STOP_ELSEWHERE = '已停止 {identifier} 上在另一个会话中的工作，占用的资源在静默检查后释放。'
STOP_MOVED = '这里的工作已转到新的委派会话；要停止，请在那个会话里按 Stop。'
STOP_MOVED_THREAD = '这段对话已转到另一个讨论串继续；要停止，请在那个讨论串里按 Stop。'
STOP_ALREADY = '这里的工作已经停止。'
# The response that follows a question `await-input` posted for a job that ended before it could park: the question
# would otherwise be its thread's last activity, asking for an answer no job reads (silent-delegation design A6).
QUESTION_WITHDRAWN = '上面的问题不用再回答：这项工作已经停止。'
QUESTION_WITHDRAWN_CHAT = '上面的问题不用再回答：这段对话已经结束。'

# A delegation Linear opened no session for (silent-delegation design P12): a status read found the card delegated to
# this app again, and no delegation session followed within the grace. Each text says that Linear opened no session
# for the delegation and what to do next, and names no cause. That holds for a delegation one of FarmBot's waiting
# threads blocked and for one made through Linear's API. After a delivery that was lost, Linear did open a session,
# which FarmBot has not heard of (design R6).
# The first line of the thought in a delegation thread whose work the delegation takes over there; the new job's
# acknowledgement follows it.
IN_PLACE_NOTE = '这张卡已重新委派给 {bot}。Linear 没有为这次委派另开会话，{bot} 在这个会话里接着处理。'
# Delegation work of the kind the labels name is kept: a waiting job asks `{question}` again, as an elicitation, so
# its thread stays a waiting thread over waiting work; one that goes on says so in a thought.
REDELEGATED_WAITING = '这张卡已重新委派给 {bot}。这里的工作还在等你的回答：\n{question}'
REDELEGATED_RUNNING = '这张卡已重新委派给 {bot}，这里的工作正在进行，会继续。'
# Work the delegation cannot take over where it is: a response ends its thread's wait, which is what kept Linear from
# opening a session, and the job stays parked and answerable; a thought where the work goes on.
SILENT_WAITING = ('这张卡已委派给 {bot}，但 Linear 没有为这次委派打开会话（{bot} 的讨论串还在等回复时会这样）。'
                  '这里的等待先结束，这项工作仍然保留：在这里回复可以继续；要按卡片现在的标签重新开始，'
                  '请把代理改为「No agent」，再委派给 {bot}，会从已有进度接着做。')
SILENT_WAITING_CHAT = ('这张卡已委派给 {bot}，但 Linear 没有为这次委派打开会话（{bot} 的讨论串还在等回复时会这样）。'
                       '这里的等待先结束，对话仍然保留：在这里回复可以继续；要让 {bot} 按卡片的标签开始处理，'
                       '请把代理改为「No agent」，再委派给 {bot}，这段对话会转到新的会话里。')
SILENT_BUSY = ('这张卡已委派给 {bot}，但 Linear 没有为这次委派打开会话。这里的工作会继续；要按卡片现在的标签重新开始，'
               '请等这里结束或按 Stop 之后，把代理改为「No agent」，再委派给 {bot}。')
# A thread that holds no work and that Linear still shows as open: the response closes it.
SILENT_ENDED = ('这张卡已委派给 {bot}，但 Linear 没有为这次委派打开会话。这个讨论串里已经没有进行中的工作，'
                '{bot} 现在把它结束，好让新的委派能打开会话。要开始处理，请把代理改为「No agent」，再委派给 {bot}。')

# Session heartbeats for a job that waits or stops for one of these reasons.
HEARTBEAT_PREDECESSOR = '工作已排队，正在等待上一次工作的清理完成。'
HEARTBEAT_STATUS_ERROR = '工作已保留；暂时读不到这张卡的状态，读取恢复后继续。'
HEARTBEAT_UNDELEGATED = '这张卡已不再委派，这项工作即将停止。'
HEARTBEAT_WITHDRAWING = '工作正在保存进度并停止。'

# The grace of a worker whose manifest is unknown: twice fix's renew interval.
DEFAULT_GRACE_SECONDS = 1200
# How long a delegation that a read found back waits for the session Linear opens for it, counted from the start of
# that read, before FarmBot settles it without one (silent-delegation design P11, §3.3). Linear's `created` arrives
# within seconds, and a lost delivery was retried 66 seconds after the first.
SILENT_GRACE_SECONDS = 90
# A job gets at most one line that ends nothing per this many seconds for such delegations: the line kept work gets,
# or the thought of a busy thread. An automation that flaps the delegate then costs one line, not one per flap (§3.3).
RENOTE_SECONDS = 1800


def notice(skill, reason, bot):
    """The one response a job of `skill` gets when it is cancelled for `reason`, one of NOTICE_REASONS. A job that
    became unreachable gets none, so ValueError says there is no text for it."""
    if reason not in _NOTICES:
        raise ValueError(f'no notice for reason {reason!r}')
    write, chat = _NOTICES[reason]
    return (write if skill in WRITE_SKILLS else chat).format(bot=bot)


def question_withdrawn(skill):
    """The response that withdraws a question a job of `skill` asked and can no longer read an answer to."""
    return QUESTION_WITHDRAWN if skill in WRITE_SKILLS else QUESTION_WITHDRAWN_CHAT


def grace_seconds(manifest):
    """How long a claimed worker has to save its progress and run `withdraw` before the controller stops it: twice
    its skill's renew interval, so a worker that renews on time has checked in at least once meanwhile."""
    if manifest is None:
        return DEFAULT_GRACE_SECONDS
    return 2 * manifest.budget['renew_minutes'] * 60
