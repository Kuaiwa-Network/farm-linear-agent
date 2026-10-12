"""Bind a scene task's delivery to its clean owned Client and actual draft."""
from uuid import uuid4

from .config import Paths
from .ledger import LedgerError, TERMINAL_STATUS_TYPES
from .publication import PublicationVerifier
from .workflow import JobWorktrees, repository_path
from .worktrees import Worktrees


def verify_delivery(ledger, item_id, token, config, api_factory, evidence):
    paths = Paths(config)
    trees = JobWorktrees(Worktrees(paths.repos, paths.worktrees, config.repos), ledger)

    def fence():
        api = api_factory()
        observed = ledger.observe_issue(api.fetch_issue(ledger.item(item_id)["issue_id"]))
        row = ledger._owned(item_id, token)
        ledger._refuse_withdrawn(row)
        ledger.require_no_reservation(item_id)
        issue = ledger.issue(row["issue_id"])
        if (not observed["in_scope"] or issue.get("archived") or issue.get("status_type") in TERMINAL_STATUS_TYPES
                or row["authority"] == "delegation" and (not api.app_user_id or issue.get("delegate_id") != api.app_user_id)):
            raise LedgerError("scene delivery requires its current open delegated issue")
        if (row["skill"] != "scene" or not row["parent_id"] or row["root_repo"] not in (None, "Farm-Client")
                or not row["task_client_base"]):
            raise LedgerError("scene delivery requires its controller-selected Client stage and entry baseline")
        if row["claimed_fingerprint"] != ledger._issue_row(row["issue_id"])["fingerprint"] or row["requeue_requested"]:
            raise LedgerError("reconcile changed issue inputs before scene delivery")
        return row

    row = fence()
    branch = ledger.staged_client_branch(item_id, issue_prefix=config.issue_prefix)
    client = repository_path(trees, "Farm-Client", item_id)
    commit = trees.head(client)
    trees.verification_commit("Farm-Client", item_id, commit, expected_branch=branch)
    urls = evidence.get("prs")
    urls = [] if urls is None else urls
    if not isinstance(urls, list) or any(not isinstance(url, str) for url in urls):
        raise LedgerError("scene PRs must be an array of URLs")
    no_change = evidence.get("no_change")
    if no_change is not None:
        if not isinstance(no_change, str) or not no_change.strip() or urls:
            raise LedgerError("a no_change scene requires a reason and no PR")
        if trees.git_in(client, "diff", "--name-only", "--no-renames", "--no-ext-diff", "--no-textconv",
                        row["task_client_base"], commit):
            raise LedgerError("a no_change scene must have no changes from its original entry baseline")
    else:
        if len(urls) != 1:
            raise LedgerError("scene delivery requires exactly its verified Client draft")
        verifier = PublicationVerifier(trees, issue_prefix=config.issue_prefix)
        verifier.verify_pr("Farm-Client", item_id, ledger.issue(row["issue_id"])["identifier"], urls[0])
    final = fence()
    trees.verification_commit("Farm-Client", item_id, commit, expected_branch=branch)
    if final["generation"] != row["generation"] or final["task_client_base"] != row["task_client_base"]:
        raise LedgerError("scene delivery changed while verifying its draft")
    proof = {"delivery_id": uuid4().hex, "generation": row["generation"],
             "fingerprint": row["claimed_fingerprint"], "commit": commit, "branch": branch,
             "baseline": row["task_client_base"], "prs": urls, "no_change": no_change}
    with ledger._transaction():
        current = ledger._owned(item_id, token)
        ledger._refuse_withdrawn(current)
        ledger.require_no_reservation(item_id)
        if current["claimed_fingerprint"] != ledger._issue_row(current["issue_id"])["fingerprint"]:
            raise LedgerError("scene delivery inputs changed")
        ledger._audit(item_id, "scene_delivery_verified", details=proof)
    return proof
