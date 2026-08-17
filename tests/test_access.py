import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point

from shroom_fm.access import (
    ACCESS_DISTANCE_CAP_M,
    access_reason,
    access_score,
    score_access,
)


def test_access_score_is_zero_for_none_distance():
    assert access_score(None) == 0.0


def test_access_score_is_one_at_zero_distance():
    assert access_score(0.0) == 1.0


def test_access_score_is_zero_at_or_beyond_cap():
    assert access_score(ACCESS_DISTANCE_CAP_M) == 0.0
    assert access_score(ACCESS_DISTANCE_CAP_M * 2) == 0.0


def test_access_score_scales_linearly_mid_range():
    assert access_score(750.0) == pytest.approx(0.5)


def test_access_reason_for_no_car_road():
    assert (
        access_reason(None, None)
        == f"no car-accessible road within {ACCESS_DISTANCE_CAP_M:.0f}m"
    )


def test_access_reason_names_distance_and_type():
    assert access_reason(320.0, "Kõrvalmaantee") == "320m from Kõrvalmaantee-class road"


def test_score_access_computes_all_fields_for_a_single_stand():
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1]},
        geometry=[Point(0, 0)],
        crs="EPSG:3301",
    )
    roads_gdf = gpd.GeoDataFrame(
        {
            "car_class": ["HIGH_CONFIDENCE", "WALK_ONLY"],
            "tyyp_tekst": ["Kõrvalmaantee", "Rada"],
        },
        geometry=[
            LineString([(0, 100), (10, 100)]),
            LineString([(0, 50), (10, 50)]),
        ],
        crs="EPSG:3301",
    )

    result = score_access(eraldis_gdf, roads_gdf)

    assert result.loc[0, "nearest_car_road_m"] == pytest.approx(100.0)
    assert result.loc[0, "nearest_high_confidence_road_m"] == pytest.approx(100.0)
    assert result.loc[0, "nearest_walk_path_m"] == pytest.approx(50.0)
    assert result.loc[0, "access_confidence"] == "HIGH_CONFIDENCE"
    assert result.loc[0, "access_score"] == pytest.approx(access_score(100.0))
    assert result.loc[0, "access_reason"] == "100m from Kõrvalmaantee-class road"


def test_score_access_handles_no_car_eligible_roads():
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1]},
        geometry=[Point(0, 0)],
        crs="EPSG:3301",
    )
    roads_gdf = gpd.GeoDataFrame(
        {"car_class": [], "tyyp_tekst": []}, geometry=[], crs="EPSG:3301"
    )

    result = score_access(eraldis_gdf, roads_gdf)

    assert result.loc[0, "nearest_car_road_m"] is None
    assert result.loc[0, "nearest_high_confidence_road_m"] is None
    assert result.loc[0, "nearest_walk_path_m"] is None
    assert result.loc[0, "access_score"] == 0.0
    assert result.loc[0, "access_confidence"] is None
    assert (
        result.loc[0, "access_reason"]
        == f"no car-accessible road within {ACCESS_DISTANCE_CAP_M:.0f}m"
    )


def test_score_access_aligns_each_stand_with_its_own_nearest_road():
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1, 2, 3]},
        geometry=[Point(0, 0), Point(1000, 1000), Point(-1000, -1000)],
        crs="EPSG:3301",
        index=[5, 1, 3],
    )
    roads_gdf = gpd.GeoDataFrame(
        {
            "car_class": ["NORMAL", "NORMAL", "NORMAL"],
            "tyyp_tekst": ["Muu tee", "Muu tee", "Muu tee"],
        },
        geometry=[
            LineString([(0, 5), (10, 5)]),
            LineString([(1000, 1015), (1010, 1015)]),
            LineString([(-1000, -975), (-990, -975)]),
        ],
        crs="EPSG:3301",
    )

    result = score_access(eraldis_gdf, roads_gdf)

    assert result.loc[5, "nearest_car_road_m"] == pytest.approx(5.0)
    assert result.loc[1, "nearest_car_road_m"] == pytest.approx(15.0)
    assert result.loc[3, "nearest_car_road_m"] == pytest.approx(25.0)


def test_score_access_resolves_tie_to_exactly_one_match():
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1]},
        geometry=[Point(0, 0)],
        crs="EPSG:3301",
    )
    roads_gdf = gpd.GeoDataFrame(
        {
            "car_class": ["NORMAL", "NORMAL"],
            "tyyp_tekst": ["Muu tee", "Muu tee"],
        },
        geometry=[
            LineString([(5, 0), (5, 10)]),
            LineString([(-5, 0), (-5, 10)]),
        ],
        crs="EPSG:3301",
    )

    result = score_access(eraldis_gdf, roads_gdf)

    assert len(result) == 1
    assert result.loc[0, "nearest_car_road_m"] == pytest.approx(5.0)


def test_score_access_appends_columns_to_eraldis_gdf():
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1]},
        geometry=[Point(24.0, 59.0)],
        crs="EPSG:4326",
    )
    roads_gdf = gpd.GeoDataFrame(
        {"car_class": ["NORMAL"], "tyyp_tekst": ["Muu tee"]},
        geometry=[LineString([(24.0, 59.001), (24.001, 59.001)])],
        crs="EPSG:4326",
    )

    result = score_access(eraldis_gdf, roads_gdf)

    assert "access_score" in result.columns
    assert "access_reason" in result.columns
    assert "nearest_car_road_m" in result.columns
    assert result.loc[0, "id"] == 1
    assert result.crs == "EPSG:4326"
