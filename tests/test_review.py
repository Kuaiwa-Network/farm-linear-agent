"""Offline review lifecycle, fencing and policy regression tests."""
import json
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from agent.ledger import Ledger
from agent.review_policy import ReviewError, digest, risk, settings
from agent.review_store import ReviewStore, summary
from test_ledger import issue

HEAD = 'a' * 40
BASE = 'b' * 40
APP = '90000000-0000-4000-8000-000000000001'


def policy(**changes):
    return settings({'mode': 'shadow', 'authority': 'test', 'repositories': {
        'common': {'backend': 'git_ff', 'method': 'merge', 'checks': [{'name': 'tests', 'app_id': 1}]}}, **changes})


class ReviewFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='review 空间 ')
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'ledger.sqlite3'
        self.now = 1000
        self.ledger = Ledger(self.path, clock=lambda: self.now)
        self.addCleanup(self.ledger.close)
        self.store = ReviewStore(self.ledger)

    def delivery(self, number=1, **changes):
        identifier = f'FARM-{number}'
        issue_id = str(uuid4())
        self.ledger.observe_issue(issue(id=issue_id, identifier=identifier, delegate_id=APP, **changes))
        session = 'session-' + str(number)
        self.ledger.ensure_session(session, issue_id, delegation=True)
        item = self.ledger.create_work_item(issue_id=issue_id, session_id=session, skill='work')
        token = self.ledger.claim(item['id'], worker_id='author')['token']
        self.ledger.checkpoint(item['id'], token, {'summary': 'implemented'})
        url = f'https://github.com/example/common/pull/{number}'
        comment = self.ledger.prepare_comment(item['id'], token, 'delivery', 'Delivered ' + url)
        self.ledger.confirm_comment(comment['action_id'], 'comment-' + str(number))
        inputs = [{'repo': 'common', 'url': url, 'head': HEAD, 'base': BASE,
                   'base_branch': 'main', 'branch': 'farmbot/' + identifier.lower()}]
        self.ledger.finish(item['id'], token, 'delivered', {'summary': 'done', 'verification': 'tests passed',
            'prs': [url], 'comment_action_id': comment['action_id']}, verified_prs=[url], review_inputs=inputs)
        delivery = dict(self.ledger.connection.execute('SELECT * FROM review_deliveries WHERE item_id=?', (item['id'],)).fetchone())
        run = self.store.enroll(delivery, digest(policy()))
        pr = dict(self.ledger.connection.execute('SELECT * FROM review_prs WHERE run_id=?', (run['id'],)).fetchone())
        return run, pr

    def candidate(self, pr, predecessors=(), letter='c'):
        return self.store.candidate(pr['id'], {'head': HEAD, 'base': BASE, 'prefix': BASE,
            'commit': letter * 40, 'tree': 'd' * 40, 'conflicts': False,
            'repository': 'common', 'target': 'main', 'url': pr['url']}, predecessors)

    def verdict(self, candidate, **changes):
        return {'snapshot': candidate['id'], 'risk': 'routine', 'reasons': [], 'findings': [],
                'summary': 'No introduced defects found', **changes}


class StoreTests(ReviewFixture):
    def test_verified_delivery_enrollment_is_atomic_and_idempotent(self):
        run, pr = self.delivery()
        delivery = dict(self.ledger.connection.execute('SELECT * FROM review_deliveries').fetchone())
        self.assertEqual(run['id'], self.store.enroll(delivery, digest(policy()))['id'])
        self.assertEqual(self.ledger.connection.execute('SELECT count(*) FROM review_prs').fetchone()[0], 1)
        self.assertTrue(self.store.current(run))
        self.assertEqual(pr['state'], 'queued')

    def test_handoff_is_delivered_and_cannot_be_revived_by_late_verdict(self):
        run, pr = self.delivery()
        snap = self.candidate(pr)
        attempt = self.store.attempt(snap['id'], 'review')
        token = self.store.claim(attempt['id'], 60)
        self.store.handoff(run['id'], 'high risk')
        with self.assertRaises(ReviewError):
            self.store.submit(attempt['id'], token, self.verdict(snap))
        done = self.store.row('review_runs', run['id'])
        self.assertEqual((done['state'], done['disposition']), ('delivered', 'human_handoff'))
        self.assertEqual(self.ledger.item(run['item_id'])['state'], 'delivered')

    def test_upstream_mutation_fences_all_speculative_descendants(self):
        pairs = [self.delivery(n) for n in (1, 2, 3)]
        a = self.candidate(pairs[0][1])
        b = self.candidate(pairs[1][1], [a['id']], 'e')
        c = self.candidate(pairs[2][1], [b['id']], 'f')
        attempt = self.store.attempt(c['id'], 'review')
        token = self.store.claim(attempt['id'], 60)
        with self.ledger._transaction():
            self.store.invalidate(a['id'], 'A was repaired')
        for snapshot in (a, b, c):
            self.assertEqual(self.store.row('review_candidates', snapshot['id'])['state'], 'stale')
        with self.assertRaises(ReviewError):
            self.store.submit(attempt['id'], token, self.verdict(c))

    def test_generation_and_input_changes_reject_verdicts(self):
        run, pr = self.delivery()
        snap = self.candidate(pr)
        attempt = self.store.attempt(snap['id'], 'review')
        token = self.store.claim(attempt['id'], 60)
        self.ledger.connection.execute('UPDATE work_items SET generation=generation+1 WHERE id=?', (run['item_id'],))
        with self.assertRaises(ReviewError):
            self.store.submit(attempt['id'], token, self.verdict(snap))

    def test_claim_expires_and_wrong_snapshot_cannot_be_submitted(self):
        _, pr = self.delivery()
        snap = self.candidate(pr)
        attempt = self.store.attempt(snap['id'], 'review')
        token = self.store.claim(attempt['id'], 60)
        with self.assertRaises(ReviewError):
            self.store.submit(attempt['id'], token, self.verdict(snap, snapshot='wrong'))
        self.now += 61
        with self.assertRaises(ReviewError):
            self.store.submit(attempt['id'], token, self.verdict(snap))

    def test_restart_preserves_handoff_and_read_only_summary(self):
        run, _ = self.delivery()
        self.store.handoff(run['id'], 'unresolved after repairs')
        other = Ledger(self.path)
        self.addCleanup(other.close)
        self.assertEqual(ReviewStore(other).row('review_runs', run['id'])['disposition'], 'human_handoff')
        before = other.connection.total_changes
        self.assertEqual(summary(other.connection)['runs'], {'delivered': 1})
        self.assertEqual(other.connection.total_changes, before)

    def test_older_development_schema_migrates_without_accepting_unbound_verdicts(self):
        run, pr = self.delivery()
        snapshot = self.candidate(pr)
        self.store.db.execute('ALTER TABLE review_candidates DROP COLUMN review_inputs')
        self.store.db.execute('ALTER TABLE review_attempts DROP COLUMN review_inputs')
        self.store.db.execute('ALTER TABLE review_prs DROP COLUMN bot_feedback')
        other = Ledger(self.path)
        self.addCleanup(other.close)
        migrated = ReviewStore(other)
        self.assertIsNone(migrated.row('review_candidates', snapshot['id'])['review_inputs'])
        self.assertFalse(migrated.inputs_current(run['id'], None))
        self.assertEqual(migrated.row('review_prs', pr['id'])['bot_feedback'], '[]')
        self.assertEqual(migrated.row('review_prs', pr['id'])['candidate_id'], snapshot['id'])

    def test_repeat_review_preserves_previous_important_findings_for_explicit_responses(self):
        run, pr = self.delivery()
        snapshot = self.candidate(pr)
        finding = {'id': 'lost-update', 'severity': 'important', 'path': 'store.py', 'line': 1,
                   'claim': 'overwrites concurrent updates', 'evidence': 'read/write interleaving',
                   'verification': 'two independent verifier contexts confirmed the causal path'}
        self.store.db.execute('UPDATE review_candidates SET verdict=? WHERE id=?',
                             (json.dumps(self.verdict(snapshot, findings=[finding])), snapshot['id']))
        attempt = self.store.attempt(snapshot['id'], 'review')
        token = self.store.claim(attempt['id'], 60)
        self.assertEqual(self.store.review_snapshot(snapshot['id'])['open_findings'], [finding])
        with self.assertRaises(ReviewError):
            self.store.submit(attempt['id'], token, self.verdict(snapshot))
        self.store.submit(attempt['id'], token, self.verdict(snapshot, responses=[{
            'id': finding['id'], 'status': 'dispute_upheld', 'evidence': 'related transaction now serializes both operations'}]))


class PolicyTests(unittest.TestCase):
    def test_off_by_default_and_additional_pool_defaults(self):
        value = settings({})
        self.assertEqual((value['mode'], value['workers'], value['depth']), ('off', 3, 3))

    def test_invalid_and_cyclic_policies_fail_before_state_creation(self):
        for value in ({'mode': 'typo'}, {'workers': True}, {'mode': 'merge'}, {'depth': 0}):
            with self.subTest(value=value), self.assertRaises(ReviewError):
                settings(value)
        with self.assertRaises(ReviewError):
            policy(repositories={'a': {'evidence_only': True, 'dependencies': ['b']},
                                 'b': {'evidence_only': True, 'dependencies': ['a']}})

    def test_generated_file_and_rename_cannot_hide_sensitive_change(self):
        p = policy()['repositories']['common']
        p['generated'] = ['*']
        self.assertTrue(risk([{'filename': 'ordinary.py', 'previous_filename': '.github/workflows/test.yml'}], p))

    def test_exact_commit_backend_does_not_silently_replace_squash(self):
        with self.assertRaises(ReviewError):
            policy(repositories={'other': {'backend': 'git_ff', 'method': 'squash', 'evidence_only': True}})

    def test_default_repository_order_and_pin_dependencies_are_explicit_in_normalized_policy(self):
        repos = {name: {'evidence_only': True} for name in ('Farm-Client', 'farm-hive', 'common', 'Farm-Contract')}
        normalized = policy(repositories=repos)['repositories']
        self.assertEqual(normalized['common']['dependencies'], ['Farm-Contract'])
        self.assertEqual(normalized['Farm-Client']['dependencies'], ['Farm-Contract', 'common', 'farm-hive'])
        repos['other'] = {'evidence_only': True, 'pin': {'repository': 'common', 'path': 'pin.env', 'key': 'SHA'}}
        self.assertEqual(policy(repositories=repos)['repositories']['other']['dependencies'], ['common'])


if __name__ == '__main__':
    unittest.main()
