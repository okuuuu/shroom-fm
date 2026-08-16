import pytest

from shroom_fm.ecotone import composition_contrast, composition_fractions, dominant_species


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


def test_composition_contrast_is_zero_for_identical_fractions():
    fractions = {"pine": 0.9, "spruce": 0.1, "birch": 0.0, "aspen": 0.0, "other": 0.0}

    assert composition_contrast(fractions, fractions) == 0.0


def test_composition_contrast_is_one_for_completely_disjoint_fractions():
    fractions_a = {"pine": 1.0, "spruce": 0.0, "birch": 0.0, "aspen": 0.0, "other": 0.0}
    fractions_b = {"pine": 0.0, "spruce": 1.0, "birch": 0.0, "aspen": 0.0, "other": 0.0}

    assert composition_contrast(fractions_a, fractions_b) == pytest.approx(1.0)


def test_composition_contrast_is_small_for_near_identical_fractions():
    fractions_a = {"pine": 0.51, "spruce": 0.49, "birch": 0.0, "aspen": 0.0, "other": 0.0}
    fractions_b = {"pine": 0.49, "spruce": 0.51, "birch": 0.0, "aspen": 0.0, "other": 0.0}

    assert composition_contrast(fractions_a, fractions_b) == pytest.approx(0.02)


def test_composition_contrast_reflects_real_mixed_stand_transition():
    fractions_a = {"pine": 0.9, "spruce": 0.1, "birch": 0.0, "aspen": 0.0, "other": 0.0}
    fractions_b = {"pine": 0.5, "spruce": 0.5, "birch": 0.0, "aspen": 0.0, "other": 0.0}

    assert composition_contrast(fractions_a, fractions_b) == pytest.approx(0.4)


def test_dominant_species_returns_highest_share_category():
    fractions = {"pine": 0.86, "spruce": 0.0, "birch": 0.14, "aspen": 0.0, "other": 0.0}

    name, share = dominant_species(fractions)

    assert name == "pine"
    assert share == pytest.approx(0.86)


def test_dominant_species_can_return_other_for_mixed_non_target_stand():
    fractions = {"pine": 0.1, "spruce": 0.1, "birch": 0.1, "aspen": 0.1, "other": 0.6}

    name, share = dominant_species(fractions)

    assert name == "other"
    assert share == pytest.approx(0.6)
