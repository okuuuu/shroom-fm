import math

import geopandas as gpd
import pytest
from shapely.geometry import Point

from shroom_fm.eraldis import (
    _build_cql_filter,
    _cql_point,
    compute_bbox,
    fetch_eraldis_annulus,
    filter_within_radius,
)


def test_compute_bbox_returns_padded_box_around_point():
    lat, lon, radius_km = 59.4370, 24.7536, 80.0

    minx, miny, maxx, maxy = compute_bbox(lat, lon, radius_km)

    padded_radius_km = radius_km * 1.1
    expected_delta_lat = padded_radius_km / 111.32
    expected_delta_lon = padded_radius_km / (111.32 * math.cos(math.radians(lat)))

    assert minx == pytest.approx(lon - expected_delta_lon)
    assert maxx == pytest.approx(lon + expected_delta_lon)
    assert miny == pytest.approx(lat - expected_delta_lat)
    assert maxy == pytest.approx(lat + expected_delta_lat)

    # sanity: the unpadded radius must fit strictly inside the box
    unpadded_delta_lat = radius_km / 111.32
    assert (maxy - lat) > unpadded_delta_lat
    assert (lat - miny) > unpadded_delta_lat


def test_filter_within_radius_keeps_only_points_inside_cutoff():
    home_lat, home_lon = 59.4370, 24.7536

    home_point_3301 = (
        gpd.GeoSeries([Point(home_lon, home_lat)], crs="EPSG:4326")
        .to_crs("EPSG:3301")
        .iloc[0]
    )

    near_point = Point(home_point_3301.x + 10_000, home_point_3301.y)  # 10km away
    far_point = Point(home_point_3301.x + 200_000, home_point_3301.y)  # 200km away

    gdf = gpd.GeoDataFrame(
        {"name": ["near", "far"]},
        geometry=[near_point, far_point],
        crs="EPSG:3301",
    )

    result = filter_within_radius(gdf, home_lat, home_lon, radius_km=80.0)

    assert list(result["name"]) == ["near"]


def test_filter_within_radius_excludes_points_inside_inner_cutoff():
    home_lat, home_lon = 59.4370, 24.7536

    home_point_3301 = (
        gpd.GeoSeries([Point(home_lon, home_lat)], crs="EPSG:4326")
        .to_crs("EPSG:3301")
        .iloc[0]
    )

    too_close_point = Point(home_point_3301.x + 2_000, home_point_3301.y)  # 2km away
    in_ring_point = Point(home_point_3301.x + 10_000, home_point_3301.y)  # 10km away
    too_far_point = Point(home_point_3301.x + 200_000, home_point_3301.y)  # 200km away

    gdf = gpd.GeoDataFrame(
        {"name": ["too_close", "in_ring", "too_far"]},
        geometry=[too_close_point, in_ring_point, too_far_point],
        crs="EPSG:3301",
    )

    result = filter_within_radius(
        gdf, home_lat, home_lon, radius_km=80.0, inner_radius_km=5.0
    )

    assert list(result["name"]) == ["in_ring"]


def test_filter_within_radius_raises_when_inner_radius_not_less_than_outer():
    with pytest.raises(ValueError):
        filter_within_radius(
            gpd.GeoDataFrame({"name": []}, geometry=[], crs="EPSG:3301"),
            59.4370,
            24.7536,
            radius_km=20.0,
            inner_radius_km=20.0,
        )


def test_cql_point_returns_northing_first_estonian_grid_point():
    lat, lon = 59.451081455185864, 24.818002100965362

    result = _cql_point(lat, lon)

    assert result == "POINT(6590647.722702539 546398.5907798207)"


def test_build_cql_filter_omits_beyond_clause_when_inner_radius_is_zero():
    lat, lon = 59.451081455185864, 24.818002100965362

    result = _build_cql_filter(lat, lon, radius_km=20.0, inner_radius_km=0.0)

    assert result == (
        "DWITHIN(shape, POINT(6590647.722702539 546398.5907798207), 20000.0, meters)"
    )
    assert "BEYOND" not in result


def test_build_cql_filter_includes_beyond_clause_when_inner_radius_is_positive():
    lat, lon = 59.451081455185864, 24.818002100965362

    result = _build_cql_filter(lat, lon, radius_km=20.0, inner_radius_km=5.0)

    assert result == (
        "DWITHIN(shape, POINT(6590647.722702539 546398.5907798207), 20000.0, meters) "
        "AND BEYOND(shape, POINT(6590647.722702539 546398.5907798207), 5000.0, meters)"
    )


def test_fetch_eraldis_annulus_raises_when_inner_radius_not_less_than_outer():
    with pytest.raises(ValueError):
        fetch_eraldis_annulus(59.4370, 24.7536, radius_km=20.0, inner_radius_km=20.0)
