import pytest

from shroom_fm.ecotone import composition_fractions


def test_composition_fractions_normalizes_single_species_stand():
    composition = [{"puuliik_kood": "MA", "osakaal": 100.0}]

    fractions = composition_fractions(composition)

    assert fractions == {"pine": 1.0, "spruce": 0.0, "birch": 0.0, "aspen": 0.0, "other": 0.0}


def test_composition_fractions_normalizes_multi_layer_stand_exceeding_100():
    composition = [
        {"puuliik_kood": "MA", "osakaal": 82.0},
        {"puuliik_kood": "KS", "osakaal": 18.0},
        {"puuliik_kood": "MA", "osakaal": 90.0},
        {"puuliik_kood": "KS", "osakaal": 10.0},
    ]

    fractions = composition_fractions(composition)

    assert fractions["pine"] == pytest.approx(0.86)
    assert fractions["birch"] == pytest.approx(0.14)
    assert fractions["spruce"] == 0.0
    assert fractions["aspen"] == 0.0
    assert fractions["other"] == 0.0
    assert sum(fractions.values()) == pytest.approx(1.0)


def test_composition_fractions_includes_other_category_for_non_target_species():
    composition = [
        {"puuliik_kood": "MA", "osakaal": 60.0},
        {"puuliik_kood": "NU", "osakaal": 40.0},
    ]

    fractions = composition_fractions(composition)

    assert fractions["pine"] == pytest.approx(0.6)
    assert fractions["other"] == pytest.approx(0.4)


def test_composition_fractions_returns_zero_for_empty_composition():
    fractions = composition_fractions([])

    assert fractions == {"pine": 0.0, "spruce": 0.0, "birch": 0.0, "aspen": 0.0, "other": 0.0}
