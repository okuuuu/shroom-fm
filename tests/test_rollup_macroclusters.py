import geopandas as gpd
import pytest
from shapely.geometry import Point

from scripts.rollup_macroclusters import _validate_columns


def test_validate_columns_passes_when_all_expected_columns_present(tmp_path):
    gdf = gpd.GeoDataFrame(
        {"id": [1, 2], "macrocluster_id": [10, 20]},
        geometry=[Point(0, 0), Point(1, 1)],
        crs="EPSG:4326",
    )
    path = tmp_path / "fixture.geojson"
    gdf.to_file(path, driver="GeoJSON")

    # Should not raise.
    _validate_columns(path, ["id", "macrocluster_id"])


def test_validate_columns_raises_value_error_naming_missing_column(tmp_path):
    gdf = gpd.GeoDataFrame(
        {"id": [1, 2], "macrocluster_id": [10, 20]},
        geometry=[Point(0, 0), Point(1, 1)],
        crs="EPSG:4326",
    )
    path = tmp_path / "fixture.geojson"
    gdf.to_file(path, driver="GeoJSON")

    with pytest.raises(ValueError, match="renamed_away_column"):
        _validate_columns(path, ["id", "macrocluster_id", "renamed_away_column"])
