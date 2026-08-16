# Ecotone Composition-Contrast Scoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Score every pair in `data/adjacency.geojson` by how much the two stands' tree-species composition differs, and buffer the boundary/gap-zone geometry into a scoutable microtype polygon, saving the result to `data/ecotones.geojson`. This is MVP step 6.

**Architecture:** A new `src/shroom_fm/ecotone.py` module holds four pure functions (`composition_fractions`, `composition_contrast`, `dominant_species`, `composition_diversity`) and one orchestrator (`score_ecotones`). A thin `scripts/score_ecotones.py` runner wires it together. No network calls — pure local computation on data already downloaded/enriched by prior branches.

**Tech Stack:** Python (`uv`-managed), `geopandas` (already a dependency), stdlib `math`, `pytest`.

## Global Constraints

- Package layout: reusable logic under `src/shroom_fm/`; thin runners under `scripts/`.
- Reuse `TARGET_SPECIES_CODES` from `src/shroom_fm/enrich.py` (`{"pine": "MA", "spruce": "KU", "birch": "KS", "aspen": "HB"}`) — do not redefine.
- Reuse `ESTONIAN_GRID_CRS` from `src/shroom_fm/eraldis.py` — do not redefine.
- Composition fractions are computed from each stand's full `composition` list (already stored per stand from the prior enrich step), normalized by that stand's **total** `osakaal` across every entry — not assumed to sum to 100. This matters because a multi-canopy-layer stand's raw `pine_share`-style column can exceed 100 (verified on real data: a two-layer stand had `pine_share = 172`), so the fractions must divide by the true total, not a fixed constant.
- Five fraction categories: `pine`, `spruce`, `birch`, `aspen`, `other` (everything not one of the four target species). Always sum to 1.0 (or all-zero for an empty composition).
- `composition_contrast` is total variation distance: `0.5 * Σ|fractions_a[k] - fractions_b[k]|`, range `[0, 1]`. No hard threshold applied anywhere in this plan — every adjacency pair gets scored, none are filtered out. Filtering/ranking is explicitly deferred to later MVP steps (7-8).
- `dominant_species` never collapses a mixed stand to `None` — it returns whichever category (including `"other"`) has the highest share, plus that share value.
- `BUFFER_DISTANCE_M = 40.0` — the midpoint of the original ±30-50m scouting-microtype sketch; an engineering starting point like the adjacency thresholds, not a validated value.
- No retries/fallback/custom exception handling — this step makes no network calls, so there's nothing to retry.
- Testing: `composition_fractions`, `composition_contrast`, `dominant_species`, `composition_diversity` are pure and unit tested. `score_ecotones` is orchestration, not unit tested in isolation — verified by running `scripts/score_ecotones.py` against real local data.
- Output: `data/ecotones.geojson`, gitignored (same reasoning as `data/adjacency.geojson`/`data/eraldis.geojson`).
- Output columns: `id_a, id_b, adjacency_type, transition_length_m, composition_contrast, dominant_species_a, dominant_share_a, diversity_a, dominant_species_b, dominant_share_b, diversity_b, geometry`.

---

### Task 1: `composition_fractions` — normalize a stand's composition

**Files:**
- Create: `src/shroom_fm/ecotone.py`
- Test: `tests/test_ecotone.py`

**Interfaces:**
- Consumes: `TARGET_SPECIES_CODES` from `src/shroom_fm/enrich.py`.
- Produces: `composition_fractions(composition: list[dict]) -> dict[str, float]` returning `{"pine": float, "spruce": float, "birch": float, "aspen": float, "other": float}` summing to 1.0 (or all-zero for empty input). Consumed by Tasks 2-5.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ecotone.py`:

```python
import pytest

from shroom_fm.ecotone import composition_fractions


def test_composition_fractions_normalizes_single_species_stand():
    composition = [{"puuliik_kood": "MA", "osakaal": 100.0}]

    fractions = composition_fractions(composition)

    assert fractions == {"pine": 1.0, "spruce": 0.0, "birch": 0.0, "aspen": 0.0, "other": 0.0}


def test_composition_fractions_normalizes_multi_layer_stand_exceeding_100():
    composition = [
        {"puuliik_kood": "MA", "osakaal": 82.0},
        {"puuliik_kood": "KS", "osakaal": 18.0},
        {"puuliik_kood": "MA", "osakaal": 90.0},
        {"puuliik_kood": "KS", "osakaal": 10.0},
    ]

    fractions = composition_fractions(composition)

    assert fractions["pine"] == pytest.approx(0.86)
    assert fractions["birch"] == pytest.approx(0.14)
    assert fractions["spruce"] == 0.0
    assert fractions["aspen"] == 0.0
    assert fractions["other"] == 0.0
    assert sum(fractions.values()) == pytest.approx(1.0)


def test_composition_fractions_includes_other_category_for_non_target_species():
    composition = [
        {"puuliik_kood": "MA", "osakaal": 60.0},
        {"puuliik_kood": "NU", "osakaal": 40.0},
    ]

    fractions = composition_fractions(composition)

    assert fractions["pine"] == pytest.approx(0.6)
    assert fractions["other"] == pytest.approx(0.4)


def test_composition_fractions_returns_zero_for_empty_composition():
    fractions = composition_fractions([])

    assert fractions == {"pine": 0.0, "spruce": 0.0, "birch": 0.0, "aspen": 0.0, "other": 0.0}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ecotone.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shroom_fm.ecotone'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/shroom_fm/ecotone.py`:

```python
from shroom_fm.enrich import TARGET_SPECIES_CODES


def composition_fractions(composition: list[dict]) -> dict[str, float]:
    categories = list(TARGET_SPECIES_CODES) + ["other"]
    total = sum(entry["osakaal"] for entry in composition)
    if total == 0:
        return {category: 0.0 for category in categories}

    target_sums = {
        name: sum(entry["osakaal"] for entry in composition if entry["puuliik_kood"] == code)
        for name, code in TARGET_SPECIES_CODES.items()
    }
    other = total - sum(target_sums.values())
    raw = {**target_sums, "other": other}
    return {category: raw[category] / total for category in categories}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ecotone.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/shroom_fm/ecotone.py tests/test_ecotone.py
git commit -m "feat: add composition_fractions for normalized stand composition"
```

---

### Task 2: `composition_contrast` — total variation distance

**Files:**
- Modify: `src/shroom_fm/ecotone.py`
- Modify: `tests/test_ecotone.py`

**Interfaces:**
- Consumes: nothing from other tasks (operates on plain dicts).
- Produces: `composition_contrast(fractions_a: dict[str, float], fractions_b: dict[str, float]) -> float` in range `[0, 1]`. Consumed by Task 5 (`score_ecotones`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_ecotone.py`:

```python
from shroom_fm.ecotone import composition_contrast, composition_fractions


def test_composition_contrast_is_zero_for_identical_fractions():
    fractions = {"pine": 0.9, "spruce": 0.1, "birch": 0.0, "aspen": 0.0, "other": 0.0}

    assert composition_contrast(fractions, fractions) == 0.0


def test_composition_contrast_is_one_for_completely_disjoint_fractions():
    fractions_a = {"pine": 1.0, "spruce": 0.0, "birch": 0.0, "aspen": 0.0, "other": 0.0}
    fractions_b = {"pine": 0.0, "spruce": 1.0, "birch": 0.0, "aspen": 0.0, "other": 0.0}

    assert composition_contrast(fractions_a, fractions_b) == pytest.approx(1.0)


def test_composition_contrast_is_small_for_near_identical_fractions():
    fractions_a = {"pine": 0.51, "spruce": 0.49, "birch": 0.0, "aspen": 0.0, "other": 0.0}
    fractions_b = {"pine": 0.49, "spruce": 0.51, "birch": 0.0, "aspen": 0.0, "other": 0.0}

    assert composition_contrast(fractions_a, fractions_b) == pytest.approx(0.02)


def test_composition_contrast_reflects_real_mixed_stand_transition():
    fractions_a = {"pine": 0.9, "spruce": 0.1, "birch": 0.0, "aspen": 0.0, "other": 0.0}
    fractions_b = {"pine": 0.5, "spruce": 0.5, "birch": 0.0, "aspen": 0.0, "other": 0.0}

    assert composition_contrast(fractions_a, fractions_b) == pytest.approx(0.4)
```

Update the existing `from shroom_fm.ecotone import composition_fractions` import line at the top of `tests/test_ecotone.py` to the combined form shown above (remove the old single-name import line; keep the existing `import pytest` line).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ecotone.py -v`
Expected: FAIL with `ImportError: cannot import name 'composition_contrast'`.

- [ ] **Step 3: Write minimal implementation**

Add to `src/shroom_fm/ecotone.py`, after `composition_fractions`:

```python
def composition_contrast(fractions_a: dict[str, float], fractions_b: dict[str, float]) -> float:
    return 0.5 * sum(abs(fractions_a[key] - fractions_b[key]) for key in fractions_a)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ecotone.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add src/shroom_fm/ecotone.py tests/test_ecotone.py
git commit -m "feat: add composition_contrast total variation distance"
```

---

### Task 3: `dominant_species` — interpretable label, never None

**Files:**
- Modify: `src/shroom_fm/ecotone.py`
- Modify: `tests/test_ecotone.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `dominant_species(fractions: dict[str, float]) -> tuple[str, float]`. Consumed by Task 5 (`score_ecotones`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_ecotone.py`:

```python
from shroom_fm.ecotone import composition_contrast, composition_fractions, dominant_species


def test_dominant_species_returns_highest_share_category():
    fractions = {"pine": 0.86, "spruce": 0.0, "birch": 0.14, "aspen": 0.0, "other": 0.0}

    name, share = dominant_species(fractions)

    assert name == "pine"
    assert share == pytest.approx(0.86)


def test_dominant_species_can_return_other_for_mixed_non_target_stand():
    fractions = {"pine": 0.1, "spruce": 0.1, "birch": 0.1, "aspen": 0.1, "other": 0.6}

    name, share = dominant_species(fractions)

    assert name == "other"
    assert share == pytest.approx(0.6)
```

Update the import line at the top of `tests/test_ecotone.py` to include `dominant_species` alongside the existing `composition_contrast, composition_fractions` (combined single import line, as shown above).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ecotone.py -v`
Expected: FAIL with `ImportError: cannot import name 'dominant_species'`.

- [ ] **Step 3: Write minimal implementation**

Add to `src/shroom_fm/ecotone.py`, after `composition_contrast`:

```python
def dominant_species(fractions: dict[str, float]) -> tuple[str, float]:
    return max(fractions.items(), key=lambda item: item[1])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ecotone.py -v`
Expected: PASS (10 passed)

- [ ] **Step 5: Commit**

```bash
git add src/shroom_fm/ecotone.py tests/test_ecotone.py
git commit -m "feat: add dominant_species interpretable label"
```

---

### Task 4: `composition_diversity` — Shannon entropy mixedness signal

**Files:**
- Modify: `src/shroom_fm/ecotone.py`
- Modify: `tests/test_ecotone.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `composition_diversity(fractions: dict[str, float]) -> float`, `>= 0`. Consumed by Task 5 (`score_ecotones`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_ecotone.py`:

```python
from shroom_fm.ecotone import (
    composition_contrast,
    composition_diversity,
    composition_fractions,
    dominant_species,
)


def test_composition_diversity_is_zero_for_monoculture():
    fractions = {"pine": 1.0, "spruce": 0.0, "birch": 0.0, "aspen": 0.0, "other": 0.0}

    assert composition_diversity(fractions) == pytest.approx(0.0)


def test_composition_diversity_matches_known_value_for_real_stand():
    fractions = {"pine": 0.0, "spruce": 0.95, "birch": 0.05, "aspen": 0.0, "other": 0.0}

    assert composition_diversity(fractions) == pytest.approx(0.1985152433458726)


def test_composition_diversity_is_higher_for_more_evenly_mixed_stand():
    monoculture = {"pine": 1.0, "spruce": 0.0, "birch": 0.0, "aspen": 0.0, "other": 0.0}
    evenly_mixed = {"pine": 0.25, "spruce": 0.25, "birch": 0.25, "aspen": 0.25, "other": 0.0}

    assert composition_diversity(evenly_mixed) > composition_diversity(monoculture)
```

Update the import line at the top of `tests/test_ecotone.py` to the combined multi-line form shown above (add `composition_diversity` alongside the existing three names).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ecotone.py -v`
Expected: FAIL with `ImportError: cannot import name 'composition_diversity'`.

- [ ] **Step 3: Write minimal implementation**

Add to the top of `src/shroom_fm/ecotone.py`:

```python
import math
```

Add after `dominant_species`:

```python
def composition_diversity(fractions: dict[str, float]) -> float:
    return -sum(p * math.log(p) for p in fractions.values() if p > 0)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ecotone.py -v`
Expected: PASS (13 passed)

- [ ] **Step 5: Commit**

```bash
git add src/shroom_fm/ecotone.py tests/test_ecotone.py
git commit -m "feat: add composition_diversity Shannon entropy signal"
```

---

### Task 5: `score_ecotones` — orchestrator

**Files:**
- Modify: `src/shroom_fm/ecotone.py`

**Interfaces:**
- Consumes: `composition_fractions`, `composition_contrast`, `dominant_species`, `composition_diversity` (Tasks 1-4); `ESTONIAN_GRID_CRS` from `src/shroom_fm/eraldis.py`.
- Produces: `score_ecotones(adjacency_gdf: gpd.GeoDataFrame, eraldis_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame`. Consumed by Task 6 (`scripts/score_ecotones.py`).

No test for this step (per Global Constraints: orchestration, verified live in Task 6, not unit tested).

- [ ] **Step 1: Add the function**

Add to the top of `src/shroom_fm/ecotone.py`:

```python
import geopandas as gpd

from shroom_fm.eraldis import ESTONIAN_GRID_CRS
```

Add after `composition_diversity`:

```python
BUFFER_DISTANCE_M = 40.0

ECOTONE_COLUMNS = [
    "id_a",
    "id_b",
    "adjacency_type",
    "transition_length_m",
    "composition_contrast",
    "dominant_species_a",
    "dominant_share_a",
    "diversity_a",
    "dominant_species_b",
    "dominant_share_b",
    "diversity_b",
    "geometry",
]


def score_ecotones(adjacency_gdf: gpd.GeoDataFrame, eraldis_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    original_crs = adjacency_gdf.crs
    projected_adjacency = adjacency_gdf.to_crs(ESTONIAN_GRID_CRS)
    composition_by_id = dict(zip(eraldis_gdf["id"], eraldis_gdf["composition"]))

    records = []
    for _, row in projected_adjacency.iterrows():
        composition_a = composition_by_id.get(row["id_a"], [])
        composition_b = composition_by_id.get(row["id_b"], [])
        fractions_a = composition_fractions(composition_a)
        fractions_b = composition_fractions(composition_b)

        dominant_a, share_a = dominant_species(fractions_a)
        dominant_b, share_b = dominant_species(fractions_b)

        records.append(
            {
                "id_a": row["id_a"],
                "id_b": row["id_b"],
                "adjacency_type": row["adjacency_type"],
                "transition_length_m": row["transition_length_m"],
                "composition_contrast": composition_contrast(fractions_a, fractions_b),
                "dominant_species_a": dominant_a,
                "dominant_share_a": share_a,
                "diversity_a": composition_diversity(fractions_a),
                "dominant_species_b": dominant_b,
                "dominant_share_b": share_b,
                "diversity_b": composition_diversity(fractions_b),
                "geometry": row["geometry"].buffer(BUFFER_DISTANCE_M),
            }
        )

    if not records:
        return gpd.GeoDataFrame(columns=ECOTONE_COLUMNS, geometry="geometry", crs=original_crs)

    ecotones = gpd.GeoDataFrame(records, columns=ECOTONE_COLUMNS, crs=ESTONIAN_GRID_CRS)
    return ecotones.to_crs(original_crs)
```

- [ ] **Step 2: Run the existing test suite to confirm nothing broke**

Run: `uv run pytest tests/test_ecotone.py -v`
Expected: PASS (13 passed) — this step only adds new code, it doesn't change the four pure functions.

- [ ] **Step 3: Sanity-check with a small synthetic pair**

Run:
```bash
uv run python -c "
import geopandas as gpd
from shapely.geometry import box, LineString
from shroom_fm.ecotone import score_ecotones

adjacency = gpd.GeoDataFrame(
    {'id_a': [1], 'id_b': [2], 'adjacency_type': ['touching'], 'transition_length_m': [100.0]},
    geometry=[LineString([(100, 0), (100, 100)])],
    crs='EPSG:3301',
).to_crs('EPSG:4326')

eraldis = gpd.GeoDataFrame(
    {'id': [1, 2], 'composition': [
        [{'puuliik_kood': 'MA', 'osakaal': 90.0}, {'puuliik_kood': 'KU', 'osakaal': 10.0}],
        [{'puuliik_kood': 'MA', 'osakaal': 50.0}, {'puuliik_kood': 'KU', 'osakaal': 50.0}],
    ]},
    geometry=[box(0, 0, 100, 100), box(100, 0, 200, 100)],
    crs='EPSG:3301',
).to_crs('EPSG:4326')

result = score_ecotones(adjacency, eraldis)
print('contrast:', result.iloc[0]['composition_contrast'])
print('dominant_a:', result.iloc[0]['dominant_species_a'], result.iloc[0]['dominant_share_a'])
print('dominant_b:', result.iloc[0]['dominant_species_b'], result.iloc[0]['dominant_share_b'])
"
```
Expected: `contrast: 0.4`, `dominant_a: pine 0.9`, `dominant_b: pine 0.5` (verified during design: a 90/10 pine/spruce stand next to a 50/50 pine/spruce stand produces exactly `0.4` contrast).

- [ ] **Step 4: Commit**

```bash
git add src/shroom_fm/ecotone.py
git commit -m "feat: add score_ecotones orchestrator"
```

---

### Task 6: `scripts/score_ecotones.py` — runnable scoring script

**Files:**
- Create: `scripts/score_ecotones.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `score_ecotones` from `src/shroom_fm/ecotone.py` (Task 5).
- Produces: nothing consumed by other tasks — this is the pipeline's end-user entry point for this step.

- [ ] **Step 1: Gitignore the output file**

Add to `.gitignore`, in the same "shroom-fm local output" section as the existing `data/eraldis.geojson`/`data/adjacency.geojson` entries:

```
data/ecotones.geojson
```

- [ ] **Step 2: Write the runner script**

Create `scripts/score_ecotones.py`:

```python
from pathlib import Path

import geopandas as gpd

from shroom_fm.ecotone import score_ecotones

ADJACENCY_PATH = Path("data/adjacency.geojson")
ERALDIS_PATH = Path("data/eraldis.geojson")
OUTPUT_PATH = Path("data/ecotones.geojson")


def main() -> None:
    adjacency_gdf = gpd.read_file(ADJACENCY_PATH)
    eraldis_gdf = gpd.read_file(ERALDIS_PATH)

    ecotones = score_ecotones(adjacency_gdf, eraldis_gdf)
    ecotones.to_file(OUTPUT_PATH, driver="GeoJSON")

    print(f"{len(ecotones)} ecotone pairs scored, saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run it against your real local data**

This step requires `data/adjacency.geojson` and `data/eraldis.geojson` to already exist (from prior branches). Both are gitignored, so they may or may not be present depending on the checkout. If either is missing, produce them first using the same local-fixture pattern from prior plans (`scripts/download_eraldis.py` at a reduced radius, then `scripts/enrich_eraldis.py`, then `scripts/compute_adjacency.py`) before running this step.

Run:
```bash
uv run scripts/score_ecotones.py
```

Expected: prints `N ecotone pairs scored, saved to data/ecotones.geojson` where `N` matches the row count of `data/adjacency.geojson` (every pair is scored, none filtered).

- [ ] **Step 4: Verify the output**

Run:
```bash
uv run python -c "
import geopandas as gpd
gdf = gpd.read_file('data/ecotones.geojson')
print(len(gdf), 'rows')
print(gdf.columns.tolist())
print(gdf['composition_contrast'].describe())
print(gdf.sort_values('composition_contrast', ascending=False).iloc[0][['id_a', 'id_b', 'composition_contrast', 'dominant_species_a', 'dominant_species_b']])
"
```
Expected: `composition_contrast` values in `[0, 1]` with real variation (not all zero or all one), and the highest-contrast row showing two genuinely different `dominant_species_a`/`dominant_species_b` values.

- [ ] **Step 5: Run the full test suite one more time**

Run: `uv run pytest -v`
Expected: PASS (26 passed: 2 in `test_wfs.py`, 2 in `test_config.py`, 2 in `test_eraldis.py`, 3 in `test_enrich.py`, 4 in `test_adjacency.py`, 13 in `test_ecotone.py`).

- [ ] **Step 6: Commit**

```bash
git add scripts/score_ecotones.py .gitignore
git commit -m "feat: add score_ecotones runner script"
```

(Do not `git add data/ecotones.geojson` — it's gitignored per Step 1; confirm via `git status` that it doesn't appear as a trackable change.)

---

## Post-plan note

This plan only covers MVP step 6 (ecotone composition-contrast scoring). Step 7 (`HabitatScore`, combining `composition_contrast` with other static features) and step 8 (exporting the top-N results) are separate follow-up work, not part of this plan. They will consume `data/ecotones.geojson` directly.
