# Concurrent, Observable WFS Fetching — Design

## Problem

`download_eraldis.py` against the user's real annulus (33-70km around home, ~65,577
stands) takes an "enormous amount of time" with zero progress output — the process is
silent until it either finishes or is killed. The root cause is `eraldis.py`'s
`fetch_eraldis_annulus`: it already does server-side CQL annulus filtering (fast), but
pages through the result set with `startIndex`/`count` **sequentially**, one WFS
`GetFeature` round-trip at a time (~66 round-trips at `PAGE_SIZE=1000`), printing
nothing until the whole thing is done.

The same shape exists in two other places once every network-bound multi-request fetch
in the project is inventoried:

| Site | Pattern | Real-scale cost (last measured) |
|---|---|---|
| `eraldis.py::fetch_eraldis_annulus` | `startIndex`/`count` pagination over a CQL annulus filter | reported by user as "enormous" for the 33-70km annulus |
| `roads.py::fetch_layer_annulus` | identical pagination pattern (duplicated code), used for both roads (~50k) and barriers (~1.5k) | ~4-5 min, tolerable today but same shape |
| `enrich.py::fetch_eraldis_element` | batches a known `eraldis_id` list, `ID_BATCH_SIZE=500` ids/request, sequential | **471.2s** in the last full-pipeline run — the single slowest step of the entire 9-step pipeline, slower than either download step |

`enrich.py::fetch_classifier` (two tiny classifier-lookup requests) and
`wfs.py::fetch_capabilities` (one-off capabilities call) are single-request call sites —
out of scope, nothing to parallelize.

## Approach

Add one new shared module, `src/shroom_fm/concurrent_fetch.py`, providing:

1. `fetch_hit_count(url, base_params, *, timeout=30) -> int` — issues the same request
   as a normal page fetch but with `resultType=hits` added, and parses the total match
   count. Per the WFS 2.0.0 spec, a `resultType=hits` response contains no feature
   instances regardless of the requested `outputFormat` — GeoServer returns XML with a
   `numberMatched` attribute on the root `wfs:FeatureCollection` element even when
   `outputFormat=application/json` was requested. Parsed via
   `xml.etree.ElementTree.fromstring(response.content).get("numberMatched")`. This
   XML-regardless-of-outputFormat behavior is a WFS-spec-level guarantee, not
   Metsaregister/ETAK-specific, but **will be confirmed live against both servers as the
   first implementation task**, consistent with this project's established practice of
   verifying WFS quirks against the real service before relying on them (see CLAUDE.md's
   "Known real-data quirks"). If live behavior differs, the fallback is parsing
   `outputFormat=application/json`'s `totalFeatures` key instead — a one-line change
   isolated to this function.

2. `fetch_pages_concurrently(url, params_list, *, max_workers=6, timeout=30, progress_label="page") -> list[bytes]` —
   given an ordered list of param dicts (one per request), fires them on a bounded
   `concurrent.futures.ThreadPoolExecutor`, and returns response bodies (`response.content`)
   **in the same order as `params_list`** (not completion order — results are written
   into a pre-sized list by index, not appended as futures complete). Each individual
   request still goes through the existing `get_with_retry` (so a single transient
   failure is retried in place, not treated as a whole-batch failure). Prints one
   progress line per completed request: `fetched {done}/{total} {progress_label}s`. If
   any request exhausts its retries and raises, the exception propagates immediately
   (no silent partial results) after in-flight requests finish and no new ones start —
   matching the existing "never fabricate/never silently drop data" fail-fast behavior
   used throughout this codebase (e.g. `ScoutScore`'s explicit-tier design). An empty
   `params_list` returns `[]` without spinning up a thread pool.

`max_workers=6` is a fixed, deliberately modest default — enough to turn ~66 sequential
round-trips into ~11 concurrent waves without hammering a government WFS server the way
an unbounded thread pool would.

### Call site changes

**`eraldis.py::fetch_eraldis_annulus`** — build the base CQL params dict once, call
`fetch_hit_count` to learn the total, build a list of `{**base_params, startIndex, count}`
page param dicts (`max(1, ceil(total / PAGE_SIZE))` entries — always at least one request,
matching today's behavior for a zero-result annulus), call `fetch_pages_concurrently`,
then `gpd.read_file` each returned page and `pd.concat` as before. Public signature
unchanged.

**`roads.py::fetch_layer_annulus`** — identical restructuring, parameterized by
`url`/`typename` as it already is. Public signature unchanged. This also incidentally
de-duplicates the pagination loop that was copy-pasted from `eraldis.py` when
`fetch_layer_annulus` was written (now both route through the one shared helper instead
of two independent copies of the same loop).

**`enrich.py::fetch_eraldis_element`** — no hit-count query needed; total batch count is
already known from `len(eraldis_ids)`. Build the list of `CQL_FILTER=eraldis_id IN (...)`
param dicts directly (`ID_BATCH_SIZE=500` per batch, unchanged), call
`fetch_pages_concurrently`, then `json.loads` each returned body and extend `rows` as
before. Public signature unchanged. Empty `eraldis_ids` continues to skip fetching
entirely (returns `pd.DataFrame([])`, no behavior change).

No calling script (`download_eraldis.py`, `download_roads.py`, `enrich_eraldis.py`,
`main.py`) needs to change — every touched function keeps its existing signature and
return type.

## Testing

Existing tests for `fetch_eraldis_annulus`, `fetch_layer_annulus`, and
`fetch_eraldis_element` mock `get_with_retry` with a `side_effect` list assuming
sequential call order. That assumption breaks under real concurrency, so those mocks
must become **order-independent**: keyed by an inspectable property of each call's
`params` (e.g. `params["startIndex"]` or the batch's id-list), not by call sequence.

New tests for `concurrent_fetch.py` itself:
- `fetch_pages_concurrently` returns results in `params_list` order even when a mock
  makes an early-index request finish after a later-index one (e.g. via a small
  `time.sleep` keyed to index, or a threading `Event`/barrier).
- `fetch_hit_count` parses a canned WFS `resultType=hits` XML response correctly.
- A single failing request (after retries exhausted) propagates its exception out of
  `fetch_pages_concurrently` rather than being swallowed.
- Empty `params_list` returns `[]` without touching the network layer (assert the mock
  was never called).
- Progress lines are printed (capture stdout, assert `done/total` counts appear in
  order).

Updated tests for the three call sites verify the same row-count/column assertions as
today, now against the hit-count-then-concurrent-page flow.

## Out of scope

- `fetch_classifier`, `fetch_capabilities` — single-request call sites, no pagination to
  parallelize.
- Any change to `PAGE_SIZE`/`ID_BATCH_SIZE` values themselves.
- Any change to retry/backoff behavior in `retry.py` — reused as-is per request.
- Any change to `roads.py`'s barrier-snap exclusion or `enrich.py`'s composition-summary
  logic downstream of the fetch.
