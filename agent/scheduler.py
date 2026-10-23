"""Turn queued work items into running workers and reap them back into ledger states (spec §6, §8)."""
import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .config import LARK_CLI_SKILLS
from .dispatch import dispatch_message
from .execution import single, skill_for
from .launcher import _read_worker_text
from .ledger import ACTIVE_STATES, LedgerError, plan_issue_branches
from .maintenance import Maintenance
from .startup_recovery import StartupRecovery
from .kw_ops import SERVER as KW_OPS_SERVER, resolve as resolve_kw_ops
from .lark_cli import tools as lark_tools
from .memory import publish_snapshot
from .publication import SUFFIXES, issue_branch, suffix_of
from .stages import COORDINATION_ROOT, current_root, runtime_can_launch, write_repositories
from .withdrawal import NOTICE_REASONS, notice as withdrawal_notice
from .worktrees import VerificationMismatch
from .workflow import (JobWorktrees, client_ready, pin_source_entry, repository_busy, repository_owner,
                       prepare_coordination, source_recovery, task_views, wake_parent)

TERMINAL = ("delivered", "blocked", "cancelled", "failed")
WAITING = ("awaiting_input", "awaiting_resource")
READ_REPO = "Farm-Client"
DEFAULT_CODEX_MODEL_SETTINGS = {"model": "latest-sol", "reasoning_effort": "xhigh"}


class TaskDeliveryMismatch(LedgerError):
    """Trusted delivery evidence no longer binds the retired task's candidate."""


class Scheduler:
    def __init__(self, ledger, launcher, skills, worktrees, *, skill_root, db_path, runtime_name, host,
                 max_concurrent=2, guidance_for=lambda item: "", claim_timeout=600, api=None,
                 slot_entries=None, preflight=None, control_ledger_factory=None, publication=None, codex_workers=None,
                 config_path=None, issue_prefix='FARM', bot_name='FarmBot', kw_ops=None, enabled_skills=None,
                 lark_cli=None, default_server_environment="公共测试服", server_slots_root=None, go_dependencies=None,
                 unity_pool=None, replay_legacy=False):
        self.replay_legacy = replay_legacy
        self.publication = publication
        self.preflight = preflight
        self.control_ledger_factory = control_ledger_factory
        self.api = api
        self.ledger = ledger
        self.launcher = launcher
        self.skills = skills
        # Pure branch-policy callers may supply no ledger; execution always has
        # one and resolves presentation ownership only through its trusted rows.
        self.worktrees = JobWorktrees(worktrees, ledger) if ledger is not None else worktrees
        self.skill_root = skill_root
        self.db_path = db_path
        self.runtime_name = runtime_name
        self.host = host
        self.max_concurrent = max_concurrent
        self.codex_workers = codex_workers or {}
        self.config_path = config_path
        self.issue_prefix = issue_prefix
        self.bot_name = bot_name
        self.kw_ops_config = dict(kw_ops or {})
        self.lark_cli = dict(lark_cli or {})
        self.default_server_environment = default_server_environment
        self.server_slots_root = server_slots_root
        self.go_dependencies = go_dependencies or {}
        self.unity_pool = unity_pool or {}
        # The loaded skills this host runs (spec §9.11); tick() refuses a queued item of any other loaded skill.
        # Without a set, every loaded skill but the opt-in ones, as skills.enabled_skills decides (P1).
        self.enabled_skills = ({name for name, skill in (skills or {}).items() if not skill.opt_in}
                               if enabled_skills is None else set(enabled_skills))
        self.guidance_for = guidance_for
        self.claim_timeout = claim_timeout
        # {slot_id: entry}, the same entries service.build hands the pool. The only thing read out of them
        # here is the -buildTarget spelling a worker is told about; the Editor itself is the pool's to run.
        self.slot_entries = slot_entries or {}
        self.active = {}
        self.lock = threading.RLock()
        self.maintenance_busy = lambda: ()
        self.maintenance_epoch = None
        self.maintenance_resources = None

    def _branch(self, issue, *, suffixes=()):
        canonical = issue_branch(issue.get('identifier'), self.issue_prefix)
        name = issue.get("branch_name") or canonical
        name = re.sub(r"[^A-Za-z0-9._/一-鿿-]+", "-", name).strip("-/")
        if suffix_of(name, canonical) in suffixes:
            return canonical  # a job with an initial root keeps these names for its suffix branches (P12)
        return name if name == canonical or name.startswith(canonical + '-') else canonical

    def _worktrees_for(self, skill, item, issue):
        paths = {}
        if single(item):
            branch = self._branch(issue)
            previous, seen = item.get('predecessor_id'), {item['id']}
            recovery = []
            while previous and previous not in seen:
                seen.add(previous)
                prior = self.ledger.item(previous)
                if prior['issue_id'] != item['issue_id']:
                    raise LedgerError('recovery predecessor belongs to a different issue')
                if not (self.ledger.cleanup_record(previous) or {}).get('done'):
                    raise LedgerError('previous worker cleanup is not certified')
                recovery.append(previous)
                previous = prior.get('predecessor_id')
            for repo in skill.writes:
                paths[repo] = self.worktrees.add(repo, item['id'], branch,
                                                refresh=not bool(item['publication_retries'] or item['capacity_retries'] or item['startup_retries']),
                                                recovery_from=recovery)
            return paths
        if item.get('root_repo') == COORDINATION_ROOT and skill.name == 'work':
            return paths  # Message routing must not fetch or alter retained candidates.
        if skill.writes:
            branch = self._branch(issue, suffixes=SUFFIXES if skill.initial_root else ())
            # P4: a job with an initial root goes back to the issue branch its plan, or its nearest predecessor's,
            # records for a repository (spec §5.7). fix has no initial root and keeps today's branches.
            recorded = self._recorded_branches(item) if skill.initial_root else {}
            if skill.name == "fgui":
                # A retained older UI run may own the canonical branch. Carry the
                # validated source branch into first Client entry; an existing
                # controller-selected Client target below remains authoritative.
                branch = recorded.get(skill.initial_root, branch)
            for repo in skill.writes:
                options = {"refresh": False} if item["publication_retries"] or item["capacity_retries"] else {}
                if skill.name in ("work", "feature", "fgui", "scene") and repo == "Farm-Client":
                    if (skill.name == "work" or item.get("parent_id")) and current_root(item.get("root_repo"), skill) != repo:
                        continue
                    target = item.get("target")
                    if (current_root(item.get("root_repo"), skill) != repo
                            and not (target or {}).get("issue_branch")):
                        continue
                    if target is None:
                        if current_root(item.get("root_repo"), skill) != repo:
                            continue  # main may advance during server work; the client stage has not begun
                        owner = self.ledger.item(repository_owner(item))
                        shared = owner.get("target")
                        if shared and shared.get("issue_branch"):
                            baseline = shared["commit_sha"]
                        elif (skill.name == "work" or item.get("parent_id")) and repo not in recorded:
                            baseline, branch = self.worktrees.stage_start(repo, item["id"], branch)
                        else:
                            baseline = self.worktrees.stage_base(repo, item["id"], branch)
                        selected = shared if shared and shared.get("issue_branch") else {
                            "repository": repo, "requested_ref": "default", "commit_sha": baseline,
                            "server_environment": self.default_server_environment,
                            "selected_at": datetime.now(timezone.utc).isoformat()}
                        item = self.ledger.pin_staged_client(item["id"], item["generation"], selected,
                            branch=selected.get("issue_branch", branch), issue_prefix=self.issue_prefix)
                        target = item["target"]
                    saved_branch = target.get("issue_branch")
                    if (target.get("repository") != repo or not saved_branch
                            or repo in recorded and recorded[repo] != saved_branch):
                        raise LedgerError("client checkout must retain its controller-selected baseline and branch")
                    # Reapply namespace/suffix checks to controller metadata, as for a saved plan.
                    plan_issue_branches({"prs": {repo: [{"role": "issue", "branch": saved_branch}]}},
                                        issue["identifier"], self.issue_prefix, reserved=True)
                    if repo in recorded:
                        options["recorded"] = True
                    paths[repo] = self.worktrees.add_stage(repo, item["id"], saved_branch,
                                                         target["commit_sha"], **options)
                    if item.get("parent_id") and not item.get("task_client_base"):
                        head = self.worktrees.head(paths[repo])
                        self.worktrees.verification_commit(repo, item["id"], head, expected_branch=saved_branch)
                        item = self.ledger.pin_task_client_base(item["id"], item["generation"], head)
                    continue
                retained_source = (source_recovery(self.ledger, item["id"], issue_prefix=self.issue_prefix)
                                   if repo == "farmgui" and skill.name == "fgui" and item.get("parent_id") else None)
                if retained_source and retained_source["branch"]:
                    paths[repo] = self.worktrees.add(repo, item["id"], retained_source["branch"], attach=True, **options)
                elif repo in recorded:
                    paths[repo] = self.worktrees.add(repo, item["id"], recorded[repo], attach=True, **options)
                else:
                    paths[repo] = self.worktrees.add(repo, item["id"], branch, **options)
                if repo == "farmgui" and skill.name == "fgui" and item.get("parent_id"):
                    pin_source_entry(self.ledger, item["id"], item["generation"], self.worktrees.head(paths[repo]),
                                     self.worktrees.git_in(paths[repo], "branch", "--show-current"),
                                     issue_prefix=self.issue_prefix,
                                     legacy_base=lambda: self.worktrees.resolve_commit("farmgui"))
        else:
            paths[READ_REPO] = self.worktrees.add_detached(READ_REPO, item["id"])
        return paths

    def _recorded_branches(self, item):
        """The issue branches the job's plan records (Ledger.recorded_branches), checked in this host's issue
        namespace as the checkpoint that saved them was (P9): a name a worker wrote chooses a checkout only when it
        is this issue's own FarmBot branch."""
        return self.ledger.recorded_branches(item["id"], issue_prefix=self.issue_prefix)

    def _reads_for(self, skill, item):
        """{repo: path} of the read-only default-branch checkouts the manifest's `reads` names (spec §9.6), made or
        refreshed for this launch. Capacity/publication retries reuse them without a fetch, like worktrees."""
        repositories = list(skill.reads)
        if item.get("parent_id") and "Farm-Contract" not in repositories:
            # The parent may have settled an unmerged OpenSpec. This detached
            # checkout borrows its clone's objects, so read-only Git can inspect
            # that recorded commit without changing the default-branch checkout.
            repositories.append("Farm-Contract")
        return {repo: self.worktrees.read_checkout(repo, item["id"],
                    refresh=not bool(item["publication_retries"] or item["capacity_retries"] or item['startup_retries']))
                for repo in repositories}

    def _reservation_stops_launch(self, item):
        """(the item's active reservation or None, whether it stops this launch): one granted to an earlier
        generation of the item, or on a slot held for recovery, is not this attempt's to use."""
        reservation = self.ledger.active_reservation(item["id"])
        from .integration import get as integration_request
        combined = integration_request(self.ledger, reservation['reservation_id']) if reservation else None
        stops = reservation is not None and (reservation["generation"] != item["generation"]
                                             or self.ledger.slot(reservation["resource"])["state"] == "held"
                                             or combined and combined['server_slot'] and
                                             self.ledger.slot(combined['server_slot'])['state']=='held')
        return reservation, stops

    @staticmethod
    def _batch_result(state_dir):
        """What the pool's own run left behind. Missing is itself a verification gap the worker must report,
        so it is represented rather than dropped — a batch worker with no `batch_result` key at all would
        read as "no slot" instead of "no evidence"."""
        try:
            return json.loads(_read_worker_text(Path(state_dir) / "unity-batch.json"))
        except (OSError, ValueError):
            return {"state": "gap", "exit_code": None, "results_file": None,
                    "result": "the pool recorded no batch run for this reservation"}

    def launch(self, item):
        if item['skill'] != 'chat' and not single(item) and not self.replay_legacy:
            self._hold_legacy(item)
            return None
        if not Maintenance(self.ledger).may_launch(item['id']):
            return None
        if not single(item):
            prepare_coordination(self.ledger, item['id'], self.launcher, self.active)
        current = self.ledger.item(item["id"])
        if (current["state"] != "queued" or current["retry_not_before"] > self.ledger.clock()
                or StartupRecovery(self.ledger).pending(item['id'])
                or not StartupRecovery(self.ledger).may_launch(self.host)):
            return None
        item = current  # the queued root/generation may have changed since the scheduler read its queue
        if item['capacity_retries'] or item['startup_retries']:
            # A voluntary approval retry has retired its claim while its process
            # may still be exiting. This also fences restart without live handles.
            try:
                self.launcher.assert_quiescent(item['id'], self.ledger.last_worker_pid(item['id']))
            except Exception:
                return None
        skill = skill_for(item, self.skills[item["skill"]])
        # Only Codex's workspace-write sandbox holds a staged attempt to its one writable root.
        if not runtime_can_launch(skill, self.runtime_name):
            raise RuntimeError(f"repository-staged {skill.name} requires the Codex workspace-write sandbox")
        root = current_root(item.get("root_repo"), skill)
        if not single(item) and (skill.name == "work" or item.get("parent_id")):
            if repository_busy(self.ledger, item, self.skills, self.active, self.launcher):
                return None
            if root == "Farm-Client":
                try:
                    client_ready(self.ledger, self.ledger._row(item["id"]))
                except LedgerError:
                    from .presentation_contract import dependencies_ready
                    if item.get('parent_id') and not dependencies_ready(self.ledger, item):
                        if item['stage'] != 'waiting-presentation-dependency':
                            with self.ledger._transaction():
                                self.ledger._set_state(item['id'], 'queued', 'waiting for declared presentation artifacts and installation',
                                                      stage='waiting-presentation-dependency')
                    return None
        write_repos = write_repositories(item, skill)
        issue = self.ledger.issue(item["issue_id"])
        # Asked again below, after the checkouts. Asked first so a launch that will stop there fetches nothing:
        # a held slot keeps its reservation active until recovery, and every tick would otherwise refresh each of
        # the job's repositories from origin and throw the result away.
        if self._reservation_stops_launch(current)[1]:
            return None
        # Preparing one job's checkouts fetched the same clone again moments after the last fetch (stage_base,
        # then add_stage; a new clone, then add). A checkout that fetched before still fetches its clone, once.
        with self.worktrees.one_fetch_per_clone():
            paths = self._worktrees_for(skill, item, issue)
            # Include read-only copies in this launch's fetch scope; a repository
            # used by both views needs one fresh fetch, not another network trip.
            # Client preparation may persist this item's baseline, never the session target.
            item = self.ledger.item(item["id"])
            if (item["state"] != "queued" or item["generation"] != current["generation"]
                    or item["root_repo"] != current["root_repo"] or item["next_root_repo"] is not None):
                return None
            reads = ({} if item.get('root_repo') == COORDINATION_ROOT else self._reads_for(skill, item))
        source_roots = [*(paths[repo] for repo in write_repos),
                        *(part for repo in write_repos for part in self.worktrees.writable_parts(repo, paths[repo]))]
        repo_root = Path(self.skill_root).parent
        # Enforcement is tool injection (spec §7): what a worker can reach is decided here, never from a
        # repository-local .codex/config.toml. A reservation-bound server such as the Unity MCP is decided
        # from the reservation the worker actually holds, and never from the skill manifest — which would
        # give every fix worker the Unity MCP whether or not it holds a slot. The isolated home alone does
        # not make that repository file inert: codex exec trusts an undecided cwd and then loads it, so
        # Launcher.spawn records the cwd as untrusted in that home.
        reservation, stops = self._reservation_stops_launch(item)
        if stops:
            return None
        servers, resource = {}, None
        if reservation is not None and reservation["kind"] in skill.resources:
            slot = self.ledger.slot(reservation["resource"])
            entry = self.slot_entries.get(slot["slot_id"], {})
            state_dir = self.launcher.state_dir(item["id"])
            resource = {"kind": reservation["kind"], "mode": reservation["mode"], "slot": slot["slot_id"],
                        "folder": slot["folder"], "instance": slot["instance"], "account": slot["account"],
                        "mcp_address": slot["mcp_address"], "commit": reservation["commit_sha"],
                        "token_file": str(Path(state_dir) / "reservation.token"),
                        # No `unity` key. A worker that may never start the Editor has no use for its path,
                        # and handing it one would be an instruction the AUTHORITY block then has to argue
                        # against. Resolving the binary is the pool's job, in run_batch and open_editor.
                        "build_target": entry.get("build_target_argument"),
                        # The outcome of the run the POOL already performed through the launcher, outside
                        # this worker's sandbox — never an argv for the worker to execute. Unity cannot run
                        # inside sandbox_workspace_write at all: it hangs on a denied Mach lookup with no
                        # file-permission denial to fix (Task 0's addendum). None on an interactive slot,
                        # where run_tests over MCP is the verify path (Task 0 Step 5).
                        "batch_result": (self._batch_result(state_dir)
                                         if reservation["mode"] == "batch" else None),
                        "results_dir": str(state_dir)}
            comparison = self.ledger.reservation_comparison(reservation["reservation_id"])
            if comparison:
                resource.update(comparison)
            elif reservation['kind'] == 'unity_slot':
                base = self.ledger.candidate_verification_base(reservation['reservation_id'])
                if base and not self.ledger.client_verification_pair(item['id'], reservation['commit_sha']):
                    resource.update(verification_role='candidate', source_base_commit=base,
                                    verification_policy='candidate-first')
            if reservation["kind"] == "server_slot":
                # The controller journal is outside worker write roots. Reconstruct endpoints from it,
                # never from a worker-writable result file or inherited production environment.
                from .server_slots import read_resource
                resource = {**read_resource(slot, reservation, self.server_slots_root),
                            "token_file": str(Path(state_dir) / "reservation.token")}
                self.worktrees.verification_commit("farm-hive", item["id"], reservation["commit_sha"])
            if reservation["kind"] == "unity_slot" and reservation["mode"] == "interactive":
                from .integration import companion
                server_reservation = companion(self.ledger, reservation)
                if server_reservation:
                    from .server_slots import read_resource
                    server_slot = self.ledger.slot(server_reservation['resource'])
                    server_resource = read_resource(server_slot, server_reservation, self.server_slots_root)
                    self.worktrees.verification_commit('farm-hive', item['id'], server_reservation['commit_sha'])
                    snapshot = self.worktrees.read_candidate('farm-hive', item['id'], server_reservation['commit_sha'])
                    reads['farm-hive'] = snapshot
                    resource['integration'] = {**server_resource, 'plan':server_reservation['integration']['plan'],
                                               'source':str(snapshot)}
                # The server exists for this worker only while the reservation is held, and the worker pins
                # its own MCP session with set_active_instance because HTTP selection is per session. The
                # shape is the runtime's, not one guess for both: write_mcp_config's JSON writer emits
                # mcpServers, where an entry without a type is not an HTTP server at all.
                servers["unity"] = ({"url": slot["mcp_address"]}
                                    if self.launcher.runtime.mcp_format == "toml"
                                    else {"type": "http", "url": slot["mcp_address"]})
        # A standing grant, unlike the Unity MCP: the manifest's `mcp` names it (spec §5) and the host profile
        # says where it is. The token stays in the controller's environment, and a worker without the server
        # keeps none of it.
        kw_ops_grant = resolve_kw_ops(skill.mcp, self.kw_ops_config, self.runtime_name, os.environ)
        if kw_ops_grant.server is not None:
            servers[KW_OPS_SERVER] = kw_ops_grant.server
        tools = {KW_OPS_SERVER: kw_ops_grant.tools} if kw_ops_grant.tools is not None else {}
        if self.unity_pool:
            tools["unity_pool"] = {"mode": "interactive", "editors": self.unity_pool["editors"],
                                   "tests": "worker MCP run_tests/get_test_job", "batch": "disabled"}
        if single(item) or skill.name in ("work", "fix", "feature") and root == "farm-hive":
            tools["server_tests"] = {"status": "configured" if self.server_slots_root else "unavailable",
                                     "resource": "server_slot"}
        if single(item) or skill.name in ('work','fix','feature') and root=='Farm-Client':
            tools['client_server_tests'] = {'status':'configured' if self.server_slots_root else 'unavailable',
                                             'request':'await-resource --resource unity_slot --mode interactive --integration-plan'}
        if skill.name in ('work', 'feature', 'fix') and root in self.go_dependencies.get('modules', {}):
            tools['go_dependencies'] = {'status':'configured', 'modules':self.go_dependencies['modules'][root],
                                        'cache_root':self.go_dependencies['cache_root'],
                                        'go':self.go_dependencies['go'],
                                        'request':'prepare-go-dependencies', 'poll':'go-dependencies'}
        if single(item) and self.go_dependencies:
            tools['go_dependencies'] = {'status': 'configured', 'modules': self.go_dependencies['modules'],
                'cache_root': self.go_dependencies['cache_root'], 'go': self.go_dependencies['go'],
                'request': 'prepare-go-dependencies --repo REPOSITORY', 'poll': 'go-dependencies'}
        if single(item) or skill.name in ("work", "feature") and root == "Farm-Client":
            # A configured slot may supply read-only generated projects/DLLs even before a reservation. This
            # exposes no Editor command or writable slot root, and the delivered typecheck validates readiness.
            writable_roots = [Path(self.db_path).parent.resolve(), *(Path(p).resolve() for p in source_roots)]
            references = [entry["folder"] for _, entry in sorted(self.slot_entries.items())
                          if entry.get("repo") == "Farm-Client" and entry.get("folder")
                          and Path(entry["folder"]).is_absolute()
                          and not any(Path(entry["folder"]).resolve().is_relative_to(p)
                                      or p.is_relative_to(Path(entry["folder"]).resolve()) for p in writable_roots)]
            tools["client_typecheck"] = ({"reference_checkout": references[0]} if references else {
                "status": "unavailable", "reason": "no configured Farm-Client reference outside worker write roots"})
        if skill.name in LARK_CLI_SKILLS:
            # A profile/home or a credential-free environment grant. Never copy app_id/secret_env into the prompt.
            tools["lark_cli"] = lark_tools(self.lark_cli, self.runtime_name, os.environ)
        try:
            memory = publish_snapshot(Path(self.db_path).resolve().parent / "memory", self.ledger.memory_rows())
        except (OSError, ValueError, sqlite3.Error) as exc:
            memory = {"status": "unavailable", "index": None, "reason": type(exc).__name__}
        session = self.ledger.session(item['session_id']) or {}
        publication = (self.publication.scope(item=item, issue=issue,
                                             paths={repo: paths[repo] for repo in write_repos},
                                             delegated=bool(session.get('delegation')),
                                             suffix_roles=skill.initial_root is not None)
                       if self.publication is not None else {'repositories': {}})
        # Carry one bounded, structured predecessor summary into the fresh prompt. The full
        # history stays in issue-context; a neutral staged attempt (a chat-to-fix restart) uses the
        # latest investigator summary, while a rooted one uses this item's validated checkpoint handoff.
        context = self.ledger.issue_context(item['id'])
        requests = context['session_messages']
        prior_context = None
        if skill.staged:
            chat_summaries = [entry['summary'] for entry in context['conversation_history']
                              if entry['skill'] == 'chat' and entry['summary']]
            if root is None and item.get('root_repo') != COORDINATION_ROOT and chat_summaries:
                prior_context = {'source': 'investigator_summary', 'summary': chat_summaries[-1],
                                 'revalidation_required': True}
            else:
                prior_context = context['handoff']
        message = dispatch_message(item=item, issue=issue, skill_path=skill.skill_md,
                                   worktrees=paths, db_path=self.db_path, runtime=self.runtime_name,
                                   guidance=self.guidance_for(item), budget=skill.budget,
                                   repo_root=repo_root, state_dir=self.launcher.state_dir(item["id"]),
                                   resource=resource, memory=memory, publication=publication, user_requests=requests,
                                   bot_name=self.bot_name, write_repositories=write_repos, root_repository=root,
                                   prior_context=prior_context, tools=tools, reads=reads)
        # The runtime's cwd is writable too. A read-only conversation must run
        # from its private state directory, not from the detached source checkout.
        primary = (self.launcher.state_dir(item['id']) if single(item) else
                   paths[write_repos[0]] if write_repos else self.launcher.state_dir(item["id"]))
        # `python3 -m agent` must resolve from any worktree, so FarmBot's root leads the worker's PYTHONPATH.
        pythonpath = os.pathsep.join(p for p in (str(repo_root), os.environ.get("PYTHONPATH", "")) if p)
        # A worktree's commits land in FarmBot's bare clone: the parts of it git writes for them are writable too,
        # never its config, hooks or info, which FarmBot's own git reads outside the sandbox (plan P10).
        # No slot folder or Unity host path is added to a worker's writable roots,
        # in either mode. The worker reads results XML in its own state directory, which
        # Launcher.spawn already makes writable, and writes nothing in the slot.
        if self.ledger.item(item["id"])["state"] != "queued":
            return None
        options = {}
        if (skill.name in LARK_CLI_SKILLS and tools.get("lark_cli", {}).get("authentication") == "environment"):
            options["lark_cli_access"] = True
        if self.runtime_name == "codex":
            options["model_settings"] = {**DEFAULT_CODEX_MODEL_SETTINGS,
                                         **self.codex_workers.get(skill.name, {})}
        if self.kw_ops_config and kw_ops_grant.keep_env is None:
            options["withheld_env"] = [self.kw_ops_config["token_env"]]
        worker_env = {"FARMBOT_DB": str(self.db_path), "PYTHONPATH": pythonpath}
        if resource and (resource["kind"] == "server_slot" or resource.get("integration")):
            worker_env.update((resource.get("integration") or resource)["environment"])
            worker_env['GOMAXPROCS'] = '2'  # Launcher owns attempt-local build/cache paths.
        if self.config_path is not None:
            # Every attempt, including resumes, uses the controller's selected file.
            # Do not mutate os.environ: another controller/test may live in this process.
            worker_env["FARMBOT_CONFIG"] = str(self.config_path)
        if not Maintenance(self.ledger).may_launch(item['id']):
            return None
        handle = self.launcher.spawn(item["id"], message, servers, int(skill.budget["max_hours"] * 3600), cwd=primary,
                                     extra_env=worker_env,
                                     writable=[Path(self.db_path).parent, *source_roots],
                                     cancelled=lambda: self.ledger.item(item["id"])["state"] != "queued", **options)
        try:
            self.ledger.set_worker(item["id"], handle.pid, self.host, int(skill.budget["lease_seconds"]))
        except LedgerError:
            self.launcher.stop(item["id"])
            raise
        self.active[item["id"]] = handle
        return handle

    def _hold_legacy(self, item):
        if item['stage'] == 'legacy-adoption-required':
            return
        with self.ledger._transaction():
            self.ledger.connection.execute("UPDATE work_items SET stage='legacy-adoption-required' WHERE id=? AND state='queued'",
                                          (item['id'],))
            self.ledger._audit(item['id'], 'legacy_execution_held', 'explicit adoption required; candidates and reports retained')

    def _notify(self, item_id, kind, body, *, item=None):
        """Best-effort session activity for outcomes the worker cannot report itself: it is dead or never ran.

        A `local-` session id was minted by `agent.service enqueue`, not by Linear, and names no agent
        session: create_activity against the real API would fail on every one of these notices and leave the
        operator with nothing. The issue comment is the only reporting surface such an item has.

        `item`, a view of the item the caller already holds, spares a read on the scheduler's own connection, which
        a caller on another thread (the lifecycle loop through `stop`) must not use.

        Returns whether Linear took it. A response or an error closes the job's thread, so one Linear refuses is
        owed to the thread and posted again by the progress loop (silent-delegation design P10, A4); a thought
        closes nothing and is not.
        """
        if self.api is None:
            return False
        try:
            item = item or self.ledger.item(item_id)
            if item.get("parent_id"):
                kind = "thought"
                body = f"[{item['task_key']}] {body}"
            if str(item["session_id"]).startswith("local-"):
                self.api.create_comment(item["issue_id"], body)
            else:
                self.api.create_activity(item["session_id"], {"type": kind, "body": body})
        except Exception as exc:
            if item is not None and kind in ("response", "error"):
                self._owe(item, kind, body, exc)
            return False
        return True

    def _owe(self, item, kind, body, exc):
        """Record the closing activity Linear refused for `item`, best effort as the post was. On a control connection
        where there is one, as `stop` cancels: `_notify` also runs on the receiver's and the lifecycle's threads,
        which must not use the scheduler's own."""
        try:
            control = self.control_ledger_factory() if self.control_ledger_factory else self.ledger
            try:
                control.owe_closure(str(item["session_id"]), issue_id=item["issue_id"], item_id=item["id"], kind=kind,
                                    body=body, error=type(exc).__name__)
            finally:
                if control is not self.ledger:
                    control.close()
        except Exception:
            pass

    def _fail_launch(self, item_id, exc):
        if self.ledger.item(item_id)["state"] == "cancelled":
            return
        try:
            self.ledger.fail_queued(item_id, f"launch failed: {type(exc).__name__}: {exc}"[:500])
        except LedgerError:
            return
        self._retire(item_id, "failed")
        self._notify(item_id, "error", f"{self.bot_name} 无法启动工作进程（{type(exc).__name__}），工作项已标记失败；可回复「重试」。")

    def _refuse_disabled(self, item_id, skill):
        """A loaded skill this host's `enabled_skills` leaves out is never launched (spec §9.11). Its item fails
        rather than waits: a queued one would keep its issue's one active slot, collect "still queued" heartbeats
        and swallow replies for work this instance will not do. Cleanup preserves any earlier attempt's source,
        and `retry`, or a requested continuation of a fix, brings the item back once the skill is enabled."""
        try:
            self.ledger.fail_queued(item_id, f"skill {skill} is not enabled on this host")
        except LedgerError:
            return  # it left the queue meanwhile (Stop, closure)
        self._retire(item_id, "failed")
        runs = "、".join(sorted(self.enabled_skills))
        self._notify(item_id, "error", f"{self.bot_name} 本实例没有启用 {skill}（本实例运行：{runs}），这项工作没有启动，"
                                       "工作项已标记失败；启用后可回复「重试」。")

    def stop(self, item_id, reason, *, states=None, notice=None, authority=None, created_before=None):
        """Cancel the item, then stop its processes.

        `states` (delegation removal, spec §9.8) cancels only an item still in one of those states, atomically; one
        a worker has claimed since keeps running and nothing is signalled. `authority` and `created_before` recheck
        the rest of such a caller's decision in the same transaction (Ledger.cancel). `notice` is then posted as the
        session's response, through `_notify` as a launch failure is, only when this call cancelled the item; it
        needs `states`, without which a repeated stop could not tell. The item's pending heartbeat goes with a
        notice, in the cancelling transaction, so the notice is its session's last word (withdrawn-work design P7).
        `notice` is the text, or a function of the cancelled item's view that returns it: the cancel follows a
        conversation's handover to the job it continued, which can be another skill's, so a caller that chose by
        the job it listed would give a fix a conversation's words. A function may return None: nothing is posted
        then, and the heartbeat still goes, for a caller that answers in the job's own thread itself, as a Linear
        Stop pressed there does (silent-delegation design A1). Returns the cancelled item, or None.
        """
        if notice is not None and states is None:
            raise ValueError("a stop notice needs states: only then is it known that this stop cancelled the item")
        conditional = states is not None or authority is not None or created_before is not None
        # Revoke the claim durably before signalling; a late worker may no longer write the ledger.
        control = self.control_ledger_factory() if self.control_ledger_factory else self.ledger
        destination = item_id
        cancelled = None
        children = []
        try:
            try:
                cancelled = control.cancel(item_id, reason, states=states, drop_progress=notice is not None,
                                           authority=authority, created_before=created_before)
                if cancelled is not None:
                    destination = cancelled["id"]
            except LedgerError:
                pass
            try:
                if control.item(destination)["skill"] == "work":
                    children = [task["id"] for task in task_views(control, destination)
                                if task["state"] == "cancelled"
                                and not (control.cleanup_record(task["id"]) or {}).get("done")]
            except LedgerError:
                pass
        finally:
            if control is not self.ledger:
                control.close()
        if conditional and cancelled is None:
            return None  # not ours to stop: a claimed worker sees the change at its next fetch-issue
        # The batch Editor is not a worker and never went through `spawn`, so `launcher.stop` below cannot
        # see it: its handle lookup and its `descendants` walk both start from a worker pid, and by now that
        # worker has already exited — it asked for the reservation and quit. Killing the group here is what
        # keeps this task's title true. Be honest about its cost: `kill_group` polls for up to 5s after
        # SIGTERM and 1s more after SIGKILL, so on the batch path Stop itself can take about six seconds,
        # and a wedged Editor is exactly Task 0's measured case. What that cannot touch is done-criterion
        # 2's budget, which is five seconds to kill the *worker*: a batch Editor runs inside the pool's
        # hand-over window, before `resume` puts the item back in the queue, so the Editor and a worker for
        # the same item are never alive at once. In every other case — interactive slots, fix workers, no
        # batch run in flight — this is one dict lookup that returns False. Killing it first also releases
        # the pool thread from `process.wait()` sooner, and the sooner that returns the sooner the slot can
        # settle. `SlotPool.run_batch` closes the other half: the window before the Editor is registered.
        for stopped_id in dict.fromkeys((*children, destination, item_id)):
            self.launcher.stop_unsandboxed(stopped_id)
            # Signal both ends if read-only execution handed off during Stop.
            self.launcher.stop(stopped_id)
        if notice is not None and cancelled is not None:
            text = notice(cancelled) if callable(notice) else notice
            if text:
                self._notify(destination, "response", text, item=cancelled)
        return cancelled

    def _reap(self):
        reaped = 0
        for finished in self.launcher.poll():
            self.active.pop(finished.item_id, None)
            item = self.ledger.item(finished.item_id)
            state = item["state"]
            if Maintenance(self.ledger).pending(finished.item_id):
                # The claim was retired deliberately. settle() still proves all descendants gone.
                reaped += 1
                continue
            if StartupRecovery(self.ledger).pending(finished.item_id):
                reaped += 1  # _recover completes the fenced attempt after all-attempt teardown
                continue
            if (state == "queued" and item["next_root_repo"] is not None
                    and item["worker_pid"] == finished.worker_pid):
                try:
                    self.launcher.assert_quiescent(finished.item_id, finished.worker_pid)
                    # The manifest re-checks the target; a skill this host does not load stays pending.
                    self.ledger.complete_repository_handoff(finished.item_id, finished.worker_pid,
                                                            skill=self.skills[item["skill"]])
                except Exception:
                    # Preserve the old PID and pending target for recovery; never launch over
                    # descendants whose teardown is unproven.
                    pass
                reaped += 1
                continue
            if (finished.failure_kind == "model_capacity" and not finished.killed
                    and not (state == 'queued' and (single(item) or item['skill'] == 'chat'))):
                retry = self.ledger.defer_capacity_retry(finished.item_id, finished.worker_pid)
                if retry:
                    self._notify(finished.item_id, "thought",
                                 f"模型暂时容量不足，工作已保留，将在 {retry['delay_seconds']} 秒后重新排队尝试"
                                 f"（自动重试 {retry['attempt']}/3，模型设置不变）。")
                    reaped += 1
                    continue
                if (state in ("running", "queued") and item["worker_pid"] == finished.worker_pid
                        and item["capacity_retries"] >= 3):
                    fail = self.ledger.fail if state == "running" else self.ledger.fail_queued
                    fail(finished.item_id, "model capacity automatic retries exhausted (3)")
                    self._notify(finished.item_id, "error", "模型容量不足，3 次自动重试已用尽；工作已保留，可稍后回复「重试」。")
                    state = "failed"
            if state == "running":
                item = self.ledger.item(finished.item_id)
                if item["lease_expires_at"] is not None and item["lease_expires_at"] <= self.ledger.clock():
                    self.ledger.recover(finished.item_id, f"worker exited with an expired lease ({finished.reason}, code {finished.returncode})")
                    self._notify(finished.item_id, "thought", "工作进程在租约过期后退出，已重新排队，等待新的 worker 接手。")
                    state = "queued"
                else:
                    self.ledger.fail(finished.item_id, f"worker exited without finishing ({finished.reason}, code {finished.returncode})")
                    self._notify(finished.item_id, "error", f"工作进程未完成即退出（{finished.reason}，退出码 {finished.returncode}），工作项已标记失败；可回复「重试」。")
                    state = "failed"
            elif state == "queued" and self.ledger.item(finished.item_id)["worker_pid"] is not None:
                if single(item) or item['skill'] == 'chat':
                    if finished.worker_pid == item['worker_pid']:
                        StartupRecovery(self.ledger).begin(item, 'worker exited before claiming')
                    reaped += 1
                    continue
                # A queued item with no pid was requeued after the worker finished (material change or
                # recovery); it is waiting for a fresh launch, not a worker that died before claiming.
                self.ledger.fail_queued(finished.item_id, f"worker exited before claiming ({finished.reason}, code {finished.returncode})")
                self._notify(finished.item_id, "error", "工作进程在认领工作项前退出，工作项已标记失败；可回复「重试」。")
                state = "failed"
            if state in TERMINAL:
                self._retire(finished.item_id, state)
            reaped += 1
        return reaped

    def resource_quiescent(self, item_id):
        """A resource pool may recycle services only after all recorded worker attempts are contained and empty."""
        with self.lock:
            if item_id in self.launcher.running():
                return False
            try:
                self.launcher.assert_quiescent(item_id, self.ledger.last_worker_pid(item_id))
            except Exception:
                return False
            return True

    def fence_resource_worker(self, recovery):
        """Stop the revoked attempt and certify its process tree before failover."""
        item_id, pid = recovery['item_id'], recovery['worker_pid']
        if item_id is None:
            return
        with self.lock:
            current = self.ledger.item(item_id)
            if current['worker_pid'] is not None and current['worker_pid'] != pid:
                raise RuntimeError('a different worker attempt owns this job')
            self.launcher.stop_unsandboxed(item_id)
            self.launcher.stop(item_id)
            # A worker that exited by itself is left unreaped by stop(); polling it now records its
            # pinned-session evidence instead of meeting a zombie whose ownership cannot be read.
            self._reap()
            recorded = []
            certified = self.launcher.certified_pids(item_id) if hasattr(self.launcher, 'certified_pids') else set()
            if pid and pid not in certified and self.launcher.alive(pid):
                if not self.launcher.owned_pid(pid, item_id):
                    raise RuntimeError('old worker process ownership is unverified')
                recorded = self.launcher.kill_owned_attempt(item_id, pid)
            self.launcher.assert_quiescent(item_id, pid, recorded)
            # Consume the old handle before its same-ID successor can launch. A poll that could not finish
            # processing the old worker keeps its handle registered, and the fence then fails to be retried.
            self._reap()
            if item_id in self.launcher.running():
                raise RuntimeError('old worker handle could not be consumed')
            self.active.pop(item_id, None)

    def _settle_startup(self, pending):
        item_id, pid = pending['item_id'], pending['worker_pid']
        try:
            if item_id in self.active or item_id in self.launcher.running():
                self.launcher.stop(item_id)
            if item_id in self.launcher.running():
                return False  # poll must consume the old handle before any successor
            recorded = []
            certified = self.launcher.certified_pids(item_id) if hasattr(self.launcher, 'certified_pids') else set()
            if pid not in certified and self.launcher.alive(pid):
                if not self.launcher.owned_pid(pid, item_id):
                    return False
                recorded = self.launcher.kill_owned_attempt(item_id, pid)
            self.launcher.assert_quiescent(item_id, pid, recorded)
        except Exception:
            return False  # uncertain descendants retain the durable fence and evidence
        self.active.pop(item_id, None)
        result = StartupRecovery(self.ledger).complete(pending)
        if result and result['status'] == 'queued':
            self._notify(item_id, 'thought', f"工作进程启动未完成；已确认安全退出，保留进度，"
                         f"至少 {result['delay_seconds']} 秒后自动重试（{result['attempt']}/3）；队列冷却可能延长等待。")
        elif result:
            self._notify(item_id, 'error', '工作进程启动的 3 次自动重试已用尽；进度已保留，可回复「重试」。')
            self._retire(item_id, 'failed')
        return result is not None

    def _recover(self):
        recovered = 0
        now = self.ledger.clock()
        Maintenance(self.ledger).settle(self.launcher)
        for pending in self.ledger.connection.execute('SELECT * FROM worker_startup_recovery WHERE host=?',
                                                      (self.host,)).fetchall():
            recovered += int(self._settle_startup(dict(pending)))
        for row in self.ledger.repository_handoffs():
            item_id, pid = row["id"], row["worker_pid"]
            if item_id in self.active:
                continue  # poll() owns teardown evidence for a live controller handle.
            try:
                recorded = []
                if self.launcher.alive(pid):
                    if not self.launcher.owned_pid(pid, item_id):
                        continue
                    recorded = self.launcher.kill_owned_attempt(item_id, pid)
                self.launcher.assert_quiescent(item_id, pid, recorded)
                self.ledger.complete_repository_handoff(item_id, pid, skill=self.skills[row["skill"]])
                recovered += 1
            except Exception:
                continue
        for item_id in self.ledger.status()["recovery_required"]:
            item = self.ledger.item(item_id)
            pid = item["worker_pid"]
            if pid and self.launcher.alive(pid) and item_id not in self.active and not self.launcher.owned_pid(pid, item_id):
                continue  # A live PID we cannot identify is not a dead worker.
            if pid and (item_id in self.active or self.launcher.owned_pid(pid, item_id)):
                if item_id in self.active:
                    self.launcher.stop(item_id)
                    self.active.pop(item_id, None)
                self.ledger.fail(item_id, "lease expired with a live worker; killed")
                self._notify(item_id, "error", "工作进程超过租约仍未汇报，已被终止，工作项已标记失败；可回复「重试」。")
            else:
                self.ledger.recover(item_id, "lease expired and worker process is gone")
                self._notify(item_id, "thought", "工作进程已消失且租约过期，已重新排队，等待新的 worker 接手。")
                recovered += 1
        for row in self.ledger.launched():
            if StartupRecovery(self.ledger).pending(row['id']):
                continue
            stale = now - row["updated_at"] > self.claim_timeout
            tracked = row["id"] in self.active
            owned = tracked or self.launcher.owned_pid(row["worker_pid"], row["id"])
            if (owned and not stale) or (not owned and self.launcher.alive(row["worker_pid"])):
                continue
            if single(row) or row['skill'] == 'chat':
                pending = StartupRecovery(self.ledger).begin(row, 'worker did not claim within the timeout' if owned
                                                            else 'worker process gone before claiming')
                if pending:
                    recovered += int(self._settle_startup(pending))
                continue
            if tracked:
                self.launcher.stop(row["id"])
                self.active.pop(row["id"], None)
            self.ledger.fail_queued(row["id"], "worker did not claim within the timeout" if owned else "worker process gone before claiming")
            self._notify(row["id"], "error", "工作进程未在时限内认领工作项，工作项已标记失败；可回复「重试」。")
            self._retire(row["id"], "failed")
            recovered += 1
        return recovered

    def _retire(self, item_id, state):
        # The caller's snapshot may predate cancellation or retry. Every terminal path uses the
        # same process, reservation, preservation and path checks; no force-removal fallback.
        if self.ledger.item(item_id)["state"] not in TERMINAL:
            return
        record = self.ledger.cleanup_record(item_id)
        if record and record["done"]:
            return
        result = record["result"] if record else {}
        try:
            pid = record.get("worker_pid") if record else self.ledger.last_worker_pid(item_id)
            boot_proof = self.ledger.cleanup_boot_proof(item_id)
            certified = (self.launcher.certified_pids(item_id, boot_proof)
                         if hasattr(self.launcher, "certified_pids") else set())
            handle = self.launcher.running().get(item_id)
            if handle and handle.process:
                if not self.launcher.exited(handle.process):
                    raise RuntimeError("worker has not exited")
                # POSIX: never reap a registered worker here; Launcher.poll records its evidence first.
                if handle.process.returncode is None:
                    raise RuntimeError("worker exit awaits teardown evidence")
            if pid and pid not in certified and self.launcher.alive(pid):
                if not self.launcher.owned_pid(pid, item_id):
                    raise RuntimeError("live worker PID ownership cannot be verified")
                result["processes"] = [pid, *self.launcher.descendants(pid)]
                self.ledger.record_cleanup(item_id, result)
                self.launcher.kill_pid(pid)
            extra = {"boot_proof": boot_proof} if boot_proof else {}
            self.launcher.assert_quiescent(item_id, pid, result.get("processes", []), **extra)
            if self.ledger.active_reservation(item_id) is not None:
                raise RuntimeError("reservation awaits quiescence")
            item = self.ledger.item(item_id)
            if item.get("parent_id"):
                # The parent retains the shared repository history. A child may
                # certify its process teardown, never remove its siblings' files.
                delivery_error = None
                if item["state"] == "delivered":
                    try:
                        self._verify_retired_task_delivery(item)
                    except (VerificationMismatch, TaskDeliveryMismatch) as exc:
                        delivery_error = str(exc)[:500]
                        with self.ledger._transaction():
                            current = self.ledger._row(item_id)
                            if current["state"] != "delivered" or current["generation"] != item["generation"]:
                                raise LedgerError("task delivery changed during teardown verification") from exc
                            # Process teardown is complete, but the published
                            # candidate cannot be trusted. Retain its files,
                            # entry baseline and prior proof for human retry.
                            self.ledger._set_state(item_id, "failed", "presentation output changed before worker teardown",
                                                   token=None, lease_expires_at=None, worker_pid=None)
                            self.ledger._audit(item_id, "task_delivery_invalidated", details={
                                "generation": item["generation"], "error": delivery_error})
                        result["delivery_error"] = delivery_error
                self.worktrees.remove_reads(item_id)
                self.ledger.record_cleanup(item_id, result, error=delivery_error, done=True)
                return
            if item["skill"] == "work":
                if any(not (self.ledger.cleanup_record(t["id"]) or {}).get("done")
                       for t in task_views(self.ledger, item_id)):
                    raise RuntimeError("presentation task teardown is not yet certified")
            saved = self.worktrees.preserve(item_id)
            # A prior removal may have succeeded for only some repositories. Keep their manifest.
            for key in ("committed", "refs"):
                result[key] = {**result.get(key, {}), **saved[key]}
            snapshots = result.setdefault("snapshots", {})
            for repo, refs in saved.get("snapshots", {}).items():
                snapshots[repo] = {**snapshots.get(repo, {}), **refs}
            result["errors"] = saved["errors"]
            self.ledger.record_cleanup(item_id, result)
            self.ledger.begin_cleanup_removal(item_id)
            self.worktrees.remove_preserved(item_id, result)
            self.worktrees.remove_reads(item_id)  # the read-only checkouts go with the worktrees (spec §9.6)
            self.ledger.record_cleanup(item_id, result, done=True)
        except Exception as exc:
            # A concurrent same-ID retry retires cleanup authority. It must not delete active files.
            if self.ledger.item(item_id)["state"] in TERMINAL:
                self.ledger.record_cleanup(item_id, result, error=str(exc)[:500])

    def _verify_retired_task_delivery(self, item):
        """After quiescence, bind shared output to the task's controller proof.

        A worker can still have filesystem access between finish and exit. No
        later writer may inherit edits or commits that appeared in that window.
        """
        row, evidence = self.ledger._row(item["id"]), item["evidence"]
        if item["skill"] == "scene":
            kind, evidence_key = "scene_delivery_verified", "scene_delivery_id"
        elif item["skill"] == "fgui":
            kind, evidence_key = (("fgui_source_verified", "ui_source_delivery_id") if evidence.get('ui_source_delivery_id') else
                                  ("fgui_no_change_verified", "ui_no_change_id") if evidence.get("no_change") else
                                  ("fgui_delivery_verified", "ui_delivery_id"))
        else:
            raise TaskDeliveryMismatch("presentation delivery has no supported controller proof")
        proofs = [json.loads(record["details"]) for record in self.ledger.connection.execute(
            "SELECT details FROM audit WHERE item_id=? AND kind=? ORDER BY id DESC", (item["id"], kind))]
        proof = next((value for value in proofs if value.get("delivery_id") == evidence.get(evidence_key)
                      and value.get("generation") == row["generation"]
                      and value.get("fingerprint") == row["claimed_fingerprint"]), None)
        if proof is None:
            raise TaskDeliveryMismatch("presentation teardown requires its current controller delivery proof")
        if kind == 'fgui_source_verified':
            from .presentation_artifacts import source_proof
            proof = source_proof(self.ledger, item)
            heads, branches = proof['heads'], proof['branches']
        elif kind == "fgui_no_change_verified":
            heads, branches = proof.get("heads"), proof.get("branches")
            if proof.get("baseline") != item.get("task_client_base"):
                raise TaskDeliveryMismatch("unchanged UI teardown must retain its original Client entry baseline")
            required = {"farmgui"}
            if item.get("task_client_base"):
                required.add("Farm-Client")
            if (not isinstance(heads, dict) or set(heads) != required
                    or not isinstance(branches, dict) or set(branches) != required):
                raise TaskDeliveryMismatch("unchanged UI teardown requires its exact certified checkout heads and branches")
        elif kind == "fgui_delivery_verified":
            recorded = self.ledger.recorded_branches(item["id"], issue_prefix=self.issue_prefix)
            source_branch = recorded.get("farmgui")
            if not source_branch:
                raise TaskDeliveryMismatch("UI teardown requires its controller-recorded source branch")
            heads = {"farmgui": proof.get("source_head"), "Farm-Client": proof.get("commit")}
            branches = {"farmgui": source_branch,
                        "Farm-Client": self.ledger.staged_client_branch(item["id"], issue_prefix=self.issue_prefix)}
        else:
            if proof.get("baseline") != item.get("task_client_base"):
                raise TaskDeliveryMismatch("scene teardown must retain its original entry baseline")
            branch = self.ledger.staged_client_branch(item["id"], issue_prefix=self.issue_prefix)
            if proof.get("branch") != branch:
                raise TaskDeliveryMismatch("scene teardown must retain its certified Client branch")
            heads, branches = {"Farm-Client": proof.get("commit")}, {"Farm-Client": branch}
        for repo, head in heads.items():
            self.worktrees.verification_commit(repo, item["id"], head, expected_branch=branches[repo])

    def acquire_unity(self, ledger, acquire, kind, **options):
        """Reserve a worker seat atomically with an interactive Unity grant.

        Only the short admission/acquire step holds the launch lock. Switching,
        compilation and MCP stay on the pool thread outside it. The active
        reservation is the durable seat; ordinary launches cannot spend it.
        Batch runs execute in the pool without a worker and keep their own capacity.
        """
        with self.lock:
            if Maintenance(ledger).requested():
                return None
            options['single_only'] = not self.replay_legacy
            request = next(iter(ledger.reservation_queue(kind, single_only=not self.replay_legacy)), None)
            if request is None:
                return None
            if request["mode"] != "interactive":
                return acquire(ledger, kind, **options)
            item = ledger.item(request["item_id"])
            if not single(item) and not self.replay_legacy:
                return None
            if single(item):
                # This request already has a running worker and its seat.
                if (item['id'] not in self.active or item['state'] != 'running'
                        or item['lease_expires_at'] <= ledger.clock() or item['withdraw_deadline'] is not None):
                    return None
                return acquire(ledger, kind, expected_reservation=request['reservation_id'], **options)
            reserved = ledger.pending_unity_workers(self.host)
            # await-resource retires the claim before the old process exits.
            # Display this actual blocker even when that old process fills the pool.
            if item['id'] in self.active or item['worker_pid'] is not None:
                ledger.unity_wait_stage(request['reservation_id'], 'waiting-for-worker-teardown')
                return None
            if item['withdraw_deadline'] is not None:
                return None
            # A queued continuation may spend its retained next worker seat after
            # the departing worker has exited; other reservations still count.
            if len(set(self.active) | (reserved - {request['item_id']})) >= self.max_concurrent:
                ledger.unity_wait_stage(request["reservation_id"], "waiting-for-worker")
                return None
            try:
                self.launcher.assert_quiescent(item["id"], ledger.last_worker_pid(item["id"]))
            except Exception:
                ledger.unity_wait_stage(request["reservation_id"], "waiting-for-worker-teardown")
                return None
            skill = skill_for(item, self.skills.get(item["skill"]))
            # Disabled skills retain the existing queued-launch refusal/cleanup
            # path after hand-over, rather than getting stranded in resource wait.
            if skill and not item.get("parent_id") and skill.exclusive and self._exclusive_running(ledger=ledger):
                return None
            if item.get("predecessor_id") and not (ledger.cleanup_record(item["predecessor_id"]) or {}).get("done"):
                return None
            if skill and (item["skill"] == "work" or item.get("parent_id")):
                if item.get("parent_id"):
                    parent = ledger.item(item["parent_id"])
                    if parent["state"] not in ACTIVE_STATES or parent["withdraw_deadline"] is not None:
                        return None
                try:
                    client_ready(ledger, ledger._row(item["id"]))
                except LedgerError:
                    return None
                if repository_busy(ledger, item, self.skills, self.active, self.launcher):
                    return None
            # Stop can change FIFO outside this lock. Never grant a different
            # job whose worker/teardown eligibility we have not checked.
            reservation = acquire(ledger, kind, expected_reservation=request["reservation_id"], **options)
            if reservation is None:
                ledger.unity_wait_stage(request["reservation_id"], "waiting-for-unity")
            return reservation

    def _mixed_writer_running(self, item):
        """Historical staged writers cannot overlap the current all-repository owner."""
        if item['skill'] == 'chat':
            return False
        return any(other['skill'] != 'chat' and single(other) != single(item)
                   for key in self.active if key != item['id']
                   for other in (self.ledger.item(key),))

    def _exclusive_running(self, *, ledger=None, exclude=None):
        """Keep the legacy exclusive lane independent of general-job capabilities.

        Native UI export owns its machine mutex separately. A general job's UI
        authoring must not prevent an unrelated legacy server attempt from running.
        """
        ledger = ledger or self.ledger
        for item_id in set(self.active) | ledger.pending_unity_workers(self.host):
            if item_id == exclude:
                continue
            item = ledger.item(item_id)
            if item.get("parent_id"):
                continue
            skill = skill_for(item, self.skills.get(item["skill"]))
            if skill is not None and skill.exclusive:
                return True
        return False

    def _sweep_worktrees(self):
        for row in self.ledger.status()["items"]:
            if row["state"] in TERMINAL and row["id"] not in self.active:
                self._retire(row["id"], row["state"])

    def _stop_cancelled(self):
        # The operator CLI has its own ledger connection and cannot access this launcher's handles.
        # Reconcile before reaping or sweeping: cancel clears worker_pid, and a batch run has no active
        # worker at all until the pool resumes it. Only the launcher knows which processes we still own.
        # Leave reservations to the pool's quiescence probe and send no second cancellation notification.
        cancelling = {row["item_id"] for row in self.ledger.reservations(states=("cancel_requested",))}
        for row in self.ledger.status()["items"]:
            # retry can replace 'cancelled' with 'queued' before this tick. The old reservation still
            # carries cancellation, and a live worker whose recorded pid was cleared is an old attempt.
            stale_worker = row["state"] == "queued" and row["worker_pid"] is None and row["id"] in self.active
            handoff_worker = row["next_root_repo"] is not None and row["id"] in self.active
            if row["state"] == "cancelled" or row["id"] in cancelling or stale_worker or handoff_worker:
                self.launcher.stop_unsandboxed(row["id"])
                self.launcher.stop(row["id"])
                if not handoff_worker:
                    self.active.pop(row["id"], None)

    def _expire_withdrawals(self):
        """A withdrawn worker that has not run `withdraw` by the end of its grace is stopped as Stop stops one
        (withdrawn-work design P2): its claim ends cancelled here, with the one response its session gets, and
        _stop_cancelled then kills it. An issue out of reach gets no response. The cancel rechecks the flag and its
        deadline: one a read cleared after this listing keeps its worker."""
        for item in self.ledger.expired_withdrawals():
            try:
                cancelled = self.ledger.expire_withdrawal(item["id"])
            except LedgerError:
                continue
            if (cancelled is not None and cancelled["state"] == "cancelled"
                    and not cancelled.get("parent_id") and cancelled["withdraw_reason"] in NOTICE_REASONS):
                self._notify(cancelled["id"], "response",
                             withdrawal_notice(cancelled["skill"], cancelled["withdraw_reason"], self.bot_name),
                             item=cancelled)

    def tick(self):
        with self.lock:
            self._expire_withdrawals()
            self._stop_cancelled()
            reaped = self._reap()
            recovered = self._recover()
            self._sweep_worktrees()
            parents = self.ledger.connection.execute("SELECT id FROM work_items w "
                "WHERE parent_id IS NULL AND skill='work' AND state='awaiting_input' AND withdraw_deadline IS NULL "
                "AND EXISTS (SELECT 1 FROM work_items t WHERE t.parent_id=w.id)").fetchall()
            if self.replay_legacy:
                for row in parents:
                    wake_parent(self.ledger, row["id"])
            queue = self.ledger.queue()
            priorities = {item['id']: item['priority'] for item in queue}
        # The worker CLI runs inside its repository sandbox. Only this host
        # path may fill missing farmgui objects for a Client continuation.
        from .fgui_history import refresh_pending
        if self.replay_legacy:
            refresh_pending(self.ledger, self.worktrees)
        launched = 0
        for item in queue:
            with self.lock:
                if item['skill'] != 'chat' and not single(item) and not self.replay_legacy:
                    self._hold_legacy(item)
                    continue
                if not Maintenance(self.ledger).may_launch(item['id']):
                    continue
                if not StartupRecovery(self.ledger).may_launch(self.host):
                    break
                task_enabled = (item.get("parent_id") and "work" in self.enabled_skills
                                and self.ledger.item(item["parent_id"])["state"] not in TERMINAL)
                if item["skill"] in self.skills and item["skill"] not in self.enabled_skills and not task_enabled:
                    # Ahead of the cap: a refusal takes no worker slot and must not wait for one.
                    self._refuse_disabled(item["id"], item["skill"])
                    continue
                reserved = self.ledger.pending_unity_workers(self.host)
                if len(self.active) >= self.max_concurrent:
                    break
                if item["id"] not in reserved and len(set(self.active) | reserved) >= self.max_concurrent:
                    continue  # a prepared Unity worker later in the queue owns this seat
                if item["skill"] not in self.skills or item["id"] in self.active:
                    continue
                # Current owners have isolated issue worktrees and share the
                # ordinary worker cap. Resource wait retains the same seat.
                if self._mixed_writer_running(item):
                    continue
                # At most one attempt of any exclusive skill at a time (spec §5.8, D16; P8). A waiting one is only
                # passed over, so it keeps its place in the queue, and fix and chat go on up to max_concurrent.
                if not item.get("parent_id") and skill_for(item, self.skills[item["skill"]]).exclusive and self._exclusive_running(exclude=item["id"]):
                    continue
                if not single(item):
                    prepare_coordination(self.ledger, item['id'], self.launcher, self.active)
                item = self.ledger.item(item['id'])
                root = current_root(item.get("root_repo"), skill_for(item, self.skills[item["skill"]]))
                if not single(item) and (item["skill"] == "work" or item.get("parent_id")):
                    owner = repository_owner(item)
                    if item.get("parent_id"):
                        parent = self.ledger.item(owner)
                        if parent["state"] not in ACTIVE_STATES or parent["withdraw_deadline"] is not None:
                            continue
                    if root == "Farm-Client":
                        try:
                            client_ready(self.ledger, self.ledger._row(item["id"]))
                        except LedgerError:
                            continue
                    if repository_busy(self.ledger, item, self.skills, self.active, self.launcher):
                        continue
                if item.get("predecessor_id"):
                    cleanup = self.ledger.cleanup_record(item["predecessor_id"])
                    if not cleanup or not cleanup["done"]:
                        continue
                reservation = self.ledger.active_reservation(item["id"])
                if reservation is not None and reservation["state"] == "cancel_requested":
                    continue
            # Network calls never hold the scheduler lock. Stop uses its own SQLite connection.
            if self.preflight and not self.preflight(item):
                continue
            with self.lock:
                if self.ledger.item(item["id"])["state"] != "queued":
                    continue
                # Preflight or another connection may have refreshed priorities.
                # Rebuild the ordering next tick instead of spending a worker seat
                # from this stale snapshot. Admission/resource checks still apply.
                if any(row['priority'] != priorities.get(row['id'], row['priority'])
                       for row in self.ledger.queue()):
                    break
                # Preflight runs without the lock. The pool may have reserved a
                # seat while Linear was being read; recheck before spawning.
                reserved = self.ledger.pending_unity_workers(self.host)
                if (len(self.active) >= self.max_concurrent or
                        item["id"] not in reserved and len(set(self.active) | reserved) >= self.max_concurrent):
                    continue
                if self._mixed_writer_running(item):
                    continue
                if (not item.get("parent_id") and skill_for(item, self.skills[item["skill"]]).exclusive
                        and self._exclusive_running(exclude=item["id"])):
                    continue
                try:
                    if self.launch(item) is not None:
                        launched += 1
                except Exception as exc:
                    self._fail_launch(item["id"], exc)
        with self.lock:
            Maintenance(self.ledger).observe(self.launcher, self.maintenance_busy(),
                                              controller_started_at=self.maintenance_epoch,
                                              resource_check=self.maintenance_resources)
        return {"launched": launched, "reaped": reaped, "recovered": recovered}
