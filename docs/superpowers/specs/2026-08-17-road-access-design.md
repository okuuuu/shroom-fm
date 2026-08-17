# Road Access — `AccessScore` — Design

Date: 2026-08-17
Status: Approved

## Purpose

First sub-project of the larger Access/Eligibility layer discussed alongside `ScoutScore`
(`CLAUDE.md` MVP step 8+): given `data/eraldis.geojson`, compute a per-stand `AccessScore`
answering "how easily can I actually get there by car/on foot," sourced from ETAK road and
barrier data. This is deliberately separate from `StandHabitatScore`/`EcotoneScore` — a
biologically excellent stand 800m past a good gravel road is not a *worse forest*, it's just
a *less convenient trip*. `AccessScore` will later multiply into a `ScoutScore` alongside
`EcotoneScore`, weather, and observation history, but never modifies ecological suitability
itself.

**Ecological scores never contain logistical/legal accessibility. Accessibility modifies
only candidate/scouting priority, never habitat suitability.**

## Scope

This spec covers **only** road/barrier ingestion and distance-based `AccessScore`. It is the
first of several planned sub-projects in the broader Access/Eligibility area (in likely
future order: land-cover eligibility filters, EELIS legal restrictions, Metsaregister
clearcut evidence, CHM-based dense-growth detection, landscape mosaic scoring) — those are
explicitly out of scope here and will each get their own spec.

## Data source: ETAK WFS

- Endpoint: `https://gsavalik.envir.ee/geoserver/etak/wfs` — confirmed live via
  `scripts/get_etak_capabilities.py` (2026-08-17, 39 layers, recorded in
  `data/etak_capabilities.json`). Re-run and diff that script's output if the service
  changes, matching the existing `metsaregister` precedent in `CLAUDE.md`.
- Layers used by this sub-project:
  - `etak:e_501_tee_j` — road/street centerlines. Confirmed real attributes (live
    `GetFeature` sample, 2026-08-17): `tyyp`/`tyyp_tekst` (road type), `laius` (width),
    `teekate`/`teekate_tekst` (surface), `liiklus`/`liiklus_tekst` (traffic direction),
    `tahtsus`/`tahtsus_tekst` (importance — mostly unfilled/`"Täitmata"` in a live 3000-row
    sample, not usable as a reliable signal), `vajalik`/`vajalik_tekst` (condition,
    e.g. `"Korras"`).
  - `etak:e_505_liikluskorralduslik_rajatis_j` — traffic-control structures. Confirmed real
    attributes: `tyyp`/`tyyp_tekst` (`Purre`/`Sõidutakistus`/`Tunnel` in live sample),
    `toke`/`toke_tekst` (barrier status, populated only for `Sõidutakistus` features;
    confirmed real values: `"Avatav"`, `"Püsivalt suletud"`, `"Täitmata"`).
- Not used by this sub-project (reserved for later sub-projects): `e_501_tee_a` (road
  areas/parking), `e_405_piire_j` (fences), `e_303_haritav_maa_a`/`e_304_lage_a`/
  `e_305_puittaimestik_a`/`e_306_margala_a`/`e_307_turbavali_a`/`e_302_ou_a` (land cover).

Real `tyyp_tekst` values confirmed live (3000-row sample near home): `Muu tee` (1770),
`Tänav` (538), `Rada` (518), `Kõrvalmaantee` (124), `Tugimaantee` (22), `Ramp või
ühendustee` (12), `Kergliiklustee` (9), `Põhimaantee` (7). Real `teekate_tekst` values:
`Pinnas`, `Kruuskate`, `Püsikate`, `Kivikate`.

## `car_class` classification

New module `src/shroom_fm/roads.py`. `classify_car_class(tyyp_tekst, teekate_tekst) ->
str`, mapping:

```
Põhimaantee / Tugimaantee / Kõrvalmaantee / Ramp või ühendustee / Tänav  -> HIGH_CONFIDENCE
Muu tee + (Püsikate | Kruuskate | Kivikate)                              -> NORMAL
Muu tee + Pinnas                                                          -> CONDITIONAL
Rada / Kergliiklustee                                                     -> WALK_ONLY
```

`Tänav` classifies as `HIGH_CONFIDENCE` alongside the maantee tiers (paved/maintained
settlement street, not a lesser tier of `Muu tee`) and `Ramp või ühendustee` likewise
(only exists attached to a maantee-tier road). Any `tyyp_tekst` not covered above (none seen
in the live sample, but the classifier must not silently misclassify an unseen value) raises
`ValueError` naming the unrecognized value — fail loud rather than guess at a car-worthiness
tier for a road type never verified against real data.

## Barrier handling (v1 approximation — no routing graph)

**No real road-network graph or pathfinding in this sub-project** — reachability is
straight-line nearest-distance, not routing. This trades accuracy for avoiding a new heavy
dependency (e.g. `networkx`) on a first pass, matching this project's established
heuristics-first pattern (same tradeoff `HabitatScore` made).

Because there's no graph, a barrier can't "cut" a path — instead, a permanently-closed
barrier (`toke_tekst == "Püsivalt suletud"`) downgrades any road segment within a small snap
distance (`BARRIER_SNAP_M = 5.0`) of it: that segment is excluded entirely from car-class
distance searches (treated as if it weren't there for `nearest_car_road_m`/
`nearest_high_confidence_road_m` purposes). `Avatav` (openable) and `Täitmata` (unknown)
barriers do not affect classification — assumed passable. This is a known, documented
simplification: it cannot tell which side of the barrier a stand is on, so it conservatively
removes the blocked segment from the search rather than truly cutting a graph. Real
graph-based reachability (`reachable_by_car_graph`, `distance_after_last_car_point_m`) is
explicitly deferred to a later sub-project.

## `AccessScore`

New module `src/shroom_fm/access.py`, per eraldis:

```python
ACCESS_DISTANCE_CAP_M = 1500.0

def nearest_road_distance_m(eraldis_geom, roads_gdf) -> float | None:
    ...  # min distance in ESTONIAN_GRID_CRS, or None if roads_gdf is empty

def access_score(nearest_car_road_m: float | None) -> float:
    if nearest_car_road_m is None:
        return 0.0
    return max(0.0, 1.0 - nearest_car_road_m / ACCESS_DISTANCE_CAP_M)
```

Computed per eraldis:

```
nearest_car_road_m             = nearest_road_distance_m against segments with
                                  car_class in {HIGH_CONFIDENCE, NORMAL, CONDITIONAL}
                                  (barrier-excluded segments already removed)
nearest_high_confidence_road_m = nearest_road_distance_m against HIGH_CONFIDENCE only
nearest_walk_path_m            = nearest_road_distance_m against WALK_ONLY only

access_score      = access_score(nearest_car_road_m)   # 0.0 if no car road within 1500m
access_confidence = car_class of whichever segment produced nearest_car_road_m
                     (None if nearest_car_road_m is None)
access_reason      = human-readable string, e.g.:
                        "320m from Kõrvalmaantee-class road"
                        "no car-accessible road within 1500m"
```

`access_confidence`/`access_reason` exist so a `0.4` is legible later — e.g. "1000m from a
NORMAL road" vs. "no HIGH_CONFIDENCE road within range" are both plausible causes of the same
numeric score, and the reason string disambiguates without re-deriving it.

**Missing-data discipline** (matching this project's established `None`/`NaN` pattern): if
`data/roads.geojson` has zero features after classification (e.g. an empty bbox result),
`nearest_car_road_m` etc. must be `None`, and `access_score` must be `0.0` via the explicit
`None` branch above — never a fabricated small number from an empty-input computation.

## Pipeline / files

```
ETAK WFS (etak:e_501_tee_j, etak:e_505_liikluskorralduslik_rajatis_j)
      │
      ▼
scripts/download_roads.py
      → data/roads.geojson      (all e_501_tee_j segments in bbox, with car_class column,
                                  barrier-excluded segments flagged/dropped)
      → data/barriers.geojson   (e_505 barrier points in bbox, with toke_tekst)
      │
      ▼
src/shroom_fm/roads.py          (classify_car_class, barrier-snap exclusion)
src/shroom_fm/access.py         (nearest_road_distance_m, access_score, per-eraldis assembly)
      │
      ▼
scripts/score_access.py         → writes access_score / access_confidence / access_reason /
                                   nearest_car_road_m / nearest_high_confidence_road_m /
                                   nearest_walk_path_m columns onto data/eraldis.geojson
```

`download_roads.py` follows the same bbox/radius pattern as `download_eraldis.py`
(`compute_bbox`/`filter_within_radius` from `eraldis.py` are geometry-agnostic and reused
as-is — no new bbox logic needed). `ETAK_WFS_URL` (added to `src/shroom_fm/wfs.py`) and
`fetch_capabilities` (already generic over `url`) are reused as-is. `roads.py` gets its own
`fetch_layer_bbox(wfs, typename, bbox, page_size=1000) -> gpd.GeoDataFrame` — a paged
`GetFeature` fetch generalized over `typename`, mirroring `fetch_eraldis_bbox`'s paging loop
in `eraldis.py` but not calling into it, since `fetch_eraldis_bbox` is hardcoded to
`ERALDIS_TYPENAME`. This keeps the change self-contained to the new module rather than
refactoring `eraldis.py`, at the cost of duplicating the paging loop once; called twice (once
per layer) to fetch both `e_501_tee_j` and `e_505_liikluskorralduslik_rajatis_j`.

`access_*` columns are appended directly onto `data/eraldis.geojson` (same pattern as
`stand_habitat_score_*`), not written to a separate file — access is a per-stand property
with no new join key required.

## Testing

Unit tests for:
- `classify_car_class` — every real `(tyyp_tekst, teekate_tekst)` combination in the mapping
  above, plus the `ValueError` case for an unrecognized `tyyp_tekst`.
- Barrier-snap exclusion — a segment within `BARRIER_SNAP_M` of a `Püsivalt suletud` barrier
  is excluded; a segment near an `Avatav` or `Täitmata` barrier is not.
- `nearest_road_distance_m` — including the empty-`roads_gdf` → `None` case.
- `access_score` — the `None` → `0.0` case, the `0m` → `1.0` case, the `>=1500m` → `0.0`
  case, and a mid-range value.
- `access_confidence`/`access_reason` string assembly.

`download_roads.py`/`scripts/get_etak_capabilities.py` are thin wiring, untested in
isolation — same precedent as `download_eraldis.py`/`get_capabilities.py`.

## Out of scope

- Real road-network graph/routing, `reachable_by_car_graph`, `distance_after_last_car_point_m`.
- `road_density_500m`.
- Parking-lot proximity (`e_501_tee_a` `parkla`).
- Teeregister enrichment (public-road confirmation, administrative class).
- Land-cover eligibility filters, EELIS legal restrictions, Metsaregister clearcut evidence,
  CHM-based dense-growth detection — each a separate future sub-project.
- Any change to `StandHabitatScore`/`EcotoneScore` — `access_score` is additive-only, never
  read by either.
- `ScoutScore` itself (combining `EcotoneScore` × `access_score` × weather + bonuses) — not
  built until enough of the Access/Eligibility layer and `FruitingScore` exist.
