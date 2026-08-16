import pytest
from shapely.geometry import box

from shroom_fm.adjacency import classify_pair


def test_classify_pair_keeps_long_touching_border():
    geom_a = box(0, 0, 100, 100)
    geom_b = box(100, 0, 200, 100)

    result = classify_pair(geom_a, geom_b)

    assert result["adjacency_type"] == "touching"
    assert result["transition_length_m"] == pytest.approx(100.0)
    assert result["gap_m"] == 0.0


def test_classify_pair_discards_corner_only_touch():
    geom_a = box(0, 0, 100, 100)
    geom_b = box(100, 100, 200, 200)

    result = classify_pair(geom_a, geom_b)

    assert result is None


def test_classify_pair_keeps_near_gap_with_long_parallel_run():
    geom_a = box(0, 0, 100, 100)
    geom_b = box(105, 0, 205, 100)

    result = classify_pair(geom_a, geom_b)

    assert result["adjacency_type"] == "near_gap"
    assert result["transition_length_m"] == pytest.approx(171.47888553998501)
    assert result["gap_m"] == pytest.approx(5.0)


def test_classify_pair_discards_near_gap_too_short_a_run():
    geom_a = box(0, 0, 20, 20)
    geom_b = box(25, 25, 45, 45)

    result = classify_pair(geom_a, geom_b)

    assert result is None
