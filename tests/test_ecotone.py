import pytest

from shroom_fm.ecotone import (
    composition_contrast,
    composition_diversity,
    composition_fractions,
    dominant_species,
    kasvukoht_contrast,
    kasvukoht_profile,
)


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


def test_composition_fractions_skips_entries_with_nan_osakaal():
    composition = [
        {"puuliik_kood": "MA", "osakaal": 100.0},
        {
            "rinne_kood": "A",
            "puuliik_kood": "PI",
            "osakaal": float("nan"),
            "vanus": float("nan"),
            "korgus": 3.5,
            "enamus": False,
            "sunniaasta": float("nan"),
            "paritolu": float("nan"),
            "diameeter": float("nan"),
            "rinnaspindala": float("nan"),
            "tagavara": float("nan"),
            "arv": float("nan"),
        },
    ]

    fractions = composition_fractions(composition)

    assert fractions == {"pine": 1.0, "spruce": 0.0, "birch": 0.0, "aspen": 0.0, "other": 0.0}
    assert sum(fractions.values()) == pytest.approx(1.0)


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


def test_composition_diversity_is_zero_for_monoculture():
    fractions = {"pine": 1.0, "spruce": 0.0, "birch": 0.0, "aspen": 0.0, "other": 0.0}

    assert composition_diversity(fractions) == pytest.approx(0.0)


def test_composition_diversity_matches_known_value_for_real_stand():
    fractions = {"pine": 0.0, "spruce": 0.95, "birch": 0.05, "aspen": 0.0, "other": 0.0}

    assert composition_diversity(fractions) == pytest.approx(0.1985152433458726)


def test_composition_diversity_is_higher_for_more_evenly_mixed_stand():
    monoculture = {"pine": 1.0, "spruce": 0.0, "birch": 0.0, "aspen": 0.0, "other": 0.0}
    evenly_mixed = {"pine": 0.25, "spruce": 0.25, "birch": 0.25, "aspen": 0.25, "other": 0.0}

    assert composition_diversity(evenly_mixed) > composition_diversity(monoculture)


def test_kasvukoht_profile_returns_known_site_type():
    profile = kasvukoht_profile("PH")

    assert profile == {"group": "palu", "moisture": 1}


def test_kasvukoht_profile_returns_none_for_unmapped_code():
    assert kasvukoht_profile("KS") is None
    assert kasvukoht_profile("KP") is None
    assert kasvukoht_profile("LP") is None


def test_kasvukoht_profile_marks_special_hydrology_types():
    lu = kasvukoht_profile("LU")
    jo = kasvukoht_profile("JO")

    assert lu == {"group": "loo", "moisture": "special"}
    assert jo == {"group": "kõdusoo", "moisture": "special"}


def test_kasvukoht_profile_marks_puistang_moisture_as_none():
    mp = kasvukoht_profile("MP")
    tp = kasvukoht_profile("TP")

    assert mp == {"group": "puistang", "moisture": None}
    assert tp == {"group": "puistang", "moisture": None}


def test_kasvukoht_contrast_graded_transition_within_same_group():
    result = kasvukoht_contrast("PH", "MS")

    assert result == {
        "site_type_changed": True,
        "group_changed": False,
        "moisture_contrast": 0.25,
    }


def test_kasvukoht_contrast_strong_transition_across_groups():
    result = kasvukoht_contrast("PH", "RB")

    assert result == {
        "site_type_changed": True,
        "group_changed": True,
        "moisture_contrast": 0.75,
    }


def test_kasvukoht_contrast_special_hydrology_type_has_no_moisture_contrast():
    result = kasvukoht_contrast("PH", "LU")

    assert result["site_type_changed"] is True
    assert result["group_changed"] is True
    assert result["moisture_contrast"] is None


def test_kasvukoht_contrast_unmapped_code_only_has_site_type_changed():
    result = kasvukoht_contrast("PH", "KS")

    assert result == {
        "site_type_changed": True,
        "group_changed": None,
        "moisture_contrast": None,
    }


def test_kasvukoht_contrast_identical_codes():
    result = kasvukoht_contrast("PH", "PH")

    assert result == {
        "site_type_changed": False,
        "group_changed": False,
        "moisture_contrast": 0.0,
    }
