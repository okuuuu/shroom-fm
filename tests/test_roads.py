import pytest

from shroom_fm.roads import (
    CAR_CLASS_CONDITIONAL,
    CAR_CLASS_HIGH_CONFIDENCE,
    CAR_CLASS_NORMAL,
    CAR_CLASS_WALK_ONLY,
    classify_car_class,
    fetch_layer_annulus,
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


def test_fetch_layer_annulus_raises_when_inner_radius_not_less_than_outer():
    with pytest.raises(ValueError):
        fetch_layer_annulus(
            "https://example.com/wfs",
            "example:layer",
            59.4370,
            24.7536,
            radius_km=20.0,
            inner_radius_km=20.0,
        )


import json


def _geojson_page(n: int) -> bytes:
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [24.75 + i * 0.001, 59.43]},
            "properties": {"id": i},
        }
        for i in range(n)
    ]
    return json.dumps({"type": "FeatureCollection", "features": features}).encode()


def test_fetch_layer_annulus_fetches_all_pages_and_concatenates(monkeypatch):
    monkeypatch.setattr("shroom_fm.roads._PAGE_SIZE", 2)
    monkeypatch.setattr(
        "shroom_fm.roads.fetch_hit_count", lambda url, params, **kw: 3
    )

    captured_params_list = []

    def fake_fetch_pages_concurrently(url, params_list, **kwargs):
        captured_params_list.extend(params_list)
        return [_geojson_page(2), _geojson_page(1)]

    monkeypatch.setattr(
        "shroom_fm.roads.fetch_pages_concurrently", fake_fetch_pages_concurrently
    )

    result = fetch_layer_annulus(
        "https://example.com/wfs", "example:layer", 59.4370, 24.7536, radius_km=20.0
    )

    assert len(result) == 3
    assert [p["startIndex"] for p in captured_params_list] == [0, 2]


def test_fetch_layer_annulus_raises_when_fetched_count_mismatches_total(monkeypatch):
    monkeypatch.setattr("shroom_fm.roads._PAGE_SIZE", 2)
    monkeypatch.setattr(
        "shroom_fm.roads.fetch_hit_count", lambda url, params, **kw: 3
    )

    def fake_fetch_pages_concurrently(url, params_list, **kwargs):
        return [_geojson_page(2), _geojson_page(0)]  # only 2 of reported 3

    monkeypatch.setattr(
        "shroom_fm.roads.fetch_pages_concurrently", fake_fetch_pages_concurrently
    )

    with pytest.raises(RuntimeError):
        fetch_layer_annulus(
            "https://example.com/wfs", "example:layer", 59.4370, 24.7536, radius_km=20.0
        )


def test_fetch_layer_annulus_issues_one_request_for_empty_result(monkeypatch):
    monkeypatch.setattr(
        "shroom_fm.roads.fetch_hit_count", lambda url, params, **kw: 0
    )

    captured_params_list = []

    def fake_fetch_pages_concurrently(url, params_list, **kwargs):
        captured_params_list.extend(params_list)
        return [_geojson_page(0)]

    monkeypatch.setattr(
        "shroom_fm.roads.fetch_pages_concurrently", fake_fetch_pages_concurrently
    )

    result = fetch_layer_annulus(
        "https://example.com/wfs", "example:layer", 59.4370, 24.7536, radius_km=20.0
    )

    assert len(result) == 0
    assert len(captured_params_list) == 1
