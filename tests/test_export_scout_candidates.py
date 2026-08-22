import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

from scripts.export_scout_candidates import (
    OUTPUT_COLUMNS,
    build_scout_candidate_rows,
    finalize_export,
)
from shroom_fm.eraldis import ESTONIAN_GRID_CRS
from shroom_fm.habitat import TARGET_SPECIES


def _fill_other_species(joined_gdf: gpd.GeoDataFrame, primary_species: str) -> gpd.GeoDataFrame:
    """build_scout_candidate_rows loops over every species passed to it, and
    weather_coverage_ratio/scout_candidates_for_species_macrocluster need
    ecotone_score_*/fruiting_modifier_* for whichever species they're called with --
    fill the non-primary species with a neutral, fully-covered value so a
    single-species test doesn't need to fabricate all 5."""
    result = joined_gdf.copy()
    n = len(result)
    for species in TARGET_SPECIES:
        if species == primary_species:
            continue
        result[f"ecotone_score_{species}"] = [1.0] * n
        result[f"fruiting_modifier_{species}"] = [0.5] * n
    return result


def test_finalize_export_reprojects_to_wgs84():
    # Regression test for a real production bug: build_scout_candidate_rows()
    # intentionally returns geometry in ESTONIAN_GRID_CRS (EPSG:3301) -- metric
    # coordinates are required for suppress_nearby_candidates' distance math -- but
    # main() wrote that CRS straight to data/scout_candidates.geojson with no
    # reprojection back to WGS84, unlike every other geometry-bearing output file in
    # this pipeline (ecotones.geojson, macroclusters.geojson, macrocluster_state.geojson
    # all stay EPSG:4326 on disk). A real point near home in EPSG:3301 (~500000, 6500000)
    # must come back as real Estonian lon/lat (~20-30, ~55-62), not raw grid meters.
    combined = gpd.GeoDataFrame(
        {"species": ["chanterelle"]},
        geometry=[Point(500000, 6500000)],
        crs=ESTONIAN_GRID_CRS,
    )

    result = finalize_export(combined)

    assert result.crs.to_string() == "EPSG:4326"
    point = result.geometry.iloc[0]
    assert 20 < point.x < 30
    assert 55 < point.y < 62


def test_build_scout_candidate_rows_ranks_each_macrocluster_independently():
    # Direct regression test for the bug this design fixes: under the OLD global
    # top-N ranking, a macrocluster far from "home" with genuinely strong local
    # candidates got ZERO ranked rows because a nearer macrocluster's candidates
    # dominated the single global cut. Two macroclusters here each have one eligible,
    # well-separated candidate for chanterelle -- both must appear in 'ranked'.
    joined_gdf = gpd.GeoDataFrame(
        {
            "id_a": [1, 3],
            "id_b": [2, 4],
            "macrocluster_id": [10, 20],
            "ecotone_score_chanterelle": [1.5, 0.9],
            "access_modifier": [0.8, 0.8],
            "fruiting_modifier_chanterelle": [1.0, 1.0],
            "scout_eligible": [True, True],
        },
        geometry=[Point(0, 0), Point(50000, 50000)],
        crs="EPSG:3301",
    )
    joined_gdf = _fill_other_species(joined_gdf, "chanterelle")

    result = build_scout_candidate_rows(joined_gdf, ["chanterelle"])

    ranked = result[(result["species"] == "chanterelle") & (result["tier"] == "ranked")]
    assert set(ranked["macrocluster_id"]) == {10, 20}
    assert (ranked["rank_macrocluster"] == 1).all()


def test_build_scout_candidate_rows_returns_none_when_nothing_publishable():
    # A single row with access_modifier=0.0/scout_eligible=False is NOT enough to make
    # this "nothing publishable" -- remote_high_value_for_species deliberately surfaces
    # exactly that kind of row (ecologically scored but access-ineligible) as a real
    # remote_high_value candidate; that's the tier's whole purpose. Genuinely nothing
    # publishable requires zero ecological score data at all: ecotone_score_chanterelle
    # itself must be null, so _compute_scored's initial notna() filter empties out
    # both the ranked and remote_high_value pools before either tier ever runs.
    joined_gdf = gpd.GeoDataFrame(
        {
            "id_a": [1],
            "id_b": [2],
            "macrocluster_id": [10],
            "ecotone_score_chanterelle": [None],
            "access_modifier": [0.0],
            "fruiting_modifier_chanterelle": [None],
            "scout_eligible": [False],
        },
        geometry=[Point(0, 0)],
        crs="EPSG:3301",
    )
    joined_gdf = _fill_other_species(joined_gdf, "chanterelle")

    result = build_scout_candidate_rows(joined_gdf, ["chanterelle"])

    assert result is None


def test_build_scout_candidate_rows_output_has_expected_columns():
    joined_gdf = gpd.GeoDataFrame(
        {
            "id_a": [1],
            "id_b": [2],
            "macrocluster_id": [10],
            "ecotone_score_chanterelle": [1.5],
            "access_modifier": [0.8],
            "access_confidence": ["HIGH_CONFIDENCE"],
            "access_reason": ["100m from road"],
            "nearest_car_road_m": [100.0],
            "fruiting_modifier_chanterelle": [1.0],
            "scout_eligible": [True],
            "weather_data_quality": ["complete"],
            "weather_data_coverage": [1.0],
            "as_of": [None],
            "transition_length_m": [50.0],
            "dominant_species_a": ["MA"],
            "dominant_species_b": ["KU"],
        },
        geometry=[Point(0, 0)],
        crs="EPSG:3301",
    )
    joined_gdf = _fill_other_species(joined_gdf, "chanterelle")

    result = build_scout_candidate_rows(joined_gdf, ["chanterelle"])

    assert list(result.columns) == OUTPUT_COLUMNS


def test_build_scout_candidate_rows_remote_high_value_stays_global_not_per_macrocluster():
    # Two access-ineligible-but-ecologically-strong candidates in DIFFERENT
    # macroclusters -- remote_high_value must rank them against each other globally
    # (both eligible for the same top-N pool), not split per macrocluster.
    joined_gdf = gpd.GeoDataFrame(
        {
            "id_a": [1, 3],
            "id_b": [2, 4],
            "macrocluster_id": [10, 20],
            "ecotone_score_chanterelle": [2.0, 1.5],
            "access_modifier": [0.0, 0.0],
            "fruiting_modifier_chanterelle": [1.0, 1.0],
            "scout_eligible": [False, False],
        },
        geometry=[Point(0, 0), Point(50000, 50000)],
        crs="EPSG:3301",
    )
    joined_gdf = _fill_other_species(joined_gdf, "chanterelle")

    result = build_scout_candidate_rows(joined_gdf, ["chanterelle"])

    remote = result[
        (result["species"] == "chanterelle") & (result["tier"] == "remote_high_value")
    ]
    assert len(remote) == 2
    assert list(remote.sort_values("rank")["ecotone_score"]) == [2.0, 1.5]
