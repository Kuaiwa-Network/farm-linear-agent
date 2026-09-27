"""Other people's work on an issue: the PRs and branches a worker asks about before it publishes (spec §4.5).

Read-only: it lists and never refuses, because a hard publication gate would need a stored acknowledgement
(spec §4.5); `verify-publication` carries the list as evidence and the skill tells the worker to ask. A source
that cannot be read is an error entry, never an empty answer. Own work is this instance's registered PRs, the
branches and PRs recorded in `plan.prs` by the job and its predecessors, and the head branches of own PRs. Any
other `farmbot/` branch or PR is foreign: another instance (TestBot) names its branches the same way.
"""
import json
import os
import re
import subprocess
from pathlib import Path

from .ledger import LedgerError
from .publication import PublicationError, github_repository
from .worktrees import GIT_ENV

GH = ("gh",)  # the argv prefix; tests point it at tests/fake_gh.py
TIMEOUT = 20
PR_FIELDS = "number,url,title,state,isDraft,headRefName,isCrossRepository,author,body"
PR_URL = re.compile(r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/pull/([1-9][0-9]*)/?", re.IGNORECASE)


class ForeignWorkError(RuntimeError):
    """One source could not be read. The message never repeats a tool's own output."""


def pr_key(url):
    """(owner, repository, number) with GitHub's case-insensitive names folded; None for anything else."""
    match = PR_URL.fullmatch(url) if isinstance(url, str) else None
    return (match[1].casefold(), match[2].casefold(), int(match[3])) if match else None


def key_pattern(identifier):
    """The issue key as a whole token in any case: FARM-1 matches farmbot/farm-1-x, never FARM-12 or XFARM-1."""
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(identifier) + r"(?![0-9])", re.IGNORECASE)


def search_prs(repository, identifier):
    """`gh pr list --search KEY` in one GitHub repository ("owner/name"), every state. GitHub's search is loose,
    so the caller keeps only PRs whose title, body or head branch carries the key."""
    try:
        result = subprocess.run([*GH, "pr", "list", "--repo", repository, "--search", identifier, "--state", "all",
                                 "--limit", "100", "--json", PR_FIELDS],
                                capture_output=True, encoding="utf-8", errors="replace", timeout=TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        raise ForeignWorkError("gh pr list timed out") from exc
    except OSError as exc:
        raise ForeignWorkError("gh is unavailable") from exc
    if result.returncode:
        raise ForeignWorkError("gh pr list failed")  # never its stderr, as publication.github_api
    try:
        listed = json.loads(result.stdout)
    except ValueError as exc:
        raise ForeignWorkError("gh pr list returned no JSON") from exc
    if not isinstance(listed, list):
        raise ForeignWorkError("gh pr list returned no list")
    return listed


def remote_branches(remote, cwd):
    """[(name, sha)] of the remote's branches from `git ls-remote --heads`: nothing is fetched or written."""
    try:
        result = subprocess.run(["git", "ls-remote", "--heads", "--", remote], capture_output=True, encoding="utf-8",
                                errors="replace", timeout=TIMEOUT, env={**os.environ, **GIT_ENV},
                                cwd=str(cwd) if Path(cwd).is_dir() else None)
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise ForeignWorkError("git ls-remote failed") from exc
    if result.returncode:
        raise ForeignWorkError("git ls-remote failed")
    branches = []
    for line in result.stdout.splitlines():
        sha, _, ref = line.partition("\t")
        if ref.startswith("refs/heads/"):
            branches.append((ref[len("refs/heads/"):], sha))
    return branches


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for inner in value.values():
            yield from _strings(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from _strings(inner)


def plan_work(plan):
    """({(repository, branch)}, {PR key: URL}) that a checkpoint plan's `prs` records (spec §5.7).

    `prs` maps a repository to a list of branch entries. Only `farmbot/` branch names and PR URLs are read,
    wherever an entry keeps them, so an absent plan, an absent `prs` and extra entry keys all read as nothing more."""
    branches, prs = set(), {}
    recorded = plan.get("prs") if isinstance(plan, dict) else None
    for repo, entries in (recorded.items() if isinstance(recorded, dict) else ()):
        for value in _strings(entries if isinstance(entries, list) else []):
            if (key := pr_key(value)) is not None:
                prs.setdefault(key, value)
            elif value.startswith("farmbot/"):
                branches.add((repo, value))
    return branches, prs


def _lineage(ledger, item_id):
    """The item and the predecessors it continues, newest first."""
    items, seen, current = [], set(), item_id
    while current and current not in seen:
        seen.add(current)
        try:
            row = ledger.item(current)
        except LedgerError:
            break
        items.append(row)
        current = row.get("predecessor_id")
    return items


def _text(value, limit=None):
    return (value[:limit] if limit else value) if isinstance(value, str) else None


def foreign_work(ledger, item_id, remotes, repos_root, *, repositories=None):
    """The foreign-work report for one item (spec §4.5).

    `remotes` is the host's configured repositories. `repositories` narrows the GitHub search and the branch
    listing (verify-publication passes its own); Linear's attachments are always read, from the ledger's snapshot,
    so a worker runs `fetch-issue` first."""
    context = ledger.issue_context(item_id)
    identifier = context["issue"]["identifier"]
    carries_key = key_pattern(identifier).search
    issue_branch = "farmbot/" + identifier.lower()
    checked = sorted(remotes) if repositories is None else [repo for repo in repositories if repo in remotes]
    configured = {}
    for repo, remote in remotes.items():
        try:
            owner, name = github_repository(remote).casefold().split("/")
        except PublicationError:
            continue
        configured[(owner, name)] = repo

    own_prs = {key: url for url in context["published_prs"] if (key := pr_key(url)) is not None}
    own_branches = set()
    for row in _lineage(ledger, item_id):
        branches, prs = plan_work(row["checkpoint"].get("plan"))
        own_branches |= branches
        for key, url in prs.items():
            own_prs.setdefault(key, url)

    foreign_prs, foreign_branches, errors = {}, [], []

    def foreign_pr(key, url):
        return foreign_prs.setdefault(key, {"url": url, "repository": configured.get(key[:2]), "title": None,
                                            "state": None, "draft": None, "head": None, "author": None,
                                            "sources": set()})

    for url in context["issue"]["attachments"]:
        if (key := pr_key(url)) is not None and key not in own_prs:
            foreign_pr(key, url)["sources"].add("linear_attachment")

    for repo in checked:
        try:
            github = github_repository(remotes[repo])
        except PublicationError:
            errors.append({"repository": repo, "source": "github_search", "error": "not a github.com repository"})
        else:
            try:
                listed = search_prs(github, identifier)
            except ForeignWorkError as exc:
                errors.append({"repository": repo, "source": "github_search", "error": str(exc)})
                listed = []
            for pr in listed:
                key = pr_key(pr.get("url")) if isinstance(pr, dict) else None
                if key is None or key[:2] != tuple(github.casefold().split("/")):
                    continue  # not a PR of the repository asked about
                head = None if pr.get("isCrossRepository") else _text(pr.get("headRefName"))
                if key in own_prs:
                    if head:
                        own_branches.add((repo, head))
                    continue
                if not any(carries_key(_text(pr.get(field)) or "") for field in ("title", "body", "headRefName")):
                    continue  # GitHub's search matched something else
                author = pr.get("author") if isinstance(pr.get("author"), dict) else {}
                entry = foreign_pr(key, pr["url"])
                entry.update(title=_text(pr.get("title"), 200), state=_text(pr.get("state")),
                             draft=pr["isDraft"] if isinstance(pr.get("isDraft"), bool) else None,
                             head=head, author=_text(author.get("login")))
                entry["sources"].add("github_search")
        try:
            branches = remote_branches(remotes[repo], repos_root)
        except ForeignWorkError as exc:
            errors.append({"repository": repo, "source": "remote_branches", "error": str(exc)})
            continue
        # A branch that heads a PR listed here is reported with that PR, not twice.
        heads = {entry["head"] for entry in foreign_prs.values() if entry["repository"] == repo and entry["head"]}
        for name, sha in branches:
            if carries_key(name) and (repo, name) not in own_branches and name not in heads:
                foreign_branches.append({"repository": repo, "name": name, "head": sha,
                                         "farmbot_name": name == issue_branch or name.startswith(issue_branch + "-")})

    prs = [{**entry, "sources": sorted(entry["sources"])} for _, entry in sorted(foreign_prs.items())]
    foreign_branches.sort(key=lambda entry: (entry["repository"], entry["name"]))
    return {"status": "found" if prs or foreign_branches else "incomplete" if errors else "none",
            "issue": identifier, "repositories": checked,
            "foreign": {"prs": prs, "branches": foreign_branches},
            "own": {"prs": sorted(own_prs.values()),
                    "branches": [{"repository": repo, "name": name} for repo, name in sorted(own_branches)]},
            "errors": errors}
