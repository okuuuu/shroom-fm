from pathlib import Path

import geopandas as gpd
import pandas as pd

from shroom_fm.eraldis import ESTONIAN_GRID_CRS
from shroom_fm.fruiting import join_ecotone_fruiting
from shroom_fm.habitat import TARGET_SPECIES
from shroom_fm.macrocluster import attach_macrocluster_id
from shroom_fm.scout import (
    MAX_SUPPRESSED_EXAMPLES_PER_TARGET,
    MIN_SCOUT_SEPARATION_M,
    MIN_SCOUT_WEATHER_COVERAGE,
    join_ecotone_access,
    remote_high_value_for_species,
    scout_candidates_for_species_macrocluster,
    weather_coverage_ratio,
)

SCOUT_CANDIDATES_PER_SPECIES_PER_MACROCLUSTER = 10
REMOTE_HIGH_VALUE_TOP_N = 10
ERALDIS_PATH = Path("data/eraldis.geojson")
ECOTONES_PATH = Path("data/ecotones.geojson")
WEATHER_PATH = Path("data/weather_eraldis.geojson")
OUTPUT_PATH = Path("data/scout_candidates.geojson")

OUTPUT_COLUMNS = [
    "species",
    "tier",
    "macrocluster_id",
    "rank_macrocluster",
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
    "suppressed_by_id",
    "suppression_distance_m",
    "pre_suppression_rank",
    "nearby_suppressed_count",
    "nearby_best_suppressed_score",
    "transition_length_m",
    "dominant_species_a",
    "dominant_species_b",
    "id_a",
    "id_b",
    "geometry",
]


def build_scout_candidate_rows(
    joined_gdf: gpd.GeoDataFrame, target_species: list[str]
) -> gpd.GeoDataFrame | None:
    """joined_gdf must already have macrocluster_id attached (see
    macrocluster.attach_macrocluster_id) and be in a metric CRS. Builds every
    candidate row (ranked, suppressed_by_nearby, remote_high_value) across all species
    and macroclusters. Returns None if nothing at all is publishable (every species
    failed both the ranked and remote_high_value gates everywhere) -- the caller must
    refuse to write output in that case, never publish an empty/untrustworthy file."""
    rows = []
    for species in target_species:
        ratio = weather_coverage_ratio(joined_gdf, species)
        species_has_remote = ratio >= MIN_SCOUT_WEATHER_COVERAGE
        if species_has_remote:
            remote = remote_high_value_for_species(
                joined_gdf, species, REMOTE_HIGH_VALUE_TOP_N
            ).copy()
            remote["species"] = species
            remote["tier"] = "remote_high_value"
            remote["rank"] = range(1, len(remote) + 1)
            if len(remote) > 0:
                # Guard against appending an empty-but-real DataFrame: rows.append(x)
                # makes `rows` non-empty (the LIST gained an element) even if `x` itself
                # has 0 rows, which would defeat the `if not rows: return None`
                # "nothing publishable" check below.
                rows.append(remote)
        else:
            print(
                f"remote_high_value unavailable for {species}: weather coverage "
                f"{ratio:.1%}, required >= {MIN_SCOUT_WEATHER_COVERAGE:.0%}"
            )

        any_macrocluster_ranked = False
        for macrocluster_id in sorted(joined_gdf["macrocluster_id"].unique()):
            bucket = joined_gdf[joined_gdf["macrocluster_id"] == macrocluster_id]
            bucket_ratio = weather_coverage_ratio(bucket, species)
            if bucket_ratio < MIN_SCOUT_WEATHER_COVERAGE:
                continue

            ranked, suppressed = scout_candidates_for_species_macrocluster(
                bucket,
                species,
                SCOUT_CANDIDATES_PER_SPECIES_PER_MACROCLUSTER,
                MIN_SCOUT_SEPARATION_M,
                MAX_SUPPRESSED_EXAMPLES_PER_TARGET,
            )
            if len(ranked) == 0:
                continue
            any_macrocluster_ranked = True

            ranked = ranked.copy()
            ranked["species"] = species
            ranked["tier"] = "ranked"
            ranked["rank_macrocluster"] = range(1, len(ranked) + 1)
            ranked["exclusion_reason"] = None
            rows.append(ranked)

            if len(suppressed) > 0:
                suppressed = suppressed.copy()
                suppressed["species"] = species
                suppressed["tier"] = "suppressed_by_nearby"
                rows.append(suppressed)

        if not species_has_remote and not any_macrocluster_ranked:
            print(f"Scout ranking unavailable for {species}: no eligible buckets")

    if not rows:
        return None

    combined = gpd.GeoDataFrame(pd.concat(rows, ignore_index=True), crs=joined_gdf.crs)
    for col in OUTPUT_COLUMNS:
        if col not in combined.columns:
            combined[col] = None
    return combined[OUTPUT_COLUMNS]


def main() -> None:
    eraldis_gdf = gpd.read_file(ERALDIS_PATH)
    ecotones_gdf = gpd.read_file(ECOTONES_PATH).to_crs(ESTONIAN_GRID_CRS)
    weather_gdf = gpd.read_file(WEATHER_PATH)

    joined = join_ecotone_access(ecotones_gdf, eraldis_gdf)
    joined = join_ecotone_fruiting(joined, weather_gdf)
    joined = attach_macrocluster_id(joined, eraldis_gdf)

    combined = build_scout_candidate_rows(joined, TARGET_SPECIES)

    if combined is None:
        print(
            "Scout ranking unavailable for all species: weather coverage too low. "
            f"No {OUTPUT_PATH} written — refusing to publish an untrustworthy ranking."
        )
        raise SystemExit(1)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    combined.to_file(OUTPUT_PATH, driver="GeoJSON")
    print(f"{len(combined)} scout candidate rows saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
