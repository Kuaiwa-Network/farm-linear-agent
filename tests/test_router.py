import unittest

from agent.router import Decision, route

SKILLS = {"chat", "fix"}


def go(**kw):
    base = dict(action="created", is_delegation=False, text="", labels=[], active_state=None,
                terminal_exists=False, available_skills=SKILLS)
    base.update(kw)
    return route(**base)


class RouterTests(unittest.TestCase):
    def test_stop_wins_over_everything(self):
        self.assertEqual(go(action="stop", active_state="running").kind, "stop")

    def test_delegated_bug_becomes_fix_work(self):
        self.assertEqual(go(is_delegation=True, labels=["Bug", "程序"]), Decision("work", "fix"))

    def test_delegation_without_bug_label_asks_one_question(self):
        decision = go(is_delegation=True, labels=["需求"])
        self.assertEqual(decision.kind, "elicit")
        self.assertIn("修复", decision.text)

    def test_mention_asking_for_qa_routes_to_qa_only_when_available(self):
        self.assertEqual(go(text="@FarmBot 帮我复现一下", available_skills=SKILLS | {"qa"}), Decision("work", "qa"))
        fallback = go(text="@FarmBot 跑冒烟")
        self.assertEqual(fallback.kind, "chat")
        self.assertIn("qa", fallback.text)

    def test_mention_never_starts_write_capable_work(self):
        self.assertEqual(go(text="fix this bug please", labels=["Bug"]).kind, "chat")

    def test_prompt_into_running_item_is_steering(self):
        self.assertEqual(go(action="prompted", text="先看服务端日志", active_state="running"), Decision("steer", None, "先看服务端日志"))

    def test_prompt_into_waiting_item_resumes_with_the_answer(self):
        self.assertEqual(go(action="prompted", text="公共测试服", active_state="awaiting_input"), Decision("resume", None, "公共测试服"))

    def test_retry_word_after_terminal_item_requeues(self):
        self.assertEqual(go(action="prompted", text="重试", terminal_exists=True).kind, "retry")
        self.assertEqual(go(action="prompted", text="重试").kind, "chat")
