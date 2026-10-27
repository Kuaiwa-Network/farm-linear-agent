"""Polling instructions must reach current workers without changing claim authority."""
import json
from pathlib import Path
import sys
import unittest

from agent.dispatch import dispatch_message
from agent.execution import CONTRACT, LEGACY
from agent.skills import load_skills


ROOT = Path(__file__).resolve().parents[1]
SKILLS = load_skills(ROOT / 'skills')


class WorkerPollingTests(unittest.TestCase):
    def prompt(self, profile):
        return dispatch_message(
            item={'id': 'polling', 'skill': profile,
                  'execution_contract': CONTRACT if profile == 'work' else LEGACY},
            issue={'identifier': 'FARM-1', 'url': 'https://linear.app/issue/FARM-1'},
            skill_path=SKILLS[profile].skill_md, worktrees={}, runtime='codex',
            db_path=ROOT / 'private state' / 'ledger.sqlite3',
            state_dir=ROOT / 'private state' / '\u8fdb\u5ea6', repo_root=ROOT,
            guidance='Issue-specific evidence belongs in the payload.',
            budget=SKILLS[profile].budget)

    def test_current_work_and_chat_receive_bounded_waits_in_launch_authority(self):
        for profile in ('work', 'chat'):
            with self.subTest(profile=profile):
                authority, payload = self.prompt(profile).split('\n\n', 1)
                self.assertEqual(authority.count('Waiting cadence:'), 1)
                for instruction in (
                    'use its existing wait handle',
                    'yield_time_ms=30000 initially',
                    'yield_time_ms=60000 after an unchanged/no-output result',
                    "within that tool's supported limit",
                    'read-only status checks 30 seconds apart, then 60 seconds',
                    'meaningful progress, not a timestamp-only change',
                    'Do not sleep after completion',
                ):
                    self.assertIn(instruction, authority)
                self.assertNotIn('Waiting cadence:', payload)
                self.assertEqual(json.loads(payload)['guidance'],
                                 'Issue-specific evidence belongs in the payload.')

    def test_claim_stays_first_and_original_renewal_budgets_are_preserved(self):
        for profile in ('work', 'chat'):
            with self.subTest(profile=profile):
                prompt = self.prompt(profile)
                authority, payload = prompt.split('\n\n', 1)
                data = json.loads(payload)
                start = data['startup']['start_argv']
                self.assertTrue(prompt.startswith('STARTUP FIRST:'))
                self.assertIn(json.dumps(start, ensure_ascii=False), prompt.splitlines()[0])
                self.assertEqual(start[:4], [sys.executable, '-m', 'agent', '--db'])
                self.assertEqual(start[-2:], ['--save-token', str(Path(data['state_dir']) / 'token')])
                self.assertIn('never submit a second claim', authority)
                self.assertIn('Before the claim succeeds, only wait for that submitted claim', authority)
                self.assertIn('do not issue authenticated commands', authority)
                self.assertEqual(data['renew_minutes'], SKILLS[profile].budget['renew_minutes'])
                self.assertEqual(data['lease_seconds'], SKILLS[profile].budget['lease_seconds'])

    def test_control_and_retry_boundaries_override_backoff_for_both_profiles(self):
        for profile in ('work', 'chat'):
            with self.subTest(profile=profile):
                authority = self.prompt(profile).split('\n\n', 1)[0]
                for instruction in (
                    'shorten waits or renew when due',
                    'lease expiry, operation deadlines and control handling take precedence',
                    'resource-status call already renews the claim',
                    'maintenance, cancellation, withdrawal and new requests',
                    'handle them immediately through the existing CLI',
                    'Never resubmit a pending command, claim or test launch',
                    'Helpers use the same cadence but never manage the FarmBot claim',
                    'unchanged status is not a test failure or permission to retry, recover or bypass approval',
                ):
                    self.assertIn(instruction, authority)
                if profile == 'chat':
                    self.assertIn('read-only conversation worker', authority)
                    self.assertIn('or operate shared Unity/server resources', authority)

    def test_current_skills_and_cli_guides_link_the_shared_polling_reference(self):
        guide = (ROOT / 'references/worker-polling.md').resolve()
        for relative, link in (
            ('skills/work/SKILL.md', '../../references/worker-polling.md'),
            ('skills/chat/SKILL.md', '../../references/worker-polling.md'),
            ('references/single-worker-cli.md', 'worker-polling.md'),
            ('references/chat-cli.md', 'worker-polling.md'),
        ):
            with self.subTest(document=relative):
                path = ROOT / relative
                self.assertIn(f']({link})', path.read_text(encoding='utf-8'))
                self.assertEqual((path.parent / link).resolve(), guide)
                self.assertTrue(guide.is_file())


if __name__ == '__main__':
    unittest.main()
