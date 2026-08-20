import geopandas as gpd
import pytest
from shapely.geometry import Point, box

from shroom_fm.forest_block import (
    MACROCLUSTER_TARGET_EXTENT_M,
    compute_forest_blocks,
    geometry_extent_m,
)


def test_geometry_extent_m_of_a_square():
    square = box(0, 0, 100, 100)
    assert geometry_extent_m(square) == pytest.approx(141.4213562373095)


def test_geometry_extent_m_of_a_point_is_zero():
    assert geometry_extent_m(Point(0, 0)) == 0.0


def test_geometry_extent_m_of_two_collinear_points():
    line = box(0, 0, 100, 0.0000001).convex_hull  # degenerately thin, effectively a line
    # Use an actual LineString to be explicit about the degenerate hull case:
    from shapely.geometry import LineString

    assert geometry_extent_m(LineString([(0, 0), (100, 0)]).convex_hull) == pytest.approx(100.0)


def test_compute_forest_blocks_merges_touching_trio():
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1, 2, 3]},
        geometry=[box(0, 0, 100, 100), box(100, 0, 200, 100), box(200, 0, 300, 100)],
        crs="EPSG:3301",
    )
    adjacency_gdf = gpd.GeoDataFrame(
        {"id_a": [1, 2], "id_b": [2, 3]},
        geometry=[Point(100, 50), Point(200, 50)],
        crs="EPSG:3301",
    )

    eraldis_result, blocks_gdf = compute_forest_blocks(eraldis_gdf, adjacency_gdf)

    assert len(blocks_gdf) == 1
    assert blocks_gdf.iloc[0]["eraldis_count"] == 3
    assert eraldis_result["forest_block_id"].nunique() == 1


def test_compute_forest_blocks_keeps_disconnected_pair_separate():
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1, 2]},
        geometry=[box(0, 0, 100, 100), box(100_000, 0, 100_100, 100)],
        crs="EPSG:3301",
    )
    adjacency_gdf = gpd.GeoDataFrame({"id_a": [], "id_b": []}, geometry=[], crs="EPSG:3301")

    eraldis_result, blocks_gdf = compute_forest_blocks(eraldis_gdf, adjacency_gdf)

    assert len(blocks_gdf) == 2
    assert list(blocks_gdf["eraldis_count"]) == [1, 1]
    assert eraldis_result["forest_block_id"].nunique() == 2


def test_compute_forest_blocks_isolated_stand_is_its_own_singleton_block():
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1]}, geometry=[box(0, 0, 100, 100)], crs="EPSG:3301"
    )
    adjacency_gdf = gpd.GeoDataFrame({"id_a": [], "id_b": []}, geometry=[], crs="EPSG:3301")

    eraldis_result, blocks_gdf = compute_forest_blocks(eraldis_gdf, adjacency_gdf)

    assert len(blocks_gdf) == 1
    assert blocks_gdf.iloc[0]["eraldis_count"] == 1
    assert eraldis_result.iloc[0]["forest_block_id"] == blocks_gdf.iloc[0]["forest_block_id"]


def test_compute_forest_blocks_flags_oversized_block():
    side = MACROCLUSTER_TARGET_EXTENT_M + 1000
    big_square = box(0, 0, side, side)
    eraldis_gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[big_square], crs="EPSG:3301")
    adjacency_gdf = gpd.GeoDataFrame({"id_a": [], "id_b": []}, geometry=[], crs="EPSG:3301")

    _, blocks_gdf = compute_forest_blocks(eraldis_gdf, adjacency_gdf)

    assert blocks_gdf.iloc[0]["oversized_block"] == True


def test_compute_forest_blocks_not_oversized_when_under_threshold():
    small_square = box(0, 0, 100, 100)
    eraldis_gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[small_square], crs="EPSG:3301")
    adjacency_gdf = gpd.GeoDataFrame({"id_a": [], "id_b": []}, geometry=[], crs="EPSG:3301")

    _, blocks_gdf = compute_forest_blocks(eraldis_gdf, adjacency_gdf)

    assert blocks_gdf.iloc[0]["oversized_block"] == False
