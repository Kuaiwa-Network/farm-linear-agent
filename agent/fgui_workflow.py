"""Claim-fenced UI review/export orchestration over trusted helper boundaries.

The worker interprets actual human intent. This command path establishes owned
source, real uploaded-image and draft provenance, then anchors successful results
in controller state. It grants no authority when the host manifest lacks Phase E.
"""
import hashlib
import json
from pathlib import Path
import re
from uuid import uuid4

from .config import Paths
from .fgui_approval import export_authority, source_digest
from .fgui_publisher import publish
from .fgui_records import export_record, human_event_time, published_preview, published_round, record_export, record_review, review_record
from .fgui_source import changed_packages, owned_source
from .ledger import LedgerError, TERMINAL_STATUS_TYPES
from .preview_upload import _plain_path, read_image
from .publication import PublicationVerifier, github_api
from .uploads import _read_regular
from .worktrees import Worktrees


def _inputs(context):
    # Messages/comments arriving during a long hash/export operation need the
    # worker's current interpretation before it proceeds with old authority.
    messages = [*context.get("session_messages", [])]
    comments = [c for c in context["issue"].get("comments", []) if c.get("author_kind") == "human"]
    value = {"fingerprint": context["fingerprint"], "messages": messages, "comments": comments}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


class UiWorkflow:
    def __init__(self, ledger, item_id, token, config, skill, api_factory, *, stage="farmgui"):
        self.ledger, self.item_id, self.token = ledger, item_id, token
        self.config, self.paths, self.api_factory = config, Paths(config), api_factory
        ledger.renew(item_id, token)
        item = ledger.item(item_id)
        if stage not in ("farmgui", "Farm-Client"):
            raise LedgerError("UI workflow stage is invalid")
        self.stage = stage
        roots = (None, "farmgui") if stage == "farmgui" else ("Farm-Client",)
        if (item["skill"] != "fgui" or item["root_repo"] not in roots
                or "fgui" not in (config.enabled_skills or []) or not skill or skill.name != "fgui"
                or "Farm-Client" not in skill.writes or "unity_slot" not in skill.resources):
            raise LedgerError("UI export requires the explicitly enabled Phase E farmgui stage")
        if not (ledger.session(item["session_id"]) or {}).get("delegation"):
            raise LedgerError("UI export requires this delegated job")
        self.trees = Worktrees(self.paths.repos, self.paths.worktrees, config.repos)
        self.initial_inputs = None
        self.fence(refresh=True)
        self.initial_inputs = _inputs(ledger.issue_context(item_id))

    def _stage(self, stage):
        if self.stage != stage:
            raise LedgerError("UI command requires its matching repository-rooted stage")

    def fence(self, *, refresh=False):
        self.ledger.renew(self.item_id, self.token)
        item = self.ledger.item(self.item_id)
        self.ledger._refuse_withdrawn(self.ledger._row(self.item_id))
        if refresh:
            api = self.api_factory(); issue = api.fetch_issue(item["issue_id"])
            self.ledger.observe_issue(issue)
            if (not api.app_user_id or issue.get("delegate_id") != api.app_user_id or issue.get("archived")
                    or issue.get("status_type") in TERMINAL_STATUS_TYPES):
                raise LedgerError("issue must remain open and delegated to FarmBot")
            self.bot_id = api.app_user_id
        if self.initial_inputs is not None and _inputs(self.ledger.issue_context(self.item_id)) != self.initial_inputs:
            raise LedgerError("new UI input requires rereading actual human requests before export")

    def _source(self, packages, *, expected_head=None, expected_pr=None):
        context = self.ledger.issue_context(self.item_id); plan = context.get("plan") or {}
        entries = (plan.get("prs") or {}).get("farmgui", [])
        entries = [entry for entry in entries if isinstance(entry, dict) and entry.get("role") == "issue"]
        if len(entries) != 1 or not isinstance(entries[0].get("pr"), dict):
            raise LedgerError("UI review requires this job's registered farmgui issue draft")
        entry = entries[0]; url = entry["pr"].get("url")
        verified = PublicationVerifier(self.trees, issue_prefix=self.config.issue_prefix).verify(
            "farmgui", self.item_id, context["issue"]["identifier"], entry.get("branch"), suffix_roles=True)
        verifier = PublicationVerifier(self.trees, issue_prefix=self.config.issue_prefix)
        verifier.verify_pr("farmgui", self.item_id, context["issue"]["identifier"], url)
        head = verified["head"]
        if (entry.get("head") != head or (expected_head and expected_head != head)
                or (expected_pr and expected_pr != url)):
            raise LedgerError("UI source draft differs from its retained reviewed HEAD")
        metadata = github_api("repos/" + verified["repository"] + "/git/ref/heads/" + verified["base_branch"])
        main = (metadata.get("object") or {}).get("sha") if isinstance(metadata, dict) else None
        if not isinstance(main, str) or not re.fullmatch(r"[0-9a-f]{40}", main):
            raise LedgerError("UI review requires current verified main identity")
        changed = changed_packages(self.trees, self.item_id, entry["branch"], head=head, base=main)
        if not isinstance(packages, dict) or any(packages.get(n) != i for n, i in changed["packages"].items()):
            raise LedgerError("UI export selection must include every actually changed package")
        identity = owned_source(self.trees, self.item_id, entry["branch"], packages, expected_head=head)
        self.fence()
        return identity, changed, url, entry["branch"]

    def review(self, *, packages, preview, asset_url, round):
        self._stage("farmgui")
        if type(round) is not int or not 0 < round <= 10000:
            raise LedgerError("UI review requires a positive bounded numbered round")
        image = read_image(preview, self.paths.runs / self.item_id)
        if image.content_type != "image/png":
            raise LedgerError("UI export review requires a retained PNG")
        digest = image.evidence()["sha256"]
        published_preview(self.ledger, self.item_id, digest, asset_url, before=self.ledger.clock())
        source, changed, url, _ = self._source(packages)
        self.fence(refresh=True)
        return record_review(self.ledger, self.item_id, self.token, {
            "format": "farmbot-fgui-review-v1", "head": source["head"], "source_digest": source_digest(source["files"]),
            "preview_sha256": digest, "asset_url": asset_url, "round": round,
            "changed_packages": changed["packages"], "selected_packages": dict(packages),
            "source_pr": url, "main": changed["main"], "other_source_changes": changed["other_source_changes"]})

    def export(self, *, review_id, preview):
        self._stage("farmgui")
        if not self.config.fgui_export:
            raise LedgerError("native UI publisher must be explicitly configured on this host")
        review = review_record(self.ledger, self.item_id, review_id)
        image = read_image(preview, self.paths.runs / self.item_id)
        if image.content_type != "image/png" or image.evidence()["sha256"] != review["preview_sha256"]:
            raise LedgerError("retained UI review image differs from its controller record")
        source, changed, url, branch = self._source(review["selected_packages"],
            expected_head=review["head"], expected_pr=review["source_pr"])
        if source_digest(source["files"]) != review["source_digest"] or changed["packages"] != review["changed_packages"]:
            raise LedgerError("UI source or changed package scope differs from the reviewed round")
        accepted = self._authority(review, source, changed); identity = uuid4().hex
        run = self.paths.runs / self.item_id / "ui-exports" / identity
        # Publisher creates the fresh run itself. Only its ancestor is prepared;
        # worker-controlled symlinks/overlap are refused by publisher validation.
        _plain_path(run.parent)
        run.parent.mkdir(parents=True, exist_ok=True)
        _plain_path(run.parent)
        block = self.config.fgui_export
        result = publish(self.paths.worktrees / self.item_id / "farmgui", review["selected_packages"],
            block["executable"], block["tool_sha256"], run, fence=self.fence,
            verify_source=lambda: owned_source(self.trees, self.item_id, branch, review["selected_packages"], expected_head=source["head"]),
            timeout=block.get("timeout_seconds", 180))
        self.fence(refresh=True)
        latest, diff, _, _ = self._source(review["selected_packages"], expected_head=source["head"], expected_pr=url)
        if latest != source or diff["packages"] != changed["packages"] or self._authority(review, source, changed) != accepted:
            raise LedgerError("UI source or authority changed during export; preserve staging and request a fresh round")
        return record_export(self.ledger, self.item_id, self.token, identity, accepted, result, source_pr=url, main=diff["main"])

    def _authority(self, review, source, changed):
        context = self.ledger.issue_context(self.item_id); plan = context.get("plan") or {}
        if (plan.get("ui") or {}).get("review_id") != review["review_id"]:
            raise LedgerError("UI export requires this checkpoint's controller review identity")
        value = export_authority(plan, context, head=source["head"], files=source["files"],
            preview_sha256=review["preview_sha256"], changed_packages=changed["packages"], bot_id=self.bot_id)
        when = human_event_time(self.ledger, self.item_id, dict(value.approval))
        published_preview(self.ledger, self.item_id, review["preview_sha256"], review["asset_url"], before=when)
        published_round(self.ledger, self.item_id, review, before=when)
        return value

    def retained_export(self, receipt_id):
        proof = export_record(self.ledger, self.item_id, receipt_id)
        context = self.ledger.issue_context(self.item_id); ui = (context.get("plan") or {}).get("ui") or {}
        if ui.get("receipt_id") != receipt_id:
            raise LedgerError("UI Client entry requires this checkpoint's actual controller export")
        review = review_record(self.ledger, self.item_id, ui.get("review_id"))
        source, changed, url, _ = self._source(review["selected_packages"],
            expected_head=proof["authority"]["head"], expected_pr=proof["source_pr"])
        if (source_digest(source["files"]) != proof["authority"]["source_digest"]
                or self._authority(review, source, changed).evidence() != proof["authority"]):
            raise LedgerError("UI source or authority differs from the controller export")
        self.fence(refresh=True)
        return proof


def read_packages(path, state_root):
    """Bounded worker input inside its own private state; no arbitrary config read."""
    raw, root = Path(path), Path(state_root)
    _plain_path(raw); _plain_path(root)
    if raw == root or not raw.resolve().is_relative_to(root.resolve()):
        raise LedgerError("UI package selection must be inside this job's private state")
    try:
        def unique(values):
            result = {}
            for key, value in values:
                if key in result: raise LedgerError("UI package selection contains duplicate names")
                result[key] = value
            return result
        before = raw.stat()
        if before.st_nlink != 1:
            raise LedgerError("UI package selection must not be hardlinked")
        value = json.loads(_read_regular(raw, 64 << 10).decode("utf-8"), object_pairs_hook=unique)
        after = raw.stat()
        if after.st_nlink != 1 or (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
            raise LedgerError("UI package selection changed while being read")
    except LedgerError:
        raise
    except (OSError, UnicodeError, ValueError):
        raise LedgerError("UI package selection must be bounded ordinary UTF-8 JSON") from None
    if not isinstance(value, dict): raise LedgerError("UI package selection must map package names to actual IDs")
    return value
