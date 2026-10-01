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
                    ("fix", "operator"): withdrawal.OPERATOR, ("chat", "operator"): withdrawal.OPERATOR_CHAT,
                    ("fix", "stopped_elsewhere"): withdrawal.STOPPED_ELSEWHERE,
                    ("chat", "stopped_elsewhere"): withdrawal.STOPPED_ELSEWHERE_CHAT}
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

    def test_a_stop_from_another_thread_has_a_text_for_each_thread(self):
        """Silent-delegation design A1: the stopped job's own thread is told the work ended, keeping what a write job
        pushed; the Stop's thread is told what was stopped, without claiming a worker ran: the job may have waited."""
        self.assertIn("已推送的分支和草稿 PR 都保留", withdrawal.notice("fix", "stopped_elsewhere", "FarmBot"))
        self.assertIn("Stop", withdrawal.notice("chat", "stopped_elsewhere", "FarmBot"))
        self.assertEqual(withdrawal.STOP_ELSEWHERE.format(identifier="FARM-1"),
                         "已停止 FARM-1 上在另一个会话中的工作，占用的资源在静默检查后释放。")
        self.assertNotIn("worker", withdrawal.STOP_ELSEWHERE)

    def test_a_withdrawn_question_has_a_text_for_a_write_job_and_one_for_a_conversation(self):
        """Silent-delegation design A6, §7: the response that follows a question whose job ended before it could
        park says nobody needs to answer, in the job's own terms."""
        self.assertEqual(withdrawal.question_withdrawn("fix"), "上面的问题不用再回答：这项工作已经停止。")
        self.assertEqual(withdrawal.question_withdrawn("chat"), "上面的问题不用再回答：这段对话已经结束。")
        for skill in ("feature", "fgui"):
            self.assertEqual(withdrawal.question_withdrawn(skill), withdrawal.QUESTION_WITHDRAWN)

    def test_silent_delegation_texts_format_and_a_conversations_names_no_branch(self):
        """Silent-delegation design §7 (TW1): what a thread is told when a delegation opened no session. Each text
        names the instance and leaves no placeholder; each note says that the instance received no session for the
        delegation, which stays true after a lost delivery (design R6), and what to do next, and promises nothing that
        holds only for some routes. A conversation's text never mentions branches or PRs."""
        texts = {name: getattr(withdrawal, name) for name in (
            "IN_PLACE_NOTE", "REDELEGATED_WAITING", "REDELEGATED_RUNNING", "SILENT_WAITING", "SILENT_WAITING_CHAT",
            "SILENT_BUSY", "SILENT_ENDED")}
        for name, text in texts.items():
            with self.subTest(name=name):
                body = text.format(bot="TestBot", question="哪个服？")
                self.assertIn("TestBot", body)
                self.assertNotIn("FarmBot", body)
                self.assertNotIn("{", body)
                self.assertNotIn("}", body)
                self.assertNotIn("分支", body)
                self.assertNotIn("PR", body)
        self.assertEqual(withdrawal.REDELEGATED_WAITING.format(bot="FarmBot", question="哪个服？"),
                         "这张卡已重新委派给 FarmBot。这里的工作还在等你的回答：\n哪个服？")
        self.assertEqual(withdrawal.IN_PLACE_NOTE.format(bot="FarmBot"),
                         "这张卡已重新委派给 FarmBot。FarmBot 没有收到这次委派的新会话，就在这个会话里接着处理。")
        for name in ("SILENT_WAITING", "SILENT_WAITING_CHAT", "SILENT_BUSY", "SILENT_ENDED"):
            with self.subTest(name=name):
                self.assertIn("TestBot 没有收到这次委派的会话", texts[name].format(bot="TestBot"))
                self.assertNotIn("Linear 没有", texts[name])  # after a lost delivery Linear did open one
                self.assertNotIn("已有进度", texts[name])  # the next delegation may start other work
                self.assertNotIn("会转到", texts[name])  # a late one finds the work cancelled first
                self.assertIn("「No agent」", texts[name])
                # Live AC-2: a response can end the wait without making UI re-delegation open a session.
                # The fallback names only this bot's completed sessions, preserving active work.
                self.assertIn("归档这张卡上 TestBot 已结束的会话", texts[name].format(bot="TestBot"))
        for name in ("SILENT_WAITING", "SILENT_WAITING_CHAT"):
            self.assertIn("在这里回复可以继续", texts[name])  # the wait ends, the work stays answerable
        self.assertIn("这项工作仍然保留", withdrawal.SILENT_WAITING)
        self.assertIn("对话仍然保留", withdrawal.SILENT_WAITING_CHAT)
        self.assertIn("Stop", withdrawal.SILENT_BUSY)
        self.assertEqual(withdrawal.RENOTE_SECONDS, 1800)
        self.assertEqual(withdrawal.SILENT_GRACE_SECONDS, 90)

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
