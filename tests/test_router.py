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


UI = [{"group": "功能", "label": "UI"}]
CODE = [{"group": "功能", "label": "Code"}]
FEATURES = SKILLS | {"fgui", "feature"}


class FeatureRoutingTests(unittest.TestCase):
    """Spec §4.3: on a delegation, the 功能 label group chooses the workflow."""

    def delegated(self, **kw):
        return go(is_delegation=True, **kw)

    def test_bug_and_a_feature_child_elicit_without_work(self):
        for groups, child in ((UI, "UI"), (CODE, "Code")):
            with self.subTest(child=child):
                decision = self.delegated(labels=["Bug", child], label_groups=groups, available_skills=FEATURES)
                self.assertEqual((decision.kind, decision.skill), ("elicit", None))
                self.assertIn(f"Bug 和 功能/{child}", decision.text)
                self.assertIn("移除不适用的那个标签", decision.text)
                self.assertNotIn("再说一次", decision.text)

    def test_text_sent_with_a_conflicting_delegation_is_asked_for_again(self):
        decision = self.delegated(labels=["Bug", "UI"], label_groups=UI, text="按效果图做")
        self.assertEqual(decision.kind, "elicit")
        self.assertIn("再说一次", decision.text)

    def test_an_enabled_feature_child_starts_its_skill_whatever_the_text(self):
        for groups, label, skill in ((UI, "UI", "fgui"), (CODE, "Code", "feature")):
            for text in ("", "先只做商店面板"):
                with self.subTest(skill=skill, text=text):
                    self.assertEqual(self.delegated(labels=[label], label_groups=groups, text=text,
                                                    available_skills=FEATURES), Decision("work", skill))

    def test_a_feature_child_this_instance_does_not_run_becomes_an_explaining_chat(self):
        for groups, label, skill in ((UI, "UI", "fgui"), (CODE, "Code", "feature")):
            with self.subTest(skill=skill):
                decision = self.delegated(labels=[label], label_groups=groups, text="看看")
                self.assertEqual((decision.kind, decision.skill), ("chat", "chat"))
                self.assertIn(f"功能/{label}，由 {skill} 处理", decision.text)
                self.assertIn("本实例运行：chat、fix", decision.text)

    def test_an_unknown_or_second_feature_child_becomes_an_explaining_chat(self):
        for groups in ([{"group": "功能", "label": "Art"}], [*CODE, *UI]):
            with self.subTest(groups=groups):
                decision = self.delegated(labels=[g["label"] for g in groups], label_groups=groups,
                                          available_skills=FEATURES)
                self.assertEqual((decision.kind, decision.skill), ("chat", "chat"))
                self.assertIn("功能/UI 对应 fgui", decision.text)
                self.assertIn("、".join(f"功能/{g['label']}" for g in groups), decision.text)

    def test_a_standalone_ui_or_code_label_is_not_a_feature_label(self):
        for label, groups in (("UI", []), ("Code", []), ("UI", [{"group": "设计", "label": "UI"}])):
            with self.subTest(label=label, groups=groups):
                self.assertEqual(self.delegated(labels=[label], label_groups=groups, available_skills=FEATURES),
                                 Decision("chat", "chat", ""))
                self.assertEqual(self.delegated(labels=["Bug", label], label_groups=groups, available_skills=FEATURES),
                                 Decision("work", "fix"))

    def test_bug_alone_keeps_its_shortcut_and_its_text_rule(self):
        self.assertEqual(self.delegated(labels=["Bug"], label_groups=[{"group": "平台", "label": "iOS"}]),
                         Decision("work", "fix"))
        self.assertEqual(self.delegated(labels=["Bug"], text="先解释"), Decision("chat", "chat", "先解释"))
        self.assertEqual(self.delegated(labels=["Bug"], available_skills={"chat"}), Decision("chat", "chat", ""))

    def test_mentions_never_start_feature_work(self):
        for groups, label in ((UI, "UI"), (CODE, "Code")):
            with self.subTest(label=label):
                self.assertEqual(go(labels=[label], label_groups=groups, text="@FarmBot 做一下",
                                    available_skills=FEATURES).kind, "chat")
        self.assertEqual(go(labels=["Bug", "UI"], label_groups=UI, text="@FarmBot 看看").kind, "chat")

    def test_a_repair_request_on_a_feature_card_is_told_how_feature_work_starts(self):
        from agent.router import feature_repair_refusal
        self.assertEqual(feature_repair_refusal(["Code"], SKILLS),
                         "this issue carries 功能/Code, so it is feature work, not a fix; feature work starts only "
                         "when an issue labelled 功能/Code is delegated, and this instance does not run feature yet")
        self.assertEqual(feature_repair_refusal(["UI"], FEATURES),
                         "this issue carries 功能/UI, so it is fgui work, not a fix; fgui work starts only when an "
                         "issue labelled 功能/UI is delegated")
        for children in (["Art"], ["Code", "UI"]):  # an unknown child, or two: neither reads as the first one
            self.assertIn("not exactly one 功能/UI or 功能/Code label", feature_repair_refusal(children, FEATURES))

    def test_a_reroute_runs_the_table_again_with_the_reply_as_its_text(self):
        reply = dict(action="prompted", is_delegation=True, reroute=True, available_skills=FEATURES)
        self.assertEqual(go(labels=["Code"], label_groups=CODE, text="已去掉 Bug", **reply), Decision("work", "feature"))
        self.assertEqual(go(labels=["Bug", "Code"], label_groups=CODE, text="好了", **reply).kind, "elicit")
        # Bug alone: the reply is text, so the read-only conversation interprets it first.
        self.assertEqual(go(labels=["Bug"], text="已去掉功能标签", **reply), Decision("chat", "chat", "已去掉功能标签"))
        # Without the flag a reply with no active item is ordinary chat, as before.
        self.assertEqual(go(action="prompted", is_delegation=True, labels=["Code"], label_groups=CODE, text="x",
                            available_skills=FEATURES), Decision("chat", "chat", "x"))
