from pathlib import Path

import geopandas as gpd

from shroom_fm.ecotone import score_ecotones

ADJACENCY_PATH = Path("data/adjacency.geojson")
ERALDIS_PATH = Path("data/eraldis.geojson")
OUTPUT_PATH = Path("data/ecotones.geojson")


def main() -> None:
    adjacency_gdf = gpd.read_file(ADJACENCY_PATH)
    eraldis_gdf = gpd.read_file(ERALDIS_PATH)

    ecotones = score_ecotones(adjacency_gdf, eraldis_gdf)
    ecotones.to_file(OUTPUT_PATH, driver="GeoJSON")

    print(f"{len(ecotones)} ecotone pairs scored, saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
