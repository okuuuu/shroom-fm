# Inner-Radius (Annulus) Filtering for `download_eraldis` — Design

Date: 2026-08-17
Status: Approved

## Purpose

MVP step 2 (`Restrict to ≤80 km from home`, currently implemented as `RADIUS_KM = 20.0` in
`scripts/download_eraldis.py`) currently downloads a solid disc of stands around home. This
means small city parks and urban infrastructure forest near home are included in every run,
even though they're rarely worth scouting compared to the larger forest ring further out.
This change adds an optional inner-radius cutoff so the download becomes an annulus
(ring) — `inner_radius_km` to `radius_km` — discarding the near-home disc while keeping
everything from the outer radius inward to that cutoff.

## Change

`filter_within_radius` in `src/shroom_fm/eraldis.py` gains an optional `inner_radius_km`
parameter, defaulting to `0.0`:

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

Default `0.0` preserves current disc behavior (`distances_km >= 0.0` is always true) for
this function's only current caller and for any future caller that doesn't pass it — no
existing behavior changes unless `inner_radius_km` is explicitly set above `0.0`.

**Validation:** raises `ValueError` if `inner_radius_km >= radius_km`. An inverted or
zero-width ring is almost certainly a config mistake (e.g. accidentally setting both to the
same value, or swapping inner/outer), not an intentional "match nothing" request — failing
loudly here is cheaper than silently shipping an empty `data/eraldis.geojson` and only
noticing much later in the pipeline.

**Both bounds inclusive** (`>=`/`<=`), matching the existing outer-bound `<=` semantics
already in use — a stand exactly at the inner or outer radius is kept, not excluded.

## `compute_bbox` — unchanged

`compute_bbox` continues to take only `radius_km` (the outer radius) and is not modified.
The WFS bbox query still needs to cover the full outer disc — the inner cutoff is a
post-fetch distance filter applied in the same place the outer cutoff already happens
(`filter_within_radius`), not a change to what gets fetched from the WFS.

## `scripts/download_eraldis.py`

```python
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
```

`INNER_RADIUS_KM` is a script-level constant, matching the existing `RADIUS_KM` pattern (not
moved into `config.toml`, which currently holds only `home_lat`/`home_lon`) — tune it
directly in the script the same way `RADIUS_KM` is tuned today, e.g. `INNER_RADIUS_KM = 5.0`
to exclude the immediate city core.

## Testing

Extend `tests/test_eraldis.py`:

- A three-point case (too-close / in-ring / too-far) proving the inner cutoff actually
  excludes a near point while keeping a point inside the ring and still excluding a far
  point — not just that the outer bound still works.
- A test asserting `ValueError` is raised when `inner_radius_km >= radius_km`.
- The existing `test_filter_within_radius_keeps_only_points_inside_cutoff` test (disc-only,
  no `inner_radius_km` argument) stays unchanged and must keep passing, proving the default
  preserves current behavior.

## Out of scope

- No `compute_bbox` signature change.
- No `config.toml` changes — radius values stay script-level constants.
- No change to any downstream pipeline step (`enrich.py`, `adjacency.py`, `ecotone.py`,
  `habitat.py`) — this only affects which stands enter `data/eraldis.geojson` in the first
  place; everything downstream is unaffected by construction.
