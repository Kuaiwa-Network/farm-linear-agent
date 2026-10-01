"""foreign-work: other people's PRs and branches for an issue, listed and never acted on (spec §4.5)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_cli
from agent import foreign_work
from agent.ledger import Ledger, LedgerError
from agent.publication import github_repository
from test_ledger import ISSUE, SESSION, issue
from test_worktrees import git

FAKE_GH = Path(__file__).resolve().parent / "fake_gh.py"
ORG = "https://github.com/example-org/"
OWN = ORG + "Farm-Client/pull/5"
HUMAN = ORG + "Farm-Client/pull/7"
ELSEWHERE = ORG + "farmgui/pull/3"
BRANCHES = {"Farm-Client": ("farmbot/farm-1", "farmbot/farm-1-harvest", "designer-one/farm-1-harvest", "farmbot/farm-12"),
            "farm-hive": ("farmbot/farm-1", "farmbot/farm-1-config", "owner-two/FARM-1-服务端")}
ANSWERS = {
    "example-org/Farm-Client": [
        {"number": 5, "url": OWN, "title": "FARM-1 修复收获翻倍", "state": "OPEN", "isDraft": True,
         "headRefName": "farmbot/farm-1-harvest", "isCrossRepository": False,
         "author": {"login": "farmbot-operator"}, "body": ""},
        {"number": 7, "url": HUMAN, "title": "修复收获", "state": "MERGED", "isDraft": False,
         "headRefName": "designer-one/farm-1-harvest", "isCrossRepository": False,
         "author": {"login": "designer-one", "name": "Designer One"}, "body": "Fixes FARM-1"},
        {"number": 9, "url": ORG + "Farm-Client/pull/9", "title": "FARM-12 另一张卡", "state": "OPEN",
         "isDraft": False, "headRefName": "farmbot/farm-12", "isCrossRepository": False,
         "author": {"login": "owner-two"}, "body": None},
        {"number": 4, "url": ORG + "farm-hive/pull/4", "title": "FARM-1 listed under another repository",
         "state": "OPEN", "isDraft": False, "headRefName": "x", "isCrossRepository": False,
         "author": {"login": "owner-two"}, "body": ""}],
    "example-org/farm-hive": {"fail": "HTTP 502 while using token dummy-secret-value"}}


def refs(path):
    """The origin's refs as raw bytes: nothing here may depend on the locale's encoding."""
    return subprocess.run(["git", "for-each-ref", "--format=%(refname) %(objectname)"], cwd=path,
                          capture_output=True, check=True).stdout


class ForeignWorkTests(unittest.TestCase):
    """Real Git remotes behind GitHub-style URLs, the fake gh and a real ledger."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="外部 工作 ")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.origins = self.root / "origins"
        self.heads = {}
        for repo, branches in BRANCHES.items():
            origin = self.origins / f"{repo}.git"
            origin.mkdir(parents=True)
            git("init", "-q", "-b", "main", ".", cwd=origin)
            (origin / "README.md").write_text(repo, encoding="utf-8")
            git("add", ".", cwd=origin)
            git("commit", "-qm", "init", cwd=origin)
            for name in branches:
                git("branch", name, cwd=origin)
            self.heads[repo] = git("rev-parse", "HEAD", cwd=origin)
        self.remotes = {repo: f"{ORG}{repo}.git" for repo in BRANCHES}
        (self.root / "prs.json").write_text(json.dumps(ANSWERS, ensure_ascii=False), encoding="utf-8")
        # Git reaches the local origins through the GitHub URLs a host configures (url.<base>.insteadOf).
        self.enterContext(patch.dict(os.environ, {
            "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": f"url.{self.origins.as_posix()}/.insteadOf",
            "GIT_CONFIG_VALUE_0": ORG, "FAKE_GH_PRS": str(self.root / "prs.json"),
            "FAKE_GH_LOG": str(self.root / "gh.jsonl")}))
        self.enterContext(patch.object(foreign_work, "GH", (sys.executable, str(FAKE_GH))))
        self.ledger = Ledger(self.root / "local" / "agent" / "ledger.sqlite3")
        self.addCleanup(self.ledger.close)
        self.ledger.observe_issue(issue(attachments=[OWN, HUMAN, ELSEWHERE, "https://linear.app/example/document/spec-1"]))
        self.ledger.ensure_session(SESSION, ISSUE, delegation=True)
        previous = self.ledger.create_work_item(issue_id=ISSUE, session_id=SESSION, skill="fix")
        self.plan(previous["id"], {"prs": {"farm-hive": [
            {"branch": "farmbot/farm-1-config", "role": "config", "head": "b" * 40}]}})
        self.ledger.cancel(previous["id"], "Stop")
        self.item = self.ledger.retry(previous["id"], "continue after Stop")  # a successor linked to it
        self.plan(self.item["id"], {"prs": {"Farm-Client": [
            {"branch": "farmbot/farm-1", "role": "issue", "head": self.heads["Farm-Client"]}]}})
        self.ledger.connection.execute("INSERT INTO published_prs(issue_id,url,generation,created_at) VALUES(?,?,?,?)",
                                       (ISSUE, OWN, 0, 0))

    def plan(self, item_id, plan):
        # Written directly: this reads `plan.prs`, and validating a plan is Task 9's.
        self.ledger.connection.execute("UPDATE work_items SET checkpoint=? WHERE id=?",
                                       (json.dumps({"plan": plan}), item_id))

    def report(self, **options):
        return foreign_work.foreign_work(self.ledger, self.item["id"], self.remotes, self.root / "local" / "repos",
                                         **options)

    def gh_calls(self):
        path = self.root / "gh.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def test_the_report_separates_other_peoples_work_from_this_jobs(self):
        report = self.report()
        self.assertEqual((report["status"], report["issue"], report["repositories"]),
                         ("found", "FARM-1", ["Farm-Client", "farm-hive"]))
        self.assertEqual(report["foreign"]["prs"], [
            {"url": HUMAN, "repository": "Farm-Client", "title": "修复收获", "state": "MERGED", "draft": False,
             "head": "designer-one/farm-1-harvest", "author": "designer-one",
             "sources": ["github_search", "linear_attachment"]},
            {"url": ELSEWHERE, "repository": None, "title": None, "state": None, "draft": None, "head": None,
             "author": None, "sources": ["linear_attachment"]}])
        # TestBot names its branches farmbot/<key> too, so an unrecorded one is foreign.
        self.assertEqual(report["foreign"]["branches"], [
            {"repository": "farm-hive", "name": "farmbot/farm-1", "head": self.heads["farm-hive"],
             "farmbot_name": True},
            {"repository": "farm-hive", "name": "owner-two/FARM-1-服务端", "head": self.heads["farm-hive"],
             "farmbot_name": False}])
        self.assertEqual(report["own"], {"prs": [OWN], "branches": [
            {"repository": "Farm-Client", "name": "farmbot/farm-1"},          # this job's plan
            {"repository": "Farm-Client", "name": "farmbot/farm-1-harvest"},  # the head of a registered PR
            {"repository": "farm-hive", "name": "farmbot/farm-1-config"}]})   # the predecessor's plan
        self.assertEqual(report["errors"], [{"repository": "farm-hive", "source": "github_search",
                                             "error": "gh pr list failed"}])
        self.assertNotIn("dummy-secret-value", json.dumps(report, ensure_ascii=False))

    def test_a_narrowed_report_reads_github_and_branches_for_one_repository(self):
        report = self.report(repositories=["Farm-Client"])
        self.assertEqual(report["repositories"], ["Farm-Client"])
        self.assertEqual([argv[argv.index("--repo") + 1] for argv in self.gh_calls()], ["example-org/Farm-Client"])
        self.assertEqual((report["foreign"]["branches"], report["errors"]), ([], []))
        self.assertEqual([pr["url"] for pr in report["foreign"]["prs"]], [HUMAN, ELSEWHERE])  # attachments stay whole

    def test_github_is_searched_with_one_argument_list_and_read_as_utf8(self):
        self.assertEqual(foreign_work.search_prs("example-org/Farm-Client", "FARM-1")[0]["title"], "FARM-1 修复收获翻倍")
        self.assertEqual(self.gh_calls(), [["pr", "list", "--repo", "example-org/Farm-Client", "--search", "FARM-1",
                                            "--state", "all", "--limit", "101", "--json",
                                            "number,url,title,state,isDraft,headRefName,isCrossRepository,author,body"]])
        with self.assertRaises(foreign_work.ForeignWorkError) as caught:
            foreign_work.search_prs("example-org/farm-hive", "FARM-1")
        self.assertNotIn("dummy-secret-value", str(caught.exception))

    def test_every_way_gh_or_git_can_fail_raises_with_a_fixed_message(self):
        """A timeout, a missing gh, output that is not JSON or not a list: each is an error, never an empty
        answer, and none repeats the tool's output."""
        cases = [(dict(side_effect=subprocess.TimeoutExpired("gh", foreign_work.TIMEOUT)), "gh pr list timed out"),
                 (dict(side_effect=FileNotFoundError("gh")), "gh is unavailable"),
                 (dict(return_value=SimpleNamespace(returncode=0, stdout="not json: dummy-secret-value")),
                  "gh pr list returned no JSON"),
                 (dict(return_value=SimpleNamespace(returncode=0, stdout="{\"token\": \"dummy-secret-value\"}")),
                  "gh pr list returned no list")]
        for run, message in cases:
            with self.subTest(message=message), patch.object(foreign_work.subprocess, "run", **run):
                with self.assertRaises(foreign_work.ForeignWorkError) as caught:
                    foreign_work.search_prs("example-org/Farm-Client", "FARM-1")
                self.assertEqual(str(caught.exception), message)
        for run in (dict(side_effect=subprocess.TimeoutExpired("git", foreign_work.TIMEOUT)),
                    dict(side_effect=OSError("no git"))):
            with self.subTest(run=run), patch.object(foreign_work.subprocess, "run", **run):
                with self.assertRaisesRegex(foreign_work.ForeignWorkError, "git ls-remote failed"):
                    foreign_work.remote_branches(self.remotes["farm-hive"], self.root)

    def test_a_search_with_more_results_than_the_limit_is_an_unread_source(self):
        """`gh pr list` stops at its limit and says nothing: one more is requested, and a full page is an error."""
        def unrelated(count):
            return [{"number": n, "url": ORG + f"Farm-Client/pull/{n}", "title": f"FARM-1{n:03d} another card",
                     "state": "MERGED", "isDraft": False, "headRefName": f"farmbot/farm-1{n:03d}",
                     "isCrossRepository": False, "author": {"login": "someone"}, "body": ""}
                    for n in range(100, 100 + count)]
        truncated = {"repository": "Farm-Client", "source": "github_search",
                     "error": "gh pr list returned more than 100 results; some were not read"}
        self.ledger.observe_issue(issue(attachments=[OWN]))
        for count, status, errors in ((100, "none", []), (101, "incomplete", [truncated])):
            with self.subTest(count=count):
                (self.root / "prs.json").write_text(json.dumps({"example-org/Farm-Client": unrelated(count)}),
                                                    encoding="utf-8")
                with patch.object(foreign_work, "remote_branches", return_value=[]):
                    report = self.report(repositories=["Farm-Client"])
                self.assertEqual((report["status"], report["errors"]), (status, errors))

    def test_remote_branches_are_listed_without_fetching_or_writing(self):
        before = {repo: refs(self.origins / f"{repo}.git") for repo in BRANCHES}
        branches = dict(foreign_work.remote_branches(self.remotes["farm-hive"], self.root / "no-clones-yet"))
        self.assertEqual(set(branches), {"main", *BRANCHES["farm-hive"]})
        self.assertEqual(branches["owner-two/FARM-1-服务端"], self.heads["farm-hive"])
        self.assertEqual({repo: refs(self.origins / f"{repo}.git") for repo in BRANCHES}, before)
        with self.assertRaises(foreign_work.ForeignWorkError):
            foreign_work.remote_branches(ORG + "no-such-repository.git", self.root)

    def test_the_key_matches_as_a_whole_token_in_any_case(self):
        carries = foreign_work.key_pattern("FARM-1").search
        for name in ("farmbot/farm-1", "farmbot/farm-1-材料", "designer/FARM-1", "FARM-1 修复"):
            self.assertTrue(carries(name), name)
        for name in ("farmbot/farm-12", "xfarm-1", "farm1", "farm-10-x"):
            self.assertFalse(carries(name), name)

    def test_a_plan_is_read_for_branch_names_and_pr_urls_wherever_an_entry_keeps_them(self):
        branches, prs = foreign_work.plan_work({"prs": {"Farm-Contract": [
            {"branch": "farmbot/farm-1", "role": "issue", "head": "c" * 40,
             "pr": {"url": ORG + "Farm-Contract/pull/2", "state": "OPEN", "merge": "merge"}}]}})
        self.assertEqual(branches, {("Farm-Contract", "farmbot/farm-1")})
        self.assertEqual(prs, {("example-org", "farm-contract", 2): ORG + "Farm-Contract/pull/2"})
        for plan in (None, {}, {"stages": {}}, {"prs": []}, {"prs": {"Farm-Client": "farmbot/farm-1"}}):
            with self.subTest(plan=plan):
                self.assertEqual(foreign_work.plan_work(plan), (set(), {}))

    def test_nothing_foreign_is_none_and_an_unread_source_is_never_none(self):
        self.ledger.observe_issue(issue(attachments=[OWN]))
        with patch.object(foreign_work, "search_prs", return_value=[]), \
                patch.object(foreign_work, "remote_branches", return_value=[("farmbot/farm-1", "a" * 40),
                                                                            ("main", "b" * 40)]):
            self.assertEqual(self.report(repositories=["Farm-Client"])["status"], "none")
        with patch.object(foreign_work, "search_prs", side_effect=foreign_work.ForeignWorkError("gh pr list failed")), \
                patch.object(foreign_work, "remote_branches", return_value=[]):
            report = self.report(repositories=["Farm-Client"])
        self.assertEqual((report["status"], report["errors"]), ("incomplete", [
            {"repository": "Farm-Client", "source": "github_search", "error": "gh pr list failed"}]))

    def test_a_failed_branch_listing_is_an_unread_source(self):
        self.ledger.observe_issue(issue(attachments=[OWN]))
        with patch.object(foreign_work, "search_prs", return_value=[]), \
                patch.object(foreign_work, "remote_branches",
                             side_effect=foreign_work.ForeignWorkError("git ls-remote failed")):
            report = self.report(repositories=["Farm-Client"])
        self.assertEqual((report["status"], report["errors"]), ("incomplete", [
            {"repository": "Farm-Client", "source": "remote_branches", "error": "git ls-remote failed"}]))

    def test_a_foreign_pr_on_a_farmbot_branch_is_reported_when_its_branch_is_gone(self):
        """TestBot's merged PR: its farmbot/ head was deleted, so only the search can still show it."""
        testbot = {"number": 11, "url": ORG + "Farm-Client/pull/11", "title": "FARM-1 TestBot's fix",
                   "state": "MERGED", "isDraft": False, "headRefName": "farmbot/farm-1-testbot",
                   "isCrossRepository": False, "author": {"login": "someone"}, "body": ""}
        self.ledger.observe_issue(issue(attachments=[OWN]))
        (self.root / "prs.json").write_text(json.dumps({"example-org/Farm-Client": [testbot]}), encoding="utf-8")
        with patch.object(foreign_work, "remote_branches", return_value=[("main", "a" * 40)]):
            report = self.report(repositories=["Farm-Client"])
        self.assertEqual((report["status"], [(pr["url"], pr["head"]) for pr in report["foreign"]["prs"]]),
                         ("found", [(testbot["url"], "farmbot/farm-1-testbot")]))

    def test_own_work_is_read_from_every_predecessor_in_the_chain(self):
        """A job stopped twice still owns the branch its first attempt recorded."""
        self.ledger.cancel(self.item["id"], "Stop again")
        third = self.ledger.retry(self.item["id"], "continue again")
        report = foreign_work.foreign_work(self.ledger, third["id"], self.remotes, self.root / "local" / "repos")
        self.assertIn({"repository": "farm-hive", "name": "farmbot/farm-1-config"}, report["own"]["branches"])
        self.assertIn({"repository": "Farm-Client", "name": "farmbot/farm-1"}, report["own"]["branches"])
        self.assertNotIn("farmbot/farm-1-config", [branch["name"] for branch in report["foreign"]["branches"]])

    def test_a_pr_a_predecessor_recorded_in_its_plan_is_own_with_its_head_branch(self):
        self.plan(self.item["predecessor_id"], {"prs": {"Farm-Client": [
            {"branch": "designer-one/farm-1-harvest", "role": "issue", "head": "d" * 40, "url": HUMAN}]}})
        report = self.report(repositories=["Farm-Client"])
        self.assertNotIn(HUMAN, [pr["url"] for pr in report["foreign"]["prs"]])
        self.assertIn(HUMAN, report["own"]["prs"])
        self.assertIn({"repository": "Farm-Client", "name": "designer-one/farm-1-harvest"}, report["own"]["branches"])

    def test_a_repository_not_on_github_is_an_error_not_a_silent_skip(self):
        report = foreign_work.foreign_work(self.ledger, self.item["id"],
                                           {"Farm-Client": str(self.origins / "Farm-Client.git")}, self.root)
        self.assertIn({"repository": "Farm-Client", "source": "github_search", "error": "not a github.com repository"},
                      report["errors"])

    def test_the_suffix_branches_a_plan_records_are_own_and_the_same_names_elsewhere_are_not(self):
        """The named suffix branches of spec §6.1: -config and its re-pins -config-<n> (P12) in common, -waivers in
        Farm-Contract, -followup in farm-hive. Recorded with their roles, they are this job's; a suffix branch the
        plan does not record for that repository, as TestBot's would be, stays foreign."""
        waivers_pr = ORG + "Farm-Contract/pull/31"
        self.ledger.observe_issue(issue(attachments=[OWN, waivers_pr]))
        self.plan(self.item["id"], {"prs": {
            "common": [{"branch": "farmbot/farm-1", "role": "issue", "head": "c" * 40, "pr": None},
                       {"branch": "farmbot/farm-1-config", "role": "config", "head": "d" * 40, "pr": None},
                       {"branch": "farmbot/farm-1-config-2", "role": "config", "head": "b" * 40, "pr": None}],
            "Farm-Contract": [{"branch": "farmbot/farm-1-waivers", "role": "waivers", "head": "e" * 40,
                               "pr": {"url": waivers_pr, "state": "OPEN", "merge": None}}],
            "farm-hive": [{"branch": "farmbot/farm-1-followup", "role": "followup", "head": "f" * 40, "pr": None}]}})
        remotes = {repo: f"{ORG}{repo}.git" for repo in ("common", "Farm-Contract", "farm-hive")}
        listed = {remotes["common"]: [("main", "a" * 40), ("farmbot/farm-1", "c" * 40),
                                      ("farmbot/farm-1-config", "d" * 40), ("farmbot/farm-1-config-2", "b" * 40)],
                  remotes["Farm-Contract"]: [("farmbot/farm-1-waivers", "e" * 40)],
                  remotes["farm-hive"]: [("farmbot/farm-1-followup", "f" * 40), ("farmbot/farm-1-waivers", "9" * 40)]}
        with patch.object(foreign_work, "search_prs", return_value=[]), \
                patch.object(foreign_work, "remote_branches", side_effect=lambda remote, cwd: listed[remote]):
            report = foreign_work.foreign_work(self.ledger, self.item["id"], remotes, self.root / "local" / "repos")
        self.assertEqual(report["foreign"], {"prs": [], "branches": [
            {"repository": "farm-hive", "name": "farmbot/farm-1-waivers", "head": "9" * 40, "farmbot_name": True}]})
        self.assertLessEqual({("common", "farmbot/farm-1-config"), ("common", "farmbot/farm-1-config-2"),
                              ("Farm-Contract", "farmbot/farm-1-waivers"), ("farm-hive", "farmbot/farm-1-followup")},
                             {(branch["repository"], branch["name"]) for branch in report["own"]["branches"]})
        self.assertIn(waivers_pr, report["own"]["prs"])

    def test_listing_changes_nothing(self):
        before = (list(self.ledger.connection.iterdump()), {repo: refs(self.origins / f"{repo}.git") for repo in BRANCHES})
        self.report()
        self.assertEqual((list(self.ledger.connection.iterdump()),
                          {repo: refs(self.origins / f"{repo}.git") for repo in BRANCHES}), before)
        self.assertTrue(self.gh_calls())
        self.assertTrue(all(argv[:2] == ["pr", "list"] for argv in self.gh_calls()))


class ForeignWorkCliTests(unittest.TestCase):
    """The worker command is claim-authenticated and read-only; verify-publication carries its report."""
    setUp = test_cli.CliTests.setUp
    publication_fixture = test_cli.CliTests.publication_fixture
    verification_fixture = test_cli.CliTests.verification_fixture
    root_item = test_cli.CliTests.root_item
    seeded_item = test_cli.CliTests.seeded_item
    json_file = test_cli.CliTests.json_file
    run_cli = test_cli.CliTests.run_cli

    def foreign_work_args(self, args):
        from agent.__main__ import parser
        return parser().parse_args(["--db", str(self.db), "foreign-work", "--item", args.item, "--token", args.token])

    @staticmethod
    def job_state(ledger):
        def rows(query):
            return [tuple(row) for row in ledger.connection.execute(query)]
        return (rows("SELECT id, metadata, fingerprint FROM issues"),
                rows("SELECT id, state, stage, checkpoint, generation, claimed_fingerprint FROM work_items"),
                rows("SELECT issue_id, url FROM published_prs"))

    @staticmethod
    def human_pr(config):
        base = "https://github.com/" + github_repository(config.repos["Farm-Client"])
        return {"number": 7, "url": base + "/pull/7", "title": "FARM-1 修复收获", "state": "OPEN", "isDraft": False,
                "headRefName": "designer-one/farm-1", "isCrossRepository": False,
                "author": {"login": "designer-one"}, "body": ""}

    def test_the_command_needs_the_claim(self):
        item = self.seeded_item()
        self.assertIn("claim token required", self.run_cli("foreign-work", "--item", item, success=False).stderr)
        self.assertIn("running claim", self.run_cli("foreign-work", "--item", item, "--token", "claim_not-this-one",
                                                    success=False).stderr)

    def test_a_wrong_token_is_refused_before_any_source_is_read(self):
        from agent.__main__ import parser, run
        args, ledger, config, api, _ = self.publication_fixture()
        wrong = parser().parse_args(["--db", str(self.db), "foreign-work", "--item", args.item,
                                     "--token", "claim_not-this-one"])
        with patch("agent.__main__.load_config", return_value=config), \
                patch("agent.foreign_work.search_prs", return_value=[]) as search, \
                patch("agent.foreign_work.remote_branches", return_value=[]) as branches:
            with self.assertRaisesRegex(LedgerError, "running claim"):
                run(wrong, ledger, lambda: api)
        self.assertEqual((search.call_count, branches.call_count), (0, 0))

    def stopped_meanwhile(self, item):
        """A foreign_work stand-in: the operator's Stop lands while the sources are being read."""
        def read(*_, **__):
            other = Ledger(self.db)
            try:
                other.cancel(item, "Stop")
            finally:
                other.close()
            return {"status": "none", "errors": []}
        return read

    def test_a_stop_during_the_reads_ends_the_command_without_a_report(self):
        from agent.__main__ import run
        args, ledger, config, api, _ = self.publication_fixture()
        with patch("agent.__main__.load_config", return_value=config), \
                patch("agent.foreign_work.foreign_work", side_effect=self.stopped_meanwhile(args.item)):
            with self.assertRaises(LedgerError):
                run(self.foreign_work_args(args), ledger, lambda: api)
        self.assertEqual(ledger.item(args.item)["state"], "cancelled")

    def test_verify_publication_ends_without_a_result_when_stopped_during_the_foreign_work_read(self):
        from agent.__main__ import run
        args, ledger, config, api, github = self.publication_fixture()
        with patch("agent.__main__.load_config", return_value=config), \
                patch("agent.publication.github_api", side_effect=github), \
                patch("agent.foreign_work.foreign_work", side_effect=self.stopped_meanwhile(args.item)):
            with self.assertRaises(LedgerError):
                run(args, ledger, lambda: api)
        self.assertEqual(ledger.item(args.item)["state"], "cancelled")

    def test_the_command_prints_the_report_and_touches_neither_linear_nor_the_job(self):
        from agent.__main__ import run
        args, ledger, config, api, _ = self.publication_fixture()
        human = self.human_pr(config)
        before = self.job_state(ledger)
        with patch("agent.__main__.load_config", return_value=config), \
                patch("agent.foreign_work.search_prs", return_value=[human]) as search, \
                patch("agent.foreign_work.remote_branches", return_value=[("farmbot/farm-1", "a" * 40)]):
            report = run(self.foreign_work_args(args), ledger, lambda: self.fail("foreign-work must not call Linear"))
        search.assert_called_once_with(github_repository(config.repos["Farm-Client"]), "FARM-1")
        self.assertEqual((report["status"], [pr["url"] for pr in report["foreign"]["prs"]]), ("found", [human["url"]]))
        # The worktree's branch name proves nothing: only a plan or a registered PR makes a branch this job's.
        self.assertEqual(report["foreign"]["branches"], [
            {"repository": "Farm-Client", "name": "farmbot/farm-1", "head": "a" * 40, "farmbot_name": True}])
        self.assertEqual(self.job_state(ledger), before)
        json.dumps(report, ensure_ascii=True, allow_nan=False)  # the one JSON object main prints

    def test_the_command_uses_the_configured_host_ledger(self):
        from agent.__main__ import run
        args, ledger, config, api, _ = self.publication_fixture()
        config.local_root = self.root / "another-host"
        with patch("agent.__main__.load_config", return_value=config):
            with self.assertRaisesRegex(LedgerError, "configured host ledger"):
                run(self.foreign_work_args(args), ledger, lambda: api)

    def test_verify_publication_carries_the_report_for_its_repository_and_still_verifies(self):
        from agent.__main__ import run
        args, ledger, config, api, github = self.publication_fixture()
        config.repos["farm-hive"] = "https://github.com/example-org/farm-hive.git"
        with patch("agent.__main__.load_config", return_value=config), \
                patch("agent.publication.github_api", side_effect=github), \
                patch("agent.foreign_work.search_prs", return_value=[self.human_pr(config)]) as search, \
                patch("agent.foreign_work.remote_branches", return_value=[]):
            result = run(args, ledger, lambda: api)
        self.assertEqual(result["status"], "verified")
        self.assertEqual((result["foreign_work"]["status"], result["foreign_work"]["repositories"]),
                         ("found", ["Farm-Client"]))
        search.assert_called_once_with(github_repository(config.repos["Farm-Client"]), "FARM-1")
        self.assertEqual(ledger.item(args.item)["state"], "running")

    def test_verify_publication_still_verifies_when_a_source_could_not_be_read(self):
        """Evidence, not a gate: an unread source is reported beside the verified destination, not refused."""
        from agent.__main__ import run
        args, ledger, config, api, github = self.publication_fixture()
        with patch("agent.__main__.load_config", return_value=config), \
                patch("agent.publication.github_api", side_effect=github), \
                patch("agent.foreign_work.search_prs", side_effect=foreign_work.ForeignWorkError("gh pr list failed")), \
                patch("agent.foreign_work.remote_branches", return_value=[]):
            result = run(args, ledger, lambda: api)
        self.assertEqual((result["status"], result["foreign_work"]["status"]), ("verified", "incomplete"))
        self.assertEqual(result["foreign_work"]["errors"],
                         [{"repository": "Farm-Client", "source": "github_search", "error": "gh pr list failed"}])

    def test_a_failed_check_is_reported_and_never_blocks_verification(self):
        from agent.__main__ import run
        args, ledger, config, api, github = self.publication_fixture()
        with patch("agent.__main__.load_config", return_value=config), \
                patch("agent.publication.github_api", side_effect=github), \
                patch("agent.foreign_work.foreign_work", side_effect=OSError("disk")):
            result = run(args, ledger, lambda: api)
        self.assertEqual((result["status"], result["foreign_work"]),
                         ("verified", {"status": "unavailable", "error": "OSError"}))
