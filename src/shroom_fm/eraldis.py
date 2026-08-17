import io
import math

import geopandas as gpd
import pandas as pd
from owslib.wfs import WebFeatureService
from shapely.geometry import Point

KM_PER_DEGREE_LAT = 111.32
BBOX_PADDING_FACTOR = 1.1
ESTONIAN_GRID_CRS = "EPSG:3301"
WGS84_CRS = "EPSG:4326"
ERALDIS_TYPENAME = "metsaregister:eraldis"
PAGE_SIZE = 1000
WGS84_URN = f"urn:ogc:def:crs:{WGS84_CRS.replace(':', '::')}"


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


def fetch_eraldis_bbox(
    wfs: WebFeatureService, bbox: tuple[float, float, float, float]
) -> gpd.GeoDataFrame:
    pages = []
    start_index = 0
    while True:
        response = wfs.getfeature(
            typename=ERALDIS_TYPENAME,
            bbox=(*bbox, WGS84_URN),
            srsname=WGS84_CRS,
            outputFormat="application/json",
            startindex=start_index,
            maxfeatures=PAGE_SIZE,
        )
        page = gpd.read_file(io.BytesIO(response.read()))
        pages.append(page)
        if len(page) < PAGE_SIZE:
            break
        start_index += PAGE_SIZE
    return gpd.GeoDataFrame(pd.concat(pages, ignore_index=True), crs=pages[0].crs)
