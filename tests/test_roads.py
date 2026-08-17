import pytest

from shroom_fm.roads import (
    CAR_CLASS_CONDITIONAL,
    CAR_CLASS_HIGH_CONFIDENCE,
    CAR_CLASS_NORMAL,
    CAR_CLASS_WALK_ONLY,
    classify_car_class,
)


def test_classify_car_class_pohimaantee_is_high_confidence():
    assert classify_car_class("Põhimaantee", None) == CAR_CLASS_HIGH_CONFIDENCE


def test_classify_car_class_tugimaantee_is_high_confidence():
    assert classify_car_class("Tugimaantee", None) == CAR_CLASS_HIGH_CONFIDENCE


def test_classify_car_class_korvalmaantee_is_high_confidence():
    assert classify_car_class("Kõrvalmaantee", None) == CAR_CLASS_HIGH_CONFIDENCE


def test_classify_car_class_ramp_is_high_confidence():
    assert classify_car_class("Ramp või ühendustee", None) == CAR_CLASS_HIGH_CONFIDENCE


def test_classify_car_class_tanav_is_high_confidence():
    assert classify_car_class("Tänav", "Püsikate") == CAR_CLASS_HIGH_CONFIDENCE


def test_classify_car_class_muu_tee_with_pusikate_is_normal():
    assert classify_car_class("Muu tee", "Püsikate") == CAR_CLASS_NORMAL


def test_classify_car_class_muu_tee_with_kruuskate_is_normal():
    assert classify_car_class("Muu tee", "Kruuskate") == CAR_CLASS_NORMAL


def test_classify_car_class_muu_tee_with_kivikate_is_normal():
    assert classify_car_class("Muu tee", "Kivikate") == CAR_CLASS_NORMAL


def test_classify_car_class_muu_tee_with_pinnas_is_conditional():
    assert classify_car_class("Muu tee", "Pinnas") == CAR_CLASS_CONDITIONAL


def test_classify_car_class_rada_is_walk_only():
    assert classify_car_class("Rada", "Pinnas") == CAR_CLASS_WALK_ONLY


def test_classify_car_class_kergliiklustee_is_walk_only():
    assert classify_car_class("Kergliiklustee", "Püsikate") == CAR_CLASS_WALK_ONLY


def test_classify_car_class_raises_for_unrecognized_tyyp_tekst():
    with pytest.raises(ValueError):
        classify_car_class("Mingi tundmatu tüüp", "Püsikate")


def test_classify_car_class_raises_for_unrecognized_muu_tee_surface():
    with pytest.raises(ValueError):
        classify_car_class("Muu tee", "Mingi tundmatu kate")


import geopandas as gpd
from shapely.geometry import LineString, Point

from shroom_fm.roads import exclude_barrier_blocked_segments


def test_exclude_barrier_blocked_segments_removes_segment_near_closed_barrier():
    roads_gdf = gpd.GeoDataFrame(
        {"name": ["blocked", "clear"]},
        geometry=[
            LineString([(0, 0), (10, 0)]),
            LineString([(1000, 1000), (1010, 1000)]),
        ],
        crs="EPSG:3301",
    )
    barriers_gdf = gpd.GeoDataFrame(
        {"toke_tekst": ["Püsivalt suletud"]},
        geometry=[Point(5, 3)],  # 3m from "blocked", within BARRIER_SNAP_M
        crs="EPSG:3301",
    )

    result = exclude_barrier_blocked_segments(roads_gdf, barriers_gdf)

    assert list(result["name"]) == ["clear"]


def test_exclude_barrier_blocked_segments_keeps_segment_near_openable_barrier():
    roads_gdf = gpd.GeoDataFrame(
        {"name": ["near_openable"]},
        geometry=[LineString([(0, 0), (10, 0)])],
        crs="EPSG:3301",
    )
    barriers_gdf = gpd.GeoDataFrame(
        {"toke_tekst": ["Avatav"]},
        geometry=[Point(5, 3)],
        crs="EPSG:3301",
    )

    result = exclude_barrier_blocked_segments(roads_gdf, barriers_gdf)

    assert list(result["name"]) == ["near_openable"]


def test_exclude_barrier_blocked_segments_keeps_segment_near_unknown_status_barrier():
    roads_gdf = gpd.GeoDataFrame(
        {"name": ["near_unknown"]},
        geometry=[LineString([(0, 0), (10, 0)])],
        crs="EPSG:3301",
    )
    barriers_gdf = gpd.GeoDataFrame(
        {"toke_tekst": ["Täitmata"]},
        geometry=[Point(5, 3)],
        crs="EPSG:3301",
    )

    result = exclude_barrier_blocked_segments(roads_gdf, barriers_gdf)

    assert list(result["name"]) == ["near_unknown"]


def test_exclude_barrier_blocked_segments_keeps_all_when_no_barriers():
    roads_gdf = gpd.GeoDataFrame(
        {"name": ["a"]},
        geometry=[LineString([(0, 0), (10, 0)])],
        crs="EPSG:3301",
    )
    barriers_gdf = gpd.GeoDataFrame({"toke_tekst": []}, geometry=[], crs="EPSG:3301")

    result = exclude_barrier_blocked_segments(roads_gdf, barriers_gdf)

    assert list(result["name"]) == ["a"]


def test_exclude_barrier_blocked_segments_keeps_segment_beyond_snap_distance():
    roads_gdf = gpd.GeoDataFrame(
        {"name": ["far"]},
        geometry=[LineString([(0, 0), (10, 0)])],
        crs="EPSG:3301",
    )
    barriers_gdf = gpd.GeoDataFrame(
        {"toke_tekst": ["Püsivalt suletud"]},
        geometry=[Point(100, 100)],  # ~135m away, beyond BARRIER_SNAP_M
        crs="EPSG:3301",
    )

    result = exclude_barrier_blocked_segments(roads_gdf, barriers_gdf)

    assert list(result["name"]) == ["far"]
