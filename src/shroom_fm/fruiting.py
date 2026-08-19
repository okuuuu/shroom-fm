"""FruitingScore: a weather-driven, per-species "worth scouting now" signal.

FruitingScore = SeasonPrior x MoistureTrigger x TemperatureModifier x PersistenceModifier

All numeric constants in this module (RAIN_SCALE_MM, MOISTURE_WEIGHTS, temperature and
persistence thresholds, SEASON_PRIORS) are Estonian v0 engineering priors, not measured
mycological constants — see docs/superpowers/specs/2026-08-19-fruiting-score-design.md
for the reasoning. Expected to be recalibrated against real observations later.
"""

import datetime as _datetime
import math

import geopandas as gpd

TARGET_SPECIES = ["kitsemampel", "chanterelle", "aspen_bolete", "birch_bolete", "porcini"]


def _is_missing(value) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


RAIN_SCALE_MM = {"0_3d": 8.0, "3_7d": 12.0, "7_14d": 18.0}

MOISTURE_WEIGHTS = {
    "chanterelle":  {"0_3d": 0.15, "3_7d": 0.35, "7_14d": 0.50},
    "kitsemampel":  {"0_3d": 0.15, "3_7d": 0.35, "7_14d": 0.50},
    "aspen_bolete": {"0_3d": 0.20, "3_7d": 0.45, "7_14d": 0.35},
    "birch_bolete": {"0_3d": 0.20, "3_7d": 0.45, "7_14d": 0.35},
    "porcini":      {"0_3d": 0.20, "3_7d": 0.45, "7_14d": 0.35},
}


def rain_response(mm: float, scale_mm: float) -> float:
    return 1.0 - math.exp(-mm / scale_mm)


def moisture_trigger(
    species: str,
    rain_0_3d: float | None,
    rain_3_7d: float | None,
    rain_7_14d: float | None,
) -> float | None:
    if _is_missing(rain_0_3d) or _is_missing(rain_3_7d) or _is_missing(rain_7_14d):
        return None
    w = MOISTURE_WEIGHTS[species]
    return (
        w["0_3d"] * rain_response(rain_0_3d, RAIN_SCALE_MM["0_3d"])
        + w["3_7d"] * rain_response(rain_3_7d, RAIN_SCALE_MM["3_7d"])
        + w["7_14d"] * rain_response(rain_7_14d, RAIN_SCALE_MM["7_14d"])
    )


TEMP_FLOOR = 0.4
TEMP_COLD_RAMP_START_C = 2.0
TEMP_COLD_RAMP_END_C = 8.0
TEMP_WARM_RAMP_START_C = 18.0
TEMP_WARM_RAMP_END_C = 26.0
FROST_GUARD_START_C = 2.0
FROST_GUARD_END_C = -2.0
FROST_GUARD_FLOOR = 0.6


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _temperature_base_curve(temp_c: float) -> float:
    if temp_c < TEMP_COLD_RAMP_START_C or temp_c > TEMP_WARM_RAMP_END_C:
        return TEMP_FLOOR
    if temp_c < TEMP_COLD_RAMP_END_C:
        fraction = (temp_c - TEMP_COLD_RAMP_START_C) / (
            TEMP_COLD_RAMP_END_C - TEMP_COLD_RAMP_START_C
        )
        return TEMP_FLOOR + fraction * (1.0 - TEMP_FLOOR)
    if temp_c <= TEMP_WARM_RAMP_START_C:
        return 1.0
    fraction = (temp_c - TEMP_WARM_RAMP_START_C) / (
        TEMP_WARM_RAMP_END_C - TEMP_WARM_RAMP_START_C
    )
    return 1.0 - fraction * (1.0 - TEMP_FLOOR)


def _frost_guard(temp_night_c: float) -> float:
    if temp_night_c >= FROST_GUARD_START_C:
        return 1.0
    if temp_night_c <= FROST_GUARD_END_C:
        return FROST_GUARD_FLOOR
    fraction = (FROST_GUARD_START_C - temp_night_c) / (
        FROST_GUARD_START_C - FROST_GUARD_END_C
    )
    return 1.0 - fraction * (1.0 - FROST_GUARD_FLOOR)


def temperature_modifier(
    temp_mean_3d: float | None, temp_night_mean_3d: float | None
) -> float | None:
    if _is_missing(temp_mean_3d) or _is_missing(temp_night_mean_3d):
        return None
    return _temperature_base_curve(temp_mean_3d) * _frost_guard(temp_night_mean_3d)


PERSISTENCE_FLOOR = 0.6
NIGHT_RH_LOW = 65.0
NIGHT_RH_HIGH = 90.0
WET_HOURS_SCALE = 6.0
SIGNIFICANT_RAIN_HALF_LIFE_HOURS = 72.0


def persistence_modifier(
    rh_night_mean_3d: float | None,
    wet_hours_72h: float | None,
    hours_since_significant_rain: float | None,
) -> float | None:
    if _is_missing(rh_night_mean_3d) or _is_missing(wet_hours_72h):
        return None
    night_rh_score = _clamp(
        (rh_night_mean_3d - NIGHT_RH_LOW) / (NIGHT_RH_HIGH - NIGHT_RH_LOW), 0.0, 1.0
    )
    wet_hours_score = 1.0 - math.exp(-wet_hours_72h / WET_HOURS_SCALE)
    if _is_missing(hours_since_significant_rain):
        recency_score = 0.0
    else:
        recency_score = math.exp(
            -math.log(2) * hours_since_significant_rain / SIGNIFICANT_RAIN_HALF_LIFE_HOURS
        )
    persistence_raw = 0.50 * night_rh_score + 0.20 * wet_hours_score + 0.30 * recency_score
    return PERSISTENCE_FLOOR + (1 - PERSISTENCE_FLOOR) * persistence_raw


SEASON_PRIORS = {
    "chanterelle": [
        ("06-01", 0.10), ("06-15", 0.35), ("07-01", 0.85), ("07-15", 1.00),
        ("09-10", 1.00), ("10-01", 0.35), ("10-15", 0.10),
    ],
    "kitsemampel": [
        ("07-20", 0.05), ("08-01", 0.20), ("08-15", 0.85), ("08-25", 1.00),
        ("09-20", 1.00), ("10-01", 0.30), ("10-10", 0.05),
    ],
    "aspen_bolete": [
        ("07-01", 0.15), ("07-15", 0.65), ("08-01", 1.00),
        ("09-20", 1.00), ("10-05", 0.30), ("10-15", 0.05),
    ],
    "birch_bolete": [
        ("07-01", 0.15), ("07-20", 0.70), ("08-01", 1.00),
        ("09-20", 1.00), ("10-05", 0.30), ("10-15", 0.05),
    ],
    "porcini": [
        ("07-01", 0.10), ("07-15", 0.60), ("08-01", 1.00),
        ("09-25", 1.00), ("10-05", 0.35), ("10-15", 0.05),
    ],
}


def _month_day_to_ordinal(month_day: str) -> int:
    month, day = (int(part) for part in month_day.split("-"))
    # Fixed non-leap reference year — no SEASON_PRIORS knot falls on Feb 29, so any
    # non-leap year gives a consistent, comparable day-of-year ordinal.
    return _datetime.date(2001, month, day).toordinal()


def season_prior(species: str, month_day: str) -> float:
    knots = SEASON_PRIORS[species]
    target = _month_day_to_ordinal(month_day)
    knot_ordinals = [_month_day_to_ordinal(md) for md, _ in knots]
    if target <= knot_ordinals[0]:
        return knots[0][1]
    if target >= knot_ordinals[-1]:
        return knots[-1][1]
    for i in range(len(knots) - 1):
        lo_ord, lo_val = knot_ordinals[i], knots[i][1]
        hi_ord, hi_val = knot_ordinals[i + 1], knots[i + 1][1]
        if lo_ord <= target <= hi_ord:
            fraction = (target - lo_ord) / (hi_ord - lo_ord)
            return lo_val + fraction * (hi_val - lo_val)
    return knots[-1][1]


def fruiting_score(
    species: str,
    month_day: str,
    rain_0_3d: float | None,
    rain_3_7d: float | None,
    rain_7_14d: float | None,
    temp_mean_3d: float | None,
    temp_night_mean_3d: float | None,
    rh_night_mean_3d: float | None,
    wet_hours_72h: float | None,
    hours_since_significant_rain: float | None,
) -> tuple[float | None, dict]:
    moisture = moisture_trigger(species, rain_0_3d, rain_3_7d, rain_7_14d)
    temperature = temperature_modifier(temp_mean_3d, temp_night_mean_3d)
    persistence = persistence_modifier(
        rh_night_mean_3d, wet_hours_72h, hours_since_significant_rain
    )
    season = season_prior(species, month_day)

    components = {
        "fruiting_moisture_score": moisture,
        "fruiting_temperature_modifier": temperature,
        "fruiting_persistence_modifier": persistence,
        "fruiting_season_prior": season,
    }

    if moisture is None or temperature is None or persistence is None:
        return None, components

    return season * moisture * temperature * persistence, components


def score_stands(weather_gdf: "gpd.GeoDataFrame", now) -> "gpd.GeoDataFrame":
    result = weather_gdf.copy()
    month_day = now.strftime("%m-%d")

    temp_modifiers = []
    persistence_modifiers = []
    for _, row in result.iterrows():
        temp_modifiers.append(
            temperature_modifier(row["temp_mean_3d"], row["temp_night_mean_3d"])
        )
        persistence_modifiers.append(
            persistence_modifier(
                row["rh_night_mean_3d"],
                row["wet_hours_72h"],
                row["hours_since_significant_rain"],
            )
        )
    result["fruiting_temperature_modifier"] = temp_modifiers
    result["fruiting_persistence_modifier"] = persistence_modifiers

    for species in TARGET_SPECIES:
        scores = []
        moisture_scores = []
        season_priors = []
        for _, row in result.iterrows():
            score, components = fruiting_score(
                species,
                month_day,
                row["rain_0_3d_mm"],
                row["rain_3_7d_mm"],
                row["rain_7_14d_mm"],
                row["temp_mean_3d"],
                row["temp_night_mean_3d"],
                row["rh_night_mean_3d"],
                row["wet_hours_72h"],
                row["hours_since_significant_rain"],
            )
            scores.append(score)
            moisture_scores.append(components["fruiting_moisture_score"])
            season_priors.append(components["fruiting_season_prior"])
        result[f"fruiting_score_{species}"] = scores
        result[f"fruiting_moisture_score_{species}"] = moisture_scores
        result[f"fruiting_season_prior_{species}"] = season_priors

    return result
