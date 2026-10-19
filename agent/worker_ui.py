"""Native package export for the single owner, without a parallel UI workflow."""
from uuid import uuid4

from .config import Paths
from .execution import single
from .fgui_source import owned_source
from .fgui_publisher import publish
from .ledger import LedgerError, TERMINAL_STATUS_TYPES, plan_issue_branches
from .preview_upload import _plain_path
from .worktrees import Worktrees


def export(ledger, item_id, token, config, api_factory, packages):
    paths = Paths(config)
    item = ledger.item(item_id)
    if not single(item) or not config.fgui_export:
        raise LedgerError('native package export requires a single worker and a configured publisher')
    trees = Worktrees(paths.repos, paths.worktrees, config.repos)
    source = paths.worktrees / item_id / 'farmgui'
    branch = trees.git_in(source, 'branch', '--show-current')
    plan_issue_branches({'prs': {'farmgui': [{'role': 'issue', 'branch': branch}]}},
                       ledger.issue(item['issue_id'])['identifier'], config.issue_prefix, reserved=True)
    initial = owned_source(trees, item_id, branch, packages, expected_head=trees.head(source))

    def fence():
        ledger.renew(item_id, token)
        ledger._refuse_withdrawn(ledger._row(item_id))

    fence()
    api = api_factory()
    issue = api.fetch_issue(item['issue_id'])
    observed = ledger.observe_issue(issue)
    if (not api.app_user_id or issue.get('delegate_id') != api.app_user_id
            or issue.get('archived') or issue.get('status_type') in TERMINAL_STATUS_TYPES):
        raise LedgerError('issue must remain open and delegated for native export')
    row = ledger._owned(item_id, token)
    if row['requeue_requested'] or observed['fingerprint'] != row['claimed_fingerprint']:
        raise LedgerError('read and revalidate changed issue inputs before export')
    run = paths.runs / item_id / 'ui-exports' / uuid4().hex
    _plain_path(run.parent)
    run.parent.mkdir(parents=True, exist_ok=True)
    block = config.fgui_export
    result = publish(source, packages, block['executable'], block['tool_sha256'], run, fence=fence,
        verify_source=lambda: owned_source(trees, item_id, branch, packages, expected_head=initial['head']),
        timeout=block.get('timeout_seconds', 180))
    fence()
    ledger.note(item_id, 'single_worker_ui_export', details={
        'head': initial['head'], 'packages': packages, 'run': str(run)})
    return {'head': initial['head'], 'packages': packages, 'run': str(run),
            'staging': str(run / 'staging'), 'result': result.evidence(),
            'next': 'Install only the selected outputs in the owned Client; check metadata, bindings and required tests.'}
