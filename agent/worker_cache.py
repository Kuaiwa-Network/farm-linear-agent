"""Per-item Go content-addressed cache; never evidence or arbitrary binary reuse."""
from pathlib import Path


def compiler_cache(launcher, item_id):
    root = launcher.state_dir(item_id).absolute()
    if root.parent != Path(launcher.runs_root).absolute() or root.name in ('.', '..'):
        raise RuntimeError('compiler cache requires a direct item directory')
    cache = root / 'compiler-cache-v1' / 'go-cache'
    # Host-selected ancestors can include system aliases such as macOS /var.
    # Do not resolve worker-owned paths before checking them: an item or cache
    # link must never redirect creation/reuse outside that item's directory.
    for parent in (root, cache.parent, cache):
        if parent.is_symlink() or parent.is_junction():
            raise RuntimeError('compiler cache path contains a link')
    # spawn and retention share the launcher lock. Old attempts must be contained
    # and empty before a new one can reuse their writable compiler artifacts.
    if any(root.glob('*/process.json')):
        launcher.assert_quiescent(item_id, None)
    cache.mkdir(parents=True, exist_ok=True)
    return cache
