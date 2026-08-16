# Ecotone Composition-Contrast Scoring — Design

Date: 2026-08-16
Status: Approved

## Purpose

This is MVP step 6 from `CLAUDE.md`: given `data/adjacency.geojson` (which stands border
each other) and `data/eraldis.geojson` (each stand's tree composition), score every
adjacent pair by how much their species composition differs, producing a scoutable
"microtype" polygon for each pair.

## Scope pivot from the original sketch

The original design sketch (and CLAUDE.md's "species-boundary transitions" framing)
suggested filtering to a binary "interesting/not interesting" set based on a dominant-species
mismatch or a fixed percentage-point threshold. Two real problems with that:

- **Dominant-species mismatch is too coarse.** `pine 85%/spruce 10%` next to
  `pine 55%/spruce 40%` is still "pine → pine" under a dominant-species rule, but is a real,
  meaningful compositional shift — mixed stands are often the most interesting ground, not
  noise to filter out.
- **A hard percentage-point threshold creates an artificial cliff.** `pine 51%/spruce 49%`
  next to `pine 49%/spruce 51%` would swing from "not interesting" to "interesting" for a
  2-point wobble that represents almost no real difference in habitat.

Instead: compute a **continuous** composition-contrast score for every adjacent pair — no
hard threshold, no discarding of mixed stands. Filtering/ranking happens later, in step 8
("export top N results"), once this contrast score is combined with other features in step 7
(`HabitatScore`). This step's job is scoring, not selecting.

## Normalization: why a raw share comparison doesn't work

`enrich.py`'s `pine_share`/`spruce_share`/`birch_share`/`aspen_share` columns sum `osakaal`
across **all** canopy layers (`rinne_kood`) for a species — a stand with two canopy layers
(e.g. a young regenerating layer and an old residual layer, each independently summing to
~100% within itself) can have `pine_share` well over 100 (confirmed on real data: a stand
with young+old pine layers has `pine_share = 172`). These raw share values are not a valid
probability distribution over the stand and can't be compared directly via a distance
metric.

The fix: compute fractions directly from the stand's full `composition` list (already stored
per stand from the prior enrich step), dividing each category's summed `osakaal` by the
stand's **total** `osakaal` across every composition entry (target species + everything
else), not assuming a fixed 100. Verified empirically against 2,000 real stands: this
normalization produces fractions summing to exactly 1.0 in every case, including known
multi-layer stands.

## Components

### `src/shroom_fm/ecotone.py` (new module)

- `composition_fractions(composition: list[dict]) -> dict[str, float]` — pure function.
  Categories: `pine`, `spruce`, `birch`, `aspen`, `other` (everything not one of the four
  target species). Sums `osakaal` per target species code (`MA`/`KU`/`KS`/`HB`), computes
  `other = total_osakaal - target_sum`, divides every category by `total_osakaal`. Empty
  composition (`total_osakaal == 0`) returns an all-zero dict rather than dividing by zero.
- `composition_contrast(fractions_a: dict, fractions_b: dict) -> float` — pure function.
  Total variation distance: `0.5 * Σ|fractions_a[k] - fractions_b[k]|` across all 5
  categories. Range `[0, 1]`: `0` = identical composition, `1` = completely disjoint. This is
  the core score — no threshold applied.
- `dominant_species(fractions: dict) -> tuple[str, float]` — pure function. Returns the
  `(category, share)` with the highest share, including `"other"` — never collapses a mixed
  stand to `None`. Kept as an interpretable label (`"pine ↔ spruce"`-style), not the
  selection criterion.
- `composition_diversity(fractions: dict) -> float` — pure function. Shannon entropy
  `-Σ p·ln(p)` over the 5 categories (skipping zero-probability categories). A continuous
  "mixedness" signal: near-0 for a near-monoculture stand, higher for an evenly-mixed one.
  Verified on real data: a 95%-spruce stand scores ~0.2, a 100%-pine stand scores ~0.0.
- `score_ecotones(adjacency_gdf: GeoDataFrame, eraldis_gdf: GeoDataFrame) -> GeoDataFrame` —
  orchestrator. For every row in `adjacency_gdf` (no filtering — every pair is scored), looks
  up both stands' `composition` from `eraldis_gdf` by `id_a`/`id_b`, computes
  `composition_fractions` for each side, then `composition_contrast`, `dominant_species`, and
  `composition_diversity` for each side. Buffers the adjacency `geometry` (already the shared
  boundary for `touching` pairs, or the gap-zone for `near_gap` pairs) by
  `BUFFER_DISTANCE_M = 40.0` (meters — the midpoint of the original ±30-50m
  scouting-microtype sketch; an engineering starting point like the adjacency thresholds,
  not a validated value) into the output polygon.

### `scripts/score_ecotones.py`

Runner: loads `data/adjacency.geojson` and `data/eraldis.geojson` → `score_ecotones()` →
saves `data/ecotones.geojson`. No network calls — pure local computation, like the adjacency
step.

## Output

`data/ecotones.geojson` — one row per adjacency pair (all pairs, not filtered), columns:
`id_a`, `id_b`, `adjacency_type`, `transition_length_m`, `composition_contrast`,
`dominant_species_a`, `dominant_share_a`, `diversity_a`, `dominant_species_b`,
`dominant_share_b`, `diversity_b`, `geometry` (buffered microtype polygon). Gitignored, same
reasoning as `data/adjacency.geojson` and `data/eraldis.geojson`.

## Error handling

No network calls in this step. No defensive handling beyond the explicit
`total_osakaal == 0` zero-division guard in `composition_fractions` (a real, verified edge
case — a stand with an empty `composition` list, e.g. no `eraldis_element` match).

## Testing

- `composition_fractions`, `composition_contrast`, `dominant_species`, and
  `composition_diversity` are pure and unit tested, covering: a normal single-species stand,
  a multi-layer stand whose raw shares exceed 100 (the case that motivated the normalization
  approach), the near-identical-fractions case (contrast ≈ 0, guards against the false-cliff
  problem), and the empty-composition edge case.
- `score_ecotones` is orchestration — not unit tested in isolation, verified by running
  `scripts/score_ecotones.py` against real local data (no network required), same pattern as
  the adjacency step.

## Out of scope

- `HabitatScore` itself (combining `composition_contrast` with other features) — MVP step 7.
- Ranking/filtering to a top-N set — MVP step 8.
- kasvukoht (site-type) or age-based ecotones (dry↔wet, old↔young) — explicitly deferred;
  this step is species-composition-contrast only, per `CLAUDE.md`'s stated MVP-6 scope.
- Any change to `src/shroom_fm/adjacency.py`, `enrich.py`, or their outputs.
