import geopandas as gpd
import networkx as nx
from shapely.geometry import Point

from shroom_fm.eraldis import ESTONIAN_GRID_CRS, WGS84_CRS

# Diagnostic threshold only — a block flagged oversized isn't auto-split in v0.
# See macrocluster.py for the hard MACROCLUSTER_MAX_EXTENT_M cap this is set
# relative to (imported from here, not redefined, to avoid the two drifting apart).
MACROCLUSTER_TARGET_EXTENT_M = 25_000


def geometry_extent_m(geometry) -> float:
    """Max pairwise distance between vertices of geometry's convex hull — a cheap,
    exact diameter measurement since hull vertex count is small, and the two points
    achieving maximum pairwise distance in any point set are always both on its
    convex hull. `geometry` must already be in a projected (meters) CRS."""
    hull = geometry.convex_hull
    if hull.geom_type == "Point":
        return 0.0
    elif hull.geom_type == "LineString":
        coords = list(hull.coords)
    else:
        coords = list(hull.exterior.coords)
    points = [Point(c) for c in coords]
    return max(
        (a.distance(b) for i, a in enumerate(points) for b in points[i + 1 :]),
        default=0.0,
    )


def compute_forest_blocks(
    eraldis_gdf: gpd.GeoDataFrame, adjacency_gdf: gpd.GeoDataFrame
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    graph = nx.Graph()
    graph.add_nodes_from(eraldis_gdf["id"])
    graph.add_edges_from(zip(adjacency_gdf["id_a"], adjacency_gdf["id_b"]))

    components = [set(c) for c in nx.connected_components(graph)]
    # Deterministic numbering: sort components by their minimum member id so
    # re-running against unchanged input reproduces the same forest_block_ids.
    components.sort(key=min)

    id_to_block = {}
    for block_id, member_ids in enumerate(components):
        for eraldis_id in member_ids:
            id_to_block[eraldis_id] = block_id

    result = eraldis_gdf.copy()
    result["forest_block_id"] = result["id"].map(id_to_block)

    projected = eraldis_gdf.to_crs(ESTONIAN_GRID_CRS)
    id_to_geom = dict(zip(projected["id"], projected.geometry))

    records = []
    for block_id, member_ids in enumerate(components):
        member_geoms = [id_to_geom[i] for i in member_ids]
        dissolved = gpd.GeoSeries(member_geoms, crs=ESTONIAN_GRID_CRS).union_all()
        extent = geometry_extent_m(dissolved)
        records.append(
            {
                "forest_block_id": block_id,
                "eraldis_count": len(member_ids),
                "geometry_extent_m": extent,
                "oversized_block": extent > MACROCLUSTER_TARGET_EXTENT_M,
                "geometry": dissolved,
            }
        )

    blocks_gdf = gpd.GeoDataFrame(records, crs=ESTONIAN_GRID_CRS).to_crs(WGS84_CRS)
    return result, blocks_gdf
