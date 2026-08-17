# Eraldis CQL Annulus Pushdown Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `download_eraldis.py`'s fetch-full-disc-then-post-filter approach with a
single WFS query that pushes the entire `inner_radius_km`-to-`radius_km` annulus filter
server-side via GeoServer `CQL_FILTER`'s `DWITHIN`/`BEYOND` spatial predicates.

**Architecture:** `eraldis.py` gains `fetch_eraldis_annulus(lat, lon, radius_km,
inner_radius_km=0.0)`, built on two new pure helper functions (`_cql_point`,
`_build_cql_filter`) and the already-merged `get_with_retry` from `retry.py`. It replaces
`fetch_eraldis_bbox` entirely (deleted, along with the constants that existed only to
support it). `download_eraldis.py` is simplified to call the new function directly — no more
`wfs`, `bbox`, or post-fetch `filter_within_radius` call.

**Tech Stack:** Python, GeoPandas, `requests` (via `get_with_retry`), pytest — same as the
rest of the project. No new dependencies.

## Global Constraints

- The real geometry attribute for `metsaregister:eraldis` is `shape`, not `geometry`
  (confirmed live via `DescribeFeatureType`) — CQL filter expressions must reference `shape`.
- CQL `POINT()` literals must be in the layer's native CRS (`EPSG:3301`) with **northing
  first** (`POINT(y x)`, i.e. `POINT(northing easting)`) — confirmed live; the reverse of
  the natural x,y convention.
- Distance values passed to `DWITHIN`/`BEYOND` must be raw meters (`radius_km * 1000`) — the
  `"kilometers"`/`"meters"` unit keyword is not honored by this server, confirmed live.
- `srsName=EPSG:4326` works with this CQL-filtered request (confirmed live) — the response
  already comes back in WGS84, no reprojection needed.
- Skip the `BEYOND` clause entirely when `inner_radius_km == 0.0` (no degenerate
  `BEYOND(..., 0, ...)` clause) — matching `filter_within_radius`'s existing no-op-at-zero
  behavior.
- Same `ValueError` validation as `filter_within_radius`: raise if `inner_radius_km >=
  radius_km`, with a message naming both values.
- `fetch_eraldis_bbox` is deleted (nothing else in the codebase calls it — confirmed via
  `grep -rn "fetch_eraldis_bbox" src/ scripts/ tests/`), along with the `WGS84_URN` constant
  (used only inside it) and the `from owslib.wfs import WebFeatureService` import (used only
  as that function's type hint).
- `compute_bbox`, `filter_within_radius`, `ESTONIAN_GRID_CRS`, `WGS84_CRS`,
  `ERALDIS_TYPENAME`, and `PAGE_SIZE` are all unchanged — `roads.py`'s `download_roads.py`
  still depends on `compute_bbox`/`filter_within_radius`, and `PAGE_SIZE` is reused by the
  new function's pagination.
- The new annulus is technically `(inner_radius_km, radius_km]` (OGC `BEYOND` is a strict
  `>`, unlike `filter_within_radius`'s inclusive `>=`) — a known, accepted, practically
  irrelevant difference. Not engineered around.

---

### Task 1: `fetch_eraldis_annulus` and CQL helpers

**Files:**
- Modify: `src/shroom_fm/eraldis.py` (whole file — small enough to replace in full)
- Modify: `scripts/download_eraldis.py` (whole file — small enough to replace in full)
- Test: `tests/test_eraldis.py` (append new tests; existing tests must keep passing
  unmodified)

**Interfaces:**
- Consumes: `get_with_retry` from `src/shroom_fm/retry.py` (already merged — signature:
  `get_with_retry(url, *, max_attempts=3, backoff_seconds=(1.0, 2.0), sleep=time.sleep,
  **kwargs)`, calls `requests.get(url, **kwargs)` then `response.raise_for_status()` inside
  the retried callable, returns the `Response`). `METSAREGISTER_OWS_URL` from
  `src/shroom_fm/wfs.py` (already exists).
- Produces: `fetch_eraldis_annulus(lat: float, lon: float, radius_km: float,
  inner_radius_km: float = 0.0) -> gpd.GeoDataFrame` in `src/shroom_fm/eraldis.py`. No other
  task in this plan depends on it (this is the only task).

- [ ] **Step 1: Write the failing tests**

At the top of `tests/test_eraldis.py`, replace the existing import line:

```python
from shroom_fm.eraldis import compute_bbox, filter_within_radius
```

with:

```python
from shroom_fm.eraldis import (
    _build_cql_filter,
    _cql_point,
    compute_bbox,
    fetch_eraldis_annulus,
    filter_within_radius,
)
```

Then append to the end of the file:

```python
def test_cql_point_returns_northing_first_estonian_grid_point():
    lat, lon = 59.451081455185864, 24.818002100965362

    result = _cql_point(lat, lon)

    assert result == "POINT(6590647.722702539 546398.5907798207)"


def test_build_cql_filter_omits_beyond_clause_when_inner_radius_is_zero():
    lat, lon = 59.451081455185864, 24.818002100965362

    result = _build_cql_filter(lat, lon, radius_km=20.0, inner_radius_km=0.0)

    assert result == (
        "DWITHIN(shape, POINT(6590647.722702539 546398.5907798207), 20000.0, meters)"
    )
    assert "BEYOND" not in result


def test_build_cql_filter_includes_beyond_clause_when_inner_radius_is_positive():
    lat, lon = 59.451081455185864, 24.818002100965362

    result = _build_cql_filter(lat, lon, radius_km=20.0, inner_radius_km=5.0)

    assert result == (
        "DWITHIN(shape, POINT(6590647.722702539 546398.5907798207), 20000.0, meters) "
        "AND BEYOND(shape, POINT(6590647.722702539 546398.5907798207), 5000.0, meters)"
    )


def test_fetch_eraldis_annulus_raises_when_inner_radius_not_less_than_outer():
    with pytest.raises(ValueError):
        fetch_eraldis_annulus(59.4370, 24.7536, radius_km=20.0, inner_radius_km=20.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_eraldis.py -v`
Expected: FAIL — the 4 new tests fail with `ImportError: cannot import name '_cql_point'`
(or similar, for whichever of `_cql_point`/`_build_cql_filter`/`fetch_eraldis_annulus` the
import line hits first — none exist yet).

- [ ] **Step 3: Write the implementation**

Replace the full contents of `src/shroom_fm/eraldis.py` with:

```python
import io
import math

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

from shroom_fm.retry import get_with_retry
from shroom_fm.wfs import METSAREGISTER_OWS_URL

KM_PER_DEGREE_LAT = 111.32
BBOX_PADDING_FACTOR = 1.1
ESTONIAN_GRID_CRS = "EPSG:3301"
WGS84_CRS = "EPSG:4326"
ERALDIS_TYPENAME = "metsaregister:eraldis"
GEOMETRY_ATTR = "shape"
PAGE_SIZE = 1000


def compute_bbox(
    lat: float, lon: float, radius_km: float
) -> tuple[float, float, float, float]:
    padded_radius_km = radius_km * BBOX_PADDING_FACTOR
    delta_lat = padded_radius_km / KM_PER_DEGREE_LAT
    delta_lon = padded_radius_km / (KM_PER_DEGREE_LAT * math.cos(math.radians(lat)))
    return (lon - delta_lon, lat - delta_lat, lon + delta_lon, lat + delta_lat)


def filter_within_radius(
    gdf: gpd.GeoDataFrame,
    lat: float,
    lon: float,
    radius_km: float,
    inner_radius_km: float = 0.0,
) -> gpd.GeoDataFrame:
    if inner_radius_km >= radius_km:
        raise ValueError(
            f"inner_radius_km ({inner_radius_km}) must be less than radius_km ({radius_km})"
        )
    projected = gdf.to_crs(ESTONIAN_GRID_CRS)
    home_point = (
        gpd.GeoSeries([Point(lon, lat)], crs=WGS84_CRS)
        .to_crs(ESTONIAN_GRID_CRS)
        .iloc[0]
    )
    distances_km = projected.geometry.distance(home_point) / 1000.0
    return gdf[(distances_km >= inner_radius_km) & (distances_km <= radius_km)]


def _cql_point(lat: float, lon: float) -> str:
    projected = (
        gpd.GeoSeries([Point(lon, lat)], crs=WGS84_CRS)
        .to_crs(ESTONIAN_GRID_CRS)
        .iloc[0]
    )
    return f"POINT({projected.y} {projected.x})"


def _build_cql_filter(
    lat: float, lon: float, radius_km: float, inner_radius_km: float
) -> str:
    point = _cql_point(lat, lon)
    clause = f"DWITHIN({GEOMETRY_ATTR}, {point}, {radius_km * 1000}, meters)"
    if inner_radius_km > 0:
        clause += (
            f" AND BEYOND({GEOMETRY_ATTR}, {point}, {inner_radius_km * 1000}, meters)"
        )
    return clause


def fetch_eraldis_annulus(
    lat: float,
    lon: float,
    radius_km: float,
    inner_radius_km: float = 0.0,
) -> gpd.GeoDataFrame:
    if inner_radius_km >= radius_km:
        raise ValueError(
            f"inner_radius_km ({inner_radius_km}) must be less than radius_km ({radius_km})"
        )
    cql_filter = _build_cql_filter(lat, lon, radius_km, inner_radius_km)
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

This removes `fetch_eraldis_bbox`, the `WGS84_URN` constant, and the `from owslib.wfs import
WebFeatureService` import entirely — none of the three have any remaining use once this
replacement lands. `compute_bbox` and `filter_within_radius` are byte-for-byte unchanged
from the current file.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_eraldis.py -v`
Expected: PASS (8 tests: 4 existing — `test_compute_bbox_returns_padded_box_around_point`,
`test_filter_within_radius_keeps_only_points_inside_cutoff`,
`test_filter_within_radius_excludes_points_inside_inner_cutoff`,
`test_filter_within_radius_raises_when_inner_radius_not_less_than_outer` — plus the 4 new
ones. The 4 existing tests must pass completely unmodified, proving `compute_bbox`/
`filter_within_radius` are untouched.)

- [ ] **Step 5: Run the full test suite to confirm no regressions**

Run: `uv run pytest tests/ -v`
Expected: PASS (117 tests: 113 existing + 4 new)

- [ ] **Step 6: Update the runner script**

Replace the full contents of `scripts/download_eraldis.py` with:

```python
from pathlib import Path

from shroom_fm.config import load_home_location
from shroom_fm.eraldis import fetch_eraldis_annulus

RADIUS_KM = 38.0
INNER_RADIUS_KM = 18.0
OUTPUT_PATH = Path("data/eraldis.geojson")


def main() -> None:
    home_lat, home_lon = load_home_location()
    nearby = fetch_eraldis_annulus(home_lat, home_lon, RADIUS_KM, INNER_RADIUS_KM)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    nearby.to_file(OUTPUT_PATH, driver="GeoJSON")

    if INNER_RADIUS_KM > 0:
        print(f"{len(nearby)} stands within {INNER_RADIUS_KM:.0f}-{RADIUS_KM:.0f}km of home")
    else:
        print(f"{len(nearby)} stands within {RADIUS_KM:.0f}km of home")
    print(f"Saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
```

`RADIUS_KM`/`INNER_RADIUS_KM` keep their currently active values (`38.0`/`18.0`) — this is a
mechanism change, not a config change. The `if INNER_RADIUS_KM > 0: ... else: ...` print
logic is unchanged from the current script.

- [ ] **Step 7: Manually verify the new code**

Re-read `src/shroom_fm/eraldis.py`'s new `fetch_eraldis_annulus`/`_cql_point`/
`_build_cql_filter` once more, checking specifically:
- `_cql_point` returns `projected.y` (northing) before `projected.x` (easting) — the
  reversed order is easy to get backwards by habit.
- `fetch_eraldis_annulus`'s pagination loop matches the existing `fetch_layer_bbox` shape in
  `roads.py` (same `startIndex`/`count`/break-on-short-page structure), using
  `get_with_retry` rather than a bare `requests.get`.
- No leftover reference to `fetch_eraldis_bbox`, `WGS84_URN`, or `WebFeatureService`
  anywhere in `eraldis.py` (run `grep -n "fetch_eraldis_bbox\|WGS84_URN\|WebFeatureService"
  src/shroom_fm/eraldis.py` — expect zero matches).

If live network access is available, additionally run:

```bash
uv run python scripts/download_eraldis.py
```

Expected: prints a "stands within ...km of home" line with a nonzero count and a "Saved to"
line, and `data/eraldis.geojson` is written. If the count differs substantially from a
previous run with the same `RADIUS_KM`/`INNER_RADIUS_KM`, investigate before trusting the
new fetch path — it should reproduce the same real-world stand set as the old bbox-based
fetch, not a different one.

- [ ] **Step 8: Commit**

```bash
git add src/shroom_fm/eraldis.py scripts/download_eraldis.py tests/test_eraldis.py
git commit -m "feat: push eraldis annulus filter server-side via CQL_FILTER DWITHIN/BEYOND"
```

---

## Self-Review Notes

- **Spec coverage:** the spec's `GEOMETRY_ATTR`/`_cql_point`/`_build_cql_filter`/
  `fetch_eraldis_annulus` design, the `BEYOND`-clause-omitted-at-zero behavior, the
  `ValueError` validation, the `srsName=EPSG:4326` no-reprojection-needed detail, the
  deletion of `fetch_eraldis_bbox`/`WGS84_URN`/the `owslib` import, and the simplified
  `download_eraldis.py` are all covered by this single task.
- **Placeholder scan:** none found — every step has complete, runnable code, and the two
  `grep`-based verification claims in the Global Constraints section were independently
  confirmed against the real repository before writing this plan (not assumed).
- **Type consistency:** `fetch_eraldis_annulus(lat: float, lon: float, radius_km: float,
  inner_radius_km: float = 0.0) -> gpd.GeoDataFrame` is identical across the spec, this
  plan's Step 1 tests, Step 3's implementation, and Step 6's script call site (positional
  `home_lat, home_lon, RADIUS_KM, INNER_RADIUS_KM`, matching parameter order exactly).
  `_cql_point(lat: float, lon: float) -> str` and `_build_cql_filter(lat: float, lon: float,
  radius_km: float, inner_radius_km: float) -> str` signatures match between their Step 3
  definitions and Step 1's test calls.
