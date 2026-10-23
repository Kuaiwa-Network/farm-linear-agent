"""Read-only, allowlisted startup timing. Never emit prompts, argv, output or credentials."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import re


def seconds(value):
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError, AttributeError):
        return None


def first_execution(lines):
    pending = None
    cell = None
    calls = {}
    for line in lines:
        if len(line) > 4 * 1024 * 1024:
            continue
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if value.get('type') != 'response_item':
            continue
        part = value.get('payload') or {}
        stamp = value.get('timestamp')
        if seconds(stamp) is None:
            continue
        kind, name, key = part.get('type'), part.get('name'), part.get('call_id')
        if kind in ('custom_tool_call', 'function_call'):
            if pending is None and name in ('exec', 'exec_command', 'functions.exec', 'functions.exec_command'):
                pending = {'submitted_at': stamp, 'call_id': key, 'direct': name.endswith('exec_command')}
            elif name in ('wait', 'functions.wait') and cell:
                try:
                    args = json.loads(part.get('arguments', '{}'))
                except ValueError:
                    continue
                if args.get('cell_id') == cell:
                    calls[key] = cell
        elif pending and kind in ('custom_tool_call_output', 'function_call_output'):
            if key != pending['call_id'] and key not in calls:
                continue
            output = str(part.get('output', ''))
            yielded = re.search(r'Script running with cell ID ([A-Za-z0-9_-]+)', output)
            if yielded:
                cell = yielded[1]
                continue
            duration = re.search(r'wall_time_seconds[^0-9]{0,12}([0-9.]+)', output)
            if duration is None and pending['direct']:
                duration = re.search(r'Wall time: ([0-9.]+)', output)
            if not duration and cell:
                continue  # a yielded/empty wait is not execution completion
            elapsed = seconds(stamp) - seconds(pending['submitted_at'])
            measured = float(duration[1]) if duration else None
            return {'submitted_at': pending['submitted_at'], 'returned_at': stamp,
                    'tool_elapsed_seconds': round(elapsed, 6), 'reported_command_seconds': measured,
                    'outside_reported_command_seconds': round(max(0, elapsed-measured), 6) if measured is not None else None,
                    'unattributed_components': ['tool_dispatch', 'automatic_review', 'sandbox_initialization', 'result_delivery']}
    return {'submitted_at': pending['submitted_at'], 'state': 'pending_or_unobserved'} if pending else None


def inspect(attempt):
    from .launcher import _read_worker_text
    attempt = Path(attempt).absolute()
    if any(p.is_symlink() or p.is_junction() for p in (attempt, *attempt.parents)):
        raise ValueError('attempt path contains a link')
    process = json.loads(_read_worker_text(attempt/'process.json'))
    timing = {k: process[k] for k in ('preparation_started_at', 'process_started_at')
              if type(process.get(k)) in (int, float)}
    results = []
    for path in sorted((attempt/'home'/'sessions').glob('*/*/*/*.jsonl')):
        if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
            continue
        with path.open(encoding='utf-8') as stream:
            result = first_execution(stream)
        if result:
            results.append(result)
    return {'schema': 1, 'launcher': timing, 'executions': results,
            'interpretation': 'Intervals outside reported command execution are not FarmBot queue time. '
            'They do not isolate review, dispatch, sandbox or delivery without corresponding runtime events.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--attempt', required=True)
    args = parser.parse_args()
    print(json.dumps(inspect(args.attempt), indent=2))


if __name__ == '__main__':
    main()
