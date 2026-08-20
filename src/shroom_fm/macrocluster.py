import geopandas as gpd
import networkx as nx

from shroom_fm.eraldis import ESTONIAN_GRID_CRS

BLOCK_NEIGHBOR_PROXY_M = 8_000


def build_block_proximity_graph(forest_blocks_gdf: gpd.GeoDataFrame) -> nx.Graph:
    projected = forest_blocks_gdf.to_crs(ESTONIAN_GRID_CRS)

    graph = nx.Graph()
    graph.add_nodes_from(projected["forest_block_id"])

    buffered = projected.copy()
    buffered["geometry"] = buffered.geometry.buffer(BLOCK_NEIGHBOR_PROXY_M)
    joined = gpd.sjoin(buffered, projected, how="inner", predicate="intersects")

    id_to_geom = dict(zip(projected["forest_block_id"], projected.geometry))

    seen = set()
    for _, row in joined.iterrows():
        block_a = row["forest_block_id_left"]
        block_b = row["forest_block_id_right"]
        if block_a == block_b:
            continue
        pair = (min(block_a, block_b), max(block_a, block_b))
        if pair in seen:
            continue
        seen.add(pair)
        gap = id_to_geom[block_a].distance(id_to_geom[block_b])
        if gap <= BLOCK_NEIGHBOR_PROXY_M:
            graph.add_edge(block_a, block_b, distance_m=gap)

    return graph
