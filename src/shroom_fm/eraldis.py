import math

KM_PER_DEGREE_LAT = 111.32
BBOX_PADDING_FACTOR = 1.1


def compute_bbox(
    lat: float, lon: float, radius_km: float
) -> tuple[float, float, float, float]:
    padded_radius_km = radius_km * BBOX_PADDING_FACTOR
    delta_lat = padded_radius_km / KM_PER_DEGREE_LAT
    delta_lon = padded_radius_km / (KM_PER_DEGREE_LAT * math.cos(math.radians(lat)))
    return (lon - delta_lon, lat - delta_lat, lon + delta_lon, lat + delta_lat)
