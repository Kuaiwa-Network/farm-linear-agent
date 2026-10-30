"""Host-only editor repair. Workers never get process-control authority."""
from contextlib import nullcontext
import json
from pathlib import Path

from .identity import CHECKS, source_snapshot
from .slots import SlotError, console_failure


class SourceMetaReconciled(SlotError):
    """Importer metadata was preserved and the slot is safe to reuse while closed."""

    def __init__(self, evidence):
        super().__init__('imported .meta changes archived; slot returned closed for an exact-commit retry', stage='git')
        self.evidence = evidence


class CandidateCompileFailure(SlotError):
    """A fresh environment-recovery Editor isolated a candidate compiler error."""

    def __init__(self, failure, evidence):
        super().__init__(str(failure), stage='compile')
        self.evidence = evidence


class UnityRecovery:
    def __init__(self, pool):
        self.pool = pool

    def _slot(self, slot_id):
        pool = self.pool
        slot = pool.ledger.slot(slot_id)
        entry = pool.entries.get(slot_id)
        if (not slot or not entry or slot['host'] != pool.host
                or Path(slot['folder']).resolve() != pool.folder(entry).resolve()):
            raise SlotError('recovery target does not match the configured local slot', stage='probe')
        return slot, entry

    def inspect(self, slot):
        verified, _ = self._slot(slot['slot_id'])
        return self.pool.mcp.recovery_snapshot(verified)

    def revalidate(self, recovery):
        """Certify a late-started Editor without restarting or modifying it."""
        pool = self.pool
        slot, entry = self._slot(recovery['slot_id'])
        if slot['state'] != 'held' or pool.ledger.active_reservation_on(slot['slot_id']):
            raise SlotError('late Editor validation requires an isolated held slot', stage='probe')
        folder = Path(slot['folder'])
        pool.worktrees.worktree_entry(entry['repo'], folder)
        before = source_snapshot(folder)
        commit = recovery.get('commit_sha')
        if not commit or before['commit_sha'] != commit or before['dirty']:
            raise SlotError('late Editor source must be clean at the recovery commit', stage='git')
        if not pool.editor_is_open(slot):
            raise SlotError('late Editor is not idle on the configured project', stage='editor')
        # A timed-out first start never saved slots.instance. Discover the live
        # project and pin this observation without changing the ledger yet.
        instance = pool.mcp.discover_instance(slot)
        if not instance:
            raise SlotError('late Editor has no connected project instance', stage='probe')
        slot = {**slot, 'instance': instance}
        if not pool.mcp.quiescent(slot, 'interactive'):
            raise SlotError('late Editor is not idle on the configured project', stage='editor')
        errors = pool.mcp.console_errors_since(slot, commit)
        if errors:
            raise console_failure(errors, 'late Editor')
        result = pool.mcp.probe(slot, {'repository': str(folder), 'commit_sha': commit,
                                     'build_target': entry.get('build_target')})
        if result.get('aggregate') != 'match' or source_snapshot(folder) != before:
            raise SlotError('late Editor identity did not match', stage='probe')
        if (result.get('editor') or {}).get('instance') != instance:
            raise SlotError('late Editor identity has no matching verified instance', stage='probe')
        return commit, instance

    def repair(self, recovery):
        if recovery.get('recovery_kind') == 'candidate':
            return self.repair_candidate(recovery)
        pool = self.pool
        slot, entry = self._slot(recovery['slot_id'])
        if slot['state'] != 'held' or pool.ledger.active_reservation_on(slot['slot_id']):
            raise SlotError('editor repair requires an isolated held slot', stage='probe')
        folder = Path(slot['folder'])
        commit = recovery.get('commit_sha') or pool.worktrees.head(folder)
        reusable = False
        if pool.editor_is_open(slot):
            try:
                if recovery.get('recovery_kind') == 'setup' and recovery.get('attempts', 1) == 1:
                    # This Editor was never handed to the worker. Let an ongoing
                    # import/reload reconnect before escalating to a process restart.
                    pool.wait_for_quiet(slot, entry['quiet_timeout'])
                pool.mcp.cancel_tests(slot)
                deadline = pool.clock() + 10
                while pool.clock() < deadline:
                    if pool.mcp.quiescent(slot, 'interactive'):
                        # Execution failures can leave native process state broken
                        # even after a clean domain reload. A new recovery record
                        # must not give that same Editor another first-attempt reuse.
                        # Only pre-handoff reconnect recovery may reuse a quiet
                        # Editor. Console environment faults require a new process.
                        reusable = (recovery.get('recovery_kind') == 'setup'
                                    and recovery.get('attempts', 1) == 1
                                    and pool.worktrees.head(folder) == commit)
                        break
                    pool.sleep(1)
            except Exception:
                pass
        if not reusable and pool.editor_is_open(slot):
            pool.mcp.terminate(slot, entry['close_timeout'])
        if not reusable and pool.editor_is_open(slot):
            raise SlotError('old editor survived recovery; its lock is retained', stage='editor')
        if not reusable:
            pool.clear_stale_lock(folder, lambda: pool.editor_is_open(slot))
        # Shared broker stays running; replacing one editor must not interrupt
        # another slot's MCP session. The restarted editor reconnects to it.
        if source_snapshot(folder)['dirty']:
            if pool.worktrees.head(folder) != commit:
                raise SlotError('dirty slot HEAD differs from the held commit', stage='git')
            if pool.editor_is_open(slot):
                pool.mcp.terminate(slot, entry['close_timeout'])
                if pool.editor_is_open(slot):
                    raise SlotError('old editor survived metadata recovery; its lock is retained', stage='editor')
                pool.clear_stale_lock(folder, lambda: pool.editor_is_open(slot))
            evidence_dir = recovery.get('evidence_dir')
            if evidence_dir:
                evidence = pool.worktrees.reconcile_slot_meta(entry['repo'], folder,
                                                               recovery['id'], evidence_dir)
                if evidence is not None:
                    raise SourceMetaReconciled(evidence)
            raise SlotError('dirty slot source retained; automatic recovery requires metadata-only changes', stage='git')
        if not pool.worktrees.slot_clean(folder):
            raise SlotError('tracked slot changes retained; automatic recovery cannot discard them', stage='git')
        if pool.worktrees.head(folder) != commit:
            pool.worktrees.checkout_commit(folder, commit)
        if pool.worktrees.pointers_remain(folder):
            raise SlotError('recovery found unhydrated LFS assets', stage='git')
        instance = slot['instance'] if reusable else pool.open_editor(slot, entry)
        pool.ledger.set_slot_state(slot['slot_id'], 'held', instance=instance)
        slot = pool.ledger.slot(slot['slot_id'])
        pool.refresh_and_wait(slot, entry['quiet_timeout'])
        errors = pool.mcp.console_errors_since(slot, commit)
        if errors:
            failure = console_failure(errors, 'recovered editor')
            if pool.keep_open and recovery.get('recovery_kind') == 'environment' and failure.stage == 'compile':
                # This path has replaced the old process and refreshed the exact
                # candidate. Baseline errors and late/reused Editors cannot
                # trigger this diagnosis. Preserve evidence before changing pins.
                source = source_snapshot(folder)
                if source['commit_sha'] != commit:
                    raise SlotError('recovered compiler diagnostics have changed source HEAD', stage='git')
                raise CandidateCompileFailure(failure, {'slot_id': slot['slot_id'], 'commit_sha': commit,
                    'instance': instance, 'errors': errors, 'source': source})
            raise failure
        result = pool.mcp.probe(slot, {'repository': str(folder), 'commit_sha': commit,
                                       'build_target': entry.get('build_target')})
        if result.get('aggregate') != 'match':
            raise SlotError('recovered editor identity did not match', stage='probe')
        return commit, instance

    def candidate_baseline(self, recovery, slot, entry, evidence_dir):
        """Keep the selected clean baseline stable across interrupted repair attempts."""
        pool = self.pool
        plan = evidence_dir / 'candidate-baseline.json'
        if plan.exists():
            if plan.is_symlink() or not plan.is_file():
                raise SlotError('candidate recovery plan is not a regular owned file', stage='git')
            data = json.loads(plan.read_text(encoding='utf-8'))
            if data.get('failed_commit') != recovery['commit_sha'] or data.get('slot_id') != slot['slot_id']:
                raise SlotError('candidate recovery plan does not match its fenced inputs', stage='git')
            baseline = data['baseline_commit']
        else:
            main = pool.worktrees.resolve_commit(entry['repo'])
            choices = [main]
            peers = {key for key, value in pool.entries.items()
                     if (value['repo'], value['build_target']) == (entry['repo'], entry['build_target'])}
            for row in pool.ledger.connection.execute('SELECT slot_id,result_json FROM identity_observations ORDER BY created_at DESC'):
                if row['slot_id'] not in peers:
                    continue
                observation = json.loads(row['result_json'])
                if self.verified_baseline(observation, entry):
                    choices.append((observation.get('expected') or {}).get('commit_sha'))
            for row in pool.ledger.connection.execute("SELECT item_id,details FROM audit WHERE kind='unity_editor_verified' ORDER BY id DESC"):
                if row['item_id'] in peers:
                    observation = json.loads(row['details']).get('observation') or {}
                    if self.verified_baseline(observation, entry):
                        choices.append((observation.get('expected') or {}).get('commit_sha'))
            from .resource_recovery import RecoveryStore
            store = RecoveryStore(pool.ledger)
            baseline = next((commit for commit in choices if pool.worktrees.COMMIT.fullmatch(commit or '')
                and commit != recovery['commit_sha'] and not store.known_candidate(pool.host, peers, commit)), None)
            if baseline is None:
                raise SlotError('No clean baseline distinct from the failed candidate is available; correct main or provide a verified candidate', stage='git')
            data = {'slot_id': slot['slot_id'], 'failed_commit': recovery['commit_sha'], 'baseline_commit': baseline}
            plan.write_text(json.dumps(data, indent=2), encoding='utf-8')
        if (not pool.worktrees.COMMIT.fullmatch(baseline or '') or baseline == recovery['commit_sha']
                or not pool.worktrees.has_commit(entry['repo'], baseline)):
            raise SlotError('candidate recovery baseline is not a verified clone commit', stage='git')
        return baseline

    @staticmethod
    def verified_baseline(observation, entry):
        expected = observation.get('expected') or {}
        return (observation.get('aggregate') == 'match'
                and all((observation.get('checks') or {}).get(key) == 'match' for key in CHECKS)
                and expected.get('build_target') == entry['build_target']
                and (observation.get('source_after') or {}).get('commit_sha') == expected.get('commit_sha'))

    def repair_candidate(self, recovery):
        """Preserve failed inputs and reuse an idle Editor independently of branch repair."""
        pool = self.pool
        slot, entry = self._slot(recovery['slot_id'])
        if slot['state'] != 'held' or pool.ledger.active_reservation_on(slot['slot_id']):
            raise SlotError('candidate repair requires a detached held slot', stage='probe')
        folder = Path(slot['folder'])
        pool.worktrees.worktree_entry(entry['repo'], folder)
        evidence_dir = Path(recovery['evidence_dir'])
        evidence_dir.mkdir(parents=True, exist_ok=True)
        baseline = self.candidate_baseline(recovery, slot, entry, evidence_dir)
        plan = json.loads((evidence_dir / 'candidate-baseline.json').read_text(encoding='utf-8'))
        previous_baseline = plan.get('previous_baseline_commit')
        if previous_baseline is not None and not pool.worktrees.COMMIT.fullmatch(previous_baseline):
            raise SlotError('previous candidate recovery baseline is not a commit', stage='git')
        reusable = False
        if pool.editor_is_open(slot):
            try:
                pool.mcp.cancel_tests(slot)
                pool.wait_for_quiet(slot, min(10, entry['quiet_timeout']))
                reusable = recovery['attempts'] == 1 and pool.mcp.quiescent(slot, 'interactive')
            except Exception:
                # An unreachable MCP cannot certify idle reuse. Process ownership
                # and confirmed exit still fence the shutdown fallback below.
                reusable = False
        if not reusable and pool.editor_is_open(slot):
            pool.mcp.terminate(slot, entry['close_timeout'])
        if not reusable:
            if pool.editor_is_open(slot):
                raise SlotError('Editor survived candidate recovery; source and lock retained', stage='editor')
            pool.clear_stale_lock(folder, lambda: pool.editor_is_open(slot))
        # Suspending automatic refresh prevents an idle live importer from observing the
        # intermediate metadata restoration. A restart after interruption resets its native
        # refresh counter; attempts after the first never reuse that uncertain Editor.
        guard = pool.mcp.suspend_imports(slot) if reusable else nullcontext()
        with guard:
            before = source_snapshot(folder)
            if before['commit_sha'] not in (recovery['commit_sha'], baseline, previous_baseline):
                # A known-input refusal can arrive while this slot is still at another
                # verified clean candidate; dirty foreign inputs are never discarded.
                if before['dirty']:
                    raise SlotError('dirty slot moved away from the fenced candidate; source retained', stage='git')
            if before['dirty']:
                archive_id = recovery['id'] if before['commit_sha'] == recovery['commit_sha'] else f"{recovery['id']}-baseline-{recovery['attempts']}"
                archive = pool.worktrees.reconcile_slot_meta(entry['repo'], folder, archive_id,
                    evidence_dir if archive_id == recovery['id'] else evidence_dir / archive_id)
                if archive is None:
                    raise SlotError('non-metadata source changes retained for human review', stage='git')
            if pool.worktrees.head(folder) != baseline:
                pool.worktrees.checkout_commit(folder, baseline)
            if not pool.worktrees.slot_clean(folder) or pool.worktrees.pointers_remain(folder):
                raise SlotError('candidate recovery did not produce hydrated clean baseline inputs', stage='git')
        instance = pool.mcp.discover_instance(slot) if reusable else pool.open_editor(slot, entry)
        pool.ledger.set_slot_state(slot['slot_id'], 'held', instance=instance)
        slot = pool.ledger.slot(slot['slot_id'])
        pool.refresh_and_wait(slot, entry['quiet_timeout'])
        errors = pool.mcp.console_errors_since(slot, baseline)
        if errors:
            raise console_failure(errors, 'recovery baseline')
        observation = pool.mcp.probe(slot, {'repository': str(folder), 'commit_sha': baseline,
                                           'build_target': entry.get('build_target')})
        if observation.get('aggregate') != 'match' or source_snapshot(folder)['dirty']:
            raise SlotError('candidate recovery baseline identity did not match', stage='probe')
        pool.ledger.note(slot['slot_id'], 'unity_editor_verified', 'candidate recovery baseline verified',
                         {'observation': observation, 'recovery_id': recovery['id']})
        return baseline, instance

    def metadata_candidate(self, recovery):
        """Inspect after worker fencing, before queuing an unchanged execution retry."""
        slot, entry = self._slot(recovery['slot_id'])
        folder = Path(slot['folder'])
        self.pool.worktrees.worktree_entry(entry['repo'], folder)
        snapshot = source_snapshot(folder)
        return (snapshot['commit_sha'] == recovery['commit_sha'] and bool(snapshot['dirty'])
                and all(row['path'].startswith('Assets/') and row['path'].casefold().endswith('.meta')
                    and row['status'] in (' M', 'M ', 'MM', '??') and row['sha256'] is not None
                    for row in snapshot['dirty']))
