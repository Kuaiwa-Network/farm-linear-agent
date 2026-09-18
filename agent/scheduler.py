"""Turn queued work items into running workers and reap them back into ledger states (spec §6, §8)."""
import re

from .dispatch import dispatch_message
from .ledger import LedgerError

TERMINAL = ("delivered", "blocked", "cancelled", "failed")
WAITING = ("awaiting_input", "awaiting_resource")
READ_REPO = "Farm-Client"


class Scheduler:
    def __init__(self, ledger, launcher, skills, worktrees, *, skill_root, db_path, runtime_name, host,
                 max_concurrent=2, guidance_for=lambda item: ""):
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
        self.active = {}

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
        message = dispatch_message(item=item, issue=issue, skill_path=self.skill_root / skill.name / "SKILL.md",
                                   worktrees=paths, db_path=self.db_path, runtime=self.runtime_name,
                                   guidance=self.guidance_for(item))
        primary = paths.get(READ_REPO) or next(iter(paths.values()))
        handle = self.launcher.spawn(item["id"], message, {}, int(skill.budget["max_hours"] * 3600), cwd=primary,
                                     extra_env={"FARMBOT_DB": str(self.db_path)})
        self.ledger.set_worker(item["id"], handle.pid, self.host)
        self.active[item["id"]] = handle
        return handle

    def stop(self, item_id, reason):
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
            elif state == "queued":
                self.ledger.fail_queued(finished.item_id, f"worker exited before claiming ({finished.reason}, code {finished.returncode})")
                state = "failed"
            if state in TERMINAL:
                self.worktrees.remove(finished.item_id)
            reaped += 1
        return reaped

    def _recover(self):
        recovered = 0
        for item_id in self.ledger.status()["recovery_required"]:
            item = self.ledger.item(item_id)
            pid = item["worker_pid"]
            if pid and self.launcher.alive(pid) and item_id in self.active:
                self.launcher.stop(item_id)
                self.ledger.fail(item_id, "lease expired with a live worker; killed")
            else:
                self.ledger.recover(item_id, "lease expired and worker process is gone")
                recovered += 1
        for row in self.ledger.status()["items"]:
            if row["state"] == "queued" and row["worker_pid"] and row["id"] not in self.active and not self.launcher.alive(row["worker_pid"]):
                self.ledger.fail_queued(row["id"], "worker process gone before claiming")
                self.worktrees.remove(row["id"])
                recovered += 1
        return recovered

    def tick(self):
        reaped = self._reap()
        recovered = self._recover()
        launched = 0
        for item in self.ledger.queue():
            if len(self.active) >= self.max_concurrent:
                break
            if item["skill"] not in self.skills or item["id"] in self.active:
                continue
            self.launch(item)
            launched += 1
        return {"launched": launched, "reaped": reaped, "recovered": recovered}
