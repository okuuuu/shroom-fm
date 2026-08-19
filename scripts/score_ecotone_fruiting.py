from pathlib import Path

import geopandas as gpd

from shroom_fm.fruiting import join_ecotone_fruiting

ECOTONES_PATH = Path("data/ecotones.geojson")
WEATHER_PATH = Path("data/weather_eraldis.geojson")


def main() -> None:
    ecotones_gdf = gpd.read_file(ECOTONES_PATH)
    weather_gdf = gpd.read_file(WEATHER_PATH)

    if not any(col.startswith("fruiting_score_") for col in weather_gdf.columns):
        raise RuntimeError(
            f"{WEATHER_PATH} has no fruiting_score_* columns — "
            "run scripts/score_fruiting.py first."
        )

    scored = join_ecotone_fruiting(ecotones_gdf, weather_gdf)
    scored.to_file(ECOTONES_PATH, driver="GeoJSON")

    print(f"{len(scored)} ecotone pairs fruiting-scored, saved to {ECOTONES_PATH}")


if __name__ == "__main__":
    main()
