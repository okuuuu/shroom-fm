from pathlib import Path

import geopandas as gpd
import pandas as pd

from shroom_fm.habitat import TARGET_SPECIES
from shroom_fm.scout import (
    MIN_SCOUT_WEATHER_COVERAGE,
    join_ecotone_access,
    scout_candidates_for_species,
    weather_coverage_ratio,
)
from shroom_fm.fruiting import join_ecotone_fruiting

TOP_N = 10
ERALDIS_PATH = Path("data/eraldis.geojson")
ECOTONES_PATH = Path("data/ecotones.geojson")
WEATHER_PATH = Path("data/weather_eraldis.geojson")
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
    "fruiting_score",
    "weather_data_quality",
    "weather_data_coverage",
    "weather_as_of",
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
    weather_gdf = gpd.read_file(WEATHER_PATH)

    joined = join_ecotone_access(ecotones_gdf, eraldis_gdf)
    joined = join_ecotone_fruiting(joined, weather_gdf)

    rows = []
    for species in TARGET_SPECIES:
        ratio = weather_coverage_ratio(joined, species)
        if ratio < MIN_SCOUT_WEATHER_COVERAGE:
            print(
                f"Scout ranking unavailable for {species}: weather coverage "
                f"{ratio:.1%}, required >= {MIN_SCOUT_WEATHER_COVERAGE:.0%}"
            )
            continue

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

    if not rows:
        print(
            "Scout ranking unavailable for all species: weather coverage too low. "
            f"No {OUTPUT_PATH} written — refusing to publish an untrustworthy ranking."
        )
        raise SystemExit(1)

    combined = gpd.GeoDataFrame(pd.concat(rows, ignore_index=True), crs=ecotones_gdf.crs)
    combined = combined[OUTPUT_COLUMNS]

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    combined.to_file(OUTPUT_PATH, driver="GeoJSON")
    print(
        f"{len(combined)} scout candidates across {len(rows) // 2} species "
        f"saved to {OUTPUT_PATH}"
    )


if __name__ == "__main__":
    main()
