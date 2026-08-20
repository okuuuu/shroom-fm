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


def test_join_ecotone_fruiting_does_not_leak_stale_modifier_when_species_column_missing():
    # ecotones_gdf already carries a fruiting_modifier_chanterelle column from a
    # previous run of this function/script; weather_gdf lacks fruiting_score_
    # chanterelle entirely (e.g. refresh_weather.py ran but score_fruiting.py
    # hasn't been re-run yet). The stale value must not survive into the result.
    ecotones_gdf = gpd.GeoDataFrame(
        {"id_a": [1], "id_b": [2], "fruiting_modifier_chanterelle": [0.75]},
        geometry=[Point(0, 0)],
        crs="EPSG:3301",
    )
    weather_gdf = gpd.GeoDataFrame(
        {"id": [1, 2], "fruiting_score_kitsemampel": [0.5, 0.5]},
        geometry=[Point(0, 0), Point(10, 10)],
        crs="EPSG:3301",
    )

    result = join_ecotone_fruiting(ecotones_gdf, weather_gdf)

    assert result.loc[0, "fruiting_modifier_chanterelle"] is None


def test_join_ecotone_fruiting_weather_meta_worst_of_two():
    ecotones_gdf = gpd.GeoDataFrame(
        {"id_a": [1], "id_b": [2]}, geometry=[Point(0, 0)], crs="EPSG:3301"
    )
    weather_gdf = gpd.GeoDataFrame(
        {
            "id": [1, 2],
            "fruiting_score_chanterelle": [0.8, 0.4],
            "weather_data_quality": ["complete", "degraded_14d"],
            "weather_data_coverage": [1.0, 0.72],
            "as_of": ["2026-08-20T10:00:00+00:00", "2026-08-19T08:00:00+00:00"],
        },
        geometry=[Point(0, 0), Point(10, 10)],
        crs="EPSG:3301",
    )

    result = join_ecotone_fruiting(ecotones_gdf, weather_gdf)

    # quality: the non-"complete" (more degraded) value wins.
    assert result.loc[0, "weather_data_quality"] == "degraded_14d"
    # coverage: the minimum (more pessimistic) value wins.
    assert result.loc[0, "weather_data_coverage"] == pytest.approx(0.72)
    # as_of: the earlier (older, more stale) value wins.
    assert result.loc[0, "weather_as_of"] == "2026-08-19T08:00:00+00:00"


def test_join_ecotone_fruiting_weather_meta_both_complete():
    ecotones_gdf = gpd.GeoDataFrame(
        {"id_a": [1], "id_b": [2]}, geometry=[Point(0, 0)], crs="EPSG:3301"
    )
    weather_gdf = gpd.GeoDataFrame(
        {
            "id": [1, 2],
            "fruiting_score_chanterelle": [0.8, 0.4],
            "weather_data_quality": ["complete", "complete"],
            "weather_data_coverage": [1.0, 1.0],
            "as_of": ["2026-08-20T10:00:00+00:00", "2026-08-20T10:00:00+00:00"],
        },
        geometry=[Point(0, 0), Point(10, 10)],
        crs="EPSG:3301",
    )

    result = join_ecotone_fruiting(ecotones_gdf, weather_gdf)

    assert result.loc[0, "weather_data_quality"] == "complete"


def test_join_ecotone_fruiting_weather_meta_none_when_stand_missing_entirely():
    ecotones_gdf = gpd.GeoDataFrame(
        {"id_a": [1], "id_b": [999]}, geometry=[Point(0, 0)], crs="EPSG:3301"
    )
    weather_gdf = gpd.GeoDataFrame(
        {
            "id": [1],
            "fruiting_score_chanterelle": [0.8],
            "weather_data_quality": ["complete"],
            "weather_data_coverage": [1.0],
            "as_of": ["2026-08-20T10:00:00+00:00"],
        },
        geometry=[Point(0, 0)],
        crs="EPSG:3301",
    )

    result = join_ecotone_fruiting(ecotones_gdf, weather_gdf)

    assert result.loc[0, "weather_data_quality"] is None
    assert result.loc[0, "weather_data_coverage"] is None
    assert result.loc[0, "weather_as_of"] is None
