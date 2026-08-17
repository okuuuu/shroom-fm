import pytest

from shroom_fm.habitat import HOST_PROFILES, TARGET_SPECIES, host_score


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

    assert score < 1.0


def test_host_score_aspen_bolete_does_not_need_aspen_dominance():
    fractions = {"pine": 0.0, "spruce": 0.0, "birch": 0.0, "aspen": 0.15, "other": 0.85}

    score = host_score("aspen_bolete", fractions)

    assert score == pytest.approx(1.0)


def test_host_score_is_zero_for_no_compatible_host():
    fractions = {"pine": 0.0, "spruce": 0.0, "birch": 0.0, "aspen": 0.0, "other": 1.0}

    score = host_score("birch_bolete", fractions)

    assert score == 0.0
