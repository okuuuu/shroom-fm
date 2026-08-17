# Road Access (AccessScore) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compute a per-`eraldis` `access_score` (plus `access_confidence`/`access_reason`
and raw nearest-road distances) from ETAK road and barrier data, so the pipeline can later
weigh "can I actually get there" separately from ecological suitability.

**Architecture:** New `src/shroom_fm/roads.py` fetches and classifies ETAK road/barrier
segments (`car_class` per segment, barrier-snap exclusion). New `src/shroom_fm/access.py`
computes nearest-road distances and `access_score`/`access_confidence`/`access_reason` per
`eraldis`, appended onto `data/eraldis.geojson`. Two new runner scripts
(`scripts/download_roads.py`, `scripts/score_access.py`) mirror the existing
`download_eraldis.py`/`score_habitat.py` pattern exactly.

**Tech Stack:** Python, GeoPandas, `owslib`, pytest — same as the rest of the project. No new
dependencies (no routing/graph library — see Global Constraints).

## Global Constraints

- **No real road-network graph or routing** — `AccessScore` is straight-line
  nearest-distance only. No `networkx` or similar dependency in this plan.
- `ETAK_WFS_URL = "https://gsavalik.envir.ee/geoserver/etak/wfs"` already exists in
  `src/shroom_fm/wfs.py` (committed) — do not re-add it.
- Confirmed live layer typenames: `etak:e_501_tee_j` (roads), `etak:e_505_liikluskorralduslik_rajatis_j` (barriers).
- `car_class` mapping (exact, from confirmed real `tyyp_tekst`/`teekate_tekst` values):
  `Põhimaantee` / `Tugimaantee` / `Kõrvalmaantee` / `Ramp või ühendustee` / `Tänav` →
  `HIGH_CONFIDENCE`; `Muu tee` + (`Püsikate` | `Kruuskate` | `Kivikate`) → `NORMAL`;
  `Muu tee` + `Pinnas` → `CONDITIONAL`; `Rada` / `Kergliiklustee` → `WALK_ONLY`. Any
  `tyyp_tekst` or `Muu tee` surface not in this table raises `ValueError` naming the value —
  never guess a tier for an unverified real-world value.
- Barrier handling: `BARRIER_SNAP_M = 5.0`. Only `toke_tekst == "Püsivalt suletud"` barriers
  exclude a road segment (any segment within `BARRIER_SNAP_M` of one). `"Avatav"` and
  `"Täitmata"` barriers do not affect classification.
- `ACCESS_DISTANCE_CAP_M = 1500.0`. `access_score = max(0.0, 1.0 - nearest_car_road_m /
  ACCESS_DISTANCE_CAP_M)`; `nearest_car_road_m is None` → `access_score = 0.0` (never a
  fabricated small positive number for missing/empty road data — same discipline as the
  rest of this project's `None`/`NaN` handling).
- `access_*` columns are appended directly onto `data/eraldis.geojson` (same pattern as
  `stand_habitat_score_*`) — not a separate output file.
- Out of scope for this plan: real routing graph, `road_density_500m`,
  `distance_after_last_car_point_m`, parking-lot proximity, Teeregister enrichment,
  land-cover eligibility filters, EELIS restrictions, Metsaregister clearcut evidence,
  CHM-based detection, `ScoutScore` itself. None of these are touched.
- `compute_bbox`/`filter_within_radius` (`src/shroom_fm/eraldis.py`) are reused as-is —
  both are already geometry-agnostic (operate on `.geometry.distance(...)`), no changes.

---

### Task 1: `car_class` classification

**Files:**
- Create: `src/shroom_fm/roads.py`
- Test: `tests/test_roads.py` (new file)

**Interfaces:**
- Consumes: nothing from elsewhere in the codebase.
- Produces: `classify_car_class(tyyp_tekst: str, teekate_tekst: str | None) -> str` and
  constants `CAR_CLASS_HIGH_CONFIDENCE = "HIGH_CONFIDENCE"`, `CAR_CLASS_NORMAL = "NORMAL"`,
  `CAR_CLASS_CONDITIONAL = "CONDITIONAL"`, `CAR_CLASS_WALK_ONLY = "WALK_ONLY"` in
  `src/shroom_fm/roads.py`. Task 3 and Task 4 both import these names.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_roads.py`:

```python
import pytest

from shroom_fm.roads import (
    CAR_CLASS_CONDITIONAL,
    CAR_CLASS_HIGH_CONFIDENCE,
    CAR_CLASS_NORMAL,
    CAR_CLASS_WALK_ONLY,
    classify_car_class,
)


def test_classify_car_class_pohimaantee_is_high_confidence():
    assert classify_car_class("Põhimaantee", None) == CAR_CLASS_HIGH_CONFIDENCE


def test_classify_car_class_tugimaantee_is_high_confidence():
    assert classify_car_class("Tugimaantee", None) == CAR_CLASS_HIGH_CONFIDENCE


def test_classify_car_class_korvalmaantee_is_high_confidence():
    assert classify_car_class("Kõrvalmaantee", None) == CAR_CLASS_HIGH_CONFIDENCE


def test_classify_car_class_ramp_is_high_confidence():
    assert classify_car_class("Ramp või ühendustee", None) == CAR_CLASS_HIGH_CONFIDENCE


def test_classify_car_class_tanav_is_high_confidence():
    assert classify_car_class("Tänav", "Püsikate") == CAR_CLASS_HIGH_CONFIDENCE


def test_classify_car_class_muu_tee_with_pusikate_is_normal():
    assert classify_car_class("Muu tee", "Püsikate") == CAR_CLASS_NORMAL


def test_classify_car_class_muu_tee_with_kruuskate_is_normal():
    assert classify_car_class("Muu tee", "Kruuskate") == CAR_CLASS_NORMAL


def test_classify_car_class_muu_tee_with_kivikate_is_normal():
    assert classify_car_class("Muu tee", "Kivikate") == CAR_CLASS_NORMAL


def test_classify_car_class_muu_tee_with_pinnas_is_conditional():
    assert classify_car_class("Muu tee", "Pinnas") == CAR_CLASS_CONDITIONAL


def test_classify_car_class_rada_is_walk_only():
    assert classify_car_class("Rada", "Pinnas") == CAR_CLASS_WALK_ONLY


def test_classify_car_class_kergliiklustee_is_walk_only():
    assert classify_car_class("Kergliiklustee", "Püsikate") == CAR_CLASS_WALK_ONLY


def test_classify_car_class_raises_for_unrecognized_tyyp_tekst():
    with pytest.raises(ValueError):
        classify_car_class("Mingi tundmatu tüüp", "Püsikate")


def test_classify_car_class_raises_for_unrecognized_muu_tee_surface():
    with pytest.raises(ValueError):
        classify_car_class("Muu tee", "Mingi tundmatu kate")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_roads.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shroom_fm.roads'` (the module
doesn't exist yet).

- [ ] **Step 3: Write the implementation**

Create `src/shroom_fm/roads.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_roads.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5: Commit**

```bash
git add src/shroom_fm/roads.py tests/test_roads.py
git commit -m "feat: classify ETAK road segments into car-access confidence tiers"
```

---

### Task 2: Barrier-snap exclusion

**Files:**
- Modify: `src/shroom_fm/roads.py` (append to the file created in Task 1)
- Test: `tests/test_roads.py` (append)

**Interfaces:**
- Consumes: nothing from Task 1 (independent pure geometry function, same file).
- Produces: `exclude_barrier_blocked_segments(roads_gdf: gpd.GeoDataFrame, barriers_gdf:
  gpd.GeoDataFrame) -> gpd.GeoDataFrame` and constants `BARRIER_SNAP_M = 5.0`,
  `CLOSED_BARRIER_STATUS = "Püsivalt suletud"` in `src/shroom_fm/roads.py`. Both input
  GeoDataFrames must already be in the same projected (metric) CRS — this function does no
  CRS conversion itself, matching `adjacency.py`'s `classify_pair` precedent. Task 3's
  `download_roads.py` is the caller responsible for projecting before calling this.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_roads.py`:

```python
import geopandas as gpd
from shapely.geometry import LineString, Point

from shroom_fm.roads import exclude_barrier_blocked_segments


def test_exclude_barrier_blocked_segments_removes_segment_near_closed_barrier():
    roads_gdf = gpd.GeoDataFrame(
        {"name": ["blocked", "clear"]},
        geometry=[
            LineString([(0, 0), (10, 0)]),
            LineString([(1000, 1000), (1010, 1000)]),
        ],
        crs="EPSG:3301",
    )
    barriers_gdf = gpd.GeoDataFrame(
        {"toke_tekst": ["Püsivalt suletud"]},
        geometry=[Point(5, 3)],  # 3m from "blocked", within BARRIER_SNAP_M
        crs="EPSG:3301",
    )

    result = exclude_barrier_blocked_segments(roads_gdf, barriers_gdf)

    assert list(result["name"]) == ["clear"]


def test_exclude_barrier_blocked_segments_keeps_segment_near_openable_barrier():
    roads_gdf = gpd.GeoDataFrame(
        {"name": ["near_openable"]},
        geometry=[LineString([(0, 0), (10, 0)])],
        crs="EPSG:3301",
    )
    barriers_gdf = gpd.GeoDataFrame(
        {"toke_tekst": ["Avatav"]},
        geometry=[Point(5, 3)],
        crs="EPSG:3301",
    )

    result = exclude_barrier_blocked_segments(roads_gdf, barriers_gdf)

    assert list(result["name"]) == ["near_openable"]


def test_exclude_barrier_blocked_segments_keeps_all_when_no_barriers():
    roads_gdf = gpd.GeoDataFrame(
        {"name": ["a"]},
        geometry=[LineString([(0, 0), (10, 0)])],
        crs="EPSG:3301",
    )
    barriers_gdf = gpd.GeoDataFrame({"toke_tekst": []}, geometry=[], crs="EPSG:3301")

    result = exclude_barrier_blocked_segments(roads_gdf, barriers_gdf)

    assert list(result["name"]) == ["a"]


def test_exclude_barrier_blocked_segments_keeps_segment_beyond_snap_distance():
    roads_gdf = gpd.GeoDataFrame(
        {"name": ["far"]},
        geometry=[LineString([(0, 0), (10, 0)])],
        crs="EPSG:3301",
    )
    barriers_gdf = gpd.GeoDataFrame(
        {"toke_tekst": ["Püsivalt suletud"]},
        geometry=[Point(100, 100)],  # ~135m away, beyond BARRIER_SNAP_M
        crs="EPSG:3301",
    )

    result = exclude_barrier_blocked_segments(roads_gdf, barriers_gdf)

    assert list(result["name"]) == ["far"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_roads.py -v`
Expected: The 4 new tests FAIL with `ImportError: cannot import name
'exclude_barrier_blocked_segments'`.

- [ ] **Step 3: Write the implementation**

Append to `src/shroom_fm/roads.py` (add `import pandas as pd` and `import geopandas as gpd`
to the top of the file, alongside the existing content from Task 1):

```python
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
```

The full top of `src/shroom_fm/roads.py` after this step should read:

```python
import geopandas as gpd
import pandas as pd

CAR_CLASS_HIGH_CONFIDENCE = "HIGH_CONFIDENCE"
...
```

(i.e. add the two imports once, above the Task 1 constants — do not duplicate them).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_roads.py -v`
Expected: PASS (17 tests: 13 from Task 1 + 4 new)

- [ ] **Step 5: Run the full test suite to confirm no regressions**

Run: `uv run pytest tests/ -v`
Expected: PASS (90 tests: 73 existing + 17 in `test_roads.py`)

- [ ] **Step 6: Commit**

```bash
git add src/shroom_fm/roads.py tests/test_roads.py
git commit -m "feat: exclude road segments blocked by permanently-closed barriers"
```

---

### Task 3: ETAK road/barrier ingestion

**Files:**
- Modify: `src/shroom_fm/roads.py` (append `fetch_layer_bbox` and typename constants)
- Create: `scripts/download_roads.py`

**Interfaces:**
- Consumes: `classify_car_class` (Task 1), `exclude_barrier_blocked_segments` (Task 2),
  `compute_bbox`/`filter_within_radius` from `src/shroom_fm/eraldis.py` (existing,
  unmodified), `ESTONIAN_GRID_CRS`/`WGS84_CRS` from `src/shroom_fm/eraldis.py` (existing),
  `ETAK_WFS_URL`/`fetch_capabilities` from `src/shroom_fm/wfs.py` (existing, already
  committed), `load_home_location` from `src/shroom_fm/config.py` (existing).
- Produces: `fetch_layer_bbox(wfs, typename: str, bbox: tuple[float, float, float, float]) ->
  gpd.GeoDataFrame` in `src/shroom_fm/roads.py`, plus `ROAD_TYPENAME =
  "etak:e_501_tee_j"` and `BARRIER_TYPENAME = "etak:e_505_liikluskorralduslik_rajatis_j"`
  constants. `scripts/download_roads.py` writes `data/roads.geojson` (road segments with a
  `car_class` column, barrier-excluded, within radius) and `data/barriers.geojson` (all
  fetched barrier points within radius, unfiltered). Task 4's `scripts/score_access.py`
  reads `data/roads.geojson` expecting `geometry`, `tyyp_tekst`, `teekate_tekst`, and
  `car_class` columns.

This task has no dedicated unit test — `fetch_layer_bbox` is a live-network paged fetch,
same precedent as `fetch_eraldis_bbox` in `src/shroom_fm/eraldis.py` (which also has no
test; see `tests/test_eraldis.py`, which only tests the pure `compute_bbox`/
`filter_within_radius` functions). Verification here is manual: reading the diff against
the confirmed-live schema, and (optionally, if network access is available) an actual run.

- [ ] **Step 1: Write the implementation**

Append to `src/shroom_fm/roads.py` (add `import io` at the top alongside the existing
`geopandas`/`pandas` imports):

```python
ROAD_TYPENAME = "etak:e_501_tee_j"
BARRIER_TYPENAME = "etak:e_505_liikluskorralduslik_rajatis_j"

_PAGE_SIZE = 1000
_WGS84_URN = "urn:ogc:def:crs:EPSG::4326"


def fetch_layer_bbox(wfs, typename: str, bbox: tuple[float, float, float, float]) -> gpd.GeoDataFrame:
    pages = []
    start_index = 0
    while True:
        response = wfs.getfeature(
            typename=typename,
            bbox=(*bbox, _WGS84_URN),
            srsname="EPSG:4326",
            outputFormat="application/json",
            startindex=start_index,
            maxfeatures=_PAGE_SIZE,
        )
        page = gpd.read_file(io.BytesIO(response.read()))
        pages.append(page)
        if len(page) < _PAGE_SIZE:
            break
        start_index += _PAGE_SIZE
    return gpd.GeoDataFrame(pd.concat(pages, ignore_index=True), crs=pages[0].crs)
```

This mirrors `fetch_eraldis_bbox` in `src/shroom_fm/eraldis.py` exactly (same paging loop,
same `bbox`/`srsname`/pagination parameters), generalized over `typename` instead of being
hardcoded to `metsaregister:eraldis`. It intentionally does not import or call
`fetch_eraldis_bbox` — that function is hardcoded to `ERALDIS_TYPENAME`, so duplicating the
short paging loop here keeps this module self-contained rather than reworking
`eraldis.py`'s existing, unrelated function.

Create `scripts/download_roads.py`:

```python
from pathlib import Path

from shroom_fm.config import load_home_location
from shroom_fm.eraldis import ESTONIAN_GRID_CRS, WGS84_CRS, compute_bbox, filter_within_radius
from shroom_fm.roads import (
    BARRIER_TYPENAME,
    ROAD_TYPENAME,
    classify_car_class,
    exclude_barrier_blocked_segments,
    fetch_layer_bbox,
)
from shroom_fm.wfs import ETAK_WFS_URL, fetch_capabilities

RADIUS_KM = 20.0
INNER_RADIUS_KM = 0.0
ROADS_OUTPUT_PATH = Path("data/roads.geojson")
BARRIERS_OUTPUT_PATH = Path("data/barriers.geojson")


def main() -> None:
    home_lat, home_lon = load_home_location()
    wfs = fetch_capabilities(ETAK_WFS_URL)
    bbox = compute_bbox(home_lat, home_lon, RADIUS_KM)

    roads = fetch_layer_bbox(wfs, ROAD_TYPENAME, bbox)
    roads = filter_within_radius(roads, home_lat, home_lon, RADIUS_KM, INNER_RADIUS_KM)
    roads["car_class"] = [
        classify_car_class(tyyp_tekst, teekate_tekst)
        for tyyp_tekst, teekate_tekst in zip(roads["tyyp_tekst"], roads["teekate_tekst"])
    ]

    barriers = fetch_layer_bbox(wfs, BARRIER_TYPENAME, bbox)
    barriers = filter_within_radius(barriers, home_lat, home_lon, RADIUS_KM, INNER_RADIUS_KM)

    roads_projected = roads.to_crs(ESTONIAN_GRID_CRS)
    barriers_projected = barriers.to_crs(ESTONIAN_GRID_CRS)
    roads_projected = exclude_barrier_blocked_segments(roads_projected, barriers_projected)
    roads = roads_projected.to_crs(WGS84_CRS)

    ROADS_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    roads.to_file(ROADS_OUTPUT_PATH, driver="GeoJSON")
    barriers.to_file(BARRIERS_OUTPUT_PATH, driver="GeoJSON")

    print(f"{len(roads)} road segments saved to {ROADS_OUTPUT_PATH}")
    print(f"{len(barriers)} barriers saved to {BARRIERS_OUTPUT_PATH}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the full test suite to confirm no regressions**

Run: `uv run pytest tests/ -v`
Expected: PASS (90 tests — this step adds no new tests, only wiring)

- [ ] **Step 3: Manually verify the new code**

Re-read `src/shroom_fm/roads.py`'s new `fetch_layer_bbox` against `fetch_eraldis_bbox` in
`src/shroom_fm/eraldis.py` side-by-side — confirm the paging loop (`startindex`/
`maxfeatures`/`len(page) < _PAGE_SIZE` break condition) matches exactly, just generalized
over `typename`. Re-read `scripts/download_roads.py` to confirm the CRS round-trip
(`to_crs(ESTONIAN_GRID_CRS)` → `exclude_barrier_blocked_segments` → `to_crs(WGS84_CRS)`)
projects before the barrier-exclusion distance check and converts back before saving.

If live network access is available, additionally run:

```bash
uv run python scripts/download_roads.py
```

Expected: prints two "saved to" lines with nonzero counts, and `data/roads.geojson`/
`data/barriers.geojson` exist. If `classify_car_class` raises `ValueError`, it means a real
`tyyp_tekst` or `Muu tee` surface exists in the live data that wasn't in the confirmed
sample — report this rather than silently widening the mapping to guess a tier.

- [ ] **Step 4: Commit**

```bash
git add src/shroom_fm/roads.py scripts/download_roads.py
git commit -m "feat: fetch and classify ETAK road/barrier data within radius of home"
```

---

### Task 4: `AccessScore` computation

**Files:**
- Create: `src/shroom_fm/access.py`
- Create: `scripts/score_access.py`
- Test: `tests/test_access.py` (new file)

**Interfaces:**
- Consumes: `CAR_CLASS_HIGH_CONFIDENCE`/`CAR_CLASS_WALK_ONLY` from `src/shroom_fm/roads.py`
  (Task 1), `ESTONIAN_GRID_CRS` from `src/shroom_fm/eraldis.py` (existing), and
  `data/roads.geojson`'s schema from Task 3 (`geometry`, `tyyp_tekst`, `car_class` columns).
- Produces: `access_score(nearest_car_road_m: float | None) -> float`,
  `access_reason(nearest_car_road_m: float | None, tyyp_tekst: str | None) -> str`,
  `nearest_segment(point_geom, roads_gdf: gpd.GeoDataFrame) -> tuple[pd.Series, float] |
  None`, `score_eraldis_access(eraldis_geom, roads_gdf: gpd.GeoDataFrame) -> dict`,
  `score_access(eraldis_gdf: gpd.GeoDataFrame, roads_gdf: gpd.GeoDataFrame) ->
  gpd.GeoDataFrame`, and `ACCESS_DISTANCE_CAP_M = 1500.0` in `src/shroom_fm/access.py`. No
  later task depends on this (final task in the plan).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_access.py`:

```python
import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point

from shroom_fm.access import (
    ACCESS_DISTANCE_CAP_M,
    access_reason,
    access_score,
    nearest_segment,
    score_access,
    score_eraldis_access,
)


def test_nearest_segment_returns_closest_row_and_distance():
    roads_gdf = gpd.GeoDataFrame(
        {"name": ["near", "far"]},
        geometry=[
            LineString([(0, 100), (10, 100)]),
            LineString([(0, 1000), (10, 1000)]),
        ],
        crs="EPSG:3301",
    )

    row, distance = nearest_segment(Point(0, 0), roads_gdf)

    assert row["name"] == "near"
    assert distance == pytest.approx(100.0)


def test_nearest_segment_returns_none_for_empty_roads():
    roads_gdf = gpd.GeoDataFrame({"name": []}, geometry=[], crs="EPSG:3301")

    assert nearest_segment(Point(0, 0), roads_gdf) is None


def test_access_score_is_zero_for_none_distance():
    assert access_score(None) == 0.0


def test_access_score_is_one_at_zero_distance():
    assert access_score(0.0) == 1.0


def test_access_score_is_zero_at_or_beyond_cap():
    assert access_score(ACCESS_DISTANCE_CAP_M) == 0.0
    assert access_score(ACCESS_DISTANCE_CAP_M * 2) == 0.0


def test_access_score_scales_linearly_mid_range():
    assert access_score(750.0) == pytest.approx(0.5)


def test_access_reason_for_no_car_road():
    assert (
        access_reason(None, None)
        == f"no car-accessible road within {ACCESS_DISTANCE_CAP_M:.0f}m"
    )


def test_access_reason_names_distance_and_type():
    assert access_reason(320.0, "Kõrvalmaantee") == "320m from Kõrvalmaantee-class road"


def test_score_eraldis_access_computes_all_fields():
    roads_gdf = gpd.GeoDataFrame(
        {
            "car_class": ["HIGH_CONFIDENCE", "WALK_ONLY"],
            "tyyp_tekst": ["Kõrvalmaantee", "Rada"],
        },
        geometry=[
            LineString([(0, 100), (10, 100)]),
            LineString([(0, 50), (10, 50)]),
        ],
        crs="EPSG:3301",
    )

    result = score_eraldis_access(Point(0, 0), roads_gdf)

    assert result["nearest_car_road_m"] == pytest.approx(100.0)
    assert result["nearest_high_confidence_road_m"] == pytest.approx(100.0)
    assert result["nearest_walk_path_m"] == pytest.approx(50.0)
    assert result["access_confidence"] == "HIGH_CONFIDENCE"
    assert result["access_score"] == pytest.approx(access_score(100.0))
    assert result["access_reason"] == "100m from Kõrvalmaantee-class road"


def test_score_eraldis_access_handles_no_roads_at_all():
    roads_gdf = gpd.GeoDataFrame(
        {"car_class": [], "tyyp_tekst": []}, geometry=[], crs="EPSG:3301"
    )

    result = score_eraldis_access(Point(0, 0), roads_gdf)

    assert result["nearest_car_road_m"] is None
    assert result["nearest_high_confidence_road_m"] is None
    assert result["nearest_walk_path_m"] is None
    assert result["access_score"] == 0.0
    assert result["access_confidence"] is None
    assert (
        result["access_reason"]
        == f"no car-accessible road within {ACCESS_DISTANCE_CAP_M:.0f}m"
    )


def test_score_access_appends_columns_to_eraldis_gdf():
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1]},
        geometry=[Point(24.0, 59.0)],
        crs="EPSG:4326",
    )
    roads_gdf = gpd.GeoDataFrame(
        {"car_class": ["NORMAL"], "tyyp_tekst": ["Muu tee"]},
        geometry=[LineString([(24.0, 59.001), (24.001, 59.001)])],
        crs="EPSG:4326",
    )

    result = score_access(eraldis_gdf, roads_gdf)

    assert "access_score" in result.columns
    assert "access_reason" in result.columns
    assert "nearest_car_road_m" in result.columns
    assert result.loc[0, "id"] == 1
    assert result.crs == "EPSG:4326"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_access.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shroom_fm.access'`.

- [ ] **Step 3: Write the implementation**

Create `src/shroom_fm/access.py`:

```python
import geopandas as gpd

from shroom_fm.eraldis import ESTONIAN_GRID_CRS
from shroom_fm.roads import CAR_CLASS_HIGH_CONFIDENCE, CAR_CLASS_WALK_ONLY

CAR_ELIGIBLE_CLASSES = {"HIGH_CONFIDENCE", "NORMAL", "CONDITIONAL"}
ACCESS_DISTANCE_CAP_M = 1500.0


def nearest_segment(point_geom, roads_gdf: gpd.GeoDataFrame):
    if roads_gdf.empty:
        return None
    distances = roads_gdf.geometry.distance(point_geom)
    idx = distances.idxmin()
    return roads_gdf.loc[idx], distances.loc[idx]


def access_score(nearest_car_road_m: float | None) -> float:
    if nearest_car_road_m is None:
        return 0.0
    return max(0.0, 1.0 - nearest_car_road_m / ACCESS_DISTANCE_CAP_M)


def access_reason(nearest_car_road_m: float | None, tyyp_tekst: str | None) -> str:
    if nearest_car_road_m is None:
        return f"no car-accessible road within {ACCESS_DISTANCE_CAP_M:.0f}m"
    return f"{nearest_car_road_m:.0f}m from {tyyp_tekst}-class road"


def score_eraldis_access(eraldis_geom, roads_gdf: gpd.GeoDataFrame) -> dict:
    car_roads = roads_gdf[roads_gdf["car_class"].isin(CAR_ELIGIBLE_CLASSES)]
    hc_roads = roads_gdf[roads_gdf["car_class"] == CAR_CLASS_HIGH_CONFIDENCE]
    walk_roads = roads_gdf[roads_gdf["car_class"] == CAR_CLASS_WALK_ONLY]

    car_match = nearest_segment(eraldis_geom, car_roads)
    hc_match = nearest_segment(eraldis_geom, hc_roads)
    walk_match = nearest_segment(eraldis_geom, walk_roads)

    nearest_car_road_m = car_match[1] if car_match is not None else None
    access_confidence = car_match[0]["car_class"] if car_match is not None else None
    nearest_car_tyyp_tekst = car_match[0]["tyyp_tekst"] if car_match is not None else None

    return {
        "nearest_car_road_m": nearest_car_road_m,
        "nearest_high_confidence_road_m": hc_match[1] if hc_match is not None else None,
        "nearest_walk_path_m": walk_match[1] if walk_match is not None else None,
        "access_score": access_score(nearest_car_road_m),
        "access_confidence": access_confidence,
        "access_reason": access_reason(nearest_car_road_m, nearest_car_tyyp_tekst),
    }


def score_access(
    eraldis_gdf: gpd.GeoDataFrame, roads_gdf: gpd.GeoDataFrame
) -> gpd.GeoDataFrame:
    result = eraldis_gdf.copy()
    eraldis_projected = eraldis_gdf.to_crs(ESTONIAN_GRID_CRS)
    roads_projected = roads_gdf.to_crs(ESTONIAN_GRID_CRS)

    records = [
        score_eraldis_access(geom, roads_projected) for geom in eraldis_projected.geometry
    ]

    for key in (
        "nearest_car_road_m",
        "nearest_high_confidence_road_m",
        "nearest_walk_path_m",
        "access_score",
        "access_confidence",
        "access_reason",
    ):
        result[key] = [record[key] for record in records]

    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_access.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Run the full test suite to confirm no regressions**

Run: `uv run pytest tests/ -v`
Expected: PASS (100 tests: 90 from prior tasks + 10 in `test_access.py`)

- [ ] **Step 6: Add the runner script**

Create `scripts/score_access.py`:

```python
from pathlib import Path

import geopandas as gpd

from shroom_fm.access import score_access

ERALDIS_PATH = Path("data/eraldis.geojson")
ROADS_PATH = Path("data/roads.geojson")


def main() -> None:
    eraldis_gdf = gpd.read_file(ERALDIS_PATH)
    roads_gdf = gpd.read_file(ROADS_PATH)

    scored = score_access(eraldis_gdf, roads_gdf)
    scored.to_file(ERALDIS_PATH, driver="GeoJSON")

    print(f"{len(scored)} stands scored for access, saved to {ERALDIS_PATH}")


if __name__ == "__main__":
    main()
```

This mirrors `scripts/score_habitat.py` exactly (read, score, overwrite in place, print
count) — no test of its own, same precedent as every other runner script in this project.

- [ ] **Step 7: Commit**

```bash
git add src/shroom_fm/access.py scripts/score_access.py tests/test_access.py
git commit -m "feat: compute per-eraldis AccessScore from classified road distances"
```

---

## Self-Review Notes

- **Spec coverage:** `car_class` mapping incl. `Tänav`/`Ramp või ühendustee` (Task 1),
  barrier-snap exclusion (Task 2), ETAK ingestion/`download_roads.py` (Task 3),
  `AccessScore`/`access_confidence`/`access_reason`/`scripts/score_access.py` (Task 4), and
  the missing-data discipline (`None` → `access_score = 0.0`, tested explicitly in Task 4)
  are all covered.
- **Placeholder scan:** none found — every step has complete, runnable code. The one
  ambiguity caught during spec self-review (where `fetch_layer_bbox` should live) is
  resolved in Task 3: it's a new, self-contained function in `roads.py`, not a refactor of
  `eraldis.py`.
- **Type consistency:** `car_class` string constants defined in Task 1
  (`CAR_CLASS_HIGH_CONFIDENCE` etc.) are the exact names imported in Task 4's `access.py`.
  `nearest_segment`'s return type (`tuple[pd.Series, float] | None`) is used consistently in
  Task 4's tests and implementation. `data/roads.geojson`'s required columns
  (`tyyp_tekst`, `teekate_tekst`, `car_class`) are produced by Task 3's
  `download_roads.py` and consumed by Task 4's `score_access` exactly as named.
- **`access_reason` uses `tyyp_tekst`, not `car_class`, for readability** — e.g. "320m from
  Kõrvalmaantee-class road" rather than "320m from HIGH_CONFIDENCE-class road" — matching
  the example in the spec. `access_confidence` stays the coarser `car_class` enum. This
  means `roads.geojson` must retain `tyyp_tekst` as a column (it does — ETAK returns it
  natively, no extra fetch needed), and `score_eraldis_access` reads both `car_class` and
  `tyyp_tekst` off the matched row.
