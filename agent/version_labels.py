"""Idempotent workspace label reconciliation, owned by one configured controller.

People own the version choices. No Git discovery, issue label changes or deletions.
"""
from .version_targets import GROUP, TargetError, version_name

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
        'description': MARKER + ' Select vMAJOR.MINOR.PATCH (for example v1.2.3) before delegating work. Leave the issue unlabeled to use repository defaults.'})
    children = [v for v in labels if (v.get('parent') or {}).get('id') == group]
    by_name = {}
    for child in children:
        if child['name'] in by_name or child.get('team') or child.get('isGroup'):
            raise RuntimeError('Target version contains ambiguous or invalid labels')
        by_name[child['name']] = child
    if len(children) + len(set(versions) - set(by_name)) > 250:
        raise RuntimeError('Target version exceeds the Linear group limit; retain existing labels and reconcile manually')
    available = []
    for name in sorted(set(versions) | set(by_name)):
        existing = by_name.get(name)
        try:
            branch = version_name(name)
        except TargetError:
            if not existing:
                continue
            description = MARKER + ' Unsupported for new work: use vMAJOR.MINOR.PATCH, for example v1.2.3. Saved job targets are retained.'
        else:
            available.append(name)
            description = MARKER + ' Target branch: ' + branch
            if name != 'main':
                description += '. Created from each repository default when work starts if missing.'
        if not existing:
            create({'name': name, 'parentId': group, 'description': description})
        elif ((existing.get('description') or '').startswith(MARKER)
              and existing['description'] != description):
            result = api.graphql('''mutation FarmBotUpdateVersionLabel($id: String!, $input: IssueLabelUpdateInput!) {
                issueLabelUpdate(id: $id, input: $input) { success }
            }''', {'id': existing['id'], 'input': {'description': description}})['issueLabelUpdate']
            if result.get('success') is not True:
                raise RuntimeError('Linear did not confirm version label update')
    return {'group_id': group, 'available': available}
