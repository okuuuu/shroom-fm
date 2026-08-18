import threading

import pytest
import requests

from shroom_fm.concurrent_fetch import fetch_hit_count, fetch_pages_concurrently


class _FakeResponse:
    def __init__(self, content: bytes):
        self.content = content


def test_fetch_pages_concurrently_returns_empty_list_for_empty_input(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "shroom_fm.concurrent_fetch.get_with_retry",
        lambda *a, **k: calls.append(1),
    )

    result = fetch_pages_concurrently("http://example.com", [])

    assert result == []
    assert calls == []


def test_fetch_pages_concurrently_returns_results_in_param_order(monkeypatch):
    first_started = threading.Event()
    second_done = threading.Event()

    def fake_get_with_retry(url, *, params, timeout):
        if params["startIndex"] == 0:
            first_started.set()
            assert second_done.wait(timeout=5), "index 1 never signaled completion"
            return _FakeResponse(b"page-0")
        first_started.wait(timeout=5)
        response = _FakeResponse(b"page-1")
        second_done.set()
        return response

    monkeypatch.setattr(
        "shroom_fm.concurrent_fetch.get_with_retry", fake_get_with_retry
    )

    results = fetch_pages_concurrently(
        "http://example.com",
        [{"startIndex": 0}, {"startIndex": 1}],
        max_workers=2,
    )

    assert results == [b"page-0", b"page-1"]


def test_fetch_pages_concurrently_propagates_exception_from_failed_request(monkeypatch):
    def fake_get_with_retry(url, *, params, timeout):
        if params["startIndex"] == 0:
            raise requests.exceptions.Timeout("boom")
        return _FakeResponse(b"ok")

    monkeypatch.setattr(
        "shroom_fm.concurrent_fetch.get_with_retry", fake_get_with_retry
    )

    with pytest.raises(requests.exceptions.Timeout):
        fetch_pages_concurrently(
            "http://example.com",
            [{"startIndex": 0}, {"startIndex": 1}],
            max_workers=2,
        )


def test_fetch_pages_concurrently_prints_progress(monkeypatch, capsys):
    def fake_get_with_retry(url, *, params, timeout):
        return _FakeResponse(f"page-{params['startIndex']}".encode())

    monkeypatch.setattr(
        "shroom_fm.concurrent_fetch.get_with_retry", fake_get_with_retry
    )

    fetch_pages_concurrently(
        "http://example.com",
        [{"startIndex": 0}, {"startIndex": 1}],
        max_workers=2,
        progress_label="widget",
    )

    out = capsys.readouterr().out
    assert "fetched 1/2 widgets" in out
    assert "fetched 2/2 widgets" in out


def test_fetch_hit_count_parses_total_features_from_json(monkeypatch):
    body = b'{"type": "FeatureCollection", "totalFeatures": 12345, "features": []}'
    captured_params = {}

    def fake_get_with_retry(url, *, params, timeout):
        captured_params.update(params)
        return _FakeResponse(body)

    monkeypatch.setattr(
        "shroom_fm.concurrent_fetch.get_with_retry", fake_get_with_retry
    )

    result = fetch_hit_count("http://example.com", {"service": "WFS"})

    assert result == 12345
    assert captured_params["count"] == 1
    assert captured_params["startIndex"] == 0
    assert captured_params["service"] == "WFS"
