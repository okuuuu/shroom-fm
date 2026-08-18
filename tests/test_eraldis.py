import pytest

from shroom_fm.eraldis import fetch_eraldis_annulus


def test_fetch_eraldis_annulus_raises_when_inner_radius_not_less_than_outer():
    with pytest.raises(ValueError):
        fetch_eraldis_annulus(59.4370, 24.7536, radius_km=20.0, inner_radius_km=20.0)
