import io
import math

import geopandas as gpd
import pandas as pd

from shroom_fm.concurrent_fetch import fetch_hit_count, fetch_pages_concurrently
from shroom_fm.cql import annulus_filter

CAR_CLASS_HIGH_CONFIDENCE = "HIGH_CONFIDENCE"
CAR_CLASS_NORMAL = "NORMAL"
CAR_CLASS_CONDITIONAL = "CONDITIONAL"
CAR_CLASS_WALK_ONLY = "WALK_ONLY"

_HIGH_CONFIDENCE_TYPES = {
    "Põhimaantee",
    "Tugimaantee",
    "Kõrvalmaantee",
    "Ramp või ühendustee",
    "Tänav",
}
_WALK_ONLY_TYPES = {"Rada", "Kergliiklustee"}
_DRIVABLE_SURFACES = {"Püsikate", "Kruuskate", "Kivikate"}


def classify_car_class(tyyp_tekst: str, teekate_tekst: str | None) -> str:
    if tyyp_tekst in _HIGH_CONFIDENCE_TYPES:
        return CAR_CLASS_HIGH_CONFIDENCE
    if tyyp_tekst in _WALK_ONLY_TYPES:
        return CAR_CLASS_WALK_ONLY
    if tyyp_tekst == "Muu tee":
        if teekate_tekst in _DRIVABLE_SURFACES:
            return CAR_CLASS_NORMAL
        if teekate_tekst == "Pinnas":
            return CAR_CLASS_CONDITIONAL
        raise ValueError(f"Unrecognized teekate_tekst for Muu tee: {teekate_tekst!r}")
    raise ValueError(f"Unrecognized tyyp_tekst: {tyyp_tekst!r}")


BARRIER_SNAP_M = 5.0
CLOSED_BARRIER_STATUS = "Püsivalt suletud"


def exclude_barrier_blocked_segments(
    roads_gdf: gpd.GeoDataFrame, barriers_gdf: gpd.GeoDataFrame
) -> gpd.GeoDataFrame:
    closed = barriers_gdf[barriers_gdf["toke_tekst"] == CLOSED_BARRIER_STATUS]
    if closed.empty or roads_gdf.empty:
        return roads_gdf
    blocked = pd.Series(False, index=roads_gdf.index)
    for barrier_geom in closed.geometry:
        blocked |= roads_gdf.geometry.distance(barrier_geom) <= BARRIER_SNAP_M
    return roads_gdf[~blocked]


ROAD_TYPENAME = "etak:e_501_tee_j"
BARRIER_TYPENAME = "etak:e_505_liikluskorralduslik_rajatis_j"
GEOMETRY_ATTR = "shape"

_PAGE_SIZE = 1000
_ETAK_OUTPUT_CRS = "EPSG:3301"


def fetch_layer_annulus(
    url: str,
    typename: str,
    lat: float,
    lon: float,
    radius_km: float,
    inner_radius_km: float = 0.0,
) -> gpd.GeoDataFrame:
    cql_filter = annulus_filter(GEOMETRY_ATTR, lat, lon, radius_km, inner_radius_km)
    base_params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": typename,
        "outputFormat": "application/json",
        "srsName": _ETAK_OUTPUT_CRS,
        "CQL_FILTER": cql_filter,
    }
    total = fetch_hit_count(url, base_params)
    num_pages = max(1, math.ceil(total / _PAGE_SIZE))
    params_list = [
        {**base_params, "startIndex": i * _PAGE_SIZE, "count": _PAGE_SIZE}
        for i in range(num_pages)
    ]
    contents = fetch_pages_concurrently(url, params_list, progress_label="road page")
    pages = []
    for i, content in enumerate(contents):
        pages.append(gpd.read_file(io.BytesIO(content)))
        contents[i] = None
    result = gpd.GeoDataFrame(pd.concat(pages, ignore_index=True), crs=pages[0].crs)
    if len(result) != total:
        raise RuntimeError(
            f"WFS returned {len(result)} features but reported {total} matches for {typename}"
        )
    return result
