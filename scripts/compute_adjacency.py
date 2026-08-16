from pathlib import Path

import geopandas as gpd

from shroom_fm.adjacency import compute_adjacency

INPUT_PATH = Path("data/eraldis.geojson")
OUTPUT_PATH = Path("data/adjacency.geojson")


def main() -> None:
    gdf = gpd.read_file(INPUT_PATH)
    adjacency = compute_adjacency(gdf)
    adjacency.to_file(OUTPUT_PATH, driver="GeoJSON")

    print(f"{len(adjacency)} adjacent pairs found, saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
