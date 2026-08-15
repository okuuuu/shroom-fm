import math

import pytest

from shroom_fm.eraldis import compute_bbox


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
