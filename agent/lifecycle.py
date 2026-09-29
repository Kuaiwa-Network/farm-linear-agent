"""Read current issue status, end the work a closure or a withdrawn delegation ends, and gate fresh launches
(withdrawn-work design P2, P3: docs/superpowers/specs/2026-09-29-withdrawn-work-design.md)."""
import time

from .ledger import ACTIVE_STATES, TERMINAL_STATUS_TYPES, StaleRouting
from .linear_api import LinearError
# UNDELEGATED is the text notice() gives a write job whose delegation went, the one the lifecycle posted before; it
# stays importable here.
from .withdrawal import UNDELEGATED, grace_seconds, notice

# The states of a job no worker holds: waiting for a launch, for a person or for a resource. Withdrawal cancels such a
# job at once; a worker that has claimed one is told to save its progress and stop instead (design P2).
UNDELEGATED_STATES = ('queued', 'awaiting_input', 'awaiting_resource')
# An issue is out of reach once Linear has said it does not exist on this many reads in a row, over at least this
# long, while the host's other calls succeeded meanwhile (design R8, E3). Any other failure is transient.
UNREACHABLE_READS = 3
UNREACHABLE_SECONDS = 900
CLOSED_REASON = 'Linear issue closed, cancelled or archived'
WITHDRAWN_REASONS = {'undelegated': 'Linear delegation removed', 'unreachable': 'Linear issue not found'}


class Lifecycle:
    def __init__(self, ledger, api, scheduler, interval=60, clock=time.time):
        self.ledger, self.api, self.scheduler = ledger, api, scheduler
        self.interval, self.clock = interval, clock

    def refresh(self, issue_id):
        """Read the issue's status and act on it; the stored status, or None when the read failed."""
        read = self._refresh(issue_id)
        return read[0] if read else None

    def _refresh(self, issue_id):
        """(stored status, this read's delegate), or None when the read failed.

        A closed card ends its work. A card not delegated to this app is marked by the first read that finds it so,
        and the work its delegation authorised is withdrawn only by a later read, an interval or more after that one,
        that finds it so again; any read that finds the delegation clears the mark (design P3). This read's own
        delegate decides, never the stored snapshot, which an equal-version write may have left stale (design U4)."""
        started = self.clock()
        try:
            raw = self.api.issue_status(issue_id)
            if raw['id'] != issue_id:
                raise ValueError('status response identifies a different issue')
            current = self.ledger.apply_issue_status(raw)
            app, delegate = self.api.app_user_id, raw.get('delegate_id')
            if current['archived'] or current['status_type'] in TERMINAL_STATUS_TYPES:
                self._close(issue_id)
            elif not app or delegate != app:
                since = self.ledger.mark_undelegated(issue_id, started)
                # Without this app's identity nothing is known to be withdrawn: the mark only holds launches back.
                if app and started - since >= self.interval:
                    self._withdraw(issue_id, started, 'undelegated')
            else:
                self.ledger.clear_undelegated(issue_id)
            self.ledger.finish_status_check(issue_id, self.interval)
            return current, delegate
        except Exception as exc:
            unreachable = isinstance(exc, LinearError) and exc.kind == 'not_found'
            self.ledger.finish_status_check(issue_id, self.interval, f'{type(exc).__name__}: {exc}'[:500],
                                            unreachable=unreachable)
            if unreachable:
                self._unreachable(issue_id, started)
            return None

    def _close(self, issue_id):
        """The issue is closed, cancelled or archived: all its work ends. A job that was active gets one closing
        response in its session; a blocked one has reported already and ends silently (design E1)."""
        for item in self.ledger.unfinished_for_issue(issue_id):
            if item['state'] in ACTIVE_STATES:
                self.scheduler.stop(item['id'], CLOSED_REASON, states=ACTIVE_STATES,
                                    notice=notice(item['skill'], 'closed', self.scheduler.bot_name))
            else:
                self.scheduler.stop(item['id'], CLOSED_REASON)

    def _withdraw(self, issue_id, before, reason, everyone=False):
        """Withdraw the issue's work for `reason`, `undelegated` or `unreachable` (design P2): the delegation's work, or
        with `everyone` all of it. Work created at or after `before`, when the read that decided this began, is left
        alone: that read cannot have seen what authorised it (design P3, A11). A job no worker holds is cancelled, with
        one response unless the issue is out of reach; a claimed worker is flagged, to save its progress and run
        `withdraw` before its grace ends; a blocked job holds nothing and ends only with the issue."""
        now = self.clock()
        for item in self.ledger.unfinished_for_issue(issue_id):
            if item['created_at'] >= before or (not everyone and item['authority'] != 'delegation'):
                continue
            if item['state'] in UNDELEGATED_STATES:
                text = None if reason == 'unreachable' else notice(item['skill'], reason, self.scheduler.bot_name)
                self.scheduler.stop(item['id'], WITHDRAWN_REASONS[reason], states=UNDELEGATED_STATES, notice=text)
            elif item['state'] == 'running':
                deadline = now + grace_seconds(self.scheduler.skills.get(item['skill']))
                try:
                    self.ledger.flag_withdrawal(item['id'], reason, deadline)
                except StaleRouting:
                    pass  # its worker parked or ended since the listing; the next read decides again
            elif everyone:
                self.scheduler.stop(item['id'], WITHDRAWN_REASONS[reason])

    def _unreachable(self, issue_id, started):
        """A read found no such issue. Once enough such reads, over long enough, are not explained by an outage of
        Linear itself, every job on the issue is withdrawn silently: its sessions went with it (design R8)."""
        check = self.ledger.status_check(issue_id)
        since = check['unreachable_since'] if check else None
        if (since is not None and check['failures'] >= UNREACHABLE_READS
                and self.clock() - since >= UNREACHABLE_SECONDS
                and (getattr(self.api, 'last_success_at', None) or 0) > since):
            self._withdraw(issue_id, started, 'unreachable', everyone=True)

    def tick(self):
        issue_id = self.ledger.due_issue()
        return {'checked': issue_id, 'ok': self.refresh(issue_id) is not None} if issue_id else {'checked': None}

    def preflight(self, item):
        """Whether a queued job may launch: its issue is open, and a job the delegation authorised still has it. An
        issue whose reads fail, or which a read found not delegated, waits for its next due read rather than being
        read again for every launch attempt, which would spend Linear's rate limit (design G3)."""
        check = self.ledger.status_check(item['issue_id'])
        if check and check['due_at'] > self.clock() and (
                check['error'] or (check['undelegated_since'] is not None and item['authority'] == 'delegation')):
            return False
        read = self._refresh(item['issue_id'])
        if read is None:
            return False
        current, delegate = read
        if current['archived'] or current['status_type'] in TERMINAL_STATUS_TYPES:
            return False
        # A mention's or the operator's conversation needs no delegation; an unknown identity admits no one's.
        return item['authority'] != 'delegation' or bool(self.api.app_user_id and delegate == self.api.app_user_id)
