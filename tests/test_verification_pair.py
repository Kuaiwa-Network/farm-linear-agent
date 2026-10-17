"""Only the exact settled controller-recorded verification round may pin main."""
from test_server_slots import ServerPoolFixture, SKILLS
from agent.ledger import LedgerError


class VerificationPairTests(ServerPoolFixture):
    BASE, CANDIDATE = "b" * 40, "c" * 40

    def candidate_first(self):
        item = self.request(root='Farm-Client', reserve=False)
        token = self.ledger.claim(item, worker_id='fixture')['token']
        self.ledger.ensure_slot('unity_slot:1', kind='unity_slot', host='test', folder=str(self.root.parent/'editor'))
        self.ledger.await_resource(item, token, 'unity_slot', 'interactive', skill=SKILLS['fix'],
                                   commit_sha=self.CANDIDATE, client_base_commit=self.BASE)
        candidate = self.ledger.acquire('unity_slot', owner='fixture', host='test')
        return item, self.settled(item, candidate), candidate

    def reuse(self, item, token, candidate):
        return self.ledger.await_resource(item, token, 'unity_slot', 'interactive', skill=SKILLS['fix'],
            commit_sha=self.BASE, comparison_candidate=self.CANDIDATE,
            reuse_candidate_reservation=candidate['reservation_id'])

    def test_candidate_first_pair_needs_no_second_candidate_run(self):
        item, token, candidate = self.candidate_first()
        self.assertEqual(self.ledger.candidate_verification_base(candidate['reservation_id']), self.BASE)
        self.reuse(item, token, candidate)
        baseline = self.ledger.acquire('unity_slot', owner='fixture', host='test')
        self.assertEqual(self.ledger.reservation_comparison(baseline['reservation_id'])['reuse_candidate_reservation'],
                         candidate['reservation_id'])
        self.assertIsNone(self.ledger.client_verification_pair(item, self.CANDIDATE, require_candidate=True))
        self.settled(item, baseline)
        pair = self.ledger.client_verification_pair(item, self.CANDIDATE, require_candidate=True,
                                                   candidate_reservation=candidate['reservation_id'])
        self.assertEqual(pair['baseline_reservation'], baseline['reservation_id'])
        self.assertEqual(pair['candidate_reservation'], candidate['reservation_id'])
        self.assertEqual(len(self.ledger.reservations()), 2)
        self.ledger.fail(item, 'maintenance')
        self.ledger.retry(item, 'retain reports')
        self.assertEqual(self.ledger.client_verification_pair(item, self.CANDIDATE, require_candidate=True), pair)

    def test_candidate_first_cannot_bypass_identity_generation_or_quiescence(self):
        item, token, candidate = self.candidate_first()
        other = self.request(root='Farm-Client', reserve=False)
        identity = candidate['reservation_id']
        original = self.ledger.reservation(identity)
        for field, value in (('item_id', other), ('generation', original['generation'] + 1),
                             ('commit_sha', 'd'*40), ('mode', 'batch'), ('kind', 'server_slot'),
                             ('state', 'active'), ('release_reason', 'operator recovery')):
            with self.subTest(field=field):
                self.ledger.connection.execute(f'UPDATE reservations SET {field}=? WHERE reservation_id=?', (value, identity))
                try:
                    with self.assertRaises(LedgerError):
                        self.reuse(item, token, candidate)
                finally:
                    self.ledger.connection.execute(f'UPDATE reservations SET {field}=? WHERE reservation_id=?', (original[field], identity))
        for invalid in ('', 'missing', None):
            with self.subTest(invalid=invalid), self.assertRaises(LedgerError):
                self.reuse(item, token, {'reservation_id': invalid}) if invalid is not None else self.ledger.await_resource(
                    item, token, 'unity_slot', 'interactive', skill=SKILLS['fix'],
                    commit_sha=self.CANDIDATE, reuse_candidate_reservation=identity)
        self.assertEqual(len(self.ledger.reservations()), 1)

    def test_explicit_reuse_cannot_repeat_an_existing_round(self):
        item, token, candidate = self.candidate_first()
        self.reuse(item, token, candidate)
        baseline = self.ledger.acquire('unity_slot', owner='fixture', host='test')
        token = self.settled(item, baseline)
        with self.assertRaisesRegex(LedgerError, 'already has a comparison round'):
            self.reuse(item, token, candidate)
        # An ordinary new round still cannot borrow the retained candidate.
        _, fresh = self.baseline(item=item, token=token)
        self.settled(item, fresh)
        self.assertIsNone(self.ledger.client_verification_pair(item, self.CANDIDATE, require_candidate=True))

    def test_candidate_first_binding_survives_controller_requeue(self):
        item, token, candidate = self.candidate_first()
        self.reuse(item, token, candidate)
        baseline = self.ledger.acquire('unity_slot', owner='fixture', host='test')
        self.ledger.release(baseline['reservation_id'], baseline['token'], 'preparation interrupted')
        fresh = self.ledger.requeue_reservation(baseline['reservation_id'], 'retry preparation')
        self.ledger.set_slot_state('unity_slot:1', 'idle_open')
        baseline = self.ledger.acquire('unity_slot', owner='fixture', host='test')
        self.assertEqual(baseline['reservation_id'], fresh)
        self.settled(item, baseline)
        self.assertEqual(self.ledger.client_verification_pair(item, self.CANDIDATE, require_candidate=True)
                         ['candidate_reservation'], candidate['reservation_id'])

    def test_retained_candidate_cannot_change_base_or_queue_continuation(self):
        item, token, candidate = self.candidate_first()
        with self.assertRaisesRegex(LedgerError, 'recorded upstream'):
            self.ledger.await_resource(item, token, 'unity_slot', 'interactive', skill=SKILLS['fix'],
                commit_sha='d'*40, comparison_candidate=self.CANDIDATE,
                reuse_candidate_reservation=candidate['reservation_id'])
        self.reuse(item, token, candidate)
        baseline = self.ledger.acquire('unity_slot', owner='fixture', host='test')
        self.ledger.resume(item, 'baseline prepared')
        token = self.ledger.claim(item, worker_id='baseline')['token']
        with self.assertRaisesRegex(LedgerError, 'candidate already tested'):
            self.ledger.continuation_baseline(item, token, baseline['token'], self.CANDIDATE)
        self.assertEqual(self.ledger.reservation(baseline['reservation_id'])['state'], 'active')
        self.assertEqual(len(self.ledger.reservations()), 2)

    def test_candidate_only_pin_requires_clean_current_generation_and_yields_to_comparison(self):
        item, token, candidate = self.candidate_first()
        self.assertEqual(self.ledger.client_candidate_round(item, self.CANDIDATE)['base_commit'], self.BASE)
        self.assertIsNone(self.ledger.client_candidate_round(item, 'd'*40))
        original = self.ledger.reservation(candidate['reservation_id'])
        for field, value in (('state', 'cancelled'), ('release_reason', 'uncertain'),
                             ('generation', original['generation'] + 1)):
            with self.subTest(field=field):
                self.ledger.connection.execute(f'UPDATE reservations SET {field}=? WHERE reservation_id=?',
                                               (value, candidate['reservation_id']))
                self.assertIsNone(self.ledger.client_candidate_round(item, self.CANDIDATE))
                self.ledger.connection.execute(f'UPDATE reservations SET {field}=? WHERE reservation_id=?',
                                               (original[field], candidate['reservation_id']))
        self.reuse(item, token, candidate)
        self.assertIsNone(self.ledger.client_candidate_round(item, self.CANDIDATE))

    def baseline(self, *, item=None, token=None):
        item = item or self.request(root="Farm-Client", reserve=False)
        token = token or self.ledger.claim(item, worker_id="fixture")["token"]
        self.ledger.ensure_slot("unity_slot:1", kind="unity_slot", host="test", folder=str(self.root.parent/"editor"))
        self.ledger.await_resource(item, token, "unity_slot", "interactive", skill=SKILLS["fix"],
                                   commit_sha=self.BASE, comparison_candidate=self.CANDIDATE)
        lease = self.ledger.acquire("unity_slot", owner="fixture", host="test")
        return item, lease

    def settled(self, item, lease, *, reason="worker reported quiescent"):
        self.ledger.release(lease["reservation_id"], lease["token"], reason)
        self.ledger.set_slot_state("unity_slot:1", "idle_open")
        self.ledger.resume(item, "fixture settled")
        return self.ledger.claim(item, worker_id="next")["token"]

    def test_unfinished_unclean_wrong_candidate_and_other_items_get_no_pin(self):
        item, baseline = self.baseline()
        self.assertIsNone(self.ledger.client_verification_pair(item,self.CANDIDATE))
        self.settled(item,baseline,reason="operator recovery after uncertainty")
        self.assertIsNone(self.ledger.client_verification_pair(item,self.CANDIDATE))
        other = self.request(root="Farm-Client",reserve=False)
        self.assertIsNone(self.ledger.client_verification_pair(other,self.CANDIDATE))

    def test_candidate_proof_required_for_publication_and_retry_keeps_exact_round(self):
        item, baseline = self.baseline()
        token = self.settled(item,baseline)
        self.assertEqual(self.ledger.client_verification_pair(item,self.CANDIDATE)["baseline_commit"],self.BASE)
        self.assertIsNone(self.ledger.client_verification_pair(item,self.CANDIDATE,require_candidate=True))
        self.assertIsNone(self.ledger.client_verification_pair(item,"d"*40))
        self.ledger.await_resource(item, token, "unity_slot", "interactive", skill=SKILLS["fix"],commit_sha=self.CANDIDATE)
        candidate = self.ledger.acquire("unity_slot",owner="fixture",host="test")
        self.settled(item,candidate)
        proof = self.ledger.client_verification_pair(item,self.CANDIDATE,require_candidate=True)
        self.assertEqual(proof["candidate_reservation"],candidate["reservation_id"])
        self.ledger.fail(item,"host maintenance")
        self.ledger.retry(item,"preserve unchanged source")
        self.assertEqual(self.ledger.client_verification_pair(item,self.CANDIDATE,require_candidate=True),proof)

    def test_new_round_cannot_borrow_an_older_completed_candidate(self):
        item, baseline = self.baseline()
        token = self.settled(item,baseline)
        self.ledger.await_resource(item,token,"unity_slot","interactive",skill=SKILLS["fix"],commit_sha=self.CANDIDATE)
        candidate = self.ledger.acquire("unity_slot",owner="fixture",host="test")
        token = self.settled(item,candidate)
        self.assertIsNotNone(self.ledger.client_verification_pair(item,self.CANDIDATE,require_candidate=True))
        _, latest = self.baseline(item=item,token=token)
        self.assertIsNone(self.ledger.client_verification_pair(item,self.CANDIDATE))
        self.assertIsNone(self.ledger.client_verification_pair(
            item, self.CANDIDATE, require_candidate=True, candidate_reservation=candidate['reservation_id']))
        self.settled(item,latest)
        self.assertIsNone(self.ledger.client_verification_pair(item,self.CANDIDATE,require_candidate=True))
        self.assertIsNone(self.ledger.client_verification_pair(
            item, self.CANDIDATE, require_candidate=True, candidate_reservation=candidate['reservation_id']))

    def test_completed_report_remains_selectable_after_a_later_diagnostic(self):
        item, baseline = self.baseline()
        token = self.settled(item, baseline)
        candidates = []
        for _ in range(2):
            self.ledger.await_resource(item, token, 'unity_slot', 'interactive', skill=SKILLS['fix'],
                                       commit_sha=self.CANDIDATE)
            candidate = self.ledger.acquire('unity_slot', owner='fixture', host='test')
            token = self.settled(item, candidate)
            candidates.append(candidate['reservation_id'])
        self.assertEqual(self.ledger.client_verification_pair(item, self.CANDIDATE)['candidate_reservation'],
                         candidates[1])
        proof = self.ledger.client_verification_pair(item, self.CANDIDATE, require_candidate=True,
                                                    candidate_reservation=candidates[0])
        self.assertEqual(proof['baseline_reservation'], baseline['reservation_id'])
        self.assertEqual(proof['candidate_reservation'], candidates[0])
        for invalid in ('missing', '', baseline['reservation_id']):
            with self.subTest(reservation=invalid):
                self.assertIsNone(self.ledger.client_verification_pair(
                    item, self.CANDIDATE, require_candidate=True, candidate_reservation=invalid))

    def test_explicit_candidate_still_requires_same_item_commit_mode_and_clean_settlement(self):
        item, baseline = self.baseline()
        token = self.settled(item, baseline)
        self.ledger.await_resource(item, token, 'unity_slot', 'interactive', skill=SKILLS['fix'],
                                   commit_sha=self.CANDIDATE)
        candidate = self.ledger.acquire('unity_slot', owner='fixture', host='test')
        self.settled(item, candidate)
        other = self.request(root='Farm-Client', reserve=False)
        reservation_id = candidate['reservation_id']
        original = self.ledger.reservation(reservation_id)
        for field, value in (('item_id', other), ('commit_sha', 'd' * 40), ('mode', 'batch'),
                             ('kind', 'server_slot'), ('state', 'active'),
                             ('release_reason', 'operator recovery after uncertainty')):
            with self.subTest(field=field):
                self.ledger.connection.execute(f'UPDATE reservations SET {field}=? WHERE reservation_id=?',
                                               (value, reservation_id))
                try:
                    self.assertIsNone(self.ledger.client_verification_pair(
                        item, self.CANDIDATE, require_candidate=True, candidate_reservation=reservation_id))
                finally:
                    self.ledger.connection.execute(f'UPDATE reservations SET {field}=? WHERE reservation_id=?',
                                                   (original[field], reservation_id))
