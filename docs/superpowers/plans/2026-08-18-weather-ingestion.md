# Weather Ingestion (KAIA Radar + MEPS) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest KAIA's radar precipitation composites and MET Norway's MEPS/MET-Nordic
hourly analysis grids, and produce `data/weather_eraldis.geojson` — a per-`eraldis`
snapshot of rolling rainfall and temperature/humidity features, refreshed on demand.

**Architecture:** Two independent ingestion modules (`radar.py`, `meps.py`), each with its
own small local file cache and its own rolling-window accumulation, both producing a
GeoDataFrame of point values in `EPSG:3301`. A join module (`weather.py`) `sjoin_nearest`s
both onto `eraldis` centroids (same pattern as `access.py`) and computes freshness/quality
flags. `scripts/refresh_weather.py` orchestrates a full refresh run.

**Tech Stack:** `h5py` (radar HDF5/ODIM parsing), `xarray` + `netCDF4` (MEPS OPeNDAP
access), `geopandas`/`pyproj` (already installed — projection handling and spatial joins),
`concurrent.futures.ThreadPoolExecutor` (bounded radar-file download concurrency).

**Spec:** `docs/superpowers/specs/2026-08-18-weather-ingestion-design.md`

## Live-verified endpoint details (confirmed directly against the real production
services while writing this plan — not assumed from documentation)

**KAIA radar (`https://avaandmed.keskkonnaportaal.ee`):**
- Query: `POST /api/lists/active/items/query` with a JSON body:
  ```json
  {
    "filter": {"and": {"children": [
      {"underContentType": {"contentType": "0102FB01"}},
      {"isEqual": {"field": "Phenomenon", "value": "COMP"}},
      {"greaterThanOrEqual": {"field": "Timestamp", "value": "2026-08-18T09:43:07Z"}}
    ]}},
    "pageSize": 2000,
    "includeFileMetadata": true,
    "fields": ["Timestamp"]
  }
  ```
  Response: `{"numFound": N, "nextBookmark": "...", "documents": [{"id": 17561477,
  "metadata": {"Timestamp": "2026-08-18T12:45:02.0000000+03:00"}, "fileMetadata": [{"id": 1, ...}]}]}`.
  Page by resubmitting with `"bookmark": nextBookmark` until fewer than `pageSize`
  documents are returned. No API key required.
- Download: `GET /api/lists/active/items/{id}/files/{fileId}` (`fileId` is always `1` for
  these single-file radar documents) → raw HDF5 bytes.
- HDF5 structure (ODIM_H5/V2_2, confirmed by downloading and inspecting a real file):
  - `/dataset1/data1/data` — the raster, real shape `(1500, 1500)`, dtype `float32`.
  - `/dataset1/what` attrs: `gain` (float), `offset` (float), `nodata` (float sentinel,
    real value `65535.0`), `undetect` (float sentinel for "radar active, no rain
    detected", real value `0.0`), `quantity` (real value `b"RATE"` — instantaneous rate in
    **mm/h**, not accumulated mm — accumulation must be computed by multiplying each
    5-minute slot's rate by `5/60`).
  - `/where` attrs: `projdef` (proj4 string, real value
    `b"+proj=merc +a=6371000 +lat_0=68 +lon_0=25"`), `xsize`/`ysize` (int, real value
    `1500`/`1500`), `xscale`/`yscale` (float, meters/pixel, real value
    `≈359.07`/`≈346.70`), `UL_lon`/`UL_lat` (float, the upper-left pixel corner).
  - Decode: `valid = (raw != nodata) & (raw != undetect)`; `rate_mm_h = raw * gain + offset`
    where valid, else `NaN`.
- Real cadence: new composite every 5 minutes, confirmed via a live query for the last 2
  hours (returned files at `:02`, `:07`, `:12`... minute marks — i.e. actual publish times
  land close to but not exactly on 5-minute boundaries; do not assume exact `:00`/`:05`
  alignment). A consistent ~5-6 minute lag exists between a file's `Timestamp` (radar scan
  time) and when it becomes downloadable.

**MEPS/MET-Nordic (`https://thredds.met.no`):**
- Hourly analysis file (best-estimate observed conditions, not a forecast — this is what
  the rolling backward-looking window needs): OPeNDAP URL
  `https://thredds.met.no/thredds/dodsC/metpparchive/{YYYY}/{MM}/{DD}/met_analysis_1_0km_nordic_{YYYY}{MM}{DD}T{HH}Z.nc`
  for any already-archived hour, or
  `https://thredds.met.no/thredds/dodsC/metpplatest/met_analysis_1_0km_nordic_latest.nc`
  for the current hour if it isn't archived yet.
- Confirmed real variables via a live `xarray.open_dataset(...)` call: `air_temperature_2m`
  (Kelvin, dims `(time, y, x)`), `relative_humidity_2m` (unitless 0-1 fraction, dims
  `(time, y, x)`), `latitude`/`longitude` (2D grids, dims `(y, x)`), `projection_lcc` (grid
  mapping variable). An hourly analysis file's `time` dimension has exactly one value.
- Confirmed real projection: `projection_lcc` attrs give
  `proj4 = "+proj=lcc +lat_0=63 +lon_0=15 +lat_1=63 +lat_2=63 +no_defs +R=6371000"`
  directly — use this proj4 string, don't hand-derive Lambert Conformal Conic parameters.
- Grid is Nordic-wide (`2321 x 1796`, ~217MB per file if fully loaded) — always subset via
  OPeNDAP's remote slicing (`.sel()`/`.isel()` on `y`/`x` after projecting a small bbox into
  the LCC CRS) rather than downloading the full grid.
- Confirmed live that both `x` and `y` coordinates are strictly ascending
  (`ds.x.diff('x') > 0` and `ds.y.diff('y') > 0` both all-`True`) — this is what makes
  `dataset.sel(x=slice(min_x, max_x), y=slice(min_y, max_y))` (label-based slicing) work
  correctly; xarray's `.sel()` slicing silently returns an empty result for a descending
  coordinate sliced with an ascending `(min, max)` range, so this was worth confirming
  rather than assuming.

## Global Constraints

- All zonal joins (radar points, MEPS points, onto `eraldis`) happen in `EPSG:3301`
  (`ESTONIAN_GRID_CRS`, imported from `shroom_fm.eraldis`) — matches `access.py`'s existing
  pattern, not WGS84.
- Radar rolling window: 14 days. MEPS rolling window: 3 days (per the spec's trimmed v1
  output columns — no `temp_*_7d`/`rh_*_7d`).
- Output columns (exact names): `rain_3d_mm`, `rain_7d_mm`, `rain_14d_mm`,
  `hours_since_rain`, `wet_hours_72h`, `temp_mean_3d`, `temp_night_mean_3d`, `rh_mean_3d`,
  `rh_night_mean_3d`, `as_of`, `weather_data_coverage`, `weather_data_quality`.
- "Measurable rain" threshold: a 5-minute radar slot counts as wet when its decoded rate is
  `> 0.0` mm/h (the `undetect` sentinel already separately encodes "no rain detected", so a
  positive decoded rate is unambiguous).
- Radar degradation threshold: if fewer than 70% of the expected 5-minute slots in a
  window are present in the cache, that window's rain/wet-hours columns become `None` and
  `weather_data_quality` includes `"partial_radar_gap"`. (`MIN_RADAR_COVERAGE = 0.7`)
- MEPS staleness threshold: if the most recent available hourly analysis file is more than
  6 hours older than `now`, `temp_*`/`rh_*` columns become `None` and
  `weather_data_quality` includes `"stale_meps"`. (`MAX_MEPS_STALENESS_HOURS = 6`)
- `weather_data_quality` is `"complete"` when no degradation applied, otherwise a
  semicolon-joined string of every applicable degradation flag (e.g.
  `"partial_radar_gap;stale_meps"`).
- Night hours (for `temp_night_mean_3d`/`rh_night_mean_3d`): 21:00-06:00 **local Estonian
  time** (`zoneinfo.ZoneInfo("Europe/Tallinn")`, correctly handling EET/EEST — not a fixed
  UTC offset).
- Never call `datetime.now()`/`datetime.utcnow()` inside accumulation functions — `now` is
  always an explicit parameter, injected by the caller (`scripts/refresh_weather.py` in
  production, a fixed test value in tests). This is what makes the rolling-window logic
  testable without mocking the clock.
- No test hits a live network endpoint — all radar/MEPS parsing tests use small synthetic
  fixtures built in-test (matching every existing test file in this project).
- Baseline before this plan: 136 tests passing (`uv run pytest tests/ -q`).

---

### Task 1: Radar file cache — query, download, expire

**Files:**
- Modify: `pyproject.toml` (add `h5py`, `xarray`, `netcdf4` dependencies)
- Modify: `src/shroom_fm/retry.py` (add `post_with_retry`)
- Create: `src/shroom_fm/radar.py`
- Test: `tests/test_radar.py`
- Test: `tests/test_retry.py` (add `post_with_retry` coverage)

**Interfaces:**
- Produces: `post_with_retry(url, *, max_attempts=DEFAULT_MAX_ATTEMPTS, backoff_seconds=DEFAULT_BACKOFF_SECONDS, sleep=time.sleep, **kwargs) -> requests.Response`
  in `retry.py`.
- Produces (in `radar.py`): `KAIA_QUERY_URL`, `KAIA_DOWNLOAD_URL_TEMPLATE`,
  `RADAR_CONTENT_TYPE`, `RADAR_PHENOMENON`, `MAX_WORKERS`;
  `query_radar_documents(since: datetime) -> list[dict]` (each dict:
  `{"id": int, "file_id": int, "timestamp": datetime}`);
  `download_radar_composite(document: dict, cache_dir: Path) -> Path`;
  `fetch_new_radar_composites(cache_dir: Path, since: datetime, *, max_workers: int = MAX_WORKERS) -> list[Path]`;
  `expire_old_radar_composites(cache_dir: Path, cutoff: datetime) -> None`;
  `cached_radar_files(cache_dir: Path, window_start: datetime, window_end: datetime) -> list[Path]`;
  `newest_cached_radar_timestamp(cache_dir: Path) -> datetime | None`;
  `cached_radar_timestamp(path: Path) -> datetime` (parses the cache filename).

- [ ] **Step 1: Add the new dependencies**

Run: `uv add h5py xarray netcdf4`

This updates `pyproject.toml` and `uv.lock` with resolved version floors — don't
hand-write version numbers.

- [ ] **Step 2: Write the failing test for `post_with_retry`**

Add to `tests/test_retry.py`:

```python
from shroom_fm.retry import post_with_retry


def test_post_with_retry_sends_json_body_and_returns_response(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return _FakeResponse(200)

    monkeypatch.setattr("shroom_fm.retry.requests.post", fake_post)

    result = post_with_retry(
        "http://example.com", json={"a": 1}, timeout=30, sleep=lambda s: None
    )

    assert result.status_code == 200
    assert calls == [("http://example.com", {"json": {"a": 1}, "timeout": 30})]


def test_post_with_retry_retries_server_error_then_succeeds(monkeypatch):
    responses = [_FakeResponse(503), _FakeResponse(200)]

    def fake_post(url, **kwargs):
        return responses.pop(0)

    monkeypatch.setattr("shroom_fm.retry.requests.post", fake_post)

    result = post_with_retry("http://example.com", sleep=lambda s: None)

    assert result.status_code == 200
```

- [ ] **Step 3: Run to verify it fails**

Run: `uv run pytest tests/test_retry.py -v`
Expected: the 2 new tests FAIL with `ImportError`/`AttributeError` (`post_with_retry`
doesn't exist yet).

- [ ] **Step 4: Add `post_with_retry` to `src/shroom_fm/retry.py`**

Add this function after `get_with_retry` (keep everything else in the file unchanged):

```python
def post_with_retry(
    url: str,
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff_seconds: tuple[float, ...] = DEFAULT_BACKOFF_SECONDS,
    sleep=time.sleep,
    **kwargs,
):
    def _post():
        response = requests.post(url, **kwargs)
        response.raise_for_status()
        return response

    return call_with_retry(
        _post, max_attempts=max_attempts, backoff_seconds=backoff_seconds, sleep=sleep
    )
```

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/test_retry.py -v`
Expected: all pass (10 existing + 2 new = 12)

- [ ] **Step 6: Write the failing tests for `radar.py`'s cache functions**

Create `tests/test_radar.py`:

```python
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
```

- [ ] **Step 7: Run to verify it fails**

Run: `uv run pytest tests/test_radar.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shroom_fm.radar'`

- [ ] **Step 8: Implement `src/shroom_fm/radar.py`**

```python
import concurrent.futures
from datetime import datetime, timezone
from pathlib import Path

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
        if len(data["documents"]) < _PAGE_SIZE:
            break
        bookmark = data["nextBookmark"]
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
```

Note: `query_radar_documents` uses `datetime.fromisoformat(...).astimezone(timezone.utc)`
— real KAIA `Timestamp` values include a `+03:00`/`+02:00` offset (e.g.
`"2026-08-18T12:45:02.0000000+03:00"`); Python's `fromisoformat` on 3.12 handles the
fractional-seconds-plus-offset format, and `.astimezone(timezone.utc)` normalizes it so
every downstream comparison is in UTC.

- [ ] **Step 9: Run to verify it passes**

Run: `uv run pytest tests/test_radar.py -v`
Expected: 9 passed

- [ ] **Step 10: Run the full suite and commit**

Run: `uv run pytest tests/ -q`
Expected: 147 passed (136 baseline + 2 retry tests + 9 radar tests)

```bash
git add pyproject.toml uv.lock src/shroom_fm/retry.py src/shroom_fm/radar.py tests/test_retry.py tests/test_radar.py
git commit -m "feat: add KAIA radar composite file cache (query, download, expire)"
```

---

### Task 2: Radar parsing and rainfall accumulation

**Files:**
- Modify: `src/shroom_fm/radar.py` (add parsing/accumulation functions)
- Modify: `tests/test_radar.py` (add parsing/accumulation tests)

**Interfaces:**
- Consumes: `cached_radar_files`, `cached_radar_timestamp` from Task 1 (same file).
- Produces: `read_radar_full_georef(path: Path) -> dict` (reads only `/where` attrs, not
  the raster — cheap);
  `radar_bbox_slice(georef: dict, bounds_wgs84: tuple[float, float, float, float], *, buffer_pixels: int = 5) -> tuple[slice, slice]`
  (row slice, col slice trimming the full grid to the area of interest);
  `parse_radar_composite(path: Path, *, row_slice: slice = slice(None), col_slice: slice = slice(None)) -> tuple[np.ndarray, dict]`
  (rate_mm_h array shaped `(ysize, xsize)` — the *sliced* size — with `NaN` where
  invalid, and a `georef` dict with keys `projdef`, `xsize`, `ysize`, `xscale`, `yscale`,
  `ul_lon`, `ul_lat`, `row_offset`, `col_offset`);
  `radar_pixel_centers(georef: dict) -> gpd.GeoDataFrame` (columns `row`, `col`, geometry
  in the radar's native CRS, built from `georef["projdef"]` and the `row_offset`/
  `col_offset` so sliced grids still place pixels at their true absolute coordinates);
  `accumulate_rainfall(cache_dir: Path, now: datetime, eraldis_bounds_wgs84: tuple[float, float, float, float]) -> tuple[gpd.GeoDataFrame, float]`
  — returns `(points_gdf, coverage)` where `points_gdf` (CRS `EPSG:3301`) has columns
  `rain_3d_mm`, `rain_7d_mm`, `rain_14d_mm`, `hours_since_rain`, `wet_hours_72h`, and
  `coverage` is the fraction (0.0-1.0) of expected 5-minute slots present in the 14-day
  window. Internally slices every file to `eraldis_bounds_wgs84`'s bbox (via
  `radar_bbox_slice`) before per-pixel processing — the real grid is 1500x1500 and the
  rolling window can span ~4000 files, so trimming to the relevant sub-region (a few
  hundred pixels per side for a home-radius-sized area) up front, rather than processing
  the whole country each time, is necessary for this to run in a reasonable time.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_radar.py`:

```python
import h5py
import numpy as np
import pytest

from shroom_fm.radar import (
    accumulate_rainfall,
    parse_radar_composite,
    radar_bbox_slice,
    radar_pixel_centers,
)


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
    from datetime import datetime, timezone

    cache_dir = tmp_path / "radar_cache"
    cache_dir.mkdir()

    def _utc(*a):
        return datetime(*a, tzinfo=timezone.utc)

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
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_radar.py -v`
Expected: the 3 new tests FAIL with `AttributeError`/`ImportError`.

- [ ] **Step 3: Implement the parsing/accumulation functions**

Add to `src/shroom_fm/radar.py` (add these imports at the top alongside the existing
ones: `h5py`, `numpy as np`, `geopandas as gpd`, `pyproj`; add these functions at the end
of the file):

```python
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


def _radar_origin(georef: dict) -> tuple[float, float, "pyproj.CRS"]:
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
) -> tuple["np.ndarray", dict]:
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


def radar_pixel_centers(georef: dict) -> "gpd.GeoDataFrame":
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
) -> tuple["gpd.GeoDataFrame", float]:
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
```

Note: for the small 2x2 synthetic fixtures used in the tests above, `radar_bbox_slice`'s
generously-sized test bboxes cover the whole fixture grid, so slicing is a no-op there —
its own dedicated test (`test_radar_bbox_slice_covers_a_small_eraldis_bbox_within_the_full_grid`)
is what actually exercises the trimming behavior against real-scale grid dimensions.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_radar.py -v`
Expected: 13 passed (9 from Task 1 + 4 new)

- [ ] **Step 5: Commit**

```bash
git add src/shroom_fm/radar.py tests/test_radar.py
git commit -m "feat: parse KAIA radar composites and accumulate rolling rainfall"
```

---

### Task 3: MEPS temperature/humidity ingestion

**Files:**
- Create: `src/shroom_fm/meps.py`
- Test: `tests/test_meps.py`

**Interfaces:**
- Produces: `MEPS_LATEST_URL`, `MEPS_ARCHIVE_URL_TEMPLATE`, `MEPS_LCC_PROJ4`;
  `meps_hourly_url(hour: datetime) -> str` (returns the archive URL for that hour);
  `fetch_meps_hourly(hour: datetime, bbox_wgs84: tuple[float, float, float, float]) -> "xr.Dataset | None"`
  (returns `None` if neither the archive nor `_latest.nc` has that hour — a fetch
  failure, not an exception, since a single missing hour is an expected, tolerable gap);
  `meps_dataset_to_points(dataset: "xr.Dataset") -> gpd.GeoDataFrame` (columns
  `temp_c`, `rh_pct`, geometry in `EPSG:3301`);
  `accumulate_meps_features(now: datetime, bbox_wgs84: tuple[float, float, float, float]) -> tuple[gpd.GeoDataFrame, float, datetime | None]`
  — returns `(points_gdf, coverage, newest_available_hour)`, `points_gdf` (CRS
  `EPSG:3301`) has columns `temp_mean_3d`, `temp_night_mean_3d`, `rh_mean_3d`,
  `rh_night_mean_3d`; `coverage` is the fraction of the 72 expected hourly files actually
  fetched; `newest_available_hour` is used by the join step to compute MEPS staleness.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_meps.py`:

```python
from datetime import datetime, timezone

import numpy as np
import pytest
import xarray as xr

from shroom_fm.meps import (
    accumulate_meps_features,
    meps_dataset_to_points,
    meps_hourly_url,
)


def _utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def _fake_dataset(temp_k: float, rh_frac: float) -> xr.Dataset:
    # Tiny 2x2 grid in the real LCC projection's coordinate space, centered near
    # Estonia's real LCC x/y (from the live-verified projection_lcc parameters).
    x = np.array([300000.0, 301000.0])
    y = np.array([700000.0, 701000.0])
    temp = np.full((1, 2, 2), temp_k, dtype=np.float32)
    rh = np.full((1, 2, 2), rh_frac, dtype=np.float32)
    lon, lat = np.meshgrid([24.7, 24.8], [59.4, 59.5])
    return xr.Dataset(
        {
            "air_temperature_2m": (("time", "y", "x"), temp),
            "relative_humidity_2m": (("time", "y", "x"), rh),
            "latitude": (("y", "x"), lat),
            "longitude": (("y", "x"), lon),
        },
        coords={"time": [np.datetime64("2026-08-15T00:00:00")], "y": y, "x": x},
    )


def test_meps_hourly_url_builds_archive_path():
    url = meps_hourly_url(_utc(2026, 8, 15, 6))
    assert url == (
        "https://thredds.met.no/thredds/dodsC/metpparchive/2026/08/15/"
        "met_analysis_1_0km_nordic_20260815T06Z.nc"
    )


def test_meps_dataset_to_points_converts_units_and_flattens_grid():
    dataset = _fake_dataset(temp_k=283.15, rh_frac=0.8)

    points = meps_dataset_to_points(dataset)

    assert len(points) == 4
    assert points["temp_c"].iloc[0] == pytest.approx(10.0)
    assert points["rh_pct"].iloc[0] == pytest.approx(80.0)
    assert points.crs is not None


def test_accumulate_meps_features_computes_day_night_means(monkeypatch):
    # 2 fake hours: one clearly "day" (noon local), one clearly "night" (02:00 local)
    fetched = {}

    def fake_fetch(hour, bbox):
        if hour == _utc(2026, 8, 14, 9):  # 12:00 EEST — day
            return _fake_dataset(temp_k=293.15, rh_frac=0.5)  # 20C
        if hour == _utc(2026, 8, 14, 23):  # 02:00 EEST next day — night
            return _fake_dataset(temp_k=283.15, rh_frac=0.9)  # 10C
        return None

    monkeypatch.setattr("shroom_fm.meps.fetch_meps_hourly", fake_fetch)

    now = _utc(2026, 8, 15, 0)  # only these 2 fake hours will resolve; rest are gaps
    bbox = (24.0, 59.0, 25.0, 60.0)

    points, coverage, newest = accumulate_meps_features(now, bbox)

    assert coverage == pytest.approx(2 / 72)
    row0 = points.iloc[0]
    assert row0["temp_mean_3d"] == pytest.approx((20.0 + 10.0) / 2)
    assert row0["temp_night_mean_3d"] == pytest.approx(10.0)
    assert row0["rh_night_mean_3d"] == pytest.approx(90.0)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_meps.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shroom_fm.meps'`

- [ ] **Step 3: Implement `src/shroom_fm/meps.py`**

```python
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import geopandas as gpd
import numpy as np
import pandas as pd
import pyproj
import xarray as xr

MEPS_LATEST_URL = (
    "https://thredds.met.no/thredds/dodsC/metpplatest/met_analysis_1_0km_nordic_latest.nc"
)
MEPS_ARCHIVE_URL_TEMPLATE = (
    "https://thredds.met.no/thredds/dodsC/metpparchive/{year:04d}/{month:02d}/{day:02d}/"
    "met_analysis_1_0km_nordic_{year:04d}{month:02d}{day:02d}T{hour:02d}Z.nc"
)
MEPS_LCC_PROJ4 = "+proj=lcc +lat_0=63 +lon_0=15 +lat_1=63 +lat_2=63 +no_defs +R=6371000"
TALLINN_TZ = ZoneInfo("Europe/Tallinn")
_MEPS_WINDOW_HOURS = 72
_MEPS_STALENESS_HOURS = 6


def meps_hourly_url(hour: datetime) -> str:
    return MEPS_ARCHIVE_URL_TEMPLATE.format(
        year=hour.year, month=hour.month, day=hour.day, hour=hour.hour
    )


def fetch_meps_hourly(
    hour: datetime, bbox_wgs84: tuple[float, float, float, float]
) -> "xr.Dataset | None":
    lcc_crs = pyproj.CRS.from_proj4(MEPS_LCC_PROJ4)
    to_lcc = pyproj.Transformer.from_crs("EPSG:4326", lcc_crs, always_xy=True)
    min_lon, min_lat, max_lon, max_lat = bbox_wgs84
    xs, ys = to_lcc.transform([min_lon, max_lon], [min_lat, max_lat])
    x_slice = slice(min(xs), max(xs))
    y_slice = slice(min(ys), max(ys))

    for url in (meps_hourly_url(hour), MEPS_LATEST_URL):
        try:
            dataset = xr.open_dataset(url)
        except OSError:
            continue
        try:
            subset = dataset.sel(x=x_slice, y=y_slice)
            if subset.sizes.get("x", 0) == 0 or subset.sizes.get("y", 0) == 0:
                continue
            return subset.load()
        finally:
            dataset.close()
    return None


def meps_dataset_to_points(dataset: "xr.Dataset") -> "gpd.GeoDataFrame":
    lcc_crs = pyproj.CRS.from_proj4(MEPS_LCC_PROJ4)
    xx, yy = np.meshgrid(dataset["x"].values, dataset["y"].values)
    temp_c = dataset["air_temperature_2m"].isel(time=0).values - 273.15
    rh_pct = dataset["relative_humidity_2m"].isel(time=0).values * 100.0
    return gpd.GeoDataFrame(
        {"temp_c": temp_c.ravel(), "rh_pct": rh_pct.ravel()},
        geometry=gpd.points_from_xy(xx.ravel(), yy.ravel()),
        crs=lcc_crs,
    )


def _is_night(hour_utc: datetime) -> bool:
    local = hour_utc.astimezone(TALLINN_TZ)
    return local.hour >= 21 or local.hour < 6


def accumulate_meps_features(
    now: datetime, bbox_wgs84: tuple[float, float, float, float]
) -> tuple["gpd.GeoDataFrame", float, "datetime | None"]:
    hours = [
        now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=i)
        for i in range(_MEPS_WINDOW_HOURS)
    ]

    all_points = []
    fetched_count = 0
    newest_available: datetime | None = None
    for hour in hours:
        dataset = fetch_meps_hourly(hour, bbox_wgs84)
        if dataset is None:
            continue
        fetched_count += 1
        if newest_available is None or hour > newest_available:
            newest_available = hour
        points = meps_dataset_to_points(dataset)
        points["hour"] = hour
        points["is_night"] = _is_night(hour)
        points["point_id"] = range(len(points))
        all_points.append(points)

    coverage = fetched_count / _MEPS_WINDOW_HOURS

    if not all_points:
        empty = gpd.GeoDataFrame(
            {
                "temp_mean_3d": [],
                "temp_night_mean_3d": [],
                "rh_mean_3d": [],
                "rh_night_mean_3d": [],
            },
            geometry=[],
            crs="EPSG:3301",
        )
        return empty, coverage, newest_available

    combined = pd.concat(all_points, ignore_index=True)
    grouped = combined.groupby("point_id")
    night = combined[combined["is_night"]].groupby("point_id")

    result = grouped[["geometry"]].first()
    result["temp_mean_3d"] = grouped["temp_c"].mean()
    result["rh_mean_3d"] = grouped["rh_pct"].mean()
    result["temp_night_mean_3d"] = night["temp_c"].mean()
    result["rh_night_mean_3d"] = night["rh_pct"].mean()

    result = gpd.GeoDataFrame(result, geometry="geometry", crs=combined.crs)
    result = result.to_crs("EPSG:3301")
    return result, coverage, newest_available
```

Note: `fetch_meps_hourly` swallows `OSError` from `xr.open_dataset` (the real error class
`netCDF4`/`xarray` raise for a 404/unreachable OPeNDAP URL) — a single missing hour is an
expected, tolerable gap in the rolling window, not a fatal error; the coverage fraction is
what surfaces the gap to `weather_data_quality`.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_meps.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/shroom_fm/meps.py tests/test_meps.py
git commit -m "feat: fetch and accumulate MEPS temperature/humidity features"
```

---

### Task 4: Join radar and MEPS features onto eraldis, with quality flags

**Files:**
- Create: `src/shroom_fm/weather.py`
- Test: `tests/test_weather.py`

**Interfaces:**
- Consumes: `accumulate_rainfall` (Task 2), `accumulate_meps_features` (Task 3),
  `ESTONIAN_GRID_CRS` from `shroom_fm.eraldis`.
- Produces: `MIN_RADAR_COVERAGE = 0.7`, `MAX_MEPS_STALENESS_HOURS = 6`;
  `weather_data_quality(radar_coverage: float, meps_newest_hour: datetime | None, now: datetime) -> str`;
  `refresh_weather(eraldis_gdf: gpd.GeoDataFrame, radar_cache_dir: Path, now: datetime) -> gpd.GeoDataFrame`
  — the top-level function `scripts/refresh_weather.py` calls; joins both feature sets
  onto `eraldis_gdf` (same `_nearest_join`-style pattern as `access.py`) and adds
  `as_of`, `weather_data_coverage`, `weather_data_quality` columns; degraded columns
  become `None` per the coverage/staleness thresholds.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_weather.py`:

```python
from datetime import datetime, timedelta, timezone

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

from shroom_fm.weather import refresh_weather, weather_data_quality


def _utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def test_weather_data_quality_is_complete_when_nothing_degraded():
    now = _utc(2026, 8, 18, 12)
    assert weather_data_quality(0.9, now - timedelta(hours=1), now) == "complete"


def test_weather_data_quality_flags_partial_radar_gap():
    now = _utc(2026, 8, 18, 12)
    assert (
        weather_data_quality(0.5, now - timedelta(hours=1), now)
        == "partial_radar_gap"
    )


def test_weather_data_quality_flags_stale_meps():
    now = _utc(2026, 8, 18, 12)
    assert weather_data_quality(0.9, now - timedelta(hours=10), now) == "stale_meps"


def test_weather_data_quality_flags_missing_meps_as_stale():
    now = _utc(2026, 8, 18, 12)
    assert weather_data_quality(0.9, None, now) == "stale_meps"


def test_weather_data_quality_joins_multiple_flags():
    now = _utc(2026, 8, 18, 12)
    assert (
        weather_data_quality(0.5, None, now)
        == "partial_radar_gap;stale_meps"
    )


def test_refresh_weather_joins_nearest_radar_and_meps_points(monkeypatch, tmp_path):
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1]}, geometry=[Point(24.0, 59.0)], crs="EPSG:4326"
    )
    now = _utc(2026, 8, 18, 12)

    radar_points = gpd.GeoDataFrame(
        {
            "rain_3d_mm": [5.0],
            "rain_7d_mm": [10.0],
            "rain_14d_mm": [20.0],
            "hours_since_rain": [3.0],
            "wet_hours_72h": [1.0],
        },
        geometry=[Point(500000, 6500000)],
        crs="EPSG:3301",
    )
    meps_points = gpd.GeoDataFrame(
        {
            "temp_mean_3d": [15.0],
            "temp_night_mean_3d": [10.0],
            "rh_mean_3d": [70.0],
            "rh_night_mean_3d": [85.0],
        },
        geometry=[Point(500000, 6500000)],
        crs="EPSG:3301",
    )

    monkeypatch.setattr(
        "shroom_fm.weather.accumulate_rainfall",
        lambda cache_dir, now_, bounds: (radar_points, 0.9),
    )
    monkeypatch.setattr(
        "shroom_fm.weather.accumulate_meps_features",
        lambda now_, bounds: (meps_points, 0.9, now_ - timedelta(hours=1)),
    )

    result = refresh_weather(eraldis_gdf, tmp_path / "radar_cache", now)

    assert result.loc[0, "rain_3d_mm"] == pytest.approx(5.0)
    assert result.loc[0, "temp_mean_3d"] == pytest.approx(15.0)
    assert result.loc[0, "weather_data_quality"] == "complete"
    assert result.loc[0, "weather_data_coverage"] == pytest.approx(0.9)
    assert result.loc[0, "as_of"] == now
    assert result.crs == "EPSG:4326"


def test_refresh_weather_nulls_degraded_columns(monkeypatch, tmp_path):
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1]}, geometry=[Point(24.0, 59.0)], crs="EPSG:4326"
    )
    now = _utc(2026, 8, 18, 12)

    radar_points = gpd.GeoDataFrame(
        {
            "rain_3d_mm": [5.0],
            "rain_7d_mm": [10.0],
            "rain_14d_mm": [20.0],
            "hours_since_rain": [3.0],
            "wet_hours_72h": [1.0],
        },
        geometry=[Point(500000, 6500000)],
        crs="EPSG:3301",
    )
    meps_points = gpd.GeoDataFrame(
        {
            "temp_mean_3d": [15.0],
            "temp_night_mean_3d": [10.0],
            "rh_mean_3d": [70.0],
            "rh_night_mean_3d": [85.0],
        },
        geometry=[Point(500000, 6500000)],
        crs="EPSG:3301",
    )

    monkeypatch.setattr(
        "shroom_fm.weather.accumulate_rainfall",
        lambda cache_dir, now_, bounds: (radar_points, 0.2),  # below threshold
    )
    monkeypatch.setattr(
        "shroom_fm.weather.accumulate_meps_features",
        lambda now_, bounds: (meps_points, 0.9, now_ - timedelta(hours=1)),
    )

    result = refresh_weather(eraldis_gdf, tmp_path / "radar_cache", now)

    assert result.loc[0, "rain_3d_mm"] is None
    assert result.loc[0, "temp_mean_3d"] == pytest.approx(15.0)  # meps unaffected
    assert result.loc[0, "weather_data_quality"] == "partial_radar_gap"


def test_refresh_weather_nulls_meps_columns_when_stale(monkeypatch, tmp_path):
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1]}, geometry=[Point(24.0, 59.0)], crs="EPSG:4326"
    )
    now = _utc(2026, 8, 18, 12)

    radar_points = gpd.GeoDataFrame(
        {
            "rain_3d_mm": [5.0],
            "rain_7d_mm": [10.0],
            "rain_14d_mm": [20.0],
            "hours_since_rain": [3.0],
            "wet_hours_72h": [1.0],
        },
        geometry=[Point(500000, 6500000)],
        crs="EPSG:3301",
    )
    meps_points = gpd.GeoDataFrame(
        {
            "temp_mean_3d": [15.0],
            "temp_night_mean_3d": [10.0],
            "rh_mean_3d": [70.0],
            "rh_night_mean_3d": [85.0],
        },
        geometry=[Point(500000, 6500000)],
        crs="EPSG:3301",
    )

    monkeypatch.setattr(
        "shroom_fm.weather.accumulate_rainfall",
        lambda cache_dir, now_, bounds: (radar_points, 0.9),
    )
    monkeypatch.setattr(
        "shroom_fm.weather.accumulate_meps_features",
        # newest available hour is 10h old — beyond MAX_MEPS_STALENESS_HOURS (6)
        lambda now_, bounds: (meps_points, 0.9, now_ - timedelta(hours=10)),
    )

    result = refresh_weather(eraldis_gdf, tmp_path / "radar_cache", now)

    assert result.loc[0, "temp_mean_3d"] is None
    assert result.loc[0, "rh_night_mean_3d"] is None
    assert result.loc[0, "rain_3d_mm"] == pytest.approx(5.0)  # radar unaffected
    assert result.loc[0, "weather_data_quality"] == "stale_meps"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_weather.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shroom_fm.weather'`

- [ ] **Step 3: Implement `src/shroom_fm/weather.py`**

```python
from datetime import datetime, timedelta
from pathlib import Path

import geopandas as gpd
import pandas as pd

from shroom_fm.eraldis import ESTONIAN_GRID_CRS
from shroom_fm.meps import accumulate_meps_features
from shroom_fm.radar import accumulate_rainfall

MIN_RADAR_COVERAGE = 0.7
MAX_MEPS_STALENESS_HOURS = 6

_RADAR_COLUMNS = (
    "rain_3d_mm",
    "rain_7d_mm",
    "rain_14d_mm",
    "hours_since_rain",
    "wet_hours_72h",
)
_MEPS_COLUMNS = (
    "temp_mean_3d",
    "temp_night_mean_3d",
    "rh_mean_3d",
    "rh_night_mean_3d",
)


def _is_meps_stale(meps_newest_hour: "datetime | None", now: datetime) -> bool:
    return meps_newest_hour is None or (now - meps_newest_hour) > timedelta(
        hours=MAX_MEPS_STALENESS_HOURS
    )


def weather_data_quality(
    radar_coverage: float, meps_newest_hour: "datetime | None", now: datetime
) -> str:
    flags = []
    if radar_coverage < MIN_RADAR_COVERAGE:
        flags.append("partial_radar_gap")
    if _is_meps_stale(meps_newest_hour, now):
        flags.append("stale_meps")
    return ";".join(flags) if flags else "complete"


def _nearest_join(
    eraldis_projected: gpd.GeoDataFrame,
    points: gpd.GeoDataFrame,
    columns: tuple[str, ...],
) -> pd.DataFrame:
    if points.empty:
        return pd.DataFrame(
            {col: [None] * len(eraldis_projected) for col in columns},
            index=eraldis_projected.index,
        )
    joined = gpd.sjoin_nearest(
        eraldis_projected[["geometry"]],
        points[["geometry", *columns]],
        how="left",
    )
    return joined.groupby(level=0).first().reindex(eraldis_projected.index)[list(columns)]


def refresh_weather(
    eraldis_gdf: gpd.GeoDataFrame, radar_cache_dir: Path, now: datetime
) -> gpd.GeoDataFrame:
    crs = eraldis_gdf.crs
    bounds = tuple(eraldis_gdf.to_crs("EPSG:4326").total_bounds)

    radar_points, radar_coverage = accumulate_rainfall(radar_cache_dir, now, bounds)
    meps_points, meps_coverage, meps_newest_hour = accumulate_meps_features(now, bounds)

    eraldis_projected = eraldis_gdf.to_crs(ESTONIAN_GRID_CRS)
    radar_joined = _nearest_join(eraldis_projected, radar_points, _RADAR_COLUMNS)
    meps_joined = _nearest_join(eraldis_projected, meps_points, _MEPS_COLUMNS)

    result = eraldis_gdf.copy()
    quality = weather_data_quality(radar_coverage, meps_newest_hour, now)
    radar_degraded = radar_coverage < MIN_RADAR_COVERAGE
    meps_stale = _is_meps_stale(meps_newest_hour, now)

    for col in _RADAR_COLUMNS:
        result[col] = [
            None if radar_degraded or pd.isna(v) else v for v in radar_joined[col]
        ]
    for col in _MEPS_COLUMNS:
        result[col] = [
            None if meps_stale or pd.isna(v) else v for v in meps_joined[col]
        ]

    result["as_of"] = now
    result["weather_data_coverage"] = radar_coverage
    result["weather_data_quality"] = quality

    return gpd.GeoDataFrame(result, geometry="geometry", crs=crs)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_weather.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/shroom_fm/weather.py tests/test_weather.py
git commit -m "feat: join radar/MEPS features onto eraldis with freshness quality flags"
```

---

### Task 5: `scripts/refresh_weather.py` orchestrator, real verification, CLAUDE.md update

**Files:**
- Create: `scripts/refresh_weather.py`
- Modify: `.gitignore` (add `data/radar_cache/`, `data/weather_eraldis.geojson`)
- Modify: `CLAUDE.md` (document the new standalone weather-refresh step)

- [ ] **Step 1: Write `scripts/refresh_weather.py`**

```python
from datetime import datetime, timedelta, timezone
from pathlib import Path

import geopandas as gpd

from shroom_fm.radar import expire_old_radar_composites, fetch_new_radar_composites, newest_cached_radar_timestamp
from shroom_fm.weather import refresh_weather

RADAR_CACHE_DIR = Path("data/radar_cache")
ERALDIS_INPUT_PATH = Path("data/eraldis.geojson")
OUTPUT_PATH = Path("data/weather_eraldis.geojson")
RADAR_WINDOW_DAYS = 14


def main() -> None:
    now = datetime.now(timezone.utc)

    since = newest_cached_radar_timestamp(RADAR_CACHE_DIR)
    if since is None:
        since = now - timedelta(days=RADAR_WINDOW_DAYS)
    new_files = fetch_new_radar_composites(RADAR_CACHE_DIR, since)
    print(f"{len(new_files)} new radar composites downloaded")

    expire_old_radar_composites(RADAR_CACHE_DIR, now - timedelta(days=RADAR_WINDOW_DAYS))

    eraldis_gdf = gpd.read_file(ERALDIS_INPUT_PATH)
    result = refresh_weather(eraldis_gdf, RADAR_CACHE_DIR, now)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    result.to_file(OUTPUT_PATH, driver="GeoJSON")

    print(f"{len(result)} eraldis stands weather-scored, saved to {OUTPUT_PATH}")
    print(f"quality breakdown: {result['weather_data_quality'].value_counts().to_dict()}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Add gitignore entries**

Add to `.gitignore`, in the "shroom-fm local output" section alongside the existing
`data/*.geojson` entries:

```
data/radar_cache/
data/weather_eraldis.geojson
```

- [ ] **Step 3: Run the full test suite**

Run: `uv run pytest tests/ -q`
Expected: 162 passed (136 baseline + 2 retry + 9 radar (Task 1) + 4 radar (Task 2) + 3
meps (Task 3) + 8 weather (Task 4); `refresh_weather.py` itself adds no new tests — it's
a thin orchestrator script with no independent unit-testable logic beyond what Tasks 1-4
already cover, matching this project's established precedent for `download_eraldis.py`/
`download_roads.py`, neither of which has its own test file).

- [ ] **Step 4: Run the real script against production config and verify**

Run: `time uv run python scripts/refresh_weather.py`

This is a real, live run against the actual KAIA and MET Norway production servers using
your real `config.toml` home coordinates and your real `data/eraldis.geojson`. Confirm:
- Progress lines print for radar downloads (not silent).
- The output file `data/weather_eraldis.geojson` is created with all 12 required columns
  (`rain_3d_mm`, `rain_7d_mm`, `rain_14d_mm`, `hours_since_rain`, `wet_hours_72h`,
  `temp_mean_3d`, `temp_night_mean_3d`, `rh_mean_3d`, `rh_night_mean_3d`, `as_of`,
  `weather_data_coverage`, `weather_data_quality`).
- Spot-check: load the output in a Python shell, pick 2-3 `eraldis` rows, and sanity-check
  their `rain_*_mm`/`temp_mean_3d` values are physically plausible (rain in the 0-100mm
  range for a 14-day Estonian summer window, temperature in a plausible seasonal range —
  not wildly out of range, not all identical/zero, not all `None`).
- Note the real wall-clock time. If the first-ever run's radar backfill (potentially
  ~4000 files across the 14-day window) takes an impractically long time (say, well over
  30 minutes), note this honestly — do not silently claim success if the real timing is
  poor. If it is impractically slow, the fix (bbox-trimming the radar pixel grid before
  the per-file decode loop, flagged as a possible follow-up in Task 2) becomes a
  documented follow-up, not something to silently skip verifying.

- [ ] **Step 5: Update CLAUDE.md**

Add a new subsection after the existing "Running the full pipeline" section (before
"Planned architecture"), documenting this as a standalone, separately-run step:

```markdown
## Weather refresh (standalone, not part of the 9-step pipeline)

`uv run python scripts/refresh_weather.py` ingests KAIA radar precipitation composites
(5-minute HDF5/ODIM files, rolling 14-day cache in `data/radar_cache/`) and MET Norway's
MEPS/MET-Nordic hourly analysis grid (rolling 3-day window, no local cache — refetched
each run) to produce `data/weather_eraldis.geojson`: per-`eraldis` `rain_3d_mm`/
`rain_7d_mm`/`rain_14d_mm`, `hours_since_rain`, `wet_hours_72h`, `temp_mean_3d`/
`temp_night_mean_3d`, `rh_mean_3d`/`rh_night_mean_3d`, plus `as_of`/
`weather_data_coverage`/`weather_data_quality` columns. Unlike the rest of the pipeline
this is time-varying and meant to be re-run on demand (e.g. before a scouting trip), not
as part of `main.py`'s 9-step sequence. `FruitingScore` (combining these features into a
per-species/date score and wiring into `ScoutScore`) is not yet built — this step only
produces the raw weather features.

[Record real measured wall-clock time and any coverage/quality findings from the Task 5
live run here.]
```

- [ ] **Step 6: Commit**

```bash
git add scripts/refresh_weather.py .gitignore CLAUDE.md
git commit -m "feat: add refresh_weather.py orchestrator and document the weather-refresh step"
```
