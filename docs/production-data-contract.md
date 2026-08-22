# shroom-fm Production Data Contract

This document describes the real output files of the shroom-fm pipeline, a system that
scores Estonian forest stands (from the state Metsaregister/Forest Registry) for
mushroom-foraging potential and exports a ranked daily shortlist of places worth
scouting. It is written for someone (or an LLM) with **no access to the source
repository** who needs to design a QGIS project/layout around these files. All field
lists, types, and example values below are taken directly from the real, current
pipeline code and a real production run — not aspirational.

## Domain glossary (minimum needed to read the fields below)

- **Eraldis** — a forest stand: a single polygon Metsaregister treats as internally
  homogeneous (species mix, age, site type). The core scoring unit for stand-level data.
- **Ecotone** — the boundary zone between two *adjacent* stands with contrasting
  composition (e.g. pine↔spruce, dry↔wet). These transition zones, not stand interiors,
  are the primary scouting target — they're where a short walk samples several habitat
  types at once.
- **Macrocluster** — a group of nearby stands/ecotones small enough to plausibly scout
  in one outing (a "region," roughly). Purely geographic, computed once, stable across
  days — it doesn't reshuffle when scores change.
- **Target species** (exactly 5, used as a suffix on many field names below):
  `kitsemampel` (gypsy mushroom), `chanterelle`, `aspen_bolete`, `birch_bolete`,
  `porcini`.
- **CRS**: all files are published in WGS84 (EPSG:4326, plain lon/lat degrees) — the
  pipeline reprojects internally to the Estonian National Grid (EPSG:3301, meters) for
  distance math, but every file on disk uses lon/lat.

## File overview

| File | Row count (real) | Geometry | Update cadence | Size on disk |
|---|---|---|---|---|
| `eraldis.geojson` | ~262,054 | Polygon/MultiPolygon (stand boundary) | Rare (re-download from Metsaregister) | ~787 MB |
| `ecotones.geojson` | ~493,499 | Polygon (buffered boundary strip between two stands) | Rare (recomputed only if `eraldis`/adjacency changes) | ~3.2 GB |
| `macroclusters.geojson` | 22 | MultiPolygon (dissolved region boundary) | Very rare (only if the stand/adjacency graph changes materially) | ~46 MB |
| `weather_eraldis.geojson` | ~262,054 | Same as `eraldis.geojson` | **Daily / before each trip** | ~1.1–1.2 GB |
| `scout_candidates.geojson` | ~4,000–4,400 (real run) | Polygon (copied from the winning ecotone) | **Daily / before each trip** (depends on `weather_eraldis.geojson` being fresh) | ~30–35 MB |
| `macrocluster_state.geojson` | 22 | MultiPolygon (same as `macroclusters.geojson`) | **Daily / before each trip** (derived from `scout_candidates.geojson`) | ~46 MB |

`eraldis`/`ecotones`/`macroclusters` are the **static base layer** (geography and
species composition only, no scores). `weather_eraldis` adds time-varying weather
features per stand. `scout_candidates` is the **final ranked shortlist** a forager
actually uses. `macrocluster_state` is a **daily per-region summary/dashboard layer** —
22 rows, one per region, no candidate-level detail, meant for an at-a-glance "which
regions look good today" view.

---

## 1. `eraldis.geojson` — forest stands (base layer)

One row per forest stand polygon. Static habitat/access scoring; no time-varying data.

**Key fields:**

| Field | Type | Meaning |
|---|---|---|
| `id` | int | Stable stand identifier — the join key used everywhere else (`id_a`/`id_b` in ecotones, etc.) |
| `pindala` | float | Area, hectares |
| `peapuuliik_kood` / `peapuuliik_kirjeldus` | string | Dominant tree species code / Estonian description (e.g. `"MA"` / `"mänd"` = pine) |
| `kasvukoht_kood` / `kasvukoht_kirjeldus` | string | Site/habitat type code / description (soil moisture & fertility class) |
| `pine_share`, `spruce_share`, `birch_share`, `aspen_share` | float | Host-tree composition shares (0–100-ish scale, can exceed 100 as raw stocking-share sum across canopy layers — not a strict percentage) |
| `composition_diversity` | float | Shannon-style diversity index of the stand's tree mix |
| `stand_habitat_score_{species}` | float (0–1) | Per-species habitat suitability score, one column per target species |
| `access_score` | float (0–1) | How reachable the stand is (road-proximity based) |
| `access_confidence` | string | `"HIGH_CONFIDENCE"` / `"NORMAL"` / `"CONDITIONAL"` |
| `access_reason` | string | Human-readable reason, e.g. `"191m from Muu tee-class road"` |
| `nearest_car_road_m`, `nearest_high_confidence_road_m`, `nearest_walk_path_m` | float (meters) | Distances to nearest road/path of each class |
| `forest_block_id` | int | Which contiguous forest massif this stand belongs to |
| `macrocluster_id` | int (0–21) | Which of the 22 regions this stand belongs to — join key into `macroclusters.geojson`/`macrocluster_state.geojson` |
| `composition` | array of objects | Detailed per-canopy-layer tree composition (species, age, share); nested, not typically needed for map styling |

**Real example (trimmed):**
```json
{
  "type": "Feature",
  "properties": {
    "id": 10683949,
    "pindala": 0.24,
    "peapuuliik_kood": "MA",
    "peapuuliik_kirjeldus": "mänd",
    "kasvukoht_kood": "SL",
    "kasvukoht_kirjeldus": "sinilille",
    "pine_share": 79.0,
    "spruce_share": 106.0,
    "birch_share": 15.0,
    "aspen_share": 0.0,
    "composition_diversity": 0.8977,
    "stand_habitat_score_kitsemampel": 0.6,
    "stand_habitat_score_chanterelle": 0.69125,
    "stand_habitat_score_aspen_bolete": 0.13875,
    "stand_habitat_score_birch_bolete": 0.328125,
    "stand_habitat_score_porcini": 0.825,
    "access_score": 0.8728,
    "access_confidence": "NORMAL",
    "access_reason": "191m from Muu tee-class road",
    "nearest_car_road_m": 190.77,
    "forest_block_id": 72,
    "macrocluster_id": 9
  },
  "geometry": { "type": "MultiPolygon", "coordinates": [ ["...~14 lon/lat vertex pairs..."] ] }
}
```

---

## 2. `ecotones.geojson` — stand-boundary transition zones

One row per adjacent stand-pair boundary. This is the layer scoring/ranking actually
happens on — `scout_candidates.geojson` rows are a filtered/ranked subset of this file's
geometries.

**Key fields:**

| Field | Type | Meaning |
|---|---|---|
| `id_a`, `id_b` | int | The two adjacent stands' `eraldis.geojson` ids |
| `adjacency_type` | string | `"touching"` or `"near_gap"` |
| `transition_length_m` | float | Length of the shared boundary, meters |
| `composition_contrast`, `age_contrast` | float | How different the two stands' species mix / age class are |
| `dominant_species_a`, `dominant_species_b` | string | Dominant species on each side (English: `"pine"`, `"spruce"`, etc.) |
| `dominant_share_a`, `dominant_share_b`, `diversity_a`, `diversity_b` | float | Per-side composition stats |
| `kasvukoht_site_type_changed` | bool | Whether the site type differs across the boundary |
| `kasvukoht_group_changed` | **string** `"True"`/`"False"`/`null` | ⚠️ Known quirk: this is a string, not a real boolean, due to a GeoJSON round-trip issue — filter on the literal strings, not boolean comparison |
| `drainage_changed` | bool | Real boolean (no quirk) — whether drainage status differs |
| `exploration_bonus`, `exploration_signal`, `exploration_coverage` | float | Contributions to the "worth a detour to sample multiple habitats" bonus |
| `ecotone_score_{species}` | float or `null` | Per-species boundary-contrast score — **the main ranking input** |
| `fruiting_modifier_{species}` | float (0–1) | Weather-driven fruiting-likelihood multiplier, averaged from both adjacent stands |
| `weather_data_quality` | string | e.g. `"complete"`, or a `;`-joined list of degradation reasons |
| `weather_data_coverage` | float, `0.0`–`1.0` | Fraction of expected weather observations actually present for this location |
| `weather_as_of` | ISO 8601 timestamp | When the weather snapshot feeding this row was taken |

**Real example (trimmed, geometry shortened):**
```json
{
  "type": "Feature",
  "properties": {
    "id_a": 100656, "id_b": 100660,
    "adjacency_type": "touching",
    "transition_length_m": 145.02,
    "composition_contrast": 0.525,
    "dominant_species_a": "pine", "dominant_share_a": 0.7, "diversity_a": 0.6109,
    "dominant_species_b": "spruce", "dominant_share_b": 0.525, "diversity_b": 0.9881,
    "kasvukoht_site_type_changed": true,
    "kasvukoht_group_changed": "True",
    "age_contrast": 0.0,
    "drainage_changed": false,
    "exploration_bonus": 0.1519,
    "ecotone_score_kitsemampel": null,
    "ecotone_score_chanterelle": null,
    "fruiting_modifier_kitsemampel": 0.0,
    "weather_data_quality": "complete",
    "weather_data_coverage": 1.0,
    "weather_as_of": "2026-08-20T12:34:16.021Z"
  },
  "geometry": { "type": "Polygon", "coordinates": [ ["...dozens of lon/lat vertex pairs, a thin buffered strip along the shared boundary..."] ] }
}
```
Note: a `null` `ecotone_score_{species}` is common and expected — most boundaries aren't
strong candidates for most species; this is not missing data.

---

## 3. `macroclusters.geojson` — the 22 static regions

One row per region. Purely geographic — no scores, doesn't change day to day.

| Field | Type | Meaning |
|---|---|---|
| `macrocluster_id` | int (0–21) | Primary key |
| `forest_block_count` | int | How many contiguous forest massifs make up this region |
| `eraldis_count` | int | Total stand count in this region |
| `centroid_extent_m`, `geometry_extent_m` | float | Region diameter estimates, meters |
| `oversized_macrocluster` | bool | Diagnostic flag (real run: `false` for all 22) |
| `within_target_extent`, `within_target_block_count` | bool | Diagnostic-only flags against soft size targets |

**Real example (trimmed):**
```json
{
  "type": "Feature",
  "properties": {
    "macrocluster_id": 21,
    "forest_block_count": 133,
    "eraldis_count": 1242,
    "centroid_extent_m": 13970.17,
    "geometry_extent_m": 14268.03,
    "oversized_macrocluster": false,
    "within_target_extent": true,
    "within_target_block_count": false
  },
  "geometry": { "type": "MultiPolygon", "coordinates": ["...many rings, one real region's dissolved outline, can be a large/complex multi-part shape..."] }
}
```

---

## 4. `weather_eraldis.geojson` — per-stand weather features (time-varying)

Same row count/geometry as `eraldis.geojson` (one row per stand) but adds rolling
rainfall/temperature/humidity features derived from radar + weather-model data. Fully
regenerated on each refresh (not incremental). Key fields (prefix pattern, not
exhaustive): `rain_3d_mm`/`rain_7d_mm`/`rain_14d_mm`, `hours_since_any_rain`,
`hours_since_significant_rain`, `temp_mean_3d`, `rh_mean_3d`, `weather_data_coverage`
(0.0–1.0, per-stand — a stand can be individually degraded even if the national picture
looks fine), `weather_data_quality`, `as_of`. This file mostly matters as a **pipeline
input**, not a map layer — you likely don't need to style it directly, since its
information reaches the map via `ecotone_score_*`/`fruiting_modifier_*` on
`ecotones.geojson` and `scout_candidates.geojson`.

---

## 5. `scout_candidates.geojson` — the daily ranked shortlist (main output layer)

**This is what a forager actually looks at.** One row per selected candidate ecotone.
Selection changed recently: candidates are now ranked **per (species, region)**, not
globally across Estonia — every one of the 22 regions gets its own top-10 shortlist per
species, rather than a single national top-10 that used to collapse onto one region near
"home."

**`tier`** is the most important field for map styling — three possible values:

- **`"ranked"`** — the actual shortlist. Exactly 10 rows per (species, macrocluster_id)
  combination that has enough eligible/well-covered candidates (fewer if a region has
  fewer than 10 real candidates). **This is the primary layer to show by default.**
- **`"suppressed_by_nearby"`** — real candidates that scored well but were excluded
  because a higher-scoring candidate within 400m already represents the same forest
  patch. Not noise — genuinely valid nearby alternatives, useful as a fallback/backup
  layer (e.g. shown faded, or on click-through from a `"ranked"` point). Capped at 3
  exported examples per `"ranked"` target (real total suppression count may be higher —
  see `nearby_suppressed_count`).
- **`"remote_high_value"`** — ecologically strong candidates whose road access
  couldn't be confirmed by the v1 distance-based heuristic. Global per species (NOT
  split per region) — think of this as "best unconfirmed-access spots in all of
  Estonia," worth a manual road-access check. Capped at 10 per species.

**Full field list:**

| Field | Type | Meaning |
|---|---|---|
| `species` | string | One of the 5 target species |
| `tier` | string | `"ranked"` / `"suppressed_by_nearby"` / `"remote_high_value"` — see above |
| `macrocluster_id` | int | Region this candidate belongs to (present on all tiers, including `remote_high_value` — informational there, not a selection criterion) |
| `rank_macrocluster` | int or `null` | 1–10 rank **within its (species, macrocluster) bucket** — only set on `"ranked"` rows |
| `rank` | int or `null` | 1–10 rank **within its species, globally** — only set on `"remote_high_value"` rows (different scope from `rank_macrocluster`, don't conflate) |
| `scout_score` | float | Final ranking score = `ecotone_score × access_modifier × fruiting_modifier`. Real magnitudes are small (e.g. `0.0001`–`0.001`) — rank/compare, don't expect scores near 1.0 |
| `ecotone_score` | float | The species' boundary-contrast score for this ecotone |
| `access_modifier` | float (0–1) | Best of the two adjacent stands' `access_score` |
| `access_confidence`, `access_reason`, `nearest_car_road_m` | — | Same meaning as on `eraldis.geojson`, for the better-served adjacent stand |
| `fruiting_score` | float (0–1) | Weather-driven fruiting-likelihood modifier at candidate-selection time |
| `weather_data_quality`, `weather_data_coverage`, `weather_as_of` | — | Same meaning as on `ecotones.geojson` |
| `exclusion_reason` | string or `null` | Only set on `remote_high_value` rows: `"REMOTE_BY_V1_ACCESS_PROXY"` or `"MISSING_FRUITING_DATA"` |
| `suppressed_by_id` | string or `null` | Only set on `suppressed_by_nearby` rows: the `"{id_a}_{id_b}"` of the `ranked` candidate that suppressed this one — join key back to the winning row |
| `suppression_distance_m` | float or `null` | Real distance (meters) from this suppressed candidate to its suppressor |
| `pre_suppression_rank` | int or `null` | This candidate's score rank before suppression removed anything above it |
| `nearby_suppressed_count` | int | Only meaningful on `ranked` rows: **true total** count of candidates this one suppressed (may exceed the 3 actually exported as `suppressed_by_nearby` rows) |
| `nearby_best_suppressed_score` | float or `null` | Score of the closest-scoring candidate this one suppressed |
| `transition_length_m`, `dominant_species_a`, `dominant_species_b` | — | Same meaning as on `ecotones.geojson` |
| `id_a`, `id_b` | int | The underlying ecotone's stand pair — join key back to `ecotones.geojson`/`eraldis.geojson` |

**Real example — a `"ranked"` row (post-redesign shape; geometry shortened):**
```json
{
  "type": "Feature",
  "properties": {
    "species": "kitsemampel",
    "tier": "ranked",
    "macrocluster_id": 9,
    "rank_macrocluster": 1,
    "rank": null,
    "scout_score": 0.0001223,
    "ecotone_score": 1.0308,
    "access_modifier": 1.0,
    "access_confidence": "CONDITIONAL",
    "access_reason": "0m from Muu tee-class road",
    "nearest_car_road_m": 0.0,
    "fruiting_score": 0.0001187,
    "weather_data_quality": "complete",
    "weather_data_coverage": 1.0,
    "weather_as_of": "2026-08-21T15:35:00.000Z",
    "exclusion_reason": null,
    "suppressed_by_id": null,
    "suppression_distance_m": null,
    "pre_suppression_rank": null,
    "nearby_suppressed_count": 39,
    "nearby_best_suppressed_score": 0.0001156,
    "transition_length_m": 138.73,
    "dominant_species_a": "pine",
    "dominant_species_b": "pine",
    "id_a": 691624, "id_b": 691638
  },
  "geometry": { "type": "Polygon", "coordinates": ["...same boundary-strip shape as the parent ecotones.geojson row..."] }
}
```

**Real production row-count breakdown** (one real run, all 22 regions × 5 species):
`4,384` total rows = `1,100` `ranked` (22 × 5 × 10, every region/species combo filled)
+ `3,234` `suppressed_by_nearby` + `50` `remote_high_value` (5 × 10, global cap).

---

## 6. `macrocluster_state.geojson` — daily per-region dashboard

One row per region (22 total) — a same-day snapshot, **not** individual candidates.
Good for a choropleth/summary view ("which regions look good today") that a user drills
into via `scout_candidates.geojson` filtered to that `macrocluster_id`.

| Field | Type | Meaning |
|---|---|---|
| `macrocluster_id` | int | Join key into `macroclusters.geojson` |
| `as_of` | ISO 8601 timestamp | When this snapshot was generated |
| `cross_macrocluster_ecotone_count` | int | Diagnostic: ecotones whose two stands fall in different regions (real runs: `0` everywhere) |
| `today_ranked_count_{species}` | int | How many `ranked` candidates this region has for this species today (0–10) |
| `today_top_score_{species}` | float or `null` | Best `scout_score` in this region/species today |
| `today_top3_mean_score_{species}` | float or `null` | Mean of the top 3 |
| `today_top_target_id_{species}` | string or `null` | `"{id_a}_{id_b}"` of the #1 candidate — join key into `scout_candidates.geojson` |
| `today_weather_coverage_{species}` | float or `null` | Region-wide weather-data coverage for this species' eligible pool; `null` (not `0`) if the pool is empty |
| `today_weather_status_{species}` | string or `null` | `"ok"` / `"insufficient_coverage"` / `null` (empty pool) — **the field to check before trusting a region/species' ranking is real and not just quietly empty** |

**Real example (trimmed to one species repeated pattern, geometry shortened):**
```json
{
  "type": "Feature",
  "properties": {
    "macrocluster_id": 16,
    "as_of": "2026-08-20T13:50:39.086Z",
    "cross_macrocluster_ecotone_count": 0,
    "today_ranked_count_kitsemampel": 10,
    "today_top_score_kitsemampel": 0.0001223,
    "today_top3_mean_score_kitsemampel": 0.0001185,
    "today_top_target_id_kitsemampel": "691624_691638",
    "today_weather_coverage_kitsemampel": 1.0,
    "today_weather_status_kitsemampel": "ok"
  },
  "geometry": { "type": "MultiPolygon", "coordinates": ["...same region outline as macroclusters.geojson..."] }
}
```

---

## Notes for a QGIS layout proposal

- **Base layers** (rarely change, large files — style thin/simple, avoid per-feature
  labels): `eraldis.geojson` (fill by `peapuuliik_kood`?), `macroclusters.geojson`
  (boundary-only outline layer, 22 features, trivial to style).
- **`ecotones.geojson`** is huge (493k rows) — not meant to be shown in full; if shown
  at all, filter to non-null `ecotone_score_{species}` for one species at a time.
- **Daily working layers**: `scout_candidates.geojson` filtered by `tier == "ranked"` as
  the default/prominent points (categorize by `species`, size/color by `scout_score` or
  `rank_macrocluster`); `tier == "suppressed_by_nearby"` as a secondary, muted/hidden
  layer joinable to its parent via `suppressed_by_id`; `tier == "remote_high_value"` as
  a distinctly-styled "worth a road check" layer.
- **`macrocluster_state.geojson`** as a choropleth (color by `today_ranked_count_*` or
  `today_weather_status_*` per species) sitting *underneath* `scout_candidates`, so a
  user sees region-level "is today good here" before drilling into individual points.
- All geometries are WGS84 (EPSG:4326) — no reprojection needed for standard QGIS/web-map display.
