"""The notices a withdrawn, superseded, closed or operator-cancelled job gets, and the grace a claimed worker has."""
import unittest
from pathlib import Path

from agent import withdrawal
from agent.lifecycle import UNDELEGATED
from agent.skills import load_skills

ROOT = Path(__file__).resolve().parents[1]
SKILLS = load_skills(ROOT / "skills")


class NoticeTests(unittest.TestCase):
    def test_each_reason_has_one_text_for_a_write_job_and_one_for_a_conversation(self):
        expected = {("fix", "undelegated"): withdrawal.UNDELEGATED, ("chat", "undelegated"): withdrawal.UNDELEGATED_CHAT,
                    ("fix", "superseded"): withdrawal.SUPERSEDED, ("chat", "superseded"): withdrawal.SUPERSEDED,
                    ("fix", "closed"): withdrawal.CLOSED, ("chat", "closed"): withdrawal.CLOSED_CHAT,
                    ("fix", "operator"): withdrawal.OPERATOR, ("chat", "operator"): withdrawal.OPERATOR_CHAT}
        for (skill, reason), text in expected.items():
            with self.subTest(skill=skill, reason=reason):
                self.assertEqual(withdrawal.notice(skill, reason, "FarmBot"), text.format(bot="FarmBot"))
        for skill in ("feature", "fgui"):
            self.assertEqual(withdrawal.notice(skill, "closed", "FarmBot"), withdrawal.notice("fix", "closed", "FarmBot"))

    def test_a_conversations_notice_never_mentions_branches(self):
        for reason in withdrawal.NOTICE_REASONS:
            with self.subTest(reason=reason):
                body = withdrawal.notice("chat", reason, "FarmBot")
                self.assertNotIn("分支", body)
                self.assertNotIn("PR", body)
                self.assertNotIn("{", body)

    def test_the_undelegated_write_text_is_the_one_the_lifecycle_already_posts(self):
        self.assertIs(UNDELEGATED, withdrawal.UNDELEGATED)
        self.assertIn("已推送的分支和草稿 PR 都保留", withdrawal.notice("fix", "undelegated", "FarmBot"))

    def test_an_unknown_reason_has_no_notice(self):
        with self.assertRaises(ValueError):
            withdrawal.notice("fix", "unreachable", "FarmBot")


class GraceTests(unittest.TestCase):
    def test_grace_is_twice_the_renew_interval_or_twenty_minutes_for_an_unknown_skill(self):
        self.assertEqual(withdrawal.grace_seconds(SKILLS["fix"]), 1200)
        self.assertEqual(withdrawal.grace_seconds(SKILLS["chat"]), 600)
        self.assertEqual(withdrawal.grace_seconds(None), 1200)


if __name__ == "__main__":
    unittest.main()
