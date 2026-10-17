"""Validate explicit draft delivery with a still-pending post-merge archive.

Input relevance and acceptance remain worker-reviewed evidence, not an automatic
diff classifier. This shape check never grants a test or publication waiver.
"""
import re


def _object(value):
    return value if isinstance(value, dict) else {}


def validate_deferred_archive(plan, prs):
    closing = _object(plan.get('closing'))
    if 'deferred_archive' not in closing:
        return
    record = closing['deferred_archive']
    if not isinstance(record, dict) or record.get('status') != 'pending_contract_merge':
        raise ValueError('deferred archive must remain pending_contract_merge')
    if closing.get('writeback_opened') is not False:
        raise ValueError('deferred archive must not claim writeback_opened')
    if plan.get('pause'):
        raise ValueError('resolve the actual pause before draft delivery')
    for step in ('waivers_removed', 'client_resynced', 'hive_resynced', 'pin_written'):
        if closing.get(step) is not True:
            raise ValueError(f'deferred archive cannot skip closing.{step}')
    stages = _object(plan.get('stages'))
    for stage in 'ABCDEF':
        state = stages.get(stage)
        if not isinstance(state, str) or not (state == 'done' or state.startswith('skipped: ') and state[9:].strip()):
            raise ValueError(f'deferred archive requires completed or explicitly skipped stage {stage}')
    for stage, decision in (
            ('F', _object(_object(plan.get('client')).get('inputs')).get('network')),
            ('D', _object(closing.get('inputs')).get('hive'))):
        if stages[stage] != 'done':
            continue
        if not isinstance(decision, dict) or decision.get('required') is not False or not decision.get('reason'):
            raise ValueError('deferred archive requires reviewed retained consumer inputs')
        for key in ('base_commit', 'source_commit', 'consumer_commit'):
            if not isinstance(decision.get(key), str) or not re.fullmatch('[0-9a-f]{40}', decision[key]):
                raise ValueError('deferred archive requires exact reviewed consumer input identities')
        if not decision.get('evidence'):
            raise ValueError('deferred archive requires retained input evidence')
    if stages['C'] == 'done' and _object(plan.get('config')).get('pin') != 'published':
        raise ValueError('deferred archive cannot bypass the published config pin')
    if record.get('docs_only') is not True or not isinstance(record.get('reason'), str) or not record['reason'].strip():
        raise ValueError('deferred archive requires a reviewed docs-only Contract reason')
    acceptance = closing.get('acceptance')
    if (not isinstance(acceptance, dict) or acceptance.get('status') != 'recorded'
            or not isinstance(acceptance.get('head'), str) or not re.fullmatch('[0-9a-f]{40}', acceptance['head'])
            or not acceptance.get('evidence') or not acceptance.get('contract_pr')
            or acceptance['contract_pr'] not in (prs or [])
            or record.get('contract_pr') != acceptance['contract_pr']):
        raise ValueError('deferred archive requires published Contract acceptance in delivery PRs')
