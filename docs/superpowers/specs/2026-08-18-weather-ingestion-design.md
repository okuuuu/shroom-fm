# Weather Ingestion (KAIA Radar + MEPS) — Design

## Problem

`FruitingScore` — the weather-driven layer that turns static `HabitatScore`/`EcotoneScore`
into a "worth scouting *this week*" ranking — is explicitly deferred in CLAUDE.md's planned
architecture, pending the static habitat pipeline being validated (it now is, through
`ScoutScore` v0). This is the first of several sub-projects needed to build it: getting
per-`eraldis` weather features into the pipeline at all. `FruitingScore`'s actual scoring
formula (combining these features into a species/date score) is a separate, later
sub-project — this spec covers ingestion only.

Weather data is fundamentally different from every other data source this project has
integrated so far: `eraldis`/`roads`/`eraldis_element` are static WFS layers filtered once
per run, but rainfall/temperature/humidity are inherently time-varying, and a useful
rainfall signal needs a rolling multi-day accumulation window, not a single point-in-time
value. This spec is scoped to two sources only, per an explicit decision to drop the
remaining validation/calibration sources (Estonian climate stations, FWI DMC/DC, personal
observations, PlutoF) from this first pass:

- **KAIA radar precipitation composites** (Harku + Sürgavere radars, 5-minute HDF5/ODIM
  files, CC-BY 4.0, published as open bulk-downloadable data) — the primary rainfall
  signal, chosen over point weather-station data specifically because a single summer
  thunderstorm can soak one forest and miss another 15km away; only a spatial raster
  captures that.
- **MEPS (MetCoOp) via MET Norway's THREDDS/OPeNDAP** — 2.5km Nordic NWP grid, providing
  temperature and relative humidity, since Estonia's station network alone isn't dense
  enough to build a spatial surface (stations remain useful as calibration, but that's an
  explicitly out-of-scope sub-project for later).

## Open verification items (must resolve before finalizing parsing code)

Two real endpoint details are not yet confirmed live, consistent with this project's
established practice of confirming WFS/API shapes against the real service before code
depends on them (see CLAUDE.md's "Known real-data quirks"):

1. KAIA's radar composite bulk-download URL/index format — the file naming convention
   (`comp.YYYYMMDDHHMMSS.CAP.NNNN.h5`) and HDF5 internal layout (`where`/`what` ODIM
   groups) are documented, but the actual directory-listing or bulk-download endpoint to
   discover/fetch new files incrementally is not yet confirmed.
2. MEPS's exact 2m temperature/relative-humidity variable and dimension names on the
   OPeNDAP endpoint (`https://thredds.met.no/thredds/dodsC/meps25files/meps_det_pp_2_5km_latest.nc`
   or the Nordic 1km postprocessed product) need a live `xarray.open_dataset(...).variables`
   inspection.

The implementation plan's first task is a live-verification spike for both, mirroring
`scripts/get_capabilities.py`/`scripts/get_etak_capabilities.py`'s precedent — no parsing
code is finalized against assumed shapes.

## Architecture

```
KAIA radar HDF5 files                    MEPS THREDDS OPeNDAP
  (5-min composites)                       (2.5km NWP grid)
        |                                          |
  h5py: parse where/what groups           xarray+netCDF4: open_dataset(url),
  for georeferencing (no hardcoded         subset to home bbox + time range
  resolution — read pixel size/origin/            |
  projection from the file itself)                |
        |                                          |
  rolling local cache                     always-fresh (no accumulation
  data/radar_cache/ (gitignored,          needed — MEPS gives recent
  auto-expires entries >14 days old)      analysis + short forecast directly)
        |                                          |
  accumulate rain_3d/7d/14d,                       |
  time_since_rain, wet_hours                       |
        |                                          |
  pixel-center points as GeoDataFrame     grid-point GeoDataFrame
        |                                          |
        +------------------+-----------------------+
                           |  sjoin_nearest onto eraldis centroids
                           |  (same pattern access.py already uses)
                           v
                data/weather_eraldis.geojson
           (overwritten each run, as_of timestamp,
            per-column coverage/quality flags)
```

## Components

**`src/shroom_fm/weather.py`** (new module):
- `parse_radar_composite(path) -> tuple[np.ndarray, dict]` — reads a single HDF5 composite
  file, returns the precipitation-rate raster and its georeferencing (projection, pixel
  size, origin) read from the file's own `where` group — never hardcoded.
- `fetch_new_radar_composites(cache_dir, since) -> list[Path]` — lists and downloads only
  composite files newer than `since` into `cache_dir`; exact discovery mechanism depends on
  the verification spike's findings.
- `expire_old_radar_composites(cache_dir, max_age_days=14) -> None` — deletes cached files
  older than the rolling window needs.
- `accumulate_rainfall(cache_dir, window_days) -> tuple[np.ndarray, dict, float]` —
  sums precipitation across all cached composites within the window, returns the summed
  raster, its georeferencing, and the fraction of expected 5-minute slots actually present
  (the coverage ratio used for `weather_data_quality`).
- `radar_pixels_to_points(raster, georef) -> gpd.GeoDataFrame` — converts a raster + its
  georeferencing into a GeoDataFrame of pixel-center points with the raster's values as a
  column, in the raster's native CRS.
- `fetch_meps_subset(bbox, hours_back) -> xarray.Dataset` — opens the MEPS OPeNDAP URL,
  subsets to the given bounding box and time range.
- `meps_grid_to_points(dataset) -> gpd.GeoDataFrame` — converts the MEPS grid subset into a
  GeoDataFrame of grid-point locations with temperature/humidity columns.
- `join_weather_to_eraldis(eraldis_gdf, radar_points, meps_points) -> gpd.GeoDataFrame` —
  `sjoin_nearest`s both point sets onto `eraldis` centroids (same pattern as
  `access.py::_nearest_join`), producing the final per-`eraldis` feature columns.

**`scripts/refresh_weather.py`** (new script, standalone — not inserted into `main.py`'s
9-step sequence, since it's run on-demand at a different cadence than the rest of the
pipeline): loads home location, refreshes the radar cache (fetch new + expire old),
accumulates rainfall, fetches the current MEPS subset, joins both onto `data/eraldis.geojson`,
writes `data/weather_eraldis.geojson`.

## Output schema (v1, trimmed)

Radar-derived:
```
rain_3d_mm, rain_7d_mm, rain_14d_mm   # rolling accumulation
hours_since_rain                       # single "measurable rain" threshold: >=0.1mm in a
                                        # single 5-minute composite counts as "wet" (not the
                                        # any/1mm/5mm split — deferred)
wet_hours_72h                          # sum of 5-minute slots meeting the same 0.1mm
                                        # threshold within the last 72h, converted to hours
```

MEPS-derived:
```
temp_mean_3d, temp_night_mean_3d
rh_mean_3d, rh_night_mean_3d
```

Metadata (required, not optional):
```
as_of                    # timestamp this refresh ran
weather_data_coverage    # fraction of the expected rolling window actually backed by
                          # real cached files/a fresh-enough MEPS run
weather_data_quality     # categorical flag: "complete" | "partial_radar_gap" |
                          # "stale_meps" | ...
```

Deferred to a later pass (not built now, but the schema leaves room for them): percentile
spread (p25/p75) per rain window, max 1h/6h rain intensity, multiple rain-event thresholds,
`vpd_mean`, and the `hours_since_1mm_event`/`hours_since_5mm_event` split from the original
reference material. None of these block a first working `FruitingScore` — they are
refinements on top of a validated base signal, matching this project's `ScoutScore` v0
precedent of shipping a minimal-but-honest first version.

## Freshness and fallback (never fabricate, never silently degrade)

This is a hard requirement, not a nice-to-have — KAIA's radar feed has had real outage
periods, so ingestion must never treat a stale cache as current:

- Every fetch records the actual max timestamp it achieved per source (the last cached
  radar file's timestamp; the MEPS run's analysis time) — never assumed from wall-clock
  time.
- If radar coverage over the requested rolling window falls below a threshold (e.g. missing
  more than 30% of the expected 5-minute slots in the 14-day window), the affected
  `rain_*d_mm`/`wet_hours_72h` columns become `None`/NaN rather than a silently-partial sum
  computed from whatever happened to be cached.
- If MEPS's latest available run is older than expected (e.g. no run within roughly the
  last 6 hours, itself indicating a THREDDS-side outage), `temp_*`/`rh_*` columns become
  `None`/NaN rather than serving a stale forecast as current conditions.
- `weather_data_quality` records which columns were degraded and why, for every row.
- This mirrors the "never fabricate, never silently drop data" discipline already used
  throughout this project: `ScoutScore`'s `remote_high_value` tier (never a fabricated `0`
  for unconfirmed access), and the strict separation between `StandHabitatScore` and
  `AccessScore` (never blending a confirmed signal with an unconfirmed one into a single
  opaque number).

## Testing

- Unit tests for parsing/accumulation/join logic use small synthetic HDF5/NetCDF fixtures
  built in-test — no test hits a live server, matching every other test file in this
  project (`tests/test_roads.py`, `tests/test_eraldis.py`, etc.).
- Coverage: georeferencing extraction from a fabricated `where` group, rolling-window
  accumulation arithmetic (including partial-coverage degradation), the zonal join
  (`sjoin_nearest` onto `eraldis` centroids), and cache expiry (`expire_old_radar_composites`
  correctly removes only entries older than the window).
- A one-off live-verification script confirms the real KAIA bulk-archive endpoint and real
  MEPS OPeNDAP variable/dimension names before parsing code is finalized against them (see
  "Open verification items" above) — this is the plan's first task.
- Real end-to-end verification against the live production endpoints happens at
  implementation-review time. Unlike prior WFS work, there's no pre-existing "known-good
  row count" to diff against (this is new data, not a refactor of existing data) — instead,
  verification spot-checks a handful of `eraldis` against manually-inspected raw radar/MEPS
  values to confirm the accumulation/join math is sane.

## Out of scope (this sub-project)

- Estonian climate-station API (`keskkonnaandmed.envir.ee/f_kliima_tund`) as a
  calibration/QC layer — deferred to its own sub-project.
- KAIA's FWI/DMC/DC dryness rasters — deferred to its own sub-project (next in the proposed
  build order: KAIA radar → FWI DMC/DC → MEPS temp/RH, though this spec bundles MEPS in
  alongside radar per explicit scope instruction).
- Personal observation history and PlutoF/eElurikkus regional-pulse signals — both deferred
  to their own sub-projects.
- `FruitingScore`'s actual scoring formula (combining these features into a per-species,
  per-date score) and its integration into `ScoutScore` — a separate, later sub-project
  that consumes this one's output.
- Historical snapshot/append-only storage of weather runs (for future model training) —
  v1 overwrites a single `data/weather_eraldis.geojson` each run; history can be added once
  PostGIS lands, per CLAUDE.md's long-term architecture.
- `main.py` orchestrator integration — this runs standalone, on a different cadence than
  the existing 9-step pipeline.
