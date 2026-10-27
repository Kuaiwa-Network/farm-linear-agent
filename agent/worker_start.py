"""Exclusive, non-printing claim-token storage for one worker startup."""
import os
import sqlite3
import time
from pathlib import Path

from .config import Paths
from .ledger import LedgerError


def claim_to_file(ledger, item_id, worker_id, destination, config):
    command_entered_at = time.time()
    ledger.item(item_id)  # Refuse unknown/traversing IDs before creating any file.
    paths = Paths(config)
    root = paths.runs / item_id
    path = Path(destination)
    # Only the normal per-item token is writable. Resolve the parent, never the
    # final component: O_EXCL must refuse existing files and dangling links too.
    if (not path.is_absolute() or path.name != 'token' or
            path.parent.resolve() != root.resolve() or root.is_symlink() or root.is_junction()):
        raise LedgerError('claim token must be the configured item state_dir/token')
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            result = ledger.claim(item_id, worker_id=worker_id)
            stream.write(result.pop('token'))
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        # Do not retry a possibly successful claim, overwrite or unlink evidence.
        # The normal launcher archives this file when starting a later attempt.
        raise LedgerError('claim token storage failed; stop and preserve the file, do not claim again') from exc
    timing = {'command_entered_at': command_entered_at, 'claim_completed_at': time.time()}
    try:
        ledger.note(item_id, 'worker_claim_timing', details=timing)
    except sqlite3.Error:
        # The claim and private token are already durable. Optional timing must
        # not turn that successful claim into an ambiguous startup failure.
        timing['audit_recorded'] = False
    return {**result, 'startup_timing': timing, 'token_file': str(path)}
