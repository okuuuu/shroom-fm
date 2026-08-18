import json

import pandas as pd

from shroom_fm.enrich import compute_species_shares, fetch_eraldis_element, summarize_composition


def test_summarize_composition_returns_empty_dict_for_empty_input():
    element_df = pd.DataFrame()

    result = summarize_composition(element_df)

    assert result == {}


def test_summarize_composition_groups_rows_by_eraldis_id():
    element_df = pd.DataFrame(
        [
            {
                "eraldis_id": 100,
                "id": "eraldis_element.0",
                "sys_id": 156307113,
                "versioon": 1762787104405.0,
                "rinne_kood": "1",
                "puuliik_kood": "MA",
                "osakaal": 80,
                "vanus": 30,
                "korgus": 12,
                "enamus": True,
                "sunniaasta": 1994,
                "paritolu": "S",
                "diameeter": 14,
                "rinnaspindala": 10.0,
                "tagavara": 90,
                "arv": 500,
            },
            {
                "eraldis_id": 100,
                "id": "eraldis_element.1",
                "sys_id": 156307114,
                "versioon": 1762787104406.0,
                "rinne_kood": "1",
                "puuliik_kood": "KU",
                "osakaal": 20,
                "vanus": 30,
                "korgus": 10,
                "enamus": False,
                "sunniaasta": 1994,
                "paritolu": "S",
                "diameeter": 12,
                "rinnaspindala": 2.0,
                "tagavara": 15,
                "arv": 100,
            },
            {
                "eraldis_id": 200,
                "id": "eraldis_element.2",
                "sys_id": 156307115,
                "versioon": 1762787104407.0,
                "rinne_kood": "1",
                "puuliik_kood": "KS",
                "osakaal": 100,
                "vanus": 15,
                "korgus": 6,
                "enamus": True,
                "sunniaasta": 2009,
                "paritolu": "N",
                "diameeter": 6,
                "rinnaspindala": 4.0,
                "tagavara": 12,
                "arv": 800,
            },
        ]
    )

    result = summarize_composition(element_df)

    assert set(result.keys()) == {100, 200}
    assert len(result[100]) == 2
    assert result[100][0]["puuliik_kood"] == "MA"
    assert result[100][0]["osakaal"] == 80
    assert len(result[200]) == 1
    assert result[200][0]["puuliik_kood"] == "KS"

    # Verify bookkeeping columns are excluded from output
    assert "id" not in result[100][0]
    assert "sys_id" not in result[100][0]
    assert "versioon" not in result[100][0]


def test_compute_species_shares_sums_osakaal_by_target_species():
    composition = [
        {"puuliik_kood": "MA", "osakaal": 70},
        {"puuliik_kood": "MA", "osakaal": 10},
        {"puuliik_kood": "KU", "osakaal": 15},
        {"puuliik_kood": "NU", "osakaal": 5},
    ]

    shares = compute_species_shares(composition)

    assert shares == {
        "pine_share": 80.0,
        "spruce_share": 15.0,
        "birch_share": 0.0,
        "aspen_share": 0.0,
    }


def _element_json_page(rows: list[dict]) -> bytes:
    return json.dumps(
        {"type": "FeatureCollection", "features": [{"properties": r} for r in rows]}
    ).encode()


def test_fetch_eraldis_element_batches_ids_and_concatenates(monkeypatch):
    monkeypatch.setattr("shroom_fm.enrich.ID_BATCH_SIZE", 2)

    captured_params_list = []

    def fake_fetch_pages_concurrently(url, params_list, **kwargs):
        captured_params_list.extend(params_list)
        return [
            _element_json_page([{"eraldis_id": 1, "puuliik_kood": "MA"}]),
            _element_json_page([{"eraldis_id": 3, "puuliik_kood": "KU"}]),
        ]

    monkeypatch.setattr(
        "shroom_fm.enrich.fetch_pages_concurrently", fake_fetch_pages_concurrently
    )

    result = fetch_eraldis_element([1, 2, 3])

    assert len(result) == 2
    assert list(result["eraldis_id"]) == [1, 3]
    assert captured_params_list[0]["CQL_FILTER"] == "eraldis_id IN (1,2)"
    assert captured_params_list[1]["CQL_FILTER"] == "eraldis_id IN (3)"


def test_fetch_eraldis_element_returns_empty_dataframe_for_empty_ids(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "shroom_fm.enrich.fetch_pages_concurrently",
        lambda *a, **k: calls.append(1) or [],
    )

    result = fetch_eraldis_element([])

    assert result.empty
    assert calls == []
