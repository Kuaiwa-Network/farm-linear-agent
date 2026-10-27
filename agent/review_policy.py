"""Validated controller policy for opt-in review. Candidate content cannot alter it."""
import fnmatch
import hashlib
import json
import re

SHA = re.compile(r'[0-9a-f]{40}\Z')
PR = re.compile(r'https://github\.com/([^/]+/[^/]+)/pull/([1-9][0-9]*)\Z')
DEFAULT_SENSITIVE = ('.github/*', 'AGENTS.md', '**/AGENTS.md', 'REVIEW.md', '**/REVIEW.md',
                     'ci/*', 'scripts/deploy*', 'agent/review*', 'skills/review/*')
MODES = ('off', 'observe', 'shadow', 'merge')
DEFAULT_ORDER = ('Farm-Contract', 'common', 'farm-hive', 'farmgui', 'Farm-Client')


class ReviewError(ValueError):
    pass


class ReviewUnavailable(RuntimeError):
    """Missing infrastructure evidence; never a negative safety verdict."""


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode('utf-8')).hexdigest()


def commit(value):
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise ReviewError('review requires a full commit SHA')
    return value


def settings(raw, repositories=None):
    if not isinstance(raw, dict):
        raise ReviewError('auto_merge must be an object')
    allowed = {'mode', 'workers', 'depth', 'repair_limit', 'retry_limit', 'worker_seconds',
               'poll_seconds', 'hold_labels', 'repositories', 'authority', 'circuit_limit', 'circuit_days', 'ci_timeout_seconds'}
    if set(raw) - allowed:
        raise ReviewError('unknown auto_merge setting')
    policy = {'mode': 'off', 'workers': 3, 'depth': 3, 'repair_limit': 3, 'retry_limit': 3,
              'worker_seconds': 2700, 'poll_seconds': 300, 'ci_timeout_seconds': 86400,
              'hold_labels': ['needs-person', 'hold-auto-merge'],
              'repositories': {}, 'authority': '', 'circuit_limit': 2, 'circuit_days': 7, **raw}
    if policy['mode'] not in MODES:
        raise ReviewError('auto_merge.mode must be off, observe, shadow or merge')
    for key, maximum in (('workers', 32), ('depth', 10), ('repair_limit', 10), ('retry_limit', 10),
                         ('worker_seconds', 86400), ('poll_seconds', 3600), ('circuit_limit', 100),
                         ('circuit_days', 365), ('ci_timeout_seconds', 604800)):
        if type(policy[key]) is not int or not 1 <= policy[key] <= maximum:
            raise ReviewError(f'auto_merge.{key} must be a positive bounded integer')
    if not isinstance(policy['authority'], str):
        raise ReviewError('auto_merge.authority must name the designated controller instance')
    if policy['mode'] != 'off' and not policy['authority']:
        raise ReviewError('review requires a designated queue authority')
    labels = policy['hold_labels']
    if not isinstance(labels, list) or not labels or any(not isinstance(v, str) or not v for v in labels):
        raise ReviewError('review hold_labels must be nonempty strings')
    if not isinstance(policy['repositories'], dict):
        raise ReviewError('review repositories must be an object')
    checked = {}
    for name, value in policy['repositories'].items():
        if repositories is not None and name not in repositories:
            raise ReviewError('review repository is not a configured publishing destination')
        keys = {'method', 'backend', 'checks', 'source_checks', 'sensitive', 'aot', 'generated', 'max_lines', 'dependencies',
                'candidate_workflow', 'workflow_ref', 'pin', 'evidence_only'}
        if not isinstance(value, dict) or set(value) - keys:
            raise ReviewError('unknown review repository setting')
        row = {'method': 'merge' if name == 'common' else 'squash', 'backend': 'github_queue',
               'checks': [], 'source_checks': [], 'sensitive': [], 'aot': [], 'generated': [], 'max_lines': 1500, 'dependencies': [],
               'candidate_workflow': '', 'workflow_ref': 'main', 'pin': None, 'evidence_only': False, **value}
        if 'dependencies' not in value and name in DEFAULT_ORDER:
            row['dependencies'] = [repo for repo in DEFAULT_ORDER[:DEFAULT_ORDER.index(name)]
                                   if repo in policy['repositories']]
        if row['method'] not in ('merge', 'squash', 'rebase') or row['backend'] not in ('github_queue', 'git_ff'):
            raise ReviewError('invalid review merge backend or method')
        if row['backend'] == 'git_ff' and row['method'] != 'merge':
            raise ReviewError('git_ff publishes exact tested merge commits; squash/rebase require github_queue')
        if name == 'common' and row['method'] != 'merge':
            raise ReviewError('common pins require merge commits')
        for key in ('sensitive', 'aot', 'generated', 'dependencies'):
            if not isinstance(row[key], list) or any(not isinstance(x, str) or not x for x in row[key]):
                raise ReviewError(f'review {key} must be a list of strings')
        if type(row['max_lines']) is not int or row['max_lines'] < 1 or type(row['evidence_only']) is not bool:
            raise ReviewError('invalid review size/evidence policy')
        if not isinstance(row['checks'], list) or not isinstance(row['source_checks'], list):
            raise ReviewError('review checks must be an array')
        for check in row['checks'] + row['source_checks']:
            if (not isinstance(check, dict) or set(check) != {'name', 'app_id'}
                    or not isinstance(check['name'], str) or not check['name']
                    or type(check['app_id']) is not int or check['app_id'] <= 0):
                raise ReviewError('each required check needs name and trusted app_id')
        if len({(c['name'], c['app_id']) for c in row['checks']}) != len(row['checks']):
            raise ReviewError('duplicate review checks')
        if not row['checks'] and not row['evidence_only']:
            raise ReviewError('review repository needs checks or explicit evidence_only policy')
        if name == 'Farm-Client' and policy['mode'] == 'merge' and not row['aot']:
            raise ReviewError('client automation requires explicit AOT patterns from its trusted triage policy')
        for key in ('candidate_workflow', 'workflow_ref'):
            if not isinstance(row[key], str) or any(c in row[key] for c in '\r\n\0'):
                raise ReviewError('invalid candidate workflow setting')
        pin = row['pin']
        if pin is not None and (not isinstance(pin, dict) or set(pin) != {'repository', 'path', 'key'}
                or any(not isinstance(v, str) or not v for v in pin.values())
                or pin['path'].startswith('/') or '..' in pin['path'].split('/')):
            raise ReviewError('pin needs repository, relative path and key')
        if pin and pin['repository'] not in row['dependencies']:
            row['dependencies'] = [*row['dependencies'], pin['repository']]
        checked[name] = row
    for name, row in checked.items():
        if set(row['dependencies']) - set(checked) or name in row['dependencies']:
            raise ReviewError('unknown/self review dependency')
        if row['pin'] and row['pin']['repository'] not in checked:
            raise ReviewError('pin repository must have review policy')
    visiting, visited = set(), set()
    def visit(name):
        if name in visiting:
            raise ReviewError('cyclic review repository dependencies')
        if name in visited:
            return
        visiting.add(name)
        for dep in checked[name]['dependencies']:
            visit(dep)
        visiting.remove(name)
        visited.add(name)
    for name in checked:
        visit(name)
    policy['repositories'] = checked
    return policy


def evidence_policy(policy, repositories):
    """Fence only evidence this delivery uses, not operational tuning or other repos."""
    names = set(repositories)
    selected = {}
    for name in sorted(names):
        row = policy['repositories'].get(name)
        selected[name] = ({**row, 'dependencies': sorted(names.intersection(row['dependencies']))}
                          if row else None)
    # v3 makes configured sensitive paths review focus, not automatic handoff.
    # Never reuse a verdict produced under the old risk instructions.
    return digest({'version': 3, 'repositories': selected})


def path_matches(entry, patterns):
    for path in dict.fromkeys((entry['filename'], entry.get('previous_filename', entry['filename']))):
        for pattern in patterns:
            if fnmatch.fnmatchcase(path, pattern):
                yield {'file': entry['filename'], 'matched_path': path, 'rule': pattern}


def review_focus(files, policy):
    """Trusted attention hints; a match alone is not evidence of high risk."""
    return [match for entry in files for match in path_matches(entry, policy['sensitive'])]


def risk(files, policy):
    """Hard gates only. Generated exclusions affect SIZE, never path gates."""
    reasons, size = [], 0
    for entry in files:
        paths = [entry['filename'], entry.get('previous_filename', entry['filename'])]
        for patterns, explanation in (
                (DEFAULT_SENSITIVE, '涉及 CI、部署或代理审查指令，必须由人工确认自动化的权限与验证规则'),
                (policy['aot'], '涉及客户端 AOT 代码，无法通过热更新修复，必须由人工确认发版风险')):
            for match in path_matches(entry, patterns):
                reasons.append(f"硬性路径门禁：{explanation}。文件：`{match['file']}`；"
                               f"命中路径：`{match['matched_path']}`；规则：`{match['rule']}`。")
        if not all(any(fnmatch.fnmatchcase(p, g) for g in policy['generated']) for p in paths):
            size += entry.get('additions', 0) + entry.get('deletions', 0)
    if size > policy['max_lines']:
        reasons.append(f"硬性规模门禁：非生成代码改动共 {size} 行，超过配置上限 {policy['max_lines']} 行；"
                       '需人工审查改动范围及验证覆盖。这是范围限制，并非已确认存在缺陷。')
    return reasons


def validate_verdict(value, snapshot):
    keys = {'snapshot', 'risk', 'reasons', 'findings', 'summary'}
    if not isinstance(value, dict) or set(value) - {'responses'} != keys:
        raise ReviewError('verdict needs snapshot, risk, reasons, findings and summary')
    if value['snapshot'] != snapshot['id'] or value['risk'] not in ('routine', 'high'):
        raise ReviewError('verdict snapshot or risk mismatch')
    if not isinstance(value['summary'], str) or not value['summary'].strip():
        raise ReviewError('verdict needs a summary')
    if not isinstance(value['reasons'], list) or any(not isinstance(v, str) or not v.strip() for v in value['reasons']):
        raise ReviewError('invalid verdict reasons')
    if value['risk'] == 'high' and not value['reasons']:
        raise ReviewError('high risk needs a reason')
    if value['risk'] == 'high':
        for reason in value['reasons']:
            # Require a reviewable explanation, not just a path/category label.
            remainder = reason.strip()
            for label, following in (('改动：', '风险：'), ('风险：', '依据：'),
                                     ('依据：', '需人工：'), ('需人工：', None)):
                if not remainder.startswith(label):
                    raise ReviewError('高风险原因须依次说明：改动、风险、依据、需人工。')
                detail, separator, tail = remainder[len(label):].partition(following or '\0')
                if not detail.strip(' \t\r\n;；') or (following and not separator):
                    raise ReviewError('高风险原因的每一项都必须填写具体说明。')
                if label in ('风险：', '需人工：') and not re.search(r'[\u3400-\u9fff]', detail):
                    raise ReviewError('风险与需人工事项须用中文说明。')
                remainder = (separator + tail).lstrip()
    if not isinstance(value['findings'], list) or len(value['findings']) > 100:
        raise ReviewError('invalid findings')
    seen = set()
    for finding in value['findings']:
        if not isinstance(finding, dict) or set(finding) != {'id', 'severity', 'path', 'line', 'claim', 'evidence', 'verification'}:
            raise ReviewError('finding schema mismatch')
        if finding['severity'] not in ('important', 'nit', 'preexisting'):
            raise ReviewError('unknown severity')
        for key in ('id', 'path', 'claim', 'evidence', 'verification'):
            if not isinstance(finding[key], str) or not finding[key].strip():
                raise ReviewError('findings need causal evidence and independent verification')
        if finding['id'] in seen or type(finding['line']) is not int or finding['line'] < 1:
            raise ReviewError('duplicate finding or invalid location')
        if finding['path'].startswith(('/', '\\')) or '..' in finding['path'].replace('\\', '/').split('/'):
            raise ReviewError('finding path escapes repository')
        seen.add(finding['id'])
    previous = {f['id'] for f in snapshot.get('open_findings', [])}
    responses = value.get('responses', [])
    if not isinstance(responses, list) or len(responses) > 100:
        raise ReviewError('invalid finding responses')
    answered = set()
    for response in responses:
        if (not isinstance(response, dict) or set(response) != {'id', 'status', 'evidence'}
                or response['id'] in answered or response['id'] not in previous
                or response['status'] not in ('fixed', 'dispute_upheld', 'dispute_rejected')
                or not isinstance(response['evidence'], str) or not response['evidence'].strip()):
            raise ReviewError('previous findings need independently verified responses')
        if response['status'] == 'dispute_rejected' and not any(f['id'] == response['id'] and f['severity'] == 'important' for f in value['findings']):
            raise ReviewError('unresolved finding must remain Important')
        answered.add(response['id'])
    if answered != previous:
        raise ReviewError('every previous Important finding needs a response')
    if len(encoded(value).encode('utf-8')) > 65536:
        raise ReviewError('verdict exceeds 64 KiB')
    return value
