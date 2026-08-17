import pytest

from shroom_fm.roads import (
    CAR_CLASS_CONDITIONAL,
    CAR_CLASS_HIGH_CONFIDENCE,
    CAR_CLASS_NORMAL,
    CAR_CLASS_WALK_ONLY,
    classify_car_class,
)


def test_classify_car_class_pohimaantee_is_high_confidence():
    assert classify_car_class("Põhimaantee", None) == CAR_CLASS_HIGH_CONFIDENCE


def test_classify_car_class_tugimaantee_is_high_confidence():
    assert classify_car_class("Tugimaantee", None) == CAR_CLASS_HIGH_CONFIDENCE


def test_classify_car_class_korvalmaantee_is_high_confidence():
    assert classify_car_class("Kõrvalmaantee", None) == CAR_CLASS_HIGH_CONFIDENCE


def test_classify_car_class_ramp_is_high_confidence():
    assert classify_car_class("Ramp või ühendustee", None) == CAR_CLASS_HIGH_CONFIDENCE


def test_classify_car_class_tanav_is_high_confidence():
    assert classify_car_class("Tänav", "Püsikate") == CAR_CLASS_HIGH_CONFIDENCE


def test_classify_car_class_muu_tee_with_pusikate_is_normal():
    assert classify_car_class("Muu tee", "Püsikate") == CAR_CLASS_NORMAL


def test_classify_car_class_muu_tee_with_kruuskate_is_normal():
    assert classify_car_class("Muu tee", "Kruuskate") == CAR_CLASS_NORMAL


def test_classify_car_class_muu_tee_with_kivikate_is_normal():
    assert classify_car_class("Muu tee", "Kivikate") == CAR_CLASS_NORMAL


def test_classify_car_class_muu_tee_with_pinnas_is_conditional():
    assert classify_car_class("Muu tee", "Pinnas") == CAR_CLASS_CONDITIONAL


def test_classify_car_class_rada_is_walk_only():
    assert classify_car_class("Rada", "Pinnas") == CAR_CLASS_WALK_ONLY


def test_classify_car_class_kergliiklustee_is_walk_only():
    assert classify_car_class("Kergliiklustee", "Püsikate") == CAR_CLASS_WALK_ONLY


def test_classify_car_class_raises_for_unrecognized_tyyp_tekst():
    with pytest.raises(ValueError):
        classify_car_class("Mingi tundmatu tüüp", "Püsikate")


def test_classify_car_class_raises_for_unrecognized_muu_tee_surface():
    with pytest.raises(ValueError):
        classify_car_class("Muu tee", "Mingi tundmatu kate")
