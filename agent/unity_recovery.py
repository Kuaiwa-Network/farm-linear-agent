"""Host-only editor repair. Workers never get process-control authority."""
from pathlib import Path

from .identity import source_snapshot
from .slots import SlotError


class SourceMetaReconciled(SlotError):
    """Importer metadata was preserved and the slot is safe to reuse while closed."""

    def __init__(self, evidence):
        super().__init__('imported .meta changes archived; slot returned closed for an exact-commit retry', stage='git')
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
            raise SlotError(f'late Editor has compilation/console errors: {errors[0]}', stage='compile')
        result = pool.mcp.probe(slot, {'repository': str(folder), 'commit_sha': commit,
                                     'build_target': entry.get('build_target')})
        if result.get('aggregate') != 'match' or source_snapshot(folder) != before:
            raise SlotError('late Editor identity did not match', stage='probe')
        if (result.get('editor') or {}).get('instance') != instance:
            raise SlotError('late Editor identity has no matching verified instance', stage='probe')
        return commit, instance

    def repair(self, recovery):
        pool = self.pool
        slot, entry = self._slot(recovery['slot_id'])
        if slot['state'] != 'held' or pool.ledger.active_reservation_on(slot['slot_id']):
            raise SlotError('editor repair requires an isolated held slot', stage='probe')
        folder = Path(slot['folder'])
        commit = recovery.get('commit_sha') or pool.worktrees.head(folder)
        reusable = False
        if pool.editor_is_open(slot):
            try:
                pool.mcp.cancel_tests(slot)
                deadline = pool.clock() + 10
                while pool.clock() < deadline:
                    if pool.mcp.quiescent(slot, 'interactive'):
                        # A failed reuse (e.g. stale runner/console state) must
                        # escalate on the next durable attempt.
                        reusable = recovery.get('attempts', 1) == 1 and pool.worktrees.head(folder) == commit
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
        pool.mcp.refresh(slot)
        pool.wait_for_quiet(slot, entry['quiet_timeout'])
        errors = pool.mcp.console_errors_since(slot, commit)
        if errors:
            raise SlotError(f'recovered editor has compilation/console errors: {errors[0]}', stage='compile')
        result = pool.mcp.probe(slot, {'repository': str(folder), 'commit_sha': commit,
                                       'build_target': entry.get('build_target')})
        if result.get('aggregate') != 'match':
            raise SlotError('recovered editor identity did not match', stage='probe')
        return commit, instance
