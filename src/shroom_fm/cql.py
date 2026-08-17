import geopandas as gpd
from shapely.geometry import Point

ESTONIAN_GRID_CRS = "EPSG:3301"
WGS84_CRS = "EPSG:4326"


def estonian_grid_point(lat: float, lon: float) -> str:
    projected = (
        gpd.GeoSeries([Point(lon, lat)], crs=WGS84_CRS)
        .to_crs(ESTONIAN_GRID_CRS)
        .iloc[0]
    )
    return f"POINT({projected.y} {projected.x})"


def annulus_filter(
    geometry_attr: str, lat: float, lon: float, radius_km: float, inner_radius_km: float
) -> str:
    if inner_radius_km >= radius_km:
        raise ValueError(
            f"inner_radius_km ({inner_radius_km}) must be less than radius_km ({radius_km})"
        )
    point = estonian_grid_point(lat, lon)
    clause = f"DWITHIN({geometry_attr}, {point}, {radius_km * 1000}, meters)"
    if inner_radius_km > 0:
        clause += (
            f" AND BEYOND({geometry_attr}, {point}, {inner_radius_km * 1000}, meters)"
        )
    return clause
