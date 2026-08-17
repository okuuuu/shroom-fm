import geopandas as gpd
import pandas as pd

from shroom_fm.eraldis import ESTONIAN_GRID_CRS
from shroom_fm.roads import CAR_CLASS_HIGH_CONFIDENCE, CAR_CLASS_WALK_ONLY

CAR_ELIGIBLE_CLASSES = {"HIGH_CONFIDENCE", "NORMAL", "CONDITIONAL"}
ACCESS_DISTANCE_CAP_M = 1500.0


def access_score(nearest_car_road_m: float | None) -> float:
    if nearest_car_road_m is None:
        return 0.0
    return max(0.0, 1.0 - nearest_car_road_m / ACCESS_DISTANCE_CAP_M)


def access_reason(nearest_car_road_m: float | None, tyyp_tekst: str | None) -> str:
    if nearest_car_road_m is None:
        return f"no car-accessible road within {ACCESS_DISTANCE_CAP_M:.0f}m"
    return f"{nearest_car_road_m:.0f}m from {tyyp_tekst}-class road"


def _nearest_join(
    eraldis_projected: gpd.GeoDataFrame,
    roads_subset: gpd.GeoDataFrame,
    distance_col: str,
    extra_cols: tuple[str, ...] = (),
) -> pd.DataFrame:
    joined = gpd.sjoin_nearest(
        eraldis_projected[["geometry"]],
        roads_subset[["geometry", *extra_cols]],
        how="left",
        distance_col=distance_col,
    )
    return joined.groupby(level=0).first().reindex(eraldis_projected.index)


def _none_if_nan(value):
    return None if pd.isna(value) else value


def score_access(
    eraldis_gdf: gpd.GeoDataFrame, roads_gdf: gpd.GeoDataFrame
) -> gpd.GeoDataFrame:
    result = eraldis_gdf.copy()
    eraldis_projected = eraldis_gdf.to_crs(ESTONIAN_GRID_CRS)
    roads_projected = roads_gdf.to_crs(ESTONIAN_GRID_CRS)

    car_roads = roads_projected[roads_projected["car_class"].isin(CAR_ELIGIBLE_CLASSES)]
    hc_roads = roads_projected[roads_projected["car_class"] == CAR_CLASS_HIGH_CONFIDENCE]
    walk_roads = roads_projected[roads_projected["car_class"] == CAR_CLASS_WALK_ONLY]

    car_joined = _nearest_join(
        eraldis_projected,
        car_roads,
        "nearest_car_road_m",
        extra_cols=("car_class", "tyyp_tekst"),
    )
    hc_joined = _nearest_join(eraldis_projected, hc_roads, "nearest_high_confidence_road_m")
    walk_joined = _nearest_join(eraldis_projected, walk_roads, "nearest_walk_path_m")

    nearest_car_road_m = [_none_if_nan(v) for v in car_joined["nearest_car_road_m"]]
    access_confidence = [_none_if_nan(v) for v in car_joined["car_class"]]
    nearest_car_tyyp_tekst = [_none_if_nan(v) for v in car_joined["tyyp_tekst"]]

    result["nearest_car_road_m"] = nearest_car_road_m
    result["nearest_high_confidence_road_m"] = [
        _none_if_nan(v) for v in hc_joined["nearest_high_confidence_road_m"]
    ]
    result["nearest_walk_path_m"] = [
        _none_if_nan(v) for v in walk_joined["nearest_walk_path_m"]
    ]
    result["access_confidence"] = access_confidence
    result["access_score"] = [access_score(v) for v in nearest_car_road_m]
    result["access_reason"] = [
        access_reason(d, t) for d, t in zip(nearest_car_road_m, nearest_car_tyyp_tekst)
    ]

    return result
