"""Read-only ledger snapshots, shared by `doctor` and the status monitor.

Never construct Ledger in a reader: its constructor creates the database and runs migrations.
"""
from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3


@contextmanager
def snapshot_connection(path, *, timeout=2.0):
    """Yield a connection that can only read, holding one read transaction for the whole block.

    `mode=ro` refuses to create a missing file and `query_only` refuses writes even from a bug; the
    transaction keeps every query on the same snapshot. SQLite may create its `-wal` and `-shm` side files
    beside a ledger whose service is stopped, and they remain after the read; the ledger itself is never
    modified.
    """
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=timeout)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        yield db
