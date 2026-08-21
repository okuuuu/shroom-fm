from datetime import date as _date, datetime, timedelta, timezone

import geopandas as gpd
import h5py
import numpy as np
import pytest
import requests

from shroom_fm.radar import (
    accumulate_rainfall,
    cached_radar_files,
    cached_radar_timestamp,
    expire_old_radar_composites,
    fetch_new_radar_composites,
    newest_cached_radar_timestamp,
    parse_radar_composite,
    parse_radar_quality,
    radar_bbox_slice,
    radar_pixel_centers,
    _validate_coverage,
)

from shroom_fm.radar import (
    OPERA_S3_BASE_URL,
    OPERA_S3_BUCKET,
    download_opera_object,
    list_recent_radar_objects,
)

from shroom_fm.radar import (
    OPERA_ARCHIVE_S3_BUCKET,
    download_archived_radar_object,
    list_archived_radar_objects,
)


def _utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def test_opera_s3_constants_match_confirmed_real_endpoint():
    assert OPERA_S3_BASE_URL == "https://s3.waw3-1.cloudferro.com/"
    assert OPERA_S3_BUCKET == "openradar-24h"
    assert OPERA_ARCHIVE_S3_BUCKET == "openradar-archive"


def test_list_recent_radar_objects_parses_real_s3_listing_xml(monkeypatch):
    # Real S3 ListObjectsV2 XML shape, confirmed live 2026-08-21 (trimmed to 2 RATE
    # entries plus a non-RATE entry that must be filtered out).
    fake_xml = """<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
<Name>openradar-24h</Name>
<Prefix>2026/08/21/OPERA/</Prefix>
<IsTruncated>false</IsTruncated>
<Contents>
<Key>2026/08/21/OPERA/COMP/OPERA@20260821T0000@0@ACRR.h5</Key>
<LastModified>2026-08-21T00:10:05.906Z</LastModified>
</Contents>
<Contents>
<Key>2026/08/21/OPERA/COMP/OPERA@20260821T0000@0@RATE.h5</Key>
<LastModified>2026-08-21T00:10:03.186Z</LastModified>
</Contents>
<Contents>
<Key>2026/08/21/OPERA/COMP/OPERA@20260821T0015@0@RATE.h5</Key>
<LastModified>2026-08-21T00:25:03.637Z</LastModified>
</Contents>
</ListBucketResult>"""

    class _FakeResponse:
        text = fake_xml
        status_code = 200

        def raise_for_status(self):
            pass

    monkeypatch.setattr(
        "shroom_fm.radar.requests.get", lambda url, timeout: _FakeResponse()
    )

    objects = list_recent_radar_objects(_date(2026, 8, 21))

    assert len(objects) == 2  # ACRR excluded, only RATE kept
    assert objects[0]["key"] == "2026/08/21/OPERA/COMP/OPERA@20260821T0000@0@RATE.h5"
    assert objects[0]["timestamp"] == _utc(2026, 8, 21, 0, 0)
    assert objects[1]["timestamp"] == _utc(2026, 8, 21, 0, 15)


def test_list_recent_radar_objects_returns_empty_for_rolled_off_date(monkeypatch):
    # Real confirmed S3 behavior for a date outside the 24h rolling window: valid XML,
    # KeyCount=0 — must return [], not raise.
    fake_xml = """<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
<Name>openradar-24h</Name>
<Prefix>2026/08/19/OPERA/</Prefix>
<IsTruncated>false</IsTruncated>
<KeyCount>0</KeyCount>
</ListBucketResult>"""

    class _FakeResponse:
        text = fake_xml
        status_code = 200

        def raise_for_status(self):
            pass

    monkeypatch.setattr(
        "shroom_fm.radar.requests.get", lambda url, timeout: _FakeResponse()
    )

    objects = list_recent_radar_objects(_date(2026, 8, 19))

    assert objects == []


def test_download_opera_object_writes_cache_file_from_real_filename(tmp_path, monkeypatch):
    class _FakeResponse:
        content = b"\x89HDF\r\n\x1a\n" + b"bytes"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(
        "shroom_fm.radar.requests.get", lambda url, timeout: _FakeResponse()
    )

    cache_dir = tmp_path / "radar_cache"
    result = download_opera_object(
        "2026/08/21/OPERA/COMP/OPERA@20260821T0015@0@RATE.h5", cache_dir
    )

    assert result.exists()
    assert cached_radar_timestamp(result) == _utc(2026, 8, 21, 0, 15)


def test_download_opera_object_skips_if_already_cached(tmp_path, monkeypatch):
    cache_dir = tmp_path / "radar_cache"
    cache_dir.mkdir()

    calls = []

    def _fake_get(url, timeout):
        calls.append(url)
        raise AssertionError("should not be called — file already cached")

    # Pre-create the expected cache file using the real naming convention
    expected_path = cache_dir / "20260821T001500Z_RATE.h5"
    expected_path.write_bytes(b"\x89HDF\r\n\x1a\n" + b"already here")

    monkeypatch.setattr("shroom_fm.radar.requests.get", _fake_get)

    result = download_opera_object(
        "2026/08/21/OPERA/COMP/OPERA@20260821T0015@0@RATE.h5", cache_dir
    )

    assert result == expected_path
    assert calls == []


def test_cached_radar_timestamp_parses_filename(tmp_path):
    path = tmp_path / "20260818T090500Z_43.h5"
    assert cached_radar_timestamp(path) == _utc(2026, 8, 18, 9, 5, 0)


def test_expire_old_radar_composites_removes_only_stale_files(tmp_path):
    cache_dir = tmp_path / "radar_cache"
    cache_dir.mkdir()
    fresh = cache_dir / "20260818T090000Z_1.h5"
    stale = cache_dir / "20260101T000000Z_2.h5"
    fresh.write_bytes(b"x")
    stale.write_bytes(b"x")

    expire_old_radar_composites(cache_dir, cutoff=_utc(2026, 8, 4))

    assert fresh.exists()
    assert not stale.exists()


def test_cached_radar_files_filters_to_window(tmp_path):
    cache_dir = tmp_path / "radar_cache"
    cache_dir.mkdir()
    in_window = cache_dir / "20260818T090000Z_1.h5"
    before_window = cache_dir / "20260801T000000Z_2.h5"
    in_window.write_bytes(b"x")
    before_window.write_bytes(b"x")

    result = cached_radar_files(cache_dir, _utc(2026, 8, 15), _utc(2026, 8, 19))

    assert result == [in_window]


def test_newest_cached_radar_timestamp_returns_none_for_empty_cache(tmp_path):
    assert newest_cached_radar_timestamp(tmp_path / "does-not-exist") is None


def test_newest_cached_radar_timestamp_returns_max(tmp_path):
    cache_dir = tmp_path / "radar_cache"
    cache_dir.mkdir()
    (cache_dir / "20260818T090000Z_1.h5").write_bytes(b"x")
    (cache_dir / "20260818T100000Z_2.h5").write_bytes(b"x")

    assert newest_cached_radar_timestamp(cache_dir) == _utc(2026, 8, 18, 10)


def test_fetch_new_radar_composites_downloads_s3_objects_within_requested_window(
    tmp_path, monkeypatch
):
    # Single calendar day. One object is before `since` and must be excluded; two
    # fall inside [since, now] and must be downloaded.
    now = _utc(2026, 8, 21, 10, 0)
    since = _utc(2026, 8, 21, 8, 0)

    objects_by_date = {
        _date(2026, 8, 21): [
            {
                "key": "2026/08/21/OPERA/COMP/OPERA@20260821T0745@0@RATE.h5",
                "timestamp": _utc(2026, 8, 21, 7, 45),
            },
            {
                "key": "2026/08/21/OPERA/COMP/OPERA@20260821T0800@0@RATE.h5",
                "timestamp": _utc(2026, 8, 21, 8, 0),
            },
            {
                "key": "2026/08/21/OPERA/COMP/OPERA@20260821T0900@0@RATE.h5",
                "timestamp": _utc(2026, 8, 21, 9, 0),
            },
        ]
    }

    def fake_list(prefix_date):
        return objects_by_date.get(prefix_date, [])

    downloaded_keys = []

    def fake_download(key, cache_dir_arg):
        downloaded_keys.append(key)
        path = cache_dir_arg / f"fake-{len(downloaded_keys)}.h5"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x89HDF\r\n\x1a\n" + b"bytes")
        return path

    monkeypatch.setattr("shroom_fm.radar.list_recent_radar_objects", fake_list)
    monkeypatch.setattr("shroom_fm.radar.download_opera_object", fake_download)

    cache_dir = tmp_path / "radar_cache"
    result = fetch_new_radar_composites(cache_dir, since, now=now)

    assert downloaded_keys == [
        "2026/08/21/OPERA/COMP/OPERA@20260821T0800@0@RATE.h5",
        "2026/08/21/OPERA/COMP/OPERA@20260821T0900@0@RATE.h5",
    ]
    assert len(result) == 2
    assert all(p.exists() for p in result)


def test_fetch_new_radar_composites_loops_over_each_date_in_range(
    tmp_path, monkeypatch
):
    # since/now straddle a UTC midnight boundary -> must query both calendar dates.
    now = _utc(2026, 8, 21, 1, 0)
    since = _utc(2026, 8, 20, 23, 0)

    objects_by_date = {
        _date(2026, 8, 20): [
            {
                "key": "2026/08/20/OPERA/COMP/OPERA@20260820T2330@0@RATE.h5",
                "timestamp": _utc(2026, 8, 20, 23, 30),
            }
        ],
        _date(2026, 8, 21): [
            {
                "key": "2026/08/21/OPERA/COMP/OPERA@20260821T0030@0@RATE.h5",
                "timestamp": _utc(2026, 8, 21, 0, 30),
            }
        ],
    }
    queried_dates = []

    def fake_list(prefix_date):
        queried_dates.append(prefix_date)
        return objects_by_date.get(prefix_date, [])

    monkeypatch.setattr("shroom_fm.radar.list_recent_radar_objects", fake_list)
    monkeypatch.setattr(
        "shroom_fm.radar.download_opera_object",
        lambda key, cache_dir_arg: cache_dir_arg / f"{key[-20:].replace('/', '_')}",
    )

    cache_dir = tmp_path / "radar_cache"
    result = fetch_new_radar_composites(cache_dir, since, now=now)

    assert queried_dates == [_date(2026, 8, 20), _date(2026, 8, 21)]
    assert len(result) == 2


def test_list_archived_radar_objects_parses_real_s3_listing_xml_from_archive_bucket(
    monkeypatch,
):
    # Same real S3 ListObjectsV2 XML shape as the recent bucket (confirmed live for
    # openradar-archive too, 2026-08-21) — just a different bucket name in the URL and
    # an older date.
    fake_xml = """<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
<Name>openradar-archive</Name>
<Prefix>2026/06/22/OPERA/</Prefix>
<IsTruncated>false</IsTruncated>
<Contents>
<Key>2026/06/22/OPERA/COMP/OPERA@20260622T0730@0@RATE.h5</Key>
<LastModified>2026-06-22T07:40:03.186Z</LastModified>
</Contents>
<Contents>
<Key>2026/06/22/OPERA/COMP/OPERA@20260622T0745@0@RATE.h5</Key>
<LastModified>2026-06-22T07:55:03.637Z</LastModified>
</Contents>
</ListBucketResult>"""

    class _FakeResponse:
        text = fake_xml
        status_code = 200

        def raise_for_status(self):
            pass

    captured_urls = []

    def fake_get(url, timeout):
        captured_urls.append(url)
        return _FakeResponse()

    monkeypatch.setattr("shroom_fm.radar.requests.get", fake_get)

    objects = list_archived_radar_objects(_date(2026, 6, 22))

    assert len(objects) == 2
    assert objects[0]["timestamp"] == _utc(2026, 6, 22, 7, 30)
    assert "openradar-archive" in captured_urls[0]
    assert "openradar-24h" not in captured_urls[0]


def test_download_archived_radar_object_hits_archive_bucket_url(tmp_path, monkeypatch):
    class _FakeResponse:
        content = b"\x89HDF\r\n\x1a\n" + b"archive-bytes"

        def raise_for_status(self):
            pass

    captured_urls = []

    def fake_get(url, timeout):
        captured_urls.append(url)
        return _FakeResponse()

    monkeypatch.setattr("shroom_fm.radar.requests.get", fake_get)

    cache_dir = tmp_path / "radar_cache"
    result = download_archived_radar_object(
        "2026/06/22/OPERA/COMP/OPERA@20260622T0730@0@RATE.h5", cache_dir
    )

    assert result.exists()
    assert cached_radar_timestamp(result) == _utc(2026, 6, 22, 7, 30)
    assert captured_urls == [
        "https://s3.waw3-1.cloudferro.com/openradar-archive/"
        "2026/06/22/OPERA/COMP/OPERA@20260622T0730@0@RATE.h5"
    ]


def test_fetch_new_radar_composites_routes_by_date_across_recent_and_archive_buckets(
    tmp_path, monkeypatch
):
    # since is 2 days before now -> the earliest date is older than the ~24h recent
    # window and must route through the historical archive bucket; the date
    # containing `now` itself must still route through the recent bucket. This
    # replaces the old NotImplementedError-for-anything-older-than-24h behavior, now
    # that a real anonymous historical archive bucket has been confirmed to exist
    # (see the "Correction" section of
    # docs/superpowers/plans/2026-08-21-opera-rest-backfill-findings.md).
    now = _utc(2026, 8, 21, 10, 0)
    since = now - timedelta(days=2)  # 2026-08-19T10:00

    recent_queried = []
    archive_queried = []

    monkeypatch.setattr(
        "shroom_fm.radar.list_recent_radar_objects",
        lambda prefix_date: recent_queried.append(prefix_date) or [],
    )
    monkeypatch.setattr(
        "shroom_fm.radar.list_archived_radar_objects",
        lambda prefix_date: archive_queried.append(prefix_date) or [],
    )

    cache_dir = tmp_path / "radar_cache"
    fetch_new_radar_composites(cache_dir, since, now=now)

    # 2026-08-21 (today, contains `now`) must use the recent bucket.
    assert _date(2026, 8, 21) in recent_queried
    # 2026-08-19 (2 days old, older than the ~24h recent window) must use archive.
    assert _date(2026, 8, 19) in archive_queried
    # Neither bucket queried for a date that belongs to the other.
    assert _date(2026, 8, 19) not in recent_queried
    assert _date(2026, 8, 21) not in archive_queried


def test_fetch_new_radar_composites_downloads_via_archive_bucket_for_old_dates(
    tmp_path, monkeypatch
):
    # A 7-day-old range, confirmed live-reachable via the archive bucket in the
    # findings doc's "Correction" section — must download real content, not raise.
    now = _utc(2026, 8, 21, 10, 0)
    since = now - timedelta(days=7)  # 2026-08-14T10:00

    def fake_list_archived(prefix_date):
        if prefix_date == since.date():
            return [
                {
                    "key": "2026/08/14/OPERA/COMP/OPERA@20260814T1000@0@RATE.h5",
                    "timestamp": since,
                }
            ]
        return []

    downloaded = []

    def fake_download_archived(key, cache_dir_arg):
        downloaded.append(key)
        path = cache_dir_arg / "archived.h5"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x89HDF\r\n\x1a\n" + b"bytes")
        return path

    monkeypatch.setattr(
        "shroom_fm.radar.list_archived_radar_objects", fake_list_archived
    )
    monkeypatch.setattr(
        "shroom_fm.radar.download_archived_radar_object", fake_download_archived
    )
    monkeypatch.setattr(
        "shroom_fm.radar.list_recent_radar_objects", lambda prefix_date: []
    )

    cache_dir = tmp_path / "radar_cache"
    result = fetch_new_radar_composites(cache_dir, since, now=now)

    assert downloaded == ["2026/08/14/OPERA/COMP/OPERA@20260814T1000@0@RATE.h5"]
    assert len(result) == 1
    assert result[0].exists()


def _write_fake_composite(
    path,
    *,
    rate_grid,
    gain=1.0,
    offset=0.0,
    nodata=-9999000.0,
    undetect=-8888000.0,
    quality_grid=None,
    projdef=None,
    xscale=None,
    yscale=None,
    ul_lon=None,
    ul_lat=None,
):
    """rate_grid is the real-world mm/h values wanted; encoded as raw = (rate-offset)/gain.
    Sentinel defaults match the real OPERA RATE product confirmed 2026-08-21 (previously
    KAIA's 65535.0/0.0 — different magnitudes, same conceptual gain/offset/nodata/
    undetect pattern the existing decode logic already reads dynamically). quality_grid,
    if given, writes a real-shaped dataset1/data1/quality1/data subgroup (the confirmed
    real ODIM qualityN-subgroup convention, gain=1.0/offset=0.0, no quantity attr) —
    left as None by default so most fixtures produce a file with NO quality layer at
    all, matching real OPERA files' actual variability and exercising the
    "must behave identically whether or not a quality subgroup is present" requirement."""
    # Default to real confirmed OPERA values, but allow override for specific tests
    if projdef is None:
        projdef = (
            "+proj=laea +lat_0=55.0 +lon_0=10.0 +x_0=1950000.0 "
            "+y_0=-2100000.0 +units=m +ellps=WGS84"
        )
    if xscale is None:
        xscale = 2000.0
    if yscale is None:
        yscale = 2000.0
    if ul_lon is None:
        ul_lon = -39.5357864125034
    if ul_lat is None:
        ul_lat = 67.0228327624372

    raw = ((np.asarray(rate_grid, dtype=np.float64) - offset) / gain).astype(np.float64)
    with h5py.File(path, "w") as f:
        f.attrs["Conventions"] = b"ODIM_H5/V2_4"
        data_grp = f.create_group("dataset1/data1")
        data_grp.create_dataset("data", data=raw)
        if quality_grid is not None:
            quality_grp = data_grp.create_group("quality1")
            quality_grp.create_dataset(
                "data", data=np.asarray(quality_grid, dtype=np.float64)
            )
            quality_what = quality_grp.create_group("what")
            quality_what.attrs["gain"] = 1.0
            quality_what.attrs["offset"] = 0.0
            quality_what.attrs["task"] = b"pl.imgw.quality.qi_total"
        # Real confirmed OPERA structure (verified 2026-08-21 against live-downloaded
        # openradar-archive files): gain/offset/nodata/undetect/quantity live under
        # dataset1/data1/what, not dataset1/what — the latter holds only dataset-level
        # metadata (startdate/enddate/product/prodname) with no decode attrs at all.
        # Write both groups so tests exercise the real, full real-file shape.
        ds_what = f.create_group("dataset1/what")
        ds_what.attrs["startdate"] = b"20260807"
        ds_what.attrs["starttime"] = b"104500"
        ds_what.attrs["enddate"] = b"20260807"
        ds_what.attrs["endtime"] = b"104500"
        ds_what.attrs["product"] = b"PPI"
        ds_what.attrs["prodname"] = b"OPERA NIMBUS instantaneous rain rate composite"
        what = data_grp.create_group("what")
        what.attrs["gain"] = gain
        what.attrs["offset"] = offset
        what.attrs["nodata"] = nodata
        what.attrs["undetect"] = undetect
        what.attrs["quantity"] = b"RATE"
        where = f.create_group("where")
        # Real confirmed OPERA projdef/grid (2026-08-21) — Lambert Azimuthal Equal-Area,
        # not KAIA's Mercator; the parsing code reads projdef dynamically so this swap
        # requires no production code changes, only realistic test fixtures. Tests may
        # override these to position grids in specific locations if needed.
        if isinstance(projdef, str):
            projdef = projdef.encode()
        where.attrs["projdef"] = projdef
        where.attrs["xsize"] = raw.shape[1]
        where.attrs["ysize"] = raw.shape[0]
        where.attrs["xscale"] = xscale
        where.attrs["yscale"] = yscale
        where.attrs["UL_lon"] = ul_lon
        where.attrs["UL_lat"] = ul_lat


def test_parse_radar_composite_decodes_valid_pixels_and_masks_sentinels(tmp_path):
    path = tmp_path / "sample.h5"
    _write_fake_composite(
        path,
        rate_grid=[[0.0, 2.0], [-9999000.0, 0.5]],
    )
    # Overwrite one raw cell to the nodata sentinel directly (bypass gain/offset math)
    with h5py.File(path, "r+") as f:
        raw = f["dataset1/data1/data"][:]
        raw[1, 0] = -9999000.0
        f["dataset1/data1/data"][:] = raw

    rate_mm_h, georef = parse_radar_composite(path)

    assert rate_mm_h.shape == (2, 2)
    assert rate_mm_h[0, 0] == 0.0  # undetect encodes "no rain", still a valid 0.0 reading
    assert rate_mm_h[0, 1] == 2.0
    assert np.isnan(rate_mm_h[1, 0])  # nodata
    assert rate_mm_h[1, 1] == 0.5
    assert georef["xsize"] == 2
    assert georef["ysize"] == 2
    assert georef["projdef"] == (
        "+proj=laea +lat_0=55.0 +lon_0=10.0 +x_0=1950000.0 "
        "+y_0=-2100000.0 +units=m +ellps=WGS84"
    )


def test_radar_pixel_centers_builds_one_point_per_pixel_in_native_crs(tmp_path):
    path = tmp_path / "sample.h5"
    _write_fake_composite(
        path,
        rate_grid=[[0.0, 0.0], [0.0, 0.0]],
        projdef="+proj=merc +a=6371000 +lat_0=68 +lon_0=25",
        xscale=359.07,
        yscale=346.70,
        ul_lon=20.354150207505985,
        ul_lat=61.33568305549931,
    )
    _, georef = parse_radar_composite(path)

    points = radar_pixel_centers(georef)

    assert len(points) == 4
    assert set(points["row"]) == {0, 1}
    assert set(points["col"]) == {0, 1}
    assert points.crs is not None


def test_radar_pixel_centers_applies_row_col_offset_for_sliced_grids():
    # georef as if parse_radar_composite sliced a larger grid starting at row 3, col 4
    # (row_offset/col_offset != 0) — verifies pixel centers land at their TRUE absolute
    # position in the full grid, not as if the sliced sub-array were itself grid (0,0).
    georef = {
        "projdef": "+proj=merc +a=6371000 +lat_0=68 +lon_0=25",
        "xsize": 2,
        "ysize": 2,
        "xscale": 359.07,
        "yscale": 346.70,
        "ul_lon": 20.354150207505985,
        "ul_lat": 61.33568305549931,
        "row_offset": 3,
        "col_offset": 4,
    }
    # Same georef but row_offset/col_offset = 0, for comparison — this represents the
    # UNSLICED full grid's pixel (3,4) computed directly.
    full_georef_at_3_4 = {
        **georef,
        "xsize": 1,
        "ysize": 1,
        "row_offset": 3,
        "col_offset": 4,
    }

    offset_points = radar_pixel_centers(georef)
    reference_point = radar_pixel_centers(full_georef_at_3_4)

    # The offset grid's (row=3, col=4) pixel — its local index (0,0) — must land at
    # exactly the same absolute coordinate as computing pixel (3,4) directly.
    offset_pixel = offset_points[
        (offset_points["row"] == 3) & (offset_points["col"] == 4)
    ].iloc[0]
    reference_pixel = reference_point.iloc[0]

    assert offset_pixel.geometry.x == pytest.approx(reference_pixel.geometry.x)
    assert offset_pixel.geometry.y == pytest.approx(reference_pixel.geometry.y)

    # And confirm it is NOT the same as what pixel (0,0) of an unsliced grid would be
    # (i.e. the offset genuinely moved the pixel, this isn't a vacuous comparison)
    zero_offset_georef = {**georef, "row_offset": 0, "col_offset": 0}
    zero_offset_points = radar_pixel_centers(zero_offset_georef)
    zero_offset_pixel = zero_offset_points[
        (zero_offset_points["row"] == 0) & (zero_offset_points["col"] == 0)
    ].iloc[0]
    assert offset_pixel.geometry.x != pytest.approx(zero_offset_pixel.geometry.x)


def test_radar_bbox_slice_covers_a_small_eraldis_bbox_within_the_full_grid():
    # Real live-verified OPERA radar grid: 1900x2200, 2000m pixels, LAEA, UL corner
    # confirmed 2026-08-21. A small bbox near Tallinn (~59.4N/24.8E) should slice out a
    # small sub-region, not the full 1900x2200 grid.
    georef = {
        "projdef": (
            "+proj=laea +lat_0=55.0 +lon_0=10.0 +x_0=1950000.0 "
            "+y_0=-2100000.0 +units=m +ellps=WGS84"
        ),
        "xsize": 1900,
        "ysize": 2200,
        "xscale": 2000.0,
        "yscale": 2000.0,
        "ul_lon": -39.5357864125034,
        "ul_lat": 67.0228327624372,
    }
    # Tallinn-area bbox, ~30km wide
    bbox = (24.6, 59.3, 25.0, 59.5)

    row_slice, col_slice = radar_bbox_slice(georef, bbox, buffer_pixels=5)

    assert 0 <= row_slice.start < row_slice.stop <= 2200
    assert 0 <= col_slice.start < col_slice.stop <= 1900
    # 30km at 2km/pixel is ~15 pixels wide plus buffer — should be small, well under
    # the coarser threshold appropriate for this grid's resolution
    assert (row_slice.stop - row_slice.start) < 50
    assert (col_slice.stop - col_slice.start) < 50


def test_accumulate_rainfall_sums_across_cached_files_in_window(tmp_path):
    cache_dir = tmp_path / "radar_cache"
    cache_dir.mkdir()

    # 3 files, 5 minutes apart, each with a 2x2 grid; pixel [0,0] rains every time,
    # pixel [1,1] never rains. Use old KAIA grid coordinates to ensure pixels overlap
    # with the test bbox (20-30°E, 56-62°N).
    _write_fake_composite(
        cache_dir / "20260815T000000Z_1.h5",
        rate_grid=[[1.0, 0.0], [0.0, 0.0]],
        projdef="+proj=merc +a=6371000 +lat_0=68 +lon_0=25",
        xscale=359.07,
        yscale=346.70,
        ul_lon=20.354150207505985,
        ul_lat=61.33568305549931,
    )
    _write_fake_composite(
        cache_dir / "20260815T000500Z_2.h5",
        rate_grid=[[2.0, 0.0], [0.0, 0.0]],
        projdef="+proj=merc +a=6371000 +lat_0=68 +lon_0=25",
        xscale=359.07,
        yscale=346.70,
        ul_lon=20.354150207505985,
        ul_lat=61.33568305549931,
    )
    _write_fake_composite(
        cache_dir / "20260815T001000Z_3.h5",
        rate_grid=[[0.0, 0.0], [0.0, 0.0]],
        projdef="+proj=merc +a=6371000 +lat_0=68 +lon_0=25",
        xscale=359.07,
        yscale=346.70,
        ul_lon=20.354150207505985,
        ul_lat=61.33568305549931,
    )

    now = _utc(2026, 8, 15, 0, 15)
    # Estonia-ish bbox covering the fake grid's corner
    bounds = (20.0, 56.0, 30.0, 62.0)

    points, coverage = accumulate_rainfall(cache_dir, now, bounds)

    row0_col0 = points[(points["row"] == 0) & (points["col"] == 0)].iloc[0]
    # accumulate_rainfall attributes a full _RADAR_SLOT_MINUTES=15-minute slot to every
    # cached file regardless of the real wall-clock gap between files, so
    # (1.0 + 2.0 + 0.0) mm/h * (15/60) h per slot = 0.75 mm total.
    assert row0_col0["rain_3d_mm"] == pytest.approx(0.75)
    assert row0_col0["rain_14d_mm"] == pytest.approx(0.75)
    assert row0_col0["hours_since_any_rain"] == pytest.approx(10 / 60)  # last wet slot was 10 min before `now`
    assert row0_col0["wet_hours_72h"] == pytest.approx(2 * 15 / 60)  # 2 wet slots

    row1_col1 = points[(points["row"] == 1) & (points["col"] == 1)].iloc[0]
    assert row1_col1["rain_3d_mm"] == pytest.approx(0.0)
    assert np.isnan(row1_col1["hours_since_any_rain"])  # never rained in the cached window

    # All 3 files fall within the 3d/7d/14d windows (they're 10 minutes apart, well
    # inside all three), but the expected slot counts differ per window, so the
    # coverage ratios differ even though the numerator (3) is the same. At the new
    # 15-minute cadence, expected_slots_Nd = (N days * 24 * 60) // 15.
    assert coverage["3d"] == pytest.approx(3 / 288)  # 3 files of 288 expected in 3d
    assert coverage["7d"] == pytest.approx(3 / 672)  # 3 files of 672 expected in 7d
    assert coverage["14d"] == pytest.approx(3 / 1344)  # 3 files of 1344 expected in 14d


def test_accumulate_rainfall_tracks_coverage_independently_per_window(tmp_path):
    cache_dir = tmp_path / "radar_cache"
    cache_dir.mkdir()

    now = _utc(2026, 8, 15, 0, 5)

    # Full 15-minute-slot coverage for the trailing 3 days (288 expected slots at the
    # new _RADAR_SLOT_MINUTES=15 cadence), but only a handful of files scattered
    # further back in days 4-14 — the 14-day aggregate should be far lower than the
    # 3-day/7-day windows even though the 3-day/7-day windows are essentially
    # complete. With the half-open [start, end) boundary, create one extra file (at
    # exactly 'now') so that after excluding that boundary file, 288 files remain in
    # the 3d window.
    slots_3d = (3 * 24 * 60) // 15
    for i in range(slots_3d + 1):
        ts = now - timedelta(minutes=15 * i)
        _write_fake_composite(
            cache_dir / f"{ts:%Y%m%dT%H%M%SZ}_{i}.h5",
            rate_grid=[[0.0, 0.0], [0.0, 0.0]],
            projdef="+proj=merc +a=6371000 +lat_0=68 +lon_0=25",
            xscale=359.07,
            yscale=346.70,
            ul_lon=20.354150207505985,
            ul_lat=61.33568305549931,
        )

    # A handful of sparse older files, days 4-14 (outside the 3d/7d windows).
    for days_ago in (5, 8, 11, 13):
        ts = now - timedelta(days=days_ago)
        _write_fake_composite(
            cache_dir / f"{ts:%Y%m%dT%H%M%SZ}_old{days_ago}.h5",
            rate_grid=[[0.0, 0.0], [0.0, 0.0]],
            projdef="+proj=merc +a=6371000 +lat_0=68 +lon_0=25",
            xscale=359.07,
            yscale=346.70,
            ul_lon=20.354150207505985,
            ul_lat=61.33568305549931,
        )

    bounds = (20.0, 56.0, 30.0, 62.0)

    _, coverage = accumulate_rainfall(cache_dir, now, bounds)

    assert coverage["3d"] == pytest.approx(1.0)
    assert coverage["7d"] < coverage["3d"]
    assert coverage["14d"] < coverage["7d"]
    assert coverage["14d"] < 0.5


def test_accumulate_rainfall_tracks_significant_and_strong_rain_events(tmp_path):
    cache_dir = tmp_path / "radar_cache"
    cache_dir.mkdir()

    # Single-pixel (1x1) grid. rate=8.0 mm/h * (15/60)h = 2.0mm per slot (each cached
    # file is attributed a full _RADAR_SLOT_MINUTES=15-minute slot regardless of the
    # real wall-clock gap between files, same as elsewhere in this file).
    # Slot sequence: 2,2,2,2,2 mm -> cumulative event total 2,4,6,8,10.
    # Crosses SIGNIFICANT_EVENT_MM=5.0 at the 3rd slot (cumulative 6.0), continues
    # advancing through the 4th slot (cumulative 8.0), and reaches STRONG_EVENT_MM=10.0
    # at the 5th slot (cumulative 10.0) since it's still the same event.
    for i, ts in enumerate(["20260815T000000Z", "20260815T000500Z", "20260815T001000Z",
                             "20260815T001500Z", "20260815T002000Z"]):
        _write_fake_composite(
            cache_dir / f"{ts}_{i}.h5",
            rate_grid=[[8.0]],
            projdef="+proj=merc +a=6371000 +lat_0=68 +lon_0=25",
            xscale=359.07,
            yscale=346.70,
            ul_lon=20.354150207505985,
            ul_lat=61.33568305549931,
        )

    # Dry gap > 6h, then a NEW event that never reaches 5mm — must not affect the
    # already-recorded significant/strong stats from the first event.
    _write_fake_composite(
        cache_dir / "20260815T080000Z_6.h5",
        rate_grid=[[4.0]],
        projdef="+proj=merc +a=6371000 +lat_0=68 +lon_0=25",
        xscale=359.07,
        yscale=346.70,
        ul_lon=20.354150207505985,
        ul_lat=61.33568305549931,
    )

    now = _utc(2026, 8, 15, 9, 0)  # 1h after the 6th file

    points, _ = accumulate_rainfall(cache_dir, now, (20.0, 56.0, 30.0, 62.0))
    row = points.iloc[0]

    # Last significant/strong slot was the 5th file (00:20:00), not the 3rd (first
    # crossing) or the 6th (a separate, non-qualifying event) — proves continuous
    # advancement through the event and correct event-boundary reset on the gap.
    expected_hours = (now - _utc(2026, 8, 15, 0, 20)).total_seconds() / 3600
    assert row["hours_since_significant_rain"] == pytest.approx(expected_hours)
    assert row["hours_since_strong_rain"] == pytest.approx(expected_hours)
    assert row["last_significant_event_mm"] == pytest.approx(10.0)
    assert row["last_strong_event_mm"] == pytest.approx(10.0)


def test_accumulate_rainfall_never_had_a_significant_event_is_nan(tmp_path):
    cache_dir = tmp_path / "radar_cache"
    cache_dir.mkdir()
    # Single slot, only 1.0mm — never reaches SIGNIFICANT_EVENT_MM=5.0.
    _write_fake_composite(
        cache_dir / "20260815T000000Z_1.h5",
        rate_grid=[[4.0]],
        projdef="+proj=merc +a=6371000 +lat_0=68 +lon_0=25",
        xscale=359.07,
        yscale=346.70,
        ul_lon=20.354150207505985,
        ul_lat=61.33568305549931,
    )

    now = _utc(2026, 8, 15, 0, 5)
    points, _ = accumulate_rainfall(cache_dir, now, (20.0, 56.0, 30.0, 62.0))
    row = points.iloc[0]

    assert np.isnan(row["hours_since_significant_rain"])
    assert np.isnan(row["hours_since_strong_rain"])
    assert row["last_significant_event_mm"] == pytest.approx(0.0)
    assert row["last_strong_event_mm"] == pytest.approx(0.0)


def test_accumulate_rainfall_max_24h_rain_captures_concentrated_window_not_whole_period(
    tmp_path,
):
    cache_dir = tmp_path / "radar_cache"
    cache_dir.mkdir()
    # Two slots close together (1.0mm each, same 24h window) = 2.0mm concentrated.
    _write_fake_composite(
        cache_dir / "20260815T000000Z_1.h5",
        rate_grid=[[4.0]],
        projdef="+proj=merc +a=6371000 +lat_0=68 +lon_0=25",
        xscale=359.07,
        yscale=346.70,
        ul_lon=20.354150207505985,
        ul_lat=61.33568305549931,
    )
    _write_fake_composite(
        cache_dir / "20260815T000500Z_2.h5",
        rate_grid=[[4.0]],
        projdef="+proj=merc +a=6371000 +lat_0=68 +lon_0=25",
        xscale=359.07,
        yscale=346.70,
        ul_lon=20.354150207505985,
        ul_lat=61.33568305549931,
    )
    # A 3rd slot 5 days later (well outside any 24h window containing the first two).
    _write_fake_composite(
        cache_dir / "20260820T000000Z_3.h5",
        rate_grid=[[4.0]],
        projdef="+proj=merc +a=6371000 +lat_0=68 +lon_0=25",
        xscale=359.07,
        yscale=346.70,
        ul_lon=20.354150207505985,
        ul_lat=61.33568305549931,
    )

    now = _utc(2026, 8, 20, 0, 10)
    points, _ = accumulate_rainfall(cache_dir, now, (20.0, 56.0, 30.0, 62.0))
    row = points.iloc[0]

    # rain_14d_mm sums all 3 slots (3.0mm total), but max_24h_rain_14d only ever sees
    # 2.0mm in any single rolling 24h window — the two early slots never coexist in
    # the same window as the late one.
    assert row["rain_14d_mm"] == pytest.approx(3.0)
    assert row["max_24h_rain_14d"] == pytest.approx(2.0)


def test_accumulate_rainfall_tracks_per_pixel_coverage_not_just_national(tmp_path):
    cache_dir = tmp_path / "radar_cache"
    cache_dir.mkdir()

    # Pixel [0,0]: real value both slots (covered). Pixel [0,1]: undetect both slots
    # (covered, confirmed-dry). Pixel [1,0]: nodata both slots (NOT covered).
    # Override projdef/xscale/yscale/ul_lon/ul_lat to place this fake tiny 2x2 grid so
    # it actually overlaps the requested Estonia-area bbox below — the same override
    # every other accumulate_rainfall test in this file uses. Without it,
    # _write_fake_composite's real-OPERA defaults (a continental LAEA grid whose UL
    # corner is near Greenland, 2km pixels) would place this fake 2-pixel-wide "full
    # field" nowhere near (20-30E, 56-62N), and radar_bbox_slice would compute an
    # empty/out-of-range slice — not a bug in accumulate_rainfall, just a test-fixture
    # location mismatch that would otherwise make this test vacuously pass or IndexError.
    _mercator_kwargs = dict(
        projdef="+proj=merc +a=6371000 +lat_0=68 +lon_0=25",
        xscale=359.07,
        yscale=346.70,
        ul_lon=20.354150207505985,
        ul_lat=61.33568305549931,
    )
    _write_fake_composite(
        cache_dir / "20260815T000000Z_RATE.h5",
        rate_grid=[[1.0, -8888000.0], [-9999000.0, 0.0]],
        **_mercator_kwargs,
    )
    with h5py.File(cache_dir / "20260815T000000Z_RATE.h5", "r+") as f:
        raw = f["dataset1/data1/data"][:]
        raw[0, 1] = -8888000.0  # undetect
        raw[1, 0] = -9999000.0  # nodata
        f["dataset1/data1/data"][:] = raw
    _write_fake_composite(
        cache_dir / "20260815T001500Z_RATE.h5",
        rate_grid=[[1.0, 0.0], [0.0, 0.0]],
        **_mercator_kwargs,
    )
    with h5py.File(cache_dir / "20260815T001500Z_RATE.h5", "r+") as f:
        raw = f["dataset1/data1/data"][:]
        raw[0, 1] = -8888000.0
        raw[1, 0] = -9999000.0
        f["dataset1/data1/data"][:] = raw

    now = _utc(2026, 8, 15, 0, 30)
    bounds = (20.0, 56.0, 30.0, 62.0)

    points, coverage = accumulate_rainfall(cache_dir, now, bounds)

    row0_col0 = points[(points["row"] == 0) & (points["col"] == 0)].iloc[0]
    row0_col1 = points[(points["row"] == 0) & (points["col"] == 1)].iloc[0]
    row1_col0 = points[(points["row"] == 1) & (points["col"] == 0)].iloc[0]

    # Per-pixel coverage_3d is valid_slots_3d / expected_slots_3d (the same nominal
    # 3-day-window denominator national coverage uses, per the design spec's "per-pixel,
    # per-rolling-window ... expected-slot counts" framing) — with only 2 real files
    # cached (against an expected_slots_3d of 288 at the new 15-min cadence), a fully
    # valid pixel's coverage_3d is 2/288, not 1.0 (this file only has 2 slots to ever
    # be valid in, so 1.0 would require a full 3-day-window's worth of files).
    expected_slots_3d = (3 * 24 * 60) // 15
    assert row0_col0["coverage_3d"] == pytest.approx(2 / expected_slots_3d)
    assert row0_col1["coverage_3d"] == pytest.approx(2 / expected_slots_3d)
    # 0 valid slots at [1,0] (nodata both times) -> pixel coverage 0.0 there, even
    # though 2 real files were downloaded and cached — this is the whole point of
    # per-pixel coverage: file COUNT is not the same as per-pixel VALIDITY.
    assert row1_col0["coverage_3d"] == pytest.approx(0.0)


def test_accumulate_rainfall_slot_minutes_is_15():
    from shroom_fm.radar import _RADAR_SLOT_MINUTES

    assert _RADAR_SLOT_MINUTES == 15


def test_accumulate_rainfall_coverage_never_exceeds_one_even_with_extra_files(tmp_path):
    # Regression test for the proven 4050/4032=1.0044... bug: even if MORE files exist
    # in the cache than the nominal expected-slot count for a window (real-world publish
    # jitter), the returned per-window NATIONAL coverage value must never exceed 1.0.
    cache_dir = tmp_path / "radar_cache"
    cache_dir.mkdir()
    now = _utc(2026, 8, 15, 3, 0)
    # 13 files at 15-minute spacing across a 3-hour window — expected_slots for a
    # 3-hour span at 15-min cadence is 12; write one extra to simulate jitter.
    for i in range(13):
        minutes_ago = 180 - i * 15
        ts = now - timedelta(minutes=minutes_ago)
        _write_fake_composite(
            cache_dir / f"{ts:%Y%m%dT%H%M%S}Z_RATE.h5", rate_grid=[[0.0]]
        )

    points, coverage = accumulate_rainfall(cache_dir, now, (20.0, 56.0, 30.0, 62.0))

    for key in ("3d", "7d", "14d"):
        assert 0.0 <= coverage[key] <= 1.0


def test_accumulate_rainfall_carries_through_quality_as_optional_enrichment(tmp_path):
    # Spec Component 3: the real per-pixel quality1 layer, when present in a cached
    # file, must be carried through as an optional quality_mean column — averaged
    # only over files that actually had a quality subgroup, never faked for files
    # that lack one.
    cache_dir = tmp_path / "radar_cache"
    cache_dir.mkdir()
    # See the comment on test_accumulate_rainfall_tracks_per_pixel_coverage_not_just_
    # national for why this location override is needed against the fake tiny grid.
    _mercator_kwargs = dict(
        projdef="+proj=merc +a=6371000 +lat_0=68 +lon_0=25",
        xscale=359.07,
        yscale=346.70,
        ul_lon=20.354150207505985,
        ul_lat=61.33568305549931,
    )
    _write_fake_composite(
        cache_dir / "20260815T000000Z_RATE.h5",
        rate_grid=[[0.0, 0.0]],
        quality_grid=[[1.0, 0.6]],
        **_mercator_kwargs,
    )
    _write_fake_composite(
        cache_dir / "20260815T001500Z_RATE.h5",
        rate_grid=[[0.0, 0.0]],
        quality_grid=[[0.8, 0.4]],
        **_mercator_kwargs,
    )

    now = _utc(2026, 8, 15, 0, 30)
    points, _ = accumulate_rainfall(cache_dir, now, (20.0, 56.0, 30.0, 62.0))

    row0_col0 = points[(points["row"] == 0) & (points["col"] == 0)].iloc[0]
    row0_col1 = points[(points["row"] == 0) & (points["col"] == 1)].iloc[0]
    assert row0_col0["quality_mean"] == pytest.approx((1.0 + 0.8) / 2)
    assert row0_col1["quality_mean"] == pytest.approx((0.6 + 0.4) / 2)


def test_accumulate_rainfall_quality_mean_is_nan_when_no_cached_file_has_quality(
    tmp_path,
):
    cache_dir = tmp_path / "radar_cache"
    cache_dir.mkdir()
    # No quality_grid given — matches real OPERA files that lack a quality subgroup.
    # Same location override as above, so points is genuinely non-empty and this test
    # actually exercises the per-pixel path instead of passing vacuously on an empty
    # GeoDataFrame.
    _write_fake_composite(
        cache_dir / "20260815T000000Z_RATE.h5",
        rate_grid=[[0.0, 0.0]],
        projdef="+proj=merc +a=6371000 +lat_0=68 +lon_0=25",
        xscale=359.07,
        yscale=346.70,
        ul_lon=20.354150207505985,
        ul_lat=61.33568305549931,
    )

    now = _utc(2026, 8, 15, 0, 15)
    points, _ = accumulate_rainfall(cache_dir, now, (20.0, 56.0, 30.0, 62.0))

    assert points["quality_mean"].isna().all()


def test_parse_radar_quality_returns_none_when_no_quality_subgroup_present(tmp_path):
    path = tmp_path / "no_quality.h5"
    _write_fake_composite(path, rate_grid=[[0.0, 1.0]])

    result = parse_radar_quality(path)

    assert result is None


def test_parse_radar_quality_decodes_real_quality_subgroup_when_present(tmp_path):
    path = tmp_path / "with_quality.h5"
    _write_fake_composite(
        path,
        rate_grid=[[0.0, 1.0], [2.0, 3.0]],
        quality_grid=[[1.0, 0.8], [0.0, 1.0]],
    )

    result = parse_radar_quality(path)

    assert result is not None
    np.testing.assert_array_almost_equal(result, [[1.0, 0.8], [0.0, 1.0]])


def test_parse_radar_quality_respects_row_col_slice(tmp_path):
    path = tmp_path / "with_quality.h5"
    _write_fake_composite(
        path,
        rate_grid=[[0.0, 1.0], [2.0, 3.0]],
        quality_grid=[[1.0, 0.8], [0.0, 1.0]],
    )

    result = parse_radar_quality(path, row_slice=slice(0, 1), col_slice=slice(1, 2))

    np.testing.assert_array_almost_equal(result, [[0.8]])


def test_cached_radar_files_excludes_the_window_end_boundary(tmp_path):
    cache_dir = tmp_path / "radar_cache"
    cache_dir.mkdir()
    _write_fake_composite(
        cache_dir / "20260815T000000Z_RATE.h5", rate_grid=[[0.0]]
    )
    # A file whose timestamp is EXACTLY window_end must be excluded — [start, end),
    # not [start, end] — this is the fix for the proven 4050/4032=1.0044... bug.
    _write_fake_composite(
        cache_dir / "20260815T001500Z_RATE.h5", rate_grid=[[0.0]]
    )

    window_end = _utc(2026, 8, 15, 0, 15)
    files = cached_radar_files(cache_dir, _utc(2026, 8, 15, 0, 0), window_end)

    assert len(files) == 1
    assert cached_radar_timestamp(files[0]) == _utc(2026, 8, 15, 0, 0)


def test_cached_radar_files_includes_the_window_start_boundary(tmp_path):
    cache_dir = tmp_path / "radar_cache"
    cache_dir.mkdir()
    _write_fake_composite(
        cache_dir / "20260815T000000Z_RATE.h5", rate_grid=[[0.0]]
    )

    files = cached_radar_files(
        cache_dir, _utc(2026, 8, 15, 0, 0), _utc(2026, 8, 15, 0, 15)
    )

    assert len(files) == 1  # window_start itself IS included — only window_end excluded


def test_validate_coverage_passes_through_a_valid_fraction():
    assert _validate_coverage(0.85, label="3d") == 0.85
    assert _validate_coverage(0.0, label="3d") == 0.0
    assert _validate_coverage(1.0, label="3d") == 1.0


def test_validate_coverage_raises_on_a_value_above_one():
    with pytest.raises(AssertionError, match="3d"):
        _validate_coverage(1.0044642857142858, label="3d")


def test_validate_coverage_raises_on_a_negative_value():
    with pytest.raises(AssertionError, match="7d"):
        _validate_coverage(-0.1, label="7d")


from shapely.geometry import box as _box


def _make_radar_points_grid():
    """A tiny 2x2 EPSG:3301 point grid, 2000m spacing, mimicking accumulate_rainfall's
    real OPERA-resolution output shape, for testing assign_radar_to_eraldis."""
    return gpd.GeoDataFrame(
        {
            "row": [0, 0, 1, 1],
            "col": [0, 1, 0, 1],
            "rain_3d_mm": [1.0, 2.0, np.nan, 4.0],
            "coverage_3d": [1.0, 1.0, 0.0, 1.0],
        },
        geometry=gpd.points_from_xy(
            [500000, 502000, 500000, 502000], [6500000, 6500000, 6498000, 6498000]
        ),
        crs="EPSG:3301",
    )


def test_assign_radar_to_eraldis_point_sample_inside_one_pixel():
    from shroom_fm.radar import assign_radar_to_eraldis

    radar_points = _make_radar_points_grid()
    # A tiny stand centered right on pixel (row=0,col=0)'s own point (500000, 6500000)
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1]},
        geometry=[_box(499900, 6499900, 500100, 6500100)],
        crs="EPSG:3301",
    )

    result = assign_radar_to_eraldis(eraldis_gdf, radar_points, ("rain_3d_mm",))

    assert result.loc[0, "rain_3d_mm"] == pytest.approx(1.0)


def test_assign_radar_to_eraldis_averages_over_multiple_intersecting_valid_pixels():
    from shroom_fm.radar import assign_radar_to_eraldis

    radar_points = _make_radar_points_grid()
    # A large stand spanning all 4 pixels: (0,0) [rain=1.0], (0,1) [rain=2.0],
    # (1,0) [rain=NaN], (1,1) [rain=4.0]
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1]},
        geometry=[_box(499000, 6497000, 503000, 6501000)],
        crs="EPSG:3301",
    )

    result = assign_radar_to_eraldis(eraldis_gdf, radar_points, ("rain_3d_mm",))

    # Spans all 4 pixels; pixel (1,0) has NaN rain (zero valid observations there) and
    # must be excluded from the mean, not treated as 0.0 — mean of [1.0, 2.0, 4.0]
    assert result.loc[0, "rain_3d_mm"] == pytest.approx((1.0 + 2.0 + 4.0) / 3)


def test_assign_radar_to_eraldis_returns_none_when_zero_valid_pixels_intersect():
    from shroom_fm.radar import assign_radar_to_eraldis

    radar_points = _make_radar_points_grid()
    # A stand entirely over pixel (1,0), which has NaN rain (zero valid observations)
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1]},
        geometry=[_box(499900, 6497900, 500100, 6498100)],
        crs="EPSG:3301",
    )

    result = assign_radar_to_eraldis(eraldis_gdf, radar_points, ("rain_3d_mm",))

    assert result.loc[0, "rain_3d_mm"] is None


def test_assign_radar_to_eraldis_returns_none_when_stand_is_far_outside_the_grid():
    from shroom_fm.radar import assign_radar_to_eraldis

    radar_points = _make_radar_points_grid()
    # A stand 500km away from the whole radar_points grid — no sjoin_nearest fallback,
    # this must be None, never a value borrowed from a distant pixel. This is the
    # direct regression test for the original bug report (stands 40-60km outside
    # KAIA's grid silently getting a fabricated near-zero value via unbounded
    # sjoin_nearest).
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1]},
        geometry=[_box(1000000, 7000000, 1000100, 7000100)],
        crs="EPSG:3301",
    )

    result = assign_radar_to_eraldis(eraldis_gdf, radar_points, ("rain_3d_mm",))

    assert result.loc[0, "rain_3d_mm"] is None


def test_assign_radar_to_eraldis_handles_multiple_stands_independently():
    from shroom_fm.radar import assign_radar_to_eraldis

    radar_points = _make_radar_points_grid()
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1, 2]},
        geometry=[
            _box(499900, 6499900, 500100, 6500100),  # pixel (0,0), rain=1.0
            _box(501900, 6499900, 502100, 6500100),  # pixel (0,1), rain=2.0
        ],
        crs="EPSG:3301",
    )

    result = assign_radar_to_eraldis(eraldis_gdf, radar_points, ("rain_3d_mm",))

    assert result.loc[0, "rain_3d_mm"] == pytest.approx(1.0)
    assert result.loc[1, "rain_3d_mm"] == pytest.approx(2.0)


def test_assign_radar_to_eraldis_finds_pixel_for_a_small_stand_not_centered_on_a_point():
    """Real eraldis stands are typically ~100-300m across, far smaller than the 2km
    OPERA pixel grid, and their bounding box is essentially randomly positioned
    relative to the fixed pixel-center grid — it will almost never itself contain a
    pixel's own point coordinate. This is a direct regression test for the real
    production bug found during Task 9's live backfill (2026-08-21): a live run against
    262,054 real stands returned weather_data_coverage's mean of 0.007 and only 1,865
    'complete' stands (~0.7%) — matching almost exactly the geometric probability that a
    small random stand bbox happens to straddle a 2km-spaced grid point — because the
    old cx[]-bbox-containment lookup (plus its zero-width centroid-slice fallback)
    structurally only ever matched stands large enough, or luckily positioned enough, to
    contain a pixel point outright."""
    from shroom_fm.radar import assign_radar_to_eraldis

    radar_points = _make_radar_points_grid()
    # A realistic small (200m) stand, offset 600-800m from pixel (0,0)'s own point
    # (500000, 6500000) but still well within that pixel's true 2km x 2km cell
    # (499000-501000, 6499000-6501000) — must resolve to pixel (0,0)'s rain=1.0, not None.
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1]},
        geometry=[_box(500600, 6499300, 500800, 6499500)],
        crs="EPSG:3301",
    )

    result = assign_radar_to_eraldis(eraldis_gdf, radar_points, ("rain_3d_mm",))

    assert result.loc[0, "rain_3d_mm"] == pytest.approx(1.0)
