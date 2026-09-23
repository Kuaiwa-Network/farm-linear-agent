"""Host-only editor repair. Workers never get process-control authority."""
from pathlib import Path

from .slots import SlotError


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
