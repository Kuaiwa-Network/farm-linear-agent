"""Interrupted baseline recovery keeps source and evidence ownership across main updates."""
import unittest
from unittest.mock import Mock

from agent.baseline_resume import pinned_baseline, retained_baseline_report
from agent.ledger import LedgerError
from agent.maintenance import Maintenance
import test_verification_pair as fixtures


def interrupt_baseline(f, item, reservation):
    ledger = f.ledger
    ledger.resume(item, 'baseline admitted')
    ledger.set_worker(item, 12345, 'test')
    token = ledger.claim(item, worker_id='baseline')['token']
    store = Maintenance(ledger)
    store.pause('deploy while baseline still missing')
    ledger.release(reservation['reservation_id'], reservation['token'], 'worker reported quiescent')
    ledger.set_slot_state('unity_slot:1', 'idle_open')
    ledger.checkpoint(item, token, {'handoff': {'facts': [], 'hypotheses': [], 'checks': [],
        'repositories': [], 'next_actions': ['reuse reports and complete missing baseline checks']}})
    store.handoff(item, token)
    store.settle(Mock(running=lambda: {}, assert_quiescent=Mock()))
    store.resume()
    return ledger.claim(item, worker_id='after-maintenance')['token']


class BaselineResumeTests(unittest.TestCase):
    def setUp(self):
        self.f = f = fixtures.VerificationPairTests(); f.setUp()
        self.addCleanup(f.doCleanups)
        self.item, self.source = f.baseline()
        self.token = interrupt_baseline(f, self.item, self.source)

    def pin(self, **options):
        return pinned_baseline(self.f.ledger, self.item, self.f.CANDIDATE,
                               self.source['reservation_id'], **options)

    def request(self, **options):
        args = dict(commit_sha=self.f.BASE, comparison_candidate=self.f.CANDIDATE,
                    resume_baseline_reservation=self.source['reservation_id'])
        args.update(options)
        return self.f.ledger.await_resource(self.item, self.token, 'unity_slot', 'interactive',
                                           skill=fixtures.SKILLS['fix'], **args)

    def test_requeue_preserves_original_and_grants_no_result(self):
        ledger = self.f.ledger
        before = ledger.item(self.item)
        old = ledger.reservation(self.source['reservation_id'])
        self.assertEqual(self.pin()['baseline_commit'], self.f.BASE)
        self.request()
        fresh = ledger.reservations()[-1]
        self.assertNotEqual(fresh['reservation_id'], old['reservation_id'])
        self.assertEqual(fresh['commit_sha'], old['commit_sha'])
        self.assertEqual(ledger.reservation(old['reservation_id']), old)
        for field in ('generation', 'checkpoint', 'evidence', 'target', 'capacity_retries', 'publication_retries'):
            self.assertEqual(ledger.item(self.item)[field], before[field], field)
        self.assertIsNone(ledger.client_verification_pair(self.item, self.f.CANDIDATE))
        self.assertEqual(ledger.reservation_comparison(fresh['reservation_id'])['candidate_commit'], self.f.CANDIDATE)
        with self.assertRaisesRegex(LedgerError, 'latest Unity'):
            self.pin()

    def test_rejects_wrong_generation_mode_kind_candidate_and_release(self):
        ledger = self.f.ledger; identity = self.source['reservation_id']
        old = ledger.reservation(identity)
        for field, value in (('generation', 99), ('kind', 'server_slot'), ('host', 'another-host'), ('state', 'active'),
                             ('release_reason', 'uncertain'), ('commit_sha', 'f'*40)):
            with self.subTest(field=field):
                ledger.connection.execute(f'UPDATE reservations SET {field}=? WHERE reservation_id=?', (value, identity))
                with self.assertRaises(LedgerError): self.pin()
                ledger.connection.execute(f'UPDATE reservations SET {field}=? WHERE reservation_id=?', (old[field], identity))
        with self.assertRaises(LedgerError): self.pin(mode='batch')
        other = self.f.request(root='Farm-Client', reserve=False)
        with self.assertRaises(LedgerError): pinned_baseline(ledger, other, self.f.CANDIDATE, identity)
        with self.assertRaises(LedgerError): pinned_baseline(ledger, self.item, 'd'*40, identity)

    def test_rejects_incomplete_handoff_reviewed_round_and_forged_binding(self):
        ledger = self.f.ledger
        ledger.connection.execute('UPDATE maintenance_handoffs SET completed_at=NULL WHERE item_id=?', (self.item,))
        with self.assertRaisesRegex(LedgerError, 'completed maintenance'): self.request()
        ledger.connection.execute('UPDATE maintenance_handoffs SET completed_at=? WHERE item_id=?', (self.f.now, self.item))
        for options in ({'commit_sha': 'e'*40}, {'reuse_candidate_reservation': 'invented'}):
            with self.subTest(options=options), self.assertRaisesRegex(LedgerError, 'preserve both'): self.request(**options)
        ledger._audit(self.item, 'verification_comparison', 'blocked',
                      {'baseline_commit': self.f.BASE, 'candidate_commit': self.f.CANDIDATE})
        with self.assertRaisesRegex(LedgerError, 'already reviewed'): self.request()

    def test_preserves_candidate_first_report_after_baseline_resume(self):
        f = self.f
        item, token, candidate = f.candidate_first()
        f.reuse(item, token, candidate)
        source = f.ledger.acquire('unity_slot', owner='fixture', host='test')
        token = interrupt_baseline(f, item, source)
        pin = pinned_baseline(f.ledger, item, f.CANDIDATE, source['reservation_id'])
        self.assertEqual(pin['reuse_candidate_reservation'], candidate['reservation_id'])
        f.ledger.await_resource(item, token, 'unity_slot', 'interactive', skill=fixtures.SKILLS['fix'],
            commit_sha=f.BASE, comparison_candidate=f.CANDIDATE, resume_baseline_reservation=source['reservation_id'],
            reuse_candidate_reservation=pin['reuse_candidate_reservation'])
        resumed = f.ledger.acquire('unity_slot', owner='fixture', host='test')
        self.assertEqual(f.ledger.reservation_comparison(resumed['reservation_id'])['reuse_candidate_reservation'], candidate['reservation_id'])
        f.settled(item, resumed)
        pair = f.ledger.client_verification_pair(item, f.CANDIDATE, require_candidate=True)
        self.assertEqual(pair['candidate_reservation'], candidate['reservation_id'])
        self.assertEqual(pair['baseline_reservation'], resumed['reservation_id'])
        self.assertEqual(sum(r['commit_sha']==f.CANDIDATE for r in f.ledger.reservations() if r['item_id']==item), 1)

    def test_claim_fencing_and_dispatch_do_not_grant_an_answer_or_pass(self):
        from agent.dispatch import dispatch_message
        self.token = 'wrong'
        with self.assertRaises(LedgerError): self.request()
        text = dispatch_message(item={'id':'i','skill':'fix','stage':'client-baseline-resume'},
            issue={'identifier':'FARM-1','url':'u'},skill_path='skill',worktrees={},db_path='db',runtime='fake',
            guidance=[],budget={'lease_seconds':2700,'renew_minutes':5})
        self.assertIn('--resume-baseline-reservation', text)
        self.assertIn('not a passing baseline', text)
        self.assertIn('not a human reply, retry reset or test verdict', text)

    def test_partial_baseline_report_keeps_old_identity_only_through_clean_resume_chain(self):
        f = self.f
        self.request()
        res = f.ledger.acquire('unity_slot', owner='fixture', host='test')
        token = f.settled(self.item, res)
        f.ledger.await_resource(self.item, token, 'unity_slot', 'interactive', skill=fixtures.SKILLS['fix'],
                               commit_sha=f.CANDIDATE)
        candidate = f.ledger.acquire('unity_slot', owner='fixture', host='test')
        f.settled(self.item, candidate)
        pair = f.ledger.client_verification_pair(self.item, f.CANDIDATE, require_candidate=True)
        self.assertTrue(retained_baseline_report(f.ledger, self.item, pair, self.source['reservation_id']))
        self.assertFalse(retained_baseline_report(f.ledger, self.item, pair, 'unrelated'))
        self.assertFalse(retained_baseline_report(f.ledger, 'other-item', pair, self.source['reservation_id']))
        f.ledger.connection.execute("UPDATE reservations SET release_reason='uncertain' WHERE reservation_id=?", (self.source['reservation_id'],))
        self.assertFalse(retained_baseline_report(f.ledger, self.item, pair, self.source['reservation_id']))
        f.ledger.connection.execute("UPDATE reservations SET release_reason='worker reported quiescent' WHERE reservation_id=?", (self.source['reservation_id'],))
        # A genuinely new round at exactly the same SHA is still not a resume.
        f.ledger.connection.execute("UPDATE audit SET details=json_remove(details,'$.baseline_resume_of') WHERE item_id=? AND kind='reservation' AND reason='queued'", (self.item,))
        self.assertFalse(retained_baseline_report(f.ledger, self.item, pair, self.source['reservation_id']))
