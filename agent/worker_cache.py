"""Per-item Go content-addressed cache; never evidence or arbitrary binary reuse."""
from pathlib import Path


def compiler_cache(launcher, item_id):
    root = launcher.state_dir(item_id).absolute()
    # spawn and retention share the launcher lock. Old attempts must be contained
    # and empty before a new one can reuse their writable compiler artifacts.
    if any(root.glob('*/process.json')):
        launcher.assert_quiescent(item_id, None)
    cache = root / 'compiler-cache-v1' / 'go-cache'
    for parent in (cache, *cache.parents):
        if parent.is_symlink() or parent.is_junction():
            raise RuntimeError('compiler cache path contains a link')
    cache.mkdir(parents=True, exist_ok=True)
    return cache
