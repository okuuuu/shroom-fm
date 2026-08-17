import pytest

from shroom_fm.habitat import (
    HOST_PROFILES,
    TARGET_SPECIES,
    host_score,
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
