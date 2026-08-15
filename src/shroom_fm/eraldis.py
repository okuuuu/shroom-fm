import math

import geopandas as gpd
from shapely.geometry import Point

KM_PER_DEGREE_LAT = 111.32
BBOX_PADDING_FACTOR = 1.1
ESTONIAN_GRID_CRS = "EPSG:3301"
WGS84_CRS = "EPSG:4326"


def compute_bbox(
    lat: float, lon: float, radius_km: float
) -> tuple[float, float, float, float]:
    padded_radius_km = radius_km * BBOX_PADDING_FACTOR
    delta_lat = padded_radius_km / KM_PER_DEGREE_LAT
    delta_lon = padded_radius_km / (KM_PER_DEGREE_LAT * math.cos(math.radians(lat)))
    return (lon - delta_lon, lat - delta_lat, lon + delta_lon, lat + delta_lat)


def filter_within_radius(
    gdf: gpd.GeoDataFrame, lat: float, lon: float, radius_km: float
) -> gpd.GeoDataFrame:
    projected = gdf.to_crs(ESTONIAN_GRID_CRS)
    home_point = (
        gpd.GeoSeries([Point(lon, lat)], crs=WGS84_CRS)
        .to_crs(ESTONIAN_GRID_CRS)
        .iloc[0]
    )
    distances_km = projected.geometry.distance(home_point) / 1000.0
    return gdf[distances_km <= radius_km]
