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
