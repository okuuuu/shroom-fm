# ORD REST API Historical-Query Findings (2026-08-21)

All testing below was done live against `https://api.meteogate.eu/eu-eumetnet-weather-radar`
on 2026-08-21, anonymously (no API key), starting around `2026-08-21T08:33:59Z` server time.
Roughly 35 requests were used against the confirmed 200/hour anonymous rate limit (dropped
from `X-RateLimit-Remaining: 190` to `167` over the course of this investigation) — well
within the "handful, not dozens" budget.

## Working request

A working, real-data-returning request **was** found — but only on the `/locations/{id}`
endpoint, not `/area` (the endpoint the brief's leading hypothesis focused on).

**Working:**
```bash
curl -sS -i -H "Accept: application/prs.coverage+json" \
  "https://api.meteogate.eu/eu-eumetnet-weather-radar/collections/observations/locations/0-20010-0-OPERA?parameter-name=RATE:comp&level=0.0&format=ODIM&datetime=2026-08-21T07:30:00Z/2026-08-21T08:30:00Z"
```
Result: `HTTP/1.1 200 OK`, `Content-Type: application/prs.coverage+json`, real CoverageJSON
body containing 4 file-download links.

Key facts about the working combination:
- The `Accept: application/prs.coverage+json` header **was** required/correct — this part
  of the brief's hypothesis was right.
- The query-parameter name is **`parameter-name`** (e.g. `RATE:comp`), not the brief's
  guessed `standard_name`+`format`+`method` triplet used separately (`format=ODIM` and
  `level=0.0` are still separate params; `standard_name`/`method` are folded into the single
  `parameter-name` value, discovered from the real API's own self-describing links — see below).
- The location id **`0-20010-0-OPERA`** is the only location this API exposes for the OPERA
  composite (confirmed via `/collections/observations/locations`, which returns exactly one
  `Feature` with `id: "0-20010-0-OPERA"`).
- Discovery path: `/collections/observations/locations` (no location id) returned a real
  `200` listing that location's `timeseries-link`, which pointed at
  `/collections/observations/items?platform=0-20010-0-OPERA&format=ODIM`. That endpoint in
  turn returned (also real `200`) two `Feature`s — one per available `parameter_name`
  (`DBZH:comp`, `RATE:comp`) — each carrying a `"data"` link in exactly the working shape
  above. Following that self-describing chain (collections → locations → items → data link),
  rather than guessing params, is what produced the first working request.

**`/area` was tried repeatedly and never worked, even for a time range confirmed to have
real data via `/locations`:**
```bash
curl -sS -i -H "Accept: application/prs.coverage+json" \
  "https://api.meteogate.eu/eu-eumetnet-weather-radar/collections/observations/area?coords=POLYGON((20%2058,26%2058,26%2060,20%2060,20%2058))&datetime=2026-08-21T07:30:00Z/2026-08-21T08:30:00Z&standard_name=RATE&format=ODIM&method=comp"
# -> 204 No Content
curl -sS -i -H "Accept: application/prs.coverage+json" \
  "https://api.meteogate.eu/eu-eumetnet-weather-radar/collections/observations/area?coords=POLYGON((20%2058,26%2058,26%2060,20%2060,20%2058))&datetime=2026-08-21T07:30:00Z/2026-08-21T08:30:00Z&standard_name=RATE&format=GeoTIFF&method=comp"
# -> 503 Service Temporarily Unavailable (text/html error page, not JSON)
curl -sS -i -H "Accept: application/prs.coverage+json" \
  "https://api.meteogate.eu/eu-eumetnet-weather-radar/collections/observations/area?coords=POLYGON((20%2058,26%2058,26%2060,20%2060,20%2058))&parameter-name=RATE:comp&level=0.0&format=ODIM&datetime=2026-08-21T07:30:00Z/2026-08-21T08:30:00Z"
# -> 503 first try, 204 No Content on retry
```
Even using the correct `parameter-name` syntax (confirmed working on `/locations`) and a
datetime range **known** to have real data (the exact same range that returned 4 real file
links via `/locations`), `/area` returned `204`/`503` every time, never real content, across
4 attempts with 2 different parameter styles. This is a genuine, confirmed dead end for
`/area`, not an unresolved ambiguity — the working endpoint for this API is `/locations/{id}`.

`503 Service Temporarily Unavailable` (generic nginx HTML error page, not part of the API's
JSON error contract) appeared intermittently and unpredictably across **both** working and
non-working query shapes throughout this session — e.g. the exact same `/locations` query
that returned `200` with real data on one call returned `503` on an immediately-preceding or
following call at a *different* datetime range. Retrying after a few seconds consistently
cleared it. This looks like general backend flakiness/load-shedding unrelated to query
correctness or historical-vs-recent datetime ranges, not a signal about data availability —
`204 No Content` (not `503`) was the consistent, reproducible signal for "no data in this
range," confirmed by retrying identical out-of-window queries 2-3 times each and always
getting `204`, never `200` with content.

## Response format

`Content-Type: application/prs.coverage+json`. Body is a CoverageJSON `Coverage` object:

```json
{
  "type": "Coverage",
  "domain": {
    "type": "Domain", "domainType": "PointSeries",
    "axes": {
      "x": {"values": [9.315660033646026]},
      "y": {"values": [49.5944338661502]},
      "z": {"values": [0.0]},
      "t": {"values": ["2026-08-21T07:30:00Z", "2026-08-21T07:45:00Z", "2026-08-21T08:00:00Z", "2026-08-21T08:15:00Z"]}
    }
  },
  "parameters": {"RATE:comp": { ... }},
  "ranges": {"RATE:comp": {"type": "NdArray", "dataType": "float", "axisNames": ["t","z","x","y"], "shape": [4,1,1,1], "values": [0.0, 0.0, 0.0, 0.0]}},
  "links": [
    {"href": "...", "rel": "canonical", ...},
    {"href": "wss://radar.meteogate.eu/ordmqtt", "rel": "items", "type": "application/prs.coverage+json", "title": "Open Radar Data Notification Service"},
    {"href": "https://s3.waw3-1.cloudferro.com/openradar-24h/2026/08/21/OPERA/COMP/OPERA@20260821T0730@0@RATE.h5", "rel": "items", "type": "application/x-odim", "title": "Data download link.", "length": 1298366},
    {"href": "https://s3.waw3-1.cloudferro.com/openradar-24h/2026/08/21/OPERA/COMP/OPERA@20260821T0745@0@RATE.h5", "rel": "items", "type": "application/x-odim", "title": "Data download link.", "length": 1301227},
    {"href": "https://s3.waw3-1.cloudferro.com/openradar-24h/2026/08/21/OPERA/COMP/OPERA@20260821T0800@0@RATE.h5", "rel": "items", "type": "application/x-odim", "title": "Data download link.", "length": 1319527},
    {"href": "https://s3.waw3-1.cloudferro.com/openradar-24h/2026/08/21/OPERA/COMP/OPERA@20260821T0815@0@RATE.h5", "rel": "items", "type": "application/x-odim", "title": "Data download link.", "length": 1320463}
  ]
}
```

**Important nuance:** the `ranges.RATE:comp.values` array (a plain float per timestamp) is a
**point extraction at the composite grid's own fixed reference coordinate**
(`lon=9.3157, lat=49.5944` — near Frankfurt, Germany, nowhere near Estonia), not the actual
gridded RATE composite. It was `0.0` in every test here (plausible — no rain at that exact
point in this window — not evidence of anything broken). **The actual payload of interest is
the `links` array with `"rel": "items"` entries** — each is a direct HTTPS URL to the real
ODIM HDF5 RATE composite file, hosted in the same `s3.waw3-1.cloudferro.com/openradar-24h`
bucket the confirmed-working current (S3-only) pipeline already downloads from. This API,
for this use case, functions as a **query/index layer over that same S3 bucket**, returning
one file-download link per matching 15-minute RATE slot — it is not a separate archive with
its own storage.

## Response cardinality

Tested against known-good recent ranges and cross-checked against the S3 listing directly:

- 1-hour range (`2026-08-21T07:30:00Z/2026-08-21T08:30:00Z`) → **4** timestamps/file-links
  (`07:30`, `07:45`, `08:00`, `08:15` — the range end `08:30` itself was not included,
  consistent with a `[start, end)`-style half-open interval, or simply that `08:30` wasn't
  yet published at query time).
- Cross-check against S3 directly:
  ```bash
  curl -sS "https://s3.waw3-1.cloudferro.com/openradar-24h/?list-type=2&prefix=2026/08/21/OPERA/COMP/OPERA@20260821T07"
  ```
  returned real `RATE.h5` keys at `0700`, `0715`, `0730`, `0745` — the API's `07:30`/`07:45`
  entries matched these S3 objects exactly (byte-for-byte matching timestamps; `08:00`/`08:15`
  would appear under the next hour's prefix, not separately re-checked but consistent with the
  established 15-minute cadence).
- 6-hour range (`2026-08-21T02:00:00Z/2026-08-21T08:00:00Z`) → **25** timestamps (6h × 4 +
  1, inclusive of both endpoints this time), **30** total links (25 file-download links plus
  5 metadata/service links).
- Full ~24-hour range (`2026-08-20T08:40:00Z/2026-08-21T08:40:00Z`) → **95** timestamps in a
  single response (one HTTP request), first timestamp `2026-08-20T08:45:00Z` (5 minutes
  after the requested start — truncated by the rolling-window boundary, see below), last
  timestamp `2026-08-21T08:15:00Z`. **100** total links.

**One request returns the full list of matching file links for the entire requested range**,
not one file per request. This is the single most important finding for Task 3's backfill
cost estimate.

## Pagination

**None observed.** No `next`/`cursor`/similar link appeared in any response body (checked the
full `links` array of the 24-hour, 95-timestamp response — only `canonical`/`service-desc`
metadata links plus one `items` link per file) or in response headers, across ranges up to
~24 hours (95-96 slots) returned whole in a single response with no truncation flag or
count-limit indicator.

## Historical (>24h) data availability — the core question

This is the actual crux of the task, beyond the brief's literal steps, and it resolves
**negatively**: the anonymous REST API does not appear to expose data beyond the same rolling
~24-hour window the S3 bucket (`openradar-24h`) already exposes.

Tests (all against the same working `/locations/0-20010-0-OPERA` request shape,
`Accept: application/prs.coverage+json`):

| Range (UTC) | Approx. age | Result |
|---|---|---|
| `2026-08-21T07:30:00Z/2026-08-21T08:30:00Z` | ~0-1h ago | `200`, 4 real file links |
| `2026-08-20T10:00:00Z/2026-08-20T11:00:00Z` | ~21-22h ago | `503` then (retry) `200`, 5 real file links — full coverage |
| `2026-08-20T08:00:00Z/2026-08-20T09:00:00Z` | ~23.5-24.5h ago (straddles the ~24h cutoff) | `200`, but only **2 of 5** expected slots returned (`08:45`, `09:00` present; `08:00`, `08:15`, `08:30` absent) — truncated exactly at the boundary |
| `2026-08-20T06:00:00Z/2026-08-20T07:00:00Z` | ~25-26h ago | `503` ×3 retries in a row (distinct from the `204` pattern below — see caveat) |
| `2026-08-20T05:00:00Z/2026-08-20T06:00:00Z` | ~27-28h ago | `204 No Content` |
| `2026-08-18T07:30:00Z/2026-08-18T08:30:00Z` | 3 days ago | `204 No Content`, reproduced twice (identical request, ~50s apart) |
| `2026-08-14T07:30:00Z/2026-08-14T08:30:00Z` | 7 days ago | `204 No Content` |

The truncation boundary in the third row lines up almost exactly with "now minus 24h": the
test ran at server time `2026-08-21T08:35:56Z` (from the response `Date` header), so "now −
24h" = `2026-08-20T08:35:56Z` — the returned slots (`08:45`, `09:00`) are both after that
instant, the missing slots (`08:00`, `08:15`, `08:30`) are all before it. This matches the
project's already-documented understanding of the S3 bucket as a rolling 24-hour window, and
strongly suggests the REST API's `/locations` endpoint is querying that same rolling window
rather than a separate historical archive — every link ever returned in this session pointed
into the `openradar-24h` S3 bucket, never a differently-named bucket or path.

**Caveat on the one `503`×3 row (25-26h ago):** this range consistently returned `503`
(not `204`) across three retries a few seconds apart, while the very similar but slightly
older 27-28h-ago range consistently returned a clean `204`. Given the broader pattern of
`503`s appearing unpredictably on both working and non-working queries throughout this
session (see "Working request" above), this specific `503` run is most plausibly the same
general backend flakiness rather than a distinct "right at the edge, ambiguous" state — but
this was not conclusively disambiguated (a `204` was never captured for that *exact* range,
only for one 1 hour earlier). This is the one open loose end from this investigation; it does
not change the overall conclusion, since both neighboring ranges (~24h and ~28h) behaved
consistently with a rolling ~24h cutoff.

No separate "archive" or "historical" collection exists in this API: `/collections` lists
exactly one collection (`observations`), and its `data_queries` only exposes `position`,
`area`, and `locations` query types — no distinct historical variant.

## Recommendation for Task 3

**The anonymous ORD REST API does not provide access to data older than ~24 hours.** Every
confirmed-working query for a range >~24h old returned `204 No Content`; every link ever
returned by a successful query pointed into the same `openradar-24h` rolling S3 bucket the
project's current pipeline already downloads from directly. There is no evidence, after
this live investigation, that anonymous REST access unlocks anything the S3 bucket doesn't
already provide — it appears to be a query/index convenience layer over that same 24-hour
window, not a separate historical archive.

Concretely, for Task 3:
- **Do not write a historical-backfill fetch function against this anonymous REST API** — it
  cannot reach the 14-day window this migration needs; a real, reproducible test against a
  3-day-old and a 7-day-old range both returned empty (`204`) results.
- **Before writing further backfill code**, register a MeteoGate API key
  (`https://api.meteogate.eu` — check the docs at
  `https://api.meteogate.eu/eu-eumetnet-weather-radar/docs` for a registration/auth path) and
  re-run the exact same `/locations/0-20010-0-OPERA?parameter-name=RATE:comp&level=0.0&format=ODIM&datetime=<historical range>`
  request authenticated, to determine whether authentication (rather than anonymity per se)
  is what gates historical access. This wasn't testable in this session (no key available),
  and the presence of `X-RateLimit-*` headers keyed specifically to "anonymous" access is at
  least suggestive that a different (possibly higher-tier, possibly historical-capable) access
  mode exists for authenticated requests. If that doesn't pan out, email
  `support.opera@eumetnet.eu` (the `creator_email`/`publisher_email` on every response in this
  session) and ask directly whether/how historical (>24h) OPERA RATE composites are obtainable.
- **If a historical path is found** (via API key or otherwise), the cardinality finding above
  means Task 3's fetch function should issue **one request per reasonably-sized time window
  (e.g. per day, or even per several days)**, not one request per 15-minute timestamp — a
  single request already proved capable of returning ~95-100 file links for a 24-hour range
  with no pagination and no observed cap. At that rate, a 14-day backfill would cost on the
  order of **14-30 requests** (one per day, or a few multi-day batches), trivially within the
  200/hour anonymous budget (or whatever an authenticated budget turns out to be) — the
  earlier "1,344 requests if one-request-per-file" worst case in the brief does not apply.
  The real blocker is not request cost, it's that no historical query has been shown to
  return data at all yet.
- **If no historical path can be found even with a key**, Task 3 should fall back to scoping
  "backfill" as "start capturing from S3/REST going forward with a sufficiently frequent
  scheduled job" rather than a true retroactive historical fetch, and this should be flagged
  back to whoever owns the overall migration plan as a real scope change, not silently
  absorbed into Task 3's implementation.
