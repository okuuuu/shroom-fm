# OPERA Radar Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace KAIA as this project's radar rainfall source with EUMETNET's OPERA
pan-European composite, fixing the unbounded-nearest-join bug and the `coverage > 1.0`
invariant violation at their root along the way.

**Architecture:** `radar.py`'s KAIA-specific catalog/download code is replaced with
OPERA's S3 (recent, confirmed working) + REST API (historical/backfill, contract
determined in Task 1) access; its generic ODIM-parsing code is reused almost unchanged
(confirmed against real downloaded OPERA files); `accumulate_rainfall` becomes
raster-native with genuine per-pixel, per-window coverage tracking; a new
coordinate-transform-based (never `sjoin_nearest`) assignment function replaces the
unbounded nearest-join onto eraldis stands.

**Tech Stack:** Python, existing project stack (geopandas/pandas/numpy/h5py/pyproj/
requests) — no new dependencies expected (S3 access confirmed working via plain HTTPS,
no `boto3` needed).

**Spec:** `docs/superpowers/specs/2026-08-21-opera-radar-migration-design.md`

## Global Constraints

- Radar composite source: EUMETNET OPERA via MeteoGate ORD API. Recent (≤24h): public
  anonymous S3, confirmed key format
  `s3://openradar-24h/YYYY/MM/DD/OPERA/COMP/OPERA@YYYYMMDDTHHMM@0@{RATE,ACRR,DBZH}.h5`,
  base URL `https://s3.waw3-1.cloudferro.com/`. Historical: ORD REST API,
  `https://api.meteogate.eu/eu-eumetnet-weather-radar`, `location_id=0-20010-0-OPERA`,
  anonymous rate limit confirmed **200 requests/hour** (real `X-RateLimit-Limit` header).
- Product: `RATE` (instantaneous rain rate), confirmed real cadence **15 minutes**
  (verified from two real consecutive files' own timestamps, not from catalog metadata
  — the catalog's `duration: PT1M` field is confirmed unreliable/generic, do not use it).
- Real confirmed grid (from two live-downloaded files, 2026-08-21): `xsize=1900`,
  `ysize=2200`, `xscale=yscale=2000.0` (2km exact), `projdef = "+proj=laea +lat_0=55.0
  +lon_0=10.0 +x_0=1950000.0 +y_0=-2100000.0 +units=m +ellps=WGS84"` (Lambert
  Azimuthal Equal-Area — existing code already reads `projdef` dynamically per file,
  never hardcodes a projection, so no code changes needed for this difference).
  `Conventions: ODIM_H5/V2_4` — same standard as KAIA.
- Real confirmed value-decode semantics: `dataset1/data1/what` has `gain=1.0`,
  `offset=0.0`, `nodata=-9999000.0`, `undetect=-8888000.0`, `quantity='RATE'` — same
  conceptual pattern as KAIA (different sentinel magnitudes, already read dynamically).
- Real confirmed quality layer: `dataset1/data1/quality1/data`, same shape as the rain
  array, decoded range exactly `[0.0, 1.0]`, identified by the ODIM `qualityN`-subgroup
  convention (no `quantity="QIND"` string — do not search for one).
- `how.nodes` lists the OPERA network's ~130-radar static roster (confirmed includes
  `eehar`/`eesur` for Estonia) — this is NOT a per-timestamp "which radars actually
  contributed" signal. Never build a diagnostic on it expecting per-file accuracy; use
  the `quality1` layer for that instead.
- Coverage must satisfy the hard invariant `0.0 <= coverage <= 1.0` — enforce with an
  assertion, never silently accept or document away a violation.
- Root cause of the currently-shipped `weather_data_coverage=1.0044642857142858` bug is
  now proven exactly: `cached_radar_files`'s window filter is inclusive on both ends
  (`window_start <= ts <= window_end`), and the real cache had exactly 4050 files
  against an `expected_slots_14d` of exactly 4032 (`4050/4032 =
  1.0044642857142858`, bit-for-bit match to the observed value) — fix the boundary to
  genuine `[window_start, now)` half-open semantics.
- No `sjoin_nearest`-based join for radar data anywhere, ever, at any distance. A stand
  with zero valid pixels intersecting it gets `None`, never a fabricated or
  extrapolated value.
- `_nearest_join` in `weather.py` stays completely unmodified — it remains MEPS-only.
- No MQTT, no hybrid KAIA+OPERA fallback, no `rasterio`/`rasterstats`, no
  `FruitingScore` constant recalibration — all explicitly out of scope per the spec.
- Baseline before this plan: 253 tests passing (`uv run pytest tests/ -q`).

---

### Task 1: Determine the ORD REST API's historical-query contract (research)

**Files:** none modified — this task produces a findings document that Task 3 depends on.

**Interfaces:**
- Produces: a written findings report (exact file path specified in Step 5) documenting
  the real, tested request URL format, required headers, response shape, response
  cardinality (how many file links one request returns for a given datetime range), and
  pagination mechanism (if any) for fetching historical (>24h old) OPERA RATE composite
  data via `https://api.meteogate.eu/eu-eumetnet-weather-radar`. Task 3 reads this
  report and cannot be written with exact code until this task completes.

This is a live-investigation task, not a code-writing task. All testing happens against
the real, live MeteoGate API — there is no way to fake/mock this contract discovery,
and no local test suite change results from this task.

- [ ] **Step 1: Confirm current API state**

Run:
```bash
curl -sS -i "https://api.meteogate.eu/eu-eumetnet-weather-radar/collections" | head -5
```
Expected: `HTTP/1.1 200 OK`. If this fails or times out, the whole API may be down —
wait and retry before proceeding; do not guess at a contract you can't observe.

- [ ] **Step 2: Determine the correct response format / `Accept` header**

The `/collections` endpoint's metadata (confirmed 2026-08-21) states
`"output_formats":["CoverageJSON"]`. A prior attempt against `/area` with a `coords`
param returned `204 No Content` with no `Accept` header set — this is the leading
hypothesis for why data queries return empty/error responses. Try:
```bash
curl -sS -i -H "Accept: application/prs.coverage+json" \
  "https://api.meteogate.eu/eu-eumetnet-weather-radar/collections/observations/area?coords=POLYGON((20%2058,26%2058,26%2060,20%2060,20%2058))&datetime=<a real recent ISO8601 range within the last few hours>&standard_name=RATE&format=ODIM&method=comp"
```
Try both `format=ODIM` and `format=GeoTIFF` if the first doesn't return real content.
If this still returns `204`/`503`/empty, try the `/locations/0-20010-0-OPERA` endpoint
with the same `Accept` header. Document the exact working combination once found. If
NEITHER endpoint produces real data content after trying reasonable header/param
combinations (a handful of attempts, not dozens — don't burn the whole 200/hour
anonymous budget on blind guessing), that itself is a valid, reportable finding —
document it as "REST data-fetch not resolved live; recommend registering a MeteoGate
API key and consulting `support.opera@eumetnet.eu` or the Swagger UI at
`/eu-eumetnet-weather-radar/docs` interactively before Task 3."

- [ ] **Step 3: If a working query is found, determine response cardinality**

Query a **known-good real time range** — one that's still within the last 24h so the
answer can be cross-checked against the S3 listing (which is confirmed working) — e.g.
a 1-hour range. Compare the number of file links/objects the API response contains
against how many 15-minute RATE slots exist in that range via S3
(`https://s3.waw3-1.cloudferro.com/openradar-24h/?list-type=2&prefix=YYYY/MM/DD/OPERA/COMP/`,
filtering to `RATE.h5` keys in that hour). Document: does one API request return one
file link, or a list covering the whole requested range? This directly determines
backfill cost (1,344 requests for 14 days at 15-min cadence if one-request-per-file, vs.
far fewer if a single request can cover hours/days at once).

- [ ] **Step 4: Determine whether pagination exists**

If a single request's response is capped at some number of results even for a longer
datetime range, look for a `next`/`cursor`/similar link in the response body or headers.
Document whatever is found, including "no pagination observed within tested range."

- [ ] **Step 5: Write the findings report**

Create `docs/superpowers/plans/2026-08-21-opera-rest-backfill-findings.md` with these
exact sections (fill in the real findings, do not leave placeholders — if a question
couldn't be resolved, say so explicitly and say what was tried):

```markdown
# ORD REST API Historical-Query Findings (2026-08-21)

## Working request

[Exact curl command / URL that returned real data, or "not resolved" with what was tried]

## Response format

[Content-Type, body shape — JSON keys, CoverageJSON structure, or file-link list shape]

## Response cardinality

[How many files/timestamps one request returns, tested against a known real range]

## Pagination

[Mechanism found, or "none observed"]

## Recommendation for Task 3

[Given the above: does Task 3's historical-fetch function issue one request per
timestamp, one per day, one per the whole 14-day window? What's the realistic backfill
time at 200 req/hour anonymous, or note that an API key is required and why]
```

- [ ] **Step 6: Commit**

```bash
git add docs/superpowers/plans/2026-08-21-opera-rest-backfill-findings.md
git commit -m "docs: document ORD REST API historical-query contract findings"
```

---

### Task 2: `radar.py` — OPERA S3 catalog/download for recent data (confirmed working)

**Files:**
- Modify: `src/shroom_fm/radar.py:1-118` (imports through `download_radar_composite`)
- Test: `tests/test_radar.py` (relevant sections)

**Interfaces:**
- Produces: `OPERA_S3_BASE_URL = "https://s3.waw3-1.cloudferro.com/"`,
  `OPERA_S3_BUCKET = "openradar-24h"` (module constants);
  `list_recent_radar_objects(prefix_date: "date") -> list[dict]` — lists real S3 objects
  for a given date under `<date:%Y/%m/%d>/OPERA/COMP/`, filtered to `RATE.h5` keys,
  returning `[{"key": str, "timestamp": datetime}, ...]`, parsed from the real
  `OPERA@YYYYMMDDTHHMM@0@RATE.h5` filename convention. Returns `[]` (not an error) for
  a date whose data has rolled off the 24h cache — this is confirmed real, valid S3
  behavior (empty `KeyCount`), not a failure.
  `download_opera_object(key: str, cache_dir: Path) -> Path` — downloads one object via
  plain HTTPS GET to `cache_dir`, using a cache filename derived from the object's own
  timestamp+`RATE` marker (replaces `_cache_filename`).
  `cached_radar_timestamp(path: Path) -> datetime` — same name/signature as today, but
  parses the new OPERA-derived cache filename convention.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_radar.py` (near the top, after the existing imports):

```python
from datetime import date as _date

from shroom_fm.radar import (
    OPERA_S3_BASE_URL,
    OPERA_S3_BUCKET,
    download_opera_object,
    list_recent_radar_objects,
)


def test_opera_s3_constants_match_confirmed_real_endpoint():
    assert OPERA_S3_BASE_URL == "https://s3.waw3-1.cloudferro.com/"
    assert OPERA_S3_BUCKET == "openradar-24h"


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
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_radar.py -v -k "opera_s3 or list_recent or download_opera"`
Expected: FAIL — `ImportError` on the new names (they don't exist yet).

- [ ] **Step 3: Implement**

Replace lines 1-118 of `src/shroom_fm/radar.py` (from the top of the file through the
end of `download_radar_composite`) with:

```python
import os
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

import geopandas as gpd
import h5py
import numpy as np
import pyproj
import requests

OPERA_S3_BASE_URL = "https://s3.waw3-1.cloudferro.com/"
OPERA_S3_BUCKET = "openradar-24h"
_S3_NAMESPACE = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
_HDF5_SIGNATURE = b"\x89HDF\r\n\x1a\n"
_KEY_TIMESTAMP_RE = re.compile(r"@(\d{8}T\d{4})@0@RATE\.h5$")


def list_recent_radar_objects(prefix_date: date) -> list[dict]:
    """Lists real RATE.h5 objects for prefix_date from the confirmed-working public
    anonymous S3 endpoint (no signing, no boto3). Returns [] — not an error — for a
    date that has legitimately rolled off the 24h rolling cache (confirmed real S3
    behavior: a valid, empty KeyCount=0 response, not an HTTP error)."""
    prefix = f"{prefix_date:%Y/%m/%d}/OPERA/COMP/"
    url = (
        f"{OPERA_S3_BASE_URL}{OPERA_S3_BUCKET}/"
        f"?list-type=2&prefix={prefix}&max-keys=1000"
    )
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


def _opera_cache_filename(timestamp: datetime) -> str:
    return f"{timestamp:%Y%m%dT%H%M%S}Z_RATE.h5"


def cached_radar_timestamp(path: Path) -> datetime:
    stem = path.name.split("_", 1)[0]
    return datetime.strptime(stem, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def download_opera_object(key: str, cache_dir: Path) -> Path:
    match = _KEY_TIMESTAMP_RE.search(key)
    timestamp = datetime.strptime(match.group(1), "%Y%m%dT%H%M").replace(
        tzinfo=timezone.utc
    )
    path = cache_dir / _opera_cache_filename(timestamp)
    if path.exists():
        return path

    url = f"{OPERA_S3_BASE_URL}{OPERA_S3_BUCKET}/{key}"
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
```

Note: this deletes `KAIA_QUERY_URL`, `KAIA_DOWNLOAD_URL_TEMPLATE`, `RADAR_CONTENT_TYPE`,
`RADAR_PHENOMENON`, `MAX_WORKERS`, `_PAGE_SIZE`, `_DOWNLOAD_429_MAX_ATTEMPTS`,
`_DOWNLOAD_429_INITIAL_BACKOFF`, `query_radar_documents`, `_cache_filename`,
`download_radar_composite`, and the `from shroom_fm.retry import get_with_retry,
post_with_retry` import, and the `concurrent.futures` import (concurrency for the
recent-data path is handled in Task 3's `fetch_new_radar_composites`, not here) — all
KAIA-specific and no longer used. `cached_radar_timestamp`'s signature is unchanged
(same function name/shape, new filename convention it parses).

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_radar.py -v -k "opera_s3 or list_recent or download_opera or cached_radar_timestamp"`
Expected: all pass. (Many other tests in this file will now fail — that's expected,
later tasks fix them. Do not try to fix unrelated failures in this task.)

- [ ] **Step 5: Commit**

```bash
git add src/shroom_fm/radar.py tests/test_radar.py
git commit -m "feat: add OPERA S3 catalog/download for recent radar data"
```

---

### Task 3: `radar.py` — OPERA REST catalog/download for historical/backfill

**Files:**
- Modify: `src/shroom_fm/radar.py` (append/replace `fetch_new_radar_composites`)
- Test: `tests/test_radar.py`

**Interfaces:**
- Consumes: Task 1's findings report
  (`docs/superpowers/plans/2026-08-21-opera-rest-backfill-findings.md`) — READ THIS
  FIRST, it determines the exact request shape this task must implement.
  `list_recent_radar_objects`/`download_opera_object` from Task 2.
- Produces: `fetch_new_radar_composites(cache_dir, since, *, now=None) -> list[Path]` —
  same name as before, same overall purpose (get everything new since `since` into the
  cache), but internally routes: for the portion of `[since, now]` that's within the
  last ~24h, use `list_recent_radar_objects`/`download_opera_object` (Task 2, confirmed
  working) per day; for the older portion, use whatever Task 1 determined works for the
  REST API.

**This task cannot be written with full exact code before Task 1 completes.** The
implementer must:

1. Read Task 1's findings report in full first.
2. If Task 1 found a working REST request contract: implement historical fetching using
   that exact confirmed request shape, following the same defensive patterns already
   established in this codebase (a real HTTP timeout, checking the HDF5 signature
   before trusting downloaded content, atomic write via a `.part` temp file + `os.replace`
   — mirror `download_opera_object`'s shape from Task 2). Add a
   `MeteoGateAPIKeyRequired` custom exception (or similar) if Task 1's findings say an
   API key is required for meaningful backfill volume, and have
   `fetch_new_radar_composites` accept an optional `api_key: str | None = None`
   parameter, read from an environment variable (`OPERA_API_KEY`) with a clear error
   message if backfill is attempted without one and Task 1 confirmed one is required.
2. If Task 1 did NOT find a working contract (the report says "not resolved"):
   implement `fetch_new_radar_composites` to serve ONLY the confirmed-working S3 recent
   window (loop `list_recent_radar_objects` over each date from `max(since, now - 24h)`
   through `now`), and have it raise a clear, actionable error (not silently return
   partial results) if `since` requests data older than what S3 can serve — e.g.:
   ```python
   raise NotImplementedError(
       "Historical OPERA backfill (>24h old) is not yet implemented — see "
       "docs/superpowers/plans/2026-08-21-opera-rest-backfill-findings.md for what "
       "was tried against the ORD REST API. Only the last ~24h (S3) is currently "
       "fetchable."
   )
   ```
   This is an honest, correct fallback given genuinely unresolved upstream API access —
   never silently ship a broken/partial backfill.

Either way, write real tests exercising the actual behavior implemented (mocking
`requests.get`/`requests.post` the same way Task 2's tests do, using the real request
shape Task 1 confirmed, or testing the `NotImplementedError` path if that's what was
implemented). Update `expire_old_radar_composites`/`cached_radar_files`/
`newest_cached_radar_timestamp` only if Task 1/2's real filename convention requires it
— these three functions currently only depend on `cached_radar_timestamp`'s return
value and `*.h5` globbing, which Task 2 already made compatible; leave them unmodified
unless a real, discovered reason requires a change (don't preemptively touch working
code).

- [ ] **Step 1: Read Task 1's findings report and decide which of the two paths above applies**

- [ ] **Step 2: Write the failing tests** (exact tests depend on Step 1's outcome — write
  real tests mirroring Task 2's mocking style, covering whichever real behavior was
  implemented)

- [ ] **Step 3: Run to verify they fail**

Run: `uv run pytest tests/test_radar.py -v -k "fetch_new_radar"`

- [ ] **Step 4: Implement**

- [ ] **Step 5: Run to verify they pass**

Run: `uv run pytest tests/test_radar.py -v -k "fetch_new_radar"`

- [ ] **Step 6: Commit**

```bash
git add src/shroom_fm/radar.py tests/test_radar.py
git commit -m "feat: add OPERA historical/backfill radar fetching"
```

---

### Task 4: `radar.py` — re-verify generic ODIM parsing against real OPERA values, add quality reading

**Files:**
- Modify: `tests/test_radar.py` (update `_write_fake_composite` and its dependent tests,
  add quality-layer tests)
- Modify: `src/shroom_fm/radar.py` (add `parse_radar_quality`)

**Interfaces:**
- Consumes: `read_radar_full_georef`, `_radar_origin`, `radar_bbox_slice`,
  `parse_radar_composite` (unchanged from before this plan — confirmed correct against
  real OPERA files, no code changes needed in this task).
- Produces: `parse_radar_quality(path: Path, *, row_slice: slice = slice(None),
  col_slice: slice = slice(None)) -> np.ndarray | None` — Task 6's `accumulate_rainfall`
  consumes this to build the `quality_mean` column (spec Component 3).

This task is mostly test-fixture-only (prove the EXISTING, unmodified parsing code
produces correct results against realistic OPERA-shaped values, using the real
confirmed constants from this plan's Global Constraints: grid `1900×2200`,
`xscale=yscale=2000.0`, the real LAEA `projdef`, `nodata=-9999000.0`,
`undetect=-8888000.0`), plus one small new function: `parse_radar_quality`, reading the
real confirmed optional `quality1` subgroup (spec Component 3 — this was missing from
an earlier draft of this plan and is added here since it's naturally paired with
`parse_radar_composite`).

- [ ] **Step 1: Update `_write_fake_composite` in `tests/test_radar.py`**

Replace the existing `_write_fake_composite` function (currently ~line 414-436) with:

```python
def _write_fake_composite(
    path,
    *,
    rate_grid,
    gain=1.0,
    offset=0.0,
    nodata=-9999000.0,
    undetect=-8888000.0,
    quality_grid=None,
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
        what = f.create_group("dataset1/what")
        what.attrs["gain"] = gain
        what.attrs["offset"] = offset
        what.attrs["nodata"] = nodata
        what.attrs["undetect"] = undetect
        what.attrs["quantity"] = b"RATE"
        where = f.create_group("where")
        # Real confirmed OPERA projdef/grid (2026-08-21) — Lambert Azimuthal Equal-Area,
        # not KAIA's Mercator; the parsing code reads projdef dynamically so this swap
        # requires no production code changes, only realistic test fixtures.
        where.attrs["projdef"] = (
            b"+proj=laea +lat_0=55.0 +lon_0=10.0 +x_0=1950000.0 "
            b"+y_0=-2100000.0 +units=m +ellps=WGS84"
        )
        where.attrs["xsize"] = raw.shape[1]
        where.attrs["ysize"] = raw.shape[0]
        where.attrs["xscale"] = 2000.0
        where.attrs["yscale"] = 2000.0
        where.attrs["UL_lon"] = -39.5357864125034
        where.attrs["UL_lat"] = 67.0228327624372
```

- [ ] **Step 2: Update the two tests that hardcode the old projdef/scale values as
  literal assertions**

In `test_parse_radar_composite_decodes_valid_pixels_and_masks_sentinels` (currently
~line 439-460): change the `rate_grid`/nodata-overwrite values to use realistic OPERA
magnitudes and update the final assertion:
```python
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
```

- [ ] **Step 3: Write failing tests for the optional quality layer (spec Component 3)**

Add to `tests/test_radar.py`:

```python
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
```

Add `parse_radar_quality` to the `from shroom_fm.radar import` line at the top of the
test file.

- [ ] **Step 4: Run to verify Step 3 fails**

Run: `uv run pytest tests/test_radar.py -v -k "parse_radar_quality"`
Expected: FAIL — `ImportError`, `parse_radar_quality` doesn't exist yet.

- [ ] **Step 5: Implement `parse_radar_quality`**

Append to `src/shroom_fm/radar.py`, near `parse_radar_composite`:

```python
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
```

- [ ] **Step 6: Run to verify Step 3 now passes**

Run: `uv run pytest tests/test_radar.py -v -k "parse_radar_quality"`
Expected: 3 passed.

Wait — this test's `rate_grid` originally used `65535.0` as one of the grid VALUES
themselves (not the nodata sentinel — that was a KAIA quirk where the test picked a
"large" number as a stand-in before manually overwriting it). Since OPERA's real nodata
sentinel (`-9999000.0`) is a huge negative number, not a huge positive one, just use
any placeholder value for that grid cell since it gets overwritten immediately after
anyway — the exact value in `rate_grid=[[0.0, 2.0], [-9999000.0, 0.5]]` above is fine
as shown (the `[1,0]` cell is written as `-9999000.0` by `_write_fake_composite` itself
already via that literal, making the subsequent manual overwrite in the test body
redundant-but-harmless — leave both for clarity, matching the original test's
structure).

In `test_radar_bbox_slice_covers_a_small_eraldis_bbox_within_the_full_grid` (currently
~line 524-546): update the georef dict's `xsize`/`ysize`/`xscale`/`yscale`/`ul_lon`/
`ul_lat`/`projdef` to the real confirmed OPERA values, and update the grid-size
assertions since a 2km-pixel grid is coarser (so a comparably-sized real-world bbox
covers *fewer* pixels than KAIA's 359m grid did — recompute what's realistic rather
than reusing the old thresholds unchanged):

```python
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
```

- [ ] **Step 7: Run to verify all Task-4-relevant tests pass**

Run: `uv run pytest tests/test_radar.py -v -k "parse_radar_composite or radar_bbox_slice or radar_pixel_centers or parse_radar_quality"`
Expected: all pass, using the real confirmed OPERA constants throughout.

- [ ] **Step 8: Commit**

```bash
git add src/shroom_fm/radar.py tests/test_radar.py
git commit -m "test: re-verify generic ODIM parsing against real confirmed OPERA constants; add parse_radar_quality"
```

---

### Task 5: `radar.py` — fix the `[start, end)` boundary bug and add the coverage invariant

**Files:**
- Modify: `src/shroom_fm/radar.py` (`cached_radar_files`, new `_validate_coverage` helper)
- Test: `tests/test_radar.py`

**Interfaces:**
- Modifies: `cached_radar_files(cache_dir, window_start, window_end) -> list[Path]` —
  same signature, fixes the boundary semantics to genuine half-open `[window_start,
  window_end)`.
- Produces: `_validate_coverage(value: float, *, label: str) -> float` — asserts
  `0.0 <= value <= 1.0`, raising `AssertionError` with a clear message naming the
  violating value and label if not, otherwise returns `value` unchanged (a pass-through
  validator, called at every point `accumulate_rainfall`/`weather.py` compute a coverage
  ratio).

**Root cause reminder (from Global Constraints, now proven exact):** the currently
shipped `weather_data_coverage=1.0044642857142858` came from
`cached_radar_files`'s inclusive-inclusive filter (`window_start <= ts <= window_end`)
combined with the real cache holding exactly 4050 files against an
`expected_slots_14d` of 4032 — `4050/4032` is bit-for-bit that exact observed value.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_radar.py`:

```python
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
```

Add `_validate_coverage` to the imports at the top of the file:
```python
from shroom_fm.radar import (
    ...,
    _validate_coverage,
)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_radar.py -v -k "cached_radar_files_ex or cached_radar_files_in or validate_coverage"`
Expected: FAIL — `cached_radar_files_includes_the_window_start_boundary` may currently
pass by accident (since the old code is inclusive on both ends), but
`_excludes_the_window_end_boundary` should FAIL, and the `_validate_coverage` tests
FAIL with `ImportError`.

- [ ] **Step 3: Implement**

Replace `cached_radar_files` (currently lines 155-167) with:
```python
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
```

Add near the top of the file, after the module constants:
```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_radar.py -v -k "cached_radar_files or validate_coverage"`
Expected: all pass.

- [ ] **Step 5: Run the full test file to check for fallout**

Run: `uv run pytest tests/test_radar.py -v`
Expected: some pre-existing tests that relied on the old inclusive-`window_end`
behavior (e.g. any test using `now` as also the timestamp of a real cached file) may
now fail — if so, this is expected: fix each by adjusting the test's `now`/file
timestamps to be genuinely consistent with `[start, end)` semantics (e.g. a test file
timestamped exactly at `now` should no longer be counted as "within the window ending
at now" — move its timestamp 1 slot earlier, or move `now` 1 slot later, whichever
preserves the test's original intent). Do not weaken `_validate_coverage` or revert the
boundary fix to make old tests pass — fix the tests' fixture timestamps instead.

- [ ] **Step 6: Commit**

```bash
git add src/shroom_fm/radar.py tests/test_radar.py
git commit -m "fix: half-open [start,end) window boundary and hard 0-1 coverage invariant"
```

---

### Task 6: `radar.py` — raster-native `accumulate_rainfall` rewrite

**Files:**
- Modify: `src/shroom_fm/radar.py` (`accumulate_rainfall` and its constants)
- Test: `tests/test_radar.py`

**Interfaces:**
- Consumes: `cached_radar_files` (Task 5, half-open), `_validate_coverage` (Task 5),
  `parse_radar_composite`/`radar_bbox_slice`/`read_radar_full_georef` (unchanged),
  `parse_radar_quality` (Task 4).
- Modifies: `_RADAR_SLOT_MINUTES = 15` (was 5). `accumulate_rainfall`'s signature is
  UNCHANGED (`cache_dir, now, eraldis_bounds_wgs84 -> tuple[gpd.GeoDataFrame,
  dict[str, float]]`) — this task changes its INTERNALS (per-pixel coverage tracking
  via the 3-way nodata/undetect/real-value distinction) and the returned `coverage`
  dict's values now flow through `_validate_coverage`, but keeps returning the same
  flattened point-per-pixel GeoDataFrame shape Task 8 (weather.py integration) and the
  new Task 7 (raster-native assignment) both need — this plan does NOT change
  `accumulate_rainfall`'s return type to raw 2D arrays; per-pixel coverage becomes a
  NEW column (`coverage_3d`/`coverage_7d`/`coverage_14d`) on the same returned
  GeoDataFrame, keeping one clean interface rather than introducing a second raw-array
  return path. Also adds a `quality_mean` column (spec Component 3's optional
  enrichment) via the new `parse_radar_quality` function from Task 4 — averaged only
  over cached files that actually had a `quality1` subgroup, `NaN` for a pixel with no
  quality data from any cached file. This column is not consumed anywhere downstream
  in this plan (Tasks 7/8 don't read it) — it exists so real, confirmed-available
  quality data is not silently discarded, ready for a future task to use once there's a
  concrete need (e.g. weighting `assign_radar_to_eraldis`'s mean by quality) — matching
  the spec's explicit framing of it as "optional enrichment," not a hard requirement.

**Correction to the spec's Architecture diagram:** the spec described this as
"maintaining 2D rainfall-accumulation AND coverage-count rasters," which could be read
as requiring a wholesale return-type change. On implementation, the simpler and
equally-correct approach is to keep `radar_pixel_centers`' existing flattened
point-GeoDataFrame return shape and add per-pixel coverage as new columns on it — this
preserves every existing consumer's interface (Task 7's assignment function, Task 8's
`weather.py` integration) while still deliverying genuine per-pixel (not just
national-aggregate) coverage. Follow this task's code exactly; it supersedes the
spec's diagram on this specific point.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_radar.py`:

```python
def test_accumulate_rainfall_tracks_per_pixel_coverage_not_just_national(tmp_path):
    cache_dir = tmp_path / "radar_cache"
    cache_dir.mkdir()

    # Pixel [0,0]: real value both slots (covered). Pixel [0,1]: undetect both slots
    # (covered, confirmed-dry). Pixel [1,0]: nodata both slots (NOT covered).
    _write_fake_composite(
        cache_dir / "20260815T000000Z_RATE.h5",
        rate_grid=[[1.0, -8888000.0], [-9999000.0, 0.0]],
    )
    with h5py.File(cache_dir / "20260815T000000Z_RATE.h5", "r+") as f:
        raw = f["dataset1/data1/data"][:]
        raw[0, 1] = -8888000.0  # undetect
        raw[1, 0] = -9999000.0  # nodata
        f["dataset1/data1/data"][:] = raw
    _write_fake_composite(
        cache_dir / "20260815T001500Z_RATE.h5",
        rate_grid=[[1.0, 0.0], [0.0, 0.0]],
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

    # 2 real files, both slots valid at [0,0] and [0,1] -> pixel coverage 1.0 there
    assert row0_col0["coverage_3d"] == pytest.approx(1.0)
    assert row0_col1["coverage_3d"] == pytest.approx(1.0)
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
    _write_fake_composite(
        cache_dir / "20260815T000000Z_RATE.h5",
        rate_grid=[[0.0, 0.0]],
        quality_grid=[[1.0, 0.6]],
    )
    _write_fake_composite(
        cache_dir / "20260815T001500Z_RATE.h5",
        rate_grid=[[0.0, 0.0]],
        quality_grid=[[0.8, 0.4]],
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
    _write_fake_composite(
        cache_dir / "20260815T000000Z_RATE.h5", rate_grid=[[0.0, 0.0]]
    )

    now = _utc(2026, 8, 15, 0, 15)
    points, _ = accumulate_rainfall(cache_dir, now, (20.0, 56.0, 30.0, 62.0))

    assert points["quality_mean"].isna().all()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_radar.py -v -k "per_pixel_coverage or slot_minutes_is_15 or never_exceeds_one or quality_mean or carries_through_quality"`
Expected: FAIL — `coverage_3d`/`coverage_7d`/`coverage_14d`/`quality_mean` columns don't
exist yet, `_RADAR_SLOT_MINUTES` is still 5.

- [ ] **Step 3: Implement**

In `src/shroom_fm/radar.py`, change `_RADAR_SLOT_MINUTES = 5` to `_RADAR_SLOT_MINUTES = 15`
(near the existing `_RADAR_WINDOW_DAYS = 14` constant).

Replace `accumulate_rainfall`'s body (the whole function, currently starting at the
`_RADAR_WINDOW_DAYS`/`_RADAR_SLOT_MINUTES` constants through the final `return points,
coverage`) — keep everything about event-detection (`RAIN_EVENT_DRY_GAP_H`,
`SIGNIFICANT_EVENT_MM`, `STRONG_EVENT_MM`, the `event_mm`/`last_significant_epoch`/etc.
tracking) EXACTLY as it is today, unchanged — only add the per-pixel coverage tracking
alongside it:

```python
def accumulate_rainfall(
    cache_dir: Path,
    now: datetime,
    eraldis_bounds_wgs84: tuple[float, float, float, float],
) -> tuple[gpd.GeoDataFrame, dict[str, float]]:
    from datetime import timedelta

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
    quality_mean = np.where(quality_count > 0, quality_sum / np.maximum(quality_count, 1), np.nan)
    points["quality_mean"] = quality_mean.ravel()
    points = points.to_crs("EPSG:3301")
    return points, coverage
```

Note: per-pixel coverage (`coverage_3d_px` etc.) is `np.clip`ped to `[0,1]` defensively
(a per-PIXEL count can't mathematically exceed its own expected-slot denominator the
way the old NATIONAL bug did, since `valid_slots_Nd` only increments once per real file
processed — this clip is a cheap safety net, not expected to ever actually fire) — the
NATIONAL `coverage` dict values go through the harder `_validate_coverage` ASSERT
instead, since that's the one with a confirmed real historical violation.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_radar.py -v`
Expected: all pass except tests explicitly deferred to a later task (none currently
known to be — if any pre-existing test fails here unexpectedly, investigate before
moving on, don't defer silently).

- [ ] **Step 5: Commit**

```bash
git add src/shroom_fm/radar.py tests/test_radar.py
git commit -m "feat: raster-native per-pixel coverage + optional quality enrichment in accumulate_rainfall, 15-min cadence"
```

---

### Task 7: `radar.py` — raster-native eraldis assignment (no `sjoin_nearest`)

**Files:**
- Modify: `src/shroom_fm/radar.py` (new function)
- Test: `tests/test_radar.py`

**Interfaces:**
- Consumes: `accumulate_rainfall`'s output (Task 6) — a point-per-pixel GeoDataFrame
  with `row`/`col`/`rain_*_mm`/`coverage_*` columns, EPSG:3301 CRS.
- Produces: `assign_radar_to_eraldis(eraldis_gdf: gpd.GeoDataFrame, radar_points:
  gpd.GeoDataFrame, columns: tuple[str, ...]) -> pd.DataFrame` — for each eraldis
  stand, finds every radar pixel whose cell the stand's geometry intersects (via a
  direct coordinate-transform-derived row/col range from the stand's own bounding box,
  NOT `gpd.sjoin`/`sjoin_nearest`), and returns the mean of `columns` over pixels with
  a non-null `rain_3d_mm` (i.e. actually valid — a `NaN` rain value means that pixel's
  window had zero real observations, and must not be averaged in as if it were a real
  0). A stand with zero valid pixels intersecting it gets `None` for every column in
  `columns` — never a fabricated value, never a value from a pixel outside the stand's
  own footprint. Replaces `weather.py`'s `_nearest_join` call specifically for radar
  data (MEPS keeps using `_nearest_join` unmodified — see Task 8).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_radar.py`:

```python
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
    # A large stand spanning pixels (0,0) [rain=1.0] and (0,1) [rain=2.0]
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1]},
        geometry=[_box(499000, 6499000, 503000, 6501000)],
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_radar.py -v -k "assign_radar_to_eraldis"`
Expected: FAIL — `ImportError`, `assign_radar_to_eraldis` doesn't exist yet.

- [ ] **Step 3: Implement**

Append to `src/shroom_fm/radar.py`:

```python
def assign_radar_to_eraldis(
    eraldis_gdf: "gpd.GeoDataFrame",
    radar_points: "gpd.GeoDataFrame",
    columns: tuple[str, ...],
) -> "pd.DataFrame":
    """For each eraldis stand, averages `columns` over every radar pixel whose point
    the stand's bounding box actually contains — a direct coordinate-range lookup, NOT
    gpd.sjoin/sjoin_nearest, so an unbounded-distance match (the original bug this
    project migrated off KAIA to fix) is structurally impossible here. A pixel with a
    NaN value for a column (zero valid observations in that pixel's window) is excluded
    from the mean, never averaged in as if it were a real value. A stand with zero
    valid pixels intersecting its own bounding box gets None for every column — never a
    value borrowed from outside the stand's own footprint, at any distance."""
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
            # Bounding-box query found nothing at all — try the pixel the stand's
            # own centroid falls in as a last direct lookup, in case the stand's
            # bounds are smaller than one pixel and cx's slice missed it.
            candidates = radar_points.cx[
                geom.centroid.x : geom.centroid.x, geom.centroid.y : geom.centroid.y
            ]
        record = {}
        for col in columns:
            valid_values = candidates[col].dropna()
            record[col] = float(valid_values.mean()) if len(valid_values) else None
        records.append(record)

    return pd.DataFrame(records, index=eraldis_gdf.index)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_radar.py -v -k "assign_radar_to_eraldis"`
Expected: 6 passed.

- [ ] **Step 5: Run the full radar test file**

Run: `uv run pytest tests/test_radar.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/shroom_fm/radar.py tests/test_radar.py
git commit -m "feat: add raster-native eraldis assignment, no sjoin_nearest for radar data"
```

---

### Task 8: `weather.py` — wire in raster-native assignment and per-stand coverage

**Files:**
- Modify: `src/shroom_fm/weather.py`
- Test: `tests/test_weather.py`

**Interfaces:**
- Consumes: `assign_radar_to_eraldis` (Task 7), `accumulate_rainfall`'s new
  `coverage_3d`/`coverage_7d`/`coverage_14d` per-pixel columns (Task 6).
- Modifies: `refresh_weather`'s internals — radar-specific joining now uses
  `assign_radar_to_eraldis` instead of `_nearest_join`; `radar_degraded_3d`/`_7d`/`_14d`
  become per-stand (derived from the newly-joined `coverage_3d`/`_7d`/`_14d` columns,
  each run through `_validate_coverage`), not single dataset-wide booleans;
  `result["weather_data_coverage"]` becomes per-stand (the joined `coverage_14d`
  value) instead of one repeated national `radar_coverage["14d"]` scalar.
  `_nearest_join` itself is UNCHANGED, still used only for `meps_joined`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_weather.py`:

```python
def test_refresh_weather_uses_per_stand_coverage_not_one_national_value(
    monkeypatch, tmp_path
):
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1, 2]},
        geometry=[Point(24.0, 59.0), Point(25.0, 59.5)],
        crs="EPSG:4326",
    )
    now = _utc(2026, 8, 18, 12)

    # Two real radar points: one with full coverage, one with degraded coverage —
    # this is the whole point of per-stand coverage vs the old national-only version.
    radar_points = gpd.GeoDataFrame(
        {
            "rain_3d_mm": [5.0, 5.0],
            "rain_7d_mm": [10.0, 10.0],
            "rain_14d_mm": [20.0, 20.0],
            "hours_since_any_rain": [3.0, 3.0],
            "wet_hours_72h": [1.0, 1.0],
            "hours_since_significant_rain": [10.0, 10.0],
            "hours_since_strong_rain": [20.0, 20.0],
            "last_significant_event_mm": [6.0, 6.0],
            "last_strong_event_mm": [12.0, 12.0],
            "max_24h_rain_14d": [8.0, 8.0],
            "coverage_3d": [1.0, 0.2],
            "coverage_7d": [1.0, 0.2],
            "coverage_14d": [1.0, 0.2],
        },
        geometry=[Point(500000, 6500000), Point(560000, 6560000)],
        crs="EPSG:3301",
    )
    meps_points = gpd.GeoDataFrame(
        {
            "temp_mean_3d": [15.0, 15.0],
            "temp_night_mean_3d": [10.0, 10.0],
            "rh_mean_3d": [70.0, 70.0],
            "rh_night_mean_3d": [85.0, 85.0],
        },
        geometry=[Point(500000, 6500000), Point(560000, 6560000)],
        crs="EPSG:3301",
    )

    monkeypatch.setattr(
        "shroom_fm.weather.accumulate_rainfall",
        lambda cache_dir, now_, bounds: (radar_points, _healthy_radar_coverage()),
    )
    monkeypatch.setattr(
        "shroom_fm.weather.accumulate_meps_features",
        lambda now_, bounds: (meps_points, 0.9, now_ - timedelta(hours=1)),
    )

    result = refresh_weather(eraldis_gdf, tmp_path / "radar_cache", now)

    # Stand 0: fully covered -> real rain values
    assert result.loc[0, "rain_3d_mm"] == pytest.approx(5.0)
    assert result.loc[0, "weather_data_coverage"] == pytest.approx(1.0)
    # Stand 1: degraded coverage (0.2 < MIN_RADAR_COVERAGE=0.7) -> nulled, INDEPENDENTLY
    # of stand 0 being fine — this is the per-stand behavior the old single national
    # coverage dict could never express.
    assert result.loc[1, "rain_3d_mm"] is None
    assert result.loc[1, "weather_data_coverage"] == pytest.approx(0.2)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_weather.py -v -k "per_stand_coverage"`
Expected: FAIL — `refresh_weather` still uses one national coverage value.

- [ ] **Step 3: Implement**

In `src/shroom_fm/weather.py`, add `assign_radar_to_eraldis` to the import from
`shroom_fm.radar` (alongside the existing `accumulate_rainfall` import), and add
`"coverage_3d"`, `"coverage_7d"`, `"coverage_14d"` to `_RADAR_COLUMNS`:

```python
from shroom_fm.radar import accumulate_rainfall, assign_radar_to_eraldis

_RADAR_COLUMNS = (
    "rain_3d_mm",
    "rain_7d_mm",
    "rain_14d_mm",
    "hours_since_any_rain",
    "wet_hours_72h",
    "hours_since_significant_rain",
    "hours_since_strong_rain",
    "last_significant_event_mm",
    "last_strong_event_mm",
    "max_24h_rain_14d",
    "coverage_3d",
    "coverage_7d",
    "coverage_14d",
)
```

Replace `refresh_weather`'s body (currently lines 98-159) with:

```python
def refresh_weather(
    eraldis_gdf: gpd.GeoDataFrame, radar_cache_dir: Path, now: datetime
) -> gpd.GeoDataFrame:
    crs = eraldis_gdf.crs
    bounds = tuple(eraldis_gdf.to_crs("EPSG:4326").total_bounds)

    radar_points, radar_coverage_national = accumulate_rainfall(radar_cache_dir, now, bounds)
    meps_points, meps_coverage, meps_newest_hour = accumulate_meps_features(now, bounds)

    eraldis_projected = eraldis_gdf.to_crs(ESTONIAN_GRID_CRS)
    radar_joined = assign_radar_to_eraldis(eraldis_projected, radar_points, _RADAR_COLUMNS)
    meps_joined = _nearest_join(eraldis_projected, meps_points, _MEPS_COLUMNS)

    result = eraldis_gdf.copy()

    # Per-stand coverage — each stand's own joined coverage_Nd value, run through the
    # same 0<=coverage<=1 invariant used everywhere else. A stand with zero valid
    # radar pixels intersecting it (assign_radar_to_eraldis returns None for
    # coverage_Nd in that case) is treated as coverage 0.0 for degradation purposes —
    # genuinely uncovered, not an unknown to be silently skipped.
    def _stand_coverage(value) -> float:
        return _validate_coverage(0.0 if pd.isna(value) else float(value), label="stand")

    radar_degraded_3d = [
        _stand_coverage(v) < MIN_RADAR_COVERAGE for v in radar_joined["coverage_3d"]
    ]
    radar_degraded_7d = [
        _stand_coverage(v) < MIN_RADAR_COVERAGE for v in radar_joined["coverage_7d"]
    ]
    radar_degraded_14d = [
        _stand_coverage(v) < MIN_RADAR_COVERAGE for v in radar_joined["coverage_14d"]
    ]
    meps_degraded = (
        _is_meps_stale(meps_newest_hour, now) or meps_coverage < MIN_MEPS_COVERAGE
    )

    def _null_if_degraded_per_stand(values, degraded_flags) -> list:
        return [
            None if degraded or pd.isna(v) else v
            for v, degraded in zip(values, degraded_flags)
        ]

    result["rain_3d_mm"] = _null_if_degraded_per_stand(
        radar_joined["rain_3d_mm"], radar_degraded_3d
    )
    result["wet_hours_72h"] = _null_if_degraded_per_stand(
        radar_joined["wet_hours_72h"], radar_degraded_3d
    )
    result["rain_7d_mm"] = _null_if_degraded_per_stand(
        radar_joined["rain_7d_mm"], radar_degraded_7d
    )
    result["rain_14d_mm"] = _null_if_degraded_per_stand(
        radar_joined["rain_14d_mm"], radar_degraded_14d
    )
    result["hours_since_any_rain"] = _null_if_degraded_per_stand(
        radar_joined["hours_since_any_rain"], radar_degraded_14d
    )
    for col in (
        "hours_since_significant_rain",
        "hours_since_strong_rain",
        "last_significant_event_mm",
        "last_strong_event_mm",
        "max_24h_rain_14d",
    ):
        result[col] = _null_if_degraded_per_stand(radar_joined[col], radar_degraded_14d)

    result["rain_0_3d_mm"] = [
        None if degraded or pd.isna(v) else v
        for v, degraded in zip(radar_joined["rain_3d_mm"], radar_degraded_3d)
    ]
    result["rain_3_7d_mm"] = [
        _bin_difference(v7, v3, degraded_7d, degraded_3d)
        for v7, v3, degraded_7d, degraded_3d in zip(
            radar_joined["rain_7d_mm"],
            radar_joined["rain_3d_mm"],
            radar_degraded_7d,
            radar_degraded_3d,
        )
    ]
    result["rain_7_14d_mm"] = [
        _bin_difference(v14, v7, degraded_14d, degraded_7d)
        for v14, v7, degraded_14d, degraded_7d in zip(
            radar_joined["rain_14d_mm"],
            radar_joined["rain_7d_mm"],
            radar_degraded_14d,
            radar_degraded_7d,
        )
    ]
    for col in _MEPS_COLUMNS:
        result[col] = _null_if_degraded(meps_joined[col], meps_degraded)

    result["as_of"] = now
    result["weather_data_coverage"] = [
        _stand_coverage(v) for v in radar_joined["coverage_14d"]
    ]
    result["weather_data_quality"] = [
        weather_data_quality(
            {"3d": _stand_coverage(c3), "7d": _stand_coverage(c7), "14d": _stand_coverage(c14)},
            meps_coverage,
            meps_newest_hour,
            now,
        )
        for c3, c7, c14 in zip(
            radar_joined["coverage_3d"], radar_joined["coverage_7d"], radar_joined["coverage_14d"]
        )
    ]

    return gpd.GeoDataFrame(result, geometry="geometry", crs=crs)
```

Add `_validate_coverage` to the `from shroom_fm.radar import` line (alongside
`accumulate_rainfall`, `assign_radar_to_eraldis`).

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_weather.py -v`
Expected: some pre-existing tests that assumed a single national
`weather_data_quality`/`weather_data_coverage` value will need their fixtures/
assertions updated to the new per-stand shape — fix each to construct
`radar_points`/`_make_radar_points()` with `coverage_3d`/`coverage_7d`/`coverage_14d`
columns (matching Task 6's real output shape) and assert per-stand results, following
the same pattern as the new test above. Do not weaken the per-stand behavior to make
old assertions pass unmodified — update the assertions to match the corrected,
more-honest behavior.

- [ ] **Step 5: Commit**

```bash
git add src/shroom_fm/weather.py tests/test_weather.py
git commit -m "feat: per-stand radar coverage and raster-native assignment in refresh_weather"
```

---

### Task 9: cache migration and real-scale verification

**Files:**
- Modify: `CLAUDE.md`
- Operational: `data/radar_cache/`

**Interfaces:** none — this is real-data verification and documentation, matching this
project's established discipline of live-verifying every pipeline stage before calling
it done.

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest tests/ -q`
Expected: confirm the actual pass count from the real run — do not predict it in
advance, this plan's own arithmetic has been wrong before in this project's history.

- [ ] **Step 2: Move the old KAIA cache aside**

```bash
mv data/radar_cache data/radar_cache_kaia_archive_2026_08_21
```
(Not deleted — kept for reference/comparison, matching this project's general
preference for reversible operations over destructive ones.)

- [ ] **Step 3: Run a real backfill**

```bash
time uv run python scripts/refresh_weather.py
```
Report the real wall-clock time and the real printed `weather_data_quality`
breakdown. Given Task 1/3's findings, this may complete fully (if historical fetch was
resolved) or may only populate the last ~24h (if Task 3 implemented the honest
`NotImplementedError` fallback for older data) — report whichever real outcome occurs,
do not claim more than what actually happened.

- [ ] **Step 4: Spot-check real output against the known Estonia/Valga finding**

```bash
uv run python3 -c "
import geopandas as gpd
w = gpd.read_file('data/weather_eraldis.geojson')
print('rows:', len(w))
print('coverage distribution:', w['weather_data_coverage'].describe())
print('any coverage > 1.0:', (w['weather_data_coverage'] > 1.0).any())
print('quality breakdown:', w['weather_data_quality'].value_counts().to_dict())
"
```
Confirm `weather_data_coverage` genuinely varies across stands now (not one repeated
national value) and that `(w['weather_data_coverage'] > 1.0).any()` is `False` — the
direct, real-data proof the invariant bug is actually fixed, not just unit-tested.

- [ ] **Step 5: Update CLAUDE.md**

Rewrite the "Weather refresh" section to replace every KAIA-specific claim with the
real OPERA facts established in this plan and its spec: the real S3/REST access paths,
the real confirmed grid/cadence/sentinel values, the per-stand (not national) coverage
model, the fixed `coverage > 1.0` bug with its now-exact root cause, and the real
backfill timing/outcome from Step 3. Update the project-status paragraph near the top
of CLAUDE.md to reflect OPERA as the radar source, not KAIA. If Task 1/3 found the
historical REST contract genuinely unresolved, document that honestly as a known
limitation with a pointer to the findings report, rather than implying full 14-day
backfill works when it may not yet.

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document OPERA radar migration in CLAUDE.md"
```
