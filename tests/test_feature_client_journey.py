"""Phase C controller journeys: local Git, scripted native helpers and fake Unity/MCP only.

The helpers model committed-input exports and UI identity reads. They do not certify real game
generators or Unity acceptance; those have separate native tooling and release records.
"""
import json
import os
import unittest
from pathlib import Path

import test_feature_journey as journey
from agent.slots import slot_entry
from agent.launcher import _PINNED_EXIT
from test_slots import FakeMcp, FakeUnity

CLIENT_PR = journey.ORG + "Farm-Client/pull/78"
WRITEBACK_PR = journey.ORG + "Farm-Contract/pull/14"
WRITEBACK = journey.BRANCH + "-writeback"

HELPER = '''import json, subprocess, sys
from pathlib import Path
mode, source, expected, destination = sys.argv[1:]
source, destination = Path(source), Path(destination)
def git(*args):
    return subprocess.run(["git", *args], cwd=source, check=True, capture_output=True,
                          text=True, encoding="utf-8").stdout.strip()
if mode in ("export", "export-config"):
    if git("rev-parse", "HEAD") != expected or git("status", "--porcelain", "--untracked-files=all"):
        raise SystemExit("input commit/cleanliness mismatch")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps({"contract_commit" if mode == "export" else "common_commit": expected}), encoding="utf-8")
elif mode == "ui":
    package = json.loads((source/"packages/Bonus/component.json").read_text(encoding="utf-8"))
    export = json.loads((destination/"Assets/GameRes/FairyRes/Bonus.export.json").read_text(encoding="utf-8"))
    if package != {"package": "pkg-bonus", "component": "button-bonus"} or export != package:
        raise SystemExit("UI/export identity mismatch")
elif mode in ("snapshot", "config-snapshot"):
    if git("rev-parse", "HEAD" if mode == "snapshot" else expected + "^{commit}") != expected:
        raise SystemExit("main snapshot mismatch")
    if not destination.exists():
        subprocess.run(["git", "clone", "--shared", "--no-checkout", str(source), str(destination)],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(destination), "checkout", "--detach", expected],
                       check=True, capture_output=True)
elif mode == "archive":
    if source.name != expected or destination.exists():
        raise SystemExit("archive identity/collision mismatch")
    before = (source/"specs/bonus.md").read_text(encoding="utf-8")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source.rename(destination)
    after = (destination/"specs/bonus.md").read_text(encoding="utf-8")
    if before != after:
        raise SystemExit("acceptance tails/counts changed")
else:
    raise SystemExit("unknown fixture mode")
'''


@unittest.skipUnless(os.name == "nt" or _PINNED_EXIT,
                     "a repository handoff needs os.waitid (CPython 3.13+ on macOS) or a Windows Job Object")
class FeatureClientJourneyTests(unittest.TestCase):
    def setUp(self):
        # Compose the established fixture without rediscovering/inheriting its thirteen Phase B tests.
        self.j = journey.CodeJobJourneyTests()
        self.addCleanup(self.j.doCleanups)
        self.j.setUp()
        self.helper = self.j.work / "native-client-fixture.py"
        self.helper.write_text(HELPER, encoding="utf-8")
        self.runner, self.mcp = FakeUnity(total=4, passed=4), FakeMcp()
        binary = self.j.root / "fake-unity"
        binary.touch()
        entry = slot_entry({"id": "unity_slot:1", "repo": "Farm-Client", "unity": str(binary)})
        pool = self.j.c.pool
        pool.entries = {entry["id"]: entry}
        pool.editors_root = self.j.root / "editors"
        pool.mcp, pool.run_unsandboxed = self.mcp, self.runner
        pool.editor_scan = lambda folder: None
        pool.editor_pid = lambda folder: None
        self.j.c.pool.ensure()
        self.j.c.scheduler.slot_entries = {entry["id"]: entry}

    def begin_client_only(self, *, ui=False, server=False):
        j = self.j
        item = j.delegate()
        j.run_contract(item, first=True, then="farm-hive", files={
            "openspec/changes/harvest-bonus/specs/bonus.md":
                "[DECIDED:Owner Two@2026-10-06]\n## 客户端侧要求\n[CLIENT-PENDING] Client PR needs merge.\n"
                "## 待裁决\n[UNREVIEWED] Presentation acceptance pending.\n"})
        self.server_work = server
        j.plan["stages"].update(D="done" if server else "skipped: client only", E="pending", F="pending")
        j.plan["ui"].update(has_ui=ui, packages=["Bonus"] if ui else [], components=["button-bonus"] if ui else [])
        return item

    def client_entry(self, item):
        j = self.j
        j.plan.pop("pause", None)
        j.plan["stages"]["E"] = "skipped: no UI" if not j.plan["ui"]["has_ui"] else "done"
        steps = j.intake()
        if self.server_work:
            j.plan["prs"]["farm-hive"] = [j.entry("farm-hive", "issue", journey.PRS["farm-hive"])]
            steps += [*j.fresh_base("farm-hive"), *j.commit_and_push("farm-hive", "Initial unmerged server input"),
                      *j.save("d-to-e", stage="server", next_action="UI readiness", published=[journey.PRS["farm-hive"]])]
            self.server_work = False
        result = j.attempt(item, [*steps, *j.save("e", stage="ui", next_action="enter Client"),
                                 *j.handoff("Farm-Client")], "UI stage to Client")
        j.settled(item, result)
        return result

    def export(self, item, source, expected, repo="Farm-Client"):
        return [["python", str(self.helper), "export", str(source), expected,
                 str(self.j.tree(item, repo) / "generated-input.json")],
                ["git", repo, "add", "generated-input.json"],
                ["git", repo, "commit", "--allow-empty", "-q", "-m", "Pinned consumer input"]]

    def verify_client(self, item, *, source=None, expected=None, round="draft"):
        j = self.j
        source = source or j.tree(item, "Farm-Contract")
        expected = expected or j.head(source)
        config_steps = []
        if j.plan.get("config", {}).get("sha"):
            common_commit = j.plan["config"]["sha"]
            snapshot = j.c.launcher.state_dir(item) / ("common-" + common_commit)
            config_steps = [["python", str(self.helper), "config-snapshot", str(j.c.paths.repos / "common.git"),
                             common_commit, str(snapshot)],
                            ["python", str(self.helper), "export-config", str(snapshot), common_commit,
                             str(j.tree(item, "Farm-Client") / "config-input.json")],
                            ["git", "Farm-Client", "add", "config-input.json"],
                            ["git", "Farm-Client", "commit", "--allow-empty", "-q", "-m", "Verified config input"]]
        j.plan["prs"]["Farm-Client"] = [j.entry("Farm-Client", "issue", None)]
        j.plan.setdefault("client", {}).update(contract_commit=expected, config_commit=j.plan.get("config", {}).get("sha"),
                                              verification={"phase": "requesting", "head": "{rev:Farm-Client:HEAD}"})
        run, payload = j.attempt(item, [*j.intake(), *self.export(item, source, expected), *config_steps,
                                       *j.save("f-request-" + round, stage="client", next_action="read batch result"),
                                       ["await-resource", "--item", "{item}", "--token", "{token}",
                                        "--resource", "unity_slot", "--mode", "batch", "--commit",
                                        "{rev:Farm-Client:HEAD}"]], "committed Client verification")
        j.settled(item, (run, payload))
        j.tick_until(lambda: item not in j.c.launcher.running(), "requesting worker teardown")
        baseline, head = payload["target"]["commit_sha"], j.head(j.tree(item, "Farm-Client"))
        self.assertEqual(j.c.ledger.item(item)["state"], "awaiting_resource")
        self.assertEqual(j.c.pool.tick()["granted"], 1)
        reservation = j.c.ledger.active_reservation(item)
        self.assertEqual(reservation["commit_sha"], head)
        j.plan["client"]["verification"] = {"phase": "released", "head": head,
                                             "reservation_id": reservation["reservation_id"]}
        released, proceed = j.work / ("released-" + round), j.work / ("quiescent-" + round)
        resumed, held = j.launch(item, [*j.intake(),
                                       *j.save("f-release-" + round, stage="client", next_action="publish after quiescence"),
                                       ["release-resource", "--item", "{item}", "--token-file", "{token_file}",
                                        "--outcome", "quiescent"],
                                       ["file", str(released), "released"], ["wait", str(proceed)],
                                       *self.publish_steps(item)], "reservation-bound Client worker")
        j.wait_busy(resumed, released, "released reservation")
        self.assertEqual(held["resource"]["commit"], head)
        summary = held["resource"]["batch_result"]
        self.assertEqual((summary["commit_sha"], summary["reservation_id"]), (head, reservation["reservation_id"]))
        self.assertTrue(self.runner.argv)
        self.assertNotIn(Path(j.c.ledger.slot(reservation["resource"])["folder"]).resolve(), j.writable(resumed))
        self.assertEqual(j.c.pool.tick()["parked"], 1)
        self.assertIsNone(j.c.ledger.active_reservation(item))
        proceed.touch()
        j.settle(item, resumed, "Client draft publication after quiescence")
        j.settled(item, (resumed, held))
        self.assertEqual(j.c.ledger.item(item)["target"]["commit_sha"], baseline)
        return baseline, head, payload

    def publish_steps(self, item):
        j = self.j
        j.plan["stages"]["F"] = "done"
        j.plan["prs"]["Farm-Client"] = [j.entry("Farm-Client", "issue", CLIENT_PR)]
        j.plan.setdefault("closing", {}).update(waivers_removed=True, client_resynced=False,
                                                hive_resynced=j.plan["stages"]["D"].startswith("skipped"),
                                                pin_written=True, writeback_opened=False)
        steps = [["git", "Farm-Client", "push", "-q", "origin", "HEAD:refs/heads/" + journey.BRANCH],
                 *j.save("f-publish", stage="closing", next_action="wait for Contract merge", published=[CLIENT_PR])]
        notice = j.plan["client"].get("closing_notice")
        if not notice:
            prior = [n[0] for n in j.notices(item)]
            request_id = "closing" if "closing" not in prior else "closing-2"
            notice = {"request_id": request_id, "body": "Client draft waits for verified Contract main and re-export."}
            j.plan["client"]["closing_notice"] = notice
        j.plan["pause"] = {"kind": "closing", "reason": "waiting", "notice": notice["request_id"], "since": journey.NOW}
        steps += [*j.save("f-park", stage="closing", next_action="wait for Contract merge"),
                  *j.notice("waiting", notice["request_id"], notice["body"]),
                  *j.pause("waiting", "Merge Contract and reply.")]
        return steps

    def test_no_ui_client_only_uses_latest_entry_main_and_reserved_committed_head(self):
        item = self.begin_client_only()
        j = self.j
        moved = j.origin_commit("Farm-Client", "Client main moved during server stages")
        self.assertFalse(j.tree(item, "Farm-Client").exists())
        self.client_entry(item)
        baseline, head, payload = self.verify_client(item)
        self.assertEqual(baseline, moved)
        self.assertIsNone(j.c.ledger.session(journey.SESSION)["target"])
        self.assertEqual(payload["stage"]["write_repositories"], ["Farm-Client"])
        self.assertNotEqual(head, baseline)
        self.assertEqual((j.c.ledger.item(item)["state"], j.plan["stages"]["E"]),
                         ("awaiting_input", "skipped: no UI"))
        self.assertEqual(j.context(item)["published_prs"], sorted([journey.PRS["Farm-Contract"], CLIENT_PR]))

    def test_ui_confirmation_rechecks_real_main_exports_and_reasks_without_client_entry(self):
        item, j = self.begin_client_only(ui=True), self.j
        package = j.origins / "farmgui.git" / "packages/Bonus/component.json"
        package.parent.mkdir(parents=True)
        identity = {"package": "pkg-bonus", "component": "button-bonus"}
        package.write_text(json.dumps(identity), encoding="utf-8")
        journey.git("add", ".", cwd=package.parents[2])
        journey.git("commit", "-qm", "UI component", cwd=package.parents[2])
        body = "Owner: confirm Bonus/button-bonus and its Client export."
        j.plan["ui"]["ready_notice"] = {"request_id": "ui-needed", "body": body, "owner": journey.OWNER}
        j.plan["pause"] = {"kind": "ui_ready", "reason": "waiting", "notice": "ui-needed", "since": journey.NOW}
        j.settled(item, j.attempt(item, [*j.intake(), *j.save("ui-wait", stage="ui", next_action="verify main exports"),
                                       *j.notice("waiting", "ui-needed", body), *j.pause("waiting", "Confirm UI ready.")], "UI wait"))
        j.human_comment("The package already exists.", author=journey.OWNER)
        self.assertEqual(j.c.ledger.item(item)["state"], "awaiting_input")
        j.reply("UI ready, please verify.")
        j.plan["pause"]["notice"] = "ui-needed-2"
        run, payload = j.attempt(item, [*j.intake(), *j.save("ui-reask", stage="ui", next_action="missing Client export"),
                                       *j.notice("waiting", "ui-needed-2", "Checked component; Client export missing."),
                                       *j.pause("waiting", "Publish Client export and reply.")], "missing UI export")
        j.settled(item, (run, payload))
        self.assertTrue((Path(payload["reads"]["farmgui"]) / "packages/Bonus/component.json").is_file())
        self.assertFalse((Path(payload["reads"]["Farm-Client"]) / "Assets/GameRes/FairyRes/Bonus.export.json").exists())
        self.assertFalse(j.tree(item, "Farm-Client").exists())
        exported = j.origins / "Farm-Client.git" / "Assets/GameRes/FairyRes/Bonus.export.json"
        exported.parent.mkdir(parents=True)
        exported.write_text(json.dumps(identity), encoding="utf-8")
        journey.git("add", ".", cwd=j.origins / "Farm-Client.git")
        journey.git("commit", "-qm", "UI exports ready", cwd=j.origins / "Farm-Client.git")
        j.reply("Exports ready now.")
        j.plan.pop("pause")
        j.plan["stages"]["E"] = "done"
        j.plan["ui"]["ready_event"] = j.context(item)["session_messages"][-1]["id"]
        j.plan["ui"]["evidence"] = {"farmgui": j.origin_head("farmgui", "main"),
                                     "client": j.origin_head("Farm-Client", "main")}
        steps = [*j.intake(), ["python", str(self.helper), "ui", str(j.reads(item, "farmgui")), "unused",
                              str(j.reads(item, "Farm-Client"))],
                 *j.save("ui-pass", stage="ui", next_action="Client implementation"), *j.handoff("Farm-Client")]
        j.settled(item, j.attempt(item, steps, "verified UI handoff"))
        self.assertEqual([n[0] for n in j.notices(item) if n[0].startswith("ui-needed")], ["ui-needed", "ui-needed-2"])
        self.verify_client(item)

    def test_wrong_export_input_preserves_output_and_creates_no_reservation_or_client_pr(self):
        item, j = self.begin_client_only(), self.j
        self.client_entry(item)
        output = j.tree(item, "Farm-Client") / "generated-input.json"
        run, payload = j.launch(item, [*j.intake(), ["file", str(output), "retained output"],
                                      ["python", str(self.helper), "export", str(j.tree(item, "Farm-Contract")),
                                       "0" * 40, str(output)]], "bad export pin")
        last = run / "last_message.txt"
        j.wait_for(last.is_file, "failed export result")
        self.assertIn("input commit/cleanliness mismatch", last.read_text(encoding="utf-8"))
        self.assertEqual(output.read_text(encoding="utf-8"), "retained output")
        self.assertEqual(j.c.ledger.reservations(), [])
        self.assertNotIn(CLIENT_PR, j.context(item)["published_prs"])

    def closing_round(self, style, *, server=False):
        item, j = self.begin_client_only(server=server), self.j
        self.client_entry(item)
        baseline, first_head, payload = self.verify_client(item)
        issue_input = j.head(j.tree(item, "Farm-Contract"))
        merged = j.merge_contract(style)
        self.assertEqual(j.is_ancestor(issue_input, merged), style == "merge")
        j.plan.pop("pause")
        j.plan["prs"]["Farm-Contract"][0]["pr"].update(state="merged", merge=style)
        j.plan["closing"]["contract_main_sha"] = merged
        state_snapshot = j.c.launcher.state_dir(item) / ("contract-main-" + merged)
        # The read-only main refresh must contain the actual merge. The issue tree stays on its unmerged HEAD.
        run, payload = j.attempt(item, [*j.intake(),
                                       ["python", str(self.helper), "snapshot", str(j.reads(item)), merged,
                                        str(state_snapshot)],
                                       *j.save("g-main", stage="closing", next_action="re-export verified main"),
                                       *j.handoff("Farm-Contract")], "select shared verified main")
        j.settled(item, (run, payload))
        self.assertEqual(j.head(j.reads(item)), merged)
        self.assertEqual(j.head(j.tree(item, "Farm-Contract")), issue_input)
        j.settled(item, j.attempt(item, [*j.intake(), *j.save("g-waivers", stage="closing", next_action="Client re-export"),
                                        *j.handoff("Farm-Client")], "no waiver work"))
        # A changed Contract input needs a fresh committed Client HEAD and a new reservation, even if bytes match.
        _, final_head, _ = self.verify_client(item, source=state_snapshot, expected=merged, round="main")
        self.assertNotEqual(final_head, first_head)
        self.assertEqual(j.c.ledger.item(item)["target"]["commit_sha"], baseline)
        j.reply("Proceed with the verified main write-back.")
        j.plan.pop("pause")
        j.plan["closing"]["client_resynced"] = True
        j.plan["closing"]["contract_main_sha"] = merged
        next_root = "farm-hive" if server else "Farm-Contract"
        j.settled(item, j.attempt(item, [*j.intake(), *j.save("g-client", stage="closing", next_action=next_root),
                                        *j.handoff(next_root)], "verified Client to next closing root"))
        client_input = json.loads((j.tree(item, "Farm-Client") / "generated-input.json").read_text(encoding="utf-8"))
        self.assertEqual(client_input["contract_commit"], merged)
        if server:
            j.plan["closing"]["hive_resynced"] = True
            j.settled(item, j.attempt(item, [*j.intake(), *self.export(item, state_snapshot, merged, repo="farm-hive"),
                                            ["git", "farm-hive", "push", "-q", "origin", "HEAD:refs/heads/" + journey.BRANCH],
                                            *j.save("g-hive", stage="closing", next_action="Contract write-back"),
                                            *j.handoff("Farm-Contract")], "same main input for hive"))
            hive_input = json.loads((j.tree(item, "farm-hive") / "generated-input.json").read_text(encoding="utf-8"))
            self.assertEqual(hive_input, client_input)
        writeback_entry = j.entry("Farm-Contract", "writeback", WRITEBACK_PR, branch=WRITEBACK)
        j.plan["prs"]["Farm-Contract"].append(writeback_entry)
        j.plan["closing"].update(writeback_branch=WRITEBACK, writeback_pr=WRITEBACK_PR, writeback_opened=True)
        j.plan["pause"] = {"kind": "closing", "reason": "waiting", "notice": "closing-writeback", "since": journey.NOW}
        acceptance = j.tree(item, "Farm-Contract") / "acceptance.json"
        steps = [*j.intake(), ["git", "Farm-Contract", "fetch", "-q", "origin"],
                 ["git", "Farm-Contract", "switch", "-q", "-c", WRITEBACK, "origin/main"],
                 ["file", str(acceptance), json.dumps({"contract_main": merged, "client_head": final_head,
                                                      "client_merge": "pending", "human_acceptance": "pending"})],
                 ["python", str(self.helper), "archive", str(j.tree(item, "Farm-Contract") / "openspec/changes/harvest-bonus"),
                  "harvest-bonus", str(j.tree(item, "Farm-Contract") / "openspec/changes/archive/2026-10-06-harvest-bonus")],
                 ["git", "Farm-Contract", "add", "openspec/changes"],
                 ["git", "Farm-Contract", "add", "acceptance.json"],
                 *j.commit_and_push("Farm-Contract", "Measured acceptance; retain pending human work", branch=WRITEBACK),
                 *j.save("g-writeback", stage="closing", next_action="human review", published=[WRITEBACK_PR]),
                 *j.notice("merge_request", "merge-writeback", "Review write-back draft; Client/human acceptance pending."),
                 ["git", "Farm-Contract", "switch", "-q", journey.BRANCH], *j.pause("waiting", "Review drafts.")]
        j.settled(item, j.attempt(item, steps, "write-back draft"))
        saved_head = j.origin_head("Farm-Contract", WRITEBACK)
        self.assertEqual(j.head(j.tree(item, "Farm-Contract")), issue_input)
        self.assertEqual(j.origin_head("Farm-Contract", WRITEBACK + "^"), merged)
        archived = journey.git("show", WRITEBACK + ":openspec/changes/archive/2026-10-06-harvest-bonus/specs/bonus.md",
                               cwd=j.origins / "Farm-Contract.git")
        for marker in ("DECIDED", "CLIENT-PENDING", "UNREVIEWED"):
            self.assertEqual(archived.count(marker), 1)
        self.assertIn("客户端侧要求", archived)
        self.assertIn("待裁决", archived)
        j.reply("Recheck the same draft.")
        # Retry reuses the registered role/head/notice and neither archives again nor creates another branch/PR.
        j.settled(item, j.attempt(item, [*j.intake(), ["git", "Farm-Contract", "switch", "-q", WRITEBACK],
                                       *j.notice("merge_request", "merge-writeback", "Review write-back draft; Client/human acceptance pending."),
                                       ["git", "Farm-Contract", "switch", "-q", journey.BRANCH],
                                       *j.pause("waiting", "Review drafts.")], "idempotent write-back retry"))
        self.assertEqual(j.origin_head("Farm-Contract", WRITEBACK), saved_head)
        self.assertEqual([n[0] for n in j.notices(item) if n[0] == "merge-writeback"], ["merge-writeback"])
        expected_prs = [journey.PRS["Farm-Contract"], CLIENT_PR, WRITEBACK_PR]
        if server:
            expected_prs.append(journey.PRS["farm-hive"])
        self.assertEqual(j.context(item)["published_prs"], sorted(expected_prs))
        j.reply("Deliver the reviewed evidence and pending merge list.")
        j.plan.pop("pause")
        j.plan["stages"]["G"] = "done"
        delivery, outcome = j.work / "client-delivery.md", j.work / "client-outcome.json"
        delivery.write_text(f"Client/main verification recorded at {merged}; drafts {expected_prs}. "
                            "Client/write-back merges and presentation human acceptance remain pending.", encoding="utf-8")
        result = {"summary": "Measured fixture evidence delivered; human merges pending.", "comment_action_id": "{action_id}",
                  "prs": expected_prs, "verification": "offline scripted export and fake Unity results at " + final_head}
        j.settled(item, j.attempt(item, [*j.intake(), *j.save("g-delivery", stage="closing", next_action="human acceptance"),
                                       ["prepare-comment", "--item", "{item}", "--token", "{token}", "--kind", "delivery", "--body-file", str(delivery)],
                                       ["post-comment", "--item", "{item}", "--token", "{token}", "--action-id", "{action_id}"],
                                       ["file", str(outcome), json.dumps(result)],
                                       ["finish", "--item", "{item}", "--token", "{token}", "--outcome", "delivered", "--input", str(outcome)]],
                                      "Client closing delivery"))
        self.assertEqual(j.c.ledger.item(item)["state"], "delivered")

    def test_merge_commit_client_resync_and_writeback_are_retry_safe(self):
        self.closing_round("merge", server=True)

    def test_squashed_merge_never_uses_unmerged_issue_head_for_client_or_writeback(self):
        self.closing_round("squash")

    def test_limit_after_e_parks_without_client_pin_until_explicit_continuation(self):
        item, j = self.begin_client_only(), self.j
        j.plan["stages"]["E"] = "skipped: no UI"
        j.plan["pause"] = {"kind": "stage_limit", "reason": "waiting", "notice": "stage-E", "since": journey.NOW}
        j.settled(item, j.attempt(item, [*j.intake(), *j.save("e-limit", stage="ui", next_action="wait for continuation"),
                                       *j.notice("stage", "stage-E", "Stage E skipped; stop here as requested."),
                                       *j.pause("waiting", "Reply to continue after E.")], "E limit"))
        self.assertFalse(j.tree(item, "Farm-Client").exists())
        self.assertIsNone(j.c.ledger.item(item)["target"])
        j.human_comment("Contract merged.", author=journey.OWNER)
        self.assertEqual(j.c.ledger.item(item)["state"], "awaiting_input")
        j.reply("Continue after E.")
        self.client_entry(item)
        self.verify_client(item)

    def test_stop_during_client_work_preserves_baseline_branch_and_dirty_recovery(self):
        item, j = self.begin_client_only(), self.j
        self.client_entry(item)
        j.plan["prs"]["Farm-Client"] = [j.entry("Farm-Client", "issue", None)]
        busy, release = j.work / "busy", j.work / "release"
        run, payload = j.launch(item, [*j.intake(), *j.save("f-busy", stage="client", next_action="continue implementation"),
                                      ["file", str(j.tree(item, "Farm-Client") / "partial-client.cs"), "partial"],
                                      ["file", str(busy), "busy"], ["wait", str(release)]], "running Client work")
        j.wait_busy(run, busy, "Client work")
        target = payload["target"]
        j.stop()
        j.tick_until(lambda: j.cleaned(item), "owned Client worker cleanup")
        self.assertEqual(j.c.ledger.item(item)["state"], "cancelled")
        self.assertEqual(j.c.ledger.item(item)["target"], target)
        retained = j.recovery("Farm-Client", item)
        clone = j.c.paths.repos / "Farm-Client.git"
        self.assertEqual(journey.git("show", retained + ":partial-client.cs", cwd=clone), "partial")
        successor = j.c.ledger.retry(item, "operator continued")
        self.assertEqual(successor["target"], target)
        self.assertEqual(j.c.ledger.recorded_branches(successor["id"])["Farm-Client"], journey.BRANCH)

    def test_unclean_feature_verification_holds_slot_and_retires_claim_without_publication(self):
        item, j = self.begin_client_only(), self.j
        self.client_entry(item)
        j.settled(item, j.attempt(item, [*j.intake(), *self.export(item, j.tree(item, "Farm-Contract"),
                                                                                j.head(j.tree(item, "Farm-Contract"))),
                                       *j.save("f-unclean-request", stage="client", next_action="verification"),
                                       ["await-resource", "--item", "{item}", "--token", "{token}",
                                        "--resource", "unity_slot", "--mode", "batch", "--commit", "{rev:Farm-Client:HEAD}"]],
                                      "unclean verification request"))
        j.tick_until(lambda: item not in j.c.launcher.running(), "request teardown")
        self.assertEqual(j.c.pool.tick()["granted"], 1)
        j.settled(item, j.attempt(item, [*j.intake(), *j.save("f-unclean", stage="client", next_action="controller recovery"),
                                       ["release-resource", "--item", "{item}", "--token-file", "{token_file}",
                                        "--outcome", "unclean"], ["expect", "state", '"recovery_queued"']],
                                      "unclean feature release"))
        self.assertEqual(j.c.ledger.slot("unity_slot:1")["state"], "held")
        self.assertNotEqual(j.c.ledger.item(item)["state"], "running")
        self.assertNotIn(CLIENT_PR, j.context(item)["published_prs"])

    def test_client_only_config_and_repin_keep_baseline_and_use_exact_new_input(self):
        j = self.j
        self.server_work = False
        item = j.delegate()
        j.run_contract(item, first=True)
        j.run_declarations(item)
        first_config = j.name_config_ref()
        j.run_config_check(item, server=False)
        # Continue a Phase B-style server-only checkpoint with explicit scope; the config remains a Client input.
        j.reply("Continue client-only work with this verified config.")
        j.plan["stages"].update(E="pending", F="pending")
        self.client_entry(item)
        baseline, _, _ = self.verify_client(item)
        data = json.loads((j.tree(item, "Farm-Client") / "config-input.json").read_text(encoding="utf-8"))
        self.assertEqual(data["common_commit"], first_config)
        self.assertTrue(j.plan["closing"]["hive_resynced"])
        self.assertTrue(j.plan["closing"]["pin_written"])
        origin = j.origins / "common.git"
        new_config = journey.git("commit-tree", first_config + "^{tree}", "-p", "main", "-m", "New designer data", cwd=origin)
        journey.git("update-ref", "refs/heads/designer/farm-1-new", new_config, cwd=origin)
        j.reply("Re-pin to the newly named designer commit.")
        j.plan.pop("pause")
        j.plan["stages"].update(C="pending", F="pending")
        j.plan["config"].update(sha=new_config, ref="designer/farm-1-new", jenkins_branch=journey.CONFIG_BRANCH + "-2")
        j.settled(item, j.attempt(item, [*j.intake(), *j.save("repin-client", stage="config", next_action="verify new config"),
                                        *j.handoff("common")], "Client re-pin handoff"))
        branch = journey.CONFIG_BRANCH + "-2"
        j.plan["prs"]["common"].append(j.entry("common", "config", None, branch=branch, head=branch))
        j.plan["stages"]["C"] = "done"
        j.settled(item, j.attempt(item, [*j.intake(), ["git", "common", "fetch", "-q", "origin"],
                                       ["git", "common", "switch", "-q", "-c", branch, new_config],
                                       ["git", "common", "push", "-q", "origin", "HEAD:refs/heads/" + branch],
                                       *j.save("repin-common", stage="config", next_action="Client export new data"),
                                       ["git", "common", "switch", "-q", journey.BRANCH], *j.handoff("Farm-Client")],
                                      "numbered config re-pin"))
        self.verify_client(item, round="repin")
        self.assertEqual(j.c.ledger.item(item)["target"]["commit_sha"], baseline)
        self.assertEqual(j.origin_head("common", journey.CONFIG_BRANCH), first_config)
        self.assertEqual(j.origin_head("common", branch), new_config)
        data = json.loads((j.tree(item, "Farm-Client") / "config-input.json").read_text(encoding="utf-8"))
        self.assertEqual(data["common_commit"], new_config)
