from pathlib import Path

from shroom_fm.config import load_home_location
from shroom_fm.eraldis import compute_bbox, fetch_eraldis_bbox, filter_within_radius
from shroom_fm.wfs import fetch_capabilities

RADIUS_KM = 20.0
INNER_RADIUS_KM = 0.0
OUTPUT_PATH = Path("data/eraldis.geojson")


def main() -> None:
    home_lat, home_lon = load_home_location()
    wfs = fetch_capabilities()

    bbox = compute_bbox(home_lat, home_lon, RADIUS_KM)
    gdf = fetch_eraldis_bbox(wfs, bbox)
    nearby = filter_within_radius(gdf, home_lat, home_lon, RADIUS_KM, INNER_RADIUS_KM)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    nearby.to_file(OUTPUT_PATH, driver="GeoJSON")

    if INNER_RADIUS_KM > 0:
        print(f"{len(nearby)} stands within {INNER_RADIUS_KM:.0f}-{RADIUS_KM:.0f}km of home")
    else:
        print(f"{len(nearby)} stands within {RADIUS_KM:.0f}km of home")
    print(f"Saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
