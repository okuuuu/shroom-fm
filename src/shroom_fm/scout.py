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
    (1-based position in scored_gdf's own sorted order, before any suppression).

    Implementation note: candidates are looked up via a uniform spatial grid keyed on
    min_separation_m, not a linear scan of the full retained set. This is exact, not
    an approximation: with grid cells exactly min_separation_m wide, any two points in
    non-adjacent cells (grid index differing by >=2 on either axis) are provably at
    least min_separation_m apart, so only a candidate's 3x3 cell neighborhood can ever
    contain a retained point within min_separation_m — every point outside that
    neighborhood is guaranteed too far to matter, and every point inside it is checked
    with a real, exact centroid.distance() call, same as before. Real-scale profiling
    (real macrocluster buckets up to ~49,000 ecotones) found the previous full-scan
    version spending >99% of its time in >100 million redundant distance() calls for
    just 5 buckets; this makes the per-candidate cost roughly constant instead of
    growing with the retained-set size."""
    if len(scored_gdf) == 0:
        empty = scored_gdf.copy()
        empty["suppressed_by_id"] = pd.Series(dtype=object)
        empty["suppression_distance_m"] = pd.Series(dtype=float)
        empty["pre_suppression_rank"] = pd.Series(dtype="Int64")
        return scored_gdf.copy(), empty

    centroids = scored_gdf.geometry.centroid
    cell_size = min_separation_m
    # grid[(cell_x, cell_y)] -> list of (idx, centroid) for RETAINED candidates whose
    # centroid falls in that cell.
    grid: dict[tuple[int, int], list] = {}
    retained_idx: list = []
    suppressed_records = []

    for position, (idx, centroid) in enumerate(zip(scored_gdf.index, centroids), start=1):
        cell_x = int(centroid.x // cell_size)
        cell_y = int(centroid.y // cell_size)
        nearest_distance = None
        nearest_retained_idx = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for r_idx, r_centroid in grid.get((cell_x + dx, cell_y + dy), ()):
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
            grid.setdefault((cell_x, cell_y), []).append((idx, centroid))

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


def _compute_scored(joined_gdf: gpd.GeoDataFrame, species: str) -> gpd.GeoDataFrame:
    """Shared first half of both remote_high_value_for_species and
    scout_candidates_for_species_macrocluster: filters to ecologically-scored rows and
    computes scout_score (None when access-ineligible or fruiting data missing —
    unchanged formula, unchanged from the old scout_candidates_for_species)."""
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
    return scored


def remote_high_value_for_species(
    joined_gdf: gpd.GeoDataFrame, species: str, top_n: int
) -> gpd.GeoDataFrame:
    """Global (not per-macrocluster) — ecologically-strong candidates the v1
    access-distance proxy couldn't confirm eligible for, or that are missing fruiting
    data, ranked by raw ecotone_score (scout_score is None for these by construction).
    This is exactly the 'remote' half of the old scout_candidates_for_species,
    unchanged behavior, split into its own function since its scope (global) now
    differs from the ranked tier's (per-macrocluster)."""

    def _exclusion_reason(eligible, fruiting_value):
        if not eligible:
            return REMOTE_EXCLUSION_REASON
        return MISSING_FRUITING_DATA_REASON

    scored = _compute_scored(joined_gdf, species)
    excluded = scored[scored["scout_score"].isna()].copy()
    excluded["exclusion_reason"] = [
        _exclusion_reason(eligible, fruiting_value)
        for eligible, fruiting_value in zip(
            excluded["scout_eligible"], excluded["fruiting_score"]
        )
    ]
    return excluded.sort_values("ecotone_score", ascending=False).head(top_n)


def scout_candidates_for_species_macrocluster(
    bucket_gdf: gpd.GeoDataFrame,
    species: str,
    top_n: int,
    min_separation_m: float,
    max_suppressed_examples: int,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """bucket_gdf must already be filtered to one macrocluster (see
    macrocluster.attach_macrocluster_id) and in a metric CRS (this project's
    ESTONIAN_GRID_CRS — suppress_nearby_candidates needs real distances). Computes
    scout_score per row (unchanged formula), sorts, applies spatial suppression, caps
    at top_n. Only rows that already have a real scout_score enter suppression — an
    access-ineligible or missing-fruiting-data candidate never suppresses a real one,
    since scout_score() already returns None for those cases before this function ever
    sees them as suppression candidates. Returns (ranked, capped_suppressed): ranked
    carries new nearby_suppressed_count/nearby_best_suppressed_score columns (computed
    from the FULL suppressed set attributable to each final ranked row, before the
    max_suppressed_examples cap truncates what's returned in capped_suppressed)."""
    scored = _compute_scored(bucket_gdf, species)
    eligible = scored[scored["scout_score"].notna()].sort_values(
        "scout_score", ascending=False
    )
    retained, suppressed = suppress_nearby_candidates(eligible, min_separation_m)

    ranked = retained.head(top_n).copy()
    ranked["own_id"] = [f"{a}_{b}" for a, b in zip(ranked["id_a"], ranked["id_b"])]

    # Only suppressed rows attributed to a FINAL ranked target matter here — a
    # candidate that was retained by suppress_nearby_candidates but didn't make the
    # top_n cut (never exported at all) shouldn't drag its own suppressed neighbors
    # into the output either.
    relevant_suppressed = suppressed[suppressed["suppressed_by_id"].isin(ranked["own_id"])]

    nearby_counts = relevant_suppressed.groupby("suppressed_by_id").size()
    nearby_best = relevant_suppressed.groupby("suppressed_by_id")["scout_score"].max()
    ranked["nearby_suppressed_count"] = (
        ranked["own_id"].map(nearby_counts).fillna(0).astype(int)
    )
    ranked["nearby_best_suppressed_score"] = ranked["own_id"].map(nearby_best)
    ranked = ranked.drop(columns=["own_id"])

    capped_suppressed = (
        relevant_suppressed.sort_values("scout_score", ascending=False)
        .groupby("suppressed_by_id", group_keys=False)
        .head(max_suppressed_examples)
    )

    return ranked, capped_suppressed


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
