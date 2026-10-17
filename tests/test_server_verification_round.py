"""Fixed server candidate and publication ancestry through repeated main movement."""
import json
import os
from pathlib import Path
from unittest.mock import patch

from agent.ledger import LedgerError
from agent.publication import PublicationError, PublicationVerifier
from agent.skills import load_skills
from agent.worktrees import Worktrees, WorktreeError
from agent.workflow import JobWorktrees
from agent.server_registration import ANCHORS, GATE, registration_delta, retained_registration_base
from test_server_slots import ServerPoolFixture
from test_worktrees import git


class ServerRoundTests(ServerPoolFixture):
    def setUp(self):
        super().setUp()
        self.origin = self.root.parent / 'origin'
        self.origin.mkdir()
        git('init', '-q', '-b', 'main', '.', cwd=self.origin)
        (self.origin / 'source.txt').write_text('initial', encoding='utf-8')
        git('add', '.', cwd=self.origin)
        git('commit', '-qm', 'initial', cwd=self.origin)
        self.initial = git('rev-parse', 'HEAD', cwd=self.origin)
        self.advance_main('selected')
        self.base = git('rev-parse', 'HEAD', cwd=self.origin)
        self.item = self.request(reserve=False)
        self.token = self.ledger.claim(self.item, worker_id='worker')['token']
        self.trees = Worktrees(self.root.parent / 'repos', self.root.parent / 'worktrees',
                              {'farm-hive': str(self.origin)})
        self.path = self.trees.add('farm-hive', self.item, 'farmbot/farm-1')
        (self.path / 'candidate.txt').write_text('issue change', encoding='utf-8')
        git('add', '.', cwd=self.path)
        git('commit', '-qm', 'candidate', cwd=self.path)
        self.candidate = self.trees.head(self.path)
        self.skills = load_skills(Path(__file__).resolve().parents[1] / 'skills')

    def advance_main(self, value):
        (self.origin / 'main.txt').write_text(value, encoding='utf-8')
        git('add', '.', cwd=self.origin)
        git('commit', '-qm', value, cwd=self.origin)

    def request_round(self):
        base = self.trees.server_verification_base('farm-hive', self.item, self.candidate)
        self.ledger.await_resource(self.item, self.token, 'server_slot', 'batch',
                                  skill=self.skills['fix'], commit_sha=self.candidate, server_base_commit=base)
        return self.ledger.reservations()[-1]

    def settle(self):
        self.assertEqual(self.pool.tick()['granted'], 1)
        reservation = self.ledger.active_reservation(self.item)
        self.token = self.ledger.claim(self.item, worker_id='resumed')['token']
        self.ledger.release(reservation['reservation_id'], self.services.started[-1]['token'], 'worker reported quiescent')
        self.pool.tick()

    def test_frozen_candidate_survives_main_changes_and_repeated_requests(self):
        self.advance_main('before request')
        first = self.request_round()
        self.assertEqual(first['commit_sha'], self.candidate)
        record = self.ledger.server_verification_round(self.item, self.candidate)
        self.assertEqual(record['base_commit'], self.base)
        self.advance_main('while queued')
        base = self.trees.server_verification_base('farm-hive', self.item, self.candidate,
                                                  verified_base=record['base_commit'])
        self.assertEqual(base, self.base)
        self.assertEqual(self.trees.head(self.path), self.candidate)
        self.assertIsNone(self.ledger.server_verification_round(self.item, self.candidate, require_released=True))
        self.settle()
        self.assertIsNotNone(self.ledger.server_verification_round(self.item, self.candidate, require_released=True))
        with self.assertRaisesRegex(LedgerError, 'immutable'):
            self.ledger.await_resource(self.item, self.token, 'server_slot', 'batch', skill=self.skills['fix'],
                                       commit_sha=self.candidate, server_base_commit=self.initial)
        self.assertEqual(len(self.ledger.reservations()), 1)  # failed change rolled back its reservation
        self.ledger.await_resource(self.item, self.token, 'server_slot', 'batch', skill=self.skills['fix'],
                                   commit_sha=self.candidate, server_base_commit=base)
        self.assertEqual(self.ledger.server_verification_round(self.item, self.candidate)['reservation_id'], first['reservation_id'])

    def test_dirty_candidate_changed_generation_and_upstream_rewind_cannot_borrow(self):
        self.request_round()
        self.settle()
        self.assertIsNone(self.ledger.server_verification_round(self.item, 'f' * 40, require_released=True))
        (self.path / 'candidate.txt').write_text('dirty', encoding='utf-8')
        with self.assertRaisesRegex(WorktreeError, 'clean worktree'):
            self.trees.server_verification_base('farm-hive', self.item, self.candidate, verified_base=self.base)
        git('restore', 'candidate.txt', cwd=self.path)
        git('reset', '--hard', self.initial, cwd=self.origin)
        with self.assertRaisesRegex(WorktreeError, 'main no longer contains'):
            self.trees.server_verification_base('farm-hive', self.item, self.candidate, verified_base=self.base)
        self.ledger.connection.execute('UPDATE work_items SET generation=generation+1 WHERE id=?', (self.item,))
        self.assertIsNone(self.ledger.server_verification_round(self.item, self.candidate, require_released=True))

    def test_publication_uses_only_exact_quiescent_round_and_still_fetches_actual_main(self):
        self.request_round()
        self.advance_main('queued main')
        remote = 'https://github.com/Kuaiwa-Network/farm-hive.git'
        self.trees.remotes['farm-hive'] = remote
        git('remote', 'set-url', 'origin', remote, cwd=self.path)
        host = self.root.parent / 'host.gitconfig'
        base = str(self.origin).replace('\\', '\\\\')
        host.write_text(f'[url "{base}"]\n\tinsteadOf = {remote}\n[url "{remote}"]\n\tpushInsteadOf = {remote}\n', encoding='utf-8')
        repo = {'full_name': 'Kuaiwa-Network/farm-hive', 'private': True, 'owner': {'login': 'Kuaiwa-Network'},
                'permissions': {'push': True}, 'html_url': remote[:-4], 'default_branch': 'main'}
        def api(endpoint, **kwargs):
            return None if '/branches/' in endpoint else repo
        verifier = PublicationVerifier(JobWorktrees(self.trees, self.ledger), api=api)
        with patch.dict(os.environ, {'GIT_CONFIG_GLOBAL': str(host)}):
            with self.assertRaises(PublicationError):
                verifier.verify('farm-hive', self.item, 'FARM-1', require_main=True)
            self.settle()
            proof = verifier.verify('farm-hive', self.item, 'FARM-1', require_main=True)
            self.assertEqual(proof['main_integration']['commit'], self.base)
            self.assertEqual(proof['main_integration']['current_main'], git('rev-parse', 'HEAD', cwd=self.origin))
            self.assertEqual(proof['head'], self.candidate)
            self.advance_main('publication main')
            self.assertEqual(verifier.verify('farm-hive', self.item, 'FARM-1', require_main=True)['head'], self.candidate)
            (self.path / 'candidate.txt').write_text('changed candidate', encoding='utf-8')
            git('commit', '-qam', 'actual repair', cwd=self.path)
            with self.assertRaises(PublicationError):
                verifier.verify('farm-hive', self.item, 'FARM-1', require_main=True)

    def test_worker_authority_freezes_server_candidate_through_resume_and_publication(self):
        from agent.dispatch import authority
        for skill in ('work', 'feature', 'fix'):
            text = authority(skill)
            self.assertIn('A server reservation fixes resource.commit', text)
            self.assertIn('overrides generic fresh-base/base-drift', text)
            self.assertIn('Tip movement alone is not', text)
            self.assertIn('after a quiescent server release', text)

    def test_registration_only_descendant_keeps_ancestry_without_certifying_tests(self):
        old = 'allow=CommitSharePut,Follow'
        new = 'allow=CommitSharePut,Follow,PostMarketResult'
        (self.path / GATE).parent.mkdir(parents=True)
        (self.path / ANCHORS).parent.mkdir(parents=True)
        (self.path / GATE).write_text('# gate\n' + old + '\ncheck_exact\n', encoding='utf-8', newline='\n')
        (self.path / ANCHORS).write_text("inject_lit \"gate\" $'" + old + "' $'" + old + ",upgradeBuildingImpl'\n", encoding='utf-8', newline='\n')
        git('add', '.', cwd=self.path)
        git('commit', '-qm', 'gate fixtures', cwd=self.path)
        git('update-index', '--chmod=+x', GATE, cwd=self.path)
        git('commit', '-qm', 'executable gate', cwd=self.path)
        self.candidate = self.trees.head(self.path)
        self.request_round()
        for name in (GATE, ANCHORS):
            p = self.path / name
            p.write_text(p.read_text(encoding='utf-8').replace(old, new), encoding='utf-8', newline='\n')
        git('commit', '-qam', 'registration only', cwd=self.path)
        repaired = self.trees.head(self.path)
        trees = JobWorktrees(self.trees, self.ledger)
        check = lambda: retained_registration_base(trees, self.ledger, self.item, repaired, 'farmbot/farm-1')
        self.assertIsNone(check())  # Unreleased evidence never lends its base.
        self.settle()
        self.advance_main('new main')
        proof = check()
        self.assertEqual(proof['base_commit'], self.base)
        self.assertEqual(proof['source_candidate'], self.candidate)
        self.assertEqual(proof['candidate_commit'], repaired)
        self.assertIsNone(proof['test_verdict'])
        self.assertIsNone(self.ledger.server_verification_round(self.item, repaired))
        self.assertEqual(self.trees.server_verification_base('farm-hive', self.item, repaired,
                                                            verified_base=proof['base_commit']), self.base)
        remote = 'https://github.com/Kuaiwa-Network/farm-hive.git'
        self.trees.remotes['farm-hive'] = remote
        git('remote', 'set-url', 'origin', remote, cwd=self.path)
        host = self.root.parent / 'registration.gitconfig'
        local = str(self.origin).replace('\\', '\\\\')
        host.write_text(f'[url "{local}"]\n\tinsteadOf = {remote}\n'
                        f'[url "{remote}"]\n\tpushInsteadOf = {remote}\n', encoding='utf-8')
        metadata = {'full_name': 'Kuaiwa-Network/farm-hive', 'private': True,
                    'owner': {'login': 'Kuaiwa-Network'}, 'permissions': {'push': True},
                    'html_url': remote[:-4], 'default_branch': 'main'}
        verifier = PublicationVerifier(trees, api=lambda endpoint, **kw: None if '/branches/' in endpoint else metadata)
        with patch.dict(os.environ, {'GIT_CONFIG_GLOBAL': str(host)}):
            publication = verifier.verify('farm-hive', self.item, 'FARM-1', require_main=True)
            self.assertEqual(publication['head'], repaired)
            self.assertEqual(publication['main_integration']['commit'], self.base)
            self.assertTrue(publication['server_registration_ancestry']['ancestry_only'])
            self.assertNotIn('server_verification_round', publication)
            git('reset', '--hard', self.initial, cwd=self.origin)
            with self.assertRaisesRegex(PublicationError, 'main no longer contains'):
                verifier.verify('farm-hive', self.item, 'FARM-1', require_main=True)
        (self.path / 'candidate.txt').write_text('dirty', encoding='utf-8')
        self.assertIsNone(check())
        git('restore', 'candidate.txt', cwd=self.path)
        self.assertIsNone(retained_registration_base(trees, self.ledger, self.item, repaired, 'farmbot/farm-2'))
        self.ledger.connection.execute('UPDATE work_items SET generation=generation+1 WHERE id=?', (self.item,))
        self.assertIsNone(check())

    def test_registration_parser_rejects_changes_to_gate_or_negative_protection(self):
        old = 'allow=CommitSharePut,Follow'
        new = 'allow=CommitSharePut,Follow,PostMarketResult'
        before = {GATE: old + '\nexact_check\n', ANCHORS: "$'" + old + "' $'" + old + ",upgradeBuildingImpl'\n"}
        after = {p: text.replace(old, new) for p, text in before.items()}
        self.assertTrue(registration_delta(before, after))
        for path, text in ((GATE, after[GATE] + 'exit 0\n'),
                           (GATE, after[GATE].replace('exact_check', 'skip_check')),
                           (GATE, after[GATE].replace('CommitSharePut,', '')),
                           (GATE, after[GATE].replace('Follow,', 'Follow,Follow,')),
                           (GATE, after[GATE].replace('PostMarketResult', 'upgradeBuildingImpl')),
                           (GATE, after[GATE].replace('PostMarketResult', '$(run)')),
                           (ANCHORS, after[ANCHORS].replace(',upgradeBuildingImpl', '')),
                           (ANCHORS, before[ANCHORS]),
                           (ANCHORS, after[ANCHORS] + 'skip_test\n'),
                           (GATE, after[GATE] + new + '\n')):
            with self.subTest(path=path, text=text):
                self.assertFalse(registration_delta(before, {**after, path: text}))
        self.assertFalse(registration_delta(before, {**after, 'workflow.yml': 'skip'}))
