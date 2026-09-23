"""FarmBot ledger CLI: the only path from a worker to the ledger and to Linear (spec §8)."""
import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

from .config import Paths, linear_api, load_config
from .ledger import Ledger, LedgerError
from .memory import prune_snapshots
from .router import WRITE_SKILLS
from .stages import FIX_REPOSITORIES, write_repositories


def parser():
    root = argparse.ArgumentParser(prog="python3 -m agent", description=__doc__)
    root.add_argument("--db", required=True, help="ledger SQLite path")
    root.add_argument("--lease-seconds", type=float, default=2700)
    sub = root.add_subparsers(dest="command", required=True)

    def cmd(name, *flags, token=False):
        p = sub.add_parser(name)
        for flag in flags:
            p.add_argument(flag, required=True)
        if token:  # a token on the command line is visible to every process on the host
            p.add_argument("--token")
            p.add_argument("--token-file")
        return p

    cmd("memory-list", "--item", token=True)
    cmd("memory-read", "--item", "--id", token=True)
    cmd("memory-save", "--item", "--input", token=True)
    forget = cmd("memory-forget", "--item", "--id", "--reason", token=True)
    forget.add_argument("--expected-revision", required=True, type=int)
    admin = sub.add_parser("memory-admin", help="trusted host only; never a worker command")
    actions = admin.add_subparsers(dest="memory_action", required=True)
    actions.add_parser("list")
    actions.add_parser("read").add_argument("--id", required=True)
    actions.add_parser("save").add_argument("--input", required=True)
    af = actions.add_parser("forget")
    af.add_argument("--id", required=True)
    af.add_argument("--expected-revision", required=True, type=int)
    af.add_argument("--reason", required=True)
    actions.add_parser("prune-snapshots", help="stop the service before pruning").add_argument("--runs-root", required=True)
    cmd("status"); cmd("queue")
    cmd("fetch-issue", "--item")
    cmd("claim", "--item", "--worker-id")
    cmd("renew", "--item", token=True)
    cmd("checkpoint", "--item", "--input", token=True)
    cmd("handoff-repository", "--item", "--to", token=True)
    cmd("issue-context", "--item")
    cmd("pop-inbox", "--item", token=True)
    cmd("verify-publication", "--item", "--repo", token=True)
    prepare = cmd("prepare-comment", "--item", "--body-file", token=True)
    prepare.add_argument("--kind", required=True, choices=["started", "blocker", "delivery"])
    cmd("post-comment", "--item", "--action-id", token=True)
    cmd("confirm-comment", "--item", "--action-id", "--remote-id", token=True)
    activity = cmd("activity", "--item", "--body-file", token=True)
    activity.add_argument("--type", required=True, choices=["thought", "action", "response", "error", "elicitation"])
    cmd("await-input", "--item", "--question", token=True)
    resume = cmd("resume-work", "--item", token=True)
    resume.add_argument("--message-id", type=int, required=True)
    repair = cmd("request-repair", "--item", "--summary-file", token=True)
    repair.add_argument("--message-id", type=int, required=True)
    resource = cmd("await-resource", "--item", "--resource", token=True)
    resource.add_argument("--mode", required=True, choices=["interactive", "batch"])
    resource.add_argument("--commit", help="full SHA of the clean Farm-Client worktree HEAD to verify; default: baseline")
    release = cmd("release-resource", "--item", token=True)
    release.add_argument("--outcome", required=True, choices=["quiescent", "unclean"])
    cmd("reservations"); cmd("slots")  # operator readers: no item, no token, nothing to authorise
    cmd("recover-slot", "--slot", "--reason")
    finish = cmd("finish", "--item", "--input", token=True)
    finish.add_argument("--outcome", required=True, choices=["blocked", "delivered"])
    for name in ("cancel", "recover", "retry"):
        cmd(name, "--item", "--reason")
    return root


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_memory_json(path):
    with Path(path).open("rb") as handle:
        raw = handle.read(16 * 1024 + 1)
    if len(raw) > 16 * 1024:
        raise LedgerError("memory input exceeds 16 KiB")
    return json.loads(raw.decode("utf-8"))


def read_text(path):
    return Path(path).read_text(encoding="utf-8")


GITHUB_REMOTE = re.compile(r"(?:https://|ssh://git@|git@)github\.com[:/]([^/\s:]+)/([^/\s]+?)(?:\.git)?/?", re.IGNORECASE)


def check_pr_targets(urls, *, allowed_repositories=None):
    """A worker may only register PRs on the repositories this host is configured for."""
    if not isinstance(urls, list) or not urls:
        return
    try:
        repos = load_config(secure_permissions=False).repos
    except (OSError, ValueError):
        if allowed_repositories is not None:
            raise LedgerError("configured repository is required to register a new PR")
        return  # no private config (tests, first run): the ledger's URL shape is the only rule
    if allowed_repositories is not None:
        repos = {name: remote for name, remote in repos.items() if name in allowed_repositories}
    prefixes = {f"https://github.com/{m.group(1)}/{m.group(2)}/pull/".casefold()
                for m in (GITHUB_REMOTE.fullmatch(str(remote)) for remote in (repos or {}).values()) if m}
    if not prefixes:  # a config that names no GitHub remote can never legitimise a PR
        raise LedgerError("no configured GitHub repository in this stage to register PRs on")
    for url in urls:
        if not isinstance(url, str) or not any(url.casefold().startswith(prefix) for prefix in prefixes):
            raise LedgerError(f"PR URL is not under a configured repository: {url}")


def resolve_token(args):
    """--token-file first, then --token, then FARMBOT_TOKEN: an explicit flag beats ambient state."""
    for value in (read_text(args.token_file).strip() if args.token_file else None,
                  (args.token or "").strip(), os.environ.get("FARMBOT_TOKEN", "").strip()):
        if value:
            return value
    raise LedgerError("claim token required: pass --token-file PATH, set FARMBOT_TOKEN, or pass --token")


def verify_late_prs(ledger, args, token, progress):
    """Only consult GitHub when Linear has already observed unregistered output."""
    published = progress.get("published_prs", []) if isinstance(progress, dict) else []
    if not isinstance(published, list) or not all(isinstance(url, str) for url in published):
        return []  # the checkpoint validator reports the payload error
    ledger.renew(args.item, token)
    context = ledger.issue_context(args.item)
    late = set(published) & set(context['issue']['attachments']) - set(context['published_prs'])
    if not late:
        return []
    from .config import ROOT
    from .publication import PublicationVerifier, github_repository
    from .skills import load_skills
    from .worktrees import Worktrees
    config = load_config(secure_permissions=False)
    paths = Paths(config)
    if Path(args.db).resolve() != paths.ledger.resolve():
        raise LedgerError("publication reconciliation must use the configured host ledger")
    item = ledger.item(args.item)
    skill = load_skills(ROOT / 'skills').get(item['skill'])
    session = ledger.session(item['session_id']) or {}
    if item['skill'] not in WRITE_SKILLS or not skill or not session.get('delegation'):
        raise LedgerError("only a delegated write worker may reconcile published PRs")
    verifier = PublicationVerifier(Worktrees(paths.repos, paths.worktrees, config.repos),
                                   issue_prefix=config.issue_prefix)
    verified = []
    for url in sorted(late):
        repo = next((name for name in write_repositories(item, skill) if name in config.repos and
                     url.casefold().startswith(('https://github.com/' + github_repository(config.repos[name]) + '/pull/').casefold())), None)
        if repo is None:
            raise LedgerError("PR URL is not under an allowed publishing repository")
        verified.append(verifier.verify_pr(repo, args.item, context['issue']['identifier'], url))
    ledger.renew(args.item, token)  # network checks cannot outlive cancellation or the claim
    return verified


def session_response(skill, outcome, evidence):
    """The activity that completes the Linear session. A chat answer is already its own response."""
    if skill == "chat" and outcome == "delivered":
        return None
    summary = evidence.get("summary", "")
    prs = [url for url in (evidence.get("prs") or []) if isinstance(url, str)] if isinstance(evidence.get("prs"), list) else []
    if outcome == "delivered":
        if evidence.get("no_change"):
            body = f"✅ 无需改动：{summary}"
        else:
            body = f"✅ 已交付：{summary}" + ("".join(f"\n- {url}" for url in prs))
    else:
        body = f"⏸ 已暂停：{summary}"
    return {"type": "response", "body": body}


def complete_session(api_factory, item, outcome, evidence):
    """Best effort after the ledger is final: Linear keeps a session active until a response or error arrives."""
    content = session_response(item["skill"], outcome, evidence)
    if content is None:
        return
    try:
        api_factory().create_activity(item["session_id"], content)
    except Exception as exc:
        print(json.dumps({"warning": "session response not posted", "error": type(exc).__name__}), file=sys.stderr, flush=True)


def owned_action(ledger, item_id, action_id, token):
    """Only a live claim on the issue a comment speaks for may act on it.

    Scoped by the item's own issue, claimed input and generation instead of by the row's item id. That is
    the same authority — a running worker may speak for its issue at the input it claimed — and it is what
    the outbox key is built from, so a second item on an unchanged issue, which prepare_comment hands the
    row the first item prepared, can still drive post-comment. A row from another issue is still refused,
    and a confirmed row makes post_comment a no-op, so this cannot post anything twice.
    """
    ledger.renew(item_id, token)
    row = ledger.connection.execute(
        """SELECT o.* FROM outbox o JOIN work_items w ON w.id=?
           WHERE o.action_id=? AND o.issue_id=w.issue_id AND o.fingerprint=w.claimed_fingerprint
                 AND o.generation=w.generation""", (item_id, action_id)).fetchone()
    if row is None:
        raise LedgerError("unknown comment action for this work item")
    return dict(row)


def post_comment(ledger, api, action):
    """Reconcile the marker against live comments before ever creating a comment."""
    if action["remote_id"]:
        return action
    issue = api.fetch_issue(action["issue_id"])
    ledger.observe_issue(issue)
    remote_id = next((c["id"] for c in issue["comments"] if action["marker"] in c["body"]), None)
    if remote_id is None:
        remote_id = api.create_comment(action["issue_id"], action["body"])
    return ledger.confirm_comment(action["action_id"], remote_id)


def run(args, ledger, api_factory):
    c = args.command
    if c == "memory-list":
        return ledger.memory_list(args.item, resolve_token(args))
    if c == "memory-read":
        return ledger.memory_read(args.item, resolve_token(args), args.id)
    if c == "memory-save":
        return ledger.memory_save(args.item, resolve_token(args), read_memory_json(args.input))
    if c == "memory-forget":
        return ledger.memory_forget(args.item, resolve_token(args), args.id, args.expected_revision, args.reason)
    if c == "memory-admin":
        action = args.memory_action
        if action == "prune-snapshots":
            if ledger.connection.execute("SELECT 1 FROM work_items WHERE state IN ('queued','running') LIMIT 1").fetchone():
                raise LedgerError("stop the service and settle queued/running work before pruning snapshots")
            runs = Path(args.runs_root)
            if not runs.is_absolute() or not runs.is_dir():
                raise LedgerError("stop the service; --runs-root must name its existing absolute runs directory")
            return prune_snapshots(Path(args.db).resolve().parent / "memory", runs)
        return ledger.memory_admin(action, value=read_memory_json(args.input) if action == "save" else None,
                                   note_id=getattr(args, "id", None),
                                   expected_revision=getattr(args, "expected_revision", None),
                                   reason=getattr(args, "reason", ""))
    if c == "status":
        # Merged here rather than inside Ledger.status(), which the scheduler calls twice a second.
        return {**ledger.status(), **ledger.lifecycle_status(), "borrowed_comments": ledger.borrowed_comments()}
    if c == "queue":
        return ledger.queue()
    if c == "fetch-issue":
        return ledger.observe_issue(api_factory().fetch_issue(ledger.item(args.item)["issue_id"]))
    if c == "claim":
        return ledger.claim(args.item, worker_id=args.worker_id)
    if c == "renew":
        return ledger.renew(args.item, resolve_token(args))
    if c == "checkpoint":
        progress = read_json(args.input)
        if isinstance(progress, dict):
            from .config import ROOT
            from .skills import load_skills
            item = ledger.item(args.item)
            skill = load_skills(ROOT / "skills").get(item["skill"])
            published = progress.get("published_prs")
            if (isinstance(published, list) and all(isinstance(url, str) for url in published)
                    and skill is not None):
                known = set(ledger.issue_context(args.item)["published_prs"])
                check_pr_targets([url for url in published if url not in known],
                                 allowed_repositories=write_repositories(item, skill))
            else:
                check_pr_targets(published)
        token = resolve_token(args)
        return ledger.checkpoint(args.item, token, progress,
                                 verified_prs=verify_late_prs(ledger, args, token, progress))
    if c == "handoff-repository":
        from .config import ROOT
        from .skills import load_skills
        token = resolve_token(args)
        ledger.renew(args.item, token)
        item = ledger.item(args.item)
        if item["skill"] != "fix" or args.to not in FIX_REPOSITORIES:
            raise LedgerError("repository handoff requires a configured fix repository")
        config = load_config(secure_permissions=False)
        if Path(args.db).resolve() != Paths(config).ledger.resolve():
            raise LedgerError("repository handoff must use the configured host ledger")
        skill = load_skills(ROOT / "skills").get("fix")
        if skill is None or args.to not in skill.writes or args.to not in config.repos:
            raise LedgerError("target repository is not configured for this fix worker")
        api = api_factory()
        issue = api.fetch_issue(item["issue_id"])
        ledger.observe_issue(issue)
        if (not api.app_user_id or issue.get("delegate_id") != api.app_user_id or issue.get("archived")
                or issue.get("status_type") in ("completed", "canceled")):
            raise LedgerError("issue must remain open and delegated to FarmBot")
        ledger.renew(args.item, token)
        return ledger.handoff_repository(args.item, token, args.to)
    if c == "issue-context":
        return ledger.issue_context(args.item)
    if c == "pop-inbox":
        return ledger.pop_inbox(args.item, resolve_token(args))
    if c == "prepare-comment":
        return ledger.prepare_comment(args.item, resolve_token(args), args.kind, read_text(args.body_file))
    if c == "post-comment":
        action = owned_action(ledger, args.item, args.action_id, resolve_token(args))
        return post_comment(ledger, api_factory(), action)
    if c == "confirm-comment":
        action = owned_action(ledger, args.item, args.action_id, resolve_token(args))
        return ledger.confirm_comment(action["action_id"], args.remote_id)
    if c == "activity":
        item = ledger.item(args.item)
        ledger.renew(args.item, resolve_token(args))  # proves ownership before speaking for the item
        api = api_factory()
        if args.type == "elicitation":
            api.needs_more_info(item["issue_id"])
        return api.create_activity(item["session_id"], {"type": args.type, "body": read_text(args.body_file)})
    if c == "await-input":
        token = resolve_token(args)
        item = ledger.item(args.item)
        ledger.renew(args.item, token)
        ledger.require_valid_checkpoint(args.item, token)
        api = api_factory()
        api.needs_more_info(item["issue_id"])
        api.create_activity(item["session_id"], {"type": "elicitation", "body": args.question})
        return ledger.await_input(args.item, token, args.question)
    if c in ("resume-work", "request-repair"):
        token = resolve_token(args)
        ledger.renew(args.item, token)
        item = ledger.item(args.item)
        api = api_factory()
        ledger.observe_issue(api.fetch_issue(item["issue_id"]))
        if c == "request-repair":
            from .config import ROOT
            from .skills import load_skills
            if "fix" not in load_skills(ROOT / "skills"):
                raise LedgerError("repair execution is not available on this host")
            resumed = ledger.request_repair(args.item, token, args.message_id, api.app_user_id,
                                             read_text(args.summary_file))
        else:
            resumed = ledger.resume_work(args.item, token, args.message_id, api.app_user_id)
        try:
            api.create_activity(item["session_id"], {
                "type": "thought" if item["session_id"] == resumed["session_id"] else "response",
                "body": ("已排队开始或继续修复，会接着你的回复和已有调查结果处理。"
                         + ("后续进展记录在原委派会话和 issue 下。"
                            if item["session_id"] != resumed["session_id"] else ""))})
        except Exception as exc:
            print(json.dumps({"warning": "repair queued; acknowledgment failed", "error": type(exc).__name__}), file=sys.stderr)
        return resumed
    if c == "verify-publication":
        from .config import ROOT
        from .publication import PublicationVerifier
        from .skills import load_skills
        from .worktrees import Worktrees, _git
        token = resolve_token(args)
        ledger.renew(args.item, token)
        item = ledger.item(args.item)
        config = load_config(secure_permissions=False)
        paths = Paths(config)
        if Path(args.db).resolve() != paths.ledger.resolve():
            raise LedgerError("publication must use the configured host ledger")
        skills = load_skills(ROOT / 'skills')
        skill = skills.get(item['skill'])
        session = ledger.session(item['session_id']) or {}
        if (item['skill'] not in WRITE_SKILLS or not skill or args.repo not in write_repositories(item, skill)
                or not session.get('delegation')):
            raise LedgerError("only a delegated write worker may verify an allowed publishing repository")
        from .publication import PublicationUnavailable
        from .publication_retry import verify_with_retries

        def verify_current():
            api = api_factory()
            issue = api.fetch_issue(item['issue_id'])
            ledger.observe_issue(issue)
            if (not api.app_user_id or issue.get('delegate_id') != api.app_user_id or issue.get('archived')
                    or issue.get('status_type') in ('completed', 'canceled')):
                raise LedgerError("issue must remain open and delegated to FarmBot")
            trees = Worktrees(paths.repos, paths.worktrees, config.repos)
            branch = _git('branch', '--show-current', cwd=paths.worktrees / args.item / args.repo)
            result = PublicationVerifier(trees, issue_prefix=config.issue_prefix).verify(
                args.repo, args.item, issue['identifier'], branch)
            ledger.renew(args.item, token)  # fence cancellation while network checks were in progress
            return result
        try:
            return verify_with_retries(verify_current, lambda: ledger.renew(args.item, token))
        except PublicationUnavailable:
            return ledger.defer_publication_retry(args.item, token, args.repo)
    if c == "await-resource":
        token = resolve_token(args)
        if args.commit is not None:
            from .worktrees import Worktrees
            ledger.renew(args.item, token)  # authenticate before reading any checkout
            item = ledger.item(args.item)
            if (item["skill"] not in WRITE_SKILLS or args.resource != "unity_slot"
                    or (item.get("target") or {}).get("repository") != "Farm-Client"
                    or (item["skill"] == "fix" and item["root_repo"] != "Farm-Client")):
                raise LedgerError("only a write worker may select a Farm-Client Unity verification commit")
            config = load_config(secure_permissions=False)
            paths = Paths(config)
            if Path(args.db).resolve() != paths.ledger.resolve():
                raise LedgerError("verification must use the configured host ledger")
            Worktrees(paths.repos, paths.worktrees, config.repos).verification_commit(
                "Farm-Client", args.item, args.commit)
        # Ownership is checked again after Git validation, fencing a concurrent stop/closure.
        return ledger.await_resource(args.item, token, args.resource, args.mode, commit_sha=args.commit)
    if c == "release-resource":
        # The worker's own verdict, not the pool's: the pool re-checks quiescence before acting on a slot
        # this worker may have wedged (spec §7). Both outcomes are verified against the reservation's own
        # hashed token, never the claim token: this is a worker command, and `unclean` — which takes the
        # host's only slot out of the pool until an operator runs recover-slot — is the consequential one.
        token = resolve_token(args)
        reservation = ledger.active_reservation(args.item)
        if reservation is None:
            raise LedgerError("this item holds no resource")
        if args.outcome == "unclean":
            held = ledger.hold_owned(reservation["reservation_id"], token, "worker reported an unclean release")
            return {"state": "recovery_queued", "reservation": held, "item": ledger.item(args.item),
                    "next_action": "exit; the controller will recover Unity and resume the saved job"}
        ledger.release(reservation["reservation_id"], token, "worker reported quiescent")
        return ledger.reservation(reservation["reservation_id"])
    if c == "reservations":
        return ledger.reservations()
    if c == "slots":
        return ledger.slots()
    if c == "recover-slot":
        return ledger.recover_slot(args.slot, args.reason)
    if c == "finish":
        evidence = read_json(args.input)
        if isinstance(evidence, dict):
            check_pr_targets(evidence.get("prs"))
        item = ledger.item(args.item)
        view = ledger.finish(args.item, resolve_token(args), args.outcome, evidence)
        complete_session(api_factory, item, args.outcome, evidence)
        return view
    if c == "cancel":
        return ledger.cancel(args.item, args.reason)
    if c == "recover":
        return ledger.recover(args.item, args.reason)
    if c == "retry":
        item = ledger.item(args.item)
        api = api_factory()
        current = api.fetch_issue(item["issue_id"])
        ledger.observe_issue(current)
        if item["skill"] in WRITE_SKILLS and current.get("delegate_id") != api.app_user_id:
            raise LedgerError("issue must remain delegated to FarmBot")
        return ledger.retry(args.item, args.reason)
    raise LedgerError(f"unknown command {c}")


def main(argv=None):
    args = parser().parse_args(argv)
    ledger = None
    try:
        from .environment import check_worker_state
        check_worker_state(args.db)
        ledger = Ledger(args.db, lease_seconds=args.lease_seconds)
        result = run(args, ledger, linear_api)
        print(json.dumps(result, ensure_ascii=True, allow_nan=False))
        return 0
    except (LedgerError, OSError, ValueError, RuntimeError, sqlite3.Error, KeyError) as exc:
        print(f"farmbot: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if ledger is not None:
            ledger.close()


if __name__ == "__main__":
    raise SystemExit(main())
