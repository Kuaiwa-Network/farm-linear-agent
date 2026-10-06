"""UI controller journeys with real native CLI/renderer and isolated Git/Linear fixtures.

The scripted worker tests orchestration and durable evidence, not model judgment,
real GitHub publication, licensed export or visual acceptance. Publication fences
have separate boundary tests; these origins are local URL-rewritten Git fixtures.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import textwrap
import unittest

from agent.config import load_config
from agent.launcher import _PINNED_EXIT
from agent.receiver import ACK
import test_feature_journey as journey
from test_ledger import DESIGNER, OWNER

ROOT = Path(__file__).resolve().parents[1]
PR = journey.ORG + "farmgui/pull/78"


@unittest.skipUnless(os.name == "nt" or _PINNED_EXIT,
                     "worker teardown requires CPython 3.13+ os.waitid or a Windows Job Object")
class UiJobJourneyTests(unittest.TestCase):
    def setUp(self):
        # Compose the established fixture without rediscovering/inheriting its Code tests.
        self.j = j = journey.CodeJobJourneyTests()
        self.addCleanup(j.doCleanups)
        j.setUp()
        j.stop_controller(j.c)
        source = j.config.source_path
        config = json.loads(source.read_text(encoding="utf-8"))
        config["enabled_skills"] = ["chat", "fix", "fgui"]
        source.write_text(json.dumps(config), encoding="utf-8")
        j.config = load_config(source)
        j.start_controller()
        j.refresh_issue(title="UI: reward panel", labels=["UI"], label_groups=[{"group": "Bot", "label": "UI"}])
        package = j.origins / "farmgui.git/assets/Reward"
        package.mkdir(parents=True)
        (package/"package.xml").write_text(
            '<packageDescription id="reward01"><resources>'
            '<component id="panel" name="Panel.xml" path="/" exported="true"/>'
            '</resources></packageDescription>', encoding="utf-8")
        (package/"Panel.xml").write_text(
            '<component size="16,12"><displayList><graph size="16,12" fillColor="#ff0000"/>'
            '</displayList></component>', encoding="utf-8")
        journey.git("add", "assets", cwd=j.origins/"farmgui.git")
        journey.git("commit", "-qm", "UI fixture", cwd=j.origins/"farmgui.git")
        self.round_helper = j.work / "round-evidence.py"
        self.round_helper.write_text(textwrap.dedent('''\
            import hashlib,json,sys
            from pathlib import Path
            checkpoint,report,notice,head=sys.argv[1:]
            p=Path(checkpoint); saved=json.loads(p.read_text(encoding="utf-8"))
            r=json.loads(Path(report).read_text(encoding="utf-8")); ui=saved["plan"]["ui"]
            sha=r["preview_sha256"]; digest=hashlib.sha256(json.dumps(r["source_hashes"],sort_keys=True).encode()).hexdigest()
            ui.update(head=head,source_digest=digest,preview_sha256=sha,pixels=r["pixels"],
                      assets=["https://uploads.linear.app/stub/"+sha],gaps=r["gaps"],kind=r["kind"])
            p.write_text(json.dumps(saved,ensure_ascii=False),encoding="utf-8")
            Path(notice).write_text("Approximate source preview, round "+str(ui["round"])+"\\n"+
                ui["assets"][0]+"\\nPR head: "+head+"\\nReply to continue; licensed export is pending.",encoding="utf-8")
            '''), encoding="utf-8")

    def park(self, item):
        j = self.j
        j.tick_until(lambda: j.c.ledger.item(item)["state"] == "awaiting_input" and not j.c.launcher.running(),
                     "UI worker to park and leave containment")
        j.plan = j.context(item)["plan"]

    def question(self, item):
        j = self.j
        j.plan = {"stages": {"A": "pending", "B": "pending", "C": "pending"},
                  "started": True, "ui": {"packages": ["Reward"], "components": ["panel"]},
                  "pause": {"kind": "answers", "question": "Which art is authoritative?"}}
        run, payload = j.attempt(item, [*j.intake(), *j.started(),
            ["download-uploads", "--item", "{item}", "--token", "{token}", "--out",
             str(j.c.launcher.state_dir(item)/"inputs/linear")],
            *j.notice("question", "art-1", "Missing reward icon; may we use a placeholder?"),
            *j.save("intake", stage="ui_intake", next_action="Resolve actual art"),
            *j.pause("question", "Please identify the actual art or approve a placeholder.")], "UI intake")
        self.park(item)
        return run, payload

    def round(self, item, number):
        j = self.j
        j.plan.update(stages={"A": "done", "B": "done", "C": "pending"},
                      prs={"farmgui": [j.entry("farmgui", "issue", PR)]},
                      pause={"kind": "visual_approval", "round": number},
                      ui={**j.plan.get("ui", {}), "round": number, "mockups": [], "approvals": []})
        output = j.c.launcher.state_dir(item)/"previews"/f"round-{number}"
        checkpoint = j.work/f"checkpoint-round-{number}.json"
        notice = j.work/f"visual-{number}.md"
        saved = j.save(f"round-{number}", stage="ui_visual", next_action="Wait for attributed visual approval", published=[PR])
        run, payload = j.attempt(item, [*j.intake(),
            *j.commit_and_push("farmgui", f"UI source round {number}"), saved[0],
            ["python", "-B", str(ROOT/"skills/fgui/tools/preview.py"), "--project-root", str(j.tree(item,"farmgui")),
             "--package", "Reward", "--component", "panel", "--output-root", str(output)],
            ["upload-image", "--item", "{item}", "--token", "{token}", "--file", str(output/"preview.png")],
            ["expect", "pixels", '{"width":16,"height":12}'],
            ["python", "-B", str(self.round_helper), str(checkpoint), str(output/"report.json"), str(notice),
             "{rev:farmgui:HEAD}"], saved[1],
            ["prepare-notice", "--item", "{item}", "--token", "{token}", "--kind", "waiting",
             "--request-id", f"visual-{number}", "--body-file", str(notice)],
            ["post-notice", "--item", "{item}", "--token", "{token}", "--request-id", f"visual-{number}"],
            *j.pause("waiting", f"Please reply about visual round {number}.")], f"UI visual round {number}")
        self.park(item)
        image = output/"preview.png"
        self.assertEqual(j.plan["ui"]["preview_sha256"], hashlib.sha256(image.read_bytes()).hexdigest())
        self.assertEqual(j.calls("upload_image")[-1]["sha256"], j.plan["ui"]["preview_sha256"])
        self.assertEqual(j.plan["ui"]["kind"], "approximate-source-preview")
        self.assertEqual(j.context(item)["published_prs"], [PR])
        return run, payload

    def assert_scope(self, item, run, payload):
        j = self.j
        self.assertEqual(set(payload["worktrees"]), {"farmgui"})
        self.assertEqual(set(payload["reads"]), {"Farm-Client"})
        self.assertEqual(payload["stage"]["write_repositories"], ["farmgui"])
        self.assertIsNone(payload["target"])
        self.assertIsNone(j.c.ledger.session(journey.SESSION)["target"])
        self.assertIsNone(payload["resource"])
        self.assertEqual(payload["tools"], {"lark_cli": {"profile": journey.PROFILE}})
        self.assertEqual(payload["execution"]["python"], os.sys.executable)
        self.assertEqual(j.head(j.reads(item,"Farm-Client")), j.origin_head("Farm-Client", "main"))
        self.assertNotIn(j.reads(item,"Farm-Client").resolve(), j.writable(run))
        self.assertEqual(j.c.ledger.reservations(), [])

    def test_intake_visual_correction_and_named_approval_stop_at_the_export_limit(self):
        j = self.j
        item = j.delegate()
        self.assertEqual(j.c.ledger.item(item)["skill"], "fgui")
        self.assertEqual(j.calls("create_activity")[0]["content"]["body"], ACK["fgui"].format(bot="FarmBot"))
        run, payload = self.question(item)
        self.assert_scope(item, run, payload)
        self.assertEqual(j.context(item)["pending_reason"], "question")
        self.assertEqual(len(j.calls("needs_more_info")), 1)
        j.human_comment("A red placeholder is acceptable for this authoring round.", author=DESIGNER)
        j.c.lifecycle.tick()
        self.assertEqual(j.c.ledger.item(item)["state"], "awaiting_input")
        j.reply("Use my placeholder answer and continue.", author=DESIGNER)
        run, payload = self.round(item, 1)
        self.assert_scope(item, run, payload)
        self.assertEqual(payload["user_requests"][-1]["author"], DESIGNER)
        first = copy.deepcopy(j.plan["ui"])
        first_head = j.head(j.tree(item,"farmgui"))
        # A correction requires a fresh explicit continuation and new actual pixels/source digest.
        j.human_comment("The preview must be green; round 1 is superseded.", author=DESIGNER)
        j.c.lifecycle.tick()
        self.assertEqual(j.c.ledger.item(item)["state"], "awaiting_input")
        j.reply("Apply the green correction, then show a new round.", author=DESIGNER)
        panel = j.tree(item,"farmgui")/"assets/Reward/Panel.xml"
        panel.write_text(panel.read_text(encoding="utf-8").replace("#ff0000", "#00ff00"), encoding="utf-8")
        journey.git("add", "assets/Reward/Panel.xml", cwd=j.tree(item,"farmgui"))
        j.plan["events"] = [{"kind": "visual_superseded", "round": 1, "preview_sha256": first["preview_sha256"]}]
        self.round(item, 2)
        self.assertNotEqual(j.plan["ui"]["preview_sha256"], first["preview_sha256"])
        self.assertNotEqual(j.plan["ui"]["source_digest"], first["source_digest"])
        self.assertNotEqual(j.plan["ui"]["head"], first_head)
        self.assertEqual(j.plan["ui"]["approvals"], [])
        self.assertEqual(len(j.started_comments()), 1)
        self.assertEqual(j.notices(item), [("art-1","question",True), ("visual-1","waiting",True), ("visual-2","waiting",True)])
        # The controller supplies actual message attribution; the scripted worker records it.
        j.reply("I approve round 2 as shown. Please export it.", author=DESIGNER)
        message = j.context(item)["session_messages"][-1]
        approved = {"round": 2, "preview_sha256": j.plan["ui"]["preview_sha256"],
                    "head": j.plan["ui"]["head"], "author": DESIGNER, "message_id": message["id"],
                    "created_at": message["created_at"]}
        j.plan["events"].extend([{**approved,"kind":"visual_approved"}, {**approved,"kind":"export_requested"}])
        j.plan["pause"] = {"kind":"stage_limit", "question":"Phase E export is unavailable."}
        run, payload = j.attempt(item, [*j.intake(),
            *j.save("accepted", stage="ui_visual", next_action="Phase E licensed export remains pending", published=[PR]),
            *j.pause("waiting", "Authoring accepted; Phase E export and Client/Unity checks remain pending.")], "UI approval")
        self.park(item)
        self.assert_scope(item, run, payload)
        self.assertEqual(payload["user_requests"][-1]["author"], DESIGNER)
        self.assertEqual(j.plan["events"][-1]["author"], DESIGNER)
        self.assertNotEqual(DESIGNER, OWNER)
        self.assertEqual(j.plan["pause"]["kind"], "stage_limit")
        self.assertEqual(j.handoffs(item), [])
        self.assertEqual(j.origin_branches("Farm-Client"), {"main"})
        self.assertEqual(len(j.calls("upload_image")), 2)

    def test_restart_and_notice_retry_reuse_the_exact_visual_notice_without_another_upload(self):
        j = self.j
        item = j.delegate()
        self.round(item, 1)
        before = copy.deepcopy(j.plan)
        remote = j.c.ledger.notices(item)[0]["remote_id"]
        body = (j.work/"visual-1.md").read_text(encoding="utf-8")
        comments, uploads = len(j.calls("create_comment")), len(j.calls("upload_image"))
        j.restart()
        self.assertEqual(j.context(item)["plan"], before)
        self.assertFalse(j.c.launcher.running())
        j.reply("Continue; reconcile the already posted preview.")
        run, payload = j.attempt(item, [*j.intake(), *j.notice("waiting", "visual-1", body),
            *j.save("retry", stage="ui_visual", next_action="Wait for round 1 approval", published=[PR]),
            *j.pause("waiting", "Round 1 still awaits your visual decision.")], "UI retry")
        self.park(item)
        self.assert_scope(item, run, payload)
        self.assertEqual(j.c.ledger.notices(item)[0]["remote_id"], remote)
        self.assertEqual(len(j.calls("create_comment")), comments)
        self.assertEqual(len(j.calls("upload_image")), uploads)
        self.assertEqual(j.plan["ui"], before["ui"])
        self.assertEqual(j.context(item)["published_prs"], [PR])

    def test_stop_contains_the_active_worker_and_successor_recovers_unpublished_ui_work(self):
        j = self.j
        item = j.delegate()
        self.round(item, 1)
        plan = copy.deepcopy(j.plan)
        published = j.origin_head("farmgui", journey.BRANCH)
        j.reply("Continue the authoring work.")
        busy = j.work/"busy-ui"
        run, _ = j.launch(item, [*j.intake(),
            *j.save("before-stop", stage="ui_visual", next_action="Finish the pending correction", published=[PR]),
            ["git", "farmgui", "commit", "--allow-empty", "-qm", "unpublished UI correction"],
            ["file", str(busy), "busy"], ["wait", str(j.work/"never-ui")],
            *j.notice("waiting", "must-not-post", "Late output must not reach Linear")], "active UI correction")
        j.wait_busy(run, busy, "UI correction")
        j.stop()
        self.assertEqual(j.c.ledger.item(item)["state"], "cancelled")
        j.tick_until(lambda: j.cleaned(item), "cancelled UI cleanup")
        self.assertFalse(j.c.launcher.running())
        self.assertNotIn("must-not-post", [row[0] for row in j.notices(item)])
        wip = j.recovery("farmgui", item)
        self.assertEqual(j.head(j.c.paths.repos/"farmgui.git", wip+"^"), published)
        self.assertEqual(len(j.calls("upload_image")), 1)
        j.reply("Continue the stopped UI card.")
        (chat,) = [row["id"] for row in j.c.ledger.items_for_session(journey.SESSION) if row["skill"] == "chat"]
        message = j.context(chat)["session_messages"][-1]["id"]
        summary = j.work/"ui-resume.md"
        summary.write_text("Continue UI from the retained draft and correction.", encoding="utf-8")
        j.attempt(chat, [["claim","--item","{item}","--worker-id","fake-chat"],
            ["request-repair","--item","{item}","--token","{token}","--message-id",str(message),
             "--summary-file",str(summary)]], "UI continuation request")
        (successor,) = [row for row in j.c.ledger.items_for_session(journey.SESSION)
                        if row["skill"] == "fgui" and row["id"] != item]
        self.assertEqual((successor["predecessor_id"], successor["target"]), (item, None))
        next_item = successor["id"]
        j.plan = j.context(next_item)["recovery"]["plan"]
        self.assertEqual(j.plan["ui"], plan["ui"])
        run, payload = j.attempt(next_item, [*j.intake(),
            *j.save("recovered", stage="ui_visual", next_action="Re-render unpublished correction", published=[PR]),
            *j.pause("waiting", "Retained correction requires a fresh visual round.")], "recovered UI worker")
        self.park(next_item)
        self.assert_scope(next_item, run, payload)
        j.assert_reattached(next_item, "farmgui", head=wip)
        self.assertEqual(len(j.calls("upload_image")), 1)
        self.assertEqual(j.context(next_item)["published_prs"], [PR])
