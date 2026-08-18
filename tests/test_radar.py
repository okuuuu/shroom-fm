from datetime import datetime, timedelta, timezone

import h5py
import numpy as np
import pytest

from shroom_fm.radar import (
    accumulate_rainfall,
    cached_radar_files,
    cached_radar_timestamp,
    download_radar_composite,
    expire_old_radar_composites,
    fetch_new_radar_composites,
    newest_cached_radar_timestamp,
    parse_radar_composite,
    query_radar_documents,
    radar_bbox_slice,
    radar_pixel_centers,
)


def _utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def test_query_radar_documents_paginates_via_bookmark(monkeypatch):
    pages = [
        {
            "documents": [
                {
                    "id": 1,
                    "metadata": {"Timestamp": "2026-08-18T09:00:00.0000000+03:00"},
                    "fileMetadata": [{"id": 1}],
                }
            ]
            * 2000,
            "nextBookmark": "page2",
        },
        {
            "documents": [
                {
                    "id": 2,
                    "metadata": {"Timestamp": "2026-08-18T09:05:00.0000000+03:00"},
                    "fileMetadata": [{"id": 1}],
                }
            ],
            "nextBookmark": None,
        },
    ]
    captured_bodies = []

    class _FakeResponse:
        def __init__(self, data):
            self._data = data

        def json(self):
            return self._data

    def fake_post_with_retry(url, *, json, timeout):
        captured_bodies.append(json)
        return _FakeResponse(pages[len(captured_bodies) - 1])

    monkeypatch.setattr("shroom_fm.radar.post_with_retry", fake_post_with_retry)

    result = query_radar_documents(_utc(2026, 8, 18, 6))

    assert len(result) == 2001
    assert result[0]["id"] == 1
    assert result[-1]["id"] == 2
    assert "bookmark" not in captured_bodies[0]
    assert captured_bodies[1]["bookmark"] == "page2"


def test_query_radar_documents_terminates_on_null_bookmark_even_if_page_is_full(monkeypatch):
    """Regression test: pagination must terminate when nextBookmark is None,
    even if the final page has exactly _PAGE_SIZE documents."""
    page_with_exact_page_size = {
        "documents": [
            {
                "id": i,
                "metadata": {"Timestamp": "2026-08-18T09:00:00.0000000+03:00"},
                "fileMetadata": [{"id": 1}],
            }
            for i in range(2000)
        ],
        "nextBookmark": None,
    }
    captured_bodies = []

    class _FakeResponse:
        def __init__(self, data):
            self._data = data

        def json(self):
            return self._data

    def fake_post_with_retry(url, *, json, timeout):
        captured_bodies.append(json)
        return _FakeResponse(page_with_exact_page_size)

    monkeypatch.setattr("shroom_fm.radar.post_with_retry", fake_post_with_retry)

    result = query_radar_documents(_utc(2026, 8, 18, 6))

    # Should have exactly 2000 documents, not re-fetched infinitely
    assert len(result) == 2000
    # Should have made exactly 1 request, not re-fetching
    assert len(captured_bodies) == 1
    # First request should not have a bookmark
    assert "bookmark" not in captured_bodies[0]


def test_download_radar_composite_skips_if_already_cached(tmp_path, monkeypatch):
    document = {"id": 42, "file_id": 1, "timestamp": _utc(2026, 8, 18, 9, 0, 0)}
    cache_dir = tmp_path / "radar_cache"
    cache_dir.mkdir()
    existing = cache_dir / "20260818T090000Z_42.h5"
    existing.write_bytes(b"cached-content")

    calls = []
    monkeypatch.setattr(
        "shroom_fm.radar.get_with_retry",
        lambda *a, **k: calls.append(1),
    )

    result = download_radar_composite(document, cache_dir)

    assert result == existing
    assert calls == []


def test_download_radar_composite_fetches_and_caches_new_file(tmp_path, monkeypatch):
    document = {"id": 43, "file_id": 1, "timestamp": _utc(2026, 8, 18, 9, 5, 0)}
    cache_dir = tmp_path / "radar_cache"
    captured_urls = []

    class _FakeResponse:
        content = b"real-h5-bytes"

    def fake_get_with_retry(url, timeout):
        captured_urls.append(url)
        return _FakeResponse()

    monkeypatch.setattr("shroom_fm.radar.get_with_retry", fake_get_with_retry)

    result = download_radar_composite(document, cache_dir)

    assert captured_urls == [
        "https://avaandmed.keskkonnaportaal.ee/api/lists/active/items/43/files/1"
    ]
    assert result.read_bytes() == b"real-h5-bytes"
    assert result.name == "20260818T090500Z_43.h5"


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


def test_fetch_new_radar_composites_downloads_all_queried_documents(
    tmp_path, monkeypatch
):
    documents = [
        {"id": 1, "file_id": 1, "timestamp": _utc(2026, 8, 18, 9, 0)},
        {"id": 2, "file_id": 1, "timestamp": _utc(2026, 8, 18, 9, 5)},
    ]
    monkeypatch.setattr(
        "shroom_fm.radar.query_radar_documents", lambda since: documents
    )

    class _FakeResponse:
        content = b"bytes"

    monkeypatch.setattr(
        "shroom_fm.radar.get_with_retry", lambda url, timeout: _FakeResponse()
    )

    cache_dir = tmp_path / "radar_cache"
    result = fetch_new_radar_composites(cache_dir, _utc(2026, 8, 18, 8))

    assert len(result) == 2
    assert all(p.exists() for p in result)


def _write_fake_composite(
    path, *, rate_grid, gain=1.0, offset=0.0, nodata=65535.0, undetect=0.0
):
    """rate_grid is the real-world mm/h values wanted; encoded as raw = (rate-offset)/gain."""
    raw = ((np.asarray(rate_grid, dtype=np.float64) - offset) / gain).astype(np.float32)
    with h5py.File(path, "w") as f:
        f.attrs["Conventions"] = b"ODIM_H5/V2_2"
        data_grp = f.create_group("dataset1/data1")
        data_grp.create_dataset("data", data=raw)
        what = f.create_group("dataset1/what")
        what.attrs["gain"] = gain
        what.attrs["offset"] = offset
        what.attrs["nodata"] = nodata
        what.attrs["undetect"] = undetect
        what.attrs["quantity"] = b"RATE"
        where = f.create_group("where")
        where.attrs["projdef"] = b"+proj=merc +a=6371000 +lat_0=68 +lon_0=25"
        where.attrs["xsize"] = raw.shape[1]
        where.attrs["ysize"] = raw.shape[0]
        where.attrs["xscale"] = 359.07
        where.attrs["yscale"] = 346.70
        where.attrs["UL_lon"] = 20.354150207505985
        where.attrs["UL_lat"] = 61.33568305549931


def test_parse_radar_composite_decodes_valid_pixels_and_masks_sentinels(tmp_path):
    path = tmp_path / "sample.h5"
    _write_fake_composite(
        path,
        rate_grid=[[0.0, 2.0], [65535.0, 0.5]],  # [1,0] will be forced to nodata below
    )
    # Overwrite one raw cell to the nodata sentinel directly (bypass gain/offset math)
    with h5py.File(path, "r+") as f:
        raw = f["dataset1/data1/data"][:]
        raw[1, 0] = 65535.0
        f["dataset1/data1/data"][:] = raw

    rate_mm_h, georef = parse_radar_composite(path)

    assert rate_mm_h.shape == (2, 2)
    assert rate_mm_h[0, 0] == 0.0  # undetect encodes "no rain", still a valid 0.0 reading
    assert rate_mm_h[0, 1] == 2.0
    assert np.isnan(rate_mm_h[1, 0])  # nodata
    assert rate_mm_h[1, 1] == 0.5
    assert georef["xsize"] == 2
    assert georef["ysize"] == 2
    assert georef["projdef"] == "+proj=merc +a=6371000 +lat_0=68 +lon_0=25"


def test_radar_pixel_centers_builds_one_point_per_pixel_in_native_crs(tmp_path):
    path = tmp_path / "sample.h5"
    _write_fake_composite(path, rate_grid=[[0.0, 0.0], [0.0, 0.0]])
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
    # Real live-verified radar grid: 1500x1500, ~359m/347m pixels, Mercator, UL corner
    # at 61.336N/20.354E. A small bbox near Tallinn (~59.4N/24.8E) should slice out a
    # small sub-region, not the full 1500x1500 grid.
    georef = {
        "projdef": "+proj=merc +a=6371000 +lat_0=68 +lon_0=25",
        "xsize": 1500,
        "ysize": 1500,
        "xscale": 359.07,
        "yscale": 346.70,
        "ul_lon": 20.354150207505985,
        "ul_lat": 61.33568305549931,
    }
    # Tallinn-area bbox, ~30km wide
    bbox = (24.6, 59.3, 25.0, 59.5)

    row_slice, col_slice = radar_bbox_slice(georef, bbox, buffer_pixels=5)

    assert 0 <= row_slice.start < row_slice.stop <= 1500
    assert 0 <= col_slice.start < col_slice.stop <= 1500
    # Should be a small fraction of the full grid, not the whole thing
    assert (row_slice.stop - row_slice.start) < 300
    assert (col_slice.stop - col_slice.start) < 300


def test_accumulate_rainfall_sums_across_cached_files_in_window(tmp_path):
    cache_dir = tmp_path / "radar_cache"
    cache_dir.mkdir()

    # 3 files, 5 minutes apart, each with a 2x2 grid; pixel [0,0] rains every time,
    # pixel [1,1] never rains.
    _write_fake_composite(
        cache_dir / "20260815T000000Z_1.h5",
        rate_grid=[[1.0, 0.0], [0.0, 0.0]],
    )
    _write_fake_composite(
        cache_dir / "20260815T000500Z_2.h5",
        rate_grid=[[2.0, 0.0], [0.0, 0.0]],
    )
    _write_fake_composite(
        cache_dir / "20260815T001000Z_3.h5",
        rate_grid=[[0.0, 0.0], [0.0, 0.0]],
    )

    now = _utc(2026, 8, 15, 0, 10)
    # Estonia-ish bbox covering the fake grid's corner
    bounds = (20.0, 56.0, 30.0, 62.0)

    points, coverage = accumulate_rainfall(cache_dir, now, bounds)

    row0_col0 = points[(points["row"] == 0) & (points["col"] == 0)].iloc[0]
    # (1.0 + 2.0 + 0.0) mm/h * (5/60) h per slot = 0.25 mm total
    assert row0_col0["rain_3d_mm"] == pytest.approx(0.25)
    assert row0_col0["rain_14d_mm"] == pytest.approx(0.25)
    assert row0_col0["hours_since_rain"] == pytest.approx(5 / 60)  # last wet slot was 5 min before `now`
    assert row0_col0["wet_hours_72h"] == pytest.approx(2 * 5 / 60)  # 2 wet slots

    row1_col1 = points[(points["row"] == 1) & (points["col"] == 1)].iloc[0]
    assert row1_col1["rain_3d_mm"] == pytest.approx(0.0)
    assert np.isnan(row1_col1["hours_since_rain"])  # never rained in the cached window

    assert coverage == pytest.approx(3 / 4032)  # 3 files present of ~4032 expected in 14d
