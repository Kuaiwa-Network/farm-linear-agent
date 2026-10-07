"""Exact Client/receipt/slot fencing around scripted actual MCP load results."""
import copy
import hashlib
import json
import unittest
from unittest.mock import Mock, patch

from agent.fgui_unity import loading_source, verify_loading
from agent.fgui_workflow import UiWorkflow
from agent.ledger import LedgerError
import test_fgui_client_workflow as client_fixtures


class UiUnityTests(unittest.TestCase):
    def setUp(self):
        self.fixture = f = client_fixtures.UiClientWorkflowTests(); f.setUp(); self.addCleanup(f.doCleanups)
        f.installed(); f.source.commit(f.client)
        self.head = f.source.git(f.client, "rev-parse", "HEAD")
        self.ledger, self.item, self.receipt = f.ledger, f.item, f.exported["receipt_id"]
        entry = {"id": "unity_slot:1", "folder": str(f.client), "build_target": "StandaloneWindows64"}
        f.source.config.slots = [entry]
        self.ledger.ensure_slot(entry["id"], kind="unity_slot", host="fixture", folder=str(f.client),
                                instance="fixture-instance", mcp_address="http://127.0.0.1:8080/mcp")
        self.ledger.await_resource(self.item, f.source.token, "unity_slot", "interactive", skill=f.source.skill,
                                   commit_sha=self.head, issue_prefix=f.source.config.issue_prefix)
        self.reservation = self.ledger.acquire("unity_slot", owner="fixture-controller", host="fixture")
        self.ledger.set_slot_state(entry["id"], "interactive_busy", parked_commit=self.head)
        self.ledger.resume(self.item, "fixture Unity acquired")
        f.source.token = self.ledger.claim(self.item, worker_id="ui-unity-fixture")["token"]
        self.workflow = UiWorkflow(self.ledger, self.item, f.source.token, f.source.config, f.source.skill,
                                   lambda: f.source.api, stage="Farm-Client")
        self.identity = Mock()
        snapshot = {"commit_sha": self.head, "dirty": [], "index_sha256": "a" * 64}
        self.identity.probe.return_value = {"aggregate": "match", "source_before": snapshot, "source_after": snapshot}
        self.descriptor_sha = hashlib.sha256((f.client / "Assets/GameRes/FairyRes/One/One_fui.bytes").read_bytes()).hexdigest()
        self.result = {"packages": [{"name": "One", "id": "pack0001", "items": 1, "disk_assets": 1,
                                    "descriptor_sha256": self.descriptor_sha}], "cleanup_complete": True}
        self.client = self.identity._client.return_value
        self.client.call_tool.side_effect = lambda *a, **kw: {"result": copy.deepcopy(self.result)}
        self.enterContext(patch("agent.fgui_unity.UnityIdentity", return_value=self.identity))

    def verify(self):
        return verify_loading(self.workflow, self.receipt)

    def test_current_reserved_committed_candidate_records_only_actual_loading(self):
        proof = self.fixture.source.cli('verify-ui-loading', '--receipt-id', self.receipt)
        self.assertEqual(proof["commit"], self.head)
        self.assertEqual(proof["reservation_id"], self.reservation["reservation_id"])
        call = self.client.call_tool.call_args
        self.assertEqual(call.args[0], "execute_code")
        self.assertEqual(call.args[1]["code"], loading_source({"One": "pack0001"}, {"One": self.descriptor_sha}))
        self.assertTrue(call.args[1]["safety_checks"])
        self.assertEqual(self.ledger.connection.execute("SELECT count(*) FROM audit WHERE kind='fgui_unity_complete'").fetchone()[0], 1)
        self.assertNotIn(self.fixture.source.token, json.dumps(proof))

    def test_wrong_or_ended_reservation_never_executes_loading(self):
        self.ledger.release(self.reservation["reservation_id"], self.reservation["token"], "fixture release")
        with self.assertRaisesRegex(LedgerError, "active interactive"): self.verify()
        self.client.call_tool.assert_not_called()

    def test_unknown_preprobe_identity_refuses_actual_loading(self):
        self.identity.probe.return_value["aggregate"] = "unknown"
        with self.assertRaisesRegex(LedgerError, "pre-probe"): self.verify()
        self.client.call_tool.assert_not_called()

    def test_wrong_missing_or_incomplete_loaded_package_and_cleanup_are_refused(self):
        for result in ({"packages": [], "cleanup_complete": True},
                       {"packages": self.result["packages"], "cleanup_complete": False},
                       {"packages": [{"name": "One", "id": "wrong001", "items": 1, "disk_assets": 1}], "cleanup_complete": True},
                       {"packages": [{"name": "One", "id": "pack0001", "items": True, "disk_assets": 1}], "cleanup_complete": True},
                       {"packages": [{"name": "One", "id": "pack0001", "items": 1, "disk_assets": 1,
                                      "descriptor_sha256": "0" * 64}], "cleanup_complete": True}):
            self.result = result
            with self.subTest(result=result), self.assertRaises(LedgerError): self.verify()
        self.assertEqual(self.ledger.connection.execute("SELECT count(*) FROM audit WHERE kind='fgui_unity_complete'").fetchone()[0], 0)

    def test_stop_during_loading_retains_no_success_record(self):
        def stopped(*args):
            self.ledger.cancel(self.item, "Stop during Unity UI load")
            return {"result": self.result}
        self.client.call_tool.side_effect = stopped
        with self.assertRaises(LedgerError): self.verify()
        self.assertEqual(self.ledger.connection.execute("SELECT count(*) FROM audit WHERE kind='fgui_unity_complete'").fetchone()[0], 0)

    def test_editor_source_or_identity_change_after_loading_is_refused(self):
        before = copy.deepcopy(self.identity.probe.return_value); after = copy.deepcopy(before)
        after["source_before"]["commit_sha"] = "b" * 40
        self.identity.probe.side_effect = [before, after]
        with self.assertRaisesRegex(LedgerError, "unchanged verified"): self.verify()


class UiLoadingSourceTests(unittest.TestCase):
    def test_package_input_cannot_inject_code_or_arbitrary_paths(self):
        for packages in ({'One"};Unsafe();': "pack0001"}, {"../escape": "pack0001"}, {"One": "bad"}, {}):
            with self.subTest(packages=packages), self.assertRaises(LedgerError): loading_source(packages, {})

    def test_probe_refuses_existing_registry_and_removes_only_owned_packages(self):
        source = loading_source({"One": "pack0001"}, {"One": "a" * 64})
        self.assertIn('GetById(pair.Value) != null', source)
        self.assertIn('finally', source)
        self.assertIn('object.ReferenceEquals', source)
        self.assertNotIn('RemoveAllPackages', source)
        self.assertIn('AssetDatabase.LoadAssetAtPath(item.file, resourceType)', source)
