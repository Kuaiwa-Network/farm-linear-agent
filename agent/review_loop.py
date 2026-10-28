"""Prompt local review events without polling GitHub on every worker check."""
import time


class ReviewLoop:
    def __init__(self, controller, clock=time.time):
        self.controller, self.clock = controller, clock
        self.last_full = 0.0
        self.next_full = 0.0

    def step(self):
        controller = self.controller
        now = self.clock()
        handled = controller.process_workers()
        pending = False
        due = None
        if controller.policy['mode'] != 'off':
            pending = bool(controller.db.execute('SELECT 1 FROM review_deliveries d LEFT JOIN review_runs r '
                'ON r.item_id=d.item_id AND r.generation=d.generation WHERE r.id IS NULL LIMIT 1').fetchone())
            row = controller.db.execute("SELECT min(p.retry_at) FROM review_prs p JOIN review_runs r ON r.id=p.run_id "
                "WHERE r.state NOT IN ('delivered','superseded') AND p.state NOT IN ('merged','delivered') "
                "AND p.retry_at>?", (self.last_full,)).fetchone()
            due = row[0] if row else None
        if handled or pending or now >= self.next_full or (due is not None and now >= due):
            controller.tick()
            self.last_full = self.clock()
            self.next_full = self.last_full + controller.policy['poll_seconds']
        # Keep local teardown checks responsive. GitHub and Linear remain on
        # the full cadence unless a genuine local event needs a fresh check.
        now = self.clock()
        delay = min(5.0, max(0.1, self.next_full - now))
        if due is not None and due > now:
            delay = min(delay, due - now)
        return max(0.1, delay)
