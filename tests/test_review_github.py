"""GitHub evidence identities, pagination and authority collision tests."""
import unittest

from agent.publication import PublicationUnavailable
from agent.review_github import GitHub
from agent.review_policy import ReviewError, encoded

SHA = 'a' * 40
REPOS = {'common': 'https://github.com/example/common.git'}
CHECK = {'name': 'tests', 'app_id': 42}


def check(identity=1, **changes):
    return {'id': identity, 'name': 'tests', 'app': {'id': 42}, 'head_sha': SHA,
            'status': 'completed', 'conclusion': 'success', **changes}


class GitHubTests(unittest.TestCase):
    def test_check_pagination_and_latest_trusted_producer_only(self):
        first = [check(n, app={'id': 99}) for n in range(1, 101)]
        second = [check(101), check(102, conclusion='failure')]
        calls = []
        def request(method, endpoint, body=None, **kwargs):
            calls.append(endpoint)
            return {'check_runs': first if endpoint.endswith('page=1') else second}
        value = GitHub(REPOS, request).checks('common', SHA, [CHECK])
        self.assertEqual(value['state'], 'failed')
        self.assertEqual(value['checks'][0]['id'], 102)
        self.assertEqual(len(calls), 2)

    def test_wrong_sha_or_app_and_missing_check_never_pass(self):
        for rows in ([], [check(head_sha='b' * 40)], [check(app={'id': 99})], [check(status='in_progress')]):
            with self.subTest(rows=rows):
                github = GitHub(REPOS, lambda *a, **kw: {'check_runs': rows})
                self.assertEqual(github.checks('common', SHA, [CHECK])['state'], 'waiting')

    def test_truncated_check_pagination_cannot_certify_an_old_green_check(self):
        github = GitHub(REPOS, lambda *a, **kw: {'total_count': 2, 'check_runs': [check()]})
        with self.assertRaisesRegex(ReviewError, 'all evidence'):
            github.checks('common', SHA, [CHECK])

    def test_cancelled_skipped_neutral_action_required_are_not_code_repairs_or_success(self):
        for conclusion in ('cancelled', 'skipped', 'neutral', 'action_required', 'stale', None):
            with self.subTest(conclusion=conclusion):
                github = GitHub(REPOS, lambda *a, **kw: {'check_runs': [check(conclusion=conclusion)]})
                self.assertEqual(github.checks('common', SHA, [CHECK])['state'], 'unavailable')

    def test_authority_ref_creation_lost_response_reconciles_exact_owner(self):
        owner = {'instance': 'production', 'controller': 'opaque', 'app': 'app', 'version': 1}
        calls, exists = [], False
        def request(method, endpoint, body=None, **kwargs):
            nonlocal exists
            calls.append((method, endpoint))
            if endpoint.endswith('/git/ref/tags/farmbot-review-authority'):
                return {'object': {'sha': SHA, 'type': 'tag'}} if exists else None
            if endpoint.endswith('/git/tags/' + SHA):
                return {'message': encoded(owner)}
            if endpoint == 'repos/example/common':
                return {'default_branch': 'main'}
            if endpoint.endswith('/git/ref/heads/main'):
                return {'object': {'sha': SHA}}
            if endpoint.endswith('/git/tags'):
                return {'sha': SHA}
            if endpoint.endswith('/git/refs'):
                exists = True
                raise PublicationUnavailable('response lost')
            raise AssertionError(endpoint)
        github = GitHub(REPOS, request)
        github.authority('common', owner)
        github.authority('common', owner)
        self.assertEqual(sum(method == 'POST' and endpoint.endswith('/git/refs') for method, endpoint in calls), 1)
        with self.assertRaises(ReviewError):
            github.authority('common', {**owner, 'controller': 'other host'})

    def test_notification_retries_missing_label_after_comment_succeeded(self):
        marker = '<!-- run -->'
        calls = []
        def request(method, endpoint, body=None, **kwargs):
            calls.append((method, endpoint, body))
            return [{'body': marker}] if method == 'GET' else {}
        GitHub(REPOS, request).notify({'repository': 'common', 'url': 'https://github.com/example/common/pull/1'}, marker, 'Handoff')
        self.assertEqual([v[2] for v in calls if v[0] == 'POST'], [{'labels': ['needs-person']}])

    def test_unresolved_review_thread_on_second_page_blocks(self):
        def request(method, endpoint, body=None, **kwargs):
            if method == 'GET':
                return []
            after = body['variables']['cursor']
            return {'data': {'repository': {'pullRequest': {'reviewThreads': {
                'nodes': [{'isResolved': not bool(after)}], 'pageInfo': {'hasNextPage': after is None, 'endCursor': 'next'}}}}}}
        github = GitHub(REPOS, request)
        self.assertTrue(github.blockers({'repository': 'common', 'url': 'https://github.com/example/common/pull/1'}))

    def test_bot_feedback_is_review_input_and_human_participation_still_blocks(self):
        author = {'__typename': 'Bot'}
        reviews = [{'id': 1, 'state': 'CHANGES_REQUESTED', 'body': 'Check overflow', 'user': {'id': 5, 'type': 'Bot'}}]
        def request(method, endpoint, body=None, **kwargs):
            if method == 'GET':
                return reviews
            return {'data': {'repository': {'pullRequest': {'reviewThreads': {
                'nodes': [{'id': 'thread1', 'isResolved': False, 'path': 'a.py', 'line': 2,
                           'comments': {'nodes': [{'id': 'comment1', 'body': 'Overflow', 'author': author}],
                                        'pageInfo': {'hasNextPage': False}}}],
                'pageInfo': {'hasNextPage': False, 'endCursor': None}}}}}}
        github = GitHub(REPOS, request)
        row = {'repository': 'common', 'url': 'https://github.com/example/common/pull/1'}
        self.assertFalse(github.blockers(row))
        self.assertEqual(len(github.bot_feedback(row)), 2)
        author['__typename'] = 'User'
        self.assertTrue(github.blockers(row))
        author['__typename'] = 'Bot'
        reviews[0]['user']['type'] = 'User'
        self.assertTrue(github.blockers(row))

    def test_rerun_only_accepts_exact_sha_and_suite_and_deduplicates_workflow(self):
        calls = []
        runs = [{'id': 12, 'head_sha': SHA, 'check_suite_id': 9, 'status': 'completed'}]
        def request(method, endpoint, body=None, **kwargs):
            calls.append((method, endpoint))
            return {'workflow_runs': runs} if method == 'GET' else {}
        github = GitHub(REPOS, request)
        ci = {'checks': [{'status': 'completed', 'conclusion': 'cancelled', 'check_suite_id': 9}] * 2}
        github.rerun('common', SHA, ci)
        self.assertEqual([c for c in calls if c[0] == 'POST'], [('POST', 'repos/example/common/actions/runs/12/rerun')])
        calls.clear()
        runs[0]['head_sha'] = 'b' * 40
        with self.assertRaisesRegex(ReviewError, 'recoverable'):
            github.rerun('common', SHA, ci)
        self.assertFalse(any(c[0] == 'POST' for c in calls))

    def test_standard_merge_commit_revert_is_attributed_without_guessing_partial_reverts(self):
        rows = [
            {'sha': 'b' * 40, 'commit': {'message': 'Revert feature\n\nThis reverts commit ' + SHA + '.'}},
            {'sha': 'c' * 40, 'commit': {'message': 'Revert merge\n\nThis reverts commit ' + SHA + ', reversing\nchanges made to ' + 'd' * 40 + '.'}},
            {'sha': 'e' * 40, 'commit': {'message': 'Revert part of ' + SHA}},
        ]
        github = GitHub(REPOS, lambda *a, **kw: rows)
        self.assertEqual(github.reverts('common', SHA, 0), ['revert:example/common:' + 'b' * 40, 'revert:example/common:' + 'c' * 40])
