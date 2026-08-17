import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point

from shroom_fm.access import (
    ACCESS_DISTANCE_CAP_M,
    access_reason,
    access_score,
    nearest_segment,
    score_access,
    score_eraldis_access,
)


def test_nearest_segment_returns_closest_row_and_distance():
    roads_gdf = gpd.GeoDataFrame(
        {"name": ["near", "far"]},
        geometry=[
            LineString([(0, 100), (10, 100)]),
            LineString([(0, 1000), (10, 1000)]),
        ],
        crs="EPSG:3301",
    )

    row, distance = nearest_segment(Point(0, 0), roads_gdf)

    assert row["name"] == "near"
    assert distance == pytest.approx(100.0)


def test_nearest_segment_returns_none_for_empty_roads():
    roads_gdf = gpd.GeoDataFrame({"name": []}, geometry=[], crs="EPSG:3301")

    assert nearest_segment(Point(0, 0), roads_gdf) is None


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


def test_score_eraldis_access_computes_all_fields():
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

    result = score_eraldis_access(Point(0, 0), roads_gdf)

    assert result["nearest_car_road_m"] == pytest.approx(100.0)
    assert result["nearest_high_confidence_road_m"] == pytest.approx(100.0)
    assert result["nearest_walk_path_m"] == pytest.approx(50.0)
    assert result["access_confidence"] == "HIGH_CONFIDENCE"
    assert result["access_score"] == pytest.approx(access_score(100.0))
    assert result["access_reason"] == "100m from Kõrvalmaantee-class road"


def test_score_eraldis_access_handles_no_roads_at_all():
    roads_gdf = gpd.GeoDataFrame(
        {"car_class": [], "tyyp_tekst": []}, geometry=[], crs="EPSG:3301"
    )

    result = score_eraldis_access(Point(0, 0), roads_gdf)

    assert result["nearest_car_road_m"] is None
    assert result["nearest_high_confidence_road_m"] is None
    assert result["nearest_walk_path_m"] is None
    assert result["access_score"] == 0.0
    assert result["access_confidence"] is None
    assert (
        result["access_reason"]
        == f"no car-accessible road within {ACCESS_DISTANCE_CAP_M:.0f}m"
    )


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
