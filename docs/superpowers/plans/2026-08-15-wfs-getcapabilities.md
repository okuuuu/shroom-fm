# WFS GetCapabilities Script Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first shroom-fm script — query the Metsaregister WFS `GetCapabilities`, print the real layer names, and save them to `data/wfs_capabilities.json` — while establishing the project's `uv`-managed package layout.

**Architecture:** A `src/shroom_fm/wfs.py` module holds three functions: `fetch_capabilities` (thin `owslib.wfs.WebFeatureService` wrapper, the network call), `layer_summary` (pure transform of `.contents` into plain dicts), and `save_layers_json` (pure I/O helper). A thin `scripts/get_capabilities.py` runner wires them together for interactive use.

**Tech Stack:** Python (managed by `uv`), `owslib` for the WFS client, `pytest` for tests.

## Global Constraints

- Dependency/environment management: `uv` (`pyproject.toml` + `uv.lock`).
- WFS endpoint: `https://gsavalik.envir.ee/geoserver/metsaregister/ows`, WFS version `"2.0.0"`.
- Package layout: reusable logic under `src/shroom_fm/`; thin runners under `scripts/`.
- No retries/fallback/custom exception handling in this script — network errors from `owslib`/`requests` propagate as-is (per spec: this is a manual diagnostic script, not an unattended pipeline stage).
- Only pure functions (`layer_summary`, `save_layers_json`) get unit tests; `fetch_capabilities` is verified by running the script against the live endpoint, not unit tested.
- Output file: `data/wfs_capabilities.json`, pretty-printed JSON list of `{"name", "title", "abstract"}`, sorted by `name`.

---

### Task 1: Project scaffold with uv

**Files:**
- Create: `pyproject.toml` (via `uv init`)
- Create: `src/shroom_fm/__init__.py`
- Create: `.python-version`
- Create: `uv.lock`

**Interfaces:**
- Produces: an importable `shroom_fm` package (empty `__init__.py`), with `owslib` and `pytest` available in the environment via `uv run`.

- [ ] **Step 1: Initialize the uv project**

Run:
```bash
uv init --package --no-readme --python 3.12
```

Expected: creates `pyproject.toml`, `src/shroom_fm/__init__.py`, `.python-version`. `uv init` detects the existing git repo and does not reinitialize it.

- [ ] **Step 2: Empty out the generated boilerplate**

`uv init --package` populates `src/shroom_fm/__init__.py` with a placeholder `main()` function. Replace its contents so the file is empty (the package needs no init-time code):

```python
```

(Write the file as zero bytes / no content.)

- [ ] **Step 3: Add runtime dependency**

Run:
```bash
uv add owslib
```

Expected: `owslib` and its transitive dependencies appear in `pyproject.toml` under `[project.dependencies]` and are locked in `uv.lock`.

- [ ] **Step 4: Add test dependency**

Run:
```bash
uv add --dev pytest
```

Expected: `pytest` appears under a dev dependency group in `pyproject.toml` and in `uv.lock`.

- [ ] **Step 5: Verify the environment**

Run:
```bash
uv run python -c "import owslib, pytest; print('ok')"
```

Expected output: `ok`

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/shroom_fm/__init__.py .python-version
git commit -m "chore: scaffold uv project with owslib and pytest"
```

---

### Task 2: `layer_summary` — extract and sort layer metadata

**Files:**
- Create: `src/shroom_fm/wfs.py`
- Test: `tests/test_wfs.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `layer_summary(wfs) -> list[dict]` where each dict is `{"name": str, "title": str | None, "abstract": str | None}`, sorted by `"name"`. Consumed by Task 5 (`scripts/get_capabilities.py`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_wfs.py`:

```python
from shroom_fm.wfs import layer_summary


class _FakeMeta:
    def __init__(self, title, abstract):
        self.title = title
        self.abstract = abstract


class _FakeWFS:
    def __init__(self, contents):
        self.contents = contents


def test_layer_summary_extracts_fields_and_sorts_by_name():
    wfs = _FakeWFS(
        {
            "metsaregister:eraldis_element": _FakeMeta(
                "Eraldis element", "Tree composition"
            ),
            "metsaregister:eraldis": _FakeMeta("Eraldis", "Stand geometry"),
        }
    )

    result = layer_summary(wfs)

    assert result == [
        {
            "name": "metsaregister:eraldis",
            "title": "Eraldis",
            "abstract": "Stand geometry",
        },
        {
            "name": "metsaregister:eraldis_element",
            "title": "Eraldis element",
            "abstract": "Tree composition",
        },
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_wfs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shroom_fm.wfs'` (or `ImportError: cannot import name 'layer_summary'`).

- [ ] **Step 3: Write minimal implementation**

Create `src/shroom_fm/wfs.py`:

```python
def layer_summary(wfs) -> list[dict]:
    layers = [
        {"name": name, "title": meta.title, "abstract": meta.abstract}
        for name, meta in wfs.contents.items()
    ]
    return sorted(layers, key=lambda layer: layer["name"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_wfs.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add src/shroom_fm/wfs.py tests/test_wfs.py
git commit -m "feat: add layer_summary for WFS capabilities"
```

---

### Task 3: `save_layers_json` — persist the layer list

**Files:**
- Modify: `src/shroom_fm/wfs.py`
- Modify: `tests/test_wfs.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `save_layers_json(layers: list[dict], path: Path) -> None`, writing pretty-printed JSON and creating parent directories as needed. Consumed by Task 5 (`scripts/get_capabilities.py`).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_wfs.py`:

```python
import json

from shroom_fm.wfs import layer_summary, save_layers_json


def test_save_layers_json_writes_file_and_creates_parent_dirs(tmp_path):
    layers = [{"name": "a", "title": "A", "abstract": None}]
    target = tmp_path / "nested" / "wfs_capabilities.json"

    save_layers_json(layers, target)

    assert target.exists()
    assert json.loads(target.read_text()) == layers
```

Update the existing `from shroom_fm.wfs import layer_summary` line at the top of the file to the combined import shown above (remove the duplicate old import line).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_wfs.py -v`
Expected: FAIL with `ImportError: cannot import name 'save_layers_json'`.

- [ ] **Step 3: Write minimal implementation**

Add to `src/shroom_fm/wfs.py` (top of file gets the new imports, function goes after `layer_summary`):

```python
import json
from pathlib import Path


def layer_summary(wfs) -> list[dict]:
    layers = [
        {"name": name, "title": meta.title, "abstract": meta.abstract}
        for name, meta in wfs.contents.items()
    ]
    return sorted(layers, key=lambda layer: layer["name"])


def save_layers_json(layers: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(layers, indent=2, ensure_ascii=False) + "\n")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_wfs.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/shroom_fm/wfs.py tests/test_wfs.py
git commit -m "feat: add save_layers_json to persist WFS layer list"
```

---

### Task 4: `fetch_capabilities` — the real WFS client call

**Files:**
- Modify: `src/shroom_fm/wfs.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `METSAREGISTER_OWS_URL` constant and `fetch_capabilities(url: str = METSAREGISTER_OWS_URL) -> WebFeatureService`. Consumed by Task 5 (`scripts/get_capabilities.py`).

No test for this step (per Global Constraints: the network call is verified by running the real script in Task 5, not unit tested).

- [ ] **Step 1: Add the constant and function**

Add to the top of `src/shroom_fm/wfs.py`, after the `import json` / `from pathlib import Path` lines:

```python
from owslib.wfs import WebFeatureService

METSAREGISTER_OWS_URL = "https://gsavalik.envir.ee/geoserver/metsaregister/ows"


def fetch_capabilities(url: str = METSAREGISTER_OWS_URL) -> WebFeatureService:
    return WebFeatureService(url, version="2.0.0")
```

Place this function before `layer_summary` in the file (so the file reads top-to-bottom as: imports, constant, `fetch_capabilities`, `layer_summary`, `save_layers_json`).

- [ ] **Step 2: Run the existing test suite to confirm nothing broke**

Run: `uv run pytest tests/test_wfs.py -v`
Expected: PASS (2 passed) — this step only adds code, it doesn't change `layer_summary`/`save_layers_json` behavior.

- [ ] **Step 3: Sanity-check the import resolves and the client constructs without erroring on argument types**

Run:
```bash
uv run python -c "from shroom_fm.wfs import fetch_capabilities, METSAREGISTER_OWS_URL; print(METSAREGISTER_OWS_URL)"
```
Expected output: `https://gsavalik.envir.ee/geoserver/metsaregister/ows`

(This only checks the import and constant — it does not make a network call. The real network call is exercised in Task 5, Step 2.)

- [ ] **Step 4: Commit**

```bash
git add src/shroom_fm/wfs.py
git commit -m "feat: add fetch_capabilities WFS client wrapper"
```

---

### Task 5: `scripts/get_capabilities.py` — runnable diagnostic script

**Files:**
- Create: `scripts/get_capabilities.py`
- Create (at runtime, then committed): `data/wfs_capabilities.json`

**Interfaces:**
- Consumes: `fetch_capabilities`, `layer_summary`, `save_layers_json` from `src/shroom_fm/wfs.py` (Tasks 2–4).
- Produces: nothing consumed by other tasks — this is the pipeline's end-user entry point for this step.

- [ ] **Step 1: Write the runner script**

Create `scripts/get_capabilities.py`:

```python
from pathlib import Path

from shroom_fm.wfs import fetch_capabilities, layer_summary, save_layers_json

OUTPUT_PATH = Path("data/wfs_capabilities.json")


def main() -> None:
    wfs = fetch_capabilities()
    layers = layer_summary(wfs)

    for layer in layers:
        print(f"{layer['name']} — {layer['title']}")

    save_layers_json(layers, OUTPUT_PATH)
    print(f"\nSaved {len(layers)} layers to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it against the live Metsaregister endpoint**

Run:
```bash
uv run scripts/get_capabilities.py
```

Expected: a list of `name — title` lines is printed (this is the manual verification of `fetch_capabilities` called for in the spec — confirm the printed names include something resembling `eraldis`, `eraldis_element`, and classifier layers), followed by a line like:
```
Saved <N> layers to data/wfs_capabilities.json
```

If this fails with a version-negotiation error from `owslib`, that is a real finding — stop and report it rather than silently changing the version string, since `"2.0.0"` was a specific design decision to revisit deliberately, not patch around.

- [ ] **Step 3: Confirm the output file**

Run:
```bash
cat data/wfs_capabilities.json
```
Expected: valid JSON, a list of objects each with `name`, `title`, `abstract` keys, sorted by `name`.

- [ ] **Step 4: Run the full test suite one more time**

Run: `uv run pytest -v`
Expected: PASS (2 passed), confirming Task 5's changes didn't touch tested behavior.

- [ ] **Step 5: Commit**

```bash
git add scripts/get_capabilities.py data/wfs_capabilities.json
git commit -m "feat: add get_capabilities runner and record real WFS layer names"
```

---

## Post-plan note

The real layer names recorded in `data/wfs_capabilities.json` after Task 5 should be checked against the assumed names in `CLAUDE.md` (`metsaregister:eraldis`, `metsaregister:eraldis_element`, classifier layers). If they differ, update `CLAUDE.md`'s "Data source: Metsaregister WFS" section accordingly — that's a follow-up, not part of this plan.
