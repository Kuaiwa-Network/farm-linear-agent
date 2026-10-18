"""A single owner delivers fixes/features without stage or evidence choreography."""
import argparse
import json
from pathlib import Path
from unittest.mock import patch, Mock

from agent import dispatch
from agent.execution import CONTRACT, LEGACY, adopt, cancel_queued_resource, queue_resource, resource_ready, resource_status, skill_for
from agent.ledger import LedgerError
from agent.maintenance import Maintenance
from agent.scheduler import Scheduler
from agent.skills import load_skills
from agent.stages import write_repositories
from test_ledger import LedgerBase, ISSUE, SESSION, issue
from test_scheduler import FakeLauncher, FakeWorktrees

ROOT = Path(__file__).resolve().parents[1]
SKILLS = load_skills(ROOT / 'skills')
SHA = 'a' * 40
PR = 'https://github.com/example-org/Farm-Client/pull/123'


class SingleWorkerTests(LedgerBase):
    def test_default_registry_is_chat_and_one_general_owner(self):
        from agent.dispatch import SKILL_AUTHORITY
        from agent.skills import enabled_skills
        current = enabled_skills(SKILLS, None, authority=SKILL_AUTHORITY)
        self.assertEqual(set(SKILLS), {'chat', 'work'})
        self.assertEqual(set(current), {'chat', 'work'})
        self.assertFalse(current['work'].staged)
        from agent.stages import runtime_can_launch
        self.assertTrue(runtime_can_launch(current['work'], 'codex'))
        self.assertFalse(runtime_can_launch(current['work'], 'claude'))

    def test_current_chat_cannot_inherit_staged_guidance_or_publication_grants(self):
        prompt = dispatch.dispatch_message(item={'id': 'chat', 'skill': 'chat', 'execution_contract': LEGACY},
            issue={'identifier': 'FARM-1', 'url': 'https://linear.app/issue/FARM-1'},
            skill_path=SKILLS['chat'].skill_md, worktrees={'Farm-Client': '/read/client'},
            db_path='/state/db', runtime='codex', guidance='', repo_root=ROOT, state_dir='/state/chat',
            budget=SKILLS['chat'].budget, write_repositories=['Farm-Client'],
            publication={'repositories': {'Farm-Client': {'verified': True}}})
        payload = json.loads(prompt.split('\n\n', 1)[1])
        self.assertEqual(payload['stage']['write_repositories'], [])
        self.assertEqual(payload['publication'], {'repositories': {}})
        self.assertEqual(Path(payload['cli']), ROOT / 'references/chat-cli.md')
        self.assertEqual(Path(payload['skill']), SKILLS['chat'].skill_md)
        self.assertNotIn('references/legacy', prompt)
        self.assertNotIn('handoff-repository', prompt)
        self.assertNotIn('verify-candidate', prompt)
        self.assertIn('read-only conversation worker', prompt)
        self.assertIn('request-repair', prompt)
        self.assertIn('live delegation', prompt)

    def test_queued_request_cancels_on_maintenance_without_losing_claim(self):
        self.claim_owner()
        request = queue_resource(self.ledger, self.key, self.token, 'unity_slot', 'interactive', SHA)
        Maintenance(self.ledger).pause('upgrade')
        cancel_queued_resource(self.ledger, self.key, self.token, request['reservation_id'])
        self.assertEqual(self.ledger.renew(self.key, self.token)['state'], 'running')
        self.assertEqual(resource_status(self.ledger, self.key, self.token)['state'], 'cancelled')
        self.ledger.checkpoint(self.key, self.token, {'summary': 'candidate already tested',
            'reports': ['saved.json'], 'handoff': 'Publish retained candidate'})
        Maintenance(self.ledger).handoff(self.key, self.token)
        item = self.ledger.item(self.key)
        self.assertEqual(item['state'], 'queued')
        self.assertEqual(item['checkpoint']['reports'], ['saved.json'])
        with self.assertRaises(LedgerError):
            self.ledger.renew(self.key, self.token)

    def test_acquired_request_cannot_be_cancelled_as_queued(self):
        self.claim_owner()
        grant = self.reservation()
        with self.assertRaisesRegex(LedgerError, 'already acquired'):
            cancel_queued_resource(self.ledger, self.key, self.token, grant['reservation_id'])
        self.assertTrue(resource_status(self.ledger, self.key, self.token)['ready'])

    def test_recovery_reviews_reports_without_queuing_a_repeat_test(self):
        from agent.resource_recovery import RecoveryStore
        self.claim_owner()
        grant = self.reservation()
        self.ledger.hold(grant['reservation_id'], 'Editor disconnected')
        store = RecoveryStore(self.ledger)
        pending = store.pending('test')[0]
        recovery = store.begin(pending['id'])
        store.detach(recovery['id'], recovery['attempts'])
        item = self.ledger.item(self.key)
        self.assertEqual((item['state'], item['stage']), ('queued', 'resource-recovery-review'))
        self.assertEqual(self.ledger.reservations(states=('queued',)), [])
        self.assertEqual(item['checkpoint']['handoff'], 'Continue original candidate')
        self.assertEqual(store.job_attempts(self.key), 1)

    def test_monitor_and_progress_distinguish_live_resource_wait_from_relaunch(self):
        from agent.monitor_view import _display_state
        from agent.session_progress import SessionProgress
        self.claim_owner()
        progress = SessionProgress(self.ledger, Mock())
        queue_resource(self.ledger, self.key, self.token, 'unity_slot', 'interactive', SHA)
        item = self.ledger.item(self.key)
        self.assertEqual(_display_state(item, self.now), 'awaiting_resource')
        self.assertIn('同一 worker', progress._content(item)['body'])
        self.ledger.ensure_slot('unity:1', kind='unity_slot', host='test', folder='/slot')
        grant = self.ledger.acquire('unity_slot', owner='pool', host='test')
        self.ledger.set_slot_state('unity:1', 'interactive_busy')
        resource_ready(self.ledger, grant)
        self.assertEqual(_display_state(self.ledger.item(self.key), self.now), 'resource_in_use')
        self.assertNotIn('正在启动', progress._content(self.ledger.item(self.key))['body'])

    def test_adoption_preserves_reports_and_real_unanswered_question(self):
        old = self.new_item('fix')
        token = self.ledger.claim(old['id'], worker_id='old')['token']
        self.ledger.checkpoint(old['id'], token, {'summary': 'Work saved', 'reports': ['original.json']})
        self.ledger.await_input(old['id'], token, 'Should the feature use daily or weekly limits?')
        Maintenance(self.ledger).pause('adopt one issue')
        adopted = adopt(self.ledger, old['id'], Mock(spec=['assert_quiescent']), 'Use same source and reports')
        self.assertEqual(adopted['execution_contract'], CONTRACT)
        self.assertEqual(adopted['state'], 'awaiting_input')
        self.assertEqual(adopted['checkpoint']['legacy_checkpoint']['reports'], ['original.json'])
        self.assertIsNotNone(self.ledger.human_question(old['id']))

    def test_single_contract_refuses_legacy_transition_even_below_cli(self):
        self.claim_owner()
        legacy = load_skills(ROOT / 'skills', replay_legacy=True)['work']
        with self.assertRaisesRegex(LedgerError, 'retired'):
            self.ledger.handoff_repository(self.key, self.token, 'common', skill=legacy)
        with self.assertRaisesRegex(LedgerError, 'claim-bound'):
            self.ledger.await_resource(self.key, self.token, 'unity_slot', 'interactive', skill=legacy, commit_sha=SHA)

    def test_adoption_retires_technical_wait_but_preserves_its_context(self):
        old = self.new_item('fix')
        token = self.ledger.claim(old['id'], worker_id='old')['token']
        self.ledger.await_input(old['id'], token, 'Old closing report gate', reason='waiting')
        Maintenance(self.ledger).pause('adopt')
        adopted = adopt(self.ledger, old['id'], Mock(spec=['assert_quiescent']), 'Review retained reports')
        self.assertEqual(adopted['state'], 'queued')
        self.assertEqual(adopted['checkpoint']['legacy_checkpoint']['pending_question'], 'Old closing report gate')
        self.assertIsNone(self.ledger.human_question(old['id']))
        self.assertTrue(Maintenance(self.ledger).requested())

    def owner(self, **changes):
        self.ledger.observe_issue(issue(**changes))
        self.ledger.ensure_session(SESSION, ISSUE, delegation=True)
        self.item = self.ledger.create_work_item(issue_id=ISSUE, session_id=SESSION, skill='work')
        self.key = self.item['id']
        return self.item

    def claim_owner(self, **changes):
        self.owner(**changes)
        self.ledger.set_worker(self.key, 123, 'test')
        self.token = self.ledger.claim(self.key, worker_id='sole-owner')['token']
        self.ledger.checkpoint(self.key, self.token, {'summary': 'Investigated issue', 'handoff': 'Continue original candidate'})

    def scheduler(self):
        self.launcher = FakeLauncher(Path(self.tmp.name) / 'runs')
        self.trees = FakeWorktrees(self.tmp.name)
        return Scheduler(self.ledger, self.launcher, SKILLS, self.trees, skill_root=ROOT / 'skills',
            db_path=self.path, runtime_name='fake', host='test', enabled_skills={'work', 'chat'})

    def reservation(self):
        self.ledger.ensure_slot('unity:1', kind='unity_slot', host='test', folder='/slot',
                                instance='instance-one', mcp_address='http://127.0.0.1:8080/mcp')
        queued = queue_resource(self.ledger, self.key, self.token, 'unity_slot', 'interactive', SHA)
        granted = self.ledger.acquire('unity_slot', owner='pool', host='test')
        self.assertEqual(queued['reservation_id'], granted['reservation_id'])
        self.ledger.set_slot_state('unity:1', 'interactive_busy', parked_commit=SHA)
        resource_ready(self.ledger, granted)
        return granted

    def test_fresh_bug_and_feature_have_the_same_single_owner_contract(self):
        self.owner(labels=['Feature'])
        self.assertEqual(self.item['execution_contract'], CONTRACT)
        effective = skill_for(self.item, SKILLS['work'])
        self.assertFalse(effective.staged)
        self.assertEqual(set(write_repositories(self.item, effective)),
                         {'Farm-Contract', 'common', 'farm-hive', 'Farm-Client', 'farmgui'})
        self.assertFalse(effective.gates)

    def test_chat_request_starts_current_owner_and_checks_live_delegation(self):
        from agent.__main__ import parser, run
        from agent.config import Config
        from test_ledger import OWNER
        app = 'e5a8c16d-9f85-4123-acf5-94e41c3304d5'
        current = issue(delegate_id=app)
        self.ledger.observe_issue(current)
        self.ledger.ensure_session(SESSION, ISSUE, delegation=True)
        chat = self.ledger.create_work_item(issue_id=ISSUE, session_id=SESSION, skill='chat')
        self.ledger.push_inbox(chat['id'], 'Fix the confirmed display issue.', author=OWNER)
        token = self.ledger.claim(chat['id'], worker_id='chat')['token']
        mid = self.ledger.issue_context(chat['id'])['session_messages'][-1]['id']
        summary = Path(self.tmp.name) / 'summary.md'
        summary.write_text('Apply the requested display fix.', encoding='utf-8')
        args = parser().parse_args(['--db', str(self.path), 'request-repair', '--item', chat['id'],
            '--token', token, '--message-id', str(mid), '--summary-file', str(summary)])
        api = Mock(app_user_id=app)
        api.fetch_issue.return_value = {**current, 'delegate_id': None}
        with patch('agent.__main__.load_config', return_value=Config('c', 's', 'w')):
            with self.assertRaisesRegex(LedgerError, 'delegated'):
                run(args, self.ledger, lambda: api)
            api.create_activity.assert_not_called()
            api.fetch_issue.return_value = current
            work = run(args, self.ledger, lambda: api)
        self.assertEqual((work['skill'], work['execution_contract']), ('work', CONTRACT))
        self.assertEqual(self.ledger.item(chat['id'])['state'], 'delivered')
        scheduler = self.scheduler()
        scheduler.launch(work)
        self.assertEqual(len(self.launcher.spawned), 1)
        self.assertIn('single-worker-v1', self.launcher.spawned[0][1])

    def test_one_launch_exposes_all_owned_repositories_without_stage_guides(self):
        self.owner()
        scheduler = self.scheduler()
        scheduler.launch(self.item)
        self.assertEqual(len(self.launcher.spawned), 1)
        prompt = self.launcher.spawned[0][1]
        payload = json.loads(prompt.split('\n\n', 1)[1])
        self.assertEqual(len(payload['worktrees']), 5)
        self.assertEqual(payload['execution_contract'], CONTRACT)
        self.assertNotIn('capabilities', payload)
        self.assertNotIn('code-stages.md', prompt)
        self.assertNotIn('handoff-repository', prompt)
        self.assertIn('Use native Codex subagents at your discretion', prompt)
        self.assertIn('only you manage the FarmBot claim', prompt)
        self.assertIn('Do not create controller-managed presentation tasks', prompt)
        self.assertIn('Settle helpers before parking', prompt)
        self.assertIn('Draft/update contracts yourself when requirements are clear', prompt)
        self.assertIn('Normal resource queueing is not a human question', prompt)
        self.assertNotIn('Do not create subagents', prompt)
        self.assertEqual(self.launcher.spawned[0][4], str(self.launcher.state_dir(self.key)))

    def test_candidate_resource_keeps_claim_pid_and_needs_no_baseline_or_resume(self):
        self.claim_owner()
        before = self.ledger.item(self.key)
        grant = self.reservation()
        after = self.ledger.renew(self.key, self.token)
        self.assertEqual((after['state'], after['worker_pid'], after['generation']),
                         ('running', before['worker_pid'], before['generation']))
        self.assertTrue(resource_status(self.ledger, self.key, self.token)['ready'])
        self.assertIsNone(self.ledger.reservation_comparison(grant['reservation_id']))
        self.ledger.release(grant['reservation_id'], grant['token'], 'worker reported quiescent')
        self.assertFalse(resource_status(self.ledger, self.key, self.token)['ready'])
        self.ledger.renew(self.key, self.token)

    def test_resource_admission_uses_its_existing_worker_seat_at_capacity(self):
        self.owner()
        scheduler = self.scheduler()
        scheduler.max_concurrent = 1
        scheduler.launch(self.item)
        self.token = self.ledger.claim(self.key, worker_id='same-worker')['token']
        self.ledger.ensure_slot('unity:1', kind='unity_slot', host='test', folder='/slot')
        queue_resource(self.ledger, self.key, self.token, 'unity_slot', 'interactive', SHA)
        result = scheduler.acquire_unity(self.ledger, lambda db, kind, **opts: db.acquire(kind, **opts),
                                         'unity_slot', owner='pool', host='test')
        self.assertIsNotNone(result)
        self.assertEqual(len(self.launcher.spawned), 1)

    def test_delivery_accepts_worker_verification_without_legacy_closing_matrix(self):
        self.claim_owner()
        report = {'summary': 'Fix applied; affected behavior checked',
                  'verification': 'Affected tests pass. Two unchanged unrelated failures documented in report.json.',
                  'prs': [PR]}
        action = self.ledger.prepare_comment(self.key, self.token, 'delivery', 'Delivered ' + PR)
        self.ledger.confirm_comment(action['action_id'], 'remote-comment')
        report['comment_action_id'] = action['action_id']
        result = self.ledger.finish(self.key, self.token, 'delivered', report)
        self.assertEqual(result['state'], 'delivered')
        self.assertEqual(result['evidence']['verification'], report['verification'])

    def test_finish_still_refuses_held_resource_or_unconfirmed_delivery(self):
        self.claim_owner()
        report = {'summary': 'Done', 'verification': 'Tests pass', 'prs': [PR], 'comment_action_id': 'not-posted'}
        grant = self.reservation()
        with self.assertRaisesRegex(LedgerError, 'release the resource'):
            self.ledger.finish(self.key, self.token, 'delivered', report)
        self.ledger.release(grant['reservation_id'], grant['token'], 'worker reported quiescent')
        with self.assertRaisesRegex(LedgerError, 'confirmed comment'):
            self.ledger.finish(self.key, self.token, 'delivered', report)

    def test_delivery_requires_resolution_of_actual_human_question(self):
        from agent.__main__ import parser, run
        from test_ledger import OWNER
        self.claim_owner()
        body = 'The contract does not specify the reset period. Daily or weekly? I recommend daily.'
        args = parser().parse_args(['--db', str(self.path), 'await-input', '--item', self.key,
            '--token', self.token, '--question', body, '--reason', 'question'])
        api = Mock(app_user_id='test-app')
        api.issue_status.return_value = issue(delegate_id='test-app')
        api.awaiting_reply.return_value = True
        with patch('agent.__main__.configured_bot_name', return_value='TestBot'):
            parked = run(args, self.ledger, lambda: api)
        self.assertEqual(parked['state'], 'awaiting_input')
        self.assertEqual(parked['checkpoint']['handoff'], 'Continue original candidate')
        api.create_activity.assert_called_once()
        session, content = api.create_activity.call_args.args
        self.assertEqual((session, content['type']), (SESSION, 'elicitation'))
        self.assertIn(body, content['body'])
        question = self.ledger.human_question(self.key)
        self.ledger.push_inbox(self.key, 'Daily.', author=OWNER, resume_waiting=True)
        self.token = self.ledger.claim(self.key, worker_id='resumed-owner')['token']
        mid = self.ledger.issue_context(self.key)['session_messages'][-1]['id']
        action = self.ledger.prepare_comment(self.key, self.token, 'delivery', 'Delivered ' + PR)
        self.ledger.confirm_comment(action['action_id'], 'remote-comment')
        report = {'summary': 'Daily reset implemented', 'verification': 'Affected tests pass',
                  'prs': [PR], 'comment_action_id': action['action_id']}
        with self.assertRaisesRegex(LedgerError, 'actual human question'):
            self.ledger.finish(self.key, self.token, 'delivered', report)
        self.ledger.resolve_question(self.key, self.token, question['id'], [mid])
        self.assertEqual(self.ledger.finish(self.key, self.token, 'delivered', report)['state'], 'delivered')

    def test_stop_during_preparation_revokes_readiness_and_preserves_evidence(self):
        self.claim_owner()
        self.ledger.ensure_slot('unity:1', kind='unity_slot', host='test', folder='/slot')
        queue_resource(self.ledger, self.key, self.token, 'unity_slot', 'interactive', SHA)
        grant = self.ledger.acquire('unity_slot', owner='pool', host='test')
        self.ledger.cancel(self.key, 'operator Stop')
        with self.assertRaises(LedgerError):
            resource_ready(self.ledger, grant)
        self.assertEqual(self.ledger.reservation(grant['reservation_id'])['state'], 'cancel_requested')
        self.assertIsNotNone(self.ledger.item(self.key)['checkpoint'])

    def test_maintenance_cannot_start_another_resource(self):
        self.claim_owner()
        Maintenance(self.ledger).pause('deploy')
        with self.assertRaisesRegex(LedgerError, 'maintenance'):
            queue_resource(self.ledger, self.key, self.token, 'unity_slot', 'interactive', SHA)

    def test_legacy_contract_is_not_silently_rewritten_on_open(self):
        old = self.new_item('work', target=None)
        reopened = self.open_ledger()
        self.assertEqual(reopened.item(old['id'])['execution_contract'], LEGACY)

    def test_staged_commands_cannot_spawn_tasks_or_reintroduce_comparison_gates(self):
        from agent.__main__ import run
        self.claim_owner()
        for command in ('delegate-task', 'handoff-repository', 'verification-closeout', 'verify-candidate'):
            with self.subTest(command=command), self.assertRaisesRegex(LedgerError, 'retired'):
                run(argparse.Namespace(command=command, item=self.key), self.ledger, Mock())
