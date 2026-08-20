import sys
from pathlib import Path

# scripts/ is a plain namespace package (no __init__.py) at the repo root, imported
# by main.py (`from scripts import ...`) and, since Finding 3's fix, by
# tests/test_rollup_macroclusters.py (`from scripts.rollup_macroclusters import
# _validate_columns`). Running via `python3 -c` or `python3 -m pytest` puts the repo
# root on sys.path automatically (cwd); the `pytest`/`uv run pytest` console-script
# entry point does not. Add it explicitly here so `scripts.*` imports work under
# either invocation.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
