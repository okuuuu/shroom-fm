# Macroclustering Design

## Problem

Today's pipeline scores individual `eraldis`, ecotones, and (per species, per day)
scout candidates across the entire ~262,054-stand download radius, then exports a flat
Top-10-per-species list (`data/scout_candidates.geojson`). There is no notion of
*region*: the forager has no way to ask "which parts of my working radius are worth a
whole outing today" before drilling into individual candidates. Manually naming
regions (e.g. "Kõrvemaa-West") doesn't scale to a season of trips and has no
consistent, revisitable boundary.

**Macroclustering** adds that missing middle layer: a set of forest-block groupings —
*operational search regions* a forager can physically pivot between during one
outing — sitting between raw `eraldis` and the existing per-day `ScoutScore` ranking.

**Critical semantic constraint, established during design:** a macrocluster is a
*geographic/transport-topology* grouping, not a grouping of today's good scores. Its
membership must not depend on species, weather, or `FruitingScore` — those determine
which candidates are active *inside* a macrocluster on a given day, never which
macrocluster a stand belongs to. This keeps macrocluster identity stable enough for
season-long tracking ("how did this region look on Aug 18 vs Aug 23") — a
score/weather-driven grouping would silently reshuffle its own definition after every
rain event, making that kind of history meaningless.

## Architecture

```
eraldis (existing, 262,054 stands)
      │
      │  existing adjacency graph (data/adjacency.geojson, MAX_GAP_M=10m)
      ▼
forest_blocks   ← NEW: connected components of the existing adjacency graph
      │
      │  block-level proximity graph (NEW cap, BLOCK_NEIGHBOR_PROXY_M)
      ▼
super-components   ← coarse staging only, not the final grouping
      │
      │  constrained complete-linkage partition, geometry-extent validated
      ▼
macroclusters (BaseMacrocluster)   ← NEW: the stable operational regions
      │
      │  (daily) join today's data/scout_candidates.geojson by macrocluster_id
      ▼
macrocluster daily state (MacroclusterState)   ← candidate counts, top targets,
                                                   coverage, per species, per cluster
```

Two new persistent, additive outputs, matching how `data/adjacency.geojson`/
`data/ecotones.geojson` already sit alongside `data/eraldis.geojson`:

- `data/forest_blocks.geojson` — one row per block.
- `data/macroclusters.geojson` — one row per cluster. **Stable base only** — never
  contains today's scores/weather. Regenerated rarely (whenever `eraldis`/adjacency
  data is re-downloaded), not on every run.
- `data/macrocluster_state.geojson` — one row per cluster, **today's snapshot only**
  (has an `as_of` column). Regenerated every time `export_scout_candidates.py` runs.
  Kept as a *separate* file from `macroclusters.geojson` on purpose: mixing today's
  scores into the canonical cluster definition would destroy the file's role as a
  stable base and make season-long comparison ("Aug 18 vs Aug 23") meaningless. v0
  keeps this as a "latest snapshot only" file (overwritten each run, same pattern
  `data/weather_eraldis.geojson` already uses) — accumulating a season-long history of
  these snapshots is real future work this separation enables, not something this spec
  builds.

`forest_block_id` and `macrocluster_id` are added as new additive columns directly
onto `data/eraldis.geojson`, mirroring how `access.py`/`habitat.py` already add
columns there — this lets anything downstream (ecotones, scout candidates) trace back
to a cluster via the eraldis ids it already carries, with no extra join hop.

## Component 1: `forest_block.py` — forest blocks from the existing adjacency graph

**File:** `src/shroom_fm/forest_block.py`

`data/adjacency.geojson` already encodes exactly the right notion of "physically one
forest massif": `touching` (shared boundary ≥20m) or `near_gap` (gap ≤`MAX_GAP_M`=10m
— genuinely road/ditch scale, not a real field or lake). A `forest_block` is simply a
**connected component of that graph** — no new geometry computation needed for the
grouping itself.

```python
def compute_forest_blocks(
    eraldis_gdf: gpd.GeoDataFrame, adjacency_gdf: gpd.GeoDataFrame
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Returns (eraldis_gdf with a new forest_block_id column,
    forest_blocks_gdf summarizing each block)."""
```

Implementation: build a graph over `eraldis` ids using `adjacency_gdf`'s `id_a`/`id_b`
pairs (`networkx.Graph` + `connected_components`, or an equivalent union-find — either
is fine, this project has no existing `networkx` dependency yet so union-find avoids
adding one if that's preferred; call it out as an open implementation choice for the
plan). Any `eraldis` with no adjacency edges at all is its own singleton block — not an
error, a real (if isolated) forest patch.

`forest_blocks_gdf` columns:
- `forest_block_id` (int, arbitrary but deterministic — e.g. assigned by sorting
  blocks by their centroid coordinate before numbering, so re-running against
  unchanged input data reproduces the same ids; per the "deterministic recompute, no
  persistence" decision, IDs are not guaranteed stable across a real re-download that
  changes the underlying `eraldis` set)
- `geometry` — the dissolved (unioned) geometry of all member stands
- `eraldis_count`
- `geometry_extent_m` — diameter of the convex hull of the block's own geometry (max
  pairwise distance among hull vertices; cheap since hull vertex count is small)
- `oversized_block` (bool) — `geometry_extent_m > MACROCLUSTER_TARGET_EXTENT_M`
  (the *soft* threshold — deliberately more sensitive than the hard cap, since this
  flag is meant to surface "worth a look" cases early). **Diagnostic only in v0, not
  auto-split.** A block this large means the forest is genuinely one contiguous massif
  at a scale approaching or exceeding a whole macrocluster — worth knowing about (it
  signals `MAX_GAP_M`/adjacency might be too permissive, or that this is just a very
  large real forest), but not corrected automatically. A block whose
  `geometry_extent_m` exceeds the *hard* `MACROCLUSTER_MAX_EXTENT_M` will always also
  surface as `oversized_macrocluster` downstream (see Component 3) — no macroclustering
  step above it can fix that. A block flagged `oversized_block` but still under
  `MACROCLUSTER_MAX_EXTENT_M` may well cluster fine on its own; the flag is purely an
  early diagnostic, not a prediction of downstream failure.

## Component 2: `macrocluster.py` — block proximity graph

**File:** `src/shroom_fm/macrocluster.py` (this and Component 3 live in the same
module — they're two steps of one algorithm, not independent concerns)

```python
def build_block_proximity_graph(forest_blocks_gdf: gpd.GeoDataFrame) -> "networkx.Graph"
```

Edge `(A, B)` exists if the **boundary-to-boundary distance** between blocks A and B is
`<= BLOCK_NEIGHBOR_PROXY_M`. Boundary distance (not centroid distance) is used here
because it's the more standard, slightly-more-honest "can I actually get from the edge
of one forest to the edge of the other" signal for *graph connectivity* — it will
later be replaced by real road-network travel time without changing this function's
shape (an edge test against a cost threshold).

Implementation: spatial-indexed nearest-neighbor query (e.g. `geopandas.sjoin_nearest`
with a `max_distance`, consistent with how `access.py` already does its nearest-road
lookup) rather than an O(n²) pairwise distance loop — at hundreds to low thousands of
forest blocks this should be comfortably fast regardless, but match the codebase's
existing spatial-index-first convention.

## Component 3: constrained partitioning into macroclusters

```python
def compute_macroclusters(
    forest_blocks_gdf: gpd.GeoDataFrame, proximity_graph: "networkx.Graph"
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Returns (forest_blocks_gdf with a new macrocluster_id column,
    macroclusters_gdf summarizing each cluster)."""
```

Algorithm, per connected component ("super-component") of the proximity graph:

1. **Extent check first.** Compute the super-component's own `geometry_extent_m` (same
   convex-hull-diameter method as Component 1, applied to the union of all its
   blocks). If it's already `<= MACROCLUSTER_MAX_EXTENT_M`, the whole super-component
   is one valid macrocluster — done, no partitioning. This is what lets a genuinely
   compact region of "3 huge blocks" or "27 tiny blocks" both stay as a single
   macrocluster; block *count* is never a validity rule, only a diagnostic (see below).

2. **Otherwise, partition.** Run
   `sklearn.cluster.AgglomerativeClustering(n_clusters=None,
   distance_threshold=MACROCLUSTER_MAX_EXTENT_M, linkage="complete",
   connectivity=<super-component's adjacency matrix from the proximity graph>)` over
   the blocks' centroid coordinates. Complete linkage gives diameter semantics (a merge
   is only as good as its *worst* pairwise distance) and the `connectivity` constraint
   means it can only ever merge blocks that are actually graph-reachable — this is what
   prevents the single-link chaining problem a naive connected-components-as-cluster
   approach would have (a chain of blocks each 7km from its neighbor, transitively
   spanning 30+km, must not become one macrocluster just because every *adjacent* pair
   satisfies the cap).

3. **Geometry-extent validation (the centroid metric can be optimistic).** For each
   resulting sub-cluster, compute its real `geometry_extent_m` (convex hull of the
   *union of member block geometries*, not just their centroids) — two large,
   elongated blocks can have centroids within `MAX_EXTENT_M` of each other while their
   actual combined footprint is significantly wider. If `geometry_extent_m` still
   exceeds `MACROCLUSTER_MAX_EXTENT_M`, recursively repartition **that specific
   cluster** with a tightened effective `distance_threshold` (e.g. scale down by a
   fixed factor each recursion — exact factor is an implementation detail for the
   plan, not load-bearing). Cap recursion depth (e.g. 5 attempts); if the cluster still
   can't be brought within the geometry-extent cap after that (this only happens when
   a single member block's own `geometry_extent_m` already exceeds
   `MACROCLUSTER_MAX_EXTENT_M` — a strict superset of Component 1's `oversized_block`
   flag, since that one fires at the softer target threshold — and no partitioning
   above such a block can fix it), stop and mark the cluster
   `oversized_macrocluster: True` rather than looping or silently accepting an
   out-of-spec cluster.

`macroclusters_gdf` columns (all **stable/base**, no daily data):
- `macrocluster_id` (int, same determinism convention as `forest_block_id`)
- `geometry` — dissolved union of member block geometries
- `forest_block_count`
- `eraldis_count` (sum over member blocks)
- `centroid_extent_m` — the linkage-algorithm's own metric, kept for diagnostics/audit
- `geometry_extent_m` — the real, validated extent (the metric `MAX_EXTENT_M` is
  actually enforced against)
- `oversized_macrocluster` (bool, see step 3)
- `within_target_extent` (bool) — `geometry_extent_m <= MACROCLUSTER_TARGET_EXTENT_M`.
  **Diagnostic only**, purely informational.
- `within_target_block_count` (bool) — `forest_block_count` within
  `TARGET_BLOCK_COUNT`. **Diagnostic only.** Neither of these two ever forces a further
  split — a naturally compact region of 4 large blocks stays one macrocluster even
  though it's outside the 5–15 target range.

## Component 4: v0 constants (`macrocluster.py`)

All named as **geometric proxies pending a real road-network graph**, not asserted
travel-time facts — same discipline as this project's other v0 engineering priors
(`RAIN_EVENT_DRY_GAP_H`, `ACCESS_DISTANCE_CAP_M`, `MAX_GAP_M`, etc.):

```python
BLOCK_NEIGHBOR_PROXY_M = 8_000        # forest_block-to-forest_block edge cap
MACROCLUSTER_MAX_EXTENT_M = 35_000    # hard cap — enforced by geometry_extent_m
MACROCLUSTER_TARGET_EXTENT_M = 25_000 # soft — diagnostic only
TARGET_BLOCK_COUNT = (5, 15)          # soft — diagnostic only
```

When a real road-network travel-time graph exists (documented future work, not this
spec — see Out of Scope), `BLOCK_NEIGHBOR_PROXY_M`'s straight-line-distance edge test
in Component 2 is the only place that changes; the rest of the algorithm (extent caps,
complete-linkage partitioning, extent validation) is unaffected, since it already
operates on graph-connectivity plus a real geometric extent, not on the proxy metric
itself.

## Component 5: `scripts/compute_forest_blocks.py` and `scripts/compute_macroclusters.py`

Thin orchestrator scripts, matching the existing `scripts/compute_adjacency.py`/
`scripts/score_ecotones.py` shape exactly:

```python
# scripts/compute_forest_blocks.py
ERALDIS_PATH = Path("data/eraldis.geojson")
ADJACENCY_PATH = Path("data/adjacency.geojson")
FOREST_BLOCKS_PATH = Path("data/forest_blocks.geojson")

def main() -> None:
    eraldis_gdf = gpd.read_file(ERALDIS_PATH)
    adjacency_gdf = gpd.read_file(ADJACENCY_PATH)
    eraldis_gdf, blocks_gdf = compute_forest_blocks(eraldis_gdf, adjacency_gdf)
    eraldis_gdf.to_file(ERALDIS_PATH, driver="GeoJSON")       # additive column
    blocks_gdf.to_file(FOREST_BLOCKS_PATH, driver="GeoJSON")  # new file
    print(f"{len(blocks_gdf)} forest blocks from {len(eraldis_gdf)} eraldis, "
          f"{blocks_gdf['oversized_block'].sum()} oversized, saved to {FOREST_BLOCKS_PATH}")
```

```python
# scripts/compute_macroclusters.py
ERALDIS_PATH = Path("data/eraldis.geojson")
FOREST_BLOCKS_PATH = Path("data/forest_blocks.geojson")
MACROCLUSTERS_PATH = Path("data/macroclusters.geojson")

def main() -> None:
    eraldis_gdf = gpd.read_file(ERALDIS_PATH)
    blocks_gdf = gpd.read_file(FOREST_BLOCKS_PATH)
    graph = build_block_proximity_graph(blocks_gdf)
    blocks_gdf, clusters_gdf = compute_macroclusters(blocks_gdf, graph)
    # propagate macrocluster_id from blocks onto eraldis via forest_block_id
    eraldis_gdf = eraldis_gdf.merge(
        blocks_gdf[["forest_block_id", "macrocluster_id"]], on="forest_block_id"
    )
    eraldis_gdf.to_file(ERALDIS_PATH, driver="GeoJSON")
    blocks_gdf.to_file(FOREST_BLOCKS_PATH, driver="GeoJSON")       # gains macrocluster_id
    clusters_gdf.to_file(MACROCLUSTERS_PATH, driver="GeoJSON")     # new file
    print(f"{len(clusters_gdf)} macroclusters from {len(blocks_gdf)} forest blocks, "
          f"{clusters_gdf['oversized_macrocluster'].sum()} oversized, "
          f"saved to {MACROCLUSTERS_PATH}")
```

Both are additions to `main.py`'s `STEPS`, positioned right after `compute_adjacency`
(they depend only on `eraldis`/`adjacency`, nothing weather- or score-related) — well
before `score_habitat`/`score_ecotone_habitat`/`score_fruiting`, matching the
"macrocluster membership doesn't depend on any score" constraint architecturally, not
just by convention.

## Component 6: cross-macrocluster ecotone invariant

**File:** `src/shroom_fm/macrocluster.py`, used by the daily rollup (Component 7).

```python
def ecotone_macrocluster_id(
    id_a: int, id_b: int, eraldis_to_macrocluster: dict[int, int]
) -> tuple[int, bool]:
    """Returns (macrocluster_id, is_cross_cluster).
    Assigns by id_a's cluster; is_cross_cluster is True iff id_a and id_b
    disagree, which should be a rare anomaly, not a silent assumption."""
    cluster_a = eraldis_to_macrocluster[id_a]
    cluster_b = eraldis_to_macrocluster[id_b]
    return cluster_a, cluster_a != cluster_b
```

Real ecotones connect `touching`/`near_gap` stands, which `forest_block` construction
should already have merged into the same block (and therefore the same macrocluster).
A nonzero `cross_macrocluster_ecotone_count` in the rollup's diagnostics is a signal to
revisit `forest_block`/proximity-graph construction, not something to route around
silently — v0 behavior is to count and warn, never crash the pipeline over it.

## Component 7: `scripts/rollup_macroclusters.py` — daily state

**File:** `src/shroom_fm/macrocluster.py` (the scoring function) +
`scripts/rollup_macroclusters.py` (thin orchestrator, run after
`export_scout_candidates.py` in `main.py`'s `STEPS`)

```python
def rollup_daily_state(
    scout_candidates_gdf: gpd.GeoDataFrame,
    eraldis_gdf: gpd.GeoDataFrame,
    macroclusters_gdf: gpd.GeoDataFrame,
    now: datetime,
) -> gpd.GeoDataFrame:
    """One row per macrocluster (including clusters with zero candidates today —
    joined from macroclusters_gdf's full id list, not just the ids that happen to
    appear in scout_candidates_gdf), with today_* columns per TARGET_SPECIES."""
```

Per macrocluster, per species, computed from `scout_candidates_gdf` rows assigned to
that cluster via Component 6's `ecotone_macrocluster_id`:

- `today_ranked_count_{species}` — count of `tier == "ranked"` rows for this
  species in this cluster.
- `today_top_score_{species}` — max `scout_score` among them; `None` if the count is 0
  (never a fabricated 0 — a cluster with zero ranked candidates for a species has an
  unknown score, not a bad one).
- `today_top3_mean_score_{species}` — mean of the top `min(3, ranked_count)`
  `scout_score` values; `None` if `ranked_count == 0`. Chosen over a percentile because
  it answers a directly operational question: *if the first spot is empty, how good are
  the next couple of options in this region?*
- `today_top_target_id_{species}` — the id of the single best-ranked candidate in this
  cluster (so "which ecotone to check first here" is a direct lookup, not a re-sort).
- `today_weather_coverage_{species}` — reuses the already-built
  `scout.weather_coverage_ratio()` (from the `FruitingScore` work), scoped to this
  cluster's candidate pool instead of the whole dataset. **Not a new metric** — this is
  the same function that already gates `export_scout_candidates.py`'s run-level
  publish decision, just grouped by `macrocluster_id`.
- `cross_macrocluster_ecotone_count` — diagnostic total for this cluster (Component 6).

`as_of` is the same timestamp `export_scout_candidates.py`/`refresh_weather.py` already
use.

**Open implementation question for the plan (not load-bearing on this design):**
`weather_coverage_ratio` and the ranked/remote candidate pool both come from the same
`joined` GeoDataFrame `export_scout_candidates.py` already builds internally
(`join_ecotone_access` + `join_ecotone_fruiting`). A separate `rollup_macroclusters.py`
script re-deriving that frame from scratch is architecturally clean (matches this
codebase's one-script-one-job convention throughout) but does recompute it a second
time. Given the post-FruitingScore-fix-wave performance work already made these joins
fast, this is very unlikely to matter at real scale — but if real-data verification
later shows otherwise, folding the rollup into `export_scout_candidates.py` to reuse
its already-built `joined` frame is the natural follow-up optimization. v0 ships as two
separate scripts.

## Testing

Mirrors `tests/test_adjacency.py`'s synthetic-fixture style:

- `test_forest_block.py`: a simple connected trio of touching stands forms one block; a
  pair separated by a >10m gap stays two blocks (matches the *existing* `MAX_GAP_M`,
  not new logic — mostly a wiring test); an isolated stand becomes its own singleton
  block; `oversized_block` fires correctly on synthetic geometry exceeding
  `MACROCLUSTER_TARGET_EXTENT_M`.
- `test_macrocluster.py`:
  - A chain of blocks each within `BLOCK_NEIGHBOR_PROXY_M` of its neighbor, but whose
    endpoints are >`MACROCLUSTER_MAX_EXTENT_M` apart — proves the complete-linkage
    partitioner actually splits it, disproving naive connected-components chaining.
  - A genuinely compact super-component of few-huge or many-tiny blocks stays as one
    macrocluster (block count alone never forces a split).
  - A synthetic case where centroid-extent passes but geometry-extent fails (two large
    elongated blocks) — proves the geometry-extent validation step catches what the
    centroid-based algorithm alone would miss, and triggers recursive repartition.
  - A degenerate case (one member block already oversized) — proves the recursion cap
    correctly bails out to `oversized_macrocluster: True` instead of looping.
  - `ecotone_macrocluster_id`: same-cluster case (`is_cross_cluster=False`) and a
    cross-cluster case (`is_cross_cluster=True`, assigned to `cluster_a`).
  - `rollup_daily_state`: a cluster with real candidates computes all `today_*` fields
    correctly (including the top-3-mean with fewer than 3 candidates); a cluster with
    zero candidates for a species gets `None`, not fabricated zeros; a cluster absent
    from `scout_candidates_gdf` entirely still appears in the output (joined from
    `macroclusters_gdf`) with all `today_*` fields `None`.
- Real-scale verification at the end against the actual 262,054-stand dataset, same
  discipline as every other component in this pipeline: real forest_block/macrocluster
  counts, real extent distributions, real `oversized_block`/`oversized_macrocluster`
  counts, real `cross_macrocluster_ecotone_count` (flag if this turns out nonzero and
  non-trivial — a signal worth investigating, not silently accepting).

## Out of Scope

- **`DailyMacroclusterState` beyond the simple rollup above** — weather-heterogeneity
  detection within a cluster, active/inactive sector labeling, and temporary
  sub-clusters (e.g. `KRV-Central/W` vs `KRV-Central/E`) when a cluster's weather is
  very uneven. Explicitly deferred; the base/state file separation this spec builds is
  what makes this possible later without redesigning the stable layer.
- **Real road-network travel time.** `BLOCK_NEIGHBOR_PROXY_M`'s straight-line distance
  is a v0 proxy. Replacing it with actual routing (building a routable graph from
  `roads.geojson`, which doesn't exist in any form yet) is separate, later work — the
  rest of this architecture (extent caps, connectivity-constrained partitioning) is
  designed to not need to change when that happens.
- **Persisted/reconciled cluster IDs across re-downloads.** v0 is deterministic
  recompute only, per the earlier decision — no matching-against-previous-run logic.
- **Automatic hard-exclusion filtering** (clearcuts, non-forest, movement-prohibited)
  before forest_block construction. v0 clusters everything in the download radius; the
  existing manual orthophoto sanity check (documented in CLAUDE.md) remains the only
  filter, applied later at candidate-selection time as it already is today.
- **Season-long history accumulation** of `macrocluster_state` snapshots. v0 writes
  only the latest snapshot each run; the base/state separation enables building an
  accumulating log later without touching this spec's architecture.
