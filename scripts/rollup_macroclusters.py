from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd

from shroom_fm.fruiting import join_ecotone_fruiting
from shroom_fm.macrocluster import rollup_daily_state
from shroom_fm.scout import join_ecotone_access

ERALDIS_PATH = Path("data/eraldis.geojson")
ECOTONES_PATH = Path("data/ecotones.geojson")
WEATHER_PATH = Path("data/weather_eraldis.geojson")
SCOUT_CANDIDATES_PATH = Path("data/scout_candidates.geojson")
MACROCLUSTERS_PATH = Path("data/macroclusters.geojson")
OUTPUT_PATH = Path("data/macrocluster_state.geojson")


def main() -> None:
    eraldis_gdf = gpd.read_file(ERALDIS_PATH)
    ecotones_gdf = gpd.read_file(ECOTONES_PATH)
    weather_gdf = gpd.read_file(WEATHER_PATH)
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
