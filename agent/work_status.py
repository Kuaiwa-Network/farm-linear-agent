"""Read-only progress from host events; never a test or completion verdict."""


def current_stage(db, item, schema=None):
    stage = item['stage']
    if item.get('execution_contract') != 'single-worker-v1' or item['state'] != 'running':
        return stage
    if item.get('generation') is None:
        return stage
    if schema is None:
        schema = {name: {r[1] for r in db.execute(f'PRAGMA table_info({name})')}
                  for name in ('reservations', 'slots', 'outbox', 'work_items')}
    has = lambda table, *fields: set(fields) <= schema.get(table, set())
    reservation = None
    if has('reservations', 'item_id', 'generation', 'sequence', 'state', 'kind', 'resource', 'created_at'):
        reservation = db.execute('SELECT state,kind,resource,created_at FROM reservations WHERE item_id=? '
            'AND generation=? ORDER BY sequence DESC LIMIT 1', (item['id'], item['generation'])).fetchone()
    if reservation and reservation['state'] in ('queued', 'active', 'cancel_requested'):
        if reservation['state'] == 'queued':
            return {'unity_slot': 'waiting-for-unity_slot', 'server_slot': 'waiting-for-server_slot'}.get(reservation['kind'], stage)
        if has('slots', 'slot_id', 'state'):
            slot = db.execute('SELECT state FROM slots WHERE slot_id=?', (reservation['resource'],)).fetchone()
            if slot and slot['state'] == 'held':
                return 'resource-held'
        return stage  # Readiness still comes from the existing ownership checks.
    if (has('work_items', 'id', 'claimed_fingerprint') and has('outbox', 'item_id', 'generation', 'fingerprint', 'kind',
                                                'created_at', 'remote_id')):
        delivery = db.execute("SELECT created_at,remote_id FROM outbox WHERE item_id=? AND generation=? "
            "AND fingerprint=(SELECT claimed_fingerprint FROM work_items WHERE id=?) "
            "AND kind='delivery' ORDER BY created_at DESC LIMIT 1",
            (item['id'], item['generation'], item['id'])).fetchone()
        if delivery and (reservation is None or delivery['created_at'] >= reservation['created_at']):
            return 'delivery-posted' if delivery['remote_id'] is not None else 'delivery-prepared'
    if reservation and reservation['state'] == 'released':
        return 'resource-released'
    return stage
