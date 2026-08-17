# Inner-Radius (Annulus) Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `download_eraldis` discard a near-home disc (city parks/infrastructure forest)
and keep only the ring further out, by adding an optional `inner_radius_km` parameter to
`filter_within_radius`.

**Architecture:** `filter_within_radius` (in `src/shroom_fm/eraldis.py`) gains one new
optional parameter and changes its filter from `distance <= radius_km` to
`inner_radius_km <= distance <= radius_km`. `scripts/download_eraldis.py` gains one new
script-level constant and passes it through. No other files change.

**Tech Stack:** Python, GeoPandas, pytest — same as the rest of the project. No new
dependencies.

## Global Constraints

- `inner_radius_km` defaults to `0.0` — this must preserve current disc behavior exactly for any caller that doesn't pass it (including the existing, unmodified test `test_filter_within_radius_keeps_only_points_inside_cutoff`).
- Both bounds inclusive: `inner_radius_km <= distance_km <= radius_km` (matching the existing outer-bound `<=` semantics already in `filter_within_radius`).
- Raise `ValueError` if `inner_radius_km >= radius_km`, with a message naming both values.
- `compute_bbox` is NOT modified — it continues to take only `radius_km` (the outer radius).
- `INNER_RADIUS_KM` is a script-level constant in `scripts/download_eraldis.py`, matching the existing `RADIUS_KM` pattern — not moved into `config.toml`.
- No new dependencies, no network-call changes (this task doesn't touch how the WFS is queried, only which of the already-fetched stands get kept).

---

### Task 1: Inner-radius filtering

**Files:**
- Modify: `src/shroom_fm/eraldis.py:27-37` (`filter_within_radius`)
- Modify: `scripts/download_eraldis.py` (whole file — small enough to replace in full)
- Test: `tests/test_eraldis.py` (append new tests; existing tests must keep passing unmodified)

**Interfaces:**
- Consumes: nothing new from elsewhere in the codebase — `filter_within_radius` already exists and is being extended in place, same signature style (`gdf, lat, lon, radius_km` stay positional in the same order; `inner_radius_km` is appended as the new final parameter with a default).
- Produces: `filter_within_radius(gdf, lat, lon, radius_km, inner_radius_km=0.0) -> gpd.GeoDataFrame`. No other task or file in this plan depends on anything beyond this signature (this is the only task in the plan).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_eraldis.py` (the existing imports at the top of the file already
include `filter_within_radius` — no new import needed):

```python
def test_filter_within_radius_excludes_points_inside_inner_cutoff():
    home_lat, home_lon = 59.4370, 24.7536

    home_point_3301 = (
        gpd.GeoSeries([Point(home_lon, home_lat)], crs="EPSG:4326")
        .to_crs("EPSG:3301")
        .iloc[0]
    )

    too_close_point = Point(home_point_3301.x + 2_000, home_point_3301.y)  # 2km away
    in_ring_point = Point(home_point_3301.x + 10_000, home_point_3301.y)  # 10km away
    too_far_point = Point(home_point_3301.x + 200_000, home_point_3301.y)  # 200km away

    gdf = gpd.GeoDataFrame(
        {"name": ["too_close", "in_ring", "too_far"]},
        geometry=[too_close_point, in_ring_point, too_far_point],
        crs="EPSG:3301",
    )

    result = filter_within_radius(
        gdf, home_lat, home_lon, radius_km=80.0, inner_radius_km=5.0
    )

    assert list(result["name"]) == ["in_ring"]


def test_filter_within_radius_raises_when_inner_radius_not_less_than_outer():
    with pytest.raises(ValueError):
        filter_within_radius(
            gpd.GeoDataFrame({"name": []}, geometry=[], crs="EPSG:3301"),
            59.4370,
            24.7536,
            radius_km=20.0,
            inner_radius_km=20.0,
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_eraldis.py -v`
Expected: `test_filter_within_radius_excludes_points_inside_inner_cutoff` FAILs with a
`TypeError` (`filter_within_radius() got an unexpected keyword argument 'inner_radius_km'`).
`test_filter_within_radius_raises_when_inner_radius_not_less_than_outer` also FAILs (same
`TypeError`, or `DID NOT RAISE` once the `TypeError` itself is fixed — either failure mode is
expected at this point, both indicate the parameter doesn't exist yet).

- [ ] **Step 3: Write the implementation**

In `src/shroom_fm/eraldis.py`, replace the existing `filter_within_radius` function
(currently lines 27-37) with:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_eraldis.py -v`
Expected: PASS (4 tests: the 2 pre-existing plus the 2 new ones. The pre-existing
`test_filter_within_radius_keeps_only_points_inside_cutoff` — which calls
`filter_within_radius` without `inner_radius_km` — must still pass unmodified, proving the
`0.0` default preserves current behavior.)

- [ ] **Step 5: Run the full test suite to confirm no regressions**

Run: `uv run pytest tests/ -v`
Expected: PASS (73 tests: 71 existing + 2 new)

- [ ] **Step 6: Update the runner script**

Replace the full contents of `scripts/download_eraldis.py` with:

```python
from pathlib import Path

from shroom_fm.config import load_home_location
from shroom_fm.eraldis import compute_bbox, fetch_eraldis_bbox, filter_within_radius
from shroom_fm.wfs import fetch_capabilities

RADIUS_KM = 20.0
INNER_RADIUS_KM = 0.0
OUTPUT_PATH = Path("data/eraldis.geojson")


def main() -> None:
    home_lat, home_lon = load_home_location()
    wfs = fetch_capabilities()

    bbox = compute_bbox(home_lat, home_lon, RADIUS_KM)
    gdf = fetch_eraldis_bbox(wfs, bbox)
    nearby = filter_within_radius(gdf, home_lat, home_lon, RADIUS_KM, INNER_RADIUS_KM)

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

This is a whole-file replacement (the file is 27 lines, small enough to replace in full
rather than patch in place) — the only changes from the current version are the new
`INNER_RADIUS_KM = 0.0` constant, passing it as the 4th positional argument to
`filter_within_radius`, and the conditional print message.

- [ ] **Step 7: Manually verify the script still runs correctly**

This step does not require live network access — the change is purely in argument-passing
and print formatting, already covered by Step 5's full suite pass. Read the diff of
`scripts/download_eraldis.py` once more against the code block in Step 6 to confirm it
matches exactly (no typos in the constant name or argument order), since this script has no
automated test of its own (matches this project's existing pattern: runner scripts are thin
wiring, verified by their underlying library functions' tests plus a real run when actually
downloading data — not unit tested in isolation).

- [ ] **Step 8: Commit**

```bash
git add src/shroom_fm/eraldis.py scripts/download_eraldis.py tests/test_eraldis.py
git commit -m "feat: add inner-radius annulus filtering to download_eraldis"
```

---

## Self-Review Notes

- **Spec coverage:** The spec's `filter_within_radius` signature/validation/inclusive-bounds requirements, `compute_bbox` non-change, `scripts/download_eraldis.py` update (constant + conditional print), and testing requirements (3-point case + ValueError case + unmodified existing test) are all covered by this single task.
- **Placeholder scan:** none found — every step has complete, runnable code.
- **Type consistency:** `filter_within_radius`'s new signature (`gdf, lat, lon, radius_km, inner_radius_km=0.0`) is identical in Step 1's tests, Step 3's implementation, and Step 6's script call site (`filter_within_radius(gdf, home_lat, home_lon, RADIUS_KM, INNER_RADIUS_KM)` — positional, matching the parameter order exactly).
