from pathlib import Path

from shroom_fm.config import load_home_location
from shroom_fm.eraldis import WGS84_CRS
from shroom_fm.roads import (
    BARRIER_TYPENAME,
    ROAD_TYPENAME,
    classify_car_class,
    exclude_barrier_blocked_segments,
    fetch_layer_annulus,
)
from shroom_fm.wfs import ETAK_WFS_URL

RADIUS_KM = 38.0
INNER_RADIUS_KM = 18.0
ROADS_OUTPUT_PATH = Path("data/roads.geojson")
BARRIERS_OUTPUT_PATH = Path("data/barriers.geojson")


def main() -> None:
    home_lat, home_lon = load_home_location()

    roads = fetch_layer_annulus(
        ETAK_WFS_URL, ROAD_TYPENAME, home_lat, home_lon, RADIUS_KM, INNER_RADIUS_KM
    )
    roads["car_class"] = [
        classify_car_class(tyyp_tekst, teekate_tekst)
        for tyyp_tekst, teekate_tekst in zip(roads["tyyp_tekst"], roads["teekate_tekst"])
    ]

    barriers = fetch_layer_annulus(
        ETAK_WFS_URL, BARRIER_TYPENAME, home_lat, home_lon, RADIUS_KM, INNER_RADIUS_KM
    )

    roads = exclude_barrier_blocked_segments(roads, barriers)
    roads = roads.to_crs(WGS84_CRS)
    barriers = barriers.to_crs(WGS84_CRS)

    ROADS_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    roads.to_file(ROADS_OUTPUT_PATH, driver="GeoJSON")
    barriers.to_file(BARRIERS_OUTPUT_PATH, driver="GeoJSON")

    print(f"{len(roads)} road segments saved to {ROADS_OUTPUT_PATH}")
    print(f"{len(barriers)} barriers saved to {BARRIERS_OUTPUT_PATH}")


if __name__ == "__main__":
    main()
