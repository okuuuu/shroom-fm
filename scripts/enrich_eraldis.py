from pathlib import Path

import geopandas as gpd

from shroom_fm.enrich import enrich_eraldis
from shroom_fm.wfs import fetch_capabilities

DATA_PATH = Path("data/eraldis.geojson")


def main() -> None:
    gdf = gpd.read_file(DATA_PATH)
    wfs = fetch_capabilities()

    enriched = enrich_eraldis(gdf, wfs)
    enriched.to_file(DATA_PATH, driver="GeoJSON")

    print(f"Enriched {len(enriched)} stands, saved to {DATA_PATH}")


if __name__ == "__main__":
    main()
