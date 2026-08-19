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


def _make_radar_points():
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
        },
        geometry=[Point(500000, 6500000)],
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
        geometry=[Point(500000, 6500000)],
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

    radar_points = _make_radar_points()
    meps_points = _make_meps_points()

    coverage = {"3d": 0.2, "7d": 0.2, "14d": 0.2}  # all radar windows below threshold
    monkeypatch.setattr(
        "shroom_fm.weather.accumulate_rainfall",
        lambda cache_dir, now_, bounds: (radar_points, coverage),
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

    radar_points = _make_radar_points()
    meps_points = _make_meps_points()

    coverage = {"3d": 0.2, "7d": 0.9, "14d": 0.9}  # only the 3-day window degraded
    monkeypatch.setattr(
        "shroom_fm.weather.accumulate_rainfall",
        lambda cache_dir, now_, bounds: (radar_points, coverage),
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

    radar_points = _make_radar_points()
    meps_points = _make_meps_points()

    coverage = {"3d": 0.2, "7d": 0.2, "14d": 0.2}
    monkeypatch.setattr(
        "shroom_fm.weather.accumulate_rainfall",
        lambda cache_dir, now_, bounds: (radar_points, coverage),
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
