"""Turn queued work items into running workers and reap them back into ledger states (spec §6, §8)."""
import os
import re
import threading
from pathlib import Path

from .dispatch import dispatch_message
from .ledger import LedgerError

TERMINAL = ("delivered", "blocked", "cancelled", "failed")
WAITING = ("awaiting_input", "awaiting_resource")
READ_REPO = "Farm-Client"


class Scheduler:
    def __init__(self, ledger, launcher, skills, worktrees, *, skill_root, db_path, runtime_name, host,
                 max_concurrent=2, guidance_for=lambda item: "", claim_timeout=600):
        self.ledger = ledger
        self.launcher = launcher
        self.skills = skills
        self.worktrees = worktrees
        self.skill_root = skill_root
        self.db_path = db_path
        self.runtime_name = runtime_name
        self.host = host
        self.max_concurrent = max_concurrent
        self.guidance_for = guidance_for
        self.claim_timeout = claim_timeout
        self.active = {}
        self.lock = threading.RLock()

    def _branch(self, issue):
        name = issue.get("branch_name") or f"farmbot/{issue['identifier'].lower()}"
        return re.sub(r"[^A-Za-z0-9._/一-鿿-]+", "-", name).strip("-/") or f"farmbot/{issue['identifier'].lower()}"

    def _worktrees_for(self, skill, item, issue):
        paths = {}
        if skill.writes:
            for repo in skill.writes:
                paths[repo] = self.worktrees.add(repo, item["id"], self._branch(issue))
        else:
            paths[READ_REPO] = self.worktrees.add_detached(READ_REPO, item["id"])
        return paths

    def launch(self, item):
        skill = self.skills[item["skill"]]
        issue = self.ledger.issue(item["issue_id"])
        paths = self._worktrees_for(skill, item, issue)
        repo_root = Path(self.skill_root).parent
        message = dispatch_message(item=item, issue=issue, skill_path=self.skill_root / skill.name / "SKILL.md",
                                   worktrees=paths, db_path=self.db_path, runtime=self.runtime_name,
                                   guidance=self.guidance_for(item), budget=skill.budget,
                                   repo_root=repo_root, state_dir=self.launcher.state_dir(item["id"]))
        primary = paths.get(READ_REPO) or next(iter(paths.values()))
        # `python3 -m agent` must resolve from any worktree, so FarmBot's root leads the worker's PYTHONPATH.
        pythonpath = os.pathsep.join(p for p in (str(repo_root), os.environ.get("PYTHONPATH", "")) if p)
        handle = self.launcher.spawn(item["id"], message, {}, int(skill.budget["max_hours"] * 3600), cwd=primary,
                                     extra_env={"FARMBOT_DB": str(self.db_path), "PYTHONPATH": pythonpath},
                                     writable=[Path(self.db_path).parent, *paths.values()])
        try:
            self.ledger.set_worker(item["id"], handle.pid, self.host, int(skill.budget["lease_seconds"]))
        except LedgerError:
            self.launcher.stop(item["id"])
            raise
        self.active[item["id"]] = handle
        return handle

    def _fail_launch(self, item_id, exc):
        try:
            self.worktrees.remove(item_id)
        except Exception:
            pass
        try:
            self.ledger.fail_queued(item_id, f"launch failed: {type(exc).__name__}: {exc}"[:500])
        except LedgerError:
            pass

    def stop(self, item_id, reason):
        # Killing the worker must not wait for an in-flight tick: a human pressed Stop.
        killed = self.launcher.stop(item_id)
        with self.lock:
            if not killed:
                # The tick may have been mid-launch: its worker was registered after the first kill.
                self.launcher.stop(item_id)
            self.active.pop(item_id, None)
            try:
                self.ledger.cancel(item_id, reason)
            except LedgerError:
                pass

    def _reap(self):
        reaped = 0
        for finished in self.launcher.poll():
            self.active.pop(finished.item_id, None)
            state = self.ledger.item(finished.item_id)["state"]
            if state == "running":
                item = self.ledger.item(finished.item_id)
                if item["lease_expires_at"] is not None and item["lease_expires_at"] <= self.ledger.clock():
                    self.ledger.recover(finished.item_id, f"worker exited with an expired lease ({finished.reason}, code {finished.returncode})")
                    state = "queued"
                else:
                    self.ledger.fail(finished.item_id, f"worker exited without finishing ({finished.reason}, code {finished.returncode})")
                    state = "failed"
            elif state == "queued" and self.ledger.item(finished.item_id)["worker_pid"] is not None:
                # A queued item with no pid was requeued after the worker finished (material change or
                # recovery); it is waiting for a fresh launch, not a worker that died before claiming.
                self.ledger.fail_queued(finished.item_id, f"worker exited before claiming ({finished.reason}, code {finished.returncode})")
                state = "failed"
            if state in TERMINAL:
                self.worktrees.remove(finished.item_id)
            reaped += 1
        return reaped

    def _recover(self):
        recovered = 0
        now = self.ledger.clock()
        for item_id in self.ledger.status()["recovery_required"]:
            item = self.ledger.item(item_id)
            pid = item["worker_pid"]
            if pid and (item_id in self.active or self.launcher.owned_pid(pid, item_id)):
                if item_id in self.active:
                    self.launcher.stop(item_id)
                    self.active.pop(item_id, None)
                else:
                    self.launcher.kill_pid(pid)
                self.ledger.fail(item_id, "lease expired with a live worker; killed")
            else:
                self.ledger.recover(item_id, "lease expired and worker process is gone")
                recovered += 1
        for row in self.ledger.launched():
            stale = now - row["updated_at"] > self.claim_timeout
            tracked = row["id"] in self.active
            owned = tracked or self.launcher.owned_pid(row["worker_pid"], row["id"])
            if owned and not stale:
                continue
            if tracked:
                self.launcher.stop(row["id"])
                self.active.pop(row["id"], None)
            elif owned:
                self.launcher.kill_pid(row["worker_pid"])
            self.ledger.fail_queued(row["id"], "worker did not claim within the timeout" if owned else "worker process gone before claiming")
            self.worktrees.remove(row["id"])
            recovered += 1
        return recovered

    def _sweep_worktrees(self):
        for row in self.ledger.status()["items"]:
            if row["state"] in TERMINAL and row["id"] not in self.active:
                self.worktrees.remove(row["id"])

    def tick(self):
        with self.lock:
            reaped = self._reap()
            recovered = self._recover()
            self._sweep_worktrees()
            launched = 0
            for item in self.ledger.queue():
                if len(self.active) >= self.max_concurrent:
                    break
                if item["skill"] not in self.skills or item["id"] in self.active:
                    continue
                try:
                    self.launch(item)
                    launched += 1
                except Exception as exc:
                    self._fail_launch(item["id"], exc)
            return {"launched": launched, "reaped": reaped, "recovered": recovered}
