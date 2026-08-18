from datetime import datetime, timedelta, timezone

import pytest

from shroom_fm.radar import (
    cached_radar_files,
    cached_radar_timestamp,
    download_radar_composite,
    expire_old_radar_composites,
    fetch_new_radar_composites,
    newest_cached_radar_timestamp,
    query_radar_documents,
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

    class _FakeResponse:
        content = b"real-h5-bytes"

    monkeypatch.setattr(
        "shroom_fm.radar.get_with_retry", lambda url, timeout: _FakeResponse()
    )

    result = download_radar_composite(document, cache_dir)

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
