import math
from datetime import datetime, timezone

import geopandas as gpd
import pytest
from shapely.geometry import Point

from shroom_fm.fruiting import (
    fruiting_score,
    moisture_trigger,
    persistence_modifier,
    score_stands,
    season_prior,
    rain_response,
    temperature_modifier,
)


def _utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def test_rain_response_at_scale_is_one_minus_e_inverse():
    assert rain_response(8.0, 8.0) == pytest.approx(1 - math.exp(-1))


def test_rain_response_at_zero_is_zero():
    assert rain_response(0.0, 8.0) == pytest.approx(0.0)


def test_moisture_trigger_all_bins_at_their_own_scale_sums_weights_times_shared_fraction():
    # All 3 bins exactly at their own scale -> each rain_response = 1-e^-1; weights
    # sum to 1.0 for every species, so the result is just that shared fraction.
    result = moisture_trigger("chanterelle", rain_0_3d=8.0, rain_3_7d=12.0, rain_7_14d=18.0)
    assert result == pytest.approx(1 - math.exp(-1))


def test_moisture_trigger_returns_none_when_any_bin_missing():
    assert moisture_trigger("chanterelle", None, 12.0, 18.0) is None
    assert moisture_trigger("chanterelle", 8.0, None, 18.0) is None
    assert moisture_trigger("chanterelle", 8.0, 12.0, None) is None


def test_moisture_trigger_handles_nan_as_missing():
    assert moisture_trigger("chanterelle", float("nan"), 12.0, 18.0) is None


def test_temperature_modifier_plateau_with_no_frost():
    assert temperature_modifier(12.0, 5.0) == pytest.approx(1.0)


def test_temperature_modifier_cold_ramp_with_no_frost():
    # (5-2)/(8-2) = 0.5 -> 0.4 + 0.5*0.6 = 0.7
    assert temperature_modifier(5.0, 5.0) == pytest.approx(0.7)


def test_temperature_modifier_applies_soft_frost_guard():
    # base=1.0 (plateau); frost: (2-0)/(2-(-2))=0.5 -> 1.0 - 0.5*0.4 = 0.8
    assert temperature_modifier(12.0, 0.0) == pytest.approx(0.8)


def test_temperature_modifier_floors_far_below_and_above_range():
    assert temperature_modifier(-10.0, -10.0) == pytest.approx(0.4 * 0.6)
    assert temperature_modifier(35.0, 15.0) == pytest.approx(0.4)


def test_temperature_modifier_returns_none_when_missing():
    assert temperature_modifier(None, 5.0) is None
    assert temperature_modifier(12.0, None) is None


def test_persistence_modifier_full_favorable_conditions():
    # night_rh_score=(90-65)/25=1.0; wet_hours_score=1-e^-1; recency_score=e^0=1.0
    # raw = .5*1 + .2*(1-e^-1) + .3*1 = 0.8 + 0.2*(1-e^-1)
    expected_raw = 0.5 * 1.0 + 0.2 * (1 - math.exp(-1)) + 0.3 * 1.0
    expected = 0.6 + 0.4 * expected_raw
    assert persistence_modifier(90.0, 6.0, 0.0) == pytest.approx(expected)


def test_persistence_modifier_nan_recency_treated_as_zero_not_missing():
    # NaN hours_since_significant_rain means "no qualifying event ever" -> recency=0,
    # NOT a missing-data None.
    result = persistence_modifier(90.0, 6.0, float("nan"))
    assert result is not None
    expected_raw = 0.5 * 1.0 + 0.2 * (1 - math.exp(-1)) + 0.3 * 0.0
    assert result == pytest.approx(0.6 + 0.4 * expected_raw)


def test_persistence_modifier_returns_none_when_rh_or_wet_hours_missing():
    assert persistence_modifier(None, 6.0, 0.0) is None
    assert persistence_modifier(90.0, None, 0.0) is None


def test_season_prior_exact_knot():
    assert season_prior("chanterelle", "07-01") == pytest.approx(0.85)


def test_season_prior_interpolates_between_knots():
    # 07-01=.85, 07-15=1.00 -> 07-08 is halfway -> 0.925
    assert season_prior("chanterelle", "07-08") == pytest.approx(0.925)


def test_season_prior_flat_extrapolates_before_first_knot():
    assert season_prior("chanterelle", "05-01") == pytest.approx(0.10)


def test_season_prior_flat_extrapolates_after_last_knot():
    assert season_prior("chanterelle", "11-01") == pytest.approx(0.10)


def test_fruiting_score_is_product_of_four_factors():
    score, components = fruiting_score(
        "chanterelle",
        "07-01",  # season_prior = 0.85
        rain_0_3d=8.0, rain_3_7d=12.0, rain_7_14d=18.0,  # moisture = 1-e^-1
        temp_mean_3d=12.0, temp_night_mean_3d=5.0,  # temperature = 1.0
        rh_night_mean_3d=90.0, wet_hours_72h=6.0, hours_since_significant_rain=0.0,
    )
    moisture = 1 - math.exp(-1)
    persistence_raw = 0.5 * 1.0 + 0.2 * (1 - math.exp(-1)) + 0.3 * 1.0
    persistence = 0.6 + 0.4 * persistence_raw
    expected = 0.85 * moisture * 1.0 * persistence
    assert score == pytest.approx(expected)
    assert components["fruiting_season_prior"] == pytest.approx(0.85)
    assert components["fruiting_moisture_score"] == pytest.approx(moisture)
    assert components["fruiting_temperature_modifier"] == pytest.approx(1.0)
    assert components["fruiting_persistence_modifier"] == pytest.approx(persistence)


def test_fruiting_score_is_none_when_moisture_missing_but_components_still_reported():
    score, components = fruiting_score(
        "chanterelle",
        "07-01",
        rain_0_3d=None, rain_3_7d=12.0, rain_7_14d=18.0,
        temp_mean_3d=12.0, temp_night_mean_3d=5.0,
        rh_night_mean_3d=90.0, wet_hours_72h=6.0, hours_since_significant_rain=0.0,
    )
    assert score is None
    assert components["fruiting_moisture_score"] is None
    assert components["fruiting_season_prior"] == pytest.approx(0.85)  # still computed


def test_score_stands_adds_per_species_and_shared_columns():
    weather_gdf = gpd.GeoDataFrame(
        {
            "id": [1],
            "rain_0_3d_mm": [8.0],
            "rain_3_7d_mm": [12.0],
            "rain_7_14d_mm": [18.0],
            "temp_mean_3d": [12.0],
            "temp_night_mean_3d": [5.0],
            "rh_night_mean_3d": [90.0],
            "wet_hours_72h": [6.0],
            "hours_since_significant_rain": [0.0],
        },
        geometry=[Point(24.0, 59.0)],
        crs="EPSG:4326",
    )

    result = score_stands(weather_gdf, _utc(2026, 7, 1))

    assert "fruiting_score_chanterelle" in result.columns
    assert "fruiting_score_kitsemampel" in result.columns
    assert "fruiting_moisture_score_chanterelle" in result.columns
    assert "fruiting_season_prior_chanterelle" in result.columns
    assert "fruiting_temperature_modifier" in result.columns  # shared, not per-species
    assert "fruiting_persistence_modifier" in result.columns  # shared, not per-species
    assert result.loc[0, "fruiting_score_chanterelle"] is not None
    assert result.loc[0, "fruiting_score_chanterelle"] > 0
