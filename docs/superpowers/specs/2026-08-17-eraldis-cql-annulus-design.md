# Eraldis CQL Annulus Pushdown — Design

Date: 2026-08-17
Status: Approved

## Purpose

Second sub-project of the WFS efficiency work following this session's live findings (first
sub-project, fetch retry/timeout infrastructure, already merged). `download_eraldis.py`
currently fetches the full outer-radius disc from Metsaregister's WFS via a bbox query, then
discards everything inside `inner_radius_km` client-side (`filter_within_radius`). When
`inner_radius_km` is large relative to `radius_km` (e.g. the currently active local config,
`RADIUS_KM=38`/`INNER_RADIUS_KM=18` — roughly 78% of the fetched disc's *area* gets thrown
away), this wastes bandwidth, pagination requests, and server-side work on stands that were
never going to be kept. This pushes the entire annulus filter (`inner_radius_km` to
`radius_km`) into the WFS query itself via GeoServer's `CQL_FILTER` spatial predicates, so
the response already *is* the final result.

## Live-verified facts this design depends on

All confirmed live against Metsaregister's real WFS this session (2026-08-17), not assumed:

- The `metsaregister:eraldis` layer's real geometry attribute name is **`shape`**, not
  `geometry` (confirmed via `DescribeFeatureType`; GeoPandas normalizes it to `geometry`
  after loading, which is why no code has needed to know the real name until now — CQL
  filter expressions reference the raw attribute name directly, so this one does).
- The layer's native storage CRS is **EPSG:3301** (Estonian National Grid), and CQL `POINT()`
  literals must be given in that CRS with **northing-first (y, x) axis order** — the
  reverse of the natural x,y convention, matching the same "authority axis order" pattern
  found in the ETAK work, just surfacing through a different WFS parameter (`CQL_FILTER`'s
  geometry literal, not `bbox`).
- The **unit keyword is not honored** — `DWITHIN`/`BEYOND`'s distance argument is always
  interpreted in the layer's native CRS unit (meters), regardless of whether `"kilometers"`
  or `"meters"` is passed as the unit string. Distances must be computed as `radius_km *
  1000` before being embedded in the filter.
- `DWITHIN(shape, POINT(...), radius_km * 1000, meters)` alone reproduces the current
  disc-fetch result exactly: **14171** stands for a 20km radius around home, identical to
  the existing bbox-fetch-then-post-filter count.
- `DWITHIN(...) AND BEYOND(shape, POINT(...), inner_radius_km * 1000, meters)` reproduces
  the current annulus-fetch result exactly: **13529** stands for a 5–20km annulus, identical
  to `filter_within_radius`'s current output.
- `srsName=EPSG:4326` works with this CQL-filtered request (confirmed by re-running the
  13529-match query with it) — real lon/lat coordinates come back directly, unlike ETAK's
  WFS, which rejected `EPSG:4326` output entirely for `e_501_tee_j`. No reprojection step is
  needed after fetching.

## `eraldis.py` changes

**New:**

```python
GEOMETRY_ATTR = "shape"


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
        clause += f" AND BEYOND({GEOMETRY_ATTR}, {point}, {inner_radius_km * 1000}, meters)"
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

This requires `from shroom_fm.retry import get_with_retry` and `from shroom_fm.wfs import
METSAREGISTER_OWS_URL` as new imports in `eraldis.py` (the latter creates a dependency from
`eraldis.py` on `wfs.py`, which is fine — `wfs.py` has no dependency back on `eraldis.py`, no
cycle). `from owslib.wfs import WebFeatureService` is used only as `fetch_eraldis_bbox`'s
type hint today (confirmed: `grep -n "WebFeatureService" src/shroom_fm/eraldis.py` matches
only the import line and that one type hint) — once `fetch_eraldis_bbox` is removed, **this
import becomes unused and must be removed** too.

**Removed:** `fetch_eraldis_bbox`, and the module-level constants `WGS84_URN` and
`PAGE_SIZE`'s *sole other use* — confirmed via `grep -rn "WGS84_URN\|PAGE_SIZE\b" src/
scripts/ tests/` that both are used only inside `fetch_eraldis_bbox` today. `PAGE_SIZE`
itself is kept (reused by `fetch_eraldis_annulus`'s pagination, same value, same purpose);
only `WGS84_URN` is fully removed as dead code (it was solely for `fetch_eraldis_bbox`'s
`bbox` tuple's trailing CRS-URN component, which no longer exists — the new function has no
`bbox` parameter at all).

**Unchanged:** `compute_bbox`, `filter_within_radius`, `ESTONIAN_GRID_CRS`, `WGS84_CRS`,
`ERALDIS_TYPENAME`. Both `compute_bbox` and `filter_within_radius` remain in active use —
`roads.py`'s `download_roads.py` still needs them for ETAK's bbox-based fetch, which is out
of scope for this change.

## `scripts/download_eraldis.py` changes

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

No more `wfs`, `bbox`, or post-fetch `filter_within_radius` call — the fetch already returns
exactly the annulus. `RADIUS_KM`/`INNER_RADIUS_KM`/`OUTPUT_PATH` constants and the print
logic are unchanged from today's script (preserving the user's currently active local
values, `38.0`/`18.0`).

## Known, accepted boundary-inclusivity difference

`filter_within_radius`'s current semantics are both-bounds-inclusive
(`inner_radius_km <= distance_km <= radius_km`, via `>=`/`<=`). OGC filter semantics define
`DWITHIN` as inclusive (`distance <= d`) but `BEYOND` as its strict complement
(`distance > d`) — so the new annulus is technically the half-open interval
`(inner_radius_km, radius_km]` rather than the fully-closed interval the old code
guaranteed. A real stand polygon's nearest point landing at *exactly* the inner-radius
distance to the meter is practically impossible, so this is accepted as a negligible,
real-world-irrelevant difference rather than engineered around (e.g. by subtracting an
epsilon from the inner radius) — doing so would trade a theoretical edge case for a real,
if tiny, distortion of the actual boundary.

## Testing

`_cql_point` and `_build_cql_filter` are pure functions — fully unit-tested:
- `_cql_point` returns the correct `POINT(northing easting)` string for a known
  lat/lon (verified against the same real home coordinates used in this session's live CQL
  testing, so the expected projected values are already known).
- `_build_cql_filter` includes only `DWITHIN` when `inner_radius_km == 0.0` (no `BEYOND`
  clause at all — not `BEYOND(..., 0, ...)`).
- `_build_cql_filter` includes both `DWITHIN AND BEYOND` when `inner_radius_km > 0`, with
  the exact expected distance values (`radius_km * 1000`, `inner_radius_km * 1000`).
- `fetch_eraldis_annulus` raises `ValueError` when `inner_radius_km >= radius_km`, matching
  `filter_within_radius`'s existing validation message format.

`fetch_eraldis_annulus` itself (the live-network paging function) gets no dedicated test —
same established precedent as `fetch_eraldis_bbox` before it and `fetch_layer_bbox` in
`roads.py` (network-touching functions are verified by live runs, not unit tests).

## Out of scope

- No change to `roads.py`/`download_roads.py` — ETAK's WFS was never verified to support
  `CQL_FILTER`'s `DWITHIN`/`BEYOND` on its layers (and it uses a different geometry attribute
  and CRS setup than Metsaregister), so extending this approach there would need its own
  live verification pass, not assumed to work identically.
- No change to `compute_bbox` or `filter_within_radius` — both remain as-is, still used by
  `roads.py`.
- No generalization into a reusable "CQL annulus fetch for any layer" helper — this is
  eraldis-specific, matching this project's existing pattern of not sharing fetch logic
  across layers with different attribute names/CRS quirks until a second concrete need
  arises (see `roads.py`'s `fetch_layer_bbox`, which deliberately duplicates rather than
  reuses `eraldis.py`'s pagination loop for the same reason).
