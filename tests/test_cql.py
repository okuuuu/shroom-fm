import pytest

from shroom_fm.cql import annulus_filter, estonian_grid_point


def test_estonian_grid_point_returns_northing_first_point():
    lat, lon = 59.451081455185864, 24.818002100965362

    result = estonian_grid_point(lat, lon)

    assert result == "POINT(6590647.722702539 546398.5907798207)"


def test_annulus_filter_omits_beyond_clause_when_inner_radius_is_zero():
    lat, lon = 59.451081455185864, 24.818002100965362

    result = annulus_filter("shape", lat, lon, radius_km=20.0, inner_radius_km=0.0)

    assert result == (
        "DWITHIN(shape, POINT(6590647.722702539 546398.5907798207), 20000.0, meters)"
    )
    assert "BEYOND" not in result


def test_annulus_filter_includes_beyond_clause_when_inner_radius_is_positive():
    lat, lon = 59.451081455185864, 24.818002100965362

    result = annulus_filter("shape", lat, lon, radius_km=20.0, inner_radius_km=5.0)

    assert result == (
        "DWITHIN(shape, POINT(6590647.722702539 546398.5907798207), 20000.0, meters) "
        "AND BEYOND(shape, POINT(6590647.722702539 546398.5907798207), 5000.0, meters)"
    )


def test_annulus_filter_raises_when_inner_radius_not_less_than_outer():
    with pytest.raises(ValueError):
        annulus_filter("shape", 59.4370, 24.7536, radius_km=20.0, inner_radius_km=20.0)
