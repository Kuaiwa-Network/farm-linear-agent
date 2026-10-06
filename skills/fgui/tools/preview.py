"""Native entry; dependencies must already be prepared, never installed by a worker."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
try:
    from agent.ui_preview import main
except ModuleNotFoundError as exc:
    if exc.name != "PIL":
        raise
    print("preview requires the prepared pinned Pillow dependency; see requirements-ui.txt", file=sys.stderr)
    raise SystemExit(1) from None

if __name__ == "__main__":
    raise SystemExit(main())
