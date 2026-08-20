import geopandas as gpd
import networkx as nx

from shroom_fm.eraldis import ESTONIAN_GRID_CRS, WGS84_CRS
from shroom_fm.forest_block import MACROCLUSTER_TARGET_EXTENT_M, geometry_extent_m

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


def _complete_linkage_merge(
    block_ids: list[int],
    id_to_centroid: dict,
    graph: nx.Graph,
    threshold: float,
) -> list[list[int]]:
    """Connectivity-constrained complete-linkage agglomerative clustering,
    implemented directly rather than via sklearn's AgglomerativeClustering:
    empirically verified (against scikit-learn 1.9.0, by reading
    _agglomerative.py's linkage_tree and by side-by-side experiments) that
    sklearn's connectivity-constrained linkage="complete" does NOT compute
    true diameter distances for a sparse (non-clique) connectivity graph —
    it silently uses a hop-weight proxy instead of the real max-over-all-
    cross-pairs distance, which defeats this algorithm on exactly the chain
    topology it exists to handle. Repeatedly merges the connectivity-adjacent
    cluster pair with the smallest true complete-linkage distance, stopping
    once the smallest remaining connectivity-adjacent pair's distance exceeds
    `threshold`."""
    clusters = [{b} for b in block_ids]

    def real_distance(a, b):
        return id_to_centroid[a].distance(id_to_centroid[b])

    def complete_linkage_distance(cluster_a, cluster_b):
        return max(real_distance(a, b) for a in cluster_a for b in cluster_b)

    def connectivity_adjacent(cluster_a, cluster_b):
        return any(graph.has_edge(a, b) for a in cluster_a for b in cluster_b)

    while len(clusters) > 1:
        best_pair = None
        best_distance = float("inf")
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                if not connectivity_adjacent(clusters[i], clusters[j]):
                    continue
                d = complete_linkage_distance(clusters[i], clusters[j])
                if d < best_distance:
                    best_distance = d
                    best_pair = (i, j)
        if best_pair is None or best_distance > threshold:
            break
        i, j = best_pair
        merged = clusters[i] | clusters[j]
        clusters = [c for k, c in enumerate(clusters) if k not in (i, j)] + [merged]

    return [sorted(c) for c in clusters]


def _partition_component(
    block_ids: list[int],
    id_to_centroid: dict,
    id_to_geom: dict,
    graph: nx.Graph,
    max_extent_m: float,
    depth: int = 0,
) -> list[list[int]]:
    geom = _dissolve([id_to_geom[i] for i in block_ids])
    if geometry_extent_m(geom) <= max_extent_m:
        return [block_ids]

    if len(block_ids) == 1 or depth >= _MAX_REPARTITION_DEPTH:
        # Either a single block already exceeds max_extent_m on its own (nothing
        # to partition), or we've recursed too many times — give up and let the
        # caller flag this group oversized rather than looping indefinitely.
        return [block_ids]

    threshold = max_extent_m * (_REPARTITION_SHRINK_FACTOR**depth)
    groups = _complete_linkage_merge(block_ids, id_to_centroid, graph, threshold)

    result = []
    for group_block_ids in groups:
        group_geom = _dissolve([id_to_geom[i] for i in group_block_ids])
        if geometry_extent_m(group_geom) <= max_extent_m:
            result.append(group_block_ids)
        else:
            result.extend(
                _partition_component(
                    group_block_ids, id_to_centroid, id_to_geom, graph, max_extent_m, depth + 1
                )
            )
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
