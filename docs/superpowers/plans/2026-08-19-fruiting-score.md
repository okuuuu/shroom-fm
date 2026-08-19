# FruitingScore Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `FruitingScore` — a weather-driven, per-species "worth scouting now"
signal combining distributed-lag rainfall, temperature, humidity, and season — and wire
it into `ScoutScore` v1 as a third multiplicative factor alongside `EcotoneScore` and
`AccessScore`.

**Architecture:** Extend already-shipped `radar.py` with event-based rain tracking (real
change to live-verified code, not just new code on top); add rain-bin differencing to
`weather.py`; add a new `fruiting.py` scoring module mirroring `habitat.py`'s shape; two
new pipeline scripts (`score_fruiting.py`, `score_ecotone_fruiting.py`); extend
`scout.py`/`export_scout_candidates.py` for the third factor, a `MISSING_FRUITING_DATA`
exclusion reason, and a run-level weather-coverage guard.

**Tech Stack:** Python, existing project stack (geopandas/pandas/numpy) — no new
dependencies.

**Spec:** `docs/superpowers/specs/2026-08-19-fruiting-score-design.md`

## Global Constraints

- `FruitingScore = SeasonPrior_s x MoistureTrigger_s x TemperatureModifier x PersistenceModifier`
  (species-specific `SeasonPrior`/`MoistureTrigger`; `TemperatureModifier`/
  `PersistenceModifier` shared across all 5 species in v0).
- `FruitingScore` is `None` if `MoistureTrigger`, `TemperatureModifier`, or
  `PersistenceModifier` is `None` (never a fabricated neutral value). `SeasonPrior`
  never returns `None` — it's a pure calendar function.
- All missingness checks in `fruiting.py` must treat both Python `None` and float `NaN`
  as "missing" (GeoJSON round-trips can turn a written `None` into `NaN` on read-back —
  same quirk this project has hit before, documented in CLAUDE.md's "Known real-data
  quirks").
- All numeric constants in `fruiting.py` (`RAIN_SCALE_MM`, `MOISTURE_WEIGHTS`,
  temperature/persistence thresholds, `SEASON_PRIORS`) are documented Estonian v0
  engineering priors, not measured constants — same documentation discipline as
  `habitat.py`'s `HOST_PROFILES`/`SITE_TYPE_PROFILES`.
- `TARGET_SPECIES = ["kitsemampel", "chanterelle", "aspen_bolete", "birch_bolete", "porcini"]`
  (exact strings, matching `habitat.py`'s existing `TARGET_SPECIES`).
- `ScoutScore` v1: `scout_score = ecotone_score * access_modifier * fruiting_modifier`,
  `None` if any of the three (or `eligible`) is falsy/`None`.
- No third `ScoutScore` tier — a candidate whose only problem is missing weather data
  gets `exclusion_reason = "MISSING_FRUITING_DATA"` in the existing `remote_high_value`
  tier. `scout_eligible == False` (access-ineligible) takes precedence over
  `MISSING_FRUITING_DATA` when both apply to the same candidate.
- Baseline before this plan: 182 tests passing (`uv run pytest tests/ -q`).

---

### Task 1: `radar.py` — event-based significant/strong rain tracking + rolling 24h max

**Files:**
- Modify: `src/shroom_fm/radar.py` (imports, new constants, `accumulate_rainfall`)
- Modify: `tests/test_radar.py` (rename `hours_since_rain` references, add new tests)

**Interfaces:**
- Modifies: `accumulate_rainfall(cache_dir, now, eraldis_bounds_wgs84) -> tuple[gpd.GeoDataFrame, dict[str, float]]`
  — same signature, same `coverage` dict shape (`{"3d", "7d", "14d"}`), but the returned
  `GeoDataFrame` gains 5 new columns and one renamed column:
  - `hours_since_rain` renamed to **`hours_since_any_rain`** (breaking rename — any
    downstream reader must update).
  - New: `hours_since_significant_rain`, `hours_since_strong_rain`,
    `last_significant_event_mm`, `last_strong_event_mm`, `max_24h_rain_14d`.
- Produces: `RAIN_EVENT_DRY_GAP_H = 6.0`, `SIGNIFICANT_EVENT_MM = 5.0`,
  `STRONG_EVENT_MM = 10.0` module constants.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_radar.py` (near `test_accumulate_rainfall_sums_across_cached_files_in_window`):

```python
def test_accumulate_rainfall_tracks_significant_and_strong_rain_events(tmp_path):
    cache_dir = tmp_path / "radar_cache"
    cache_dir.mkdir()

    # Single-pixel (1x1) grid. rate=24.0 mm/h * (5/60)h = 2.0mm per slot.
    # Slot sequence (5 min apart): 2,2,2,2 mm -> cumulative event total 2,4,6,8.
    # Crosses SIGNIFICANT_EVENT_MM=5.0 at the 3rd slot (cumulative 6.0), continues
    # advancing through the 4th slot (cumulative 8.0) since it's still the same event.
    _write_fake_composite(cache_dir / "20260815T000000Z_1.h5", rate_grid=[[24.0]])
    _write_fake_composite(cache_dir / "20260815T000500Z_2.h5", rate_grid=[[24.0]])
    _write_fake_composite(cache_dir / "20260815T001000Z_3.h5", rate_grid=[[24.0]])
    # 4th slot: cumulative 8+2=10.0 -> crosses STRONG_EVENT_MM=10.0 too.
    _write_fake_composite(cache_dir / "20260815T001500Z_4.h5", rate_grid=[[24.0]])

    # Dry gap > 6h, then a NEW event that never reaches 5mm — must not affect the
    # already-recorded significant/strong stats from the first event.
    _write_fake_composite(cache_dir / "20260815T080000Z_5.h5", rate_grid=[[12.0]])  # 1.0mm

    now = _utc(2026, 8, 15, 9, 0)  # 1h after the 5th file

    points, _ = accumulate_rainfall(cache_dir, now, (20.0, 56.0, 30.0, 62.0))
    row = points.iloc[0]

    # Last significant/strong slot was the 4th file (00:15:00), not the 3rd (first
    # crossing) or the 5th (a separate, non-qualifying event) — proves continuous
    # advancement through the event and correct event-boundary reset on the gap.
    expected_hours = (now - _utc(2026, 8, 15, 0, 15)).total_seconds() / 3600
    assert row["hours_since_significant_rain"] == pytest.approx(expected_hours)
    assert row["hours_since_strong_rain"] == pytest.approx(expected_hours)
    assert row["last_significant_event_mm"] == pytest.approx(10.0)
    assert row["last_strong_event_mm"] == pytest.approx(10.0)


def test_accumulate_rainfall_never_had_a_significant_event_is_nan(tmp_path):
    cache_dir = tmp_path / "radar_cache"
    cache_dir.mkdir()
    # Single slot, only 1.0mm — never reaches SIGNIFICANT_EVENT_MM=5.0.
    _write_fake_composite(cache_dir / "20260815T000000Z_1.h5", rate_grid=[[12.0]])

    now = _utc(2026, 8, 15, 0, 5)
    points, _ = accumulate_rainfall(cache_dir, now, (20.0, 56.0, 30.0, 62.0))
    row = points.iloc[0]

    assert np.isnan(row["hours_since_significant_rain"])
    assert np.isnan(row["hours_since_strong_rain"])
    assert row["last_significant_event_mm"] == pytest.approx(0.0)
    assert row["last_strong_event_mm"] == pytest.approx(0.0)


def test_accumulate_rainfall_max_24h_rain_captures_concentrated_window_not_whole_period(
    tmp_path,
):
    cache_dir = tmp_path / "radar_cache"
    cache_dir.mkdir()
    # Two slots close together (1.0mm each, same 24h window) = 2.0mm concentrated.
    _write_fake_composite(cache_dir / "20260815T000000Z_1.h5", rate_grid=[[12.0]])
    _write_fake_composite(cache_dir / "20260815T000500Z_2.h5", rate_grid=[[12.0]])
    # A 3rd slot 5 days later (well outside any 24h window containing the first two).
    _write_fake_composite(cache_dir / "20260820T000000Z_3.h5", rate_grid=[[12.0]])

    now = _utc(2026, 8, 20, 0, 10)
    points, _ = accumulate_rainfall(cache_dir, now, (20.0, 56.0, 30.0, 62.0))
    row = points.iloc[0]

    # rain_14d_mm sums all 3 slots (3.0mm total), but max_24h_rain_14d only ever sees
    # 2.0mm in any single rolling 24h window — the two early slots never coexist in
    # the same window as the late one.
    assert row["rain_14d_mm"] == pytest.approx(3.0)
    assert row["max_24h_rain_14d"] == pytest.approx(2.0)
```

Also update the EXISTING test `test_accumulate_rainfall_sums_across_cached_files_in_window`:
change both occurrences of `"hours_since_rain"` (the column-name string in
`row0_col0["hours_since_rain"]` and `row1_col1["hours_since_rain"]`) to
`"hours_since_any_rain"`.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_radar.py -v`
Expected: the 3 new tests FAIL (`KeyError` on the new column names), and the modified
existing test FAILs too (still asserts the old `"hours_since_rain"` column name, which
no longer exists once you implement Step 3 — but since you haven't implemented Step 3
yet, it should currently still PASS under the old column name; that's fine, you're about
to break it on purpose by renaming the assertion in Step 1, then fix the implementation
in Step 3 to make it pass again under the new name).

- [ ] **Step 3: Implement in `src/shroom_fm/radar.py`**

Add these imports at the top (alongside the existing ones): `from collections import
deque`.

Add these constants near `_RADAR_WINDOW_DAYS`/`_RADAR_SLOT_MINUTES`:

```python
RAIN_EVENT_DRY_GAP_H = 6.0
SIGNIFICANT_EVENT_MM = 5.0
STRONG_EVENT_MM = 10.0
```

Replace the entire `accumulate_rainfall` function body with:

```python
def accumulate_rainfall(
    cache_dir: Path,
    now: datetime,
    eraldis_bounds_wgs84: tuple[float, float, float, float],
) -> tuple[gpd.GeoDataFrame, dict[str, float]]:
    from datetime import timedelta

    window_start = now - timedelta(days=_RADAR_WINDOW_DAYS)
    files = cached_radar_files(cache_dir, window_start, now)

    cutoff_3d = now - timedelta(days=3)
    cutoff_7d = now - timedelta(days=7)
    cutoff_72h = now - timedelta(hours=72)

    expected_slots_14d = (_RADAR_WINDOW_DAYS * 24 * 60) // _RADAR_SLOT_MINUTES
    expected_slots_7d = (7 * 24 * 60) // _RADAR_SLOT_MINUTES
    expected_slots_3d = (3 * 24 * 60) // _RADAR_SLOT_MINUTES

    if not files:
        coverage = {"3d": 0.0, "7d": 0.0, "14d": 0.0}
        empty = gpd.GeoDataFrame(
            {
                "row": [],
                "col": [],
                "rain_3d_mm": [],
                "rain_7d_mm": [],
                "rain_14d_mm": [],
                "hours_since_any_rain": [],
                "wet_hours_72h": [],
                "hours_since_significant_rain": [],
                "hours_since_strong_rain": [],
                "last_significant_event_mm": [],
                "last_strong_event_mm": [],
                "max_24h_rain_14d": [],
            },
            geometry=[],
            crs="EPSG:3301",
        )
        return empty, coverage

    full_georef = read_radar_full_georef(files[0])
    row_slice, col_slice = radar_bbox_slice(full_georef, eraldis_bounds_wgs84)

    _, georef = parse_radar_composite(
        files[0], row_slice=row_slice, col_slice=col_slice
    )
    shape = (georef["ysize"], georef["xsize"])

    rain_3d = np.zeros(shape)
    rain_7d = np.zeros(shape)
    rain_14d = np.zeros(shape)
    last_wet_epoch = np.full(shape, -np.inf)
    wet_slots_72h = np.zeros(shape, dtype=int)

    event_mm = np.zeros(shape)
    event_last_wet_epoch = np.full(shape, -np.inf)
    last_significant_epoch = np.full(shape, -np.inf)
    last_significant_mm = np.zeros(shape)
    last_strong_epoch = np.full(shape, -np.inf)
    last_strong_mm = np.zeros(shape)

    window_buffer = deque()
    window_sum = np.zeros(shape)
    max_24h_rain = np.zeros(shape)

    slot_hours = _RADAR_SLOT_MINUTES / 60
    count_3d = 0
    count_7d = 0

    rain_event_dry_gap_seconds = RAIN_EVENT_DRY_GAP_H * 3600
    max_24h_seconds = 24 * 3600

    for path in files:
        timestamp = cached_radar_timestamp(path)
        epoch = timestamp.timestamp()
        rate_mm_h, file_georef = parse_radar_composite(
            path, row_slice=row_slice, col_slice=col_slice
        )
        if (file_georef["xsize"], file_georef["ysize"]) != (
            georef["xsize"],
            georef["ysize"],
        ):
            raise ValueError(
                f"{path} has a different grid shape than the first cached file — "
                "radar product geometry is expected to be stable"
            )
        mm_this_slot = np.nan_to_num(rate_mm_h, nan=0.0) * slot_hours
        rain_14d += mm_this_slot
        if timestamp >= cutoff_7d:
            rain_7d += mm_this_slot
            count_7d += 1
        if timestamp >= cutoff_3d:
            rain_3d += mm_this_slot
            count_3d += 1
        wet_mask = np.nan_to_num(rate_mm_h, nan=-1.0) > 0.0
        last_wet_epoch = np.where(wet_mask, epoch, last_wet_epoch)
        if timestamp >= cutoff_72h:
            wet_slots_72h += wet_mask.astype(int)

        # Event-based significant/strong rain tracking: a run of wet slots with no
        # gap exceeding RAIN_EVENT_DRY_GAP_H between consecutive wet slots is one
        # event. Re-evaluated on every wet slot (not just the crossing slot), so
        # once event_mm first reaches a threshold, every later wet slot of the SAME
        # event keeps advancing that threshold's timestamp through to the event's
        # actual end, not freezing at the instant of crossing.
        gap_exceeded = wet_mask & (
            (epoch - event_last_wet_epoch) > rain_event_dry_gap_seconds
        )
        event_mm = np.where(gap_exceeded, 0.0, event_mm)
        event_mm = np.where(wet_mask, event_mm + mm_this_slot, event_mm)
        event_last_wet_epoch = np.where(wet_mask, epoch, event_last_wet_epoch)

        newly_significant = wet_mask & (event_mm >= SIGNIFICANT_EVENT_MM)
        last_significant_epoch = np.where(
            newly_significant, epoch, last_significant_epoch
        )
        last_significant_mm = np.where(
            newly_significant, event_mm, last_significant_mm
        )

        newly_strong = wet_mask & (event_mm >= STRONG_EVENT_MM)
        last_strong_epoch = np.where(newly_strong, epoch, last_strong_epoch)
        last_strong_mm = np.where(newly_strong, event_mm, last_strong_mm)

        # Rolling 24h max: maintain a sliding sum of the trailing 24h of slots.
        window_buffer.append((epoch, mm_this_slot))
        window_sum = window_sum + mm_this_slot
        while window_buffer and (epoch - window_buffer[0][0]) > max_24h_seconds:
            _, old_mm = window_buffer.popleft()
            window_sum = window_sum - old_mm
        max_24h_rain = np.maximum(max_24h_rain, window_sum)

    coverage = {
        "3d": count_3d / expected_slots_3d if expected_slots_3d else 0.0,
        "7d": count_7d / expected_slots_7d if expected_slots_7d else 0.0,
        "14d": len(files) / expected_slots_14d if expected_slots_14d else 0.0,
    }

    hours_since_any_rain = np.where(
        last_wet_epoch == -np.inf,
        np.nan,
        (now.timestamp() - last_wet_epoch) / 3600,
    )
    hours_since_significant_rain = np.where(
        last_significant_epoch == -np.inf,
        np.nan,
        (now.timestamp() - last_significant_epoch) / 3600,
    )
    hours_since_strong_rain = np.where(
        last_strong_epoch == -np.inf,
        np.nan,
        (now.timestamp() - last_strong_epoch) / 3600,
    )
    wet_hours_72h = wet_slots_72h * slot_hours

    points = radar_pixel_centers(georef)
    points["rain_3d_mm"] = rain_3d.ravel()
    points["rain_7d_mm"] = rain_7d.ravel()
    points["rain_14d_mm"] = rain_14d.ravel()
    points["hours_since_any_rain"] = hours_since_any_rain.ravel()
    points["wet_hours_72h"] = wet_hours_72h.ravel()
    points["hours_since_significant_rain"] = hours_since_significant_rain.ravel()
    points["hours_since_strong_rain"] = hours_since_strong_rain.ravel()
    points["last_significant_event_mm"] = last_significant_mm.ravel()
    points["last_strong_event_mm"] = last_strong_mm.ravel()
    points["max_24h_rain_14d"] = max_24h_rain.ravel()
    points = points.to_crs("EPSG:3301")
    return points, coverage
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_radar.py -v`
Expected: all pass (23 existing + 3 new = 26 — but 1 existing test's assertions changed
in Step 1, not a new test, so the file's test *count* goes from 23 to 26).

- [ ] **Step 5: Commit**

```bash
git add src/shroom_fm/radar.py tests/test_radar.py
git commit -m "feat: track significant/strong rain events and rolling 24h max in radar.py"
```

---

### Task 2: `weather.py` — non-overlapping rain bins, new column wiring

**Files:**
- Modify: `src/shroom_fm/weather.py`
- Modify: `tests/test_weather.py`

**Interfaces:**
- Consumes: Task 1's renamed/new `accumulate_rainfall` output columns.
- Produces: `refresh_weather`'s output gains `rain_0_3d_mm`, `rain_3_7d_mm`,
  `rain_7_14d_mm` (each nullable), plus the new radar columns
  (`hours_since_significant_rain`, `hours_since_strong_rain`,
  `last_significant_event_mm`, `last_strong_event_mm`, `max_24h_rain_14d`) gated the
  same way `hours_since_any_rain`/`rain_14d_mm` already are (on `radar_degraded_14d`).
  `hours_since_rain` output column is renamed to `hours_since_any_rain` (matches Task 1's
  rename).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_weather.py`:

```python
def test_refresh_weather_computes_non_overlapping_rain_bins(monkeypatch, tmp_path):
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1]}, geometry=[Point(24.0, 59.0)], crs="EPSG:4326"
    )
    now = _utc(2026, 8, 18, 12)

    radar_points = gpd.GeoDataFrame(
        {
            "rain_3d_mm": [5.0],
            "rain_7d_mm": [12.0],
            "rain_14d_mm": [20.0],
            "hours_since_any_rain": [3.0],
            "wet_hours_72h": [1.0],
            "hours_since_significant_rain": [10.0],
            "hours_since_strong_rain": [20.0],
            "last_significant_event_mm": [6.0],
            "last_strong_event_mm": [12.0],
            "max_24h_rain_14d": [8.0],
        },
        geometry=[Point(500000, 6500000)],
        crs="EPSG:3301",
    )
    meps_points = gpd.GeoDataFrame(
        {
            "temp_mean_3d": [15.0],
            "temp_night_mean_3d": [10.0],
            "rh_mean_3d": [70.0],
            "rh_night_mean_3d": [85.0],
        },
        geometry=[Point(500000, 6500000)],
        crs="EPSG:3301",
    )

    monkeypatch.setattr(
        "shroom_fm.weather.accumulate_rainfall",
        lambda cache_dir, now_, bounds: (radar_points, _healthy_radar_coverage()),
    )
    monkeypatch.setattr(
        "shroom_fm.weather.accumulate_meps_features",
        lambda now_, bounds: (meps_points, 0.9, now_ - timedelta(hours=1)),
    )

    result = refresh_weather(eraldis_gdf, tmp_path / "radar_cache", now)

    assert result.loc[0, "rain_0_3d_mm"] == pytest.approx(5.0)
    assert result.loc[0, "rain_3_7d_mm"] == pytest.approx(7.0)  # 12 - 5
    assert result.loc[0, "rain_7_14d_mm"] == pytest.approx(8.0)  # 20 - 12
    assert result.loc[0, "hours_since_any_rain"] == pytest.approx(3.0)
    assert result.loc[0, "hours_since_significant_rain"] == pytest.approx(10.0)
    assert result.loc[0, "hours_since_strong_rain"] == pytest.approx(20.0)
    assert result.loc[0, "last_significant_event_mm"] == pytest.approx(6.0)
    assert result.loc[0, "last_strong_event_mm"] == pytest.approx(12.0)
    assert result.loc[0, "max_24h_rain_14d"] == pytest.approx(8.0)


def test_refresh_weather_nulls_rain_bins_when_component_window_degraded(
    monkeypatch, tmp_path
):
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1]}, geometry=[Point(24.0, 59.0)], crs="EPSG:4326"
    )
    now = _utc(2026, 8, 18, 12)

    radar_points = gpd.GeoDataFrame(
        {
            "rain_3d_mm": [5.0],
            "rain_7d_mm": [12.0],
            "rain_14d_mm": [20.0],
            "hours_since_any_rain": [3.0],
            "wet_hours_72h": [1.0],
            "hours_since_significant_rain": [10.0],
            "hours_since_strong_rain": [20.0],
            "last_significant_event_mm": [6.0],
            "last_strong_event_mm": [12.0],
            "max_24h_rain_14d": [8.0],
        },
        geometry=[Point(500000, 6500000)],
        crs="EPSG:3301",
    )
    meps_points = gpd.GeoDataFrame(
        {
            "temp_mean_3d": [15.0],
            "temp_night_mean_3d": [10.0],
            "rh_mean_3d": [70.0],
            "rh_night_mean_3d": [85.0],
        },
        geometry=[Point(500000, 6500000)],
        crs="EPSG:3301",
    )

    # 7d window degraded -> both rain_3_7d_mm (needs 3d+7d) and rain_7_14d_mm
    # (needs 7d+14d) must be null; rain_0_3d_mm (needs only 3d) stays real.
    monkeypatch.setattr(
        "shroom_fm.weather.accumulate_rainfall",
        lambda cache_dir, now_, bounds: (
            radar_points,
            {"3d": 0.9, "7d": 0.2, "14d": 0.9},
        ),
    )
    monkeypatch.setattr(
        "shroom_fm.weather.accumulate_meps_features",
        lambda now_, bounds: (meps_points, 0.9, now_ - timedelta(hours=1)),
    )

    result = refresh_weather(eraldis_gdf, tmp_path / "radar_cache", now)

    assert result.loc[0, "rain_0_3d_mm"] == pytest.approx(5.0)
    assert result.loc[0, "rain_3_7d_mm"] is None
    assert result.loc[0, "rain_7_14d_mm"] is None


def test_refresh_weather_clamps_tiny_negative_bin_difference_to_zero(monkeypatch, tmp_path):
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1]}, geometry=[Point(24.0, 59.0)], crs="EPSG:4326"
    )
    now = _utc(2026, 8, 18, 12)

    # rain_7d_mm very slightly less than rain_3d_mm — floating-point rounding noise,
    # not a real accounting error (within the 1e-6 epsilon).
    radar_points = gpd.GeoDataFrame(
        {
            "rain_3d_mm": [5.0],
            "rain_7d_mm": [5.0 - 1e-9],
            "rain_14d_mm": [20.0],
            "hours_since_any_rain": [3.0],
            "wet_hours_72h": [1.0],
            "hours_since_significant_rain": [10.0],
            "hours_since_strong_rain": [20.0],
            "last_significant_event_mm": [6.0],
            "last_strong_event_mm": [12.0],
            "max_24h_rain_14d": [8.0],
        },
        geometry=[Point(500000, 6500000)],
        crs="EPSG:3301",
    )
    meps_points = gpd.GeoDataFrame(
        {
            "temp_mean_3d": [15.0],
            "temp_night_mean_3d": [10.0],
            "rh_mean_3d": [70.0],
            "rh_night_mean_3d": [85.0],
        },
        geometry=[Point(500000, 6500000)],
        crs="EPSG:3301",
    )

    monkeypatch.setattr(
        "shroom_fm.weather.accumulate_rainfall",
        lambda cache_dir, now_, bounds: (radar_points, _healthy_radar_coverage()),
    )
    monkeypatch.setattr(
        "shroom_fm.weather.accumulate_meps_features",
        lambda now_, bounds: (meps_points, 0.9, now_ - timedelta(hours=1)),
    )

    result = refresh_weather(eraldis_gdf, tmp_path / "radar_cache", now)

    assert result.loc[0, "rain_3_7d_mm"] == pytest.approx(0.0)


def test_refresh_weather_raises_on_large_negative_bin_difference(monkeypatch, tmp_path):
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1]}, geometry=[Point(24.0, 59.0)], crs="EPSG:4326"
    )
    now = _utc(2026, 8, 18, 12)

    # rain_7d_mm meaningfully less than rain_3d_mm — a real accounting bug, not
    # rounding noise. Must raise rather than silently clamp/hide it.
    radar_points = gpd.GeoDataFrame(
        {
            "rain_3d_mm": [5.0],
            "rain_7d_mm": [1.0],
            "rain_14d_mm": [20.0],
            "hours_since_any_rain": [3.0],
            "wet_hours_72h": [1.0],
            "hours_since_significant_rain": [10.0],
            "hours_since_strong_rain": [20.0],
            "last_significant_event_mm": [6.0],
            "last_strong_event_mm": [12.0],
            "max_24h_rain_14d": [8.0],
        },
        geometry=[Point(500000, 6500000)],
        crs="EPSG:3301",
    )
    meps_points = gpd.GeoDataFrame(
        {
            "temp_mean_3d": [15.0],
            "temp_night_mean_3d": [10.0],
            "rh_mean_3d": [70.0],
            "rh_night_mean_3d": [85.0],
        },
        geometry=[Point(500000, 6500000)],
        crs="EPSG:3301",
    )

    monkeypatch.setattr(
        "shroom_fm.weather.accumulate_rainfall",
        lambda cache_dir, now_, bounds: (radar_points, _healthy_radar_coverage()),
    )
    monkeypatch.setattr(
        "shroom_fm.weather.accumulate_meps_features",
        lambda now_, bounds: (meps_points, 0.9, now_ - timedelta(hours=1)),
    )

    with pytest.raises(ValueError):
        refresh_weather(eraldis_gdf, tmp_path / "radar_cache", now)
```

Also update the existing shared fixture helper `_make_radar_points()` in
`tests/test_weather.py` (currently at lines 101-112, returning a GeoDataFrame with
columns `rain_3d_mm`, `rain_7d_mm`, `rain_14d_mm`, `hours_since_rain`, `wet_hours_72h`
and used by 6 existing tests: `test_refresh_weather_joins_nearest_radar_and_meps_points`,
`test_refresh_weather_nulls_degraded_columns`,
`test_refresh_weather_nulls_only_3d_columns_when_only_3d_window_degraded`,
`test_refresh_weather_nulls_meps_columns_when_stale`,
`test_refresh_weather_nulls_meps_columns_when_coverage_below_threshold`,
`test_refresh_weather_nulls_all_columns_when_both_degraded`): rename its
`"hours_since_rain"` column key to `"hours_since_any_rain"`, and add the 5 new columns
(`hours_since_significant_rain`, `hours_since_strong_rain`, `last_significant_event_mm`,
`last_strong_event_mm`, `max_24h_rain_14d`) with reasonable non-null values (e.g. `10.0`,
`20.0`, `6.0`, `12.0`, `8.0` respectively) — `_RADAR_COLUMNS` will require all of these to
be present on the mocked `accumulate_rainfall` return value once Task 2's implementation
lands, or the join will raise a `KeyError`.

Also update the two existing assertions that reference the old column name directly:
`test_refresh_weather_nulls_only_3d_columns_when_only_3d_window_degraded` (currently
line 212: `assert result.loc[0, "hours_since_rain"] == pytest.approx(3.0)`) and
`test_refresh_weather_nulls_all_columns_when_both_degraded` (currently line 299:
`assert result.loc[0, "hours_since_rain"] is None`) — both must read
`"hours_since_any_rain"` instead.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_weather.py -v`
Expected: the 4 new tests FAIL (`KeyError`/`AttributeError` on the new columns), and any
existing test whose fixture you didn't yet update to include the new columns FAILs too
once you get to Step 3 (that's expected and will be fixed by your fixture updates in
Step 1 — if you did the fixture updates correctly in Step 1, only the 4 new tests should
fail at this point).

- [ ] **Step 3: Implement in `src/shroom_fm/weather.py`**

Replace `_RADAR_COLUMNS` and add a helper + bin-diff logic. Full replacement of the
top-of-file constants and the `refresh_weather` function:

```python
_RADAR_COLUMNS = (
    "rain_3d_mm",
    "rain_7d_mm",
    "rain_14d_mm",
    "hours_since_any_rain",
    "wet_hours_72h",
    "hours_since_significant_rain",
    "hours_since_strong_rain",
    "last_significant_event_mm",
    "last_strong_event_mm",
    "max_24h_rain_14d",
)
_MEPS_COLUMNS = (
    "temp_mean_3d",
    "temp_night_mean_3d",
    "rh_mean_3d",
    "rh_night_mean_3d",
)

_BIN_DIFF_EPSILON = 1e-6


def _bin_difference(minuend, subtrahend, minuend_degraded: bool, subtrahend_degraded: bool):
    if minuend_degraded or subtrahend_degraded or pd.isna(minuend) or pd.isna(subtrahend):
        return None
    diff = minuend - subtrahend
    if diff < -_BIN_DIFF_EPSILON:
        raise ValueError(
            f"Rain bin difference is negative beyond rounding tolerance ({diff}) — "
            "this should be mathematically impossible since the larger window's sum "
            "is a superset of the smaller window's slots; likely an accumulation bug."
        )
    return max(0.0, diff)
```

Replace the body of `refresh_weather` from `result["rain_3d_mm"] = ...` through
`result["hours_since_rain"] = ...` (the radar-column-nulling block) with:

```python
    result["rain_3d_mm"] = _null_if_degraded(radar_joined["rain_3d_mm"], radar_degraded_3d)
    result["wet_hours_72h"] = _null_if_degraded(
        radar_joined["wet_hours_72h"], radar_degraded_3d
    )
    result["rain_7d_mm"] = _null_if_degraded(radar_joined["rain_7d_mm"], radar_degraded_7d)
    result["rain_14d_mm"] = _null_if_degraded(
        radar_joined["rain_14d_mm"], radar_degraded_14d
    )
    result["hours_since_any_rain"] = _null_if_degraded(
        radar_joined["hours_since_any_rain"], radar_degraded_14d
    )
    for col in (
        "hours_since_significant_rain",
        "hours_since_strong_rain",
        "last_significant_event_mm",
        "last_strong_event_mm",
        "max_24h_rain_14d",
    ):
        result[col] = _null_if_degraded(radar_joined[col], radar_degraded_14d)

    result["rain_0_3d_mm"] = [
        None if radar_degraded_3d or pd.isna(v) else v for v in radar_joined["rain_3d_mm"]
    ]
    result["rain_3_7d_mm"] = [
        _bin_difference(v7, v3, radar_degraded_7d, radar_degraded_3d)
        for v7, v3 in zip(radar_joined["rain_7d_mm"], radar_joined["rain_3d_mm"])
    ]
    result["rain_7_14d_mm"] = [
        _bin_difference(v14, v7, radar_degraded_14d, radar_degraded_7d)
        for v14, v7 in zip(radar_joined["rain_14d_mm"], radar_joined["rain_7d_mm"])
    ]
```

(This replaces the old 5-line `hours_since_rain`-nulling statement and adds the new
per-column loop plus the three bin-difference columns — everything else in
`refresh_weather`, including the `_MEPS_COLUMNS` loop and the trailing `as_of`/
`weather_data_coverage`/`weather_data_quality` assignments, stays unchanged.)

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_weather.py -v`
Expected: all pass (real count depends on how many existing tests needed fixture
updates in Step 1 — report the actual number from the run).

- [ ] **Step 5: Commit**

```bash
git add src/shroom_fm/weather.py tests/test_weather.py
git commit -m "feat: derive non-overlapping rain bins in weather.py, wire new radar columns"
```

---

### Task 3: `src/shroom_fm/fruiting.py` — core scoring module

**Files:**
- Create: `src/shroom_fm/fruiting.py`
- Test: `tests/test_fruiting.py`

**Interfaces:**
- Produces: `TARGET_SPECIES` (same 5 strings as `habitat.py`'s);
  `rain_response(mm, scale_mm) -> float`;
  `moisture_trigger(species, rain_0_3d, rain_3_7d, rain_7_14d) -> float | None`;
  `temperature_modifier(temp_mean_3d, temp_night_mean_3d) -> float | None`;
  `persistence_modifier(rh_night_mean_3d, wet_hours_72h, hours_since_significant_rain) -> float | None`;
  `season_prior(species, month_day: str) -> float` (`month_day` format `"MM-DD"`);
  `fruiting_score(species, month_day, rain_0_3d, rain_3_7d, rain_7_14d, temp_mean_3d, temp_night_mean_3d, rh_night_mean_3d, wet_hours_72h, hours_since_significant_rain) -> tuple[float | None, dict]`
  (the dict has keys `fruiting_moisture_score`, `fruiting_temperature_modifier`,
  `fruiting_persistence_modifier`, `fruiting_season_prior`);
  `score_stands(weather_gdf, now: datetime) -> gpd.GeoDataFrame`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_fruiting.py`:

```python
import math
from datetime import datetime, timezone

import geopandas as gpd
import pytest
from shapely.geometry import Point

from shroom_fm.fruiting import (
    fruiting_score,
    moisture_trigger,
    persistence_modifier,
    score_stands,
    season_prior,
    rain_response,
    temperature_modifier,
)


def _utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def test_rain_response_at_scale_is_one_minus_e_inverse():
    assert rain_response(8.0, 8.0) == pytest.approx(1 - math.exp(-1))


def test_rain_response_at_zero_is_zero():
    assert rain_response(0.0, 8.0) == pytest.approx(0.0)


def test_moisture_trigger_all_bins_at_their_own_scale_sums_weights_times_shared_fraction():
    # All 3 bins exactly at their own scale -> each rain_response = 1-e^-1; weights
    # sum to 1.0 for every species, so the result is just that shared fraction.
    result = moisture_trigger("chanterelle", rain_0_3d=8.0, rain_3_7d=12.0, rain_7_14d=18.0)
    assert result == pytest.approx(1 - math.exp(-1))


def test_moisture_trigger_returns_none_when_any_bin_missing():
    assert moisture_trigger("chanterelle", None, 12.0, 18.0) is None
    assert moisture_trigger("chanterelle", 8.0, None, 18.0) is None
    assert moisture_trigger("chanterelle", 8.0, 12.0, None) is None


def test_moisture_trigger_handles_nan_as_missing():
    assert moisture_trigger("chanterelle", float("nan"), 12.0, 18.0) is None


def test_temperature_modifier_plateau_with_no_frost():
    assert temperature_modifier(12.0, 5.0) == pytest.approx(1.0)


def test_temperature_modifier_cold_ramp_with_no_frost():
    # (5-2)/(8-2) = 0.5 -> 0.4 + 0.5*0.6 = 0.7
    assert temperature_modifier(5.0, 5.0) == pytest.approx(0.7)


def test_temperature_modifier_applies_soft_frost_guard():
    # base=1.0 (plateau); frost: (2-0)/(2-(-2))=0.5 -> 1.0 - 0.5*0.4 = 0.8
    assert temperature_modifier(12.0, 0.0) == pytest.approx(0.8)


def test_temperature_modifier_floors_far_below_and_above_range():
    assert temperature_modifier(-10.0, -10.0) == pytest.approx(0.4 * 0.6)
    assert temperature_modifier(35.0, 15.0) == pytest.approx(0.4)


def test_temperature_modifier_returns_none_when_missing():
    assert temperature_modifier(None, 5.0) is None
    assert temperature_modifier(12.0, None) is None


def test_persistence_modifier_full_favorable_conditions():
    # night_rh_score=(90-65)/25=1.0; wet_hours_score=1-e^-1; recency_score=e^0=1.0
    # raw = .5*1 + .2*(1-e^-1) + .3*1 = 0.8 + 0.2*(1-e^-1)
    expected_raw = 0.5 * 1.0 + 0.2 * (1 - math.exp(-1)) + 0.3 * 1.0
    expected = 0.6 + 0.4 * expected_raw
    assert persistence_modifier(90.0, 6.0, 0.0) == pytest.approx(expected)


def test_persistence_modifier_nan_recency_treated_as_zero_not_missing():
    # NaN hours_since_significant_rain means "no qualifying event ever" -> recency=0,
    # NOT a missing-data None.
    result = persistence_modifier(90.0, 6.0, float("nan"))
    assert result is not None
    expected_raw = 0.5 * 1.0 + 0.2 * (1 - math.exp(-1)) + 0.3 * 0.0
    assert result == pytest.approx(0.6 + 0.4 * expected_raw)


def test_persistence_modifier_returns_none_when_rh_or_wet_hours_missing():
    assert persistence_modifier(None, 6.0, 0.0) is None
    assert persistence_modifier(90.0, None, 0.0) is None


def test_season_prior_exact_knot():
    assert season_prior("chanterelle", "07-01") == pytest.approx(0.85)


def test_season_prior_interpolates_between_knots():
    # 07-01=.85, 07-15=1.00 -> 07-08 is halfway -> 0.925
    assert season_prior("chanterelle", "07-08") == pytest.approx(0.925)


def test_season_prior_flat_extrapolates_before_first_knot():
    assert season_prior("chanterelle", "05-01") == pytest.approx(0.10)


def test_season_prior_flat_extrapolates_after_last_knot():
    assert season_prior("chanterelle", "11-01") == pytest.approx(0.10)


def test_fruiting_score_is_product_of_four_factors():
    score, components = fruiting_score(
        "chanterelle",
        "07-01",  # season_prior = 0.85
        rain_0_3d=8.0, rain_3_7d=12.0, rain_7_14d=18.0,  # moisture = 1-e^-1
        temp_mean_3d=12.0, temp_night_mean_3d=5.0,  # temperature = 1.0
        rh_night_mean_3d=90.0, wet_hours_72h=6.0, hours_since_significant_rain=0.0,
    )
    moisture = 1 - math.exp(-1)
    persistence_raw = 0.5 * 1.0 + 0.2 * (1 - math.exp(-1)) + 0.3 * 1.0
    persistence = 0.6 + 0.4 * persistence_raw
    expected = 0.85 * moisture * 1.0 * persistence
    assert score == pytest.approx(expected)
    assert components["fruiting_season_prior"] == pytest.approx(0.85)
    assert components["fruiting_moisture_score"] == pytest.approx(moisture)
    assert components["fruiting_temperature_modifier"] == pytest.approx(1.0)
    assert components["fruiting_persistence_modifier"] == pytest.approx(persistence)


def test_fruiting_score_is_none_when_moisture_missing_but_components_still_reported():
    score, components = fruiting_score(
        "chanterelle",
        "07-01",
        rain_0_3d=None, rain_3_7d=12.0, rain_7_14d=18.0,
        temp_mean_3d=12.0, temp_night_mean_3d=5.0,
        rh_night_mean_3d=90.0, wet_hours_72h=6.0, hours_since_significant_rain=0.0,
    )
    assert score is None
    assert components["fruiting_moisture_score"] is None
    assert components["fruiting_season_prior"] == pytest.approx(0.85)  # still computed


def test_score_stands_adds_per_species_and_shared_columns():
    weather_gdf = gpd.GeoDataFrame(
        {
            "id": [1],
            "rain_0_3d_mm": [8.0],
            "rain_3_7d_mm": [12.0],
            "rain_7_14d_mm": [18.0],
            "temp_mean_3d": [12.0],
            "temp_night_mean_3d": [5.0],
            "rh_night_mean_3d": [90.0],
            "wet_hours_72h": [6.0],
            "hours_since_significant_rain": [0.0],
        },
        geometry=[Point(24.0, 59.0)],
        crs="EPSG:4326",
    )

    result = score_stands(weather_gdf, _utc(2026, 7, 1))

    assert "fruiting_score_chanterelle" in result.columns
    assert "fruiting_score_kitsemampel" in result.columns
    assert "fruiting_moisture_score_chanterelle" in result.columns
    assert "fruiting_season_prior_chanterelle" in result.columns
    assert "fruiting_temperature_modifier" in result.columns  # shared, not per-species
    assert "fruiting_persistence_modifier" in result.columns  # shared, not per-species
    assert result.loc[0, "fruiting_score_chanterelle"] is not None
    assert result.loc[0, "fruiting_score_chanterelle"] > 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_fruiting.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shroom_fm.fruiting'`

- [ ] **Step 3: Implement `src/shroom_fm/fruiting.py`**

```python
"""FruitingScore: a weather-driven, per-species "worth scouting now" signal.

FruitingScore = SeasonPrior x MoistureTrigger x TemperatureModifier x PersistenceModifier

All numeric constants in this module (RAIN_SCALE_MM, MOISTURE_WEIGHTS, temperature and
persistence thresholds, SEASON_PRIORS) are Estonian v0 engineering priors, not measured
mycological constants — see docs/superpowers/specs/2026-08-19-fruiting-score-design.md
for the reasoning. Expected to be recalibrated against real observations later.
"""

import datetime as _datetime
import math

import geopandas as gpd

TARGET_SPECIES = ["kitsemampel", "chanterelle", "aspen_bolete", "birch_bolete", "porcini"]


def _is_missing(value) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


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


def moisture_trigger(
    species: str,
    rain_0_3d: float | None,
    rain_3_7d: float | None,
    rain_7_14d: float | None,
) -> float | None:
    if _is_missing(rain_0_3d) or _is_missing(rain_3_7d) or _is_missing(rain_7_14d):
        return None
    w = MOISTURE_WEIGHTS[species]
    return (
        w["0_3d"] * rain_response(rain_0_3d, RAIN_SCALE_MM["0_3d"])
        + w["3_7d"] * rain_response(rain_3_7d, RAIN_SCALE_MM["3_7d"])
        + w["7_14d"] * rain_response(rain_7_14d, RAIN_SCALE_MM["7_14d"])
    )


TEMP_FLOOR = 0.4
TEMP_COLD_RAMP_START_C = 2.0
TEMP_COLD_RAMP_END_C = 8.0
TEMP_WARM_RAMP_START_C = 18.0
TEMP_WARM_RAMP_END_C = 26.0
FROST_GUARD_START_C = 2.0
FROST_GUARD_END_C = -2.0
FROST_GUARD_FLOOR = 0.6


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _temperature_base_curve(temp_c: float) -> float:
    if temp_c < TEMP_COLD_RAMP_START_C or temp_c > TEMP_WARM_RAMP_END_C:
        return TEMP_FLOOR
    if temp_c < TEMP_COLD_RAMP_END_C:
        fraction = (temp_c - TEMP_COLD_RAMP_START_C) / (
            TEMP_COLD_RAMP_END_C - TEMP_COLD_RAMP_START_C
        )
        return TEMP_FLOOR + fraction * (1.0 - TEMP_FLOOR)
    if temp_c <= TEMP_WARM_RAMP_START_C:
        return 1.0
    fraction = (temp_c - TEMP_WARM_RAMP_START_C) / (
        TEMP_WARM_RAMP_END_C - TEMP_WARM_RAMP_START_C
    )
    return 1.0 - fraction * (1.0 - TEMP_FLOOR)


def _frost_guard(temp_night_c: float) -> float:
    if temp_night_c >= FROST_GUARD_START_C:
        return 1.0
    if temp_night_c <= FROST_GUARD_END_C:
        return FROST_GUARD_FLOOR
    fraction = (FROST_GUARD_START_C - temp_night_c) / (
        FROST_GUARD_START_C - FROST_GUARD_END_C
    )
    return 1.0 - fraction * (1.0 - FROST_GUARD_FLOOR)


def temperature_modifier(
    temp_mean_3d: float | None, temp_night_mean_3d: float | None
) -> float | None:
    if _is_missing(temp_mean_3d) or _is_missing(temp_night_mean_3d):
        return None
    return _temperature_base_curve(temp_mean_3d) * _frost_guard(temp_night_mean_3d)


PERSISTENCE_FLOOR = 0.6
NIGHT_RH_LOW = 65.0
NIGHT_RH_HIGH = 90.0
WET_HOURS_SCALE = 6.0
SIGNIFICANT_RAIN_HALF_LIFE_HOURS = 72.0


def persistence_modifier(
    rh_night_mean_3d: float | None,
    wet_hours_72h: float | None,
    hours_since_significant_rain: float | None,
) -> float | None:
    if _is_missing(rh_night_mean_3d) or _is_missing(wet_hours_72h):
        return None
    night_rh_score = _clamp(
        (rh_night_mean_3d - NIGHT_RH_LOW) / (NIGHT_RH_HIGH - NIGHT_RH_LOW), 0.0, 1.0
    )
    wet_hours_score = 1.0 - math.exp(-wet_hours_72h / WET_HOURS_SCALE)
    if _is_missing(hours_since_significant_rain):
        recency_score = 0.0
    else:
        recency_score = math.exp(
            -math.log(2) * hours_since_significant_rain / SIGNIFICANT_RAIN_HALF_LIFE_HOURS
        )
    persistence_raw = 0.50 * night_rh_score + 0.20 * wet_hours_score + 0.30 * recency_score
    return PERSISTENCE_FLOOR + (1 - PERSISTENCE_FLOOR) * persistence_raw


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


def _month_day_to_ordinal(month_day: str) -> int:
    month, day = (int(part) for part in month_day.split("-"))
    # Fixed non-leap reference year — no SEASON_PRIORS knot falls on Feb 29, so any
    # non-leap year gives a consistent, comparable day-of-year ordinal.
    return _datetime.date(2001, month, day).toordinal()


def season_prior(species: str, month_day: str) -> float:
    knots = SEASON_PRIORS[species]
    target = _month_day_to_ordinal(month_day)
    knot_ordinals = [_month_day_to_ordinal(md) for md, _ in knots]
    if target <= knot_ordinals[0]:
        return knots[0][1]
    if target >= knot_ordinals[-1]:
        return knots[-1][1]
    for i in range(len(knots) - 1):
        lo_ord, lo_val = knot_ordinals[i], knots[i][1]
        hi_ord, hi_val = knot_ordinals[i + 1], knots[i + 1][1]
        if lo_ord <= target <= hi_ord:
            fraction = (target - lo_ord) / (hi_ord - lo_ord)
            return lo_val + fraction * (hi_val - lo_val)
    return knots[-1][1]


def fruiting_score(
    species: str,
    month_day: str,
    rain_0_3d: float | None,
    rain_3_7d: float | None,
    rain_7_14d: float | None,
    temp_mean_3d: float | None,
    temp_night_mean_3d: float | None,
    rh_night_mean_3d: float | None,
    wet_hours_72h: float | None,
    hours_since_significant_rain: float | None,
) -> tuple[float | None, dict]:
    moisture = moisture_trigger(species, rain_0_3d, rain_3_7d, rain_7_14d)
    temperature = temperature_modifier(temp_mean_3d, temp_night_mean_3d)
    persistence = persistence_modifier(
        rh_night_mean_3d, wet_hours_72h, hours_since_significant_rain
    )
    season = season_prior(species, month_day)

    components = {
        "fruiting_moisture_score": moisture,
        "fruiting_temperature_modifier": temperature,
        "fruiting_persistence_modifier": persistence,
        "fruiting_season_prior": season,
    }

    if moisture is None or temperature is None or persistence is None:
        return None, components

    return season * moisture * temperature * persistence, components


def score_stands(weather_gdf: "gpd.GeoDataFrame", now) -> "gpd.GeoDataFrame":
    result = weather_gdf.copy()
    month_day = now.strftime("%m-%d")

    temp_modifiers = []
    persistence_modifiers = []
    for _, row in result.iterrows():
        temp_modifiers.append(
            temperature_modifier(row["temp_mean_3d"], row["temp_night_mean_3d"])
        )
        persistence_modifiers.append(
            persistence_modifier(
                row["rh_night_mean_3d"],
                row["wet_hours_72h"],
                row["hours_since_significant_rain"],
            )
        )
    result["fruiting_temperature_modifier"] = temp_modifiers
    result["fruiting_persistence_modifier"] = persistence_modifiers

    for species in TARGET_SPECIES:
        scores = []
        moisture_scores = []
        season_priors = []
        for _, row in result.iterrows():
            score, components = fruiting_score(
                species,
                month_day,
                row["rain_0_3d_mm"],
                row["rain_3_7d_mm"],
                row["rain_7_14d_mm"],
                row["temp_mean_3d"],
                row["temp_night_mean_3d"],
                row["rh_night_mean_3d"],
                row["wet_hours_72h"],
                row["hours_since_significant_rain"],
            )
            scores.append(score)
            moisture_scores.append(components["fruiting_moisture_score"])
            season_priors.append(components["fruiting_season_prior"])
        result[f"fruiting_score_{species}"] = scores
        result[f"fruiting_moisture_score_{species}"] = moisture_scores
        result[f"fruiting_season_prior_{species}"] = season_priors

    return result
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_fruiting.py -v`
Expected: 20 passed

- [ ] **Step 5: Commit**

```bash
git add src/shroom_fm/fruiting.py tests/test_fruiting.py
git commit -m "feat: add FruitingScore core scoring module"
```

---

### Task 4: `scripts/score_fruiting.py`

**Files:**
- Create: `scripts/score_fruiting.py`

**Interfaces:**
- Consumes: `fruiting.score_stands(weather_gdf, now) -> gpd.GeoDataFrame` from Task 3.

- [ ] **Step 1: Implement**

```python
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd

from shroom_fm.fruiting import score_stands

WEATHER_PATH = Path("data/weather_eraldis.geojson")


def main() -> None:
    weather_gdf = gpd.read_file(WEATHER_PATH)

    if "rain_0_3d_mm" not in weather_gdf.columns:
        raise RuntimeError(
            f"{WEATHER_PATH} has no rain_0_3d_mm column — "
            "run scripts/refresh_weather.py first."
        )

    now = datetime.now(timezone.utc)
    scored = score_stands(weather_gdf, now)
    scored.to_file(WEATHER_PATH, driver="GeoJSON")

    print(f"{len(scored)} stands fruiting-scored, saved to {WEATHER_PATH}")


if __name__ == "__main__":
    main()
```

This script has no dedicated test file, matching this project's established precedent
for thin orchestrator scripts (`download_eraldis.py`, `score_habitat.py`, etc. — all the
real logic lives in the tested `fruiting.py` module this script just calls).

- [ ] **Step 2: Verify it imports and runs against a syntactically valid (if not
  necessarily real) `data/weather_eraldis.geojson`**

Run: `uv run python3 -c "import scripts.score_fruiting"` — confirms no import errors.
(Real end-to-end verification against production data happens in Task 7.)

- [ ] **Step 3: Commit**

```bash
git add scripts/score_fruiting.py
git commit -m "feat: add score_fruiting.py pipeline script"
```

---

### Task 5: `scripts/score_ecotone_fruiting.py`

**Files:**
- Create: `scripts/score_ecotone_fruiting.py`
- Test: `tests/test_score_ecotone_fruiting.py` (the join logic is small but real enough
  to deserve a direct test rather than living untested inside a script — matching how
  `score_ecotone_habitat.py`'s equivalent logic lives in the tested `habitat.py` module;
  here, since the averaging logic is specific to this one script and not reused
  elsewhere, put the tested function in `src/shroom_fm/fruiting.py` instead of the
  script, and keep the script itself a thin orchestrator)

**Interfaces:**
- Produces (in `src/shroom_fm/fruiting.py`, added to the file from Task 3):
  `join_ecotone_fruiting(ecotones_gdf, weather_gdf) -> gpd.GeoDataFrame` — adds
  `fruiting_modifier_{species}` for each of `TARGET_SPECIES` onto `ecotones_gdf`, as the
  average of the two stands' `fruiting_score_{species}` (both stands' values must be
  non-`None` for the average to be non-`None` — if either stand's `fruiting_score` is
  `None`, the ecotone's `fruiting_modifier` is `None` too, never a partial/fabricated
  average from just one side).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_score_ecotone_fruiting.py`:

```python
import geopandas as gpd
import pytest
from shapely.geometry import Point

from shroom_fm.fruiting import join_ecotone_fruiting


def test_join_ecotone_fruiting_averages_both_stands():
    ecotones_gdf = gpd.GeoDataFrame(
        {"id_a": [1], "id_b": [2]}, geometry=[Point(0, 0)], crs="EPSG:3301"
    )
    weather_gdf = gpd.GeoDataFrame(
        {
            "id": [1, 2],
            "fruiting_score_chanterelle": [0.8, 0.4],
        },
        geometry=[Point(0, 0), Point(10, 10)],
        crs="EPSG:3301",
    )

    result = join_ecotone_fruiting(ecotones_gdf, weather_gdf)

    assert result.loc[0, "fruiting_modifier_chanterelle"] == pytest.approx(0.6)


def test_join_ecotone_fruiting_is_none_when_either_stand_missing_score():
    ecotones_gdf = gpd.GeoDataFrame(
        {"id_a": [1], "id_b": [2]}, geometry=[Point(0, 0)], crs="EPSG:3301"
    )
    weather_gdf = gpd.GeoDataFrame(
        {
            "id": [1, 2],
            "fruiting_score_chanterelle": [0.8, None],
        },
        geometry=[Point(0, 0), Point(10, 10)],
        crs="EPSG:3301",
    )

    result = join_ecotone_fruiting(ecotones_gdf, weather_gdf)

    assert result.loc[0, "fruiting_modifier_chanterelle"] is None


def test_join_ecotone_fruiting_is_none_when_referenced_stand_missing_entirely():
    ecotones_gdf = gpd.GeoDataFrame(
        {"id_a": [1], "id_b": [999]}, geometry=[Point(0, 0)], crs="EPSG:3301"
    )
    weather_gdf = gpd.GeoDataFrame(
        {"id": [1], "fruiting_score_chanterelle": [0.8]},
        geometry=[Point(0, 0)],
        crs="EPSG:3301",
    )

    result = join_ecotone_fruiting(ecotones_gdf, weather_gdf)

    assert result.loc[0, "fruiting_modifier_chanterelle"] is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_score_ecotone_fruiting.py -v`
Expected: FAIL — `ImportError: cannot import name 'join_ecotone_fruiting'`

- [ ] **Step 3: Implement**

Add to `src/shroom_fm/fruiting.py` (append at the end of the file, after `score_stands`):

```python
def _none_if_nan(value):
    import pandas as pd
    return None if pd.isna(value) else value


def join_ecotone_fruiting(
    ecotones_gdf: "gpd.GeoDataFrame", weather_gdf: "gpd.GeoDataFrame"
) -> "gpd.GeoDataFrame":
    result = ecotones_gdf.copy()

    for species in TARGET_SPECIES:
        score_col = f"fruiting_score_{species}"
        scores_by_id = dict(zip(weather_gdf["id"], weather_gdf[score_col]))
        modifiers = []
        for id_a, id_b in zip(result["id_a"], result["id_b"]):
            score_a = _none_if_nan(scores_by_id.get(id_a))
            score_b = _none_if_nan(scores_by_id.get(id_b))
            if score_a is None or score_b is None:
                modifiers.append(None)
            else:
                modifiers.append((score_a + score_b) / 2.0)
        result[f"fruiting_modifier_{species}"] = modifiers

    return result
```

Create `scripts/score_ecotone_fruiting.py`:

```python
from pathlib import Path

import geopandas as gpd

from shroom_fm.fruiting import join_ecotone_fruiting

ECOTONES_PATH = Path("data/ecotones.geojson")
WEATHER_PATH = Path("data/weather_eraldis.geojson")


def main() -> None:
    ecotones_gdf = gpd.read_file(ECOTONES_PATH)
    weather_gdf = gpd.read_file(WEATHER_PATH)

    if not any(col.startswith("fruiting_score_") for col in weather_gdf.columns):
        raise RuntimeError(
            f"{WEATHER_PATH} has no fruiting_score_* columns — "
            "run scripts/score_fruiting.py first."
        )

    scored = join_ecotone_fruiting(ecotones_gdf, weather_gdf)
    scored.to_file(ECOTONES_PATH, driver="GeoJSON")

    print(f"{len(scored)} ecotone pairs fruiting-scored, saved to {ECOTONES_PATH}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_score_ecotone_fruiting.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/shroom_fm/fruiting.py scripts/score_ecotone_fruiting.py tests/test_score_ecotone_fruiting.py
git commit -m "feat: add join_ecotone_fruiting and score_ecotone_fruiting.py"
```

---

### Task 6: `ScoutScore` v1 — third factor, `MISSING_FRUITING_DATA`, run-level coverage guard

**Files:**
- Modify: `src/shroom_fm/scout.py`
- Modify: `tests/test_scout.py`
- Modify: `scripts/export_scout_candidates.py`

**Interfaces:**
- Modifies: `scout_score(ecotone_score, access_modifier, fruiting_modifier, eligible) -> float | None`
  (adds the `fruiting_modifier` parameter — 3rd positional, before `eligible`).
- Modifies: `scout_candidates_for_species(joined_gdf, species, top_n) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]`
  — same signature and return shape, but `joined_gdf` must now also carry
  `fruiting_modifier_{species}` (from Task 5's `join_ecotone_fruiting`), and the
  `remote` tier's `exclusion_reason` can now be `"MISSING_FRUITING_DATA"` in addition to
  the existing `REMOTE_EXCLUSION_REASON`.
- Produces: `MISSING_FRUITING_DATA_REASON = "MISSING_FRUITING_DATA"` constant;
  `weather_coverage_ratio(joined_gdf, species) -> float` (new, in `scout.py`);
  `MIN_SCOUT_WEATHER_COVERAGE = 0.90` constant.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_scout.py` (near the existing `scout_score`/
`scout_candidates_for_species` tests):

```python
def test_scout_score_multiplies_all_three_factors_when_eligible():
    assert scout_score(1.2, 0.5, 0.8, True) == pytest.approx(0.48)


def test_scout_score_is_none_when_fruiting_modifier_missing():
    assert scout_score(1.2, 0.5, None, True) is None


def test_scout_candidates_for_species_reports_missing_fruiting_data_reason():
    joined_gdf = gpd.GeoDataFrame(
        {
            "ecotone_score_chanterelle": [1.5, 1.2],
            "access_modifier": [0.8, 0.9],
            "fruiting_modifier_chanterelle": [0.7, None],
            "scout_eligible": [True, True],
        },
        geometry=[Point(0, 0), Point(1, 0)],
        crs="EPSG:3301",
    )

    ranked, remote = scout_candidates_for_species(joined_gdf, "chanterelle", top_n=5)

    assert len(ranked) == 1
    assert len(remote) == 1
    assert remote.iloc[0]["exclusion_reason"] == "MISSING_FRUITING_DATA"
    assert remote.iloc[0]["ecotone_score"] == pytest.approx(1.2)


def test_scout_candidates_for_species_access_ineligibility_takes_precedence_over_missing_fruiting():
    joined_gdf = gpd.GeoDataFrame(
        {
            "ecotone_score_chanterelle": [1.5],
            "access_modifier": [0.0],
            "fruiting_modifier_chanterelle": [None],  # both problems apply at once
            "scout_eligible": [False],
        },
        geometry=[Point(0, 0)],
        crs="EPSG:3301",
    )

    ranked, remote = scout_candidates_for_species(joined_gdf, "chanterelle", top_n=5)

    assert len(ranked) == 0
    assert len(remote) == 1
    assert remote.iloc[0]["exclusion_reason"] == REMOTE_EXCLUSION_REASON


def test_weather_coverage_ratio_computes_fraction_with_fruiting_data():
    joined_gdf = gpd.GeoDataFrame(
        {
            "ecotone_score_chanterelle": [1.0, 1.0, 1.0, None],
            "access_modifier": [0.5, 0.5, 0.5, 0.5],
            "fruiting_modifier_chanterelle": [0.5, None, 0.5, 0.5],
            "scout_eligible": [True, True, False, True],
        },
        geometry=[Point(i, 0) for i in range(4)],
        crs="EPSG:3301",
    )
    # Eligible pool (non-null ecotone_score AND scout_eligible): rows 0, 1 (row 2 is
    # ecologically scored but access-ineligible; row 3 has no ecotone_score at all).
    # Of that pool of 2, row 0 has fruiting data, row 1 doesn't -> ratio 0.5.
    ratio = weather_coverage_ratio(joined_gdf, "chanterelle")
    assert ratio == pytest.approx(0.5)


def test_weather_coverage_ratio_is_one_when_no_eligible_candidates_exist():
    joined_gdf = gpd.GeoDataFrame(
        {
            "ecotone_score_chanterelle": [None],
            "access_modifier": [0.5],
            "fruiting_modifier_chanterelle": [None],
            "scout_eligible": [False],
        },
        geometry=[Point(0, 0)],
        crs="EPSG:3301",
    )
    # No candidates are even eligible to begin with — vacuously "fully covered",
    # not a coverage problem to report.
    assert weather_coverage_ratio(joined_gdf, "chanterelle") == pytest.approx(1.0)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_scout.py -v`
Expected: the new tests FAIL (`TypeError` on `scout_score`'s new required argument;
`ImportError`/`AttributeError` for `weather_coverage_ratio`); the 2 existing
`scout_score`-related tests that call it with the OLD 3-arg signature
(`test_scout_score_multiplies_when_eligible`, `test_scout_score_is_none_when_ineligible`,
`test_scout_score_is_none_when_ecotone_score_missing`,
`test_scout_score_is_none_when_access_modifier_missing`) will also fail once you
implement Step 3 — update those 4 existing tests' calls to pass a `fruiting_modifier`
argument (e.g. `scout_score(1.2, 0.5, 1.0, True)` for the ones that expect a real
result, `scout_score(1.2, 0.5, 1.0, False)` for the ineligible one, etc. — keep each
existing test's original intent, just add a neutral non-`None` `fruiting_modifier` value
so the new required parameter doesn't change what each test is actually checking).

- [ ] **Step 3: Implement in `src/shroom_fm/scout.py`**

Replace `scout_score` and `scout_candidates_for_species`, and add
`MISSING_FRUITING_DATA_REASON`, `MIN_SCOUT_WEATHER_COVERAGE`, and
`weather_coverage_ratio`:

```python
MISSING_FRUITING_DATA_REASON = "MISSING_FRUITING_DATA"
MIN_SCOUT_WEATHER_COVERAGE = 0.90


def scout_score(
    ecotone_score: float | None,
    access_modifier: float | None,
    fruiting_modifier: float | None,
    eligible: bool,
) -> float | None:
    if (
        not eligible
        or ecotone_score is None
        or access_modifier is None
        or fruiting_modifier is None
    ):
        return None
    return ecotone_score * access_modifier * fruiting_modifier


def scout_candidates_for_species(
    joined_gdf: gpd.GeoDataFrame, species: str, top_n: int
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    ecotone_col = f"ecotone_score_{species}"
    fruiting_col = f"fruiting_modifier_{species}"
    scored = joined_gdf[joined_gdf[ecotone_col].notna()].copy()
    scored["ecotone_score"] = scored[ecotone_col]
    scored["fruiting_score"] = scored[fruiting_col]
    scored["scout_score"] = [
        scout_score(ecotone_score_value, access_modifier_value, fruiting_value, eligible)
        for ecotone_score_value, access_modifier_value, fruiting_value, eligible in zip(
            scored["ecotone_score"],
            scored["access_modifier"],
            scored["fruiting_score"],
            scored["scout_eligible"],
        )
    ]

    def _exclusion_reason(eligible, fruiting_value):
        if not eligible:
            return REMOTE_EXCLUSION_REASON
        return MISSING_FRUITING_DATA_REASON

    ranked = (
        scored[scored["scout_score"].notna()]
        .sort_values("scout_score", ascending=False)
        .head(top_n)
    )
    excluded = scored[scored["scout_score"].isna()].copy()
    excluded["exclusion_reason"] = [
        _exclusion_reason(eligible, fruiting_value)
        for eligible, fruiting_value in zip(
            excluded["scout_eligible"], excluded["fruiting_score"]
        )
    ]
    remote = excluded.sort_values("ecotone_score", ascending=False).head(top_n)
    return ranked, remote


def weather_coverage_ratio(joined_gdf: gpd.GeoDataFrame, species: str) -> float:
    ecotone_col = f"ecotone_score_{species}"
    fruiting_col = f"fruiting_modifier_{species}"
    eligible_pool = joined_gdf[
        joined_gdf[ecotone_col].notna() & (joined_gdf["scout_eligible"] == True)  # noqa: E712
    ]
    if len(eligible_pool) == 0:
        return 1.0
    with_fruiting_data = eligible_pool[eligible_pool[fruiting_col].notna()]
    return len(with_fruiting_data) / len(eligible_pool)
```

(`== True` with `# noqa: E712` rather than plain truthiness, because `scout_eligible` is
stored as an `object`-dtype column holding real Python `bool`/`numpy.bool_` values per
`join_ecotone_access`'s existing comment about avoiding `numpy.bool_` identity pitfalls —
using `.astype(bool)` first is an equally valid alternative if you prefer; either works,
just don't use `is True` on a Series element-wise, which doesn't vectorize.)

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_scout.py -v`
Expected: all pass (11 existing + 6 new = 17 — note 4 existing tests' call sites changed
in Step 1/2, not counted as new, but the file's total test count goes from 11 to 17).

- [ ] **Step 5: Update `scripts/export_scout_candidates.py`**

Full replacement of the file:

```python
from pathlib import Path

import geopandas as gpd
import pandas as pd

from shroom_fm.habitat import TARGET_SPECIES
from shroom_fm.scout import (
    MIN_SCOUT_WEATHER_COVERAGE,
    join_ecotone_access,
    scout_candidates_for_species,
    weather_coverage_ratio,
)
from shroom_fm.fruiting import join_ecotone_fruiting

TOP_N = 10
ERALDIS_PATH = Path("data/eraldis.geojson")
ECOTONES_PATH = Path("data/ecotones.geojson")
WEATHER_PATH = Path("data/weather_eraldis.geojson")
OUTPUT_PATH = Path("data/scout_candidates.geojson")

OUTPUT_COLUMNS = [
    "species",
    "tier",
    "rank",
    "scout_score",
    "ecotone_score",
    "access_modifier",
    "access_confidence",
    "access_reason",
    "nearest_car_road_m",
    "fruiting_score",
    "exclusion_reason",
    "transition_length_m",
    "dominant_species_a",
    "dominant_species_b",
    "id_a",
    "id_b",
    "geometry",
]


def main() -> None:
    eraldis_gdf = gpd.read_file(ERALDIS_PATH)
    ecotones_gdf = gpd.read_file(ECOTONES_PATH)
    weather_gdf = gpd.read_file(WEATHER_PATH)

    joined = join_ecotone_access(ecotones_gdf, eraldis_gdf)
    joined = join_ecotone_fruiting(joined, weather_gdf)

    rows = []
    for species in TARGET_SPECIES:
        ratio = weather_coverage_ratio(joined, species)
        if ratio < MIN_SCOUT_WEATHER_COVERAGE:
            print(
                f"Scout ranking unavailable for {species}: weather coverage "
                f"{ratio:.1%}, required >= {MIN_SCOUT_WEATHER_COVERAGE:.0%}"
            )
            continue

        ranked, remote = scout_candidates_for_species(joined, species, TOP_N)

        ranked = ranked.copy()
        ranked["species"] = species
        ranked["tier"] = "ranked"
        ranked["rank"] = range(1, len(ranked) + 1)
        ranked["exclusion_reason"] = None

        remote = remote.copy()
        remote["species"] = species
        remote["tier"] = "remote_high_value"
        remote["rank"] = range(1, len(remote) + 1)

        rows.append(ranked)
        rows.append(remote)

    if not rows:
        print(
            "Scout ranking unavailable for all species: weather coverage too low. "
            f"No {OUTPUT_PATH} written — refusing to publish an untrustworthy ranking."
        )
        raise SystemExit(1)

    combined = gpd.GeoDataFrame(pd.concat(rows, ignore_index=True), crs=ecotones_gdf.crs)
    combined = combined[OUTPUT_COLUMNS]

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    combined.to_file(OUTPUT_PATH, driver="GeoJSON")
    print(
        f"{len(combined)} scout candidates across {len(rows) // 2} species "
        f"saved to {OUTPUT_PATH}"
    )


if __name__ == "__main__":
    main()
```

Per-species coverage gating (skip that species, keep going for others) rather than an
all-or-nothing run-level check — this matches the spec's actual failure mode more
precisely: weather coverage is the same underlying signal for every species (same
`weather_eraldis.geojson`), so in practice all 5 species will usually pass or fail
together, but computing the ratio once per species (using each species' own
`ecotone_score_{species}`-eligible pool) is more correct than assuming they're
identical, and costs nothing extra to compute.

- [ ] **Step 6: Commit**

```bash
git add src/shroom_fm/scout.py tests/test_scout.py scripts/export_scout_candidates.py
git commit -m "feat: add fruiting_modifier to ScoutScore v1, MISSING_FRUITING_DATA reason, coverage guard"
```

---

### Task 7: Real-scale verification and CLAUDE.md update

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest tests/ -q`
Expected: all tests pass (record the exact final count from this run — starting from
the 182-test baseline, Task 1 added net +2 (24 total in test_radar.py's new tests minus
the 1 modified-not-added existing one, i.e. +3 new tests), Task 2 added +4, Task 3 added
+20, Task 5 added +3, Task 6 added +6, so expect roughly 182+3+4+20+3+6 = 218 — confirm
the actual number from the real run rather than trusting this arithmetic blindly, since
this project's history shows plan-time test-count arithmetic has been wrong before).

- [ ] **Step 2: Run the real pipeline against production data**

This requires `data/weather_eraldis.geojson` and `data/ecotones.geojson` to already
exist (from a prior real `scripts/refresh_weather.py` and the earlier pipeline steps).
Run, in order:

```bash
time uv run python scripts/score_fruiting.py
time uv run python scripts/score_ecotone_fruiting.py
time uv run python scripts/export_scout_candidates.py
```

Confirm: `score_fruiting.py` reports a real stand count matching
`data/weather_eraldis.geojson`'s row count; `score_ecotone_fruiting.py` reports a real
ecotone count; `export_scout_candidates.py` either reports real scout candidates across
5 species, OR — if real weather coverage happens to be below
`MIN_SCOUT_WEATHER_COVERAGE` for some/all species at the time this runs — reports the
honest "Scout ranking unavailable" message for those species rather than a fabricated
ranking. Either outcome is an acceptable, correct result; report which one actually
happened and why (check the real `weather_data_quality`/`weather_data_coverage` columns
on `data/weather_eraldis.geojson` to explain it).

Spot-check `data/scout_candidates.geojson` (if produced): pick 2-3 rows, confirm
`fruiting_score` is a plausible number in `[0, 1]` for `ranked` tier rows, confirm any
`remote_high_value` rows with `exclusion_reason = "MISSING_FRUITING_DATA"` genuinely
have a real ecological score but a missing/degraded weather signal (cross-check against
`data/weather_eraldis.geojson`'s `weather_data_quality` for that stand).

- [ ] **Step 3: Update CLAUDE.md**

Add a new subsection after the existing "Weather refresh" section, documenting
`FruitingScore` as now real (no longer deferred), the new pipeline steps
(`score_fruiting.py`, `score_ecotone_fruiting.py`), the `MISSING_FRUITING_DATA`
exclusion reason, the `MIN_SCOUT_WEATHER_COVERAGE` run-level guard, and the real
timing/coverage numbers measured in Step 2. Also update the project status paragraph
near the top of CLAUDE.md (which currently says `FruitingScore` "is not yet built") to
reflect that it's now real, and note explicitly that all `fruiting.py` constants are v0
engineering priors pending calibration against real observations — not to be mistaken
for validated mycological thresholds by a future reader.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document FruitingScore and ScoutScore v1 in CLAUDE.md"
```
