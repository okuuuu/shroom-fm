from datetime import datetime, timedelta, timezone
from pathlib import Path

import geopandas as gpd

from shroom_fm.radar import expire_old_radar_composites, fetch_new_radar_composites
from shroom_fm.weather import refresh_weather

RADAR_CACHE_DIR = Path("data/radar_cache")
ERALDIS_INPUT_PATH = Path("data/eraldis.geojson")
OUTPUT_PATH = Path("data/weather_eraldis.geojson")
RADAR_WINDOW_DAYS = 14


def main() -> None:
    now = datetime.now(timezone.utc)

    since = now - timedelta(days=RADAR_WINDOW_DAYS)
    new_files = fetch_new_radar_composites(RADAR_CACHE_DIR, since)
    print(f"{len(new_files)} new radar composites downloaded")

    expire_old_radar_composites(RADAR_CACHE_DIR, now - timedelta(days=RADAR_WINDOW_DAYS))

    eraldis_gdf = gpd.read_file(ERALDIS_INPUT_PATH)
    result = refresh_weather(eraldis_gdf, RADAR_CACHE_DIR, now)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    result.to_file(OUTPUT_PATH, driver="GeoJSON")

    print(f"{len(result)} eraldis stands weather-scored, saved to {OUTPUT_PATH}")
    print(f"quality breakdown: {result['weather_data_quality'].value_counts().to_dict()}")


if __name__ == "__main__":
    main()
