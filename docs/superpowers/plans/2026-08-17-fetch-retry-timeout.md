# Fetch Retry/Timeout Infrastructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a shared retry/timeout helper, applied to all 5 network-fetch call sites in
shroom-fm, that retries only genuinely transient failures (timeouts, connection errors,
5xx) and never malformed-request 400s — so a single dropped connection during a long
multi-page WFS download doesn't abort the whole run.

**Architecture:** New `src/shroom_fm/retry.py` module with two functions:
`call_with_retry` (generic — wraps any callable) for the 3 `owslib`-based call sites, and
`get_with_retry` (a thin `requests.get` + `raise_for_status` + retry convenience wrapper
built on top of `call_with_retry`) for the 2 raw-`requests`-based call sites. Both are fully
unit-tested with an injectable `sleep`. The 5 existing fetch functions then each get their
one network call wrapped, unchanged otherwise.

**Tech Stack:** Python, `requests`, `owslib`, pytest — same as the rest of the project. No
new dependencies.

## Global Constraints

- Retry **only** on `requests.exceptions.Timeout`, `requests.exceptions.ConnectionError`,
  or `requests.exceptions.HTTPError` where `exception.response.status_code >= 500`. Never
  retry `owslib.util.ServiceException`, any `HTTPError` with status `< 500`, or any other
  exception type — these represent a malformed/rejected request (this session hit several
  real `400`s from wrong `srsName`/axis order; retrying those wastes time repeating the
  same failure).
- Default retry policy: `max_attempts=3` (1 initial try + 2 retries), `backoff_seconds=(1.0,
  2.0)` (wait 1s after the 1st failure, 2s after the 2nd; the 3rd/last failure raises
  immediately with no further wait).
- `sleep` must be an injectable parameter (default `time.sleep`) so tests can verify retry
  count and backoff values without real delays.
- Both raw-`requests` call sites (`enrich.py`'s `fetch_eraldis_element`, `roads.py`'s
  `fetch_layer_bbox`) get `timeout=30` on their `requests.get(...)` call — matching
  `owslib`'s own default `timeout=30`, which the 3 `owslib`-based call sites already have.
- **Correctness requirement for the raw-`requests` sites:** `response.raise_for_status()`
  must be called *inside* the callable passed to the retry wrapper, not after it returns —
  calling it afterward means a bad-status response would already have been returned
  successfully by the retry wrapper (since plain `requests.get` doesn't raise on a bad
  status by itself), so it would never actually be retried. This is why `get_with_retry`
  exists as a combined `get` + `raise_for_status` + retry helper, rather than wrapping
  `requests.get` alone.
- Wrap only the individual HTTP call inside each function's pagination loop, not the whole
  function — a failure on page 37 of 90 must retry page 37, not restart from page 0.
- No new tests for the 5 wrapped fetch functions themselves — matches this project's
  existing precedent (network-touching functions are verified by live runs, not unit
  tests; `fetch_eraldis_bbox` and `fetch_layer_bbox` both have no dedicated test today).

---

### Task 1: `retry.py` — `call_with_retry` and `get_with_retry`

**Files:**
- Create: `src/shroom_fm/retry.py`
- Test: `tests/test_retry.py` (new file)

**Interfaces:**
- Consumes: nothing from elsewhere in the codebase.
- Produces: `call_with_retry(func, *args, max_attempts=3, backoff_seconds=(1.0, 2.0),
  sleep=time.sleep, **kwargs)` — calls `func(*args, **kwargs)`, retrying on transient
  failure per the Global Constraints, returning `func`'s return value or re-raising its
  last exception. `get_with_retry(url, *, max_attempts=3, backoff_seconds=(1.0, 2.0),
  sleep=time.sleep, **kwargs)` — calls `requests.get(url, **kwargs)` then
  `response.raise_for_status()` inside the retried callable, returning the `Response`. Task
  2 imports both by name from `shroom_fm.retry`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_retry.py`:

```python
import requests
import pytest

from shroom_fm.retry import call_with_retry, get_with_retry


def _http_error(status_code: int) -> requests.exceptions.HTTPError:
    response = requests.Response()
    response.status_code = status_code
    return requests.exceptions.HTTPError(response=response)


class _FailNTimes:
    def __init__(self, fail_count, exc_factory, return_value="ok"):
        self.fail_count = fail_count
        self.exc_factory = exc_factory
        self.return_value = return_value
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.calls <= self.fail_count:
            raise self.exc_factory()
        return self.return_value


def test_call_with_retry_returns_immediately_on_success():
    sleeps = []
    target = _FailNTimes(fail_count=0, exc_factory=requests.exceptions.Timeout)

    result = call_with_retry(target, sleep=sleeps.append)

    assert result == "ok"
    assert target.calls == 1
    assert sleeps == []


def test_call_with_retry_retries_transient_failure_then_succeeds():
    sleeps = []
    target = _FailNTimes(fail_count=1, exc_factory=requests.exceptions.ConnectionError)

    result = call_with_retry(target, sleep=sleeps.append)

    assert result == "ok"
    assert target.calls == 2
    assert sleeps == [1.0]


def test_call_with_retry_exhausts_all_attempts_then_raises():
    sleeps = []
    target = _FailNTimes(fail_count=5, exc_factory=requests.exceptions.Timeout)

    with pytest.raises(requests.exceptions.Timeout):
        call_with_retry(target, max_attempts=3, sleep=sleeps.append)

    assert target.calls == 3
    assert sleeps == [1.0, 2.0]


def test_call_with_retry_does_not_retry_service_exception():
    from owslib.util import ServiceException

    sleeps = []
    target = _FailNTimes(fail_count=5, exc_factory=lambda: ServiceException("bad request"))

    with pytest.raises(ServiceException):
        call_with_retry(target, sleep=sleeps.append)

    assert target.calls == 1
    assert sleeps == []


def test_call_with_retry_does_not_retry_client_error_http_status():
    sleeps = []
    target = _FailNTimes(fail_count=5, exc_factory=lambda: _http_error(404))

    with pytest.raises(requests.exceptions.HTTPError):
        call_with_retry(target, sleep=sleeps.append)

    assert target.calls == 1
    assert sleeps == []


def test_call_with_retry_does_retry_server_error_http_status():
    sleeps = []
    target = _FailNTimes(fail_count=1, exc_factory=lambda: _http_error(503))

    result = call_with_retry(target, sleep=sleeps.append)

    assert result == "ok"
    assert target.calls == 2
    assert sleeps == [1.0]


def test_call_with_retry_does_not_retry_plain_value_error():
    sleeps = []
    target = _FailNTimes(fail_count=5, exc_factory=lambda: ValueError("malformed"))

    with pytest.raises(ValueError):
        call_with_retry(target, sleep=sleeps.append)

    assert target.calls == 1
    assert sleeps == []


class _FakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise _http_error(self.status_code)


def test_get_with_retry_returns_response_on_success(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return _FakeResponse(200)

    monkeypatch.setattr("shroom_fm.retry.requests.get", fake_get)

    result = get_with_retry(
        "http://example.com", params={"a": 1}, timeout=30, sleep=lambda s: None
    )

    assert result.status_code == 200
    assert calls == [("http://example.com", {"params": {"a": 1}, "timeout": 30})]


def test_get_with_retry_retries_server_error_then_succeeds(monkeypatch):
    responses = [_FakeResponse(503), _FakeResponse(200)]
    sleeps = []

    def fake_get(url, **kwargs):
        return responses.pop(0)

    monkeypatch.setattr("shroom_fm.retry.requests.get", fake_get)

    result = get_with_retry("http://example.com", sleep=sleeps.append)

    assert result.status_code == 200
    assert sleeps == [1.0]


def test_get_with_retry_does_not_retry_client_error(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return _FakeResponse(400)

    monkeypatch.setattr("shroom_fm.retry.requests.get", fake_get)

    with pytest.raises(requests.exceptions.HTTPError):
        get_with_retry("http://example.com", sleep=lambda s: None)

    assert len(calls) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_retry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shroom_fm.retry'`.

- [ ] **Step 3: Write the implementation**

Create `src/shroom_fm/retry.py`:

```python
import time

import requests

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS = (1.0, 2.0)


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
        return True
    if isinstance(exc, requests.exceptions.HTTPError):
        response = exc.response
        return response is not None and response.status_code >= 500
    return False


def call_with_retry(
    func,
    *args,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff_seconds: tuple[float, ...] = DEFAULT_BACKOFF_SECONDS,
    sleep=time.sleep,
    **kwargs,
):
    for attempt in range(max_attempts):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            if not _is_retryable(exc) or attempt == max_attempts - 1:
                raise
            delay = backoff_seconds[min(attempt, len(backoff_seconds) - 1)]
            sleep(delay)


def get_with_retry(
    url: str,
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff_seconds: tuple[float, ...] = DEFAULT_BACKOFF_SECONDS,
    sleep=time.sleep,
    **kwargs,
):
    def _get():
        response = requests.get(url, **kwargs)
        response.raise_for_status()
        return response

    return call_with_retry(
        _get, max_attempts=max_attempts, backoff_seconds=backoff_seconds, sleep=sleep
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_retry.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Run the full test suite to confirm no regressions**

Run: `uv run pytest tests/ -v`
Expected: PASS (114 tests: 103 existing + 11 new)

- [ ] **Step 6: Commit**

```bash
git add src/shroom_fm/retry.py tests/test_retry.py
git commit -m "feat: add call_with_retry/get_with_retry transient-failure retry helper"
```

---

### Task 2: Apply retry/timeout to all 5 fetch call sites

**Files:**
- Modify: `src/shroom_fm/wfs.py` (`fetch_capabilities`)
- Modify: `src/shroom_fm/enrich.py` (`fetch_classifier`, `fetch_eraldis_element`)
- Modify: `src/shroom_fm/eraldis.py` (`fetch_eraldis_bbox`)
- Modify: `src/shroom_fm/roads.py` (`fetch_layer_bbox`)

**Interfaces:**
- Consumes: `call_with_retry`, `get_with_retry` from `src/shroom_fm/retry.py` (Task 1).
- Produces: no new public interfaces — all 4 files' existing function signatures and return
  types are unchanged. No later task in this plan depends on this (final task).

This task has no dedicated unit tests (matches the Global Constraints' stated precedent —
these are the same 5 live-network functions that have never had unit tests). Verification is
the full existing test suite (proves nothing broke) plus a careful manual read-through of
each of the 4 diffs against the exact code shown below (proves the wrapping was applied
correctly, since a subtly-wrong wrap — e.g. wrapping the wrong call, or calling
`raise_for_status()` outside the retried callable — would not be caught by any existing
test).

- [ ] **Step 1: Update `src/shroom_fm/wfs.py`**

Replace the full contents of `src/shroom_fm/wfs.py` with:

```python
import json
from pathlib import Path

from owslib.wfs import WebFeatureService

from shroom_fm.retry import call_with_retry

METSAREGISTER_OWS_URL = "https://gsavalik.envir.ee/geoserver/metsaregister/ows"
ETAK_WFS_URL = "https://gsavalik.envir.ee/geoserver/etak/wfs"


def fetch_capabilities(url: str = METSAREGISTER_OWS_URL) -> WebFeatureService:
    return call_with_retry(WebFeatureService, url, version="2.0.0")


def layer_summary(wfs) -> list[dict]:
    layers = [
        {"name": name, "title": meta.title, "abstract": meta.abstract}
        for name, meta in wfs.contents.items()
    ]
    return sorted(layers, key=lambda layer: layer["name"])


def save_layers_json(layers: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(layers, indent=2, ensure_ascii=False) + "\n")
```

The only changes from the current version: the new `from shroom_fm.retry import
call_with_retry` import, and `fetch_capabilities`'s body wraps the `WebFeatureService(...)`
constructor call. `layer_summary`/`save_layers_json` are untouched.

- [ ] **Step 2: Update `src/shroom_fm/enrich.py`**

In `src/shroom_fm/enrich.py`, add the import (alongside the existing imports at the top):

```python
from shroom_fm.retry import call_with_retry, get_with_retry
```

Replace the existing `fetch_classifier` function with:

```python
def fetch_classifier(wfs: WebFeatureService, typename: str) -> dict[str, str]:
    response = call_with_retry(
        wfs.getfeature, typename=typename, outputFormat="application/json"
    )
    data = json.loads(response.read())
    return {
        feature["properties"]["kood"]: feature["properties"]["kirjeldus"]
        for feature in data["features"]
    }
```

Replace the existing `fetch_eraldis_element` function with:

```python
def fetch_eraldis_element(eraldis_ids: list[int]) -> pd.DataFrame:
    rows = []
    for i in range(0, len(eraldis_ids), ID_BATCH_SIZE):
        batch = eraldis_ids[i : i + ID_BATCH_SIZE]
        id_list = ",".join(str(eid) for eid in batch)
        response = get_with_retry(
            METSAREGISTER_OWS_URL,
            params={
                "service": "WFS",
                "version": "2.0.0",
                "request": "GetFeature",
                "typeName": ERALDIS_ELEMENT_TYPENAME,
                "outputFormat": "application/json",
                "CQL_FILTER": f"eraldis_id IN ({id_list})",
            },
            timeout=30,
        )
        data = response.json()
        rows.extend(feature["properties"] for feature in data["features"])
    return pd.DataFrame(rows)
```

Note this adds `timeout=30` (not present before) and switches from bare `requests.get(...)`
to `get_with_retry(...)` — `response.raise_for_status()` is no longer called separately
because `get_with_retry` already calls it internally, inside the retried callable (calling
it a second time afterward would be redundant dead code). `fetch_eraldis_element` was the
only user of `requests` in this file (confirmed: `grep -n "requests\." src/shroom_fm/enrich.py`
matches exactly one line before this change) — after this change, **remove the now-unused
`import requests` line** from the top of `enrich.py`.

- [ ] **Step 3: Update `src/shroom_fm/eraldis.py`**

In `src/shroom_fm/eraldis.py`, add the import (alongside the existing imports at the top):

```python
from shroom_fm.retry import call_with_retry
```

Replace the existing `fetch_eraldis_bbox` function with:

```python
def fetch_eraldis_bbox(
    wfs: WebFeatureService, bbox: tuple[float, float, float, float]
) -> gpd.GeoDataFrame:
    pages = []
    start_index = 0
    while True:
        response = call_with_retry(
            wfs.getfeature,
            typename=ERALDIS_TYPENAME,
            bbox=(*bbox, WGS84_URN),
            srsname=WGS84_CRS,
            outputFormat="application/json",
            startindex=start_index,
            maxfeatures=PAGE_SIZE,
        )
        page = gpd.read_file(io.BytesIO(response.read()))
        pages.append(page)
        if len(page) < PAGE_SIZE:
            break
        start_index += PAGE_SIZE
    return gpd.GeoDataFrame(pd.concat(pages, ignore_index=True), crs=pages[0].crs)
```

`compute_bbox` and `filter_within_radius` are untouched.

- [ ] **Step 4: Update `src/shroom_fm/roads.py`**

In `src/shroom_fm/roads.py`, add the import (alongside the existing imports at the top):

```python
from shroom_fm.retry import get_with_retry
```

Replace the existing `fetch_layer_bbox` function with:

```python
def fetch_layer_bbox(url: str, typename: str, bbox: tuple[float, float, float, float]) -> gpd.GeoDataFrame:
    # ETAK's WFS (unlike Metsaregister's) enforces the EPSG:4326 URN's strict
    # authority axis order (lat, lon) for the bbox filter, and only allows
    # EPSG:3301 as output srsName for this layer — both confirmed live
    # 2026-08-17 (see CLAUDE.md's "Known real-data quirks"). owslib's
    # getfeature() silently re-serializes any bbox tuple back to (lon, lat)
    # regardless of the order passed in, defeating the axis fix — confirmed
    # live by inspecting the actual request URL it sends — so this fetch
    # uses requests directly instead, matching enrich.py's precedent for
    # owslib limitations.
    minx, miny, maxx, maxy = bbox
    bbox_param = f"{miny},{minx},{maxy},{maxx},{_WGS84_URN}"
    pages = []
    start_index = 0
    while True:
        response = get_with_retry(
            url,
            params={
                "service": "WFS",
                "version": "2.0.0",
                "request": "GetFeature",
                "typeNames": typename,
                "bbox": bbox_param,
                "srsName": _ETAK_OUTPUT_CRS,
                "outputFormat": "application/json",
                "startIndex": start_index,
                "count": _PAGE_SIZE,
            },
            timeout=30,
        )
        page = gpd.read_file(io.BytesIO(response.content))
        pages.append(page)
        if len(page) < _PAGE_SIZE:
            break
        start_index += _PAGE_SIZE
    return gpd.GeoDataFrame(pd.concat(pages, ignore_index=True), crs=pages[0].crs)
```

The only changes from the current version: the new `from shroom_fm.retry import
get_with_retry` import, `timeout=30` added to the request, the switch from
`requests.get(...)` + a separate `response.raise_for_status()` line to `get_with_retry(...)`
(which calls `raise_for_status()` internally, inside the retried callable — the separate
line is removed as redundant), and `response.content` stays the same (unchanged from
before). `classify_car_class` and `exclude_barrier_blocked_segments` are untouched.
`fetch_layer_bbox` was the only user of `requests` in this file (confirmed: `grep -n
"requests\." src/shroom_fm/roads.py` matches exactly one line before this change) — after
this change, **remove the now-unused `import requests` line** from the top of `roads.py`.

- [ ] **Step 5: Run the full test suite to confirm no regressions**

Run: `uv run pytest tests/ -v`
Expected: PASS (114 tests — this step adds no new tests, only wiring changes to existing
functions)

- [ ] **Step 6: Manually verify the wrapping is correct**

Re-read all 4 modified files once more against the code blocks in Steps 1-4 above, checking
specifically:
- `fetch_capabilities`, `fetch_classifier`, `fetch_eraldis_bbox` each wrap their single
  network call with `call_with_retry`, passing the callable as the first positional
  argument (not calling it themselves first).
- `fetch_eraldis_element` and `fetch_layer_bbox` each use `get_with_retry` instead of raw
  `requests.get`, and neither has a leftover separate `response.raise_for_status()` call
  after it (that would be dead/redundant code, since `get_with_retry` already calls it
  internally).
- Both `get_with_retry` call sites pass `timeout=30`.
- No function's pagination loop was accidentally wrapped as a whole (i.e. `call_with_retry`
  or `get_with_retry` is called once per page inside the `while True:` loop, not once around
  the entire loop).
- `import requests` was removed from both `enrich.py` and `roads.py` (no longer used by
  either file after switching to `get_with_retry`).

- [ ] **Step 7: Commit**

```bash
git add src/shroom_fm/wfs.py src/shroom_fm/enrich.py src/shroom_fm/eraldis.py src/shroom_fm/roads.py
git commit -m "feat: apply retry/timeout to all WFS fetch call sites"
```

---

## Self-Review Notes

- **Spec coverage:** the spec's `call_with_retry` design, retry-vs-not-retry predicate,
  default policy, injectable `sleep`, and application to all 5 call sites are all covered.
- **Spec deviation, deliberately introduced during planning:** the spec only described
  `call_with_retry` (a generic wrapper). While writing this plan, applying it literally to
  the 2 raw-`requests` call sites surfaced a real correctness gap: if `response =
  call_with_retry(requests.get, url, ...)` and `response.raise_for_status()` is called
  *after* that line (as the spec's prose implies), a bad-status response is already
  successfully returned by `call_with_retry` before `raise_for_status()` ever runs — so it
  would never be retried, silently defeating the point of this whole plan for exactly the 2
  call sites most likely to hit transient failures (raw HTTP, no `owslib` status-code
  handling). This plan adds `get_with_retry` (Task 1) — a `requests.get` +
  `raise_for_status` + retry convenience function that calls `raise_for_status()` *inside*
  the retried callable — and uses it for both `fetch_eraldis_element` and
  `fetch_layer_bbox` (Task 2) instead of wrapping bare `requests.get`. This is a refinement
  of the spec's design to fix a bug the spec's prose would otherwise have produced, not a
  scope change — flagged here per this project's practice of documenting deviations
  transparently rather than silently diverging from an approved spec.
- **Placeholder scan:** none found — every step has complete, runnable code.
- **Type consistency:** `call_with_retry(func, *args, max_attempts=..., backoff_seconds=...,
  sleep=..., **kwargs)` and `get_with_retry(url, *, max_attempts=..., backoff_seconds=...,
  sleep=..., **kwargs)` signatures are identical between Task 1's tests, Task 1's
  implementation, and every Task 2 call site.
