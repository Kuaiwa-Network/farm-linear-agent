"""A Client parent must be able to route the answer its unfinished UI task needs."""
import json
import unittest
from unittest.mock import patch

from agent.ledger import LedgerError
from agent.maintenance import Maintenance
from agent.stages import COORDINATION_ROOT, write_repositories
from agent.workflow import await_tasks, forward_answer, wake_parent
import test_general_scheduling as fixtures
import test_ledger as ledger_fixtures


class ParentCoordinationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.GeneralSchedulingTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.ledger, self.scheduler = self.fixture.ledger, self.fixture.scheduler
        self.parent, self.token = self.fixture.parent, self.fixture.token
        self.task = self.fixture.task()
        child_token = self.ledger.claim(self.task['id'], worker_id='ui')['token']
        self.ledger.await_input(self.task['id'], child_token, 'Confirm round 2')
        self.ledger.checkpoint(self.parent, self.token, {'plan': {'stages': {
            'A': 'done', 'B': 'skipped: unchanged', 'C': 'skipped: unchanged', 'D': 'done'}},
            'candidate': 'a' * 40, 'report': 'existing-playmode-report.json'})
        self.ledger.connection.execute("UPDATE work_items SET root_repo='Farm-Client' WHERE id=?", (self.parent,))
        self.ledger.connection.execute('UPDATE work_items SET target_json=? WHERE id=?',
            (json.dumps({**ledger_fixtures.PIN, 'issue_branch': 'farmbot/farm-1'}), self.parent))
        await_tasks(self.ledger, self.parent, self.token)
        wake_parent(self.ledger, self.parent)
        # Old releases already persisted exactly this queued Client-root state.
        self.scheduler.active.pop(self.parent)
        self.ledger.push_inbox(self.parent, 'Confirm round 2 and continue export',
                               author=ledger_fixtures.DESIGNER, received_at=123.0)
        self.message = dict(self.ledger.connection.execute('SELECT * FROM inbox WHERE item_id=?',
                                                          (self.parent,)).fetchone())

    def test_existing_queued_parent_routes_original_answer_without_client_write_or_retest(self):
        before = self.ledger.item(self.parent)
        with patch.object(self.scheduler, '_reads_for', side_effect=AssertionError('routing needs no Git refresh')), \
                patch.object(self.scheduler, '_recorded_branches', side_effect=AssertionError('keep existing worktrees')):
            self.assertEqual(self.scheduler.tick()['launched'], 1)
        item_id, prompt, servers, _, cwd = self.scheduler.launcher.spawned[-1]
        self.assertEqual(item_id, self.parent)
        payload = json.loads(prompt.split('\n\n')[-1])
        self.assertEqual(payload['stage']['write_repositories'], [])
        self.assertIsNone(payload['stage']['root_repository'])
        self.assertEqual(payload['worktrees'], {})
        self.assertEqual(cwd, str(self.scheduler.launcher.state_dir(self.parent)))
        self.assertEqual(servers, {})
        self.assertEqual(self.scheduler.launcher.spawn_writable, [str(self.scheduler.db_path.parent)])
        after = self.ledger.item(self.parent)
        for field in ('generation', 'target', 'checkpoint'):
            self.assertEqual(after[field], before[field])
        self.token = self.ledger.claim(self.parent, worker_id='coordinator')['token']
        forward_answer(self.ledger, self.parent, self.token, 'ui', self.message['id'])
        forward_answer(self.ledger, self.parent, self.token, 'ui', self.message['id'])
        messages = self.ledger.connection.execute('SELECT * FROM inbox WHERE item_id=?',
                                                 (self.task['id'],)).fetchall()
        self.assertEqual(len(messages), 1)
        for field in ('body', 'author_json', 'created_at'):
            self.assertEqual(messages[0][field], self.message[field])
        self.assertEqual(self.ledger.item(self.task['id'])['state'], 'queued')
        self.assertIsNone(self.ledger.active_reservation(self.parent))
        with self.assertRaisesRegex(LedgerError, 'presentation delivery'):
            self.ledger.handoff_repository(self.parent, self.token, 'Farm-Client', skill=fixtures.SKILLS['work'])

    def test_completion_returns_to_original_client_through_certified_handoff(self):
        self.scheduler.launch(self.ledger.item(self.parent))
        self.token = self.ledger.claim(self.parent, worker_id='coordinator')['token']
        forward_answer(self.ledger, self.parent, self.token, 'ui', self.message['id'])
        self.ledger.pop_inbox(self.parent, self.token)
        await_tasks(self.ledger, self.parent, self.token)
        self.scheduler.active.pop(self.parent)
        self.assertFalse(wake_parent(self.ledger, self.parent))
        child_token = self.ledger.claim(self.task['id'], worker_id='ui')['token']
        self.ledger.pop_inbox(self.task['id'], child_token)
        self.ledger.finish(self.task['id'], child_token, 'delivered', {
            'summary': 'Presentation verified', 'verification': 'Existing proof', 'no_change': 'No new delta'})
        self.assertFalse(wake_parent(self.ledger, self.parent))
        self.ledger.record_cleanup(self.task['id'], {}, done=True)
        self.assertTrue(wake_parent(self.ledger, self.parent))
        self.assertIsNotNone(self.scheduler.launch(self.ledger.item(self.parent)))
        self.token = self.ledger.claim(self.parent, worker_id='resume')['token']
        before = self.ledger.item(self.parent)
        self.ledger.checkpoint(self.parent, self.token, {**before['checkpoint'], 'handoff': {
            'facts': [], 'hypotheses': [], 'checks': [], 'repositories': [],
            'next_actions': ['Resume original candidate']}})
        pending = self.ledger.handoff_repository(self.parent, self.token, 'Farm-Client', skill=fixtures.SKILLS['work'])
        self.assertEqual(write_repositories(pending, fixtures.SKILLS['work']), ())
        with self.assertRaisesRegex(LedgerError, 'teardown'):
            self.ledger.claim(self.parent, worker_id='too-early')
        ready = self.ledger.complete_repository_handoff(self.parent, pending['worker_pid'], skill=fixtures.SKILLS['work'])
        self.assertEqual(write_repositories(ready, fixtures.SKILLS['work']), ('Farm-Client',))
        self.assertEqual(ready['target'], before['target'])
        self.assertEqual(ready['generation'], before['generation'])
        self.assertEqual(ready['checkpoint']['candidate'], 'a' * 40)
        self.assertEqual(ready['checkpoint']['report'], 'existing-playmode-report.json')

    def test_coordination_cannot_request_unity_or_server_resources(self):
        self.scheduler.launch(self.ledger.item(self.parent))
        self.token = self.ledger.claim(self.parent, worker_id='coordinator')['token']
        for kind, mode in [('unity_slot', 'interactive'), ('server_slot', 'batch')]:
            with self.subTest(kind=kind), self.assertRaises(LedgerError):
                self.ledger.await_resource(self.parent, self.token, kind, mode,
                                           skill=fixtures.SKILLS['work'], commit_sha='a' * 40)
        self.assertIsNone(self.ledger.active_reservation(self.parent))

    def test_maintenance_and_issue_scope_remain_authoritative(self):
        Maintenance(self.ledger).pause('operator maintenance')
        self.assertIsNone(self.scheduler.launch(self.ledger.item(self.parent)))
        self.assertEqual(self.ledger.item(self.parent)['root_repo'], 'Farm-Client')
        other = self.fixture.fixture.item(issue_id=ledger_fixtures.OTHER, session='other', identifier='FARM-2', skill='work')
        Maintenance(self.ledger).resume(only_issue='FARM-2')
        self.assertIsNone(self.scheduler.launch(self.ledger.item(self.parent)))
        self.assertNotEqual(other['id'], self.parent)
        self.assertEqual(self.ledger.item(self.parent)['root_repo'], 'Farm-Client')

    def test_withdrawal_does_not_become_coordination(self):
        self.ledger.connection.execute('UPDATE work_items SET withdraw_deadline=? WHERE id=?',
                                       (self.fixture.fixture.now + 1200, self.parent))
        self.assertIsNone(self.scheduler.launch(self.ledger.item(self.parent)))
        self.assertEqual(self.ledger.item(self.parent)['root_repo'], 'Farm-Client')

    def test_existing_resource_reservation_is_not_repurposed(self):
        self.ledger.ensure_slot('held-slot', kind='unity_slot', host='test', folder='unused')
        for state in ('queued', 'active', 'cancel_requested'):
            with self.subTest(state=state):
                self.ledger.connection.execute('DELETE FROM reservations WHERE item_id=?', (self.parent,))
                self.ledger.connection.execute('INSERT INTO reservations '
                    '(reservation_id,item_id,generation,kind,mode,state,commit_sha,created_at,resource,token_hash,owner) '
                    "VALUES(?,?,1,?,?,?,?,0,'held-slot','offline-token-hash','offline-worker')",
                    ('held', self.parent, 'unity_slot', 'interactive', state, 'a' * 40))
                self.assertIsNone(self.scheduler.launch(self.ledger.item(self.parent)))
                self.assertEqual(self.ledger.item(self.parent)['root_repo'], 'Farm-Client')

    def test_worker_cannot_select_coordination_root(self):
        self.scheduler.launch(self.ledger.item(self.parent))
        self.token = self.ledger.claim(self.parent, worker_id='coordinator')['token']
        with self.assertRaisesRegex(LedgerError, 'not a work target'):
            self.ledger.handoff_repository(self.parent, self.token, COORDINATION_ROOT, skill=fixtures.SKILLS['work'])

    def test_coordination_preserves_parent_context_instead_of_old_investigation(self):
        context = self.ledger.issue_context(self.parent)
        context['conversation_history'] = [{'skill': 'chat', 'summary': 'Old investigation'}]
        context['handoff'] = {'facts': [], 'next_actions': ['Route existing round 2 answer']}
        with patch.object(self.ledger, 'issue_context', return_value=context):
            self.scheduler.launch(self.ledger.item(self.parent))
        payload = json.loads(self.scheduler.launcher.spawned[-1][1].split('\n\n')[-1])
        self.assertEqual(payload['prior_context'], context['handoff'])

    def test_uncertain_old_worker_teardown_keeps_parent_parked(self):
        with patch.object(self.scheduler.launcher, 'assert_quiescent', side_effect=RuntimeError('descendant alive')):
            self.assertIsNone(self.scheduler.launch(self.ledger.item(self.parent)))
        self.assertEqual(self.ledger.item(self.parent)['root_repo'], 'Farm-Client')

