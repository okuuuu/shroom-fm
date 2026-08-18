from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import geopandas as gpd
import numpy as np
import pandas as pd
import pyproj
import xarray as xr

MEPS_LATEST_URL = (
    "https://thredds.met.no/thredds/dodsC/metpplatest/met_analysis_1_0km_nordic_latest.nc"
)
MEPS_ARCHIVE_URL_TEMPLATE = (
    "https://thredds.met.no/thredds/dodsC/metpparchive/{year:04d}/{month:02d}/{day:02d}/"
    "met_analysis_1_0km_nordic_{year:04d}{month:02d}{day:02d}T{hour:02d}Z.nc"
)
MEPS_LCC_PROJ4 = "+proj=lcc +lat_0=63 +lon_0=15 +lat_1=63 +lat_2=63 +no_defs +R=6371000"
TALLINN_TZ = ZoneInfo("Europe/Tallinn")
_MEPS_WINDOW_HOURS = 72
_MEPS_STALENESS_HOURS = 6


def meps_hourly_url(hour: datetime) -> str:
    return MEPS_ARCHIVE_URL_TEMPLATE.format(
        year=hour.year, month=hour.month, day=hour.day, hour=hour.hour
    )


def fetch_meps_hourly(
    hour: datetime, bbox_wgs84: tuple[float, float, float, float]
) -> "xr.Dataset | None":
    lcc_crs = pyproj.CRS.from_proj4(MEPS_LCC_PROJ4)
    to_lcc = pyproj.Transformer.from_crs("EPSG:4326", lcc_crs, always_xy=True)
    min_lon, min_lat, max_lon, max_lat = bbox_wgs84
    xs, ys = to_lcc.transform([min_lon, max_lon], [min_lat, max_lat])
    x_slice = slice(min(xs), max(xs))
    y_slice = slice(min(ys), max(ys))

    expected_time = np.datetime64(pd.Timestamp(hour).tz_localize(None))

    for url, verify_time in ((meps_hourly_url(hour), False), (MEPS_LATEST_URL, True)):
        try:
            dataset = xr.open_dataset(url)
        except OSError:
            continue
        try:
            if verify_time:
                actual_time = dataset["time"].values[0]
                if actual_time != expected_time:
                    continue
            subset = dataset.sel(x=x_slice, y=y_slice)
            if subset.sizes.get("x", 0) == 0 or subset.sizes.get("y", 0) == 0:
                continue
            return subset.load()
        finally:
            dataset.close()
    return None


def meps_dataset_to_points(dataset: "xr.Dataset") -> "gpd.GeoDataFrame":
    lcc_crs = pyproj.CRS.from_proj4(MEPS_LCC_PROJ4)
    xx, yy = np.meshgrid(dataset["x"].values, dataset["y"].values)
    temp_c = dataset["air_temperature_2m"].isel(time=0).values - 273.15
    rh_pct = dataset["relative_humidity_2m"].isel(time=0).values * 100.0
    points = gpd.GeoDataFrame(
        {
            "x": xx.ravel(),
            "y": yy.ravel(),
            "temp_c": temp_c.ravel(),
            "rh_pct": rh_pct.ravel(),
        },
        geometry=gpd.points_from_xy(xx.ravel(), yy.ravel()),
        crs=lcc_crs,
    )
    return points.to_crs("EPSG:3301")


def _is_night(hour_utc: datetime) -> bool:
    local = hour_utc.astimezone(TALLINN_TZ)
    return local.hour >= 21 or local.hour < 6


def accumulate_meps_features(
    now: datetime, bbox_wgs84: tuple[float, float, float, float]
) -> tuple["gpd.GeoDataFrame", float, "datetime | None"]:
    hours = [
        now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=i)
        for i in range(_MEPS_WINDOW_HOURS)
    ]

    all_points = []
    fetched_count = 0
    newest_available: datetime | None = None
    for hour in hours:
        dataset = fetch_meps_hourly(hour, bbox_wgs84)
        if dataset is None:
            continue
        fetched_count += 1
        if newest_available is None or hour > newest_available:
            newest_available = hour
        points = meps_dataset_to_points(dataset)
        points["hour"] = hour
        points["is_night"] = _is_night(hour)
        all_points.append(points)

    coverage = fetched_count / _MEPS_WINDOW_HOURS

    if not all_points:
        empty = gpd.GeoDataFrame(
            {
                "temp_mean_3d": [],
                "temp_night_mean_3d": [],
                "rh_mean_3d": [],
                "rh_night_mean_3d": [],
            },
            geometry=[],
            crs="EPSG:3301",
        )
        return empty, coverage, newest_available

    combined = pd.concat(all_points, ignore_index=True)
    grouped = combined.groupby(["x", "y"])
    night = combined[combined["is_night"]].groupby(["x", "y"])

    result = grouped[["geometry"]].first()
    result["temp_mean_3d"] = grouped["temp_c"].mean()
    result["rh_mean_3d"] = grouped["rh_pct"].mean()
    result["temp_night_mean_3d"] = night["temp_c"].mean()
    result["rh_night_mean_3d"] = night["rh_pct"].mean()

    result = gpd.GeoDataFrame(result, geometry="geometry", crs=combined.crs)
    return result, coverage, newest_available
