"""Regressions reproduced from the September 21-22 worker attempts."""
from test_ledger import LedgerBase, ISSUE, issue, comment
from agent.ledger import LedgerError


HANDOFF = {"facts": [{"claim": "Needs a product decision", "evidence": "investigation.md"}],
           "hypotheses": [], "checks": [], "repositories": [], "next_actions": ["Read the reply"]}
PR = "https://github.com/Kuaiwa-Network/farmgui/pull/113"


class RejectedCheckpointTests(LedgerBase):
    def rejected(self):
        item = self.new_item()
        token = self.ledger.claim(item["id"], worker_id="worker")["token"]
        self.ledger.checkpoint(item["id"], token, {"handoff": HANDOFF})
        with self.assertRaises(LedgerError):
            self.ledger.checkpoint(item["id"], token, {"handoff": {"facts": ["wrong shape"]}})
        return item["id"], token

    def test_rejected_handoff_cannot_be_followed_by_await_input(self):
        item, token = self.rejected()
        with self.assertRaisesRegex(LedgerError, "checkpoint"):
            self.ledger.await_input(item, token, "Which behavior?")
        self.assertEqual(self.ledger.item(item)["state"], "running")

    def test_rejected_handoff_cannot_be_followed_by_resource_or_finish(self):
        item, token = self.rejected()
        with self.assertRaisesRegex(LedgerError, "checkpoint"):
            self.ledger.await_resource(item, token, "unity_slot", "batch")
        action = self.ledger.prepare_comment(item, token, "blocker", "Decision needed")
        self.ledger.confirm_comment(action["action_id"], "comment")
        with self.assertRaisesRegex(LedgerError, "checkpoint"):
            self.ledger.finish(item, token, "blocked", {
                "summary": "Decision needed", "comment_action_id": action["action_id"]})

    def test_error_survives_restart_and_stage_only_checkpoint_until_handoff_repaired(self):
        item, token = self.rejected()
        ledger = self.open_ledger()
        ledger.checkpoint(item, token, {"stage": "waiting"})
        self.assertIsNotNone(ledger.issue_context(item)["checkpoint_error"])
        with self.assertRaisesRegex(LedgerError, "checkpoint"):
            ledger.await_input(item, token, "Which behavior?")
        ledger.checkpoint(item, token, {"handoff": HANDOFF})
        self.assertIsNone(ledger.issue_context(item)["checkpoint_error"])
        self.assertEqual(ledger.await_input(item, token, "Which behavior?")["state"], "awaiting_input")

    def test_wrong_claim_cannot_poison_another_workers_checkpoint(self):
        item = self.new_item()
        token = self.ledger.claim(item["id"], worker_id="worker")["token"]
        with self.assertRaises(LedgerError):
            self.ledger.checkpoint(item["id"], "wrong-token", {"handoff": {"facts": []}})
        self.assertEqual(self.ledger.await_input(item["id"], token, "Which behavior?")["state"], "awaiting_input")


class PublicationEchoTests(LedgerBase):
    def working(self, **changes):
        item = self.new_item(**changes)
        token = self.ledger.claim(item["id"], worker_id="worker")["token"]
        return item["id"], token

    def test_verified_late_pr_registration_restores_input_fingerprint(self):
        item, token = self.working()
        action = self.ledger.prepare_comment(item, token, "delivery", "Fixed: " + PR)
        self.ledger.confirm_comment(action["action_id"], "comment")
        self.ledger.observe_issue(issue(attachments=[PR]))
        self.ledger.checkpoint(item, token, {"published_prs": [PR]}, verified_prs=[PR])
        result = self.ledger.finish(item, token, "delivered", {"summary": "Fixed", "verification": "Focused tests passed",
                                   "prs": [PR], "comment_action_id": action["action_id"]})
        self.assertEqual(result["state"], "delivered")
        self.assertEqual(self.ledger.issue_context(item)["published_prs"], [PR])

    def test_unverified_input_pr_is_still_rejected(self):
        item, token = self.working()
        self.ledger.observe_issue(issue(attachments=[PR]))
        with self.assertRaisesRegex(LedgerError, "issue input"):
            self.ledger.checkpoint(item, token, {"published_prs": [PR]})

    def test_claimed_pr_cannot_be_reclassified_as_output(self):
        item, token = self.working(attachments=[PR])
        with self.assertRaisesRegex(LedgerError, "issue input"):
            self.ledger.checkpoint(item, token, {"published_prs": [PR]}, verified_prs=[PR])

    def test_human_change_is_not_swallowed_by_late_registration(self):
        for changes in ({"description": "New requirement"}, {"comments": [comment()]},
                        {"attachments": [PR, "https://example.com/new-evidence"]}):
            with self.subTest(changes=changes):
                self.setUp()
                item, token = self.working()
                self.ledger.observe_issue(issue(**({"attachments": [PR]} | changes)))
                with self.assertRaisesRegex(LedgerError, "issue input"):
                    self.ledger.checkpoint(item, token, {"published_prs": [PR]}, verified_prs=[PR])
                self.assertEqual(self.ledger.issue_context(item)["published_prs"], [])
