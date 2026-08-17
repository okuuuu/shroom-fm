import geopandas as gpd

from shroom_fm.eraldis import ESTONIAN_GRID_CRS
from shroom_fm.roads import CAR_CLASS_HIGH_CONFIDENCE, CAR_CLASS_WALK_ONLY

CAR_ELIGIBLE_CLASSES = {"HIGH_CONFIDENCE", "NORMAL", "CONDITIONAL"}
ACCESS_DISTANCE_CAP_M = 1500.0


def nearest_segment(point_geom, roads_gdf: gpd.GeoDataFrame):
    if roads_gdf.empty:
        return None
    distances = roads_gdf.geometry.distance(point_geom)
    idx = distances.idxmin()
    return roads_gdf.loc[idx], distances.loc[idx]


def access_score(nearest_car_road_m: float | None) -> float:
    if nearest_car_road_m is None:
        return 0.0
    return max(0.0, 1.0 - nearest_car_road_m / ACCESS_DISTANCE_CAP_M)


def access_reason(nearest_car_road_m: float | None, tyyp_tekst: str | None) -> str:
    if nearest_car_road_m is None:
        return f"no car-accessible road within {ACCESS_DISTANCE_CAP_M:.0f}m"
    return f"{nearest_car_road_m:.0f}m from {tyyp_tekst}-class road"


def score_eraldis_access(eraldis_geom, roads_gdf: gpd.GeoDataFrame) -> dict:
    car_roads = roads_gdf[roads_gdf["car_class"].isin(CAR_ELIGIBLE_CLASSES)]
    hc_roads = roads_gdf[roads_gdf["car_class"] == CAR_CLASS_HIGH_CONFIDENCE]
    walk_roads = roads_gdf[roads_gdf["car_class"] == CAR_CLASS_WALK_ONLY]

    car_match = nearest_segment(eraldis_geom, car_roads)
    hc_match = nearest_segment(eraldis_geom, hc_roads)
    walk_match = nearest_segment(eraldis_geom, walk_roads)

    nearest_car_road_m = car_match[1] if car_match is not None else None
    access_confidence = car_match[0]["car_class"] if car_match is not None else None
    nearest_car_tyyp_tekst = car_match[0]["tyyp_tekst"] if car_match is not None else None

    return {
        "nearest_car_road_m": nearest_car_road_m,
        "nearest_high_confidence_road_m": hc_match[1] if hc_match is not None else None,
        "nearest_walk_path_m": walk_match[1] if walk_match is not None else None,
        "access_score": access_score(nearest_car_road_m),
        "access_confidence": access_confidence,
        "access_reason": access_reason(nearest_car_road_m, nearest_car_tyyp_tekst),
    }


def score_access(
    eraldis_gdf: gpd.GeoDataFrame, roads_gdf: gpd.GeoDataFrame
) -> gpd.GeoDataFrame:
    result = eraldis_gdf.copy()
    eraldis_projected = eraldis_gdf.to_crs(ESTONIAN_GRID_CRS)
    roads_projected = roads_gdf.to_crs(ESTONIAN_GRID_CRS)

    records = [
        score_eraldis_access(geom, roads_projected) for geom in eraldis_projected.geometry
    ]

    for key in (
        "nearest_car_road_m",
        "nearest_high_confidence_road_m",
        "nearest_walk_path_m",
        "access_score",
        "access_confidence",
        "access_reason",
    ):
        result[key] = [record[key] for record in records]

    return result
