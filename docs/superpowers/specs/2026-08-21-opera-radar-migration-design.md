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

## Architecture

```
                    OPERA Open Radar Data (MeteoGate)
                              │
        ┌─────────────────────┴─────────────────────┐
   Historical/backfill                    Recent (≤24h)
   ORD REST API + API key                 public anonymous S3
   (RATE, 15-min slots,                   24h rolling cache
    rolling 14-day window)                (no documented rate limit,
        │                                  not asserted "unlimited")
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

QIND: read and carried through as optional quality metadata when a
file has it (schema-supported, not guaranteed present in every real
file — verify once implementation starts); pipeline behavior is
identical whether or not it's present.
```

## Components

### Component 1: `radar.py` — OPERA catalog/download (replaces KAIA-specific code)

Replaces `KAIA_QUERY_URL`/`KAIA_DOWNLOAD_URL_TEMPLATE`/`query_radar_documents`/
`download_radar_composite`/`_cache_filename`/`cached_radar_timestamp`.

- **Backfill/historical** (anything older than 24h, needed to populate/maintain the
  rolling 14-day window): query MeteoGate's ORD REST API
  (`https://api.meteogate.eu/eu-eumetnet-weather-radar`), `location_id=0-20010-0-OPERA`,
  `standard_name=RATE`, `method=comp`, `format=ODIM`, `datetime=<ISO8601 range>`. Uses a
  registered API key (MeteoGate Developer Portal), not anonymous access — anonymous mode
  is documented as having "low query limits," and a 14-day backfill at 15-minute cadence
  is ~1,344 requests, not a casual anonymous-tier workload.
- **Recent** (last 24h): list and fetch from the public anonymous S3 bucket
  (`openradar-24h`, `https://s3.waw3-1.cloudferro.com/`, `--no-sign-request` equivalent
  via plain HTTPS GET) — genuinely rate-limit-free for anonymous access is *not*
  documented anywhere by EUMETNET, so this is described as "no documented per-query rate
  limit," never "unlimited."
- `fetch_new_radar_composites(cache_dir, since)` keeps its existing signature (confirmed
  via reading `scripts/refresh_weather.py`, which calls it generically) — internally
  routes each requested time slice to S3 or the REST API by age, so the orchestrator
  script needs zero changes.
- Real OPERA filename/timestamp convention needs confirming against a genuinely
  downloaded file once implementation starts (cannot be verified without live access) —
  `_cache_filename`/`cached_radar_timestamp` get adapted to whatever that convention
  turns out to be.

### Component 2: `radar.py` — generic ODIM parsing (reused, re-verify against real data)

`expire_old_radar_composites`, `cached_radar_files`, `newest_cached_radar_timestamp`,
`read_radar_full_georef`, `_radar_origin`, `radar_bbox_slice`, `parse_radar_composite`
are kept largely as-is: they already read `projdef`/`xscale`/`yscale`/corner values
dynamically from each file's own `where`/`what` attrs (not hardcoded to KAIA), and
`radar_bbox_slice` already computes the AOI row/col crop from a cheap metadata-only read
before any per-file raster decode — exactly the early-crop discipline OPERA's much
larger (~3800×4400km) grid requires. These functions must be re-verified against a real
downloaded OPERA file (different `projdef`, likely different corner/scale conventions,
possibly a much larger `xsize`/`ysize`) once implementation can actually fetch one — the
*design* doesn't change, but nothing here is assumed correct without a real-file check.

### Component 3: `radar.py` — nodata/undetect/QIND-aware validity

`parse_radar_composite`'s existing decode already correctly distinguishes `nodata`
(→ `NaN`, invalid) from `undetect` (→ `0.0`, valid confirmed-dry) at the per-slot value
level. This component extends that distinction into the *aggregate coverage counter*
(new — today's coverage counting is purely file-count/temporal, with no per-pixel
concept at all): a pixel's coverage increments only on `undetect` or a real rate value,
never on `nodata`. Getting this backwards in either direction reintroduces a real bug:
treating `undetect` as missing would make genuinely dry regions look under-covered;
treating `nodata` as a real zero would reintroduce the exact class of bug this whole
migration exists to fix.

QIND (ODIM's standard `[0,1]` quality-indicator dataset) is read and carried through
as optional enrichment metadata when a file has it. The schema supports it; a specific
product instance is not guaranteed to include it — pipeline behavior must be identical
whether or not QIND is present. Never a hard requirement, never silently required for
correctness.

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
