import pytest

from shroom_fm.config import load_home_location


def test_load_home_location_reads_lat_lon(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text("home_lat = 59.437\nhome_lon = 24.7536\n")

    lat, lon = load_home_location(config_file)

    assert lat == 59.437
    assert lon == 24.7536


def test_load_home_location_missing_file_raises_clear_error(tmp_path):
    missing_path = tmp_path / "config.toml"

    with pytest.raises(FileNotFoundError, match="config.example.toml"):
        load_home_location(missing_path)
