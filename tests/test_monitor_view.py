"""The /api/status document: what it shows, what it never shows, and how it judges the service."""
from contextlib import closing, redirect_stdout
import io
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from agent.config import Config
from agent.heartbeat import Heartbeat, validate
from agent.ledger import Ledger
from agent.monitor_view import CLEANUP_GRACE, LOOP_LIMITS, RECENT_LIMIT, build_status
from agent.resource_recovery import MAX_REPAIR_ATTEMPTS, RecoveryStore
from agent.service import Components, serve
from test_ledger import PIN, comment, issue

NOW = 1_000_000.0
HEALTHY = {"ok": True, "status": 200, "latency_ms": 3, "error_type": None, "checked_at": NOW}
DOWN = {"ok": False, "status": None, "latency_ms": 2000, "error_type": "ConnectionRefusedError", "checked_at": NOW}
INSTANCE = {"environment": "production", "instance_id": "default", "bot_name": "FarmBot", "host": "test-host"}


def beat(phase="serving", written_at=NOW - 1, *, loops=None, rejected_at=None, rejected=0, stopped_at=None,
         workers=None):
    payload = Heartbeat(runtime="codex", revision=("a3f9f77c1d2e", False), clock=lambda: NOW - 100).payload()
    payload.update(phase=phase, written_at=written_at, stopped_at=stopped_at, loops=loops or {}, workers=workers)
    payload["webhooks"]["last_rejected_at"] = rejected_at
    payload["webhooks"]["counts"]["rejected"] = rejected
    return validate(payload)


def loop(started_at, finished_at, errors=0, error_type=None):
    return {"started_at": started_at, "finished_at": finished_at, "consecutive_errors": errors,
            "error_type": error_type, "error_at": finished_at if errors else None}


class ViewBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "state 状态" / "ledger.sqlite3"
        self.clock = NOW - 3600
        self.ledger = Ledger(self.path, clock=lambda: self.clock, lease_seconds=3600)
        self.addCleanup(self.ledger.close)
        self.count = 0

    def job(self, skill="fix", **changes):
        self.count += 1
        n = self.count
        issue_id = f"10000000-0000-4000-8000-{n:012d}"
        self.ledger.observe_issue(issue(**({"id": issue_id, "identifier": f"FARM-{n}", "title": f"标题 {n}",
                                            "url": f"https://linear.app/k/issue/FARM-{n}"} | changes)))
        self.ledger.ensure_session(f"session-{n}", issue_id, delegation=True)
        return self.ledger.create_work_item(issue_id=issue_id, session_id=f"session-{n}", skill=skill, target=PIN)

    def claim(self, item):
        self.ledger.set_worker(item["id"], 515151, "test-host")
        return self.ledger.claim(item["id"], worker_id="test")

    def sql(self, statement, *values):
        self.ledger.connection.execute(statement, values)

    def status(self, heartbeat=("missing", None), health=HEALTHY, failing_since=None, now=NOW, instance=INSTANCE):
        return build_status(self.path, heartbeat=heartbeat, health=health, instance=instance,
                            monitor_revision=("b1d5bd4a0c11", False), now=now, failing_since=failing_since,
                            renew_seconds={"fix": 600, "chat": 300})

    def active(self, document, identifier):
        return next(job for job in document["active"] if job["identifier"] == identifier)


class ActiveWorkTests(ViewBase):
    def test_running_and_waiting_jobs_show_identity_stage_and_timing(self):
        running, waiting = self.job(), self.job(skill="chat")
        self.clock = NOW - 1800
        claim = self.claim(running)
        self.sql("UPDATE work_items SET stage='verify', root_repo='farmgui' WHERE id=?", running["id"])
        self.clock = NOW - 1200
        answer = self.claim(waiting)
        self.clock = NOW - 900
        self.ledger.await_input(waiting["id"], answer["token"], "private question")
        self.clock = NOW - 600
        self.ledger.note(running["id"], "checkpoint", "saved")
        self.clock = NOW - 60
        self.ledger.renew(running["id"], claim["token"])  # moves updated_at; state_since must not follow it
        document = self.status()
        job = self.active(document, "FARM-1")
        self.assertEqual({key: job[key] for key in ("title", "url", "skill", "state", "display_state", "stage", "repo")},
                         {"title": "标题 1", "url": "https://linear.app/k/issue/FARM-1", "skill": "fix",
                          "state": "running", "display_state": "running", "stage": "verify", "repo": "farmgui"})
        self.assertEqual((job["state_since"], job["checkpoint_at"]), (NOW - 1800, NOW - 600))
        chat = self.active(document, "FARM-2")
        self.assertEqual((chat["display_state"], chat["state_since"], chat["checkpoint_at"]),
                         ("awaiting_input", NOW - 900, None))
        self.assertEqual(document["counts"], {"queued": 0, "running": 1, "awaiting_input": 1, "awaiting_resource": 0})

    def test_queued_work_is_split_into_launching_switching_retry_and_queue_position(self):
        first, second, launching, switching, retrying = (self.job() for _ in range(5))
        self.sql("UPDATE work_items SET priority=1 WHERE id=?", second["id"])
        self.ledger.set_worker(launching["id"], 5151, "test-host")
        self.sql("UPDATE work_items SET worker_pid=5252, next_root_repo='farm-hive' WHERE id=?", switching["id"])
        self.sql("UPDATE work_items SET retry_not_before=? WHERE id=?", NOW + 300, retrying["id"])
        states = {job["identifier"]: (job["display_state"], job["queue_position"], job["retry_at"])
                  for job in self.status()["active"]}
        self.assertEqual(states, {"FARM-1": ("queued", 2, None), "FARM-2": ("queued", 1, None),
                                  "FARM-3": ("launching", None, None), "FARM-4": ("switching_repo", None, None),
                                  "FARM-5": ("retry_wait", None, NOW + 300)})
        self.assertEqual(self.active(self.status(), "FARM-1")["state_since"], NOW - 3600)  # created, never requeued

    def test_unity_waits_show_their_reservation_queue_position_and_recovery(self):
        jobs = [self.job() for _ in range(3)]
        for job in jobs:
            self.ledger.await_resource(job["id"], self.claim(job)["token"], "unity_slot", "batch")
        self.sql("UPDATE work_items SET stage='waiting_for_recovery' WHERE id=?", jobs[2]["id"])
        document = self.status()
        states = {job["identifier"]: (job["display_state"], job["queue_position"]) for job in document["active"]}
        self.assertEqual(states, {"FARM-1": ("awaiting_resource", 1), "FARM-2": ("awaiting_resource", 2),
                                  "FARM-3": ("waiting_for_recovery", 3)})
        self.assertEqual(document["unity_queue"], 3)

    def test_only_linear_issue_links_and_github_pull_requests_are_emitted(self):
        safe, unsafe = self.job(), self.job(url="javascript:alert(1)")
        for url in ("https://github.com/Kuaiwa-Network/farmgui/pull/512", "https://evil.example/owner/repo/pull/1",
                    "https://github.com/Kuaiwa-Network/farmgui/pull/512?x=<script>"):
            self.sql("INSERT INTO published_prs(issue_id,url,generation,created_at) VALUES(?,?,0,?)",
                     safe["issue_id"], url, NOW)
        document = self.status()
        self.assertEqual(self.active(document, "FARM-1")["prs"],
                         [{"url": "https://github.com/Kuaiwa-Network/farmgui/pull/512", "label": "farmgui#512"}])
        self.assertIsNone(self.active(document, "FARM-2")["url"])

    def test_long_text_is_clamped(self):
        item = self.job(title="长" * 500)
        self.sql("UPDATE work_items SET stage=? WHERE id=?", "s" * 500, item["id"])
        job = self.status()["active"][0]
        self.assertEqual((len(job["title"]), len(job["stage"])), (200, 120))


class WorkerTests(ViewBase):
    def running(self, claimed_at, skill="fix"):
        item = self.job(skill=skill)
        self.clock = claimed_at
        return item, self.claim(item)

    def tracking(self, *items):
        return ("fresh", beat(workers={item["id"]: {"started_at": NOW - 700, "deadline": NOW + 20000} for item in items}))

    def test_a_tracked_worker_that_renews_on_time_is_alive(self):
        item, claim = self.running(NOW - 600)
        self.clock = NOW - 120
        self.ledger.renew(item["id"], claim["token"])
        worker = self.active(self.status(heartbeat=self.tracking(item)), "FARM-1")["worker"]
        self.assertEqual(worker, {"state": "alive", "tracked": True, "started_at": NOW - 700, "deadline": NOW + 20000,
                                  "renewed_at": NOW - 120, "lease_expires_at": NOW - 120 + 3600})

    def test_missed_renewals_are_flagged_long_before_the_lease_expires(self):
        overdue, _ = self.running(NOW - 1000)  # a fix worker renews every 600 s, and 1000 s is over 1.5 intervals
        on_time, claim = self.running(NOW - 1000)
        self.clock = NOW - 800
        self.ledger.renew(on_time["id"], claim["token"])
        document = self.status(heartbeat=self.tracking(overdue, on_time))
        self.assertEqual(self.active(document, "FARM-1")["worker"]["state"], "renewal_overdue")
        self.assertEqual(self.active(document, "FARM-2")["worker"]["state"], "alive")
        self.assertIn({"code": "renewal_overdue", "subject": "FARM-1", "since": NOW - 1000, "count": None},
                      document["attention"])

    def test_a_running_job_serve_is_not_managing_is_untracked(self):
        self.running(NOW - 600)
        self.running(NOW - 10)  # claimed 10 s ago, inside the 30 s grace that follows every claim
        document = self.status(heartbeat=("fresh", beat(workers={})))
        self.assertEqual(self.active(document, "FARM-1")["worker"]["state"], "untracked")
        self.assertEqual(self.active(document, "FARM-2")["worker"]["state"], "alive")
        self.assertIn(("worker_untracked", "FARM-1"), {(item["code"], item["subject"]) for item in document["attention"]})
        unknown = self.active(self.status(), "FARM-1")["worker"]  # no heartbeat: tracking cannot be checked
        self.assertEqual((unknown["state"], unknown["tracked"]), ("alive", None))

    def test_a_stopped_heartbeat_that_still_lists_the_worker_leaves_tracking_unknown(self):
        # serve's shutdown beat keeps listing the workers it left running; only a serving beat's list is trusted.
        item, claim = self.running(NOW - 600)
        self.clock = NOW - 120
        self.ledger.renew(item["id"], claim["token"])
        stopped = beat("stopped", NOW - 2, stopped_at=NOW - 2,
                       workers={item["id"]: {"started_at": NOW - 700, "deadline": NOW + 20000}})
        worker = self.active(self.status(heartbeat=("fresh", stopped)), "FARM-1")["worker"]
        self.assertEqual(worker, {"state": "alive", "tracked": None, "started_at": None, "deadline": None,
                                  "renewed_at": NOW - 120, "lease_expires_at": NOW - 120 + 3600})

    def test_a_retried_job_is_judged_from_its_new_claim_not_an_earlier_attempts_renewals(self):
        item, claim = self.running(NOW - 3000)
        self.clock = NOW - 2400
        self.ledger.renew(item["id"], claim["token"])  # the first attempt's last renewal
        self.clock = NOW - 2000
        self.ledger.fail(item["id"], "worker failed")
        self.ledger.record_cleanup(item["id"], {}, done=True)  # the scheduler finished the first cleanup
        self.ledger.retry(item["id"], "重试")
        self.clock = NOW - 60
        self.claim(item)  # the second attempt, which has not renewed yet
        document = self.status(heartbeat=self.tracking(item))
        worker = self.active(document, "FARM-1")["worker"]
        self.assertEqual((worker["state"], worker["renewed_at"]), ("alive", NOW - 60))
        self.assertNotIn("renewal_overdue", {entry["code"] for entry in document["attention"]})

    def test_an_expired_lease_outranks_the_other_worker_states(self):
        self.running(NOW - 4000)  # the one-hour lease ran out 400 s ago
        document = self.status(heartbeat=("fresh", beat(workers={})))
        self.assertEqual(self.active(document, "FARM-1")["worker"]["state"], "lease_expired")
        self.assertEqual([item["code"] for item in document["attention"] if item["subject"] == "FARM-1"],
                         ["lease_expired"])

    def test_jobs_that_are_not_running_have_no_worker(self):
        self.job()
        self.assertIsNone(self.status()["active"][0]["worker"])


class HistoryTests(ViewBase):
    def test_recent_work_is_the_newest_thirty_of_the_last_seven_days(self):
        items = [self.job() for _ in range(RECENT_LIMIT + 2)]
        for index, item in enumerate(items):
            self.sql("UPDATE work_items SET state='delivered', updated_at=? WHERE id=?", NOW - 3600 * (index + 1), item["id"])
        self.sql("UPDATE work_items SET updated_at=? WHERE id=?", NOW - 8 * 86400, items[0]["id"])
        recent = self.status()["recent"]
        self.assertEqual(len(recent), RECENT_LIMIT)
        self.assertEqual(recent[0]["identifier"], "FARM-2")
        self.assertNotIn("FARM-1", [job["identifier"] for job in recent])

    def test_outcomes_distinguish_no_change_and_mark_retried_work(self):
        delivered, unchanged, failed, cancelled, blocked = (self.job() for _ in range(5))
        for item, state, evidence in ((delivered, "delivered", '{"prs": ["https://github.com/o/r/pull/1"]}'),
                                      (unchanged, "delivered", '{"no_change": "already fixed"}'),
                                      (failed, "failed", "{}"), (cancelled, "cancelled", "{}"),
                                      (blocked, "blocked", "{}")):
            self.sql("UPDATE work_items SET state=?, evidence=?, updated_at=? WHERE id=?", state, evidence, NOW - 60, item["id"])
        successor = self.job()
        self.sql("UPDATE work_items SET predecessor_id=? WHERE id=?", failed["id"], successor["id"])
        recent = {job["identifier"]: (job["outcome"], job["retried"]) for job in self.status()["recent"]}
        self.assertEqual(recent, {"FARM-1": ("delivered", False), "FARM-2": ("no_change", False),
                                  "FARM-3": ("failed", True), "FARM-4": ("cancelled", False),
                                  "FARM-5": ("blocked", False)})


class SlotTests(ViewBase):
    def slot(self, slot_id):
        self.ledger.ensure_slot(slot_id, kind="unity_slot", host="test-host",
                                folder=str(Path(self.tmp.name) / slot_id.replace(":", "-")))

    def test_slots_show_their_holder_mode_commit_and_recovery(self):
        self.slot("unity_slot:1")
        self.slot("unity_slot:2")
        holder = self.job()
        self.ledger.await_resource(holder["id"], self.claim(holder)["token"], "unity_slot", "interactive")
        granted = self.ledger.acquire("unity_slot", owner="pool", host="test-host")
        self.ledger.set_slot_state(granted["resource"], "interactive_busy", parked_commit="9f2e1c0" + "0" * 33)
        self.ledger.set_slot_state("unity_slot:2", "held")
        store = RecoveryStore(self.ledger)
        store.discover("test-host")
        recovery = store.begin(store.pending("test-host")[0]["id"])
        store.failed(recovery["id"], recovery["attempts"], "private repair error")
        slots = {slot["slot_id"]: slot for slot in self.status()["slots"]}
        self.assertEqual(slots["unity_slot:1"], {"slot_id": "unity_slot:1", "kind": "unity_slot",
                                                 "state": "interactive_busy", "commit": "9f2e1c0",
                                                 "holder": "FARM-1", "mode": "interactive", "recovery": None})
        self.assertEqual(slots["unity_slot:2"]["recovery"],
                         {"state": "pending", "attempts": 1, "max_attempts": MAX_REPAIR_ATTEMPTS})

    def test_a_slot_reports_a_recovery_only_while_it_is_held(self):
        self.slot("unity_slot:1")
        self.ledger.set_slot_state("unity_slot:1", "held")
        store = RecoveryStore(self.ledger)
        store.discover("test-host")
        for _ in range(MAX_REPAIR_ATTEMPTS):  # every automatic repair fails, until recovery gives up
            pending = store.pending("test-host")[0]
            self.clock = max(self.clock, pending["due_at"])
            recovery = store.begin(pending["id"])
            store.failed(recovery["id"], recovery["attempts"], "private repair error")
        document = self.status()
        self.assertEqual(document["slots"][0]["recovery"],
                         {"state": "exhausted", "attempts": MAX_REPAIR_ATTEMPTS, "max_attempts": MAX_REPAIR_ATTEMPTS})
        self.assertIn(("slot_held", "unity_slot:1", MAX_REPAIR_ATTEMPTS),
                      {(item["code"], item["subject"], item["count"]) for item in document["attention"]})
        self.ledger.recover_slot("unity_slot:1", "operator repaired the Editor")  # the exhausted row stays
        slot = self.status()["slots"][0]
        self.assertEqual((slot["state"], slot["recovery"]), ("idle_closed", None))

    def test_a_slot_held_again_after_a_repair_shows_no_recovery_until_one_opens(self):
        self.slot("unity_slot:1")
        self.ledger.set_slot_state("unity_slot:1", "held")
        store = RecoveryStore(self.ledger)
        store.discover("test-host")
        recovery = store.begin(store.pending("test-host")[0]["id"])
        store.detach(recovery["id"], recovery["attempts"])
        store.complete(recovery["id"], recovery["attempts"], "9f2e1c0" + "0" * 33, "instance-1")
        self.assertIsNone(self.status()["slots"][0]["recovery"])  # repaired and idle_open
        self.ledger.set_slot_state("unity_slot:1", "held")  # SlotPool.park_idle holds a slot it cannot park
        self.assertIsNone(self.status()["slots"][0]["recovery"])  # the newest recovery is the repaired one
        store.discover("test-host")
        self.assertEqual(self.status()["slots"][0]["recovery"],
                         {"state": "pending", "attempts": 0, "max_attempts": MAX_REPAIR_ATTEMPTS})


class AttentionTests(ViewBase):
    def found(self, document):
        return {(item["code"], item["subject"]) for item in document["attention"]}

    def test_ledger_conditions_raise_attention_only_past_their_thresholds(self):
        cleaned, fresh_cleanup, flaky, broken, expired = (self.job() for _ in range(5))
        # Cleanup is overdue only for a job that has finished, timed from when it finished; both must have
        # finished, or the grace goes untested. Both rows were rewritten just now, as a failing _retire leaves them.
        for item, finished_at in ((cleaned, NOW - CLEANUP_GRACE - 1), (fresh_cleanup, NOW - CLEANUP_GRACE + 30)):
            self.sql("UPDATE work_items SET state='failed', updated_at=? WHERE id=?", finished_at, item["id"])
            self.sql("INSERT INTO job_cleanup(item_id, worker_pid, updated_at) VALUES(?,?,?)", item["id"], 1, NOW - 1)
        self.sql("UPDATE issue_checks SET error='private', failures=2, checked_at=? WHERE issue_id=?", NOW - 5, flaky["issue_id"])
        self.sql("UPDATE issue_checks SET error='private', failures=3, checked_at=? WHERE issue_id=?", NOW - 5, broken["issue_id"])
        self.clock = NOW - 4000  # with a one-hour lease, this claim expired 400 seconds ago
        self.claim(expired)
        document = self.status()
        self.assertEqual(self.found(document), {("cleanup_pending", "FARM-1"), ("issue_status_error", "FARM-4"),
                                                ("lease_expired", "FARM-5")})
        self.assertEqual(document["verdict"], "attention")

    def test_cleanup_is_pending_only_for_a_job_that_has_finished(self):
        # retry() and set_worker reopen a finished job's cleanup row for its next attempt without moving
        # updated_at, so a job that is running again is not a stuck cleanup however old the row looks.
        retried, inserted, cancelled = (self.job() for _ in range(3))
        self.claim(retried)
        self.ledger.fail(retried["id"], "worker failed")
        self.ledger.record_cleanup(retried["id"], {}, done=True)  # the scheduler finished the first cleanup
        self.ledger.cancel(cancelled["id"], "Linear stop")  # opens a cleanup row the scheduler has not finished
        self.clock = NOW - 60
        self.ledger.retry(retried["id"], "重试")
        self.claim(retried)
        self.claim(inserted)
        self.sql("INSERT INTO job_cleanup(item_id, worker_pid, updated_at) VALUES(?,?,?)",
                 inserted["id"], 1, NOW - CLEANUP_GRACE - 1)
        record = self.ledger.cleanup_record(retried["id"])
        self.assertEqual((record["done"], record["updated_at"]), (False, NOW - 3600))  # reopened, still dated
        self.assertEqual([item for item in self.status()["attention"] if item["code"] == "cleanup_pending"],
                         [{"code": "cleanup_pending", "subject": "FARM-3", "since": NOW - 3600, "count": None}])

    def cleanup_items(self, document):
        return [item for item in document["attention"] if item["code"] == "cleanup_pending"]

    def test_a_cleanup_that_keeps_failing_is_flagged_from_when_the_job_finished(self):
        # Scheduler._retire records each failed attempt, rewriting the row's updated_at about once a second.
        item = self.job()
        self.clock = NOW - 1260
        self.claim(item)
        self.clock = NOW - 1200
        self.ledger.fail(item["id"], "worker failed")
        for attempt in range(20):  # one failed cleanup a minute for 20 minutes
            self.clock = NOW - 1200 + 60 * attempt
            self.ledger.record_cleanup(item["id"], {}, error="reservation awaits quiescence")
        record = self.ledger.cleanup_record(item["id"])
        self.assertEqual((record["done"], record["updated_at"]), (False, NOW - 60))  # the row looks recent
        document = self.status()
        self.assertEqual(self.cleanup_items(document),
                         [{"code": "cleanup_pending", "subject": "FARM-1", "since": NOW - 1200, "count": None}])
        self.assertEqual((document["verdict"], document["verdict_since"]), ("attention", NOW - 1200))

    def test_a_retried_job_that_has_just_failed_again_is_not_yet_overdue(self):
        # retry() and set_worker reopen the first attempt's row without moving its updated_at.
        item = self.job()
        self.claim(item)
        self.clock = NOW - 3000
        self.ledger.fail(item["id"], "worker failed")
        self.ledger.record_cleanup(item["id"], {}, done=True)  # the scheduler finished the first cleanup
        self.clock = NOW - 1800
        self.ledger.retry(item["id"], "重试")
        self.claim(item)
        self.clock = NOW - 1
        self.ledger.fail(item["id"], "worker failed again")  # the next _retire has not run yet
        record = self.ledger.cleanup_record(item["id"])
        self.assertEqual((record["done"], record["updated_at"]), (False, NOW - 3000))  # older than the grace
        self.assertEqual(self.ledger.item(item["id"])["state"], "failed")
        document = self.status()
        self.assertEqual(self.cleanup_items(document), [])
        self.assertEqual(document["verdict"], "ok")

    def test_cancelling_a_blocked_job_restarts_its_cleanup_grace_once(self):
        # Accepted: cancel() turns a blocked job into a cancelled one and so moves its finish time, once.
        item = self.job()
        token = self.claim(item)["token"]
        action = self.ledger.prepare_comment(item["id"], token, "blocker", "请补充复现步骤。")
        self.ledger.confirm_comment(action["action_id"], "remote-comment-1")
        self.clock = NOW - 1200
        self.ledger.finish(item["id"], token, "blocked", {"summary": "需要设备信息",
                                                          "comment_action_id": action["action_id"]})
        for attempt in range(20):  # one failed cleanup a minute for 20 minutes
            self.clock = NOW - 1200 + 60 * attempt
            self.ledger.record_cleanup(item["id"], {}, error="reservation awaits quiescence")
        self.assertEqual(self.cleanup_items(self.status()),
                         [{"code": "cleanup_pending", "subject": "FARM-1", "since": NOW - 1200, "count": None}])
        self.clock = NOW
        self.ledger.cancel(item["id"], "Linear stop")
        self.assertEqual(self.ledger.item(item["id"])["state"], "cancelled")
        flagged = [{"code": "cleanup_pending", "subject": "FARM-1", "since": NOW, "count": None}]
        for later, expected in ((1, []), (CLEANUP_GRACE - 1, []), (CLEANUP_GRACE, flagged)):
            with self.subTest(seconds_after_cancel=later):
                self.assertEqual(self.cleanup_items(self.status(now=NOW + later)), expected)

    def test_a_busy_slot_without_a_reservation_and_a_slow_cancellation_are_flagged(self):
        for slot in ("unity_slot:1", "unity_slot:2"):
            self.ledger.ensure_slot(slot, kind="unity_slot", host="test-host",
                                    folder=str(Path(self.tmp.name) / slot.replace(":", "-")))
        self.ledger.set_slot_state("unity_slot:1", "batch_busy")
        item = self.job()
        self.ledger.await_resource(item["id"], self.claim(item)["token"], "unity_slot", "batch")
        self.ledger.acquire("unity_slot", owner="pool", host="test-host")  # slot 1 is busy, so slot 2
        self.clock = NOW - 400
        self.ledger.cancel(item["id"], "Linear stop")  # the active reservation becomes cancel_requested
        found = self.found(self.status())
        self.assertIn(("slot_without_reservation", "unity_slot:1"), found)
        self.assertIn(("reservation_cancel_pending", "FARM-1"), found)
        self.assertNotIn(("slot_without_reservation", "unity_slot:2"), found)

    def test_a_cancellation_is_flagged_only_five_minutes_after_its_job_was_cancelled(self):
        self.ledger.ensure_slot("unity_slot:1", kind="unity_slot", host="test-host",
                                folder=str(Path(self.tmp.name) / "unity_slot-1"))
        item = self.job()
        self.ledger.await_resource(item["id"], self.claim(item)["token"], "unity_slot", "batch")
        self.ledger.acquire("unity_slot", owner="pool", host="test-host")
        self.clock = NOW
        self.ledger.cancel(item["id"], "Linear stop")  # the active reservation becomes cancel_requested
        flagged = [{"code": "reservation_cancel_pending", "subject": "FARM-1", "since": NOW, "count": None}]
        for later, expected in ((1, []), (299, []), (300, flagged)):  # the spec's "5 minutes or more"
            with self.subTest(seconds_after_cancel=later):
                attention = self.status(now=NOW + later)["attention"]
                self.assertEqual([entry for entry in attention if entry["code"] == "reservation_cancel_pending"],
                                 expected)


class ServiceTests(ViewBase):
    def test_the_verdict_follows_the_spec_table(self):
        self.job()
        cases = [
            ("stopped", ("stale", beat("stopped", NOW - 300, stopped_at=NOW - 300)), DOWN, NOW - 300),
            ("starting", ("fresh", beat("starting")), DOWN, NOW - 100),
            ("unresponsive", ("stale", beat("serving", NOW - 120)), DOWN, NOW - 120),
            ("unresponsive", ("missing", None), DOWN, NOW - 30),
            ("unresponsive", ("unreadable", None), DOWN, NOW - 30),
            ("attention", ("fresh", beat("serving")), DOWN, NOW - 30),
            ("attention", ("stale", beat("serving", NOW - 120)), HEALTHY, NOW - 120),
            ("attention", ("fresh", beat("stopped", NOW - 2, stopped_at=NOW - 2)), HEALTHY, NOW - 2),
            ("attention", ("unreadable", None), HEALTHY, None),
            ("ok", ("fresh", beat("serving")), HEALTHY, None),
            ("ok", ("missing", None), HEALTHY, None),
        ]
        for verdict, heartbeat, health, since in cases:
            with self.subTest(verdict=verdict, heartbeat=heartbeat[0], health=health["ok"]):
                document = self.status(heartbeat=heartbeat, health=health, failing_since=NOW - 30)
                self.assertEqual((document["verdict"], document["verdict_since"]), (verdict, since))

    def test_rules_five_and_six_keep_their_own_time_beside_an_older_item(self):
        item = self.job()
        self.clock = NOW - 7200
        self.claim(item)  # the one-hour lease expired an hour ago
        cases = [
            ("rule 5", ("fresh", beat("serving")), DOWN, "receiver_unreachable", NOW - 30),
            ("rule 6, stale", ("stale", beat("serving", NOW - 120)), HEALTHY, "heartbeat_stale", NOW - 120),
            ("rule 6, stopped", ("fresh", beat("stopped", NOW - 2, stopped_at=NOW - 2)), HEALTHY, "heartbeat_stale",
             NOW - 2),
            ("rule 6, unreadable", ("unreadable", None), HEALTHY, "heartbeat_unreadable", None),
            ("rule 7", ("fresh", beat("serving")), HEALTHY, None, NOW - 3600),
        ]
        for rule, heartbeat, health, code, since in cases:
            with self.subTest(rule=rule):
                document = self.status(heartbeat=heartbeat, health=health, failing_since=NOW - 30)
                codes = {entry["code"]: entry["since"] for entry in document["attention"]}
                self.assertEqual(codes["lease_expired"], NOW - 3600)
                if code:
                    self.assertIn(code, codes)
                self.assertEqual((document["verdict"], document["verdict_since"]), ("attention", since))

    def test_health_and_instance_are_built_from_their_own_fields(self):
        health = {**DOWN, "error_type": "<script>", "body": "PRIVATE-BODY", "url": "http://127.0.0.1:8787/health"}
        instance = {**INSTANCE, "bot_name": "B" * 100, "token": "PRIVATE-TOKEN", "local_root": "/srv/private"}
        document = self.status(heartbeat=("fresh", beat()), health=health, instance=instance)
        self.assertEqual(document["service"]["health"], {"ok": False, "status": None, "latency_ms": 2000,
                                                         "error_type": "Error", "checked_at": NOW})
        self.assertEqual(document["instance"], {"environment": "production", "instance_id": "default",
                                                "bot_name": "B" * 64, "host": "test-host"})
        self.assertEqual(document["verdict"], "attention")  # the verdict still reads the probe's ok
        self.assertIn("receiver_unreachable", {entry["code"] for entry in document["attention"]})
        text = json.dumps(document, ensure_ascii=False)
        for secret in ("PRIVATE-BODY", "127.0.0.1:8787", "PRIVATE-TOKEN", "/srv/private", "<script>"):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, text)
        for error_type, shown in (("Connection Error", "Error"), (42, "Error"), ("", "Error"),
                                  ("ConnectionRefusedError", "ConnectionRefusedError"), (None, None)):
            with self.subTest(error_type=error_type):
                shown_type = self.status(health={**DOWN, "error_type": error_type})["service"]["health"]["error_type"]
                self.assertEqual(shown_type, shown)
        bare = self.status(health={"ok": True}, instance={})
        self.assertEqual(bare["service"]["health"], {"ok": True, "status": None, "latency_ms": None,
                                                     "error_type": None, "checked_at": None})
        self.assertEqual(bare["instance"], dict.fromkeys(("environment", "instance_id", "bot_name", "host")))
        self.assertEqual(bare["verdict"], "ok")

    def test_loops_and_webhooks_raise_attention_from_a_fresh_heartbeat(self):
        loops = {"receive": loop(NOW - 2, NOW - 1),
                 "schedule": loop(NOW - LOOP_LIMITS["schedule"] - 5, NOW - LOOP_LIMITS["schedule"] - 10),
                 "pool": loop(NOW - 600, NOW - 700),
                 "lifecycle": loop(NOW - 5, NOW - 4, errors=3, error_type="TimeoutError")}
        document = self.status(heartbeat=("fresh", beat(loops=loops, rejected_at=NOW - 60, rejected=4)))
        self.assertEqual({entry["name"]: entry["state"] for entry in document["service"]["loops"]},
                         {"receive": "idle", "schedule": "stalled", "pool": "busy", "lifecycle": "erroring"})
        self.assertEqual({(item["code"], item["subject"], item["count"]) for item in document["attention"]},
                         {("loop_stalled", "schedule", None), ("loop_erroring", "lifecycle", 3),
                          ("webhook_rejected", None, 4)})
        self.assertEqual(self.status(heartbeat=("fresh", beat(rejected_at=NOW - 901, rejected=4)))["attention"], [])

    def test_only_a_live_heartbeat_judges_its_loops(self):
        # As the beat recorded them: a loop idle, one busy, one busy past its limit and one erroring.
        written = NOW - 600
        loops = {"receive": loop(written - 2, written - 1), "schedule": loop(written - 30, written - 40),
                 "pool": loop(written - LOOP_LIMITS["pool"] - 60, written - LOOP_LIMITS["pool"] - 70),
                 "lifecycle": loop(written - 5, written - 4, errors=3, error_type="TimeoutError")}
        judged = {"receive": "idle", "schedule": "busy", "pool": "stalled", "lifecycle": "erroring"}
        cases = [("fresh", "serving", judged, {"loop_stalled", "loop_erroring"}),
                 ("stale", "serving", dict.fromkeys(judged), set()),
                 ("fresh", "stopped", dict.fromkeys(judged), set()),
                 ("stale", "stopped", dict.fromkeys(judged), set())]
        for state, phase, states, codes in cases:
            with self.subTest(state=state, phase=phase):
                last = beat(phase, written, loops=loops, stopped_at=written if phase == "stopped" else None)
                # A fresh beat is read within seconds of its writing, a stale one ten minutes later.
                document = self.status(heartbeat=(state, last), now=written + 1 if state == "fresh" else NOW)
                shown = {entry.pop("name"): entry for entry in document["service"]["loops"]}
                self.assertEqual({name: entry.pop("state") for name, entry in shown.items()}, states)
                self.assertEqual(shown, loops)  # every loop keeps the times and errors its beat recorded
                raised = {entry["code"] for entry in document["attention"]}
                self.assertEqual(raised & {"loop_stalled", "loop_erroring"}, codes)

    def test_a_stale_heartbeat_raises_no_loop_or_webhook_item(self):
        # A dead serve's last beat: a loop busy past its limit, another erroring and a webhook just rejected.
        written = NOW - 120
        loops = {"schedule": loop(written - LOOP_LIMITS["schedule"] - 60, written - LOOP_LIMITS["schedule"] - 70),
                 "lifecycle": loop(written - 5, written - 4, errors=5, error_type="TimeoutError")}
        last = beat("serving", written, loops=loops, rejected_at=written - 30, rejected=2)
        # Read while fresh, the same beat raises all three, so the fixture holds what a stale beat must not raise.
        fresh = self.status(heartbeat=("fresh", last), now=written + 1)
        self.assertEqual({entry["code"] for entry in fresh["attention"]},
                         {"loop_stalled", "loop_erroring", "webhook_rejected"})
        for health, codes in ((HEALTHY, ["heartbeat_stale"]), (DOWN, [])):
            with self.subTest(health=health["ok"]):
                document = self.status(heartbeat=("stale", last), health=health, failing_since=NOW - 30)
                self.assertEqual([entry["code"] for entry in document["attention"]], codes)

    def test_a_loop_pausing_longer_than_its_limit_reads_idle(self):
        # While serve drains at shutdown it keeps writing fresh serving beats, and a loop whose last iteration has
        # finished pauses for good. However long ago that iteration started, the loop is idle, never stalled.
        started = NOW - LOOP_LIMITS["receive"] - 60
        document = self.status(heartbeat=("fresh", beat(loops={"receive": loop(started, started + 1)})))
        self.assertEqual([(entry["name"], entry["state"]) for entry in document["service"]["loops"]],
                         [("receive", "idle")])
        self.assertEqual(document["attention"], [])

    def test_two_consecutive_loop_errors_are_not_yet_erroring(self):
        for errors, state, codes in ((2, "idle", []), (3, "erroring", ["loop_erroring"])):
            with self.subTest(errors=errors):
                loops = {"lifecycle": loop(NOW - 5, NOW - 4, errors=errors, error_type="TimeoutError")}
                document = self.status(heartbeat=("fresh", beat(loops=loops)))
                self.assertEqual([entry["state"] for entry in document["service"]["loops"]], [state])
                self.assertEqual([entry["code"] for entry in document["attention"]], codes)

    def test_the_document_reports_the_monitors_own_revision_beside_serves(self):
        document = self.status(heartbeat=("fresh", beat()))
        self.assertEqual(document["monitor"], {"revision": "b1d5bd4a0c11", "dirty": False})
        self.assertEqual(document["service"]["heartbeat"]["revision"], "a3f9f77c1d2e")  # serve's, from its beat
        for revision in (("b1d5bd4a0c11", True), (None, None)):
            with self.subTest(revision=revision):
                built = build_status(self.path, heartbeat=("missing", None), health=HEALTHY, instance=INSTANCE,
                                     monitor_revision=revision, now=NOW)
                self.assertEqual(built["monitor"], {"revision": revision[0], "dirty": revision[1]})

    def test_linear_and_agent_session_times_come_from_the_ledger(self):
        first, second = self.job(), self.job()
        self.sql("UPDATE issue_checks SET checked_at=? WHERE issue_id=?", NOW - 30, first["issue_id"])
        self.sql("UPDATE issue_checks SET checked_at=?, error='private', failures=1 WHERE issue_id=?", NOW - 5, second["issue_id"])
        self.sql("CREATE TABLE webhook_events (event_key TEXT PRIMARY KEY, session_id TEXT NOT NULL, ack_id TEXT NOT NULL, "
                 "status TEXT NOT NULL, payload TEXT, received_at REAL NOT NULL, completed_at REAL, error TEXT)")
        self.sql("INSERT INTO webhook_events VALUES('k','s','a','done',NULL,?,NULL,NULL)", NOW - 840)
        service = self.status()["service"]
        self.assertEqual(service["linear"], {"last_ok_at": NOW - 30, "failing_issues": 1})
        self.assertEqual(service["agent_event_at"], NOW - 840)


class ServeLoopTests(ViewBase):
    """serve's own heartbeat read into the document, on stand-in components with no listening socket."""

    def test_serve_times_a_loops_work_and_not_the_pause_after_it(self):
        # Resource recovery pauses 15 s after each tick. A beat written during that pause reads idle: a loop that
        # sleeps between ticks is not busy.
        ticked, released = threading.Event(), threading.Event()
        self.addCleanup(released.set)
        receiver, server, launcher = Mock(), Mock(), Mock()
        receiver.process_one.return_value = False
        server.server_address = ("127.0.0.1", 1234)
        server.serve_forever.side_effect = lambda: released.wait(20)  # returns, as server.shutdown() makes it
        launcher.runtime.name = "fake"
        launcher.running.return_value = {}
        config = Config("client", "secret", "signing", host="test", runtime="fake",
                        local_root=Path(self.tmp.name) / "serve 状态")
        components = Components(config, None, None, Mock(), {"chat"}, None, launcher, Mock(), receiver, server,
                                SimpleNamespace(ensure=lambda: None, tick=lambda: None, close=lambda: None),
                                recovery=SimpleNamespace(tick=ticked.set, close=lambda: None))
        problems = []

        def run():
            try:
                with redirect_stdout(io.StringIO()):
                    serve(components=components)
            except BaseException as exc:
                problems.append(exc)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 20)
        self.assertTrue(ticked.wait(20), "serve never ran the resource recovery loop")
        deadline = time.monotonic() + 5  # the tick is under way, and serve records its end before the pause
        while (server.heartbeat.payload()["loops"]["resource_recovery"]["finished_at"] is None
               and time.monotonic() < deadline):
            time.sleep(0.01)
        pausing = validate(json.loads(json.dumps(server.heartbeat.payload())))
        released.set()
        thread.join(timeout=20)
        self.assertFalse(thread.is_alive())
        self.assertEqual(problems, [])
        document = self.status(heartbeat=("fresh", pausing), now=pausing["written_at"])
        loops = {entry["name"]: entry["state"] for entry in document["service"]["loops"]}
        self.assertEqual(loops["resource_recovery"], "idle")


class OlderLedgerTests(unittest.TestCase):
    def build(self, path):
        return build_status(path, heartbeat=("missing", None), health=HEALTHY, instance=INSTANCE,
                            monitor_revision=(None, False), now=NOW)

    def test_a_ledger_with_only_the_required_tables_still_lists_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old ledger.sqlite3"
            with closing(sqlite3.connect(path)) as db:
                db.executescript("""
                    CREATE TABLE issues (id TEXT PRIMARY KEY, metadata TEXT NOT NULL);
                    CREATE TABLE work_items (id TEXT PRIMARY KEY, issue_id TEXT, skill TEXT, state TEXT, stage TEXT,
                                             created_at REAL, updated_at REAL);""")
                db.execute("INSERT INTO issues VALUES(?,?)", ("issue-1", json.dumps(
                    {"identifier": "FARM-9", "title": "旧账本", "url": "https://linear.app/k/issue/FARM-9"})))
                db.execute("INSERT INTO work_items VALUES('item-1','issue-1','fix','running','intake',?,?)", (NOW - 60, NOW - 60))
                db.commit()
            document = self.build(path)
        self.assertTrue(document["ledger"]["ok"])
        self.assertEqual([job["identifier"] for job in document["active"]], ["FARM-9"])
        self.assertIsNone(document["slots"])
        self.assertIsNone(document["unity_queue"])
        self.assertEqual(set(document["ledger"]["missing_optional"]),
                         {"audit", "published_prs", "slots", "reservations", "resource_recoveries", "job_cleanup",
                          "issue_checks", "webhook_events"})
        self.assertEqual(document["verdict"], "ok")

    def test_optional_tables_without_their_optional_columns_still_fill_their_sections(self):
        # slots without parked_commit or updated_at, and published_prs without created_at.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old ledger.sqlite3"
            with closing(sqlite3.connect(path)) as db:
                db.executescript("""
                    CREATE TABLE issues (id TEXT PRIMARY KEY, metadata TEXT NOT NULL);
                    CREATE TABLE work_items (id TEXT PRIMARY KEY, issue_id TEXT, skill TEXT, state TEXT, stage TEXT,
                                             created_at REAL, updated_at REAL);
                    CREATE TABLE slots (slot_id TEXT PRIMARY KEY, kind TEXT, state TEXT);
                    CREATE TABLE published_prs (issue_id TEXT, url TEXT);""")
                db.execute("INSERT INTO issues VALUES(?,?)", ("issue-1", json.dumps(
                    {"identifier": "FARM-9", "title": "旧账本", "url": "https://linear.app/k/issue/FARM-9"})))
                db.execute("INSERT INTO work_items VALUES('item-1','issue-1','fix','running','intake',?,?)", (NOW - 60, NOW - 60))
                db.executemany("INSERT INTO slots VALUES(?,?,?)",
                               [("unity_slot:1", "unity_slot", "idle_open"), ("unity_slot:2", "unity_slot", "held")])
                db.executemany("INSERT INTO published_prs VALUES(?,?)",
                               [("issue-1", "https://github.com/Kuaiwa-Network/farmgui/pull/9"),
                                ("issue-1", "https://github.com/Kuaiwa-Network/common/pull/3")])
                db.commit()
            document = self.build(path)
        self.assertTrue(document["ledger"]["ok"])
        free = {"commit": None, "holder": None, "mode": None, "recovery": None}
        self.assertEqual(document["slots"],
                         [{"slot_id": "unity_slot:1", "kind": "unity_slot", "state": "idle_open", **free},
                          {"slot_id": "unity_slot:2", "kind": "unity_slot", "state": "held", **free}])
        self.assertEqual(document["attention"],
                         [{"code": "slot_held", "subject": "unity_slot:2", "since": None, "count": None}])
        # Without created_at, a job's PRs come in URL order.
        self.assertEqual(document["active"][0]["prs"],
                         [{"url": "https://github.com/Kuaiwa-Network/common/pull/3", "label": "common#3"},
                          {"url": "https://github.com/Kuaiwa-Network/farmgui/pull/9", "label": "farmgui#9"}])
        self.assertTrue({"slots", "published_prs"}.isdisjoint(document["ledger"]["missing_optional"]))

    def test_a_missing_ledger_or_required_table_is_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.sqlite3"
            missing = self.build(path)
            self.assertFalse(path.exists())
            with closing(sqlite3.connect(path)) as db:
                db.execute("CREATE TABLE issues (id TEXT PRIMARY KEY, metadata TEXT NOT NULL)")
                db.commit()
            partial = self.build(path)
        self.assertEqual((missing["verdict"], missing["ledger"]["error_type"]), ("unknown", "OperationalError"))
        self.assertEqual((partial["verdict"], partial["ledger"]["error_type"]), ("unknown", "SchemaMismatch"))


class PrivacyTests(ViewBase):
    def test_private_ledger_content_never_reaches_the_document(self):
        item = self.job(description="PRIVATE-DESCRIPTION", comments=[comment(body="PRIVATE-COMMENT")])
        claim = self.claim(item)
        self.ledger.push_inbox(item["id"], "PRIVATE-INBOX")
        self.sql("UPDATE work_items SET checkpoint=?, evidence=? WHERE id=?", json.dumps({"progress": "PRIVATE-CHECKPOINT"}),
                 json.dumps({"summary": "PRIVATE-EVIDENCE"}), item["id"])
        self.sql("UPDATE issue_checks SET error=?, failures=9, checked_at=? WHERE issue_id=?",
                 "PRIVATE-ERROR /srv/private/secret", NOW, item["issue_id"])
        finished = self.job()  # cleanup_pending reads only a finished job's row
        self.sql("UPDATE work_items SET state='failed' WHERE id=?", finished["id"])
        self.sql("INSERT INTO job_cleanup(item_id, worker_pid, error, updated_at) VALUES(?,?,?,?)",
                 finished["id"], 424242, "PRIVATE-CLEANUP", NOW - 3600)
        self.ledger.ensure_slot("unity_slot:1", kind="unity_slot", host="test-host",
                                folder=str(Path(self.tmp.name) / "PRIVATE-FOLDER"))
        waiting = self.job()
        answer = self.claim(waiting)
        self.ledger.await_input(waiting["id"], answer["token"], "PRIVATE-QUESTION")
        token_hash = self.ledger.connection.execute("SELECT token FROM work_items WHERE id=?", (item["id"],)).fetchone()[0]
        document = self.status(heartbeat=("fresh", beat()))
        self.assertIn("cleanup_pending", {entry["code"] for entry in document["attention"]})  # the private row is read
        text = json.dumps(document, ensure_ascii=False)
        for secret in ("PRIVATE-DESCRIPTION", "PRIVATE-COMMENT", "PRIVATE-INBOX", "PRIVATE-CHECKPOINT",
                       "PRIVATE-EVIDENCE", "PRIVATE-ERROR", "/srv/private", "PRIVATE-CLEANUP", "PRIVATE-FOLDER",
                       "PRIVATE-QUESTION", "424242", "515151", claim["token"], token_hash, answer["token"], "session-1"):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, text)

    def test_building_the_document_never_modifies_the_ledger(self):
        self.job()
        self.ledger.close()  # no writer, as when serve is stopped: the WAL files have gone
        before = (self.path.stat().st_mtime_ns, self.path.read_bytes())
        self.status(heartbeat=("fresh", beat()))
        self.assertEqual((self.path.stat().st_mtime_ns, self.path.read_bytes()), before)
        self.assertFalse(self.path.with_name(self.path.name + "-journal").exists())
