from datetime import datetime, timedelta, timezone

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

from shroom_fm.weather import refresh_weather, weather_data_quality


def _utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def _healthy_radar_coverage():
    return {"3d": 0.9, "7d": 0.9, "14d": 0.9}


# assign_radar_to_eraldis (Task 7/8) does a direct coordinate-range lookup, not
# gpd.sjoin_nearest — so radar/meps point fixtures must land exactly at each test
# stand's own projected (EPSG:3301) location, not merely at some other "nearby"
# round-number coordinate under a different, disconnected origin. Computed here via
# the identical to_crs("EPSG:3301") reprojection refresh_weather performs internally
# on Point(24.0, 59.0) (the single-stand fixtures' shared eraldis coordinate below),
# so the two are guaranteed to agree bit-for-bit rather than relying on a hand-typed
# literal that could silently drift out of sync.
_STAND_XY = tuple(
    gpd.GeoDataFrame(geometry=[Point(24.0, 59.0)], crs="EPSG:4326")
    .to_crs("EPSG:3301")
    .geometry.iloc[0]
    .coords[0]
)


def test_weather_data_quality_is_complete_when_nothing_degraded():
    now = _utc(2026, 8, 18, 12)
    assert (
        weather_data_quality(
            _healthy_radar_coverage(), 0.9, now - timedelta(hours=1), now
        )
        == "complete"
    )


def test_weather_data_quality_flags_partial_radar_gap_3d():
    now = _utc(2026, 8, 18, 12)
    coverage = {"3d": 0.2, "7d": 0.9, "14d": 0.9}
    assert (
        weather_data_quality(coverage, 0.9, now - timedelta(hours=1), now)
        == "partial_radar_gap_3d"
    )


def test_weather_data_quality_flags_partial_radar_gap_7d():
    now = _utc(2026, 8, 18, 12)
    coverage = {"3d": 0.9, "7d": 0.5, "14d": 0.9}
    assert (
        weather_data_quality(coverage, 0.9, now - timedelta(hours=1), now)
        == "partial_radar_gap_7d"
    )


def test_weather_data_quality_flags_partial_radar_gap_14d():
    now = _utc(2026, 8, 18, 12)
    coverage = {"3d": 0.9, "7d": 0.9, "14d": 0.5}
    assert (
        weather_data_quality(coverage, 0.9, now - timedelta(hours=1), now)
        == "partial_radar_gap_14d"
    )


def test_weather_data_quality_flags_all_radar_windows_when_all_degraded():
    now = _utc(2026, 8, 18, 12)
    coverage = {"3d": 0.1, "7d": 0.2, "14d": 0.3}
    assert (
        weather_data_quality(coverage, 0.9, now - timedelta(hours=1), now)
        == "partial_radar_gap_3d;partial_radar_gap_7d;partial_radar_gap_14d"
    )


def test_weather_data_quality_flags_stale_meps():
    now = _utc(2026, 8, 18, 12)
    assert (
        weather_data_quality(
            _healthy_radar_coverage(), 0.9, now - timedelta(hours=10), now
        )
        == "stale_meps"
    )


def test_weather_data_quality_flags_missing_meps_as_stale():
    now = _utc(2026, 8, 18, 12)
    assert (
        weather_data_quality(_healthy_radar_coverage(), 0.9, None, now) == "stale_meps"
    )


def test_weather_data_quality_flags_partial_meps_gap_when_not_stale():
    now = _utc(2026, 8, 18, 12)
    assert (
        weather_data_quality(
            _healthy_radar_coverage(), 0.3, now - timedelta(hours=1), now
        )
        == "partial_meps_gap"
    )


def test_weather_data_quality_joins_multiple_flags():
    now = _utc(2026, 8, 18, 12)
    coverage = {"3d": 0.5, "7d": 0.9, "14d": 0.9}
    assert (
        weather_data_quality(coverage, 0.9, None, now)
        == "partial_radar_gap_3d;stale_meps"
    )


def _make_radar_points(coverage_3d=0.9, coverage_7d=0.9, coverage_14d=0.9):
    """Defaults match _healthy_radar_coverage()'s 0.9 so callers that don't care about
    per-stand degradation get the same "healthy" behavior as before Task 8. Per-stand
    degradation is now driven by these coverage_Nd columns on the radar points
    themselves (joined onto each stand by assign_radar_to_eraldis), not by the
    dataset-wide coverage dict accumulate_rainfall used to return — pass explicit
    coverage_* kwargs to construct a degraded-coverage stand."""
    return gpd.GeoDataFrame(
        {
            "rain_3d_mm": [5.0],
            "rain_7d_mm": [10.0],
            "rain_14d_mm": [20.0],
            "hours_since_any_rain": [3.0],
            "wet_hours_72h": [1.0],
            "hours_since_significant_rain": [10.0],
            "hours_since_strong_rain": [20.0],
            "last_significant_event_mm": [6.0],
            "last_strong_event_mm": [12.0],
            "max_24h_rain_14d": [8.0],
            "coverage_3d": [coverage_3d],
            "coverage_7d": [coverage_7d],
            "coverage_14d": [coverage_14d],
        },
        geometry=[Point(*_STAND_XY)],
        crs="EPSG:3301",
    )


def _make_meps_points():
    return gpd.GeoDataFrame(
        {
            "temp_mean_3d": [15.0],
            "temp_night_mean_3d": [10.0],
            "rh_mean_3d": [70.0],
            "rh_night_mean_3d": [85.0],
        },
        geometry=[Point(*_STAND_XY)],
        crs="EPSG:3301",
    )


def test_refresh_weather_joins_nearest_radar_and_meps_points(monkeypatch, tmp_path):
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1]}, geometry=[Point(24.0, 59.0)], crs="EPSG:4326"
    )
    now = _utc(2026, 8, 18, 12)

    radar_points = _make_radar_points()
    meps_points = _make_meps_points()

    monkeypatch.setattr(
        "shroom_fm.weather.accumulate_rainfall",
        lambda cache_dir, now_, bounds: (radar_points, _healthy_radar_coverage()),
    )
    monkeypatch.setattr(
        "shroom_fm.weather.accumulate_meps_features",
        lambda now_, bounds: (meps_points, 0.9, now_ - timedelta(hours=1)),
    )

    result = refresh_weather(eraldis_gdf, tmp_path / "radar_cache", now)

    assert result.loc[0, "rain_3d_mm"] == pytest.approx(5.0)
    assert result.loc[0, "temp_mean_3d"] == pytest.approx(15.0)
    assert result.loc[0, "weather_data_quality"] == "complete"
    assert result.loc[0, "weather_data_coverage"] == pytest.approx(0.9)
    assert result.loc[0, "as_of"] == now
    assert result.crs == "EPSG:4326"


def test_refresh_weather_nulls_degraded_columns(monkeypatch, tmp_path):
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1]}, geometry=[Point(24.0, 59.0)], crs="EPSG:4326"
    )
    now = _utc(2026, 8, 18, 12)

    # All radar windows below MIN_RADAR_COVERAGE (0.7) — set on the radar point's own
    # coverage_Nd columns, since that's what drives per-stand degradation now (the
    # dataset-wide dict accumulate_rainfall's mock returns below is no longer
    # consulted for this decision).
    radar_points = _make_radar_points(coverage_3d=0.2, coverage_7d=0.2, coverage_14d=0.2)
    meps_points = _make_meps_points()

    monkeypatch.setattr(
        "shroom_fm.weather.accumulate_rainfall",
        lambda cache_dir, now_, bounds: (radar_points, _healthy_radar_coverage()),
    )
    monkeypatch.setattr(
        "shroom_fm.weather.accumulate_meps_features",
        lambda now_, bounds: (meps_points, 0.9, now_ - timedelta(hours=1)),
    )

    result = refresh_weather(eraldis_gdf, tmp_path / "radar_cache", now)

    assert result.loc[0, "rain_3d_mm"] is None
    assert result.loc[0, "temp_mean_3d"] == pytest.approx(15.0)  # meps unaffected
    assert (
        result.loc[0, "weather_data_quality"]
        == "partial_radar_gap_3d;partial_radar_gap_7d;partial_radar_gap_14d"
    )


def test_refresh_weather_nulls_only_3d_columns_when_only_3d_window_degraded(
    monkeypatch, tmp_path
):
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1]}, geometry=[Point(24.0, 59.0)], crs="EPSG:4326"
    )
    now = _utc(2026, 8, 18, 12)

    # Only the 3-day window degraded — set on the radar point's own coverage_Nd
    # columns (per-stand now), not the dataset-wide dict accumulate_rainfall's mock
    # returns below (no longer consulted for degradation).
    radar_points = _make_radar_points(coverage_3d=0.2, coverage_7d=0.9, coverage_14d=0.9)
    meps_points = _make_meps_points()

    monkeypatch.setattr(
        "shroom_fm.weather.accumulate_rainfall",
        lambda cache_dir, now_, bounds: (radar_points, _healthy_radar_coverage()),
    )
    monkeypatch.setattr(
        "shroom_fm.weather.accumulate_meps_features",
        lambda now_, bounds: (meps_points, 0.9, now_ - timedelta(hours=1)),
    )

    result = refresh_weather(eraldis_gdf, tmp_path / "radar_cache", now)

    assert result.loc[0, "rain_3d_mm"] is None
    assert result.loc[0, "wet_hours_72h"] is None
    assert result.loc[0, "rain_7d_mm"] == pytest.approx(10.0)
    assert result.loc[0, "rain_14d_mm"] == pytest.approx(20.0)
    assert result.loc[0, "hours_since_any_rain"] == pytest.approx(3.0)
    assert result.loc[0, "weather_data_quality"] == "partial_radar_gap_3d"


def test_refresh_weather_nulls_meps_columns_when_stale(monkeypatch, tmp_path):
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1]}, geometry=[Point(24.0, 59.0)], crs="EPSG:4326"
    )
    now = _utc(2026, 8, 18, 12)

    radar_points = _make_radar_points()
    meps_points = _make_meps_points()

    monkeypatch.setattr(
        "shroom_fm.weather.accumulate_rainfall",
        lambda cache_dir, now_, bounds: (radar_points, _healthy_radar_coverage()),
    )
    monkeypatch.setattr(
        "shroom_fm.weather.accumulate_meps_features",
        # newest available hour is 10h old — beyond MAX_MEPS_STALENESS_HOURS (6)
        lambda now_, bounds: (meps_points, 0.9, now_ - timedelta(hours=10)),
    )

    result = refresh_weather(eraldis_gdf, tmp_path / "radar_cache", now)

    assert result.loc[0, "temp_mean_3d"] is None
    assert result.loc[0, "rh_night_mean_3d"] is None
    assert result.loc[0, "rain_3d_mm"] == pytest.approx(5.0)  # radar unaffected
    assert result.loc[0, "weather_data_quality"] == "stale_meps"


def test_refresh_weather_nulls_meps_columns_when_coverage_below_threshold(
    monkeypatch, tmp_path
):
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1]}, geometry=[Point(24.0, 59.0)], crs="EPSG:4326"
    )
    now = _utc(2026, 8, 18, 12)

    radar_points = _make_radar_points()
    meps_points = _make_meps_points()

    monkeypatch.setattr(
        "shroom_fm.weather.accumulate_rainfall",
        lambda cache_dir, now_, bounds: (radar_points, _healthy_radar_coverage()),
    )
    monkeypatch.setattr(
        "shroom_fm.weather.accumulate_meps_features",
        # not stale (recent), but coverage is below MIN_MEPS_COVERAGE (0.7)
        lambda now_, bounds: (meps_points, 0.3, now_ - timedelta(hours=1)),
    )

    result = refresh_weather(eraldis_gdf, tmp_path / "radar_cache", now)

    assert result.loc[0, "temp_mean_3d"] is None
    assert result.loc[0, "temp_night_mean_3d"] is None
    assert result.loc[0, "rh_mean_3d"] is None
    assert result.loc[0, "rh_night_mean_3d"] is None
    assert result.loc[0, "rain_3d_mm"] == pytest.approx(5.0)  # radar unaffected
    assert result.loc[0, "weather_data_quality"] == "partial_meps_gap"


def test_refresh_weather_nulls_all_columns_when_both_degraded(monkeypatch, tmp_path):
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1]}, geometry=[Point(24.0, 59.0)], crs="EPSG:4326"
    )
    now = _utc(2026, 8, 18, 12)

    # All radar windows degraded — set on the radar point's own coverage_Nd columns
    # (per-stand now), not the dataset-wide dict accumulate_rainfall's mock returns
    # below (no longer consulted for degradation).
    radar_points = _make_radar_points(coverage_3d=0.2, coverage_7d=0.2, coverage_14d=0.2)
    meps_points = _make_meps_points()

    monkeypatch.setattr(
        "shroom_fm.weather.accumulate_rainfall",
        lambda cache_dir, now_, bounds: (radar_points, _healthy_radar_coverage()),
    )
    monkeypatch.setattr(
        "shroom_fm.weather.accumulate_meps_features",
        # 10h old — beyond MAX_MEPS_STALENESS_HOURS (6)
        lambda now_, bounds: (meps_points, 0.9, now_ - timedelta(hours=10)),
    )

    result = refresh_weather(eraldis_gdf, tmp_path / "radar_cache", now)

    assert result.loc[0, "rain_3d_mm"] is None
    assert result.loc[0, "rain_7d_mm"] is None
    assert result.loc[0, "rain_14d_mm"] is None
    assert result.loc[0, "hours_since_any_rain"] is None
    assert result.loc[0, "wet_hours_72h"] is None
    assert result.loc[0, "temp_mean_3d"] is None
    assert result.loc[0, "temp_night_mean_3d"] is None
    assert result.loc[0, "rh_mean_3d"] is None
    assert result.loc[0, "rh_night_mean_3d"] is None
    assert (
        result.loc[0, "weather_data_quality"]
        == "partial_radar_gap_3d;partial_radar_gap_7d;partial_radar_gap_14d;stale_meps"
    )


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
            "coverage_3d": [0.9],
            "coverage_7d": [0.9],
            "coverage_14d": [0.9],
        },
        geometry=[Point(*_STAND_XY)],
        crs="EPSG:3301",
    )
    meps_points = gpd.GeoDataFrame(
        {
            "temp_mean_3d": [15.0],
            "temp_night_mean_3d": [10.0],
            "rh_mean_3d": [70.0],
            "rh_night_mean_3d": [85.0],
        },
        geometry=[Point(*_STAND_XY)],
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
            # 7d window degraded -> both rain_3_7d_mm (needs 3d+7d) and
            # rain_7_14d_mm (needs 7d+14d) must be null; rain_0_3d_mm (needs only
            # 3d) stays real. Set on the radar point's own coverage_Nd columns
            # (per-stand now), not the dataset-wide dict accumulate_rainfall's
            # mock returns below (no longer consulted for degradation).
            "coverage_3d": [0.9],
            "coverage_7d": [0.2],
            "coverage_14d": [0.9],
        },
        geometry=[Point(*_STAND_XY)],
        crs="EPSG:3301",
    )
    meps_points = gpd.GeoDataFrame(
        {
            "temp_mean_3d": [15.0],
            "temp_night_mean_3d": [10.0],
            "rh_mean_3d": [70.0],
            "rh_night_mean_3d": [85.0],
        },
        geometry=[Point(*_STAND_XY)],
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
            "coverage_3d": [0.9],
            "coverage_7d": [0.9],
            "coverage_14d": [0.9],
        },
        geometry=[Point(*_STAND_XY)],
        crs="EPSG:3301",
    )
    meps_points = gpd.GeoDataFrame(
        {
            "temp_mean_3d": [15.0],
            "temp_night_mean_3d": [10.0],
            "rh_mean_3d": [70.0],
            "rh_night_mean_3d": [85.0],
        },
        geometry=[Point(*_STAND_XY)],
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
            "coverage_3d": [0.9],
            "coverage_7d": [0.9],
            "coverage_14d": [0.9],
        },
        geometry=[Point(*_STAND_XY)],
        crs="EPSG:3301",
    )
    meps_points = gpd.GeoDataFrame(
        {
            "temp_mean_3d": [15.0],
            "temp_night_mean_3d": [10.0],
            "rh_mean_3d": [70.0],
            "rh_night_mean_3d": [85.0],
        },
        geometry=[Point(*_STAND_XY)],
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


def test_refresh_weather_uses_per_stand_coverage_not_one_national_value(
    monkeypatch, tmp_path
):
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1, 2]},
        geometry=[Point(24.0, 59.0), Point(25.0, 59.5)],
        crs="EPSG:4326",
    )
    now = _utc(2026, 8, 18, 12)

    # Radar/MEPS points colocated exactly with each stand's own projected (EPSG:3301)
    # location. assign_radar_to_eraldis does a direct coordinate-range lookup (no
    # sjoin_nearest fallback for unbounded distance — that's the whole point of the
    # migration), so a fixture point must actually land at the stand's own geometry,
    # not merely "nearby" under some other, disconnected set of round-number
    # coordinates — computed here via the same to_crs("EPSG:3301") reprojection
    # refresh_weather itself performs internally, rather than hand-picked, so the two
    # are guaranteed to agree.
    stand_projected = eraldis_gdf.to_crs("EPSG:3301")
    stand_points = [
        Point(geom.x, geom.y) for geom in stand_projected.geometry
    ]

    # Two real radar points: one with full coverage, one with degraded coverage —
    # this is the whole point of per-stand coverage vs the old national-only version.
    radar_points = gpd.GeoDataFrame(
        {
            "rain_3d_mm": [5.0, 5.0],
            "rain_7d_mm": [10.0, 10.0],
            "rain_14d_mm": [20.0, 20.0],
            "hours_since_any_rain": [3.0, 3.0],
            "wet_hours_72h": [1.0, 1.0],
            "hours_since_significant_rain": [10.0, 10.0],
            "hours_since_strong_rain": [20.0, 20.0],
            "last_significant_event_mm": [6.0, 6.0],
            "last_strong_event_mm": [12.0, 12.0],
            "max_24h_rain_14d": [8.0, 8.0],
            "coverage_3d": [1.0, 0.2],
            "coverage_7d": [1.0, 0.2],
            "coverage_14d": [1.0, 0.2],
        },
        geometry=stand_points,
        crs="EPSG:3301",
    )
    meps_points = gpd.GeoDataFrame(
        {
            "temp_mean_3d": [15.0, 15.0],
            "temp_night_mean_3d": [10.0, 10.0],
            "rh_mean_3d": [70.0, 70.0],
            "rh_night_mean_3d": [85.0, 85.0],
        },
        geometry=stand_points,
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

    # Stand 0: fully covered -> real rain values
    assert result.loc[0, "rain_3d_mm"] == pytest.approx(5.0)
    assert result.loc[0, "weather_data_coverage"] == pytest.approx(1.0)
    # Stand 1: degraded coverage (0.2 < MIN_RADAR_COVERAGE=0.7) -> nulled, INDEPENDENTLY
    # of stand 0 being fine — this is the per-stand behavior the old single national
    # coverage dict could never express. Asserted via pd.isna() rather than `is None`:
    # once a column genuinely mixes a real float (stand 0) with a missing value
    # (stand 1) across rows — itself only possible now that degradation is per-stand —
    # pandas silently upcasts the column to float64 and a Python None becomes NaN, not
    # a preserved None sentinel; pd.isna() is the correct/only reliable predicate for
    # "missing" on such a column, and is exactly what this module's own internals
    # (_bin_difference, _null_if_degraded_per_stand) already use throughout.
    assert pd.isna(result.loc[1, "rain_3d_mm"])
    assert result.loc[1, "weather_data_coverage"] == pytest.approx(0.2)
