import geopandas as gpd
import pytest
from shapely.geometry import Point

from shroom_fm.scout import (
    REMOTE_EXCLUSION_REASON,
    join_ecotone_access,
    scout_candidates_for_species,
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


def test_scout_candidates_for_species_splits_and_sorts_tiers():
    joined_gdf = gpd.GeoDataFrame(
        {
            "ecotone_score_chanterelle": [1.5, 1.2, 0.9, 0.6, None],
            "access_modifier": [0.8, 0.5, 0.9, 0.1, 0.9],
            # Neutral fruiting_modifier (all 1.0): this test verifies tiering/sorting on
            # ecotone_score x access_modifier alone, unaffected by the fruiting factor.
            "fruiting_modifier_chanterelle": [1.0, 1.0, 1.0, 1.0, 1.0],
            "scout_eligible": [True, True, False, True, True],
        },
        geometry=[Point(i, 0) for i in range(5)],
        crs="EPSG:3301",
    )

    ranked, remote = scout_candidates_for_species(joined_gdf, "chanterelle", top_n=5)

    assert [round(v, 4) for v in ranked["scout_score"]] == [1.2, 0.6, 0.06]
    assert len(remote) == 1
    assert remote.iloc[0]["ecotone_score"] == pytest.approx(0.9)
    assert remote.iloc[0]["exclusion_reason"] == REMOTE_EXCLUSION_REASON


def test_scout_candidates_for_species_caps_each_tier_independently():
    joined_gdf = gpd.GeoDataFrame(
        {
            "ecotone_score_chanterelle": [3.0, 2.0, 1.0],
            "access_modifier": [1.0, 1.0, 1.0],
            # Neutral fruiting_modifier (all 1.0): this test verifies per-tier top_n
            # capping, unaffected by the fruiting factor.
            "fruiting_modifier_chanterelle": [1.0, 1.0, 1.0],
            "scout_eligible": [True, True, True],
        },
        geometry=[Point(i, 0) for i in range(3)],
        crs="EPSG:3301",
    )

    ranked, remote = scout_candidates_for_species(joined_gdf, "chanterelle", top_n=2)

    assert len(ranked) == 2
    assert list(ranked["scout_score"]) == [3.0, 2.0]
    assert len(remote) == 0


def test_scout_score_multiplies_all_three_factors_when_eligible():
    assert scout_score(1.2, 0.5, 0.8, True) == pytest.approx(0.48)


def test_scout_score_is_none_when_fruiting_modifier_missing():
    assert scout_score(1.2, 0.5, None, True) is None


def test_scout_candidates_for_species_reports_missing_fruiting_data_reason():
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

    ranked, remote = scout_candidates_for_species(joined_gdf, "chanterelle", top_n=5)

    assert len(ranked) == 1
    assert len(remote) == 1
    assert remote.iloc[0]["exclusion_reason"] == "MISSING_FRUITING_DATA"
    assert remote.iloc[0]["ecotone_score"] == pytest.approx(1.2)


def test_scout_candidates_for_species_access_ineligibility_takes_precedence_over_missing_fruiting():
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

    ranked, remote = scout_candidates_for_species(joined_gdf, "chanterelle", top_n=5)

    assert len(ranked) == 0
    assert len(remote) == 1
    assert remote.iloc[0]["exclusion_reason"] == REMOTE_EXCLUSION_REASON


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
