"""Bounded GitHub adapter. Raw transport errors and credentials never enter worker prompts."""
import json
import subprocess
from datetime import datetime, timezone
from urllib.parse import quote

from .publication import github_repository, PublicationUnavailable
from .review_policy import PR, ReviewError, commit, encoded


class GitHub:
    def __init__(self, repositories, request=None):
        self.repositories = {name: github_repository(url) for name, url in repositories.items()}
        self.request = request or self._request

    @staticmethod
    def _request(method, endpoint, body=None, missing=False):
        argv = ['gh', 'api', '--hostname', 'github.com', '--method', method, endpoint,
                '-H', 'X-GitHub-Api-Version: 2022-11-28']
        if body is not None:
            argv += ['--input', '-']
        try:
            result = subprocess.run(argv, input=json.dumps(body) if body is not None else None,
                                    capture_output=True, encoding='utf-8', timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PublicationUnavailable('GitHub review transport unavailable') from exc
        if result.returncode:
            if missing and 'HTTP 404' in result.stderr:
                return None
            raise PublicationUnavailable('GitHub review request failed')
        if not result.stdout.strip():
            return {}
        try:
            return json.loads(result.stdout)
        except ValueError as exc:
            raise PublicationUnavailable('GitHub returned invalid review metadata') from exc

    def endpoint(self, repo, suffix=''):
        if repo not in self.repositories:
            raise ReviewError('unconfigured review repository')
        return 'repos/' + self.repositories[repo] + suffix

    def authority(self, repo, owner):
        """One durable designated controller across hosts, without sharing private state.

        GitHub's create-ref operation has a single winner. Never steal/expire a lock:
        moving authority requires operator reconciliation of the old queue first.
        """
        reference = '/git/ref/tags/farmbot-review-authority'
        value = self.request('GET', self.endpoint(repo, reference), missing=True)
        if value is None:
            tag = self.request('POST', self.endpoint(repo, '/git/tags'), {
                'tag': 'farmbot-review-authority', 'message': encoded(owner),
                'object': self.default_head(repo), 'type': 'commit',
                'tagger': {'name': 'FarmBot', 'email': 'farmbot@localhost', 'date': '2000-01-01T00:00:00Z'}})
            try:
                self.request('POST', self.endpoint(repo, '/git/refs'),
                             {'ref': 'refs/tags/farmbot-review-authority', 'sha': commit(tag['sha'])})
            except PublicationUnavailable:
                pass  # An uncertain response or another creator: read back, never assume ownership.
            value = self.request('GET', self.endpoint(repo, reference))
        if value.get('object', {}).get('type') != 'tag':
            raise ReviewError('Repository queue authority marker is invalid')
        tag = self.request('GET', self.endpoint(repo, '/git/tags/' + commit(value['object']['sha'])))
        if json.loads(tag.get('message', 'null')) != owner:
            raise ReviewError('Repository belongs to another review queue authority')

    def number(self, repo, url):
        found = PR.fullmatch(url)
        if not found or found[1].casefold() != self.repositories[repo].casefold():
            raise ReviewError('review URL differs from configured repository')
        return int(found[2])

    def pr(self, row):
        return self.request('GET', self.endpoint(row['repository'], '/pulls/' + str(self.number(row['repository'], row['url']))))

    def validate_pr(self, row, value):
        repository = self.repositories[row['repository']]
        if (not isinstance(value, dict) or value.get('html_url') != row['url']
                or value.get('state') != 'open' or value.get('head', {}).get('sha') != row['source_head']
                or value.get('head', {}).get('ref') != row['source_branch']
                or value.get('base', {}).get('ref') != row['target']
                or str(value.get('head', {}).get('repo', {}).get('full_name')).casefold() != repository.casefold()
                or str(value.get('base', {}).get('repo', {}).get('full_name')).casefold() != repository.casefold()):
            raise ReviewError('review PR identity, head, target or state changed')
        meta = self.request('GET', self.endpoint(row['repository']))
        if (meta.get('private') is not True or str(meta.get('full_name')).casefold() != repository.casefold()
                or meta.get('permissions', {}).get('push') is not True
                or meta.get('default_branch') != row['target'] or meta.get('archived')):
            raise ReviewError('review repository identity or default branch changed')

    def pages(self, repo, suffix, key=None):
        rows, expected_count = [], 0
        for page in range(1, 101):
            value = self.request('GET', self.endpoint(repo, suffix) + ('&' if '?' in suffix else '?') + f'per_page=100&page={page}')
            batch = value.get(key) if key else value
            if key and 'total_count' in value:
                if type(value['total_count']) is not int or value['total_count'] < 0:
                    raise ReviewError('GitHub result count is invalid')
                expected_count = max(expected_count, value['total_count'])
            if not isinstance(batch, list):
                raise ReviewError('GitHub pagination returned invalid data')
            rows.extend(batch)
            if len(batch) < 100:
                if len(rows) < expected_count:
                    raise ReviewError('GitHub pagination ended before all evidence was returned')
                return rows
        raise ReviewError('GitHub result exceeded pagination limit; evidence is incomplete')

    def files(self, row):
        return self.pages(row['repository'], '/pulls/' + str(self.number(row['repository'], row['url'])) + '/files')

    def checks(self, repo, sha, required):
        commit(sha)
        rows = self.pages(repo, '/commits/' + sha + '/check-runs?filter=all', 'check_runs')
        evidence, failures, waiting, unavailable = [], [], [], []
        for expected in required:
            matching = [r for r in rows if r.get('name') == expected['name']
                        and r.get('app', {}).get('id') == expected['app_id'] and r.get('head_sha') == sha]
            newest = max(matching, key=lambda r: r['id']) if matching else None
            if newest is None or newest.get('status') != 'completed':
                waiting.append(expected['name'])
            elif newest.get('conclusion') in ('failure', 'timed_out'):
                failures.append(expected['name'])
            elif newest.get('conclusion') != 'success':
                unavailable.append(expected['name'])
            if newest:
                proof = {k: newest.get(k) for k in ('id', 'name', 'head_sha', 'status', 'conclusion',
                                                   'details_url', 'started_at', 'completed_at')}
                proof['app_id'] = newest['app']['id']
                proof['check_suite_id'] = (newest.get('check_suite') or {}).get('id')
                output = newest.get('output') or {}
                proof['diagnostics'] = {k: str(output.get(k) or '')[:6000] for k in ('title', 'summary', 'text')}
                evidence.append(proof)
        return {'sha': sha, 'state': 'unavailable' if unavailable else 'failed' if failures else 'waiting' if waiting else 'passed',
                'failures': failures, 'waiting': waiting, 'unavailable': unavailable, 'checks': evidence}

    def dispatch(self, snapshot, policy):
        if policy['candidate_workflow']:
            return self.request('POST', self.endpoint(snapshot['repository'], '/actions/workflows/'
                + quote(policy['candidate_workflow'], safe='') + '/dispatches'),
                {'ref': policy['workflow_ref'], 'inputs': {'farmbot_candidate': snapshot['commit'],
                                                         'farmbot_snapshot': snapshot['id']}})
        # A configured workflow on farmbot-review/* pushes may provide the checks instead.
        return {}

    def ready(self, row, metadata):
        if metadata.get('draft'):
            value = self.request('POST', 'graphql', {'query': 'mutation($id:ID!){markPullRequestReadyForReview(input:{pullRequestId:$id}){pullRequest{id isDraft}}}',
                                                   'variables': {'id': metadata['node_id']}})
            result = (value.get('data') or {}).get('markPullRequestReadyForReview') or {}
            if value.get('errors') or result.get('pullRequest', {}).get('isDraft') is not False:
                raise PublicationUnavailable('GitHub did not confirm ready conversion')
            return value

    def blockers(self, row):
        number = self.number(row['repository'], row['url'])
        reviews = self.pages(row['repository'], f'/pulls/{number}/reviews')
        latest = {}
        for review in sorted(reviews, key=lambda r: r['id']):
            if review.get('state') in ('APPROVED', 'CHANGES_REQUESTED', 'DISMISSED'):
                latest[review['user']['id']] = review['state']
        if any(state == 'CHANGES_REQUESTED' for state in latest.values()):
            return True
        owner, name = self.repositories[row['repository']].split('/')
        cursor = None
        for _ in range(100):
            value = self.request('POST', 'graphql', {'query': '''query($owner:String!,$name:String!,$number:Int!,$cursor:String){
                repository(owner:$owner,name:$name){pullRequest(number:$number){reviewThreads(first:100,after:$cursor){
                nodes{isResolved} pageInfo{hasNextPage endCursor}}}}}''',
                'variables': {'owner': owner, 'name': name, 'number': number, 'cursor': cursor}})
            if value.get('errors'):
                raise ReviewError('Cannot establish review thread status')
            threads = value['data']['repository']['pullRequest']['reviewThreads']
            if any(t['isResolved'] is not True for t in threads['nodes']):
                return True
            if not threads['pageInfo']['hasNextPage']:
                return False
            cursor = threads['pageInfo']['endCursor']
        raise ReviewError('Review thread pagination incomplete')

    def commits(self, row):
        return {commit(v['sha']) for v in self.pages(row['repository'], '/pulls/' + str(self.number(row['repository'], row['url'])) + '/commits')}

    def protected(self, row):
        value = self.request('GET', self.endpoint(row['repository'], '/branches/' + quote(row['target'], safe='')))
        if type(value.get('protected')) is not bool:
            raise ReviewError('Branch protection could not be determined')
        return value['protected']

    def default_head(self, repo):
        metadata = self.request('GET', self.endpoint(repo))
        return self.target({'repository': repo, 'target': metadata['default_branch']})

    def reverts(self, repo, sha, since):
        timestamp = datetime.fromtimestamp(since, timezone.utc).isoformat()
        # The commits endpoint defaults to the repository's default branch.
        rows = self.pages(repo, '/commits?since=' + quote(timestamp, safe=''))
        prefix = 'This reverts commit ' + commit(sha)
        # git revert -m emits the second form for the merge commits this backend
        # publishes. Checking only the ordinary single-parent message misses them.
        needles = {prefix + '.', prefix + ', reversing'}
        return ['revert:' + self.repositories[repo] + ':' + commit(r['sha']) for r in rows
                if needles.intersection(r.get('commit', {}).get('message', '').splitlines())]

    def target(self, row):
        value = self.request('GET', self.endpoint(row['repository'], '/git/ref/heads/' + quote(row['target'], safe='')))
        return commit(value['object']['sha'])

    def notify(self, row, marker, body, *, handoff=True):
        number = self.number(row['repository'], row['url'])
        comments = self.pages(row['repository'], f'/issues/{number}/comments')
        if not any(marker in c.get('body', '') for c in comments):
            self.request('POST', self.endpoint(row['repository'], f'/issues/{number}/comments'), {'body': body + '\n\n' + marker})
        if handoff:
            self.request('POST', self.endpoint(row['repository'], f'/issues/{number}/labels'), {'labels': ['needs-person']})


class StubGitHub(GitHub):
    """Offline subprocess fixture; explicit responses for reads and writes, no network fallback."""
    def __init__(self, repositories, directory):
        from pathlib import Path
        self.directory = Path(directory)
        super().__init__(repositories, self.stub_request)

    def stub_request(self, method, endpoint, body=None, missing=False):
        with (self.directory / 'review-github-calls.jsonl').open('a', encoding='utf-8') as handle:
            handle.write(json.dumps({'method': method, 'endpoint': endpoint, 'body': body}) + '\n')
        responses = json.loads((self.directory / 'review-github.json').read_text(encoding='utf-8'))
        key = method + ' ' + endpoint
        if key not in responses:
            if missing:
                return None
            raise PublicationUnavailable('offline GitHub fixture has no response')
        return responses[key]
