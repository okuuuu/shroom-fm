import geopandas as gpd

from shroom_fm.eraldis import ESTONIAN_GRID_CRS, WGS84_CRS

# Engineering starting points, not biological constants — retune once real
# field scouting results validate or invalidate them.
MAX_GAP_M = 10.0  # near_gap: max distance between boundaries to consider
MIN_CONTACT_LENGTH_M = 20.0  # touching: min shared-boundary length to keep (discards corners)
MIN_PROXIMITY_LENGTH_M = 20.0  # near_gap: min estimated parallel-run length to keep


def classify_pair(geom_a, geom_b) -> dict | None:
    shared = geom_a.boundary.intersection(geom_b.boundary)
    if shared.length >= MIN_CONTACT_LENGTH_M:
        return {
            "adjacency_type": "touching",
            "transition_length_m": shared.length,
            "gap_m": 0.0,
            "geometry": shared,
        }

    gap = geom_a.distance(geom_b)
    if 0 < gap <= MAX_GAP_M:
        zone = geom_a.buffer(MAX_GAP_M).intersection(geom_b.buffer(MAX_GAP_M))
        proximity_length = zone.area / MAX_GAP_M
        if proximity_length >= MIN_PROXIMITY_LENGTH_M:
            return {
                "adjacency_type": "near_gap",
                "transition_length_m": proximity_length,
                "gap_m": gap,
                "geometry": zone,
            }

    return None


def find_candidate_pairs(gdf: gpd.GeoDataFrame) -> list[tuple[int, int]]:
    buffered = gdf.copy()
    buffered["geometry"] = buffered.geometry.buffer(MAX_GAP_M)
    joined = gpd.sjoin(buffered, gdf, how="inner", predicate="intersects")

    pairs = set()
    for idx, row in joined.iterrows():
        id_a = gdf.loc[idx, "id"]
        id_b = row["id_right"]
        if id_a == id_b:
            continue
        pairs.add((min(id_a, id_b), max(id_a, id_b)))
    return sorted(pairs)


ADJACENCY_COLUMNS = ["id_a", "id_b", "adjacency_type", "transition_length_m", "gap_m", "geometry"]


def compute_adjacency(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    projected = gdf.to_crs(ESTONIAN_GRID_CRS)
    pairs = find_candidate_pairs(projected)

    id_to_geom = dict(zip(projected["id"], projected.geometry))

    records = []
    for id_a, id_b in pairs:
        result = classify_pair(id_to_geom[id_a], id_to_geom[id_b])
        if result is not None:
            records.append({"id_a": id_a, "id_b": id_b, **result})

    if not records:
        return gpd.GeoDataFrame(columns=ADJACENCY_COLUMNS, geometry="geometry", crs=WGS84_CRS)

    adjacency = gpd.GeoDataFrame(records, crs=ESTONIAN_GRID_CRS)
    return adjacency.to_crs(WGS84_CRS)
