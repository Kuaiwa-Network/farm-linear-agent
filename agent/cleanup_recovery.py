"""Trusted host recovery for legacy Windows attempts lacking process containment."""
from datetime import datetime
import json
import os
import re
import subprocess

from .config import Paths
from .ledger import Ledger, LedgerError
from .scheduler import TERMINAL


def windows_boot_time():
    if os.name != 'nt':
        raise LedgerError('legacy boot recovery is Windows-only')
    output = subprocess.run(['powershell', '-NoProfile', '-Command',
        '(Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime().ToString("o")'],
        capture_output=True, text=True, check=True, timeout=15).stdout.strip()
    return datetime.fromisoformat(output).timestamp()


def recover_after_boot(config, item_id, reason):
    """Record OS boot evidence; normal scheduler preservation/removal still does the cleanup."""
    if not reason or not reason.strip():
        raise LedgerError('operator reason is required')
    boot = windows_boot_time()
    paths = Paths(config)
    ledger = Ledger(paths.ledger)
    try:
        with ledger._transaction():
            item = ledger.item(item_id)
            if item['state'] not in TERMINAL:
                raise LedgerError('cleanup recovery requires a terminal item')
            if item['host'] != config.host:
                raise LedgerError('cleanup recovery requires the original host')
            if ledger.active_reservation(item_id) is not None:
                raise LedgerError('resource reservation must be settled first')
            record = ledger.cleanup_record(item_id)
            if not record or record['done'] or record['removing']:
                raise LedgerError('no pending cleanup available for recovery')
            attempts = ledger.connection.execute(
                "SELECT reason,created_at FROM audit WHERE item_id=? AND kind='worker'", (item_id,)).fetchall()
            if not attempts or boot <= max(row['created_at'] for row in attempts):
                raise LedgerError('a machine restart after the last worker launch is required')
            pids = set()
            for row in attempts:
                match = re.fullmatch(r'pid ([0-9]+) on (.+)', row['reason'])
                if not match or match[2] != config.host:
                    raise LedgerError('unverified worker host or process identity')
                pids.add(int(match[1]))
            root = paths.runs / item_id
            for path in root.glob('*/process.json'):
                if path.stat().st_mtime >= boot:
                    raise LedgerError('an attempt record is newer than the machine restart')
                attempt = json.loads(path.read_text(encoding='utf-8'))
                if attempt.get('state') == 'not_started':
                    continue
                if attempt.get('pid') not in pids:
                    raise LedgerError('attempt identity is not covered by launch audit')
            pids.update(record['result'].get('processes', []))
            for path in root.glob('*/killed.json'):
                if path.stat().st_mtime >= boot:
                    raise LedgerError('a teardown record is newer than the machine restart')
                pids.update(json.loads(path.read_text(encoding='utf-8'))['descendants'])
            proof = {'processes': sorted(pids), 'boot_time': boot, 'host': config.host}
            # Authoritative evidence is append-only: an in-flight cleanup sweep cannot overwrite it.
            ledger._audit(item_id, 'cleanup_recovery', reason, proof)
            ledger.connection.execute('UPDATE job_cleanup SET error=NULL,updated_at=? WHERE item_id=?',
                                      (ledger.clock(), item_id))
            return proof
    finally:
        ledger.close()
