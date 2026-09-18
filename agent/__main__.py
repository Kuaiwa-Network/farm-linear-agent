"""FarmBot ledger CLI: the only path from a worker to the ledger and to Linear (spec §8)."""
import argparse
import json
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

    def cmd(name, *flags):
        p = sub.add_parser(name)
        for flag in flags:
            p.add_argument(flag, required=True)
        return p

    cmd("status"); cmd("queue")
    fetch = sub.add_parser("fetch-issue"); group = fetch.add_mutually_exclusive_group(required=True)
    group.add_argument("--item"); group.add_argument("--issue")
    cmd("claim", "--item", "--worker-id")
    cmd("renew", "--item", "--token")
    cmd("checkpoint", "--item", "--token", "--input")
    cmd("issue-context", "--item")
    cmd("pop-inbox", "--item", "--token")
    prepare = cmd("prepare-comment", "--item", "--token", "--body-file")
    prepare.add_argument("--kind", required=True, choices=["started", "blocker", "delivery"])
    cmd("post-comment", "--action-id")
    cmd("confirm-comment", "--action-id", "--remote-id")
    activity = cmd("activity", "--item", "--token", "--body-file")
    activity.add_argument("--type", required=True, choices=["thought", "action", "response", "error", "elicitation"])
    cmd("await-input", "--item", "--token", "--question")
    resource = cmd("await-resource", "--item", "--token", "--resource")
    resource.add_argument("--mode", required=True, choices=["interactive", "batch"])
    finish = cmd("finish", "--item", "--token", "--input")
    finish.add_argument("--outcome", required=True, choices=["blocked", "delivered"])
    for name in ("cancel", "recover", "retry"):
        cmd(name, "--item", "--reason")
    return root


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_text(path):
    return Path(path).read_text(encoding="utf-8")


def post_comment(ledger, api, action_id):
    """Reconcile the marker against live comments before ever creating a comment."""
    action = next((a for a in ledger.connection.execute("SELECT * FROM outbox WHERE action_id=?", (action_id,))), None)
    if action is None:
        raise LedgerError("unknown comment action")
    if action["remote_id"]:
        return dict(action)
    issue = api.fetch_issue(action["issue_id"])
    ledger.observe_issue(issue)
    remote_id = next((c["id"] for c in issue["comments"] if action["marker"] in c["body"]), None)
    if remote_id is None:
        remote_id = api.create_comment(action["issue_id"], action["body"])
    return ledger.confirm_comment(action_id, remote_id)


def run(args, ledger, api_factory):
    c = args.command
    if c == "status":
        return ledger.status()
    if c == "queue":
        return ledger.queue()
    if c == "fetch-issue":
        issue_id = ledger.item(args.item)["issue_id"] if args.item else args.issue
        view = ledger.observe_issue(api_factory().fetch_issue(issue_id))
        return view
    if c == "claim":
        return ledger.claim(args.item, worker_id=args.worker_id)
    if c == "renew":
        return ledger.renew(args.item, args.token)
    if c == "checkpoint":
        return ledger.checkpoint(args.item, args.token, read_json(args.input))
    if c == "issue-context":
        return ledger.issue_context(args.item)
    if c == "pop-inbox":
        return ledger.pop_inbox(args.item, args.token)
    if c == "prepare-comment":
        return ledger.prepare_comment(args.item, args.token, args.kind, read_text(args.body_file))
    if c == "post-comment":
        return post_comment(ledger, api_factory(), args.action_id)
    if c == "confirm-comment":
        return ledger.confirm_comment(args.action_id, args.remote_id)
    if c == "activity":
        item = ledger.item(args.item)
        ledger.renew(args.item, args.token)  # proves ownership before speaking for the item
        return api_factory().create_activity(item["session_id"], {"type": args.type, "body": read_text(args.body_file)})
    if c == "await-input":
        item = ledger.item(args.item)
        ledger.renew(args.item, args.token)
        api_factory().create_activity(item["session_id"], {"type": "elicitation", "body": args.question})
        return ledger.await_input(args.item, args.token, args.question)
    if c == "await-resource":
        try:
            slots = load_config().slots
        except (OSError, ValueError):
            slots = []
        if not slots:
            raise LedgerError("no unity slots configured on this host; record the verification gap instead")
        return ledger.await_resource(args.item, args.token, args.resource, args.mode)
    if c == "finish":
        return ledger.finish(args.item, args.token, args.outcome, read_json(args.input))
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
