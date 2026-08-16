# Join Tree Composition + Classifier Labels onto Eraldis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enrich the already-downloaded `data/eraldis.geojson` in place with per-stand tree composition (from `metsaregister:eraldis_element`) and human-readable classifier labels (from `kl_puuliik`/`kl_kasvukoht`), completing MVP steps 3-4.

**Architecture:** A new `src/shroom_fm/enrich.py` module holds two pure functions (`summarize_composition`, `compute_species_shares`) and two network functions (`fetch_classifier`, `fetch_eraldis_element`), composed by an orchestrator (`enrich_eraldis`). A thin `scripts/enrich_eraldis.py` runner loads the existing GeoJSON, enriches it, and overwrites it.

**Tech Stack:** Python (`uv`-managed), `owslib` + `requests` (both already dependencies), `pandas`/`geopandas` (already a dependency), `pytest`.

## Global Constraints

- Package layout: reusable logic under `src/shroom_fm/`; thin runners under `scripts/`.
- `metsaregister:eraldis_element` has no geometry — it must be filtered by `eraldis_id`, not bbox. `owslib.getfeature()` has no `CQL_FILTER` support, so the `eraldis_element` fetch uses `requests` directly against `METSAREGISTER_OWS_URL` (from `src/shroom_fm/wfs.py`) with `CQL_FILTER=eraldis_id IN (...)`, batched at 500 IDs per request.
- Classifier layers (`kl_puuliik`, `kl_kasvukoht`) are small and non-spatial — fetched in full via plain `owslib.getfeature()`, no filtering/pagination.
- Target species codes (confirmed live against the real `kl_puuliik` classifier): `pine` = `MA`, `spruce` = `KU`, `birch` = `KS`, `aspen` = `HB`.
- No retries/fallback/custom exception handling for network calls — errors from `requests`/`owslib` propagate as-is.
- Testing: `summarize_composition` and `compute_species_shares` are pure and unit tested. `fetch_eraldis_element`, `fetch_classifier`, and the `enrich_eraldis` orchestrator are not unit tested — verified by running `scripts/enrich_eraldis.py` against the live endpoint.
- Output: `data/eraldis.geojson` is overwritten in place (already gitignored from the prior branch — no change to that gitignore entry needed).
- Nested list-valued properties (the `composition` column) round-trip correctly through `geopandas`'s GeoJSON writer/reader — confirmed empirically before writing this plan. No JSON-string encoding workaround is needed.

---

### Task 1: `summarize_composition` — group composition rows by stand

**Files:**
- Create: `src/shroom_fm/enrich.py`
- Test: `tests/test_enrich.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `summarize_composition(element_df: pandas.DataFrame) -> dict[int, list[dict]]`. Consumed by Task 5 (`enrich_eraldis`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_enrich.py`:

```python
import pandas as pd

from shroom_fm.enrich import summarize_composition


def test_summarize_composition_groups_rows_by_eraldis_id():
    element_df = pd.DataFrame(
        [
            {
                "eraldis_id": 100,
                "rinne_kood": "1",
                "puuliik_kood": "MA",
                "osakaal": 80,
                "vanus": 30,
                "korgus": 12,
                "enamus": True,
                "sunniaasta": 1994,
                "paritolu": "S",
                "diameeter": 14,
                "rinnaspindala": 10.0,
                "tagavara": 90,
                "arv": 500,
            },
            {
                "eraldis_id": 100,
                "rinne_kood": "1",
                "puuliik_kood": "KU",
                "osakaal": 20,
                "vanus": 30,
                "korgus": 10,
                "enamus": False,
                "sunniaasta": 1994,
                "paritolu": "S",
                "diameeter": 12,
                "rinnaspindala": 2.0,
                "tagavara": 15,
                "arv": 100,
            },
            {
                "eraldis_id": 200,
                "rinne_kood": "1",
                "puuliik_kood": "KS",
                "osakaal": 100,
                "vanus": 15,
                "korgus": 6,
                "enamus": True,
                "sunniaasta": 2009,
                "paritolu": "N",
                "diameeter": 6,
                "rinnaspindala": 4.0,
                "tagavara": 12,
                "arv": 800,
            },
        ]
    )

    result = summarize_composition(element_df)

    assert set(result.keys()) == {100, 200}
    assert len(result[100]) == 2
    assert result[100][0]["puuliik_kood"] == "MA"
    assert result[100][0]["osakaal"] == 80
    assert len(result[200]) == 1
    assert result[200][0]["puuliik_kood"] == "KS"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_enrich.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shroom_fm.enrich'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/shroom_fm/enrich.py`:

```python
COMPOSITION_DETAIL_COLUMNS = [
    "rinne_kood",
    "puuliik_kood",
    "osakaal",
    "vanus",
    "korgus",
    "enamus",
    "sunniaasta",
    "paritolu",
    "diameeter",
    "rinnaspindala",
    "tagavara",
    "arv",
]


def summarize_composition(element_df) -> dict[int, list[dict]]:
    composition_by_id: dict[int, list[dict]] = {}
    for eraldis_id, group in element_df.groupby("eraldis_id"):
        composition_by_id[eraldis_id] = group[COMPOSITION_DETAIL_COLUMNS].to_dict("records")
    return composition_by_id
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_enrich.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add src/shroom_fm/enrich.py tests/test_enrich.py
git commit -m "feat: add summarize_composition to group eraldis_element by stand"
```

---

### Task 2: `compute_species_shares` — sum target-species shares

**Files:**
- Modify: `src/shroom_fm/enrich.py`
- Modify: `tests/test_enrich.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `compute_species_shares(composition: list[dict]) -> dict[str, float]` returning `{"pine_share": float, "spruce_share": float, "birch_share": float, "aspen_share": float}`. Consumed by Task 5 (`enrich_eraldis`).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_enrich.py`:

```python
from shroom_fm.enrich import compute_species_shares, summarize_composition


def test_compute_species_shares_sums_osakaal_by_target_species():
    composition = [
        {"puuliik_kood": "MA", "osakaal": 70},
        {"puuliik_kood": "MA", "osakaal": 10},
        {"puuliik_kood": "KU", "osakaal": 15},
        {"puuliik_kood": "NU", "osakaal": 5},
    ]

    shares = compute_species_shares(composition)

    assert shares == {
        "pine_share": 80.0,
        "spruce_share": 15.0,
        "birch_share": 0.0,
        "aspen_share": 0.0,
    }
```

Update the existing `from shroom_fm.enrich import summarize_composition` import line at the top of `tests/test_enrich.py` to the combined form shown above (remove the old single-name import line, keep `pandas as pd` import for the existing test).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_enrich.py -v`
Expected: FAIL with `ImportError: cannot import name 'compute_species_shares'`.

- [ ] **Step 3: Write minimal implementation**

Add to `src/shroom_fm/enrich.py`, after `COMPOSITION_DETAIL_COLUMNS` and before `summarize_composition`:

```python
TARGET_SPECIES_CODES = {
    "pine": "MA",
    "spruce": "KU",
    "birch": "KS",
    "aspen": "HB",
}
```

Add after `summarize_composition`:

```python
def compute_species_shares(composition: list[dict]) -> dict[str, float]:
    shares = {f"{name}_share": 0.0 for name in TARGET_SPECIES_CODES}
    for entry in composition:
        for name, code in TARGET_SPECIES_CODES.items():
            if entry["puuliik_kood"] == code:
                shares[f"{name}_share"] += entry["osakaal"]
    return shares
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_enrich.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/shroom_fm/enrich.py tests/test_enrich.py
git commit -m "feat: add compute_species_shares for target host-tree shares"
```

---

### Task 3: `fetch_classifier` — small classifier lookup fetch

**Files:**
- Modify: `src/shroom_fm/enrich.py`

**Interfaces:**
- Consumes: `WebFeatureService` type from `owslib.wfs` (already used in `src/shroom_fm/wfs.py`).
- Produces: `fetch_classifier(wfs: WebFeatureService, typename: str) -> dict[str, str]`. Consumed by Task 5 (`enrich_eraldis`).

No test for this step (per Global Constraints: network calls are verified live in Task 6, not unit tested).

- [ ] **Step 1: Add the function**

Add to the top of `src/shroom_fm/enrich.py`:

```python
import json

from owslib.wfs import WebFeatureService
```

Add after `compute_species_shares`:

```python
def fetch_classifier(wfs: WebFeatureService, typename: str) -> dict[str, str]:
    response = wfs.getfeature(typename=typename, outputFormat="application/json")
    data = json.loads(response.read())
    return {
        feature["properties"]["kood"]: feature["properties"]["kirjeldus"]
        for feature in data["features"]
    }
```

- [ ] **Step 2: Run the existing test suite to confirm nothing broke**

Run: `uv run pytest tests/test_enrich.py -v`
Expected: PASS (2 passed)

- [ ] **Step 3: Sanity-check the import resolves**

Run:
```bash
uv run python -c "from shroom_fm.enrich import fetch_classifier; print(fetch_classifier.__name__)"
```
Expected output: `fetch_classifier`

- [ ] **Step 4: Commit**

```bash
git add src/shroom_fm/enrich.py
git commit -m "feat: add fetch_classifier for kl_puuliik/kl_kasvukoht lookups"
```

---

### Task 4: `fetch_eraldis_element` — batched CQL_FILTER fetch

**Files:**
- Modify: `src/shroom_fm/enrich.py`

**Interfaces:**
- Consumes: `METSAREGISTER_OWS_URL` constant from `src/shroom_fm/wfs.py`.
- Produces: `fetch_eraldis_element(eraldis_ids: list[int]) -> pandas.DataFrame`. Consumed by Task 5 (`enrich_eraldis`).

No test for this step (network call, verified live in Task 6).

- [ ] **Step 1: Add the function**

Add to the top of `src/shroom_fm/enrich.py`, alongside the existing imports:

```python
import pandas as pd
import requests

from shroom_fm.wfs import METSAREGISTER_OWS_URL
```

Add constants after `TARGET_SPECIES_CODES`:

```python
ERALDIS_ELEMENT_TYPENAME = "metsaregister:eraldis_element"
ID_BATCH_SIZE = 500
```

Add the function after `fetch_classifier`:

```python
def fetch_eraldis_element(eraldis_ids: list[int]) -> pd.DataFrame:
    rows = []
    for i in range(0, len(eraldis_ids), ID_BATCH_SIZE):
        batch = eraldis_ids[i : i + ID_BATCH_SIZE]
        id_list = ",".join(str(eid) for eid in batch)
        response = requests.get(
            METSAREGISTER_OWS_URL,
            params={
                "service": "WFS",
                "version": "2.0.0",
                "request": "GetFeature",
                "typeName": ERALDIS_ELEMENT_TYPENAME,
                "outputFormat": "application/json",
                "CQL_FILTER": f"eraldis_id IN ({id_list})",
            },
        )
        data = response.json()
        rows.extend(feature["properties"] for feature in data["features"])
    return pd.DataFrame(rows)
```

- [ ] **Step 2: Run the existing test suite to confirm nothing broke**

Run: `uv run pytest tests/test_enrich.py -v`
Expected: PASS (2 passed)

- [ ] **Step 3: Sanity-check the import resolves and a live single-batch call works**

Run:
```bash
uv run python -c "
from shroom_fm.enrich import fetch_eraldis_element
df = fetch_eraldis_element([1734482, 1734484])
print(len(df), 'rows')
print(df.columns.tolist())
"
```
Expected: a row count greater than 0 (these are real stand IDs confirmed live during design), with columns including `eraldis_id`, `puuliik_kood`, `osakaal`.

This is a real network call, not a mock — if it errors, report the exact error rather than guessing a fix (e.g. if `CQL_FILTER` syntax needs adjustment, that's a real finding worth stopping on).

- [ ] **Step 4: Commit**

```bash
git add src/shroom_fm/enrich.py
git commit -m "feat: add fetch_eraldis_element batched CQL_FILTER query"
```

---

### Task 5: `enrich_eraldis` — orchestrator

**Files:**
- Modify: `src/shroom_fm/enrich.py`

**Interfaces:**
- Consumes: `summarize_composition`, `compute_species_shares`, `fetch_classifier`, `fetch_eraldis_element` (Tasks 1-4).
- Produces: `enrich_eraldis(gdf: geopandas.GeoDataFrame, wfs: WebFeatureService) -> geopandas.GeoDataFrame`. Consumed by Task 6 (`scripts/enrich_eraldis.py`).

No test for this step (orchestrator wiring tested pure functions with untested network calls — verified live in Task 6, per Global Constraints).

- [ ] **Step 1: Add the function**

Add to the top of `src/shroom_fm/enrich.py`, alongside the existing imports:

```python
import geopandas as gpd
```

Add two more constants after `ID_BATCH_SIZE`:

```python
PUULIIK_TYPENAME = "metsaregister:kl_puuliik"
KASVUKOHT_TYPENAME = "metsaregister:kl_kasvukoht"
```

Add the function at the end of `src/shroom_fm/enrich.py`, after `fetch_eraldis_element`:

```python
def enrich_eraldis(gdf: gpd.GeoDataFrame, wfs: WebFeatureService) -> gpd.GeoDataFrame:
    crs = gdf.crs
    eraldis_ids = gdf["id"].tolist()

    element_df = fetch_eraldis_element(eraldis_ids)
    composition_by_id = summarize_composition(element_df)

    result = gdf.copy()
    result["composition"] = result["id"].map(composition_by_id)
    result["composition"] = result["composition"].apply(
        lambda value: value if isinstance(value, list) else []
    )

    shares = result["composition"].apply(compute_species_shares)
    shares_df = pd.DataFrame(shares.tolist(), index=result.index)
    for column in shares_df.columns:
        result[column] = shares_df[column]

    puuliik_labels = fetch_classifier(wfs, PUULIIK_TYPENAME)
    kasvukoht_labels = fetch_classifier(wfs, KASVUKOHT_TYPENAME)
    result["peapuuliik_kirjeldus"] = result["peapuuliik_kood"].map(puuliik_labels)
    result["kasvukoht_kirjeldus"] = result["kasvukoht_kood"].map(kasvukoht_labels)

    return gpd.GeoDataFrame(result, geometry="geometry", crs=crs)
```

- [ ] **Step 2: Run the existing test suite to confirm nothing broke**

Run: `uv run pytest tests/test_enrich.py -v`
Expected: PASS (2 passed) — this task only adds wiring code around already-tested functions.

- [ ] **Step 3: Sanity-check the import resolves**

Run:
```bash
uv run python -c "from shroom_fm.enrich import enrich_eraldis; print(enrich_eraldis.__name__)"
```
Expected output: `enrich_eraldis`

(Full live exercise of this function happens in Task 6.)

- [ ] **Step 4: Commit**

```bash
git add src/shroom_fm/enrich.py
git commit -m "feat: add enrich_eraldis orchestrator"
```

---

### Task 6: `scripts/enrich_eraldis.py` — runnable enrichment script

**Files:**
- Create: `scripts/enrich_eraldis.py`

**Interfaces:**
- Consumes: `enrich_eraldis` (Task 5), `fetch_capabilities` from `src/shroom_fm/wfs.py`.
- Produces: nothing consumed by other tasks — this is the pipeline's end-user entry point for this step.

- [ ] **Step 1: Write the runner script**

Create `scripts/enrich_eraldis.py`:

```python
from pathlib import Path

import geopandas as gpd

from shroom_fm.enrich import enrich_eraldis
from shroom_fm.wfs import fetch_capabilities

DATA_PATH = Path("data/eraldis.geojson")


def main() -> None:
    gdf = gpd.read_file(DATA_PATH)
    wfs = fetch_capabilities()

    enriched = enrich_eraldis(gdf, wfs)
    enriched.to_file(DATA_PATH, driver="GeoJSON")

    print(f"Enriched {len(enriched)} stands, saved to {DATA_PATH}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Produce a local test dataset to enrich**

`data/eraldis.geojson` is gitignored (personal, geographically correlated with home), so a fresh checkout/worktree won't have it. Before running the enrichment script, produce a small local test file the same way the prior branch's live verification did — a small radius, not the full 80km default, so this stays fast:

```bash
cp config.example.toml config.toml
uv run python -c "
from shroom_fm.config import load_home_location
from shroom_fm.eraldis import compute_bbox, fetch_eraldis_bbox, filter_within_radius
from shroom_fm.wfs import fetch_capabilities
from pathlib import Path

lat, lon = load_home_location()
wfs = fetch_capabilities()
bbox = compute_bbox(lat, lon, 10.0)
gdf = fetch_eraldis_bbox(wfs, bbox)
nearby = filter_within_radius(gdf, lat, lon, 10.0)
Path('data').mkdir(exist_ok=True)
nearby.to_file('data/eraldis.geojson', driver='GeoJSON')
print(len(nearby), 'stands saved for testing')
"
```

Expected: a positive stand count printed, `data/eraldis.geojson` created (using `config.example.toml`'s placeholder Tallinn coordinates — this is a test fixture, not real personal data, consistent with how the prior branch's live verification worked).

- [ ] **Step 3: Run the enrichment script against the live endpoint**

Run:
```bash
uv run scripts/enrich_eraldis.py
```

Expected: prints `Enriched N stands, saved to data/eraldis.geojson` where N matches the count from Step 2.

If this fails, treat it as a real finding rather than silently working around it — in particular, an error from the `CQL_FILTER` request (Task 4's function) is a genuine integration finding worth stopping on, the same way a WFS version-negotiation error would be handled.

- [ ] **Step 4: Verify the enriched output**

Run:
```bash
uv run python -c "
import geopandas as gpd
gdf = gpd.read_file('data/eraldis.geojson')
print(gdf.columns.tolist())
row = gdf.iloc[0]
print('composition type:', type(row['composition']))
print('composition sample:', row['composition'][:2] if row['composition'] else 'empty')
print('shares:', row['pine_share'], row['spruce_share'], row['birch_share'], row['aspen_share'])
print('peapuuliik_kirjeldus:', row['peapuuliik_kirjeldus'])
print('kasvukoht_kirjeldus:', row['kasvukoht_kirjeldus'])
"
```
Expected: `composition` type is `list` (confirmed to round-trip correctly through GeoJSON — see Global Constraints), the four `*_share` columns are present with numeric values, and both `*_kirjeldus` columns contain real Estonian-language labels (not `None`, unless that particular stand's code genuinely isn't in the classifier — note which if so).

- [ ] **Step 5: Run the full test suite one more time**

Run: `uv run pytest -v`
Expected: PASS (8 passed: 2 in `test_wfs.py`, 2 in `test_config.py`, 2 in `test_eraldis.py`, 2 in `test_enrich.py`).

- [ ] **Step 6: Commit**

```bash
git add scripts/enrich_eraldis.py
git commit -m "feat: add enrich_eraldis runner script"
```

(Do not `git add data/eraldis.geojson` or `config.toml` — both are already gitignored from the prior branch; confirm via `git status` that neither appears as a trackable change.)

---

## Post-plan note

This plan only covers MVP steps 3-4 (join tree composition, join `kasvukohatüüp`) from `CLAUDE.md`. The next steps — calculating neighbouring stands, detecting ecotones, and computing `HabitatScore` — are separate follow-up work, not part of this plan.
