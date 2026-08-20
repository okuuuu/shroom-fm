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


from shroom_fm.macrocluster import (
    MACROCLUSTER_MAX_EXTENT_M,
    TARGET_BLOCK_COUNT,
    compute_macroclusters,
)


def _square_block(block_id, x0, y0, side, eraldis_count=1):
    from shapely.geometry import box

    return {
        "forest_block_id": block_id,
        "eraldis_count": eraldis_count,
        "geometry_extent_m": side * 1.4142135623730951,
        "oversized_block": False,
        "geometry": box(x0, y0, x0 + side, y0 + side),
    }


def test_compute_macroclusters_keeps_compact_super_component_as_one_cluster():
    # 3 blocks, all mutually within a few km, well under MAX_EXTENT_M overall.
    import geopandas as gpd
    import networkx as nx
    from shapely.geometry import box

    blocks_gdf = gpd.GeoDataFrame(
        [
            _square_block(0, 0, 0, 100),
            _square_block(1, 3_000, 0, 100),
            _square_block(2, 6_000, 0, 100),
        ],
        crs="EPSG:3301",
    )
    graph = nx.Graph()
    graph.add_nodes_from([0, 1, 2])
    graph.add_edge(0, 1)
    graph.add_edge(1, 2)
    graph.add_edge(0, 2)

    blocks_result, clusters_gdf = compute_macroclusters(blocks_gdf, graph)

    assert len(clusters_gdf) == 1
    assert clusters_gdf.iloc[0]["forest_block_count"] == 3
    assert blocks_result["macrocluster_id"].nunique() == 1
    assert clusters_gdf.iloc[0]["oversized_macrocluster"] == False


def test_compute_macroclusters_splits_a_chain_that_transitively_spans_too_far():
    # 5 blocks each 7km from the next (0-1-2-3-4), every ADJACENT pair is within
    # BLOCK_NEIGHBOR_PROXY_M (8km), but block 0 and block 4 are 28km apart —
    # well beyond MACROCLUSTER_MAX_EXTENT_M (35km) is NOT violated by the whole
    # chain's raw span here on purpose: use a bigger step so naive connected-
    # components chaining would produce one 40km+ cluster if not split.
    import geopandas as gpd
    import networkx as nx

    step = 9_000  # each block 9km from the next along a line
    n = 5
    blocks_gdf = gpd.GeoDataFrame(
        [_square_block(i, i * step, 0, 100) for i in range(n)],
        crs="EPSG:3301",
    )
    graph = nx.Graph()
    graph.add_nodes_from(range(n))
    for i in range(n - 1):
        graph.add_edge(i, i + 1)  # only adjacent pairs connected — a real chain

    blocks_result, clusters_gdf = compute_macroclusters(blocks_gdf, graph)

    # Total chain span is (n-1)*step = 36km > MAX_EXTENT_M (35km), so this must
    # NOT collapse into one cluster the way naive connected-components would.
    assert len(clusters_gdf) > 1
    for _, cluster in clusters_gdf.iterrows():
        assert cluster["geometry_extent_m"] <= MACROCLUSTER_MAX_EXTENT_M


def test_compute_macroclusters_does_not_force_split_for_small_block_count():
    # 3 large-but-compact blocks — block count (3) is below TARGET_BLOCK_COUNT's
    # minimum (5), but this must NOT be split just to hit the target range,
    # since within_target_block_count is diagnostic-only.
    import geopandas as gpd
    import networkx as nx

    blocks_gdf = gpd.GeoDataFrame(
        [
            _square_block(0, 0, 0, 5_000),
            _square_block(1, 5_500, 0, 5_000),
            _square_block(2, 11_000, 0, 5_000),
        ],
        crs="EPSG:3301",
    )
    graph = nx.Graph()
    graph.add_nodes_from([0, 1, 2])
    graph.add_edge(0, 1)
    graph.add_edge(1, 2)
    graph.add_edge(0, 2)

    _, clusters_gdf = compute_macroclusters(blocks_gdf, graph)

    assert len(clusters_gdf) == 1
    assert clusters_gdf.iloc[0]["forest_block_count"] == 3
    assert clusters_gdf.iloc[0]["within_target_block_count"] == False


def test_compute_macroclusters_flags_a_single_block_that_is_already_oversized():
    import geopandas as gpd
    import networkx as nx

    side = MACROCLUSTER_MAX_EXTENT_M + 5_000
    blocks_gdf = gpd.GeoDataFrame([_square_block(0, 0, 0, side)], crs="EPSG:3301")
    graph = nx.Graph()
    graph.add_node(0)

    _, clusters_gdf = compute_macroclusters(blocks_gdf, graph)

    assert len(clusters_gdf) == 1
    assert clusters_gdf.iloc[0]["oversized_macrocluster"] == True


def test_macrocluster_constants():
    assert MACROCLUSTER_MAX_EXTENT_M == 35_000
    assert TARGET_BLOCK_COUNT == (5, 15)
