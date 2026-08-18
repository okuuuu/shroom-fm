# Concurrent, Observable WFS Fetching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace sequential, silent WFS pagination/batching in `eraldis.py`, `roads.py`,
and `enrich.py` with a shared concurrent-fetch primitive that runs requests on a bounded
thread pool and prints progress, cutting wall-clock time on the slowest pipeline steps
(especially `enrich_eraldis`'s 471s composition fetch and `download_eraldis`'s ~65k-stand
annulus pagination) while making long-running fetches observable instead of silent.

**Architecture:** One new module, `src/shroom_fm/concurrent_fetch.py`, provides
`fetch_hit_count` (WFS `resultType=hits` total-count lookup) and
`fetch_pages_concurrently` (bounded `ThreadPoolExecutor` fan-out over a list of param
dicts, returning response bodies in input order with progress printed per completion).
Three existing fetch functions — `eraldis.py::fetch_eraldis_annulus`,
`roads.py::fetch_layer_annulus`, `enrich.py::fetch_eraldis_element` — are rewritten to
build their param lists up front and delegate to these two functions instead of looping
sequentially. Every request still goes through the existing `get_with_retry` for
per-request retry/backoff.

**Tech Stack:** Python, `concurrent.futures.ThreadPoolExecutor` (stdlib), `requests`
(via existing `shroom_fm.retry`), `geopandas`/`pandas`, `xml.etree.ElementTree` (stdlib,
for parsing WFS hits responses), `pytest` + `monkeypatch`/`capsys` for tests.

**Spec:** `docs/superpowers/specs/2026-08-18-concurrent-fetch-design.md`

## Global Constraints

- `MAX_WORKERS = 6` is the fixed default concurrency for `fetch_pages_concurrently` —
  deliberately modest, don't raise it without a new decision.
- Public signatures of `fetch_eraldis_annulus`, `fetch_layer_annulus`, and
  `fetch_eraldis_element` do not change — no calling script (`download_eraldis.py`,
  `download_roads.py`, `enrich_eraldis.py`, `main.py`) needs any edit.
- `PAGE_SIZE` (`eraldis.py`, 1000) and `_PAGE_SIZE`/`ID_BATCH_SIZE` (`roads.py`/`enrich.py`,
  1000/500) values are unchanged.
- `retry.py`'s retry/backoff behavior is unchanged — `get_with_retry` is reused as-is,
  called once per individual request inside `fetch_pages_concurrently`.
- `fetch_pages_concurrently` returns response bodies in the same order as its input
  `params_list`, regardless of which request completes first.
- An empty `params_list` returns `[]` immediately — no thread pool spun up, no request
  made.
- If any request exhausts its retries and raises, `fetch_pages_concurrently` lets that
  exception propagate out (no silent partial results, no swallowed errors) — in-flight
  requests are allowed to finish but no new ones start.
- `fetch_classifier` (`enrich.py`) and `fetch_capabilities` (`wfs.py`) are out of scope —
  single-request call sites, not touched.
- Baseline before this plan: 123 tests passing (`uv run pytest tests/ -q`).

---

### Task 1: `concurrent_fetch.py` — shared hit-count + concurrent-page-fetch module

**Files:**
- Create: `src/shroom_fm/concurrent_fetch.py`
- Test: `tests/test_concurrent_fetch.py`

**Interfaces:**
- Produces: `fetch_hit_count(url: str, base_params: dict, *, timeout: int = 30) -> int`
- Produces: `fetch_pages_concurrently(url: str, params_list: list[dict], *, max_workers: int = MAX_WORKERS, timeout: int = 30, progress_label: str = "page") -> list[bytes]`
- Produces: `MAX_WORKERS = 6` (module constant)
- Consumes: `shroom_fm.retry.get_with_retry(url, *, params, timeout, sleep=...)` — existing,
  unchanged.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_concurrent_fetch.py`:

```python
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


def test_fetch_hit_count_parses_number_matched_from_xml(monkeypatch):
    xml = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0" '
        b'numberMatched="12345" numberReturned="0"/>'
    )
    captured_params = {}

    def fake_get_with_retry(url, *, params, timeout):
        captured_params.update(params)
        return _FakeResponse(xml)

    monkeypatch.setattr(
        "shroom_fm.concurrent_fetch.get_with_retry", fake_get_with_retry
    )

    result = fetch_hit_count("http://example.com", {"service": "WFS"})

    assert result == 12345
    assert captured_params["resultType"] == "hits"
    assert captured_params["service"] == "WFS"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_concurrent_fetch.py -v`
Expected: FAIL / ERROR — `ModuleNotFoundError: No module named 'shroom_fm.concurrent_fetch'`

- [ ] **Step 3: Implement `concurrent_fetch.py`**

Create `src/shroom_fm/concurrent_fetch.py`:

```python
import concurrent.futures
from xml.etree import ElementTree

from shroom_fm.retry import get_with_retry

MAX_WORKERS = 6


def fetch_hit_count(url: str, base_params: dict, *, timeout: int = 30) -> int:
    response = get_with_retry(
        url, params={**base_params, "resultType": "hits"}, timeout=timeout
    )
    root = ElementTree.fromstring(response.content)
    return int(root.get("numberMatched"))


def fetch_pages_concurrently(
    url: str,
    params_list: list[dict],
    *,
    max_workers: int = MAX_WORKERS,
    timeout: int = 30,
    progress_label: str = "page",
) -> list[bytes]:
    if not params_list:
        return []

    total = len(params_list)
    results: list[bytes | None] = [None] * total
    done = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_index = {
            executor.submit(get_with_retry, url, params=params, timeout=timeout): index
            for index, params in enumerate(params_list)
        }
        try:
            for future in concurrent.futures.as_completed(future_to_index):
                index = future_to_index[future]
                results[index] = future.result().content
                done += 1
                print(f"  fetched {done}/{total} {progress_label}s")
        except Exception:
            for pending in future_to_index:
                pending.cancel()
            raise

    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_concurrent_fetch.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/shroom_fm/concurrent_fetch.py tests/test_concurrent_fetch.py
git commit -m "feat: add shared concurrent WFS page-fetch helper"
```

---

### Task 2: Live-verify `resultType=hits` response shape against Metsaregister and ETAK

This task has no unit-test deliverable — it's a live-network verification gate before
Task 1's `fetch_hit_count` is relied on by real call sites in Tasks 3-5. Per this
project's established practice (see CLAUDE.md's "Known real-data quirks"), WFS server
behavior is confirmed against the real service before code depends on it, not assumed
from the spec alone.

**Files:**
- Modify (only if verification shows the XML assumption is wrong):
  `src/shroom_fm/concurrent_fetch.py`
- Modify (only in that same case): `tests/test_concurrent_fetch.py`

- [ ] **Step 1: Probe Metsaregister's `resultType=hits` response**

Run:

```bash
uv run python3 -c "
import requests
r = requests.get(
    'https://gsavalik.envir.ee/geoserver/metsaregister/ows',
    params={
        'service': 'WFS', 'version': '2.0.0', 'request': 'GetFeature',
        'typeNames': 'metsaregister:eraldis', 'outputFormat': 'application/json',
        'resultType': 'hits',
    },
    timeout=30,
)
print(r.status_code)
print(r.headers.get('Content-Type'))
print(r.content[:500])
"
```

- [ ] **Step 2: Probe ETAK's `resultType=hits` response**

Run:

```bash
uv run python3 -c "
import requests
r = requests.get(
    'https://gsavalik.envir.ee/geoserver/etak/wfs',
    params={
        'service': 'WFS', 'version': '2.0.0', 'request': 'GetFeature',
        'typeNames': 'etak:e_501_tee_j', 'outputFormat': 'application/json',
        'srsName': 'EPSG:3301', 'resultType': 'hits',
    },
    timeout=30,
)
print(r.status_code)
print(r.headers.get('Content-Type'))
print(r.content[:500])
"
```

- [ ] **Step 3: Branch on what the probes showed**

**If both responses are XML with a `numberMatched` attribute on the root element**
(the expected WFS 2.0.0 spec behavior — `resultType=hits` ignores `outputFormat` and
always returns XML) — no code change needed. `fetch_hit_count` as written in Task 1
already handles this. Skip to Step 4.

**If instead a response is JSON** (i.e. `outputFormat=application/json` was honored
even for `resultType=hits`, returning `{"type": "FeatureCollection", "totalFeatures": N, "features": []}`)
— patch `fetch_hit_count` in `src/shroom_fm/concurrent_fetch.py`:

```python
import json

from shroom_fm.retry import get_with_retry

MAX_WORKERS = 6


def fetch_hit_count(url: str, base_params: dict, *, timeout: int = 30) -> int:
    response = get_with_retry(
        url, params={**base_params, "resultType": "hits"}, timeout=timeout
    )
    data = json.loads(response.content)
    return int(data["totalFeatures"])
```

(remove the `from xml.etree import ElementTree` import; keep `fetch_pages_concurrently`
unchanged below it) — and update `tests/test_concurrent_fetch.py`'s
`test_fetch_hit_count_parses_number_matched_from_xml` to instead build a canned JSON
response and rename it `test_fetch_hit_count_parses_total_features_from_json`:

```python
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
    assert captured_params["resultType"] == "hits"
```

Then run `uv run pytest tests/test_concurrent_fetch.py -v` and confirm 5 passed before
continuing.

- [ ] **Step 4: Record the outcome and commit if anything changed**

If Step 3 required a code change:

```bash
git add src/shroom_fm/concurrent_fetch.py tests/test_concurrent_fetch.py
git commit -m "fix: parse WFS hits count as JSON per live-verified server behavior"
```

If no code change was needed, there is nothing to commit — proceed directly to Task 3.

---

### Task 3: Rewire `eraldis.py::fetch_eraldis_annulus`

**Files:**
- Modify: `src/shroom_fm/eraldis.py` (full file, 47 lines)
- Test: `tests/test_eraldis.py`

**Interfaces:**
- Consumes: `fetch_hit_count(url, base_params, *, timeout=30) -> int`,
  `fetch_pages_concurrently(url, params_list, *, max_workers=MAX_WORKERS, timeout=30, progress_label="page") -> list[bytes]`
  from Task 1's `shroom_fm.concurrent_fetch`.
- Produces: `fetch_eraldis_annulus(lat, lon, radius_km, inner_radius_km=0.0) -> gpd.GeoDataFrame`
  — signature unchanged from before this task.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_eraldis.py` (keep the existing
`test_fetch_eraldis_annulus_raises_when_inner_radius_not_less_than_outer` test as-is):

```python
import json

from shroom_fm.eraldis import fetch_eraldis_annulus


def _geojson_page(n: int) -> bytes:
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [24.75 + i * 0.001, 59.43]},
            "properties": {"id": i},
        }
        for i in range(n)
    ]
    return json.dumps({"type": "FeatureCollection", "features": features}).encode()


def test_fetch_eraldis_annulus_fetches_all_pages_and_concatenates(monkeypatch):
    monkeypatch.setattr("shroom_fm.eraldis.PAGE_SIZE", 2)
    monkeypatch.setattr(
        "shroom_fm.eraldis.fetch_hit_count", lambda url, params, **kw: 3
    )

    captured_params_list = []

    def fake_fetch_pages_concurrently(url, params_list, **kwargs):
        captured_params_list.extend(params_list)
        return [_geojson_page(2), _geojson_page(1)]

    monkeypatch.setattr(
        "shroom_fm.eraldis.fetch_pages_concurrently", fake_fetch_pages_concurrently
    )

    result = fetch_eraldis_annulus(59.4370, 24.7536, radius_km=20.0)

    assert len(result) == 3
    assert [p["startIndex"] for p in captured_params_list] == [0, 2]
    assert [p["count"] for p in captured_params_list] == [2, 2]


def test_fetch_eraldis_annulus_issues_one_request_for_empty_result(monkeypatch):
    monkeypatch.setattr(
        "shroom_fm.eraldis.fetch_hit_count", lambda url, params, **kw: 0
    )

    captured_params_list = []

    def fake_fetch_pages_concurrently(url, params_list, **kwargs):
        captured_params_list.extend(params_list)
        return [_geojson_page(0)]

    monkeypatch.setattr(
        "shroom_fm.eraldis.fetch_pages_concurrently", fake_fetch_pages_concurrently
    )

    result = fetch_eraldis_annulus(59.4370, 24.7536, radius_km=20.0)

    assert len(result) == 0
    assert len(captured_params_list) == 1
    assert captured_params_list[0]["startIndex"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_eraldis.py -v`
Expected: the 2 new tests FAIL with `AttributeError` (no `fetch_hit_count`/
`fetch_pages_concurrently` attribute on `shroom_fm.eraldis` yet); the existing
`ValueError` test still passes.

- [ ] **Step 3: Rewrite `src/shroom_fm/eraldis.py`**

Replace the full file contents with:

```python
import io
import math

import geopandas as gpd
import pandas as pd

from shroom_fm.concurrent_fetch import fetch_hit_count, fetch_pages_concurrently
from shroom_fm.cql import annulus_filter
from shroom_fm.wfs import METSAREGISTER_OWS_URL

ESTONIAN_GRID_CRS = "EPSG:3301"
WGS84_CRS = "EPSG:4326"
ERALDIS_TYPENAME = "metsaregister:eraldis"
GEOMETRY_ATTR = "shape"
PAGE_SIZE = 1000


def fetch_eraldis_annulus(
    lat: float,
    lon: float,
    radius_km: float,
    inner_radius_km: float = 0.0,
) -> gpd.GeoDataFrame:
    cql_filter = annulus_filter(GEOMETRY_ATTR, lat, lon, radius_km, inner_radius_km)
    base_params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": ERALDIS_TYPENAME,
        "outputFormat": "application/json",
        "srsName": WGS84_CRS,
        "CQL_FILTER": cql_filter,
    }
    total = fetch_hit_count(METSAREGISTER_OWS_URL, base_params)
    num_pages = max(1, math.ceil(total / PAGE_SIZE))
    params_list = [
        {**base_params, "startIndex": i * PAGE_SIZE, "count": PAGE_SIZE}
        for i in range(num_pages)
    ]
    contents = fetch_pages_concurrently(
        METSAREGISTER_OWS_URL, params_list, progress_label="eraldis page"
    )
    pages = [gpd.read_file(io.BytesIO(content)) for content in contents]
    return gpd.GeoDataFrame(pd.concat(pages, ignore_index=True), crs=pages[0].crs)
```

Note: `annulus_filter` still raises `ValueError` before any network call when
`inner_radius_km >= radius_km`, so the existing validation test keeps passing unchanged.
`get_with_retry` is no longer imported directly here — it's used internally by
`concurrent_fetch.py` now.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_eraldis.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/shroom_fm/eraldis.py tests/test_eraldis.py
git commit -m "perf: fetch eraldis annulus pages concurrently with progress"
```

---

### Task 4: Rewire `roads.py::fetch_layer_annulus`

**Files:**
- Modify: `src/shroom_fm/roads.py` (full file, 96 lines)
- Test: `tests/test_roads.py`

**Interfaces:**
- Consumes: `fetch_hit_count`, `fetch_pages_concurrently` from `shroom_fm.concurrent_fetch`
  (Task 1).
- Produces: `fetch_layer_annulus(url, typename, lat, lon, radius_km, inner_radius_km=0.0) -> gpd.GeoDataFrame`
  — signature unchanged from before this task.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_roads.py` (keep every existing test, including
`test_fetch_layer_annulus_raises_when_inner_radius_not_less_than_outer`, as-is):

```python
import json


def _geojson_page(n: int) -> bytes:
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [24.75 + i * 0.001, 59.43]},
            "properties": {"id": i},
        }
        for i in range(n)
    ]
    return json.dumps({"type": "FeatureCollection", "features": features}).encode()


def test_fetch_layer_annulus_fetches_all_pages_and_concatenates(monkeypatch):
    monkeypatch.setattr("shroom_fm.roads._PAGE_SIZE", 2)
    monkeypatch.setattr(
        "shroom_fm.roads.fetch_hit_count", lambda url, params, **kw: 3
    )

    captured_params_list = []

    def fake_fetch_pages_concurrently(url, params_list, **kwargs):
        captured_params_list.extend(params_list)
        return [_geojson_page(2), _geojson_page(1)]

    monkeypatch.setattr(
        "shroom_fm.roads.fetch_pages_concurrently", fake_fetch_pages_concurrently
    )

    result = fetch_layer_annulus(
        "https://example.com/wfs", "example:layer", 59.4370, 24.7536, radius_km=20.0
    )

    assert len(result) == 3
    assert [p["startIndex"] for p in captured_params_list] == [0, 2]


def test_fetch_layer_annulus_issues_one_request_for_empty_result(monkeypatch):
    monkeypatch.setattr(
        "shroom_fm.roads.fetch_hit_count", lambda url, params, **kw: 0
    )

    captured_params_list = []

    def fake_fetch_pages_concurrently(url, params_list, **kwargs):
        captured_params_list.extend(params_list)
        return [_geojson_page(0)]

    monkeypatch.setattr(
        "shroom_fm.roads.fetch_pages_concurrently", fake_fetch_pages_concurrently
    )

    result = fetch_layer_annulus(
        "https://example.com/wfs", "example:layer", 59.4370, 24.7536, radius_km=20.0
    )

    assert len(result) == 0
    assert len(captured_params_list) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_roads.py -v`
Expected: the 2 new tests FAIL with `AttributeError`; all existing tests still pass.

- [ ] **Step 3: Rewrite `fetch_layer_annulus` in `src/shroom_fm/roads.py`**

Change the imports at the top of the file from:

```python
import io

import geopandas as gpd
import pandas as pd

from shroom_fm.cql import annulus_filter
from shroom_fm.retry import get_with_retry
```

to:

```python
import io
import math

import geopandas as gpd
import pandas as pd

from shroom_fm.concurrent_fetch import fetch_hit_count, fetch_pages_concurrently
from shroom_fm.cql import annulus_filter
```

Then replace the `fetch_layer_annulus` function (originally lines 63-95) with:

```python
def fetch_layer_annulus(
    url: str,
    typename: str,
    lat: float,
    lon: float,
    radius_km: float,
    inner_radius_km: float = 0.0,
) -> gpd.GeoDataFrame:
    cql_filter = annulus_filter(GEOMETRY_ATTR, lat, lon, radius_km, inner_radius_km)
    base_params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": typename,
        "outputFormat": "application/json",
        "srsName": _ETAK_OUTPUT_CRS,
        "CQL_FILTER": cql_filter,
    }
    total = fetch_hit_count(url, base_params)
    num_pages = max(1, math.ceil(total / _PAGE_SIZE))
    params_list = [
        {**base_params, "startIndex": i * _PAGE_SIZE, "count": _PAGE_SIZE}
        for i in range(num_pages)
    ]
    contents = fetch_pages_concurrently(url, params_list, progress_label="road page")
    pages = [gpd.read_file(io.BytesIO(content)) for content in contents]
    return gpd.GeoDataFrame(pd.concat(pages, ignore_index=True), crs=pages[0].crs)
```

Everything else in the file (`classify_car_class`, `exclude_barrier_blocked_segments`,
constants) is unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_roads.py -v`
Expected: all tests pass (19 existing + 2 new = 21)

- [ ] **Step 5: Commit**

```bash
git add src/shroom_fm/roads.py tests/test_roads.py
git commit -m "perf: fetch ETAK layer annulus pages concurrently with progress"
```

---

### Task 5: Rewire `enrich.py::fetch_eraldis_element`

**Files:**
- Modify: `src/shroom_fm/enrich.py` (lines 1-8 imports, lines 67-86 function)
- Test: `tests/test_enrich.py`

**Interfaces:**
- Consumes: `fetch_pages_concurrently` from `shroom_fm.concurrent_fetch` (Task 1). No
  `fetch_hit_count` needed — batch count is already known from `len(eraldis_ids)`.
- Produces: `fetch_eraldis_element(eraldis_ids: list[int]) -> pd.DataFrame` — signature
  unchanged from before this task.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_enrich.py`:

```python
import json

from shroom_fm.enrich import fetch_eraldis_element


def _element_json_page(rows: list[dict]) -> bytes:
    return json.dumps(
        {"type": "FeatureCollection", "features": [{"properties": r} for r in rows]}
    ).encode()


def test_fetch_eraldis_element_batches_ids_and_concatenates(monkeypatch):
    monkeypatch.setattr("shroom_fm.enrich.ID_BATCH_SIZE", 2)

    captured_params_list = []

    def fake_fetch_pages_concurrently(url, params_list, **kwargs):
        captured_params_list.extend(params_list)
        return [
            _element_json_page([{"eraldis_id": 1, "puuliik_kood": "MA"}]),
            _element_json_page([{"eraldis_id": 3, "puuliik_kood": "KU"}]),
        ]

    monkeypatch.setattr(
        "shroom_fm.enrich.fetch_pages_concurrently", fake_fetch_pages_concurrently
    )

    result = fetch_eraldis_element([1, 2, 3])

    assert len(result) == 2
    assert list(result["eraldis_id"]) == [1, 3]
    assert captured_params_list[0]["CQL_FILTER"] == "eraldis_id IN (1,2)"
    assert captured_params_list[1]["CQL_FILTER"] == "eraldis_id IN (3)"


def test_fetch_eraldis_element_returns_empty_dataframe_for_empty_ids(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "shroom_fm.enrich.fetch_pages_concurrently",
        lambda *a, **k: calls.append(1) or [],
    )

    result = fetch_eraldis_element([])

    assert result.empty
    assert calls == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_enrich.py -v`
Expected: the 2 new tests FAIL with `AttributeError`; existing tests still pass.

- [ ] **Step 3: Rewrite `fetch_eraldis_element` in `src/shroom_fm/enrich.py`**

Change the import line (currently line 7):

```python
from shroom_fm.retry import call_with_retry, get_with_retry
```

to:

```python
from shroom_fm.concurrent_fetch import fetch_pages_concurrently
from shroom_fm.retry import call_with_retry
```

Then replace `fetch_eraldis_element` (originally lines 67-86) with:

```python
def fetch_eraldis_element(eraldis_ids: list[int]) -> pd.DataFrame:
    if not eraldis_ids:
        return pd.DataFrame([])
    batches = [
        eraldis_ids[i : i + ID_BATCH_SIZE] for i in range(0, len(eraldis_ids), ID_BATCH_SIZE)
    ]
    params_list = [
        {
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeName": ERALDIS_ELEMENT_TYPENAME,
            "outputFormat": "application/json",
            "CQL_FILTER": "eraldis_id IN ({})".format(
                ",".join(str(eid) for eid in batch)
            ),
        }
        for batch in batches
    ]
    contents = fetch_pages_concurrently(
        METSAREGISTER_OWS_URL, params_list, progress_label="composition batch"
    )
    rows = []
    for content in contents:
        data = json.loads(content)
        rows.extend(feature["properties"] for feature in data["features"])
    return pd.DataFrame(rows)
```

Everything else in the file (`summarize_composition`, `compute_species_shares`,
`fetch_classifier`, `enrich_eraldis`) is unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_enrich.py -v`
Expected: all tests pass (3 existing + 2 new = 5)

- [ ] **Step 5: Commit**

```bash
git add src/shroom_fm/enrich.py tests/test_enrich.py
git commit -m "perf: fetch eraldis_element composition batches concurrently with progress"
```

---

### Task 6: Real-scale verification and CLAUDE.md update

**Files:**
- Modify: `CLAUDE.md` (timing notes in "Running the full pipeline")
- No new source files.

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest tests/ -q`
Expected: 134 passed (123 baseline + 5 from Task 1 + 2 from Task 3 + 2 from Task 4 + 2
from Task 5)

- [ ] **Step 2: Run `download_eraldis.py` against real config and time it**

Run: `time uv run python scripts/download_eraldis.py`

Confirm: progress lines print as pages complete (not silent), the final stand count
matches the last known-good value for the user's real `RADIUS_KM`/`INNER_RADIUS_KM`
(65,577 stands), and wall-clock time is visibly lower than a fully sequential run would
take (no fixed target — the point is progress is now visible and the run no longer looks
hung).

- [ ] **Step 3: Run `enrich_eraldis.py` against the output of Step 2 and time it**

Run: `time uv run python scripts/enrich_eraldis.py`

Confirm: `composition batch` progress lines print, final enriched stand count still
65,577, and wall-clock time is meaningfully below the previous 471.2s baseline (this was
the single slowest step in the last full-pipeline run — record the new time in the
commit message for Step 5).

- [ ] **Step 4: Run `download_roads.py` against real config and time it**

Run: `time uv run python scripts/download_roads.py`

Confirm: `road page` progress lines print for both the roads and barriers layers, final
counts match the last known-good values (50,008 roads, 1,564 barriers), and wall-clock
time is at or below the previous ~4-5 minute baseline.

- [ ] **Step 5: Update CLAUDE.md's real-scale timing notes and commit**

In the "Running the full pipeline" section, update the per-step timing note that
currently reads (approximately) "step 7 takes several minutes... step 1 is fast" to
reflect that steps 1, 6 (`enrich_eraldis`), and 7 now fetch pages/batches concurrently
with progress output instead of running silently, and record the new measured times from
Steps 2-4 above in place of the old ones (471.2s for `enrich_eraldis` in particular).

```bash
git add CLAUDE.md
git commit -m "docs: record concurrent-fetch timing improvements in CLAUDE.md"
```
