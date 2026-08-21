import os
import re
import time
from collections import deque
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree

import geopandas as gpd
import h5py
import numpy as np
import pyproj
import requests

OPERA_S3_BASE_URL = "https://s3.waw3-1.cloudferro.com/"
OPERA_S3_BUCKET = "openradar-24h"
# Genuinely historical, anonymously-accessible archive (distinct bucket from the rolling
# ~24h OPERA_S3_BUCKET above) — confirmed live 2026-08-21, reaching back to at least
# 2020-01-15, well beyond this project's 14-day window. See the "Correction
# (2026-08-21, post-completion)" section of
# docs/superpowers/plans/2026-08-21-opera-rest-backfill-findings.md: an earlier
# investigation of the anonymous ORD REST API alone (not this bucket) found no
# historical access and concluded (at the time, correctly) that no backfill was
# possible; this bucket was found afterward via a separate route listing and directly
# confirmed anonymously accessible with the same list/download pattern as the recent
# bucket.
OPERA_ARCHIVE_S3_BUCKET = "openradar-archive"
_S3_NAMESPACE = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
_HDF5_SIGNATURE = b"\x89HDF\r\n\x1a\n"
_KEY_TIMESTAMP_RE = re.compile(r"@(\d{8}T\d{4})@0@RATE\.h5$")


def _validate_coverage(value: float, *, label: str) -> float:
    """Coverage is a fraction of expected observations actually present — it can never
    exceed 1.0 or fall below 0.0. A violation is a real bug (see cached_radar_files'
    docstring for the confirmed historical example), never something to silently accept
    or clip away without noticing."""
    assert 0.0 <= value <= 1.0, (
        f"coverage[{label}]={value!r} violates the 0.0<=coverage<=1.0 invariant — "
        "this indicates a real counting/boundary bug, not expected jitter"
    )
    return value


def _list_radar_objects(bucket: str, prefix_date: date) -> list[dict]:
    """Lists real RATE.h5 objects for prefix_date in the given bucket, via the
    confirmed-working anonymous S3 ?list-type=2 listing (no signing, no boto3, no API
    key). Shared by both OPERA_S3_BUCKET (rolling ~24h) and OPERA_ARCHIVE_S3_BUCKET
    (genuine multi-year history) — both use the same key format and the same anonymous
    access pattern, confirmed live for both 2026-08-21. Returns [] — not an error — for
    a date the bucket has no data for (e.g. a rolled-off recent date): confirmed real S3
    behavior is a valid, empty KeyCount=0 response, not an HTTP error.

    Deliberately does not follow S3 pagination (NextContinuationToken): a single-day,
    single-product-type prefix like this one returns at most a few hundred keys (15-min
    cadence), far under the 1000-key max-keys page size used here, so truncation isn't a
    real concern for this call shape even though the archive bucket does support
    pagination for broader prefixes (confirmed live, not needed here)."""
    prefix = f"{prefix_date:%Y/%m/%d}/OPERA/COMP/"
    url = f"{OPERA_S3_BASE_URL}{bucket}/?list-type=2&prefix={prefix}&max-keys=1000"
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    root = ElementTree.fromstring(response.text)

    objects = []
    for content in root.findall("s3:Contents", _S3_NAMESPACE):
        key = content.find("s3:Key", _S3_NAMESPACE).text
        match = _KEY_TIMESTAMP_RE.search(key)
        if match is None:
            continue
        timestamp = datetime.strptime(match.group(1), "%Y%m%dT%H%M").replace(
            tzinfo=timezone.utc
        )
        objects.append({"key": key, "timestamp": timestamp})
    return objects


def list_recent_radar_objects(prefix_date: date) -> list[dict]:
    """Lists RATE.h5 objects for prefix_date from the rolling ~24h OPERA_S3_BUCKET."""
    return _list_radar_objects(OPERA_S3_BUCKET, prefix_date)


def list_archived_radar_objects(prefix_date: date) -> list[dict]:
    """Lists RATE.h5 objects for prefix_date from the genuinely historical
    OPERA_ARCHIVE_S3_BUCKET — see that constant's docstring/comment for provenance."""
    return _list_radar_objects(OPERA_ARCHIVE_S3_BUCKET, prefix_date)


def _opera_cache_filename(timestamp: datetime) -> str:
    return f"{timestamp:%Y%m%dT%H%M%S}Z_RATE.h5"


def cached_radar_timestamp(path: Path) -> datetime:
    stem = path.name.split("_", 1)[0]
    return datetime.strptime(stem, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def _download_radar_object(bucket: str, key: str, cache_dir: Path) -> Path:
    match = _KEY_TIMESTAMP_RE.search(key)
    timestamp = datetime.strptime(match.group(1), "%Y%m%dT%H%M").replace(
        tzinfo=timezone.utc
    )
    # Same cache filename convention regardless of source bucket — both buckets carry
    # identical real data for any timestamp they both cover, so a file already fetched
    # from one bucket correctly counts as a cache hit if later requested from the
    # other, with no re-download or divergence risk.
    path = cache_dir / _opera_cache_filename(timestamp)
    if path.exists():
        return path

    url = f"{OPERA_S3_BASE_URL}{bucket}/{key}"
    response = requests.get(url, timeout=30)
    response.raise_for_status()

    if not response.content.startswith(_HDF5_SIGNATURE):
        raise ValueError(
            f"Downloaded content for {key} is not a valid HDF5 file "
            f"(missing signature) — got {len(response.content)} bytes"
        )

    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".h5.part")
    tmp_path.write_bytes(response.content)
    os.replace(tmp_path, path)
    return path


def download_opera_object(key: str, cache_dir: Path) -> Path:
    """Downloads one object from the rolling ~24h OPERA_S3_BUCKET."""
    return _download_radar_object(OPERA_S3_BUCKET, key, cache_dir)


def download_archived_radar_object(key: str, cache_dir: Path) -> Path:
    """Downloads one object from the genuinely historical OPERA_ARCHIVE_S3_BUCKET."""
    return _download_radar_object(OPERA_ARCHIVE_S3_BUCKET, key, cache_dir)


_S3_RECENT_WINDOW_HOURS = 24


def fetch_new_radar_composites(
    cache_dir: Path, since: datetime, *, now: datetime | None = None
) -> list[Path]:
    """Fetches new OPERA RATE composites into cache_dir, covering [since, now].

    Routes each requested calendar date to whichever bucket serves it: dates within the
    last ~_S3_RECENT_WINDOW_HOURS use the rolling OPERA_S3_BUCKET (Task 2's
    list_recent_radar_objects/download_opera_object); older dates use the genuinely
    historical, anonymously-accessible OPERA_ARCHIVE_S3_BUCKET
    (list_archived_radar_objects/download_archived_radar_object) — confirmed live to
    reach back to at least 2020-01-15, far beyond this project's 14-day window (see the
    "Correction" section of
    docs/superpowers/plans/2026-08-21-opera-rest-backfill-findings.md). Both buckets
    carry identical real data for any date they both cover, so this routing boundary
    isn't correctness-critical — an earlier version of this function raised
    NotImplementedError for anything older than ~24h, based on an anonymous REST API
    investigation that (correctly, at the time) found no historical access; a later
    live test of a separate route/bucket found real anonymous historical access does
    exist, so no NotImplementedError/API-key fallback is needed for this project's
    14-day backfill need.
    """
    now = now or datetime.now(timezone.utc)
    recent_window_start = now - timedelta(hours=_S3_RECENT_WINDOW_HOURS)

    cache_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    current_date = since.date()
    while current_date <= now.date():
        if current_date >= recent_window_start.date():
            objects = list_recent_radar_objects(current_date)
            download = download_opera_object
        else:
            objects = list_archived_radar_objects(current_date)
            download = download_archived_radar_object
        for obj in objects:
            if since <= obj["timestamp"] <= now:
                paths.append(download(obj["key"], cache_dir))
        current_date += timedelta(days=1)
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
    """window_end is EXCLUSIVE — [window_start, window_end) — not inclusive. The prior
    inclusive-inclusive version, combined with real-world publish-cadence jitter, is
    the confirmed exact root cause of a historical coverage > 1.0 bug (a real cache of
    4050 files against an expected_slots_14d of 4032 produced coverage=4050/4032=
    1.0044642857142858, bit-for-bit the value once shipped in production)."""
    if not cache_dir.exists():
        return []
    return sorted(
        (
            p
            for p in cache_dir.glob("*.h5")
            if window_start <= cached_radar_timestamp(p) < window_end
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


def parse_radar_quality(
    path: Path,
    *,
    row_slice: slice = slice(None),
    col_slice: slice = slice(None),
) -> np.ndarray | None:
    """Reads the optional per-pixel quality layer (real confirmed ODIM qualityN-subgroup
    convention under dataset1/data1, e.g. dataset1/data1/quality1/data — NOT identified
    by a quantity="QIND" string, no such string exists in real files) if present,
    decoded via its own gain/offset the same way parse_radar_composite decodes the rain
    value. Returns None — not an error, not a zero-filled array — if the file has no
    quality subgroup at all: pipeline behavior must be identical for a file that lacks
    one (spec Component 3), so callers must treat None as "no enrichment available
    this slot", never as "quality is zero"."""
    with h5py.File(path, "r") as f:
        data1 = f["dataset1/data1"]
        quality_keys = sorted(k for k in data1.keys() if k.startswith("quality"))
        if not quality_keys:
            return None
        quality_grp = data1[quality_keys[0]]
        raw = quality_grp["data"][row_slice, col_slice].astype(np.float64)
        what = quality_grp["what"]
        gain = float(what.attrs.get("gain", 1.0))
        offset = float(what.attrs.get("offset", 0.0))
        return raw * gain + offset


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

RAIN_EVENT_DRY_GAP_H = 6.0
SIGNIFICANT_EVENT_MM = 5.0
STRONG_EVENT_MM = 10.0


def accumulate_rainfall(
    cache_dir: Path,
    now: datetime,
    eraldis_bounds_wgs84: tuple[float, float, float, float],
) -> tuple[gpd.GeoDataFrame, dict[str, float]]:
    window_start = now - timedelta(days=_RADAR_WINDOW_DAYS)
    files = cached_radar_files(cache_dir, window_start, now)

    cutoff_3d = now - timedelta(days=3)
    cutoff_7d = now - timedelta(days=7)
    cutoff_72h = now - timedelta(hours=72)

    expected_slots_14d = (_RADAR_WINDOW_DAYS * 24 * 60) // _RADAR_SLOT_MINUTES
    expected_slots_7d = (7 * 24 * 60) // _RADAR_SLOT_MINUTES
    expected_slots_3d = (3 * 24 * 60) // _RADAR_SLOT_MINUTES

    if not files:
        coverage = {"3d": 0.0, "7d": 0.0, "14d": 0.0}
        empty = gpd.GeoDataFrame(
            {
                "row": [],
                "col": [],
                "rain_3d_mm": [],
                "rain_7d_mm": [],
                "rain_14d_mm": [],
                "hours_since_any_rain": [],
                "wet_hours_72h": [],
                "hours_since_significant_rain": [],
                "hours_since_strong_rain": [],
                "last_significant_event_mm": [],
                "last_strong_event_mm": [],
                "max_24h_rain_14d": [],
            },
            geometry=[],
            crs="EPSG:3301",
        )
        return empty, coverage

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

    event_mm = np.zeros(shape)
    event_last_wet_epoch = np.full(shape, -np.inf)
    last_significant_epoch = np.full(shape, -np.inf)
    last_significant_mm = np.zeros(shape)
    last_strong_epoch = np.full(shape, -np.inf)
    last_strong_mm = np.zeros(shape)

    window_buffer = deque()
    window_sum = np.zeros(shape)
    max_24h_rain = np.zeros(shape)

    slot_hours = _RADAR_SLOT_MINUTES / 60
    count_3d = 0
    count_7d = 0

    rain_event_dry_gap_seconds = RAIN_EVENT_DRY_GAP_H * 3600
    max_24h_seconds = 24 * 3600

    for path in files:
        timestamp = cached_radar_timestamp(path)
        epoch = timestamp.timestamp()
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
            count_7d += 1
        if timestamp >= cutoff_3d:
            rain_3d += mm_this_slot
            count_3d += 1
        wet_mask = np.nan_to_num(rate_mm_h, nan=-1.0) > 0.0
        last_wet_epoch = np.where(wet_mask, epoch, last_wet_epoch)
        if timestamp >= cutoff_72h:
            wet_slots_72h += wet_mask.astype(int)

        # Event-based significant/strong rain tracking: a run of wet slots with no
        # gap exceeding RAIN_EVENT_DRY_GAP_H between consecutive wet slots is one
        # event. Re-evaluated on every wet slot (not just the crossing slot), so
        # once event_mm first reaches a threshold, every later wet slot of the SAME
        # event keeps advancing that threshold's timestamp through to the event's
        # actual end, not freezing at the instant of crossing.
        gap_exceeded = wet_mask & (
            (epoch - event_last_wet_epoch) > rain_event_dry_gap_seconds
        )
        event_mm = np.where(gap_exceeded, 0.0, event_mm)
        event_mm = np.where(wet_mask, event_mm + mm_this_slot, event_mm)
        event_last_wet_epoch = np.where(wet_mask, epoch, event_last_wet_epoch)

        newly_significant = wet_mask & (event_mm >= SIGNIFICANT_EVENT_MM)
        last_significant_epoch = np.where(
            newly_significant, epoch, last_significant_epoch
        )
        last_significant_mm = np.where(
            newly_significant, event_mm, last_significant_mm
        )

        newly_strong = wet_mask & (event_mm >= STRONG_EVENT_MM)
        last_strong_epoch = np.where(newly_strong, epoch, last_strong_epoch)
        last_strong_mm = np.where(newly_strong, event_mm, last_strong_mm)

        # Rolling 24h max: maintain a sliding sum of the trailing 24h of slots.
        window_buffer.append((epoch, mm_this_slot))
        window_sum = window_sum + mm_this_slot
        while window_buffer and (epoch - window_buffer[0][0]) > max_24h_seconds:
            _, old_mm = window_buffer.popleft()
            window_sum = window_sum - old_mm
        max_24h_rain = np.maximum(max_24h_rain, window_sum)

    coverage = {
        "3d": count_3d / expected_slots_3d if expected_slots_3d else 0.0,
        "7d": count_7d / expected_slots_7d if expected_slots_7d else 0.0,
        "14d": len(files) / expected_slots_14d if expected_slots_14d else 0.0,
    }

    hours_since_any_rain = np.where(
        last_wet_epoch == -np.inf,
        np.nan,
        (now.timestamp() - last_wet_epoch) / 3600,
    )
    hours_since_significant_rain = np.where(
        last_significant_epoch == -np.inf,
        np.nan,
        (now.timestamp() - last_significant_epoch) / 3600,
    )
    hours_since_strong_rain = np.where(
        last_strong_epoch == -np.inf,
        np.nan,
        (now.timestamp() - last_strong_epoch) / 3600,
    )
    wet_hours_72h = wet_slots_72h * slot_hours

    points = radar_pixel_centers(georef)
    points["rain_3d_mm"] = rain_3d.ravel()
    points["rain_7d_mm"] = rain_7d.ravel()
    points["rain_14d_mm"] = rain_14d.ravel()
    points["hours_since_any_rain"] = hours_since_any_rain.ravel()
    points["wet_hours_72h"] = wet_hours_72h.ravel()
    points["hours_since_significant_rain"] = hours_since_significant_rain.ravel()
    points["hours_since_strong_rain"] = hours_since_strong_rain.ravel()
    points["last_significant_event_mm"] = last_significant_mm.ravel()
    points["last_strong_event_mm"] = last_strong_mm.ravel()
    points["max_24h_rain_14d"] = max_24h_rain.ravel()
    points = points.to_crs("EPSG:3301")
    return points, coverage
