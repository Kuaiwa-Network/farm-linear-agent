"""Archive relocated Bee build state only while an owned slot Editor is closed."""
import hashlib
import json
from pathlib import Path
import re
from uuid import uuid4

from .fgui_install import _bytes
from .launcher import _write_worker_file
from .preview_upload import _plain_path


def archive_relocated_bee(folder, *, editor_open):
    folder = Path(folder)
    _plain_path(folder)
    library = folder / 'Library'
    bee = library / 'Bee'
    marker = library / 'farmbot-bee-location.json'
    if not bee.exists():
        return None
    _plain_path(bee); _plain_path(marker)
    current = str(folder.resolve())
    prior = json.loads(_bytes(marker, 4096).decode('utf-8')) if marker.exists() else {}
    if not isinstance(prior, dict) or prior.get('project') is not None and not isinstance(prior['project'], str):
        raise ValueError('Bee cache location evidence is damaged; preserve it for recovery')
    relocated = prior.get('project') not in (None, current)
    log = folder / 'Logs' / 'farmbot-editor.log'
    failure_hash = None
    if log.exists():
        _plain_path(log)
        # Only the bounded tail is needed; old paths are evidence, never targets.
        with log.open('rb') as stream:
            stream.seek(max(0, log.stat().st_size - (1 << 20)))
            raw = stream.read((1 << 20) + 1)
        text = raw.decode('utf-8', errors='replace')
        missing = re.findall(r'Could not find a part of the path "([^"\r\n]+)"', text)
        for value in missing:
            parts = value.replace('\\', '/').split('/Library/Bee/', 1)
            if len(parts) == 2 and parts[0].casefold() != current.replace('\\', '/').casefold():
                failure_hash = hashlib.sha256(raw).hexdigest()
                relocated |= failure_hash != prior.get('handled_failure_sha256')
    if editor_open():
        raise ValueError('Bee cache maintenance requires the configured Editor to be closed')
    evidence = None
    if relocated:
        archive = library / 'FarmBotCacheRecovery' / uuid4().hex
        _plain_path(archive.parent)
        archive.mkdir(parents=True)
        target = archive / 'Bee'
        # Both resolved absolute targets remain inside this exact slot Library.
        if (bee.resolve() != library.resolve() / 'Bee'
                or not target.resolve().is_relative_to(library.resolve())):
            raise ValueError('Bee cache archive escaped the configured Library')
        if editor_open():
            raise ValueError('Editor started during Bee cache maintenance')
        bee.rename(target)
        evidence = {'cache': 'Library/Bee', 'archive': str(target),
                    'project': current, 'previous_project': prior.get('project'),
                    'failure_sha256': failure_hash}
        (archive / 'receipt.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
    _write_worker_file(marker, json.dumps({'project': current, 'handled_failure_sha256': failure_hash
                                          or prior.get('handled_failure_sha256')}) + '\n', sync=True)
    return evidence
