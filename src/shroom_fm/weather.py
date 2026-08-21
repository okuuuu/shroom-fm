from datetime import datetime, timedelta
from pathlib import Path

import geopandas as gpd
import pandas as pd

from shroom_fm.eraldis import ESTONIAN_GRID_CRS
from shroom_fm.meps import accumulate_meps_features
from shroom_fm.radar import (
    _validate_coverage,
    accumulate_rainfall,
    assign_radar_to_eraldis,
)

MIN_RADAR_COVERAGE = 0.7
MAX_MEPS_STALENESS_HOURS = 6
MIN_MEPS_COVERAGE = 0.7

_RADAR_COLUMNS = (
    "rain_3d_mm",
    "rain_7d_mm",
    "rain_14d_mm",
    "hours_since_any_rain",
    "wet_hours_72h",
    "hours_since_significant_rain",
    "hours_since_strong_rain",
    "last_significant_event_mm",
    "last_strong_event_mm",
    "max_24h_rain_14d",
    "coverage_3d",
    "coverage_7d",
    "coverage_14d",
)
_MEPS_COLUMNS = (
    "temp_mean_3d",
    "temp_night_mean_3d",
    "rh_mean_3d",
    "rh_night_mean_3d",
)

_BIN_DIFF_EPSILON = 1e-6


def _bin_difference(minuend, subtrahend, minuend_degraded: bool, subtrahend_degraded: bool):
    if minuend_degraded or subtrahend_degraded or pd.isna(minuend) or pd.isna(subtrahend):
        return None
    diff = minuend - subtrahend
    if diff < -_BIN_DIFF_EPSILON:
        raise ValueError(
            f"Rain bin difference is negative beyond rounding tolerance ({diff}) — "
            "this should be mathematically impossible since the larger window's sum "
            "is a superset of the smaller window's slots; likely an accumulation bug."
        )
    return max(0.0, diff)


def _is_meps_stale(meps_newest_hour: "datetime | None", now: datetime) -> bool:
    return meps_newest_hour is None or (now - meps_newest_hour) > timedelta(
        hours=MAX_MEPS_STALENESS_HOURS
    )


def weather_data_quality(
    radar_coverage: dict,
    meps_coverage: float,
    meps_newest_hour: "datetime | None",
    now: datetime,
) -> str:
    flags = []
    if radar_coverage["3d"] < MIN_RADAR_COVERAGE:
        flags.append("partial_radar_gap_3d")
    if radar_coverage["7d"] < MIN_RADAR_COVERAGE:
        flags.append("partial_radar_gap_7d")
    if radar_coverage["14d"] < MIN_RADAR_COVERAGE:
        flags.append("partial_radar_gap_14d")
    if _is_meps_stale(meps_newest_hour, now):
        flags.append("stale_meps")
    elif meps_coverage < MIN_MEPS_COVERAGE:
        flags.append("partial_meps_gap")
    return ";".join(flags) if flags else "complete"


def _null_if_degraded(values, degraded: bool) -> list:
    return [None if degraded or pd.isna(v) else v for v in values]


def _nearest_join(
    eraldis_projected: gpd.GeoDataFrame,
    points: gpd.GeoDataFrame,
    columns: tuple[str, ...],
) -> pd.DataFrame:
    if points.empty:
        return pd.DataFrame(
            {col: [None] * len(eraldis_projected) for col in columns},
            index=eraldis_projected.index,
        )
    joined = gpd.sjoin_nearest(
        eraldis_projected[["geometry"]],
        points[["geometry", *columns]],
        how="left",
    )
    return joined.groupby(level=0).first().reindex(eraldis_projected.index)[list(columns)]


def refresh_weather(
    eraldis_gdf: gpd.GeoDataFrame, radar_cache_dir: Path, now: datetime
) -> gpd.GeoDataFrame:
    crs = eraldis_gdf.crs
    bounds = tuple(eraldis_gdf.to_crs("EPSG:4326").total_bounds)

    # accumulate_rainfall's second return value (the old national/dataset-wide coverage
    # dict) is deliberately unused here — coverage is computed per-stand below from each
    # stand's own joined coverage_Nd columns instead. The call itself stays: the
    # national-coverage computation inside accumulate_rainfall is still load-bearing for
    # its own internal 0<=coverage<=1 invariant validation.
    radar_points, _ = accumulate_rainfall(radar_cache_dir, now, bounds)
    meps_points, meps_coverage, meps_newest_hour = accumulate_meps_features(now, bounds)

    eraldis_projected = eraldis_gdf.to_crs(ESTONIAN_GRID_CRS)
    radar_joined = assign_radar_to_eraldis(eraldis_projected, radar_points, _RADAR_COLUMNS)
    meps_joined = _nearest_join(eraldis_projected, meps_points, _MEPS_COLUMNS)

    result = eraldis_gdf.copy()

    # Per-stand coverage — each stand's own joined coverage_Nd value, run through the
    # same 0<=coverage<=1 invariant used everywhere else. A stand with zero valid
    # radar pixels intersecting it (assign_radar_to_eraldis returns None for
    # coverage_Nd in that case) is treated as coverage 0.0 for degradation purposes —
    # genuinely uncovered, not an unknown to be silently skipped.
    def _stand_coverage(value) -> float:
        return _validate_coverage(0.0 if pd.isna(value) else float(value), label="stand")

    radar_degraded_3d = [
        _stand_coverage(v) < MIN_RADAR_COVERAGE for v in radar_joined["coverage_3d"]
    ]
    radar_degraded_7d = [
        _stand_coverage(v) < MIN_RADAR_COVERAGE for v in radar_joined["coverage_7d"]
    ]
    radar_degraded_14d = [
        _stand_coverage(v) < MIN_RADAR_COVERAGE for v in radar_joined["coverage_14d"]
    ]
    meps_degraded = (
        _is_meps_stale(meps_newest_hour, now) or meps_coverage < MIN_MEPS_COVERAGE
    )

    def _null_if_degraded_per_stand(values, degraded_flags) -> list:
        return [
            None if degraded or pd.isna(v) else v
            for v, degraded in zip(values, degraded_flags)
        ]

    result["rain_3d_mm"] = _null_if_degraded_per_stand(
        radar_joined["rain_3d_mm"], radar_degraded_3d
    )
    result["wet_hours_72h"] = _null_if_degraded_per_stand(
        radar_joined["wet_hours_72h"], radar_degraded_3d
    )
    result["rain_7d_mm"] = _null_if_degraded_per_stand(
        radar_joined["rain_7d_mm"], radar_degraded_7d
    )
    result["rain_14d_mm"] = _null_if_degraded_per_stand(
        radar_joined["rain_14d_mm"], radar_degraded_14d
    )
    result["hours_since_any_rain"] = _null_if_degraded_per_stand(
        radar_joined["hours_since_any_rain"], radar_degraded_14d
    )
    for col in (
        "hours_since_significant_rain",
        "hours_since_strong_rain",
        "last_significant_event_mm",
        "last_strong_event_mm",
        "max_24h_rain_14d",
    ):
        result[col] = _null_if_degraded_per_stand(radar_joined[col], radar_degraded_14d)

    result["rain_0_3d_mm"] = [
        None if degraded or pd.isna(v) else v
        for v, degraded in zip(radar_joined["rain_3d_mm"], radar_degraded_3d)
    ]
    result["rain_3_7d_mm"] = [
        _bin_difference(v7, v3, degraded_7d, degraded_3d)
        for v7, v3, degraded_7d, degraded_3d in zip(
            radar_joined["rain_7d_mm"],
            radar_joined["rain_3d_mm"],
            radar_degraded_7d,
            radar_degraded_3d,
        )
    ]
    result["rain_7_14d_mm"] = [
        _bin_difference(v14, v7, degraded_14d, degraded_7d)
        for v14, v7, degraded_14d, degraded_7d in zip(
            radar_joined["rain_14d_mm"],
            radar_joined["rain_7d_mm"],
            radar_degraded_14d,
            radar_degraded_7d,
        )
    ]
    for col in _MEPS_COLUMNS:
        result[col] = _null_if_degraded(meps_joined[col], meps_degraded)

    result["as_of"] = now
    result["weather_data_coverage"] = [
        _stand_coverage(v) for v in radar_joined["coverage_14d"]
    ]
    result["weather_data_quality"] = [
        weather_data_quality(
            {"3d": _stand_coverage(c3), "7d": _stand_coverage(c7), "14d": _stand_coverage(c14)},
            meps_coverage,
            meps_newest_hour,
            now,
        )
        for c3, c7, c14 in zip(
            radar_joined["coverage_3d"], radar_joined["coverage_7d"], radar_joined["coverage_14d"]
        )
    ]

    return gpd.GeoDataFrame(result, geometry="geometry", crs=crs)
