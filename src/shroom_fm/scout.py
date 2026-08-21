import geopandas as gpd
import pandas as pd

from shroom_fm.access import ACCESS_DISTANCE_CAP_M

MAX_WALK_FROM_CAR_M = ACCESS_DISTANCE_CAP_M
REMOTE_EXCLUSION_REASON = "REMOTE_BY_V1_ACCESS_PROXY"

ACCESS_COLUMNS = ["access_score", "access_confidence", "access_reason", "nearest_car_road_m"]


def join_ecotone_access(
    ecotones_gdf: gpd.GeoDataFrame, eraldis_gdf: gpd.GeoDataFrame
) -> gpd.GeoDataFrame:
    access_by_id = eraldis_gdf.set_index("id")[ACCESS_COLUMNS]
    access_a = access_by_id.reindex(ecotones_gdf["id_a"]).reset_index(drop=True)
    access_b = access_by_id.reindex(ecotones_gdf["id_b"]).reset_index(drop=True)

    result = ecotones_gdf.copy().reset_index(drop=True)

    access_modifier = []
    access_confidence = []
    access_reason = []
    nearest_car_road_m = []
    scout_eligible = []

    for a, b in zip(access_a.itertuples(index=False), access_b.itertuples(index=False)):
        score_a = 0.0 if pd.isna(a.access_score) else a.access_score
        score_b = 0.0 if pd.isna(b.access_score) else b.access_score
        winner = a if score_a >= score_b else b
        winner_score = score_a if score_a >= score_b else score_b
        winner_distance = (
            None if pd.isna(winner.nearest_car_road_m) else winner.nearest_car_road_m
        )

        access_modifier.append(winner_score)
        access_confidence.append(
            None if pd.isna(winner.access_confidence) else winner.access_confidence
        )
        access_reason.append(None if pd.isna(winner.access_reason) else winner.access_reason)
        nearest_car_road_m.append(winner_distance)
        scout_eligible.append(
            winner_distance is not None and winner_distance <= MAX_WALK_FROM_CAR_M
        )

    result["access_modifier"] = access_modifier
    result["access_confidence"] = access_confidence
    result["access_reason"] = access_reason
    result["nearest_car_road_m"] = nearest_car_road_m
    # Assign as object dtype, not pandas' inferred bool dtype: a bool-dtype column
    # would round-trip through .loc as numpy.bool_, which fails `is True`/`is False`
    # identity checks against Python bool downstream (and in this module's own tests).
    result["scout_eligible"] = pd.array(scout_eligible, dtype=object)

    return result


MISSING_FRUITING_DATA_REASON = "MISSING_FRUITING_DATA"
MIN_SCOUT_WEATHER_COVERAGE = 0.90

MIN_SCOUT_SEPARATION_M = 400.0
MAX_SUPPRESSED_EXAMPLES_PER_TARGET = 3


def suppress_nearby_candidates(
    scored_gdf: gpd.GeoDataFrame, min_separation_m: float
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """scored_gdf must already be sorted by scout_score descending, with real geometry
    in a metric CRS (this project's ESTONIAN_GRID_CRS) — greedy nearest-neighbor
    suppression: walks rows in score order, keeping a candidate only if its centroid is
    farther than min_separation_m from every already-KEPT candidate's centroid (not
    every prior candidate — a candidate suppressed earlier never itself becomes a
    reference point). Returns (retained, suppressed). Suppressed rows gain
    suppressed_by_id (the retaining candidate's f"{id_a}_{id_b}", same convention as
    rollup_daily_state's today_top_target_id_{species}), suppression_distance_m (real
    centroid distance to the suppressor, not the threshold), and pre_suppression_rank
    (1-based position in scored_gdf's own sorted order, before any suppression)."""
    if len(scored_gdf) == 0:
        empty = scored_gdf.copy()
        empty["suppressed_by_id"] = pd.Series(dtype=object)
        empty["suppression_distance_m"] = pd.Series(dtype=float)
        empty["pre_suppression_rank"] = pd.Series(dtype="Int64")
        return scored_gdf.copy(), empty

    centroids = scored_gdf.geometry.centroid
    retained_idx: list = []
    retained_centroids: list = []
    suppressed_records = []

    for position, (idx, centroid) in enumerate(zip(scored_gdf.index, centroids), start=1):
        nearest_distance = None
        nearest_retained_idx = None
        for r_idx, r_centroid in zip(retained_idx, retained_centroids):
            d = centroid.distance(r_centroid)
            if nearest_distance is None or d < nearest_distance:
                nearest_distance = d
                nearest_retained_idx = r_idx
        if nearest_distance is not None and nearest_distance < min_separation_m:
            retaining_row = scored_gdf.loc[nearest_retained_idx]
            suppressed_records.append(
                {
                    "index": idx,
                    "suppressed_by_id": f"{retaining_row['id_a']}_{retaining_row['id_b']}",
                    "suppression_distance_m": nearest_distance,
                    "pre_suppression_rank": position,
                }
            )
        else:
            retained_idx.append(idx)
            retained_centroids.append(centroid)

    retained = scored_gdf.loc[retained_idx].copy()

    if suppressed_records:
        suppressed_meta = pd.DataFrame(suppressed_records).set_index("index")
        suppressed = scored_gdf.loc[suppressed_meta.index].copy()
        suppressed["suppressed_by_id"] = suppressed_meta["suppressed_by_id"]
        suppressed["suppression_distance_m"] = suppressed_meta["suppression_distance_m"]
        suppressed["pre_suppression_rank"] = suppressed_meta["pre_suppression_rank"]
    else:
        suppressed = scored_gdf.iloc[0:0].copy()
        suppressed["suppressed_by_id"] = pd.Series(dtype=object)
        suppressed["suppression_distance_m"] = pd.Series(dtype=float)
        suppressed["pre_suppression_rank"] = pd.Series(dtype="Int64")

    return retained, suppressed


def scout_score(
    ecotone_score: float | None,
    access_modifier: float | None,
    fruiting_modifier: float | None,
    eligible: bool,
) -> float | None:
    if (
        not eligible
        or ecotone_score is None
        or access_modifier is None
        or fruiting_modifier is None
    ):
        return None
    return ecotone_score * access_modifier * fruiting_modifier


def scout_candidates_for_species(
    joined_gdf: gpd.GeoDataFrame, species: str, top_n: int
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    ecotone_col = f"ecotone_score_{species}"
    fruiting_col = f"fruiting_modifier_{species}"
    scored = joined_gdf[joined_gdf[ecotone_col].notna()].copy()
    scored["ecotone_score"] = scored[ecotone_col]
    scored["fruiting_score"] = scored[fruiting_col]
    scored["scout_score"] = [
        scout_score(ecotone_score_value, access_modifier_value, fruiting_value, eligible)
        for ecotone_score_value, access_modifier_value, fruiting_value, eligible in zip(
            scored["ecotone_score"],
            scored["access_modifier"],
            scored["fruiting_score"],
            scored["scout_eligible"],
        )
    ]

    def _exclusion_reason(eligible, fruiting_value):
        if not eligible:
            return REMOTE_EXCLUSION_REASON
        return MISSING_FRUITING_DATA_REASON

    ranked = (
        scored[scored["scout_score"].notna()]
        .sort_values("scout_score", ascending=False)
        .head(top_n)
    )
    excluded = scored[scored["scout_score"].isna()].copy()
    excluded["exclusion_reason"] = [
        _exclusion_reason(eligible, fruiting_value)
        for eligible, fruiting_value in zip(
            excluded["scout_eligible"], excluded["fruiting_score"]
        )
    ]
    remote = excluded.sort_values("ecotone_score", ascending=False).head(top_n)
    return ranked, remote


def weather_coverage_ratio(joined_gdf: gpd.GeoDataFrame, species: str) -> float:
    ecotone_col = f"ecotone_score_{species}"
    fruiting_col = f"fruiting_modifier_{species}"
    eligible_pool = joined_gdf[
        joined_gdf[ecotone_col].notna() & (joined_gdf["scout_eligible"] == True)  # noqa: E712
    ]
    if len(eligible_pool) == 0:
        return 1.0
    with_fruiting_data = eligible_pool[eligible_pool[fruiting_col].notna()]
    return len(with_fruiting_data) / len(eligible_pool)
