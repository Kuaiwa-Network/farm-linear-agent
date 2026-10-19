"""FarmBot ledger CLI: the only path from a worker to the ledger and to Linear (spec §8)."""
import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

from .config import Config, Paths, linear_api, load_config
from .attention import mark_reply_label, question_notice
from .ledger import ACTIVE_STATES, AWAIT_REASONS, NOTICE_KINDS, TERMINAL_STATUS_TYPES, Ledger, LedgerError, require_listed_resource
from .memory import prune_snapshots
from .router import CONVERSATION_SKILLS, WRITE_SKILLS, continuation_refusal
from .stages import current_root, write_repositories
from .withdrawal import NOTICE_REASONS, notice as withdrawal_notice, question_withdrawn
from .execution import single, skill_for


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
    cmd('shared-test-repairs', '--item', '--candidate', token=True)
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
    claim = cmd("claim", "--item", "--worker-id")
    claim.add_argument('--context', action='store_true',
                       help='include the current resume context in the same claim response')
    claim.add_argument('--save-token', help='exclusively save state_dir/token; omit the secret from stdout')
    cmd("renew", "--item", token=True)
    cmd("maintenance-handoff", "--item", token=True)
    cmd("document-reader-status", "--item", token=True)
    cmd("checkpoint", "--item", "--input", token=True)
    cmd('closing-preflight', '--item', token=True)
    index = cmd('evidence-index', '--item', token=True)
    mode = index.add_mutually_exclusive_group(required=True)
    mode.add_argument('--input', help='JSON with pins and selected report paths, purposes and summaries')
    mode.add_argument('--verify', help='existing immutable evidence index in this item state directory')
    handoff = cmd("handoff-repository", "--item", "--to", token=True)
    handoff.add_argument('--checkpoint', help='save this checkpoint through normal gates before handing off')
    cmd("revalidate", "--item", "--fingerprint", token=True)
    context = cmd("issue-context", "--item")
    context.add_argument("--view", choices=["full", "resume"], default="full")
    cmd("pop-inbox", "--item", token=True)
    dependency = cmd("prepare-go-dependencies", "--item", "--module", "--commit", token=True)
    dependency.add_argument('--repo', help='repository for a single-worker dependency request')
    dependency.add_argument("--retry", action="store_true", help="retry a failed/cancelled request after host correction; at most three")
    cmd("go-dependencies", "--item", "--request-id", token=True)
    cmd("tasks", "--item")
    cmd("delegate-task", "--item", "--input", token=True)
    cmd("await-tasks", "--item", token=True)
    answer_task = cmd("answer-task", "--item", "--task", token=True)
    answer_task.add_argument("--message-id", type=int, required=True)
    retry_task_parser = cmd("retry-task", "--item", "--task", token=True)
    retry_task_parser.add_argument("--message-id", type=int, required=True)
    cmd("verify-publication", "--item", "--repo", token=True)
    cmd("retry-approval", "--item", "--error-file", token=True)
    cmd("foreign-work", "--item", token=True)
    uploads = cmd("download-uploads", "--item", "--out", token=True)
    cmd("upload-image", "--item", "--file", token=True)
    review_ui = cmd("review-ui", "--item", "--preview", "--asset-url", "--packages", token=True)
    review_ui.add_argument("--round", type=int, required=True)
    cmd("export-ui", "--item", "--review-id", "--preview", token=True)
    cmd('verify-ui-source', '--item', token=True)
    cmd('verify-scene-assets', '--item', token=True)
    cmd('authorize-scene-scope', '--item', '--task', '--input', token=True)
    cmd('yield-presentation', '--item', '--input', token=True)
    cmd('resume-presentation', '--item', '--task', token=True)
    cmd("install-ui", "--item", "--receipt-id", token=True)
    verify_ui = cmd("verify-ui", "--item", "--receipt-id", token=True)
    verify_ui.add_argument("--commit", required=True)
    cmd("verify-ui-loading", "--item", "--receipt-id", token=True)
    cmd("verify-ui-guards", "--item", "--receipt-id", token=True)
    cmd("ui-verification-status", "--item", "--receipt-id", token=True)
    cmd("authorize-ui-guard-repair", "--item", "--receipt-id", "--input", token=True)
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
    # `elicitation` stays a choice only so that `run` can refuse it with the command to use instead (silent-delegation
    # design A8); help offers the kinds `activity` posts.
    activity.add_argument("--type", required=True, choices=["thought", "action", "response", "error", "elicitation"],
                          metavar="{thought,action,response,error}",
                          help="the activity's kind; ask a question with await-input, which posts it and parks the job")
    pause = cmd("await-input", "--item", "--question", token=True)
    pause.add_argument("--reason", choices=AWAIT_REASONS, default="question",
                       help="question (default) adds 待回复; waiting, for a human step elsewhere, does not")
    resolved = cmd("resolve-question", "--item", "--question-id", token=True)
    resolved.add_argument("--message-id", type=int, action="append", required=True)
    resume = cmd("resume-work", "--item", token=True)
    resume.add_argument("--message-id", type=int, required=True)
    repair = cmd("request-repair", "--item", "--summary-file", token=True)
    repair.add_argument("--message-id", type=int, required=True)
    resource = cmd("await-resource", "--item", "--resource", token=True)
    cmd('resource-status', '--item', token=True)
    cmd('cancel-resource', '--item', '--reservation', token=True)
    cmd('unity-tools', '--item', token=True)
    cmd('unity-read', '--item', '--uri', token=True)
    cmd('unity-tool', '--item', '--name', '--input', token=True)
    cmd('unity-test-result', '--item', '--job-id', token=True)
    cmd('publish-ui', '--item', '--packages', token=True)
    resource.add_argument('--checkpoint', help='save this checkpoint before requesting the resource')
    resource.add_argument("--server-test-plan", help="JSON file with exact packages, tests and race selection")
    resource.add_argument('--integration-plan', help='single-client/game-server plan, from the Client stage')
    integration = cmd('run-integration', '--item', '--client-command', token=True)
    resource.add_argument("--server-scope-reason", help="concrete coverage reason for broader or named-only server checks")
    resource.add_argument("--mode", required=True, choices=["interactive", "batch"])
    resource.add_argument("--commit", help="full SHA of the clean Farm-Client worktree HEAD to verify; default: baseline")
    resource.add_argument("--baseline", action="store_true",
                          help="test trusted remote main for comparison; --commit still names the owned candidate")
    resource.add_argument('--reuse-candidate-reservation',
                          help='bind a completed candidate-first run to its first comparison baseline')
    resource.add_argument('--resume-baseline-reservation',
                          help='resume the original baseline after certified maintenance handoff')
    resource.add_argument('--repair-candidate-reservation',
                          help='continue a settled original comparison at a repaired descendant, preserving its base')
    continuation = cmd('continue-verification', '--item', '--commit', '--reservation-token-file', token=True)
    continuation.add_argument('--checkpoint', help='save completed baseline evidence before continuing')
    continuation.set_defaults(resource='unity_slot', mode='interactive', baseline=False, server_test_plan=None)
    progress = cmd('verification-progress', '--item', token=True)
    progress.add_argument('--phase', required=True, choices=['preparation', 'testing', 'presentation-qa',
                                                           'failure-review', 'comparison', 'publication', 'delivery'])
    comparison = cmd('compare-verification', '--item', '--baseline', '--candidate', '--scope', token=True)
    comparison.add_argument('--repair-original', help='retained original full candidate report for test-only repair')
    cmd('verify-candidate', '--item', '--candidate', '--scope', token=True)
    cmd('verification-closeout', '--item', '--input', token=True)
    release = cmd("release-resource", "--item", token=True)
    release.add_argument("--outcome", required=True, choices=["quiescent", "unclean"])
    cmd("withdraw", "--item", token=True)
    cmd("reservations"); cmd("slots")  # operator readers: no item, no token, nothing to authorise
    cmd("recover-slot", "--slot", "--reason")
    finish = cmd("finish", "--item", "--input", token=True)
    finish.add_argument('--checkpoint', help='save the compact final checkpoint through normal gates before finish checks')
    finish.add_argument("--outcome", required=True, choices=["blocked", "delivered"])
    checks = finish.add_mutually_exclusive_group()
    checks.add_argument("--check-input", action="store_true",
                        help="validate local outcome shape before preparing delivery; does not finish or verify live gates")
    checks.add_argument('--check-ready', action='store_true',
                        help='check saved completion gates before posting delivery, without finishing')
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
    if work is not None and set(running) <= {'chat', 'work'} and not single(ledger.item(work['id'])):
        return None, 'historical staged work requires settled host adoption into single-worker-v1 before continuation'
    if work is not None and work["skill"] in running:
        return work["skill"], None
    children = bot_children(issue.get("label_groups"))
    refusal = start_refusal(children, running) if start else None
    if refusal is None and work is not None:
        refusal = continuation_refusal(work["skill"])
    if refusal is not None:
        return None, refusal
    return (("work" if "work" in running else start_skill(children)) if start else None), None


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
    if item.get("parent_id"):
        return False  # the parent owns the one session-closing notice
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
    one response withdraws it, owed when Linear refuses it (silent-delegation design A6, P9). Work that goes on in
    the thread speaks there itself, and nothing is posted over it: the job, queued again or still claimed, or newer
    work that took its thread meanwhile, as SessionProgress posts no terminal text over a successor (A7). Work the
    job handed over to in another thread is not in this one, and the question here is withdrawn."""
    try:
        item = ledger.item(item_id)
    except LedgerError:
        return
    if item.get("parent_id"):
        return  # its thought cannot strand an elicitation or close the parent
    working = ledger.active_item_for_session(item["session_id"])
    if working is not None and working["session_id"] == item["session_id"]:
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
    skill = skill_for(item, load_skills(ROOT / 'skills', replay_legacy=not single(item)).get(item['skill']))
    session = ledger.session(item['session_id']) or {}
    if item['skill'] not in WRITE_SKILLS or not skill or not session.get('delegation'):
        raise LedgerError("only a delegated write worker may reconcile published PRs")
    from .workflow import JobWorktrees
    verifier = PublicationVerifier(JobWorktrees(Worktrees(paths.repos, paths.worktrees, config.repos), ledger),
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


def task_progress(ledger, api_factory, item, body, *, question=False):
    """Optional thoughts must not fail an already committed task transition.

    The task's question/outcome is durable in the ledger and wakes its parent,
    which owns the human conversation. Record transport failures for diagnosis.
    """
    try:
        current = ledger.item(item["id"])
        parent = ledger.item(item["parent_id"]) if item.get("parent_id") else current
        if parent["state"] not in ACTIVE_STATES or parent["withdraw_deadline"] is not None:
            return False
        api = api_factory()
        if question:
            if api.awaiting_reply(item["issue_id"]) is not False:
                mark_reply_label(ledger, item["id"])
            body = question_notice(ledger, item, body, configured_bot_name())
        if str(item["session_id"]).startswith("local-"):
            api.create_comment(item["issue_id"], body)
        else:
            api.create_activity(item["session_id"], {"type": "thought", "body": body})
        return True
    except Exception as exc:
        print(json.dumps({"warning": "task progress not posted", "error": type(exc).__name__}),
              file=sys.stderr, flush=True)
        try:
            ledger.note(item["id"], "task_progress_not_posted", details={"error": type(exc).__name__})
        except Exception:
            pass
        return False


def owned_action(ledger, item_id, action_id, token):
    """Only a live claim on the issue a comment speaks for may act on it.

    Scoped by the item's own issue, claimed input and generation instead of by the row's item id. That is
    the same authority — a running worker may speak for its issue at the input it claimed — and it is what
    the outbox key is built from, so a second item on an unchanged issue, which prepare_comment hands the
    row the first item prepared, can still drive post-comment. A row from another issue is still refused,
    and a confirmed row makes post_comment a no-op, so this cannot post anything twice.
    """
    ledger.renew(item_id, token)
    if ledger.item(item_id).get("parent_id"):
        raise LedgerError("only the general job may post or confirm outcome comments")
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
    # Old commands remain available to explicitly retained historical jobs;
    # they cannot recreate stages/subtasks or semantic gates on a new owner.
    from .execution import RETIRED_COMMANDS
    if c in RETIRED_COMMANDS and single(ledger.item(args.item)):
        raise LedgerError('this staged command is retired for single-worker jobs; continue in the current worker')
    if c in ('resource-status', 'unity-tools', 'unity-read', 'unity-tool', 'unity-test-result'):
        from . import worker_resources
        function = worker_resources.status if c == 'resource-status' else worker_resources.unity
        return function(args, ledger, load_config(secure_permissions=False), resolve_token(args))
    if c == 'cancel-resource':
        from .execution import cancel_queued_resource
        return cancel_queued_resource(ledger, args.item, resolve_token(args), args.reservation)
    if c == 'publish-ui':
        from .worker_resources import context
        from .worker_ui import export
        from .fgui_workflow import read_packages
        token = resolve_token(args)
        config = load_config(secure_permissions=False)
        _, paths = context(args, ledger, config, token)
        return export(ledger, args.item, token, config, api_factory,
                      read_packages(args.packages, paths.runs / args.item))
    if c == "document-reader-status":
        from .lark_cli import SKILLS
        from .lark_diagnostics import profile_status
        token = resolve_token(args)
        ledger.renew(args.item, token)
        if ledger.item(args.item)["skill"] not in SKILLS:
            raise LedgerError("document reader diagnostics require a document-reading claim")
        config = load_config(secure_permissions=False)
        if Path(args.db).resolve() != Paths(config).ledger.resolve():
            raise LedgerError("document reader diagnostics must use the configured host ledger")
        result = profile_status(config.lark_cli)
        ledger.renew(args.item, token)
        return result
    if c in ('prepare-go-dependencies', 'go-dependencies'):
        from .go_dependencies import request, status
        from .workflow import JobWorktrees
        from .worktrees import Worktrees
        token = resolve_token(args)
        ledger.renew(args.item, token)
        config = load_config(secure_permissions=False)
        paths = Paths(config)
        if Path(args.db).resolve() != paths.ledger.resolve():
            raise LedgerError('Go dependency request must use the configured host ledger')
        if not config.go_dependencies:
            raise LedgerError('Host Go dependency preparation is not configured')
        if c == 'go-dependencies':
            return status(ledger, config, args.item, args.request_id)
        trees = JobWorktrees(Worktrees(paths.repos, paths.worktrees, config.repos), ledger)
        return request(ledger, trees, config, args.item, token, args.module, args.commit, retry=args.retry,
                       repo=getattr(args, 'repo', None))
    if c == "memory-list":
        return ledger.memory_list(args.item, resolve_token(args))
    if c == 'shared-test-repairs':
        from .shared_test_repairs import discover
        return discover(args, ledger, load_config(secure_permissions=False), resolve_token(args))
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
        if args.save_token:
            from .worker_start import claim_to_file
            config = load_config(secure_permissions=False)
            if Path(args.db).resolve() != Paths(config).ledger.resolve():
                raise LedgerError('claim token storage must use the configured host ledger')
            result = claim_to_file(ledger, args.item, args.worker_id, args.save_token, config)
        else:
            result = ledger.claim(args.item, worker_id=args.worker_id)
        if args.context:
            from .resume_context import resume_view
            result['context'] = resume_view(ledger.issue_context(args.item))
        return result
    if c == "renew":
        return ledger.renew(args.item, resolve_token(args))
    if c == "retry-approval":
        from .launcher import _read_worker_text
        token = resolve_token(args)
        ledger.renew(args.item, token)  # authenticate before reading worker-supplied evidence
        return ledger.defer_approval_retry(args.item, token, _read_worker_text(Path(args.error_file)))
    if c == "maintenance-handoff":
        from .maintenance import Maintenance
        return Maintenance(ledger).handoff(args.item, resolve_token(args))
    if c in ('closing-preflight', 'evidence-index'):
        token = resolve_token(args)
        ledger.renew(args.item, token)
        item = ledger.item(args.item)
        refuse_withdrawn(item)
        ledger.require_valid_checkpoint(args.item, token)
        owned = ledger._owned(args.item, token)
        fingerprint = ledger._issue_row(owned['issue_id'])['fingerprint']
        changed = owned['requeue_requested'] or fingerprint != owned['claimed_fingerprint']
        if changed and (c == 'closing-preflight' or not args.verify):
            raise LedgerError('issue changed; revalidate before preparing closeout evidence')
        if c == 'closing-preflight':
            from .closing import validate_closing_inputs
            try:
                validate_closing_inputs(item['checkpoint'].get('plan') or {})
            except ValueError as exc:
                raise LedgerError(str(exc)) from exc
            return {'consumer_inputs_checked': True, 'completion_checked': False,
                    'next': 'Reuse the reports for required acceptance writeback; check-ready before delivery.'}
        from .evidence_index import create_index, verify_index
        config = load_config(secure_permissions=False)
        paths = Paths(config)
        if Path(args.db).resolve() != paths.ledger.resolve():
            raise LedgerError('evidence index must use the configured host ledger')
        state = paths.runs / args.item
        identity = {'id': item['id'], 'claimed_fingerprint': owned['claimed_fingerprint'],
                    'current_fingerprint': fingerprint, 'requires_issue_review': bool(changed)}
        try:
            return (verify_index(state, identity, args.verify) if args.verify else
                    create_index(state, identity, read_json(args.input)))
        except (ValueError, OSError) as exc:
            raise LedgerError(str(exc)) from exc
    if c == "checkpoint":
        progress = read_json(args.input)
        if isinstance(progress, dict):
            from .config import ROOT
            from .skills import load_skills
            item = ledger.item(args.item)
            skill = skill_for(item, load_skills(ROOT / "skills", replay_legacy=not single(item)).get(item["skill"]))
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
        if args.checkpoint:
            run(argparse.Namespace(**{**vars(args), 'command': 'checkpoint', 'input': args.checkpoint}),
                ledger, api_factory)
        item = ledger.item(args.item)
        refuse_withdrawn(item)
        # The item's own manifest decides its stages: any staged skill, to a repository in its writes.
        skill = skill_for(item, load_skills(ROOT / "skills", replay_legacy=not single(item)).get(item["skill"]))
        if skill is None or not skill.staged:
            raise LedgerError(f"repository handoff requires a staged skill; {item['skill']} is not staged on this host")
        if args.to not in skill.writes:
            raise LedgerError(f"repository handoff requires a configured {skill.name} repository")
        config = load_config(secure_permissions=False)
        if Path(args.db).resolve() != Paths(config).ledger.resolve():
            raise LedgerError("repository handoff must use the configured host ledger")
        if args.to not in config.repos:
            raise LedgerError(f"target repository is not configured for this {skill.name} worker")
        if item["skill"] == "fgui" and args.to == "Farm-Client":
            from .fgui_workflow import UiWorkflow
            plan = ledger.issue_context(args.item).get("plan") or {}
            receipt = (plan.get("ui") or {}).get("receipt_id")
            UiWorkflow(ledger, args.item, token, config, skill, api_factory).retained_export(receipt)
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
        context = ledger.issue_context(args.item)
        if args.view == 'resume':
            from .resume_context import resume_view
            return resume_view(context)
        return context
    if c == "pop-inbox":
        return ledger.pop_inbox(args.item, resolve_token(args))
    if c == "tasks":
        from .workflow import task_views
        return task_views(ledger, args.item)
    if c in ("delegate-task", "await-tasks", "answer-task", "retry-task"):
        from .workflow import await_tasks, delegate_task, forward_answer, retry_task
        token = resolve_token(args)
        ledger.renew(args.item, token)
        item = ledger.item(args.item)
        refuse_withdrawn(item)
        if item["skill"] != "work" or item.get("parent_id"):
            raise LedgerError("task coordination requires the general job's own claim")
        config = load_config(secure_permissions=False)
        if Path(args.db).resolve() != Paths(config).ledger.resolve() or "work" not in (config.enabled_skills or []):
            raise LedgerError("task coordination requires the configured general-worker host ledger")
        api = api_factory()
        status = api.issue_status(item["issue_id"])
        if (not api.app_user_id or status.get("delegate_id") != api.app_user_id or status["archived"]
                or status["status_type"] in TERMINAL_STATUS_TYPES):
            raise LedgerError("issue must remain open and delegated to FarmBot")
        if c == "delegate-task":
            request = read_json(args.input)
            needed = "farmgui" if isinstance(request, dict) and request.get("capability") == "fgui" else "Farm-Client"
            if needed not in config.repos:
                raise LedgerError("required presentation repository is not configured on this host")
            return delegate_task(ledger, args.item, token, request)
        if c == "answer-task":
            return forward_answer(ledger, args.item, token, args.task, args.message_id)
        if c == "retry-task":
            return retry_task(ledger, args.item, token, args.task, args.message_id)
        result = await_tasks(ledger, args.item, token)
        body = "工作进度已保存，正在等待本 issue 的界面/场景任务；问题和答复会保留在同一会话。"
        task_progress(ledger, lambda: api, result, body)
        return result
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
        if item.get("parent_id"):
            body = f"[{item['task_key']}] {read_text(args.body_file)}"
            api = api_factory()
            return (api.create_comment(item["issue_id"], body) if str(item["session_id"]).startswith("local-") else
                    api.create_activity(item["session_id"], {"type": "thought", "body": body}))
        return api_factory().create_activity(item["session_id"], {"type": args.type, "body": read_text(args.body_file)})
    if c == "resolve-question":
        return ledger.resolve_question(args.item, resolve_token(args), args.question_id, args.message_id)
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
        if item.get("parent_id"):
            view = ledger.await_input(args.item, token, args.question, reason=args.reason)
            task_progress(ledger, lambda: api, view, f"[{item['task_key']}] {args.question}",
                          question=args.reason == "question")
            return view
        if args.reason == "question":
            if api.awaiting_reply(item["issue_id"]) is not False:
                mark_reply_label(ledger, item["id"])
        body = (question_notice(ledger, item, args.question, configured_bot_name())
                if args.reason == "question" else args.question)
        content = ({"type": "thought", "body": f"[{item['task_key']}] {args.question}"}
                   if item.get("parent_id") else {"type": "elicitation", "body": body})
        if str(item["session_id"]).startswith("local-"):
            api.create_comment(item["issue_id"], content["body"])
        else:
            api.create_activity(item["session_id"], content)
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
    if c in ('verify-scene-assets', 'authorize-scene-scope', 'yield-presentation', 'resume-presentation'):
        token = resolve_token(args)
        config = load_config(secure_permissions=False)
        paths = Paths(config)
        if Path(args.db).resolve() != paths.ledger.resolve():
            raise LedgerError('scene checks must use the configured host ledger')
        from .scene_scope import authorize, verify_loading
        if c == 'verify-scene-assets':
            return verify_loading(ledger, args.item, token, config, api_factory)
        from .launcher import Launcher, RUNTIMES
        from .fgui_workflow import read_packages
        api = api_factory()
        issue = api.fetch_issue(ledger.item(args.item)['issue_id'])
        observed = ledger.observe_issue(issue)
        if (not observed['in_scope'] or issue.get('status_type') in TERMINAL_STATUS_TYPES
                or not api.app_user_id or issue.get('delegate_id') != api.app_user_id or issue.get('archived')):
            raise LedgerError('scene scope requires current delegation')
        if c in ('yield-presentation', 'resume-presentation'):
            from .presentation_repair import yield_client, resume
            from .workflow import JobWorktrees
            from .worktrees import Worktrees
            trees = JobWorktrees(Worktrees(paths.repos, paths.worktrees, config.repos), ledger)
            if c == 'yield-presentation':
                return yield_client(ledger, args.item, token, trees, config, read_packages(args.input, paths.runs / args.item))
            return resume(ledger, args.item, token, args.task, trees, config,
                          Launcher(paths.runs, RUNTIMES[config.runtime], config.host))
        return authorize(ledger, args.item, token, args.task, read_packages(args.input, paths.runs / args.item),
                         Launcher(paths.runs, RUNTIMES[config.runtime], config.host))
    if c in ("review-ui", "export-ui", "install-ui", "verify-ui", "verify-ui-loading", "verify-ui-guards", 'verify-ui-source',
             "ui-verification-status", "authorize-ui-guard-repair"):
        token = resolve_token(args)
        ledger.renew(args.item, token)
        config = load_config(secure_permissions=False)
        paths = Paths(config)
        if Path(args.db).resolve() != paths.ledger.resolve():
            raise LedgerError("UI export must use the configured host ledger")
        from .config import ROOT
        from .skills import load_skills
        from .fgui_workflow import UiWorkflow, read_packages
        item = ledger.item(args.item)
        skill = load_skills(ROOT / "skills", replay_legacy=not single(item)).get(item["skill"])
        source_stage = (c in ('review-ui', 'export-ui') or c == 'verify-ui-source'
                        and ledger.item(args.item)['root_repo'] in (None, 'farmgui'))
        workflow = UiWorkflow(ledger, args.item, token, config, skill, api_factory,
                              stage="farmgui" if source_stage else "Farm-Client",
                              receipt_id=getattr(args, 'receipt_id', None), consume=c == 'install-ui')
        if c == 'verify-ui-source':
            from .presentation_artifacts import verify_source_delivery
            from .fgui_records import export_record
            ui = ledger.item(args.item)['checkpoint'].get('plan', {}).get('ui', {})
            proof = export_record(ledger, args.item, ui.get('receipt_id'))
            return verify_source_delivery(workflow, [proof['source_pr']])
        if c == "authorize-ui-guard-repair":
            from .fgui_repair import authorize_repair
            return authorize_repair(workflow, args.receipt_id, read_packages(args.input, paths.runs / args.item))
        if c == "install-ui":
            from .fgui_client import install
            return install(workflow, args.receipt_id)
        if c == "verify-ui":
            from .fgui_scope import verify_candidate
            return verify_candidate(workflow, args.receipt_id, expected_commit=args.commit)
        if c == "verify-ui-loading":
            from .fgui_unity import verify_loading
            return verify_loading(workflow, args.receipt_id)
        if c == "verify-ui-guards":
            from .fgui_guards import verify_guards
            return verify_guards(workflow, args.receipt_id)
        if c == "ui-verification-status":
            from .fgui_delivery import verification_status
            return verification_status(workflow, args.receipt_id)
        if c == "review-ui":
            return workflow.review(packages=read_packages(args.packages, paths.runs / args.item),
                                   preview=args.preview, asset_url=args.asset_url, round=args.round)
        return workflow.export(review_id=args.review_id, preview=args.preview)
    if c == "upload-image":
        from .preview_upload import read_image
        token = resolve_token(args)
        ledger.renew(args.item, token)
        item = ledger.item(args.item)
        refuse_withdrawn(item)
        config = load_config(secure_permissions=False)
        paths = Paths(config)
        if Path(args.db).resolve() != paths.ledger.resolve():
            raise LedgerError("preview upload must use the configured host ledger")
        internal = item.get("parent_id") and "work" in (config.enabled_skills or [])
        scene = internal and item["skill"] == "scene" and item["root_repo"] in (None, "Farm-Client")
        if not single(item) and not scene and (item["skill"] != "fgui" or item["root_repo"] not in (None, "farmgui")):
            raise LedgerError("preview upload requires this job's farmgui authoring stage")
        if not single(item) and not internal and "fgui" not in (config.enabled_skills or []):
            raise LedgerError("preview upload requires explicit fgui enablement on this host")
        image = read_image(args.file, paths.runs / args.item)
        ledger.renew(args.item, token)
        refuse_withdrawn(ledger.item(args.item))
        api = api_factory()
        issue = api.fetch_issue(item["issue_id"])
        ledger.observe_issue(issue)
        if (not api.app_user_id or issue.get("delegate_id") != api.app_user_id or issue.get("archived")
                or issue.get("status_type") in TERMINAL_STATUS_TYPES):
            raise LedgerError("issue must remain open and delegated to FarmBot")
        def keepalive():
            ledger.renew(args.item, token)
            refuse_withdrawn(ledger.item(args.item))
        keepalive()
        result = api.upload_image(image, keepalive=keepalive)
        keepalive()
        if scene or single(item):
            with ledger._transaction():
                ledger._owned(args.item, token)
                ledger._audit(args.item, "scene_preview_uploaded", details=result)
        else:
            from .fgui_records import record_preview
            record_preview(ledger, args.item, token, image, result)
        return result
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
        from .worktrees import Worktrees
        token = resolve_token(args)
        ledger.renew(args.item, token)
        item = ledger.item(args.item)
        refuse_withdrawn(item)
        config = load_config(secure_permissions=False)
        paths = Paths(config)
        if Path(args.db).resolve() != paths.ledger.resolve():
            raise LedgerError("publication must use the configured host ledger")
        skills = load_skills(ROOT / 'skills', replay_legacy=not single(item))
        skill = skill_for(item, skills.get(item['skill']))
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
            from .workflow import JobWorktrees
            trees = JobWorktrees(Worktrees(paths.repos, paths.worktrees, config.repos), ledger)
            ui_scope = None
            if item['skill'] == 'fgui' and args.repo == 'Farm-Client':
                from .fgui_workflow import UiWorkflow
                from .fgui_scope import verify_candidate
                from .fgui_delivery import verification_record
                from .fgui_client import installation_record
                workflow = UiWorkflow(ledger, args.item, token, config, skill, api_factory, stage='Farm-Client')
                receipt = ((ledger.issue_context(args.item).get('plan') or {}).get('ui') or {}).get('receipt_id')
                ui_scope = verify_candidate(workflow, receipt)
                installed = installation_record(ledger, args.item, receipt)
                verification_record(workflow, 'fgui_guards_complete', 'guards_sha256', receipt, ui_scope, installed)
            result = PublicationVerifier(trees, issue_prefix=config.issue_prefix).verify(
                args.repo, args.item, issue['identifier'], suffix_roles=skill.initial_root is not None,
                require_main=ui_scope is None and not single(item))
            if ui_scope is not None:
                # Fresh UI scope already verifies the exact certified candidate
                # and its qualified base's ancestry of actual remote main.
                integration = ui_scope.get('main_integration')
                if (result['head'] != ui_scope['commit'] or not integration
                        or result['base_branch'] != integration['branch']):
                    raise LedgerError('UI publication differs from its qualified candidate and default branch')
                result['main_integration'] = integration
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
    if c == 'verification-progress':
        return ledger.verification_progress(args.item, resolve_token(args), args.phase)
    if c == 'compare-verification':
        from .verification import compare_recorded_pair
        return compare_recorded_pair(args, ledger, load_config(secure_permissions=False), resolve_token(args))
    if c == 'verify-candidate':
        from .verification import verify_recorded_candidate
        return verify_recorded_candidate(args, ledger, load_config(secure_permissions=False), resolve_token(args))
    if c == 'verification-closeout':
        from .verification_closeout import prepare
        return prepare(args, ledger, load_config(secure_permissions=False), resolve_token(args))
    if c in ("await-resource", 'continue-verification'):
        from .config import ROOT
        from .skills import load_skills
        token = resolve_token(args)
        ledger.renew(args.item, token)  # authenticate before reading a manifest or any checkout
        if args.checkpoint:
            run(argparse.Namespace(**{**vars(args), 'command': 'checkpoint', 'input': args.checkpoint}),
                ledger, api_factory)
        item = ledger.item(args.item)
        refuse_withdrawn(item)
        # The item's own manifest decides what it may wait for (P11), as it decides handoff-repository's stages.
        if single(item):
            from .worker_resources import request
            return request(args, ledger, load_config(secure_permissions=False), token)
        skill = skill_for(item, load_skills(ROOT / "skills", replay_legacy=not single(item)).get(item["skill"]))
        if skill is None:
            raise LedgerError(f"a resource request requires the item's skill; {item['skill']} is not loaded here")
        require_listed_resource(skill, args.resource)
        mode = args.mode
        if args.resource == "unity_slot":
            try:
                host_config = load_config(secure_permissions=False)
            except FileNotFoundError:
                host_config = None  # isolated legacy CLI fixtures supply no private host profile
            if host_config is not None and host_config.unity_pool:
                if Path(args.db).resolve() != Paths(host_config).ledger.resolve():
                    raise LedgerError("verification must use the configured host ledger")
                mode = "interactive"
        request_options = {}
        integration_file = getattr(args, 'integration_plan', None)
        if integration_file:
            from .integration import validate_plan
            if (args.resource!='unity_slot' or mode!='interactive' or args.baseline or not args.commit
                    or current_root(item['root_repo'], skill)!='Farm-Client'):
                raise LedgerError('integration requires an interactive Client candidate request')
            require_listed_resource(skill, 'server_slot')
            config = load_config(secure_permissions=False)
            if not config.server_slots or Path(args.db).resolve()!=Paths(config).ledger.resolve():
                raise LedgerError('integration requires this configured host server pool and ledger')
            from .resource_admission import validate_settings
            budget = validate_settings(config.resource_admission)
            if budget['unity_mib'] + budget['server_mib'] > budget['max_reserved_mib']:
                raise LedgerError('integration needs budget for one Unity Editor plus one full server lease')
            plan = validate_plan(read_json(integration_file))
            from .workflow import JobWorktrees
            from .worktrees import Worktrees
            paths = Paths(config)
            trees = JobWorktrees(Worktrees(paths.repos, paths.worktrees, config.repos), ledger)
            trees.verification_commit('farm-hive', args.item, plan['server_commit'])
            request_options['integration_plan'] = plan
        repair_source = getattr(args, 'repair_candidate_reservation', None)
        if repair_source is not None and (args.resource != 'unity_slot' or args.baseline or not args.commit
                or item['skill'] not in ('work', 'feature', 'fix') or c != 'await-resource'):
            raise LedgerError('--repair-candidate-reservation requires a Client candidate request')
        retained_candidate = getattr(args, 'reuse_candidate_reservation', None)
        resumed_baseline = getattr(args, 'resume_baseline_reservation', None)
        if resumed_baseline is not None and (not args.baseline or args.resource != 'unity_slot'
                or retained_candidate is not None or c != 'await-resource'):
            raise LedgerError('--resume-baseline-reservation requires --baseline without a new candidate binding')
        if retained_candidate is not None:
            if not args.baseline:
                raise LedgerError('--reuse-candidate-reservation requires --baseline')
            request_options['reuse_candidate_reservation'] = retained_candidate
        if args.server_test_plan:
            if args.resource != "server_slot":
                raise LedgerError("--server-test-plan requires server_slot")
            from .server_test_plan import validate_plan
            request_options["server_test_plan"] = validate_plan(read_json(args.server_test_plan))
        selected_commit = args.commit
        if args.baseline and (args.resource != "unity_slot" or args.commit is None
                              or item["skill"] not in ("work", "fix", "feature")):
            raise LedgerError("baseline comparison requires a work/fix/feature Unity request with --commit candidate")
        if args.commit is not None:
            from .worktrees import Worktrees
            server_request = args.resource == "server_slot"
            if server_request and (item["skill"] not in ("work", "fix", "feature") or args.mode != "batch"
                                   or current_root(item["root_repo"], skill) != "farm-hive"):
                raise LedgerError("server integration requires a farm-hive write stage")
            if not server_request and (item["skill"] not in WRITE_SKILLS or args.resource != "unity_slot"
                    or (item.get("target") or {}).get("repository") != "Farm-Client"
                    or current_root(item["root_repo"], skill) != "Farm-Client"):
                raise LedgerError("only a write worker may select a Farm-Client Unity verification commit")
            config = load_config(secure_permissions=False)
            paths = Paths(config)
            if Path(args.db).resolve() != paths.ledger.resolve():
                raise LedgerError("verification must use the configured host ledger")
            options = {}
            if server_request and not config.server_slots:
                raise LedgerError("this host has no configured server integration slots")
            if not server_request and item["skill"] in ("work", "feature", "fgui", "scene"):
                options["expected_branch"] = ledger.staged_client_branch(args.item, issue_prefix=config.issue_prefix)
                request_options["issue_prefix"] = config.issue_prefix
            if item["skill"] == "fgui":
                if mode != "interactive":
                    raise LedgerError("UI package loading requires an interactive Unity reservation")
                from .fgui_workflow import UiWorkflow
                from .fgui_scope import verify_candidate
                from .fgui_delivery import verification_record
                from .fgui_client import installation_record
                workflow = UiWorkflow(ledger, args.item, token, config, skill, api_factory, stage="Farm-Client")
                receipt = ((ledger.issue_context(args.item).get("plan") or {}).get("ui") or {}).get("receipt_id")
                scope = verify_candidate(workflow, receipt, expected_commit=args.commit)
                installed = installation_record(ledger, args.item, receipt)
                verification_record(workflow, "fgui_guards_complete", "guards_sha256", receipt, scope, installed)
                if scope.get("main_integration"):
                    options["verified_base"] = scope["main_integration"]["commit"]
            from .workflow import JobWorktrees
            trees = JobWorktrees(Worktrees(paths.repos, paths.worktrees, config.repos), ledger)
            if repair_source is not None:
                from .repair_verification import source_pair
                pair = source_pair(ledger, args.item, repair_source)
                delta = trees.verification_repair_delta('Farm-Client', args.item, args.commit,
                                                       pair['candidate_commit'], **options)
                options['verified_base'] = pair['baseline_commit']
                request_options['client_repair'] = {'source_reservation': repair_source,
                    'source_commit': pair['candidate_commit'], 'candidate_commit': args.commit,
                    'baseline_commit': pair['baseline_commit'], 'changed_paths': delta}
            if args.baseline:
                if resumed_baseline is not None:
                    from .baseline_resume import pinned_baseline
                    resumed = pinned_baseline(ledger, args.item, args.commit, resumed_baseline, mode=mode)
                    options['verified_base'] = resumed['baseline_commit']
                    request_options['resume_baseline_reservation'] = resumed_baseline
                    request_options['reuse_candidate_reservation'] = resumed['reuse_candidate_reservation']
                elif retained_candidate is not None:
                    # Authenticate and validate the binding before using its controller-recorded base.
                    ledger._validate_retained_candidate(item, retained_candidate, args.commit, mode)
                    pinned = ledger.candidate_verification_base(retained_candidate)
                    if pinned:
                        options['verified_base'] = pinned
                selected_commit = trees.verification_baseline(
                    "Farm-Client", args.item, args.commit, **options)
                if (resumed_baseline is None and
                        trees.verification_tree('Farm-Client', selected_commit) ==
                        trees.verification_tree('Farm-Client', args.commit)):
                    if ledger.client_verification_pair(args.item, args.commit):
                        raise LedgerError('this same-tree candidate already has a comparison; '
                                          'reuse its actual pair with compare-verification')
                    if retained_candidate is not None or ledger.client_candidate_round(args.item, args.commit):
                        raise LedgerError('baseline and candidate have the same file tree; use verify-candidate '
                                          'with the retained candidate report, diagnose gaps without rerunning baseline')
                    request_options['client_base_commit'] = selected_commit
                    selected_commit = args.commit
                else:
                    request_options["comparison_candidate"] = args.commit
            else:
                if not server_request and item['skill'] in ('work', 'fix', 'feature'):
                    if c == 'continue-verification':
                        resource_token = read_text(args.reservation_token_file).strip()
                        source = ledger.continuation_baseline(args.item, token, resource_token, args.commit)
                        options['verified_base'] = source['commit_sha']
                        request_options['continuation'] = (source['reservation_id'], resource_token)
                    else:
                        pair = ledger.client_verification_pair(args.item, args.commit)
                        if pair:
                            options['verified_base'] = pair['baseline_commit']
                        else:
                            candidate_round = ledger.client_candidate_round(args.item, args.commit)
                            if candidate_round:
                                options['verified_base'] = candidate_round['base_commit']
                if not server_request and item['skill'] in ('work', 'fix', 'feature'):
                    request_options['client_base_commit'] = trees.verification_baseline(
                        'Farm-Client', args.item, args.commit, **options)
                else:
                    trees.verification_commit("farm-hive" if server_request else "Farm-Client",
                                              args.item, args.commit, require_main=not server_request, **options)
                if server_request:
                    server_round = ledger.server_verification_round(args.item, args.commit)
                    request_options['server_base_commit'] = trees.server_verification_base(
                        'farm-hive', args.item, args.commit,
                        verified_base=server_round['base_commit'] if server_round else None)
                    from .server_test_plan import check_scope
                    if 'server_test_plan' not in request_options:
                        raise LedgerError('server verification requires --server-test-plan')
                    selection = check_scope(request_options['server_test_plan'], trees.server_changed_scope(
                        'farm-hive', args.item, args.commit, request_options['server_base_commit']),
                        getattr(args, 'server_scope_reason', None))
                    ledger.renew(args.item, token)
                    ledger.note(args.item, 'server_test_scope', 'selected from candidate diff', selection)
        elif (args.resource == "unity_slot" and item["skill"] == "fix"
                and current_root(item["root_repo"], skill) == "Farm-Client"
                and (item.get("target") or {}).get("repository") == "Farm-Client"):
            # Legacy fix requests may omit --commit; omission cannot bypass main synchronization.
            from .worktrees import Worktrees
            from .workflow import JobWorktrees
            config = load_config(secure_permissions=False)
            paths = Paths(config)
            if Path(args.db).resolve() != paths.ledger.resolve():
                raise LedgerError("verification must use the configured host ledger")
            JobWorktrees(Worktrees(paths.repos, paths.worktrees, config.repos), ledger).verification_commit(
                "Farm-Client", args.item, item["target"]["commit_sha"], require_main=True)
        # Ownership is checked again after Git validation, fencing a concurrent stop/closure.
        if c == 'continue-verification' and 'continuation' not in request_options:
            raise LedgerError('only Client code jobs may continue a comparison baseline')
        return ledger.await_resource(args.item, token, args.resource, mode, skill=skill,
                                     commit_sha=selected_commit, **request_options)
    if c == 'run-integration':
        from .integration_runtime import run_owned
        return run_owned(ledger, args, resolve_token(args))
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
            if reservation["kind"] == "server_slot":
                return {"state": "held", "reservation": held, "item": ledger.item(args.item),
                        "next_action": "checkpoint and park or fail, then exit; verified server slot recovery is required"}
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
        # When this read began: if it finds the delegation, the ledger records from then that the delegation is
        # back (silent-delegation design §3.1, P11).
        read_at = ledger.clock()
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
        from .config import ROOT
        from .skills import load_skills
        from .withdrawal import grace_seconds
        grace = grace_seconds(load_skills(ROOT / "skills", replay_legacy=not single(item)).get(item["skill"]))
        view, reason = ledger.withdraw(args.item, token, delegated=delegated, closed=closed, read_at=read_at,
                                       grace_seconds=grace)
        posted = post_closing_notice(ledger, lambda: api, view, reason) if view["state"] == "cancelled" else False
        return {**view, "reason": reason, "notice_posted": posted,
                "next_action": "exit; a later delegation continues this job from its checkpoint"}
    if c == "reservations":
        return ledger.reservations()
    if c == "slots":
        return ledger.slots()
    if c == "recover-slot":
        return ledger.recover_slot(args.slot, args.reason)
    if c == "finish":
        token = resolve_token(args)
        ledger.renew(args.item, token)
        if getattr(args, 'checkpoint', None):
            if args.check_input:
                raise LedgerError('--checkpoint is for --check-ready or actual finish, not shape-only --check-input')
            run(argparse.Namespace(**{**vars(args), 'command': 'checkpoint', 'input': args.checkpoint}),
                ledger, api_factory)
        evidence = read_json(args.input)
        if not isinstance(evidence, dict):
            raise LedgerError("finish input must be an object")
        check_pr_targets(evidence.get("prs"))
        item = ledger.item(args.item)
        ledger.validate_finish_input(args.item, token, args.outcome, evidence)
        if args.check_input:
            return {'input_valid': True, 'completion_checked': False,
                    'maintenance_requested': item['maintenance_requested'],
                    'next': 'Complete live gates and confirmed delivery, then run finish without --check-input.'}
        if item['skill'] == 'work' and args.outcome == 'delivered' and not single(item):
            from .presentation_artifacts import verify_parent_head
            from .workflow import task_views
            if any((t.get('evidence') or {}).get('ui_source_delivery_id') for t in task_views(ledger, args.item)):
                config = load_config(secure_permissions=False)
                if Path(args.db).resolve() != Paths(config).ledger.resolve():
                    raise LedgerError('parent integration delivery must use the configured host ledger')
                verify_parent_head(ledger, item, config)
        if args.check_ready:
            return {**ledger.finish(args.item, token, args.outcome, evidence, check_only=True),
                    'maintenance_requested': item['maintenance_requested']}
        if item["skill"] == "fgui" and args.outcome == "delivered" and not evidence.get("no_change"):
            from .fgui_workflow import UiWorkflow
            from .fgui_delivery import verify_delivery
            from .config import ROOT
            from .skills import load_skills
            config = load_config(secure_permissions=False)
            if Path(args.db).resolve() != Paths(config).ledger.resolve():
                raise LedgerError("UI delivery must use the configured host ledger")
            workflow = UiWorkflow(ledger, args.item, token, config, load_skills(ROOT / "skills", replay_legacy=True).get("fgui"),
                                  api_factory, stage='farmgui' if item['root_repo'] in (None, 'farmgui') else 'Farm-Client')
            from .presentation_contract import source_only
            if item.get('parent_id') and (source_only(item) or evidence.get('source_only') is True):
                from .presentation_artifacts import verify_source_delivery
                proof = verify_source_delivery(workflow, evidence.get('prs'))
                evidence.update(ui_source_delivery_id=proof['delivery_id'], receipt_id=proof['receipt_id'])
            else:
                evidence["ui_delivery_id"] = verify_delivery(workflow, evidence.get("prs"))["delivery_id"]
        if item["skill"] == "scene" and args.outcome == "delivered":
            from .scene_delivery import verify_delivery
            config = load_config(secure_permissions=False)
            if Path(args.db).resolve() != Paths(config).ledger.resolve():
                raise LedgerError("scene delivery must use the configured host ledger")
            evidence["scene_delivery_id"] = verify_delivery(ledger, args.item, token, config, api_factory, evidence)["delivery_id"]
        if item["skill"] == "fgui" and item.get("parent_id") and args.outcome == "delivered" and evidence.get("no_change"):
            from .fgui_delivery import verify_no_change
            config = load_config(secure_permissions=False)
            if Path(args.db).resolve() != Paths(config).ledger.resolve():
                raise LedgerError("UI no-change delivery must use the configured host ledger")
            evidence["ui_no_change_id"] = verify_no_change(ledger, args.item, token, config, api_factory,
                                                          evidence["no_change"])["delivery_id"]
        view = ledger.finish(args.item, token, args.outcome, evidence)
        if item.get("parent_id") and view["state"] in ("delivered", "blocked"):
            task_progress(ledger, api_factory, view,
                          f"[{item['task_key']}] {evidence['summary']}；本 issue 的整体验证和交付由主任务继续。")
        elif not item.get("parent_id"):
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
