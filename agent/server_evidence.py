"""Read completed Go package outcomes without treating an interrupted suite as passed."""
import hashlib
import json
from pathlib import Path

from .server_test_plan import validate_plan


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def checked_file(root, name, sha):
    root = Path(root).resolve()
    path = root / name
    if (not isinstance(sha, str) or len(sha) != 64 or path.resolve() != path
            or not path.is_relative_to(root) or not path.is_file() or digest(path) != sha):
        raise ValueError('retained server report path/hash mismatch')
    return path


def completed_packages(report, module, plan, required_tests):
    plan = validate_plan(plan)
    if plan['tests'] or any('...' in p for p in plan['packages']):
        raise ValueError('retained evidence requires explicit complete packages')
    packages = {module + p[1:] if p != '.' else module for p in plan['packages']}
    if not packages or set(required_tests) != packages:
        raise ValueError('required tests must identify every selected package')
    states, starts, tests, active, skips, outside_failures = {}, {}, {}, set(), [], []
    with Path(report).open(encoding='utf-8') as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except ValueError:
                continue  # Go build diagnostics may precede JSON events.
            if not isinstance(event, dict):
                continue
            package, test, action = event.get('Package'), event.get('Test'), event.get('Action')
            if package not in packages:
                if action == 'fail':
                    outside_failures.append({'package': package, 'test': test})
                continue
            if action == 'fail':
                raise ValueError('selected server package contains a failure')
            if not test:
                if action == 'start':
                    starts[package] = starts.get(package, 0) + 1
                    states[package] = 'start'
                elif action in ('pass', 'skip'):
                    states[package] = action
            else:
                key = (package, test)
                if action == 'run':
                    active.add(key)
                elif action in ('pass', 'skip'):
                    active.discard(key)
                    tests[key] = action
                    if action == 'skip':
                        skips.append({'package': package, 'test': test})
    if active or any(starts.get(p) != 1 or states.get(p) != 'pass' for p in packages):
        raise ValueError('selected server packages are incomplete, repeated or skipped')
    for package, names in required_tests.items():
        if not isinstance(names, list) or not names or any(tests.get((package, name)) != 'pass' for name in names):
            raise ValueError('required server acceptance test missing or skipped')
    return {'packages_passed': sorted(packages), 'required_tests': required_tests,
            'skips': skips, 'outside_failures': outside_failures, 'whole_suite_passed': False}


def verify_manifest(root, manifest, *, candidate, generation, reservation):
    """The host operator binds historical provenance; hashes detect subsequent edits.

    This is not a worker API and does not infer source/service identity from test text.
    The operator must inspect those identities before submitting the manifest.
    """
    if (manifest.get('candidate_commit') != candidate or manifest.get('generation') != generation
            or manifest.get('reservation_id') != reservation or not manifest.get('provenance_review')
            or not manifest.get('files') or manifest.get('report') not in manifest['files']):
        raise ValueError('retained server evidence identity/provenance mismatch')
    paths = {name: checked_file(root, name, sha) for name, sha in manifest['files'].items()}
    plan = validate_plan(manifest['plan'])
    if not plan['race']:
        raise ValueError('retained server acceptance requires race evidence')
    metadata = json.loads(paths[manifest['source_metadata']].read_text(encoding='utf-8'))
    command = metadata.get('command', [])
    if (metadata.get('source_commit') != candidate or not isinstance(command, list)
            or '-race' not in command or '-json' not in command or '-run' in command
            or not ('./...' in command or all(p in command for p in plan['packages']))):
        raise ValueError('retained command does not certify selected candidate/race scope')
    resource = metadata.get('resource') or {}
    if (not isinstance(resource, dict) or resource.get('commit') != candidate
            or any((resource.get('environment') or {}).get(key) != '1'
                   for key in ('MONGO_REQUIRED', 'REDIS_REQUIRED', 'CLUSTER_REQUIRED'))):
        raise ValueError('retained service resource does not match candidate')
    result = completed_packages(paths[manifest['report']], manifest['module'], plan, manifest['required_tests'])
    if result['outside_failures']:
        raise ValueError('outside failures require explicit comparison, not automatic reuse')
    return result
