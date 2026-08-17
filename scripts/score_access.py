from pathlib import Path

import geopandas as gpd

from shroom_fm.access import score_access

ERALDIS_PATH = Path("data/eraldis.geojson")
ROADS_PATH = Path("data/roads.geojson")


def main() -> None:
    eraldis_gdf = gpd.read_file(ERALDIS_PATH)
    roads_gdf = gpd.read_file(ROADS_PATH)

    scored = score_access(eraldis_gdf, roads_gdf)
    scored.to_file(ERALDIS_PATH, driver="GeoJSON")

    print(f"{len(scored)} stands scored for access, saved to {ERALDIS_PATH}")


if __name__ == "__main__":
    main()
