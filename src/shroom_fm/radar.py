import os
import re
from collections import deque
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree

import geopandas as gpd
import h5py
import numpy as np
import pyproj

from shroom_fm.retry import get_with_retry

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


def _timestamp_from_key(key: str) -> datetime:
    """Parses the OPERA RATE.h5 timestamp embedded in an S3 object key. Shared by both
    the listing and download paths so the match-and-parse logic only lives in one
    place. Raises a clear ValueError — not a bare AttributeError from a `None` regex
    match — if key doesn't match the expected `...@YYYYMMDDTHHMM@0@RATE.h5` shape."""
    match = _KEY_TIMESTAMP_RE.search(key)
    if match is None:
        raise ValueError(f"Key does not match expected OPERA RATE.h5 pattern: {key!r}")
    return datetime.strptime(match.group(1), "%Y%m%dT%H%M").replace(tzinfo=timezone.utc)


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
    # get_with_retry (shroom_fm.retry, a generic HTTP-retry helper already used by
    # wfs.py/enrich.py/concurrent_fetch.py) gives this real ~1,344-request backfill
    # transient-network resilience the original bare requests.get here lacked — it
    # also calls raise_for_status() internally, so no separate call is needed here.
    response = get_with_retry(url, timeout=30)
    root = ElementTree.fromstring(response.text)

    objects = []
    for content in root.findall("s3:Contents", _S3_NAMESPACE):
        key = content.find("s3:Key", _S3_NAMESPACE).text
        try:
            timestamp = _timestamp_from_key(key)
        except ValueError:
            # Not a RATE.h5 key (e.g. a sibling ACRR/DBZH product listed in the same
            # prefix) — skip it, not an error.
            continue
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
    timestamp = _timestamp_from_key(key)
    # Same cache filename convention regardless of source bucket — both buckets carry
    # identical real data for any timestamp they both cover, so a file already fetched
    # from one bucket correctly counts as a cache hit if later requested from the
    # other, with no re-download or divergence risk.
    path = cache_dir / _opera_cache_filename(timestamp)
    if path.exists():
        return path

    url = f"{OPERA_S3_BASE_URL}{bucket}/{key}"
    # get_with_retry — see _list_radar_objects for why (transient-network resilience
    # over a real ~1,344-request cold backfill); it already calls raise_for_status().
    response = get_with_retry(url, timeout=30)

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
        # Sequential downloads by deliberate choice for this migration (simplicity
        # over speed for an on-demand script, ~14min for a real 14-day cold backfill)
        # — not an oversight. KAIA-era code used a 3-worker ThreadPoolExecutor here;
        # re-adding concurrency for OPERA is a deliberate, ruled-on deferral.
        for obj in objects:
            if since <= obj["timestamp"] <= now:
                paths.append(download(obj["key"], cache_dir))
        current_date += timedelta(days=1)
    return paths


def expire_old_radar_composites(cache_dir: Path, cutoff: datetime) -> None:
    if not cache_dir.exists():
        return
    # Glob specifically for *_RATE.h5 (the real _opera_cache_filename suffix), not the
    # generic *.h5, so a leftover file from a different product/source (e.g. a stray
    # pre-migration KAIA-era cache file, an ACRR file, a DBZH file) is never picked up
    # by construction — see CLAUDE.md's "Weather refresh" section for why this matters
    # for a real production checkout's cache directory.
    for path in cache_dir.glob("*_RATE.h5"):
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
    # Glob specifically for *_RATE.h5 — see expire_old_radar_composites' comment above
    # for why: this makes any leftover non-OPERA-RATE file (KAIA-era, ACRR, DBZH, ...)
    # invisible to this function by construction.
    return sorted(
        (
            p
            for p in cache_dir.glob("*_RATE.h5")
            if window_start <= cached_radar_timestamp(p) < window_end
        ),
        key=cached_radar_timestamp,
    )


def newest_cached_radar_timestamp(cache_dir: Path) -> datetime | None:
    if not cache_dir.exists():
        return None
    # Glob specifically for *_RATE.h5 — see expire_old_radar_composites' comment above.
    files = list(cache_dir.glob("*_RATE.h5"))
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
    country-plus grid (real OPERA grid is 1900x2200 — untrimmed processing of the
    ~1,344 files cached for a 14-day window at 15-minute cadence would be far slower
    than necessary)."""
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
        # from disk, not the full 1900x2200 array.
        raw = f["dataset1/data1/data"][row_slice, col_slice]
        # Real confirmed OPERA structure (verified 2026-08-21 against live-downloaded
        # openradar-archive files): gain/offset/nodata/undetect/quantity live under
        # dataset1/data1/what, not dataset1/what (which holds only dataset-level
        # metadata — startdate/enddate/product/prodname — no decode attrs at all).
        what = dict(f["dataset1/data1/what"].attrs)
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
_RADAR_SLOT_MINUTES = 15

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
                "coverage_3d": [],
                "coverage_7d": [],
                "coverage_14d": [],
                "quality_mean": [],
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

    # Per-pixel valid-observation counters (nodata excluded, undetect+real included) —
    # this is what makes coverage genuinely spatial rather than a single national
    # file-count ratio.
    valid_slots_3d = np.zeros(shape, dtype=int)
    valid_slots_7d = np.zeros(shape, dtype=int)
    valid_slots_14d = np.zeros(shape, dtype=int)

    event_mm = np.zeros(shape)
    event_last_wet_epoch = np.full(shape, -np.inf)
    last_significant_epoch = np.full(shape, -np.inf)
    last_significant_mm = np.zeros(shape)
    last_strong_epoch = np.full(shape, -np.inf)
    last_strong_mm = np.zeros(shape)

    # Optional quality enrichment (spec Component 3) — summed/counted only over the
    # files that actually carried a quality1 subgroup, so a mix of quality-bearing and
    # quality-less cached files still produces an honest mean, not a value silently
    # diluted by files that had no quality data at all.
    quality_sum = np.zeros(shape)
    quality_count = np.zeros(shape, dtype=int)

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
        # A pixel is a "valid observation" (counts toward coverage) whenever
        # parse_radar_composite did NOT decode it to NaN — i.e. nodata is excluded,
        # but undetect (a confirmed-dry reading) and any real rate value both count.
        pixel_valid = ~np.isnan(rate_mm_h)

        quality = parse_radar_quality(path, row_slice=row_slice, col_slice=col_slice)
        if quality is not None:
            quality_sum += quality
            quality_count += 1

        mm_this_slot = np.nan_to_num(rate_mm_h, nan=0.0) * slot_hours
        rain_14d += mm_this_slot
        valid_slots_14d += pixel_valid.astype(int)
        if timestamp >= cutoff_7d:
            rain_7d += mm_this_slot
            valid_slots_7d += pixel_valid.astype(int)
            count_7d += 1
        if timestamp >= cutoff_3d:
            rain_3d += mm_this_slot
            valid_slots_3d += pixel_valid.astype(int)
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
        "3d": _validate_coverage(
            count_3d / expected_slots_3d if expected_slots_3d else 0.0, label="3d"
        ),
        "7d": _validate_coverage(
            count_7d / expected_slots_7d if expected_slots_7d else 0.0, label="7d"
        ),
        "14d": _validate_coverage(
            len(files) / expected_slots_14d if expected_slots_14d else 0.0, label="14d"
        ),
    }

    coverage_3d_px = np.clip(
        valid_slots_3d / expected_slots_3d if expected_slots_3d else np.zeros(shape),
        0.0,
        1.0,
    )
    coverage_7d_px = np.clip(
        valid_slots_7d / expected_slots_7d if expected_slots_7d else np.zeros(shape),
        0.0,
        1.0,
    )
    coverage_14d_px = np.clip(
        valid_slots_14d / expected_slots_14d if expected_slots_14d else np.zeros(shape),
        0.0,
        1.0,
    )

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
    points["coverage_3d"] = coverage_3d_px.ravel()
    points["coverage_7d"] = coverage_7d_px.ravel()
    points["coverage_14d"] = coverage_14d_px.ravel()
    quality_mean = np.where(
        quality_count > 0, quality_sum / np.maximum(quality_count, 1), np.nan
    )
    points["quality_mean"] = quality_mean.ravel()
    points = points.to_crs("EPSG:3301")
    return points, coverage


# Just over half the diagonal of one real OPERA pixel (2000m x 2000m in its native
# LAEA CRS; real cached files' own points, once averaged over their row/col index
# range in EPSG:3301, show ~1956-2440m effective spacing depending on axis/rotation —
# see assign_radar_to_eraldis's docstring). For an axis-aligned square ~2000m cell, a
# stand centroid at the worst-case position (a cell corner) is at most half that cell's
# diagonal — ~1414m — from the cell's own center point, and 1500m keeps a margin over
# that bound. This is an empirically sufficient margin, not a mathematically exhaustive
# guarantee for an arbitrarily rotated/non-square grid cell: the observed effective
# spacing varies by axis (~1956-2440m), so a real cell's true worst-case corner
# distance could in principle exceed 1500m along a long/rotated axis, meaning this
# fallback could rarely miss a genuinely-containing pixel. That failure mode is safe,
# not silently wrong — a miss returns None (see below), it never fabricates or borrows
# a value the way an unbounded nearest-join would. In practice, the real 262,054-stand
# backfill (2026-08-21) achieved 100% completion at this radius, but that one real run
# is evidence the edge case wasn't hit that time, not proof it can never occur for
# every possible grid rotation.
_ASSIGN_FALLBACK_RADIUS_M = 1500.0


def assign_radar_to_eraldis(
    eraldis_gdf: "gpd.GeoDataFrame",
    radar_points: "gpd.GeoDataFrame",
    columns: tuple[str, ...],
) -> "pd.DataFrame":
    """For each eraldis stand, averages `columns` over every radar pixel whose point the
    stand's own (unbuffered) bounding box actually contains — a direct coordinate-range
    lookup, NOT gpd.sjoin/sjoin_nearest — used as-is for large stands (or any stand
    whose bbox already spans one or more pixel points).

    Real eraldis stands are typically ~100-300m across, far smaller than OPERA's 2km
    pixel spacing, so for most stands that direct bbox query finds nothing at all: the
    stand's own tiny bounding box essentially never itself contains one of the fixed,
    regularly-spaced pixel-center points. This was a real production bug (not caught by
    unit tests, whose fixtures all happened to center stand geometry exactly on a pixel
    point) found via Task 9's real 262,054-stand backfill on 2026-08-21:
    weather_data_coverage came back with mean 0.007 and only 1,865 'complete' stands
    (~0.7%), matching almost exactly the geometric probability that a small,
    effectively-randomly-positioned stand bbox happens to straddle a 2km-spaced grid
    point. For that empty-bbox case only, this falls back to a single-nearest-point
    lookup bounded to _ASSIGN_FALLBACK_RADIUS_M (~1500m, just over half of one real
    pixel's diagonal for an axis-aligned square cell — see that constant's docstring
    for the empirically-sufficient-but-not-mathematically-exhaustive caveat) of the
    stand's own centroid — a bounded, fixed-radius fallback, not gpd.sjoin_nearest and
    not an unbounded match: at that radius it is expected to reach only the stand's own
    true containing pixel, never a neighboring one, nowhere near the tens-of-km
    unbounded matches the original KAIA-era sjoin_nearest bug produced. A rare miss
    under an unusually rotated/non-square cell fails safe — the stand simply gets None,
    the same as the far-outside-grid case (still covered by
    test_assign_radar_to_eraldis_returns_none_when_stand_is_far_outside_the_grid, a
    stand 500km away that must still resolve to None) — never a fabricated or borrowed
    value. An earlier version of this fix
    tried to infer the grid's true pixel spacing from the reprojected point coordinates
    themselves and buffer the bbox query outward by half of it; this looked
    mathematically sound but was empirically wrong in two different ways in a row on
    real data (nearest-neighbor min-diff over all points' x/y values is corrupted by
    reprojection-induced cross-row jitter down to millimeters; the row/col-index-range
    average that replaced it overshoots true spacing under grid rotation and, more
    importantly, silently required a 'row'/'col' index column real callers always
    provide but some existing tests' simpler fixtures do not) — replaced with this
    fixed-radius nearest-point fallback specifically to depend on nothing but the
    already-reprojected point geometry and stand geometry, not on grid metadata that
    varies with the caller's fixture.

    A pixel with a NaN value for a column (zero valid observations in that pixel's
    window) is excluded from the mean, never averaged in as if it were a real value. A
    stand with zero valid pixels found (by either lookup) gets None for every column —
    never a value borrowed from outside the stand's own footprint, at any distance."""
    import pandas as pd

    if radar_points.empty:
        return pd.DataFrame(
            {col: [None] * len(eraldis_gdf) for col in columns},
            index=eraldis_gdf.index,
        )

    records = []
    for geom in eraldis_gdf.geometry:
        minx, miny, maxx, maxy = geom.bounds
        candidates = radar_points.cx[minx:maxx, miny:maxy]
        if candidates.empty:
            centroid = geom.centroid
            prefilter = radar_points.cx[
                centroid.x
                - _ASSIGN_FALLBACK_RADIUS_M : centroid.x
                + _ASSIGN_FALLBACK_RADIUS_M,
                centroid.y
                - _ASSIGN_FALLBACK_RADIUS_M : centroid.y
                + _ASSIGN_FALLBACK_RADIUS_M,
            ]
            if not prefilter.empty:
                distances = prefilter.geometry.distance(centroid)
                nearest_idx = distances.idxmin()
                if distances.loc[nearest_idx] <= _ASSIGN_FALLBACK_RADIUS_M:
                    candidates = prefilter.loc[[nearest_idx]]
        record = {}
        for col in columns:
            # NOTE: dropna() here collapses two different real meanings of NaN into
            # one — "no qualifying event ever" (a real, meaningful NaN for columns
            # like hours_since_any_rain/hours_since_significant_rain) is treated the
            # same as "invalid observation". Currently harmless since real eraldis
            # stands rarely span multiple 2km pixels (so there's rarely more than one
            # candidate value to average over in the first place), but worth flagging
            # for a future reader/caller that might rely on averaging over many pixels.
            valid_values = candidates[col].dropna()
            record[col] = float(valid_values.mean()) if len(valid_values) else None
        records.append(record)

    return pd.DataFrame(records, index=eraldis_gdf.index, columns=list(columns))
