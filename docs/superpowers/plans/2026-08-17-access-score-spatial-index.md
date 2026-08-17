# AccessScore Spatial-Index Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix `score_access.py`'s unusable real-scale runtime (killed after hours by the
user) by replacing `access.py`'s brute-force, unindexed per-stand nearest-road loop with
spatial-indexed `geopandas.sjoin_nearest` calls, with byte-for-byte identical output
semantics.

**Architecture:** `access.py`'s `nearest_segment`/`score_eraldis_access` (the per-stand
Python-loop helpers) are removed. A new `_nearest_join` helper wraps one `sjoin_nearest`
call + tie-collapse + row-order-safe reindex; `score_access` calls it 3 times (once per
road-class subset) against the whole `eraldis_gdf` at once, instead of looping per row.
`access_score`/`access_reason` (pure, already tested) are unchanged.

**Tech Stack:** Python, GeoPandas (`sjoin_nearest`, confirmed available and
`shapely.STRtree`-backed in the installed `geopandas==1.1.4`), pandas, pytest. No new
dependencies.

## Global Constraints

- `sjoin_nearest(..., how="left")` needs no special-casing for an empty right-side
  subset — confirmed live it returns `NaN` for every left row with no crash and no dropped
  rows.
- `.groupby(level=0).first()` collapses exact-distance ties to one match per stand, but
  **re-sorts by index** — its output row order does not match the input row order unless
  the input index happens to already be sorted ascending. `.reindex(eraldis_projected.index)`
  immediately after the groupby-collapse is required to restore correct row alignment —
  confirmed live this is a real bug otherwise (verified with a shuffled index producing a
  differently-ordered collapsed result).
- Output column semantics (`nearest_car_road_m`, `nearest_high_confidence_road_m`,
  `nearest_walk_path_m`, `access_score`, `access_confidence`, `access_reason`) must be
  byte-for-byte identical to the current `score_access`'s output — this is a performance
  fix only, not a behavior change. Missing values resolve to `None` (never left as `NaN`,
  never a fabricated small positive number) — matching this project's established
  missing-data discipline.
- `access_score`/`access_reason` (in `access.py`) are unchanged — their existing
  `None`-only contract is preserved; NaN-to-`None` normalization happens before either
  function is ever called, via a new `_none_if_nan` helper.
- `nearest_segment`/`score_eraldis_access` are deleted — nothing else in the codebase calls
  either (this is the only task touching `access.py`).
- `access.py` needs a new `import pandas as pd` (currently only imports `geopandas as gpd`)
  for `pd.isna` in `_none_if_nan`.
- `scripts/score_access.py` is not modified — it already just calls `score_access(...)`.
- Unlike this project's WFS fetch functions, `score_access` takes plain `GeoDataFrame`s (no
  network calls) — it **can and should** be unit-tested directly with small synthetic
  fixtures; the "don't unit-test live-network functions" precedent does not apply here.

---

### Task 1: Spatial-indexed `score_access`

**Files:**
- Modify: `src/shroom_fm/access.py` (whole file — small enough to replace in full)
- Modify: `tests/test_access.py` (whole file — small enough to replace in full)

**Interfaces:**
- Consumes: nothing new from elsewhere in the codebase — `CAR_CLASS_HIGH_CONFIDENCE`/
  `CAR_CLASS_WALK_ONLY` from `shroom_fm.roads` and `ESTONIAN_GRID_CRS` from
  `shroom_fm.eraldis` are already imported today and remain used.
- Produces: `score_access(eraldis_gdf: gpd.GeoDataFrame, roads_gdf: gpd.GeoDataFrame) ->
  gpd.GeoDataFrame` — same public signature as today. `access_score(nearest_car_road_m:
  float | None) -> float` and `access_reason(nearest_car_road_m: float | None, tyyp_tekst:
  str | None) -> str` are unchanged. `nearest_segment`/`score_eraldis_access` no longer
  exist. No other task in this plan depends on this (only task).

- [ ] **Step 1: Write the failing tests**

Replace the full contents of `tests/test_access.py` with:

```python
import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point

from shroom_fm.access import (
    ACCESS_DISTANCE_CAP_M,
    access_reason,
    access_score,
    score_access,
)


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


def test_score_access_computes_all_fields_for_a_single_stand():
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1]},
        geometry=[Point(0, 0)],
        crs="EPSG:3301",
    )
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

    result = score_access(eraldis_gdf, roads_gdf)

    assert result.loc[0, "nearest_car_road_m"] == pytest.approx(100.0)
    assert result.loc[0, "nearest_high_confidence_road_m"] == pytest.approx(100.0)
    assert result.loc[0, "nearest_walk_path_m"] == pytest.approx(50.0)
    assert result.loc[0, "access_confidence"] == "HIGH_CONFIDENCE"
    assert result.loc[0, "access_score"] == pytest.approx(access_score(100.0))
    assert result.loc[0, "access_reason"] == "100m from Kõrvalmaantee-class road"


def test_score_access_handles_no_car_eligible_roads():
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1]},
        geometry=[Point(0, 0)],
        crs="EPSG:3301",
    )
    roads_gdf = gpd.GeoDataFrame(
        {"car_class": [], "tyyp_tekst": []}, geometry=[], crs="EPSG:3301"
    )

    result = score_access(eraldis_gdf, roads_gdf)

    assert result.loc[0, "nearest_car_road_m"] is None
    assert result.loc[0, "nearest_high_confidence_road_m"] is None
    assert result.loc[0, "nearest_walk_path_m"] is None
    assert result.loc[0, "access_score"] == 0.0
    assert result.loc[0, "access_confidence"] is None
    assert (
        result.loc[0, "access_reason"]
        == f"no car-accessible road within {ACCESS_DISTANCE_CAP_M:.0f}m"
    )


def test_score_access_aligns_each_stand_with_its_own_nearest_road():
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1, 2, 3]},
        geometry=[Point(0, 0), Point(1000, 1000), Point(-1000, -1000)],
        crs="EPSG:3301",
        index=[5, 1, 3],
    )
    roads_gdf = gpd.GeoDataFrame(
        {
            "car_class": ["NORMAL", "NORMAL", "NORMAL"],
            "tyyp_tekst": ["Muu tee", "Muu tee", "Muu tee"],
        },
        geometry=[
            LineString([(0, 5), (10, 5)]),
            LineString([(1000, 1015), (1010, 1015)]),
            LineString([(-1000, -975), (-990, -975)]),
        ],
        crs="EPSG:3301",
    )

    result = score_access(eraldis_gdf, roads_gdf)

    assert result.loc[5, "nearest_car_road_m"] == pytest.approx(5.0)
    assert result.loc[1, "nearest_car_road_m"] == pytest.approx(15.0)
    assert result.loc[3, "nearest_car_road_m"] == pytest.approx(25.0)


def test_score_access_resolves_tie_to_exactly_one_match():
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1]},
        geometry=[Point(0, 0)],
        crs="EPSG:3301",
    )
    roads_gdf = gpd.GeoDataFrame(
        {
            "car_class": ["NORMAL", "NORMAL"],
            "tyyp_tekst": ["Muu tee", "Muu tee"],
        },
        geometry=[
            LineString([(5, 0), (5, 10)]),
            LineString([(-5, 0), (-5, 10)]),
        ],
        crs="EPSG:3301",
    )

    result = score_access(eraldis_gdf, roads_gdf)

    assert len(result) == 1
    assert result.loc[0, "nearest_car_road_m"] == pytest.approx(5.0)


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
Expected: The tests that call `score_access` FAIL — either with wrong/slow behavior from the
old implementation, or (once Step 3 removes `nearest_segment`/`score_eraldis_access`) an
`ImportError` if this step is run after Step 3 by mistake. At this point (before Step 3),
the 6 `access_score`/`access_reason` tests should PASS (those functions are unchanged), and
the 5 `score_access`-based tests should FAIL or hang/be slow against the *old*
`score_access` implementation for `test_score_access_aligns_each_stand_with_its_own_nearest_road`
specifically — that test is new and exercises the exact alignment case the old
per-row-loop implementation actually handles correctly today (the old code never had the
`groupby().first()` reordering bug, since it looped one row at a time) — so it's fine if
this one test passes even before Step 3. The point of Step 2 is confirming the test file is
syntactically valid and importable, not that every test fails; proceed to Step 3 regardless.

- [ ] **Step 3: Write the implementation**

Replace the full contents of `src/shroom_fm/access.py` with:

```python
import geopandas as gpd
import pandas as pd

from shroom_fm.eraldis import ESTONIAN_GRID_CRS
from shroom_fm.roads import CAR_CLASS_HIGH_CONFIDENCE, CAR_CLASS_WALK_ONLY

CAR_ELIGIBLE_CLASSES = {"HIGH_CONFIDENCE", "NORMAL", "CONDITIONAL"}
ACCESS_DISTANCE_CAP_M = 1500.0


def access_score(nearest_car_road_m: float | None) -> float:
    if nearest_car_road_m is None:
        return 0.0
    return max(0.0, 1.0 - nearest_car_road_m / ACCESS_DISTANCE_CAP_M)


def access_reason(nearest_car_road_m: float | None, tyyp_tekst: str | None) -> str:
    if nearest_car_road_m is None:
        return f"no car-accessible road within {ACCESS_DISTANCE_CAP_M:.0f}m"
    return f"{nearest_car_road_m:.0f}m from {tyyp_tekst}-class road"


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

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_access.py -v`
Expected: PASS (11 tests: 6 unchanged `access_score`/`access_reason` tests + 1 unchanged
`test_score_access_appends_columns_to_eraldis_gdf` + 4 new `score_access` tests). Confirmed
by direct count: `grep -c "^def test_" tests/test_access.py` before this task showed 12
tests (2 `nearest_segment` + 3 `score_eraldis_access` + 6 `access_score`/`access_reason` +
1 `score_access`); this step's replacement removes the 2 `nearest_segment` and 3
`score_eraldis_access` tests (5 removed) and adds 4 new `score_access` tests, giving
12 - 5 + 4 = 11.

- [ ] **Step 5: Run the full test suite to confirm no regressions**

Run: `uv run pytest tests/ -v`
Expected: PASS (116 tests: 117 existing - 5 removed + 4 added = 116). Confirm this matches
`uv run pytest tests/ --collect-only -q | tail -1` — do not trust this arithmetic blindly,
recompute it from the real before/after collected counts, the way this note itself was
verified before being written into this plan.

- [ ] **Step 6: Manually verify against real data if available**

If `data/eraldis.geojson` and `data/roads.geojson` already exist locally (from a prior real
run), time the fix directly:

```bash
time uv run python scripts/score_access.py
```

Expected: completes in seconds to low minutes, not hours — this is the entire point of the
fix. If these files don't exist in your environment, skip this step and note it in your
report; the unit tests in Step 4/5 are the primary verification for this task.

- [ ] **Step 7: Commit**

```bash
git add src/shroom_fm/access.py tests/test_access.py
git commit -m "perf: replace brute-force AccessScore nearest-road search with sjoin_nearest"
```

---

## Self-Review Notes

- **Spec coverage:** the spec's `_nearest_join`/`_none_if_nan` design, the empty-subset
  no-special-case behavior, the `.reindex()` alignment fix, the byte-for-byte-identical
  output semantics, and the removal of `nearest_segment`/`score_eraldis_access` are all
  covered by this single task.
- **Placeholder scan:** none found — every step has complete, runnable code. Steps 4/5's
  expected test counts (11, 116) were verified by direct count (`grep -c "^def test_"`,
  `pytest --collect-only`) against the real files before being written here, not estimated
  by hand — this project's plans have miscounted expected test totals twice before (the
  road-access and fetch-retry-timeout plans), so this plan checks its own arithmetic
  against the actual repository rather than repeating that mistake a third time.
- **Type consistency:** `score_access(eraldis_gdf: gpd.GeoDataFrame, roads_gdf:
  gpd.GeoDataFrame) -> gpd.GeoDataFrame` is unchanged from the existing public signature,
  used identically by the (unmodified) `scripts/score_access.py`. `_nearest_join`'s
  signature and `_none_if_nan`'s signature are used consistently between their Step 3
  definitions and call sites within the same step.
