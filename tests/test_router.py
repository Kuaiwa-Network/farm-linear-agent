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

    def test_delegation_without_bug_label_starts_a_conversation(self):
        decision = go(is_delegation=True, labels=["需求"])
        self.assertEqual(decision.kind, "chat")

    def test_natural_language_is_interpreted_without_keyword_dispatch(self):
        for text in ("@FarmBot 帮我复现一下", "不要测试，只解释", "how does QA work?", "修复"):
            self.assertEqual(go(text=text, available_skills=SKILLS | {"qa"}), Decision("chat", "chat", text))

    def test_question_on_delegated_bug_is_interpreted_before_writable_work(self):
        self.assertEqual(go(is_delegation=True, labels=["Bug"], text="先解释原因，不要修改"),
                         Decision("chat", "chat", "先解释原因，不要修改"))

    def test_mention_never_starts_write_capable_work(self):
        self.assertEqual(go(text="fix this bug please", labels=["Bug"]).kind, "chat")

    def test_prompt_into_running_item_is_steering(self):
        self.assertEqual(go(action="prompted", text="先看服务端日志", active_state="running"), Decision("steer", None, "先看服务端日志"))

    def test_prompt_into_waiting_item_resumes_with_the_answer(self):
        self.assertEqual(go(action="prompted", text="公共测试服", active_state="awaiting_input"), Decision("resume", None, "公共测试服"))

    def test_terminal_prompts_are_interpreted_by_chat_not_a_keyword_match(self):
        for text in ("重试", "Please pick this back up", "先别重试", "Can you explain how to restart?"):
            self.assertEqual(go(action="prompted", text=text, terminal_exists=True), Decision("chat", "chat", text))
        self.assertEqual(go(action="prompted", text="重试").kind, "chat")
