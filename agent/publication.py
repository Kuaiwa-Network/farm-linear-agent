"""Read-only evidence for publishing a delegated fix to its configured GitHub repo.

This does not publish or override the runtime's approval reviewer. Failed checks
withhold publishing scope while allowing the worker to investigate locally.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote

from .router import WRITE_SKILLS
from .worktrees import GITHUB_REMOTE, VerificationMismatch, WorktreeError


class PublicationError(RuntimeError):
    pass


class PublicationUnavailable(PublicationError):
    """A temporary transport failure, not a rejected publishing destination."""


def issue_branch(identifier, issue_prefix='FARM'):
    """Validate the configured issue namespace before deriving a writable branch."""
    if not isinstance(identifier, str) or not re.fullmatch(re.escape(issue_prefix) + r'-[0-9]+', identifier):
        raise PublicationError("publication requires an identifier in the configured issue namespace")
    return 'farmbot/' + identifier.lower()


def is_issue_branch(branch, identifier, issue_prefix='FARM'):
    """The issue-branch policy: `branch` is this issue's FarmBot branch, `farmbot/<key>` or `farmbot/<key>-<suffix>`.
    Publication applies it to the branch it publishes, and a checkpoint to the issue branches a plan records (plan
    P9). Raises PublicationError, as issue_branch does, for an identifier outside the configured namespace."""
    prefix = issue_branch(identifier, issue_prefix)
    return isinstance(branch, str) and (branch == prefix or branch.startswith(prefix + '-'))


# Named branches beside the issue branch of a job whose skill has an initial root (spec §6.1; P4).
# A human publishes a config branch's tip with designer-source.pipeline, so it adds no commits of FarmBot's own.
# A re-pin that does not descend from the pushed tip takes the next unused number, never a force push (P12).
CONFIG_SUFFIX = '-config'
CONFIG_REPIN_SUFFIX = CONFIG_SUFFIX + '-<n>'
SUFFIXES = (CONFIG_SUFFIX, CONFIG_REPIN_SUFFIX, '-waivers', '-followup', '-writeback')
_REPIN_NUMBER = re.compile(r'[2-9]|[1-9][0-9]+')


def suffix_of(branch, canonical):
    """The entry of SUFFIXES for this issue branch, else None. Re-pin numbers start at 2 without leading zeros."""
    if not isinstance(branch, str) or not branch.startswith(canonical + '-'):
        return None
    suffix = branch[len(canonical):]
    if suffix in SUFFIXES and suffix != CONFIG_REPIN_SUFFIX:
        return suffix
    stem, _, number = suffix.rpartition('-')
    return CONFIG_REPIN_SUFFIX if stem == CONFIG_SUFFIX and _REPIN_NUMBER.fullmatch(number) else None


def github_repository(url):
    match = GITHUB_REMOTE.fullmatch(str(url))
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


class StubGitHub:
    """Test double for github_api in offline fixtures, whose worker CLI runs in a subprocess beyond a test's patches.
    Answers GET endpoints from `github.json` in the Linear stub directory and records each call; an endpoint the file
    does not list is GitHub's 404."""
    def __init__(self, directory):
        self.directory = Path(directory)

    def __call__(self, endpoint, *, missing_ok=False):
        with open(self.directory / 'github-calls.jsonl', 'a', encoding='utf-8') as handle:
            handle.write(json.dumps({'endpoint': endpoint}, ensure_ascii=False) + '\n')
        data = json.loads((self.directory / 'github.json').read_text(encoding='utf-8')).get(endpoint)
        if data is None and missing_ok:
            return None
        if not isinstance(data, dict):
            raise PublicationError('GitHub metadata request failed')
        return data


def github_reader(config):
    """github_api, or an offline profile's StubGitHub: a fake-runtime host whose Linear stub fixture also supplies
    `github.json`. Live profiles refuse stub selectors, as they do for Linear."""
    stub = os.environ.get('FARMBOT_LINEAR_STUB_DIR')
    if stub and config.runtime == 'fake' and (Path(stub) / 'github.json').is_file():
        from .environment import validate_runtime
        validate_runtime(config)
        return StubGitHub(stub)
    return github_api


class PublicationVerifier:
    def __init__(self, worktrees, *, api=None, issue_prefix='FARM'):
        self.worktrees = worktrees
        self.api = api or github_api
        self.issue_prefix = issue_prefix

    def verify(self, repo, item_id, identifier, branch=None, *, suffix_roles=False, require_main=False):
        """Apply suffix roles for a skill with an initial root; fix keeps any suffix Linear suggests."""
        try:
            return self._verify(repo, item_id, identifier, branch, suffix_roles, require_main)
        except VerificationMismatch as exc:
            raise PublicationError(str(exc)) from exc
        except (WorktreeError, OSError, subprocess.TimeoutExpired) as exc:
            raise PublicationError('cannot verify the configured worktree and push destination') from exc

    def verify_pr(self, repo, item_id, identifier, url, *, delivered=False, proof=False):
        """Prove a late Linear attachment is this job's actual draft output.

        delivered: a PR a finished delivery names. A person may since have marked it ready for review or merged
        it, so either counts, but it must still be this job's exact repository, branch and HEAD. A PR closed
        without merging never counts, and neither does one that GitHub does not know."""
        verified = self.verify(repo, item_id, identifier)
        prefix = verified['url'].rstrip('/') + '/pull/'
        if not isinstance(url, str) or not url.startswith(prefix) or not re.fullmatch(r'[1-9][0-9]*', url[len(prefix):]):
            raise PublicationError('PR URL differs from the verified repository')
        endpoint = 'repos/' + verified['repository'] + '/pulls/' + url[len(prefix):]
        pr = self.api(endpoint, missing_ok=True) if delivered else self.api(endpoint)
        if pr is None:
            raise PublicationError(f'GitHub has no pull request at {url}')
        if not isinstance(pr, dict):
            raise PublicationError('GitHub returned invalid PR metadata')
        head, base = pr.get('head'), pr.get('base')
        if delivered:
            current = (pr.get('state') == 'open' and isinstance(pr.get('draft'), bool)
                       or pr.get('state') == 'closed' and pr.get('merged') is True)
            requirement = "an open or merged PR for this job's exact repository, branch and current HEAD"
        else:
            current = pr.get('state') == 'open' and pr.get('draft') is True
            requirement = "an open draft for this job's exact repository, branch and HEAD"
        if (pr.get('html_url') != url or not current
                or not isinstance(head, dict) or not isinstance(base, dict)
                or head.get('ref') != verified['branch'] or head.get('sha') != verified['head']
                or base.get('ref') != verified['base_branch']
                or not isinstance(head.get('repo'), dict) or not isinstance(base.get('repo'), dict)
                or head['repo'].get('full_name') != verified['repository']
                or base['repo'].get('full_name') != verified['repository']):
            raise PublicationError(f"{url} must be {requirement}")
        if proof:
            if not isinstance(base.get('sha'), str) or not re.fullmatch(r'[0-9a-f]{40}', base['sha']):
                raise PublicationError('review delivery requires GitHub base commit identity')
            return {'repo': repo, 'url': url, 'head': verified['head'], 'branch': verified['branch'],
                    'base_branch': verified['base_branch'], 'base': base['sha']}
        return url

    def _verify(self, repo, item_id, identifier, branch, suffix_roles=False, require_main=False):
        prefix = issue_branch(identifier, self.issue_prefix)
        from .version_targets import TargetError, Store
        target = None
        ledger = getattr(self.worktrees, 'ledger', None)
        if ledger is not None:
            try:
                saved = Store(ledger).current(item_id)
                if saved:
                    target = saved['repositories'].get(repo)
                    if target is None:
                        raise TargetError('repository is not part of the saved target')
            except TargetError as exc:
                raise PublicationError(str(exc)) from exc
        configured = self.worktrees.remotes.get(repo)
        expected = github_repository(configured)
        root = self.worktrees.worktrees_root.resolve()
        from .workflow import repository_path
        path = repository_path(self.worktrees, repo, item_id)
        if not path.is_dir() or path.resolve() != path or not path.is_relative_to(root):
            raise PublicationError("publication requires this job's own configured worktree")
        try:
            self.worktrees.worktree_entry(repo, path)
        except WorktreeError as exc:
            raise PublicationError("publication requires this job's own configured worktree") from exc

        def git(*args):
            # The repository named explicitly (plan P10): the controller runs this check at every launch.
            return self.worktrees.git_in(path, *args, env={'GIT_NO_REPLACE_OBJECTS': '1'})

        actual_branch = git('branch', '--show-current')
        branch = actual_branch if branch is None else branch
        if not is_issue_branch(branch, identifier, self.issue_prefix) or actual_branch != branch:
            raise PublicationError("publication requires this issue's FarmBot feature branch")
        if suffix_roles and suffix_of(branch, prefix) == '-writeback' and repo != 'Farm-Contract':
            raise PublicationError('the -writeback role is only for this issue\'s Farm-Contract worktree')
        # The worker fetched before checking out the named commit. None of this issue's Jenkins branches can
        # vouch for a commit only they carry, even after an earlier push. Use the same hardened git as every check.
        if suffix_roles and suffix_of(branch, prefix) in (CONFIG_SUFFIX, CONFIG_REPIN_SUFFIX) and git(
                'rev-list', '--max-count=1', 'HEAD', '--not', f'--exclude=origin/{prefix}{CONFIG_SUFFIX}',
                f'--exclude=origin/{prefix}{CONFIG_SUFFIX}-*', '--remotes=origin'):
            raise PublicationError('a -config branch adds no commits: its HEAD must already be on another branch of '
                                   'origin; fetch, then check out the farm-common commit that was named')
        push_urls = git('remote', 'get-url', '--push', '--all', 'origin').splitlines()
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
        base_branch = target['branch'] if target else metadata['default_branch']
        if branch in (metadata['default_branch'], base_branch):
            raise PublicationError('publishing to the default branch or selected base branch is not authorized')
        # Ignore rules do not protect reports that a worker has force-added or that
        # an earlier commit already tracks. Check the outgoing branch, not the file
        # system, so a pre-existing report on the base branch is not a false alarm.
        if target:
            try:
                self.worktrees.verify_target(repo, path, target)
                current_base = self.worktrees.resolve_target(repo, base_branch)
                missing = self.worktrees.git_in(path, 'rev-list', '--max-count=1', target['commit'], '--not',
                    current_base['commit'], env={'GIT_NO_REPLACE_OBJECTS': '1'})
                if missing:
                    raise TargetError('target branch history changed; reconcile before publication')
            except TargetError as exc:
                raise PublicationError(str(exc)) from exc
        base_ref = current_base['commit'] if target else f"refs/remotes/origin/{metadata['default_branch']}"
        if (git('diff', '--name-only', f'{base_ref}...HEAD', '--', 'reports')
                or git('diff', '--cached', '--name-only', '--', 'reports')):
            raise PublicationError('run report changes must stay in private state, not the published branch')
        remote_branch = self.api('repos/' + expected + '/branches/' + quote(branch, safe=''), missing_ok=True)
        if remote_branch is not None and (not isinstance(remote_branch, dict)
                                         or remote_branch.get('name') != branch or remote_branch.get('protected') is not False):
            raise PublicationError('publishing to a protected or unverifiable branch is not authorized')
        head = self.worktrees.head(path)
        result = {'status': 'verified', 'repository': metadata['full_name'], 'url': metadata['html_url'],
                'push_url': push_urls[0], 'push_remote': 'origin', 'branch': branch, 'head': head,
                'base_branch': base_branch, 'private': True, 'write_access': True,
                'verified_at': datetime.now(timezone.utc).isoformat()}
        if target:
            result['base_commit'] = target['commit']
        # A command description, never execution or a reusable authorization. Use the
        # verified remote name so Git's configured rewriting remains in force.
        result['push_argv'] = ['git', 'push', 'origin', f'HEAD:refs/heads/{branch}']
        result['execution_hint'] = {
            'windows_git_review': sys.platform == 'win32',
            'guidance': ('On Windows, request the runtime normal automatic approval for this exact Git push '
                         'outside the restricted shell before its first execution; Git hooks may need MSYS '
                         'signal pipes. Keep hooks, destination checks and approval enabled. A denial stops '
                         'the action; never retry with hooks disabled, broader roots or bypassed review. '
                         'This hint is not approval. Other platforms use their ordinary execution policy.')}
        # Config publication pins an existing remote commit and must not merge new source into that pin.
        config_pin = suffix_roles and suffix_of(branch, prefix) in (CONFIG_SUFFIX, CONFIG_REPIN_SUFFIX)
        if require_main and not config_pin:
            options = {}
            ledger = getattr(self.worktrees, 'ledger', None)
            verification_pair = (ledger.client_verification_pair(item_id, head, require_candidate=True)
                                 if repo == 'Farm-Client' and ledger is not None else None)
            if verification_pair is not None:
                if (verification_pair.get('candidate_commit') != head or not verification_pair.get('candidate_reservation')):
                    raise PublicationError('settled verification pair must match this exact Client candidate')
                options['verified_base'] = verification_pair['baseline_commit']
            elif repo == 'Farm-Client' and ledger is not None:
                candidate_round = ledger.client_candidate_round(item_id, head)
                if candidate_round:
                    options['verified_base'] = candidate_round['base_commit']
                    result['client_verification_round'] = candidate_round
            server_round = (ledger.server_verification_round(item_id, head, require_released=True)
                            if repo == 'farm-hive' and ledger is not None else None)
            if server_round is not None:
                options['verified_base'] = server_round['base_commit']
                result['server_verification_round'] = server_round
            elif repo == 'farm-hive' and ledger is not None:
                from .server_registration import retained_registration_base
                registration = retained_registration_base(self.worktrees, ledger, item_id, head, branch)
                if registration:
                    options['verified_base'] = registration['base_commit']
                    result['server_registration_ancestry'] = registration
            result['main_integration'] = self.worktrees.require_main_integration(
                repo, path, head, default_branch=metadata['default_branch'], **options)
            if verification_pair:
                result['verification_pair'] = verification_pair
        if ledger is not None:
            try:
                Store(ledger).current(item_id)
            except TargetError as exc:
                raise PublicationError(str(exc)) from exc
        return result

    def scope(self, *, item, issue, paths, delegated, suffix_roles=False):
        scope = {'repositories': {}}
        if not delegated or item['skill'] not in WRITE_SKILLS:
            return scope
        for repo in paths:
            try:
                scope['repositories'][repo] = self.verify(repo, item['id'], issue['identifier'],
                                                          suffix_roles=suffix_roles)
            except PublicationError as exc:
                scope['repositories'][repo] = {'status': 'unverified', 'reason': str(exc)}
        return scope
