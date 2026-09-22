"""Read-only evidence for publishing a delegated fix to its configured GitHub repo.

This does not publish or override the runtime's approval reviewer. Failed checks
withhold publishing scope while allowing the worker to investigate locally.
"""
from datetime import datetime, timezone
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import quote

from .router import WRITE_SKILLS
from .worktrees import WorktreeError, _git


class PublicationError(RuntimeError):
    pass


class PublicationUnavailable(PublicationError):
    """A temporary transport failure, not a rejected publishing destination."""


def issue_branch(identifier, issue_prefix='FARM'):
    """Validate the configured issue namespace before deriving a writable branch."""
    if not isinstance(identifier, str) or not re.fullmatch(re.escape(issue_prefix) + r'-[0-9]+', identifier):
        raise PublicationError("publication requires an identifier in the configured issue namespace")
    return 'farmbot/' + identifier.lower()


def github_repository(url):
    match = re.fullmatch(r'(?:https://github\.com/|ssh://git@github\.com/|git@github\.com:)'
                         r'([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?', str(url), re.IGNORECASE)
    if not match or any(part in ('.', '..') for part in match.groups()):
        raise PublicationError('publishing requires a credential-free github.com repository URL')
    return '/'.join(match.groups())


def github_api(endpoint, *, missing_ok=False):
    try:
        result = subprocess.run(['gh', 'api', '--hostname', 'github.com', '--method', 'GET', endpoint],
                                capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired as exc:
        raise PublicationUnavailable('GitHub verification timed out') from exc
    except OSError as exc:
        raise PublicationError('GitHub verification unavailable') from exc
    if result.returncode:
        diagnostic = (result.stderr or "").lower()
        status = re.search(r"http (\d{3})", diagnostic)
        if ((status and int(status[1]) in (429, 500, 502, 503, 504))
                or any(term in diagnostic for term in ("tls handshake timeout", "i/o timeout",
                       "context deadline exceeded", "connection reset by peer", "unexpected eof"))):
            raise PublicationUnavailable('GitHub metadata transport temporarily unavailable')
    try:
        data = json.loads(result.stdout)
    except ValueError as exc:
        raise PublicationError('GitHub verification returned no usable metadata') from exc
    if result.returncode:
        if isinstance(data, dict) and str(data.get('status')) in ('429', '500', '502', '503', '504'):
            raise PublicationUnavailable('GitHub metadata service temporarily unavailable')
        if missing_ok and isinstance(data, dict) and str(data.get('status')) == '404':
            return None
        # Never echo raw stderr: credentials and transport URLs do not belong in launch prompts.
        raise PublicationError('GitHub metadata request failed')
    if not isinstance(data, dict):
        raise PublicationError('GitHub verification returned invalid metadata')
    return data


class PublicationVerifier:
    def __init__(self, worktrees, *, api=None, issue_prefix='FARM'):
        self.worktrees = worktrees
        self.api = api or github_api
        self.issue_prefix = issue_prefix

    def verify(self, repo, item_id, identifier, branch=None):
        try:
            return self._verify(repo, item_id, identifier, branch)
        except (WorktreeError, OSError, subprocess.TimeoutExpired) as exc:
            raise PublicationError('cannot verify the configured worktree and push destination') from exc

    def verify_pr(self, repo, item_id, identifier, url):
        """Prove a late Linear attachment is this job's actual draft output."""
        verified = self.verify(repo, item_id, identifier)
        prefix = verified['url'].rstrip('/') + '/pull/'
        if not isinstance(url, str) or not url.startswith(prefix) or not re.fullmatch(r'[1-9][0-9]*', url[len(prefix):]):
            raise PublicationError('PR URL differs from the verified repository')
        pr = self.api('repos/' + verified['repository'] + '/pulls/' + url[len(prefix):])
        if not isinstance(pr, dict):
            raise PublicationError('GitHub returned invalid PR metadata')
        head, base = pr.get('head'), pr.get('base')
        if (pr.get('html_url') != url or pr.get('state') != 'open' or pr.get('draft') is not True
                or not isinstance(head, dict) or not isinstance(base, dict)
                or head.get('ref') != verified['branch'] or head.get('sha') != verified['head']
                or base.get('ref') != verified['base_branch']
                or not isinstance(head.get('repo'), dict) or not isinstance(base.get('repo'), dict)
                or head['repo'].get('full_name') != verified['repository']
                or base['repo'].get('full_name') != verified['repository']):
            raise PublicationError("PR must be an open draft for this job's exact repository, branch and HEAD")
        return url

    def _verify(self, repo, item_id, identifier, branch):
        prefix = issue_branch(identifier, self.issue_prefix)
        configured = self.worktrees.remotes.get(repo)
        expected = github_repository(configured)
        root = self.worktrees.worktrees_root.resolve()
        path = root / item_id / repo
        if (not path.is_dir() or path.resolve() != path or not path.is_relative_to(root)
                or Path(_git('rev-parse', '--show-toplevel', cwd=path)).resolve() != path
                or Path(_git('rev-parse', '--path-format=absolute', '--git-common-dir', cwd=path)).resolve()
                   != self.worktrees.clone_path(repo).resolve()):
            raise PublicationError("publication requires this job's own configured worktree")
        actual_branch = _git('branch', '--show-current', cwd=path)
        branch = actual_branch if branch is None else branch
        if (not isinstance(branch, str)
                or not (branch == prefix or branch.startswith(prefix + '-'))
                or actual_branch != branch):
            raise PublicationError("publication requires this issue's FarmBot feature branch")
        push_urls = _git('remote', 'get-url', '--push', '--all', 'origin', cwd=path).splitlines()
        if len(push_urls) != 1 or github_repository(push_urls[0]).casefold() != expected.casefold():
            raise PublicationError('effective origin push destination differs from the configured repository')
        metadata = self.api('repos/' + expected)
        if (not isinstance(metadata, dict)
                or any(not isinstance(metadata.get(key), str) or not metadata[key]
                       for key in ('full_name', 'html_url', 'default_branch'))
                or not isinstance(metadata.get('owner'), dict)
                or not isinstance(metadata['owner'].get('login'), str)
                or not isinstance(metadata.get('permissions'), dict)):
            raise PublicationError('GitHub verification returned invalid repository metadata')
        if (metadata['full_name'].casefold() != expected.casefold()
                or metadata['owner']['login'].casefold() != expected.split('/')[0].casefold()
                or metadata['html_url'].rstrip('/').casefold() != ('https://github.com/' + expected).casefold()
                or metadata.get('private') is not True
                or metadata['permissions'].get('push') is not True):
            raise PublicationError('GitHub did not verify the configured private repository and write access')
        if branch == metadata['default_branch']:
            raise PublicationError('publishing to the default branch is not authorized')
        remote_branch = self.api('repos/' + expected + '/branches/' + quote(branch, safe=''), missing_ok=True)
        if remote_branch is not None and (not isinstance(remote_branch, dict)
                                         or remote_branch.get('name') != branch or remote_branch.get('protected') is not False):
            raise PublicationError('publishing to a protected or unverifiable branch is not authorized')
        return {'status': 'verified', 'repository': metadata['full_name'], 'url': metadata['html_url'],
                'push_url': push_urls[0], 'push_remote': 'origin', 'branch': branch, 'head': self.worktrees.head(path),
                'base_branch': metadata['default_branch'], 'private': True, 'write_access': True,
                'verified_at': datetime.now(timezone.utc).isoformat()}

    def scope(self, *, item, issue, paths, delegated):
        scope = {'repositories': {}}
        if not delegated or item['skill'] not in WRITE_SKILLS:
            return scope
        for repo in paths:
            try:
                scope['repositories'][repo] = self.verify(repo, item['id'], issue['identifier'])
            except PublicationError as exc:
                scope['repositories'][repo] = {'status': 'unverified', 'reason': str(exc)}
        return scope
