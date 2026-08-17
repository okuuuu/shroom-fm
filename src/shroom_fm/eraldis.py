import io
import math

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

from shroom_fm.retry import get_with_retry
from shroom_fm.wfs import METSAREGISTER_OWS_URL

KM_PER_DEGREE_LAT = 111.32
BBOX_PADDING_FACTOR = 1.1
ESTONIAN_GRID_CRS = "EPSG:3301"
WGS84_CRS = "EPSG:4326"
ERALDIS_TYPENAME = "metsaregister:eraldis"
GEOMETRY_ATTR = "shape"
PAGE_SIZE = 1000


def compute_bbox(
    lat: float, lon: float, radius_km: float
) -> tuple[float, float, float, float]:
    padded_radius_km = radius_km * BBOX_PADDING_FACTOR
    delta_lat = padded_radius_km / KM_PER_DEGREE_LAT
    delta_lon = padded_radius_km / (KM_PER_DEGREE_LAT * math.cos(math.radians(lat)))
    return (lon - delta_lon, lat - delta_lat, lon + delta_lon, lat + delta_lat)


def filter_within_radius(
    gdf: gpd.GeoDataFrame,
    lat: float,
    lon: float,
    radius_km: float,
    inner_radius_km: float = 0.0,
) -> gpd.GeoDataFrame:
    if inner_radius_km >= radius_km:
        raise ValueError(
            f"inner_radius_km ({inner_radius_km}) must be less than radius_km ({radius_km})"
        )
    projected = gdf.to_crs(ESTONIAN_GRID_CRS)
    home_point = (
        gpd.GeoSeries([Point(lon, lat)], crs=WGS84_CRS)
        .to_crs(ESTONIAN_GRID_CRS)
        .iloc[0]
    )
    distances_km = projected.geometry.distance(home_point) / 1000.0
    return gdf[(distances_km >= inner_radius_km) & (distances_km <= radius_km)]


def _cql_point(lat: float, lon: float) -> str:
    projected = (
        gpd.GeoSeries([Point(lon, lat)], crs=WGS84_CRS)
        .to_crs(ESTONIAN_GRID_CRS)
        .iloc[0]
    )
    return f"POINT({projected.y} {projected.x})"


def _build_cql_filter(
    lat: float, lon: float, radius_km: float, inner_radius_km: float
) -> str:
    point = _cql_point(lat, lon)
    clause = f"DWITHIN({GEOMETRY_ATTR}, {point}, {radius_km * 1000}, meters)"
    if inner_radius_km > 0:
        clause += (
            f" AND BEYOND({GEOMETRY_ATTR}, {point}, {inner_radius_km * 1000}, meters)"
        )
    return clause


def fetch_eraldis_annulus(
    lat: float,
    lon: float,
    radius_km: float,
    inner_radius_km: float = 0.0,
) -> gpd.GeoDataFrame:
    if inner_radius_km >= radius_km:
        raise ValueError(
            f"inner_radius_km ({inner_radius_km}) must be less than radius_km ({radius_km})"
        )
    cql_filter = _build_cql_filter(lat, lon, radius_km, inner_radius_km)
    pages = []
    start_index = 0
    while True:
        response = get_with_retry(
            METSAREGISTER_OWS_URL,
            params={
                "service": "WFS",
                "version": "2.0.0",
                "request": "GetFeature",
                "typeNames": ERALDIS_TYPENAME,
                "outputFormat": "application/json",
                "srsName": WGS84_CRS,
                "CQL_FILTER": cql_filter,
                "startIndex": start_index,
                "count": PAGE_SIZE,
            },
            timeout=30,
        )
        page = gpd.read_file(io.BytesIO(response.content))
        pages.append(page)
        if len(page) < PAGE_SIZE:
            break
        start_index += PAGE_SIZE
    return gpd.GeoDataFrame(pd.concat(pages, ignore_index=True), crs=pages[0].crs)
