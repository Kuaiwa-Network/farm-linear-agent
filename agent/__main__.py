"""FarmBot ledger CLI: the only path from a worker to the ledger and to Linear (spec §8)."""
import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

from .config import Config, Paths, linear_api, load_config
from .ledger import ACTIVE_STATES, AWAIT_REASONS, NOTICE_KINDS, TERMINAL_STATUS_TYPES, Ledger, LedgerError
from .memory import prune_snapshots
from .router import CONVERSATION_SKILLS, WRITE_SKILLS, continuation_refusal
from .stages import write_repositories
from .withdrawal import NOTICE_REASONS, notice as withdrawal_notice, question_withdrawn


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
    cmd("revalidate", "--item", "--fingerprint", token=True)
    cmd("issue-context", "--item")
    cmd("pop-inbox", "--item", token=True)
    cmd("verify-publication", "--item", "--repo", token=True)
    cmd("foreign-work", "--item", token=True)
    uploads = cmd("download-uploads", "--item", "--out", token=True)
    uploads.add_argument("--url", action="append",
                         help="one of the claimed issue's upload URLs, unsigned as issue-context shows it; repeatable; "
                              "default: every upload in its description and human comments")
    prepare = cmd("prepare-comment", "--item", "--body-file", token=True)
    prepare.add_argument("--kind", required=True, choices=["started", "blocker", "delivery"])
    cmd("post-comment", "--item", "--action-id", token=True)
    cmd("confirm-comment", "--item", "--action-id", "--remote-id", token=True)
    notice = cmd("prepare-notice", "--item", "--request-id", "--body-file", token=True)
    notice.add_argument("--kind", required=True, choices=NOTICE_KINDS)
    cmd("post-notice", "--item", "--request-id", token=True)
    activity = cmd("activity", "--item", "--body-file", token=True)
    activity.add_argument("--type", required=True, choices=["thought", "action", "response", "error", "elicitation"])
    pause = cmd("await-input", "--item", "--question", token=True)
    pause.add_argument("--reason", choices=AWAIT_REASONS, default="question",
                       help="question (default) adds needs-more-info; waiting, for a human step elsewhere, does not")
    resume = cmd("resume-work", "--item", token=True)
    resume.add_argument("--message-id", type=int, required=True)
    repair = cmd("request-repair", "--item", "--summary-file", token=True)
    repair.add_argument("--message-id", type=int, required=True)
    resource = cmd("await-resource", "--item", "--resource", token=True)
    resource.add_argument("--mode", required=True, choices=["interactive", "batch"])
    resource.add_argument("--commit", help="full SHA of the clean Farm-Client worktree HEAD to verify; default: baseline")
    release = cmd("release-resource", "--item", token=True)
    release.add_argument("--outcome", required=True, choices=["quiescent", "unclean"])
    cmd("withdraw", "--item", token=True)
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


def enabled_skill_names():
    """The skills this host runs (spec §9.11). With no readable private config, as in test fixtures, every loaded
    skill but the opt-in ones (P1): the scheduler still refuses to launch one the controller's config leaves out."""
    from .config import ROOT
    from .dispatch import SKILL_AUTHORITY
    from .skills import enabled_skills, load_skills
    try:
        names = load_config(secure_permissions=False).enabled_skills
    except (OSError, ValueError):
        names = None
    return set(enabled_skills(load_skills(ROOT / "skills"), names, authority=SKILL_AUTHORITY))


def conversation_request(ledger, item_id, issue, running, *, start):
    """What a request in a conversation starts or continues, as (skill, refusal); at most one is not None.

    The delegation's own earlier job (`resumable_work`: a fix, or a feature job; spec §9.4) continues, whatever the
    card's label now says, where this host runs its skill. Otherwise `request-repair` (start=True) starts the
    workflow the card's Bot label names, where this host runs it and a conversation may start it (D18 f; Bot label
    group design §4.4): 修改 or no Bot child, `fix`; Code, `feature`. `resume-work` (start=False) starts nothing.
    A refusal says why nothing starts. Where the delegation's job has a skill this host does not run, a label that
    refuses a first start says so first, and no other skill's job starts in its place. (None, None) also when the
    item is not a conversation, which the ledger refuses itself. While the chat item is active no other item can
    appear on the issue, so this cannot change before the ledger's transaction."""
    from .router import bot_children, start_refusal, start_skill
    context = ledger.issue_context(item_id)
    if context["coordination"]["skill"] != "chat":
        return None, None
    work = context["resumable_work"]
    if work is not None and work["skill"] in running:
        return work["skill"], None
    children = bot_children(issue.get("label_groups"))
    refusal = start_refusal(children, running) if start else None
    if refusal is None and work is not None:
        refusal = continuation_refusal(work["skill"])
    if refusal is not None:
        return None, refusal
    return (start_skill(children) if start else None), None


def configured_issue_prefix():
    """The host's issue namespace (Config.issue_prefix), in which a plan's issue branches are checked (plan P9);
    Config's default where no private config is readable, as in test fixtures, which the scheduler and the
    publication verifier default to as well."""
    try:
        return load_config(secure_permissions=False).issue_prefix
    except (OSError, ValueError):
        return Config.issue_prefix


def configured_bot_name():
    """The Linear app this host speaks as (Config.expected_bot_name), as the notices name it; Config's default where
    no private config is readable, as in test fixtures."""
    try:
        return load_config(secure_permissions=False).expected_bot_name
    except (OSError, ValueError):
        return Config.expected_bot_name


def refuse_withdrawn(item):
    """A worker told its work is withdrawn publishes, pauses and hands off nothing (withdrawn-work design P2, A10):
    refused before anything reaches Linear or GitHub."""
    if item["withdraw_deadline"] is not None:
        raise LedgerError(Ledger.WITHDRAWN)


def owe_closing(ledger, item, content, exc):
    """Linear refused `content`, the response or error that closes `item`'s thread: it is owed to the thread, and the
    controller's progress loop posts it again (silent-delegation design P10, A4). Best effort, as the post was; an
    operator's `local-` job, which has no Linear thread, is owed nothing."""
    try:
        ledger.owe_closure(str(item["session_id"]), issue_id=item["issue_id"], item_id=item["id"],
                           kind=content["type"], body=content["body"], error=type(exc).__name__)
    except Exception:
        pass


def post_closing_notice(ledger, api_factory, item, reason):
    """Once the ledger is final: the one notice a job cancelled for `reason` gets (design P7), as its session's
    response, or as an issue comment for an operator's `local-` job, which has no Linear session. A job whose issue is
    out of reach gets none. True when Linear took it; a response it refused is owed to the session (owe_closing)."""
    if reason not in NOTICE_REASONS:
        return False
    body = None
    try:
        body = withdrawal_notice(item["skill"], reason, configured_bot_name())
        api = api_factory()
        if str(item["session_id"]).startswith("local-"):
            api.create_comment(item["issue_id"], body)
        else:
            api.create_activity(item["session_id"], {"type": "response", "body": body})
        return True
    except Exception as exc:
        print(json.dumps({"warning": "closing notice not posted", "error": type(exc).__name__}), file=sys.stderr,
              flush=True)
        if body is not None:
            owe_closing(ledger, item, {"type": "response", "body": body}, exc)
        return False


def withdraw_question(ledger, api, item_id):
    """`await-input` posted its question, and the ledger then refused to park the job: a Stop, a closure or a failure
    ended it in between. The question would stay its thread's last activity, asking for an answer no job reads, so
    one response withdraws it, owed when Linear refuses it (silent-delegation design A6, P9). A job that goes on,
    queued again or still claimed, speaks in its thread itself and is told nothing here."""
    try:
        item = ledger.item(item_id)
    except LedgerError:
        return
    if item["state"] in ACTIVE_STATES:
        return
    content = {"type": "response", "body": question_withdrawn(item["skill"])}
    try:
        api.create_activity(item["session_id"], content)
    except Exception as exc:
        print(json.dumps({"warning": "question not withdrawn", "error": type(exc).__name__}), file=sys.stderr,
              flush=True)
        owe_closing(ledger, item, content, exc)


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


def complete_session(ledger, api_factory, item, outcome, evidence):
    """After the ledger is final: Linear keeps a session active until a response or error arrives, so a response it
    refuses is owed to the session (owe_closing)."""
    content = session_response(item["skill"], outcome, evidence)
    if content is None:
        return
    try:
        api_factory().create_activity(item["session_id"], content)
    except Exception as exc:
        print(json.dumps({"warning": "session response not posted", "error": type(exc).__name__}), file=sys.stderr, flush=True)
        owe_closing(ledger, item, content, exc)


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


def owned_notice(ledger, item_id, request_id, token):
    """A notice belongs to one work item: only that item's live claim may post it."""
    ledger.renew(item_id, token)
    notice = ledger.notice(item_id, request_id)
    if notice is None:
        raise LedgerError("unknown notice for this work item; prepare it first")
    return notice


def post_notice(ledger, api, notice):
    """Reconcile the marker against live comments before ever creating one, exactly as post_comment does; an
    issue that has left scope gets no new notice, though one already posted is still recorded."""
    if notice["remote_id"]:
        return notice
    issue = api.fetch_issue(notice["issue_id"])
    observed = ledger.observe_issue(issue)
    remote_id = next((c["id"] for c in issue["comments"] if notice["marker"] in c["body"]), None)
    if remote_id is None:
        if not observed["in_scope"]:
            raise LedgerError("issue left scope; the notice was not posted")
        remote_id = api.create_comment(notice["issue_id"], notice["body"])
    return ledger.confirm_notice(notice["item_id"], notice["request_id"], remote_id)


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
        api = api_factory()
        issue_id = ledger.item(args.item)["issue_id"]
        issue = api.fetch_issue(issue_id)
        # A claimed worker learns here that the delegation was removed or moved, or that its work was withdrawn, and
        # then withdraws itself (withdrawn-work design P2). LinearAPI.fetch_issue establishes this app's identity
        # before it reads the issue.
        observed = ledger.observe_issue(issue)
        delegated = issue.get("delegate_id") == api.app_user_id
        if not delegated:
            # The lifecycle's next status read confirms or clears this; the worker cancels nothing itself (§3).
            ledger.request_status_check(issue_id)
        return {**observed, "delegated": delegated,
                "withdrawn": ledger.item(args.item)["withdraw_deadline"] is not None}
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
                                 verified_prs=verify_late_prs(ledger, args, token, progress),
                                 issue_prefix=configured_issue_prefix())
    if c == "handoff-repository":
        from .config import ROOT
        from .skills import load_skills
        token = resolve_token(args)
        ledger.renew(args.item, token)
        item = ledger.item(args.item)
        refuse_withdrawn(item)
        # The item's own manifest decides its stages: any staged skill, to a repository in its writes.
        skill = load_skills(ROOT / "skills").get(item["skill"])
        if skill is None or not skill.staged:
            raise LedgerError(f"repository handoff requires a staged skill; {item['skill']} is not staged on this host")
        if args.to not in skill.writes:
            raise LedgerError(f"repository handoff requires a configured {skill.name} repository")
        config = load_config(secure_permissions=False)
        if Path(args.db).resolve() != Paths(config).ledger.resolve():
            raise LedgerError("repository handoff must use the configured host ledger")
        if args.to not in config.repos:
            raise LedgerError(f"target repository is not configured for this {skill.name} worker")
        api = api_factory()
        issue = api.fetch_issue(item["issue_id"])
        ledger.observe_issue(issue)
        if (not api.app_user_id or issue.get("delegate_id") != api.app_user_id or issue.get("archived")
                or issue.get("status_type") in TERMINAL_STATUS_TYPES):
            raise LedgerError("issue must remain open and delegated to FarmBot")
        ledger.renew(args.item, token)
        return ledger.handoff_repository(args.item, token, args.to, skill=skill)
    if c == "revalidate":
        return ledger.revalidate(args.item, resolve_token(args), args.fingerprint)
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
    if c == "prepare-notice":
        return ledger.prepare_notice(args.item, resolve_token(args), args.kind, args.request_id,
                                     read_text(args.body_file))
    if c == "post-notice":
        notice = owned_notice(ledger, args.item, args.request_id, resolve_token(args))
        return post_notice(ledger, api_factory(), notice)
    if c == "activity":
        if args.type == "elicitation":
            # A question parks its job, or its thread waits for an answer no job reads (silent-delegation design
            # A8, P9): refused before anything reaches Linear.
            raise LedgerError("ask with await-input: it posts the question and parks the job")
        item = ledger.item(args.item)
        ledger.renew(args.item, resolve_token(args))  # proves ownership before speaking for the item
        return api_factory().create_activity(item["session_id"], {"type": args.type, "body": read_text(args.body_file)})
    if c == "await-input":
        token = resolve_token(args)
        ledger.renew(args.item, token)
        item = ledger.item(args.item)
        refuse_withdrawn(item)
        ledger.require_valid_checkpoint(args.item, token)
        ledger.require_no_reservation(args.item)  # refuse before anything reaches Linear
        api = api_factory()
        if item["authority"] == "delegation":
            # Work the delegation authorised never parks on a card that no longer has it: nothing would answer it
            # there (withdrawn-work design J1). A fresh read decides, before anything is written to Linear.
            status = api.issue_status(item["issue_id"])
            if status["archived"] or status["status_type"] in TERMINAL_STATUS_TYPES:
                raise LedgerError("issue closed or archived: save a checkpoint, then run withdraw and exit")
            if not api.app_user_id or status.get("delegate_id") != api.app_user_id:
                raise LedgerError(Ledger.WITHDRAWN)
        if args.reason == "question":
            api.needs_more_info(item["issue_id"])
        api.create_activity(item["session_id"], {"type": "elicitation", "body": args.question})
        try:
            return ledger.await_input(args.item, token, args.question, reason=args.reason)
        except LedgerError:
            withdraw_question(ledger, api, args.item)  # the job ended between the question and the park
            raise
    if c in ("resume-work", "request-repair"):
        token = resolve_token(args)
        ledger.renew(args.item, token)
        item = ledger.item(args.item)
        # Both queue write work, which this host may not run (spec §9.11): refuse before asking Linear anything
        # when it runs no skill a conversation starts or continues. Which one applies needs the fresh card.
        running = enabled_skill_names()
        if not running & set(CONVERSATION_SKILLS):
            raise LedgerError("repair execution is not available on this host")
        api = api_factory()
        current = api.fetch_issue(item["issue_id"])
        ledger.observe_issue(current)
        skill, refusal = conversation_request(ledger, args.item, current, running, start=c == "request-repair")
        if refusal is not None:
            raise LedgerError(refusal)
        # The read's own delegate decides: the stored snapshot keeps an older one when this read's updatedAt did not
        # move (withdrawn-work design J4, U4).
        if c == "request-repair":
            # A request from a work item names no skill here; the ledger refuses it itself.
            resumed = ledger.request_repair(args.item, token, args.message_id, api.app_user_id,
                                             read_text(args.summary_file), start_skill=skill or "fix",
                                             delegate_id=current.get("delegate_id"))
        else:
            resumed = ledger.resume_work(args.item, token, args.message_id, api.app_user_id,
                                         delegate_id=current.get("delegate_id"))
        # D18: 修改 names the fix workflow; other work is named generically, never promised as a fix.
        named = "修改" if resumed["skill"] == "fix" else "这项工作"
        moved = item["session_id"] != resumed["session_id"]
        content = {"type": "response" if moved else "thought",
                   "body": (f"已排队开始或继续{named}，会接着你的回复和已有调查结果处理。"
                            + ("后续进展记录在原委派会话和 issue 下。" if moved else ""))}
        try:
            api.create_activity(item["session_id"], content)
        except Exception as exc:
            print(json.dumps({"warning": "repair queued; acknowledgment failed", "error": type(exc).__name__}), file=sys.stderr)
            if moved:
                # The response closes the conversation's thread, whose job runs in another: it is owed. In the job's
                # own thread the acknowledgement is a thought, and the job speaks there next.
                owe_closing(ledger, item, content, exc)
        return resumed
    if c == "download-uploads":
        from .uploads import download_issue_uploads, issue_uploads, output_directory
        token = resolve_token(args)
        ledger.renew(args.item, token)  # authenticate before touching the disk or Linear
        output_directory(args.out, create=False)  # a bad --out is refused before Linear is asked
        item = ledger.item(args.item)
        api = api_factory()
        issue = api.fetch_issue(item["issue_id"])
        ledger.observe_issue(issue)
        available = issue_uploads(issue)
        for number, url in enumerate(args.url or [], 1):
            if url not in available:
                raise LedgerError(f"--url {number} is not an upload of the claimed issue; pass an unsigned URL "
                                  "from its description or human comments, as issue-context shows it")
        out = output_directory(args.out)  # created only once every --url is known to be the issue's
        summary = download_issue_uploads(api, issue, out, urls=args.url,
                                         renew=lambda: ledger.renew(args.item, token))
        ledger.renew(args.item, token)  # a claim lost during the transfers still fails the command
        return summary
    if c == "foreign-work":
        from .foreign_work import foreign_work
        token = resolve_token(args)
        ledger.renew(args.item, token)  # read-only, yet only the claimed worker may ask
        config = load_config(secure_permissions=False)
        paths = Paths(config)
        if Path(args.db).resolve() != paths.ledger.resolve():
            raise LedgerError("foreign-work must use the configured host ledger")
        report = foreign_work(ledger, args.item, config.repos, paths.repos)
        ledger.renew(args.item, token)  # a Stop during the network reads ends the claim here
        return report
    if c == "verify-publication":
        from .config import ROOT
        from .publication import PublicationVerifier
        from .skills import load_skills
        from .worktrees import Worktrees, _git
        token = resolve_token(args)
        ledger.renew(args.item, token)
        item = ledger.item(args.item)
        refuse_withdrawn(item)
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
                    or issue.get('status_type') in TERMINAL_STATUS_TYPES):
                raise LedgerError("issue must remain open and delegated to FarmBot")
            trees = Worktrees(paths.repos, paths.worktrees, config.repos)
            branch = _git('branch', '--show-current', cwd=paths.worktrees / args.item / args.repo)
            result = PublicationVerifier(trees, issue_prefix=config.issue_prefix).verify(
                args.repo, args.item, issue['identifier'], branch)
            ledger.renew(args.item, token)  # fence cancellation while network checks were in progress
            refuse_withdrawn(ledger.item(args.item))  # and a withdrawal flagged meanwhile
            return result
        try:
            result = verify_with_retries(verify_current, lambda: ledger.renew(args.item, token))
        except PublicationUnavailable:
            return ledger.defer_publication_retry(args.item, token, args.repo)
        from .foreign_work import foreign_work
        # Evidence, not a gate (spec §4.5): refusing on it would need a stored acknowledgement. verify_current has
        # just observed the issue, so the attachments read here are fresh.
        try:
            result["foreign_work"] = foreign_work(ledger, args.item, config.repos, paths.repos,
                                                  repositories=[args.repo])
        except (OSError, ValueError, RuntimeError) as exc:
            result["foreign_work"] = {"status": "unavailable", "error": type(exc).__name__}
        ledger.renew(args.item, token)  # a Stop during these reads ends the claim here, as after verification
        refuse_withdrawn(ledger.item(args.item))
        return result
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
    if c == "withdraw":
        # A worker ends its own withdrawn work (withdrawn-work design P2, A10): its checkpoint is saved, and the job
        # ends cancelled, so a later delegation continues it from the plan. It needs a flag, or a fresh read that
        # finds the card closed or, for the delegation's work, no longer delegated here; the ledger decides.
        token = resolve_token(args)
        ledger.renew(args.item, token)
        item = ledger.item(args.item)
        api = api_factory()
        try:
            status = api.issue_status(item["issue_id"])
        except Exception:
            if item["withdraw_deadline"] is None:
                raise  # an unread card withdraws nothing (design P8)
            status = None  # the flag, which two confirming reads or a new delegation set, decides alone
        if status is None:
            delegated = closed = False
        else:
            delegated = bool(api.app_user_id) and status.get("delegate_id") == api.app_user_id
            closed = status["archived"] or status["status_type"] in TERMINAL_STATUS_TYPES
        view, reason = ledger.withdraw(args.item, token, delegated=delegated, closed=closed)
        posted = post_closing_notice(ledger, lambda: api, view, reason)
        return {**view, "reason": reason, "notice_posted": posted,
                "next_action": "exit; a later delegation continues this job from its checkpoint"}
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
        complete_session(ledger, api_factory, item, args.outcome, evidence)
        return view
    if c == "cancel":
        # An active job the operator stops gets one note, and its pending heartbeat goes in the same transaction, so
        # no "stopped" correction follows the note (withdrawn-work design I2, P7). A blocked job has reported
        # already and ends silently, as on closure; one that had already ended gets nothing.
        cancelled = ledger.cancel(args.item, args.reason, states=ACTIVE_STATES, drop_progress=True)
        if cancelled is None:
            return ledger.cancel(args.item, args.reason)
        post_closing_notice(ledger, api_factory, cancelled, "operator")
        return cancelled
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
