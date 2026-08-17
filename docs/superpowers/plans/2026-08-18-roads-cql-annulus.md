# Roads CQL Annulus Pushdown Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the CQL_FILTER-based annulus pushdown already built for Metsaregister's
`eraldis` fetch to ETAK's road/barrier fetch, cutting `download_roads.py`'s real-world
runtime from ~12 minutes (fetch-full-disc-then-post-filter) down to roughly what
`download_eraldis.py` now takes.

**Architecture:** A new shared `src/shroom_fm/cql.py` module houses the CQL point/filter
construction logic (now proven byte-for-byte identical between Metsaregister and ETAK).
`eraldis.py`'s `fetch_eraldis_annulus` and a new `roads.py` `fetch_layer_annulus` both call
into it. `compute_bbox`, `filter_within_radius`, and `fetch_layer_bbox` are deleted once
`download_roads.py` no longer needs them.

**Tech Stack:** Python, GeoPandas, `requests` (via the existing `get_with_retry`), pytest —
same as the rest of the project. No new dependencies.

## Global Constraints

- ETAK's `etak:e_501_tee_j` and `etak:e_505_liikluskorralduslik_rajatis_j` both use `shape`
  as their real geometry attribute name (confirmed live via `DescribeFeatureType`), the same
  as Metsaregister's `eraldis` layer.
- CQL behavior on ETAK is confirmed live to be identical to Metsaregister's: native
  `EPSG:3301`, **northing-first** `POINT(y x)` literal, distances in raw meters (unit
  keyword not honored), `srsName=EPSG:3301` required for output on these ETAK layers
  (unlike Metsaregister, which accepts `EPSG:4326` output directly).
- The `inner_radius_km >= radius_km` `ValueError` validation lives in `cql.annulus_filter`
  itself — neither `fetch_eraldis_annulus` nor the new `fetch_layer_annulus` re-implements
  it; both get it automatically by calling `annulus_filter`.
- `cql.py` defines its own `ESTONIAN_GRID_CRS = "EPSG:3301"` / `WGS84_CRS = "EPSG:4326"`
  rather than importing them from `eraldis.py` (would be circular, since `eraldis.py` calls
  into `cql.py`). `eraldis.py` keeps exporting its own copies of these two constants
  unchanged — other modules (`access.py`, `scout.py`) still import them from `eraldis.py`.
- `compute_bbox`, `filter_within_radius`, `KM_PER_DEGREE_LAT`, `BBOX_PADDING_FACTOR` are
  deleted from `eraldis.py` — confirmed via `grep -rn "compute_bbox\|filter_within_radius"
  src/ scripts/ tests/` that `download_roads.py` was their only remaining production
  caller. `fetch_layer_bbox` is deleted from `roads.py` for the same reason.
- Since CQL forces `srsName=EPSG:3301` output for both ETAK fetches, `download_roads.py`
  no longer needs an explicit `.to_crs(ESTONIAN_GRID_CRS)` step before
  `exclude_barrier_blocked_segments` — only the final `.to_crs(WGS84_CRS)` before saving
  remains.
- `RADIUS_KM`/`INNER_RADIUS_KM` in `download_roads.py` stay at their currently active real
  values (`38.0`/`18.0`, already synced to match `download_eraldis.py` in a prior session).
- `fetch_layer_annulus`/`fetch_eraldis_annulus` remain untested beyond their `ValueError`
  case — matches this project's established precedent for live-network fetch functions.

---

### Task 1: `cql.py` shared module

**Files:**
- Create: `src/shroom_fm/cql.py`
- Create: `tests/test_cql.py`

**Interfaces:**
- Consumes: nothing from elsewhere in the codebase.
- Produces: `estonian_grid_point(lat: float, lon: float) -> str` and `annulus_filter(
  geometry_attr: str, lat: float, lon: float, radius_km: float, inner_radius_km: float) ->
  str` in `src/shroom_fm/cql.py`. Tasks 2 and 3 both import `annulus_filter` from this
  module.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cql.py`:

```python
import pytest

from shroom_fm.cql import annulus_filter, estonian_grid_point


def test_estonian_grid_point_returns_northing_first_point():
    lat, lon = 59.451081455185864, 24.818002100965362

    result = estonian_grid_point(lat, lon)

    assert result == "POINT(6590647.722702539 546398.5907798207)"


def test_annulus_filter_omits_beyond_clause_when_inner_radius_is_zero():
    lat, lon = 59.451081455185864, 24.818002100965362

    result = annulus_filter("shape", lat, lon, radius_km=20.0, inner_radius_km=0.0)

    assert result == (
        "DWITHIN(shape, POINT(6590647.722702539 546398.5907798207), 20000.0, meters)"
    )
    assert "BEYOND" not in result


def test_annulus_filter_includes_beyond_clause_when_inner_radius_is_positive():
    lat, lon = 59.451081455185864, 24.818002100965362

    result = annulus_filter("shape", lat, lon, radius_km=20.0, inner_radius_km=5.0)

    assert result == (
        "DWITHIN(shape, POINT(6590647.722702539 546398.5907798207), 20000.0, meters) "
        "AND BEYOND(shape, POINT(6590647.722702539 546398.5907798207), 5000.0, meters)"
    )


def test_annulus_filter_raises_when_inner_radius_not_less_than_outer():
    with pytest.raises(ValueError):
        annulus_filter("shape", 59.4370, 24.7536, radius_km=20.0, inner_radius_km=20.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cql.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shroom_fm.cql'`.

- [ ] **Step 3: Write the implementation**

Create `src/shroom_fm/cql.py`:

```python
import geopandas as gpd
from shapely.geometry import Point

ESTONIAN_GRID_CRS = "EPSG:3301"
WGS84_CRS = "EPSG:4326"


def estonian_grid_point(lat: float, lon: float) -> str:
    projected = (
        gpd.GeoSeries([Point(lon, lat)], crs=WGS84_CRS)
        .to_crs(ESTONIAN_GRID_CRS)
        .iloc[0]
    )
    return f"POINT({projected.y} {projected.x})"


def annulus_filter(
    geometry_attr: str, lat: float, lon: float, radius_km: float, inner_radius_km: float
) -> str:
    if inner_radius_km >= radius_km:
        raise ValueError(
            f"inner_radius_km ({inner_radius_km}) must be less than radius_km ({radius_km})"
        )
    point = estonian_grid_point(lat, lon)
    clause = f"DWITHIN({geometry_attr}, {point}, {radius_km * 1000}, meters)"
    if inner_radius_km > 0:
        clause += (
            f" AND BEYOND({geometry_attr}, {point}, {inner_radius_km * 1000}, meters)"
        )
    return clause
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cql.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the full test suite to confirm no regressions**

Run: `uv run pytest tests/ -v`
Expected: PASS (129 tests: 125 existing + 4 new)

- [ ] **Step 6: Commit**

```bash
git add src/shroom_fm/cql.py tests/test_cql.py
git commit -m "feat: add shared CQL annulus-filter builder"
```

---

### Task 2: Migrate `eraldis.py` to the shared `cql.py`

**Files:**
- Modify: `src/shroom_fm/eraldis.py` (whole file — small enough to replace in full)
- Modify: `tests/test_eraldis.py` (whole file — small enough to replace in full)

**Interfaces:**
- Consumes: `annulus_filter` from `src/shroom_fm/cql.py` (Task 1).
- Produces: `fetch_eraldis_annulus(lat: float, lon: float, radius_km: float,
  inner_radius_km: float = 0.0) -> gpd.GeoDataFrame` — same public signature as before,
  unchanged. `compute_bbox`/`filter_within_radius` no longer exist. No other task in this
  plan depends on this.

- [ ] **Step 1: Write the failing test**

Replace the full contents of `tests/test_eraldis.py` with:

```python
import pytest

from shroom_fm.eraldis import fetch_eraldis_annulus


def test_fetch_eraldis_annulus_raises_when_inner_radius_not_less_than_outer():
    with pytest.raises(ValueError):
        fetch_eraldis_annulus(59.4370, 24.7536, radius_km=20.0, inner_radius_km=20.0)
```

- [ ] **Step 2: Run test to verify it still passes against the old implementation**

Run: `uv run pytest tests/test_eraldis.py -v`
Expected: PASS (1 test) — `fetch_eraldis_annulus`'s own inline `ValueError` check, still
present at this point, already satisfies this test. This step confirms the trimmed-down
test file itself is valid before the implementation changes underneath it.

- [ ] **Step 3: Write the implementation**

Replace the full contents of `src/shroom_fm/eraldis.py` with:

```python
import io

import geopandas as gpd
import pandas as pd

from shroom_fm.cql import annulus_filter
from shroom_fm.retry import get_with_retry
from shroom_fm.wfs import METSAREGISTER_OWS_URL

ESTONIAN_GRID_CRS = "EPSG:3301"
WGS84_CRS = "EPSG:4326"
ERALDIS_TYPENAME = "metsaregister:eraldis"
GEOMETRY_ATTR = "shape"
PAGE_SIZE = 1000


def fetch_eraldis_annulus(
    lat: float,
    lon: float,
    radius_km: float,
    inner_radius_km: float = 0.0,
) -> gpd.GeoDataFrame:
    cql_filter = annulus_filter(GEOMETRY_ATTR, lat, lon, radius_km, inner_radius_km)
    pages = []
    start_index = 0
    while True:
        response = get_with_retry(
            METSAREGISTER_OWS_URL,
            params={
                "service": "WFS",
                "version": "2.0.0",
                "request": "GetFeature",
                "typeNames": ERALDIS_TYPENAME,
                "outputFormat": "application/json",
                "srsName": WGS84_CRS,
                "CQL_FILTER": cql_filter,
                "startIndex": start_index,
                "count": PAGE_SIZE,
            },
            timeout=30,
        )
        page = gpd.read_file(io.BytesIO(response.content))
        pages.append(page)
        if len(page) < PAGE_SIZE:
            break
        start_index += PAGE_SIZE
    return gpd.GeoDataFrame(pd.concat(pages, ignore_index=True), crs=pages[0].crs)
```

This removes `compute_bbox`, `filter_within_radius`, `KM_PER_DEGREE_LAT`,
`BBOX_PADDING_FACTOR`, `_cql_point`, `_build_cql_filter`, the `math` import, and the
`shapely.geometry.Point` import — none are used by anything left in the file.
`ESTONIAN_GRID_CRS`, `WGS84_CRS`, `ERALDIS_TYPENAME`, `GEOMETRY_ATTR`, `PAGE_SIZE` are kept
unchanged (still exported; `access.py`/`scout.py` import `ESTONIAN_GRID_CRS` from this
file — do not remove it).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_eraldis.py -v`
Expected: PASS (1 test) — now passing because `fetch_eraldis_annulus` calls
`annulus_filter`, which raises the same `ValueError`.

- [ ] **Step 5: Run the full test suite to confirm no regressions**

Run: `uv run pytest tests/ -v`
Expected: PASS (122 tests: 129 from Task 1, minus 7 removed from `test_eraldis.py` — the 4
`compute_bbox`/`filter_within_radius` tests and 3 `_cql_point`/`_build_cql_filter` tests
that existed before this task and are not present in the replaced file above).

- [ ] **Step 6: Manually verify no other file references the removed functions**

Run: `grep -rn "compute_bbox\|filter_within_radius\|_cql_point\|_build_cql_filter" src/
scripts/ tests/`
Expected: no matches (Task 3, not yet done, will be the one to remove
`scripts/download_roads.py`'s remaining references — if this grep finds matches there
before Task 3 runs, that's expected and will be resolved by Task 3, not this task; if it
finds matches anywhere else, investigate before committing).

- [ ] **Step 7: Commit**

```bash
git add src/shroom_fm/eraldis.py tests/test_eraldis.py
git commit -m "refactor: migrate eraldis.py to shared cql.py, remove unused bbox helpers"
```

---

### Task 3: `roads.py`'s `fetch_layer_annulus` and `download_roads.py`

**Files:**
- Modify: `src/shroom_fm/roads.py` (whole file — small enough to replace in full)
- Modify: `scripts/download_roads.py` (whole file — small enough to replace in full)
- Modify: `tests/test_roads.py` (append new test; existing tests must keep passing
  unmodified)

**Interfaces:**
- Consumes: `annulus_filter` from `src/shroom_fm/cql.py` (Task 1). Does not depend on
  Task 2 (roads.py never used `eraldis.py`'s removed functions directly).
- Produces: `fetch_layer_annulus(url: str, typename: str, lat: float, lon: float,
  radius_km: float, inner_radius_km: float = 0.0) -> gpd.GeoDataFrame` in
  `src/shroom_fm/roads.py`. `fetch_layer_bbox` no longer exists. No other task in this plan
  depends on this (final task).

- [ ] **Step 1: Write the failing test**

At the top of `tests/test_roads.py`, replace the existing import block:

```python
from shroom_fm.roads import (
    CAR_CLASS_CONDITIONAL,
    CAR_CLASS_HIGH_CONFIDENCE,
    CAR_CLASS_NORMAL,
    CAR_CLASS_WALK_ONLY,
    classify_car_class,
)
```

with:

```python
from shroom_fm.roads import (
    CAR_CLASS_CONDITIONAL,
    CAR_CLASS_HIGH_CONFIDENCE,
    CAR_CLASS_NORMAL,
    CAR_CLASS_WALK_ONLY,
    classify_car_class,
    fetch_layer_annulus,
)
```

Then append to the end of the file:

```python
def test_fetch_layer_annulus_raises_when_inner_radius_not_less_than_outer():
    with pytest.raises(ValueError):
        fetch_layer_annulus(
            "https://example.com/wfs",
            "example:layer",
            59.4370,
            24.7536,
            radius_km=20.0,
            inner_radius_km=20.0,
        )
```

- [ ] **Step 2: Run tests to verify the new test fails**

Run: `uv run pytest tests/test_roads.py -v`
Expected: the new test FAILS with `ImportError: cannot import name 'fetch_layer_annulus'`.
The 18 pre-existing tests in this file still PASS (they don't depend on
`fetch_layer_bbox`/`fetch_layer_annulus` at all).

- [ ] **Step 3: Write the implementation**

Replace the full contents of `src/shroom_fm/roads.py` with:

```python
import io

import geopandas as gpd
import pandas as pd

from shroom_fm.cql import annulus_filter
from shroom_fm.retry import get_with_retry

CAR_CLASS_HIGH_CONFIDENCE = "HIGH_CONFIDENCE"
CAR_CLASS_NORMAL = "NORMAL"
CAR_CLASS_CONDITIONAL = "CONDITIONAL"
CAR_CLASS_WALK_ONLY = "WALK_ONLY"

_HIGH_CONFIDENCE_TYPES = {
    "Põhimaantee",
    "Tugimaantee",
    "Kõrvalmaantee",
    "Ramp või ühendustee",
    "Tänav",
}
_WALK_ONLY_TYPES = {"Rada", "Kergliiklustee"}
_DRIVABLE_SURFACES = {"Püsikate", "Kruuskate", "Kivikate"}


def classify_car_class(tyyp_tekst: str, teekate_tekst: str | None) -> str:
    if tyyp_tekst in _HIGH_CONFIDENCE_TYPES:
        return CAR_CLASS_HIGH_CONFIDENCE
    if tyyp_tekst in _WALK_ONLY_TYPES:
        return CAR_CLASS_WALK_ONLY
    if tyyp_tekst == "Muu tee":
        if teekate_tekst in _DRIVABLE_SURFACES:
            return CAR_CLASS_NORMAL
        if teekate_tekst == "Pinnas":
            return CAR_CLASS_CONDITIONAL
        raise ValueError(f"Unrecognized teekate_tekst for Muu tee: {teekate_tekst!r}")
    raise ValueError(f"Unrecognized tyyp_tekst: {tyyp_tekst!r}")


BARRIER_SNAP_M = 5.0
CLOSED_BARRIER_STATUS = "Püsivalt suletud"


def exclude_barrier_blocked_segments(
    roads_gdf: gpd.GeoDataFrame, barriers_gdf: gpd.GeoDataFrame
) -> gpd.GeoDataFrame:
    closed = barriers_gdf[barriers_gdf["toke_tekst"] == CLOSED_BARRIER_STATUS]
    if closed.empty or roads_gdf.empty:
        return roads_gdf
    blocked = pd.Series(False, index=roads_gdf.index)
    for barrier_geom in closed.geometry:
        blocked |= roads_gdf.geometry.distance(barrier_geom) <= BARRIER_SNAP_M
    return roads_gdf[~blocked]


ROAD_TYPENAME = "etak:e_501_tee_j"
BARRIER_TYPENAME = "etak:e_505_liikluskorralduslik_rajatis_j"
GEOMETRY_ATTR = "shape"

_PAGE_SIZE = 1000
_ETAK_OUTPUT_CRS = "EPSG:3301"


def fetch_layer_annulus(
    url: str,
    typename: str,
    lat: float,
    lon: float,
    radius_km: float,
    inner_radius_km: float = 0.0,
) -> gpd.GeoDataFrame:
    cql_filter = annulus_filter(GEOMETRY_ATTR, lat, lon, radius_km, inner_radius_km)
    pages = []
    start_index = 0
    while True:
        response = get_with_retry(
            url,
            params={
                "service": "WFS",
                "version": "2.0.0",
                "request": "GetFeature",
                "typeNames": typename,
                "outputFormat": "application/json",
                "srsName": _ETAK_OUTPUT_CRS,
                "CQL_FILTER": cql_filter,
                "startIndex": start_index,
                "count": _PAGE_SIZE,
            },
            timeout=30,
        )
        page = gpd.read_file(io.BytesIO(response.content))
        pages.append(page)
        if len(page) < _PAGE_SIZE:
            break
        start_index += _PAGE_SIZE
    return gpd.GeoDataFrame(pd.concat(pages, ignore_index=True), crs=pages[0].crs)
```

`classify_car_class` and `exclude_barrier_blocked_segments` are byte-for-byte unchanged
from the current file. `fetch_layer_bbox` is removed entirely, along with its comment
about `owslib`'s bbox-parameter axis-reserialization bug (specific to the `bbox` parameter,
not `CQL_FILTER` — no longer applicable).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_roads.py -v`
Expected: PASS (19 tests: 18 existing + 1 new)

- [ ] **Step 5: Run the full test suite to confirm no regressions**

Run: `uv run pytest tests/ -v`
Expected: PASS (123 tests: 122 from Task 2 + 1 new)

- [ ] **Step 6: Rewrite the runner script**

Replace the full contents of `scripts/download_roads.py` with:

```python
from pathlib import Path

from shroom_fm.config import load_home_location
from shroom_fm.eraldis import WGS84_CRS
from shroom_fm.roads import (
    BARRIER_TYPENAME,
    ROAD_TYPENAME,
    classify_car_class,
    exclude_barrier_blocked_segments,
    fetch_layer_annulus,
)
from shroom_fm.wfs import ETAK_WFS_URL

RADIUS_KM = 38.0
INNER_RADIUS_KM = 18.0
ROADS_OUTPUT_PATH = Path("data/roads.geojson")
BARRIERS_OUTPUT_PATH = Path("data/barriers.geojson")


def main() -> None:
    home_lat, home_lon = load_home_location()

    roads = fetch_layer_annulus(
        ETAK_WFS_URL, ROAD_TYPENAME, home_lat, home_lon, RADIUS_KM, INNER_RADIUS_KM
    )
    roads["car_class"] = [
        classify_car_class(tyyp_tekst, teekate_tekst)
        for tyyp_tekst, teekate_tekst in zip(roads["tyyp_tekst"], roads["teekate_tekst"])
    ]

    barriers = fetch_layer_annulus(
        ETAK_WFS_URL, BARRIER_TYPENAME, home_lat, home_lon, RADIUS_KM, INNER_RADIUS_KM
    )

    roads = exclude_barrier_blocked_segments(roads, barriers)
    roads = roads.to_crs(WGS84_CRS)
    barriers = barriers.to_crs(WGS84_CRS)

    ROADS_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    roads.to_file(ROADS_OUTPUT_PATH, driver="GeoJSON")
    barriers.to_file(BARRIERS_OUTPUT_PATH, driver="GeoJSON")

    print(f"{len(roads)} road segments saved to {ROADS_OUTPUT_PATH}")
    print(f"{len(barriers)} barriers saved to {BARRIERS_OUTPUT_PATH}")


if __name__ == "__main__":
    main()
```

Note `exclude_barrier_blocked_segments(roads, barriers)` is now called directly on the
freshly-fetched `roads`/`barriers` (no `.to_crs(ESTONIAN_GRID_CRS)` step first) — both
already arrive in `EPSG:3301` because `fetch_layer_annulus` requests `srsName=EPSG:3301`
internally (ETAK requires it, confirmed live). `compute_bbox`/`filter_within_radius`/
`ESTONIAN_GRID_CRS` are no longer imported — only `WGS84_CRS` (for the final save-time
reprojection) is still needed from `eraldis.py`.

- [ ] **Step 7: Manually verify against real data if available**

Re-read `src/shroom_fm/roads.py`'s `fetch_layer_annulus` once more against
`src/shroom_fm/eraldis.py`'s `fetch_eraldis_annulus` (from Task 2) — confirm the paging
loop shape matches exactly (same `startIndex`/`count`/break-on-short-page structure), with
the only real differences being the `url`/`typename` parameters (generalized here, fixed
there) and `srsName` (`_ETAK_OUTPUT_CRS = "EPSG:3301"` here vs. `WGS84_CRS` there — this
difference is required, not a bug: ETAK rejects `EPSG:4326` output on these layers,
Metsaregister accepts it).

If live network access is available and `config.toml` exists with real home coordinates,
run the real script:

```bash
uv run python scripts/download_roads.py
```

Expected: prints two "saved to" lines with nonzero counts, completing in well under the
previous ~12-minute runtime (the annulus query should only transfer the actual 18-38km ring
data, not the full 0-38km disc). Compare the printed road/barrier counts against a prior
real run if you have one recorded — they should be very close (not necessarily bit-identical,
since ETAK data can change between runs, but same order of magnitude).

- [ ] **Step 8: Final cleanup verification**

Run: `grep -rn "compute_bbox\|filter_within_radius\|_cql_point\|_build_cql_filter\|fetch_layer_bbox" src/ scripts/ tests/`
Expected: no matches anywhere in the codebase — confirms Task 2's Step 6 note about
`download_roads.py` still referencing the old functions is now resolved by this task's
rewrite.

- [ ] **Step 9: Commit**

```bash
git add src/shroom_fm/roads.py scripts/download_roads.py tests/test_roads.py
git commit -m "feat: push roads/barriers annulus filter server-side via CQL_FILTER"
```

---

## Self-Review Notes

- **Spec coverage:** the spec's shared `cql.py` module (with centralized `ValueError`
  validation), `eraldis.py`'s migration and dead-code removal, `roads.py`'s
  `fetch_layer_annulus`, `download_roads.py`'s simplified CRS handling, and the
  full test reorganization across three files are all covered by these three tasks.
- **Placeholder scan:** none found — every step has complete, runnable code. Task 2's
  Step 6 explicitly anticipates and explains an expected transient grep match
  (`download_roads.py` still referencing removed functions until Task 3 runs), rather than
  leaving it as an unexplained gap.
- **Type consistency:** `annulus_filter(geometry_attr: str, lat: float, lon: float,
  radius_km: float, inner_radius_km: float) -> str` signature is identical across Task 1's
  tests/implementation and both Task 2's and Task 3's call sites. `fetch_layer_annulus`'s
  signature matches between Task 3's test and implementation exactly.
- **Test counts independently verified against the real repository before writing this
  plan:** current suite is 125 tests total, `test_eraldis.py` has 8, `test_roads.py` has 18
  — confirmed via `pytest --collect-only`/`grep -c "^def test_"`, not estimated. Running
  total after each task: 129 (Task 1) → 122 (Task 2) → 123 (Task 3).
