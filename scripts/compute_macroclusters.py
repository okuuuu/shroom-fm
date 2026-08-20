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

    # Idempotent, additive column assignment (not .merge()): eraldis.geojson may
    # already carry a macrocluster_id column from a previous run of this script (this
    # is a normal workflow via main.py --skip), and .merge() on a frame that already
    # has the column being merged in produces macrocluster_id_x/macrocluster_id_y
    # instead of overwriting it. Direct dict-map assignment is idempotent (safe to run
    # any number of times) and avoids the full extra copy of the large GeoDataFrame
    # that .merge() creates. Matches the idiom forest_block.py's compute_forest_blocks
    # already uses for forest_block_id.
    block_to_cluster = dict(zip(blocks_gdf["forest_block_id"], blocks_gdf["macrocluster_id"]))
    eraldis_gdf["macrocluster_id"] = eraldis_gdf["forest_block_id"].map(block_to_cluster)

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
