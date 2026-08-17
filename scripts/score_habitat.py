from pathlib import Path

import geopandas as gpd

from shroom_fm.habitat import score_stands

ERALDIS_PATH = Path("data/eraldis.geojson")


def main() -> None:
    eraldis_gdf = gpd.read_file(ERALDIS_PATH)

    scored = score_stands(eraldis_gdf)
    scored.to_file(ERALDIS_PATH, driver="GeoJSON")

    print(f"{len(scored)} stands scored, saved to {ERALDIS_PATH}")


if __name__ == "__main__":
    main()
