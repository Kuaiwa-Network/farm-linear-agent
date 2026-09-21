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


def check_pr_targets(urls):
    """A worker may only register PRs on the repositories this host is configured for."""
    if not isinstance(urls, list) or not urls:
        return
    try:
        repos = load_config().repos
    except (OSError, ValueError):
        return  # no private config (tests, first run): the ledger's URL shape is the only rule
    prefixes = {f"https://github.com/{m.group(1)}/{m.group(2)}/pull/".casefold()
                for m in (GITHUB_REMOTE.fullmatch(str(remote)) for remote in (repos or {}).values()) if m}
    if not prefixes:  # a config that names no GitHub remote can never legitimise a PR
        raise LedgerError("no configured GitHub repository to register PRs on; check repos in the host config")
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
            check_pr_targets(progress.get("published_prs"))
        return ledger.checkpoint(args.item, resolve_token(args), progress)
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
        api = api_factory()
        api.needs_more_info(item["issue_id"])
        api.create_activity(item["session_id"], {"type": "elicitation", "body": args.question})
        return ledger.await_input(args.item, token, args.question)
    if c == "resume-work":
        token = resolve_token(args)
        ledger.renew(args.item, token)
        item = ledger.item(args.item)
        api = api_factory()
        ledger.observe_issue(api.fetch_issue(item["issue_id"]))
        resumed = ledger.resume_work(args.item, token, args.message_id, api.app_user_id)
        try:
            api.create_activity(item["session_id"], {
                "type": "thought" if item["session_id"] == resumed["session_id"] else "response",
                "body": ("已为取消的修复任务创建新工作项，你的回复和历史进展会交给新的 worker。"
                         if resumed.get("predecessor_id") else
                         "已恢复原修复任务，你的回复会交给新的 worker；后续进展会记录在原任务会话和 issue 下。")})
        except Exception as exc:
            print(json.dumps({"warning": "work resumed; acknowledgment failed", "error": type(exc).__name__}), file=sys.stderr)
        return resumed
    if c == "verify-publication":
        from .config import ROOT
        from .publication import PublicationVerifier
        from .skills import load_skills
        from .worktrees import Worktrees, _git
        token = resolve_token(args)
        ledger.renew(args.item, token)
        item = ledger.item(args.item)
        config = load_config()
        paths = Paths(config)
        if Path(args.db).resolve() != paths.ledger.resolve():
            raise LedgerError("publication must use the configured host ledger")
        skills = load_skills(ROOT / 'skills')
        skill = skills.get(item['skill'])
        session = ledger.session(item['session_id']) or {}
        if (item['skill'] not in WRITE_SKILLS or not skill or args.repo not in skill.writes
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
            result = PublicationVerifier(trees).verify(args.repo, args.item, issue['identifier'], branch)
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
                    or (item.get("target") or {}).get("repository") != "Farm-Client"):
                raise LedgerError("only a write worker may select a Farm-Client Unity verification commit")
            config = load_config()
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
            # The worker says it left the Editor in a state it could not settle; the slot waits for an
            # operator's recover-slot rather than going to the next worker wedged.
            return ledger.hold_owned(reservation["reservation_id"], token, "worker reported an unclean release")
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
        ledger = Ledger(args.db, lease_seconds=args.lease_seconds)
        result = run(args, ledger, linear_api)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except (LedgerError, OSError, ValueError, RuntimeError, sqlite3.Error, KeyError) as exc:
        print(f"farmbot: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if ledger is not None:
            ledger.close()


if __name__ == "__main__":
    raise SystemExit(main())
