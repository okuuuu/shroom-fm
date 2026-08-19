# FruitingScore — Design

## Problem

`ScoutScore` v0 (`ecotone_score × access_modifier`) is purely static — it ranks *where*
is ecologically good and reachable, but says nothing about *when* to go. `FruitingScore`
is the weather-driven layer CLAUDE.md's planned architecture always intended to add once
both the static habitat pipeline and weather ingestion were validated (both landed this
session). This spec covers `FruitingScore` itself and its integration into `ScoutScore`
v1 — not personal observation history or the PlutoF/eElurikkus regional-pulse signal,
both still separate, later sub-projects.

## Architecture

```
data/weather_eraldis.geojson (rain_3d/7d/14d_mm, hours_since_any_rain, wet_hours_72h,
                               temp_mean_3d/night, rh_mean_3d/night, per-window coverage)
        |
        | scripts/score_fruiting.py  (src/shroom_fm/fruiting.py)
        v
data/weather_eraldis.geojson  (+ fruiting_score_{species}, debug sub-columns)
        |
        | scripts/score_ecotone_fruiting.py
        v
data/ecotones.geojson  (+ fruiting_modifier_{species})
        |
        | scout.py: scout_score = ecotone_score x access_modifier x fruiting_modifier
        v
data/scout_candidates.geojson  (+ per-candidate weather-unconfirmed status,
                                  run-level coverage guard before export)
```

`FruitingScore(species, stand) = SeasonPrior_s x MoistureTrigger_s x TemperatureModifier x PersistenceModifier`

Each factor answers one question, kept separate rather than folded into one opaque
number (same discipline as `StandHabitatScore`/`AccessScore`/`EcotoneScore` staying
distinct):

- **SeasonPrior** — can this species even be in its seasonal phase right now?
- **MoistureTrigger** — was there enough water in roughly the last two weeks?
- **TemperatureModifier** — is the current temperature regime not actively hurting?
- **PersistenceModifier** — have recent favorable conditions stayed damp enough?

If *any* factor is `None` (insufficient coverage in the window it depends on),
`FruitingScore` is `None` — never a fabricated neutral value. This matches
`stand_habitat_score`'s existing `_is_missing`-gated pattern in `habitat.py`.

## Component 1: `radar.py` extensions (real changes to already-shipped, live-verified code)

`accumulate_rainfall`'s existing single forward pass (files already iterated in
ascending timestamp order) gains additional per-pixel state, computed in the *same*
pass — no second read of the cache:

**Rename:** `hours_since_rain` -> `hours_since_any_rain` (any measurable rain, `>0.0`
mm/h — kept, explicitly diagnostic-only now, not a primary `FruitingScore` input, since
0.1mm drizzle resetting a "when did it last rain" clock is misleading for a
biological-moisture signal).

**New: event-based significant/strong rain tracking.** A "rain event" is a run of wet
slots where no gap between consecutive wet slots exceeds `RAIN_EVENT_DRY_GAP_H = 6.0`
hours. Per-pixel state maintained across the loop:

```python
RAIN_EVENT_DRY_GAP_H = 6.0
SIGNIFICANT_EVENT_MM = 5.0
STRONG_EVENT_MM = 10.0

event_mm = np.zeros(shape)
event_last_wet_epoch = np.full(shape, -np.inf)
last_significant_epoch = np.full(shape, -np.inf)
last_significant_mm = np.zeros(shape)
last_strong_epoch = np.full(shape, -np.inf)
last_strong_mm = np.zeros(shape)

for path in files:
    # ... existing rate_mm_h / mm_this_slot / wet_mask computation unchanged ...
    epoch = timestamp.timestamp()

    gap_exceeded = wet_mask & (
        (epoch - event_last_wet_epoch) > RAIN_EVENT_DRY_GAP_H * 3600
    )
    event_mm = np.where(gap_exceeded, 0.0, event_mm)
    event_mm = np.where(wet_mask, event_mm + mm_this_slot, event_mm)
    event_last_wet_epoch = np.where(wet_mask, epoch, event_last_wet_epoch)

    # Re-evaluated on every wet slot, not just the crossing slot: event_mm only
    # grows (or resets at a new event) within one event, so once it first crosses
    # a threshold this condition stays True for every later wet slot of the SAME
    # event, continuously advancing the timestamp through to the event's actual
    # end (last wet slot) rather than freezing at the instant of crossing.
    newly_significant = wet_mask & (event_mm >= SIGNIFICANT_EVENT_MM)
    last_significant_epoch = np.where(newly_significant, epoch, last_significant_epoch)
    last_significant_mm = np.where(newly_significant, event_mm, last_significant_mm)

    newly_strong = wet_mask & (event_mm >= STRONG_EVENT_MM)
    last_strong_epoch = np.where(newly_strong, epoch, last_strong_epoch)
    last_strong_mm = np.where(newly_strong, event_mm, last_strong_mm)
```

After the loop: `hours_since_significant_rain`/`hours_since_strong_rain` computed from
`last_significant_epoch`/`last_strong_epoch` the same way `hours_since_any_rain` already
is (`-inf` -> `NaN`, i.e. "no qualifying event anywhere in the cached 14-day window").
Exported: `hours_since_significant_rain`, `hours_since_strong_rain`,
`last_significant_event_mm`, `last_strong_event_mm`.

**New: `max_24h_rain_14d`.** A per-pixel rolling-24h-sum maximum over the whole window —
distinguishes one storm from steady light rain at equal `rain_14d_mm`. Implemented with
a bounded sliding buffer (append each slot's per-pixel `mm_this_slot` array with its
epoch; while the oldest buffered entry is more than 24h before the current slot, evict
it and subtract from a running 24h-sum accumulator; track the running max of that
accumulator). Bounded memory (~288 slots x grid size at 5-min cadence, not the full
~4000-file history).

**Dropped from v0:** `rain_days_14d` (distinct rainy calendar days) — lower-value,
adds bookkeeping; can be added later if real usage shows it's worth it.

## Component 2: `weather.py` — non-overlapping rain bins

New, in `refresh_weather` (not `radar.py` — this is a derived/business-logic step, not
raw physical accumulation from the cache):

```python
rain_0_3d_mm  = rain_3d_mm
rain_3_7d_mm  = rain_7d_mm - rain_3d_mm   if coverage["3d"] and coverage["7d"] both clear MIN_RADAR_COVERAGE else None
rain_7_14d_mm = rain_14d_mm - rain_7d_mm  if coverage["7d"] and coverage["14d"] both clear MIN_RADAR_COVERAGE else None
```

Computed from the *raw* joined values (before per-column degradation nulling), gated by
`radar_coverage["3d"]`/`["7d"]`/`["14d"]` directly. A result in `(-1e-6, 0)` clamps to
`0.0` (float rounding — `rain_7d_mm` is a superset-sum of `rain_3d_mm`'s slots, so it can
never be truly smaller). A result below `-1e-6` raises `ValueError` — that would mean the
accumulation logic itself is broken, not a data-quality issue to silently paper over.

`hours_since_significant_rain`/`hours_since_strong_rain`/`last_significant_event_mm`/
`last_strong_event_mm`/`max_24h_rain_14d` are all derived from scanning the full 14-day
cache (same as `rain_14d_mm`/`hours_since_any_rain` already are) — gated on
`radar_degraded_14d`, consistent with those existing columns.

## Component 3: `src/shroom_fm/fruiting.py` — core scoring

```python
RAIN_SCALE_MM = {"0_3d": 8.0, "3_7d": 12.0, "7_14d": 18.0}

MOISTURE_WEIGHTS = {
    "chanterelle":  {"0_3d": 0.15, "3_7d": 0.35, "7_14d": 0.50},
    "kitsemampel":  {"0_3d": 0.15, "3_7d": 0.35, "7_14d": 0.50},
    "aspen_bolete": {"0_3d": 0.20, "3_7d": 0.45, "7_14d": 0.35},
    "birch_bolete": {"0_3d": 0.20, "3_7d": 0.45, "7_14d": 0.35},
    "porcini":      {"0_3d": 0.20, "3_7d": 0.45, "7_14d": 0.35},
}


def rain_response(mm: float, scale_mm: float) -> float:
    return 1.0 - math.exp(-mm / scale_mm)


def moisture_trigger(species, rain_0_3d, rain_3_7d, rain_7_14d) -> float | None:
    if rain_0_3d is None or rain_3_7d is None or rain_7_14d is None:
        return None
    w = MOISTURE_WEIGHTS[species]
    return (
        w["0_3d"]  * rain_response(rain_0_3d,  RAIN_SCALE_MM["0_3d"])
      + w["3_7d"]  * rain_response(rain_3_7d,  RAIN_SCALE_MM["3_7d"])
      + w["7_14d"] * rain_response(rain_7_14d, RAIN_SCALE_MM["7_14d"])
    )
```

`temperature_modifier(temp_mean_3d, temp_night_mean_3d) -> float | None`: `None` if
either input is `None`. Trapezoid on `temp_mean_3d`: floor `0.4` below 2C or above 26C,
ramps `0.4->1.0` across 2-8C, plateau `1.0` across 8-18C, ramps `1.0->0.4` across
18-26C. Multiplied by a soft frost guard on `temp_night_mean_3d`: `1.0` at >=2C,
linearly interpolating to `0.6` at <=-2C (a few very cold nights worsen the window,
don't destroy it).

`persistence_modifier(rh_night_mean_3d, wet_hours_72h, hours_since_significant_rain) -> float | None`:
`None` if `rh_night_mean_3d` or `wet_hours_72h` is `None` (a genuinely missing MEPS/radar
signal); `hours_since_significant_rain` being `NaN` (no qualifying event in the whole
cache) is *not* missingness here — it means "recency score is 0", handled explicitly:

```python
night_rh_score = clamp((rh_night_mean_3d - 65) / (90 - 65), 0.0, 1.0)
wet_hours_score = 1.0 - math.exp(-wet_hours_72h / 6.0)
recency_score = (
    0.0 if hours_since_significant_rain is None or math.isnan(hours_since_significant_rain)
    else math.exp(-math.log(2) * hours_since_significant_rain / 72.0)
)
persistence_raw = 0.50 * night_rh_score + 0.20 * wet_hours_score + 0.30 * recency_score
return 0.6 + 0.4 * persistence_raw
```

`season_prior(species, month_day: str) -> float`: piecewise-linear interpolation over
each species' knot list (below), flat-extrapolated before the first / after the last
knot (not decaying further past the shoulders — an unusually early/late season should
still be visible in ranking, not zeroed).

```python
SEASON_PRIORS = {
    "chanterelle": [
        ("06-01", 0.10), ("06-15", 0.35), ("07-01", 0.85), ("07-15", 1.00),
        ("09-10", 1.00), ("10-01", 0.35), ("10-15", 0.10),
    ],
    "kitsemampel": [
        ("07-20", 0.05), ("08-01", 0.20), ("08-15", 0.85), ("08-25", 1.00),
        ("09-20", 1.00), ("10-01", 0.30), ("10-10", 0.05),
    ],
    "aspen_bolete": [
        ("07-01", 0.15), ("07-15", 0.65), ("08-01", 1.00),
        ("09-20", 1.00), ("10-05", 0.30), ("10-15", 0.05),
    ],
    "birch_bolete": [
        ("07-01", 0.15), ("07-20", 0.70), ("08-01", 1.00),
        ("09-20", 1.00), ("10-05", 0.30), ("10-15", 0.05),
    ],
    "porcini": [
        ("07-01", 0.10), ("07-15", 0.60), ("08-01", 1.00),
        ("09-25", 1.00), ("10-05", 0.35), ("10-15", 0.05),
    ],
}
```

All numeric constants in this component (`RAIN_SCALE_MM`, `MOISTURE_WEIGHTS`,
temperature/persistence thresholds, `SEASON_PRIORS`) are explicit Estonian v0
engineering priors, not measured mycological constants — documented as such in the
module docstring, matching `habitat.py`'s existing `HOST_PROFILES`/`SITE_TYPE_PROFILES`
precedent. `SEASON_PRIORS` in particular is expected to be replaced later by a smoothed,
occurrence-effort-adjusted curve derived from eElurikkus/PlutoF observations (a separate,
later sub-project).

`fruiting_score(species, ...) -> float | None`: `None` if any of the four factors is
`None`; otherwise their product.

## Component 4: pipeline scripts

`scripts/score_fruiting.py` — reads `data/weather_eraldis.geojson`, adds
`fruiting_score_{species}` and debug columns (`fruiting_moisture_score_{species}`,
`fruiting_season_prior_{species}`, `fruiting_temperature_modifier`,
`fruiting_persistence_modifier` — the latter two shared across species, not
per-species, since v0 uses one shared curve for both) additively, overwrites the same
file. Same shape as `score_habitat.py`. Its own step, run after `refresh_weather.py`.

`scripts/score_ecotone_fruiting.py` — joins each ecotone's two stands'
`fruiting_score_{species}` (simple average — adjacent stands share the same physical
weather, unlike `AccessScore` where "drive to whichever's easier" justified taking the
max) onto `data/ecotones.geojson` as `fruiting_modifier_{species}`.

## Component 5: `ScoutScore` v1

```python
def scout_score(ecotone_score, access_modifier, fruiting_modifier, eligible) -> float | None:
    if not eligible or ecotone_score is None or access_modifier is None or fruiting_modifier is None:
        return None
    return ecotone_score * access_modifier * fruiting_modifier
```

**No third tier.** The existing two-tier `ranked`/`remote_high_value` split stays, but
`exclusion_reason` (already a real, populated column on the `remote_high_value` tier)
gains a second possible value:

- `REMOTE_BY_V1_ACCESS_PROXY` — access unconfirmed (existing).
- `MISSING_FRUITING_DATA` — weather unconfirmed for this stand (new).

Both land in the same `remote_high_value` tier — ecologically strong, operationally
un-actionable *right now* for a stated reason, never a silently-dropped row.
`export_scout_candidates.py`'s `OUTPUT_COLUMNS` (shared by both tiers, since both come
from one concatenated DataFrame) gains `fruiting_score` (nullable), `weather_data_quality`,
`weather_data_coverage`, and `weather_as_of` (all pulled through from the joined
`weather_eraldis.geojson` data) so a `MISSING_FRUITING_DATA` row is self-explanatory
without cross-referencing another file — and a `ranked` row shows the `fruiting_score`
that actually contributed to its `scout_score`, for the same debuggability reason every
other sub-score is already exposed.

**Precedence when a candidate fails both checks at once:** `scout_eligible == False`
(access-ineligible) is checked first, matching `scout_score`'s existing short-circuit
order — a candidate that's both access-ineligible and weather-unconfirmed reports
`exclusion_reason = "REMOTE_BY_V1_ACCESS_PROXY"`, not `"MISSING_FRUITING_DATA"`, since
"can't physically get there" is the more fundamental blocker of the two.

**Run-level coverage guard**, in `scripts/export_scout_candidates.py`, computed once
across the full candidate pool before any per-species ranking:

```python
MIN_SCOUT_WEATHER_COVERAGE = 0.90

weather_coverage_ratio = (
    candidates_with_fruiting_data / ecologically_and_access_eligible_candidates
)
```

("Ecologically and access eligible" = has a non-null `ecotone_score_{species}` for at
least one species AND `scout_eligible` — i.e., candidates that *would* be rankable if
weather were the only missing piece.) If `weather_coverage_ratio < MIN_SCOUT_WEATHER_COVERAGE`,
the script does **not** write `data/scout_candidates.geojson` — it prints a clear
diagnostic (`"Scout ranking unavailable: weather coverage {ratio:.1%}, required >= {MIN_SCOUT_WEATHER_COVERAGE:.0%}"`)
and exits non-zero. Rationale: a Top-N computed from 3% of stands with weather data would
look identical in shape to a trustworthy ranking while silently representing something
else entirely — distinguishing "one candidate missing weather" (fine, tag and keep
going) from "most of the run is missing weather" (the ranking itself is untrustworthy,
refuse to publish it) matters precisely because this project has already found real
radar-coverage gaps in production.

## Testing

Pure functions (`rain_response`, `temperature_modifier`, `persistence_modifier`,
`season_prior`, `moisture_trigger`, `fruiting_score`) unit-tested with concrete expected
values at representative points (knot values, midpoints between knots, floor/ceiling
edges, `None`-propagation for each individual missing input). `radar.py`'s new
event-tracking logic tested with synthetic multi-file fixtures constructing specific
event shapes (two events separated by exactly/just-under/just-over the 6h dry gap;
an event that crosses 5mm then continues past 10mm; a slot sequence with no qualifying
event at all). `weather.py`'s bin-differencing tested for the coverage-gated `None`
cases and the epsilon-clamp/integrity-raise cases. `scout.py`'s `MISSING_FRUITING_DATA`
exclusion-reason path and the run-level coverage guard both get dedicated tests. Real
end-to-end verification against the already-real `data/weather_eraldis.geojson`/
`data/ecotones.geojson` at implementation-review time, same discipline as every prior
branch this session.

## Out of scope

- Personal observation history, PlutoF/eElurikkus regional-pulse signal — separate,
  later sub-projects (already noted in CLAUDE.md).
- Calibrating `SEASON_PRIORS`/`MOISTURE_WEIGHTS`/`RAIN_SCALE_MM`/temperature-persistence
  thresholds against real field data — explicitly deferred, these are v0 engineering
  priors by design.
- `rain_days_14d` — dropped from v0 (see Component 1).
- A genuine soil-moisture proxy (KAIA's FWI DMC/DC layers, mentioned in the original
  weather-ingestion brainstorm) that could eventually replace some of the
  rain-derived `PersistenceModifier` terms — separate, later sub-project.
- Per-species `TemperatureModifier`/`PersistenceModifier` curves (v0 uses one shared
  curve for each, across all 5 species).
