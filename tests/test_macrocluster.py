import geopandas as gpd
from shapely.geometry import box

from shroom_fm.macrocluster import BLOCK_NEIGHBOR_PROXY_M, build_block_proximity_graph


def test_build_block_proximity_graph_connects_nearby_blocks():
    # Two blocks 3km apart — well within BLOCK_NEIGHBOR_PROXY_M (8km)
    blocks_gdf = gpd.GeoDataFrame(
        {"forest_block_id": [0, 1]},
        geometry=[box(0, 0, 100, 100), box(3_100, 0, 3_200, 100)],
        crs="EPSG:3301",
    )

    graph = build_block_proximity_graph(blocks_gdf)

    assert graph.has_edge(0, 1)


def test_build_block_proximity_graph_does_not_connect_far_blocks():
    # Two blocks ~20km apart — beyond BLOCK_NEIGHBOR_PROXY_M (8km)
    blocks_gdf = gpd.GeoDataFrame(
        {"forest_block_id": [0, 1]},
        geometry=[box(0, 0, 100, 100), box(20_100, 0, 20_200, 100)],
        crs="EPSG:3301",
    )

    graph = build_block_proximity_graph(blocks_gdf)

    assert not graph.has_edge(0, 1)


def test_build_block_proximity_graph_connects_every_pair_within_threshold():
    # A row of 3 blocks each 3km from its immediate neighbor, so 0-1 and 1-2
    # both connect, but 0-2 (6km apart) also connects since it's still under
    # the 8km cap — this is NOT a chaining test, just confirming every pair
    # within threshold gets an edge, not just nearest-neighbor pairs.
    blocks_gdf = gpd.GeoDataFrame(
        {"forest_block_id": [0, 1, 2]},
        geometry=[
            box(0, 0, 100, 100),
            box(3_100, 0, 3_200, 100),
            box(6_200, 0, 6_300, 100),
        ],
        crs="EPSG:3301",
    )

    graph = build_block_proximity_graph(blocks_gdf)

    assert graph.has_edge(0, 1)
    assert graph.has_edge(1, 2)
    assert graph.has_edge(0, 2)


def test_build_block_proximity_graph_includes_isolated_block_as_a_node():
    blocks_gdf = gpd.GeoDataFrame(
        {"forest_block_id": [0, 1]},
        geometry=[box(0, 0, 100, 100), box(100_000, 0, 100_100, 100)],
        crs="EPSG:3301",
    )

    graph = build_block_proximity_graph(blocks_gdf)

    assert set(graph.nodes) == {0, 1}
    assert graph.number_of_edges() == 0


def test_block_neighbor_proxy_m_default_value():
    assert BLOCK_NEIGHBOR_PROXY_M == 8_000
