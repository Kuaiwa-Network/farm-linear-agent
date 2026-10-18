"""Source entry scopes survive retries and Stop without hiding retained output."""
import unittest
from unittest.mock import patch

from agent.ledger import LedgerError
from agent.workflow import JobWorktrees, retry_task, source_entry
import test_general_scheduling as scheduling_fixtures
import test_ledger as ledger_fixtures
import test_worktrees as git_fixtures


class SourceEntryTests(unittest.TestCase):
    def setUp(self):
        self.scenario = scenario = scheduling_fixtures.GeneralSchedulingTests()
        scenario.setUp()
        self.addCleanup(scenario.doCleanups)
        self.ledger, self.scheduler = scenario.ledger, scenario.scheduler
        self.git = fixture = git_fixtures.WorktreeTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.trees.remotes["farmgui"] = str(fixture.origin)
        self.scheduler.worktrees = JobWorktrees(fixture.trees, self.ledger)

    def enter(self, task):
        return self.scheduler._worktrees_for(scheduling_fixtures.SKILLS["fgui"], task,
                                            self.ledger.issue(task["issue_id"]))["farmgui"]

    def change(self, path, body="Retained UI source"):
        (path / "Panel.xml").write_text(body, encoding="utf-8")
        git_fixtures.git("add", "Panel.xml", cwd=path)
        git_fixtures.git("commit", "-qm", "UI source candidate", cwd=path)
        return self.git.trees.head(path)

    def retry(self, task):
        self.ledger.push_inbox(self.scenario.parent, "Retry the retained UI source.", author=ledger_fixtures.DESIGNER)
        message = self.ledger.issue_context(self.scenario.parent)["session_messages"][-1]["id"]
        self.ledger.pop_inbox(self.scenario.parent, self.scenario.token)
        return retry_task(self.ledger, self.scenario.parent, self.scenario.token, task["task_key"], message)

    def successor(self):
        previous = self.scenario.parent
        view = self.ledger.create_work_item(issue_id=ledger_fixtures.ISSUE, session_id=ledger_fixtures.SESSION,
                                           skill="work", target=None, execution_contract='staged-v1')
        self.assertEqual(view["predecessor_id"], previous)
        self.scenario.parent = view["id"]
        self.scenario.token = self.ledger.claim(view["id"], worker_id="successor")["token"]
        self.ledger.checkpoint(view["id"], self.scenario.token, {"plan": {"stages": {"A": "done", "D": "pending"}}})

    def test_first_entry_records_the_actual_controller_created_suffix_branch(self):
        clone = self.git.trees.ensure_clone("farmgui")
        git_fixtures.git("branch", "farmbot/farm-1", self.git.trees.head(self.git.origin), cwd=clone)
        task = self.scenario.task()
        path = self.enter(task)
        branch = self.git.trees.git_in(path, "branch", "--show-current")
        self.assertNotEqual(branch, "farmbot/farm-1")
        self.assertEqual(source_entry(self.ledger, task["id"])["branch"], branch)

    def test_same_task_retry_retains_original_entry_and_dirty_candidate(self):
        task = self.scenario.task()
        path = self.enter(task)
        original = source_entry(self.ledger, task["id"])
        self.ledger.claim(task["id"], worker_id="ui")
        self.change(path)
        (path / "untracked.xml").write_text("Still under review", encoding="utf-8")
        self.ledger.fail(task["id"], "source worker stopped")
        self.scheduler._retire(task["id"], "failed")
        retried = self.retry(task)
        self.assertEqual(self.enter(retried), path)
        self.assertEqual(source_entry(self.ledger, task["id"]), original)
        self.assertTrue((path / "untracked.xml").exists())

    def stopped_source(self):
        task = self.scenario.task()
        path = self.enter(task)
        entry = source_entry(self.ledger, task["id"])
        self.ledger.claim(task["id"], worker_id="ui")
        candidate = self.change(path)
        self.ledger.cancel(self.scenario.parent, "Stop")
        self.scheduler._retire(task["id"], "cancelled")
        self.scheduler._retire(self.scenario.parent, "cancelled")
        self.assertTrue(self.ledger.cleanup_record(self.scenario.parent)["done"])
        self.successor()
        return task, entry, candidate

    def test_stop_successor_with_same_task_key_retains_candidate_and_original_scope(self):
        previous, entry, candidate = self.stopped_source()
        task = self.scenario.task()
        path = self.enter(task)
        self.assertEqual(task["predecessor_id"], previous["id"])
        self.assertEqual(self.git.trees.head(path), candidate)
        self.assertEqual(source_entry(self.ledger, task["id"])["commit"], entry["commit"])
        self.assertEqual(source_entry(self.ledger, task["id"])["branch"], entry["branch"])

    def test_stop_successor_with_fresh_key_reattaches_candidate_instead_of_resetting_to_main(self):
        previous, entry, candidate = self.stopped_source()
        task = self.scenario.task("new-ui-key")
        path = self.enter(task)
        self.assertIsNone(task["predecessor_id"])
        self.assertEqual(self.git.trees.head(path), candidate)
        self.assertEqual((path / "Panel.xml").read_text(encoding="utf-8"), "Retained UI source")
        proof = source_entry(self.ledger, task["id"])
        self.assertEqual(proof["commit"], entry["commit"])
        self.assertEqual(proof["branch"], entry["branch"])
        self.assertEqual(proof["predecessor_tasks"], [previous["id"]])

    def test_interrupted_predecessor_requires_certified_child_teardown_before_checkout(self):
        task = self.scenario.task()
        path = self.enter(task)
        self.ledger.claim(task["id"], worker_id="ui")
        self.ledger.cancel(self.scenario.parent, "Stop")
        self.successor()
        successor = self.scenario.task("fresh-key")
        with self.assertRaisesRegex(LedgerError, "certified predecessor teardown"):
            self.enter(successor)
        self.assertTrue(path.exists())
        self.assertIsNone(source_entry(self.ledger, successor["id"]))

    def test_older_same_id_attempt_resolves_fresh_default_outside_write_transaction(self):
        task = self.scenario.task()
        path = self.enter(task)
        self.ledger.connection.execute("DELETE FROM audit WHERE item_id=? AND kind='task_source_entry'", (task["id"],))
        self.ledger.claim(task["id"], worker_id="older-ui")
        candidate = self.change(path)
        self.ledger.fail(task["id"], "older controller attempt stopped")
        self.scheduler._retire(task["id"], "failed")
        (self.git.origin / "new-main.txt").write_text("Default branch advanced", encoding="utf-8")
        git_fixtures.git("add", "new-main.txt", cwd=self.git.origin)
        git_fixtures.git("commit", "-qm", "New default", cwd=self.git.origin)
        expected = self.git.trees.head(self.git.origin)
        resolve = self.git.trees.resolve_commit
        def resolved(repo):
            self.assertFalse(self.ledger.connection.in_transaction)
            return resolve(repo)
        with patch.object(self.git.trees, "resolve_commit", side_effect=resolved) as call:
            retried = self.retry(task)
            self.enter(retried)
        call.assert_called_once_with("farmgui")
        proof = source_entry(self.ledger, task["id"])
        self.assertEqual(proof["commit"], expected)
        self.assertNotEqual(proof["commit"], candidate)
        self.assertEqual(self.git.trees.head(path), candidate)

    def test_completed_current_parent_sibling_allows_later_task_to_use_current_head(self):
        previous, old_entry, candidate = self.stopped_source()
        first = self.scenario.task("revalidate-ui")
        path = self.enter(first)
        with self.ledger._transaction():
            self.ledger._set_state(first["id"], "delivered", "scripted certified presentation")
        self.ledger.record_cleanup(first["id"], {}, done=True)
        second = self.scenario.task("independent-later-ui")
        self.enter(second)
        entry = source_entry(self.ledger, second["id"])
        self.assertEqual(entry["commit"], candidate)
        self.assertNotEqual(entry["commit"], old_entry["commit"])
        self.assertEqual(entry["predecessor_tasks"], [])

    def test_unknown_older_predecessor_branch_is_refused_without_new_checkout(self):
        task = self.scenario.task()
        path = self.enter(task)
        self.ledger.connection.execute("DELETE FROM audit WHERE item_id=? AND kind='task_source_entry'", (task["id"],))
        self.ledger.claim(task["id"], worker_id="older-ui")
        self.ledger.cancel(self.scenario.parent, "Stop")
        self.scheduler._retire(task["id"], "cancelled")
        self.scheduler._retire(self.scenario.parent, "cancelled")
        self.successor()
        successor = self.scenario.task("fresh-key")
        with self.assertRaisesRegex(LedgerError, "unknown or ambiguous"):
            self.enter(successor)
        self.assertIsNone(source_entry(self.ledger, successor["id"]))
