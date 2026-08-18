import json

import geopandas as gpd
import pandas as pd
from owslib.wfs import WebFeatureService

from shroom_fm.concurrent_fetch import fetch_pages_concurrently
from shroom_fm.retry import call_with_retry
from shroom_fm.wfs import METSAREGISTER_OWS_URL

COMPOSITION_DETAIL_COLUMNS = [
    "rinne_kood",
    "puuliik_kood",
    "osakaal",
    "vanus",
    "korgus",
    "enamus",
    "sunniaasta",
    "paritolu",
    "diameeter",
    "rinnaspindala",
    "tagavara",
    "arv",
]

TARGET_SPECIES_CODES = {
    "pine": "MA",
    "spruce": "KU",
    "birch": "KS",
    "aspen": "HB",
}

ERALDIS_ELEMENT_TYPENAME = "metsaregister:eraldis_element"
ID_BATCH_SIZE = 500
PUULIIK_TYPENAME = "metsaregister:kl_puuliik"
KASVUKOHT_TYPENAME = "metsaregister:kl_kasvukoht"


def summarize_composition(element_df) -> dict[int, list[dict]]:
    if element_df.empty:
        return {}
    composition_by_id: dict[int, list[dict]] = {}
    for eraldis_id, group in element_df.groupby("eraldis_id"):
        composition_by_id[eraldis_id] = group[COMPOSITION_DETAIL_COLUMNS].to_dict("records")
    return composition_by_id


def compute_species_shares(composition: list[dict]) -> dict[str, float]:
    shares = {f"{name}_share": 0.0 for name in TARGET_SPECIES_CODES}
    for entry in composition:
        for name, code in TARGET_SPECIES_CODES.items():
            if entry["puuliik_kood"] == code:
                shares[f"{name}_share"] += entry["osakaal"]
    return shares


def fetch_classifier(wfs: WebFeatureService, typename: str) -> dict[str, str]:
    response = call_with_retry(
        wfs.getfeature, typename=typename, outputFormat="application/json"
    )
    data = json.loads(response.read())
    return {
        feature["properties"]["kood"]: feature["properties"]["kirjeldus"]
        for feature in data["features"]
    }


def fetch_eraldis_element(eraldis_ids: list[int]) -> pd.DataFrame:
    if not eraldis_ids:
        return pd.DataFrame([])
    batches = [
        eraldis_ids[i : i + ID_BATCH_SIZE] for i in range(0, len(eraldis_ids), ID_BATCH_SIZE)
    ]
    params_list = [
        {
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeName": ERALDIS_ELEMENT_TYPENAME,
            "outputFormat": "application/json",
            "CQL_FILTER": "eraldis_id IN ({})".format(
                ",".join(str(eid) for eid in batch)
            ),
        }
        for batch in batches
    ]
    contents = fetch_pages_concurrently(
        METSAREGISTER_OWS_URL, params_list, progress_label="composition batch"
    )
    rows = []
    for content in contents:
        data = json.loads(content)
        rows.extend(feature["properties"] for feature in data["features"])
    return pd.DataFrame(rows)


def enrich_eraldis(gdf: gpd.GeoDataFrame, wfs: WebFeatureService) -> gpd.GeoDataFrame:
    crs = gdf.crs
    eraldis_ids = gdf["id"].tolist()

    element_df = fetch_eraldis_element(eraldis_ids)
    composition_by_id = summarize_composition(element_df)

    result = gdf.copy()
    result["composition"] = result["id"].map(composition_by_id)
    result["composition"] = result["composition"].apply(
        lambda value: value if isinstance(value, list) else []
    )

    shares = result["composition"].apply(compute_species_shares)
    shares_df = pd.DataFrame(shares.tolist(), index=result.index)
    for column in shares_df.columns:
        result[column] = shares_df[column]

    puuliik_labels = fetch_classifier(wfs, PUULIIK_TYPENAME)
    kasvukoht_labels = fetch_classifier(wfs, KASVUKOHT_TYPENAME)
    result["peapuuliik_kirjeldus"] = result["peapuuliik_kood"].map(puuliik_labels)
    result["kasvukoht_kirjeldus"] = result["kasvukoht_kood"].map(kasvukoht_labels)

    return gpd.GeoDataFrame(result, geometry="geometry", crs=crs)
