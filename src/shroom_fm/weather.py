from datetime import datetime, timedelta
from pathlib import Path

import geopandas as gpd
import pandas as pd

from shroom_fm.eraldis import ESTONIAN_GRID_CRS
from shroom_fm.meps import accumulate_meps_features
from shroom_fm.radar import accumulate_rainfall

MIN_RADAR_COVERAGE = 0.7
MAX_MEPS_STALENESS_HOURS = 6

_RADAR_COLUMNS = (
    "rain_3d_mm",
    "rain_7d_mm",
    "rain_14d_mm",
    "hours_since_rain",
    "wet_hours_72h",
)
_MEPS_COLUMNS = (
    "temp_mean_3d",
    "temp_night_mean_3d",
    "rh_mean_3d",
    "rh_night_mean_3d",
)


def _is_meps_stale(meps_newest_hour: "datetime | None", now: datetime) -> bool:
    return meps_newest_hour is None or (now - meps_newest_hour) > timedelta(
        hours=MAX_MEPS_STALENESS_HOURS
    )


def weather_data_quality(
    radar_coverage: float, meps_newest_hour: "datetime | None", now: datetime
) -> str:
    flags = []
    if radar_coverage < MIN_RADAR_COVERAGE:
        flags.append("partial_radar_gap")
    if _is_meps_stale(meps_newest_hour, now):
        flags.append("stale_meps")
    return ";".join(flags) if flags else "complete"


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

    radar_points, radar_coverage = accumulate_rainfall(radar_cache_dir, now, bounds)
    meps_points, meps_coverage, meps_newest_hour = accumulate_meps_features(now, bounds)

    eraldis_projected = eraldis_gdf.to_crs(ESTONIAN_GRID_CRS)
    radar_joined = _nearest_join(eraldis_projected, radar_points, _RADAR_COLUMNS)
    meps_joined = _nearest_join(eraldis_projected, meps_points, _MEPS_COLUMNS)

    result = eraldis_gdf.copy()
    quality = weather_data_quality(radar_coverage, meps_newest_hour, now)
    radar_degraded = radar_coverage < MIN_RADAR_COVERAGE
    meps_stale = _is_meps_stale(meps_newest_hour, now)

    for col in _RADAR_COLUMNS:
        result[col] = [
            None if radar_degraded or pd.isna(v) else v for v in radar_joined[col]
        ]
    for col in _MEPS_COLUMNS:
        result[col] = [
            None if meps_stale or pd.isna(v) else v for v in meps_joined[col]
        ]

    result["as_of"] = now
    result["weather_data_coverage"] = radar_coverage
    result["weather_data_quality"] = quality

    return gpd.GeoDataFrame(result, geometry="geometry", crs=crs)
