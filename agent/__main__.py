"""FarmBot ledger CLI: the only path from a worker to the ledger and to Linear (spec §8)."""
import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

from .config import linear_api, load_config
from .ledger import Ledger, LedgerError


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

    cmd("status"); cmd("queue")
    cmd("fetch-issue", "--item")
    cmd("claim", "--item", "--worker-id")
    cmd("renew", "--item", token=True)
    cmd("checkpoint", "--item", "--input", token=True)
    cmd("issue-context", "--item")
    cmd("pop-inbox", "--item", token=True)
    prepare = cmd("prepare-comment", "--item", "--body-file", token=True)
    prepare.add_argument("--kind", required=True, choices=["started", "blocker", "delivery"])
    cmd("post-comment", "--item", "--action-id", token=True)
    cmd("confirm-comment", "--item", "--action-id", "--remote-id", token=True)
    activity = cmd("activity", "--item", "--body-file", token=True)
    activity.add_argument("--type", required=True, choices=["thought", "action", "response", "error", "elicitation"])
    cmd("await-input", "--item", "--question", token=True)
    resource = cmd("await-resource", "--item", "--resource", token=True)
    resource.add_argument("--mode", required=True, choices=["interactive", "batch"])
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
    """Only a live claim on the item that prepared a comment may act on it."""
    ledger.renew(item_id, token)
    row = ledger.connection.execute("SELECT * FROM outbox WHERE action_id=? AND item_id=?", (action_id, item_id)).fetchone()
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
    if c == "status":
        return ledger.status()
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
        return api_factory().create_activity(item["session_id"], {"type": args.type, "body": read_text(args.body_file)})
    if c == "await-input":
        token = resolve_token(args)
        item = ledger.item(args.item)
        ledger.renew(args.item, token)
        api_factory().create_activity(item["session_id"], {"type": "elicitation", "body": args.question})
        return ledger.await_input(args.item, token, args.question)
    if c == "await-resource":
        return ledger.await_resource(args.item, resolve_token(args), args.resource, args.mode)
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
