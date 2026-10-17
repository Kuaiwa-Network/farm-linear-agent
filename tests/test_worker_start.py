import json
from pathlib import Path
import unittest

from agent.dispatch import dispatch_message

ROOT = Path(__file__).resolve().parents[1]


class WorkerStartTests(unittest.TestCase):
    def launch(self, skill='work', root='Farm-Contract'):
        message = dispatch_message(item={'id': 'job', 'skill': skill, 'stage': 'contract-acceptance'},
            issue={'identifier': 'FARM-1579', 'url': 'https://example.test/issue'},
            skill_path=ROOT/'skills'/skill/'SKILL.md', worktrees={
                'Farm-Contract': '/owned/space 中文/Contract', 'Farm-Client': '/read-only/Client'},
            db_path='/private/db', runtime='codex', guidance='', budget={'lease_seconds': 2700, 'renew_minutes': 5},
            repo_root=ROOT, root_repository=root, write_repositories=[root] if root else [])
        authority, payload = message.split('\n\n', 1)
        return authority, json.loads(payload)

    def test_startup_batch_selects_only_current_root_and_keeps_write_scope(self):
        for skill in ('work', 'feature', 'fix'):
            with self.subTest(skill=skill):
                authority, payload = self.launch(skill)
                reads = payload['startup']['read_together']
                self.assertEqual(reads, [str(Path('/owned/space 中文/Contract')/'AGENTS.md'),
                    str(ROOT/'skills'/skill/'SKILL.md'), str(ROOT/'references/worker-start.md')])
                self.assertNotIn('/read-only/Client', json.dumps(reads))
                self.assertEqual(payload['stage']['write_repositories'], ['Farm-Contract'])
                self.assertIn('Fetch the live issue before acting', authority)
                self.assertFalse(payload['startup']['evidence_is_authority'])

    def test_neutral_and_chat_do_not_gain_repository_or_closeout_authority(self):
        _, neutral = self.launch(root=None)
        self.assertEqual(len(neutral['startup']['read_together']), 2)
        self.assertEqual(neutral['stage']['write_repositories'], [])
        authority, chat = self.launch(skill='chat', root=None)
        self.assertIsNone(chat['startup'])
        self.assertNotIn('startup.read_together', authority)

    def test_guides_keep_failed_checks_and_frozen_consumer_evidence_separate(self):
        startup=(ROOT/'references/worker-start.md').read_text(encoding='utf-8')
        self.assertIn('compare its returned fingerprint with `context.fingerprint`', startup)
        self.assertIn('reload `issue-context --view resume` and revalidate', startup)
        guide=(ROOT/'references/closeout-reuse.md').read_text(encoding='utf-8')
        for clause in ('before editing or running the Contract gates', 'Run all required Contract gates',
                       'actual required input changes do', 'Never disable hooks',
                       'does not\nrequire consumer generation'):
            self.assertIn(clause, guide)
        authority, _ = self.launch()
        self.assertIn('A denial still stops the action', authority)
        self.assertIn('unchanged Client/server', authority)
