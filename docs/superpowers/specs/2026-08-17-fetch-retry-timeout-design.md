# Fetch Retry/Timeout Infrastructure — Design

Date: 2026-08-17
Status: Approved

## Purpose

First of two sub-projects building on this session's live WFS findings. None of shroom-fm's
5 network-fetch call sites currently set an explicit timeout or retry on transient failure —
a real risk for long runs (the ETAK road fetch alone took ~90 sequential paged requests over
several minutes; any one transient failure currently aborts the whole run). This adds a
shared retry/timeout helper applied to every fetch call site, so a single dropped connection
or momentary 5xx doesn't waste an entire multi-minute download.

This is explicitly **not** about the CQL annulus-pushdown work (sub-project 2, next) — that
depends on this landing first, so the new CQL-based eraldis fetch gets durability from day
one instead of a second retrofit pass.

## Critical constraint: what NOT to retry

This session hit several genuine `400 Bad Request`s during live ETAK/Metsaregister testing
(wrong `srsName`, wrong bbox axis order, wrong CQL property name) — each represents a
malformed or rejected request, not a transient failure. Retrying these would just repeat the
same failure `max_attempts` times before giving up, wasting time and muddying the real error
under retry-loop noise. The retry helper must retry **only** genuinely transient conditions:
timeouts, connection errors, and `5xx` server errors. `4xx` errors and any OWS-level
exception report must propagate immediately, unretried.

## Why one shared helper works for both `owslib` and raw `requests` call sites

`owslib`'s `WebFeatureService`/`getfeature()` use `requests` internally
(`owslib.util.openURL`, confirmed by reading the installed package's source) — so both
transport paths ultimately raise from the same `requests.exceptions.*` family for
network-level failures (`Timeout`, `ConnectionError`). For HTTP-status-level failures, the
two paths diverge slightly:

- **`owslib`-based calls**: `openURL` raises `owslib.util.ServiceException` for `status_code
  == 400` (an OWS exception report — never retryable), and raises `requests.exceptions.HTTPError`
  via `req.raise_for_status()` for `status_code in [401, 403, 404, 500, 502, 503, 504]`
  (confirmed by reading `owslib/util.py`).
- **Raw-`requests`-based calls**: nothing raises on a bad status unless the caller calls
  `response.raise_for_status()` explicitly, which raises the same
  `requests.exceptions.HTTPError`.

Because both paths converge on `requests.exceptions.HTTPError` for HTTP-status failures, one
retry predicate works for both: retry on `Timeout`, `ConnectionError`, or `HTTPError` where
`exception.response.status_code >= 500`. Never retry `ServiceException`, a `HTTPError` with
status `< 500`, or anything else (e.g. `ValueError` from malformed content).

## `src/shroom_fm/retry.py` (new module)

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
```

- `sleep` is injectable specifically so tests can verify retry/backoff behavior without real
  delays — this is the one piece of this sub-project that gets genuine unit-test coverage
  (see Testing below).
- `owslib.util.ServiceException` needs no explicit check: it isn't a `requests.exceptions.*`
  subclass, so it falls through both `isinstance` branches to `return False` (not retryable)
  automatically — no import of it needed in `retry.py`.
- Default policy: `max_attempts=3` (1 initial + 2 retries), `backoff_seconds=(1.0, 2.0)`
  (wait 1s after the 1st failure, 2s after the 2nd; the 3rd/last attempt's failure is raised
  immediately with no further wait).

## Applying it at all 5 call sites

Each site wraps only the individual HTTP call, not the whole surrounding function — so a
failure on page 37 of a 90-page pagination loop retries page 37, not the whole fetch from
page 0.

- **`src/shroom_fm/wfs.py`** — `fetch_capabilities(url)`: wrap the `WebFeatureService(url,
  version="2.0.0")` constructor call (this is also what performs the implicit
  `GetCapabilities` request).
- **`src/shroom_fm/enrich.py`** — `fetch_classifier`: wrap `wfs.getfeature(...)`.
  `fetch_eraldis_element`: wrap `requests.get(...)`.
- **`src/shroom_fm/eraldis.py`** — `fetch_eraldis_bbox`: wrap `wfs.getfeature(...)` (inside
  the existing pagination loop).
- **`src/shroom_fm/roads.py`** — `fetch_layer_bbox`: wrap `requests.get(...)` (inside the
  existing pagination loop).

## Two prerequisite fixes (required for the exception model to work uniformly)

- **`enrich.py`'s `fetch_eraldis_element`** currently does not call
  `response.raise_for_status()` after `requests.get(...)`. Without it, a `5xx` response
  raises nothing at all — `call_with_retry` would never see a retryable exception, and the
  bad response body would silently flow into `response.json()` and fail there with an
  unrelated, confusing `JSONDecodeError` instead of being retried. Adding
  `response.raise_for_status()` immediately after the `requests.get(...)` call is required,
  not optional.
- **`roads.py`'s `fetch_layer_bbox`** already calls `response.raise_for_status()` (added in
  the prior road-access branch's final review fix) — no change needed there.

## Explicit timeouts

Both raw-`requests` call sites (`enrich.py`'s `fetch_eraldis_element`, `roads.py`'s
`fetch_layer_bbox`) get `timeout=30` added to their `requests.get(...)` calls — matching
`owslib`'s own default `timeout=30` (confirmed in `owslib/feature/wfs200.py`), which the
`owslib`-based call sites already have via `fetch_capabilities`'s existing pass-through of
`WebFeatureService`'s `timeout` parameter (no change needed there beyond wrapping with
retry).

## Testing

`tests/test_retry.py` (new file) gets full unit coverage of `call_with_retry`, using a fake
`sleep` function (records calls, doesn't actually delay) and fake target functions:

- Succeeds on the first attempt: returns the value, `sleep` never called.
- Fails once (retryable), then succeeds: returns the value, `sleep` called exactly once with
  `backoff_seconds[0]`.
- Fails `max_attempts` times (all retryable): raises the last exception, `sleep` called
  `max_attempts - 1` times with the expected backoff values.
- A non-retryable exception (`ServiceException`, a `HTTPError` with `status_code=400`, or a
  plain `ValueError`) raised on the first attempt: raises immediately, `sleep` never called,
  no retry attempted.
- A `HTTPError` with `status_code=503` is retried; a `HTTPError` with `status_code=404` is
  not — covering the 5xx/4xx boundary explicitly.

No new tests for the 5 wrapped fetch functions themselves — matches this project's existing,
established precedent (network-touching functions are verified by live runs, not unit
tests; see `fetch_eraldis_bbox`, `fetch_layer_bbox`, neither of which has a dedicated test
today).

## Out of scope

- The CQL annulus-pushdown work itself (sub-project 2 — depends on this landing first).
- Jitter/randomization on backoff delays.
- Per-call-site-configurable retry policy — one shared default policy for all 5 sites.
- Circuit breakers, rate limiting, or any cross-request state.
- Retrying `owslib.util.ServiceException` under any condition — it always represents a
  rejected/malformed request per this session's live findings, never a transient one.
