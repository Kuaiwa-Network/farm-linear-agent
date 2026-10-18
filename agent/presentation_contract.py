"""Versioned child boundaries; legacy jobs retain their original recovery protocol."""
import json
import re
from pathlib import PurePosixPath

from .ledger import LedgerError


def request(item):
    value = item.get('task_request') if isinstance(item, dict) else item['task_request']
    return json.loads(value) if isinstance(value, str) else value or {}


def source_only(item):
    parent = item.get('parent_id') if isinstance(item, dict) else item['parent_id']
    return bool(parent and item['skill'] == 'fgui'
                and request(item).get('_contract') == 'presentation-v1')


def validate_scope(value):
    if not isinstance(value, dict) or set(value) != {'assets', 'validation'}:
        raise LedgerError('scene scope requires exact assets and validation (source or unity)')
    assets = value['assets']
    if (not isinstance(assets, list) or not 0 < len(assets) <= 128
            or value['validation'] not in ('source', 'unity')):
        raise LedgerError('scene scope requires bounded assets and a validation mode')
    seen = set()
    for name in assets:
        if (not isinstance(name, str) or '\\' in name or ':' in name or '\x00' in name
                or not name.startswith('Assets/') or name.endswith('/')
                or any(p in ('', '.', '..') or p.rstrip(' .') != p
                       or re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', p)
                       for p in name.split('/')) or any(ord(c) < 32 or c in '*?<>|"' for c in name)
                or PurePosixPath(name).suffix.lower() not in
                ('.unity', '.prefab', '.asset', '.mat', '.anim', '.controller', '.overridecontroller',
                 '.png', '.jpg', '.jpeg', '.tga', '.psd', '.fbx', '.obj')):
            raise LedgerError('scene assets must be exact presentation files under Assets, never scripts or directories')
        if name.casefold() in seen:
            raise LedgerError('scene assets contain a duplicate or case alias')
        seen.add(name.casefold())
    return value


def dependencies_ready(ledger, item):
    """A scene needing FGUI installation never claims Client while waiting for it."""
    names = request(item).get('depends_on', [])
    for name in names:
        row = ledger.connection.execute('SELECT id,state,evidence FROM work_items WHERE parent_id=? AND task_key=?',
                                        (item['parent_id'], name)).fetchone()
        if not row or row['state'] != 'delivered' or not (ledger.cleanup_record(row['id']) or {}).get('done'):
            return False
        proof = json.loads(row['evidence'])
        if proof.get('ui_source_delivery_id'):
            receipt = proof.get('receipt_id')
            from .fgui_client import installation_record
            if not installation_record(ledger, item['parent_id'], receipt):
                return False
    return True
