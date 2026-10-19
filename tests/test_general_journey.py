"""General jobs through real CLI/worker containment and isolated Git/Linear."""
import json
import os
from pathlib import Path
import unittest

from agent.config import load_config
from agent.launcher import _PINNED_EXIT
import test_feature_journey as journey
from test_ledger import DESIGNER


@unittest.skipUnless(os.name == "nt" or _PINNED_EXIT,
                     "real worker teardown requires Windows Job Objects or POSIX pinned exit evidence")
class GeneralJobJourneyTests(unittest.TestCase):
    def setUp(self):
        self.j = j = journey.CodeJobJourneyTests()
        j.setUp()
        self.addCleanup(j.doCleanups)
        j.stop_controller(j.c)
        path = Path(j.config.source_path)
        config = json.loads(path.read_text(encoding="utf-8"))
        config["enabled_skills"] = ["chat", "work"]
        path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        j.config = load_config(path)
        j.start_controller()
        j.refresh_issue(labels=["Bug"], label_groups=[])

    def task_request(self, key="ui", capability="fgui"):
        path = self.j.work / f"task-{key}.json"
        path.write_text(json.dumps({"key": key, "capability": capability, **({"scope": {"assets": ["Assets/nursery.prefab"], "validation": "source"}} if capability == "scene" else {}),
            "instructions": "Use the shared OpenSpec; ask for actual nursery art and dimensions."}), encoding="utf-8")
        return path

    def test_server_and_ui_ask_independently_and_partial_reply_is_routed_before_stop(self):
        j = self.j
        parent = j.delegate()
        self.assertEqual(j.c.ledger.item(parent)["skill"], "work")
        self.assertIsNone(j.c.ledger.session(journey.SESSION)["target"])
        j.plan = {"stages": {"A": "done", "D": "pending"},
                  "prs": {"Farm-Contract": [j.entry("Farm-Contract", "issue", None)]}}
        contract = j.tree(parent, "Farm-Contract") / "presentation-contract.md"
        j.attempt(parent, [*j.intake(), ["file", str(contract), "Nursery: display the chosen seed before submission."],
            ["git", "Farm-Contract", "add", "presentation-contract.md"],
            *j.commit_and_push("Farm-Contract", "Shared nursery presentation contract"),
            *j.save("shared-spec", stage="contract", next_action="Server and UI"),
            ["delegate-task", "--item", "{item}", "--token", "{token}", "--input", str(self.task_request())],
            *j.handoff("farm-hive")], "settled contract")
        task = j.c.ledger.issue_context(parent)["tasks"][0]["id"]
        j.plan = j.context(parent)["plan"]  # retain the committed HEAD, not the scripted placeholder
        busy, proceed = j.work / "server-busy", j.work / "server-continue"
        server_script = [*j.intake(), ["file", str(busy), "server running"], ["wait", str(proceed)],
            *j.save("server-question", stage="server", next_action="Server answer needed"),
            *j.pause("question", "Server: which reward rule applies?")]
        ui_script = [*j.intake(), *j.save("ui-question", stage="ui_intake", next_action="Actual nursery art needed"),
            *j.pause("question", "UI: provide nursery art and dimensions.")]
        server_run, server_payload = j.launch(parent, {parent: server_script, task: ui_script}, "parallel server")
        j.tick_until(lambda: bool(j.runs(task)), "parallel UI launch")
        (ui_run,) = j.runs(task)
        j.wait_busy(server_run, busy, "server")
        j.settle(task, ui_run, "UI question")
        self.assertEqual(j.c.ledger.item(parent)["state"], "running")
        self.assertEqual(j.c.ledger.item(task)["state"], "awaiting_input")
        self.assertEqual(server_payload["stage"]["write_repositories"], ["farm-hive"])
        ui_payload = json.loads((ui_run / "prompt.md").read_text(encoding="utf-8").split("\n\n", 1)[1])
        self.assertEqual(ui_payload["stage"]["write_repositories"], ["farmgui"])
        self.assertTrue(Path(ui_payload["worktrees"]["farmgui"]).samefile(j.tree(parent, "farmgui")))
        self.assertEqual(ui_payload["task"]["parent_id"], parent)
        recorded = j.context(parent)["plan"]["prs"]["Farm-Contract"][0]["head"]
        contract_read = Path(ui_payload["reads"]["Farm-Contract"])
        self.assertEqual(journey.git("show", recorded + ":presentation-contract.md", cwd=contract_read),
                         "Nursery: display the chosen seed before submission.")
        self.assertEqual(j.head(contract_read), j.origin_head("Farm-Contract", "main"))
        self.assertNotIn(contract_read.resolve(), j.writable(ui_run))
        self.assertIsNone(j.c.ledger.item(parent)["target"])
        proceed.write_text("continue", encoding="utf-8")
        j.settle(parent, server_run, "server question")
        j.tick_until(lambda: not j.c.launcher.running(), "both parked")
        self.assertEqual(j.c.ledger.item(parent)["state"], "awaiting_input")
        self.assertEqual(j.c.ledger.item(task)["state"], "awaiting_input")
        j.reply("Use nursery-A.png. Width is still undecided.", author=DESIGNER)
        message = j.context(parent)["session_messages"][-1]
        j.plan = {"stages": {"A": "done", "D": "pending"}}
        coordinator = [*j.intake(), ["answer-task", "--item", "{item}", "--token", "{token}",
            "--task", "ui", "--message-id", str(message["id"])],
            *j.save("route-answer", stage="server", next_action="Remaining server/UI decisions"),
            *j.pause("question", "Server rule and UI width remain unanswered.")]
        partial_ui = [*j.intake(), *j.save("partial-ui", stage="ui_intake", next_action="Retain art choice, ask width"),
            *j.pause("question", "Nursery-A saved; width is still needed.")]
        j.attempt(parent, {parent: coordinator, task: partial_ui}, "answer routing")
        before = j.runs(task)
        j.tick_until(lambda: bool(j.runs(task) - before), "partial UI continuation")
        (resumed,) = j.runs(task) - before
        j.settle(task, resumed, "partial UI question")
        forwarded = j.context(task)["session_messages"][-1]
        self.assertEqual(forwarded["body"], message["body"])
        self.assertEqual(forwarded["author"], DESIGNER)
        self.assertEqual(forwarded["created_at"], message["created_at"])
        self.assertIn("width is still needed", j.context(task)["pending_question"])
        j.stop()
        j.tick_until(lambda: all((j.c.ledger.cleanup_record(i) or {}).get("done") for i in (parent, task)),
                     "whole-job Stop and teardown")
        self.assertEqual(j.c.ledger.item(parent)["state"], "cancelled")
        self.assertEqual(j.c.ledger.item(task)["state"], "cancelled")

    def test_scene_entry_uses_shared_client_checkout_without_unlocking_gameplay(self):
        j = self.j
        parent = j.delegate()
        j.plan = {"stages": {"A": "skipped: existing contract", "D": "pending"}}
        j.attempt(parent, [*j.intake(), *j.save("scene-spec", stage="contract", next_action="Prepare presentation"),
            ["delegate-task", "--item", "{item}", "--token", "{token}", "--input", str(self.task_request("scene", "scene"))],
            *j.pause("question", "Server decision needed")], "scene delegation")
        task = j.context(parent)["tasks"][0]["id"]
        run, payload = j.attempt(task, [*j.intake(), *j.save("scene-assets", stage="scene", next_action="Ask for scene art"),
            *j.pause("question", "Scene: required art is missing.")], "scene entry")
        child, owner = j.c.ledger.item(task), j.c.ledger.item(parent)
        self.assertEqual(payload["stage"]["write_repositories"], ["Farm-Client"])
        self.assertTrue(Path(payload["worktrees"]["Farm-Client"]).samefile(j.tree(parent, "Farm-Client")))
        self.assertEqual(child["target"], owner["target"])
        self.assertEqual(child["task_client_base"], j.head(j.tree(parent, "Farm-Client")))
        self.assertEqual(owner["root_repo"], None)
        self.assertEqual(owner["checkpoint"]["plan"]["stages"]["D"], "pending")
        j.stop()
        j.tick_until(lambda: not j.c.launcher.running(), "Stop")


if __name__ == "__main__":
    unittest.main()
