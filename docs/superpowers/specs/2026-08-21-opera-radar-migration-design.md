# OPERA Radar Migration Design

## Problem

A routine sanity check of `data/weather_eraldis.geojson` (30 minutes after a real
`refresh_weather.py` run) found essentially zero rain across the *entire* 262,054-stand
dataset (`max(rain_14d_mm) = 0.0086mm`, `hours_since_any_rain` never below 24.3h
anywhere), despite a live KAIA radar map showing real rain up to 16mm/h near Tallinn a
few hours earlier. This contradicted the pipeline's own `weather_data_quality: "complete"`
label for the run.

**Root cause, established via direct verification against the real cached HDF5 files —
not guessed:**

1. `accumulate_rainfall`, run in isolation against the real cache, correctly produces
   real, spatially-varying rain (max 2.53mm within its footprint) — the bug is not in
   value decoding.
2. The bug traced to `weather.py`'s `_nearest_join`: `gpd.sjoin_nearest` has no maximum
   join distance, so every one of the 262,054 stands gets matched to *whatever* radar
   pixel is nearest — even 40-60km away — producing a plausible-looking but meaningless
   value instead of a flagged missing state. This is the proximate, code-level bug.
3. Investigating *why* so much of the dataset fell outside real radar reach led to the
   deeper cause: sampling `how.nodes` across the full 14-day cache (4,050 files) showed
   **95.2% of cached files are `nodes: eehar` (Harku only)**; only 4.7% are the genuine
   two-radar `eesyr,eehar` (Harku+Sürgavere) composite. Per-day breakdown shows a clean,
   real 12-day outage (2026-08-07 through 2026-08-18: 0% dual-radar every single day),
   consistent with Ilmateenistus's own live-reported "Sürgavere radar currently not
   working due to technical problems." A hypothesis that this was instead a CRS/
   georeferencing bug in this project's own code was raised, investigated directly
   (comparing the file's stated corner coordinates against its `xscale`/`yscale`/
   `xsize`/`ysize` via the file's own real `projdef`, and independently against the
   actual valid/nodata spatial footprint of the raw data) — the geo-referencing code
   was confirmed correct; the corner metadata (`LL/LR/UL/UR_lon/lat`) was confirmed
   stale/inconsistent with the real per-file grid (byte-for-byte identical across every
   sampled file regardless of actual content — a KAIA-side metadata quality issue, not
   a fix target for this project).
4. Separately, even when both radars are healthy, KAIA's fixed small grid
   (~580×559km, anchored near lon 19.99/lat 61.51) does not fully contain the eraldis
   download annulus — 3 of 4 bbox corners map outside the grid's own 720×720 bounds.

**Decision:** rather than patch the KAIA-specific join/coverage logic, replace the
radar data source entirely with EUMETNET's OPERA pan-European composite via the public
MeteoGate Open Radar Data (ORD) API — confirmed to genuinely cover Estonia (Keskkonnaagentuur,
the same agency operating KAIA, is an OPERA member; Harku and Sürgavere are two of the
200+ radars feeding the OPERA mosaic, so OPERA's mosaic can draw on neighboring
countries' radars during a domestic outage) at 2km/15-min resolution — coarser than
KAIA's 0.8km/5-min, but reliably covering the full annulus regardless of any single
country's radar health. This is a deliberate simplicity-over-resolution tradeoff: no
dual-source fallback logic, one ingestion path, at the cost of always operating at
OPERA's resolution even on days when Estonia's own radars are fully healthy.

This migration is also the natural point to fix the join-distance bug at its root
(never join across unbounded distance, for any future data source) and a second,
independently-discovered defect: **the currently-shipped `weather_data_coverage` can
exceed 1.0** (observed `1.0044...`), which is mathematically impossible for a fraction
and was previously documented as a "harmless" cadence artifact rather than fixed. This
spec treats it as the real invariant violation it is.

**Live pre-implementation verification (2026-08-21), against the real API and real
downloaded files — not just documentation:** two real, consecutive OPERA `RATE`
composites were fetched directly from the public `openradar-24h` S3 bucket
(`s3://openradar-24h/2026/08/21/OPERA/COMP/OPERA@20260821T00{00,15}@0@RATE.h5`) and
fully inspected. This resolved every open question the spec originally deferred to
"verify once implementation starts": real grid is exactly `1900×2200` pixels at
`2000m`/`2000m` (2km confirmed exactly, not estimated); real `projdef` is
`+proj=laea +lat_0=55.0 +lon_0=10.0 ...` (Lambert Azimuthal Equal-Area, not Mercator as
assumed — irrelevant, since the existing code already reads `projdef` dynamically per
file and never hardcodes a projection family); real corners span roughly -40°E to
+58°E, 31.7°N to 67.6°N — genuinely pan-European, Estonia sits deep in the interior;
real cadence for RATE confirmed as 15 minutes from the two files' own `starttime`
attrs (`000000` → `001500`), not the `PT1M` the ORD catalog metadata's `duration`
field misleadingly suggested (that field turned out to be unreliable — every
parameter in the catalog, including RATE/DBZH, also reports `unit: "%"`, which is
physically meaningless for a rain rate, confirming the catalog metadata layer is
generic/templated in places and not to be trusted over real file content); real
`gain`/`offset`/`nodata`(`-9999000.0`)/`undetect`(`-8888000.0`) confirm the exact same
conceptual decode pattern as KAIA (different sentinel values, already read
dynamically, not hardcoded anywhere in the existing code); a genuine per-pixel quality
layer is present (`dataset1/data1/quality1/data`, decoded range exactly `[0.0, 1.0]`,
`how.task='pl.imgw.quality.qi_total'`) — the real QIND-equivalent, identified by the
ODIM `qualityN` subgroup convention rather than a `quantity` string (see Component 3).
**Decisively: all four test points across Estonia — home/Tallinn, Tartu, Pärnu, and
Valga — show real, valid, `quality=1.000` observations in this real file.** Valga is
the exact location that motivated this whole investigation (visible on the live KAIA
map, unreachable in KAIA's own small grid); OPERA covers it cleanly. Separately
confirmed: the ORD REST API's anonymous rate limit is a real, numeric
`200 requests/hour` (from the live `X-RateLimit-Limit` response header, not
"undocumented" as first assumed), and the S3 bucket genuinely holds `OPERA/COMP/`
composite files at recent dates — an initial check against a stale 2-day-old date
wrongly suggested otherwise before this was caught and corrected. **Still open, not
yet resolved live:** the exact request/response contract for fetching a *historical*
(>24h old) date range via the REST API — every attempt against
`/collections/observations/locations/{location_id}` returned `503` even for a minimal,
correctly-shaped request, while sibling endpoints (`/collections`, `/area`) responded
normally (`/area` returned a real `422` validation error for a missing param, then a
real `204 No Content` for a corrected request — inconclusive on its own, likely a
response-format negotiation issue rather than a hard failure, but not yet confirmed
working end-to-end). This is carried into the plan as an explicit early task, not
assumed.

## Architecture

```
                    OPERA Open Radar Data (MeteoGate)
                              │
        ┌─────────────────────┴─────────────────────┐
   Historical/backfill                    Recent (≤24h) — CONFIRMED WORKING
   ORD REST API + API key                 public anonymous S3, no signing needed:
   (request contract NOT YET             s3://openradar-24h/YYYY/MM/DD/OPERA/COMP/
    confirmed — see Component 1)          OPERA@YYYYMMDDTHHMM@0@{RATE,ACRR,DBZH}.h5
   200 req/hour anonymous                 (+ .tiff variants); real files fetched
   (real, numeric, confirmed live)        and fully inspected 2026-08-21
        │                                  │
        └─────────────────────┬─────────────────────┘
                    data/radar_cache/ (ODIM HDF5 — same standard as KAIA's)
                              │
              per-file: read cheap `where`-only metadata,
              compute AOI row/col crop ONCE (existing
              radar_bbox_slice pattern, reused as-is),
              decode ONLY that cropped sub-array — never
              materialize the full ~1900×2200 European grid
                              │
              decode rate_mm_h AND a 3-state per-pixel status:
                nodata     → invalid observation (coverage += 0)
                undetect   → valid, rain = 0.0  (coverage += 1)
                real value → valid, rain >= 0   (coverage += 1)
              (value-decode already gets this right today; the
               new part is carrying validity into the AGGREGATE
               coverage counter, not just the per-slot rain value)
                              │
              per-pixel, per-rolling-window (1d/3d/7d/14d)
              rainfall AND coverage rasters, expected-slot counts
              fixed at 15-min cadence (96/288/672/1344), explicit
              [start, end) alignment, hard invariant
              0.0 <= coverage <= 1.0 (assert, don't shrug)
                              │
              raster-native zonal assignment onto eraldis
              (direct row/col index for a stand inside one pixel —
               a coordinate transform, not a spatial join, so no
               sjoin_nearest-class bug is structurally possible;
               mean over intersecting valid pixels for a stand
               spanning several; zero valid pixels intersecting
               → None, never a distant/extrapolated value)
                              │
                    weather_eraldis.geojson
              (coverage is now a real per-stand spatial+temporal
               fraction, not just "how many files arrived today")

Quality layer: confirmed present in real files as `dataset1/data1/quality1/data`
([0,1] range, ODIM `qualityN`-subgroup convention, not a `quantity="QIND"` string)
— read and carried through as optional enrichment; pipeline behavior is identical
whether or not a given file has one.
```

## Components

### Component 1: `radar.py` — OPERA catalog/download (replaces KAIA-specific code)

Replaces `KAIA_QUERY_URL`/`KAIA_DOWNLOAD_URL_TEMPLATE`/`query_radar_documents`/
`download_radar_composite`/`_cache_filename`/`cached_radar_timestamp`.

- **Recent (last 24h) — CONFIRMED WORKING against real live data.** List and fetch
  from the public anonymous S3 bucket via plain HTTPS GET, no signing, no `boto3`:
  `https://s3.waw3-1.cloudferro.com/openradar-24h/?list-type=2&prefix=YYYY/MM/DD/OPERA/COMP/`
  for listing, then `GET https://s3.waw3-1.cloudferro.com/openradar-24h/<key>` per
  object. Real confirmed key format:
  `YYYY/MM/DD/OPERA/COMP/OPERA@YYYYMMDDTHHMM@0@{RATE,ACRR,DBZH}.h5` (a `.tiff`
  cloud-optimized-GeoTIFF sibling also exists per key — not used by this project, ODIM
  HDF5 is sufficient and matches the existing parsing code). **Important operational
  note discovered live:** this is a genuine 24-hour *rolling* cache — querying a date
  more than ~24-48h old returns a real, valid, empty (`KeyCount: 0`) response, not an
  error — `_cache_filename`/backfill logic must not treat an empty listing for an old
  date as a fetch failure, just as "this data has legitimately rolled off, use the REST
  API instead."
- **Backfill/historical** (anything older than the S3 24h window, needed to populate/
  maintain the rolling 14-day window): query MeteoGate's ORD REST API
  (`https://api.meteogate.eu/eu-eumetnet-weather-radar`), `location_id=0-20010-0-OPERA`,
  `standard_name=RATE`, `method=comp`, `format=ODIM`, `datetime=<ISO8601 range>`. Uses a
  registered API key (MeteoGate Developer Portal) — anonymous access is capped at a
  real, confirmed **200 requests/hour** (live `X-RateLimit-Limit` header), and a 14-day
  backfill at 15-minute cadence is ~1,344 timestamps, not a casual anonymous-tier
  workload regardless of how many timestamps one request can cover. **The exact request/
  response contract for this endpoint is NOT yet confirmed** — every live attempt against
  `/collections/observations/locations/{location_id}` returned `503` even for a minimal,
  correctly-shaped request, while `/collections` and `/area` (a sibling data-query type)
  both responded normally. This is carried into the plan as an explicit early task
  (obtain a real API key, determine the working request shape and response cardinality/
  pagination) — not assumed or guessed at in this spec.
- `fetch_new_radar_composites(cache_dir, since)` keeps its existing signature (confirmed
  via reading `scripts/refresh_weather.py`, which calls it generically) — internally
  routes each requested time slice to S3 or the REST API by age, so the orchestrator
  script needs zero changes.
- Real OPERA filename/timestamp convention: **confirmed** — `OPERA@YYYYMMDDTHHMM@0@RATE.h5`
  (the REST API's own delivered filenames need separate confirmation once its
  request/response contract is resolved, but are expected to match, since both paths
  serve the same underlying product).

### Component 2: `radar.py` — generic ODIM parsing (reused, re-verify against real data)

`expire_old_radar_composites`, `cached_radar_files`, `newest_cached_radar_timestamp`,
`read_radar_full_georef`, `_radar_origin`, `radar_bbox_slice`, `parse_radar_composite`
are kept largely as-is: they already read `projdef`/`xscale`/`yscale`/corner values
dynamically from each file's own `where`/`what` attrs (not hardcoded to KAIA), and
`radar_bbox_slice` already computes the AOI row/col crop from a cheap metadata-only read
before any per-file raster decode — exactly the early-crop discipline OPERA's grid
requires. **Confirmed against two real downloaded files (2026-08-21):** real grid is
exactly `xsize=1900, ysize=2200` at `xscale=yscale=2000.0` (2km, exact); real `projdef`
is `+proj=laea +lat_0=55.0 +lon_0=10.0 +x_0=1950000.0 +y_0=-2100000.0 +units=m
+ellps=WGS84` (Lambert Azimuthal Equal-Area — different projection family than KAIA's
Mercator, but the existing code never hardcoded Mercator, reads `projdef` dynamically
via `pyproj.CRS.from_proj4`, so this required zero code changes to work); real corners
span roughly (-39.5°E, 67.0°N) to (57.8°E, 67.6°N) at the top and (-10.4°E, 31.7°N) to
(29.4°E, 32.0°N) at the bottom — genuinely pan-European, Estonia sits deep in the
interior. `Conventions: ODIM_H5/V2_4` — same standard as KAIA's files.

### Component 3: `radar.py` — nodata/undetect/quality-aware validity

**Confirmed live pitfall — do not repeat KAIA's corner-metadata mistake.** The real
file's top-level `how.nodes` attribute lists ~130 radar site codes (the OPERA network's
full static roster, including both `eehar` and `eesur` for Estonia) — this is *not* a
per-timestamp "which radars actually contributed valid data to this composite" signal;
it reads as a fixed roster, not dynamically computed per file, same class of trap as
KAIA's stale `LL/LR/UL/UR` corners. **The per-pixel `quality1` layer is the real,
per-timestamp validity/confidence signal** — do not build any diagnostic on `how.nodes`
expecting it to reflect a specific outage the way this project's original KAIA
investigation used it.

`parse_radar_composite`'s existing decode already correctly distinguishes `nodata`
(→ `NaN`, invalid) from `undetect` (→ `0.0`, valid confirmed-dry) at the per-slot value
level. This component extends that distinction into the *aggregate coverage counter*
(new — today's coverage counting is purely file-count/temporal, with no per-pixel
concept at all): a pixel's coverage increments only on `undetect` or a real rate value,
never on `nodata`. Getting this backwards in either direction reintroduces a real bug:
treating `undetect` as missing would make genuinely dry regions look under-covered;
treating `nodata` as a real zero would reintroduce the exact class of bug this whole
migration exists to fix.

**Confirmed against a real file (2026-08-21):** a per-pixel quality dataset genuinely
exists at `dataset1/data1/quality1/data`, same shape as the rain data, decoded value
range exactly `[0.0, 1.0]` (`quality1/what` has `gain=1.0`/`offset=0.0`, no separate
`quantity` attr — its identity as the quality layer comes from the ODIM `qualityN`
subgroup-under-`data1` convention, not a `quantity="QIND"` string as the spec originally
assumed), `quality1/how.task = 'pl.imgw.quality.qi_total'` (IMWM's real, named
total-quality-index algorithm). At the 4 Estonian points spot-checked
(home/Tallinn, Tartu, Pärnu, Valga), this quality value was `1.000` — real, present,
high-confidence, including at Valga specifically, the exact point that motivated this
whole investigation. Read and carried through as optional enrichment when present;
pipeline behavior must be identical for a file that lacks a `qualityN` subgroup — never
a hard requirement, never silently required for correctness.

Separately confirmed real value-decode semantics from the same file:
`dataset1/data1/what` has `gain=1.0`, `offset=0.0`, `nodata=-9999000.0`,
`undetect=-8888000.0`, `quantity='RATE'` — same conceptual gain/offset/nodata/undetect
pattern as KAIA's files (very different sentinel magnitudes, but the existing decode
logic already reads these dynamically from each file's own attrs, never hardcoded, so
this required zero code changes to keep working).

### Component 4: `radar.py` — `accumulate_rainfall` rewrite (raster-native)

Shifts from "flatten the grid into a point-per-pixel GeoDataFrame with a scalar
`rain_Nd_mm` per point" to maintaining genuine 2D rainfall-accumulation and
coverage-count rasters per rolling window (1d/3d/7d/14d), aligned to a fixed 15-minute
slot grid with explicit `[start, end)` interval semantics (not ambiguous inclusive/
exclusive boundaries — the proximate cause of today's shipped `coverage > 1.0` bug,
confirmed impossible and root-caused here rather than carried forward). `_RADAR_SLOT_MINUTES`
becomes 15 (was 5); expected-slot counts become 96/288/672/1344 for 1d/3d/7d/14d.
`RAIN_EVENT_DRY_GAP_H`/`SIGNIFICANT_EVENT_MM`/`STRONG_EVENT_MM` (hour/mm-based, not
slot-count-based) carry over unchanged in *meaning*; the slot-count conversion just
naturally becomes coarser. **New hard invariant: `0.0 <= coverage <= 1.0`, asserted, not
silently accepted** — any violation is a real bug to fix, not a value to shrug at in
documentation.

### Component 5: `radar.py` — raster-native eraldis assignment (no `sjoin_nearest`)

New function (lives in `radar.py`, not `weather.py`'s generic `_nearest_join`, since
it's raster-specific): for each eraldis stand, transform its centroid through the same
pyproj-based coordinate transform `radar_bbox_slice` already uses to get an exact
row/col index, then look up that pixel's rainfall/coverage directly — a coordinate
transform and array index, not a spatial join, so an `sjoin_nearest`-class unbounded-
distance bug is structurally impossible here, not just guarded against. For a stand
whose geometry spans multiple 2km pixels (more likely than with KAIA's finer 0.8km
grid, given typical eraldis stand sizes), average over every pixel the stand's geometry
actually intersects — simple mean over intersecting *valid* pixels is the v0 approach
(same "v0 engineering prior, revisit once real data justifies more" discipline used
elsewhere in this project — a true area-weighted average is explicitly deferred, see Out
of Scope). If **zero** pixels intersecting a stand have any valid observation for a given
window, the result is `None` — never a fabricated or extrapolated value, regardless of
how close the nearest valid pixel might be.

### Component 6: `weather.py` — per-stand coverage integration

`_nearest_join` (MEPS-only, point-based, genuinely untouched by this spec) is kept
exactly as-is. `radar_coverage` changes shape: from one national `{"3d": ..., "7d": ...,
"14d": ...}` dict to a genuinely per-stand value (each stand's own spatial+temporal
coverage from Component 5's assignment, not a single dataset-wide average that could
mask a real, localized gap the way this whole investigation started from). This
composes naturally with the *already-shipped* `weather_data_quality` reporting shape,
which is already a per-stand-then-aggregated `Counter` (e.g. `{'complete': 262054}`),
not a single string — extending coverage to be genuinely per-stand is a natural
extension of the existing pattern, not a new one.

### Component 7: cache migration

`data/radar_cache/`'s existing ~4,050 KAIA HDF5 files are unusable under the new schema
(different product, different scale, different projection convention) — wiped or moved
aside, then a fresh OPERA backfill populates the cache from scratch. This is a real,
potentially slow one-time operation that needs its own live-verified timing once
implementation is testable against the real API — cannot be assumed to behave like
KAIA's documented cold-start (which itself took "well over 30 minutes... likely
multiple hours in the worst case").

## Testing

- `tests/test_radar.py`: rewritten fixtures for OPERA-shaped files (new `projdef`,
  realistic corner/scale values once known); new tests for the nodata/undetect/QIND
  3-way coverage distinction (a fixture with a mix of all three per-slot, asserting the
  coverage counter only increments on undetect/real-value); the `0.0 <= coverage <= 1.0`
  invariant (a fixture deliberately probing a boundary/edge condition that would have
  produced `>1.0` under the old inclusive/exclusive-boundary logic, asserting it no
  longer does); the raster-native assignment function (point-inside-one-pixel case,
  stand-spanning-multiple-pixels case with a mean over valid pixels, and the
  zero-valid-pixels-intersecting → `None` case — this last one is the direct regression
  test for the original bug report).
- `tests/test_weather.py`: updated for per-stand `radar_coverage` shape; existing
  `weather_data_quality` tests should mostly carry over given the Counter shape is
  unchanged, just fed genuinely per-stand data now instead of one repeated national
  value.
- Real-scale verification (once implementation is far enough along to fetch real OPERA
  data): confirm a real backfill completes and produces per-stand coverage that
  correctly distinguishes covered from uncovered regions (unlike the KAIA-era run, which
  reported uniform `"complete"` for stands with zero real signal); spot-check that a
  known-uncovered case (e.g. during a future domestic radar outage, if one recurs) shows
  `None` rain values with an honest coverage label, not a fabricated near-zero.

## Out of Scope

- **Hybrid KAIA+OPERA fallback design.** Explicitly considered and rejected — OPERA-only,
  full replacement, per explicit decision (accepting the 2km/15-min resolution ceiling
  even on days both Estonian radars are healthy, in exchange for one ingestion path and
  no per-stand source-selection logic).
- **MQTT real-time notifications.** OPERA's notification service is described by
  EUMETNET as the most efficient access method, but it assumes an always-on consumer —
  this project's `refresh_weather.py` is explicitly a short-lived, on-demand script
  ("meant to be re-run on demand... not as part of `main.py`'s sequence"), which doesn't
  fit a persistent pub/sub connection's usage model. Revisit only if this project's
  usage pattern changes to a genuinely long-running service.
- **True area-weighted zonal statistics** (weighting each intersecting pixel by its
  exact overlap fraction with a stand's geometry, e.g. via `rasterio`/`rasterstats`).
  V0 uses a simple mean over intersecting valid pixels — deferred pending evidence this
  approximation actually matters given real eraldis-stand-size-to-2km-pixel ratios.
- **Fixing the `>1.0` coverage-invariant bug class anywhere outside radar coverage**
  (e.g. if MEPS coverage has a similar theoretical risk) — this spec fixes it at the
  root for radar specifically, since that's where it was found; a similar audit of MEPS
  coverage math is a separate, later concern if it turns out to matter.
- **Recalibrating any `FruitingScore` constants** (`RAIN_SCALE_MM`, `MOISTURE_WEIGHTS`,
  etc.) for OPERA's coarser 2km/15-min signal versus KAIA's 0.8km/5-min. These remain
  v0 engineering priors regardless of the underlying radar source; revisiting them
  against real observations is already documented as separate, deferred work.
- **`boto3` as a new dependency.** Plan assumes plain HTTPS GET (via the existing
  `requests` dependency) is sufficient for anonymous S3 listing/fetch — confirm this
  empirically once implementation starts; only add `boto3` if that proves genuinely
  awkward.
