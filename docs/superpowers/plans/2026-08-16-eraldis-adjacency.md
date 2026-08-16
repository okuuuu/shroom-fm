# Neighbouring-Stand Adjacency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** For each stand in `data/eraldis.geojson`, compute which other stands it is meaningfully adjacent to (either sharing a real boundary, or separated by a small gap but running near-parallel for a real stretch), and save the result to `data/adjacency.geojson`. This is MVP step 5, the prerequisite for step 6 (ecotone detection).

**Architecture:** A new `src/shroom_fm/adjacency.py` module holds `classify_pair` (pure, the two-tier touching/near-gap classification logic), `find_candidate_pairs` (spatial-index-based candidate pruning via a buffered self-`sjoin`), and `compute_adjacency` (orchestrator). A thin `scripts/compute_adjacency.py` runner wires them together. Unlike prior pipeline steps, this one makes **no network calls** — it's pure local geometry computation.

**Tech Stack:** Python (`uv`-managed), `geopandas`/`shapely` (already dependencies), `pytest`.

## Global Constraints

- Package layout: reusable logic under `src/shroom_fm/`; thin runners under `scripts/`.
- All geometry math happens in `EPSG:3301` (Estonian national grid, meters) — reuse the `ESTONIAN_GRID_CRS`/`WGS84_CRS` constants already defined in `src/shroom_fm/eraldis.py`, do not redefine them.
- Three named constants govern classification, documented as engineering starting points, not biological constants:
  - `MAX_GAP_M = 10.0` — max distance between boundaries to consider for `near_gap`.
  - `MIN_CONTACT_LENGTH_M = 20.0` — min shared-boundary length to keep a `touching` pair (discards corner-only contacts).
  - `MIN_PROXIMITY_LENGTH_M = 20.0` — min estimated parallel-run length to keep a `near_gap` pair.
- The near-gap check only applies when `0 < gap <= MAX_GAP_M` — **not** `gap <= MAX_GAP_M`. A `gap` of exactly `0.0` means the polygons actually touch (just below the `MIN_CONTACT_LENGTH_M` threshold, e.g. a corner-only contact), and must be discarded outright, not re-evaluated as a near-gap candidate. (Verified empirically during design: without the `> 0` guard, a corner-only touch between two 100m squares gets misclassified as `near_gap` with a ~35.7m proximity length, because the buffer-intersection heuristic is fooled by the large buffer overlap around the shared corner point — the `> 0` guard is what correctly discards it.)
- No network calls in this step — none of the "no retries" constraints from earlier steps apply the same way, since there's nothing to retry.
- Testing: `classify_pair` is pure and unit tested (four cases). `find_candidate_pairs` and `compute_adjacency` are geopandas orchestration, not unit tested in isolation — verified by running `scripts/compute_adjacency.py` against real local data (no network needed for this verification, unlike prior steps).
- Output: `data/adjacency.geojson`, gitignored (derived from the already-gitignored `data/eraldis.geojson`, so geographically correlated with home the same way).
- Output columns: `id_a`, `id_b`, `adjacency_type`, `transition_length_m`, `gap_m`, `geometry` — lean, no duplicated species/kasvukoht attributes (consumers join back to `eraldis.geojson` by id).

---

### Task 1: `classify_pair` — two-tier adjacency classification

**Files:**
- Create: `src/shroom_fm/adjacency.py`
- Test: `tests/test_adjacency.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `classify_pair(geom_a, geom_b) -> dict | None` where a non-`None` return is `{"adjacency_type": "touching" | "near_gap", "transition_length_m": float, "gap_m": float, "geometry": <shapely geometry>}`. Consumed by Task 2 (`compute_adjacency`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_adjacency.py`:

```python
import pytest
from shapely.geometry import box

from shroom_fm.adjacency import classify_pair


def test_classify_pair_keeps_long_touching_border():
    geom_a = box(0, 0, 100, 100)
    geom_b = box(100, 0, 200, 100)

    result = classify_pair(geom_a, geom_b)

    assert result["adjacency_type"] == "touching"
    assert result["transition_length_m"] == pytest.approx(100.0)
    assert result["gap_m"] == 0.0


def test_classify_pair_discards_corner_only_touch():
    geom_a = box(0, 0, 100, 100)
    geom_b = box(100, 100, 200, 200)

    result = classify_pair(geom_a, geom_b)

    assert result is None


def test_classify_pair_keeps_near_gap_with_long_parallel_run():
    geom_a = box(0, 0, 100, 100)
    geom_b = box(105, 0, 205, 100)

    result = classify_pair(geom_a, geom_b)

    assert result["adjacency_type"] == "near_gap"
    assert result["transition_length_m"] == pytest.approx(171.47888553998501)
    assert result["gap_m"] == pytest.approx(5.0)


def test_classify_pair_discards_near_gap_too_short_a_run():
    geom_a = box(0, 0, 20, 20)
    geom_b = box(25, 25, 45, 45)

    result = classify_pair(geom_a, geom_b)

    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_adjacency.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shroom_fm.adjacency'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/shroom_fm/adjacency.py`:

```python
MAX_GAP_M = 10.0
MIN_CONTACT_LENGTH_M = 20.0
MIN_PROXIMITY_LENGTH_M = 20.0


def classify_pair(geom_a, geom_b) -> dict | None:
    shared = geom_a.boundary.intersection(geom_b.boundary)
    if shared.length >= MIN_CONTACT_LENGTH_M:
        return {
            "adjacency_type": "touching",
            "transition_length_m": shared.length,
            "gap_m": 0.0,
            "geometry": shared,
        }

    gap = geom_a.distance(geom_b)
    if 0 < gap <= MAX_GAP_M:
        zone = geom_a.buffer(MAX_GAP_M).intersection(geom_b.buffer(MAX_GAP_M))
        proximity_length = zone.area / MAX_GAP_M
        if proximity_length >= MIN_PROXIMITY_LENGTH_M:
            return {
                "adjacency_type": "near_gap",
                "transition_length_m": proximity_length,
                "gap_m": gap,
                "geometry": zone,
            }

    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_adjacency.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/shroom_fm/adjacency.py tests/test_adjacency.py
git commit -m "feat: add classify_pair for two-tier stand adjacency"
```

---

### Task 2: `find_candidate_pairs` + `compute_adjacency` — orchestration

**Files:**
- Modify: `src/shroom_fm/adjacency.py`

**Interfaces:**
- Consumes: `classify_pair` (Task 1); `ESTONIAN_GRID_CRS`/`WGS84_CRS` constants from `src/shroom_fm/eraldis.py`.
- Produces: `find_candidate_pairs(gdf) -> list[tuple[int, int]]` and `compute_adjacency(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame`. `compute_adjacency` is consumed by Task 3 (`scripts/compute_adjacency.py`).

No test for this step (per Global Constraints: geopandas orchestration, verified live in Task 3, not unit tested).

- [ ] **Step 1: Add the functions**

Add to the top of `src/shroom_fm/adjacency.py`:

```python
import geopandas as gpd

from shroom_fm.eraldis import ESTONIAN_GRID_CRS, WGS84_CRS
```

Add after `classify_pair`:

```python
def find_candidate_pairs(gdf: gpd.GeoDataFrame) -> list[tuple[int, int]]:
    buffered = gdf.copy()
    buffered["geometry"] = buffered.geometry.buffer(MAX_GAP_M)
    joined = gpd.sjoin(buffered, gdf, how="inner", predicate="intersects")

    pairs = set()
    for idx, row in joined.iterrows():
        id_a = gdf.loc[idx, "id"]
        id_b = row["id_right"]
        if id_a == id_b:
            continue
        pairs.add((min(id_a, id_b), max(id_a, id_b)))
    return sorted(pairs)


ADJACENCY_COLUMNS = ["id_a", "id_b", "adjacency_type", "transition_length_m", "gap_m", "geometry"]


def compute_adjacency(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    projected = gdf.to_crs(ESTONIAN_GRID_CRS)
    pairs = find_candidate_pairs(projected)

    id_to_geom = dict(zip(projected["id"], projected.geometry))

    records = []
    for id_a, id_b in pairs:
        result = classify_pair(id_to_geom[id_a], id_to_geom[id_b])
        if result is not None:
            records.append({"id_a": id_a, "id_b": id_b, **result})

    if not records:
        return gpd.GeoDataFrame(columns=ADJACENCY_COLUMNS, geometry="geometry", crs=WGS84_CRS)

    adjacency = gpd.GeoDataFrame(records, crs=ESTONIAN_GRID_CRS)
    return adjacency.to_crs(WGS84_CRS)
```

This handles the empty-result case explicitly (verified during design: constructing a `GeoDataFrame` from an empty records list without an explicit `geometry=`/columns setup raises `ValueError: Assigning CRS to a GeoDataFrame without a geometry column is not supported` — the `if not records` branch avoids this).

- [ ] **Step 2: Run the existing test suite to confirm nothing broke**

Run: `uv run pytest tests/test_adjacency.py -v`
Expected: PASS (4 passed) — this step only adds new code, it doesn't change `classify_pair`.

- [ ] **Step 3: Sanity-check with a small synthetic GeoDataFrame**

Run:
```bash
uv run python -c "
import geopandas as gpd
from shapely.geometry import box
from shroom_fm.adjacency import compute_adjacency

gdf = gpd.GeoDataFrame(
    {'id': [1, 2, 3]},
    geometry=[
        box(0, 0, 100, 100),
        box(100, 0, 200, 100),
        box(1000, 1000, 1100, 1100),
    ],
    crs='EPSG:3301',
).to_crs('EPSG:4326')

result = compute_adjacency(gdf)
print(len(result), 'adjacent pairs')
print(result[['id_a', 'id_b', 'adjacency_type', 'transition_length_m']])
"
```
Expected: `1 adjacent pairs`, with `id_a=1, id_b=2, adjacency_type=touching, transition_length_m≈100.0` — stand 3 (far away) correctly excluded.

- [ ] **Step 4: Commit**

```bash
git add src/shroom_fm/adjacency.py
git commit -m "feat: add find_candidate_pairs and compute_adjacency orchestrator"
```

---

### Task 3: `scripts/compute_adjacency.py` — runnable adjacency script

**Files:**
- Create: `scripts/compute_adjacency.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `compute_adjacency` from `src/shroom_fm/adjacency.py` (Task 2).
- Produces: nothing consumed by other tasks — this is the pipeline's end-user entry point for this step.

- [ ] **Step 1: Gitignore the output file**

Add to `.gitignore`, in the same "shroom-fm local output" section as the existing `data/eraldis.geojson` entry:

```
data/adjacency.geojson
```

- [ ] **Step 2: Write the runner script**

Create `scripts/compute_adjacency.py`:

```python
from pathlib import Path

import geopandas as gpd

from shroom_fm.adjacency import compute_adjacency

INPUT_PATH = Path("data/eraldis.geojson")
OUTPUT_PATH = Path("data/adjacency.geojson")


def main() -> None:
    gdf = gpd.read_file(INPUT_PATH)
    adjacency = compute_adjacency(gdf)
    adjacency.to_file(OUTPUT_PATH, driver="GeoJSON")

    print(f"{len(adjacency)} adjacent pairs found, saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run it against your real local data**

This step requires `data/eraldis.geojson` to already exist (from a prior branch's download/enrich scripts) — it's gitignored, so it may or may not be present depending on the checkout. If missing, produce a small local test fixture first (same pattern used in prior plans):

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

Then run:
```bash
uv run scripts/compute_adjacency.py
```

Expected: prints `N adjacent pairs found, saved to data/adjacency.geojson` with `N > 0`. This step makes no network calls, so if `data/eraldis.geojson` already exists, this runs in seconds even for a large file — no multi-hour risk like the earlier download step.

- [ ] **Step 4: Verify the output**

Run:
```bash
uv run python -c "
import geopandas as gpd
gdf = gpd.read_file('data/adjacency.geojson')
print(len(gdf), 'rows')
print(gdf.columns.tolist())
print(gdf['adjacency_type'].value_counts())
print(gdf.iloc[0])
"
```
Expected: rows with `id_a`/`id_b`/`adjacency_type`/`transition_length_m`/`gap_m`/`geometry` columns, `adjacency_type` values only `touching` or `near_gap`, and real numeric transition lengths.

- [ ] **Step 5: Run the full test suite one more time**

Run: `uv run pytest -v`
Expected: PASS (13 passed: 2 in `test_wfs.py`, 2 in `test_config.py`, 2 in `test_eraldis.py`, 3 in `test_enrich.py`, 4 in `test_adjacency.py`).

- [ ] **Step 6: Commit**

```bash
git add scripts/compute_adjacency.py .gitignore
git commit -m "feat: add compute_adjacency runner script"
```

(Do not `git add data/adjacency.geojson` — it's gitignored per Step 1; confirm via `git status` that it doesn't appear as a trackable change.)

---

## Post-plan note

This plan only covers MVP step 5 (neighbouring-stand adjacency). Step 6 — detecting ecotones by filtering adjacent pairs for meaningfully different species composition, then buffering the boundary/gap-zone geometry already computed here into a scoutable microtype — is separate follow-up work, not part of this plan. It will consume `data/adjacency.geojson` directly (the `geometry` column on `touching` rows is already the shared boundary; on `near_gap` rows it's already the buffer-intersection zone), joined back to `data/eraldis.geojson` by `id_a`/`id_b` for species/kasvukoht attributes.
