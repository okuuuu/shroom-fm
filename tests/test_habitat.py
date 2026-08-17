import math

import pytest

from shroom_fm.habitat import (
    HOST_PROFILES,
    TARGET_SPECIES,
    _stand_fractions,
    base_habitat,
    ecotone_score,
    exploration_bonus,
    host_score,
    kasvukoht_dimension_score,
    normalize_bool_or_none,
    site_modifier,
    site_type_score,
    stand_habitat_score,
)


def test_target_species_has_five_entries():
    assert TARGET_SPECIES == ["kitsemampel", "chanterelle", "aspen_bolete", "birch_bolete", "porcini"]


def test_host_score_saturates_at_saturation_share():
    fractions = {"pine": 0.5, "spruce": 0.0, "birch": 0.0, "aspen": 0.0, "other": 0.5}

    score = host_score("chanterelle", fractions)

    assert score == pytest.approx(1.0)


def test_host_score_scales_linearly_below_saturation_share():
    fractions = {"pine": 0.20, "spruce": 0.0, "birch": 0.0, "aspen": 0.0, "other": 0.8}

    score = host_score("chanterelle", fractions)

    assert score == pytest.approx(0.5)


def test_host_score_uses_best_host_not_sum_of_hosts():
    mediocre_mix = {"pine": 0.25, "spruce": 0.20, "birch": 0.20, "aspen": 0.0, "other": 0.35}

    score = host_score("porcini", mediocre_mix)

    assert score == pytest.approx(0.75)


def test_host_score_aspen_bolete_does_not_need_aspen_dominance():
    fractions = {"pine": 0.0, "spruce": 0.0, "birch": 0.0, "aspen": 0.15, "other": 0.85}

    score = host_score("aspen_bolete", fractions)

    assert score == pytest.approx(1.0)


def test_host_score_is_zero_for_no_compatible_host():
    fractions = {"pine": 0.0, "spruce": 0.0, "birch": 0.0, "aspen": 0.0, "other": 1.0}

    score = host_score("birch_bolete", fractions)

    assert score == 0.0


def test_site_type_score_returns_profile_value_for_mapped_group():
    # PH -> palu group; chanterelle's palu score is 1.00
    score = site_type_score("chanterelle", "PH")

    assert score == pytest.approx(1.00)


def test_site_type_score_returns_none_for_unmapped_kasvukoht():
    score = site_type_score("chanterelle", "KS")

    assert score is None


def test_site_type_score_returns_none_for_special_hydrology_group():
    # JO -> kõdusoo group, not in any species' SITE_TYPE_PROFILES table
    score = site_type_score("porcini", "JO")

    assert score is None


def test_site_modifier_bounds():
    assert site_modifier(1.0) == pytest.approx(1.00)
    assert site_modifier(0.0) == pytest.approx(0.50)
    assert site_modifier(0.8) == pytest.approx(0.90)


def test_stand_habitat_score_combines_host_and_site_multiplicatively():
    # 40% pine, PH (palu) site: chanterelle host_score = min(1, 0.4/0.4) = 1.0,
    # site_type_score(palu) = 1.0 -> site_modifier = 1.0 -> stand score = 1.0
    fractions = {"pine": 0.4, "spruce": 0.0, "birch": 0.0, "aspen": 0.0, "other": 0.6}

    score = stand_habitat_score("chanterelle", fractions, "PH")

    assert score == pytest.approx(1.0)


def test_stand_habitat_score_is_none_for_missing_composition():
    score = stand_habitat_score("chanterelle", None, "PH")

    assert score is None


def test_stand_habitat_score_is_none_for_unmapped_kasvukoht():
    fractions = {"pine": 0.4, "spruce": 0.0, "birch": 0.0, "aspen": 0.0, "other": 0.6}

    score = stand_habitat_score("chanterelle", fractions, "KS")

    assert score is None


def test_stand_habitat_score_poor_site_dampens_but_does_not_zero_strong_host():
    # 80% pine (host_score saturates to 1.0), salu site (chanterelle's worst
    # mapped score, 0.20) -> site_modifier = 0.5 + 0.5*0.20 = 0.60
    fractions = {"pine": 0.8, "spruce": 0.0, "birch": 0.0, "aspen": 0.0, "other": 0.2}

    score = stand_habitat_score("chanterelle", fractions, "ND")

    assert score == pytest.approx(0.60)
    assert score > 0.0


def test_stand_fractions_is_none_for_empty_composition():
    assert _stand_fractions([]) is None


def test_stand_fractions_is_none_for_all_nan_osakaal():
    composition = [{"puuliik_kood": "MA", "osakaal": float("nan")}]

    assert _stand_fractions(composition) is None


def test_stand_fractions_returns_real_fractions_when_data_present():
    composition = [{"puuliik_kood": "MA", "osakaal": 100.0}]

    fractions = _stand_fractions(composition)

    assert fractions is not None
    assert fractions["pine"] == pytest.approx(1.0)


def test_normalize_bool_or_none_handles_real_bool():
    assert normalize_bool_or_none(True) is True
    assert normalize_bool_or_none(False) is False


def test_normalize_bool_or_none_handles_geojson_roundtrip_string():
    # kasvukoht_group_changed round-trips through GeoJSON as the string
    # "True"/"False", not a real bool, because it's a mixed bool+None column.
    assert normalize_bool_or_none("True") is True
    assert normalize_bool_or_none("False") is False


def test_normalize_bool_or_none_handles_missing():
    assert normalize_bool_or_none(None) is None
    assert normalize_bool_or_none(float("nan")) is None


def test_kasvukoht_dimension_score_prefers_moisture_contrast_when_available():
    score = kasvukoht_dimension_score(0.5, True)

    assert score == pytest.approx(0.5)


def test_kasvukoht_dimension_score_falls_back_to_group_changed():
    assert kasvukoht_dimension_score(None, True) == 1.0
    assert kasvukoht_dimension_score(None, False) == 0.0
    assert kasvukoht_dimension_score(float("nan"), True) == 1.0


def test_kasvukoht_dimension_score_none_when_both_missing():
    assert kasvukoht_dimension_score(None, None) is None


def test_exploration_bonus_full_evidence():
    bonus, signal, coverage = exploration_bonus(
        composition_contrast=1.0,
        moisture_contrast=1.0,
        group_changed=True,
        age_contrast=1.0,
        drainage_changed=True,
        transition_length_m=200.0,
    )

    assert signal == pytest.approx(1.0)
    assert coverage == pytest.approx(1.0)
    assert bonus == pytest.approx(0.3)


def test_exploration_bonus_treats_nan_composition_contrast_as_missing():
    # data/ecotones.geojson uses NaN (not None) for missing composition_contrast.
    bonus, signal, coverage = exploration_bonus(
        composition_contrast=float("nan"),
        moisture_contrast=1.0,
        group_changed=True,
        age_contrast=1.0,
        drainage_changed=True,
        transition_length_m=200.0,
    )

    assert coverage == pytest.approx(0.65)  # 1.0 - composition_contrast's 0.35 weight
    assert bonus == pytest.approx(0.3 * (0.25 + 0.20 + 0.10 + 0.10))


def test_exploration_bonus_single_low_weight_term_does_not_reach_full_cap():
    # transition_length is always computed (transition_length_m is never None),
    # so it always contributes its 0.10 weight to coverage alongside drainage's
    # 0.10 -> coverage=0.20 here, not just drainage's own weight.
    bonus, signal, coverage = exploration_bonus(
        composition_contrast=None,
        moisture_contrast=None,
        group_changed=None,
        age_contrast=None,
        drainage_changed=True,
        transition_length_m=0.0,
    )

    assert coverage == pytest.approx(0.20)
    assert bonus == pytest.approx(0.3 * 0.10)  # only drainage contributes to signal
    assert bonus < 0.05


def test_exploration_bonus_no_evidence_is_zero():
    bonus, signal, coverage = exploration_bonus(
        composition_contrast=None,
        moisture_contrast=None,
        group_changed=None,
        age_contrast=None,
        drainage_changed=None,
        transition_length_m=0.0,
    )

    assert coverage == pytest.approx(0.10)  # transition_length is always available
    assert bonus == pytest.approx(0.0)


def test_base_habitat_weights_max_higher_than_min():
    score = base_habitat(1.0, 0.0)

    assert score == pytest.approx(0.7)


def test_base_habitat_none_if_either_side_missing():
    assert base_habitat(None, 0.5) is None
    assert base_habitat(float("nan"), 0.5) is None


def test_ecotone_score_applies_bonus_multiplicatively():
    score = ecotone_score(1.0, 1.0, 0.3)

    assert score == pytest.approx(1.3)


def test_ecotone_score_none_if_base_habitat_missing():
    assert ecotone_score(None, 0.5, 0.3) is None
