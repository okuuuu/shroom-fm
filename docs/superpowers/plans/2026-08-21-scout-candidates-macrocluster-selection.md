# Scout Candidates Per-Macrocluster Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `export_scout_candidates.py`'s global top-10-per-species ranking
(which real production data shows collapses onto a single macrocluster near home) with
per-(species, macrocluster) selection plus greedy spatial-suppression, while keeping
`remote_high_value` global and moving the weather-coverage publish gate to per-bucket.

**Architecture:** `macrocluster.py` gains `attach_macrocluster_id` (reusing the existing
`ecotone_macrocluster_id` resolution, now applied before ranking instead of only at
rollup time). `scout.py`'s `scout_candidates_for_species` is replaced by two functions:
`remote_high_value_for_species` (global, unchanged behavior) and
`scout_candidates_for_species_macrocluster` (per-bucket, new — calls a new
`suppress_nearby_candidates` greedy-NMS function). `export_scout_candidates.py` is
restructured around a testable `build_scout_candidate_rows` core function, looping
species × macrocluster. `rollup_daily_state` drops its own per-candidate macrocluster
re-derivation (the column now arrives pre-attached) and gains
`today_weather_status_{species}`.

**Tech Stack:** Python, existing project stack (geopandas/pandas/shapely) — no new
dependencies.

**Spec:** `docs/superpowers/specs/2026-08-21-scout-candidates-macrocluster-selection-design.md`

## Global Constraints

- `MIN_SCOUT_SEPARATION_M = 400.0` (scout.py) — v0 engineering prior, operational
  heuristic, not a biological constant.
- `MAX_SUPPRESSED_EXAMPLES_PER_TARGET = 3` (scout.py).
- `SCOUT_CANDIDATES_PER_SPECIES_PER_MACROCLUSTER = 10` (export_scout_candidates.py,
  replaces `TOP_N` for the `ranked` tier).
- `REMOTE_HIGH_VALUE_TOP_N = 10` (export_scout_candidates.py, replaces `TOP_N` for the
  `remote_high_value` tier — independently tunable from the per-macrocluster count even
  though both currently equal 10).
- `MIN_SCOUT_WEATHER_COVERAGE = 0.90` (scout.py, unchanged value) — now applied per
  `(species, macrocluster)` bucket for the `ranked` tier, and still per-species-globally
  for the `remote_high_value` tier (its scope is explicitly unchanged).
- `remote_high_value` tier stays global per species — NOT split per macrocluster.
- Spatial suppression happens only AFTER a row has a real (non-`None`) `scout_score` —
  `scout_score()` already returns `None` for access-ineligible or missing-fruiting-data
  rows, so those rows structurally can never enter suppression or suppress a real
  candidate. Do not add a separate explicit ordering step for this — it already falls
  out of the existing formula.
- `today_weather_status_{species}` (new column on `data/macrocluster_state.geojson`)
  values: `"ok"`, `"insufficient_coverage"`, or `None` (when the eligible pool for that
  species in that macrocluster is empty — mirrors `today_weather_coverage_{species}`'s
  existing `None`-for-empty-pool convention exactly; `None` means "not applicable," never
  conflate it with `"insufficient_coverage"`, which means "data exists but is poor").
- `rollup_macroclusters.py`/`rollup_daily_state` recomputes the coverage-ratio check
  itself (does not receive it from `export_scout_candidates.py` — no new inter-script
  state) — this is a deliberate, already-decided tradeoff, not a gap to fix.
- `rank_global` is explicitly OUT OF SCOPE for this plan — do not add it.
- `suppressed_by_id` format: `f"{id_a}_{id_b}"` of the RETAINING candidate — the exact
  same convention `rollup_daily_state` already uses for `today_top_target_id_{species}`.
- Circular-import constraint: `scout.py` must NOT import from `macrocluster.py`
  (`macrocluster.py` already imports `weather_coverage_ratio` from `scout.py`, so the
  reverse would be circular). `attach_macrocluster_id` therefore lives in
  `macrocluster.py`, not `scout.py`, even though it operates on a "joined" (ecotone ×
  access × fruiting) frame — this mirrors `rollup_daily_state`'s own existing precedent
  of macrocluster-domain logic operating on scout-domain frames.
- `data/ecotones.geojson` is stored in WGS84 (confirmed: `ecotone.py`'s
  `compute_ecotones` computes internally in `ESTONIAN_GRID_CRS` but returns
  `.to_crs(original_crs)`, and the adjacency input it's built from is WGS84) —
  `export_scout_candidates.py` currently has NO `.to_crs()` call before passing
  `ecotones_gdf` into the join chain. This plan ADDS one (`.to_crs(ESTONIAN_GRID_CRS)`
  right after reading `ecotones_gdf`), since `suppress_nearby_candidates` needs real
  metric centroid distances — degrees would make `MIN_SCOUT_SEPARATION_M` meaningless.
  `scout.py`'s functions themselves stay CRS-agnostic in their own contracts (matching
  existing convention — `join_ecotone_access` never reprojects internally either); the
  caller is responsible for passing already-projected geometry.
- Baseline before this plan: `uv run pytest tests/ -q` passing (run it yourself at plan
  start to confirm the exact current count — do not assume a number).

---

### Task 1: `attach_macrocluster_id` (macrocluster.py)

**Files:**
- Modify: `src/shroom_fm/macrocluster.py` (add function after `ecotone_macrocluster_id`,
  currently ending at line 215)
- Test: `tests/test_macrocluster.py`

**Interfaces:**
- Consumes: `ecotone_macrocluster_id(id_a, id_b, eraldis_to_macrocluster) -> tuple[int, bool]`
  (already exists in this file, unchanged).
- Produces: `attach_macrocluster_id(joined_gdf: gpd.GeoDataFrame, eraldis_gdf:
  gpd.GeoDataFrame) -> gpd.GeoDataFrame` — Task 4 consumes this directly.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_macrocluster.py`, near the existing `ecotone_macrocluster_id` tests
(after line 317, before `test_rollup_daily_state_computes_ranked_stats_for_a_populated_cluster`):

```python
def test_attach_macrocluster_id_same_cluster():
    joined_gdf = gpd.GeoDataFrame(
        {"id_a": [1], "id_b": [2]}, geometry=[Point(0, 0)], crs="EPSG:3301"
    )
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1, 2], "macrocluster_id": [5, 5]},
        geometry=[Point(0, 0), Point(1, 1)],
        crs="EPSG:3301",
    )

    result = attach_macrocluster_id(joined_gdf, eraldis_gdf)

    assert result.loc[0, "macrocluster_id"] == 5
    assert result.loc[0, "is_cross_macrocluster"] == False


def test_attach_macrocluster_id_cross_cluster_assigns_by_id_a():
    joined_gdf = gpd.GeoDataFrame(
        {"id_a": [1], "id_b": [2]}, geometry=[Point(0, 0)], crs="EPSG:3301"
    )
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1, 2], "macrocluster_id": [5, 6]},
        geometry=[Point(0, 0), Point(1, 1)],
        crs="EPSG:3301",
    )

    result = attach_macrocluster_id(joined_gdf, eraldis_gdf)

    assert result.loc[0, "macrocluster_id"] == 5
    assert result.loc[0, "is_cross_macrocluster"] == True


def test_attach_macrocluster_id_multiple_rows_resolved_independently():
    joined_gdf = gpd.GeoDataFrame(
        {"id_a": [1, 3], "id_b": [2, 4]},
        geometry=[Point(0, 0), Point(1, 1)],
        crs="EPSG:3301",
    )
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1, 2, 3, 4], "macrocluster_id": [5, 5, 9, 9]},
        geometry=[Point(0, 0)] * 4,
        crs="EPSG:3301",
    )

    result = attach_macrocluster_id(joined_gdf, eraldis_gdf)

    assert list(result["macrocluster_id"]) == [5, 9]


def test_attach_macrocluster_id_does_not_mutate_input():
    joined_gdf = gpd.GeoDataFrame(
        {"id_a": [1], "id_b": [2]}, geometry=[Point(0, 0)], crs="EPSG:3301"
    )
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1, 2], "macrocluster_id": [5, 5]},
        geometry=[Point(0, 0), Point(1, 1)],
        crs="EPSG:3301",
    )

    attach_macrocluster_id(joined_gdf, eraldis_gdf)

    assert "macrocluster_id" not in joined_gdf.columns
```

Add `attach_macrocluster_id` to the existing `from shroom_fm.macrocluster import
ecotone_macrocluster_id, rollup_daily_state` line at the top of the file's second import
block (line 278).

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_macrocluster.py -v -k attach_macrocluster_id`
Expected: FAIL — `ImportError`, `attach_macrocluster_id` doesn't exist yet.

- [ ] **Step 3: Implement**

Insert into `src/shroom_fm/macrocluster.py` immediately after `ecotone_macrocluster_id`
(after line 215, before `def rollup_daily_state`):

```python
def attach_macrocluster_id(
    joined_gdf: gpd.GeoDataFrame, eraldis_gdf: gpd.GeoDataFrame
) -> gpd.GeoDataFrame:
    """Adds macrocluster_id (+ diagnostic-only is_cross_macrocluster) to every row of
    joined_gdf (ecotones x access x fruiting, id_a/id_b columns), reusing
    ecotone_macrocluster_id's existing cross-macrocluster convention (bucketed under
    stand A's macrocluster) — the same resolution rollup_daily_state already performs,
    just moved earlier in the pipeline so per-macrocluster ranking can happen at export
    time instead of only at rollup time."""
    eraldis_to_macrocluster = dict(zip(eraldis_gdf["id"], eraldis_gdf["macrocluster_id"]))
    cluster_ids = []
    cross_flags = []
    for id_a, id_b in zip(joined_gdf["id_a"], joined_gdf["id_b"]):
        cluster_id, is_cross = ecotone_macrocluster_id(id_a, id_b, eraldis_to_macrocluster)
        cluster_ids.append(cluster_id)
        cross_flags.append(is_cross)
    result = joined_gdf.copy()
    result["macrocluster_id"] = cluster_ids
    result["is_cross_macrocluster"] = cross_flags
    return result
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_macrocluster.py -v -k attach_macrocluster_id`
Expected: 4 passed.

- [ ] **Step 5: Run the full test file**

Run: `uv run pytest tests/test_macrocluster.py -v`
Expected: all pass (this task only adds a new function; nothing existing should break).

- [ ] **Step 6: Commit**

```bash
git add src/shroom_fm/macrocluster.py tests/test_macrocluster.py
git commit -m "feat: add attach_macrocluster_id, reusing ecotone_macrocluster_id earlier in the pipeline"
```

---

### Task 2: `suppress_nearby_candidates` (scout.py)

**Files:**
- Modify: `src/shroom_fm/scout.py` (add constant + function)
- Test: `tests/test_scout.py`

**Interfaces:**
- Produces: `suppress_nearby_candidates(scored_gdf: gpd.GeoDataFrame, min_separation_m:
  float) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]` — Task 3 consumes this directly.
  `scored_gdf` must already be sorted by `scout_score` descending and have real
  geometry in a metric CRS; must have `id_a`/`id_b`/`scout_score` columns.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_scout.py`, after the existing imports (add `suppress_nearby_candidates`
to the `from shroom_fm.scout import (...)` block at the top):

```python
def test_suppress_nearby_candidates_suppresses_a_close_lower_scored_candidate():
    scored_gdf = gpd.GeoDataFrame(
        {"id_a": [1, 3], "id_b": [2, 4], "scout_score": [1.0, 0.9]},
        geometry=[Point(0, 0), Point(100, 0)],
        crs="EPSG:3301",
    )

    retained, suppressed = suppress_nearby_candidates(scored_gdf, min_separation_m=400.0)

    assert len(retained) == 1
    assert retained.iloc[0]["id_a"] == 1
    assert len(suppressed) == 1
    assert suppressed.iloc[0]["suppressed_by_id"] == "1_2"
    assert suppressed.iloc[0]["suppression_distance_m"] == pytest.approx(100.0)
    assert suppressed.iloc[0]["pre_suppression_rank"] == 2


def test_suppress_nearby_candidates_retains_candidates_beyond_threshold():
    scored_gdf = gpd.GeoDataFrame(
        {"id_a": [1, 3], "id_b": [2, 4], "scout_score": [1.0, 0.9]},
        geometry=[Point(0, 0), Point(1000, 0)],
        crs="EPSG:3301",
    )

    retained, suppressed = suppress_nearby_candidates(scored_gdf, min_separation_m=400.0)

    assert len(retained) == 2
    assert len(suppressed) == 0


def test_suppress_nearby_candidates_checks_against_currently_retained_set_only():
    # Point B (score 0.9) is close to A (score 1.0) and gets suppressed by A. Point C
    # (score 0.5) is far from A but would be close to B if B had been retained --
    # greedy NMS must check against the RETAINED set, not every prior candidate, so C
    # is correctly retained (its nearest RETAINED neighbor is A, at 1000m away).
    scored_gdf = gpd.GeoDataFrame(
        {"id_a": [1, 3, 5], "id_b": [2, 4, 6], "scout_score": [1.0, 0.9, 0.5]},
        geometry=[Point(0, 0), Point(100, 0), Point(1000, 0)],
        crs="EPSG:3301",
    )

    retained, suppressed = suppress_nearby_candidates(scored_gdf, min_separation_m=400.0)

    assert sorted(retained["id_a"]) == [1, 5]
    assert list(suppressed["id_a"]) == [3]
    assert suppressed.iloc[0]["suppressed_by_id"] == "1_2"
    assert suppressed.iloc[0]["pre_suppression_rank"] == 2


def test_suppress_nearby_candidates_handles_empty_input():
    scored_gdf = gpd.GeoDataFrame(
        {"id_a": [], "id_b": [], "scout_score": []}, geometry=[], crs="EPSG:3301"
    )

    retained, suppressed = suppress_nearby_candidates(scored_gdf, min_separation_m=400.0)

    assert len(retained) == 0
    assert len(suppressed) == 0
    assert "suppressed_by_id" in suppressed.columns
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_scout.py -v -k suppress_nearby_candidates`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Implement**

Add to `src/shroom_fm/scout.py`, after the `MISSING_FRUITING_DATA_REASON`/
`MIN_SCOUT_WEATHER_COVERAGE` constants (after line 63):

```python
MIN_SCOUT_SEPARATION_M = 400.0
MAX_SUPPRESSED_EXAMPLES_PER_TARGET = 3


def suppress_nearby_candidates(
    scored_gdf: gpd.GeoDataFrame, min_separation_m: float
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """scored_gdf must already be sorted by scout_score descending, with real geometry
    in a metric CRS (this project's ESTONIAN_GRID_CRS) — greedy nearest-neighbor
    suppression: walks rows in score order, keeping a candidate only if its centroid is
    farther than min_separation_m from every already-KEPT candidate's centroid (not
    every prior candidate — a candidate suppressed earlier never itself becomes a
    reference point). Returns (retained, suppressed). Suppressed rows gain
    suppressed_by_id (the retaining candidate's f"{id_a}_{id_b}", same convention as
    rollup_daily_state's today_top_target_id_{species}), suppression_distance_m (real
    centroid distance to the suppressor, not the threshold), and pre_suppression_rank
    (1-based position in scored_gdf's own sorted order, before any suppression)."""
    if len(scored_gdf) == 0:
        empty = scored_gdf.copy()
        empty["suppressed_by_id"] = pd.Series(dtype=object)
        empty["suppression_distance_m"] = pd.Series(dtype=float)
        empty["pre_suppression_rank"] = pd.Series(dtype="Int64")
        return scored_gdf.copy(), empty

    centroids = scored_gdf.geometry.centroid
    retained_idx: list = []
    retained_centroids: list = []
    suppressed_records = []

    for position, (idx, centroid) in enumerate(zip(scored_gdf.index, centroids), start=1):
        nearest_distance = None
        nearest_retained_idx = None
        for r_idx, r_centroid in zip(retained_idx, retained_centroids):
            d = centroid.distance(r_centroid)
            if nearest_distance is None or d < nearest_distance:
                nearest_distance = d
                nearest_retained_idx = r_idx
        if nearest_distance is not None and nearest_distance < min_separation_m:
            retaining_row = scored_gdf.loc[nearest_retained_idx]
            suppressed_records.append(
                {
                    "index": idx,
                    "suppressed_by_id": f"{retaining_row['id_a']}_{retaining_row['id_b']}",
                    "suppression_distance_m": nearest_distance,
                    "pre_suppression_rank": position,
                }
            )
        else:
            retained_idx.append(idx)
            retained_centroids.append(centroid)

    retained = scored_gdf.loc[retained_idx].copy()

    if suppressed_records:
        suppressed_meta = pd.DataFrame(suppressed_records).set_index("index")
        suppressed = scored_gdf.loc[suppressed_meta.index].copy()
        suppressed["suppressed_by_id"] = suppressed_meta["suppressed_by_id"]
        suppressed["suppression_distance_m"] = suppressed_meta["suppression_distance_m"]
        suppressed["pre_suppression_rank"] = suppressed_meta["pre_suppression_rank"]
    else:
        suppressed = scored_gdf.iloc[0:0].copy()
        suppressed["suppressed_by_id"] = pd.Series(dtype=object)
        suppressed["suppression_distance_m"] = pd.Series(dtype=float)
        suppressed["pre_suppression_rank"] = pd.Series(dtype="Int64")

    return retained, suppressed
```

Note: `pandas` (`import pandas as pd`) is already imported at the top of `scout.py` —
verify this before assuming it, and add the import if it's somehow missing.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_scout.py -v -k suppress_nearby_candidates`
Expected: 4 passed.

- [ ] **Step 5: Run the full test file**

Run: `uv run pytest tests/test_scout.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/shroom_fm/scout.py tests/test_scout.py
git commit -m "feat: add suppress_nearby_candidates greedy spatial NMS"
```

---

### Task 3: `remote_high_value_for_species` + `scout_candidates_for_species_macrocluster` (scout.py)

**Files:**
- Modify: `src/shroom_fm/scout.py` (replace `scout_candidates_for_species` — originally
  lines 78-115, shifted down by Task 2's new constant/function inserted earlier in this
  same file; locate by the `def scout_candidates_for_species(` line — with two new
  functions plus a shared private helper)
- Test: `tests/test_scout.py` (replace the 4 existing `scout_candidates_for_species`
  tests — `test_scout_candidates_for_species_splits_and_sorts_tiers`,
  `test_scout_candidates_for_species_caps_each_tier_independently`,
  `test_scout_candidates_for_species_reports_missing_fruiting_data_reason`,
  `test_scout_candidates_for_species_access_ineligibility_takes_precedence_over_missing_fruiting`
  — locate each by name, not by line number, since Task 2 already added tests earlier
  in this same file — with tests for the two new functions)

**Interfaces:**
- Consumes: `scout_score` (unchanged), `suppress_nearby_candidates` (Task 2),
  `REMOTE_EXCLUSION_REASON`/`MISSING_FRUITING_DATA_REASON` (unchanged, already exist).
- Produces: `remote_high_value_for_species(joined_gdf, species, top_n) ->
  gpd.GeoDataFrame` and `scout_candidates_for_species_macrocluster(bucket_gdf, species,
  top_n, min_separation_m, max_suppressed_examples) -> tuple[gpd.GeoDataFrame,
  gpd.GeoDataFrame]` (ranked, capped_suppressed) — Task 4 consumes both directly.
  `scout_candidates_for_species` no longer exists after this task.

- [ ] **Step 1: Write the failing tests**

In `tests/test_scout.py`, replace the current `from shroom_fm.scout import (...)` block
at the top of the file (by this point, after Task 2, it already includes
`suppress_nearby_candidates` alongside the original 5 names — locate it by content, its
exact line range will have shifted since Task 2) with:

```python
from shroom_fm.scout import (
    REMOTE_EXCLUSION_REASON,
    join_ecotone_access,
    remote_high_value_for_species,
    scout_candidates_for_species_macrocluster,
    scout_score,
    suppress_nearby_candidates,
    weather_coverage_ratio,
)
```

Delete the 4 existing tests naming `scout_candidates_for_species` (lines 113-200:
`test_scout_candidates_for_species_splits_and_sorts_tiers` through
`test_scout_candidates_for_species_access_ineligibility_takes_precedence_over_missing_fruiting`).
Replace them with:

```python
def test_remote_high_value_for_species_selects_excluded_candidates_by_ecotone_score():
    joined_gdf = gpd.GeoDataFrame(
        {
            "ecotone_score_chanterelle": [1.5, 1.2, 0.9, 0.6, None],
            "access_modifier": [0.8, 0.5, 0.9, 0.1, 0.9],
            "fruiting_modifier_chanterelle": [1.0, 1.0, 1.0, 1.0, 1.0],
            "scout_eligible": [True, True, False, True, True],
        },
        geometry=[Point(i, 0) for i in range(5)],
        crs="EPSG:3301",
    )

    remote = remote_high_value_for_species(joined_gdf, "chanterelle", top_n=5)

    assert len(remote) == 1
    assert remote.iloc[0]["ecotone_score"] == pytest.approx(0.9)
    assert remote.iloc[0]["exclusion_reason"] == REMOTE_EXCLUSION_REASON


def test_remote_high_value_for_species_caps_at_top_n():
    joined_gdf = gpd.GeoDataFrame(
        {
            "ecotone_score_chanterelle": [3.0, 2.0, 1.0],
            "access_modifier": [0.0, 0.0, 0.0],
            "fruiting_modifier_chanterelle": [1.0, 1.0, 1.0],
            "scout_eligible": [False, False, False],
        },
        geometry=[Point(i, 0) for i in range(3)],
        crs="EPSG:3301",
    )

    remote = remote_high_value_for_species(joined_gdf, "chanterelle", top_n=2)

    assert len(remote) == 2
    assert list(remote["ecotone_score"]) == [3.0, 2.0]


def test_remote_high_value_for_species_reports_missing_fruiting_data_reason():
    joined_gdf = gpd.GeoDataFrame(
        {
            "ecotone_score_chanterelle": [1.5, 1.2],
            "access_modifier": [0.8, 0.9],
            "fruiting_modifier_chanterelle": [0.7, None],
            "scout_eligible": [True, True],
        },
        geometry=[Point(0, 0), Point(1, 0)],
        crs="EPSG:3301",
    )

    remote = remote_high_value_for_species(joined_gdf, "chanterelle", top_n=5)

    assert len(remote) == 1
    assert remote.iloc[0]["exclusion_reason"] == "MISSING_FRUITING_DATA"
    assert remote.iloc[0]["ecotone_score"] == pytest.approx(1.2)


def test_remote_high_value_for_species_access_ineligibility_takes_precedence():
    joined_gdf = gpd.GeoDataFrame(
        {
            "ecotone_score_chanterelle": [1.5],
            "access_modifier": [0.0],
            "fruiting_modifier_chanterelle": [None],  # both problems apply at once
            "scout_eligible": [False],
        },
        geometry=[Point(0, 0)],
        crs="EPSG:3301",
    )

    remote = remote_high_value_for_species(joined_gdf, "chanterelle", top_n=5)

    assert len(remote) == 1
    assert remote.iloc[0]["exclusion_reason"] == REMOTE_EXCLUSION_REASON


def test_scout_candidates_for_species_macrocluster_ranks_within_bucket():
    bucket_gdf = gpd.GeoDataFrame(
        {
            "id_a": [1, 3, 5],
            "id_b": [2, 4, 6],
            "ecotone_score_chanterelle": [1.5, 1.2, 0.9],
            "access_modifier": [0.8, 0.5, 0.9],
            "fruiting_modifier_chanterelle": [1.0, 1.0, 1.0],
            "scout_eligible": [True, True, True],
        },
        geometry=[Point(0, 0), Point(2000, 0), Point(4000, 0)],
        crs="EPSG:3301",
    )

    ranked, suppressed = scout_candidates_for_species_macrocluster(
        bucket_gdf,
        "chanterelle",
        top_n=10,
        min_separation_m=400.0,
        max_suppressed_examples=3,
    )

    assert len(ranked) == 3
    # scout_score = ecotone_score * access_modifier * fruiting_modifier:
    # row0: 1.5*0.8*1.0=1.2, row1: 1.2*0.5*1.0=0.6, row2: 0.9*0.9*1.0=0.81 -- sorted desc.
    assert [round(v, 4) for v in ranked["scout_score"]] == [1.2, 0.81, 0.6]
    assert len(suppressed) == 0
    assert (ranked["nearby_suppressed_count"] == 0).all()


def test_scout_candidates_for_species_macrocluster_caps_ranked_at_top_n():
    bucket_gdf = gpd.GeoDataFrame(
        {
            "id_a": [1, 3, 5],
            "id_b": [2, 4, 6],
            "ecotone_score_chanterelle": [3.0, 2.0, 1.0],
            "access_modifier": [1.0, 1.0, 1.0],
            "fruiting_modifier_chanterelle": [1.0, 1.0, 1.0],
            "scout_eligible": [True, True, True],
        },
        geometry=[Point(0, 0), Point(2000, 0), Point(4000, 0)],
        crs="EPSG:3301",
    )

    ranked, suppressed = scout_candidates_for_species_macrocluster(
        bucket_gdf,
        "chanterelle",
        top_n=2,
        min_separation_m=400.0,
        max_suppressed_examples=3,
    )

    assert len(ranked) == 2
    assert list(ranked["scout_score"]) == [3.0, 2.0]


def test_scout_candidates_for_species_macrocluster_suppresses_and_reports_nearby_aggregates():
    # Two candidates close together (100m apart, well under min_separation_m=400) --
    # the lower-scored one gets suppressed, and the retained one reports the
    # aggregate columns.
    bucket_gdf = gpd.GeoDataFrame(
        {
            "id_a": [1, 3],
            "id_b": [2, 4],
            "ecotone_score_chanterelle": [1.5, 1.3],
            "access_modifier": [0.8, 0.8],
            "fruiting_modifier_chanterelle": [1.0, 1.0],
            "scout_eligible": [True, True],
        },
        geometry=[Point(0, 0), Point(100, 0)],
        crs="EPSG:3301",
    )

    ranked, suppressed = scout_candidates_for_species_macrocluster(
        bucket_gdf,
        "chanterelle",
        top_n=10,
        min_separation_m=400.0,
        max_suppressed_examples=3,
    )

    assert len(ranked) == 1
    assert ranked.iloc[0]["nearby_suppressed_count"] == 1
    assert ranked.iloc[0]["nearby_best_suppressed_score"] == pytest.approx(1.3 * 0.8)
    assert len(suppressed) == 1
    assert suppressed.iloc[0]["suppressed_by_id"] == "1_2"


def test_scout_candidates_for_species_macrocluster_caps_exported_suppressed_examples():
    # 4 candidates all within 400m of the top-scoring one -- all 3 lower-scored ones
    # get suppressed, but only max_suppressed_examples=2 of them are exported as rows,
    # while nearby_suppressed_count still reports the true total of 3.
    bucket_gdf = gpd.GeoDataFrame(
        {
            "id_a": [1, 3, 5, 7],
            "id_b": [2, 4, 6, 8],
            "ecotone_score_chanterelle": [2.0, 1.8, 1.6, 1.4],
            "access_modifier": [1.0, 1.0, 1.0, 1.0],
            "fruiting_modifier_chanterelle": [1.0, 1.0, 1.0, 1.0],
            "scout_eligible": [True, True, True, True],
        },
        geometry=[Point(0, 0), Point(50, 0), Point(100, 0), Point(150, 0)],
        crs="EPSG:3301",
    )

    ranked, suppressed = scout_candidates_for_species_macrocluster(
        bucket_gdf,
        "chanterelle",
        top_n=10,
        min_separation_m=400.0,
        max_suppressed_examples=2,
    )

    assert len(ranked) == 1
    assert ranked.iloc[0]["nearby_suppressed_count"] == 3
    assert len(suppressed) == 2
    assert list(suppressed["scout_score"]) == [1.8, 1.6]  # best 2 of the 3 suppressed


def test_scout_candidates_for_species_macrocluster_ineligible_rows_never_enter_suppression():
    # An access-ineligible candidate (scout_score is None) must never suppress a real,
    # reachable one, and must never itself appear in ranked or suppressed.
    bucket_gdf = gpd.GeoDataFrame(
        {
            "id_a": [1, 3],
            "id_b": [2, 4],
            "ecotone_score_chanterelle": [5.0, 1.0],  # row 0 scores very high but is ineligible
            "access_modifier": [0.0, 0.8],
            "fruiting_modifier_chanterelle": [1.0, 1.0],
            "scout_eligible": [False, True],
        },
        geometry=[Point(0, 0), Point(50, 0)],  # 50m apart -- would suppress if eligible
        crs="EPSG:3301",
    )

    ranked, suppressed = scout_candidates_for_species_macrocluster(
        bucket_gdf,
        "chanterelle",
        top_n=10,
        min_separation_m=400.0,
        max_suppressed_examples=3,
    )

    assert len(ranked) == 1
    assert ranked.iloc[0]["id_a"] == 3
    assert len(suppressed) == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_scout.py -v`
Expected: FAIL — `ImportError` on `remote_high_value_for_species`/
`scout_candidates_for_species_macrocluster`.

- [ ] **Step 3: Implement**

In `src/shroom_fm/scout.py`, replace `scout_candidates_for_species` (originally lines
78-115, but locate it by name — this task's Step 1 already noted its position has
shifted since Task 2) with:

```python
def _compute_scored(joined_gdf: gpd.GeoDataFrame, species: str) -> gpd.GeoDataFrame:
    """Shared first half of both remote_high_value_for_species and
    scout_candidates_for_species_macrocluster: filters to ecologically-scored rows and
    computes scout_score (None when access-ineligible or fruiting data missing —
    unchanged formula, unchanged from the old scout_candidates_for_species)."""
    ecotone_col = f"ecotone_score_{species}"
    fruiting_col = f"fruiting_modifier_{species}"
    scored = joined_gdf[joined_gdf[ecotone_col].notna()].copy()
    scored["ecotone_score"] = scored[ecotone_col]
    scored["fruiting_score"] = scored[fruiting_col]
    scored["scout_score"] = [
        scout_score(ecotone_score_value, access_modifier_value, fruiting_value, eligible)
        for ecotone_score_value, access_modifier_value, fruiting_value, eligible in zip(
            scored["ecotone_score"],
            scored["access_modifier"],
            scored["fruiting_score"],
            scored["scout_eligible"],
        )
    ]
    return scored


def remote_high_value_for_species(
    joined_gdf: gpd.GeoDataFrame, species: str, top_n: int
) -> gpd.GeoDataFrame:
    """Global (not per-macrocluster) — ecologically-strong candidates the v1
    access-distance proxy couldn't confirm eligible for, or that are missing fruiting
    data, ranked by raw ecotone_score (scout_score is None for these by construction).
    This is exactly the 'remote' half of the old scout_candidates_for_species,
    unchanged behavior, split into its own function since its scope (global) now
    differs from the ranked tier's (per-macrocluster)."""

    def _exclusion_reason(eligible, fruiting_value):
        if not eligible:
            return REMOTE_EXCLUSION_REASON
        return MISSING_FRUITING_DATA_REASON

    scored = _compute_scored(joined_gdf, species)
    excluded = scored[scored["scout_score"].isna()].copy()
    excluded["exclusion_reason"] = [
        _exclusion_reason(eligible, fruiting_value)
        for eligible, fruiting_value in zip(
            excluded["scout_eligible"], excluded["fruiting_score"]
        )
    ]
    return excluded.sort_values("ecotone_score", ascending=False).head(top_n)


def scout_candidates_for_species_macrocluster(
    bucket_gdf: gpd.GeoDataFrame,
    species: str,
    top_n: int,
    min_separation_m: float,
    max_suppressed_examples: int,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """bucket_gdf must already be filtered to one macrocluster (see
    macrocluster.attach_macrocluster_id) and in a metric CRS (this project's
    ESTONIAN_GRID_CRS — suppress_nearby_candidates needs real distances). Computes
    scout_score per row (unchanged formula), sorts, applies spatial suppression, caps
    at top_n. Only rows that already have a real scout_score enter suppression — an
    access-ineligible or missing-fruiting-data candidate never suppresses a real one,
    since scout_score() already returns None for those cases before this function ever
    sees them as suppression candidates. Returns (ranked, capped_suppressed): ranked
    carries new nearby_suppressed_count/nearby_best_suppressed_score columns (computed
    from the FULL suppressed set attributable to each final ranked row, before the
    max_suppressed_examples cap truncates what's returned in capped_suppressed)."""
    scored = _compute_scored(bucket_gdf, species)
    eligible = scored[scored["scout_score"].notna()].sort_values(
        "scout_score", ascending=False
    )
    retained, suppressed = suppress_nearby_candidates(eligible, min_separation_m)

    ranked = retained.head(top_n).copy()
    ranked["own_id"] = [f"{a}_{b}" for a, b in zip(ranked["id_a"], ranked["id_b"])]

    # Only suppressed rows attributed to a FINAL ranked target matter here — a
    # candidate that was retained by suppress_nearby_candidates but didn't make the
    # top_n cut (never exported at all) shouldn't drag its own suppressed neighbors
    # into the output either.
    relevant_suppressed = suppressed[suppressed["suppressed_by_id"].isin(ranked["own_id"])]

    nearby_counts = relevant_suppressed.groupby("suppressed_by_id").size()
    nearby_best = relevant_suppressed.groupby("suppressed_by_id")["scout_score"].max()
    ranked["nearby_suppressed_count"] = (
        ranked["own_id"].map(nearby_counts).fillna(0).astype(int)
    )
    ranked["nearby_best_suppressed_score"] = ranked["own_id"].map(nearby_best)
    ranked = ranked.drop(columns=["own_id"])

    capped_suppressed = (
        relevant_suppressed.sort_values("scout_score", ascending=False)
        .groupby("suppressed_by_id", group_keys=False)
        .head(max_suppressed_examples)
    )

    return ranked, capped_suppressed
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_scout.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/shroom_fm/scout.py tests/test_scout.py
git commit -m "feat: replace scout_candidates_for_species with per-bucket ranking + global remote_high_value"
```

---

### Task 4: `export_scout_candidates.py` rewrite

**Files:**
- Modify: `scripts/export_scout_candidates.py` (full rewrite)
- Test: `tests/test_export_scout_candidates.py` (new file — this script currently has
  no dedicated test file)

**Interfaces:**
- Consumes: `attach_macrocluster_id` (Task 1), `remote_high_value_for_species` /
  `scout_candidates_for_species_macrocluster` / `MIN_SCOUT_SEPARATION_M` /
  `MAX_SUPPRESSED_EXAMPLES_PER_TARGET` / `MIN_SCOUT_WEATHER_COVERAGE` /
  `weather_coverage_ratio` (Task 2/3, existing), `join_ecotone_access`/
  `join_ecotone_fruiting` (existing, unchanged), `TARGET_SPECIES` (existing,
  `shroom_fm.habitat`), `ESTONIAN_GRID_CRS` (existing, `shroom_fm.eraldis`).
- Produces: `build_scout_candidate_rows(joined_gdf: gpd.GeoDataFrame, target_species:
  list[str]) -> gpd.GeoDataFrame | None` — the testable core, extracted from `main()`
  so this task's regression test can call it directly without touching real files.

- [ ] **Step 1: Write the failing test**

Create `tests/test_export_scout_candidates.py`:

```python
import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

from scripts.export_scout_candidates import OUTPUT_COLUMNS, build_scout_candidate_rows
from shroom_fm.habitat import TARGET_SPECIES


def _fill_other_species(joined_gdf: gpd.GeoDataFrame, primary_species: str) -> gpd.GeoDataFrame:
    """build_scout_candidate_rows loops over every species passed to it, and
    weather_coverage_ratio/scout_candidates_for_species_macrocluster need
    ecotone_score_*/fruiting_modifier_* for whichever species they're called with --
    fill the non-primary species with a neutral, fully-covered value so a
    single-species test doesn't need to fabricate all 5."""
    result = joined_gdf.copy()
    n = len(result)
    for species in TARGET_SPECIES:
        if species == primary_species:
            continue
        result[f"ecotone_score_{species}"] = [1.0] * n
        result[f"fruiting_modifier_{species}"] = [0.5] * n
    return result


def test_build_scout_candidate_rows_ranks_each_macrocluster_independently():
    # Direct regression test for the bug this design fixes: under the OLD global
    # top-N ranking, a macrocluster far from "home" with genuinely strong local
    # candidates got ZERO ranked rows because a nearer macrocluster's candidates
    # dominated the single global cut. Two macroclusters here each have one eligible,
    # well-separated candidate for chanterelle -- both must appear in 'ranked'.
    joined_gdf = gpd.GeoDataFrame(
        {
            "id_a": [1, 3],
            "id_b": [2, 4],
            "macrocluster_id": [10, 20],
            "ecotone_score_chanterelle": [1.5, 0.9],
            "access_modifier": [0.8, 0.8],
            "fruiting_modifier_chanterelle": [1.0, 1.0],
            "scout_eligible": [True, True],
        },
        geometry=[Point(0, 0), Point(50000, 50000)],
        crs="EPSG:3301",
    )
    joined_gdf = _fill_other_species(joined_gdf, "chanterelle")

    result = build_scout_candidate_rows(joined_gdf, ["chanterelle"])

    ranked = result[(result["species"] == "chanterelle") & (result["tier"] == "ranked")]
    assert set(ranked["macrocluster_id"]) == {10, 20}
    assert (ranked["rank_macrocluster"] == 1).all()


def test_build_scout_candidate_rows_returns_none_when_nothing_publishable():
    # A single row with access_modifier=0.0/scout_eligible=False is NOT enough to make
    # this "nothing publishable" -- remote_high_value_for_species deliberately surfaces
    # exactly that kind of row (ecologically scored but access-ineligible) as a real
    # remote_high_value candidate; that's the tier's whole purpose. Genuinely nothing
    # publishable requires zero ecological score data at all: ecotone_score_chanterelle
    # itself must be null, so _compute_scored's initial notna() filter empties out
    # both the ranked and remote_high_value pools before either tier ever runs.
    joined_gdf = gpd.GeoDataFrame(
        {
            "id_a": [1],
            "id_b": [2],
            "macrocluster_id": [10],
            "ecotone_score_chanterelle": [None],
            "access_modifier": [0.0],
            "fruiting_modifier_chanterelle": [None],
            "scout_eligible": [False],
        },
        geometry=[Point(0, 0)],
        crs="EPSG:3301",
    )
    joined_gdf = _fill_other_species(joined_gdf, "chanterelle")

    result = build_scout_candidate_rows(joined_gdf, ["chanterelle"])

    assert result is None


def test_build_scout_candidate_rows_output_has_expected_columns():
    joined_gdf = gpd.GeoDataFrame(
        {
            "id_a": [1],
            "id_b": [2],
            "macrocluster_id": [10],
            "ecotone_score_chanterelle": [1.5],
            "access_modifier": [0.8],
            "access_confidence": ["HIGH_CONFIDENCE"],
            "access_reason": ["100m from road"],
            "nearest_car_road_m": [100.0],
            "fruiting_modifier_chanterelle": [1.0],
            "scout_eligible": [True],
            "weather_data_quality": ["complete"],
            "weather_data_coverage": [1.0],
            "as_of": [None],
            "transition_length_m": [50.0],
            "dominant_species_a": ["MA"],
            "dominant_species_b": ["KU"],
        },
        geometry=[Point(0, 0)],
        crs="EPSG:3301",
    )
    joined_gdf = _fill_other_species(joined_gdf, "chanterelle")

    result = build_scout_candidate_rows(joined_gdf, ["chanterelle"])

    assert list(result.columns) == OUTPUT_COLUMNS


def test_build_scout_candidate_rows_remote_high_value_stays_global_not_per_macrocluster():
    # Two access-ineligible-but-ecologically-strong candidates in DIFFERENT
    # macroclusters -- remote_high_value must rank them against each other globally
    # (both eligible for the same top-N pool), not split per macrocluster.
    joined_gdf = gpd.GeoDataFrame(
        {
            "id_a": [1, 3],
            "id_b": [2, 4],
            "macrocluster_id": [10, 20],
            "ecotone_score_chanterelle": [2.0, 1.5],
            "access_modifier": [0.0, 0.0],
            "fruiting_modifier_chanterelle": [1.0, 1.0],
            "scout_eligible": [False, False],
        },
        geometry=[Point(0, 0), Point(50000, 50000)],
        crs="EPSG:3301",
    )
    joined_gdf = _fill_other_species(joined_gdf, "chanterelle")

    result = build_scout_candidate_rows(joined_gdf, ["chanterelle"])

    remote = result[
        (result["species"] == "chanterelle") & (result["tier"] == "remote_high_value")
    ]
    assert len(remote) == 2
    assert list(remote.sort_values("rank")["ecotone_score"]) == [2.0, 1.5]
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_export_scout_candidates.py -v`
Expected: FAIL — `ImportError`, `build_scout_candidate_rows` doesn't exist yet.

- [ ] **Step 3: Implement**

Replace the full content of `scripts/export_scout_candidates.py` with:

```python
from pathlib import Path

import geopandas as gpd
import pandas as pd

from shroom_fm.eraldis import ESTONIAN_GRID_CRS
from shroom_fm.fruiting import join_ecotone_fruiting
from shroom_fm.habitat import TARGET_SPECIES
from shroom_fm.macrocluster import attach_macrocluster_id
from shroom_fm.scout import (
    MAX_SUPPRESSED_EXAMPLES_PER_TARGET,
    MIN_SCOUT_SEPARATION_M,
    MIN_SCOUT_WEATHER_COVERAGE,
    join_ecotone_access,
    remote_high_value_for_species,
    scout_candidates_for_species_macrocluster,
    weather_coverage_ratio,
)

SCOUT_CANDIDATES_PER_SPECIES_PER_MACROCLUSTER = 10
REMOTE_HIGH_VALUE_TOP_N = 10
ERALDIS_PATH = Path("data/eraldis.geojson")
ECOTONES_PATH = Path("data/ecotones.geojson")
WEATHER_PATH = Path("data/weather_eraldis.geojson")
OUTPUT_PATH = Path("data/scout_candidates.geojson")

OUTPUT_COLUMNS = [
    "species",
    "tier",
    "macrocluster_id",
    "rank_macrocluster",
    "rank",
    "scout_score",
    "ecotone_score",
    "access_modifier",
    "access_confidence",
    "access_reason",
    "nearest_car_road_m",
    "fruiting_score",
    "weather_data_quality",
    "weather_data_coverage",
    "weather_as_of",
    "exclusion_reason",
    "suppressed_by_id",
    "suppression_distance_m",
    "pre_suppression_rank",
    "nearby_suppressed_count",
    "nearby_best_suppressed_score",
    "transition_length_m",
    "dominant_species_a",
    "dominant_species_b",
    "id_a",
    "id_b",
    "geometry",
]


def build_scout_candidate_rows(
    joined_gdf: gpd.GeoDataFrame, target_species: list[str]
) -> gpd.GeoDataFrame | None:
    """joined_gdf must already have macrocluster_id attached (see
    macrocluster.attach_macrocluster_id) and be in a metric CRS. Builds every
    candidate row (ranked, suppressed_by_nearby, remote_high_value) across all species
    and macroclusters. Returns None if nothing at all is publishable (every species
    failed both the ranked and remote_high_value gates everywhere) -- the caller must
    refuse to write output in that case, never publish an empty/untrustworthy file."""
    rows = []
    for species in target_species:
        ratio = weather_coverage_ratio(joined_gdf, species)
        species_has_remote = ratio >= MIN_SCOUT_WEATHER_COVERAGE
        if species_has_remote:
            remote = remote_high_value_for_species(
                joined_gdf, species, REMOTE_HIGH_VALUE_TOP_N
            ).copy()
            remote["species"] = species
            remote["tier"] = "remote_high_value"
            remote["rank"] = range(1, len(remote) + 1)
            if len(remote) > 0:
                # Guard against appending an empty-but-real DataFrame: rows.append(x)
                # makes `rows` non-empty (the LIST gained an element) even if `x` itself
                # has 0 rows, which would defeat the `if not rows: return None`
                # "nothing publishable" check below.
                rows.append(remote)
        else:
            print(
                f"remote_high_value unavailable for {species}: weather coverage "
                f"{ratio:.1%}, required >= {MIN_SCOUT_WEATHER_COVERAGE:.0%}"
            )

        any_macrocluster_ranked = False
        for macrocluster_id in sorted(joined_gdf["macrocluster_id"].unique()):
            bucket = joined_gdf[joined_gdf["macrocluster_id"] == macrocluster_id]
            bucket_ratio = weather_coverage_ratio(bucket, species)
            if bucket_ratio < MIN_SCOUT_WEATHER_COVERAGE:
                continue

            ranked, suppressed = scout_candidates_for_species_macrocluster(
                bucket,
                species,
                SCOUT_CANDIDATES_PER_SPECIES_PER_MACROCLUSTER,
                MIN_SCOUT_SEPARATION_M,
                MAX_SUPPRESSED_EXAMPLES_PER_TARGET,
            )
            if len(ranked) == 0:
                continue
            any_macrocluster_ranked = True

            ranked = ranked.copy()
            ranked["species"] = species
            ranked["tier"] = "ranked"
            ranked["rank_macrocluster"] = range(1, len(ranked) + 1)
            ranked["exclusion_reason"] = None
            rows.append(ranked)

            if len(suppressed) > 0:
                suppressed = suppressed.copy()
                suppressed["species"] = species
                suppressed["tier"] = "suppressed_by_nearby"
                rows.append(suppressed)

        if not species_has_remote and not any_macrocluster_ranked:
            print(f"Scout ranking unavailable for {species}: no eligible buckets")

    if not rows:
        return None

    combined = gpd.GeoDataFrame(pd.concat(rows, ignore_index=True), crs=joined_gdf.crs)
    for col in OUTPUT_COLUMNS:
        if col not in combined.columns:
            combined[col] = None
    return combined[OUTPUT_COLUMNS]


def main() -> None:
    eraldis_gdf = gpd.read_file(ERALDIS_PATH)
    ecotones_gdf = gpd.read_file(ECOTONES_PATH).to_crs(ESTONIAN_GRID_CRS)
    weather_gdf = gpd.read_file(WEATHER_PATH)

    joined = join_ecotone_access(ecotones_gdf, eraldis_gdf)
    joined = join_ecotone_fruiting(joined, weather_gdf)
    joined = attach_macrocluster_id(joined, eraldis_gdf)

    combined = build_scout_candidate_rows(joined, TARGET_SPECIES)

    if combined is None:
        print(
            "Scout ranking unavailable for all species: weather coverage too low. "
            f"No {OUTPUT_PATH} written — refusing to publish an untrustworthy ranking."
        )
        raise SystemExit(1)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    combined.to_file(OUTPUT_PATH, driver="GeoJSON")
    print(f"{len(combined)} scout candidate rows saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_export_scout_candidates.py -v`
Expected: all pass.

- [ ] **Step 5: Run the full test suite**

Run: `uv run pytest tests/ -q`
Expected: all pass, no fallout elsewhere (this task only changes
`export_scout_candidates.py`, which nothing else imports from).

- [ ] **Step 6: Commit**

```bash
git add scripts/export_scout_candidates.py tests/test_export_scout_candidates.py
git commit -m "feat: rank scout candidates per (species, macrocluster) with spatial suppression"
```

---

### Task 5: `rollup_daily_state` — simplify grouping + add `today_weather_status_{species}`

**Files:**
- Modify: `src/shroom_fm/macrocluster.py` (`rollup_daily_state` and its import line —
  line numbers below are from BEFORE Task 1's insertion and will have shifted down by
  Task 1's added function; locate every block by its content/function name, not by the
  cited numbers)
- Test: `tests/test_macrocluster.py` (update 6 existing `rollup_daily_state` tests'
  fixtures — originally lines 320-520, also shifted by Task 1's new tests earlier in
  this same file — plus 3 new tests; locate each by its `def test_...` name)

**Interfaces:**
- Consumes: `scout_candidates_gdf` now assumed to already carry a real `macrocluster_id`
  column per row (attached by Task 4's `attach_macrocluster_id` call at export time) —
  `rollup_daily_state`'s own signature is UNCHANGED, only its internal candidate-grouping
  logic changes.
- Produces: `data/macrocluster_state.geojson` gains `today_weather_status_{species}` per
  species (values: `"ok"` / `"insufficient_coverage"` / `None`).

**Behavior-preservation note:** the spec asks this simplification to be proven
behavior-preserving. Rather than keeping the old `ecotone_macrocluster_id`
re-derivation temporarily side-by-side in a dedicated comparison test (extra code for a
one-time check), this task achieves the same proof more directly: all 6 pre-existing
`rollup_daily_state` tests already assert exact expected values for
`today_ranked_count_*`/`today_top_score_*`/`today_top3_mean_score_*`/
`today_top_target_id_*` (validated correct against the OLD re-derivation code before
this task). Step 1 updates only their `scout_candidates_gdf` fixtures (adding the
`macrocluster_id` column the new code reads directly) — their assertions are left
completely unchanged. All 6 passing unmodified against the new implementation IS the
behavior-preservation proof.

- [ ] **Step 1: Write the failing tests**

First, update the 6 EXISTING `rollup_daily_state` tests in `tests/test_macrocluster.py`
(lines 320-520) — each currently builds `scout_candidates_gdf` WITHOUT a
`macrocluster_id` column (relying on the old re-derivation this task removes). Add
`"macrocluster_id": [...]` to each test's `scout_candidates_gdf` dict, matching that
test's own scenario:

In `test_rollup_daily_state_computes_ranked_stats_for_a_populated_cluster` (line 320),
change:
```python
    scout_candidates_gdf = gpd.GeoDataFrame(
        {
            "species": ["chanterelle", "chanterelle", "chanterelle"],
            "tier": ["ranked", "ranked", "ranked"],
            "scout_score": [0.9, 0.7, 0.5],
            "id_a": [1, 3, 5],
            "id_b": [2, 4, 6],
        },
```
to:
```python
    scout_candidates_gdf = gpd.GeoDataFrame(
        {
            "species": ["chanterelle", "chanterelle", "chanterelle"],
            "tier": ["ranked", "ranked", "ranked"],
            "scout_score": [0.9, 0.7, 0.5],
            "id_a": [1, 3, 5],
            "id_b": [2, 4, 6],
            "macrocluster_id": [10, 10, 10],
        },
```

In `test_rollup_daily_state_top3_mean_with_fewer_than_three_candidates` (line 366),
change:
```python
    scout_candidates_gdf = gpd.GeoDataFrame(
        {
            "species": ["chanterelle"],
            "tier": ["ranked"],
            "scout_score": [0.6],
            "id_a": [1],
            "id_b": [2],
        },
```
to:
```python
    scout_candidates_gdf = gpd.GeoDataFrame(
        {
            "species": ["chanterelle"],
            "tier": ["ranked"],
            "scout_score": [0.6],
            "id_a": [1],
            "id_b": [2],
            "macrocluster_id": [10],
        },
```

In the remaining 4 tests (`test_rollup_daily_state_cluster_with_zero_candidates_gets_none_not_zero`
at line 407, `test_rollup_daily_state_weather_coverage_is_none_for_empty_eligible_pool`
at line 443, `test_rollup_daily_state_weather_coverage_is_none_when_no_ecotones_in_cluster`
at line 486, `test_rollup_daily_state_counts_cross_macrocluster_ecotones` at line 521),
each has an EMPTY `scout_candidates_gdf`:
```python
    scout_candidates_gdf = gpd.GeoDataFrame(
        {"species": [], "tier": [], "scout_score": [], "id_a": [], "id_b": []},
        geometry=[],
        crs="EPSG:4326",
    )
```
change to:
```python
    scout_candidates_gdf = gpd.GeoDataFrame(
        {
            "species": [], "tier": [], "scout_score": [],
            "id_a": [], "id_b": [], "macrocluster_id": [],
        },
        geometry=[],
        crs="EPSG:4326",
    )
```
(all 4 occurrences — find each by its surrounding test function name, not just the
literal text, since this exact empty-dict pattern appears identically in all 4).

Then add 3 new tests after `test_rollup_daily_state_counts_cross_macrocluster_ecotones`
(end of file):

```python
def test_rollup_daily_state_weather_status_is_ok_when_coverage_meets_threshold():
    scout_candidates_gdf = gpd.GeoDataFrame(
        {
            "species": [], "tier": [], "scout_score": [],
            "id_a": [], "id_b": [], "macrocluster_id": [],
        },
        geometry=[],
        crs="EPSG:4326",
    )
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1, 2], "macrocluster_id": [10, 10]},
        geometry=[Point(0, 0)] * 2,
        crs="EPSG:4326",
    )
    macroclusters_gdf = gpd.GeoDataFrame(
        {"macrocluster_id": [10]}, geometry=[Point(0, 0)], crs="EPSG:4326"
    )
    joined_gdf = gpd.GeoDataFrame(
        {
            "id_a": [1],
            "id_b": [2],
            **_joined_columns(1, [1.0], [True], [0.6]),
        },
        geometry=[Point(0, 0)],
        crs="EPSG:4326",
    )

    result = rollup_daily_state(
        scout_candidates_gdf, joined_gdf, eraldis_gdf, macroclusters_gdf, _utc(2026, 8, 20)
    )

    row = result[result["macrocluster_id"] == 10].iloc[0]
    assert row["today_weather_coverage_chanterelle"] == pytest.approx(1.0)
    assert row["today_weather_status_chanterelle"] == "ok"


def test_rollup_daily_state_weather_status_is_insufficient_coverage_below_threshold():
    scout_candidates_gdf = gpd.GeoDataFrame(
        {
            "species": [], "tier": [], "scout_score": [],
            "id_a": [], "id_b": [], "macrocluster_id": [],
        },
        geometry=[],
        crs="EPSG:4326",
    )
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1, 2, 3, 4], "macrocluster_id": [10, 10, 10, 10]},
        geometry=[Point(0, 0)] * 4,
        crs="EPSG:4326",
    )
    macroclusters_gdf = gpd.GeoDataFrame(
        {"macrocluster_id": [10]}, geometry=[Point(0, 0)], crs="EPSG:4326"
    )
    # 4 eligible rows, only 1 has real fruiting data -> coverage 0.25, well below
    # MIN_SCOUT_WEATHER_COVERAGE (0.90).
    joined_gdf = gpd.GeoDataFrame(
        {
            "id_a": [1, 2, 3, 4],
            "id_b": [1, 2, 3, 4],
            **_joined_columns(
                4, [1.0, 1.0, 1.0, 1.0], [True, True, True, True], [0.6, None, None, None]
            ),
        },
        geometry=[Point(0, 0)] * 4,
        crs="EPSG:4326",
    )

    result = rollup_daily_state(
        scout_candidates_gdf, joined_gdf, eraldis_gdf, macroclusters_gdf, _utc(2026, 8, 20)
    )

    row = result[result["macrocluster_id"] == 10].iloc[0]
    assert row["today_weather_coverage_chanterelle"] == pytest.approx(0.25)
    assert row["today_weather_status_chanterelle"] == "insufficient_coverage"


def test_rollup_daily_state_weather_status_is_none_for_empty_eligible_pool():
    scout_candidates_gdf = gpd.GeoDataFrame(
        {
            "species": [], "tier": [], "scout_score": [],
            "id_a": [], "id_b": [], "macrocluster_id": [],
        },
        geometry=[],
        crs="EPSG:4326",
    )
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1, 2], "macrocluster_id": [10, 10]},
        geometry=[Point(0, 0)] * 2,
        crs="EPSG:4326",
    )
    macroclusters_gdf = gpd.GeoDataFrame(
        {"macrocluster_id": [10]}, geometry=[Point(0, 0)], crs="EPSG:4326"
    )
    joined_gdf = gpd.GeoDataFrame(
        {
            "id_a": [1],
            "id_b": [2],
            **_joined_columns(1, [1.0], [False], [0.6]),
        },
        geometry=[Point(0, 0)],
        crs="EPSG:4326",
    )

    result = rollup_daily_state(
        scout_candidates_gdf, joined_gdf, eraldis_gdf, macroclusters_gdf, _utc(2026, 8, 20)
    )

    row = result[result["macrocluster_id"] == 10].iloc[0]
    assert row["today_weather_coverage_chanterelle"] is None
    assert row["today_weather_status_chanterelle"] is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_macrocluster.py -v -k weather_status`
Expected: FAIL — `today_weather_status_chanterelle` column doesn't exist yet. (Also
confirm the 6 fixture-updated tests still pass at this point — they should, since
adding an unused `macrocluster_id` key to a fixture doesn't itself break anything
before Step 3's implementation change.)

- [ ] **Step 3: Implement**

In `src/shroom_fm/macrocluster.py`, change the `shroom_fm.scout` import line
(originally line 12, but locate it by content — it's the only line importing from
`shroom_fm.scout`) from:
```python
from shroom_fm.scout import weather_coverage_ratio
```
to:
```python
from shroom_fm.scout import MIN_SCOUT_WEATHER_COVERAGE, weather_coverage_ratio
```

Inside `rollup_daily_state`, replace this block (originally lines 225-234, locate by
content — it's the first thing the function does with `eraldis_to_macrocluster` and
`scout_candidates_gdf`):
```python
    eraldis_to_macrocluster = dict(zip(eraldis_gdf["id"], eraldis_gdf["macrocluster_id"]))

    # Assign every candidate and every scored ecotone to a macrocluster, counting
    # cross-cluster anomalies as we go (diagnostic, never a hard failure).
    candidate_cluster_ids = []
    for id_a, id_b in zip(scout_candidates_gdf["id_a"], scout_candidates_gdf["id_b"]):
        cluster_id, _ = ecotone_macrocluster_id(id_a, id_b, eraldis_to_macrocluster)
        candidate_cluster_ids.append(cluster_id)
    candidates = scout_candidates_gdf.copy()
    candidates["macrocluster_id"] = candidate_cluster_ids
```
with:
```python
    eraldis_to_macrocluster = dict(zip(eraldis_gdf["id"], eraldis_gdf["macrocluster_id"]))

    # scout_candidates_gdf already carries its own real macrocluster_id per row
    # (attached at export time by export_scout_candidates.py's attach_macrocluster_id
    # call) -- no need to re-derive it here via ecotone_macrocluster_id, unlike
    # joined_gdf below, which still needs it (joined_gdf is the raw ecotone x access x
    # fruiting frame, computed independently in this script, never run through
    # attach_macrocluster_id).
    candidates = scout_candidates_gdf.copy()
```

Further down in the same function, inside the `for species in TARGET_SPECIES:` loop,
replace this block (originally lines 292-297, locate by content — it's the
`if eligible_pool_size == 0:` branch that sets `today_weather_coverage_{species}`):
```python
            if eligible_pool_size == 0:
                record[f"today_weather_coverage_{species}"] = None
            else:
                record[f"today_weather_coverage_{species}"] = weather_coverage_ratio(
                    cluster_joined, species
                )
```
with:
```python
            if eligible_pool_size == 0:
                record[f"today_weather_coverage_{species}"] = None
                record[f"today_weather_status_{species}"] = None
            else:
                coverage = weather_coverage_ratio(cluster_joined, species)
                record[f"today_weather_coverage_{species}"] = coverage
                record[f"today_weather_status_{species}"] = (
                    "ok" if coverage >= MIN_SCOUT_WEATHER_COVERAGE else "insufficient_coverage"
                )
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_macrocluster.py -v`
Expected: all pass (the 6 updated existing tests plus the 3 new ones).

- [ ] **Step 5: Run the full test suite**

Run: `uv run pytest tests/ -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/shroom_fm/macrocluster.py tests/test_macrocluster.py
git commit -m "feat: simplify rollup_daily_state candidate grouping, add today_weather_status_{species}"
```

---

### Task 6: fix stale `scout_candidates_for_species` comment reference + final verification

**Files:**
- Modify: `src/shroom_fm/fruiting.py:251` (stale comment)

**Interfaces:** none — this is a small housekeeping fix plus final real-suite
verification, no new production interfaces.

- [ ] **Step 1: Fix the stale comment**

In `src/shroom_fm/fruiting.py`, find this comment (around line 250-252):
```python
    # Computed once per row (shared across all 5 species) via the zip()-over-
    # columns idiom used elsewhere in this codebase (habitat.score_stands,
    # scout.scout_candidates_for_species) instead of iterrows(), which builds a
    # full pandas Series per row.
```
Change `scout.scout_candidates_for_species` (a function this plan deletes in Task 3) to
`scout.remote_high_value_for_species` (the closest surviving example of the same
zip()-over-columns idiom):
```python
    # Computed once per row (shared across all 5 species) via the zip()-over-
    # columns idiom used elsewhere in this codebase (habitat.score_stands,
    # scout.remote_high_value_for_species) instead of iterrows(), which builds a
    # full pandas Series per row.
```

- [ ] **Step 2: Run the full test suite**

Run: `uv run pytest tests/ -q`
Expected: report the real, actual pass count — do not predict it in advance.

- [ ] **Step 3: Commit**

```bash
git add src/shroom_fm/fruiting.py
git commit -m "docs: fix stale scout_candidates_for_species comment reference"
```
