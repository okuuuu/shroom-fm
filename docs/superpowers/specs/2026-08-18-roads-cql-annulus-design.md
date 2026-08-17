# Roads CQL Annulus Pushdown — Design

Date: 2026-08-18
Status: Approved

## Purpose

Extends the CQL_FILTER-based annulus pushdown already built for Metsaregister's `eraldis`
fetch (`fetch_eraldis_annulus`) to ETAK's road/barrier fetch in `roads.py`/
`download_roads.py`, which still uses the older fetch-full-disc-then-post-filter approach.
This was explicitly deferred when the eraldis CQL work was scoped, because ETAK's WFS had
never been verified to support `CQL_FILTER`'s `DWITHIN`/`BEYOND` spatial predicates, and
ETAK is already known to have its own quirks distinct from Metsaregister's WFS (confirmed
`srsName=EPSG:3301`-only output, strict axis order for the `bbox` parameter). A real run
against the current `18`–`38`km home config took **12m20s**, almost entirely network wait
on paginating through the full 0–38km disc only to discard everything inside 18km.

## Live-verified facts this design depends on

All confirmed live against ETAK's real WFS this session (2026-08-18):

- `etak:e_501_tee_j` and `etak:e_505_liikluskorralduslik_rajatis_j` (barriers) both use
  **`shape`** as their real geometry attribute name (confirmed via `DescribeFeatureType`) —
  the same name Metsaregister's `eraldis` layer uses.
- `CQL_FILTER=DWITHIN(shape, POINT(...), N, meters)` against `e_501_tee_j`, using the exact
  same pattern as Metsaregister (native `EPSG:3301`, **northing-first** `POINT(y x)`,
  `srsName=EPSG:3301`), reproduces the current bbox-fetch-then-post-filter count exactly:
  **15384** road segments for a 5km radius around home.
- The combined `DWITHIN(...) AND BEYOND(...)` annulus filter also reproduces the current
  approach exactly: **11541** segments for a 2–5km annulus.
- The unit keyword is not honored here either — `"kilometers"` and `"meters"` with the same
  numeric value both return 15384 matches, confirming distances must be raw meters, same as
  Metsaregister.
- The barrier layer also supports the same `CQL_FILTER` pattern: 149 matches for a 5km
  `DWITHIN` query.
- **In short: ETAK's WFS behaves identically to Metsaregister's for CQL purposes** — same
  geometry attribute name, same native CRS, same axis order, same ignored-unit-keyword
  behavior. No new quirks were found; this is a clean extension, not a rediscovery.

## Shared CQL helper: `src/shroom_fm/cql.py` (new module)

The CQL point/filter-building logic is now proven byte-for-byte identical between what
`eraldis.py` already does and what `roads.py` needs — unlike `fetch_layer_bbox`'s
pagination loop (deliberately duplicated earlier because the *transport* mechanism
genuinely differed between `owslib` and raw `requests`), this is pure geometry/string
logic with no such justification for duplicating it a second time.

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

`annulus_filter` is generalized over `geometry_attr` (not hardcoded to `"shape"`) — both
current layers happen to use that name, but this keeps the module honest about what's
actually been verified rather than baking in an assumption that every future layer will
match.

The `inner_radius_km >= radius_km` validation lives here — in the one function both
`fetch_eraldis_annulus` and the new `fetch_layer_annulus` call before doing anything else
— rather than being duplicated at each call site. Both fetch functions now get the check
"for free" simply by calling `annulus_filter`; neither needs its own copy of the same
`if`/`raise`.

`cql.py` defines its own `ESTONIAN_GRID_CRS`/`WGS84_CRS` rather than importing them from
`eraldis.py` — `eraldis.py`'s `fetch_eraldis_annulus` needs to call into `cql.py`, so
`cql.py` importing back from `eraldis.py` would be circular. This duplicates two constant
*values* (not logic), matching the existing tolerance for this kind of minor duplication
already present between `eraldis.py` and `roads.py` (`_PAGE_SIZE`, `_WGS84_URN` are
similarly duplicated today).

## `eraldis.py` changes

`fetch_eraldis_annulus` now calls `cql.annulus_filter(GEOMETRY_ATTR, lat, lon, radius_km,
inner_radius_km)` instead of its own private `_cql_point`/`_build_cql_filter` — both
deleted, along with `fetch_eraldis_annulus`'s own now-redundant `inner_radius_km >=
radius_km` check (that validation now lives inside `annulus_filter` itself, see above —
`fetch_eraldis_annulus` gets it automatically as the first thing `annulus_filter` does).
Also deleted: `compute_bbox`, `filter_within_radius`, `KM_PER_DEGREE_LAT`,
`BBOX_PADDING_FACTOR` — confirmed via `grep -rn "compute_bbox\|filter_within_radius" src/
scripts/ tests/` that `download_roads.py` was their only remaining production caller; once
it migrates to `fetch_layer_annulus` (below), nothing else uses them. The `math` and
`shapely.geometry.Point` imports drop out as a consequence (no longer used by anything left
in the file). `ESTONIAN_GRID_CRS`, `WGS84_CRS`, `ERALDIS_TYPENAME`, `GEOMETRY_ATTR`,
`PAGE_SIZE` are unchanged — still exported and used elsewhere (e.g. `access.py`/`scout.py`
import `ESTONIAN_GRID_CRS` from `eraldis.py`; that must keep working).

## `roads.py` changes

New `fetch_layer_annulus(url: str, typename: str, lat: float, lon: float, radius_km:
float, inner_radius_km: float = 0.0) -> gpd.GeoDataFrame`, mirroring
`fetch_eraldis_annulus`'s shape (same paging loop using `get_with_retry`; the
`inner_radius_km >= radius_km` validation is inherited automatically from calling
`cql.annulus_filter`, not re-implemented here) but generalized over `typename` so it's
called once for roads and once for barriers. Uses a new `GEOMETRY_ATTR = "shape"` constant
(module-local to `roads.py`,
not shared — each layer's geometry attribute name is a property of that layer, even though
both currently happen to be `"shape"`) and the existing `_ETAK_OUTPUT_CRS = "EPSG:3301"`.
`fetch_layer_bbox` is deleted — nothing calls it once `download_roads.py` migrates. Its
now-obsolete comment about `owslib`'s bbox-parameter axis-reserialization bug goes with it
(that bug was specific to the `bbox` GetFeature parameter, not `CQL_FILTER`, so it no
longer applies).

## `download_roads.py` changes

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

No more `compute_bbox`/`filter_within_radius`/`ESTONIAN_GRID_CRS` imports, and no explicit
`.to_crs(ESTONIAN_GRID_CRS)` step before `exclude_barrier_blocked_segments` — since CQL
forces `srsName=EPSG:3301` output for both fetches, `roads`/`barriers` already arrive in
that CRS, so `exclude_barrier_blocked_segments` (which needs a projected/metric CRS for its
distance check) can operate on them directly. Only the final `.to_crs(WGS84_CRS)` before
saving remains, matching this project's established convention of always saving GeoJSON in
WGS84. `RADIUS_KM`/`INNER_RADIUS_KM` keep their currently active real values (`38.0`/
`18.0`, already synced to match `download_eraldis.py`).

## Testing

- New `tests/test_cql.py`: `estonian_grid_point` (northing-first output for known real
  coordinates, same values already used in `eraldis.py`'s existing tests), `annulus_filter`
  (omits `BEYOND` at `inner_radius_km == 0`, includes it when positive, generalized
  correctly over a passed-in `geometry_attr`), and `annulus_filter`'s `ValueError` on
  `inner_radius_km >= radius_km` (the validation now lives here, so this is where it gets
  its primary direct test) — moved and adapted from `eraldis.py`'s existing `_cql_point`/
  `_build_cql_filter` tests (currently in `tests/test_eraldis.py`), since those private
  functions no longer exist in `eraldis.py`.
- `tests/test_eraldis.py`: the 4 `compute_bbox`/`filter_within_radius` tests and the 3
  `_cql_point`/`_build_cql_filter` tests are removed (moved to `test_cql.py` where
  applicable). `test_fetch_eraldis_annulus_raises_when_inner_radius_not_less_than_outer`
  remains as an integration-level check that the validation still propagates correctly
  through `fetch_eraldis_annulus` even though it no longer implements the check itself.
- `tests/test_roads.py`: one new integration-level test confirming `fetch_layer_annulus`
  raises `ValueError` on `inner_radius_km >= radius_km` (via `annulus_filter`), mirroring
  `eraldis.py`'s equivalent test. `classify_car_class`/`exclude_barrier_blocked_segments`
  tests are untouched.
- `fetch_layer_annulus`/`fetch_eraldis_annulus` themselves remain untested beyond their
  `ValueError` cases — same established precedent as every other live-network fetch
  function in this project.

## Out of scope

- No change to `access.py`, `scout.py`, `habitat.py`, or any downstream scoring code — this
  is purely a fetch-mechanism change; `data/roads.geojson`'s schema and content semantics
  are unchanged (same columns, same `car_class` values, same barrier exclusion behavior).
- No generalization beyond the two currently-known-CQL-compatible WFS servers
  (Metsaregister, ETAK) — if a future data source doesn't support `CQL_FILTER`, it needs its
  own live verification and likely its own bbox-based fetch path, not an assumption that
  `cql.py` will work everywhere.
