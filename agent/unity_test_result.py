"""Bounded read of retained Unity test evidence, never a test retry or verdict."""
import json
import math
from pathlib import Path
import re

from .ledger import LedgerError


def retained_result(client, job_id, acquired_at):
    if not isinstance(job_id, str) or not re.fullmatch(r'[0-9a-f]{32}', job_id):
        raise LedgerError('expected the exact 32-character Unity test job ID')
    if (not isinstance(acquired_at, (int, float)) or isinstance(acquired_at, bool)
            or not math.isfinite(acquired_at) or acquired_at <= 0):
        raise LedgerError('reservation acquisition time is unavailable')
    source = Path(__file__).with_name('probes').joinpath('test-result.cs.txt').read_text(encoding='utf-8')
    source = source.replace('__JOB_ID__', job_id).replace('__ACQUIRED_MS__', str(math.ceil(acquired_at * 1000)))
    observed = client.call_tool('execute_code', {'action': 'execute', 'code': source, 'safety_checks': True})
    report = observed.get('result')
    if isinstance(report, str):
        report = json.loads(report)
    if not isinstance(report, dict) or report.get('job_id') != job_id or report.get('schema') != 1:
        raise LedgerError('Unity retained report response is missing or mismatched')
    return report
