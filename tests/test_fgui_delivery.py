"""Real durable UI handoffs/installations with scripted guards, Editor and PR API."""
import unittest
import hashlib
import json
from unittest.mock import Mock, patch, create_autospec

from agent.fgui_delivery import verify_delivery
from agent.fgui_unity import verify_loading
from agent.fgui_workflow import UiWorkflow
from agent.ledger import LedgerError
from agent.publication import PublicationVerifier, PublicationError
from test_ledger import DESIGNER
import test_fgui_guards as guard_fixtures


class UiDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.guard_fixture = g = guard_fixtures.UiGuardWorkflowTests(); g.setUp(); self.addCleanup(g.doCleanups)
        self.fixture = f = g.fixture; self.ledger, self.item, self.receipt = f.ledger, f.item, f.exported['receipt_id']
        self.head = f.source.git(f.client, 'rev-parse', 'HEAD')
        self.client_url = 'https://github.com/example/Farm-Client/pull/2'
        f.source.plan['prs']['Farm-Client'] = [{'role': 'issue', 'branch': f.source.branch, 'head': self.head,
            'pr': {'url': self.client_url, 'state': 'draft', 'merge': None}}]
        f.source.save_plan()
        self.urls = [f.source.url, self.client_url]
        self.verifier = create_autospec(PublicationVerifier, instance=True)
        self.verifier.verify.return_value = {'head': self.head}
        self.verifier.verify_pr.return_value = self.client_url
        self.enterContext(patch('agent.fgui_delivery.PublicationVerifier', return_value=self.verifier))

    def complete_checks(self):
        f = self.fixture; self.guard_fixture.verify()
        entry = {'id': 'unity_slot:1', 'folder': str(f.client), 'build_target': 'StandaloneWindows64'}
        f.source.config.slots = [entry]
        self.ledger.ensure_slot(entry['id'], kind='unity_slot', host='fixture', folder=str(f.client),
                                instance='fixture-instance', mcp_address='http://127.0.0.1:8080/mcp')
        self.ledger.await_resource(self.item, f.source.token, 'unity_slot', 'interactive', skill=f.source.skill,
                                   commit_sha=self.head, issue_prefix=f.source.config.issue_prefix)
        reservation = self.ledger.acquire('unity_slot', owner='fixture-controller', host='fixture')
        self.ledger.set_slot_state(entry['id'], 'interactive_busy', parked_commit=self.head)
        self.ledger.resume(self.item, 'fixture acquired')
        f.source.token = self.ledger.claim(self.item, worker_id='ui-delivery-fixture')['token']
        f.workflow = UiWorkflow(self.ledger, self.item, f.source.token, f.source.config, f.source.skill,
                                lambda: f.source.api, stage='Farm-Client')
        identity = Mock(); snapshot = {'commit_sha': self.head, 'dirty': []}
        identity.probe.return_value = {'aggregate': 'match', 'source_before': snapshot, 'source_after': snapshot}
        identity._client.return_value.call_tool.return_value = {'result': {'packages': [
            {'name': 'One', 'id': 'pack0001', 'items': 1, 'disk_assets': 1,
             'descriptor_sha256': hashlib.sha256((f.client / 'Assets/GameRes/FairyRes/One/One_fui.bytes').read_bytes()).hexdigest()}],
            'cleanup_complete': True}}
        with patch('agent.fgui_unity.UnityIdentity', return_value=identity): verify_loading(f.workflow, self.receipt)
        self.ledger.release(reservation['reservation_id'], reservation['token'], 'fixture clean release')
        return verify_delivery(f.workflow, self.urls)

    def evidence(self, proof=None):
        return {'summary': 'Both UI drafts ready for human merges', 'verification': 'Guard and actual package-loading checks passed',
                'prs': self.urls, 'ui_delivery_id': (proof or {}).get('delivery_id', 'forged'), 'comment_action_id': 'not-posted'}

    def configured_github_origins(self):
        # Local owned clones and scripted API; no network or live publication.
        f = self.fixture
        for repo, path in (('farmgui', f.source.path), ('Farm-Client', f.client)):
            remote = 'https://github.com/example/' + repo + '.git'
            f.source.config.repos[repo] = remote
            f.workflow.trees.remotes[repo] = remote
            f.source.trees.remotes[repo] = remote
            f.source.git(path, 'remote', 'set-url', 'origin', remote)

    def test_handoff_install_guards_unity_release_and_both_drafts_certify_delivery(self):
        proof = self.complete_checks(); f = self.fixture
        self.assertEqual(proof['commit'], self.head); self.assertEqual(proof['prs'], sorted(self.urls))
        self.assertEqual(proof['approval']['author'], {key: DESIGNER[key] for key in ('id', 'name')})
        evidence = self.evidence(proof)
        action = self.ledger.prepare_comment(self.item, f.source.token, 'delivery', '\n'.join(self.urls) + '\nHuman merges remain.')
        remote = f.source.api.create_comment(f.source.fixture.item['issue_id'], action['body'])
        self.ledger.confirm_comment(action['action_id'], remote); evidence['comment_action_id'] = action['action_id']
        self.configured_github_origins()
        path = f.paths.runs / self.item / 'delivery.json'; path.write_text(json.dumps(evidence), encoding='utf-8')
        self.assertEqual(f.source.cli('finish', '--outcome', 'delivered', '--input', path)['state'], 'delivered')

    def test_real_publication_verifier_checks_owned_branch_and_actual_draft_metadata(self):
        self.complete_checks(); f = self.fixture
        self.configured_github_origins()
        metadata = {'full_name': 'example/Farm-Client', 'private': True, 'owner': {'login': 'example'},
                    'permissions': {'push': True}, 'html_url': 'https://github.com/example/Farm-Client', 'default_branch': 'main'}
        draft = {'html_url': self.client_url, 'state': 'open', 'draft': True,
                 'head': {'ref': f.source.branch, 'sha': self.head, 'repo': {'full_name': 'example/Farm-Client'}},
                 'base': {'ref': 'main', 'repo': {'full_name': 'example/Farm-Client'}}}
        def github(endpoint, **options):
            if '/branches/' in endpoint: return None
            if '/pulls/' in endpoint: return draft
            return metadata
        with patch('agent.fgui_delivery.PublicationVerifier', PublicationVerifier), patch('agent.publication.github_api', side_effect=github):
            self.assertEqual(verify_delivery(f.workflow, self.urls)['prs'], sorted(self.urls))
            draft['head']['sha'] = 'a' * 40
            with self.assertRaisesRegex(PublicationError, 'exact repository, branch and HEAD'):
                verify_delivery(f.workflow, self.urls)

    def test_cli_refuses_resource_before_guards_and_refuses_batch_loading(self):
        f = self.fixture
        with self.assertRaisesRegex(LedgerError, 'current controller'):
            f.source.cli('await-resource', '--resource', 'unity_slot', '--mode', 'interactive', '--commit', self.head)
        with self.assertRaisesRegex(LedgerError, 'interactive Unity'):
            f.source.cli('await-resource', '--resource', 'unity_slot', '--mode', 'batch', '--commit', self.head)
        self.assertEqual(self.ledger.reservations(), [])

    def test_invalid_finish_object_is_rejected_without_delivery_verification(self):
        f = self.fixture; path = f.paths.runs / self.item / 'invalid-finish.json'
        path.write_text('[]', encoding='utf-8')
        with self.assertRaisesRegex(LedgerError, 'finish input must be an object'):
            f.source.cli('finish', '--outcome', 'delivered', '--input', path)
        self.verifier.verify.assert_not_called()

    def test_worker_checkpoint_or_finish_prose_cannot_fabricate_guard_or_unity_pass(self):
        self.fixture.source.plan['ui']['verification'] = 'All checks passed'; self.fixture.source.save_plan()
        with self.assertRaisesRegex(LedgerError, 'current controller'): verify_delivery(self.fixture.workflow, self.urls)
        with self.assertRaisesRegex(LedgerError, 'full UI delivery'):
            self.ledger.finish(self.item, self.fixture.source.token, 'delivered', self.evidence())

    def test_new_commit_invalidates_old_guard_and_loading_results(self):
        self.complete_checks(); f = self.fixture
        f.source.git(f.client, 'commit', '--allow-empty', '-qm', 'new unverified head')
        with self.assertRaisesRegex(LedgerError, 'current controller'): verify_delivery(f.workflow, self.urls)

    def test_draft_inventory_must_match_exact_source_and_tested_client_pair(self):
        self.complete_checks()
        for urls in ([self.client_url], [*self.urls, 'https://github.com/example/foreign/pull/3']):
            with self.subTest(urls=urls), self.assertRaisesRegex(LedgerError, 'exactly its verified'):
                verify_delivery(self.fixture.workflow, urls)
        self.fixture.source.plan['prs']['Farm-Client'][0]['head'] = 'a' * 40
        self.fixture.source.save_plan()
        with self.assertRaisesRegex(LedgerError, 'exact current Client'): verify_delivery(self.fixture.workflow, self.urls)

    def test_damaged_controller_guard_record_cannot_certify_delivery(self):
        self.complete_checks()
        self.ledger.connection.execute("UPDATE audit SET details='{}' WHERE kind='fgui_guards_complete'")
        with self.assertRaisesRegex(LedgerError, 'damaged controller'): verify_delivery(self.fixture.workflow, self.urls)
