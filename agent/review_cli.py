"""Claim-bound review worker commands; no GitHub mutation or merge command."""
import argparse
import json
import os
from pathlib import Path
import sys
import sqlite3
import time
from uuid import UUID

from .config import Paths, load_config
from .environment import check_worker_state
from .ledger import Ledger
from .review_policy import ReviewError, settings
from .review_store import ReviewStore


def main(argv=None):
    started_at, started_clock = time.time(), time.monotonic()
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', required=True)
    parser.add_argument('command', choices=('claim', 'renew', 'submit'))
    parser.add_argument('--attempt', required=True)
    parser.add_argument('--token-file', required=True)
    parser.add_argument('--input')
    args = parser.parse_args(argv)
    ledger = None
    try:
        if str(UUID(args.attempt)) != args.attempt:
            raise ReviewError('review attempt must be its canonical UUID')
        check_worker_state(args.db)  # Before opening/migrating any ledger.
        config = load_config(secure_permissions=False)
        paths = Paths(config)
        policy = settings(config.auto_merge, config.repos)
        if policy['mode'] == 'off' or Path(args.db).resolve() != paths.ledger.resolve():
            raise ReviewError('review command requires the enabled configured ledger')
        if os.environ.get('FARMBOT_ITEM_ID') != args.attempt:
            raise ReviewError('review command must belong to this launched attempt')
        token_file = Path(args.token_file)
        expected = paths.runs / 'review' / args.attempt / 'token'
        if token_file.resolve() != expected.resolve() or token_file.is_symlink():
            raise ReviewError('review token must stay in its own state directory')
        ledger = Ledger(args.db)
        store = ReviewStore(ledger)
        if args.command == 'claim':
            # Exclusive file creation prevents overwriting credentials after an ambiguous claim.
            with os.fdopen(os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w', encoding='utf-8') as handle:
                token = store.claim(args.attempt, policy['worker_seconds'])
                handle.write(token)
            # Distinguish scheduling before CLI startup from time inside the CLI.
            result = {'claimed': True, 'attempt': args.attempt,
                      'timing': {'cli_started_at': started_at,
                                 'cli_elapsed_ms': round((time.monotonic() - started_clock) * 1000, 1)}}
        else:
            token = token_file.read_text(encoding='utf-8').strip()
            if args.command == 'renew':
                with ledger._transaction():
                    store.owned(args.attempt, token)
                    ledger.connection.execute('UPDATE review_attempts SET expires_at=? WHERE id=?',
                        (ledger.clock() + policy['worker_seconds'], args.attempt))
                result = {'renewed': True}
            else:
                source = Path(args.input or '')
                if source.stat().st_size > 65536:
                    raise ReviewError('review result exceeds 64 KiB')
                store.submit(args.attempt, token, json.loads(source.read_text(encoding='utf-8')))
                result = {'submitted': True, 'exit_worker': True}
        print(json.dumps(result))
        return 0
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print('farmbot review: ' + str(exc), file=sys.stderr)
        return 1
    finally:
        if ledger is not None:
            ledger.close()


if __name__ == '__main__':
    raise SystemExit(main())
