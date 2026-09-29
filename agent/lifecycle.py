"""Read current issue status, cancel closed work, and gate fresh launches."""
import time

from .ledger import TERMINAL_STATUS_TYPES
from .router import WRITE_SKILLS
# The one session response such a job gets; {bot} is the instance's configured Linear app name.
from .withdrawal import UNDELEGATED

# Delegation removal (spec §9.8, D16) cancels a job of a skill with an initial root only while no worker holds it:
# waiting for a launch, for a person or for a resource. A worker that has claimed it sees the change itself.
UNDELEGATED_STATES = ('queued', 'awaiting_input', 'awaiting_resource')


class Lifecycle:
    def __init__(self, ledger, api, scheduler, interval=60, clock=time.time):
        self.ledger, self.api, self.scheduler = ledger, api, scheduler
        self.interval, self.clock = interval, clock

    def refresh(self, issue_id):
        try:
            raw = self.api.issue_status(issue_id)
            if raw['id'] != issue_id:
                raise ValueError('status response identifies a different issue')
            current = self.ledger.apply_issue_status(raw)
            if current['archived'] or current['status_type'] in TERMINAL_STATUS_TYPES:
                for item in self.ledger.unfinished_for_issue(issue_id):
                    self.scheduler.stop(item['id'], 'Linear issue closed, cancelled or archived')
            elif self.api.app_user_id and current.get('delegate_id') != self.api.app_user_id:
                self._cancel_undelegated(issue_id)
            self.ledger.finish_status_check(issue_id, self.interval)
            return current
        except Exception as exc:
            self.ledger.finish_status_check(issue_id, self.interval, f'{type(exc).__name__}: {exc}'[:500])
            return None

    def _cancel_undelegated(self, issue_id):
        """The issue is no longer delegated to this app: its queued and waiting jobs of skills with an initial root
        end here, and their branches and PRs stay for whoever takes the card over (spec §9.8). fix and chat keep
        today's rule, under which losing the delegation only holds back their launches (preflight)."""
        notice = UNDELEGATED.format(bot=self.scheduler.bot_name)
        for item in self.ledger.unfinished_for_issue(issue_id):
            skill = self.scheduler.skills.get(item['skill'])
            if skill is None or skill.initial_root is None or item['state'] not in UNDELEGATED_STATES:
                continue
            self.scheduler.stop(item['id'], 'Linear delegation removed', states=UNDELEGATED_STATES, notice=notice)

    def tick(self):
        issue_id = self.ledger.due_issue()
        return {'checked': issue_id, 'ok': self.refresh(issue_id) is not None} if issue_id else {'checked': None}

    def preflight(self, item):
        check = self.ledger.status_check(item['issue_id'])
        if check and check['error'] and check['due_at'] > self.clock():
            return False
        current = self.refresh(item['issue_id'])
        return bool(current and not current['archived'] and current['status_type'] not in TERMINAL_STATUS_TYPES
                    and (item['skill'] not in WRITE_SKILLS or current.get('delegate_id') == self.api.app_user_id))
