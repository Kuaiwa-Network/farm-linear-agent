"""Scope-based admission, restart compatibility and public wait diagnostics without live services."""
import json
import unittest
from unittest.mock import patch

from agent.server_test_plan import FULL_PLAN, profile, test_argv, validate_plan
from agent.resource_admission import validate_settings
from agent.server_slots import read_resource
from test_server_slots import ServerPoolFixture

FOCUSED = {"packages": ["./modules/animal"], "tests": ["TestSaveAnimal"], "race": False}
TARGETED = {**FOCUSED, "tests": []}


class PlanTests(unittest.TestCase):
    def test_host_derives_tier_and_exact_argv_without_accepting_cost_labels(self):
        self.assertEqual(profile(FOCUSED), "focused")
        self.assertEqual(profile(TARGETED), "targeted")
        for plan in (FULL_PLAN, {**FOCUSED, "race": True}, {**FOCUSED, "packages": ["./modules/..."]},
                     {**FOCUSED, "packages": ["./server"]}, {**FOCUSED, "packages": ["./SERVER"]},
                     {**FOCUSED, "packages": ["./SeRvEr/subpackage"]},
                     {**FOCUSED, "packages": ["./a", "./b", "./c", "./d", "./e"]}):
            self.assertEqual(profile(plan), "full")
        self.assertEqual(test_argv(FOCUSED), ["go", "test", "-p=1", "-count=1", "-timeout=600s", "-json", "-run",
                                            "^(TestSaveAnimal)$", "./modules/animal"])
        for bad in ({**FOCUSED, "profile": "focused"}, {**FOCUSED, "packages": ["../server"]},
                    {**FOCUSED, "packages": ["./x/../server"]}, {**FOCUSED, "packages": ["-race"]},
                    {**FOCUSED, "tests": ["Test.*"]}, {**FOCUSED, "race": 0}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_plan(bad)

    def test_legacy_settings_and_ordered_tier_overrides(self):
        self.assertEqual(validate_settings({})["server_targeted_mib"], 4608)
        settings = validate_settings({"server_mib": 8192})
        self.assertEqual([settings[k] for k in ("server_focused_mib", "server_targeted_mib", "server_mib")],
                         [4096, 6144, 8192])
        for changes in ({"server_focused_mib": 7000}, {"server_targeted_mib": 9000}, {"server_focused_mib": True}):
            with self.assertRaises(ValueError):
                validate_settings({"server_mib": 8192, **changes})


class ScopeAdmissionTests(ServerPoolFixture):
    def setUp(self):
        super().setUp()
        self.admission.settings = validate_settings({"server_mib": 8192})
        self.admission.observe = lambda: {"available_mib": 14000, "commit_mib": 9000}

    def detail(self):
        return json.loads(self.ledger.connection.execute("SELECT wait_detail FROM reservations WHERE state='queued' ORDER BY sequence").fetchone()[0])

    def test_focused_runs_at_headroom_that_correctly_blocks_full_and_names_exact_shortfall(self):
        item = self.request(plan=FOCUSED)
        self.assertEqual(self.pool.tick()["granted"], 1)
        lease = self.services.started[0]
        with patch("agent.server_slots.time.time", return_value=self.now):
            resource = read_resource(self.ledger.slot(lease["slot"]), self.ledger.active_reservation(item), self.root)
        self.assertEqual(resource["memory_profile"], "focused")
        self.assertEqual(resource["test_plan"], FOCUSED)
        self.assertEqual(resource["test_argv"], test_argv(FOCUSED))
        full = self.request(plan=FULL_PLAN)
        self.assertEqual(self.pool.tick()["granted"], 0)
        detail = self.detail()
        self.assertEqual(detail["blockers"], ["commit_headroom"])
        self.assertEqual((detail["profile"], detail["requested_mib"], detail["commit_mib"], detail["required_commit_mib"]),
                         ("full", 8192, 9000, 12288))
        self.assertEqual(self.ledger.item(full)["state"], "awaiting_resource")

    def test_active_focused_cost_survives_restart_and_counts_once_in_shared_budget(self):
        self.admission.settings["max_reserved_mib"] = 8192
        self.request(plan=FOCUSED)
        self.pool.tick()
        self.pool.close()
        self.pool = self.make_pool()
        self.pool.ensure()
        self.request(plan=FOCUSED)
        self.assertEqual(self.pool.tick()["granted"], 1)
        self.request(plan=FOCUSED)
        self.assertEqual(self.pool.tick()["granted"], 0)
        self.assertEqual(self.detail()["blockers"], ["reservation_budget"])
        self.assertEqual(self.detail()["reserved_mib"], 8192)

    def test_missing_plan_cannot_broaden_a_focused_journal_to_full_scope(self):
        item = self.request(plan=FOCUSED)
        self.pool.tick()
        lease = self.services.started[0]
        path = self.pool._path(lease["slot"])
        journal = json.loads(path.read_text(encoding="utf-8"))
        journal.pop("test_plan")
        path.write_text(json.dumps(journal),encoding="utf-8")
        from agent.server_slots import ServerSlotError
        with patch("agent.server_slots.time.time", return_value=self.now), self.assertRaisesRegex(ServerSlotError, "plan"):
            read_resource(self.ledger.slot(lease["slot"]), self.ledger.active_reservation(item), self.root)

    def test_released_but_unsettled_focused_slot_retains_its_cost_and_backend_wait_is_explicit(self):
        self.request(plan=FOCUSED)
        self.pool.tick()
        lease = self.services.started[0]
        self.ledger.release(lease["reservation_id"], lease["token"], "quiescent")
        self.ledger.set_slot_state(lease["slot"], "held")
        self.assertEqual(self.admission.assess(self.ledger, "test", 0)["reserved_mib"], 4096)
        self.request(plan=FOCUSED)
        self.pool.ready = False
        self.pool.tick()
        self.assertEqual(self.detail()["blockers"], ["server_pool_unavailable"])

    def test_fifo_waits_are_distinct_from_memory_and_stopped_head_cannot_lend_its_cheaper_cost(self):
        head = self.request(plan=FULL_PLAN)
        self.request(plan=FOCUSED)
        self.assertEqual(self.pool.tick()["granted"], 0)
        details = [json.loads(r[0]) for r in self.ledger.connection.execute("SELECT wait_detail FROM reservations ORDER BY sequence")]
        self.assertEqual(details[1]["blockers"], ["fifo"])
        self.ledger.cancel(head, "operator stop")
        self.assertEqual(self.pool.tick()["granted"], 1)
        self.assertEqual(self.services.started[0]["test_plan"], FOCUSED)

    def test_stop_race_does_not_grant_a_full_successor_at_the_focused_price(self):
        head = self.request(plan=FOCUSED)
        tail = self.request(plan=FULL_PLAN)
        original = self.ledger.acquire
        def stop_then_acquire(*args, **kwargs):
            self.ledger.cancel(head, "stop after admission check")
            return original(*args, **kwargs)
        with patch.object(self.ledger, "acquire", side_effect=stop_then_acquire):
            self.assertIsNone(self.admission.acquire(self.ledger, "server_slot", host="test", owner="pool"))
        self.assertIsNone(self.ledger.active_reservation(tail))

    def test_targeted_tier_needs_six_gib_plus_floor_and_release_clears_old_wait_detail(self):
        item = self.request(plan=TARGETED)
        self.pool.tick()
        self.assertEqual((self.detail()["profile"], self.detail()["required_commit_mib"]), ("targeted", 10240))
        self.admission.observe = lambda: {"available_mib": 14000, "commit_mib": 11000}
        self.assertEqual(self.pool.tick()["granted"], 1)
        self.assertIsNone(self.ledger.connection.execute("SELECT wait_detail FROM reservations WHERE item_id=?", (item,)).fetchone()[0])

    def test_legacy_null_scope_wakes_once_without_losing_checkpoint_candidate_or_allocating(self):
        item = self.request()
        before = self.ledger.item(item)
        self.ledger.connection.execute("UPDATE reservations SET server_test_plan=NULL WHERE item_id=?", (item,))
        self.exited = False
        self.pool.tick()
        self.assertEqual(self.detail()["blockers"], ["worker_teardown"])
        self.exited = True
        self.pool.tick()
        after = self.ledger.item(item)
        self.assertEqual((after["state"], after["stage"]), ("queued", "server-test-plan-required"))
        self.assertEqual(after["checkpoint"], before["checkpoint"])
        self.assertEqual(after["target"], before["target"])
        self.assertFalse(self.services.started)
        self.assertEqual(self.ledger.reservations()[0]["state"], "cancelled")
        self.pool.tick()
        self.assertEqual(self.ledger.item(item)["state"], "queued")

    def test_requeue_keeps_scope_and_unknown_memory_still_blocks_light_test(self):
        item = self.request(plan=FOCUSED)
        self.admission.observe = lambda: (_ for _ in ()).throw(OSError("private error"))
        self.pool.tick()
        self.assertEqual(self.detail()["blockers"], ["memory_observation"])
        old = self.ledger.reservations()[0]
        self.ledger.cancel(item, "stop")
        fresh = self.ledger.requeue_reservation(old["reservation_id"], "retry")
        self.assertEqual(self.ledger.reservation(fresh)["server_test_plan"], FOCUSED)

    def test_no_free_server_capacity_does_not_claim_memory_is_the_reason(self):
        self.admission.observe = lambda: {"available_mib": 100000, "commit_mib": 100000}
        for _ in range(4):
            self.request(plan=FOCUSED)
        self.assertEqual(self.pool.tick()["granted"], 3)
        self.pool.tick()
        self.assertEqual(self.detail()["blockers"], ["server_capacity"])
