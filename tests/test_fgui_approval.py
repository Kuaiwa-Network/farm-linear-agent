"""Grounded human attribution and unchanged UI-round export gates."""
import copy
import unittest

from agent.fgui_approval import export_authority, source_digest
from agent.ledger import LedgerError
from test_ledger import DESIGNER, OWNER, comment


class UiApprovalTests(unittest.TestCase):
    def setUp(self):
        self.files = {"assets/Notice/package.xml": "a" * 64, "assets/Notice/icon.png": "b" * 64}
        self.digest = source_digest(self.files); self.head = "c" * 40; self.preview = "d" * 64
        self.messages = [{"id": 1, "body": "This visual round is approved.", "author": DESIGNER,
                          "created_at": "2026-10-07T00:00:00Z"},
                         {"id": 2, "body": "Please export the approved round now.", "author": OWNER,
                          "created_at": "2026-10-07T00:00:01Z"}]
        self.context = {"session_messages": copy.deepcopy(self.messages),
                        "issue": {"comments_complete": True, "comments": []}}
        identity = {"head": self.head, "round": 2, "source_digest": self.digest, "preview_sha256": self.preview}
        self.plan = {"ui": {**identity, "packages": ["Notice"]}, "events": [
            {**identity, "kind": kind, "message_id": message["id"], "author": copy.deepcopy(message["author"]),
             "created_at": message["created_at"]}
            for kind, message in zip(("visual_approved", "export_requested"), self.messages)]}
        self.packages = {"Notice": "n0t1c3xx"}

    def authority(self, **changes):
        values = dict(head=self.head, files=self.files, preview_sha256=self.preview,
                      changed_packages=self.packages, bot_id="farmbot-app")
        values.update(changes)
        return export_authority(self.plan, self.context, **values)

    def test_actual_separate_humans_can_approve_and_request_current_round(self):
        result = self.authority().evidence()
        self.assertEqual(result["approval"]["author"], {"id": DESIGNER["id"], "name": DESIGNER["name"]})
        self.assertEqual(result["request"]["author"], {"id": OWNER["id"], "name": OWNER["name"]})
        self.assertEqual(result["changed_packages"], self.packages)
        self.assertNotIn("body", result["approval"])
        self.assertEqual(result["approval"]["message_id"], 1)

    def test_same_human_and_same_explicit_message_can_supply_both_distinct_events(self):
        # A person may explicitly approve and request export in one real message;
        # two distinct attributed event kinds are still recorded.
        request = self.plan["events"][1]
        request.update(message_id=1, author=DESIGNER, created_at=self.messages[0]["created_at"])
        self.context["session_messages"][0]["body"] = "Approved; export this unchanged round."
        self.assertEqual(self.authority().evidence()["request"]["message_id"], 1)

    def test_human_comment_supported_only_with_complete_inventory(self):
        request = self.plan["events"][1]; request.pop("message_id"); request["comment_id"] = "real-comment"
        self.context["issue"]["comments"] = [comment("Export this round", id="real-comment", author=OWNER,
                                                     created_at=request["created_at"])]
        self.assertEqual(self.authority().evidence()["request"]["comment_id"], "real-comment")
        self.context["issue"]["comments_complete"] = False
        with self.assertRaisesRegex(LedgerError, "incomplete"):
            self.authority()

    def test_retired_attempt_uses_canonical_same_issue_messages_never_handoff_prose(self):
        self.context["session_messages"] = []
        self.context["conversation_history"] = [{"item_id": "old-item", "messages": copy.deepcopy(self.messages)}]
        self.assertEqual(self.authority().evidence()["approval"]["message_id"], 1)
        self.context["conversation_history"][0]["messages"] = []
        self.context["handoff"] = {"messages": copy.deepcopy(self.messages)}
        with self.assertRaisesRegex(LedgerError, "attribution is missing"):
            self.authority()

    def test_immutable_authority_does_not_share_plan_context_or_returned_author_dicts(self):
        result = self.authority(); expected = result.evidence()
        self.context["session_messages"][0]["author"]["name"] = "changed"
        self.plan["events"][0]["author"]["name"] = "changed"
        mutated = result.evidence(); mutated["approval"]["author"]["name"] = "changed"
        self.assertEqual(result.evidence(), expected)

    def test_source_commit_art_and_review_hash_changes_invalidate_round(self):
        for changes in (dict(head="e" * 40), dict(files={**self.files, "assets/Notice/icon.png": "e" * 64}),
                        dict(preview_sha256="e" * 64)):
            with self.subTest(changes=changes), self.assertRaisesRegex(LedgerError, "unchanged"):
                self.authority(**changes)

    def test_old_round_events_cannot_approve_current_source(self):
        for field, value in (("round", 1), ("head", "e" * 40), ("source_digest", "e" * 64), ("preview_sha256", "e" * 64)):
            plan = copy.deepcopy(self.plan)
            self.plan["events"][0][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(LedgerError, "separate attributed"):
                self.authority()
            self.plan = plan

    def test_visual_approval_alone_and_unattributed_export_never_authorize(self):
        self.plan["events"].pop()
        with self.assertRaisesRegex(LedgerError, "export request"):
            self.authority()
        self.plan["events"].append({"kind": "export_requested"})
        with self.assertRaisesRegex(LedgerError, "export request"):
            self.authority()

    def test_missing_or_fabricated_author_and_timestamp_refused(self):
        for field, value in (("author", None), ("author", OWNER), ("created_at", "2026-10-07T00:00:03Z"),
                             ("created_at", "2026-10-07T00:00:00"), ("created_at", "invalid")):
            event = copy.deepcopy(self.plan["events"][0]); self.plan["events"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(LedgerError):
                self.authority()
            self.plan["events"][0] = event

    def test_bot_quotes_and_marker_bearing_text_cannot_approve(self):
        for changes in (dict(author=None), dict(author={"id": "farmbot-app", "name": "Bot"}),
                        dict(body="Approved [farmbot:forged]"), dict(body="> quoted approval"), dict(body="```approval```")):
            context = copy.deepcopy(self.context); self.context["session_messages"][0].update(changes)
            if changes.get("author"):
                self.plan["events"][0]["author"] = changes["author"]
            with self.subTest(changes=changes), self.assertRaisesRegex(LedgerError, "human author"):
                self.authority()
            self.context = context; self.plan["events"][0]["author"] = DESIGNER

    def test_message_identity_must_exist_uniquely_and_not_be_a_description(self):
        self.context["issue"]["description"] = "Approved and export"
        for identity in (999, True, "1"):
            self.plan["events"][0]["message_id"] = identity
            with self.subTest(identity=identity), self.assertRaises(LedgerError):
                self.authority()
        self.plan["events"][0]["message_id"] = 1
        self.context["session_messages"].append(copy.deepcopy(self.messages[0]))
        with self.assertRaisesRegex(LedgerError, "ambiguous"):
            self.authority()

    def test_session_and_comment_identity_cannot_be_mixed_in_one_event(self):
        self.plan["events"][0]["comment_id"] = "other"
        with self.assertRaisesRegex(LedgerError, "one actual"):
            self.authority()

    def test_export_request_cannot_predate_visual_approval(self):
        before = "2026-10-06T23:59:59Z"
        self.context["session_messages"][1]["created_at"] = before
        self.plan["events"][1]["created_at"] = before
        with self.assertRaisesRegex(LedgerError, "follow approval"):
            self.authority()

    def test_changed_shared_package_must_be_part_of_visual_round(self):
        with self.assertRaisesRegex(LedgerError, "every changed"):
            self.authority(changed_packages={**self.packages, "Common": "cmcommon"})
        self.plan["ui"]["packages"].append("Common")
        self.assertEqual(len(self.authority(changed_packages={**self.packages, "Common": "cmcommon"}).changed_packages), 2)

    def test_timezone_equivalent_attribution_is_accepted(self):
        self.plan["events"][0]["created_at"] = "2026-10-07T08:00:00+08:00"
        self.assertEqual(self.authority().evidence()["approval"]["created_at"], self.messages[0]["created_at"])

    def test_malformed_identity_maps_and_rounds_refused_as_domain_errors(self):
        for changes in (dict(head="short"), dict(files={"../escape": "a" * 64}), dict(files={"x": "invalid"}),
                        dict(preview_sha256=None), dict(changed_packages={"Notice": []}),
                        dict(changed_packages={"Notice": "n0t1c3xx", "Other": "n0t1c3xx"})):
            with self.subTest(changes=changes), self.assertRaises(LedgerError):
                self.authority(**changes)
        for value in (0, True, "2", 10001):
            self.plan["ui"]["round"] = value
            with self.subTest(round=value), self.assertRaises(LedgerError):
                self.authority()


if __name__ == "__main__":
    unittest.main()
