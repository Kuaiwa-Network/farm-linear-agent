"""Conversation requests change execution mode; the ledger controls authority."""
import os
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from agent.config import Config
from agent.dispatch import SKILL_AUTHORITY
from agent.ledger import LedgerError
from agent.skills import SkillError
from agent.__main__ import parser, run
from test_ledger import LedgerBase, ISSUE, OTHER, PIN, SESSION, SKILLS, issue
from test_receiver import ReceiverBase, APP
from test_skills import opt_in_skill


class RepairWorkTests(LedgerBase):
    def setUp(self):
        super().setUp()
        # The CLI reads the host config for enabled_skills; these tests must never see this checkout's private one.
        patcher = patch.dict(os.environ, {"FARMBOT_CONFIG": str(Path(self.tmp.name) / "no-config.json")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def conversation(self, *, delegated=True, session=SESSION):
        self.ledger.observe_issue(issue(delegate_id=APP, labels=[]))
        self.ledger.ensure_session(session, ISSUE, delegated)
        self.ledger.set_session_target(session, PIN)
        chat = self.ledger.create_work_item(issue_id=ISSUE, session_id=session, skill="chat")
        self.ledger.push_inbox(chat["id"], "修复，保留现有排序规则")
        token = self.ledger.claim(chat["id"], worker_id="conversation")["token"]
        return chat, token

    def request(self, chat, token, message_id=None):
        if message_id is None:
            message_id = self.ledger.issue_context(chat["id"])["session_messages"][-1]["id"]
        return self.ledger.request_repair(chat["id"], token, message_id, APP,
                                          "User requested repair. Investigated the sorting view; verify its model.")

    def test_first_repair_preserves_pin_messages_findings_and_retires_read_only_claim(self):
        chat, token = self.conversation()
        self.ledger.pop_inbox(chat["id"], token)
        self.ledger.await_input(chat["id"], token, "排序是否需要改变？")
        self.ledger.push_inbox(chat["id"], "不改变排序，修复显示", resume_waiting=True)
        token = self.ledger.claim(chat["id"], worker_id="conversation-2")["token"]
        fix = self.request(chat, token)
        self.assertEqual((fix["skill"], fix["state"], fix["session_id"]), ("fix", "queued", SESSION))
        self.assertEqual(fix["target"], PIN)
        self.assertEqual(self.ledger.item(chat["id"])["state"], "delivered")
        context = self.ledger.issue_context(fix["id"])
        self.assertEqual([m["body"] for m in context["session_messages"]],
                         ["修复，保留现有排序规则", "不改变排序，修复显示"])
        history = next(h for h in context["conversation_history"] if h["item_id"] == chat["id"])
        self.assertIn("sorting view", history["summary"])
        self.assertEqual(history["pending_question"], "排序是否需要改变？")
        with self.assertRaises(LedgerError):
            self.request(chat, token)
        self.assertEqual(len(self.ledger.queue()), 1)

    def test_mention_can_use_recorded_delegation_without_redelegating(self):
        chat, token = self.conversation(delegated=False, session="mention")
        self.ledger.ensure_session("delegated", ISSUE, True)
        self.ledger.set_session_target("delegated", PIN)
        fix = self.request(chat, token)
        self.assertEqual(fix["session_id"], "delegated")
        self.assertEqual(fix["target"], PIN)
        self.assertEqual(self.ledger.active_item_for_session("mention")["id"], fix["id"],
                         "replies and Stop in the original conversation must still reach the repair")

    def test_fresh_delegate_field_alone_does_not_grant_write_authority(self):
        chat, token = self.conversation(delegated=False)
        with self.assertRaisesRegex(LedgerError, "delegation session"):
            self.request(chat, token)
        self.assertEqual(self.ledger.item(chat["id"])["state"], "running")

    def test_foreign_delegation_session_cannot_authorize_this_issue(self):
        chat, token = self.conversation(delegated=False)
        self.ledger.observe_issue(issue(id=OTHER, delegate_id=APP))
        self.ledger.ensure_session("foreign", OTHER, True)
        with self.assertRaisesRegex(LedgerError, "delegation session"):
            self.request(chat, token)

    def test_closed_or_revoked_issue_does_not_retire_conversation(self):
        chat, token = self.conversation()
        for changes in ({"delegate_id": None}, {"status_type": "completed"}, {"archived": True}):
            self.ledger.observe_issue(issue(**({"delegate_id": APP} | changes)))
            with self.assertRaisesRegex(LedgerError, "open and delegated"):
                self.request(chat, token)
            self.assertEqual(self.ledger.item(chat["id"])["state"], "running")

    def test_newer_message_fences_a_stale_repair_decision(self):
        chat, token = self.conversation()
        message = self.ledger.issue_context(chat["id"])["session_messages"][-1]["id"]
        self.ledger.push_inbox(chat["id"], "先别修，只解释一下")
        with self.assertRaisesRegex(LedgerError, "latest message"):
            self.request(chat, token, message)
        self.assertEqual(self.ledger.item(chat["id"])["state"], "running")

    def test_invalid_message_token_and_expired_claim_cannot_start_repair(self):
        chat, token = self.conversation()
        for presented, message in (("wrong", 1), (token, 999)):
            with self.assertRaises(LedgerError):
                self.request(chat, presented, message)
        self.now += 61
        with self.assertRaisesRegex(LedgerError, "expired"):
            self.request(chat, token)

    def test_legacy_empty_prompt_is_not_an_actual_repair_request(self):
        chat, token = self.conversation()
        self.ledger.push_inbox(chat["id"], "（无正文）")
        with self.assertRaisesRegex(LedgerError, "real message"):
            self.request(chat, token)

    def test_stop_wins_over_pending_repair_request(self):
        chat, token = self.conversation()
        self.ledger.cancel(chat["id"], "Linear Stop")
        with self.assertRaises(LedgerError):
            self.request(chat, token)
        self.assertEqual(self.ledger.queue(), [])

    def test_late_message_follows_first_repair_handoff(self):
        chat, token = self.conversation()
        fix = self.request(chat, token)
        self.ledger.push_inbox(chat["id"], "Also check the animal tab")
        self.assertEqual(self.ledger.issue_context(fix["id"])["session_messages"][-1]["body"],
                         "Also check the animal tab")

    def test_completed_answer_remains_available_to_followup(self):
        chat, token = self.conversation()
        self.ledger.finish(chat["id"], token, "delivered",
                           {"summary": "Cause: missing refresh", "prs": [], "verification": "answered"})
        followup = self.ledger.create_work_item(issue_id=ISSUE, session_id=SESSION, skill="chat")
        self.ledger.push_inbox(followup["id"], "好，修一下")
        context = self.ledger.issue_context(followup["id"])
        self.assertEqual(context["conversation_history"][0]["summary"], "Cause: missing refresh")
        self.assertEqual(len(context["session_messages"]), 1)

    def test_request_resumes_previous_cancelled_fix_instead_of_unrelated_fresh_work(self):
        previous = self.new_item(delegate_id=APP)
        self.ledger.cancel(previous["id"], "Stop")
        chat, token = self.conversation()
        fix = self.request(chat, token)
        self.assertEqual(fix["predecessor_id"], previous["id"])
        self.assertEqual(self.ledger.item(previous["id"])["state"], "cancelled")

    def cli_request(self, chat, token):
        path = Path(self.tmp.name) / "summary.md"
        path.write_text("Fix the confirmed display issue; preserve sorting.", encoding="utf-8")
        message = self.ledger.issue_context(chat["id"])["session_messages"][-1]["id"]
        return parser().parse_args(["--db", str(self.path), "request-repair", "--item", chat["id"],
                                   "--token", token, "--message-id", str(message), "--summary-file", str(path)])

    def test_cli_checks_live_delegation_before_starting_first_repair(self):
        chat, token = self.conversation()
        args = self.cli_request(chat, token)
        remote = issue(delegate_id=None)
        sent = []
        api = SimpleNamespace(app_user_id=APP, fetch_issue=lambda _: remote,
                              create_activity=lambda session, content: sent.append((session, content)))
        with self.assertRaisesRegex(LedgerError, "delegated"):
            run(args, self.ledger, lambda: api)
        self.assertEqual(sent, [])
        remote["delegate_id"] = APP
        fix = run(args, self.ledger, lambda: api)
        self.assertEqual((fix["skill"], fix["state"]), ("fix", "queued"))
        self.assertEqual(sent[0][0], SESSION)
        self.assertEqual(sent[0][1], {"type": "thought", "body": "已排队开始或继续修改，会接着你的回复和已有调查结果处理。"})
        self.assertIn("summary", self.ledger.item(chat["id"])["evidence"])

    def test_cli_fences_stop_while_refreshing_linear(self):
        chat, token = self.conversation()
        args = self.cli_request(chat, token)
        def stopped(_):
            other = self.open_ledger()
            other.cancel(chat["id"], "Stop while fetching Linear")
            return issue(delegate_id=APP)
        api = SimpleNamespace(app_user_id=APP, fetch_issue=stopped)
        with self.assertRaisesRegex(LedgerError, "claim"):
            run(args, self.ledger, lambda: api)
        self.assertEqual(self.ledger.queue(), [])

    def test_cli_refuses_repair_on_a_host_that_does_not_run_fix(self):
        from unittest.mock import patch
        from agent.config import Config
        chat, token = self.conversation()
        args = self.cli_request(chat, token)
        sent = []
        api = SimpleNamespace(app_user_id=APP, fetch_issue=lambda _: issue(delegate_id=APP),
                              create_activity=lambda session, content: sent.append(content))
        resume = parser().parse_args(["--db", str(self.path), "resume-work", "--item", chat["id"], "--token", token,
                                      "--message-id", str(args.message_id)])
        with patch("agent.__main__.load_config", return_value=Config("c", "s", "w", enabled_skills=["chat"])):
            for command in (args, resume):
                with self.subTest(command=command.command), \
                        self.assertRaisesRegex(LedgerError, "repair execution is not available on this host"):
                    run(command, self.ledger, lambda: api)
        self.assertEqual((self.ledger.item(chat["id"])["state"], self.ledger.queue(), sent), ("running", [], []))
        with patch("agent.__main__.load_config", return_value=Config("c", "s", "w", enabled_skills=["chat", "fix"])):
            self.assertEqual(run(args, self.ledger, lambda: api)["skill"], "fix")

    def feature_card(self):
        return issue(delegate_id=APP, labels=["Code"], label_groups=[{"group": "Bot", "label": "Code"}])

    def test_cli_refuses_a_first_fix_on_a_code_card_and_says_why(self):
        """D18 f: a start request follows the Bot label, and no host runs feature yet, so nothing starts."""
        from unittest.mock import patch
        from agent.config import Config
        chat, token = self.conversation()
        args = self.cli_request(chat, token)
        sent = []
        api = SimpleNamespace(app_user_id=APP, fetch_issue=lambda _: self.feature_card(),
                              create_activity=lambda session, content: sent.append(content))
        with patch("agent.__main__.load_config", return_value=Config("c", "s", "w")):
            with self.assertRaises(LedgerError) as refused:
                run(args, self.ledger, lambda: api)
            self.assertEqual(str(refused.exception),
                             "this issue carries Bot/Code, so it is feature work, not a fix, and this instance does not "
                             "run feature yet")
            self.assertEqual((self.ledger.item(chat["id"])["state"], self.ledger.queue(), sent), ("running", [], []))
            # The same conversation on a Bug card without a Bot label gets its fix.
            api.fetch_issue = lambda _: issue(delegate_id=APP)
            self.assertEqual(run(args, self.ledger, lambda: api)["skill"], "fix")

    def test_cli_still_continues_an_earlier_fix_on_a_card_now_labelled_for_feature_work(self):
        """Only a first fix is refused: continuing the delegation's own fix job is unchanged."""
        from unittest.mock import patch
        from agent.config import Config
        previous = self.new_item(delegate_id=APP)
        self.ledger.cancel(previous["id"], "Stop")
        chat, token = self.conversation()
        api = SimpleNamespace(app_user_id=APP, fetch_issue=lambda _: self.feature_card(),
                              create_activity=lambda session, content: None)
        with patch("agent.__main__.load_config", return_value=Config("c", "s", "w")):
            fix = run(self.cli_request(chat, token), self.ledger, lambda: api)
        self.assertEqual((fix["skill"], fix["predecessor_id"]), ("fix", previous["id"]))

    def stub_api(self, current, calls=None):
        def fetch(ref):
            if calls is not None:
                calls.append(ref)
            return current
        return SimpleNamespace(app_user_id=APP, fetch_issue=fetch, create_activity=lambda session, content: None)

    def test_a_ui_label_outside_the_bot_group_does_not_block_a_first_fix(self):
        """Only the Bot group's children name a workflow; a bare `UI` label, or one of another group, does not."""
        for groups in ([], [{"group": "设计", "label": "UI"}]):
            with self.subTest(groups=groups):
                self.setUp()
                chat, token = self.conversation()
                api = self.stub_api(issue(delegate_id=APP, labels=["UI"], label_groups=groups))
                with patch("agent.__main__.load_config", return_value=Config("c", "s", "w")):
                    self.assertEqual(run(self.cli_request(chat, token), self.ledger, lambda: api)["skill"], "fix")

    def test_a_bug_card_that_also_carries_bot_code_is_refused_a_first_fix(self):
        chat, token = self.conversation()
        api = self.stub_api(issue(delegate_id=APP, labels=["Bug", "Code"], label_groups=[{"group": "Bot", "label": "Code"}]))
        with patch("agent.__main__.load_config", return_value=Config("c", "s", "w")):
            with self.assertRaisesRegex(LedgerError, "Bot/Code, so it is feature work, not a fix"):
                run(self.cli_request(chat, token), self.ledger, lambda: api)
        self.assertEqual((self.ledger.item(chat["id"])["state"], self.ledger.queue()), ("running", []))

    def test_a_change_card_or_one_without_a_bot_label_gets_its_fix(self):
        """D18 d and f: 修改, or no Bot label at all, is fix, whatever the labels for people say."""
        change = [{"group": "Bot", "label": "修改"}]
        for labels, groups in ((["Bug", "修改"], change), (["Improvement", "修改"], change), (["修改"], change),
                               (["Bug"], []), (["Improvement"], []), (["程序"], [{"group": "部门", "label": "程序"}]),
                               (["修改"], [{"group": "功能", "label": "修改"}])):
            with self.subTest(labels=labels, groups=groups):
                self.setUp()
                chat, token = self.conversation()
                api = self.stub_api(issue(delegate_id=APP, labels=labels, label_groups=groups))
                with patch("agent.__main__.load_config", return_value=Config("c", "s", "w")):
                    self.assertEqual(run(self.cli_request(chat, token), self.ledger, lambda: api)["skill"], "fix")

    def test_a_card_whose_bot_children_name_no_workflow_is_refused_a_first_start(self):
        for groups in ([{"group": "Bot", "label": "Art"}],
                       [{"group": "Bot", "label": "Code"}, {"group": "Bot", "label": "UI"}],
                       [{"group": "Bot", "label": "修改"}, {"group": "Bot", "label": "UI"}]):
            with self.subTest(groups=groups):
                self.setUp()
                chat, token = self.conversation()
                api = self.stub_api(issue(delegate_id=APP, labels=[g["label"] for g in groups], label_groups=groups))
                with patch("agent.__main__.load_config", return_value=Config("c", "s", "w")):
                    with self.assertRaisesRegex(LedgerError, "so it names no workflow"):
                        run(self.cli_request(chat, token), self.ledger, lambda: api)
                self.assertEqual((self.ledger.item(chat["id"])["state"], self.ledger.queue()), ("running", []))

    def test_an_earlier_feature_or_fgui_job_does_not_open_a_first_fix(self):
        """A conversation never continues an fgui job, and continues a feature job only where this instance runs
        feature, so neither lifts the refusal here: the request would otherwise start a first fix on a UI or Code
        card."""
        for skill, label in (("feature", "Code"), ("fgui", "UI")):
            with self.subTest(skill=skill):
                self.setUp()
                self.ledger.observe_issue(issue(delegate_id=APP))
                self.ledger.ensure_session(SESSION, ISSUE, True)
                earlier = self.ledger.create_work_item(issue_id=ISSUE, session_id=SESSION, skill=skill, target=PIN)
                self.ledger.cancel(earlier["id"], "Stop")
                chat, token = self.conversation()
                api = self.stub_api(issue(delegate_id=APP, labels=[label],
                                          label_groups=[{"group": "Bot", "label": label}]))
                with patch("agent.__main__.load_config", return_value=Config("c", "s", "w")):
                    with self.assertRaisesRegex(LedgerError, f"Bot/{label}, so it is {skill} work, not a fix"):
                        run(self.cli_request(chat, token), self.ledger, lambda: api)
                self.assertEqual(self.ledger.queue(), [])

    def test_only_a_delegation_sessions_fix_lifts_the_refusal(self):
        """A fix recorded in a mention session, as an operator's enqueue can leave one, is not resumable
        work, so it does not open a first start on a Code card."""
        self.ledger.observe_issue(issue(delegate_id=APP))
        self.ledger.ensure_session("mention", ISSUE, False)
        stray = self.ledger.create_work_item(issue_id=ISSUE, session_id="mention", skill="fix", target=PIN)
        self.ledger.cancel(stray["id"], "Stop")
        chat, token = self.conversation()
        api = self.stub_api(self.feature_card())
        with patch("agent.__main__.load_config", return_value=Config("c", "s", "w")):
            with self.assertRaisesRegex(LedgerError, "Bot/Code, so it is feature work, not a fix"):
                run(self.cli_request(chat, token), self.ledger, lambda: api)

    def test_a_request_from_a_work_item_gets_the_ledgers_refusal_not_the_labels(self):
        self.ledger.observe_issue(issue(delegate_id=APP))
        self.ledger.ensure_session(SESSION, ISSUE, True)
        fix = self.ledger.create_work_item(issue_id=ISSUE, session_id=SESSION, skill="fix", target=PIN)
        self.ledger.push_inbox(fix["id"], "继续")
        token = self.ledger.claim(fix["id"], worker_id="w")["token"]
        api = self.stub_api(self.feature_card())
        with patch("agent.__main__.load_config", return_value=Config("c", "s", "w")):
            with self.assertRaisesRegex(LedgerError, "requires an owned read-only chat item"):
                run(self.cli_request(fix, token), self.ledger, lambda: api)

    def test_the_old_group_name_is_still_refused_as_feature_work(self):
        chat, token = self.conversation()
        api = self.stub_api(issue(delegate_id=APP, labels=["Code"], label_groups=[{"group": "功能", "label": "Code"}]))
        with patch("agent.__main__.load_config", return_value=Config("c", "s", "w")):
            with self.assertRaisesRegex(LedgerError, "Bot/Code, so it is feature work, not a fix"):
                run(self.cli_request(chat, token), self.ledger, lambda: api)

    def test_a_configured_skill_the_checkout_lacks_stops_the_cli(self):
        chat, token = self.conversation()
        api = self.stub_api(issue(delegate_id=APP))
        with patch("agent.__main__.load_config", return_value=Config("c", "s", "w", enabled_skills=["chat", "fix", "feature"])):
            with self.assertRaisesRegex(SkillError, "does not have: feature"):
                run(self.cli_request(chat, token), self.ledger, lambda: api)
        self.assertEqual((self.ledger.item(chat["id"])["state"], self.ledger.queue()), ("running", []))

    def test_a_disabled_fix_is_refused_before_linear_is_read(self):
        chat, token = self.conversation()
        calls = []
        api = self.stub_api(issue(delegate_id=APP), calls)
        with patch("agent.__main__.load_config", return_value=Config("c", "s", "w", enabled_skills=["chat"])):
            with self.assertRaisesRegex(LedgerError, "repair execution is not available on this host"):
                run(self.cli_request(chat, token), self.ledger, lambda: api)
        self.assertEqual(calls, [])

    def feature_host(self, enabled=("chat", "fix", "feature")):
        """This checkout's skills plus Task 1's opt-in fixture `feature`, its AUTHORITY part, and a config whose
        enabled_skills is `enabled`: the worker CLI of a host that runs feature."""
        fixture = opt_in_skill(Path(self.tmp.name) / "fixture-skills")
        for patcher in (patch("agent.skills.load_skills", return_value={**SKILLS, fixture.name: fixture}),
                        patch.dict(SKILL_AUTHORITY, {fixture.name: "Fixture feature grants. "}),
                        patch("agent.__main__.load_config",
                              return_value=Config("c", "s", "w", enabled_skills=list(enabled)))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def earlier_feature_job(self):
        """The delegation's own job on the card, a feature job that was stopped."""
        self.ledger.observe_issue(issue(delegate_id=APP))
        self.ledger.ensure_session(SESSION, ISSUE, True)
        earlier = self.ledger.create_work_item(issue_id=ISSUE, session_id=SESSION, skill="feature")
        self.ledger.cancel(earlier["id"], "Stop")
        return earlier

    def change_card(self):
        return issue(delegate_id=APP, labels=["修改"], label_groups=[{"group": "Bot", "label": "修改"}])

    def resume_request(self, chat, token):
        message = self.ledger.issue_context(chat["id"])["session_messages"][-1]["id"]
        return parser().parse_args(["--db", str(self.path), "resume-work", "--item", chat["id"], "--token", token,
                                    "--message-id", str(message)])

    def test_a_request_continues_the_delegations_feature_job_where_feature_runs(self):
        """spec §9.4: the delegation's own job continues, whatever the label now says; this card was relabelled
        修改 after its feature job stopped, and gets no first fix."""
        earlier = self.earlier_feature_job()
        chat, token = self.conversation()
        self.feature_host()
        successor = run(self.cli_request(chat, token), self.ledger, lambda: self.stub_api(self.change_card()))
        self.assertEqual((successor["skill"], successor["predecessor_id"], successor["session_id"]),
                         ("feature", earlier["id"], SESSION))

    def test_resume_work_continues_a_feature_job_where_feature_runs(self):
        earlier = self.earlier_feature_job()
        chat, token = self.conversation(delegated=False, session="mention")
        self.feature_host()
        successor = run(self.resume_request(chat, token), self.ledger, lambda: self.stub_api(self.feature_card()))
        self.assertEqual((successor["skill"], successor["predecessor_id"]), ("feature", earlier["id"]))

    def test_a_job_whose_skill_this_host_does_not_run_is_not_continued_and_nothing_starts_instead(self):
        """Never a different skill (spec §9.4): where feature does not run, the delegation's feature job blocks a
        first fix on a 修改 card too, and both commands say why, before the ledger changes anything."""
        self.earlier_feature_job()
        chat, token = self.conversation()
        api = self.stub_api(self.change_card())
        with patch("agent.__main__.load_config", return_value=Config("c", "s", "w")):
            for command in (self.cli_request(chat, token), self.resume_request(chat, token)):
                with self.subTest(command=command.command):
                    with self.assertRaises(LedgerError) as refused:
                        run(command, self.ledger, lambda: api)
                    self.assertEqual(str(refused.exception),
                                     "this issue's earlier feature job continues only on an instance that runs "
                                     "feature, and this one does not")
        self.assertEqual((self.ledger.item(chat["id"])["state"], self.ledger.queue()), ("running", []))

    def recording_api(self, current):
        sent = []
        return sent, SimpleNamespace(app_user_id=APP, fetch_issue=lambda _: current,
                                     create_activity=lambda session, content: sent.append((session, content)))

    def test_a_request_on_a_code_card_starts_feature_where_it_runs(self):
        """D18 f: the Bot label chooses a first start. The job takes the delegation session and no client target
        (plan P6), and the acknowledgement promises no fix."""
        chat, token = self.conversation()
        self.feature_host()
        sent, api = self.recording_api(self.feature_card())
        feature = run(self.cli_request(chat, token), self.ledger, lambda: api)
        self.assertEqual((feature["skill"], feature["state"], feature["session_id"], feature["target"],
                          feature["predecessor_id"]), ("feature", "queued", SESSION, None, None))
        self.assertEqual([m["body"] for m in self.ledger.issue_context(feature["id"])["session_messages"]],
                         ["修复，保留现有排序规则"])
        self.assertEqual(sent, [(SESSION, {"type": "thought",
                                           "body": "已排队开始或继续这项工作，会接着你的回复和已有调查结果处理。"})])

    def test_a_conversation_a_mention_opened_starts_feature_only_through_a_recorded_delegation(self):
        """Bot label group design §4.7: the mention starts nothing itself; its conversation asks through the
        card's recorded delegation session, which the acknowledgement points to."""
        chat, token = self.conversation(delegated=False, session="mention")
        self.feature_host()
        sent, api = self.recording_api(self.feature_card())
        with self.assertRaisesRegex(LedgerError, "a recorded delegation session on this issue is required"):
            run(self.cli_request(chat, token), self.ledger, lambda: api)
        self.ledger.ensure_session("delegated", ISSUE, True)
        feature = run(self.cli_request(chat, token), self.ledger, lambda: api)
        self.assertEqual((feature["skill"], feature["session_id"]), ("feature", "delegated"))
        self.assertEqual(sent, [("mention", {"type": "response", "body": "已排队开始或继续这项工作，会接着你的回复和已有调查"
                                                                          "结果处理。后续进展记录在原委派会话和 issue 下。"})])

    def test_where_feature_runs_a_ui_card_still_starts_nothing_and_a_change_card_still_starts_fix(self):
        chat, token = self.conversation()
        self.feature_host()
        ui_card = issue(delegate_id=APP, labels=["UI"], label_groups=[{"group": "Bot", "label": "UI"}])
        with self.assertRaises(LedgerError) as refused:
            run(self.cli_request(chat, token), self.ledger, lambda: self.stub_api(ui_card))
        self.assertEqual(str(refused.exception), "this issue carries Bot/UI, so it is fgui work, not a fix, and this "
                                                 "instance does not run fgui yet")
        sent, api = self.recording_api(self.change_card())
        fix = run(self.cli_request(chat, token), self.ledger, lambda: api)
        self.assertEqual((fix["skill"], fix["target"]), ("fix", PIN))  # a fix keeps the delegation's target
        self.assertEqual(sent[0][1]["body"], "已排队开始或继续修改，会接着你的回复和已有调查结果处理。")

    def test_a_host_that_runs_feature_but_not_fix_reads_linear_then_refuses_a_first_fix(self):
        chat, token = self.conversation()
        self.feature_host(enabled=("chat", "feature"))
        calls = []
        with self.assertRaisesRegex(LedgerError, "repair execution is not available on this host"):
            run(self.cli_request(chat, token), self.ledger, lambda: self.stub_api(self.change_card(), calls))
        self.assertEqual((len(calls), self.ledger.item(chat["id"])["state"]), (1, "running"))
        feature = run(self.cli_request(chat, token), self.ledger, lambda: self.stub_api(self.feature_card()))
        self.assertEqual(feature["skill"], "feature")

    def test_the_ledger_starts_only_what_a_conversation_may_start_and_continues_whatever_it_finds(self):
        chat, token = self.conversation()
        message = self.ledger.issue_context(chat["id"])["session_messages"][-1]["id"]
        with self.assertRaisesRegex(LedgerError, "a conversation starts only fix or feature work"):
            self.ledger.request_repair(chat["id"], token, message, APP, "Make the panel.", start_skill="fgui")
        self.assertEqual(self.ledger.item(chat["id"])["state"], "running")
        self.ledger.cancel(chat["id"], "next case")
        previous = self.new_item(delegate_id=APP)
        self.ledger.cancel(previous["id"], "Stop")
        chat, token = self.conversation()
        message = self.ledger.issue_context(chat["id"])["session_messages"][-1]["id"]
        successor = self.ledger.request_repair(chat["id"], token, message, APP, "Continue.", start_skill="feature")
        self.assertEqual((successor["skill"], successor["predecessor_id"]), ("fix", previous["id"]))


class RepairReceiverTests(ReceiverBase):
    def test_farm_1261_delegation_question_reply_can_start_first_repair(self):
        self.api.fetch_issue.return_value = issue(labels=[], delegate_id=APP)
        self.receive(); self.receiver.process_one()
        items = self.ledger.items_for_session("session-1")
        self.assertEqual(len(items), 1, "delegation must retain a conversation, not a one-off elicitation")
        chat = items[0]
        self.assertEqual(chat["skill"], "chat")
        self.assertEqual(self.ledger.issue_context(chat["id"])["session_messages"], [])
        token = self.ledger.claim(chat["id"], worker_id="conversation")["token"]
        self.ledger.pop_inbox(chat["id"], token)
        self.ledger.await_input(chat["id"], token, "需要修复图鉴表现吗？")
        self.api.fetch_issue.return_value = issue(labels=["Bug"], delegate_id=APP)
        self.receive(self.event("prompted", body="修复")); self.receiver.process_one()
        token = self.ledger.claim(chat["id"], worker_id="conversation-2")["token"]
        message = self.ledger.issue_context(chat["id"])["session_messages"][-1]["id"]
        fix = self.ledger.request_repair(chat["id"], token, message, APP, "Repair the atlas display.")
        self.assertEqual((fix["skill"], fix["state"], fix["session_id"]), ("fix", "queued", "session-1"))
        self.assertEqual(self.ledger.issue_context(fix["id"])["session_messages"][-1]["body"], "修复")
