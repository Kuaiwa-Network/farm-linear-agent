"""Bind helper reports to runtime transcripts kept outside the issue worker write roots."""
import json
import os
from pathlib import Path
import re
from uuid import UUID

from .review_policy import ReviewError
from .worktrees import _is_link


def evidence_root(state_dir):
    return state_dir.parent.parent / 'review-transcripts' / state_dir.name


def prepare(home, runtime, writable_roots=()):
    directory = 'projects' if runtime == 'claude' else 'sessions'
    # Only the native CLI writes the target. Always read from the controller-selected
    # location, even if a worker subsequently replaces its in-home directory link.
    attempt = home.parent
    target = evidence_root(attempt.parent) / attempt.name
    if (any(target.resolve().is_relative_to(Path(root).resolve()) for root in writable_roots)
            or any(_is_link(p) for p in (target.parent, target.parent.parent))):
        raise ReviewError('native review transcripts require a controller-owned location outside worker write roots')
    target.mkdir(parents=True, exist_ok=False)
    if os.name == 'nt':
        import _winapi
        _winapi.CreateJunction(str(target), str(home / directory))
    else:
        (home / directory).symlink_to(target, target_is_directory=True)


def verify_native_report(state_dir, report, runtime):
    """Require the actual native child output for this ticket."""
    claude = runtime == 'claude'
    try:
        reviewer = report['reviewer']
        parent = str(UUID(os.environ['CLAUDE_CODE_SESSION_ID' if claude else 'CODEX_THREAD_ID']))
        home = Path(os.environ['CLAUDE_CONFIG_DIR' if claude else 'CODEX_HOME']).absolute()
    except (KeyError, AttributeError, TypeError, ValueError) as exc:
        raise ReviewError('review requires the current native parent and child IDs') from exc
    agent_path = not claude and isinstance(reviewer, str) and re.fullmatch(r'/root/[a-zA-Z0-9_-]{1,100}', reviewer)
    if (home.name != 'home' or home.parent.parent != state_dir.absolute()
            or not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12}', home.parent.name)
            or not isinstance(reviewer, str) or not (agent_path or re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', reviewer))
            or reviewer == parent):
        raise ReviewError('review must come from a child of the current isolated attempt')
    root = evidence_root(state_dir) / home.parent.name
    pattern = f'*/{parent}/subagents/agent-{reviewer}.jsonl' if claude else f'*/*/*/rollout-*-{reviewer}.jsonl'
    if agent_path:
        pattern = '*/*/*/rollout-*.jsonl'
    expected = {'ticket': report.get('ticket'), 'verdict': report.get('verdict')}
    for path in root.glob(pattern):
        if any(_is_link(p) for p in (path, *path.parents) if p.is_relative_to(root.parent.parent)) or path.stat().st_nlink != 1:
            raise ReviewError('unexpected linked native review transcript')
        with path.open(encoding='utf-8') as stream:
            if claude:
                final = None
                for line in stream:
                    entry = json.loads(line)
                    if entry.get('type') == 'assistant' and entry.get('agentId') == reviewer and entry.get('isSidechain'):
                        parts = entry.get('message', {}).get('content', [])
                        final = ''.join(part['text'] for part in parts if part.get('type') == 'text')
            else:
                meta = json.loads(stream.readline()).get('payload', {})
                source = meta.get('source')
                spawn = source.get('subagent', {}).get('thread_spawn', {}) if isinstance(source, dict) else {}
                identity = spawn.get('agent_path') if agent_path else meta.get('id')
                if identity != reviewer or spawn.get('parent_thread_id') != parent or spawn.get('depth') != 1:
                    continue
                final = None
                for line in stream:
                    if '"task_complete"' in line:
                        event = json.loads(line)
                        entry = event.get('payload', {})
                        if event.get('type') == 'event_msg' and entry.get('type') == 'task_complete':
                            final = entry.get('last_agent_message')
            try:
                if json.loads(final or '') == expected:
                    return reviewer if claude else str(UUID(meta['id']))
            except ValueError:
                pass
    raise ReviewError('no matching completed native child verdict for this ticket')
