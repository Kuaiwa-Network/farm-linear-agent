"""One Client reservation with an atomically fenced companion server slot.

The game code runs only in the resumed worker, never in a controller command.
"""
import json
import re

FULL_SERVER_PLAN = {'packages':['./...'], 'tests':[], 'race':True}


def validate_plan(raw):
    if not isinstance(raw, dict) or set(raw) != {'server_commit', 'server_argv', 'scenarios', 'startup_seconds'}:
        raise ValueError('integration plan requires server_commit, server_argv, scenarios and startup_seconds')
    if not isinstance(raw['server_commit'], str) or not re.fullmatch('[0-9a-f]{40}', raw['server_commit']):
        raise ValueError('integration requires an exact server commit')
    argv = validate_argv(raw['server_argv'])
    if not any('{host}' in arg for arg in argv) or not any('{port}' in arg for arg in argv):
        raise ValueError('server argv must explicitly bind {host} and {port}')
    if any('{' in arg.replace('{host}', '').replace('{port}', '') or
           '}' in arg.replace('{host}', '').replace('{port}', '') for arg in argv):
        raise ValueError('only {host} and {port} placeholders are supported')
    scenarios = raw['scenarios']
    if (not isinstance(scenarios, list) or not 1 <= len(scenarios) <= 32
            or any(not isinstance(s, str) or not 1 <= len(s.strip()) <= 500 for s in scenarios)):
        raise ValueError('integration requires 1-32 explicit acceptance scenarios')
    if type(raw['startup_seconds']) is not int or not 1 <= raw['startup_seconds'] <= 600:
        raise ValueError('integration startup_seconds must be 1-600')
    return {**raw, 'server_argv':argv, 'scenarios':[s.strip() for s in scenarios]}


def validate_argv(raw):
    if (not isinstance(raw, list) or not 1 <= len(raw) <= 128
            or any(not isinstance(arg, str) or not arg or len(arg) > 4096 or '\0' in arg for arg in raw)):
        raise ValueError('command must be a nonempty argv list, without shell expansion')
    return list(raw)


def get(ledger, reservation_id):
    row = ledger.connection.execute('SELECT * FROM integration_leases WHERE reservation_id=?',
                                    (reservation_id,)).fetchone()
    if row is None:
        return None
    return {'plan':validate_plan(json.loads(row['plan'])), 'server_slot':row['server_slot']}


def companion(ledger, reservation):
    value = get(ledger, reservation['reservation_id'])
    if not value or not value['server_slot']:
        return None
    return {**reservation, 'kind':'server_slot', 'mode':'batch', 'resource':value['server_slot'],
            'commit_sha':value['plan']['server_commit'], 'server_test_plan':FULL_SERVER_PLAN,
            'integration':value}


def server_pending(ledger, *, single_only=False):
    """Include combined requests in warmup priority without creating a second queue entry."""
    requests = [r for r in ledger.reservations(states=('queued',))
                if r['kind']=='server_slot' or get(ledger, r['reservation_id'])]
    if single_only:
        from .execution import single
        requests = [r for r in requests if single(ledger.item(r['item_id']))]
    from .admission_scope import read, issue_ids
    selected = read(ledger.connection)
    if selected:
        requests = [r for r in requests if ledger.item(r['item_id'])['issue_id'] in issue_ids(selected)]
    return requests


def copy_plan(ledger, source, replacement):
    """Called inside the existing retry transaction; never carry an occupied server slot."""
    value = get(ledger, source)
    if value:
        ledger.connection.execute('INSERT INTO integration_leases(reservation_id,plan) VALUES(?,?)',
                                  (replacement, json.dumps(value['plan'])))


def settle_companion(ledger, reservation, reason):
    """Controller-only, after journal ownership and *all* worker attempts' teardown.

    Unity recovery can rotate the old worker token. The server's verified journal
    must still be cleanable after that fencing, without reviving the old token.
    """
    with ledger._transaction():
        current = ledger.reservation(reservation['reservation_id'])
        value = get(ledger, reservation['reservation_id'])
        if not value or value['server_slot']!=reservation['resource']:
            raise ValueError('companion settlement identity changed')
        if current['state'] in ('active','cancel_requested'):
            state = 'cancelled' if current['state']=='cancel_requested' else 'released'
            ledger.connection.execute('UPDATE reservations SET state=?,released_at=?,release_reason=? WHERE reservation_id=?',
                                      (state, ledger.clock(), reason, current['reservation_id']))
            # A quarantined Unity Editor remains the recovery controller's resource.
            ledger.connection.execute("UPDATE slots SET state='switching',updated_at=? WHERE slot_id=? AND state<>'held'",
                                      (ledger.clock(), current['resource']))
            ledger._audit(current['item_id'], 'reservation', state,
                          {'reservation_id':current['reservation_id'], 'reason':reason})
