"""UI delivery binds both actual drafts to certified guards and Unity loading."""
import hashlib
import json
from uuid import uuid4

from .fgui_client import installation_record
from .fgui_records import _canonical, _live
from .fgui_scope import verify_candidate
from .ledger import LedgerError
from .publication import PublicationVerifier


def verification_record(workflow, kind, checksum, receipt_id, scope, installed):
    candidates = [json.loads(row["details"]) for row in workflow.ledger.connection.execute(
        "SELECT details FROM audit WHERE item_id=? AND kind=? ORDER BY id DESC", (workflow.item_id, kind))]
    for proof in candidates:
        raw = {k: v for k, v in proof.items() if k != checksum}
        if hashlib.sha256(_canonical(raw).encode("utf-8")).hexdigest() != proof.get(checksum):
            raise LedgerError("UI verification has damaged controller evidence; preserve recovery records")
        if (proof.get("receipt_id") == receipt_id and proof.get("commit") == scope["commit"]
                and proof.get("source_head") == scope["source_head"]
                and proof.get("installation_sha256") == installed["installation_sha256"]):
            return proof
    raise LedgerError("UI delivery requires current controller guard and actual Unity loading records")


def verify_delivery(workflow, urls):
    workflow._stage("Farm-Client")
    workflow.ledger.require_no_reservation(workflow.item_id)
    plan = workflow.ledger.issue_context(workflow.item_id).get("plan") or {}
    receipt_id = (plan.get("ui") or {}).get("receipt_id")
    exported = workflow.retained_export(receipt_id)
    scope = verify_candidate(workflow, receipt_id)
    installed = installation_record(workflow.ledger, workflow.item_id, receipt_id)
    guards = verification_record(workflow, "fgui_guards_complete", "guards_sha256", receipt_id, scope, installed)
    unity = verification_record(workflow, "fgui_unity_complete", "unity_sha256", receipt_id, scope, installed)
    entries = (plan.get("prs") or {}).get("Farm-Client")
    if (not isinstance(entries, list) or len(entries) != 1 or not isinstance(entries[0], dict)
            or set(entries[0]) != {"role", "branch", "head", "pr"} or entries[0]["role"] != "issue"
            or entries[0]["branch"] != scope["branch"] or entries[0]["head"] != scope["commit"]
            or not isinstance(entries[0]["pr"], dict) or entries[0]["pr"].get("state") != "draft"
            or entries[0]["pr"].get("merge") is not None):
        raise LedgerError("UI delivery requires its exact current Client issue draft in the checkpoint")
    entry = entries[0]
    identifier = workflow.ledger.issue_context(workflow.item_id)["issue"]["identifier"]
    verifier = PublicationVerifier(workflow.trees, issue_prefix=workflow.config.issue_prefix)
    current = verifier.verify("Farm-Client", workflow.item_id, identifier, scope["branch"], suffix_roles=True)
    if current["head"] != scope["commit"]:
        raise LedgerError("UI Client publication head differs from the tested committed candidate")
    client_url = verifier.verify_pr("Farm-Client", workflow.item_id, identifier, entry["pr"].get("url"))
    expected = {exported["source_pr"], client_url}
    if not isinstance(urls, list) or len(urls) != 2 or set(urls) != expected:
        raise LedgerError("UI delivery must name exactly its verified farmgui and Client drafts")
    workflow.fence(refresh=True)
    if verify_candidate(workflow, receipt_id) != scope:
        raise LedgerError("UI tested candidate changed during draft verification")
    workflow.ledger.require_no_reservation(workflow.item_id)
    proof = {"format": "farmbot-fgui-delivery-v1", "delivery_id": uuid4().hex, "receipt_id": receipt_id,
             "commit": scope["commit"], "source_head": scope["source_head"], "prs": sorted(expected),
             "guards_sha256": guards["guards_sha256"], "unity_sha256": unity["unity_sha256"],
             "approval": exported["authority"]["approval"], "request": exported["authority"]["request"]}
    with workflow.ledger._transaction():
        row = _live(workflow.ledger, workflow.item_id, workflow.token, ("Farm-Client",))
        workflow.ledger.require_no_reservation(workflow.item_id)
        proof.update(generation=row["generation"], fingerprint=row["claimed_fingerprint"])
        workflow.ledger._audit(workflow.item_id, "fgui_delivery_verified", details=proof)
    return proof


def verify_no_change(ledger, item_id, token, config, api_factory, reason):
    """Certify clean owned checkouts for an internal UI task with no delta."""
    from .config import Paths
    from .ledger import TERMINAL_STATUS_TYPES
    from .publication import is_issue_branch
    from .workflow import JobWorktrees, repository_path, source_entry
    from .worktrees import Worktrees
    paths = Paths(config)
    trees = JobWorktrees(Worktrees(paths.repos, paths.worktrees, config.repos), ledger)

    def fence():
        api = api_factory()
        observed = ledger.observe_issue(api.fetch_issue(ledger.item(item_id)["issue_id"]))
        row = ledger._owned(item_id, token)
        ledger._refuse_withdrawn(row)
        ledger.require_no_reservation(item_id)
        issue = ledger.issue(row["issue_id"])
        if row["skill"] != "fgui" or not row["parent_id"]:
            raise LedgerError("internal UI no-change verification requires its general job")
        if (not observed["in_scope"] or issue.get("archived") or issue.get("status_type") in TERMINAL_STATUS_TYPES
                or row["authority"] == "delegation" and (not api.app_user_id or issue.get("delegate_id") != api.app_user_id)):
            raise LedgerError("UI no-change delivery requires its current open delegated issue")
        if row["claimed_fingerprint"] != ledger._issue_row(row["issue_id"])["fingerprint"] or row["requeue_requested"]:
            raise LedgerError("reconcile changed issue inputs before UI no-change delivery")
        return row, issue

    row, issue = fence()
    entry = source_entry(ledger, item_id)
    if entry is None:
        raise LedgerError("UI no-change delivery requires its controller-recorded source entry")
    recorded = ledger.recorded_branches(item_id, issue_prefix=config.issue_prefix)
    if recorded.get("farmgui", entry["branch"]) != entry["branch"]:
        raise LedgerError("UI no-change delivery must retain its recorded source branch")
    heads, branches = {}, {}
    repositories = ["farmgui", *(["Farm-Client"] if row["task_client_base"] else [])]
    for repo in repositories:
        path = repository_path(trees, repo, item_id)
        head = trees.head(path)
        branch = (ledger.staged_client_branch(item_id, issue_prefix=config.issue_prefix) if repo == "Farm-Client"
                  else entry["branch"])
        if not is_issue_branch(branch, issue["identifier"], config.issue_prefix):
            raise LedgerError("UI no-change delivery requires its owned issue branch")
        trees.verification_commit(repo, item_id, head, expected_branch=branch)
        if repo == "farmgui" and trees.git_in(path, "diff", "--name-only", "--no-renames", "--no-ext-diff",
                                              "--no-textconv", entry["commit"], head):
            raise LedgerError("UI no-change delivery must retain zero source diff from its original task entry")
        if repo == "Farm-Client" and trees.git_in(path, "diff", "--name-only", "--no-renames", "--no-ext-diff",
                "--no-textconv", row["task_client_base"], head):
            raise LedgerError("UI no-change delivery must retain zero Client diff from its original task baseline")
        heads[repo], branches[repo] = head, branch
    fence()
    for repo, head in heads.items():
        trees.verification_commit(repo, item_id, head, expected_branch=branches[repo])
    proof = {"delivery_id": uuid4().hex, "generation": row["generation"],
             "fingerprint": row["claimed_fingerprint"], "heads": heads, "branches": branches,
             "baseline": row["task_client_base"], "source_entry": entry, "no_change": reason}
    with ledger._transaction():
        ledger._owned(item_id, token)
        ledger._audit(item_id, "fgui_no_change_verified", details=proof)
    return proof
