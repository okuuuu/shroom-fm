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
    # Fixed leap-year reference year (2000): the *target* month_day passed to
    # season_prior comes from the real wall clock (now.strftime("%m-%d")), and a
    # real Feb 29 does occur — a non-leap reference year would raise ValueError on
    # that input. Any leap year works equally well as a reference for relative
    # ordinal comparison, as long as it's used consistently for both the knot
    # table and the target date, which it is (both go through this function).
    return _datetime.date(2000, month, day).toordinal()


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


# Internal sentinel distinguishing "caller didn't pass this" from "caller passed
# None (a real missing-data value)" for fruiting_score's precomputed-* kwargs below.
_UNSET = object()


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
    *,
    _precomputed_temperature=_UNSET,
    _precomputed_persistence=_UNSET,
    _precomputed_season=_UNSET,
) -> tuple[float | None, dict]:
    """Compute FruitingScore for one (species, row) pair.

    The three leading-underscore keyword-only params are an internal performance
    escape hatch for score_stands (below), which already computes
    temperature_modifier/persistence_modifier once per row (shared across all 5
    species) and season_prior once per species (constant for the whole run) —
    passing them in here avoids recomputing the same value 5x per row. Not part
    of this function's public contract: every positional-only caller (including
    all existing tests) is unaffected and gets the original from-scratch
    computation.
    """
    moisture = moisture_trigger(species, rain_0_3d, rain_3_7d, rain_7_14d)
    temperature = (
        temperature_modifier(temp_mean_3d, temp_night_mean_3d)
        if _precomputed_temperature is _UNSET
        else _precomputed_temperature
    )
    persistence = (
        persistence_modifier(rh_night_mean_3d, wet_hours_72h, hours_since_significant_rain)
        if _precomputed_persistence is _UNSET
        else _precomputed_persistence
    )
    season = (
        season_prior(species, month_day)
        if _precomputed_season is _UNSET
        else _precomputed_season
    )

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

    # Computed once per row (shared across all 5 species) via the zip()-over-
    # columns idiom used elsewhere in this codebase (habitat.score_stands,
    # scout.remote_high_value_for_species) instead of iterrows(), which builds a
    # full pandas Series per row.
    temp_modifiers = [
        temperature_modifier(temp_mean_3d, temp_night_mean_3d)
        for temp_mean_3d, temp_night_mean_3d in zip(
            result["temp_mean_3d"], result["temp_night_mean_3d"]
        )
    ]
    persistence_modifiers = [
        persistence_modifier(rh_night_mean_3d, wet_hours_72h, hours_since_significant_rain)
        for rh_night_mean_3d, wet_hours_72h, hours_since_significant_rain in zip(
            result["rh_night_mean_3d"],
            result["wet_hours_72h"],
            result["hours_since_significant_rain"],
        )
    ]
    result["fruiting_temperature_modifier"] = temp_modifiers
    result["fruiting_persistence_modifier"] = persistence_modifiers

    for species in TARGET_SPECIES:
        # season_prior depends only on (species, month_day) — both fixed for this
        # entire run — so compute it once per species (5 total), not once per row.
        season = season_prior(species, month_day)
        scores = []
        moisture_scores = []
        season_priors = []
        for (
            rain_0_3d,
            rain_3_7d,
            rain_7_14d,
            temp_mean_3d,
            temp_night_mean_3d,
            rh_night_mean_3d,
            wet_hours_72h,
            hours_since_significant_rain,
            temp_mod,
            pers_mod,
        ) in zip(
            result["rain_0_3d_mm"],
            result["rain_3_7d_mm"],
            result["rain_7_14d_mm"],
            result["temp_mean_3d"],
            result["temp_night_mean_3d"],
            result["rh_night_mean_3d"],
            result["wet_hours_72h"],
            result["hours_since_significant_rain"],
            temp_modifiers,
            persistence_modifiers,
        ):
            score, components = fruiting_score(
                species,
                month_day,
                rain_0_3d,
                rain_3_7d,
                rain_7_14d,
                temp_mean_3d,
                temp_night_mean_3d,
                rh_night_mean_3d,
                wet_hours_72h,
                hours_since_significant_rain,
                _precomputed_temperature=temp_mod,
                _precomputed_persistence=pers_mod,
                _precomputed_season=season,
            )
            scores.append(score)
            moisture_scores.append(components["fruiting_moisture_score"])
            season_priors.append(components["fruiting_season_prior"])
        result[f"fruiting_score_{species}"] = scores
        result[f"fruiting_moisture_score_{species}"] = moisture_scores
        result[f"fruiting_season_prior_{species}"] = season_priors

    return result


def _none_if_nan(value):
    import pandas as pd
    return None if pd.isna(value) else value


_WEATHER_META_SOURCE_COLUMNS = ("weather_data_quality", "weather_data_coverage", "as_of")


def _worst_quality(quality_a: str, quality_b: str) -> str:
    """Report the more-degraded of two per-stand weather_data_quality strings.

    "complete" always loses to any non-complete (degraded) value. If both stands
    are degraded with different flag strings, pick a deterministic (lexicographic)
    tie-break rather than an arbitrary/order-dependent one.
    """
    if quality_a == quality_b:
        return quality_a
    if quality_a == "complete":
        return quality_b
    if quality_b == "complete":
        return quality_a
    return min(quality_a, quality_b)


def join_ecotone_fruiting(
    ecotones_gdf: "gpd.GeoDataFrame", weather_gdf: "gpd.GeoDataFrame"
) -> "gpd.GeoDataFrame":
    result = ecotones_gdf.copy()

    for species in TARGET_SPECIES:
        score_col = f"fruiting_score_{species}"
        # If weather_gdf lacks this species' column (e.g. score_fruiting.py hasn't
        # been re-run since the last refresh_weather.py run), the absence must
        # propagate as an explicit None for every row — never silently preserve a
        # stale fruiting_modifier_{species} value already present in `result` from
        # a previous run of this function against `ecotones_gdf`.
        if score_col not in weather_gdf.columns:
            result[f"fruiting_modifier_{species}"] = None
            continue
        scores_by_id = dict(zip(weather_gdf["id"], weather_gdf[score_col]))
        modifiers = []
        for id_a, id_b in zip(result["id_a"], result["id_b"]):
            score_a = _none_if_nan(scores_by_id.get(id_a))
            score_b = _none_if_nan(scores_by_id.get(id_b))
            if score_a is None or score_b is None:
                modifiers.append(None)
            else:
                modifiers.append((score_a + score_b) / 2.0)
        result[f"fruiting_modifier_{species}"] = modifiers

    # weather_data_quality/weather_data_coverage/weather_as_of: species-independent,
    # per-stand columns carried from weather_gdf onto the ecotone as a "worst of the
    # two adjacent stands" summary, so a MISSING_FRUITING_DATA row in the final
    # export is self-explanatory without cross-referencing weather_eraldis.geojson.
    if not set(_WEATHER_META_SOURCE_COLUMNS) <= set(weather_gdf.columns):
        result["weather_data_quality"] = None
        result["weather_data_coverage"] = None
        result["weather_as_of"] = None
        return result

    quality_by_id = dict(zip(weather_gdf["id"], weather_gdf["weather_data_quality"]))
    coverage_by_id = dict(zip(weather_gdf["id"], weather_gdf["weather_data_coverage"]))
    as_of_by_id = dict(zip(weather_gdf["id"], weather_gdf["as_of"]))

    qualities, coverages, as_ofs = [], [], []
    for id_a, id_b in zip(result["id_a"], result["id_b"]):
        quality_a = _none_if_nan(quality_by_id.get(id_a))
        quality_b = _none_if_nan(quality_by_id.get(id_b))
        coverage_a = _none_if_nan(coverage_by_id.get(id_a))
        coverage_b = _none_if_nan(coverage_by_id.get(id_b))
        as_of_a = _none_if_nan(as_of_by_id.get(id_a))
        as_of_b = _none_if_nan(as_of_by_id.get(id_b))

        # Never fabricate a one-sided value: if either stand's id is missing
        # entirely from weather_gdf (or its value is null), report None — same
        # discipline as fruiting_modifier_{species} above.
        qualities.append(
            None if quality_a is None or quality_b is None
            else _worst_quality(quality_a, quality_b)
        )
        coverages.append(
            None if coverage_a is None or coverage_b is None
            else min(coverage_a, coverage_b)
        )
        as_ofs.append(
            None if as_of_a is None or as_of_b is None else min(as_of_a, as_of_b)
        )

    result["weather_data_quality"] = qualities
    result["weather_data_coverage"] = coverages
    result["weather_as_of"] = as_ofs

    return result
