import geopandas as gpd
import pandas as pd
import pytest
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


def test_partition_component_connectivity_splits_at_depth_exhaustion():
    # Regression test for a real gap found via review: the depth-exhaustion
    # early return in _partition_component used to skip the post-hoc
    # nx.connected_components check every other return path goes through,
    # so a group that centroid-clustering could never shrink under the cap
    # (and so recursed all the way to _MAX_REPARTITION_DEPTH) could be
    # returned as one merged group even if it wasn't actually reachable via
    # the graph.
    #
    # Three blocks share the SAME centroid (0, 0) -- so scipy's
    # complete-linkage clustering, which operates purely on centroid
    # distance, can never split them apart no matter how far the threshold
    # shrinks across recursive calls (distance 0 is always <= any positive
    # threshold). Their real geometries are far apart (100km+ separations),
    # so the dissolved group's geometry_extent_m always exceeds
    # MACROCLUSTER_MAX_EXTENT_M, forcing every level of recursion down to
    # _MAX_REPARTITION_DEPTH. The graph has no edges among them at all, so
    # they are NOT actually connected/reachable from one another -- the fix
    # must split them into three singleton groups rather than merging them.
    import networkx as nx
    from shapely.geometry import Point

    from shroom_fm.macrocluster import MACROCLUSTER_MAX_EXTENT_M, _partition_component

    id_to_centroid = {0: Point(0, 0), 1: Point(0, 0), 2: Point(0, 0)}
    id_to_geom = {
        0: box(0, 0, 10, 10),
        1: box(100_000, 0, 100_010, 10),
        2: box(200_000, 0, 200_010, 10),
    }
    graph = nx.Graph()
    graph.add_nodes_from([0, 1, 2])  # no edges -- fully disconnected

    result = _partition_component(
        block_ids=[0, 1, 2],
        id_to_centroid=id_to_centroid,
        id_to_geom=id_to_geom,
        graph=graph,
        max_extent_m=MACROCLUSTER_MAX_EXTENT_M,
    )

    assert sorted(result) == [[0], [1], [2]]


def test_partition_component_depth_exhaustion_keeps_merged_group_if_actually_connected():
    # Contrast case: same centroid-collision/oversized-geometry setup that
    # forces recursion to _MAX_REPARTITION_DEPTH, but this time the three
    # blocks ARE connected via the graph -- the depth-exhaustion path must
    # still give up and return them as one group (existing "flag oversized
    # and move on" behavior), not spuriously split a genuinely connected
    # group just because it hit the recursion limit.
    import networkx as nx
    from shapely.geometry import Point

    from shroom_fm.macrocluster import MACROCLUSTER_MAX_EXTENT_M, _partition_component

    id_to_centroid = {0: Point(0, 0), 1: Point(0, 0), 2: Point(0, 0)}
    id_to_geom = {
        0: box(0, 0, 10, 10),
        1: box(100_000, 0, 100_010, 10),
        2: box(200_000, 0, 200_010, 10),
    }
    graph = nx.Graph()
    graph.add_edge(0, 1)
    graph.add_edge(1, 2)

    result = _partition_component(
        block_ids=[0, 1, 2],
        id_to_centroid=id_to_centroid,
        id_to_geom=id_to_geom,
        graph=graph,
        max_extent_m=MACROCLUSTER_MAX_EXTENT_M,
    )

    assert result == [[0, 1, 2]]


from datetime import datetime, timezone

from shroom_fm.habitat import TARGET_SPECIES
from shroom_fm.macrocluster import ecotone_macrocluster_id, rollup_daily_state


def _utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def _joined_columns(n: int, chanterelle_ecotone, chanterelle_eligible, chanterelle_fruiting):
    """rollup_daily_state loops over every TARGET_SPECIES internally, so a real
    joined_gdf always has ecotone_score_*/fruiting_modifier_* for all 5 species
    (join_ecotone_fruiting guarantees this — it nulls a species' column rather than
    omitting it when weather data is missing). Test fixtures must match that shape:
    fill the 4 non-chanterelle species with a neutral, fully-covered value (ecotone
    score 1.0, fruiting modifier 0.5) so weather_coverage_ratio doesn't KeyError,
    while only chanterelle's values are what each test actually asserts against."""
    columns = {
        "scout_eligible": pd.array(chanterelle_eligible, dtype=object),
    }
    for species in TARGET_SPECIES:
        if species == "chanterelle":
            columns["ecotone_score_chanterelle"] = chanterelle_ecotone
            columns["fruiting_modifier_chanterelle"] = chanterelle_fruiting
        else:
            columns[f"ecotone_score_{species}"] = [1.0] * n
            columns[f"fruiting_modifier_{species}"] = [0.5] * n
    return columns


def test_ecotone_macrocluster_id_same_cluster():
    mapping = {1: 5, 2: 5}
    cluster_id, is_cross = ecotone_macrocluster_id(1, 2, mapping)
    assert cluster_id == 5
    assert is_cross is False


def test_ecotone_macrocluster_id_cross_cluster_assigns_by_id_a():
    mapping = {1: 5, 2: 6}
    cluster_id, is_cross = ecotone_macrocluster_id(1, 2, mapping)
    assert cluster_id == 5
    assert is_cross is True


def test_rollup_daily_state_computes_ranked_stats_for_a_populated_cluster():
    import geopandas as gpd
    from shapely.geometry import Point

    scout_candidates_gdf = gpd.GeoDataFrame(
        {
            "species": ["chanterelle", "chanterelle", "chanterelle"],
            "tier": ["ranked", "ranked", "ranked"],
            "scout_score": [0.9, 0.7, 0.5],
            "id_a": [1, 3, 5],
            "id_b": [2, 4, 6],
        },
        geometry=[Point(0, 0)] * 3,
        crs="EPSG:4326",
    )
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1, 2, 3, 4, 5, 6], "macrocluster_id": [10, 10, 10, 10, 10, 10]},
        geometry=[Point(0, 0)] * 6,
        crs="EPSG:4326",
    )
    macroclusters_gdf = gpd.GeoDataFrame(
        {"macrocluster_id": [10]}, geometry=[Point(0, 0)], crs="EPSG:4326"
    )
    joined_gdf = gpd.GeoDataFrame(
        {
            "id_a": [1, 3, 5],
            "id_b": [2, 4, 6],
            **_joined_columns(3, [1.0, 1.0, 1.0], [True, True, True], [0.9, 0.7, None]),
        },
        geometry=[Point(0, 0)] * 3,
        crs="EPSG:4326",
    )

    result = rollup_daily_state(
        scout_candidates_gdf, joined_gdf, eraldis_gdf, macroclusters_gdf, _utc(2026, 8, 20)
    )

    row = result[result["macrocluster_id"] == 10].iloc[0]
    assert row["today_ranked_count_chanterelle"] == 3
    assert row["today_top_score_chanterelle"] == pytest.approx(0.9)
    assert row["today_top3_mean_score_chanterelle"] == pytest.approx((0.9 + 0.7 + 0.5) / 3)
    assert row["today_top_target_id_chanterelle"] is not None
    assert row["today_weather_coverage_chanterelle"] == pytest.approx(2 / 3)
    assert row["as_of"] == _utc(2026, 8, 20)


def test_rollup_daily_state_top3_mean_with_fewer_than_three_candidates():
    import geopandas as gpd
    from shapely.geometry import Point

    scout_candidates_gdf = gpd.GeoDataFrame(
        {
            "species": ["chanterelle"],
            "tier": ["ranked"],
            "scout_score": [0.6],
            "id_a": [1],
            "id_b": [2],
        },
        geometry=[Point(0, 0)],
        crs="EPSG:4326",
    )
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1, 2], "macrocluster_id": [10, 10]},
        geometry=[Point(0, 0)] * 2,
        crs="EPSG:4326",
    )
    macroclusters_gdf = gpd.GeoDataFrame(
        {"macrocluster_id": [10]}, geometry=[Point(0, 0)], crs="EPSG:4326"
    )
    joined_gdf = gpd.GeoDataFrame(
        {
            "id_a": [1],
            "id_b": [2],
            **_joined_columns(1, [1.0], [True], [0.6]),
        },
        geometry=[Point(0, 0)],
        crs="EPSG:4326",
    )

    result = rollup_daily_state(
        scout_candidates_gdf, joined_gdf, eraldis_gdf, macroclusters_gdf, _utc(2026, 8, 20)
    )

    row = result[result["macrocluster_id"] == 10].iloc[0]
    assert row["today_top3_mean_score_chanterelle"] == pytest.approx(0.6)


def test_rollup_daily_state_cluster_with_zero_candidates_gets_none_not_zero():
    import geopandas as gpd
    from shapely.geometry import Point

    scout_candidates_gdf = gpd.GeoDataFrame(
        {"species": [], "tier": [], "scout_score": [], "id_a": [], "id_b": []},
        geometry=[],
        crs="EPSG:4326",
    )
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1], "macrocluster_id": [10]}, geometry=[Point(0, 0)], crs="EPSG:4326"
    )
    macroclusters_gdf = gpd.GeoDataFrame(
        {"macrocluster_id": [10]}, geometry=[Point(0, 0)], crs="EPSG:4326"
    )
    joined_gdf = gpd.GeoDataFrame(
        {
            "id_a": [],
            "id_b": [],
            **_joined_columns(0, [], [], []),
        },
        geometry=[],
        crs="EPSG:4326",
    )

    result = rollup_daily_state(
        scout_candidates_gdf, joined_gdf, eraldis_gdf, macroclusters_gdf, _utc(2026, 8, 20)
    )

    row = result[result["macrocluster_id"] == 10].iloc[0]
    assert row["today_ranked_count_chanterelle"] == 0
    assert row["today_top_score_chanterelle"] is None
    assert row["today_top3_mean_score_chanterelle"] is None
    assert row["today_top_target_id_chanterelle"] is None


def test_rollup_daily_state_counts_cross_macrocluster_ecotones():
    import geopandas as gpd
    from shapely.geometry import Point

    scout_candidates_gdf = gpd.GeoDataFrame(
        {"species": [], "tier": [], "scout_score": [], "id_a": [], "id_b": []},
        geometry=[],
        crs="EPSG:4326",
    )
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1, 2], "macrocluster_id": [10, 20]},
        geometry=[Point(0, 0)] * 2,
        crs="EPSG:4326",
    )
    macroclusters_gdf = gpd.GeoDataFrame(
        {"macrocluster_id": [10, 20]}, geometry=[Point(0, 0)] * 2, crs="EPSG:4326"
    )
    joined_gdf = gpd.GeoDataFrame(
        {
            "id_a": [1],
            "id_b": [2],  # id_a is in cluster 10, id_b is in cluster 20 — cross-cluster
            **_joined_columns(1, [1.0], [True], [0.5]),
        },
        geometry=[Point(0, 0)],
        crs="EPSG:4326",
    )

    result = rollup_daily_state(
        scout_candidates_gdf, joined_gdf, eraldis_gdf, macroclusters_gdf, _utc(2026, 8, 20)
    )

    row_10 = result[result["macrocluster_id"] == 10].iloc[0]
    row_20 = result[result["macrocluster_id"] == 20].iloc[0]
    assert row_10["cross_macrocluster_ecotone_count"] == 1
    assert row_20["cross_macrocluster_ecotone_count"] == 0
