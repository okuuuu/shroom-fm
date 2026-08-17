from pathlib import Path

import geopandas as gpd

from shroom_fm.habitat import score_ecotone_habitat

ECOTONES_PATH = Path("data/ecotones.geojson")
ERALDIS_PATH = Path("data/eraldis.geojson")


def main() -> None:
    ecotones_gdf = gpd.read_file(ECOTONES_PATH)
    eraldis_gdf = gpd.read_file(ERALDIS_PATH)

    if not any(col.startswith("stand_habitat_score_") for col in eraldis_gdf.columns):
        raise RuntimeError(
            f"{ERALDIS_PATH} has no stand_habitat_score_* columns — "
            "run scripts/score_habitat.py first."
        )

    scored = score_ecotone_habitat(ecotones_gdf, eraldis_gdf)
    scored.to_file(ECOTONES_PATH, driver="GeoJSON")

    print(f"{len(scored)} ecotone pairs habitat-scored, saved to {ECOTONES_PATH}")


if __name__ == "__main__":
    main()
