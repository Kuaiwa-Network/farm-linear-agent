"""Idempotent workspace label reconciliation, owned by one configured controller.

No issue labels are applied or removed. Deleted Git branches leave their existing
labels available as historical selections, with an explicit unavailable description.
"""
from .version_targets import GROUP, VERSION

MARKER = '[farmbot-version-target]'


def sync(api, versions):
    labels, after, cursors = [], None, set()
    while True:
        data = api.graphql('''query FarmBotVersionLabels($after: String) {
            issueLabels(first: 100, after: $after) {
                nodes { id name description isGroup team { id } parent { id name } }
                pageInfo { hasNextPage endCursor }
            } }''', {'after': after})['issueLabels']
        labels.extend(data['nodes'])
        page = data['pageInfo']
        if not page['hasNextPage']:
            break
        after = page['endCursor']
        if not after or after in cursors:
            raise RuntimeError('Linear version label pagination did not advance')
        cursors.add(after)
    groups = [v for v in labels if v['name'] == GROUP and not v.get('team') and not v.get('parent')]
    if len(groups) > 1 or (groups and groups[0].get('isGroup') is not True):
        raise RuntimeError('Target version must be one unambiguous workspace label group')

    def create(values):
        result = api.graphql('''mutation FarmBotCreateVersionLabel($input: IssueLabelCreateInput!) {
            issueLabelCreate(input: $input) { success issueLabel { id } }
        }''', {'input': values})['issueLabelCreate']
        if result.get('success') is not True or not (result.get('issueLabel') or {}).get('id'):
            raise RuntimeError('Linear did not confirm version label creation')
        return result['issueLabel']['id']

    group = groups[0]['id'] if groups else create({'name': GROUP, 'isGroup': True,
        'description': MARKER + ' Select the version before delegating work. Leave the issue unlabeled to use repository defaults.'})
    children = [v for v in labels if (v.get('parent') or {}).get('id') == group]
    by_name = {}
    for child in children:
        if child['name'] in by_name or child.get('team') or child.get('isGroup'):
            raise RuntimeError('Target version contains ambiguous or invalid labels')
        by_name[child['name']] = child
    if len(children) + len(set(versions) - set(by_name)) > 250:
        raise RuntimeError('Target version exceeds the Linear group limit; retain existing labels and reconcile manually')
    for name in sorted(set(versions) | set(by_name)):
        if name != 'main' and not VERSION.fullmatch(name):
            continue
        branch = versions.get(name)
        description = (MARKER + ' Target branch: ' + branch if branch else
                       MARKER + ' Unavailable: release branch no longer exists. Existing issue selections are retained.')
        existing = by_name.get(name)
        if not existing:
            create({'name': name, 'parentId': group, 'description': description})
        elif ((existing.get('description') or '').startswith(MARKER)
              and existing['description'] != description):
            result = api.graphql('''mutation FarmBotUpdateVersionLabel($id: String!, $input: IssueLabelUpdateInput!) {
                issueLabelUpdate(id: $id, input: $input) { success }
            }''', {'id': existing['id'], 'input': {'description': description}})['issueLabelUpdate']
            if result.get('success') is not True:
                raise RuntimeError('Linear did not confirm version label update')
    return {'group_id': group, 'available': sorted(versions)}
