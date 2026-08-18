import io

import geopandas as gpd
import pandas as pd

from shroom_fm.cql import annulus_filter
from shroom_fm.retry import get_with_retry
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
