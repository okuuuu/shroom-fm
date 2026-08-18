from pathlib import Path

from shroom_fm.config import load_home_location
from shroom_fm.eraldis import ESTONIAN_GRID_CRS, WGS84_CRS, compute_bbox, filter_within_radius
from shroom_fm.roads import (
    BARRIER_TYPENAME,
    ROAD_TYPENAME,
    classify_car_class,
    exclude_barrier_blocked_segments,
    fetch_layer_bbox,
)
from shroom_fm.wfs import ETAK_WFS_URL

RADIUS_KM = 38.0
INNER_RADIUS_KM = 18.0
ROADS_OUTPUT_PATH = Path("data/roads.geojson")
BARRIERS_OUTPUT_PATH = Path("data/barriers.geojson")


def main() -> None:
    home_lat, home_lon = load_home_location()
    bbox = compute_bbox(home_lat, home_lon, RADIUS_KM)

    roads = fetch_layer_bbox(ETAK_WFS_URL, ROAD_TYPENAME, bbox)
    roads = filter_within_radius(roads, home_lat, home_lon, RADIUS_KM, INNER_RADIUS_KM)
    roads["car_class"] = [
        classify_car_class(tyyp_tekst, teekate_tekst)
        for tyyp_tekst, teekate_tekst in zip(roads["tyyp_tekst"], roads["teekate_tekst"])
    ]

    barriers = fetch_layer_bbox(ETAK_WFS_URL, BARRIER_TYPENAME, bbox)
    barriers = filter_within_radius(barriers, home_lat, home_lon, RADIUS_KM, INNER_RADIUS_KM)

    roads_projected = roads.to_crs(ESTONIAN_GRID_CRS)
    barriers_projected = barriers.to_crs(ESTONIAN_GRID_CRS)
    roads_projected = exclude_barrier_blocked_segments(roads_projected, barriers_projected)
    roads = roads_projected.to_crs(WGS84_CRS)
    barriers = barriers_projected.to_crs(WGS84_CRS)

    ROADS_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    roads.to_file(ROADS_OUTPUT_PATH, driver="GeoJSON")
    barriers.to_file(BARRIERS_OUTPUT_PATH, driver="GeoJSON")

    print(f"{len(roads)} road segments saved to {ROADS_OUTPUT_PATH}")
    print(f"{len(barriers)} barriers saved to {BARRIERS_OUTPUT_PATH}")


if __name__ == "__main__":
    main()
