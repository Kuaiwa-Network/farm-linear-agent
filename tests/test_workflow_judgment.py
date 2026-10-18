"""Regressions for worker decision instructions, not a simulation of model judgment.

The paired assertions retain the necessary gates while removing unconditional
questions, commits, investigation and resource requests from worker-facing text.
"""
import unittest
from pathlib import Path

from agent.dispatch import dispatch_message


ROOT = Path(__file__).resolve().parents[1]


def instructions(skill):
    folder = ROOT / 'skills' / 'legacy' / skill if skill in ('work', 'fix') else ROOT / 'skills' / skill
    text = (folder / "SKILL.md").read_text(encoding="utf-8")
    if skill == 'feature':
        assert '../legacy/work/code-stages.md' in text
        text += (ROOT / 'skills/legacy/work/code-stages.md').read_text(encoding='utf-8')
    return " ".join(text.split())


class WorkflowJudgmentTests(unittest.TestCase):
    def launched_authority(self, skill):
        message = dispatch_message(
            item={"id": "item", "skill": skill}, issue={"identifier": "FARM-1", "url": "u"},
            skill_path=ROOT / "skills" / skill / "SKILL.md", worktrees={}, db_path="/db",
            runtime="codex", guidance="", budget={"lease_seconds": 1, "renew_minutes": 1})
        return message.split("\n\n", 1)[0]

    def test_questions_are_conditional_but_existing_human_gaps_stay_fenced(self):
        for skill in ("work", "feature", "fix", "fgui", "scene", "chat"):
            with self.subTest(skill=skill):
                text = self.launched_authority(skill)
                self.assertIn("if no such gap remains, record the evidence and continue", text)
                self.assertIn("Reuse rulings only within their unchanged scope", text)
                self.assertIn("never supply a missing human approval", text)
                self.assertIn("resolve-question", text)
                self.assertIn("Partial answers retain the question", text)
        feature = instructions("feature")
        self.assertIn("When no human gap remains", feature)
        self.assertIn("Required Farm-Contract review is preserved", feature)
        self.assertIn("`[UNREVIEWED]` never backs a proto field", feature)
        self.assertNotIn('Post the round (`questions-N`, "Questions and deciders") and pause', feature)

    def test_existing_contract_reuse_does_not_require_a_manufactured_commit(self):
        text = instructions("work")
        self.assertIn("commit actual contract changes", text)
        self.assertIn("record the existing full commit SHA, scenario/file references", text)
        self.assertIn("no new contract commit, branch or PR is needed solely for delegation", text)
        self.assertIn("Changed behavior still needs the shared OpenSpec change", text)
        self.assertIn("existing-contract reuse cannot hide an unresolved gap", text)
        self.assertNotIn("Commit the settled contract", text)

    def test_server_pool_availability_does_not_create_a_test_requirement(self):
        for skill in ("work", "feature", "fix"):
            with self.subTest(skill=skill):
                text = self.launched_authority(skill)
                self.assertIn("only for required service-backed checks whose evidence is still missing or invalid", text)
                self.assertIn("A configured pool alone never requires a lease", text)
                self.assertIn("Missing services or service-skipped tests remain a gap", text)
                self.assertIn("do not drop required race or coverage", text)
                self.assertIn("reservation-bound loopback services", text)
        for skill in ("feature", "fix"):
            with self.subTest(skill=skill):
                text = instructions(skill)
                local = ("Service-independent checks run locally" if skill == "fix"
                         else "Run service-independent checks locally")
                self.assertTrue(local in text, f"{skill} must retain local service-independent checks")
                self.assertIn("without valid evidence" if skill == "fix" else "service-backed run remains", text)
        self.assertNotIn('When `tools.server_tests.status` is `configured`, commit', instructions("feature"))

    def test_investigation_expands_from_findings_without_waiving_gates(self):
        text = instructions("fix")
        self.assertIn("start read-only with the repositories and paths indicated", text)
        self.assertIn("expand the search when evidence is missing, contradictory", text)
        self.assertIn("All stage/write restrictions still apply", text)
        self.assertNotIn("inspect all worktrees", text)
        for skill in ("work", "feature", "fix"):
            with self.subTest(skill=skill):
                text = self.launched_authority(skill)
                self.assertIn("not merely because more worktrees exist", text)
                self.assertIn("fresh claim/publication checks or certified resource teardown", text)

    def test_legacy_ui_reuses_verified_evidence_but_keeps_artifact_and_approval_gates(self):
        text = instructions("feature").split("## UI ready and stage E", 1)[1].split("## Stage F:", 1)[0]
        self.assertIn("without a new `ui-needed` notice or human pause", text)
        self.assertIn("scope, source/export identities, dependencies and required approvals", text)
        self.assertIn("both refreshed defaults", text)
        self.assertIn("published main exports", text)
        self.assertIn("never mark it passed", text)
        self.assertIn("A reply resolves only its stated gap and does not replace artifact checks", text)
        self.assertIn("partial answers leave the remaining gaps open", text)
        self.assertIn("Keep required named visual approvals, export/provenance, loading", text)
        self.assertNotIn("always ask once", text)
        self.assertNotIn("Only after human confirmation", text)
        self.assertIn("The legacy code capability's E gate becomes this verified task join", instructions("work"))
        templates = (ROOT / "references" / "comment-templates.md").read_text(encoding="utf-8")
        notice = templates.split("## feature UI needed", 1)[1].split("\n## ", 1)[0]
        self.assertIn("证据齐全时直接继续，不发确认通知", notice)
        self.assertIn("实际缺口", notice)
