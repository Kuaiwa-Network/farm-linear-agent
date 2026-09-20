"""Fresh-process memory rehearsal with real CLI/SQLite and fake model executables."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from agent.config import StubLinear
from agent.launcher import Launcher, RUNTIMES
from agent.ledger import Ledger
from agent.scheduler import Scheduler
from test_ledger import issue
from test_memory import NOTE
from test_scheduler import FakeWorktrees, SKILLS

ROOT = Path(__file__).resolve().parents[1]
WORKER = r'''
import json, os, pathlib, subprocess, sys
p = json.loads(sys.stdin.read().split("\n\n", 1)[1])
state = pathlib.Path(p["state_dir"])
mode = os.environ["MEMORY_TEST_MODE"]
def cli(*args):
    result = subprocess.run([sys.executable, "-m", "agent", "--db", p["database"], *args],
                            capture_output=True, text=True, timeout=15)
    if result.returncode: raise RuntimeError(result.stderr)
    return json.loads(result.stdout)
token = cli("claim", "--item", p["item_id"], "--worker-id", "scripted-worker")["token"]
token_path = state / "token"
token_path.write_text(token)
token_path.chmod(0o600)
auth = ["--item", p["item_id"], "--token-file", str(token_path)]
index = pathlib.Path(p["memory"]["index"])
listed = cli("memory-list", *auth)
evidence = {"home": os.environ.get("CODEX_HOME") or os.environ["CLAUDE_CONFIG_DIR"],
            "count": len(listed), "index": index.read_text()}
if mode == "save":
    inp = state / "note.json"
    inp.write_text(os.environ["MEMORY_TEST_NOTE"])
    saved = cli("memory-save", *auth, "--input", str(inp))
    evidence.update(id=saved["id"], revision=saved["revision"])
elif mode == "recall":
    note = cli("memory-read", *auth, "--id", listed[0]["id"])
    evidence.update(id=note["id"], body=note["body"], topic=(index.parent / (note["id"] + ".md")).read_text())
    update = {k: note[k] for k in ["title", "category", "scope", "body", "source", "build_commit"]}
    update.update(id=note["id"], expected_revision=note["revision"], body="Reverified in a later run.")
    inp = state / "note.json"
    inp.write_text(json.dumps(update))
    evidence["revision"] = cli("memory-save", *auth, "--input", str(inp))["revision"]
answer = state / "answer.md"
answer.write_text("Memory rehearsal completed.")
cli("activity", *auth, "--type", "response", "--body-file", str(answer))
outcome = state / "outcome.json"
outcome.write_text(json.dumps({"summary":"done","comment_action_id":None,"verification":"scripted test","prs":[]}))
cli("finish", *auth, "--outcome", "delivered", "--input", str(outcome))
pathlib.Path(sys.argv[1]).write_text(json.dumps(evidence))
'''


class MemoryIntegrationTests(unittest.TestCase):
    def test_fresh_workers_share_memory_in_both_runtime_formats(self):
        for name in ("codex", "claude"):
            with self.subTest(runtime=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                db = Ledger(root / "agent" / "ledger.sqlite3")
                worker = root / "worker.py"
                worker.write_text(WORKER)
                stub = root / "stub"
                stub.mkdir()
                runtime = RUNTIMES[name]._replace(command=[sys.executable, str(worker), "{last_message}"], seed_files={})
                launcher = Launcher(root / "runs", runtime, host="test")
                scheduler = Scheduler(db, launcher, SKILLS, FakeWorktrees(root / "worktrees"),
                                      skill_root=ROOT / "skills", db_path=root / "agent" / "ledger.sqlite3",
                                      runtime_name=name, host="test", api=StubLinear(stub))
                evidence = []
                try:
                    for mode in ("save", "recall", "forgotten"):
                        if mode == "forgotten":
                            db.memory_admin("forget", note_id=evidence[0]["id"], expected_revision=2, reason="rehearsal cleanup")
                        issue_id = str(uuid4())
                        db.observe_issue(issue(id=issue_id))
                        db.ensure_session(mode, issue_id, delegation=False)
                        item = db.create_work_item(issue_id=issue_id, session_id=mode, skill="chat")
                        with patch.dict(os.environ, {"MEMORY_TEST_MODE": mode, "MEMORY_TEST_NOTE": json.dumps(NOTE),
                                                     "FARMBOT_LINEAR_STUB_DIR": str(stub),
                                                     "FARMBOT_CONFIG": str(root / "missing.json")}):
                            scheduler.launch(item)
                        deadline = time.monotonic() + 20
                        finished = []
                        while time.monotonic() < deadline and not finished:
                            finished = launcher.poll()
                            if not finished: time.sleep(0.03)
                        self.assertTrue(finished, mode)
                        self.assertEqual(finished[0].returncode, 0,
                                         (launcher.state_dir(item["id"])).as_posix() + ": " +
                                         "\n".join(p.read_text() for p in launcher.state_dir(item["id"]).glob("*/stderr.log")))
                        evidence.append(json.loads(finished[0].last_message))
                        self.assertEqual(db.item(item["id"])["state"], "delivered")
                    self.assertEqual(len({e["home"] for e in evidence}), 3)
                    self.assertEqual(evidence[0]["id"], evidence[1]["id"])
                    self.assertEqual(evidence[1]["body"], NOTE["body"])
                    self.assertIn(NOTE["body"], evidence[1]["topic"])
                    self.assertNotIn(NOTE["body"], evidence[1]["index"])
                    self.assertEqual(evidence[1]["revision"], 2)
                    self.assertEqual(evidence[2]["count"], 0)
                    self.assertNotIn(evidence[0]["id"], evidence[2]["index"])
                    self.assertEqual(db.reservations(), [])
                    calls = [json.loads(line) for line in (stub / "calls.jsonl").read_text().splitlines()]
                    self.assertEqual([c["method"] for c in calls], ["create_activity"] * 3)
                finally:
                    for item_id in launcher.running(): launcher.stop(item_id)
                    launcher.poll()
                    db.close()
