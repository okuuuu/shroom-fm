import math

import geopandas as gpd

from shroom_fm.enrich import TARGET_SPECIES_CODES
from shroom_fm.eraldis import ESTONIAN_GRID_CRS


def composition_fractions(composition: list[dict]) -> dict[str, float]:
    categories = list(TARGET_SPECIES_CODES) + ["other"]
    valid_entries = [entry for entry in composition if not math.isnan(entry["osakaal"])]
    total = sum(entry["osakaal"] for entry in valid_entries)
    if total == 0:
        return {category: 0.0 for category in categories}

    target_sums = {
        name: sum(entry["osakaal"] for entry in valid_entries if entry["puuliik_kood"] == code)
        for name, code in TARGET_SPECIES_CODES.items()
    }
    other = total - sum(target_sums.values())
    raw = {**target_sums, "other": other}
    return {category: raw[category] / total for category in categories}


def composition_contrast(fractions_a: dict[str, float], fractions_b: dict[str, float]) -> float:
    return 0.5 * sum(abs(fractions_a[key] - fractions_b[key]) for key in fractions_a)


def dominant_species(fractions: dict[str, float]) -> tuple[str, float]:
    return max(fractions.items(), key=lambda item: item[1])


def composition_diversity(fractions: dict[str, float]) -> float:
    return -sum(p * math.log(p) for p in fractions.values() if p > 0)


BUFFER_DISTANCE_M = 40.0

ECOTONE_COLUMNS = [
    "id_a",
    "id_b",
    "adjacency_type",
    "transition_length_m",
    "composition_contrast",
    "dominant_species_a",
    "dominant_share_a",
    "diversity_a",
    "dominant_species_b",
    "dominant_share_b",
    "diversity_b",
    "geometry",
]


def score_ecotones(adjacency_gdf: gpd.GeoDataFrame, eraldis_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    original_crs = adjacency_gdf.crs
    projected_adjacency = adjacency_gdf.to_crs(ESTONIAN_GRID_CRS)
    composition_by_id = dict(zip(eraldis_gdf["id"], eraldis_gdf["composition"]))

    records = []
    for _, row in projected_adjacency.iterrows():
        composition_a = composition_by_id.get(row["id_a"], [])
        composition_b = composition_by_id.get(row["id_b"], [])
        fractions_a = composition_fractions(composition_a) if composition_a else None
        fractions_b = composition_fractions(composition_b) if composition_b else None

        dominant_a, share_a = dominant_species(fractions_a) if fractions_a else (None, None)
        dominant_b, share_b = dominant_species(fractions_b) if fractions_b else (None, None)
        diversity_a_value = composition_diversity(fractions_a) if fractions_a else None
        diversity_b_value = composition_diversity(fractions_b) if fractions_b else None
        contrast = composition_contrast(fractions_a, fractions_b) if fractions_a and fractions_b else float("nan")

        records.append(
            {
                "id_a": row["id_a"],
                "id_b": row["id_b"],
                "adjacency_type": row["adjacency_type"],
                "transition_length_m": row["transition_length_m"],
                "composition_contrast": contrast,
                "dominant_species_a": dominant_a,
                "dominant_share_a": share_a,
                "diversity_a": diversity_a_value,
                "dominant_species_b": dominant_b,
                "dominant_share_b": share_b,
                "diversity_b": diversity_b_value,
                "geometry": row["geometry"].buffer(BUFFER_DISTANCE_M),
            }
        )

    if not records:
        return gpd.GeoDataFrame(columns=ECOTONE_COLUMNS, geometry="geometry", crs=original_crs)

    ecotones = gpd.GeoDataFrame(records, columns=ECOTONE_COLUMNS, crs=ESTONIAN_GRID_CRS)
    return ecotones.to_crs(original_crs)
