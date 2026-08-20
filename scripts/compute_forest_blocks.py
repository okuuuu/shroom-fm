from pathlib import Path

import geopandas as gpd

from shroom_fm.forest_block import compute_forest_blocks

ERALDIS_PATH = Path("data/eraldis.geojson")
ADJACENCY_PATH = Path("data/adjacency.geojson")
FOREST_BLOCKS_PATH = Path("data/forest_blocks.geojson")


def main() -> None:
    eraldis_gdf = gpd.read_file(ERALDIS_PATH)
    adjacency_gdf = gpd.read_file(ADJACENCY_PATH)

    eraldis_gdf, blocks_gdf = compute_forest_blocks(eraldis_gdf, adjacency_gdf)

    eraldis_gdf.to_file(ERALDIS_PATH, driver="GeoJSON")
    blocks_gdf.to_file(FOREST_BLOCKS_PATH, driver="GeoJSON")

    print(
        f"{len(blocks_gdf)} forest blocks from {len(eraldis_gdf)} eraldis, "
        f"{int(blocks_gdf['oversized_block'].sum())} oversized, "
        f"saved to {FOREST_BLOCKS_PATH}"
    )


if __name__ == "__main__":
    main()
