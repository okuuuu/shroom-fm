# Scout Candidates: Per-Macrocluster Selection + Spatial Suppression — Design

Date: 2026-08-21
Status: Approved

## Problem

`scripts/export_scout_candidates.py` currently ranks each species' scout candidates
globally across all of Estonia: `scout_candidates_for_species` sorts the full
`joined_gdf` (ecotones × access × fruiting) by `scout_score` and takes the top 10.
Verified against real production data (`data/macrocluster_state.geojson`, current run):
all 10 `ranked` candidates for every one of the 5 target species land in a single
macrocluster (16, nearest home) — `today_ranked_count_{species} == 0` for all 21 other
macroclusters. `ScoutScore`'s access-modifier weighting makes proximity to home dominate
the global ranking, so a global top-10 cut structurally starves every other macrocluster
of candidates regardless of how ecologically strong they are.

The correct unit of selection is `species × macrocluster`, not `species` alone — a
scouting trip targets one macrocluster at a time (see `CLAUDE.md`'s "Macroclustering"
section: a macrocluster is "a group of nearby `forest_block`s small enough to plausibly
scout in one outing"), so every macrocluster the user might actually visit needs its own
locally-ranked shortlist, not a share of one nationally-ranked list.

A second, related problem: taking a naive top-10-by-score-per-macrocluster cut risks
returning near-duplicate candidates — several ecotones 50-150m apart that are, in
practice, the same forest patch. This needs spatial suppression before the final cut,
not just a per-macrocluster score sort.

## Ranking scope: species × macrocluster (not species alone)

`ranked` tier candidates are now selected per `(species, macrocluster_id)` bucket, 10
per bucket (`SCOUT_CANDIDATES_PER_SPECIES_PER_MACROCLUSTER = 10`), instead of 10 per
species globally. With 5 species and up to 22 macroclusters, this bounds `ranked` +
`suppressed_by_nearby` rows at roughly 500-1,100 total (well within GeoJSON/QGIS's
comfortable range) — a small fraction of the real ~493k-ecotone pool, and nowhere near
"top 50 places in all of Estonia," which is what a global cut effectively produced.

`remote_high_value` — the tier for ecologically-strong candidates the v1 access-distance
proxy couldn't confirm a nearby road for — **stays global per species**, unchanged from
today's behavior. Its purpose (surfacing standout ecological candidates worth a road-access
re-check, wherever in Estonia they are) doesn't depend on macrocluster locality the way
`ranked` (an actual day-trip shortlist) does, and splitting it per-macrocluster would
roughly double this tier's row count for no benefit to its actual use case.

## Macrocluster resolution: reuse `ecotone_macrocluster_id`, don't reimplement

A new function, `attach_macrocluster_id(joined_gdf, eraldis_gdf) -> gpd.GeoDataFrame`
(in `scout.py`), adds a `macrocluster_id` column (plus a diagnostic-only
`is_cross_macrocluster` bool) to every ecotone row, calling the existing
`ecotone_macrocluster_id(id_a, id_b, eraldis_to_macrocluster)` from `macrocluster.py`
(already used by `rollup_daily_state` for exactly this resolution, just later in the
pipeline than needed now) — never reimplemented. Same existing convention for a
cross-macrocluster ecotone: bucketed under stand A's macrocluster
(`cluster_a = eraldis_to_macrocluster[id_a]`), diagnostic flag set. Real production data
shows 0 cross-macrocluster ecotones currently (documented in `CLAUDE.md`'s
"Macroclustering" section — `touching`/`near_gap` adjacency almost always keeps both
stands of a pair inside the same `forest_block`), so this remains a rare edge case
inherited unchanged, not something newly designed here.

This is a new, separate function rather than folded into `join_ecotone_access` —
access-joining and macrocluster-resolution are different concerns, each independently
testable.

## Weather coverage gate: per (species, macrocluster), not per species globally

`MIN_SCOUT_WEATHER_COVERAGE = 0.90` (unchanged value) is now checked once per
`(species, macrocluster)` bucket instead of once per species across all of Estonia.
Concretely: `weather_coverage_ratio(joined_gdf, species)`'s signature is unchanged; the
caller passes an already-macrocluster-filtered subset of `joined_gdf` instead of the
whole pool, making the check per-bucket for free.

**Why this matters, concretely:** a species could show 96% weather-data coverage
nationally while one specific macrocluster's local coverage is genuinely poor (28%, say)
— under a global-only gate, that macrocluster's candidates would still get ranked and
published as if the data were trustworthy. Under the per-bucket gate, that
`(species, macrocluster)` combination is skipped — `ranked_count = 0` for that bucket,
with the reason explicitly recorded (see next section) — rather than silently
publishing a ranking built on insufficient real data. This is the same "never fabricate,
never silently degrade" discipline this project already applies elsewhere (`AccessScore`,
`remote_high_value`'s own exclusion-reason tracking).

If every bucket for a species fails this gate, that species contributes zero `ranked`
rows across all macroclusters — but `remote_high_value` (computed globally, gated
separately by the existing species-wide `weather_coverage_ratio` check, unchanged) can
still publish for that species. The existing "refuse to publish `scout_candidates.geojson`
at all if every species fails everywhere" guard in `export_scout_candidates.py` is
unchanged in spirit, re-evaluated against the new per-bucket-aware row counts.

## Reporting gate failures: `macrocluster_state.geojson`, not `scout_candidates.geojson`

A macrocluster with zero eligible candidates for a species (whether from the weather
gate or genuine ecological absence) has no row to attach a status to in
`scout_candidates.geojson` — it's a pure candidate-rows file. The gate outcome is
reported instead as a new per-species column on `data/macrocluster_state.geojson`
(`rollup_macroclusters.py`'s output, which already has exactly one row per macrocluster):
`today_weather_status_{species}` (values: `"ok"` / `"insufficient_coverage"` — exact
string constants defined in `macrocluster.py` alongside the existing tier/exclusion-reason
constants in `scout.py`).

`rollup_macroclusters.py` runs as a separate script/process from
`export_scout_candidates.py` (per `main.py`'s step order) with no shared state between
them. Rather than invent an inter-script communication mechanism for one boolean per
bucket, `rollup_daily_state` **recomputes the same cheap per-bucket coverage-ratio check
itself** — it already has `joined_gdf` in scope, and re-deriving one ratio comparison per
`(species, macrocluster)` is negligible cost compared to the file's existing per-request
work. This does mean the gate decision is computed twice (once by each script) rather
than computed once and shared — an accepted, deliberate tradeoff for avoiding new
cross-script state.

## Spatial suppression: operational compression, not data rejection

Suppression happens **after** eligibility/access filtering, not before — concretely, a
row that never got a real `scout_score` (because `scout_eligible` was `False` or fruiting
data was missing — `scout_score()` already returns `None` in exactly these cases, unchanged)
never enters the suppression step at all, so an access-ineligible candidate can never
suppress a real, reachable one. This falls out naturally from the existing formula rather
than needing a separate explicit ordering step.

A new function in `scout.py`:

```python
def suppress_nearby_candidates(
    scored_gdf: gpd.GeoDataFrame,  # pre-sorted by scout_score desc, has real geometry
    min_separation_m: float,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Greedy NMS: walks scored_gdf in score order, keeping a candidate only if its
    centroid is farther than min_separation_m from every already-kept candidate's
    centroid. Returns (retained, suppressed). Suppressed rows carry suppressed_by_id —
    the retained candidate's id_a/id_b pair formatted as f"{id_a}_{id_b}", the exact
    same convention rollup_daily_state already uses for today_top_target_id_{species}
    (see macrocluster.py) — suppression_distance_m (real centroid distance to the suppressor,
    not the threshold), and pre_suppression_rank (this candidate's score-sorted position
    before suppression removed anything above it)."""
```

`MIN_SCOUT_SEPARATION_M = 400.0` — a v0 engineering prior (same discipline as this
project's other such constants: `ACCESS_DISTANCE_CAP_M`, `BLOCK_NEIGHBOR_PROXY_M`, etc.)
framed as an operational heuristic ("two ecotones 70m apart usually aren't two
independent scouting stops"), not a claimed biological/ecological constant. Distance
computed in the project's standard metric CRS (EPSG:3301), consistent with every other
distance calculation in this codebase.

**Suppression is compression, not rejection.** A suppressed candidate isn't evidence of
bad data — it's a real, valid ecotone that happens to sit too close to a
higher-scoring neighbor to warrant its own separate scouting stop. The full underlying
population remains fully available in `data/ecotones.geojson`; this design only decides
what's worth surfacing as a *distinct* scouting target in the day's shortlist.

Given that, exporting every suppressed ecotone would be pointless volume (a long, dense
ecotone complex could suppress dozens of neighbors within 400m of a single winner) — so
only the **best few** suppressed alternatives per retained target are exported, capped
by `MAX_SUPPRESSED_EXAMPLES_PER_TARGET = 3`, as `tier="suppressed_by_nearby"` rows. The
retained (`ranked`) row itself gets two new aggregate columns, computed from its **full**
suppressed set (before the export cap truncates what's written out) — so a retained
target with, say, 6 real suppressed neighbors reports `nearby_suppressed_count = 6`
even though only its best 3 get their own exported rows:

- `nearby_suppressed_count` — total real count of ecotones this target suppressed.
- `nearby_best_suppressed_score` — the highest `scout_score` among everything it
  suppressed (i.e., how close the runner-up was).

This is explicitly framed as a stepping stone toward a future "hotspot" grouping
(clustering several nearby strong ecotones into one first-class scouting-hotspot
concept) — out of scope for this design, but the `suppressed_by_id` linkage and the two
aggregate columns are exactly the data a future hotspot layer would consume.

## Pipeline architecture

```
join_ecotone_access + join_ecotone_fruiting        (unchanged — scout.py)
        ↓
attach_macrocluster_id                              (NEW — scout.py, reuses
                                                       macrocluster.py's
                                                       ecotone_macrocluster_id)
        ↓
for each species:
    remote_high_value_for_species(joined, species,   (split out from today's
      REMOTE_HIGH_VALUE_TOP_N)                        scout_candidates_for_species;
        ↓                                             unchanged computation, still
    tier="remote_high_value", global scope             global scope)

    for each macrocluster_id present in joined:
        ↓
    bucket = joined[joined.macrocluster_id == mc_id]
        ↓
    weather coverage gate                             (CHANGED — per (species,
    (MIN_SCOUT_WEATHER_COVERAGE)                        macrocluster) now; skip
        ↓ (pass)                                        bucket + implicitly report
    compute scout_score per row                         via macrocluster_state.geojson
    (unchanged formula; None if                          if it fails)
     access-ineligible/missing
     fruiting data)
        ↓
    sort by scout_score desc
        ↓
    suppress_nearby_candidates                        (NEW — greedy NMS,
    (MIN_SCOUT_SEPARATION_M)                            MIN_SCOUT_SEPARATION_M)
        ↓
    top 10 retained → tier="ranked",
      rank_macrocluster 1..10
    suppressed → tier="suppressed_by_nearby",
      capped at MAX_SUPPRESSED_EXAMPLES_PER_TARGET
      per retained target
```

`rollup_macroclusters.py` / `rollup_daily_state`: drops its own `ecotone_macrocluster_id`
re-derivation for the *candidates* frame (groups by the now-present `macrocluster_id`
column directly instead); keeps the helper only for the `joined_gdf`-wide
`cross_macrocluster_ecotone_count` diagnostic (unrelated to candidate selection). Adds
`today_weather_status_{species}` per the recompute-the-gate approach above.

## Output schema: `data/scout_candidates.geojson`

```
species
tier                        # "ranked" | "suppressed_by_nearby" | "remote_high_value"
macrocluster_id              # populated on ALL rows (informational even for
                                remote_high_value, since attach_macrocluster_id runs on
                                the whole pool before either loop)
rank_macrocluster             # 1..10, ranked tier only; None otherwise
rank                          # existing column, now remote_high_value tier only;
                                None otherwise (kept distinct from rank_macrocluster
                                since the two tiers have different scopes — global vs
                                per-macrocluster — and conflating them into one column
                                would be ambiguous)
scout_score
ecotone_score
access_modifier
access_confidence
access_reason
nearest_car_road_m
fruiting_score
weather_data_quality
weather_data_coverage
weather_as_of
exclusion_reason              # remote_high_value only (existing, unchanged)
suppressed_by_id              # NEW — suppressed_by_nearby only
suppression_distance_m        # NEW — suppressed_by_nearby only
pre_suppression_rank          # NEW — suppressed_by_nearby only
nearby_suppressed_count       # NEW — ranked tier only (full suppressed count, pre-cap)
nearby_best_suppressed_score  # NEW — ranked tier only
transition_length_m
dominant_species_a
dominant_species_b
id_a
id_b
geometry
```

`rank_global` (an optional Estonia-wide rank across all macroclusters' retained
candidates, floated in early discussion) is **explicitly dropped for v0** — it needs an
extra global sort per species after all per-macrocluster buckets are computed, and
nothing in this design consumes it. Adding it later is a non-breaking, purely additive
schema change if it turns out useful (e.g. for a future "best N spots nationally, but
still grouped by trip" view) — not designed further here.

## Output schema: `data/macrocluster_state.geojson`

One new column per species: `today_weather_status_{species}` (string: `"ok"` or
`"insufficient_coverage"`, exact constant names defined alongside `scout.py`'s existing
`REMOTE_EXCLUSION_REASON`/`MISSING_FRUITING_DATA_REASON` pattern). Existing
`today_ranked_count_{species}`/`today_top_score_{species}`/`today_top3_mean_score_{species}`/
`today_top_target_id_{species}`/`today_weather_coverage_{species}` columns are unchanged
in meaning, now computed from the per-macrocluster `ranked` tier rows already scoped to
that macrocluster (previously these were already grouping the flat `scout_candidates_gdf`
by macrocluster post-hoc via the now-redundant `ecotone_macrocluster_id` re-derivation —
this design doesn't change what these columns mean, only removes a redundant computation
upstream of them).

## New constants (`scout.py`)

```python
SCOUT_CANDIDATES_PER_SPECIES_PER_MACROCLUSTER = 10   # ranked tier, per (species, macrocluster)
REMOTE_HIGH_VALUE_TOP_N = 10                          # unchanged value; kept as its own
                                                        # constant (was TOP_N) since it's
                                                        # independently tunable — global
                                                        # scope, not per-macrocluster
MIN_SCOUT_SEPARATION_M = 400.0                        # v0 engineering prior
MAX_SUPPRESSED_EXAMPLES_PER_TARGET = 3
# MIN_SCOUT_WEATHER_COVERAGE = 0.90                   # unchanged value, now applied
                                                        # per (species, macrocluster)
                                                        # bucket instead of per species
```

## Testing

- `scout.py`: `attach_macrocluster_id` (cross-macrocluster and same-macrocluster cases,
  reusing `ecotone_macrocluster_id`'s existing test coverage where possible rather than
  duplicating it); `suppress_nearby_candidates` (a real NMS test — candidates within
  threshold suppressed, candidates beyond threshold retained, suppressed rows carry
  correct `suppressed_by_id`/`suppression_distance_m`/`pre_suppression_rank`, retained
  rows carry correct `nearby_suppressed_count`/`nearby_best_suppressed_score` even when
  the export cap truncates which suppressed rows actually get returned);
  `scout_candidates_for_species_macrocluster` (integration of gate + scoring + sort +
  suppression for one bucket); `remote_high_value_for_species` (split-out, same
  behavior as today's existing `scout_candidates_for_species`'s remote half — existing
  tests for that behavior should mostly carry over onto the new function name).
- `export_scout_candidates.py`: real end-to-end shape check — multiple macroclusters
  each get their own `ranked` rows for a species with eligible candidates in more than
  one macrocluster (the direct regression test for the bug this design fixes: today,
  every real macrocluster except one gets zero candidates).
- `rollup_macroclusters.py`/`macrocluster.py`: `today_weather_status_{species}` reflects
  a real per-bucket gate failure/pass; a regression test confirms `today_ranked_count_*`/
  `today_top_score_*`/`today_top3_mean_score_*`/`today_top_target_id_*` come out
  identical whether computed by grouping on the now-present `macrocluster_id` column
  directly (the new approach) or by the old per-candidate `ecotone_macrocluster_id`
  re-derivation (kept temporarily side-by-side in the test only, not in production code)
  — proving the simplification is behavior-preserving before the old derivation path is
  deleted.

## Out of Scope

- **Full "hotspot" grouping** (clustering several nearby strong ecotones into one
  first-class scouting-hotspot entity, rather than one retained target + linked
  suppressed alternatives). This design's `suppressed_by_id`/`nearby_suppressed_count`/
  `nearby_best_suppressed_score` fields are explicitly meant as a stepping stone toward
  that, not a substitute for it.
- **`rank_global`** — see above, trivially additive later if needed.
- **QGIS `.qml` styling** (e.g. showing `rank_macrocluster <= 5` by default, fading
  `6-10`, or styling `suppressed_by_nearby` faintly) — a downstream, non-code concern,
  not part of this design.
- **Recalibrating `MIN_SCOUT_SEPARATION_M`/`MAX_SUPPRESSED_EXAMPLES_PER_TARGET`/
  `SCOUT_CANDIDATES_PER_SPECIES_PER_MACROCLUSTER` against real scouting-trip outcomes**
  — all three remain v0 engineering priors, same discipline as this project's other
  hand-picked constants (`FruitingScore`'s rain-response scales, `HabitatScore`'s host
  profiles, etc.), pending real observation data.
- **Changing the underlying `scout_score` formula itself** (`ecotone_score ×
  access_modifier × fruiting_modifier`) — this design is entirely about selection/export,
  not scoring.
