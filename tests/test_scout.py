import geopandas as gpd
import pytest
from shapely.geometry import Point

from shroom_fm.scout import (
    REMOTE_EXCLUSION_REASON,
    join_ecotone_access,
    scout_candidates_for_species,
    scout_score,
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
    assert scout_score(1.2, 0.5, True) == pytest.approx(0.6)


def test_scout_score_is_none_when_ineligible():
    assert scout_score(1.2, 0.5, False) is None


def test_scout_score_is_none_when_ecotone_score_missing():
    assert scout_score(None, 0.5, True) is None


def test_scout_score_is_none_when_access_modifier_missing():
    assert scout_score(1.0, None, True) is None


def test_scout_candidates_for_species_splits_and_sorts_tiers():
    joined_gdf = gpd.GeoDataFrame(
        {
            "ecotone_score_chanterelle": [1.5, 1.2, 0.9, 0.6, None],
            "access_modifier": [0.8, 0.5, 0.9, 0.1, 0.9],
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
            "scout_eligible": [True, True, True],
        },
        geometry=[Point(i, 0) for i in range(3)],
        crs="EPSG:3301",
    )

    ranked, remote = scout_candidates_for_species(joined_gdf, "chanterelle", top_n=2)

    assert len(ranked) == 2
    assert list(ranked["scout_score"]) == [3.0, 2.0]
    assert len(remote) == 0
