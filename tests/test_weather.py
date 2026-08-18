from datetime import datetime, timedelta, timezone

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

from shroom_fm.weather import refresh_weather, weather_data_quality


def _utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def test_weather_data_quality_is_complete_when_nothing_degraded():
    now = _utc(2026, 8, 18, 12)
    assert weather_data_quality(0.9, now - timedelta(hours=1), now) == "complete"


def test_weather_data_quality_flags_partial_radar_gap():
    now = _utc(2026, 8, 18, 12)
    assert (
        weather_data_quality(0.5, now - timedelta(hours=1), now)
        == "partial_radar_gap"
    )


def test_weather_data_quality_flags_stale_meps():
    now = _utc(2026, 8, 18, 12)
    assert weather_data_quality(0.9, now - timedelta(hours=10), now) == "stale_meps"


def test_weather_data_quality_flags_missing_meps_as_stale():
    now = _utc(2026, 8, 18, 12)
    assert weather_data_quality(0.9, None, now) == "stale_meps"


def test_weather_data_quality_joins_multiple_flags():
    now = _utc(2026, 8, 18, 12)
    assert (
        weather_data_quality(0.5, None, now)
        == "partial_radar_gap;stale_meps"
    )


def test_refresh_weather_joins_nearest_radar_and_meps_points(monkeypatch, tmp_path):
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1]}, geometry=[Point(24.0, 59.0)], crs="EPSG:4326"
    )
    now = _utc(2026, 8, 18, 12)

    radar_points = gpd.GeoDataFrame(
        {
            "rain_3d_mm": [5.0],
            "rain_7d_mm": [10.0],
            "rain_14d_mm": [20.0],
            "hours_since_rain": [3.0],
            "wet_hours_72h": [1.0],
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
        lambda cache_dir, now_, bounds: (radar_points, 0.9),
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

    radar_points = gpd.GeoDataFrame(
        {
            "rain_3d_mm": [5.0],
            "rain_7d_mm": [10.0],
            "rain_14d_mm": [20.0],
            "hours_since_rain": [3.0],
            "wet_hours_72h": [1.0],
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
        lambda cache_dir, now_, bounds: (radar_points, 0.2),  # below threshold
    )
    monkeypatch.setattr(
        "shroom_fm.weather.accumulate_meps_features",
        lambda now_, bounds: (meps_points, 0.9, now_ - timedelta(hours=1)),
    )

    result = refresh_weather(eraldis_gdf, tmp_path / "radar_cache", now)

    assert result.loc[0, "rain_3d_mm"] is None
    assert result.loc[0, "temp_mean_3d"] == pytest.approx(15.0)  # meps unaffected
    assert result.loc[0, "weather_data_quality"] == "partial_radar_gap"


def test_refresh_weather_nulls_meps_columns_when_stale(monkeypatch, tmp_path):
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1]}, geometry=[Point(24.0, 59.0)], crs="EPSG:4326"
    )
    now = _utc(2026, 8, 18, 12)

    radar_points = gpd.GeoDataFrame(
        {
            "rain_3d_mm": [5.0],
            "rain_7d_mm": [10.0],
            "rain_14d_mm": [20.0],
            "hours_since_rain": [3.0],
            "wet_hours_72h": [1.0],
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
        lambda cache_dir, now_, bounds: (radar_points, 0.9),
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
