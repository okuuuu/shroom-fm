import json

import pytest

from shroom_fm.eraldis import fetch_eraldis_annulus


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


def test_fetch_eraldis_annulus_raises_when_inner_radius_not_less_than_outer():
    with pytest.raises(ValueError):
        fetch_eraldis_annulus(59.4370, 24.7536, radius_km=20.0, inner_radius_km=20.0)


def test_fetch_eraldis_annulus_fetches_all_pages_and_concatenates(monkeypatch):
    monkeypatch.setattr("shroom_fm.eraldis.PAGE_SIZE", 2)
    monkeypatch.setattr(
        "shroom_fm.eraldis.fetch_hit_count", lambda url, params, **kw: 3
    )

    captured_params_list = []

    def fake_fetch_pages_concurrently(url, params_list, **kwargs):
        captured_params_list.extend(params_list)
        return [_geojson_page(2), _geojson_page(1)]

    monkeypatch.setattr(
        "shroom_fm.eraldis.fetch_pages_concurrently", fake_fetch_pages_concurrently
    )

    result = fetch_eraldis_annulus(59.4370, 24.7536, radius_km=20.0)

    assert len(result) == 3
    assert [p["startIndex"] for p in captured_params_list] == [0, 2]
    assert [p["count"] for p in captured_params_list] == [2, 2]


def test_fetch_eraldis_annulus_issues_one_request_for_empty_result(monkeypatch):
    monkeypatch.setattr(
        "shroom_fm.eraldis.fetch_hit_count", lambda url, params, **kw: 0
    )

    captured_params_list = []

    def fake_fetch_pages_concurrently(url, params_list, **kwargs):
        captured_params_list.extend(params_list)
        return [_geojson_page(0)]

    monkeypatch.setattr(
        "shroom_fm.eraldis.fetch_pages_concurrently", fake_fetch_pages_concurrently
    )

    result = fetch_eraldis_annulus(59.4370, 24.7536, radius_km=20.0)

    assert len(result) == 0
    assert len(captured_params_list) == 1
    assert captured_params_list[0]["startIndex"] == 0
