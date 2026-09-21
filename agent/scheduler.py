"""Turn queued work items into running workers and reap them back into ledger states (spec §6, §8)."""
import json
import os
import re
import sqlite3
import threading
from pathlib import Path

from .dispatch import dispatch_message
from .ledger import LedgerError
from .memory import publish_snapshot

TERMINAL = ("delivered", "blocked", "cancelled", "failed")
WAITING = ("awaiting_input", "awaiting_resource")
READ_REPO = "Farm-Client"


class Scheduler:
    def __init__(self, ledger, launcher, skills, worktrees, *, skill_root, db_path, runtime_name, host,
                 max_concurrent=2, guidance_for=lambda item: "", claim_timeout=600, api=None,
                 slot_entries=None, preflight=None, control_ledger_factory=None, publication=None, codex_workers=None):
        self.publication = publication
        self.preflight = preflight
        self.control_ledger_factory = control_ledger_factory
        self.api = api
        self.ledger = ledger
        self.launcher = launcher
        self.skills = skills
        self.worktrees = worktrees
        self.skill_root = skill_root
        self.db_path = db_path
        self.runtime_name = runtime_name
        self.host = host
        self.max_concurrent = max_concurrent
        self.codex_workers = codex_workers or {}
        self.guidance_for = guidance_for
        self.claim_timeout = claim_timeout
        # {slot_id: entry}, the same entries service.build hands the pool. The only thing read out of them
        # here is the -buildTarget spelling a worker is told about; the Editor itself is the pool's to run.
        self.slot_entries = slot_entries or {}
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

    @staticmethod
    def _batch_result(state_dir):
        """What the pool's own run left behind. Missing is itself a verification gap the worker must report,
        so it is represented rather than dropped — a batch worker with no `batch_result` key at all would
        read as "no slot" instead of "no evidence"."""
        try:
            return json.loads((Path(state_dir) / "unity-batch.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"state": "gap", "exit_code": None, "results_file": None,
                    "result": "the pool recorded no batch run for this reservation"}

    def launch(self, item):
        if self.ledger.item(item["id"])["state"] != "queued":
            return None
        skill = self.skills[item["skill"]]
        issue = self.ledger.issue(item["issue_id"])
        paths = self._worktrees_for(skill, item, issue)
        repo_root = Path(self.skill_root).parent
        # Enforcement is tool injection (spec §7): what a worker can reach is decided here, from the
        # reservation it actually holds, and never from the skill manifest — which would give every fix
        # worker the Unity MCP whether or not it holds a slot — and never from a repository-local
        # .codex/config.toml, which the isolated home makes inert.
        reservation = self.ledger.active_reservation(item["id"])
        servers, resource = {}, None
        if reservation is not None and reservation["kind"] in skill.resources:
            slot = self.ledger.slot(reservation["resource"])
            entry = self.slot_entries.get(slot["slot_id"], {})
            state_dir = self.launcher.state_dir(item["id"])
            resource = {"kind": reservation["kind"], "mode": reservation["mode"], "slot": slot["slot_id"],
                        "folder": slot["folder"], "instance": slot["instance"], "account": slot["account"],
                        "mcp_address": slot["mcp_address"], "commit": reservation["commit_sha"],
                        "token_file": str(Path(state_dir) / "reservation.token"),
                        # No `unity` key. A worker that may never start the Editor has no use for its path,
                        # and handing it one would be an instruction the AUTHORITY block then has to argue
                        # against. Resolving the binary is the pool's job, in run_batch and open_editor.
                        "build_target": entry.get("build_target_argument"),
                        # The outcome of the run the POOL already performed through the launcher, outside
                        # this worker's sandbox — never an argv for the worker to execute. Unity cannot run
                        # inside sandbox_workspace_write at all: it hangs on a denied Mach lookup with no
                        # file-permission denial to fix (Task 0's addendum). None on an interactive slot,
                        # where run_tests over MCP is the verify path (Task 0 Step 5).
                        "batch_result": (self._batch_result(state_dir)
                                         if reservation["mode"] == "batch" else None),
                        "results_dir": str(state_dir)}
            if reservation["mode"] == "interactive":
                # The server exists for this worker only while the reservation is held, and the worker pins
                # its own MCP session with set_active_instance because HTTP selection is per session. The
                # shape is the runtime's, not one guess for both: write_mcp_config's JSON writer emits
                # mcpServers, where an entry without a type is not an HTTP server at all.
                servers["unity"] = ({"url": slot["mcp_address"]}
                                    if self.launcher.runtime.mcp_format == "toml"
                                    else {"type": "http", "url": slot["mcp_address"]})
        try:
            memory = publish_snapshot(Path(self.db_path).resolve().parent / "memory", self.ledger.memory_rows())
        except (OSError, ValueError, sqlite3.Error) as exc:
            memory = {"status": "unavailable", "index": None, "reason": type(exc).__name__}
        session = self.ledger.session(item['session_id']) or {}
        publication = (self.publication.scope(item=item, issue=issue, paths=paths,
                                             delegated=bool(session.get('delegation')))
                       if self.publication is not None else {'repositories': {}})
        # Inbox entries are session requests; ordinary issue comments stay in issue-context.
        requests = self.ledger.issue_context(item['id'])['session_messages']
        message = dispatch_message(item=item, issue=issue, skill_path=self.skill_root / skill.name / "SKILL.md",
                                   worktrees=paths, db_path=self.db_path, runtime=self.runtime_name,
                                   guidance=self.guidance_for(item), budget=skill.budget,
                                   repo_root=repo_root, state_dir=self.launcher.state_dir(item["id"]),
                                   resource=resource, memory=memory, publication=publication, user_requests=requests)
        primary = paths.get(READ_REPO) or next(iter(paths.values()))
        # `python3 -m agent` must resolve from any worktree, so FarmBot's root leads the worker's PYTHONPATH.
        pythonpath = os.pathsep.join(p for p in (str(repo_root), os.environ.get("PYTHONPATH", "")) if p)
        # A worktree's commits land in FarmBot's bare clone, so the clone must be writable too.
        clones = [self.worktrees.clone_path(repo) for repo in paths]
        # `writable` is deliberately unchanged: no slot folder and no Unity host path is ever added to a
        # worker's roots, in either mode. The worker reads the results XML in its own state directory, which
        # Launcher.spawn already makes writable, and writes nothing in the slot.
        if self.ledger.item(item["id"])["state"] != "queued":
            return None
        options = {}
        if self.runtime_name == "codex" and skill.name in self.codex_workers:
            options["model_settings"] = self.codex_workers[skill.name]
        handle = self.launcher.spawn(item["id"], message, servers, int(skill.budget["max_hours"] * 3600), cwd=primary,
                                     extra_env={"FARMBOT_DB": str(self.db_path), "PYTHONPATH": pythonpath},
                                     writable=[Path(self.db_path).parent, *paths.values(), *clones],
                                     cancelled=lambda: self.ledger.item(item["id"])["state"] != "queued", **options)
        try:
            self.ledger.set_worker(item["id"], handle.pid, self.host, int(skill.budget["lease_seconds"]))
        except LedgerError:
            self.launcher.stop(item["id"])
            raise
        self.active[item["id"]] = handle
        return handle

    def _notify(self, item_id, kind, body):
        """Best-effort session activity for outcomes the worker cannot report itself: it is dead or never ran.

        A `local-` session id was minted by `agent.service enqueue`, not by Linear, and names no agent
        session: create_activity against the real API would fail on every one of these notices and leave the
        operator with nothing. The issue comment is the only reporting surface such an item has.
        """
        if self.api is None:
            return
        try:
            item = self.ledger.item(item_id)
            if str(item["session_id"]).startswith("local-"):
                self.api.create_comment(item["issue_id"], body)
            else:
                self.api.create_activity(item["session_id"], {"type": kind, "body": body})
        except Exception:
            pass

    def _fail_launch(self, item_id, exc):
        if self.ledger.item(item_id)["state"] == "cancelled":
            return
        try:
            self.ledger.fail_queued(item_id, f"launch failed: {type(exc).__name__}: {exc}"[:500])
        except LedgerError:
            return
        self._retire(item_id, "failed")
        self._notify(item_id, "error", f"FarmBot 无法启动工作进程（{type(exc).__name__}），工作项已标记失败；可回复「重试」。")

    def stop(self, item_id, reason):
        # Revoke the claim durably before signalling; a late worker may no longer write the ledger.
        control = self.control_ledger_factory() if self.control_ledger_factory else self.ledger
        try:
            try:
                control.cancel(item_id, reason)
            except LedgerError:
                pass
        finally:
            if control is not self.ledger:
                control.close()
        # The batch Editor is not a worker and never went through `spawn`, so `launcher.stop` below cannot
        # see it: its handle lookup and its `descendants` walk both start from a worker pid, and by now that
        # worker has already exited — it asked for the reservation and quit. Killing the group here is what
        # keeps this task's title true. Be honest about its cost: `kill_group` polls for up to 5s after
        # SIGTERM and 1s more after SIGKILL, so on the batch path Stop itself can take about six seconds,
        # and a wedged Editor is exactly Task 0's measured case. What that cannot touch is done-criterion
        # 2's budget, which is five seconds to kill the *worker*: a batch Editor runs inside the pool's
        # hand-over window, before `resume` puts the item back in the queue, so the Editor and a worker for
        # the same item are never alive at once. In every other case — interactive slots, fix workers, no
        # batch run in flight — this is one dict lookup that returns False. Killing it first also releases
        # the pool thread from `process.wait()` sooner, and the sooner that returns the sooner the slot can
        # settle. `SlotPool.run_batch` closes the other half: the window before the Editor is registered.
        self.launcher.stop_unsandboxed(item_id)
        # Killing the worker must not wait for an in-flight tick: a human pressed Stop.
        self.launcher.stop(item_id)

    def _reap(self):
        reaped = 0
        for finished in self.launcher.poll():
            self.active.pop(finished.item_id, None)
            state = self.ledger.item(finished.item_id)["state"]
            if state == "running":
                item = self.ledger.item(finished.item_id)
                if item["lease_expires_at"] is not None and item["lease_expires_at"] <= self.ledger.clock():
                    self.ledger.recover(finished.item_id, f"worker exited with an expired lease ({finished.reason}, code {finished.returncode})")
                    self._notify(finished.item_id, "thought", "工作进程在租约过期后退出，已重新排队，等待新的 worker 接手。")
                    state = "queued"
                else:
                    self.ledger.fail(finished.item_id, f"worker exited without finishing ({finished.reason}, code {finished.returncode})")
                    self._notify(finished.item_id, "error", f"工作进程未完成即退出（{finished.reason}，退出码 {finished.returncode}），工作项已标记失败；可回复「重试」。")
                    state = "failed"
            elif state == "queued" and self.ledger.item(finished.item_id)["worker_pid"] is not None:
                # A queued item with no pid was requeued after the worker finished (material change or
                # recovery); it is waiting for a fresh launch, not a worker that died before claiming.
                self.ledger.fail_queued(finished.item_id, f"worker exited before claiming ({finished.reason}, code {finished.returncode})")
                self._notify(finished.item_id, "error", "工作进程在认领工作项前退出，工作项已标记失败；可回复「重试」。")
                state = "failed"
            if state in TERMINAL:
                self._retire(finished.item_id, state)
            reaped += 1
        return reaped

    def _recover(self):
        recovered = 0
        now = self.ledger.clock()
        for item_id in self.ledger.status()["recovery_required"]:
            item = self.ledger.item(item_id)
            pid = item["worker_pid"]
            if pid and self.launcher.alive(pid) and item_id not in self.active and not self.launcher.owned_pid(pid, item_id):
                continue  # A live PID we cannot identify is not a dead worker.
            if pid and (item_id in self.active or self.launcher.owned_pid(pid, item_id)):
                if item_id in self.active:
                    self.launcher.stop(item_id)
                    self.active.pop(item_id, None)
                self.ledger.fail(item_id, "lease expired with a live worker; killed")
                self._notify(item_id, "error", "工作进程超过租约仍未汇报，已被终止，工作项已标记失败；可回复「重试」。")
            else:
                self.ledger.recover(item_id, "lease expired and worker process is gone")
                self._notify(item_id, "thought", "工作进程已消失且租约过期，已重新排队，等待新的 worker 接手。")
                recovered += 1
        for row in self.ledger.launched():
            stale = now - row["updated_at"] > self.claim_timeout
            tracked = row["id"] in self.active
            owned = tracked or self.launcher.owned_pid(row["worker_pid"], row["id"])
            if (owned and not stale) or (not owned and self.launcher.alive(row["worker_pid"])):
                continue
            if tracked:
                self.launcher.stop(row["id"])
                self.active.pop(row["id"], None)
            self.ledger.fail_queued(row["id"], "worker did not claim within the timeout" if owned else "worker process gone before claiming")
            self._notify(row["id"], "error", "工作进程未在时限内认领工作项，工作项已标记失败；可回复「重试」。")
            self._retire(row["id"], "failed")
            recovered += 1
        return recovered

    def _retire(self, item_id, state):
        # The caller's snapshot may predate cancellation or retry. Every terminal path uses the
        # same process, reservation, preservation and path checks; no force-removal fallback.
        if self.ledger.item(item_id)["state"] not in TERMINAL:
            return
        record = self.ledger.cleanup_record(item_id)
        if record and record["done"]:
            return
        result = record["result"] if record else {}
        try:
            pid = record.get("worker_pid") if record else self.ledger.last_worker_pid(item_id)
            boot_proof = self.ledger.cleanup_boot_proof(item_id)
            certified = (self.launcher.certified_pids(item_id, boot_proof)
                         if hasattr(self.launcher, "certified_pids") else set())
            handle = self.launcher.running().get(item_id)
            if handle and handle.process and handle.process.poll() is None:
                raise RuntimeError("worker has not exited")
            if pid and pid not in certified and self.launcher.alive(pid):
                if not self.launcher.owned_pid(pid, item_id):
                    raise RuntimeError("live worker PID ownership cannot be verified")
                result["processes"] = [pid, *self.launcher.descendants(pid)]
                self.ledger.record_cleanup(item_id, result)
                self.launcher.kill_pid(pid)
            extra = {"boot_proof": boot_proof} if boot_proof else {}
            self.launcher.assert_quiescent(item_id, pid, result.get("processes", []), **extra)
            if self.ledger.active_reservation(item_id) is not None:
                raise RuntimeError("reservation awaits quiescence")
            saved = self.worktrees.preserve(item_id)
            # A prior removal may have succeeded for only some repositories. Keep their manifest.
            for key in ("committed", "refs"):
                result[key] = {**result.get(key, {}), **saved[key]}
            result["errors"] = saved["errors"]
            self.ledger.record_cleanup(item_id, result)
            self.ledger.begin_cleanup_removal(item_id)
            self.worktrees.remove_preserved(item_id, result)
            self.ledger.record_cleanup(item_id, result, done=True)
        except Exception as exc:
            # A concurrent same-ID retry retires cleanup authority. It must not delete active files.
            if self.ledger.item(item_id)["state"] in TERMINAL:
                self.ledger.record_cleanup(item_id, result, error=str(exc)[:500])

    def _sweep_worktrees(self):
        for row in self.ledger.status()["items"]:
            if row["state"] in TERMINAL and row["id"] not in self.active:
                self._retire(row["id"], row["state"])

    def _stop_cancelled(self):
        # The operator CLI has its own ledger connection and cannot access this launcher's handles.
        # Reconcile before reaping or sweeping: cancel clears worker_pid, and a batch run has no active
        # worker at all until the pool resumes it. Only the launcher knows which processes we still own.
        # Leave reservations to the pool's quiescence probe and send no second cancellation notification.
        cancelling = {row["item_id"] for row in self.ledger.reservations(states=("cancel_requested",))}
        for row in self.ledger.status()["items"]:
            # retry can replace 'cancelled' with 'queued' before this tick. The old reservation still
            # carries cancellation, and a live worker whose recorded pid was cleared is an old attempt.
            stale_worker = row["state"] == "queued" and row["worker_pid"] is None and row["id"] in self.active
            if row["state"] == "cancelled" or row["id"] in cancelling or stale_worker:
                self.launcher.stop_unsandboxed(row["id"])
                self.launcher.stop(row["id"])
                self.active.pop(row["id"], None)

    def tick(self):
        with self.lock:
            self._stop_cancelled()
            reaped = self._reap()
            recovered = self._recover()
            self._sweep_worktrees()
            queue = self.ledger.queue()
        launched = 0
        for item in queue:
            with self.lock:
                if len(self.active) >= self.max_concurrent:
                    break
                if item["skill"] not in self.skills or item["id"] in self.active:
                    continue
                if item.get("predecessor_id"):
                    cleanup = self.ledger.cleanup_record(item["predecessor_id"])
                    if not cleanup or not cleanup["done"]:
                        continue
                reservation = self.ledger.active_reservation(item["id"])
                if reservation is not None and reservation["state"] == "cancel_requested":
                    continue
            # Network calls never hold the scheduler lock. Stop uses its own SQLite connection.
            if self.preflight and not self.preflight(item):
                continue
            with self.lock:
                if self.ledger.item(item["id"])["state"] != "queued":
                    continue
                try:
                    if self.launch(item) is not None:
                        launched += 1
                except Exception as exc:
                    self._fail_launch(item["id"], exc)
        return {"launched": launched, "reaped": reaped, "recovered": recovered}
