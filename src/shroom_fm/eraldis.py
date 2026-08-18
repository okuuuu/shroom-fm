import io
import math

import geopandas as gpd
import pandas as pd

from shroom_fm.concurrent_fetch import fetch_hit_count, fetch_pages_concurrently
from shroom_fm.cql import annulus_filter
from shroom_fm.wfs import METSAREGISTER_OWS_URL

ESTONIAN_GRID_CRS = "EPSG:3301"
WGS84_CRS = "EPSG:4326"
ERALDIS_TYPENAME = "metsaregister:eraldis"
GEOMETRY_ATTR = "shape"
PAGE_SIZE = 1000


def fetch_eraldis_annulus(
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
        "typeNames": ERALDIS_TYPENAME,
        "outputFormat": "application/json",
        "srsName": WGS84_CRS,
        "CQL_FILTER": cql_filter,
    }
    total = fetch_hit_count(METSAREGISTER_OWS_URL, base_params)
    num_pages = max(1, math.ceil(total / PAGE_SIZE))
    params_list = [
        {**base_params, "startIndex": i * PAGE_SIZE, "count": PAGE_SIZE}
        for i in range(num_pages)
    ]
    contents = fetch_pages_concurrently(
        METSAREGISTER_OWS_URL, params_list, progress_label="eraldis page"
    )
    pages = [gpd.read_file(io.BytesIO(content)) for content in contents]
    return gpd.GeoDataFrame(pd.concat(pages, ignore_index=True), crs=pages[0].crs)
