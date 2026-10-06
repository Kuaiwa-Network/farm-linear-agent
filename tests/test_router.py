import unittest

from agent.router import Decision, route, start_refusal, start_skill

SKILLS = {"chat", "fix"}


def go(**kw):
    base = dict(action="created", is_delegation=False, text="", labels=[], active_state=None,
                terminal_exists=False, available_skills=SKILLS)
    base.update(kw)
    return route(**base)


class RouterTests(unittest.TestCase):
    def test_stop_wins_over_everything(self):
        self.assertEqual(go(action="stop", active_state="running").kind, "stop")

    def test_a_delegated_bug_card_opens_a_conversation_that_says_how_to_get_a_fix(self):
        """D18 c: Bug is a label for people; a bug can be designer-only work, so it no longer starts fix."""
        self.assertEqual(go(is_delegation=True, labels=["Bug", "程序"]), Decision("chat", "chat", "", unlabelled=True))

    def test_delegation_without_a_bot_label_starts_a_conversation(self):
        self.assertEqual(go(is_delegation=True, labels=["需求"]), Decision("chat", "chat", "", unlabelled=True))

    def test_natural_language_is_interpreted_without_keyword_dispatch(self):
        for text in ("@FarmBot 帮我复现一下", "不要测试，只解释", "how does QA work?", "修复"):
            self.assertEqual(go(text=text, available_skills=SKILLS | {"qa"}), Decision("chat", "chat", text))

    def test_text_on_an_unlabelled_delegation_goes_to_the_conversation(self):
        self.assertEqual(go(is_delegation=True, labels=["Bug"], text="先解释原因，不要修改"),
                         Decision("chat", "chat", "先解释原因，不要修改", unlabelled=True))

    def test_mention_never_starts_write_capable_work(self):
        self.assertEqual(go(text="fix this bug please", labels=["Bug"]), Decision("chat", "chat", "fix this bug please"))

    def test_prompt_into_running_item_is_steering(self):
        self.assertEqual(go(action="prompted", text="先看服务端日志", active_state="running"), Decision("steer", None, "先看服务端日志"))

    def test_prompt_into_waiting_item_resumes_with_the_answer(self):
        self.assertEqual(go(action="prompted", text="公共测试服", active_state="awaiting_input"), Decision("resume", None, "公共测试服"))

    def test_terminal_prompts_are_interpreted_by_chat_not_a_keyword_match(self):
        for text in ("重试", "Please pick this back up", "先别重试", "Can you explain how to restart?"):
            self.assertEqual(go(action="prompted", text=text, terminal_exists=True), Decision("chat", "chat", text))
        self.assertEqual(go(action="prompted", text="重试").kind, "chat")

    def test_a_session_event_routes_only_to_what_the_receiver_acts_on(self):
        """Silent-delegation design A9: for a `created` or `prompted` event the router decides work, chat, steer or
        resume, the kinds the receiver acts on. It never asks a question itself: only a worker does, through
        `await-input`, which parks its job."""
        kinds = set()
        for action in ("created", "prompted"):
            for is_delegation in (True, False):
                for active_state in (None, "queued", "running", "awaiting_input", "awaiting_resource"):
                    for terminal_exists in (True, False):
                        for reroute in (True, False):
                            for groups in ((), CHANGE, UI, CODE, CHANGE + CODE, [{"group": "Bot", "label": "X"}]):
                                for skills in ({"chat"}, SKILLS, FEATURES):
                                    kinds.add(go(action=action, is_delegation=is_delegation, text="哪个服？",
                                                 active_state=active_state, terminal_exists=terminal_exists,
                                                 reroute=reroute, label_groups=groups, available_skills=skills).kind)
        self.assertEqual(kinds, {"work", "chat", "steer", "resume"})


CHANGE = [{"group": "Bot", "label": "修改"}]
UI = [{"group": "Bot", "label": "UI"}]
CODE = [{"group": "Bot", "label": "Code"}]
FEATURES = SKILLS | {"fgui", "feature"}


class BotRoutingTests(unittest.TestCase):
    """D18: on a delegation, a child of the Bot label group chooses the workflow, and nothing else does."""

    def delegated(self, **kw):
        return go(is_delegation=True, **kw)

    def test_each_bot_child_starts_its_skill_whatever_the_text(self):
        for groups, label, skill in ((CHANGE, "修改", "fix"), (UI, "UI", "fgui"), (CODE, "Code", "feature")):
            for text in ("", "先只做商店面板", "这是什么问题？"):
                with self.subTest(skill=skill, text=text):
                    self.assertEqual(self.delegated(labels=[label], label_groups=groups, text=text,
                                                    available_skills=FEATURES), Decision("work", skill))

    def test_the_labels_for_people_never_change_what_a_bot_child_starts(self):
        """Bug with a Bot child is no conflict any more: the single-select group names the one workflow."""
        department = [{"group": "部门", "label": "程序"}]
        for labels, groups, skill in ((["Bug", "修改"], CHANGE, "fix"), (["Improvement", "修改"], CHANGE, "fix"),
                                      (["程序", "修改"], [*department, *CHANGE], "fix"),
                                      (["Bug", "UI"], UI, "fgui"), (["Bug", "Feature", "Code"], CODE, "feature")):
            with self.subTest(labels=labels):
                self.assertEqual(self.delegated(labels=labels, label_groups=groups, available_skills=FEATURES),
                                 Decision("work", skill))

    def test_a_bot_child_this_instance_does_not_run_becomes_an_explaining_chat(self):
        for groups, label, skill, running in ((UI, "UI", "fgui", SKILLS), (CODE, "Code", "feature", SKILLS),
                                              (CHANGE, "修改", "fix", {"chat"})):
            with self.subTest(skill=skill):
                decision = self.delegated(labels=[label], label_groups=groups, text="看看", available_skills=running)
                self.assertEqual((decision.kind, decision.skill, decision.unlabelled), ("chat", "chat", False))
                self.assertIn(f"Bot/{label}，由 {skill} 处理，但本实例没有启用 {skill}", decision.text)
                self.assertIn("本实例运行：" + "、".join(sorted(running)), decision.text)

    def test_an_unknown_or_second_bot_child_becomes_an_explaining_chat(self):
        for groups in ([{"group": "Bot", "label": "Art"}], [*CODE, *UI], [*CHANGE, *UI]):
            with self.subTest(groups=groups):
                decision = self.delegated(labels=[g["label"] for g in groups], label_groups=groups,
                                          available_skills=FEATURES)
                self.assertEqual((decision.kind, decision.skill), ("chat", "chat"))
                self.assertIn("Bot/修改 对应 fix，Bot/UI 对应 fgui，Bot/Code 对应 feature", decision.text)
                self.assertIn("、".join(f"Bot/{label}" for label in sorted(g["label"] for g in groups)), decision.text)
                self.assertIn("无法对应到一项工作", decision.text)
                self.assertIn("不会开始这项工作", decision.text)
                self.assertNotIn("功能", decision.text)

    def test_a_standalone_label_or_one_in_another_group_is_not_a_bot_label(self):
        for label, groups in (("UI", []), ("Code", []), ("修改", []), ("UI", [{"group": "设计", "label": "UI"}]),
                              ("修改", [{"group": "部门", "label": "修改"}])):
            for labels in ([label], ["Bug", label]):
                with self.subTest(labels=labels, groups=groups):
                    self.assertEqual(self.delegated(labels=labels, label_groups=groups, available_skills=FEATURES),
                                     Decision("chat", "chat", "", unlabelled=True))

    def test_bug_improvement_or_no_label_opens_a_conversation_with_the_first_message(self):
        for labels in (["Bug"], ["Improvement"], ["Bug", "Improvement"], []):
            with self.subTest(labels=labels):
                self.assertEqual(self.delegated(labels=labels, label_groups=[{"group": "平台", "label": "iOS"}]),
                                 Decision("chat", "chat", "", unlabelled=True))
        # Without fix there is no fix to explain: the ordinary acknowledgement.
        self.assertEqual(self.delegated(labels=["Bug"], available_skills={"chat"}), Decision("chat", "chat", ""))

    def test_the_group_is_still_read_under_its_old_name_for_one_release(self):
        """The rename reaches both bots at their next read, the code at different times (design §4.1)."""
        for label, skill in (("修改", "fix"), ("UI", "fgui"), ("Code", "feature")):
            with self.subTest(label=label):
                self.assertEqual(self.delegated(labels=[label], label_groups=[{"group": "功能", "label": label}],
                                                available_skills=FEATURES), Decision("work", skill))
        decision = self.delegated(labels=["Code"], label_groups=[{"group": "功能", "label": "Code"}])
        self.assertIn("这张卡带有 Bot/Code", decision.text)

    def test_the_group_name_matches_exactly(self):
        for name in ("bot", "Bot ", "BOT", "Bots"):
            with self.subTest(name=name):
                self.assertEqual(self.delegated(labels=["修改"], label_groups=[{"group": name, "label": "修改"}]),
                                 Decision("chat", "chat", "", unlabelled=True))

    def test_mentions_never_start_bot_work_and_keep_the_ordinary_acknowledgement(self):
        for groups, label in ((CHANGE, "修改"), (UI, "UI"), (CODE, "Code")):
            with self.subTest(label=label):
                self.assertEqual(go(labels=[label], label_groups=groups, text="@FarmBot 做一下",
                                    available_skills=FEATURES), Decision("chat", "chat", "@FarmBot 做一下"))
        self.assertEqual(go(labels=["Bug"], text="@FarmBot 看看"), Decision("chat", "chat", "@FarmBot 看看"))

    def test_a_reroute_runs_the_table_again_with_the_reply_as_its_text(self):
        reply = dict(action="prompted", is_delegation=True, reroute=True, available_skills=FEATURES)
        self.assertEqual(go(labels=["Code"], label_groups=CODE, text="好了", **reply), Decision("work", "feature"))
        # 修改 decides whatever the reply says (design §10, question 1).
        self.assertEqual(go(labels=["修改"], label_groups=CHANGE, text="这是什么原因？", **reply), Decision("work", "fix"))
        # No Bot child: the reply is already the conversation's first message, so no instructions go before it.
        self.assertEqual(go(labels=["Bug"], text="请修复", **reply), Decision("chat", "chat", "请修复"))
        # Without the flag a reply with no active item is ordinary chat, as before.
        self.assertEqual(go(action="prompted", is_delegation=True, labels=["Code"], label_groups=CODE, text="x",
                            available_skills=FEATURES), Decision("chat", "chat", "x"))


class StartRequestTests(unittest.TestCase):
    """D18 f (design §4.4): a start request in a conversation follows the card's Bot label."""

    def test_no_bot_child_or_only_the_change_child_may_start_fix(self):
        for children in ([], ["修改"]):
            with self.subTest(children=children):
                self.assertIsNone(start_refusal(children, SKILLS))
                self.assertIsNone(start_refusal(children, FEATURES))

    def test_a_first_start_on_a_ui_or_code_card_is_refused_with_its_reason(self):
        self.assertEqual(start_refusal(["Code"], SKILLS),
                         "this issue carries Bot/Code, so it is feature work, not a fix, and this instance does not "
                         "run feature yet")
        self.assertEqual(start_refusal(["UI"], SKILLS),
                         "this issue carries Bot/UI, so it is fgui work, not a fix, and this instance does not run "
                         "fgui yet")
        self.assertIsNone(start_refusal(["UI"], FEATURES))

    def test_the_no_workflow_refusal_says_what_to_do(self):
        self.assertEqual(start_refusal(["Art"], SKILLS),
                         "this issue carries Bot/Art, not exactly one of Bot/修改, Bot/UI or Bot/Code, so it names "
                         "no workflow; correct the label, then ask again")

    def test_an_unknown_child_or_two_children_name_no_workflow(self):
        for children in (["Art"], ["Code", "UI"], ["UI", "修改"]):
            with self.subTest(children=children):
                refusal = start_refusal(children, FEATURES)
                self.assertIn("not exactly one of Bot/修改, Bot/UI or Bot/Code, so it names no workflow", refusal)
                self.assertIn("、".join(f"Bot/{child}" for child in children), refusal)

    def test_the_label_names_the_skill_a_first_start_creates(self):
        for children, skill in (([], "fix"), (["修改"], "fix"), (["Code"], "feature"), (["UI"], "fgui"),
                                (["Art"], None), (["Code", "UI"], None)):
            with self.subTest(children=children):
                self.assertEqual(start_skill(children), skill)

    def test_a_code_card_may_start_feature_where_this_instance_runs_it(self):
        """Opt-in Code/UI conversations follow their own label and host enablement."""
        self.assertIsNone(start_refusal(["Code"], FEATURES))
        self.assertIsNone(start_refusal(["Code"], {"chat", "feature"}))
        self.assertIsNone(start_refusal(["UI"], FEATURES))
        self.assertIsNone(start_refusal(["UI"], {"chat", "fgui"}))

    def test_without_fix_a_card_whose_label_names_fix_starts_nothing(self):
        """A host may run feature and not fix: a request there reads Linear, then refuses a first fix."""
        for children in ([], ["修改"]):
            with self.subTest(children=children):
                self.assertEqual(start_refusal(children, {"chat", "feature"}),
                                 "repair execution is not available on this host")



class FeatureEnablementRoutingTests(unittest.TestCase):
    """Phase B, Task 12: with the checkout's real manifests, a Bot/Code delegation starts feature exactly where the
    host's enabled_skills names it (P1, D18)."""

    def running(self, names):
        from pathlib import Path
        from agent.dispatch import SKILL_AUTHORITY
        from agent.skills import enabled_skills, load_skills
        root = Path(__file__).resolve().parents[1]
        return set(enabled_skills(load_skills(root / "skills"), names, authority=SKILL_AUTHORITY))

    def test_a_host_that_names_feature_routes_every_bot_code_delegation_to_it(self):
        running = self.running(["chat", "fix", "feature"])
        for labels, text in ((["Code"], ""), (["Bug", "Code"], ""), (["Code"], "先只做服务端")):
            with self.subTest(labels=labels, text=text):
                self.assertEqual(go(is_delegation=True, labels=labels, label_groups=CODE, text=text,
                                    available_skills=running), Decision("work", "feature"))
        reply = dict(action="prompted", is_delegation=True, reroute=True, available_skills=running)
        self.assertEqual(go(labels=["Code"], label_groups=CODE, text="现在开始", **reply), Decision("work", "feature"))

    def test_without_enabled_skills_feature_stays_off_and_the_card_opens_the_explaining_conversation(self):
        running = self.running(None)
        self.assertEqual(running, {"chat", "fix"})
        decision = go(is_delegation=True, labels=["Code"], label_groups=CODE, available_skills=running)
        self.assertEqual((decision.kind, decision.skill), ("chat", "chat"))
        self.assertIn("Bot/Code，由 feature 处理，但本实例没有启用 feature（本实例运行：chat、fix）", decision.text)

    def test_a_mention_never_starts_feature_even_where_it_runs(self):
        running = self.running(["chat", "fix", "feature"])
        self.assertEqual(go(labels=["Code"], label_groups=CODE, text="@FarmBot 做一下", available_skills=running),
                         Decision("chat", "chat", "@FarmBot 做一下"))
