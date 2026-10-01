"""A Code job's journey, offline (feature-workers design §12, "Journey"; Phase B plan, Task 15).

The controller runs as `serve` builds it (`agent.service.build`, `fake` runtime) without its threads: the test turns
the receiver, the scheduler and the status check itself. Every worker attempt is `tests/fake_cli.py` in its `cli`
mode, started by the real launcher, running the real worker CLI and real Git from a script the test sets before the
attempt launches. The fake is not the feature skill: it takes a feature worker's steps in the skill's order, so that
the controller's side of every stage can be checked (roots, worktrees and branches, the read-only default-branch
checkouts, the plan, notices, pauses, registered PRs, cleanup).

Linear is the file stub (`FARMBOT_LINEAR_STUB_DIR`). The five repositories are local Git origins behind the configured
`https://github.com/example-org/...` remotes, reached through a `url.<base>.insteadOf` rewrite in the environment,
with the host's own Git configuration ignored; the read-only checkouts of the manifest's three `reads` repositories
are fetched from the same origins. The `feature` manifest is the checkout's own `skills/feature/skill.json`. Nothing
reaches GitHub: the controller's publication check at launch stops at the rewritten push URL, and the fake never runs
`verify-publication` or `foreign-work`, which have tests of their own.

What each check needs from Phase B: routing, the acknowledgement and the missing target (Tasks 1, 3 and 12);
`tools.lark_cli` (Task 10); `reads` (Task 6); the `stage` and `merge_request` notices (Task 9); re-attachment of a
successor and of a retried job (Tasks 2 and 5); a conversation continuing a stopped Code job (Tasks 2 and 3); one
exclusive attempt at a time (Task 7); a parked job cancelled after confirmation, and a running one checkpointing then withdrawing, and
`fetch-issue`'s `delegated` (Task 8); an enqueued Code job without a target, and `await-resource` refused for a
resource the manifest does not list (P11).
"""
import hashlib
import hmac
import json
import os
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from agent.config import load_config
from agent.launcher import _PINNED_EXIT
from agent.receiver import ACK
from agent.service import build, enqueue
from test_ledger import DESIGNER, ISSUE, OTHER, OWNER, comment, issue

ROOT = Path(__file__).resolve().parents[1]
APP = "e5a8c16d-9f85-4123-acf5-94e41c3304d5"  # the stub Linear's app user (agent.config.StubLinear)
THIRD = "10000000-0000-4000-8000-000000000003"
ORG = "https://github.com/example-org/"
REPOS = ("Farm-Contract", "common", "farm-hive", "Farm-Client", "farmgui")
FEATURE = ("Farm-Contract", "common", "farm-hive")  # what the feature manifest writes in Phase B (P2)
READS = ("Farm-Contract", "Farm-Client", "farmgui")  # what it reads, each at its default branch (P2)
SESSION = "session-code"
BRANCH = "farmbot/farm-1"
CONFIG_BRANCH = "farmbot/farm-1-config"
WAIVERS_BRANCH = "farmbot/farm-1-waivers"
CONFIG_REF = "designer/farm-1-config-data"
PRS = {"Farm-Contract": ORG + "Farm-Contract/pull/12", "common": ORG + "common/pull/34",
       "farm-hive": ORG + "farm-hive/pull/56"}
WAIVERS_PR = ORG + "Farm-Contract/pull/13"
FOLLOWUP_BRANCH = f"{BRANCH}-followup"
FOLLOWUP_PR = ORG + "farm-hive/pull/57"
ALL_PRS = sorted([*PRS.values(), WAIVERS_PR])
PROFILE = "farmbot-journey"
SECRET = "journey-client-secret"
SIGNING = "journey-signing-secret"
NOW = "2026-09-28T02:00:00+00:00"
SKIPPED = "skipped: 这次变更不需要新的配置表"
# request-repair's acknowledgement for work other than a fix (plan, Shared Interfaces, "Worker-visible changes").
QUEUED = "已排队开始或继续这项工作，会接着你的回复和已有调查结果处理。"
QUESTIONS = ("FarmBot 起草合约前需要这些答复（按角色分组）。\n\n## 主策\n- 低置信度：同类加成能否叠加？\n\n"
             f"请 {OWNER['url']} 转给相关的人；{DESIGNER['url']} 请回答主策部分。")
MERGE_CONTRACT = (f"合约草稿 PR：{PRS['Farm-Contract']}\n请 {OWNER['url']} review 后合并。"
                  "合并后 BREAKING_WAIVERS 会过期，FarmBot 会另开 PR 移除；下一阶段不等合并。")
CONFIG_NEEDED = (f"配置声明草稿 PR：{PRS['common']}，请 {OWNER['url']} review 后合并。\n"
                 f"{DESIGNER['url']} 请在「加成」表按表头「加成比例」（int32）填数据，"
                 "提交后在本 issue 写明分支或提交并回复。")
CLOSING = (f"服务端草稿 PR：{PRS['farm-hive']}。收尾需要（本卡不含 UI 步骤）：\n"
           f"1. {OWNER['url']} 合并合约 PR {PRS['Farm-Contract']}；\n"
           f"2. 在 Jenkins 用 {CONFIG_BRANCH} 分支运行 designer-source.pipeline，把打印的三行 pin 贴到本 issue；\n"
           "3. 完成任一步后在这里回复。卡片移到 Done 或 Canceled 会取消这项工作。")
CLOSING_WITHOUT_CONFIG = (f"服务端草稿 PR：{PRS['farm-hive']}。收尾需要（本卡不含 UI 步骤）：\n"
                          f"1. {OWNER['url']} 合并合约 PR {PRS['Farm-Contract']}，然后在这里回复。\n"
                          "卡片移到 Done 或 Canceled 会取消这项工作。")
MERGE_WAIVERS = f"移除过期 BREAKING_WAIVERS 的草稿 PR：{WAIVERS_PR}，请 {OWNER['url']} 合并。"
STILL_WAITING = ("已完成：服务端已按合并后的合约重新同步。\n还在等：Jenkins 打印的三行 pin。\n"
                 f"请处理：{OWNER['url']}，贴到本 issue 后在这里回复。")
PIN_LINES = "设计源版本 20260928.1a2b3c4\n内容摘要 0f0e0d0c\n归档校验 0a0b0c0d"
DELIVERY = ("FarmBot 已完成这张功能卡的合约、配置声明和服务端（草稿 PR，待 review）：\n"
            + "".join(f"- {url}\n" for url in ALL_PRS)
            + f"- 还需合并：请 {OWNER['url']} 按顺序合并移除豁免的 PR 和服务端 PR，FarmBot 不会合并\n"
            "- 客户端还要做：协议导出、配置导出和客户端代码，本卡这次不做")


def git(*args, cwd):
    """Git as a person at a terminal would run it, with a fixed identity and the environment the test sets."""
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True,
                          capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()


@unittest.skipUnless(os.name == "nt" or _PINNED_EXIT,
                     "a repository handoff completes on teardown evidence, which a worker that exits by itself leaves "
                     "only with os.waitid (CPython 3.13+ on macOS) or a Windows Job Object")
class CodeJobJourneyTests(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="功能 旅程 ")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.origins, self.stub, self.work = self.root / "origins", self.root / "stub", self.root / "worker"
        for path in (self.origins, self.stub, self.work):
            path.mkdir()
        (self.root / "gitconfig").write_text("", encoding="utf-8")
        config_path = self.root / "config" / "farmbot.json"
        config_path.parent.mkdir()
        config_path.write_text(json.dumps({
            "client_id": "journey-client", "client_secret": SECRET, "webhook_secret": SIGNING, "host": "journey",
            "runtime": "fake", "repos": {repo: f"{ORG}{repo}.git" for repo in REPOS}, "max_concurrent": 2,
            "port": 0, "local_root": str(self.root / "local"), "enabled_skills": ["chat", "fix", "feature"],
            "lark_cli": {"profile": PROFILE}}, ensure_ascii=False), encoding="utf-8")
        self.enterContext(patch.dict(os.environ, {
            "FARMBOT_LINEAR_STUB_DIR": str(self.stub), "FARMBOT_CONFIG": str(config_path),
            "FAKE_CLI_REPO": str(ROOT), "FAKE_CLI_MODE": "cli", "FAKE_CLI_STEPS": "[]",
            # The host's own Git configuration stays out, so no push rewrite or credential helper of its own applies.
            "GIT_CONFIG_GLOBAL": str(self.root / "gitconfig"), "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": f"url.{self.origins.as_posix()}/.insteadOf",
            "GIT_CONFIG_VALUE_0": ORG, "GIT_TERMINAL_PROMPT": "0"}))
        for repo in REPOS:
            origin = self.origins / f"{repo}.git"
            origin.mkdir()
            git("init", "-q", "-b", "main", ".", cwd=origin)
            (origin / "README.md").write_text(repo, encoding="utf-8")
            git("add", ".", cwd=origin)
            git("commit", "-qm", "init", cwd=origin)
        self.version, self.replies, self.fields, self.plan = 0, 0, {}, {}
        self.refresh_issue()
        self.config = load_config(config_path)
        self.start_controller()

    # The controller.

    def start_controller(self):
        """The controller as `serve` builds it; the test turns its loops."""
        self.c = build(self.config)
        self.addCleanup(self.stop_controller, self.c)

    @staticmethod
    def stop_controller(c):
        """A clean shutdown's closes, after stopping and reaping any worker a failed test left running."""
        deadline = time.time() + 20
        for item_id in list(c.launcher.running()):
            c.launcher.stop(item_id)
        while c.launcher.running() and time.time() < deadline:
            c.launcher.poll()
            time.sleep(0.05)
        c.server.server_close()
        c.receiver.close()
        c.ledger.close()
        c.pool.close()
        c.lifecycle.ledger.close()
        c.progress.ledger.close()
        c.recovery.close()

    def restart(self):
        """A clean restart of the settled service on the same state root."""
        self.tick_until(lambda: not self.c.launcher.running(), "the last worker to be reaped")
        self.stop_controller(self.c)
        self.start_controller()

    def tick_until(self, done, what, timeout=60):
        deadline = time.time() + timeout
        while not done():
            if time.time() > deadline:
                self.fail(f"timed out waiting for {what}")
            self.c.scheduler.tick()
            time.sleep(0.05)

    def wait_for(self, done, what, timeout=90):
        """Without ticking: a tick could complete a handoff and launch the next attempt before its script is set."""
        deadline = time.time() + timeout
        while not done():
            if time.time() > deadline:
                self.fail(f"timed out waiting for {what}")
            time.sleep(0.05)

    # Linear, as the stub and signed webhooks.

    def card(self, *, id=ISSUE, identifier="FARM-1", label="Code", **changes):
        """An issue as the stub serves it: one Bot child, delegated to the app, owned by Owner Two and written by
        Designer One. Each call moves `updated_at` on, as every change in Linear does."""
        self.version += 1
        stamp = datetime(2026, 9, 28, 1, tzinfo=timezone.utc) + timedelta(minutes=self.version)
        value = issue(id=id, identifier=identifier, branch_name=f"farmbot/{identifier.lower()}",
                      title="收获加成：新增加成配置表", description="详见策划案。", labels=[label],
                      label_groups=[{"group": "Bot", "label": label}], delegate_id=APP, assignee=OWNER,
                      creator=DESIGNER, updated_at=stamp.isoformat())
        value.update(changes)
        return value

    def publish(self, value):
        """What the stub answers for that issue from now on."""
        (self.stub / f"issue-{value['id']}.json").write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def refresh_issue(self, **changes):
        """FARM-1 as it now stands in Linear, every earlier change kept."""
        self.fields.update(changes)
        self.publish(self.card(**self.fields))

    def human_comment(self, body, *, author, publish=True):
        """A person's comment on FARM-1, kept for every later read. Unpublished, it is the issue JSON that a scripted
        step writes into the stub while an attempt runs."""
        number = len(self.fields.get("comments", [])) + 1
        self.fields["comments"] = [*self.fields.get("comments", []), comment(
            body, id=f"human-{number}", author=author, parent_id=None,
            url=f"https://linear.app/example/issue/FARM-1#comment-human-{number}")]
        if publish:
            self.refresh_issue()
        return json.dumps(self.card(**self.fields), ensure_ascii=False)

    def event(self, action, session, *, issue_id=ISSUE, identifier="FARM-1", **fields):
        return {"type": "AgentSessionEvent", "action": action, "webhookTimestamp": int(time.time() * 1000),
                "organizationId": "org", "oauthClientId": "journey-client", "appUserId": APP,
                "agentSession": {"id": session, "creator": dict(OWNER),
                                 "issue": {"id": issue_id, "identifier": identifier, "url": "u"}}, **fields}

    def deliver(self, event, expected="accepted"):
        body = json.dumps(event, ensure_ascii=False).encode("utf-8")
        signature = hmac.new(SIGNING.encode("utf-8"), body, hashlib.sha256).hexdigest()
        self.assertEqual(self.c.receiver.receive(body, signature), (200, expected))
        while self.c.receiver.process_one():
            pass

    def delegate(self, session=SESSION, *, issue_id=ISSUE, identifier="FARM-1"):
        """The owner delegates the card from the Linear UI, without text; the item it created."""
        self.deliver(self.event("created", session, issue_id=issue_id, identifier=identifier))
        (item,) = self.c.ledger.items_for_session(session)
        return item["id"]

    def reply(self, text, *, author=OWNER, session=SESSION):
        self.replies += 1
        self.deliver(self.event("prompted", session, agentActivity={
            "id": f"reply-{self.replies}", "agentSessionId": session, "user": dict(author),
            "createdAt": datetime.now(timezone.utc).isoformat(), "content": {"type": "prompt", "body": text}}))

    def stop(self, session=SESSION):
        self.deliver(self.event("prompted", session, agentActivity={
            "id": "stop-1", "agentSessionId": session, "signal": "stop", "content": {"type": "prompt"},
            "createdAt": datetime.now(timezone.utc).isoformat()}), expected="stop received")

    def calls(self, method):
        path = self.stub / "calls.jsonl"
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
        return [row for row in rows if row["method"] == method]

    # Worker attempts.

    def runs(self, item_id):
        state = self.c.launcher.state_dir(item_id)
        return {path for path in state.iterdir() if (path / "prompt.md").is_file()} if state.is_dir() else set()

    def launch(self, item_id, steps, what):
        """Tick until the item's next attempt starts with `steps` as its script. The script is set before the first
        of those ticks, because the tick that completes a handoff launches the next attempt in the same pass."""
        os.environ["FAKE_CLI_STEPS"] = json.dumps(steps)
        before = self.runs(item_id)
        self.tick_until(lambda: self.runs(item_id) - before, f"{what} to launch")
        (run,) = self.runs(item_id) - before
        return run, json.loads((run / "prompt.md").read_text(encoding="utf-8").split("\n\n", 1)[1])

    def settle(self, item_id, run, what):
        """Wait, without ticking, for the attempt's script to end, and require that it ended well."""
        last = run / "last_message.txt"

        def ended():
            text = last.read_text(encoding="utf-8") if last.is_file() else ""
            return text.startswith("cli-error:") or text == f"cli-done:{item_id}"
        self.wait_for(ended, f"{what} to finish its script")
        message = last.read_text(encoding="utf-8")
        self.assertEqual(message, f"cli-done:{item_id}", f"{what}: {message}")

    def wait_busy(self, run, marker, what):
        """Wait, without ticking, until the attempt reaches the `wait` step after writing `marker`; an attempt whose
        script failed before it fails the test at once, with the fake's message."""
        last = run / "last_message.txt"
        self.wait_for(lambda: marker.is_file() or last.is_file(), f"{what} to be busy")
        if not marker.is_file():
            self.fail(f"{what}: {last.read_text(encoding='utf-8')}")

    def attempt(self, item_id, steps, what):
        """One attempt from its launch to the end of its script: the run directory and the launch payload."""
        run, payload = self.launch(item_id, steps, what)
        self.settle(item_id, run, what)
        return run, payload

    @staticmethod
    def intake():
        """How every attempt begins: the claim, the inbox and a fresh read of the issue, which is still delegated
        to this app (`fetch-issue`'s `delegated`, Task 8)."""
        return [["claim", "--item", "{item}", "--worker-id", "fake"],
                ["pop-inbox", "--item", "{item}", "--token", "{token}"],
                ["fetch-issue", "--item", "{item}"], ["expect", "delegated", "true"],
                ["issue-context", "--item", "{item}"]]

    def started(self):
        path = self.work / "started.md"
        path.write_text("👀 FarmBot 已开始处理：正在读策划案和合约，问题和草稿 PR 会补充在本 issue。", encoding="utf-8")
        return [["prepare-comment", "--item", "{item}", "--token", "{token}", "--kind", "started",
                 "--body-file", str(path)],
                ["post-comment", "--item", "{item}", "--token", "{token}", "--action-id", "{action_id}"]]

    def notice(self, kind, request_id, text):
        path = self.work / f"{request_id}.md"
        path.write_text(text, encoding="utf-8")
        return [["prepare-notice", "--item", "{item}", "--token", "{token}", "--kind", kind,
                 "--request-id", request_id, "--body-file", str(path)],
                ["post-notice", "--item", "{item}", "--token", "{token}", "--request-id", request_id]]

    def save(self, name, *, stage, next_action, published=()):
        """Write and save a checkpoint: the plan as it now stands (any `{rev:...}` in it read when the step runs), a
        handoff naming the next action, and the PRs this attempt opened."""
        path = self.work / f"checkpoint-{name}.json"
        body = {"stage": stage, "plan": self.plan, "published_prs": list(published),
                "handoff": {"facts": [], "hypotheses": [], "checks": [], "repositories": [],
                            "next_actions": [next_action]}}
        return [["file", str(path), json.dumps(body, ensure_ascii=False)],
                ["checkpoint", "--item", "{item}", "--token", "{token}", "--input", str(path)]]

    @staticmethod
    def pause(reason, question):
        return [["await-input", "--item", "{item}", "--token", "{token}", "--question", question, "--reason", reason]]

    @staticmethod
    def handoff(to):
        return [["handoff-repository", "--item", "{item}", "--token", "{token}", "--to", to]]

    @staticmethod
    def fresh_base(repo):
        """A stage's branch with no commits yet starts from the default branch as fetched now (Task 13, Publishing)."""
        return [["git", repo, "fetch", "-q", "origin"], ["git", repo, "merge", "-q", "--ff-only", "origin/main"]]

    @staticmethod
    def commit_and_push(repo, message, branch=BRANCH):
        return [["git", repo, "commit", "--allow-empty", "-q", "-m", message],
                ["git", repo, "push", "-q", "origin", f"HEAD:refs/heads/{branch}"]]

    @staticmethod
    def entry(repo, role, url, *, branch=BRANCH, head="HEAD"):
        """A `plan.prs` entry as the worker records it; its head is read in the worktree when the step runs."""
        return {"branch": branch, "role": role, "head": f"{{rev:{repo}:{head}}}",
                "pr": {"url": url, "state": "draft", "merge": None} if url else None}

    # The stages, each one attempt of the job; after each, `self.plan` is the plan the ledger holds.

    def begin(self):
        self.plan = {"stages": dict.fromkeys("ABCDG", "pending"), "started": True,
                     "change": {"name": "harvest-bonus", "path": "openspec/changes/harvest-bonus"},
                     "ui": {"has_ui": False, "packages": [], "components": []}}

    def settled(self, item_id, result):
        self.plan = self.context(item_id)["plan"]
        return result

    def run_intake_and_questions(self, item_id):
        """Stage A's first attempt: the started comment, a grouped question round and a question pause (§5.1, §6.2)."""
        self.begin()
        self.plan["pause"] = {"kind": "answers", "reason": "question", "notice": "questions-1", "since": NOW}
        return self.settled(item_id, self.attempt(item_id, [
            *self.intake(), *self.started(),
            *self.save("a1", stage="contract", next_action="读答复后写合约变更"),
            *self.notice("question", "questions-1", QUESTIONS),
            *self.pause("question", "FarmBot 在 issue 评论里问了几个问题，请回答后在这里回复。")], "stage A's intake"))

    def run_contract(self, item_id, *, first=False, then="common", comment_meanwhile=None):
        """Stage A's change: the later stages settled (with then="farm-hive" the change needs no config, so B and C
        are skipped and `stage-B` says so for both, §6.1), the contract commit and its draft PR, then the
        merge request that ends stage A and a handoff that does not wait for the merge (§6.2; P15).
        `comment_meanwhile` is the issue as a person's comment during the attempt leaves it."""
        if first:
            self.begin()
        self.plan.pop("pause", None)
        steps = [*self.intake(), *(self.started() if first else [])]
        if then == "farm-hive":
            self.plan["stages"].update(B=SKIPPED, C=SKIPPED)
            steps += self.notice("stage", "stage-B", "阶段 B（配表声明）跳过：这次变更不需要新的配置表；"
                                                     "阶段 C（配置核对）随之跳过。")
        self.plan["prs"] = {"Farm-Contract": [self.entry("Farm-Contract", "issue", PRS["Farm-Contract"])]}
        steps += [*self.fresh_base("Farm-Contract"),
                  *self.commit_and_push("Farm-Contract", "FARM-1 合约：收获加成（openspec change harvest-bonus）"),
                  *self.save("a2", stage="contract", next_action="请 owner 合并合约 PR",
                             published=[PRS["Farm-Contract"]]),
                  *self.notice("merge_request", "merge-contract", MERGE_CONTRACT)]
        self.plan["stages"]["A"] = "done"
        steps += self.save("a3", stage="contract", next_action=f"在 {then} 继续")
        if comment_meanwhile is not None:
            # A person comments while this attempt runs: the fake writes the stub's issue at that moment. The worker
            # reads it, revalidates on the fingerprint it read and saves its handoff again (spec §11).
            steps += [["file", str(self.stub / f"issue-{ISSUE}.json"), comment_meanwhile],
                      ["fetch-issue", "--item", "{item}"], ["issue-context", "--item", "{item}"],
                      ["revalidate", "--item", "{item}", "--token", "{token}", "--fingerprint", "{fingerprint}"],
                      *self.save("a3-revalidated", stage="contract", next_action=f"在 {then} 继续")]
        return self.settled(item_id, self.attempt(item_id, [*steps, *self.handoff(then)], "stage A"))

    def run_declarations(self, item_id):
        """Stage B: the definition layer on common, its draft PR, the config-needed comment, a waiting pause (§6.3)."""
        self.plan["stages"]["B"] = "done"
        self.plan["prs"]["common"] = [self.entry("common", "issue", PRS["common"])]
        self.plan["config"] = {"declared": [{"file": "designer/china/source/_table.xml/加成.xml", "sheet": "加成",
                                             "header": "加成比例", "field": "bonus_rate", "type": "int32"}]}
        self.plan["pause"] = {"kind": "config_ready", "reason": "waiting", "notice": "config-needed", "since": NOW}
        return self.settled(item_id, self.attempt(item_id, [
            *self.intake(), *self.fresh_base("common"),
            *self.commit_and_push("common", "FARM-1 声明收获加成表（定义层、清单和计数）"),
            *self.save("b", stage="declarations", next_action="策划 填好数据并写明分支后核对配置",
                       published=[PRS["common"]]),
            *self.notice("waiting", "config-needed", CONFIG_NEEDED),
            *self.pause("waiting", "等 策划 在 common 提交数据；写明分支或提交后请在这里回复。")], "stage B"))

    def name_config_ref(self):
        """策划 commit the data on a branch of their own, then name it in the issue and in the session (§6.4)."""
        origin = self.origins / "common.git"
        data = git("commit-tree", f"{BRANCH}^{{tree}}", "-p", BRANCH, "-m", "策划：填入收获加成数据", cwd=origin)
        git("update-ref", f"refs/heads/{CONFIG_REF}", data, cwd=origin)
        self.human_comment(f"数据已提交在 common 的 {CONFIG_REF} 分支。", author=DESIGNER)
        self.reply(f"配置好了：{CONFIG_REF}", author=DESIGNER)
        return data

    def run_config_check(self, item_id, *, server=True):
        """Stage C in the resumed common-rooted attempt: the named ref, then the Jenkins branch at its commit, adding
        no commits and recorded before its push; back on the issue branch, the `stage` notice for C's pass and the
        handoff to farm-hive (§6.4; Task 14; P15)."""
        message = self.context(item_id)["session_messages"][-1]
        self.plan.pop("pause")
        self.plan["config"].update(ref=CONFIG_REF, sha=f"{{rev:common:origin/{CONFIG_REF}}}")
        self.plan["prs"]["common"].append(self.entry("common", "config", None, branch=CONFIG_BRANCH,
                                                     head=f"origin/{CONFIG_REF}"))
        self.plan["events"] = [{"kind": "config_ready", "person": message["author"], "message_id": message["id"],
                                "at": message["created_at"]}]
        steps = [*self.intake(), ["git", "common", "fetch", "-q", "origin"],
                 ["git", "common", "switch", "-q", "-c", CONFIG_BRANCH, f"{{rev:common:origin/{CONFIG_REF}}}"],
                 *self.save("c1", stage="config", next_action=f"推送 {CONFIG_BRANCH}"),
                 ["git", "common", "push", "-q", "origin", f"HEAD:refs/heads/{CONFIG_BRANCH}"],
                 ["git", "common", "switch", "-q", "-"]]
        self.plan["stages"]["C"] = "done"
        self.plan["config"].update(jenkins_branch=CONFIG_BRANCH, expected_version="2026-09-28.abcdef0", pin="local")
        body = f"阶段 C 完成：{CONFIG_REF} 的表头和生成结果已核对，Jenkins 分支 {CONFIG_BRANCH} 已推送。"
        self.plan["config"]["stage_notice"] = {"request_id": "stage-C", "sha": self.plan["config"]["sha"],
                                               "jenkins_branch": CONFIG_BRANCH, "body": body}
        steps += [*self.save("c-notice", stage="config", next_action="发布保存的阶段 C 核对结果"),
                  *self.notice("stage", "stage-C", body)]
        if server:
            steps += [*self.save("c2", stage="config", next_action="在 farm-hive 同步未合并的合约并实现服务端"),
                      *self.handoff("farm-hive")]
        else:
            self.plan["stages"]["D"] = "skipped: 只有客户端需要新配置，服务端无需变更"
            self.plan["closing"] = {"waivers_removed": False, "hive_resynced": True, "pin_written": True}
            self.plan["pause"] = {"kind": "closing", "reason": "waiting", "notice": "closing", "since": NOW}
            steps += [*self.save("c2", stage="closing", next_action="合约合并后移除豁免，再交付客户端待办"),
                      *self.notice("waiting", "closing", f"请 {OWNER['url']} 合并合约和声明 PR 后回复。"
                                   "服务端已跳过；配置 SHA 留给后续客户端阶段。"),
                      *self.pause("waiting", "请合并合约和声明 PR 后回复。")]
        return self.settled(item_id, self.attempt(item_id, steps, "stage C"))

    def run_server(self, item_id):
        """Stage D: from main as fetched now, the server change against the unmerged contract, its draft PR; then, in
        the same attempt, the closing comment, which asks for no UI step, and a waiting pause (§5.7 "Fresh bases",
        §6.5, §6.8)."""
        config_done = self.plan["stages"]["C"] == "done"
        self.plan["stages"]["D"] = "done"
        self.plan["prs"]["farm-hive"] = [self.entry("farm-hive", "issue", PRS["farm-hive"])]
        # A step that is not needed is true from the start: without config there is no pin to write.
        self.plan["closing"] = {"waivers_removed": False, "hive_resynced": False, "pin_written": not config_done}
        self.plan["pause"] = {"kind": "closing", "reason": "waiting", "notice": "closing", "since": NOW}
        return self.settled(item_id, self.attempt(item_id, [
            *self.intake(), *self.fresh_base("farm-hive"),
            *self.commit_and_push("farm-hive", "FARM-1 服务端：同步未合并的合约（-unreachable）并实现收获加成"),
            *self.save("d", stage="server", next_action="等合约合并和 Jenkins 发布", published=[PRS["farm-hive"]]),
            *self.notice("waiting", "closing", CLOSING if config_done else CLOSING_WITHOUT_CONFIG),
            *self.pause("waiting", "等合约 PR 合并和 Jenkins 发布；任一步完成后请在这里回复。")], "stage D"))

    def merge_contract(self, style):
        """The owner merges the contract PR, with a merge commit or squashed, and says so; the new main."""
        origin = self.origins / "Farm-Contract.git"
        if style == "merge":
            git("merge", "--no-ff", "-q", "-m", "Merge pull request #12 from example-org/farmbot/farm-1", BRANCH,
                cwd=origin)
        else:
            git("merge", "--squash", "-q", BRANCH, cwd=origin)
            git("commit", "--allow-empty", "-q", "-m", "FARM-1 合约：收获加成 (#12)", cwd=origin)
        self.reply("合约 PR #12 已合并。")
        return self.origin_head("Farm-Contract", "main")

    def run_closing_start(self, item_id, style):
        """The resumed farm-hive attempt records the relayed merge and hands off to Farm-Contract, the first closing
        root (§6.8; Task 14, "Each resume")."""
        message = self.context(item_id)["session_messages"][-1]
        self.plan.pop("pause")
        self.plan["prs"]["Farm-Contract"][0]["pr"].update(state="merged", merge=style)
        self.plan["events"].append({"kind": "merged", "person": message["author"], "message_id": message["id"],
                                    "at": message["created_at"]})
        return self.settled(item_id, self.attempt(item_id, [
            *self.intake(), *self.save("g1", stage="closing", next_action="在 Farm-Contract 移除过期的 BREAKING_WAIVERS"),
            *self.handoff("Farm-Contract")], "the closing steps' first attempt"))

    def run_waivers(self, item_id):
        """Waiver removal on a suffix branch from main, its PR registered while that branch is checked out; then back
        to the issue branch, which the farm-hive attempt reads, and the handoff (§6.8; Task 14, "Waivers")."""
        self.plan["prs"]["Farm-Contract"].append(self.entry("Farm-Contract", "waivers", WAIVERS_PR,
                                                            branch=WAIVERS_BRANCH, head=WAIVERS_BRANCH))
        steps = [*self.intake(), ["git", "Farm-Contract", "fetch", "-q", "origin"],
                 ["git", "Farm-Contract", "switch", "-q", "-c", WAIVERS_BRANCH, "origin/main"],
                 *self.commit_and_push("Farm-Contract", "移除 FARM-1 合并后过期的 BREAKING_WAIVERS", branch=WAIVERS_BRANCH),
                 *self.save("g2", stage="closing", next_action="请 owner 合并移除豁免的 PR", published=[WAIVERS_PR]),
                 *self.notice("merge_request", "merge-waivers", MERGE_WAIVERS),
                 ["git", "Farm-Contract", "switch", "-q", "-"]]
        self.plan["closing"]["waivers_removed"] = True
        steps += [*self.save("g2-done", stage="closing", next_action="在 farm-hive 重新同步合约"),
                  *self.handoff("farm-hive")]
        return self.settled(item_id, self.attempt(item_id, steps, "waiver removal"))

    def run_resync(self, item_id):
        """The re-sync after the merge, pushed to the hive PR; without the pin lines yet, a re-ask (`closing-2`) and a
        second closing pause (Task 14, "Each resume")."""
        self.plan["closing"]["hive_resynced"] = True
        self.plan["pause"] = {"kind": "closing", "reason": "waiting", "notice": "closing-2", "since": NOW}
        return self.settled(item_id, self.attempt(item_id, [
            *self.intake(), *self.commit_and_push("farm-hive", "FARM-1 合约合并后重新同步协议"),
            *self.save("g3", stage="closing", next_action="收到 pin 后写入 toolchain.env"),
            *self.notice("waiting", "closing-2", STILL_WAITING),
            *self.pause("waiting", "请把 Jenkins 打印的三行 pin 贴到 issue，然后在这里回复。")], "the re-sync"))

    def post_pin_lines(self):
        self.human_comment(PIN_LINES, author=OWNER)
        self.reply("pin 已贴在评论里。")

    def run_delivery(self, item_id):
        """The published pin, then the delivery naming every PR, the merges still to do and the client work."""
        message = self.context(item_id)["session_messages"][-1]
        self.plan.pop("pause")
        self.plan["stages"]["G"] = "done"
        self.plan["closing"]["pin_written"] = True
        self.plan["config"]["pin"] = "published"
        self.plan["events"].append({"kind": "pin_posted", "person": message["author"], "message_id": message["id"],
                                    "at": message["created_at"]})
        delivery, outcome = self.work / "delivery.md", self.work / "outcome.json"
        delivery.write_text(DELIVERY, encoding="utf-8")
        evidence = {"summary": "合约、配置声明和服务端草稿 PR 已交付；客户端部分留给下一阶段。",
                    "comment_action_id": "{action_id}", "prs": ALL_PRS,
                    "verification": "合约门禁、配置生成和服务端本地门禁已运行；CI 的 pin 门禁待合并后转绿。"}
        return self.settled(item_id, self.attempt(item_id, [
            *self.intake(), *self.commit_and_push("farm-hive", "FARM-1 写入已发布的设计源 pin"),
            *self.save("g4", stage="closing", next_action="交付"),
            ["prepare-comment", "--item", "{item}", "--token", "{token}", "--kind", "delivery",
             "--body-file", str(delivery)],
            ["post-comment", "--item", "{item}", "--token", "{token}", "--action-id", "{action_id}"],
            ["file", str(outcome), json.dumps(evidence, ensure_ascii=False)],
            ["finish", "--item", "{item}", "--token", "{token}", "--outcome", "delivered", "--input", str(outcome)]],
            "the delivery"))

    # What the controller left.

    def context(self, item_id):
        return self.c.ledger.issue_context(item_id)

    def tree(self, item_id, repo):
        return self.c.paths.worktrees / item_id / repo

    def reads(self, item_id, repo="Farm-Contract"):
        """Where Task 6 checks out a `reads` repository's default branch: beside, not inside, the item's worktrees."""
        return self.c.paths.worktrees / f"{item_id}.reads" / f"{repo}@main"

    @staticmethod
    def branch(path):
        return git("branch", "--show-current", cwd=path)

    @staticmethod
    def upstream(path):
        return git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}", cwd=path)

    @staticmethod
    def head(path, ref="HEAD"):
        return git("rev-parse", "--verify", ref, cwd=path)

    def origin_head(self, repo, ref):
        return self.head(self.origins / f"{repo}.git", ref)

    def origin_branches(self, repo):
        return set(git("for-each-ref", "--format=%(refname:short)", "refs/heads",
                       cwd=self.origins / f"{repo}.git").splitlines())

    def origin_commit(self, repo, message):
        """Someone else's commit on the repository's main, as when another PR merges while the job waits."""
        git("commit", "--allow-empty", "-q", "-m", message, cwd=self.origins / f"{repo}.git")
        return self.origin_head(repo, "main")

    def is_ancestor(self, ancestor, commit, repo="Farm-Contract"):
        return subprocess.run(["git", "merge-base", "--is-ancestor", ancestor, commit],
                              cwd=self.origins / f"{repo}.git", capture_output=True).returncode == 0

    def recovery(self, repo, item_id):
        return self.head(self.c.paths.repos / f"{repo}.git", f"refs/farmbot/recovery/{item_id}")

    def cleaned(self, item_id):
        return bool((self.c.ledger.cleanup_record(item_id) or {}).get("done"))

    def notices(self, item_id):
        return [(notice["request_id"], notice["kind"], notice["remote_id"] is not None)
                for notice in self.c.ledger.notices(item_id)]

    def handoffs(self, item_id):
        rows = self.c.ledger.connection.execute(
            "SELECT details FROM audit WHERE item_id=? AND kind='repository_handoff_requested' ORDER BY id", (item_id,))
        return [(details["from"], details["to"]) for details in (json.loads(row["details"]) for row in rows)]

    def started_comments(self):
        return [call for call in self.calls("create_comment") if "已开始处理" in call["body"]]

    @staticmethod
    def writable(run):
        """The roots the launcher let this attempt write, from the isolated home it wrote for the worker."""
        settings = tomllib.loads((run / "home" / "config.toml").read_text(encoding="utf-8"))
        return [Path(path).resolve() for path in settings["sandbox_workspace_write"]["writable_roots"]]

    def assert_stage(self, item_id, run, payload, root):
        """One root per attempt (§6.1): only it writable, only it in the publication scope, the profile named."""
        self.assertEqual(payload["stage"], {"root_repository": root, "write_repositories": [root],
                                            "read_only_worktrees": [repo for repo in FEATURE if repo != root]})
        self.assertEqual({repo: Path(path).resolve() for repo, path in payload["worktrees"].items()},
                         {repo: self.tree(item_id, repo).resolve() for repo in FEATURE})
        self.assertEqual(list(payload["publication"]["repositories"]), [root])
        writable = self.writable(run)
        for repo in FEATURE:
            self.assertEqual(self.tree(item_id, repo).resolve() in writable, repo == root, repo)
        self.assertEqual(payload["tools"].get("lark_cli"), {"profile": PROFILE})

    def assert_reads(self, item_id, run, payload, contract_main):
        """Each `reads` repository at its default branch, detached, refreshed at this launch, beside the item's
        worktrees and in no writable root (Task 6; P2): Farm-Contract at `contract_main`, Farm-Client and farmgui at
        their origin's main now."""
        self.assertEqual({repo: Path(path).resolve() for repo, path in payload["reads"].items()},
                         {repo: self.reads(item_id, repo).resolve() for repo in READS})
        writable = self.writable(run)
        for repo in READS:
            checkout = self.reads(item_id, repo)
            main = contract_main if repo == "Farm-Contract" else self.origin_head(repo, "main")
            self.assertEqual((self.head(checkout), self.branch(checkout)), (main, ""), repo)
            self.assertFalse(any(checkout.resolve().is_relative_to(root) for root in writable), repo)

    def assert_reattached(self, item_id, repo, head=None):
        """On the issue branch the plan records, tracking it (Task 5): at the published head, or at `head` when the
        clone's branch has a commit of its own that it keeps."""
        tree = self.tree(item_id, repo)
        self.assertEqual((self.branch(tree), self.upstream(tree), self.head(tree)),
                         (BRANCH, f"origin/{BRANCH}", head or self.origin_head(repo, BRANCH)), repo)

    def assert_no_secrets(self):
        """No launch message, worker record or log, and nothing sent to Linear, carries a credential or a claim."""
        for path in [*self.c.paths.runs.rglob("*"), *self.stub.rglob("*")]:
            if path.is_file():
                content = path.read_bytes()
                for secret in (SECRET, SIGNING, "claim_"):
                    self.assertNotIn(secret.encode("utf-8"), content, str(path))

    # The journey.

    def test_one_code_job_goes_from_the_contract_through_the_server_to_its_closing_steps(self):
        item = self.delegate()
        self.assertEqual(self.c.ledger.item(item)["skill"], "feature")
        ack = self.calls("create_activity")[0]
        self.assertEqual((ack["session_id"], ack["content"]),
                         (SESSION, {"type": "thought", "body": ACK["feature"].format(bot="FarmBot")}))
        self.assertIsNone(self.c.ledger.session(SESSION)["target"])        # no Farm-Client target for feature (P6)

        # Stage A, first attempt, at the initial root: started comment, grouped questions, a question pause.
        run, payload = self.run_intake_and_questions(item)
        self.assert_stage(item, run, payload, "Farm-Contract")
        self.assert_reads(item, run, payload, self.origin_head("Farm-Contract", "main"))
        self.assertEqual((payload["skill"], payload["target"], payload["user_requests"]),
                         (str(ROOT / "skills" / "feature" / "SKILL.md"), None, []))
        self.assertEqual({repo: self.branch(self.tree(item, repo)) for repo in FEATURE}, dict.fromkeys(FEATURE, BRANCH))
        self.assertFalse({"Farm-Client", "farmgui"} & set(payload["worktrees"]))  # read, never written (P2)
        current = self.c.ledger.item(item)
        self.assertEqual((current["state"], current["root_repo"], current["stage"]),
                         ("awaiting_input", None, "contract"))
        self.assertEqual((self.context(item)["pending_reason"], self.plan["pause"]["kind"]), ("question", "answers"))
        self.assertEqual(len(self.calls("needs_more_info")), 1)
        self.assertEqual(self.notices(item), [("questions-1", "question", True)])

        # The designer answers in a comment and the owner resumes the job with a reply (§5.1).
        self.human_comment("加成不叠加，同类加成取最大值。", author=DESIGNER)
        self.reply("已在评论里回答了，继续。")
        self.assertEqual(self.c.ledger.item(item)["state"], "queued")
        # A reply in a feature session pins no target either, and its acknowledgement has no target line (P6).
        self.assertEqual(self.calls("create_activity")[-1]["content"], {"type": "thought", "body": "收到回复，继续处理。"})
        self.assertIsNone(self.c.ledger.session(SESSION)["target"])

        # Stage A's change: its draft PR, then the merge request that ends the stage (no `stage` notice for A, P15),
        # and a handoff without waiting for the merge.
        run, payload = self.run_contract(item)
        self.assert_stage(item, run, payload, "Farm-Contract")
        self.assertEqual([(m["body"], m["author"]) for m in payload["user_requests"]],
                         [("已在评论里回答了，继续。", OWNER)])
        contract = self.tree(item, "Farm-Contract")
        self.assertEqual(self.origin_head("Farm-Contract", BRANCH), self.head(contract))
        self.assertEqual(self.plan["prs"]["Farm-Contract"], [{
            "branch": BRANCH, "role": "issue", "head": self.head(contract),
            "pr": {"url": PRS["Farm-Contract"], "state": "draft", "merge": None}}])
        self.assertEqual(self.plan["stages"]["A"], "done")
        self.assertEqual(self.handoffs(item), [("Farm-Contract", "common")])
        self.assertEqual(self.context(item)["published_prs"], [PRS["Farm-Contract"]])
        self.assertEqual(self.notices(item)[-1], ("merge-contract", "merge_request", True))
        # Linear's GitHub integration attaches the PR; FarmBot's own output is not issue input.
        fingerprint = self.context(item)["fingerprint"]
        self.refresh_issue(attachments=[PRS["Farm-Contract"]])

        # Stage B at common. The contract worktree stays on the issue branch at the contract commit.
        run, payload = self.run_declarations(item)
        self.assert_stage(item, run, payload, "common")
        self.assertEqual(payload["prior_context"]["content"]["next_actions"], ["在 common 继续"])
        self.assertEqual(self.context(item)["fingerprint"], fingerprint)
        self.assertEqual((self.branch(contract), self.head(contract)),
                         (BRANCH, self.origin_head("Farm-Contract", BRANCH)))
        declarations = self.tree(item, "common")
        self.assertEqual((self.branch(declarations), self.head(declarations)),
                         (BRANCH, self.origin_head("common", BRANCH)))
        current = self.c.ledger.item(item)
        self.assertEqual((current["state"], current["root_repo"], current["stage"]),
                         ("awaiting_input", "common", "declarations"))
        self.assertEqual(self.context(item)["pending_reason"], "waiting")
        self.assertEqual(len(self.calls("needs_more_info")), 1)           # a waiting pause adds no label
        self.assertEqual(self.context(item)["published_prs"], sorted([PRS["Farm-Contract"], PRS["common"]]))

        # Config ready: the designer names a branch, and stage C runs in the resumed common-rooted attempt.
        data = self.name_config_ref()
        run, payload = self.run_config_check(item)
        self.assert_stage(item, run, payload, "common")
        self.assertEqual(payload["user_requests"][-1]["author"], DESIGNER)
        self.assertEqual(self.origin_head("common", CONFIG_BRANCH), data)  # the Jenkins branch adds no commits
        self.assertEqual((self.branch(declarations), self.head(declarations)),
                         (BRANCH, self.origin_head("common", BRANCH)))
        self.assertEqual({key: self.plan["config"][key] for key in ("ref", "sha", "jenkins_branch")},
                         {"ref": CONFIG_REF, "sha": data, "jenkins_branch": CONFIG_BRANCH})
        self.assertEqual(self.plan["prs"]["common"][-1], {"branch": CONFIG_BRANCH, "role": "config", "head": data,
                                                          "pr": None})
        self.assertEqual(self.handoffs(item)[-1], ("common", "farm-hive"))

        # Stage D. farm-hive main moved while the job waited: the branch starts from main as fetched at this launch,
        # and the sync reads ../Farm-Contract, the item's contract worktree on the issue branch (§6.5). Farm-Client
        # moved too: its read-only checkout follows (Task 6).
        moved = self.origin_commit("farm-hive", "其他人的服务端改动")
        self.origin_commit("Farm-Client", "其他人的客户端改动")
        run, payload = self.run_server(item)
        self.assert_stage(item, run, payload, "farm-hive")
        self.assert_reads(item, run, payload, self.origin_head("Farm-Contract", "main"))
        hive = self.tree(item, "farm-hive")
        self.assertEqual((self.branch(hive), self.head(hive, "HEAD^"), self.head(hive)),
                         (BRANCH, moved, self.origin_head("farm-hive", BRANCH)))
        self.assertEqual(((hive.parent / "Farm-Contract").resolve(), self.branch(contract)),
                         (contract.resolve(), BRANCH))
        self.assertEqual(self.context(item)["published_prs"], sorted(PRS.values()))
        self.assertEqual((self.c.ledger.item(item)["state"], self.plan["pause"]["kind"]), ("awaiting_input", "closing"))

        # Closing. The owner merges the contract PR with a merge commit and says so; the read-only main checkout is
        # refreshed at the next launch, and the change's head is on main, so the sibling worktree re-syncs as it is.
        merged = self.merge_contract("merge")
        run, payload = self.run_closing_start(item, "merge")
        self.assert_stage(item, run, payload, "farm-hive")
        self.assert_reads(item, run, payload, merged)
        change = self.origin_head("Farm-Contract", BRANCH)
        self.assertTrue(self.is_ancestor(change, merged))
        self.assertEqual(self.head(contract), change)
        self.assertEqual(self.handoffs(item)[-1], ("farm-hive", "Farm-Contract"))

        run, payload = self.run_waivers(item)
        self.assert_stage(item, run, payload, "Farm-Contract")
        self.assertEqual(self.branch(contract), BRANCH)
        self.assertEqual(self.origin_head("Farm-Contract", f"{WAIVERS_BRANCH}^"), merged)
        self.assertEqual(self.handoffs(item)[-1], ("Farm-Contract", "farm-hive"))

        run, payload = self.run_resync(item)
        self.assert_stage(item, run, payload, "farm-hive")
        self.assertEqual(self.plan["closing"], {"waivers_removed": True, "hive_resynced": True, "pin_written": False})
        self.assertEqual(self.c.ledger.item(item)["state"], "awaiting_input")

        # The pin lines arrive in a comment; the reply resumes the job, which writes the pin and delivers.
        self.post_pin_lines()
        run, payload = self.run_delivery(item)
        self.assert_stage(item, run, payload, "farm-hive")
        self.assertEqual(self.c.ledger.item(item)["state"], "delivered")
        self.assertEqual(self.plan["stages"], dict.fromkeys("ABCDG", "done"))
        self.assertEqual([event["kind"] for event in self.plan["events"]], ["config_ready", "merged", "pin_posted"])
        self.assertEqual(self.context(item)["published_prs"], ALL_PRS)
        # P15: `stage` only for C's pass (no stage was skipped); merge requests end stage A and the waiver removal.
        self.assertEqual(self.notices(item), [
            ("questions-1", "question", True), ("merge-contract", "merge_request", True),
            ("config-needed", "waiting", True), ("stage-C", "stage", True), ("closing", "waiting", True),
            ("merge-waivers", "merge_request", True), ("closing-2", "waiting", True)])
        self.assertEqual(len(self.started_comments()), 1)
        response = self.calls("create_activity")[-1]["content"]
        self.assertEqual(response["type"], "response")
        for url in ALL_PRS:
            self.assertIn(url, response["body"])

        # Cleanup keeps every head as a recovery ref, removes the worktrees and the read-only checkouts, and leaves
        # every branch on the remotes for the owner.
        self.tick_until(lambda: self.cleaned(item), "cleanup after the delivery")
        self.assertFalse((self.c.paths.worktrees / item).exists())
        self.assertFalse(self.reads(item).parent.exists())
        for repo in FEATURE:
            self.assertEqual(self.recovery(repo, item), self.origin_head(repo, BRANCH), repo)
        self.assertLessEqual({BRANCH, WAIVERS_BRANCH}, self.origin_branches("Farm-Contract"))
        self.assertLessEqual({BRANCH, CONFIG_BRANCH}, self.origin_branches("common"))
        self.assertIn(BRANCH, self.origin_branches("farm-hive"))
        for repo in ("Farm-Client", "farmgui"):                            # read only: nothing pushed there
            self.assertEqual(self.origin_branches(repo), {"main"}, repo)
        self.assert_no_secrets()

    # The variants.

    def test_a_stage_without_work_is_skipped_and_said_so(self):
        item = self.delegate()
        self.run_contract(item, first=True, then="farm-hive")
        run, payload = self.run_server(item)
        self.assert_stage(item, run, payload, "farm-hive")
        self.assertEqual(self.handoffs(item), [("Farm-Contract", "farm-hive")])
        self.assertEqual((self.plan["stages"]["B"], self.plan["stages"]["C"]), (SKIPPED, SKIPPED))
        self.assertEqual(self.plan["closing"]["pin_written"], True)        # no config: no pin to write
        # P15: `stage-B` for the skipped B, saying C is skipped with it (Task 13), and none for A, which its merge
        # request ends.
        self.assertEqual([notice[:2] for notice in self.notices(item)], [
            ("stage-B", "stage"), ("merge-contract", "merge_request"), ("closing", "waiting")])
        # common was never a root: nothing was committed or pushed there, and nobody was asked for config.
        self.assertNotIn(BRANCH, self.origin_branches("common"))
        self.assertEqual(self.head(self.tree(item, "common")), self.origin_head("common", "main"))
        self.assertEqual(self.context(item)["published_prs"], sorted([PRS["Farm-Contract"], PRS["farm-hive"]]))

    def test_a_restart_while_the_job_waits_for_config_resumes_it_where_it_was(self):
        item = self.delegate()
        self.run_contract(item, first=True)
        self.run_declarations(item)
        declarations = self.tree(item, "common")
        head = self.head(declarations)
        self.restart()
        self.assertEqual(self.c.ledger.item(item)["state"], "awaiting_input")
        self.assertEqual((self.context(item)["pending_reason"], self.context(item)["plan"]["pause"]["kind"]),
                         ("waiting", "config_ready"))
        data = self.name_config_ref()
        run, payload = self.run_config_check(item)
        self.assert_stage(item, run, payload, "common")
        self.assertEqual(self.head(declarations), head)                   # the same worktree, not a new one
        self.assertEqual(self.origin_head("common", CONFIG_BRANCH), data)
        self.assertEqual(len(self.started_comments()), 1)
        self.assertEqual([notice[0] for notice in self.notices(item)],
                         ["merge-contract", "config-needed", "stage-C"])

    def test_stop_then_a_continuation_reattaches_the_successor_to_the_recorded_branches(self):
        item = self.delegate()
        self.run_contract(item, first=True)
        # Stage B pushes and records its branch, then is stopped while busy with a commit it has not pushed.
        self.plan["prs"]["common"] = [self.entry("common", "issue", PRS["common"])]
        busy = self.work / "busy"
        run, _ = self.launch(item, [
            *self.intake(), *self.commit_and_push("common", "FARM-1 声明收获加成表"),
            *self.save("b", stage="declarations", next_action="重新生成清单", published=[PRS["common"]]),
            ["git", "common", "commit", "--allow-empty", "-q", "-m", "wip: 重新生成清单"],
            ["file", str(busy), "busy"], ["wait", str(self.work / "never")]], "stage B")
        self.wait_busy(run, busy, "stage B")
        self.stop()
        self.assertEqual(self.c.ledger.item(item)["state"], "cancelled")
        self.tick_until(lambda: self.cleaned(item), "the stopped job's cleanup")
        wip = self.recovery("common", item)                               # the unpushed commit is kept
        self.assertEqual(self.head(self.c.paths.repos / "common.git", f"{wip}^"), self.origin_head("common", BRANCH))
        # Someone pushes to the contract branch meanwhile (§11); the successor builds on it.
        origin = self.origins / "Farm-Contract.git"
        git("checkout", "-q", BRANCH, cwd=origin)
        git("commit", "--allow-empty", "-q", "-m", "补充合约说明", cwd=origin)
        git("checkout", "-q", "main", cwd=origin)
        # The owner asks to continue. The conversation continues the Code job as a linked successor.
        self.reply("继续把这张卡做完。")
        (chat,) = [row["id"] for row in self.c.ledger.items_for_session(SESSION) if row["skill"] == "chat"]
        message = self.context(chat)["session_messages"][-1]["id"]
        summary = self.work / "repair-summary.md"
        summary.write_text("继续被停止的功能工作，从已记录的分支接着做。", encoding="utf-8")
        run, payload = self.attempt(chat, [["claim", "--item", "{item}", "--worker-id", "fake-chat"],
                                           ["request-repair", "--item", "{item}", "--token", "{token}",
                                            "--message-id", str(message), "--summary-file", str(summary)]],
                                    "the conversation")
        self.assertNotIn("lark_cli", payload["tools"])                    # only feature workers name the profile
        self.assertNotIn("reads", payload)                                # and get the read-only checkouts
        # The acknowledgement names the work generically, and no event in the session pinned a target (P6).
        self.assertIn({"type": "thought", "body": QUEUED}, [call["content"] for call in self.calls("create_activity")])
        self.assertIsNone(self.c.ledger.session(SESSION)["target"])
        (successor,) = [row for row in self.c.ledger.items_for_session(SESSION)
                        if row["skill"] == "feature" and row["id"] != item]
        self.assertEqual((successor["predecessor_id"], successor["state"], successor["target"]), (item, "queued", None))
        successor = successor["id"]
        # It starts at the initial root and saves, as its own, the plan it reads from `recovery`.
        self.plan = self.context(successor)["recovery"]["plan"]
        run, payload = self.attempt(successor, [
            *self.intake(), *self.save("s1", stage="contract", next_action="回到 common 继续声明"),
            *self.handoff("common")], "the successor's first attempt")
        self.assert_stage(successor, run, payload, "Farm-Contract")
        self.assert_reads(successor, run, payload, self.origin_head("Farm-Contract", "main"))
        self.assert_reattached(successor, "Farm-Contract")               # moved on to the other person's commit
        self.assert_reattached(successor, "common", head=wip)            # its own unpushed commit kept
        hive = self.tree(successor, "farm-hive")                          # nothing recorded: today's branch
        self.assertEqual((self.branch(hive), self.head(hive)),
                         (f"{BRANCH}-{successor}", self.origin_head("farm-hive", "main")))
        run, payload = self.attempt(successor, [*self.intake(), *self.pause("waiting", "等 策划 填数据。")],
                                    "the successor at common")
        self.assert_stage(successor, run, payload, "common")
        self.assertEqual(self.head(self.tree(successor, "common")), wip)

    def test_config_only_work_closes_without_a_hive_branch_or_published_pin(self):
        """B/C can supply the future client without D: only waiver work remains after the contract merge."""
        item = self.delegate()
        self.run_contract(item, first=True)
        self.run_declarations(item)
        data = self.name_config_ref()
        self.run_config_check(item, server=False)
        self.assertEqual(self.plan["closing"],
                         {"waivers_removed": False, "hive_resynced": True, "pin_written": True})
        self.assertEqual(self.plan["config"]["sha"], data)
        self.assertNotIn("farm-hive", self.plan["prs"])
        self.merge_contract("merge")
        self.plan.pop("pause")
        self.plan["prs"]["Farm-Contract"][0]["pr"].update(state="merged", merge="merge")
        self.settled(item, self.attempt(item, [
            *self.intake(), *self.save("client-closing", stage="closing", next_action="移除合约豁免"),
            *self.handoff("Farm-Contract")], "config-only closing"))
        self.plan["prs"]["Farm-Contract"].append(self.entry("Farm-Contract", "waivers", WAIVERS_PR,
                                                            branch=WAIVERS_BRANCH, head=WAIVERS_BRANCH))
        steps = [*self.intake(), ["git", "Farm-Contract", "fetch", "-q", "origin"],
                 ["git", "Farm-Contract", "switch", "-q", "--no-track", "-c", WAIVERS_BRANCH, "origin/main"],
                 *self.commit_and_push("Farm-Contract", "移除配置合约的豁免", branch=WAIVERS_BRANCH),
                 *self.save("client-waivers", stage="closing", next_action="交付客户端待办", published=[WAIVERS_PR]),
                 *self.notice("merge_request", "merge-waivers", MERGE_WAIVERS)]
        self.plan["closing"]["waivers_removed"] = True
        self.plan["stages"]["G"] = "done"
        prs = sorted([PRS["Farm-Contract"], PRS["common"], WAIVERS_PR])
        body = "合约和配置声明草稿已交付；服务端跳过。配置 SHA 留给客户端配置导出和实现：" + data
        delivery, outcome = self.work / "client-delivery.md", self.work / "client-outcome.json"
        delivery.write_text(body, encoding="utf-8")
        steps += [*self.save("client-delivered", stage="closing", next_action="后续客户端导出与实现"),
                  ["prepare-comment", "--item", "{item}", "--token", "{token}", "--kind", "delivery",
                   "--body-file", str(delivery)],
                  ["post-comment", "--item", "{item}", "--token", "{token}", "--action-id", "{action_id}"],
                  ["file", str(outcome), json.dumps({"summary": body, "comment_action_id": "{action_id}",
                                                     "prs": prs, "verification": "离线控制器旅程；未运行产品生成器"})],
                  ["finish", "--item", "{item}", "--token", "{token}", "--outcome", "delivered",
                   "--input", str(outcome)]]
        run, payload = self.settled(item, self.attempt(item, steps, "config-only delivery"))
        self.assert_stage(item, run, payload, "Farm-Contract")
        self.assertEqual(self.c.ledger.item(item)["state"], "delivered")
        self.assertEqual(self.plan["config"]["pin"], "local")
        self.assertEqual(self.context(item)["published_prs"], prs)
        self.assertEqual(self.origin_branches("farm-hive"), {"main"})
        self.assertNotIn("farm-hive", [to for _, to in self.handoffs(item)])
        self.assert_no_secrets()

    def test_a_successor_selects_the_recorded_followup_before_writing_the_remaining_pin(self):
        """Stop between re-sync and pin: the controller restores the issue branch, then the worker selects followup."""
        item = self.delegate()
        self.run_contract(item, first=True)
        self.run_declarations(item)
        self.name_config_ref()
        self.run_config_check(item)
        self.run_server(item)
        hive_origin = self.origins / "farm-hive.git"
        git("merge", "--no-ff", "-q", "-m", "Hive PR merged before closing", BRANCH, cwd=hive_origin)
        self.merge_contract("merge")
        self.run_closing_start(item, "merge")
        self.run_waivers(item)
        self.plan["prs"]["farm-hive"][0]["pr"].update(state="merged", merge="merge")
        self.plan["prs"]["farm-hive"].append(self.entry("farm-hive", "followup", FOLLOWUP_PR,
                                                        branch=FOLLOWUP_BRANCH))
        self.plan["closing"].update(hive_resynced=True, hive_branch=FOLLOWUP_BRANCH, hive_pr=FOLLOWUP_PR)
        self.plan["pause"] = {"kind": "closing", "reason": "waiting", "notice": "closing-2", "since": NOW}
        self.settled(item, self.attempt(item, [
            *self.intake(), ["git", "farm-hive", "fetch", "-q", "origin"],
            ["git", "farm-hive", "switch", "-q", "--no-track", "-c", FOLLOWUP_BRANCH, "origin/main"],
            *self.commit_and_push("farm-hive", "Followup: contract re-sync", branch=FOLLOWUP_BRANCH),
            *self.save("followup-resynced", stage="closing", next_action="在现有 followup 写入 pin",
                       published=[FOLLOWUP_PR]),
            *self.notice("waiting", "closing-2", "Followup 已重新同步合约；等 Jenkins pin。"),
            *self.pause("waiting", "等 Jenkins pin。")], "followup re-sync"))
        followup_head, issue_head = (self.origin_head("farm-hive", b) for b in (FOLLOWUP_BRANCH, BRANCH))
        self.stop()
        self.tick_until(lambda: self.cleaned(item), "followup cleanup after Stop")
        self.reply("继续，pin 已提供。")
        (chat,) = [row["id"] for row in self.c.ledger.items_for_session(SESSION) if row["skill"] == "chat"]
        message = self.context(chat)["session_messages"][-1]["id"]
        summary = self.work / "followup-summary.md"
        summary.write_text("继续已记录 followup 的剩余 pin 工作。", encoding="utf-8")
        self.attempt(chat, [["claim", "--item", "{item}", "--worker-id", "fake-chat"],
                            ["request-repair", "--item", "{item}", "--token", "{token}",
                             "--message-id", str(message), "--summary-file", str(summary)]], "followup continuation")
        (successor,) = [row["id"] for row in self.c.ledger.items_for_session(SESSION)
                        if row["skill"] == "feature" and row["id"] != item]
        self.plan = self.context(successor)["recovery"]["plan"]
        self.assertEqual(self.plan["closing"]["hive_branch"], FOLLOWUP_BRANCH)
        self.plan.pop("pause")
        self.settled(successor, self.attempt(successor, [
            *self.intake(), *self.save("followup-successor", stage="closing", next_action="选择 followup 再写 pin"),
            *self.handoff("farm-hive")], "followup successor's initial root"))
        self.assert_reattached(successor, "farm-hive", head=issue_head)
        self.plan["closing"]["pin_written"] = True
        self.plan["config"]["pin"] = "published"
        run, payload = self.settled(successor, self.attempt(successor, [
            *self.intake(), ["git", "farm-hive", "fetch", "-q", "origin"],
            ["git", "farm-hive", "switch", "-q", FOLLOWUP_BRANCH],
            *self.commit_and_push("farm-hive", "Followup: published pin", branch=FOLLOWUP_BRANCH),
            *self.save("followup-pin", stage="closing", next_action="在同一 followup PR 交付"),
            *self.pause("waiting", "请 review 同一 followup PR。")], "pin on recovered followup"))
        self.assert_stage(successor, run, payload, "farm-hive")
        self.assertEqual(self.branch(self.tree(successor, "farm-hive")), FOLLOWUP_BRANCH)
        self.assertEqual(self.origin_head("farm-hive", BRANCH), issue_head)
        self.assertEqual(self.head(hive_origin, f"{FOLLOWUP_BRANCH}^"), followup_head)
        self.assertEqual(self.plan["closing"]["hive_pr"], FOLLOWUP_PR)
        self.assertEqual([pr["branch"] for pr in self.plan["prs"]["farm-hive"]], [BRANCH, FOLLOWUP_BRANCH])
        self.assertEqual(self.origin_branches("farm-hive"), {"main", BRANCH, FOLLOWUP_BRANCH})
        self.assert_no_secrets()

    def test_a_budget_kill_fails_the_job_and_retry_restarts_it_at_the_initial_root(self):
        item = self.delegate()
        self.run_contract(item, first=True)
        self.run_declarations(item)
        self.name_config_ref()
        self.run_config_check(item)
        busy = self.work / "busy"
        run, _ = self.launch(item, [*self.intake(), *self.fresh_base("farm-hive"),
                                    ["git", "farm-hive", "commit", "--allow-empty", "-q", "-m", "wip: 服务端实现进行中"],
                                    ["file", str(busy), "busy"], ["wait", str(self.work / "never")]], "stage D")
        self.wait_busy(run, busy, "stage D")
        # Past its 10-hour budget, the launcher kills the attempt; its lease is still valid, so the job fails.
        self.c.launcher.clock = lambda: time.time() + 11 * 3600
        self.tick_until(lambda: self.c.ledger.item(item)["state"] == "failed", "the budget kill")
        self.c.launcher.clock = time.time
        self.assertIn("budget", self.calls("create_activity")[-1]["content"]["body"])
        self.tick_until(lambda: self.cleaned(item), "the failed job's cleanup")
        wip = self.recovery("farm-hive", item)
        # The operator retries; the same job restarts at its initial root with fresh allowances.
        subprocess.run([sys.executable, "-m", "agent", "--db", str(self.c.paths.ledger), "retry", "--item", item,
                        "--reason", "operator retry after the budget kill"], cwd=ROOT, check=True, capture_output=True)
        retried = self.c.ledger.item(item)
        self.assertEqual((retried["state"], retried["root_repo"], retried["capacity_retries"],
                          retried["publication_retries"]), ("queued", None, 0, 0))
        run, payload = self.attempt(item, [
            *self.intake(), *self.save("r1", stage="server", next_action="回到 farm-hive 继续服务端"),
            *self.handoff("farm-hive")], "the retried job's first attempt")
        self.assert_stage(item, run, payload, "Farm-Contract")
        self.assertTrue(payload["prior_context"]["stale"])               # recall from before the retry, to verify
        self.assert_reattached(item, "Farm-Contract")
        self.assert_reattached(item, "common")
        hive = self.tree(item, "farm-hive")                               # nothing recorded: from its recovery ref
        self.assertEqual((self.branch(hive), self.head(hive)), (f"{BRANCH}-{item}", wip))
        run, payload = self.attempt(item, [*self.intake(), *self.pause("waiting", "等合约合并。")],
                                    "farm-hive after the retry")
        self.assert_stage(item, run, payload, "farm-hive")
        self.assertEqual(self.head(hive), wip)

    def test_a_comment_during_an_attempt_is_read_and_revalidated_before_the_handoff(self):
        item = self.delegate()
        commented = self.human_comment("字段名请用 bonus_rate。", author=DESIGNER, publish=False)
        self.run_contract(item, first=True, comment_meanwhile=commented)
        rows = self.c.ledger.connection.execute("SELECT details FROM audit WHERE item_id=? AND kind='revalidate'",
                                                (item,))
        (revalidation,) = [json.loads(row["details"]) for row in rows]
        self.assertNotEqual(revalidation["from"], revalidation["to"])
        self.assertEqual(revalidation["to"], self.context(item)["fingerprint"])
        self.assertEqual((self.handoffs(item), self.c.ledger.item(item)["generation"]),
                         ([("Farm-Contract", "common")], 0))              # handed off, not requeued
        run, payload = self.run_declarations(item)
        self.assert_stage(item, run, payload, "common")
        self.assertIn("字段名请用 bonus_rate。", [c["body"] for c in self.context(item)["issue"]["comments"]])

    def test_removing_the_delegation_cancels_the_parked_job_and_keeps_its_branches(self):
        from agent.lifecycle import UNDELEGATED  # Task 8
        item = self.delegate()
        self.run_contract(item, first=True)
        self.run_declarations(item)
        before = len(self.calls("create_activity"))
        self.refresh_issue(delegate_id=None)                              # someone takes the card over (§4.2)
        seen = time.time()
        with patch.object(self.c.lifecycle, "clock", return_value=seen):
            self.c.lifecycle.refresh(ISSUE)
        self.assertEqual(self.c.ledger.item(item)["state"], "awaiting_input")
        self.assertEqual(len(self.calls("create_activity")), before)
        with patch.object(self.c.lifecycle, "clock", return_value=seen + self.c.lifecycle.interval):
            self.c.lifecycle.refresh(ISSUE)
        self.assertEqual(self.c.ledger.item(item)["state"], "cancelled")
        self.assertEqual([call["content"] for call in self.calls("create_activity")[before:]],
                         [{"type": "response", "body": UNDELEGATED.format(bot="FarmBot")}])
        self.c.lifecycle.refresh(ISSUE)                                   # a later read says nothing more
        self.assertEqual(len(self.calls("create_activity")), before + 1)
        self.tick_until(lambda: self.cleaned(item), "cleanup of the cancelled job")
        for repo in ("Farm-Contract", "common"):
            self.assertIn(BRANCH, self.origin_branches(repo))
            self.assertEqual(self.recovery(repo, item), self.origin_head(repo, BRANCH))
        self.assertFalse((self.c.paths.worktrees / item).exists())
        self.assertFalse(self.reads(item).parent.exists())
        self.assertEqual(self.context(item)["published_prs"], sorted([PRS["Farm-Contract"], PRS["common"]]))

    def test_a_worker_saves_its_checkpoint_then_withdraws_after_confirmed_delegation_removal(self):
        """Two separated status reads flag a held claim; the worker checkpoints and withdraws without
        publishing or asking. Its branches and recovery evidence survive (#73's current authority)."""
        from agent.lifecycle import UNDELEGATED
        item = self.delegate()
        self.begin()
        self.plan["prs"] = {"Farm-Contract": [self.entry("Farm-Contract", "issue", None)]}
        busy, go = self.work / "busy", self.work / "go"
        run, _ = self.launch(item, [
            *self.intake(), *self.started(),
            *self.commit_and_push("Farm-Contract", "FARM-1 合约：收获加成（进行中）"),
            *self.save("a1", stage="contract", next_action="继续写合约"),
            ["file", str(busy), "busy"], ["wait", str(go)],
            ["fetch-issue", "--item", "{item}"], ["expect", "delegated", "false"],
            ["expect", "withdrawn", "true"],
            *self.save("a1-withdrawn", stage="contract", next_action="委派已移除：交给接手的人"),
            ["withdraw", "--item", "{item}", "--token", "{token}"]], "stage A")
        self.wait_busy(run, busy, "stage A")
        before = len(self.calls("create_activity"))
        comments = len(self.calls("create_comment"))
        self.refresh_issue(delegate_id=None)
        seen = time.time()
        with patch.object(self.c.lifecycle, "clock", return_value=seen):
            self.c.lifecycle.refresh(ISSUE)
        self.assertEqual(self.c.ledger.item(item)["state"], "running")
        self.assertIsNone(self.c.ledger.item(item)["withdraw_deadline"])
        with patch.object(self.c.lifecycle, "clock", return_value=seen + self.c.lifecycle.interval):
            self.c.lifecycle.refresh(ISSUE)
        held = self.c.ledger.item(item)
        self.assertEqual(held["state"], "running")
        self.assertIsNotNone(held["withdraw_deadline"])
        self.assertEqual(len(self.calls("create_activity")), before)
        go.touch()
        self.settle(item, run, "stage A")
        self.assertEqual(self.c.ledger.item(item)["state"], "cancelled")
        self.assertEqual([call["content"] for call in self.calls("create_activity")[before:]],
                         [{"type": "response", "body": UNDELEGATED.format(bot="FarmBot")}])
        self.assertEqual(len(self.calls("create_comment")), comments)
        self.assertEqual(self.context(item)["handoff"]["content"]["next_actions"],
                         ["委派已移除：交给接手的人"])
        self.tick_until(lambda: self.cleaned(item), "cleanup of the withdrawn job")
        self.assertIn(BRANCH, self.origin_branches("Farm-Contract"))
        self.assertEqual(self.recovery("Farm-Contract", item), self.origin_head("Farm-Contract", BRANCH))
        self.assertFalse(self.reads(item).parent.exists())

    def test_an_enqueued_code_job_pins_no_target_and_may_not_take_a_unity_slot(self):
        """P11: `enqueue` starts `feature` only on a Bot/Code card and pins it no Farm-Client target (P6); a worker's
        `await-resource` for a resource the manifest does not list is refused before any other check, and the
        refusal leaves its claim intact."""
        self.publish(self.card(id=THIRD, identifier="FARM-3", label="修改"))
        with self.assertRaisesRegex(RuntimeError, "Bot/Code"):
            enqueue(self.config, issue_ref=THIRD, skill="feature")
        self.assertEqual(self.c.ledger.unfinished_for_issue(THIRD), [])
        queued = enqueue(self.config, issue_ref=ISSUE, skill="feature")
        session = f"local-{ISSUE}"
        self.assertEqual((queued["skill"], queued["session_id"], queued["target"]), ("feature", session, None))
        self.assertIsNone(self.c.ledger.session(session)["target"])
        item, refusal = queued["id"], self.work / "refusal.txt"
        self.begin()
        run, payload = self.attempt(item, [
            *self.intake(), *self.save("e1", stage="contract", next_action="读策划案"),
            ["refused", str(refusal), "await-resource", "--item", "{item}", "--token", "{token}",
             "--resource", "unity_slot", "--mode", "batch"],
            *self.pause("waiting", "等 owner 确认范围。")], "the enqueued job")
        self.assert_stage(item, run, payload, "Farm-Contract")
        self.assert_reads(item, run, payload, self.origin_head("Farm-Contract", "main"))
        self.assertIsNone(payload["target"])
        text = refusal.read_text(encoding="utf-8")
        self.assertIn("unity_slot", text)                                 # the manifest refuses it (P11) ...
        self.assertNotIn("pinned commit", text)                           # ... before the missing target could
        self.assertEqual(self.c.ledger.item(item)["state"], "awaiting_input")  # the claim outlived the refusal
        self.assertEqual([row for row in self.c.ledger.reservations() if row["item_id"] == item], [])

    def test_a_squashed_contract_merge_is_re_synced_from_the_read_only_main_checkout(self):
        item = self.delegate()
        self.run_contract(item, first=True)
        self.run_declarations(item)
        self.name_config_ref()
        self.run_config_check(item)
        self.run_server(item)
        change = self.origin_head("Farm-Contract", BRANCH)
        squashed = self.merge_contract("squash")
        run, payload = self.run_closing_start(item, "squash")
        self.assert_reads(item, run, payload, squashed)
        self.assertFalse(self.is_ancestor(change, squashed))             # the change's head is not on main,
        self.assertEqual(self.head(self.tree(item, "Farm-Contract")), change)  # so the sibling cannot re-sync
        self.run_waivers(item)
        self.assertEqual(self.origin_head("Farm-Contract", f"{WAIVERS_BRANCH}^"), squashed)
        run, payload = self.run_resync(item)
        self.assert_reads(item, run, payload, squashed)                   # what this attempt re-syncs from

    def test_two_code_jobs_take_turns_while_a_fix_runs_beside_them(self):
        self.publish(self.card(id=OTHER, identifier="FARM-2"))
        self.publish(self.card(id=THIRD, identifier="FARM-3", label="修改"))
        first = self.delegate()
        second = self.delegate("session-code-2", issue_id=OTHER, identifier="FARM-2")
        fix = self.delegate("session-fix-3", issue_id=THIRD, identifier="FARM-3")
        self.assertEqual([self.c.ledger.item(i)["skill"] for i in (first, second, fix)], ["feature", "feature", "fix"])
        release = {first: self.work / "release-first", fix: self.work / "release-fix"}
        blocker, outcome = self.work / "blocker.md", self.work / "fix-outcome.json"
        blocker.write_text("缺少信息：需要确认复现步骤。", encoding="utf-8")
        claim = ["claim", "--item", "{item}", "--worker-id", "fake"]
        os.environ["FAKE_CLI_STEPS"] = json.dumps({
            first: [claim, ["wait", str(release[first])], *self.pause("waiting", "等合约合并。")],
            second: [claim, *self.pause("waiting", "等合约合并。")],
            fix: [claim, ["wait", str(release[fix])],
                  ["prepare-comment", "--item", "{item}", "--token", "{token}", "--kind", "blocker",
                   "--body-file", str(blocker)],
                  ["post-comment", "--item", "{item}", "--token", "{token}", "--action-id", "{action_id}"],
                  ["file", str(outcome), json.dumps({"summary": "缺少信息", "comment_action_id": "{action_id}"})],
                  ["finish", "--item", "{item}", "--token", "{token}", "--outcome", "blocked",
                   "--input", str(outcome)]]})

        def launched(item_id):
            return bool(self.runs(item_id))
        self.c.scheduler.tick()
        self.assertEqual([launched(i) for i in (first, second, fix)], [True, False, True])
        for _ in range(5):                                                # the second waits while the first runs
            self.c.scheduler.tick()
            self.assertFalse(launched(second))
        self.assertEqual([row["id"] for row in self.c.ledger.queue()], [second])
        release[first].touch()
        self.tick_until(lambda: launched(second), "the second Code job to launch once the first parked")
        self.assertEqual((self.c.ledger.item(first)["state"], self.c.ledger.item(fix)["state"]),
                         ("awaiting_input", "running"))
        release[fix].touch()
        self.tick_until(lambda: self.c.ledger.item(fix)["state"] == "blocked", "the fix to finish")
        self.tick_until(lambda: self.c.ledger.item(second)["state"] == "awaiting_input", "the second job to park")


if __name__ == "__main__":
    unittest.main()
