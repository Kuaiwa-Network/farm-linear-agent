"""Execution guidance reaches the actual work launch without widening authority."""
import json
from pathlib import Path
import unittest

from agent.dispatch import dispatch_message
from agent.skills import load_skills


ROOT = Path(__file__).resolve().parents[1]


class WorkerExecutionFlowTests(unittest.TestCase):
    def test_work_launch_includes_preparation_release_and_delivery_boundaries(self):
        skill = load_skills(ROOT / 'skills')['work']
        prompt = dispatch_message(
            item={'id': 'flow', 'skill': 'work', 'execution_contract': 'single-worker-v1'},
            issue={'identifier': 'FARM-1', 'url': 'https://linear.app/issue/FARM-1'},
            skill_path=skill.skill_md, worktrees={}, runtime='codex',
            db_path=ROOT / 'private state' / 'ledger.sqlite3',
            state_dir=ROOT / 'private state', repo_root=ROOT, guidance='', budget=skill.budget)
        authority, payload = prompt.split('\n\n', 1)
        self.assertTrue(prompt.startswith('STARTUP FIRST:'))
        self.assertEqual(json.loads(payload)['execution_contract'], 'single-worker-v1')
        for required in (
                'references/worker-execution-flow.md', 'fixture migrations are settled',
                'exploratory checks for a', 'concrete diagnostic question need no completed implementation',
                'prepare and typecheck any necessary helper before reserving',
                'Native schema discovery still requires the ready owned instance',
                'offline compilation is not native acceptance',
                'release promptly after verified', 'a portrait setup is not a universal substitute',
                'fresh verification before each push', 'batching preparation never reuses publication authority',
                'finish --check-ready on final PR heads', 'confirm the delivery outbox comment and finish promptly',
                'concise completion/blocker handoff', 'compact receipts by default',
                'Choose --view full before running', 'response_view.read_full_item',
                'omitted_fields lists only non-empty payloads',
                'Do not repeat a mutation to recover omitted output',
                'A quiescent release is checked by the host',
                'Never push default/protected branches, force-push, merge PRs, deploy',
                'maintenance, withdrawal and inbox flags; act on those flags immediately'):
            with self.subTest(instruction=required):
                self.assertIn(required, authority)

    def test_skill_and_cli_link_complete_workflow_reference(self):
        for document, link in (
                ('skills/work/SKILL.md', '../../references/worker-execution-flow.md'),
                ('references/single-worker-cli.md', 'worker-execution-flow.md'),
                ('docs/operating-contract.md', '../references/worker-execution-flow.md')):
            path = ROOT / document
            self.assertIn(f']({link})', path.read_text(encoding='utf-8'))
            self.assertTrue((path.parent / link).resolve().is_file())
