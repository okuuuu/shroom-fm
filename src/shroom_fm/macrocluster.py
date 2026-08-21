from datetime import datetime

import geopandas as gpd
import networkx as nx
import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import pdist

from shroom_fm.eraldis import ESTONIAN_GRID_CRS, WGS84_CRS
from shroom_fm.forest_block import MACROCLUSTER_TARGET_EXTENT_M, geometry_extent_m
from shroom_fm.habitat import TARGET_SPECIES
from shroom_fm.scout import weather_coverage_ratio

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


MACROCLUSTER_MAX_EXTENT_M = 35_000
TARGET_BLOCK_COUNT = (5, 15)

_MAX_REPARTITION_DEPTH = 5
_REPARTITION_SHRINK_FACTOR = 0.8


def _dissolve(geoms):
    return gpd.GeoSeries(geoms, crs=ESTONIAN_GRID_CRS).union_all()


def _partition_component(
    block_ids: list[int],
    id_to_centroid: dict,
    id_to_geom: dict,
    graph: nx.Graph,
    max_extent_m: float,
    depth: int = 0,
) -> list[list[int]]:
    """Partitions block_ids (already known to be one connected component of
    `graph`) into groups where every group's real geometry_extent_m is
    <= max_extent_m AND every group is internally connected via `graph`.

    Uses scipy's global complete-linkage clustering (vectorized, O(n^2) via
    scipy's NN-chain algorithm — not the O(n^4)-class from-scratch loop this
    replaces) as a fast centroid-distance-based first pass, computed WITHOUT
    any connectivity restriction in the clustering step itself (this is what
    avoids reproducing the correctness bug found in
    sklearn.cluster.AgglomerativeClustering's connectivity-constrained
    linkage="complete", which silently used partial/incomplete distance info
    for non-graph-adjacent pairs — see this module's docstring history).
    Connectivity is instead enforced as a separate, simple post-hoc check:
    any resulting flat cluster that isn't actually graph-connected gets split
    into its real connected sub-components (splitting only removes members,
    so this can only shrink geometry_extent_m, never re-exceed the cap — no
    extent re-check needed after a connectivity split). A resulting group
    whose REAL geometry_extent_m still exceeds the cap (centroid distance can
    be optimistic for large/elongated blocks) is recursively repartitioned
    with a shrunk threshold — hierarchical clustering's monotonicity property
    (a strictly smaller threshold can only produce equal-or-more-refined
    groups, never fewer) guarantees this makes progress and terminates.
    """
    if len(block_ids) == 1:
        return [block_ids]
    if depth >= _MAX_REPARTITION_DEPTH:
        # Give up trying to shrink extent further, but still apply the same
        # post-hoc connectivity check every other return path goes through:
        # a group that's still oversized after exhausting repartition depth
        # might nonetheless be graph-disconnected, and skipping the check
        # here would let two graph-unreachable blocks get merged into one
        # macrocluster just because centroid distance never happened to
        # split them. Splitting can only shrink geometry_extent_m (never
        # re-exceed the already-checked cap), so no extent re-check is
        # needed here either.
        connected_groups = [sorted(c) for c in nx.connected_components(graph.subgraph(block_ids))]
        if len(connected_groups) == 1:
            return [block_ids]
        return connected_groups

    threshold = max_extent_m * (_REPARTITION_SHRINK_FACTOR**depth)
    ordered_ids = sorted(block_ids)

    if len(ordered_ids) == 2:
        # Handled directly as a simple case, avoiding the overhead of
        # building a distance matrix and calling scipy for just two points
        # (scipy.cluster.hierarchy.linkage itself handles n=2 fine; this is
        # purely an optimization, not a workaround for a scipy limitation).
        a, b = ordered_ids
        dist = id_to_centroid[a].distance(id_to_centroid[b])
        centroid_groups = [[a, b]] if dist <= threshold else [[a], [b]]
    else:
        coords = np.array(
            [[id_to_centroid[i].x, id_to_centroid[i].y] for i in ordered_ids]
        )
        condensed = pdist(coords, metric="euclidean")
        Z = linkage(condensed, method="complete")
        labels = fcluster(Z, t=threshold, criterion="distance")
        groups_by_label: dict[int, list[int]] = {}
        for block_id, label in zip(ordered_ids, labels):
            groups_by_label.setdefault(label, []).append(block_id)
        centroid_groups = list(groups_by_label.values())

    result = []
    for group in centroid_groups:
        if len(group) == 1:
            result.append(group)
            continue
        geom = _dissolve([id_to_geom[i] for i in group])
        if geometry_extent_m(geom) > max_extent_m:
            result.extend(
                _partition_component(
                    group, id_to_centroid, id_to_geom, graph, max_extent_m, depth + 1
                )
            )
            continue
        connected_groups = [
            sorted(c) for c in nx.connected_components(graph.subgraph(group))
        ]
        if len(connected_groups) == 1:
            result.append(group)
        else:
            result.extend(connected_groups)

    return result


def compute_macroclusters(
    forest_blocks_gdf: gpd.GeoDataFrame, graph: nx.Graph
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    projected = forest_blocks_gdf.to_crs(ESTONIAN_GRID_CRS)
    id_to_geom = dict(zip(projected["forest_block_id"], projected.geometry))
    id_to_centroid = {i: geom.centroid for i, geom in id_to_geom.items()}
    id_to_eraldis_count = dict(zip(projected["forest_block_id"], projected["eraldis_count"]))

    super_components = [set(c) for c in nx.connected_components(graph)]
    super_components.sort(key=min)

    all_groups: list[list[int]] = []
    for component in super_components:
        block_ids = sorted(component)
        groups = _partition_component(
            block_ids, id_to_centroid, id_to_geom, graph, MACROCLUSTER_MAX_EXTENT_M
        )
        all_groups.extend(groups)

    all_groups.sort(key=min)

    block_id_to_cluster = {}
    records = []
    for cluster_id, group in enumerate(all_groups):
        for block_id in group:
            block_id_to_cluster[block_id] = cluster_id

        member_geoms = [id_to_geom[i] for i in group]
        dissolved = _dissolve(member_geoms)
        centroid_geom = _dissolve([id_to_centroid[i] for i in group])
        geom_extent = geometry_extent_m(dissolved)
        centroid_extent = geometry_extent_m(centroid_geom)
        eraldis_count = int(sum(id_to_eraldis_count[i] for i in group))

        records.append(
            {
                "macrocluster_id": cluster_id,
                "geometry": dissolved,
                "forest_block_count": len(group),
                "eraldis_count": eraldis_count,
                "centroid_extent_m": centroid_extent,
                "geometry_extent_m": geom_extent,
                "oversized_macrocluster": geom_extent > MACROCLUSTER_MAX_EXTENT_M,
                "within_target_extent": geom_extent <= MACROCLUSTER_TARGET_EXTENT_M,
                "within_target_block_count": (
                    TARGET_BLOCK_COUNT[0] <= len(group) <= TARGET_BLOCK_COUNT[1]
                ),
            }
        )

    clusters_gdf = gpd.GeoDataFrame(records, crs=ESTONIAN_GRID_CRS).to_crs(WGS84_CRS)

    result_blocks = forest_blocks_gdf.copy()
    result_blocks["macrocluster_id"] = result_blocks["forest_block_id"].map(block_id_to_cluster)

    return result_blocks, clusters_gdf


def ecotone_macrocluster_id(
    id_a: int, id_b: int, eraldis_to_macrocluster: dict[int, int]
) -> tuple[int, bool]:
    cluster_a = eraldis_to_macrocluster[id_a]
    cluster_b = eraldis_to_macrocluster[id_b]
    return cluster_a, cluster_a != cluster_b


def attach_macrocluster_id(
    joined_gdf: gpd.GeoDataFrame, eraldis_gdf: gpd.GeoDataFrame
) -> gpd.GeoDataFrame:
    """Adds macrocluster_id (+ diagnostic-only is_cross_macrocluster) to every row of
    joined_gdf (ecotones x access x fruiting, id_a/id_b columns), reusing
    ecotone_macrocluster_id's existing cross-macrocluster convention (bucketed under
    stand A's macrocluster) — the same resolution rollup_daily_state already performs,
    just moved earlier in the pipeline so per-macrocluster ranking can happen at export
    time instead of only at rollup time."""
    eraldis_to_macrocluster = dict(zip(eraldis_gdf["id"], eraldis_gdf["macrocluster_id"]))
    cluster_ids = []
    cross_flags = []
    for id_a, id_b in zip(joined_gdf["id_a"], joined_gdf["id_b"]):
        cluster_id, is_cross = ecotone_macrocluster_id(id_a, id_b, eraldis_to_macrocluster)
        cluster_ids.append(cluster_id)
        cross_flags.append(is_cross)
    result = joined_gdf.copy()
    result["macrocluster_id"] = cluster_ids
    result["is_cross_macrocluster"] = cross_flags
    return result


def rollup_daily_state(
    scout_candidates_gdf: gpd.GeoDataFrame,
    joined_gdf: gpd.GeoDataFrame,
    eraldis_gdf: gpd.GeoDataFrame,
    macroclusters_gdf: gpd.GeoDataFrame,
    now: datetime,
) -> gpd.GeoDataFrame:
    eraldis_to_macrocluster = dict(zip(eraldis_gdf["id"], eraldis_gdf["macrocluster_id"]))

    # Assign every candidate and every scored ecotone to a macrocluster, counting
    # cross-cluster anomalies as we go (diagnostic, never a hard failure).
    candidate_cluster_ids = []
    for id_a, id_b in zip(scout_candidates_gdf["id_a"], scout_candidates_gdf["id_b"]):
        cluster_id, _ = ecotone_macrocluster_id(id_a, id_b, eraldis_to_macrocluster)
        candidate_cluster_ids.append(cluster_id)
    candidates = scout_candidates_gdf.copy()
    candidates["macrocluster_id"] = candidate_cluster_ids

    joined_cluster_ids = []
    cross_flags = []
    for id_a, id_b in zip(joined_gdf["id_a"], joined_gdf["id_b"]):
        cluster_id, is_cross = ecotone_macrocluster_id(id_a, id_b, eraldis_to_macrocluster)
        joined_cluster_ids.append(cluster_id)
        cross_flags.append(is_cross)
    joined = joined_gdf.copy()
    joined["macrocluster_id"] = joined_cluster_ids
    joined["is_cross_macrocluster"] = cross_flags

    records = []
    for cluster_id in macroclusters_gdf["macrocluster_id"]:
        record = {"macrocluster_id": cluster_id, "as_of": now}
        cluster_candidates = candidates[candidates["macrocluster_id"] == cluster_id]
        cluster_joined = joined[joined["macrocluster_id"] == cluster_id]
        record["cross_macrocluster_ecotone_count"] = int(
            cluster_joined["is_cross_macrocluster"].sum()
        )

        for species in TARGET_SPECIES:
            ranked = cluster_candidates[
                (cluster_candidates["species"] == species)
                & (cluster_candidates["tier"] == "ranked")
            ]
            ranked_count = len(ranked)
            record[f"today_ranked_count_{species}"] = ranked_count
            if ranked_count == 0:
                record[f"today_top_score_{species}"] = None
                record[f"today_top3_mean_score_{species}"] = None
                record[f"today_top_target_id_{species}"] = None
            else:
                sorted_ranked = ranked.sort_values("scout_score", ascending=False)
                record[f"today_top_score_{species}"] = float(sorted_ranked.iloc[0]["scout_score"])
                top3 = sorted_ranked["scout_score"].head(3)
                record[f"today_top3_mean_score_{species}"] = float(top3.mean())
                record[f"today_top_target_id_{species}"] = (
                    f"{sorted_ranked.iloc[0]['id_a']}_{sorted_ranked.iloc[0]['id_b']}"
                )
            # weather_coverage_ratio() deliberately fails open to 1.0 for an empty
            # eligible pool — correct for its ORIGINAL caller
            # (export_scout_candidates.py's MIN_SCOUT_WEATHER_COVERAGE publish-refusal
            # guard, where an empty macro-scale pool shouldn't block a species'
            # ranking). But here it's a REPORTED METRIC for one specific macrocluster,
            # and a 1.0 for a cluster with zero eligible ecotones for this species reads
            # as "100% weather coverage" when the truth is "no data to have coverage
            # over at all" — a fabrication this project's discipline forbids. Check the
            # eligible pool size locally (deliberately not touching
            # scout.weather_coverage_ratio itself, whose fail-open behavior is correct
            # and load-bearing for its original caller) and report None instead.
            ecotone_col = f"ecotone_score_{species}"
            eligible_pool_size = len(
                cluster_joined[
                    cluster_joined[ecotone_col].notna()
                    & (cluster_joined["scout_eligible"] == True)  # noqa: E712
                ]
            )
            if eligible_pool_size == 0:
                record[f"today_weather_coverage_{species}"] = None
            else:
                record[f"today_weather_coverage_{species}"] = weather_coverage_ratio(
                    cluster_joined, species
                )

        records.append(record)

    return gpd.GeoDataFrame(records, geometry=macroclusters_gdf.geometry.values, crs=macroclusters_gdf.crs)
