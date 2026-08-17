# ScoutScore v0 + Top-N Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** MVP step 8 — export the top 5 scoutable ecotone candidates per target species to
`data/scout_candidates.geojson`, using a "ScoutScore v0" built only from the currently-built
`EcotoneScore` and `AccessScore` (weather/history/mosaic bonus remain explicitly absent, not
faked).

**Architecture:** New `src/shroom_fm/scout.py` joins each ecotone to both its stands'
`AccessScore` fields (picking the better-served stand), computes `scout_score` only when
`scout_eligible`, and splits each species' candidates into a `ranked` tier and a
`remote_high_value` tier (ecologically scored but access-excluded, never deleted). New
`scripts/export_scout_candidates.py` wires this into a single combined GeoJSON export.

**Tech Stack:** Python, GeoPandas, pandas, pytest — same as the rest of the project. No new
dependencies.

## Global Constraints

- Result unit is **ecotones only** (from `data/ecotones.geojson`) — no stand-interior
  candidates in this export.
- Ranking is **per species** — five independent top-N lists, never a cross-species
  aggregate.
- `access_modifier = max(access_score_a, access_score_b)` — the better-served stand's
  `access_score`, `access_confidence`, `access_reason`, and `nearest_car_road_m` are all
  taken together from that same winning stand (never a blend of both sides).
- `AccessScore` itself (`access.py`) is never modified, floored, or hard-zeroed by this
  feature.
- `MAX_WALK_FROM_CAR_M = ACCESS_DISTANCE_CAP_M` (reused from `access.py`, not a new
  independent constant). `scout_eligible` is `True` only when the winning stand's
  `nearest_car_road_m` is not `None` and `<= MAX_WALK_FROM_CAR_M`.
- `scout_score = ecotone_score * access_modifier` only when `scout_eligible`; otherwise
  `scout_score = None` (never `0`) and the row carries `exclusion_reason =
  "REMOTE_BY_V1_ACCESS_PROXY"`.
- Two tiers per species, each capped at `TOP_N` (default `5`) **independently** (a species
  can contribute up to `2 * TOP_N` rows total): `ranked` (sorted by `scout_score` desc) and
  `remote_high_value` (sorted by `ecotone_score` desc, only rows with a real
  `ecotone_score_<species>` but `scout_score is None`).
- `NaN` from unmatched stand references (e.g. a missing `id_a`/`id_b` lookup) must
  normalize to `0.0` (for `access_score`) or `None` (for `access_confidence`/
  `access_reason`/`nearest_car_road_m`) — never propagate as `NaN` into the output.
- `TARGET_SPECIES` is imported from `habitat.py` (already defines the 5-species list), not
  redefined.
- `scout.py`'s functions (`join_ecotone_access`, `scout_score`,
  `scout_candidates_for_species`) are pure/data functions — fully unit-tested.
  `scripts/export_scout_candidates.py` is thin, untested wiring, matching every other
  runner script in this project.

---

### Task 1: `scout.py` and the export script

**Files:**
- Create: `src/shroom_fm/scout.py`
- Create: `scripts/export_scout_candidates.py`
- Test: `tests/test_scout.py` (new file)

**Interfaces:**
- Consumes: `ACCESS_DISTANCE_CAP_M` from `src/shroom_fm/access.py` (existing). `TARGET_SPECIES`
  from `src/shroom_fm/habitat.py` (existing).
- Produces: `join_ecotone_access(ecotones_gdf, eraldis_gdf) -> gpd.GeoDataFrame`,
  `scout_score(ecotone_score: float | None, access_modifier: float | None, eligible: bool)
  -> float | None`, `scout_candidates_for_species(joined_gdf, species: str, top_n: int) ->
  tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]` in `src/shroom_fm/scout.py`. No other task in
  this plan depends on this (only task).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scout.py`:

```python
import geopandas as gpd
import pytest
from shapely.geometry import Point

from shroom_fm.scout import (
    REMOTE_EXCLUSION_REASON,
    join_ecotone_access,
    scout_candidates_for_species,
    scout_score,
)


def test_join_ecotone_access_uses_better_served_stand():
    ecotones_gdf = gpd.GeoDataFrame(
        {"id_a": [1], "id_b": [2]},
        geometry=[Point(0, 0)],
        crs="EPSG:3301",
    )
    eraldis_gdf = gpd.GeoDataFrame(
        {
            "id": [1, 2],
            "access_score": [0.8, 0.3],
            "access_confidence": ["HIGH_CONFIDENCE", "NORMAL"],
            "access_reason": [
                "100m from Kõrvalmaantee-class road",
                "800m from Muu tee-class road",
            ],
            "nearest_car_road_m": [100.0, 800.0],
        },
        geometry=[Point(0, 0), Point(10, 10)],
        crs="EPSG:3301",
    )

    result = join_ecotone_access(ecotones_gdf, eraldis_gdf)

    assert result.loc[0, "access_modifier"] == pytest.approx(0.8)
    assert result.loc[0, "access_confidence"] == "HIGH_CONFIDENCE"
    assert result.loc[0, "access_reason"] == "100m from Kõrvalmaantee-class road"
    assert result.loc[0, "nearest_car_road_m"] == pytest.approx(100.0)
    assert result.loc[0, "scout_eligible"] is True


def test_join_ecotone_access_ineligible_when_winning_side_beyond_cap():
    ecotones_gdf = gpd.GeoDataFrame(
        {"id_a": [1], "id_b": [2]},
        geometry=[Point(0, 0)],
        crs="EPSG:3301",
    )
    eraldis_gdf = gpd.GeoDataFrame(
        {
            "id": [1, 2],
            "access_score": [0.0, 0.0],
            "access_confidence": [None, None],
            "access_reason": [
                "no car-accessible road within 1500m",
                "no car-accessible road within 1500m",
            ],
            "nearest_car_road_m": [None, None],
        },
        geometry=[Point(0, 0), Point(10, 10)],
        crs="EPSG:3301",
    )

    result = join_ecotone_access(ecotones_gdf, eraldis_gdf)

    assert result.loc[0, "access_modifier"] == 0.0
    assert result.loc[0, "nearest_car_road_m"] is None
    assert result.loc[0, "scout_eligible"] is False


def test_join_ecotone_access_normalizes_missing_stand_reference_to_zero_access():
    ecotones_gdf = gpd.GeoDataFrame(
        {"id_a": [1], "id_b": [999]},
        geometry=[Point(0, 0)],
        crs="EPSG:3301",
    )
    eraldis_gdf = gpd.GeoDataFrame(
        {
            "id": [1],
            "access_score": [0.2],
            "access_confidence": ["CONDITIONAL"],
            "access_reason": ["1200m from Muu tee-class road"],
            "nearest_car_road_m": [1200.0],
        },
        geometry=[Point(0, 0)],
        crs="EPSG:3301",
    )

    result = join_ecotone_access(ecotones_gdf, eraldis_gdf)

    assert result.loc[0, "access_modifier"] == pytest.approx(0.2)
    assert result.loc[0, "nearest_car_road_m"] == pytest.approx(1200.0)
    assert result.loc[0, "scout_eligible"] is True


def test_scout_score_multiplies_when_eligible():
    assert scout_score(1.2, 0.5, True) == pytest.approx(0.6)


def test_scout_score_is_none_when_ineligible():
    assert scout_score(1.2, 0.5, False) is None


def test_scout_score_is_none_when_ecotone_score_missing():
    assert scout_score(None, 0.5, True) is None


def test_scout_score_is_none_when_access_modifier_missing():
    assert scout_score(1.0, None, True) is None


def test_scout_candidates_for_species_splits_and_sorts_tiers():
    joined_gdf = gpd.GeoDataFrame(
        {
            "ecotone_score_chanterelle": [1.5, 1.2, 0.9, 0.6, None],
            "access_modifier": [0.8, 0.5, 0.9, 0.1, 0.9],
            "scout_eligible": [True, True, False, True, True],
        },
        geometry=[Point(i, 0) for i in range(5)],
        crs="EPSG:3301",
    )

    ranked, remote = scout_candidates_for_species(joined_gdf, "chanterelle", top_n=5)

    assert [round(v, 4) for v in ranked["scout_score"]] == [1.2, 0.6, 0.06]
    assert len(remote) == 1
    assert remote.iloc[0]["ecotone_score"] == pytest.approx(0.9)
    assert remote.iloc[0]["exclusion_reason"] == REMOTE_EXCLUSION_REASON


def test_scout_candidates_for_species_caps_each_tier_independently():
    joined_gdf = gpd.GeoDataFrame(
        {
            "ecotone_score_chanterelle": [3.0, 2.0, 1.0],
            "access_modifier": [1.0, 1.0, 1.0],
            "scout_eligible": [True, True, True],
        },
        geometry=[Point(i, 0) for i in range(3)],
        crs="EPSG:3301",
    )

    ranked, remote = scout_candidates_for_species(joined_gdf, "chanterelle", top_n=2)

    assert len(ranked) == 2
    assert list(ranked["scout_score"]) == [3.0, 2.0]
    assert len(remote) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_scout.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shroom_fm.scout'`.

- [ ] **Step 3: Write the implementation**

Create `src/shroom_fm/scout.py`:

```python
import geopandas as gpd
import pandas as pd

from shroom_fm.access import ACCESS_DISTANCE_CAP_M

MAX_WALK_FROM_CAR_M = ACCESS_DISTANCE_CAP_M
REMOTE_EXCLUSION_REASON = "REMOTE_BY_V1_ACCESS_PROXY"

ACCESS_COLUMNS = ["access_score", "access_confidence", "access_reason", "nearest_car_road_m"]


def join_ecotone_access(
    ecotones_gdf: gpd.GeoDataFrame, eraldis_gdf: gpd.GeoDataFrame
) -> gpd.GeoDataFrame:
    access_by_id = eraldis_gdf.set_index("id")[ACCESS_COLUMNS]
    access_a = access_by_id.reindex(ecotones_gdf["id_a"]).reset_index(drop=True)
    access_b = access_by_id.reindex(ecotones_gdf["id_b"]).reset_index(drop=True)

    result = ecotones_gdf.copy().reset_index(drop=True)

    access_modifier = []
    access_confidence = []
    access_reason = []
    nearest_car_road_m = []
    scout_eligible = []

    for a, b in zip(access_a.itertuples(index=False), access_b.itertuples(index=False)):
        score_a = 0.0 if pd.isna(a.access_score) else a.access_score
        score_b = 0.0 if pd.isna(b.access_score) else b.access_score
        winner = a if score_a >= score_b else b
        winner_score = score_a if score_a >= score_b else score_b
        winner_distance = (
            None if pd.isna(winner.nearest_car_road_m) else winner.nearest_car_road_m
        )

        access_modifier.append(winner_score)
        access_confidence.append(
            None if pd.isna(winner.access_confidence) else winner.access_confidence
        )
        access_reason.append(None if pd.isna(winner.access_reason) else winner.access_reason)
        nearest_car_road_m.append(winner_distance)
        scout_eligible.append(
            winner_distance is not None and winner_distance <= MAX_WALK_FROM_CAR_M
        )

    result["access_modifier"] = access_modifier
    result["access_confidence"] = access_confidence
    result["access_reason"] = access_reason
    result["nearest_car_road_m"] = nearest_car_road_m
    result["scout_eligible"] = scout_eligible

    return result


def scout_score(
    ecotone_score: float | None, access_modifier: float | None, eligible: bool
) -> float | None:
    if not eligible or ecotone_score is None or access_modifier is None:
        return None
    return ecotone_score * access_modifier


def scout_candidates_for_species(
    joined_gdf: gpd.GeoDataFrame, species: str, top_n: int
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    ecotone_col = f"ecotone_score_{species}"
    scored = joined_gdf[joined_gdf[ecotone_col].notna()].copy()
    scored["ecotone_score"] = scored[ecotone_col]
    scored["scout_score"] = [
        scout_score(ecotone_score_value, access_modifier_value, eligible)
        for ecotone_score_value, access_modifier_value, eligible in zip(
            scored["ecotone_score"], scored["access_modifier"], scored["scout_eligible"]
        )
    ]

    ranked = (
        scored[scored["scout_score"].notna()]
        .sort_values("scout_score", ascending=False)
        .head(top_n)
    )
    remote = (
        scored[scored["scout_score"].isna()]
        .assign(exclusion_reason=REMOTE_EXCLUSION_REASON)
        .sort_values("ecotone_score", ascending=False)
        .head(top_n)
    )
    return ranked, remote
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_scout.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Run the full test suite to confirm no regressions**

Run: `uv run pytest tests/ -v`
Expected: PASS (125 tests: 116 existing + 9 new)

- [ ] **Step 6: Write the export script**

Create `scripts/export_scout_candidates.py`:

```python
from pathlib import Path

import geopandas as gpd
import pandas as pd

from shroom_fm.habitat import TARGET_SPECIES
from shroom_fm.scout import join_ecotone_access, scout_candidates_for_species

TOP_N = 5
ERALDIS_PATH = Path("data/eraldis.geojson")
ECOTONES_PATH = Path("data/ecotones.geojson")
OUTPUT_PATH = Path("data/scout_candidates.geojson")

OUTPUT_COLUMNS = [
    "species",
    "tier",
    "rank",
    "scout_score",
    "ecotone_score",
    "access_modifier",
    "access_confidence",
    "access_reason",
    "nearest_car_road_m",
    "exclusion_reason",
    "transition_length_m",
    "dominant_species_a",
    "dominant_species_b",
    "id_a",
    "id_b",
    "geometry",
]


def main() -> None:
    eraldis_gdf = gpd.read_file(ERALDIS_PATH)
    ecotones_gdf = gpd.read_file(ECOTONES_PATH)
    joined = join_ecotone_access(ecotones_gdf, eraldis_gdf)

    rows = []
    for species in TARGET_SPECIES:
        ranked, remote = scout_candidates_for_species(joined, species, TOP_N)

        ranked = ranked.copy()
        ranked["species"] = species
        ranked["tier"] = "ranked"
        ranked["rank"] = range(1, len(ranked) + 1)
        ranked["exclusion_reason"] = None

        remote = remote.copy()
        remote["species"] = species
        remote["tier"] = "remote_high_value"
        remote["rank"] = range(1, len(remote) + 1)

        rows.append(ranked)
        rows.append(remote)

    combined = gpd.GeoDataFrame(pd.concat(rows, ignore_index=True), crs=ecotones_gdf.crs)
    combined = combined[OUTPUT_COLUMNS]

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    combined.to_file(OUTPUT_PATH, driver="GeoJSON")
    print(
        f"{len(combined)} scout candidates across {len(TARGET_SPECIES)} species "
        f"saved to {OUTPUT_PATH}"
    )


if __name__ == "__main__":
    main()
```

This has no dedicated test — matches this project's established precedent for runner
scripts (thin wiring, verified by the underlying library functions' tests plus a real run).

- [ ] **Step 7: Manually verify against real data if available**

If `data/eraldis.geojson` and `data/ecotones.geojson` already exist locally (from a prior
real pipeline run, both with their score columns populated), run the real script:

```bash
uv run python scripts/export_scout_candidates.py
```

Expected: prints a count line (e.g. "N scout candidates across 5 species saved to
data/scout_candidates.geojson"), and `data/scout_candidates.geojson` exists. Read it back
and sanity-check: every row has a `species` in the 5 target species, `tier` is either
`"ranked"` or `"remote_high_value"`, `ranked` rows have a non-null `scout_score` and null
`exclusion_reason`, `remote_high_value` rows have a null `scout_score` and
`exclusion_reason == "REMOTE_BY_V1_ACCESS_PROXY"`, and no species contributes more than
`2 * TOP_N` (10) total rows. If these files don't exist in your environment, skip this step
and note it clearly in your report — the unit tests in Steps 4/5 are the primary
verification for this task.

- [ ] **Step 8: Commit**

```bash
git add src/shroom_fm/scout.py scripts/export_scout_candidates.py tests/test_scout.py
git commit -m "feat: export top-N scout candidates per species to GeoJSON"
```

---

## Self-Review Notes

- **Spec coverage:** the spec's ecotone-only result unit, per-species ranking, `max()`
  access-combine rule, `scout_eligible`/no-floor design, two-tier
  `ranked`/`remote_high_value` split (each capped independently at `TOP_N`), NaN-to-`None`/
  `0.0` normalization, and long-format combined GeoJSON export are all covered by this
  single task.
- **Placeholder scan:** none found — every step has complete, runnable code. The spec's own
  self-review already caught and fixed one placeholder (the `f"ecotone_score_{{species}}"`
  templated-but-unresolved output column) before this plan was written; this plan's
  `OUTPUT_COLUMNS` uses the already-resolved `"ecotone_score"` name throughout, consistent
  with `scout_candidates_for_species` copying the species-specific column into that generic
  name before returning.
- **Type consistency:** `join_ecotone_access(ecotones_gdf, eraldis_gdf) -> gpd.GeoDataFrame`,
  `scout_score(ecotone_score, access_modifier, eligible) -> float | None`, and
  `scout_candidates_for_species(joined_gdf, species, top_n) -> tuple[gpd.GeoDataFrame,
  gpd.GeoDataFrame]` signatures are identical across this plan's tests, implementation, and
  the export script's call sites. `REMOTE_EXCLUSION_REASON` is imported and used consistently
  between the test file and the implementation.
- **Verified against real data before writing this plan:** `data/eraldis.geojson` has a real
  `id` column (`int32`) and `data/ecotones.geojson` has real `id_a`/`id_b` columns (also
  `int32`) — the join's dtype compatibility was confirmed live, not assumed.
  `pd.DataFrame.reindex()` called with a `pandas.Series` argument (as in
  `access_by_id.reindex(ecotones_gdf["id_a"])`) was confirmed live to reindex by the
  Series' *values* in the Series' own row order, unaffected by either DataFrame's index
  labels — the exact behavior this task's `join_ecotone_access` depends on.
