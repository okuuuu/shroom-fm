from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd

from shroom_fm.fruiting import score_stands

WEATHER_PATH = Path("data/weather_eraldis.geojson")


def main() -> None:
    weather_gdf = gpd.read_file(WEATHER_PATH)

    if "rain_0_3d_mm" not in weather_gdf.columns:
        raise RuntimeError(
            f"{WEATHER_PATH} has no rain_0_3d_mm column — "
            "run scripts/refresh_weather.py first."
        )

    now = datetime.now(timezone.utc)
    scored = score_stands(weather_gdf, now)
    scored.to_file(WEATHER_PATH, driver="GeoJSON")

    print(f"{len(scored)} stands fruiting-scored, saved to {WEATHER_PATH}")


if __name__ == "__main__":
    main()
