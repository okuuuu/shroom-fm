import random

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

from shroom_fm.scout import (
    REMOTE_EXCLUSION_REASON,
    join_ecotone_access,
    remote_high_value_for_species,
    scout_candidates_for_species_macrocluster,
    scout_score,
    suppress_nearby_candidates,
    weather_coverage_ratio,
)


def test_join_ecotone_access_uses_better_served_stand():
    ecotones_gdf = gpd.GeoDataFrame(
        {"id_a": [1], "id_b": [2]},
        geometry=[Point(0, 0)],
        crs="EPSG:3301",
    )
    eraldis_gdf = gpd.GeoDataFrame(
        {
            "id": [1, 2],
            "access_score": [0.8, 0.3],
            "access_confidence": ["HIGH_CONFIDENCE", "NORMAL"],
            "access_reason": [
                "100m from Kõrvalmaantee-class road",
                "800m from Muu tee-class road",
            ],
            "nearest_car_road_m": [100.0, 800.0],
        },
        geometry=[Point(0, 0), Point(10, 10)],
        crs="EPSG:3301",
    )

    result = join_ecotone_access(ecotones_gdf, eraldis_gdf)

    assert result.loc[0, "access_modifier"] == pytest.approx(0.8)
    assert result.loc[0, "access_confidence"] == "HIGH_CONFIDENCE"
    assert result.loc[0, "access_reason"] == "100m from Kõrvalmaantee-class road"
    assert result.loc[0, "nearest_car_road_m"] == pytest.approx(100.0)
    assert result.loc[0, "scout_eligible"] is True


def test_join_ecotone_access_ineligible_when_winning_side_beyond_cap():
    ecotones_gdf = gpd.GeoDataFrame(
        {"id_a": [1], "id_b": [2]},
        geometry=[Point(0, 0)],
        crs="EPSG:3301",
    )
    eraldis_gdf = gpd.GeoDataFrame(
        {
            "id": [1, 2],
            "access_score": [0.0, 0.0],
            "access_confidence": [None, None],
            "access_reason": [
                "no car-accessible road within 1500m",
                "no car-accessible road within 1500m",
            ],
            "nearest_car_road_m": [None, None],
        },
        geometry=[Point(0, 0), Point(10, 10)],
        crs="EPSG:3301",
    )

    result = join_ecotone_access(ecotones_gdf, eraldis_gdf)

    assert result.loc[0, "access_modifier"] == 0.0
    assert result.loc[0, "nearest_car_road_m"] is None
    assert result.loc[0, "scout_eligible"] is False


def test_join_ecotone_access_normalizes_missing_stand_reference_to_zero_access():
    ecotones_gdf = gpd.GeoDataFrame(
        {"id_a": [1], "id_b": [999]},
        geometry=[Point(0, 0)],
        crs="EPSG:3301",
    )
    eraldis_gdf = gpd.GeoDataFrame(
        {
            "id": [1],
            "access_score": [0.2],
            "access_confidence": ["CONDITIONAL"],
            "access_reason": ["1200m from Muu tee-class road"],
            "nearest_car_road_m": [1200.0],
        },
        geometry=[Point(0, 0)],
        crs="EPSG:3301",
    )

    result = join_ecotone_access(ecotones_gdf, eraldis_gdf)

    assert result.loc[0, "access_modifier"] == pytest.approx(0.2)
    assert result.loc[0, "nearest_car_road_m"] == pytest.approx(1200.0)
    assert result.loc[0, "scout_eligible"] is True


def test_scout_score_multiplies_when_eligible():
    assert scout_score(1.2, 0.5, 1.0, True) == pytest.approx(0.6)


def test_scout_score_is_none_when_ineligible():
    assert scout_score(1.2, 0.5, 1.0, False) is None


def test_scout_score_is_none_when_ecotone_score_missing():
    assert scout_score(None, 0.5, 1.0, True) is None


def test_scout_score_is_none_when_access_modifier_missing():
    assert scout_score(1.0, None, 1.0, True) is None


def test_scout_score_multiplies_all_three_factors_when_eligible():
    assert scout_score(1.2, 0.5, 0.8, True) == pytest.approx(0.48)


def test_scout_score_is_none_when_fruiting_modifier_missing():
    assert scout_score(1.2, 0.5, None, True) is None


def test_remote_high_value_for_species_selects_excluded_candidates_by_ecotone_score():
    joined_gdf = gpd.GeoDataFrame(
        {
            "ecotone_score_chanterelle": [1.5, 1.2, 0.9, 0.6, None],
            "access_modifier": [0.8, 0.5, 0.9, 0.1, 0.9],
            "fruiting_modifier_chanterelle": [1.0, 1.0, 1.0, 1.0, 1.0],
            "scout_eligible": [True, True, False, True, True],
        },
        geometry=[Point(i, 0) for i in range(5)],
        crs="EPSG:3301",
    )

    remote = remote_high_value_for_species(joined_gdf, "chanterelle", top_n=5)

    assert len(remote) == 1
    assert remote.iloc[0]["ecotone_score"] == pytest.approx(0.9)
    assert remote.iloc[0]["exclusion_reason"] == REMOTE_EXCLUSION_REASON


def test_remote_high_value_for_species_caps_at_top_n():
    joined_gdf = gpd.GeoDataFrame(
        {
            "ecotone_score_chanterelle": [3.0, 2.0, 1.0],
            "access_modifier": [0.0, 0.0, 0.0],
            "fruiting_modifier_chanterelle": [1.0, 1.0, 1.0],
            "scout_eligible": [False, False, False],
        },
        geometry=[Point(i, 0) for i in range(3)],
        crs="EPSG:3301",
    )

    remote = remote_high_value_for_species(joined_gdf, "chanterelle", top_n=2)

    assert len(remote) == 2
    assert list(remote["ecotone_score"]) == [3.0, 2.0]


def test_remote_high_value_for_species_reports_missing_fruiting_data_reason():
    joined_gdf = gpd.GeoDataFrame(
        {
            "ecotone_score_chanterelle": [1.5, 1.2],
            "access_modifier": [0.8, 0.9],
            "fruiting_modifier_chanterelle": [0.7, None],
            "scout_eligible": [True, True],
        },
        geometry=[Point(0, 0), Point(1, 0)],
        crs="EPSG:3301",
    )

    remote = remote_high_value_for_species(joined_gdf, "chanterelle", top_n=5)

    assert len(remote) == 1
    assert remote.iloc[0]["exclusion_reason"] == "MISSING_FRUITING_DATA"
    assert remote.iloc[0]["ecotone_score"] == pytest.approx(1.2)


def test_remote_high_value_for_species_access_ineligibility_takes_precedence():
    joined_gdf = gpd.GeoDataFrame(
        {
            "ecotone_score_chanterelle": [1.5],
            "access_modifier": [0.0],
            "fruiting_modifier_chanterelle": [None],  # both problems apply at once
            "scout_eligible": [False],
        },
        geometry=[Point(0, 0)],
        crs="EPSG:3301",
    )

    remote = remote_high_value_for_species(joined_gdf, "chanterelle", top_n=5)

    assert len(remote) == 1
    assert remote.iloc[0]["exclusion_reason"] == REMOTE_EXCLUSION_REASON


def test_scout_candidates_for_species_macrocluster_ranks_within_bucket():
    bucket_gdf = gpd.GeoDataFrame(
        {
            "id_a": [1, 3, 5],
            "id_b": [2, 4, 6],
            "ecotone_score_chanterelle": [1.5, 1.2, 0.9],
            "access_modifier": [0.8, 0.5, 0.9],
            "fruiting_modifier_chanterelle": [1.0, 1.0, 1.0],
            "scout_eligible": [True, True, True],
        },
        geometry=[Point(0, 0), Point(2000, 0), Point(4000, 0)],
        crs="EPSG:3301",
    )

    ranked, suppressed = scout_candidates_for_species_macrocluster(
        bucket_gdf,
        "chanterelle",
        top_n=10,
        min_separation_m=400.0,
        max_suppressed_examples=3,
    )

    assert len(ranked) == 3
    # scout_score = ecotone_score * access_modifier * fruiting_modifier:
    # row0: 1.5*0.8*1.0=1.2, row1: 1.2*0.5*1.0=0.6, row2: 0.9*0.9*1.0=0.81 -- sorted desc.
    assert [round(v, 4) for v in ranked["scout_score"]] == [1.2, 0.81, 0.6]
    assert len(suppressed) == 0
    assert (ranked["nearby_suppressed_count"] == 0).all()


def test_scout_candidates_for_species_macrocluster_caps_ranked_at_top_n():
    bucket_gdf = gpd.GeoDataFrame(
        {
            "id_a": [1, 3, 5],
            "id_b": [2, 4, 6],
            "ecotone_score_chanterelle": [3.0, 2.0, 1.0],
            "access_modifier": [1.0, 1.0, 1.0],
            "fruiting_modifier_chanterelle": [1.0, 1.0, 1.0],
            "scout_eligible": [True, True, True],
        },
        geometry=[Point(0, 0), Point(2000, 0), Point(4000, 0)],
        crs="EPSG:3301",
    )

    ranked, suppressed = scout_candidates_for_species_macrocluster(
        bucket_gdf,
        "chanterelle",
        top_n=2,
        min_separation_m=400.0,
        max_suppressed_examples=3,
    )

    assert len(ranked) == 2
    assert list(ranked["scout_score"]) == [3.0, 2.0]


def test_scout_candidates_for_species_macrocluster_suppresses_and_reports_nearby_aggregates():
    # Two candidates close together (100m apart, well under min_separation_m=400) --
    # the lower-scored one gets suppressed, and the retained one reports the
    # aggregate columns.
    bucket_gdf = gpd.GeoDataFrame(
        {
            "id_a": [1, 3],
            "id_b": [2, 4],
            "ecotone_score_chanterelle": [1.5, 1.3],
            "access_modifier": [0.8, 0.8],
            "fruiting_modifier_chanterelle": [1.0, 1.0],
            "scout_eligible": [True, True],
        },
        geometry=[Point(0, 0), Point(100, 0)],
        crs="EPSG:3301",
    )

    ranked, suppressed = scout_candidates_for_species_macrocluster(
        bucket_gdf,
        "chanterelle",
        top_n=10,
        min_separation_m=400.0,
        max_suppressed_examples=3,
    )

    assert len(ranked) == 1
    assert ranked.iloc[0]["nearby_suppressed_count"] == 1
    assert ranked.iloc[0]["nearby_best_suppressed_score"] == pytest.approx(1.3 * 0.8)
    assert len(suppressed) == 1
    assert suppressed.iloc[0]["suppressed_by_id"] == "1_2"


def test_scout_candidates_for_species_macrocluster_caps_exported_suppressed_examples():
    # 4 candidates all within 400m of the top-scoring one -- all 3 lower-scored ones
    # get suppressed, but only max_suppressed_examples=2 of them are exported as rows,
    # while nearby_suppressed_count still reports the true total of 3.
    bucket_gdf = gpd.GeoDataFrame(
        {
            "id_a": [1, 3, 5, 7],
            "id_b": [2, 4, 6, 8],
            "ecotone_score_chanterelle": [2.0, 1.8, 1.6, 1.4],
            "access_modifier": [1.0, 1.0, 1.0, 1.0],
            "fruiting_modifier_chanterelle": [1.0, 1.0, 1.0, 1.0],
            "scout_eligible": [True, True, True, True],
        },
        geometry=[Point(0, 0), Point(50, 0), Point(100, 0), Point(150, 0)],
        crs="EPSG:3301",
    )

    ranked, suppressed = scout_candidates_for_species_macrocluster(
        bucket_gdf,
        "chanterelle",
        top_n=10,
        min_separation_m=400.0,
        max_suppressed_examples=2,
    )

    assert len(ranked) == 1
    assert ranked.iloc[0]["nearby_suppressed_count"] == 3
    assert len(suppressed) == 2
    assert list(suppressed["scout_score"]) == [1.8, 1.6]  # best 2 of the 3 suppressed


def test_scout_candidates_for_species_macrocluster_ineligible_rows_never_enter_suppression():
    # An access-ineligible candidate (scout_score is None) must never suppress a real,
    # reachable one, and must never itself appear in ranked or suppressed.
    bucket_gdf = gpd.GeoDataFrame(
        {
            "id_a": [1, 3],
            "id_b": [2, 4],
            "ecotone_score_chanterelle": [5.0, 1.0],  # row 0 scores very high but is ineligible
            "access_modifier": [0.0, 0.8],
            "fruiting_modifier_chanterelle": [1.0, 1.0],
            "scout_eligible": [False, True],
        },
        geometry=[Point(0, 0), Point(50, 0)],  # 50m apart -- would suppress if eligible
        crs="EPSG:3301",
    )

    ranked, suppressed = scout_candidates_for_species_macrocluster(
        bucket_gdf,
        "chanterelle",
        top_n=10,
        min_separation_m=400.0,
        max_suppressed_examples=3,
    )

    assert len(ranked) == 1
    assert ranked.iloc[0]["id_a"] == 3
    assert len(suppressed) == 0


def test_weather_coverage_ratio_computes_fraction_with_fruiting_data():
    joined_gdf = gpd.GeoDataFrame(
        {
            "ecotone_score_chanterelle": [1.0, 1.0, 1.0, None],
            "access_modifier": [0.5, 0.5, 0.5, 0.5],
            "fruiting_modifier_chanterelle": [0.5, None, 0.5, 0.5],
            "scout_eligible": [True, True, False, True],
        },
        geometry=[Point(i, 0) for i in range(4)],
        crs="EPSG:3301",
    )
    # Eligible pool (non-null ecotone_score AND scout_eligible): rows 0, 1 (row 2 is
    # ecologically scored but access-ineligible; row 3 has no ecotone_score at all).
    # Of that pool of 2, row 0 has fruiting data, row 1 doesn't -> ratio 0.5.
    ratio = weather_coverage_ratio(joined_gdf, "chanterelle")
    assert ratio == pytest.approx(0.5)


def test_weather_coverage_ratio_is_one_when_no_eligible_candidates_exist():
    joined_gdf = gpd.GeoDataFrame(
        {
            "ecotone_score_chanterelle": [None],
            "access_modifier": [0.5],
            "fruiting_modifier_chanterelle": [None],
            "scout_eligible": [False],
        },
        geometry=[Point(0, 0)],
        crs="EPSG:3301",
    )
    # No candidates are even eligible to begin with — vacuously "fully covered",
    # not a coverage problem to report.
    assert weather_coverage_ratio(joined_gdf, "chanterelle") == pytest.approx(1.0)


def test_suppress_nearby_candidates_suppresses_a_close_lower_scored_candidate():
    scored_gdf = gpd.GeoDataFrame(
        {"id_a": [1, 3], "id_b": [2, 4], "scout_score": [1.0, 0.9]},
        geometry=[Point(0, 0), Point(100, 0)],
        crs="EPSG:3301",
    )

    retained, suppressed = suppress_nearby_candidates(scored_gdf, min_separation_m=400.0)

    assert len(retained) == 1
    assert retained.iloc[0]["id_a"] == 1
    assert len(suppressed) == 1
    assert suppressed.iloc[0]["suppressed_by_id"] == "1_2"
    assert suppressed.iloc[0]["suppression_distance_m"] == pytest.approx(100.0)
    assert suppressed.iloc[0]["pre_suppression_rank"] == 2


def test_suppress_nearby_candidates_retains_candidates_beyond_threshold():
    scored_gdf = gpd.GeoDataFrame(
        {"id_a": [1, 3], "id_b": [2, 4], "scout_score": [1.0, 0.9]},
        geometry=[Point(0, 0), Point(1000, 0)],
        crs="EPSG:3301",
    )

    retained, suppressed = suppress_nearby_candidates(scored_gdf, min_separation_m=400.0)

    assert len(retained) == 2
    assert len(suppressed) == 0


def test_suppress_nearby_candidates_checks_against_currently_retained_set_only():
    # Point B (score 0.9) is close to A (score 1.0) and gets suppressed by A. Point C
    # (score 0.5) is far from A but would be close to B if B had been retained --
    # greedy NMS must check against the RETAINED set, not every prior candidate, so C
    # is correctly retained (its nearest RETAINED neighbor is A, at 1000m away).
    scored_gdf = gpd.GeoDataFrame(
        {"id_a": [1, 3, 5], "id_b": [2, 4, 6], "scout_score": [1.0, 0.9, 0.5]},
        geometry=[Point(0, 0), Point(100, 0), Point(1000, 0)],
        crs="EPSG:3301",
    )

    retained, suppressed = suppress_nearby_candidates(scored_gdf, min_separation_m=400.0)

    assert sorted(retained["id_a"]) == [1, 5]
    assert list(suppressed["id_a"]) == [3]
    assert suppressed.iloc[0]["suppressed_by_id"] == "1_2"
    assert suppressed.iloc[0]["pre_suppression_rank"] == 2


def test_suppress_nearby_candidates_handles_empty_input():
    scored_gdf = gpd.GeoDataFrame(
        {"id_a": [], "id_b": [], "scout_score": []}, geometry=[], crs="EPSG:3301"
    )

    retained, suppressed = suppress_nearby_candidates(scored_gdf, min_separation_m=400.0)

    assert len(retained) == 0
    assert len(suppressed) == 0
    assert "suppressed_by_id" in suppressed.columns


def _naive_suppress_nearby_candidates(scored_gdf, min_separation_m):
    """Reference implementation: the exact O(n*k) linear-scan algorithm
    suppress_nearby_candidates used before it was optimized to a spatial-grid lookup.
    Kept ONLY in this test file (never imported by production code) as a brute-force
    oracle to prove the optimized implementation produces byte-for-byte identical
    output on a large, randomized, realistic-density input -- the small hand-picked
    tests above exercise correctness on simple cases, this one exercises it at a scale
    close to what real macrocluster buckets look like."""
    if len(scored_gdf) == 0:
        empty = scored_gdf.copy()
        empty["suppressed_by_id"] = pd.Series(dtype=object)
        empty["suppression_distance_m"] = pd.Series(dtype=float)
        empty["pre_suppression_rank"] = pd.Series(dtype="Int64")
        return scored_gdf.copy(), empty

    centroids = scored_gdf.geometry.centroid
    retained_idx = []
    retained_centroids = []
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


def test_suppress_nearby_candidates_matches_naive_reference_at_realistic_scale():
    # 400 candidates scattered over a ~6km x 6km area (density chosen so a real
    # fraction of pairs fall within MIN_SCOUT_SEPARATION_M=400m of each other, forcing
    # real suppression decisions across many grid cells and cell boundaries -- not just
    # the handful of hand-picked points the other tests use).
    rng = random.Random(20260822)
    n = 400
    xs = [rng.uniform(500_000, 506_000) for _ in range(n)]
    ys = [rng.uniform(6_500_000, 6_506_000) for _ in range(n)]
    scores = [rng.uniform(0.0, 1.0) for _ in range(n)]

    scored_gdf = gpd.GeoDataFrame(
        {
            "id_a": list(range(1, n + 1)),
            "id_b": list(range(1001, 1001 + n)),
            "scout_score": scores,
        },
        geometry=[Point(x, y) for x, y in zip(xs, ys)],
        crs="EPSG:3301",
    ).sort_values("scout_score", ascending=False)

    fast_retained, fast_suppressed = suppress_nearby_candidates(scored_gdf, min_separation_m=400.0)
    naive_retained, naive_suppressed = _naive_suppress_nearby_candidates(
        scored_gdf, min_separation_m=400.0
    )

    assert sorted(fast_retained.index) == sorted(naive_retained.index)
    assert sorted(fast_suppressed.index) == sorted(naive_suppressed.index)
    # Real suppression must actually be happening in this scenario -- if this fires 0,
    # the density/area parameters above stopped exercising the interesting case.
    assert len(fast_suppressed) > 0

    fast_by_idx = fast_suppressed.set_index(fast_suppressed.index)
    naive_by_idx = naive_suppressed.set_index(naive_suppressed.index)
    for idx in fast_by_idx.index:
        assert fast_by_idx.loc[idx, "suppressed_by_id"] == naive_by_idx.loc[idx, "suppressed_by_id"]
        assert fast_by_idx.loc[idx, "suppression_distance_m"] == pytest.approx(
            naive_by_idx.loc[idx, "suppression_distance_m"]
        )
        assert fast_by_idx.loc[idx, "pre_suppression_rank"] == naive_by_idx.loc[idx, "pre_suppression_rank"]
