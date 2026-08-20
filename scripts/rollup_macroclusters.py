from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import pyogrio

from shroom_fm.fruiting import join_ecotone_fruiting
from shroom_fm.habitat import TARGET_SPECIES
from shroom_fm.macrocluster import rollup_daily_state
from shroom_fm.scout import ACCESS_COLUMNS, join_ecotone_access

ERALDIS_PATH = Path("data/eraldis.geojson")
ECOTONES_PATH = Path("data/ecotones.geojson")
WEATHER_PATH = Path("data/weather_eraldis.geojson")
SCOUT_CANDIDATES_PATH = Path("data/scout_candidates.geojson")
MACROCLUSTERS_PATH = Path("data/macroclusters.geojson")
OUTPUT_PATH = Path("data/macrocluster_state.geojson")

# These three inputs are large full-featured GeoJSON files (eraldis.geojson 787MB,
# ecotones.geojson 3.15GB, weather_eraldis.geojson 1.1GB on disk) but none of the
# functions below (join_ecotone_access, join_ecotone_fruiting, rollup_daily_state) touch
# geometry or need any column beyond this small subset — loading them as full
# GeoDataFrames via gpd.read_file() pulls in every column and every geometry for no
# reason and OOMs on a 7.7GB machine. Read only the needed columns, with no geometry, via
# pyogrio directly instead.
ERALDIS_COLUMNS = ["id", "macrocluster_id", *ACCESS_COLUMNS]
ECOTONES_COLUMNS = ["id_a", "id_b", *[f"ecotone_score_{species}" for species in TARGET_SPECIES]]
WEATHER_COLUMNS = [
    "id",
    *[f"fruiting_score_{species}" for species in TARGET_SPECIES],
    "weather_data_quality",
    "weather_data_coverage",
    "as_of",
]


def _validate_columns(path: Path, expected_columns: list[str]) -> None:
    """pyogrio.read_dataframe(path, columns=[...]) silently DROPS a requested column
    name that doesn't exist in the file — no warning, no error. If one of these three
    files' schema ever drifts (a column renamed/removed upstream), that would let the
    script silently proceed with a smaller-than-expected frame, surfacing later as a
    confusing KeyError deep inside join_ecotone_access/join_ecotone_fruiting/
    rollup_daily_state, far from the actual cause. Fail immediately and legibly
    instead."""
    available = set(pyogrio.read_info(path)["fields"])
    missing = set(expected_columns) - available
    if missing:
        raise ValueError(
            f"{path} is missing expected column(s) {sorted(missing)} — "
            f"schema may have drifted; available fields: {sorted(available)}"
        )


def main() -> None:
    _validate_columns(ERALDIS_PATH, ERALDIS_COLUMNS)
    eraldis_gdf = pyogrio.read_dataframe(
        ERALDIS_PATH, columns=ERALDIS_COLUMNS, read_geometry=False
    )
    _validate_columns(ECOTONES_PATH, ECOTONES_COLUMNS)
    ecotones_gdf = pyogrio.read_dataframe(
        ECOTONES_PATH, columns=ECOTONES_COLUMNS, read_geometry=False
    )
    _validate_columns(WEATHER_PATH, WEATHER_COLUMNS)
    weather_gdf = pyogrio.read_dataframe(
        WEATHER_PATH, columns=WEATHER_COLUMNS, read_geometry=False
    )
    scout_candidates_gdf = gpd.read_file(SCOUT_CANDIDATES_PATH)
    macroclusters_gdf = gpd.read_file(MACROCLUSTERS_PATH)

    joined = join_ecotone_access(ecotones_gdf, eraldis_gdf)
    joined = join_ecotone_fruiting(joined, weather_gdf)

    now = datetime.now(timezone.utc)
    state = rollup_daily_state(scout_candidates_gdf, joined, eraldis_gdf, macroclusters_gdf, now)
    state.to_file(OUTPUT_PATH, driver="GeoJSON")

    total_cross = int(state["cross_macrocluster_ecotone_count"].sum())
    print(
        f"{len(state)} macrocluster states rolled up, "
        f"{total_cross} cross-macrocluster ecotones (diagnostic), "
        f"saved to {OUTPUT_PATH}"
    )


if __name__ == "__main__":
    main()
