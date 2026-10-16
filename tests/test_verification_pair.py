"""Only the exact settled controller-recorded verification round may pin main."""
from test_server_slots import ServerPoolFixture, SKILLS


class VerificationPairTests(ServerPoolFixture):
    BASE, CANDIDATE = "b" * 40, "c" * 40

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
