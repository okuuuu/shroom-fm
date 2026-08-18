from pathlib import Path

import geopandas as gpd
import pandas as pd

from shroom_fm.habitat import TARGET_SPECIES
from shroom_fm.scout import join_ecotone_access, scout_candidates_for_species

TOP_N = 10
ERALDIS_PATH = Path("data/eraldis.geojson")
ECOTONES_PATH = Path("data/ecotones.geojson")
OUTPUT_PATH = Path("data/scout_candidates.geojson")

OUTPUT_COLUMNS = [
    "species",
    "tier",
    "rank",
    "scout_score",
    "ecotone_score",
    "access_modifier",
    "access_confidence",
    "access_reason",
    "nearest_car_road_m",
    "exclusion_reason",
    "transition_length_m",
    "dominant_species_a",
    "dominant_species_b",
    "id_a",
    "id_b",
    "geometry",
]


def main() -> None:
    eraldis_gdf = gpd.read_file(ERALDIS_PATH)
    ecotones_gdf = gpd.read_file(ECOTONES_PATH)
    joined = join_ecotone_access(ecotones_gdf, eraldis_gdf)

    rows = []
    for species in TARGET_SPECIES:
        ranked, remote = scout_candidates_for_species(joined, species, TOP_N)

        ranked = ranked.copy()
        ranked["species"] = species
        ranked["tier"] = "ranked"
        ranked["rank"] = range(1, len(ranked) + 1)
        ranked["exclusion_reason"] = None

        remote = remote.copy()
        remote["species"] = species
        remote["tier"] = "remote_high_value"
        remote["rank"] = range(1, len(remote) + 1)

        rows.append(ranked)
        rows.append(remote)

    combined = gpd.GeoDataFrame(pd.concat(rows, ignore_index=True), crs=ecotones_gdf.crs)
    combined = combined[OUTPUT_COLUMNS]

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    combined.to_file(OUTPUT_PATH, driver="GeoJSON")
    print(
        f"{len(combined)} scout candidates across {len(TARGET_SPECIES)} species "
        f"saved to {OUTPUT_PATH}"
    )


if __name__ == "__main__":
    main()
