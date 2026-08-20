from pathlib import Path

import geopandas as gpd

from shroom_fm.macrocluster import build_block_proximity_graph, compute_macroclusters

ERALDIS_PATH = Path("data/eraldis.geojson")
FOREST_BLOCKS_PATH = Path("data/forest_blocks.geojson")
MACROCLUSTERS_PATH = Path("data/macroclusters.geojson")


def main() -> None:
    eraldis_gdf = gpd.read_file(ERALDIS_PATH)
    blocks_gdf = gpd.read_file(FOREST_BLOCKS_PATH)

    graph = build_block_proximity_graph(blocks_gdf)
    blocks_gdf, clusters_gdf = compute_macroclusters(blocks_gdf, graph)

    eraldis_gdf = eraldis_gdf.merge(
        blocks_gdf[["forest_block_id", "macrocluster_id"]], on="forest_block_id", how="left"
    )

    eraldis_gdf.to_file(ERALDIS_PATH, driver="GeoJSON")
    blocks_gdf.to_file(FOREST_BLOCKS_PATH, driver="GeoJSON")
    clusters_gdf.to_file(MACROCLUSTERS_PATH, driver="GeoJSON")

    print(
        f"{len(clusters_gdf)} macroclusters from {len(blocks_gdf)} forest blocks, "
        f"{int(clusters_gdf['oversized_macrocluster'].sum())} oversized, "
        f"saved to {MACROCLUSTERS_PATH}"
    )


if __name__ == "__main__":
    main()
