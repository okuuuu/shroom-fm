import pandas as pd

from shroom_fm.enrich import summarize_composition


def test_summarize_composition_groups_rows_by_eraldis_id():
    element_df = pd.DataFrame(
        [
            {
                "eraldis_id": 100,
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
