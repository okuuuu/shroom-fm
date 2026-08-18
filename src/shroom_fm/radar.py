import concurrent.futures
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import h5py
import numpy as np
import pyproj

from shroom_fm.retry import get_with_retry, post_with_retry

KAIA_QUERY_URL = "https://avaandmed.keskkonnaportaal.ee/api/lists/active/items/query"
KAIA_DOWNLOAD_URL_TEMPLATE = (
    "https://avaandmed.keskkonnaportaal.ee/api/lists/active/items/{id}/files/{file_id}"
)
RADAR_CONTENT_TYPE = "0102FB01"
RADAR_PHENOMENON = "COMP"
MAX_WORKERS = 6
_PAGE_SIZE = 2000


def query_radar_documents(since: datetime) -> list[dict]:
    documents: list[dict] = []
    bookmark = None
    while True:
        body = {
            "filter": {
                "and": {
                    "children": [
                        {"underContentType": {"contentType": RADAR_CONTENT_TYPE}},
                        {"isEqual": {"field": "Phenomenon", "value": RADAR_PHENOMENON}},
                        {
                            "greaterThanOrEqual": {
                                "field": "Timestamp",
                                "value": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            }
                        },
                    ]
                }
            },
            "pageSize": _PAGE_SIZE,
            "includeFileMetadata": True,
            "fields": ["Timestamp"],
        }
        if bookmark is not None:
            body["bookmark"] = bookmark
        response = post_with_retry(KAIA_QUERY_URL, json=body, timeout=30)
        data = response.json()
        for doc in data["documents"]:
            documents.append(
                {
                    "id": doc["id"],
                    "file_id": doc["fileMetadata"][0]["id"],
                    "timestamp": datetime.fromisoformat(
                        doc["metadata"]["Timestamp"]
                    ).astimezone(timezone.utc),
                }
            )
        if not data["documents"] or data.get("nextBookmark") is None:
            break
        bookmark = data.get("nextBookmark")
    return documents


def _cache_filename(document: dict) -> str:
    return f"{document['timestamp']:%Y%m%dT%H%M%SZ}_{document['id']}.h5"


def cached_radar_timestamp(path: Path) -> datetime:
    stem = path.name.split("_", 1)[0]
    return datetime.strptime(stem, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def download_radar_composite(document: dict, cache_dir: Path) -> Path:
    path = cache_dir / _cache_filename(document)
    if path.exists():
        return path
    url = KAIA_DOWNLOAD_URL_TEMPLATE.format(
        id=document["id"], file_id=document["file_id"]
    )
    response = get_with_retry(url, timeout=30)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path.write_bytes(response.content)
    return path


def fetch_new_radar_composites(
    cache_dir: Path, since: datetime, *, max_workers: int = MAX_WORKERS
) -> list[Path]:
    documents = query_radar_documents(since)
    cache_dir.mkdir(parents=True, exist_ok=True)
    if not documents:
        return []
    paths: list[Path | None] = [None] * len(documents)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_index = {
            executor.submit(download_radar_composite, doc, cache_dir): i
            for i, doc in enumerate(documents)
        }
        done = 0
        for future in concurrent.futures.as_completed(future_to_index):
            index = future_to_index[future]
            paths[index] = future.result()
            done += 1
            print(f"  downloaded {done}/{len(documents)} radar composites")
    return paths


def expire_old_radar_composites(cache_dir: Path, cutoff: datetime) -> None:
    if not cache_dir.exists():
        return
    for path in cache_dir.glob("*.h5"):
        if cached_radar_timestamp(path) < cutoff:
            path.unlink()


def cached_radar_files(
    cache_dir: Path, window_start: datetime, window_end: datetime
) -> list[Path]:
    if not cache_dir.exists():
        return []
    return sorted(
        (
            p
            for p in cache_dir.glob("*.h5")
            if window_start <= cached_radar_timestamp(p) <= window_end
        ),
        key=cached_radar_timestamp,
    )


def newest_cached_radar_timestamp(cache_dir: Path) -> datetime | None:
    if not cache_dir.exists():
        return None
    files = list(cache_dir.glob("*.h5"))
    if not files:
        return None
    return max(cached_radar_timestamp(p) for p in files)


def read_radar_full_georef(path: Path) -> dict:
    """Reads only the /where attrs (not the raster itself) — cheap, used to compute the
    bbox slice before any per-file raster data is read."""
    with h5py.File(path, "r") as f:
        where = dict(f["where"].attrs)

    def _decode(value):
        return value.decode() if isinstance(value, bytes) else value

    return {
        "projdef": _decode(where["projdef"]),
        "xsize": int(where["xsize"]),
        "ysize": int(where["ysize"]),
        "xscale": float(where["xscale"]),
        "yscale": float(where["yscale"]),
        "ul_lon": float(where["UL_lon"]),
        "ul_lat": float(where["UL_lat"]),
        "row_offset": 0,
        "col_offset": 0,
    }


def _radar_origin(georef: dict) -> tuple[float, float, pyproj.CRS]:
    radar_crs = pyproj.CRS.from_proj4(georef["projdef"])
    to_radar = pyproj.Transformer.from_crs("EPSG:4326", radar_crs, always_xy=True)
    x0, y0 = to_radar.transform(georef["ul_lon"], georef["ul_lat"])
    return x0, y0, radar_crs


def radar_bbox_slice(
    georef: dict,
    bounds_wgs84: tuple[float, float, float, float],
    *,
    buffer_pixels: int = 5,
) -> tuple[slice, slice]:
    """Row/col slice covering bounds_wgs84 (min_lon, min_lat, max_lon, max_lat) within
    the full radar grid described by georef, so per-file processing only touches the
    small sub-region relevant to this project's home area instead of the whole
    country-plus grid (real grid is 1500x1500 — untrimmed processing of ~4000 cached
    files would be far slower than necessary)."""
    x0, y0, radar_crs = _radar_origin(georef)
    to_radar = pyproj.Transformer.from_crs("EPSG:4326", radar_crs, always_xy=True)
    min_lon, min_lat, max_lon, max_lat = bounds_wgs84
    corner_lons = [min_lon, max_lon, min_lon, max_lon]
    corner_lats = [min_lat, min_lat, max_lat, max_lat]
    xs, ys = to_radar.transform(corner_lons, corner_lats)

    cols = [(x - x0) / georef["xscale"] for x in xs]
    rows = [(y0 - y) / georef["yscale"] for y in ys]

    col_start = max(0, int(min(cols)) - buffer_pixels)
    col_stop = min(georef["xsize"], int(max(cols)) + buffer_pixels + 1)
    row_start = max(0, int(min(rows)) - buffer_pixels)
    row_stop = min(georef["ysize"], int(max(rows)) + buffer_pixels + 1)

    return slice(row_start, row_stop), slice(col_start, col_stop)


def parse_radar_composite(
    path: Path,
    *,
    row_slice: slice = slice(None),
    col_slice: slice = slice(None),
) -> tuple[np.ndarray, dict]:
    with h5py.File(path, "r") as f:
        # h5py slices at the dataset level — only the requested sub-region is read
        # from disk, not the full 1500x1500 array.
        raw = f["dataset1/data1/data"][row_slice, col_slice]
        what = dict(f["dataset1/what"].attrs)
        where = dict(f["where"].attrs)
    gain = float(what["gain"])
    offset = float(what["offset"])
    nodata = float(what["nodata"])
    undetect = float(what["undetect"])
    valid = (raw != nodata) & (raw != undetect)
    rate_mm_h = np.where(
        valid, raw.astype(np.float64) * gain + offset, np.nan
    )
    # undetect itself means "radar active, no rain" — a real, valid 0.0 reading
    rate_mm_h = np.where(raw == undetect, 0.0, rate_mm_h)

    def _decode(value):
        return value.decode() if isinstance(value, bytes) else value

    full_ysize = int(where["ysize"])
    full_xsize = int(where["xsize"])
    row_start, row_stop, _ = row_slice.indices(full_ysize)
    col_start, col_stop, _ = col_slice.indices(full_xsize)
    georef = {
        "projdef": _decode(where["projdef"]),
        "xsize": col_stop - col_start,
        "ysize": row_stop - row_start,
        "xscale": float(where["xscale"]),
        "yscale": float(where["yscale"]),
        "ul_lon": float(where["UL_lon"]),
        "ul_lat": float(where["UL_lat"]),
        "row_offset": row_start,
        "col_offset": col_start,
    }
    return rate_mm_h, georef


def radar_pixel_centers(georef: dict) -> gpd.GeoDataFrame:
    x0, y0, radar_crs = _radar_origin(georef)
    row_offset = georef.get("row_offset", 0)
    col_offset = georef.get("col_offset", 0)
    cols = np.arange(georef["xsize"]) + col_offset
    rows = np.arange(georef["ysize"]) + row_offset
    xs = x0 + (cols + 0.5) * georef["xscale"]
    ys = y0 - (rows + 0.5) * georef["yscale"]
    xx, yy = np.meshgrid(xs, ys)
    return gpd.GeoDataFrame(
        {
            "row": np.repeat(rows, georef["xsize"]),
            "col": np.tile(cols, georef["ysize"]),
        },
        geometry=gpd.points_from_xy(xx.ravel(), yy.ravel()),
        crs=radar_crs,
    )


_RADAR_WINDOW_DAYS = 14
_RADAR_SLOT_MINUTES = 5


def accumulate_rainfall(
    cache_dir: Path,
    now: datetime,
    eraldis_bounds_wgs84: tuple[float, float, float, float],
) -> tuple[gpd.GeoDataFrame, float]:
    from datetime import timedelta

    window_start = now - timedelta(days=_RADAR_WINDOW_DAYS)
    files = cached_radar_files(cache_dir, window_start, now)

    expected_slots = (_RADAR_WINDOW_DAYS * 24 * 60) // _RADAR_SLOT_MINUTES
    coverage = len(files) / expected_slots if expected_slots else 0.0

    if not files:
        empty = gpd.GeoDataFrame(
            {
                "row": [],
                "col": [],
                "rain_3d_mm": [],
                "rain_7d_mm": [],
                "rain_14d_mm": [],
                "hours_since_rain": [],
                "wet_hours_72h": [],
            },
            geometry=[],
            crs="EPSG:3301",
        )
        return empty, coverage

    # Determine the full grid's georeferencing from the first file's attrs only (cheap
    # — does not read the raster itself), then compute the small sub-region slice
    # covering the eraldis bbox once, and reuse that same slice for every file in the
    # window so each per-file read only touches the relevant sub-region.
    full_georef = read_radar_full_georef(files[0])
    row_slice, col_slice = radar_bbox_slice(full_georef, eraldis_bounds_wgs84)

    _, georef = parse_radar_composite(
        files[0], row_slice=row_slice, col_slice=col_slice
    )
    shape = (georef["ysize"], georef["xsize"])

    rain_3d = np.zeros(shape)
    rain_7d = np.zeros(shape)
    rain_14d = np.zeros(shape)
    last_wet_epoch = np.full(shape, -np.inf)
    wet_slots_72h = np.zeros(shape, dtype=int)

    cutoff_3d = now - timedelta(days=3)
    cutoff_7d = now - timedelta(days=7)
    cutoff_72h = now - timedelta(hours=72)
    slot_hours = _RADAR_SLOT_MINUTES / 60

    for path in files:
        timestamp = cached_radar_timestamp(path)
        rate_mm_h, file_georef = parse_radar_composite(
            path, row_slice=row_slice, col_slice=col_slice
        )
        if (file_georef["xsize"], file_georef["ysize"]) != (
            georef["xsize"],
            georef["ysize"],
        ):
            raise ValueError(
                f"{path} has a different grid shape than the first cached file — "
                "radar product geometry is expected to be stable"
            )
        mm_this_slot = np.nan_to_num(rate_mm_h, nan=0.0) * slot_hours
        rain_14d += mm_this_slot
        if timestamp >= cutoff_7d:
            rain_7d += mm_this_slot
        if timestamp >= cutoff_3d:
            rain_3d += mm_this_slot
        wet_mask = np.nan_to_num(rate_mm_h, nan=-1.0) > 0.0
        last_wet_epoch = np.where(wet_mask, timestamp.timestamp(), last_wet_epoch)
        if timestamp >= cutoff_72h:
            wet_slots_72h += wet_mask.astype(int)

    hours_since_rain = np.where(
        last_wet_epoch == -np.inf,
        np.nan,
        (now.timestamp() - last_wet_epoch) / 3600,
    )
    wet_hours_72h = wet_slots_72h * slot_hours

    points = radar_pixel_centers(georef)
    points["rain_3d_mm"] = rain_3d.ravel()
    points["rain_7d_mm"] = rain_7d.ravel()
    points["rain_14d_mm"] = rain_14d.ravel()
    points["hours_since_rain"] = hours_since_rain.ravel()
    points["wet_hours_72h"] = wet_hours_72h.ravel()
    points = points.to_crs("EPSG:3301")
    return points, coverage
