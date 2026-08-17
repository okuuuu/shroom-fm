# AccessScore Spatial-Index Optimization — Design

Date: 2026-08-17
Status: Approved

## Purpose

`scripts/score_access.py` is unusably slow at real scale — the user ran it against real
data (~65k `eraldis` stands × ~77k ETAK road segments) and had to kill it after it ran for
hours. This replaces the brute-force nearest-neighbor computation in
`src/shroom_fm/access.py` with a spatial-indexed one, without changing `AccessScore`'s
output semantics (`access_score`/`access_confidence`/`access_reason`/the three
`nearest_*_road_m` columns all mean exactly what they meant before — this is a performance
fix, not a scoring-behavior change).

## Root cause

`score_access` (`access.py`) loops over every `eraldis` row in Python
(`score_eraldis_access(geom, roads_projected) for geom in eraldis_projected.geometry`), and
for *each* row, `score_eraldis_access` re-filters the full `roads_gdf` into 3 subsets
(car-eligible / high-confidence / walk-only) and calls `nearest_segment`, which computes
`roads_gdf.geometry.distance(point_geom)` — an unindexed, brute-force distance calculation
against every row in each subset. At real scale this is roughly `N_eraldis × N_roads × 3`
individual geometry distance computations (tens of billions), with no spatial index
involved at any point — exactly why it takes hours.

## Fix: `geopandas.sjoin_nearest`

Confirmed live against the installed `geopandas==1.1.4`: `sjoin_nearest` is
`shapely.STRtree`-backed and turns this into an indexed nearest-neighbor query. Three
things about its behavior were verified empirically before finalizing this design (not
assumed):

1. **Empty right-side subset** (e.g. no `WALK_ONLY` roads at all) — `sjoin_nearest(...,
   how="left")` returns every left row with `NaN` in the matched columns, no crash and no
   dropped rows. No special-case branch is needed for this.
2. **Exact-distance ties** (multiple road segments equidistant from a stand) — `sjoin_nearest`
   returns one output row per tied match (multiple rows sharing the same left index).
   `.groupby(level=0).first()` collapses this to exactly one match per stand, matching the
   spirit of the current `idxmin()`'s implicit first-occurrence tie-break.
3. **Critical alignment trap**: `.groupby(level=0).first()` re-sorts by index — its output
   row order does **not** match the input `eraldis_projected`'s row order whenever the
   input index isn't already sorted ascending. Verified directly: with a shuffled index
   `[5, 1, 3]`, the collapsed join result comes back ordered `[1, 3, 5]`. Naively assigning
   `.values` from the collapsed result back onto `result[col]` would silently misassign
   distances to the wrong stands. The fix is `.reindex(eraldis_projected.index)`
   immediately after the groupby-collapse, before any further use — verified this produces
   exactly the original row order with correct values.

## `access.py` changes

**Removed:** `nearest_segment`, `score_eraldis_access` — both become dead code once
`score_access` no longer loops per-row; nothing else in the codebase calls either.

**Unchanged:** `access_score`, `access_reason` (pure, already tested, `None`-only contract
kept exactly as-is — NaN from the join is normalized to `None` before these functions ever
see a value, so neither function needs to learn about NaN).

**New:** requires adding `import pandas as pd` to `access.py`'s existing imports (currently
just `import geopandas as gpd`) — needed for `pd.isna` in `_none_if_nan`.

```python
def _nearest_join(
    eraldis_projected: gpd.GeoDataFrame,
    roads_subset: gpd.GeoDataFrame,
    distance_col: str,
    extra_cols: tuple[str, ...] = (),
) -> pd.DataFrame:
    joined = gpd.sjoin_nearest(
        eraldis_projected[["geometry"]],
        roads_subset[["geometry", *extra_cols]],
        how="left",
        distance_col=distance_col,
    )
    return joined.groupby(level=0).first().reindex(eraldis_projected.index)


def _none_if_nan(value):
    return None if pd.isna(value) else value
```

`_nearest_join` is called once per road-class subset (car-eligible, high-confidence,
walk-only) against the *entire* `eraldis_projected` GeoDataFrame at once — 3 vectorized
spatial joins total, replacing the previous `N_eraldis`-iteration Python loop. Each call
selects only the columns it actually needs from the road subset (`geometry` plus, for the
car-eligible join only, `car_class`/`tyyp_tekst` — needed for `access_confidence`/
`access_reason`) rather than carrying all ~30 of ETAK's raw columns through the join.

**Rewritten `score_access`:**

```python
def score_access(
    eraldis_gdf: gpd.GeoDataFrame, roads_gdf: gpd.GeoDataFrame
) -> gpd.GeoDataFrame:
    result = eraldis_gdf.copy()
    eraldis_projected = eraldis_gdf.to_crs(ESTONIAN_GRID_CRS)
    roads_projected = roads_gdf.to_crs(ESTONIAN_GRID_CRS)

    car_roads = roads_projected[roads_projected["car_class"].isin(CAR_ELIGIBLE_CLASSES)]
    hc_roads = roads_projected[roads_projected["car_class"] == CAR_CLASS_HIGH_CONFIDENCE]
    walk_roads = roads_projected[roads_projected["car_class"] == CAR_CLASS_WALK_ONLY]

    car_joined = _nearest_join(
        eraldis_projected,
        car_roads,
        "nearest_car_road_m",
        extra_cols=("car_class", "tyyp_tekst"),
    )
    hc_joined = _nearest_join(eraldis_projected, hc_roads, "nearest_high_confidence_road_m")
    walk_joined = _nearest_join(eraldis_projected, walk_roads, "nearest_walk_path_m")

    nearest_car_road_m = [_none_if_nan(v) for v in car_joined["nearest_car_road_m"]]
    access_confidence = [_none_if_nan(v) for v in car_joined["car_class"]]
    nearest_car_tyyp_tekst = [_none_if_nan(v) for v in car_joined["tyyp_tekst"]]

    result["nearest_car_road_m"] = nearest_car_road_m
    result["nearest_high_confidence_road_m"] = [
        _none_if_nan(v) for v in hc_joined["nearest_high_confidence_road_m"]
    ]
    result["nearest_walk_path_m"] = [
        _none_if_nan(v) for v in walk_joined["nearest_walk_path_m"]
    ]
    result["access_confidence"] = access_confidence
    result["access_score"] = [access_score(v) for v in nearest_car_road_m]
    result["access_reason"] = [
        access_reason(d, t) for d, t in zip(nearest_car_road_m, nearest_car_tyyp_tekst)
    ]

    return result
```

Because `_nearest_join` already `.reindex()`s to `eraldis_projected.index` internally, every
`for v in car_joined[...]`/`hc_joined[...]`/`walk_joined[...]` iteration below it is
guaranteed to walk rows in exactly `result`'s row order — no further alignment bookkeeping
needed at the call site.

Output columns, their meaning, and their `None`-for-missing semantics are byte-for-byte
identical to the current `score_access`'s output — this is purely an internal algorithm
change.

## Testing

Existing tests in `tests/test_access.py` that exercise `access_score`/`access_reason`
directly are unaffected (those functions are unchanged). Tests for the removed
`nearest_segment`/`score_eraldis_access` are removed along with the functions themselves.

New/updated tests for `score_access` (using small, real-geometry `GeoDataFrame` fixtures,
matching this project's established no-mocking test style) cover:
- A stand with a nearby car-eligible road, high-confidence road, and walk-only road all
  present — all three `nearest_*_road_m` values and `access_confidence`/`access_reason`
  come back correct.
- A stand with **no** car-eligible roads in the input at all (empty subset after filtering)
  — `nearest_car_road_m`/`access_confidence`/`access_reason` all resolve to their
  correct "missing" values (`None`/`None`/the "no car-accessible road" message), not `NaN`
  and not a crash.
- **Multiple stands in one call**, each with a different nearest road, to prove the
  `.reindex()`-based alignment fix actually works end-to-end (not just for a single-row
  case, which could accidentally pass even with a broken alignment) — this is the test that
  would have caught the alignment trap described above if it existed.
- A tie case (two equidistant roads) resolving to exactly one match, not two rows or a
  crash.

`score_access`'s live-network-adjacent characteristics (it takes real `GeoDataFrame`s, not
network calls) mean it — unlike the WFS fetch functions — **can and should** be tested
directly with small synthetic `GeoDataFrame`s; this isn't a live-network function, so this
project's "don't unit-test live-network functions" precedent doesn't apply here.

## Out of scope

- No change to `roads.py`, `eraldis.py`, or any WFS fetch function — this is scoped
  entirely to `access.py`'s internal computation.
- No change to `ACCESS_DISTANCE_CAP_M`, `CAR_ELIGIBLE_CLASSES`, or any other constant.
- No change to `scripts/score_access.py` itself — it already just calls `score_access(...)`
  and doesn't need to know how the internals changed.
- No parallelization/chunking — a single set of 3 vectorized `sjoin_nearest` calls should
  already be fast enough at real scale (STRtree-indexed nearest-neighbor queries are
  typically `O(N log M)`, not `O(N × M)`); revisit only if this specific fix still proves
  too slow in practice.
