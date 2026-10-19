import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent.config import StubLinear
from agent.issue_workflow import reconcile, request_update
from agent.ledger import LedgerError
from agent.lifecycle import Lifecycle
from agent.linear_api import LinearAPI
import test_ledger
import test_scheduler
import test_single_worker
from test_ledger import ISSUE, OTHER, TEAM, issue
from test_linear_api import APP, FakeHTTP


class WorkflowTests(test_ledger.LedgerBase):
    def pending(self):
        row = self.ledger.connection.execute('SELECT * FROM issue_workflow_updates WHERE issue_id=?',
                                             (ISSUE,)).fetchone()
        return dict(row) if row else None

    def api(self):
        snapshot = {**issue(delegate_id=APP), 'updated_at': '2026-10-19T00:00:00Z'}
        return SimpleNamespace(app_user_id=APP, issue_status=lambda _: snapshot,
                               set_issue_workflow_state=Mock(return_value=True))

    def finish(self, item, outcome='delivered', *, check_only=False):
        token = self.ledger.claim(item['id'], worker_id='worker')['token']
        action = self.ledger.prepare_comment(item['id'], token,
                                             'delivery' if outcome == 'delivered' else 'blocker', 'result')
        self.ledger.confirm_comment(action['action_id'], 'remote-comment')
        return self.ledger.finish(item['id'], token, outcome, {
            'summary': 'result', 'verification': 'checked', 'no_change': 'already fixed', 'prs': [],
            'comment_action_id': action['action_id']}, check_only=check_only)

    def test_queueing_does_not_change_status_and_first_worker_requests_progress(self):
        item = self.new_item()
        self.assertIsNone(self.pending())
        self.ledger.set_worker(item['id'], 1234, 'host')
        self.assertEqual(self.pending()['target'], 'In Progress')
        self.assertEqual(self.ledger.due_issue(), ISSUE)
        api = self.api()
        lifecycle = Lifecycle(self.ledger, api, SimpleNamespace(), clock=lambda: self.now)
        self.assertTrue(lifecycle.tick()['ok'])
        api.set_issue_workflow_state.assert_called_once()
        self.assertEqual(api.set_issue_workflow_state.call_args.args, (ISSUE, 'In Progress'))
        self.assertEqual(self.pending()['synced'], 1)

    def test_delivery_supersedes_pending_progress_and_is_polled_without_active_work(self):
        item = self.new_item()
        self.ledger.set_worker(item['id'], 1234, 'host')
        self.assertEqual(self.finish(item)['state'], 'delivered')
        self.assertEqual(self.pending()['target'], 'In Review')
        api = self.api()
        api.set_issue_workflow_state.side_effect = OSError('network secret')
        lifecycle = Lifecycle(self.ledger, api, SimpleNamespace(), clock=lambda: self.now)
        self.assertTrue(lifecycle.tick()['ok'])
        self.assertEqual(self.pending()['error'], 'OSError')
        self.assertEqual(self.pending()['synced'], 0)
        self.assertIsNone(self.ledger.due_issue())
        self.ledger.close()
        self.ledger = self.open_ledger()
        lifecycle.ledger = self.ledger
        self.now += 60
        api.set_issue_workflow_state.side_effect = None
        lifecycle.tick()
        self.assertEqual(self.pending()['synced'], 1)
        self.now += 60
        self.assertIsNone(self.ledger.due_issue())
        self.assertEqual([call.args[1] for call in api.set_issue_workflow_state.call_args_list],
                         ['In Review', 'In Review'])

    def test_sync_error_does_not_block_launch_preflight(self):
        item = self.new_item()
        self.ledger.set_worker(item['id'], 1234, 'host')
        api = self.api()
        api.set_issue_workflow_state.side_effect = OSError('offline')
        self.assertTrue(Lifecycle(self.ledger, api, SimpleNamespace(), clock=lambda: self.now).preflight(item))
        self.assertIsNone(self.ledger.status_check(ISSUE)['error'])
        self.assertEqual(self.pending()['synced'], 0)

    def test_worker_restarts_do_not_repeat_a_confirmed_transition(self):
        item = self.new_item()
        self.ledger.set_worker(item['id'], 1234, 'host')
        api = self.api()
        reconcile(self.ledger, api, ISSUE)
        self.ledger.set_worker(item['id'], 2345, 'host')
        reconcile(self.ledger, api, ISSUE)
        self.assertEqual(api.set_issue_workflow_state.call_count, 1)
        self.assertEqual(self.pending()['revision'], 1)

    def test_skipped_transition_can_be_requested_by_a_later_launch(self):
        item = self.new_item()
        self.ledger.set_worker(item['id'], 1234, 'host')
        api = self.api()
        api.set_issue_workflow_state.return_value = False
        reconcile(self.ledger, api, ISSUE)
        self.assertEqual(self.pending()['synced'], 2)
        self.ledger.set_worker(item['id'], 2345, 'host')
        self.assertEqual(self.pending()['synced'], 0)

    def test_check_only_does_not_request_review(self):
        item = self.new_item()
        self.ledger.set_worker(item['id'], 1234, 'host')
        self.finish(item, check_only=True)
        self.assertEqual(self.pending()['target'], 'In Progress')
        row = self.ledger._row(item['id'])
        self.assertEqual(row['state'], 'running')
        self.ledger.cancel(item['id'], 'stopped')
        reconcile(self.ledger, self.api(), ISSUE)
        self.assertEqual(self.pending()['synced'], 2)

    def test_blocked_finish_does_not_request_review(self):
        item = self.new_item()
        self.finish(item, 'blocked')
        self.assertIsNone(self.pending())

    def test_rejected_or_requeued_delivery_never_requests_review(self):
        item = self.new_item()
        token = self.ledger.claim(item['id'], worker_id='worker')['token']
        with self.assertRaises(LedgerError):
            self.ledger.finish(item['id'], token, 'delivered', {'summary': 'done'})
        action = self.ledger.prepare_comment(item['id'], token, 'delivery', 'result')
        self.ledger.confirm_comment(action['action_id'], 'remote')
        self.ledger.observe_issue(issue(description='changed requirements'))
        finished = self.ledger.finish(item['id'], token, 'delivered', {
            'summary': 'done', 'verification': 'checked', 'no_change': 'already fixed',
            'prs': [], 'comment_action_id': action['action_id']})
        self.assertEqual(finished['state'], 'queued')
        self.assertIsNone(self.pending())

    def test_chat_and_child_tasks_do_not_move_the_issue(self):
        chat = self.new_item(skill='chat')
        self.ledger.set_worker(chat['id'], 1234, 'host')
        self.assertIsNone(self.pending())
        row = dict(self.ledger._row(chat['id']))
        row.update(skill='fgui', parent_id='parent', authority='delegation')
        with self.ledger._transaction():
            request_update(self.ledger, row, 'In Review')
        self.assertIsNone(self.pending())

    def test_old_ledger_upgrade_creates_an_empty_queue_without_backfilling_deliveries(self):
        item = self.new_item()
        self.finish(item)
        self.ledger.connection.execute('DROP TABLE issue_workflow_updates')
        self.ledger.close()
        self.ledger = self.open_ledger()
        self.assertIsNone(self.pending())

    def test_cancelled_or_superseded_requests_are_discarded(self):
        item = self.new_item()
        self.ledger.set_worker(item['id'], 1234, 'host')
        self.ledger.cancel(item['id'], 'stopped')
        api = self.api()
        reconcile(self.ledger, api, ISSUE)
        api.set_issue_workflow_state.assert_not_called()
        self.assertEqual(self.pending()['synced'], 2)

    def test_delivery_arriving_during_sync_remains_pending(self):
        item = self.new_item()
        self.ledger.set_worker(item['id'], 1234, 'host')
        api = self.api()
        def deliver(*args, **kwargs):
            self.finish(item)
            self.assertFalse(kwargs['still_current']())
            return False
        api.set_issue_workflow_state.side_effect = deliver
        reconcile(self.ledger, api, ISSUE)
        self.assertEqual(self.pending()['target'], 'In Review')
        self.assertEqual(self.pending()['synced'], 0)

    def test_stub_controller_moves_the_card_from_start_through_confirmed_delivery(self):
        item = self.new_item(delegate_id=APP)
        directory = Path(self.tmp.name) / 'linear'
        directory.mkdir()
        (directory / f'issue-{ISSUE}.json').write_text(json.dumps(issue(delegate_id=APP)), encoding='utf-8')
        api = StubLinear(directory)
        lifecycle = Lifecycle(self.ledger, api, SimpleNamespace(), clock=lambda: self.now)
        self.ledger.set_worker(item['id'], 1234, 'host')
        self.assertTrue(lifecycle.tick()['ok'])
        self.assertEqual(api.fetch_issue(ISSUE)['status'], 'In Progress')
        self.finish(item)
        self.assertTrue(lifecycle.tick()['ok'])
        self.assertEqual(api.fetch_issue(ISSUE)['status'], 'In Review')

    def test_a_successor_requests_its_own_progress_transition(self):
        item = self.new_item()
        self.ledger.set_worker(item['id'], 1234, 'host')
        api = self.api()
        reconcile(self.ledger, api, ISSUE)
        self.ledger.cancel(item['id'], 'stopped')
        successor = self.ledger.retry(item['id'], 'resume')
        self.ledger.set_worker(successor['id'], 2345, 'host')
        self.assertEqual(self.pending()['item_id'], successor['id'])
        self.assertEqual(self.pending()['synced'], 0)
        reconcile(self.ledger, api, ISSUE)
        self.assertEqual(api.set_issue_workflow_state.call_count, 2)


class WorkflowLaunchTests(unittest.TestCase):
    setUp = test_scheduler.SchedulerTests.setUp
    item = test_scheduler.SchedulerTests.item

    def test_scheduler_requests_progress_only_after_a_successful_spawn(self):
        item = self.item()
        self.scheduler.tick()
        update = self.ledger.connection.execute('SELECT target FROM issue_workflow_updates').fetchone()
        self.assertEqual(update['target'], 'In Progress')
        self.assertEqual(self.launcher.spawned[0][0], item['id'])

    def test_spawn_failure_does_not_request_progress(self):
        self.item()
        with patch.object(self.launcher, 'spawn', side_effect=OSError('cannot launch')):
            self.scheduler.tick()
        self.assertIsNone(self.ledger.connection.execute('SELECT * FROM issue_workflow_updates').fetchone())


class SingleWorkerWorkflowTests(test_ledger.LedgerBase):
    owner = test_single_worker.SingleWorkerTests.owner
    scheduler = test_single_worker.SingleWorkerTests.scheduler
    pending = WorkflowTests.pending
    api = WorkflowTests.api
    finish = WorkflowTests.finish

    def test_current_owner_requests_progress_and_review_at_actual_delivery(self):
        self.owner(delegate_id=APP)
        scheduler = self.scheduler()
        scheduler.launch(self.item)
        self.assertEqual(self.pending()['target'], 'In Progress')
        message = self.launcher.spawned[0][1]
        self.assertIn('The controller moves delegated issue work to In Progress', message)
        self.assertIn('Workers must not set issue status themselves.', message)
        api = self.api()
        lifecycle = Lifecycle(self.ledger, api, scheduler, clock=lambda: self.now)
        self.assertTrue(lifecycle.tick()['ok'])
        self.assertEqual(self.finish(self.item)['state'], 'delivered')
        self.assertTrue(lifecycle.tick()['ok'])
        self.assertEqual([call.args[1] for call in api.set_issue_workflow_state.call_args_list],
                         ['In Progress', 'In Review'])

    def test_current_owner_completion_preflight_does_not_request_review(self):
        self.owner(delegate_id=APP)
        self.ledger.set_worker(self.key, 123, 'test')
        self.finish(self.item, check_only=True)
        self.assertEqual(self.ledger.item(self.key)['state'], 'running')
        self.assertEqual(self.pending()['target'], 'In Progress')


class WorkflowAPITests(unittest.TestCase):
    def issue(self, **changes):
        return dict(id=ISSUE, team={'id': TEAM}, archivedAt=None, trashed=False,
                    delegate={'id': APP}, state={'id': 'todo', 'name': 'Todo', 'type': 'unstarted'}, **changes)

    def state(self, name, state_id='target', team=TEAM):
        return {'id': state_id, 'name': name, 'type': 'started', 'team': {'id': team}}

    def api(self, target='In Progress', *, issues=None, states=None, mutation=None):
        issues = issues or [self.issue(), self.issue()]
        states = states if states is not None else [self.state(target)]
        pages = states if states and isinstance(states[0], list) else [states]
        answers = {
            'FarmBotWorkflowIssue': [{'data': {'issue': value}} for value in issues],
            'FarmBotWorkflowStates': [{'data': {'workflowStates': {'nodes': values, 'pageInfo': {
                'hasNextPage': i < len(pages) - 1, 'endCursor': str(i)}}}} for i, values in enumerate(pages)],
            'FarmBotSetWorkflowState': [{'data': {'issueUpdate': mutation if mutation is not None else {
                'success': True, 'issue': {'id': ISSUE, 'state': {'id': 'target'}}}}}],
        }
        self.http = FakeHTTP(answers)
        api = LinearAPI('client', 'secret', request=self.http)
        api.app_user_id = APP
        return api

    def operations(self):
        return [call[2] for call in self.http.calls if isinstance(call[2], dict)]

    def test_both_states_are_resolved_in_the_issues_team_and_only_state_id_is_written(self):
        for target in ('In Progress', 'In Review'):
            with self.subTest(target=target):
                api = self.api(target)
                self.assertTrue(api.set_issue_workflow_state(ISSUE, target))
                operations = self.operations()
                self.assertEqual(operations[1]['variables'], {'team': TEAM, 'after': None})
                self.assertEqual(operations[-1]['variables'], {'id': ISSUE, 'input': {'stateId': 'target'}})

    def test_states_are_paginated_and_other_teams_and_started_states_are_not_guessed(self):
        api = self.api(states=[[self.state('In Progress', team='other')], [self.state('In Progress')]])
        self.assertTrue(api.set_issue_workflow_state(ISSUE, 'In Progress'))
        self.assertEqual(self.operations()[2]['variables']['after'], '0')
        for states in ([], [self.state('In Review')], [self.state('In Progress'), self.state('In Progress', 'duplicate')]):
            api = self.api(states=states)
            with self.assertRaisesRegex(RuntimeError, 'exactly one'):
                api.set_issue_workflow_state(ISSUE, 'In Progress')

    def test_already_correct_status_makes_no_mutation(self):
        current = self.issue()
        current['state'] = self.state('In Progress')
        api = self.api(issues=[current, current])
        self.assertTrue(api.set_issue_workflow_state(ISSUE, 'In Progress'))
        self.assertFalse(any('mutation' in op['query'] for op in self.operations()))

    def test_closed_archived_trashed_and_undelegated_issues_are_never_changed(self):
        changes = [{'delegate': None}, {'delegate': {'id': 'other'}}, {'archivedAt': 'now'}, {'trashed': True}]
        changes += [{'state': {'id': 'closed', 'name': kind, 'type': kind}}
                    for kind in ('completed', 'canceled', 'duplicate')]
        for change in changes:
            for second_read in (False, True):
                with self.subTest(change=change, second_read=second_read):
                    changed = {**self.issue(), **change}
                    api = self.api(issues=[self.issue(), changed] if second_read else [changed])
                    self.assertFalse(api.set_issue_workflow_state(ISSUE, 'In Progress'))
                    self.assertFalse(any('mutation' in op['query'] for op in self.operations()))

    def test_superseded_request_is_fenced_before_mutation(self):
        api = self.api()
        current = Mock(side_effect=[True, False])
        self.assertFalse(api.set_issue_workflow_state(ISSUE, 'In Progress', still_current=current))
        self.assertFalse(any('mutation' in op['query'] for op in self.operations()))

    def test_mutation_requires_explicit_confirmation_and_exact_issue_and_state(self):
        for result in ({'success': False}, {'success': True},
                       {'success': True, 'issue': {'id': OTHER, 'state': {'id': 'target'}}},
                       {'success': True, 'issue': {'id': ISSUE, 'state': {'id': 'wrong'}}}):
            with self.subTest(result=result):
                with self.assertRaisesRegex(RuntimeError, 'did not confirm'):
                    self.api(mutation=result).set_issue_workflow_state(ISSUE, 'In Progress')

    def test_unknown_identity_or_target_is_refused_before_network_access(self):
        api = self.api()
        api.app_user_id = None
        with self.assertRaisesRegex(RuntimeError, 'verified app identity'):
            api.set_issue_workflow_state(ISSUE, 'In Progress')
        with self.assertRaises(ValueError):
            api.set_issue_workflow_state(ISSUE, 'Done')
        self.assertEqual(self.http.calls, [])
