# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

shroom-fm predicts where to forage for mushrooms in Estonian forests. It scores forest
stands (`eraldis`) from the state Metsaregister (Estonian Forest Registry) on habitat
suitability for specific species (chanterelles, spruce milk caps / `kuuseriisikas`, etc.),
then layers recent weather on top to produce a current, ranked shortlist of places worth
scouting — instead of manually clicking around the Metsaregister web map.

**Status: MVP steps 1-8 done, plus `FruitingScore`.** `src/shroom_fm/` holds `wfs.py` (WFS capabilities client),
`config.py` (home location loading), `eraldis.py` (server-side CQL annulus download via
`fetch_eraldis_annulus`), `cql.py` (shared `estonian_grid_point`/`annulus_filter` helpers
that build the `DWITHIN`/`BEYOND` CQL_FILTER string used by both the eraldis and roads
annulus fetches), `enrich.py` (joins tree composition from `eraldis_element` and resolves
`kasvukoht`/`puuliik` classifier labels), `adjacency.py` (computes which stands are
meaningfully adjacent — `touching` or `near_gap`), `ecotone.py` (scores every adjacent pair
by species composition contrast, kasvukoht site-type/moisture contrast, development-class
age contrast, and drainage change — all continuous/unfiltered — and buffers the boundary
into a scoutable microtype polygon), `habitat.py` (per-species `StandHabitatScore` for stand
interiors and `EcotoneScore` for adjacent-pair boundaries, for the five target species —
kitsemampel, chanterelle, aspen bolete, birch bolete, porcini — from host tree composition
and kasvukoht site-type suitability, kept as two distinct scores rather than one combined
`HabitatScore`), `roads.py` (ETAK road/barrier WFS fetch, `car_class` classification of road
segments, and barrier-snap exclusion of segments near a permanently-closed barrier),
`access.py` (per-eraldis `AccessScore` from nearest-road distances, additive-only onto
`data/eraldis.geojson`), and `scout.py` (`ScoutScore` v1 — joins each ecotone to its two
stands' `AccessScore`, taking `access_modifier = max(access_score_a, access_score_b)` and
splitting candidates per species into a `ranked` tier, `scout_score = ecotone_score ×
access_modifier × fruiting_modifier`, and a `remote_high_value` tier for ecologically-strong candidates the v1
access distance-proxy couldn't confirm a nearby road for — never a fabricated `0` or floor,
see `docs/superpowers/specs/2026-08-17-scout-candidates-export-design.md`). All scripts are
runnable — see "Running the full pipeline" below for the exact command sequence and
dependency order. The road-access piece of the Access/Eligibility layer has landed as
`AccessScore` (see `docs/superpowers/specs/2026-08-17-road-access-design.md`) — additive-only
onto `data/eraldis.geojson`, never modifying `StandHabitatScore`/`EcotoneScore`. MVP step 8
(export top N → GeoJSON) has landed as `ScoutScore` v1 + `scripts/export_scout_candidates.py`
→ `data/scout_candidates.geojson`. `FruitingScore` (see "FruitingScore (weather-driven
scoring)" below) is now real: `src/shroom_fm/fruiting.py` combines the weather-refresh
features into a per-species, per-date `SeasonPrior × MoistureTrigger × TemperatureModifier
× PersistenceModifier` score, exported via `scripts/score_fruiting.py` (per-stand) and
`scripts/score_ecotone_fruiting.py` (per-ecotone, averaging both adjacent stands'
`fruiting_score_*`), and `ScoutScore` v1 now multiplies in a third `fruiting_modifier`
factor (`scout_score = ecotone_score × access_modifier × fruiting_modifier`) alongside a
`MISSING_FRUITING_DATA` exclusion reason and a run-level `MIN_SCOUT_WEATHER_COVERAGE`
guard — see that section for details. A geography-only grouping layer sitting between raw
`eraldis` and all of the above — `forest_block` → `macrocluster` (see "Macroclustering"
below) — has also landed as code (`src/shroom_fm/forest_block.py`,
`src/shroom_fm/macrocluster.py`) and is wired into `main.py`. **Real-scale verification
against the full 262,054-stand dataset on 2026-08-20 found `scripts/compute_forest_blocks.py`
real and working** (11,658 forest blocks, 2m8.7s). `scripts/compute_macroclusters.py`
initially did not complete at real scale (its hand-written connectivity-constrained
complete-linkage merge was an O(n⁴)-class blowup on the single, all-262,054-stand-spanning
super-component Estonia's real forest density produces at this layer's proximity threshold)
— **this has since been fixed** by replacing that merge loop with `scipy.cluster.hierarchy`'s
vectorized global complete-linkage clustering plus a simple post-hoc `networkx`
connectivity-split, and now completes in **6m1s real time, producing 22 macroclusters from
the 11,658 forest blocks, 0 flagged `oversized_macrocluster`**; see "Macroclustering" below
for the full before/after diagnosis. `scripts/rollup_macroclusters.py` can now run (it no
longer lacks its `data/macroclusters.geojson` input), but real-scale verification of *that*
script surfaced a separate, new problem: it OOM-kills (reproduced twice, exit 137 both
times) on this 7.7GB machine while loading `data/eraldis.geojson` (787MB),
`data/ecotones.geojson` (**3.15GB** on disk), and `data/weather_eraldis.geojson` (1.1GB) all
at once — unrelated to the macrocluster-partitioning algorithm fix (this script and its
`join_ecotone_fruiting`/`join_ecotone_access` dependencies were not touched by that fix); see
"Macroclustering" below for details. `data/macrocluster_state.geojson` has therefore still
never been produced against real data. Still deferred: personal observation history and a
landscape-mosaic diversity bonus — neither exists yet, and `ScoutScore` v1 simply omits
them from its formula rather than faking neutral placeholder values for them. This file
documents the target architecture so implementation stays consistent; update it as more of
the pipeline lands.

## Running the full pipeline

Unit tests (fast, no network): `uv run pytest tests/`

Real pipeline (hits live WFS endpoints; needs `config.toml` with home coordinates — copy
`config.example.toml` and fill in `home_lat`/`home_lon`). Steps 1-6 and step 9 are
independent of each other (either branch can run first, or in parallel); step 10 needs both
branches done; step 11 needs everything upstream; step 12 needs step 5 and step 11 done
(this is `main.py`'s real `STEPS` order — see below):

1. `uv run python scripts/download_eraldis.py` — Metsaregister stands within home radius →
   `data/eraldis.geojson` (`RADIUS_KM`/`INNER_RADIUS_KM` script constants; currently a
   70km/33km annulus)
2. `uv run python scripts/enrich_eraldis.py` — join tree composition + kasvukoht/puuliik
   labels onto `data/eraldis.geojson`
3. `uv run python scripts/compute_adjacency.py` — find adjacent stand pairs →
   `data/adjacency.geojson`
4. `uv run python scripts/compute_forest_blocks.py` — connected-component `forest_block`s
   from the adjacency graph → `data/forest_blocks.geojson`, plus a new `forest_block_id`
   column onto `data/eraldis.geojson` (needs step 3 already done). **Real and working at
   production scale** — see "Macroclustering" below.
5. `uv run python scripts/compute_macroclusters.py` — partitions `forest_block`s into
   `macrocluster`s → `data/macroclusters.geojson`, plus a new `macrocluster_id` column onto
   `data/forest_blocks.geojson`/`data/eraldis.geojson` (needs step 4 already done). **Fixed
   and verified at production scale** (6m1s real time, 22 macroclusters, 0 oversized) — see
   "Macroclustering" below.
6. `uv run python scripts/score_ecotones.py` — score boundary contrast →
   `data/ecotones.geojson`
7. `uv run python scripts/score_habitat.py` — `StandHabitatScore` per species onto
   `data/eraldis.geojson` (must run before step 8)
8. `uv run python scripts/score_ecotone_habitat.py` — `EcotoneScore` per species onto
   `data/ecotones.geojson` (depends on step 7's `stand_habitat_score_*` columns already
   existing)
9. `uv run python scripts/download_roads.py` — ETAK roads/barriers within the same home
   radius → `data/roads.geojson`/`data/barriers.geojson` (only needs home coordinates, not
   steps 1-6)
10. `uv run python scripts/score_access.py` — `AccessScore` onto `data/eraldis.geojson`
    (needs steps 1 and 9 already done)
11. `uv run python scripts/export_scout_candidates.py` — top-10-per-species `ScoutScore` v1
    shortlist → `data/scout_candidates.geojson` (needs steps 7, 8, and 10 already done, plus
    the FruitingScore steps already run against a fresh `data/weather_eraldis.geojson`;
    `main.py`'s real `STEPS` list runs `score_fruiting`/`score_ecotone_fruiting` between
    steps 10 and 11, not shown as separate numbered steps here — same as before this task)
12. `uv run python scripts/rollup_macroclusters.py` — joins today's
    `data/scout_candidates.geojson` (step 11) against `data/macroclusters.geojson` (step 5)
    for a per-macrocluster daily snapshot → `data/macrocluster_state.geojson` (needs steps 5
    and 11 already done — step 5's blocker is fixed, but **this step now OOM-kills at real
    scale for a separate, unrelated reason** — loading `data/eraldis.geojson` +
    `data/ecotones.geojson` (3.15GB) + `data/weather_eraldis.geojson` all at once exceeds
    this 7.7GB machine's memory; see "Macroclustering" below).

At real scale (262,054 stands, 82,731 road segments, 1,878 barriers within the current
33-70km/37km-wide annulus around home): steps 1 (`download_eraldis`), 2 (`enrich_eraldis`),
and 9 (`download_roads`) now fetch WFS pages/batches concurrently (6 workers, via
`concurrent_fetch.py`) with per-page/per-batch progress output printed as they complete,
instead of running silently. Measured wall-clock times from a live run against the real
Metsaregister/ETAK endpoints on 2026-08-18, at this radius: step 1 12m58s (263 eraldis
pages), step 2 8m5s (525 `eraldis_element` composition batches — previously 471.2s
sequential for only 131 batches at the old, smaller 38km/18km radius, so per-batch
throughput improved roughly 4x even though this run covers 4x the batches in about the same
total wall time), step 9 2m32s for both the roads and barriers layers (previously ~4-5min,
also at the old smaller radius, so this is faster in absolute terms despite the current
radius yielding more roads/barriers than before). Step 3 is fast (local computation); step 4
(`compute_forest_blocks`) took **2m8.7s** real time at real scale (262,054 eraldis →
11,658 forest blocks, 1 flagged `oversized_block`); step 5 (`compute_macroclusters`), after
the scipy-based fix described in "Macroclustering" below, took **6m1s** real time (22
macroclusters, 0 oversized). Steps 6-8 are fast (local computation), step 10 takes well
under a minute (spatial-indexed via `geopandas.sjoin_nearest`, not a brute-force loop), step
11 is near-instant. Step 12 (`rollup_macroclusters`) is still unverified at real scale — it
OOM-kills (reproduced twice) for a separate, unrelated memory-scaling reason; see
"Macroclustering" below.

Note: the road/barrier counts above (82,731 / 1,878) are higher than an earlier plan
document's "50,008 roads, 1,564 barriers" figure — that figure was measured at the old
38km/18km radius before `download_roads.py`'s `RADIUS_KM`/`INNER_RADIUS_KM` were widened to
70km/33km in the same change that widened `download_eraldis.py`'s, and was never
re-verified against the wider radius. The 82,731/1,878 counts here are live-verified against
the current script constants.

**Known real-data quirks** (found only via live verification against real Metsaregister
data, not visible from synthetic test fixtures):
- ETAK's WFS (`etak:e_501_tee_j` and presumably other ETAK layers) behaves differently from
  Metsaregister's WFS in two ways, both confirmed live 2026-08-17: (1) it only allows
  `srsName=EPSG:3301` for output geometry on this layer — `EPSG:4326` is rejected with an
  `ows:ExceptionReport` — still true today and directly relied on in `roads.py`'s
  `fetch_layer_annulus` via its `_ETAK_OUTPUT_CRS` constant; (2) its `bbox` GetFeature
  parameter enforces the EPSG:4326 URN's strict authority-defined axis order (**lat, lon** —
  not the "GIS convention" lon,lat that Metsaregister's WFS accepts), confirmed by testing
  both orders directly against the live server. Separately, `owslib`'s
  `WebFeatureService.getfeature()` silently re-serializes any bbox tuple it's given back into
  lon,lat order on the wire regardless of the order passed in — confirmed by inspecting the
  actual outgoing request URL — so an axis-order fix cannot be applied through `owslib` at
  all for this endpoint. (Historical: (2) and the owslib bypass applied to the old bbox-based
  `fetch_layer_bbox`, since removed — no longer relevant since both the eraldis and roads
  fetches now use `CQL_FILTER` instead of the `bbox` parameter. Kept here since the
  underlying axis-order/owslib facts about ETAK's WFS remain true and could matter again if a
  bbox-based fetch is ever needed there.)
- A composition entry's `osakaal` (share) can be `NaN` for understory/undergrowth layers
  (`rinne_kood: "A"`) — the registry doesn't measure a stocking share for undergrowth. Around
  15% of real stands have at least one such entry. Any code summing `osakaal` across a
  stand's composition must filter out NaN entries first, or the sum (and everything derived
  from it) becomes NaN. `ecotone.py`'s `composition_fractions` does this; `enrich.py`'s
  `compute_species_shares` does not yet filter NaN and is a latent risk if a future
  download/radius happens to include an affected stand with a NaN entry on one of the four
  target species (not observed in current data, but not structurally prevented either).
- A small fraction of stands (~0.4% in one real sample) have an entirely empty `composition`
  list (no matching `eraldis_element` rows at all). Downstream code must treat "no
  composition data" as `None`/`NaN`, not silently compute a plausible-looking but fabricated
  value from an empty/all-zero input.
- `ecotone.py`'s `kasvukoht_group_changed` column (nullable bool: `True`/`False`/`None` for
  unmapped kasvukoht codes) round-trips through GeoJSON as the **string** `"True"`/`"False"`,
  not a real bool — confirmed by reading `data/ecotones.geojson` back with `geopandas`. This
  is a `fiona`/GeoDataFrame GeoJSON quirk affecting mixed bool+`None` columns specifically:
  `drainage_changed` (always a real bool, never `None`) round-trips fine and stays `bool`.
  Any code reading `kasvukoht_group_changed` back from the saved GeoJSON must compare against
  the strings `"True"`/`"False"` (or cast explicitly), not `is True`/`is False`.

## Weather refresh (standalone, not part of the 9-step pipeline)

`uv run python scripts/refresh_weather.py` ingests KAIA radar precipitation composites
(5-minute HDF5/ODIM files, rolling 14-day cache in `data/radar_cache/`) and MET Norway's
MEPS/MET-Nordic hourly analysis grid (rolling 3-day window, no local cache — refetched
each run) to produce `data/weather_eraldis.geojson`: per-`eraldis` `rain_3d_mm`/
`rain_7d_mm`/`rain_14d_mm`, the non-overlapping `rain_0_3d_mm`/`rain_3_7d_mm`/
`rain_7_14d_mm` bins derived from those rolling sums, `hours_since_any_rain` (renamed
from `hours_since_rain`), `hours_since_significant_rain`/`hours_since_strong_rain` and
`last_significant_event_mm`/`last_strong_event_mm` (event-based, ≥5mm/≥10mm cumulative
rain events with a 6-hour dry-gap boundary), `max_24h_rain_14d` (rolling 24-hour-max, not
the flat 14-day total), `wet_hours_72h`, `temp_mean_3d`/`temp_night_mean_3d`,
`rh_mean_3d`/`rh_night_mean_3d`, plus `as_of`/`weather_data_coverage`/
`weather_data_quality` columns. Unlike the rest of the pipeline this is time-varying and
meant to be re-run on demand (e.g. before a scouting trip), not as part of `main.py`'s
9-step sequence. `FruitingScore` (combining these features into a per-species/date score
and wiring into `ScoutScore`) is now real — see "FruitingScore (weather-driven scoring)"
below.

**Real first-ever (cold-start) run, verified live against production KAIA/MET Norway
servers on 2026-08-18:** a warm/mostly-populated-cache invocation of
`scripts/refresh_weather.py` itself completes in 3-4 minutes (measured: 3m24s and 4m4s
across two runs) and produces `data/weather_eraldis.geojson` with all 262,054 real
`eraldis` stands scored, `weather_data_quality: {'complete': 262054}`. But getting the
`data/radar_cache/` warm enough to reach that "complete" label for a brand-new deployment
took **well over 30 minutes of real wall-clock time** — likely multiple hours in the worst
case — not because of the per-file bbox-slicing (which works as designed and keeps each
file's own processing cheap), but because KAIA's document-query and file-download
endpoints both enforce a real, fairly aggressive rate limit (repeated live `HTTP 429 Too
Many Requests`) once a client requests on the order of ~2000+ documents/files in a short
burst. Two real bugs surfaced and were fixed live during this verification (both already
committed, not introduced by this step): (1) the real KAIA API never returns
`nextBookmark: null` to signal pagination end — it echoes the same non-null bookmark
forever with an empty `documents` list once exhausted, contradicting the null-bookmark
termination Task 1 originally shipped and regression-tested against a synthetic mock; (2)
`download_radar_composite` now retries `HTTP 429` locally with exponential backoff (the
shared `retry.py` deliberately excludes 4xx by design) and `MAX_WORKERS` was reduced from
6 to 3 to reduce how often the limit is hit in the first place. Even with both fixes, a
genuinely cold cache (this environment's very first run, starting from zero cached files)
needed several separate `fetch_new_radar_composites` passes and manual pauses across
multiple hours of real elapsed time to climb from 0 to ~3,420 of the ~4,110 documents in
the 14-day window (84.5% overall raw coverage) before a run's `weather_data_coverage`
(currently 0.849) cleared the `MIN_RADAR_COVERAGE = 0.7` threshold used for the `quality`
label.

**Known real-data quirk found via this live verification, not caught by unit tests:**
even at 84.5% *overall* 14-day coverage, the *most recent* 1-3 days remained far sparser
(12-20%) than the aggregate figure suggests, because KAIA returns documents in
chronological order and a rate-limited, worker-pool-bounded download loop naturally
finishes older (earlier-submitted) files before newer ones — so recency lags overall
completeness during a cold backfill. In this run's real output, `rain_3d_mm` and
`rain_7d_mm` came back as **exactly 0.0 for all 262,054 stands** (zero variance) while
`rain_14d_mm` (drawing on the better-sampled older two-thirds of the window) was nonzero
for ~3.5% of stands (up to 3.24mm). `temp_mean_3d`/`temp_night_mean_3d` (15-17°C) and
`rh_mean_3d`/`rh_night_mean_3d` (70-85%) both looked physically plausible and varied
normally across stands — only the *rain* features were affected, since they alone depend
on the most-recent, most rate-limited portion of the radar window. This uniform-zero rain
result is honestly ambiguous: it may reflect a genuine short dry spell in this specific
2026-08 window, or it may still be an artifact of incomplete recent-day radar sampling —
the current data does not let us tell these apart with confidence, and this should be
re-checked once a routine (non-cold-start) run has a fully warm cache. `weather_data_quality`
does not currently distinguish "overall coverage is fine but recency is poor" from true
completeness — a possible future follow-up would be a separate recency-specific coverage
check (e.g. over just the trailing 3-day window) rather than relying solely on the
14-day-aggregate `MIN_RADAR_COVERAGE` threshold.

**Resolved by a later, fully-warm run (2026-08-19/20, verified live during FruitingScore's
Task 7):** a routine (non-cold-start) `refresh_weather.py` run reached
`weather_data_quality: {'complete': 262054}` with `weather_data_coverage` slightly over 1.0
(1.0074 — the same harmless KAIA-publish-cadence-faster-than-nominal artifact noted
elsewhere) — genuinely full coverage, not the earlier run's 84.5%. `rain_3d_mm`/
`rain_7d_mm` (and the newer `rain_0_3d_mm`/`rain_3_7d_mm`) were still ~0 for effectively
all 262,054 stands (max `rain_14d_mm`/`max_24h_rain_14d` across the entire annulus was
0.0086mm), and `hours_since_significant_rain`/`hours_since_strong_rain` were `NaN` (never
triggered) for every stand. With full radar coverage this time, the earlier ambiguity is
resolved: this is a real, extended dry spell in the covered region, not a cold-start
recency-sampling artifact. `fruiting_score_*` for every species came back correspondingly
tiny (max ~0.00016, mean ~1e-7) even though `fruiting_season_prior_*` was at its 1.0 peak
and `fruiting_temperature_modifier` was a perfect 1.0 — `MoistureTrigger`'s near-zero value
correctly suppressed the otherwise-ideal season/temperature conditions, which is the
intended behavior of the multiplicative formula, not a bug.

## FruitingScore (weather-driven scoring)

`src/shroom_fm/fruiting.py` combines the weather-refresh features above into a per-species,
per-date score: `FruitingScore = SeasonPrior × MoistureTrigger × TemperatureModifier ×
PersistenceModifier`. `MoistureTrigger` is a species-weighted sum of a smooth exponential
saturation response (`1 - exp(-mm/scale_mm)`, not a naive linear cap) over the three
non-overlapping rain bins. `TemperatureModifier` multiplies an independent base
temperature curve (floor 0.4 outside [2,26]°C, plateau at 1.0 across [8,18]°C) by a
separate soft frost-guard factor derived from night temperature. `PersistenceModifier`
floors at 0.6 plus a weighted blend of night humidity, `wet_hours_72h` saturation, and
exponential recency-of-significant-rain decay (72h half-life) — a missing/never-happened
`hours_since_significant_rain` is treated as "no qualifying event" (contributes 0 to that
sub-score), not as missing data for the whole modifier. `SeasonPrior` is a per-species
piecewise-linear curve over hand-picked Estonian anchor dates (RMK/Finnish-food-agency/
personal-diary sourced), flat-extrapolated (never decaying to zero) outside its knot range.
**All numeric constants in this module — rain-response scales, moisture weights,
temperature/frost breakpoints, persistence weights, the `SEASON_PRIORS` knot tables — are
v0 engineering priors, not measured mycological constants**, pending calibration against
real logged observations (same discipline as `habitat.py`'s `HOST_PROFILES`/
`SITE_TYPE_PROFILES`). `FruitingScore` is `None` (never a fabricated neutral value) if
`MoistureTrigger`, `TemperatureModifier`, or `PersistenceModifier` is `None`; `SeasonPrior`
is a pure calendar function and never returns `None`.

Two pipeline scripts apply this: `scripts/score_fruiting.py` adds `fruiting_score_{species}`
(plus `fruiting_moisture_score_{species}`/`fruiting_season_prior_{species}` and the two
species-shared `fruiting_temperature_modifier`/`fruiting_persistence_modifier` columns)
onto `data/weather_eraldis.geojson`; `scripts/score_ecotone_fruiting.py` then averages each
ecotone's two adjacent stands' `fruiting_score_{species}` into `fruiting_modifier_{species}`
onto `data/ecotones.geojson` — `None` (never a one-sided fabricated average) if either
stand's score is missing or the stand id isn't present at all. `ScoutScore` v1
(`src/shroom_fm/scout.py`) multiplies in this third factor: `scout_score = ecotone_score ×
access_modifier × fruiting_modifier`. A candidate that's access-eligible but lacks usable
fruiting data lands in the existing `remote_high_value` tier (no new third tier) tagged
`exclusion_reason = "MISSING_FRUITING_DATA"`; if a candidate fails both access-eligibility
and fruiting-data-availability at once, the existing `REMOTE_BY_V1_ACCESS_PROXY` reason
takes precedence. `scripts/export_scout_candidates.py` also gates each species on a
run-level `MIN_SCOUT_WEATHER_COVERAGE = 0.90`: if too small a fraction of a species'
ecologically-and-access-eligible candidates have real fruiting data, that species' ranking
is refused (loud diagnostic printed, species skipped, other species still processed
normally) rather than publishing a misleadingly small/unrepresentative Top-N; if literally
every species is refused, the script exits non-zero without writing
`data/scout_candidates.geojson` at all.

**Real pipeline run, verified live against the full 2026-08-18 dataset (262,054 stands,
493,499 ecotone pairs) on 2026-08-20**, immediately after a fresh, fully-warm
`refresh_weather.py` run (see the resolved dry-spell note above):
`scripts/score_fruiting.py` took **2m44s**, `scripts/score_ecotone_fruiting.py` took
**4m55s** — the clear outlier of the three FruitingScore scripts, roughly proportional to
its ~1.9× row count (493,499 ecotone pairs vs. 262,054 stands) rather than a
disproportionate blowup, but both `score_stands`/`join_ecotone_fruiting` use row-wise
`pandas.DataFrame.iterrows()` rather than a vectorized approach — unlike `score_access.py`,
which uses `geopandas.sjoin_nearest` — and this is the one part of the FruitingScore
pipeline that could benefit from vectorization if it needs to get materially faster.
`scripts/export_scout_candidates.py` took **2m41s** and produced a full ranking for all 5
species (100 total candidates: 10 `ranked` + 10 `remote_high_value` per species) — the
`MIN_SCOUT_WEATHER_COVERAGE` guard did not trigger for any species, consistent with the
100% weather coverage that run measured; no `MISSING_FRUITING_DATA` rows appeared in this
run's output for the same reason (every stand had a real, if extremely small, fruiting
score). Spot-checked output rows confirm `scout_score` correctly equals the product of its
three factors (e.g. one real row: `ecotone_score=1.030809 × access_modifier=1.0 ×
fruiting_score=0.000113 ≈ scout_score=0.000117`).

## Macroclustering (`forest_block` → `macrocluster` grouping)

`src/shroom_fm/forest_block.py` and `src/shroom_fm/macrocluster.py` add a new grouping
layer that sits between raw `eraldis` and everything scored above it: `forest_block`
(a connected component of the existing `data/adjacency.geojson` `touching`/`near_gap`
graph — i.e. "one physically contiguous forest massif") and `macrocluster` (a group of
nearby `forest_block`s small enough to plausibly scout in one outing). **Critical design
constraint: membership in a `forest_block` or `macrocluster` never depends on any score** —
not `HabitatScore`, not `AccessScore`, not `FruitingScore`, not `ScoutScore`. It's a pure
geography/adjacency grouping, computed once from `data/eraldis.geojson`/
`data/adjacency.geojson` and stable across days, so a macrocluster's identity doesn't
reshuffle after every rain event — a forager can meaningfully ask "how did this region look
on Aug 18 vs Aug 23" the way they couldn't if the grouping itself were score-driven. Daily
scores (today's `data/scout_candidates.geojson`) are joined onto this stable base separately
by `scripts/rollup_macroclusters.py`, never folded back into the base grouping itself.

Pipeline position (see "Running the full pipeline" above and `main.py`'s real `STEPS` list):
`scripts/compute_forest_blocks.py` runs right after `compute_adjacency` (step 4) and
`scripts/compute_macroclusters.py` right after that (step 5), both before any scoring step
— `forest_block_id`/`macrocluster_id` land as new additive columns on `data/eraldis.geojson`
the same way `access_score` etc. do. `scripts/rollup_macroclusters.py` runs last (step 12,
after `export_scout_candidates`), joining today's `data/scout_candidates.geojson` against
`data/macroclusters.geojson` to produce `data/macrocluster_state.geojson` — a same-day
snapshot (`as_of` + per-species `today_ranked_count_*`/`today_top_score_*`/
`today_top3_mean_score_*`/`today_top_target_id_*`/`today_weather_coverage_*` and a
diagnostic `cross_macrocluster_ecotone_count`), kept as a separate file from
`data/macroclusters.geojson` rather than mutating the stable base, matching how
`data/weather_eraldis.geojson` is already a "latest snapshot only" file re-generated each
run.

**v0 engineering priors** (`src/shroom_fm/macrocluster.py`/`forest_block.py` constants) —
named as geometric proxies for a future real road-network travel-time graph, not asserted
travel-time facts, same discipline as this project's other v0 priors
(`ACCESS_DISTANCE_CAP_M`, `MAX_GAP_M`, `FruitingScore`'s rain-response scales, etc.):
`BLOCK_NEIGHBOR_PROXY_M = 8_000` (straight-line boundary-to-boundary distance cap for a
`forest_block`-to-`forest_block` proximity-graph edge), `MACROCLUSTER_MAX_EXTENT_M =
35_000` (hard cap on a macrocluster's convex-hull diameter, enforced by construction),
`MACROCLUSTER_TARGET_EXTENT_M = 25_000` (soft/diagnostic-only threshold — also what
`oversized_block` is measured against), `TARGET_BLOCK_COUNT = (5, 15)` (soft/diagnostic-only
per-macrocluster block-count band).

**Round 1 (2026-08-20, since fixed): `compute_forest_blocks.py` succeeded,
`compute_macroclusters.py` did not complete.**

`scripts/compute_forest_blocks.py` is real and working at production scale: **2m8.7s**
real time, producing `11,658 forest block`s from `262,054 eraldis` with **1 flagged
`oversized_block`** (`data/forest_blocks.geojson`), plus the new `forest_block_id` column
on `data/eraldis.geojson`.

`scripts/compute_macroclusters.py`, run immediately after against that real
`data/forest_blocks.geojson`, **did not finish on that first attempt** — it was still
running, pinned at 100% of one CPU core, after 42 minutes, then died with no completion
output and no traceback (consistent with the OS OOM-killer: the process's own RSS had
climbed to ~49-54% of this box's 7.7GB total RAM, `dmesg` showed active OOM-killer runs in
this window, and system memory/swap fully recovered the instant the process disappeared —
`real 42m5.6s` / `user 41m54.6s` from the shell's own `time` measurement of the dead
child). No `data/macroclusters.geojson` was ever written, so `data/eraldis.geojson`/
`data/forest_blocks.geojson` were left exactly as `compute_forest_blocks.py` produced them
— no partial/corrupt output. `scripts/rollup_macroclusters.py` could not be run either
(it requires `data/macroclusters.geojson`), so the real-scale spot-checks this task was
originally supposed to perform could not be obtained.

**Root cause, confirmed via a targeted diagnostic (not a guess):** reproducing
`build_block_proximity_graph`'s exact edge-construction logic with vectorized
numpy/`shapely.distance` instead of `geopandas`' `sjoin` + `DataFrame.iterrows()` (so it
actually finishes — ~100s total) showed the real `data/forest_blocks.geojson` collapses
into **exactly one super-component containing all 11,658 forest blocks**, with 2,209,103
edges in the block-proximity graph at `BLOCK_NEIGHBOR_PROXY_M = 8,000m`. This isn't a bug
in the graph-construction code — Estonia is genuinely forested densely enough (262,054
stands already consolidate into "only" 11,658 `near_gap`/`touching`-adjacency forest
blocks, ~22.5 eraldis/block on average) that an 8km proximity threshold transitively
connects the entire 70km-radius annulus into one graph component, not into many small
independent ones. That single super-component was exactly the worst case for the
then-current `_complete_linkage_merge`: each `while` iteration re-scanned **all** O(k²)
remaining candidate cluster pairs from scratch with no incremental caching, and both
`connectivity_adjacent` and `complete_linkage_distance` cost `O(|cluster_a| ×
|cluster_b|)` rather than `O(1)` once clusters grow past singletons — for n≈11,658
starting from singletons this was a genuine, confirmed **O(n⁴)-class blowup**, not merely
"slow": the observed 42 minutes of CPU time did not complete even a single full pass at
this scale, and extrapolating the growth rate would have put a real finish time at many
hours to days in pure Python, not minutes. This confirmed, at real production scale, the
exact O(n⁴) performance concern two reviewers flagged during Task 4's implementation (see
the plan's Task 4 note on replacing `sklearn.cluster.AgglomerativeClustering` with a
manual connectivity-constrained complete-linkage merge) — it was not a theoretical worry.

**Round 2 (2026-08-20, the fix): replaced the hand-written O(n⁴) merge with
`scipy.cluster.hierarchy` + a post-hoc `networkx` connectivity check.**

`_complete_linkage_merge` was deleted outright. `_partition_component`
(`src/shroom_fm/macrocluster.py`) now clusters purely on real geometric (centroid)
distance using `scipy.cluster.hierarchy.linkage(method="complete")` +
`fcluster(criterion="distance")` — scipy's vectorized NN-chain algorithm, O(n²) time
rather than the old O(n⁴)-class from-scratch loop — computed **without any connectivity
restriction in the clustering step itself**, which is deliberate: it avoids reproducing
the Task 4 Round 1 defect where `sklearn.cluster.AgglomerativeClustering`'s
connectivity-constrained `linkage="complete"` silently used a hop-weight proxy instead of
true diameter distances for non-adjacent pairs. Connectivity is instead checked as a
separate, simple post-hoc step: any flat cluster scipy produces that isn't actually
`networkx`-connected (in the subgraph restricted to just that cluster's members) gets
split into its real connected sub-components. This is provably safe — splitting only
removes members from a group, so `geometry_extent_m` (convex-hull diameter) can only
shrink or stay the same after a split, never re-exceed the cap that was already checked
before the split, so no extent re-validation is needed after a connectivity split. A group
whose real `geometry_extent_m` still exceeds the cap after clustering (centroid distance
can be optimistic for large/elongated blocks) is recursively repartitioned with a shrunk
threshold, same as before.

Empirically verified before trusting the scipy swap (same discipline that caught the
Round 1 sklearn bug): a standalone script reproduced the existing test's 5-node,
9km-spaced chain scenario directly against `scipy.cluster.hierarchy.linkage`/`fcluster`
(not through `compute_macroclusters`) and confirmed, by reading the actual linkage matrix,
*why* it produces the `{0,1}`/`{2,3,4}` split at `threshold=35,000`: the algorithm's last
candidate merge — joining `{0,1}` with `{2,3,4}` — would require true complete-linkage
distance `max(dist(0,2..4))=36,000`, which exceeds the threshold, so that merge correctly
never happens. A second standalone script confirmed the post-hoc connectivity-split fires
correctly: two blocks 50m apart (well within any clustering threshold, so scipy groups
them into one flat cluster) but with **no direct graph edge** between them — even though
both belong to the same larger connected super-component via other nodes — got correctly
split back into two singleton groups by `nx.connected_components(graph.subgraph(group))`;
the same two blocks *with* a direct edge present correctly merged into one group.

All 16 `tests/test_macrocluster.py` tests and the full 247-test suite pass unchanged — no
test needed to change, confirming this is a pure algorithm-internals swap behind the same
`compute_macroclusters` interface.

**Real-scale re-verification, 2026-08-20, after the fix:** `time uv run python
scripts/compute_macroclusters.py` against the same real `data/forest_blocks.geojson`
(11,658 forest blocks, one 11,658-node super-component) completed in **real 6m1.407s**
(`user 5m51.673s`, `sys 0m11.048s`) — down from 42+ minutes and an OOM kill — printing:
`22 macroclusters from 11658 forest blocks, 0 oversized, saved to
data/macroclusters.geojson`. Spot-check of `data/macroclusters.geojson`: all 22 rows'
`geometry_extent_m` are `<= MACROCLUSTER_MAX_EXTENT_M` (35,000m; max observed
34,399.9m), 0 rows flagged `oversized_macrocluster`, `forest_block_count` sums to exactly
11,658 and `eraldis_count` sums to exactly 262,054 across the 22 rows (full accounting, no
blocks lost or double-counted). `within_target_block_count` is `False` for all 22 rows
(clusters range from 133 to 920 forest blocks each, far above the diagnostic-only
`TARGET_BLOCK_COUNT = (5, 15)` band) — expected and not a bug, since that band is
diagnostic-only (never enforced) and was calibrated for a much less densely-connected
scenario than Estonia's real forest cover turned out to produce.

**New, separate finding: `scripts/rollup_macroclusters.py` OOM-kills at real scale, for a
reason unrelated to the macrocluster-partitioning algorithm.** With
`data/macroclusters.geojson` now real, `rollup_macroclusters.py` was run against real data
for the first time. It was killed by the OS OOM-killer twice in a row (`dmesg` confirms
both: `Out of memory: Killed process ... python3 ... anon-rss:5776332kB` /
`anon-rss:5782464kB`, exit code 137 both times, no output file produced either time). The
likely cause: this script loads `data/eraldis.geojson` (787MB on disk), the real
`data/ecotones.geojson` (**3.15GB** on disk — far larger than the other pipeline files),
and `data/weather_eraldis.geojson` (1.1GB) fully into memory via `gpd.read_file()` before
doing any filtering, on a machine with only 7.7GB RAM + 2GB swap. This is **not** a
consequence of the Round 2 clustering fix — `rollup_macroclusters.py`,
`rollup_daily_state`, `join_ecotone_fruiting`, and `join_ecotone_access` were not touched
by that fix — it's a pre-existing memory-scaling limitation of the rollup step that this
task happened to be the first to exercise against real data end-to-end. It remains
unfixed and undiagnosed beyond this: `data/macrocluster_state.geojson` has never been
produced against real data, so the `cross_macrocluster_ecotone_count` real count and the
`data/macrocluster_state.geojson`-vs-`data/scout_candidates.geojson` spot-check remain
outstanding. A real fix would likely need `rollup_macroclusters.py` (or
`join_ecotone_fruiting`/`join_ecotone_access`) to read only the columns/rows it needs
rather than full GeoDataFrames, or to run on a machine with more memory.

## Planned architecture

Data pipeline (this is the long-term target shape; see "Running the full pipeline" above
for the actual current script sequence — `habitat`/`ecotone`/`access`/`FruitingScore`
scores and the `ScoutScore` v1 export are all real today, only personal observation
history remains unbuilt, and this diagram omits the ETAK roads/barriers WFS that `access`
score now depends on alongside Metsaregister). The `forest_block`/`macrocluster` grouping
layer (see "Macroclustering" above) sits beside this score chain, not inside it — it's
computed straight off `eraldis`/adjacency with no score input, and only rejoins the chain
at the very end when `rollup_macroclusters.py` groups today's `ScoutScore` export by
`macrocluster_id`. `compute_forest_blocks.py`'s and `compute_macroclusters.py`'s halves of
that layer are both real and verified at production scale now; `rollup_macroclusters.py`
itself (the final join step) is not yet usable at real scale for a separate reason — see
"Macroclustering" above for the confirmed real-scale OOM finding:

```
Metsaregister WFS (GeoServer OWS)
      │
      ├── eraldis            geometry + stand metadata (species mix, age, ownership)
      ├── eraldis_element    tree composition detail, joined via eraldis.id
      └── classifiers        kasvukohatüüp, puuliik, etc. lookup tables
              │
              ▼
        GeoPandas (feature engineering)
              │
      ┌───────┼─────────┐        forest_block → macrocluster
      │       │         │        (geography only, no scores —
   habitat  ecotone   access      both steps real and verified
    score    score     score      at real scale; rollup_macro-
      │       │         │         clusters.py OOMs at real scale)
      └───────┼─────────┘                      │
              ▼                                │
        HabitatScore (static, recomputed rarely)
              │                                │
              + FruitingScore(t)  — rainfall/temp history, recency of rain
              + observation history (your own logged finds)
              ▼                                │
        ScoutScore                             │
              │                                │
              ▼                                │
        GeoJSON export ─────────────────────────┴──▶ rollup_macroclusters.py
              │                                       → daily macrocluster state
              ▼
        QGIS / map viewer
```

Longer-term target stack: **PostGIS** (stores `eraldis` geometry, computed scores, weather
history, personal find log) + **Python/GeoPandas** (WFS ingestion, feature engineering,
scoring) + a **React** frontend for browsing scored areas, with QGIS as an interim/backup
viewer. Model the scoring initially as hand-picked weighted heuristics per species; once a
season of personal observations (`date, lat, lon, species, kg, minutes, microtype,
fresh/old`) accumulates, revisit as a trained model (e.g. LightGBM) predicting
`P(productive | forest, weather, season)`.

### MVP build order (CLI, no DB yet)

1. Download Metsaregister polygons via WFS
2. Restrict to ≤80 km from home
3. Join tree composition (`eraldis_element`)
4. Join `kasvukohatüüp` (site/habitat type)
5. Calculate neighbouring stands (spatial adjacency)
6. Detect interesting ecotones (species-boundary transitions, e.g. pine↔spruce)
7. Calculate `HabitatScore`
8. Export top N results → GeoJSON
9. View in QGIS (or a lighter-weight viewer if one turns out to fit better)

Weather-driven `FruitingScore` has landed (see "FruitingScore (weather-driven scoring)"
above) now that the static habitat scoring pipeline is validated. PostGIS storage remains
deferred.

## Data source: Metsaregister WFS

- Endpoint: `https://gsavalik.envir.ee/geoserver/metsaregister/ows` — a single GeoServer OWS
  endpoint; request type is chosen via the `service=WFS` or `service=WMS` query param.
- **Use WFS only for data extraction.** It returns actual geometry + attributes, so
  GeoPandas can load it directly (`gpd.read_file(WFS_URL)`), join, filter, and score. WMS
  returns pre-rendered map tiles — useful only as a visual reference layer in QGIS, not for
  computing anything.
- Key layers: `metsaregister:eraldis` (stand geometry/metadata), `metsaregister:eraldis_element`
  (tree species composition, joins to `eraldis` via `eraldis.id = eraldis_element.eraldis_id`),
  plus classifier lookup layers `metsaregister:kl_kasvukoht` (kasvukohatüüp) and
  `metsaregister:kl_puuliik` (puuliik).
- Do **not** use the `AKS` WFS shown alongside it in Keskkonnaagentuur's service listing —
  that's the address/place-name registry (`Aadressandmete ja kohanimede süsteem`), unrelated
  to forest data.
- Data is published as open data under CC-BY 4.0.
- Real layer names have been confirmed via a live `GetCapabilities` call (23 layers as of
  2026-08-15, recorded in `data/wfs_capabilities.json`) — the names above are verified, not
  assumed. Re-run `scripts/get_capabilities.py` and diff the output if the service changes.
- EELIS (Keskkonnaagentuur) also exposes public WMS/WFS and may be a useful supplementary
  source later (e.g. protected areas, hydrology) but is not part of the core pipeline.
- `metsaregister:eraldis`'s real attribute columns (confirmed via a live `GetFeature` call,
  2026-08-16) include: `kvartali_nr`, `eraldise_nr` (the `Kvartal`/`Eraldis` identifiers),
  `peapuuliik_kood` (`Peamine puuliik`), `kasvukoht_kood` (`Kasvukoht`), `arengukl_kood`
  (`Arenguklass`), plus `pindala` (area), `korgus` (height), `keskm_vanus` (mean age),
  `omandivorm_kood` (ownership form), and others. `scripts/download_eraldis.py` downloads
  this layer already joined with these attributes — no separate attribute fetch needed for
  the fields already present here.
- `metsaregister:eraldis_element` (confirmed live, 2026-08-16) has **no geometry** — it can't
  be bbox-filtered, only filtered by `eraldis_id` (GeoServer's `CQL_FILTER=eraldis_id IN
  (...)` vendor extension works; `owslib.getfeature()` has no CQL support, so this fetch uses
  `requests` directly). Real columns: `eraldis_id`, `rinne_kood` (canopy layer), `puuliik_kood`
  (species), `osakaal` (share), `vanus`, `korgus`, `enamus`, `sunniaasta`, `paritolu`,
  `diameeter`, `rinnaspindala`, `tagavara`, `arv` — multiple rows per stand.
- `kl_puuliik`/`kl_kasvukoht` classifiers are small, non-spatial, `{kood, kirjeldus}` shape
  (confirmed live: `kl_puuliik` has 30 rows, e.g. `MA`→`mänd` (pine), `KU`→`kuusk` (spruce),
  `KS`→`kask` (birch), `HB`→`haab` (aspen)). `scripts/enrich_eraldis.py` resolves both onto
  `eraldis` as `peapuuliik_kirjeldus`/`kasvukoht_kirjeldus` (real example value seen live:
  `kasvukoht_kirjeldus = "jänesekapsa-mustika"`), and adds `pine_share`/`spruce_share`/
  `birch_share`/`aspen_share` columns computed from composition for this project's four
  target host-tree species.

## Domain glossary (Estonian forestry terms used throughout the data and code)

- **`Metsaeraldised`** — the Metsaregister layer/dataset of forest stands.
- **`Eraldis`** — a stand: the core scoring unit. A single polygon that Metsaregister itself
  models as homogeneous in species composition, age, height, and site type — this is why
  it's the right granularity for scoring, finer than `Kvartal`.
- **`Kvartal`** — forest quarter/compartment; treat as a coarser grouping identifier, not an
  analysis unit.
- **`Peamine puuliik`** — main tree species of the stand.
- **`Kasvukoht` / `Kasvukohatüüp`** — forest site/habitat type (soil moisture, fertility
  class). Combined with `Peamine puuliik`, this carries much more signal than species alone
  — e.g. pine correlates with poorer/drier sandy, peaty, or some very wet sites, spruce with
  different conditions.
- **`Arenguklass`** — development/age class of the stand.
- **Ecotone** — a boundary between two adjacent stands of different composition (e.g.
  pine↔spruce, forest↔bog, old↔young stand). These transition zones, not stand interiors,
  are often the most interesting scouting targets and can be generated automatically by
  intersecting adjacent stand boundaries and buffering the resulting line (~30–50 m).

## Species heuristics (informing the scoring model)

The scoring model targets only high-value edible species: **kitsemampel** (gypsy
mushroom), **chanterelles**, **aspen boletes**, **birch boletes**, and **porcini**. Milk
caps, russulas, and other lower-priority edible mushrooms are not included in the target
score.

- **Kitsemampel** (`Cortinarius caperatus`): strongly favor sparse pine stands and
  pine-dominated forests approaching boggy or paludifying conditions. Particularly
  promising candidates are open pine forests, pine/bog transitions, and mosaics containing
  both relatively dry pine ground and wetter depressions. Dense closed spruce forest and
  deciduous-dominated stands should receive little or no species-specific score. Because
  kitsemampel may fruit abundantly when conditions are suitable, contiguous areas of
  suitable habitat are valuable in addition to individual ecotones.
- **Chanterelles** (`Cantharellus cibarius`): favor pine and pine-mixed forests, while
  retaining mixed coniferous/deciduous stands as viable habitat. Useful candidate
  structures include `pine dominant → pine/birch mixed → spruce inclusion`, especially
  where several such stand types can be sampled along one route. Stand boundaries should
  receive an exploration bonus rather than being treated as an intrinsic biological
  requirement: the purpose is to sample several tree/moisture combinations efficiently.
  Chanterelles commonly occur in groups, so previous positive observations should strongly
  increase the local historical score.
- **Aspen boletes** (`Leccinum` spp., especially haavapuravik): strongly favor stands
  containing **aspen**, including deciduous and mixed forests. Aspen does not need to be
  the dominant tree: an otherwise mixed stand with a substantial aspen component should
  remain a strong candidate. Useful structures include `aspen stand → mixed deciduous
  forest`, `aspen → birch`, and `aspen-containing mixed forest → forest edge`. Birch and
  willow presence may contribute a weaker positive signal because related red-capped
  `Leccinum` can associate with these hosts as well.
- **Birch boletes** (`Leccinum scabrum` group): make **birch presence/share** the primary
  species-specific feature. Favor birch stands, birch-dominated mixed forest, and
  coniferous stands with a meaningful birch component. Do not require dry forest:
  birch-associated boletes also occur in moist forests and around bog margins, so `birch
  forest → wetter depression/bog edge` should remain a valid candidate rather than being
  filtered out. For this group, tree-composition data is more important than the nominal
  dominant-tree class alone.
- **Porcini / king boletes** (`Boletus edulis` group): in Estonia, prioritize **spruce and
  spruce-mixed forest** for the common `Boletus edulis`, but do not make spruce mandatory.
  Birch and pine are also valid mycorrhizal hosts, while pine-associated porcini are
  particularly relevant in sandy pine forests. High-value candidate structures therefore
  include `spruce dominant → spruce/birch mixed`, `spruce → pine`, and mixed stands
  containing several suitable host species. The model should score host-tree composition
  rather than encode a single rigid "porcini forest" type. If desired later,
  pine-associated and deciduous-associated porcini can be represented as separate habitat
  profiles rather than forcing all `Boletus` into one heuristic.
- **Multi-species / general high-value foraging**: prefer forest mosaics that contain
  several target habitats within a short walking distance, for example `pine | spruce |
  birch | aspen | wet depression | forest road`. Such areas should receive an additional
  **diversity/exploration score** because one short scouting loop can test habitat for
  several target species. This bonus should be separate from the individual species
  scores, so a highly suitable single-species stand is not penalized merely for being
  homogeneous.
- **Orthophoto sanity check**: discard or heavily penalize candidates showing a recent
  large clear-cut, farmland or regrowing field instead of established forest, extremely
  dense young growth, clearly unusable access, or a large uniform plantation where a
  structurally richer candidate is available nearby. This is initially a manual filter and
  can later be encoded using land-cover, canopy, disturbance, and road-access features.
