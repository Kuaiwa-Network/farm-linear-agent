"""Durable presentation tasks belonging to one delegated general job.

Execution profiles retain repository-specific grants. They are capabilities,
not issue categories. Workers never share a claim, and Client writes serialize.
"""
import json
from functools import wraps
from uuid import uuid4


CAPABILITIES = {"fgui": "fgui", "scene": "scene"}


def repository_owner(item):
    return item.get("parent_id") or item["id"]


def repository_path(trees, repo, item_id):
    owner = repository_owner(trees.ledger.item(item_id)) if isinstance(trees, JobWorktrees) else item_id
    return trees.worktrees_root.resolve() / owner / repo


class JobWorktrees:
    """Resolve task repository operations to their job's owned checkout.

    Run directories, tokens and audit records remain task-specific. Use this
    adapter only with the trusted ledger; no worker-provided path chooses an owner.
    """
    _second = {"add", "add_stage", "stage_base", "add_detached", "verification_commit"}

    def __init__(self, trees, ledger):
        self.trees, self.ledger = trees, ledger

    def __getattr__(self, name):
        value = getattr(self.trees, name)
        if name not in self._second:
            return value

        @wraps(value)
        def owned(*args, **kwargs):
            args = list(args)
            if len(args) > 1:
                args[1] = repository_owner(self.ledger.item(args[1]))
            elif "item_id" in kwargs:
                kwargs["item_id"] = repository_owner(self.ledger.item(kwargs["item_id"]))
            return value(*args, **kwargs)
        return owned


def task_views(ledger, item_id):
    row = ledger._row(item_id)
    parent = row["parent_id"] or row["id"]
    return [ledger._view(r) for r in ledger.connection.execute(
        "SELECT * FROM work_items WHERE parent_id=? ORDER BY created_at,rowid", (parent,))]


def source_entry(ledger, item_id):
    """The controller's immutable source scope, not a worker checkpoint."""
    from .ledger import COMMIT_SHA, LedgerError
    records = [json.loads(row["details"]) for row in ledger.connection.execute(
        "SELECT details FROM audit WHERE item_id=? AND kind='task_source_entry' ORDER BY id", (item_id,))]
    if not records:
        return None
    if (len(records) != 1 or not isinstance(records[0], dict)
            or not isinstance(records[0].get("commit"), str) or not COMMIT_SHA.fullmatch(records[0]["commit"])
            or not isinstance(records[0].get("branch"), str) or not records[0]["branch"]):
        raise LedgerError("presentation source entry evidence is damaged; preserve recovery records")
    return records[0]


def source_recovery(ledger, item_id, *, issue_prefix="FARM"):
    """Select retained source identity before the controller chooses a checkout."""
    from .ledger import LedgerError, plan_issue_branches
    row = ledger._row(item_id)
    if row["skill"] != "fgui" or not row["parent_id"]:
        raise LedgerError("source recovery requires an internal UI task")
    identifier = ledger.issue(row["issue_id"])["identifier"]

    def branch_for(task, entry):
        recorded = ledger.recorded_branches(task["id"], issue_prefix=issue_prefix).get("farmgui")
        branch = entry["branch"] if entry else recorded
        if branch:
            plan_issue_branches({"prs": {"farmgui": [{"role": "issue", "branch": branch}]}},
                                identifier, issue_prefix, reserved=True)
        if entry and recorded and recorded != branch:
            raise LedgerError("source entry disagrees with its recorded branch; preserve recovery evidence")
        return branch

    def attempted(task, entry):
        return bool(entry or ledger.last_worker_pid(task["id"]) is not None
                    or json.loads(task["checkpoint"]).get("worker_id"))

    prior = source_entry(ledger, item_id)
    if prior:
        return {"branch": branch_for(row, prior), "commit": prior["commit"], "legacy": False,
                "predecessor_tasks": prior.get("predecessor_tasks", []), "entry": prior}
    if attempted(row, None):
        return {"branch": branch_for(row, None), "commit": None, "legacy": True,
                "predecessor_tasks": [item_id], "entry": None}
    parent = ledger._row(row["parent_id"])
    siblings = ledger.connection.execute("SELECT * FROM work_items WHERE parent_id=? AND skill='fgui' AND id<>?",
                                         (parent["id"], item_id)).fetchall()
    if any(task["state"] == "delivered" and (ledger.cleanup_record(task["id"]) or {}).get("done") for task in siblings):
        return {"branch": None, "commit": None, "legacy": False, "predecessor_tasks": [], "entry": None}
    previous, seen = parent["predecessor_id"], set()
    while previous and previous not in seen:
        seen.add(previous)
        predecessor = ledger._row(previous)
        tasks = ledger.connection.execute("SELECT * FROM work_items WHERE parent_id=? AND skill='fgui' "
                                          "ORDER BY created_at,rowid", (previous,)).fetchall()
        if predecessor["skill"] == "fgui":
            tasks = [predecessor, *tasks]
        unfinished = []
        for task in tasks:
            entry = source_entry(ledger, task["id"])
            if task["state"] != "delivered" and attempted(task, entry):
                unfinished.append((task, entry))
        if unfinished:
            if any(not (ledger.cleanup_record(task["id"]) or {}).get("done") for task, _ in unfinished):
                raise LedgerError("source baseline recovery waits for certified predecessor teardown")
            branches = {branch_for(task, entry) for task, entry in unfinished}
            if None in branches or len(branches) != 1:
                raise LedgerError("interrupted source branches are unknown or ambiguous; preserve recovery evidence")
            entries = [entry for _, entry in unfinished]
            legacy = any(entry is None for entry in entries)
            commits = {entry["commit"] for entry in entries if entry}
            if not legacy and len(commits) != 1:
                raise LedgerError("interrupted source baselines are ambiguous; preserve recovery evidence")
            return {"branch": branches.pop(), "commit": None if legacy else commits.pop(), "legacy": legacy,
                    "predecessor_tasks": [task["id"] for task, _ in unfinished], "entry": None}
        if any(task["state"] == "delivered" for task in tasks):
            break
        previous = predecessor["predecessor_id"]
    return {"branch": None, "commit": None, "legacy": False, "predecessor_tasks": [], "entry": None}


def pin_source_entry(ledger, item_id, generation, commit, branch, *, issue_prefix="FARM", legacy_base=None):
    """Capture scope before a source worker can write, retaining interrupted work."""
    from .ledger import ACTIVE_STATES, COMMIT_SHA, LedgerError, plan_issue_branches
    if not isinstance(commit, str) or not COMMIT_SHA.fullmatch(commit):
        raise LedgerError("presentation source entry requires a full commit")
    recovery = source_recovery(ledger, item_id, issue_prefix=issue_prefix)
    baseline = recovery["commit"] or commit
    if recovery["legacy"]:
        # Resolve/fetch before the SQLite write transaction. The recovery
        # metadata is rechecked atomically before the controller records it.
        baseline = legacy_base() if legacy_base is not None else None
        if not isinstance(baseline, str) or not COMMIT_SHA.fullmatch(baseline):
            raise LedgerError("legacy interrupted source requires a freshly verified default baseline")
    with ledger._transaction():
        row = ledger._row(item_id)
        if (row["skill"] != "fgui" or not row["parent_id"] or row["state"] != "queued"
                or row["generation"] != generation or row["token"] is not None or row["worker_pid"] is not None
                or row["next_root_repo"] is not None):
            raise LedgerError("presentation source entry requires its queued source generation")
        parent = ledger._row(row["parent_id"])
        if parent["state"] not in ACTIVE_STATES or parent["skill"] != "work":
            raise LedgerError("presentation source entry requires its active general job")
        ledger._refuse_withdrawn(row)
        ledger._refuse_withdrawn(parent)
        identifier = ledger.issue(row["issue_id"])["identifier"]
        plan_issue_branches({"prs": {"farmgui": [{"role": "issue", "branch": branch}]}},
                            identifier, issue_prefix, reserved=True)
        prior = source_entry(ledger, item_id)
        if prior:
            if prior["branch"] != branch:
                raise LedgerError("presentation source entry branch is immutable")
            return prior
        if source_recovery(ledger, item_id, issue_prefix=issue_prefix) != recovery:
            raise LedgerError("source recovery changed during baseline selection; retry without discarding evidence")
        if recovery["branch"] and recovery["branch"] != branch:
            raise LedgerError("presentation must reattach its retained source branch")
        proof = {"commit": baseline, "branch": branch, "entry_head": commit,
                 "predecessor_tasks": recovery["predecessor_tasks"]}
        ledger._audit(item_id, "task_source_entry", details=proof)
        return proof


def delegate_task(ledger, item_id, token, request):
    from .ledger import LedgerError, REQUEST_ID, _json, _text
    if not isinstance(request, dict) or set(request) != {"key", "capability", "instructions"}:
        raise LedgerError("task requires key, capability and instructions only")
    key, capability, instructions = request["key"], request["capability"], request["instructions"]
    if not isinstance(key, str) or not REQUEST_ID.fullmatch(key):
        raise LedgerError("task key must be 1-64 ASCII letters, digits, '.', '_' or '-'")
    if not isinstance(capability, str) or capability not in CAPABILITIES:
        raise LedgerError("presentation capability must be fgui or scene")
    _text(instructions, "task instructions")
    if len(instructions) > 8000:
        raise LedgerError("task instructions exceed 8000 characters")
    with ledger._transaction():
        parent = ledger._owned(item_id, token)
        ledger._refuse_withdrawn(parent)
        ledger.require_valid_checkpoint(item_id, token)
        if parent["skill"] != "work" or parent["parent_id"] is not None:
            raise LedgerError("only the general job can delegate presentation tasks")
        if parent["claimed_fingerprint"] != ledger._issue_row(parent["issue_id"])["fingerprint"] or parent["requeue_requested"]:
            raise LedgerError("reconcile changed issue inputs before delegating presentation")
        plan = json.loads(parent["checkpoint"]).get("plan") or {}
        if plan.get("stages", {}).get("A") not in ("done", "skipped: existing contract"):
            raise LedgerError("settle the shared OpenSpec behavior before presentation implementation")
        prior = ledger.connection.execute("SELECT * FROM work_items WHERE parent_id=? AND task_key=?",
                                          (item_id, key)).fetchone()
        if prior:
            if json.loads(prior["task_request"]) != request:
                raise LedgerError("task key already records different instructions; use a new key")
            return ledger._view(prior)
        task_id, now = str(uuid4()), ledger.clock()
        predecessor = None
        if parent["predecessor_id"]:
            previous = ledger.connection.execute("SELECT id FROM work_items WHERE parent_id=? AND task_key=? AND skill=?",
                                                 (parent["predecessor_id"], key, CAPABILITIES[capability])).fetchone()
            predecessor = previous["id"] if previous else None
        ledger.connection.execute("""INSERT INTO work_items
            (id,issue_id,session_id,skill,state,priority,created_at,updated_at,authority,parent_id,task_key,task_request,predecessor_id)
            VALUES(?,?,?,?,'queued',?,?,?,'delegation',?,?,?,?)""",
            (task_id, parent["issue_id"], parent["session_id"], CAPABILITIES[capability], parent["priority"],
             now, now, item_id, key, _json(request), predecessor))
        ledger._audit(task_id, "create", "presentation task", {"parent_id": item_id, "key": key})
        ledger._audit(item_id, "task_delegated", details={"item_id": task_id, **request})
        return ledger._view(ledger._row(task_id))


def await_tasks(ledger, item_id, token):
    from .ledger import LedgerError, _json
    with ledger._transaction():
        row = ledger._owned(item_id, token)
        ledger._refuse_withdrawn(row)
        ledger.require_valid_checkpoint(item_id, token)
        ledger.require_no_reservation(item_id)
        if row["skill"] != "work" or row["parent_id"]:
            raise LedgerError("only the general job can wait for presentation tasks")
        tasks = task_views(ledger, item_id)
        if not tasks:
            raise LedgerError("this job has no presentation tasks")
        if _tasks_settled(ledger, tasks):
            raise LedgerError("presentation tasks are already delivered with certified teardown; continue the job")
        checkpoint = json.loads(row["checkpoint"])
        checkpoint["waiting_tasks"] = True
        checkpoint.pop("pending_question", None)
        checkpoint.pop("pending_reason", None)
        pending = ledger.connection.execute("SELECT 1 FROM inbox WHERE item_id=? AND consumed_at IS NULL", (item_id,)).fetchone()
        ledger._set_state(item_id, "queued" if pending else "awaiting_input", "waiting for presentation tasks", token=None,
                          lease_expires_at=None, worker_pid=None, checkpoint=_json(checkpoint))
        return ledger._view(ledger._row(item_id))


def forward_answer(ledger, item_id, token, task_key, message_id):
    """Forward an actual human message, with its author and time, to one task.

    No synthesized answer, cross-issue message or automatic blanket resume.
    """
    from .ledger import ACTIVE_STATES, LedgerError
    if type(message_id) is not int or message_id < 1:
        raise LedgerError("answer requires a positive inbox message id")
    with ledger._transaction():
        parent = ledger._owned(item_id, token)
        ledger._refuse_withdrawn(parent)
        if parent["skill"] != "work" or parent["parent_id"]:
            raise LedgerError("only the general job can route task answers")
        task = ledger.connection.execute("SELECT * FROM work_items WHERE parent_id=? AND task_key=?",
                                         (item_id, task_key)).fetchone()
        message = ledger.connection.execute("SELECT * FROM inbox WHERE item_id=? AND id=?",
                                            (item_id, message_id)).fetchone()
        if not task or task["state"] not in ACTIVE_STATES:
            raise LedgerError("answer requires an active task belonging to this job")
        if not message or not message["author_json"]:
            raise LedgerError("answer requires this job's actual attributed human message")
        if ledger.connection.execute("SELECT 1 FROM audit WHERE item_id=? AND kind='task_answer' "
                                     "AND json_extract(details,'$.message_id')=?", (task["id"], message_id)).fetchone():
            return ledger._view(task)
        ledger.connection.execute("INSERT INTO inbox(item_id,body,author_json,created_at) VALUES(?,?,?,?)",
                                  (task["id"], message["body"], message["author_json"], message["created_at"]))
        if task["state"] == "awaiting_input":
            ledger._set_state(task["id"], "queued", "human answer routed to presentation task", token=None,
                              lease_expires_at=None, worker_pid=None, resume_authorized=1,
                              **ledger._new_stage_allowances(task))
        ledger._audit(task["id"], "task_answer", details={"message_id": message_id, "parent_id": item_id})
        return ledger._view(ledger._row(task["id"]))


def retry_task(ledger, item_id, token, task_key, message_id):
    from .ledger import LedgerError
    if type(message_id) is not int or message_id < 1:
        raise LedgerError("retry requires a positive inbox message id")
    with ledger._transaction():
        parent = ledger._owned(item_id, token)
        ledger._refuse_withdrawn(parent)
        if parent["skill"] != "work" or parent["parent_id"]:
            raise LedgerError("only the general job can retry its tasks")
        task = ledger.connection.execute("SELECT * FROM work_items WHERE parent_id=? AND task_key=?",
                                         (item_id, task_key)).fetchone()
        message = ledger.connection.execute("SELECT * FROM inbox WHERE item_id=? AND id=?",
                                            (item_id, message_id)).fetchone()
        if not task or task["state"] not in ("failed", "blocked", "cancelled") or not message or not message["author_json"]:
            raise LedgerError("task retry requires a terminal task and this job's attributed human request")
        if not (ledger.cleanup_record(task["id"]) or {}).get("done"):
            raise LedgerError("task retry waits for certified teardown")
        ledger._guard_cleanup_retry(task["id"])
        ledger.connection.execute("DELETE FROM resource_job_retries WHERE item_id=?", (task["id"],))
        ledger._set_state(task["id"], "queued", "human requested presentation retry", generation=task["generation"] + 1,
                          worker_pid=None, root_repo=None, next_root_repo=None, token=None, lease_expires_at=None,
                          capacity_retries=0, publication_retries=0, retry_not_before=0,
                          withdraw_deadline=None, withdraw_reason=None)
        ledger.connection.execute("INSERT INTO inbox(item_id,body,author_json,created_at) VALUES(?,?,?,?)",
                                  (task["id"], message["body"], message["author_json"], message["created_at"]))
        ledger._audit(task["id"], "task_retry", details={"message_id": message_id})
        return ledger._view(ledger._row(task["id"]))


def client_ready(ledger, row):
    """Required presentation tasks finish before parent Client implementation.

    Presentation's own asset installation may enter Client during server work;
    it is serialized with every other writer by the scheduler.
    """
    from .ledger import LedgerError
    parent = ledger._row(row["parent_id"]) if row["parent_id"] else row
    if parent["skill"] != "work":
        return
    if row["parent_id"]:
        return  # asset/scene authoring precedes the parent's gameplay integration
    stages = json.loads(parent["checkpoint"]).get("plan", {}).get("stages", {})
    server = stages.get("D", "")
    if not _stage_settled(server):
        raise LedgerError("Client work waits for required server/config work to finish")
    if any(not _stage_settled(stages.get(stage)) for stage in ("B", "C")):
        raise LedgerError("Client work waits for required configuration stages B/C to finish or be explicitly skipped")
    for task in task_views(ledger, row["id"]):
        cleanup = ledger.cleanup_record(task["id"])
        if task["state"] != "delivered" or not cleanup or not cleanup["done"]:
            raise LedgerError("Client work waits for presentation delivery and certified task teardown")


def _stage_settled(value):
    return value == "done" or (isinstance(value, str) and value.startswith("skipped:")
                               and bool(value.split(":", 1)[1].strip()))


def _tasks_settled(ledger, tasks):
    return bool(tasks) and all(task["state"] == "delivered" and (ledger.cleanup_record(task["id"]) or {}).get("done")
                               for task in tasks)


def repository_busy(ledger, item, skills, active=(), launcher=None):
    """Serialize a job's repository across live, retiring and reserved attempts.

    A controller restart loses its in-memory handles, not a writer's authority
    or teardown obligations. Presentation retains reviewed source and its
    Client candidate until delivery, including human waits and task retries.
    """
    from .stages import current_root
    from .ledger import ACTIVE_STATES
    owner = repository_owner(item)
    root = current_root(item.get("root_repo"), skills[item["skill"]])
    for other in [ledger.item(owner), *task_views(ledger, owner)]:
        if other["id"] == item["id"]:
            continue
        cleanup = ledger.cleanup_record(other["id"])
        unfinished = other["state"] != "delivered" or not (cleanup or {}).get("done")
        retained_client = (root == "Farm-Client" and other.get("parent_id")
                           and other.get("task_client_base") and unfinished)
        retained_source = (root == "farmgui" and other.get("parent_id") and other["skill"] == "fgui"
                           and (other["checkpoint"].get("worker_id") or ledger.last_worker_pid(other["id"]) is not None)
                           and unfinished)
        if retained_client or retained_source:
            return True
        if root not in (current_root(other.get("root_repo"), skills[other["skill"]]), other["next_root_repo"]):
            continue
        unsettled = (other["id"] in active or other["state"] == "running" or other["worker_pid"] is not None
                     or other["next_root_repo"] is not None or ledger.active_reservation(other["id"]) is not None
                     or other["state"] not in ACTIVE_STATES and not (cleanup or {}).get("done"))
        if unsettled:
            return True
        if launcher is not None and other["state"] in ACTIVE_STATES:
            # await-input clears worker_pid before its process exits. After a
            # restart, claim retirement alone cannot prove that writer stopped.
            pid = ledger.last_worker_pid(other["id"])
            if pid is not None:
                try:
                    launcher.assert_quiescent(other["id"], pid)
                except Exception:
                    return True
    return False


def wake_parent(ledger, parent_id):
    """Controller wakes a parked coordinator for answers, failures or completed tasks."""
    from .ledger import _json
    with ledger._transaction():
        row = ledger._row(parent_id)
        checkpoint = json.loads(row["checkpoint"])
        if row["state"] != "awaiting_input" or not checkpoint.get("waiting_tasks"):
            return False
        tasks = task_views(ledger, parent_id)
        settled = _tasks_settled(ledger, tasks)
        actionable = [t for t in tasks if t["state"] in ("awaiting_input", "blocked", "failed", "cancelled")]
        # A task question wakes the parent once per question round. Parking again
        # while the same question awaits a person must not create a launch loop.
        signature = []
        for task in tasks if settled else actionable:
            # Identical question text can begin a new round after a partial
            # answer, and a human-authorized retry can fail for the same reason.
            # State-transition audit IDs distinguish those events without
            # waking again for unrelated checkpoints or cleanup diagnostics.
            event = ledger.connection.execute("SELECT MAX(id) FROM audit WHERE item_id=? AND kind=?",
                                              (task["id"], task["state"])).fetchone()[0]
            signature.append([task["id"], task["generation"], task["state"],
                              task["checkpoint"].get("pending_question"), event])
        # Remember each task's last observed actionable round. Resolving one
        # task, or launching a sibling, creates no new question for the parent.
        # Keep the list format so old persisted wake checkpoints remain usable.
        observed = {entry[0]: entry for entry in checkpoint.get("task_wake", [])}
        if not signature or all(observed.get(entry[0]) == entry for entry in signature):
            return False
        observed.update((entry[0], entry) for entry in signature)
        checkpoint["task_wake"] = list(observed.values())
        checkpoint.pop("waiting_tasks", None)
        ledger._set_state(parent_id, "queued", "presentation task progress", token=None, lease_expires_at=None,
                          worker_pid=None, checkpoint=_json(checkpoint))
        return True
