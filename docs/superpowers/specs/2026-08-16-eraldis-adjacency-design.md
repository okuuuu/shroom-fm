# Neighbouring-Stand Adjacency — Design

Date: 2026-08-16
Status: Approved

## Purpose

This is MVP step 5 from `CLAUDE.md`: for each `eraldis` stand, find which other stands it
is meaningfully adjacent to. This is the direct prerequisite for step 6 (ecotone detection)
— you can't find `pine ↔ spruce` transition zones without first knowing which stands border
each other.

Scope note: this design covers adjacency only, not macrocluster generation (DBSCAN/HDBSCAN
grouping of stands into travel-region clusters). That's a different technique for a
different purpose (trip planning, not ecotone detection) and is explicitly deferred to its
own future spec.

## Adjacency model

Two stands are candidate neighbours under two distinct relationship types, not a single
binary "touching" test:

- **`touching`** — polygon boundaries actually share a segment. Point/corner-only contacts
  (two polygons meeting at a single vertex) are explicitly **not** useful adjacency for this
  project's purpose (finding scoutable transition zones) and are discarded via a minimum
  contact-length threshold.
- **`near_gap`** — polygons don't touch, but are close (e.g. separated by an unmapped forest
  road, ditch, or a survey gap) *and* run near-parallel for a meaningful stretch — not just
  two isolated close points. A bare "closest-point distance ≤ N m" test is insufficient on
  its own (two polygons can have close corners with no real shared transition); a length
  requirement on the near-parallel run is also needed.

Three named constants govern both rules, explicitly documented as **engineering starting
points, not biological constants** — easy to retune once real field results validate or
invalidate them:

```python
MAX_GAP_M = 10.0             # near_gap: max distance between boundaries to consider
MIN_CONTACT_LENGTH_M = 20.0  # touching: min shared-boundary length to keep (discards corners)
MIN_PROXIMITY_LENGTH_M = 20.0  # near_gap: min estimated parallel-run length to keep
```

All geometry math happens in a projected metric CRS (`EPSG:3301`, the Estonian national
grid — same CRS already used for radius filtering in `src/shroom_fm/eraldis.py`), since
lengths and distances in meters are meaningless in WGS84 degrees.

### Near-gap proximity-length estimation

There's no single built-in shapely function for "how far do these two boundaries run
near-parallel." The chosen approach is a buffer-intersection area heuristic:

```
zone = geom_a.buffer(MAX_GAP_M).intersection(geom_b.buffer(MAX_GAP_M))
proximity_length_m ≈ zone.area / MAX_GAP_M
```

This is an **approximation** (a normalized-width estimate of the overlap zone's extent, not
an exact geometric measurement of a parallel run) — flagged explicitly as such. It's cheap,
implementable with existing dependencies, and good enough to distinguish "two stands run
alongside each other for tens of meters" from "two stands happen to have nearby corners."
Refining this later (e.g. with an actual nearest-point-pair-based line-following algorithm)
is a reasonable future improvement if this proves too rough in practice, but is out of scope
for this MVP step.

## Components

### `src/shroom_fm/adjacency.py` (new module)

- `classify_pair(geom_a, geom_b) -> dict | None` — pure function, the core classification
  logic described above. Returns `{"adjacency_type": "touching" | "near_gap",
  "transition_length_m": float, "gap_m": float, "geometry": <shapely geometry>}` for a kept
  pair (geometry is the shared-boundary LineString for `touching`, or the buffer-intersection
  `zone` Polygon for `near_gap`), or `None` for a discarded pair (corner-only touch, too far
  apart, or gap-adjacent but too short a run).
- `find_candidate_pairs(gdf: GeoDataFrame) -> list[tuple[int, int]]` — cheap candidate
  generation via a buffered self-`sjoin` (buffer each stand by `MAX_GAP_M`, spatial-join
  against the unbuffered set), so the pairwise classification step below only runs on
  plausible nearby pairs instead of every pair in the dataset — critical at the scale of
  tens of thousands of stands. Deduplicates to canonical `(min_id, max_id)` ordering and
  excludes self-pairs.
- `compute_adjacency(gdf: GeoDataFrame) -> GeoDataFrame` — orchestrator. Reprojects `gdf` to
  `EPSG:3301`, calls `find_candidate_pairs`, runs `classify_pair` on each candidate pair,
  keeps the non-`None` results, and returns a GeoDataFrame with columns `id_a`, `id_b`,
  `adjacency_type`, `transition_length_m`, `gap_m`, `geometry` — reprojected back to WGS84
  (`EPSG:4326`) for the output file, consistent with `eraldis.geojson`.

### `scripts/compute_adjacency.py`

Runner: loads `data/eraldis.geojson` → `compute_adjacency()` → saves to
`data/adjacency.geojson`. No network calls — this is pure local geometry computation on data
already downloaded/enriched by the prior two branches. Verification is running it against
real local data and checking the output is sane (e.g. spot-checking a known stand's
neighbours), not a live-endpoint check like earlier steps.

## Output

`data/adjacency.geojson` — one feature per kept adjacent pair, with `id_a`/`id_b` referencing
the `id` column already present in `data/eraldis.geojson`. Deliberately lean: no duplicated
species/kasvukoht attributes from the two stands — consumers (step 6, ecotone detection) join
back to `eraldis.geojson` by id when they need that. This file is not personally-identifying
in the way `eraldis.geojson`/`config.toml` are (it's derived geometry relationships, not
directly a home-location-correlated dataset on its own) — no special gitignore treatment
beyond what already applies to files under `data/`; follow the existing pattern (this file is
derived from the already-gitignored `eraldis.geojson`, so it should be gitignored too, for
the same reason — geographically correlated with home once joined back).

## Error handling

No network calls in this step, so the "no retries" posture from earlier steps doesn't apply
in the same way — there's nothing to retry. Degenerate/invalid input geometry would raise
naturally from shapely; no defensive handling added, consistent with the project's posture of
not guarding against inputs that shouldn't occur.

## Testing

- `classify_pair` is pure and fully unit tested with small synthetic shapely geometries,
  covering all four outcomes: a long touching border (kept as `touching`), a corner-only
  touch (discarded), a near-gap with a long parallel run (kept as `near_gap`), and a
  near-gap that's too short/isolated (discarded).
- `find_candidate_pairs` and `compute_adjacency` are geopandas-heavy orchestration — not
  unit tested in isolation, verified by running `scripts/compute_adjacency.py` against real
  local data and sanity-checking the output.

## Out of scope

- Macrocluster generation (DBSCAN/HDBSCAN travel-region clustering) — separate future spec.
- Ecotone detection itself (buffering the adjacency geometry into scoutable microtypes,
  filtering by species difference) — MVP step 6, next after this.
- `separator_type` classification (road/ditch/stream/unknown for `near_gap` pairs) — would
  require joining road/hydrology geometry data not currently part of this pipeline; noted as
  a good future enhancement once that data source is added, not part of this step.
- Any change to `src/shroom_fm/eraldis.py`, `enrich.py`, or their outputs.
