# HabitatScore Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement MVP step 7 (`HabitatScore`) — per-species `StandHabitatScore` for every
stand and per-species `EcotoneScore` for every adjacent-pair boundary, for the five target
species (kitsemampel, chanterelle, aspen bolete, birch bolete, porcini).

**Architecture:** New module `src/shroom_fm/habitat.py` with pure scoring functions plus two
orchestrators (`score_stands`, `score_ecotone_habitat`), following `ecotone.py`'s existing
pattern. Two new runner scripts extend `data/eraldis.geojson` and `data/ecotones.geojson` in
place with new columns, in that order (ecotone scoring depends on stand scoring having
already run).

**Tech Stack:** Python, GeoPandas, pytest — same as the rest of the project. No new
dependencies.

## Global Constraints

- `StandHabitatScore = host_score(species, fractions) * site_modifier(site_type_score(species, kasvukoht_kood))`, where `host_score = max` over host-tree contributions (never `sum`), and `site_modifier(s) = 0.5 + 0.5 * s` (`SITE_MODIFIER_FLOOR = 0.5`).
- `EcotoneScore = base_habitat(score_a, score_b) * (1 + exploration_bonus)`, where `base_habitat = 0.7 * max(score_a, score_b) + 0.3 * min(score_a, score_b)`.
- `exploration_bonus = EXPLORATION_BONUS_CAP * exploration_signal` where `EXPLORATION_BONUS_CAP = 0.3`, `exploration_signal = sum(value * nominal_weight for available terms)` — weights are **never** renormalized when a term is missing (a single available 0.10-weight term must contribute at most `0.3 * 0.10 = 0.03`, not the full 0.3 cap).
- Exploration term weights: `composition_contrast=0.35`, `kasvukoht_dimension=0.25`, `age_contrast=0.20`, `drainage_changed=0.10`, `transition_length=0.10` (`TRANSITION_LENGTH_CAP_M=200.0`).
- `kasvukoht_moisture_contrast` and `age_contrast` from `ecotone.py` are already normalized to `[0,1]` — no additional per-field cap.
- **None/NaN-propagation, never fabricate:** `host_score` uses `fractions=None` (not a computed value) to signal missing composition data — caller's responsibility, mirroring `ecotone.py`'s `fractions_a = composition_fractions(composition_a) if composition_a else None` pattern. `site_type_score` returns `None` for unmapped/special-hydrology kasvukoht codes. `stand_habitat_score`/`base_habitat`/`ecotone_score` propagate `None` rather than guessing.
- **NaN is not None:** `data/ecotones.geojson`'s `composition_contrast` column uses `float("nan")` (not `None`) for missing pairs (verified live: 282/23087 rows). Any code reading it must check `math.isnan(value)` in addition to `value is None`.
- **GeoJSON bool+`None` round-trip quirk (documented in `CLAUDE.md`):** `kasvukoht_group_changed` round-trips through `data/ecotones.geojson` as the **string** `"True"`/`"False"`/`nan`, not a real bool, because it mixes bool and `None`. Code reading it back from the saved file must normalize it, not compare with `is True`/`is False` directly.
- Value ranges: `StandHabitatScore ∈ [0,1]`, `ExplorationBonus ∈ [0,0.3]`, `EcotoneScore ∈ [0,1.3]` — `EcotoneScore` is a ranking score and must **not** be clamped to `[0,1]`.
- No network calls anywhere in this plan.

---

### Task 1: Host scoring

**Files:**
- Create: `src/shroom_fm/habitat.py`
- Test: `tests/test_habitat.py`

**Interfaces:**
- Consumes: nothing from other tasks (first task).
- Produces: `TARGET_SPECIES: list[str]`, `HOST_PROFILES: dict[str, dict[str, tuple[float, float]]]`, `host_score(species: str, fractions: dict[str, float]) -> float`. Later tasks import all three from `shroom_fm.habitat`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_habitat.py`:

```python
import pytest

from shroom_fm.habitat import HOST_PROFILES, TARGET_SPECIES, host_score


def test_target_species_has_five_entries():
    assert TARGET_SPECIES == ["kitsemampel", "chanterelle", "aspen_bolete", "birch_bolete", "porcini"]


def test_host_score_saturates_at_saturation_share():
    fractions = {"pine": 0.5, "spruce": 0.0, "birch": 0.0, "aspen": 0.0, "other": 0.5}

    score = host_score("chanterelle", fractions)

    assert score == pytest.approx(1.0)


def test_host_score_scales_linearly_below_saturation_share():
    fractions = {"pine": 0.20, "spruce": 0.0, "birch": 0.0, "aspen": 0.0, "other": 0.8}

    score = host_score("chanterelle", fractions)

    assert score == pytest.approx(0.5)


def test_host_score_uses_best_host_not_sum_of_hosts():
    mediocre_mix = {"pine": 0.25, "spruce": 0.20, "birch": 0.20, "aspen": 0.0, "other": 0.35}

    score = host_score("porcini", mediocre_mix)

    assert score < 1.0


def test_host_score_aspen_bolete_does_not_need_aspen_dominance():
    fractions = {"pine": 0.0, "spruce": 0.0, "birch": 0.0, "aspen": 0.15, "other": 0.85}

    score = host_score("aspen_bolete", fractions)

    assert score == pytest.approx(1.0)


def test_host_score_is_zero_for_no_compatible_host():
    fractions = {"pine": 0.0, "spruce": 0.0, "birch": 0.0, "aspen": 0.0, "other": 1.0}

    score = host_score("birch_bolete", fractions)

    assert score == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_habitat.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shroom_fm.habitat'`

- [ ] **Step 3: Write the implementation**

Create `src/shroom_fm/habitat.py`:

```python
import math


TARGET_SPECIES = ["kitsemampel", "chanterelle", "aspen_bolete", "birch_bolete", "porcini"]

# {species: {tree: (affinity, saturation_share)}}
# Engineering priors from mycorrhizal-host literature and Estonian forestry
# sources (RMK), not yet calibrated against field observations.
HOST_PROFILES = {
    "kitsemampel": {
        "pine": (1.00, 0.35),
        "spruce": (0.65, 0.30),
        "birch": (0.40, 0.25),
    },
    "chanterelle": {
        "pine": (1.00, 0.40),
        "spruce": (0.75, 0.35),
        "birch": (0.75, 0.35),
    },
    "aspen_bolete": {
        "aspen": (1.00, 0.15),
        "birch": (0.40, 0.20),
    },
    "birch_bolete": {
        "birch": (1.00, 0.20),
    },
    # Practical porcini/white-bolete target group (includes pine-associated
    # ecology), not molecularly verified B. edulis sensu stricto.
    "porcini": {
        "spruce": (1.00, 0.30),
        "pine": (0.90, 0.30),
        "birch": (0.75, 0.25),
    },
}


def host_score(species: str, fractions: dict[str, float]) -> float:
    contributions = [
        affinity * min(1.0, fractions[tree] / saturation_share)
        for tree, (affinity, saturation_share) in HOST_PROFILES[species].items()
    ]
    return max(contributions, default=0.0)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_habitat.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/shroom_fm/habitat.py tests/test_habitat.py
git commit -m "feat: add host_score with saturating per-host-tree contributions"
```

---

### Task 2: Site-type scoring and StandHabitatScore

**Files:**
- Modify: `src/shroom_fm/habitat.py` (append)
- Test: `tests/test_habitat.py` (append)

**Interfaces:**
- Consumes: `TARGET_SPECIES`, `host_score` (Task 1); `kasvukoht_profile` from `shroom_fm.ecotone` (existing — returns `{"group": str, "moisture": int | "special" | None} | None`).
- Produces: `SITE_TYPE_PROFILES: dict[str, dict[str, float]]`, `SITE_MODIFIER_FLOOR: float`, `site_type_score(species: str, kasvukoht_kood: str | None) -> float | None`, `site_modifier(site_type_score_value: float) -> float`, `stand_habitat_score(species: str, fractions: dict[str, float] | None, kasvukoht_kood: str | None) -> float | None`. Task 3 imports `stand_habitat_score`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_habitat.py`:

```python
from shroom_fm.habitat import site_modifier, site_type_score, stand_habitat_score


def test_site_type_score_returns_profile_value_for_mapped_group():
    # PH -> palu group; chanterelle's palu score is 1.00
    score = site_type_score("chanterelle", "PH")

    assert score == pytest.approx(1.00)


def test_site_type_score_returns_none_for_unmapped_kasvukoht():
    score = site_type_score("chanterelle", "KS")

    assert score is None


def test_site_type_score_returns_none_for_special_hydrology_group():
    # JO -> kõdusoo group, not in any species' SITE_TYPE_PROFILES table
    score = site_type_score("porcini", "JO")

    assert score is None


def test_site_modifier_bounds():
    assert site_modifier(1.0) == pytest.approx(1.00)
    assert site_modifier(0.0) == pytest.approx(0.50)
    assert site_modifier(0.8) == pytest.approx(0.90)


def test_stand_habitat_score_combines_host_and_site_multiplicatively():
    # 40% pine, PH (palu) site: chanterelle host_score = min(1, 0.4/0.4) = 1.0,
    # site_type_score(palu) = 1.0 -> site_modifier = 1.0 -> stand score = 1.0
    fractions = {"pine": 0.4, "spruce": 0.0, "birch": 0.0, "aspen": 0.0, "other": 0.6}

    score = stand_habitat_score("chanterelle", fractions, "PH")

    assert score == pytest.approx(1.0)


def test_stand_habitat_score_is_none_for_missing_composition():
    score = stand_habitat_score("chanterelle", None, "PH")

    assert score is None


def test_stand_habitat_score_is_none_for_unmapped_kasvukoht():
    fractions = {"pine": 0.4, "spruce": 0.0, "birch": 0.0, "aspen": 0.0, "other": 0.6}

    score = stand_habitat_score("chanterelle", fractions, "KS")

    assert score is None


def test_stand_habitat_score_poor_site_dampens_but_does_not_zero_strong_host():
    # 80% pine (host_score saturates to 1.0), salu site (chanterelle's worst
    # mapped score, 0.20) -> site_modifier = 0.5 + 0.5*0.20 = 0.60
    fractions = {"pine": 0.8, "spruce": 0.0, "birch": 0.0, "aspen": 0.0, "other": 0.2}

    score = stand_habitat_score("chanterelle", fractions, "ND")

    assert score == pytest.approx(0.60)
    assert score > 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_habitat.py -v`
Expected: FAIL with `ImportError: cannot import name 'site_type_score'`

- [ ] **Step 3: Write the implementation**

Append to `src/shroom_fm/habitat.py`:

```python
from shroom_fm.ecotone import kasvukoht_profile


# {species: {group: score in [0,1]}}. Groups not present in a species' table
# (kõdusoo, puistang — special hydrology / spoil ground, not on the normal
# ecological gradient) resolve to None via dict.get, not a guessed default.
SITE_TYPE_PROFILES = {
    "kitsemampel": {
        "nõmme": 0.85, "palu": 1.00, "laane": 0.45, "sürja": 0.20, "salu": 0.10,
        "rabastuv": 1.00, "sooviku": 0.25, "rohusoo": 0.10, "samblasoo": 0.15, "loo": 0.15,
    },
    "chanterelle": {
        "nõmme": 0.70, "palu": 1.00, "laane": 0.85, "sürja": 0.40, "salu": 0.20,
        "rabastuv": 0.45, "sooviku": 0.20, "rohusoo": 0.10, "samblasoo": 0.10, "loo": 0.25,
    },
    "aspen_bolete": {
        "nõmme": 0.60, "palu": 0.75, "laane": 0.90, "sürja": 0.85, "salu": 0.85,
        "rabastuv": 0.60, "sooviku": 0.75, "rohusoo": 0.55, "samblasoo": 0.35, "loo": 0.70,
    },
    "birch_bolete": {
        "nõmme": 0.65, "palu": 0.85, "laane": 0.85, "sürja": 0.75, "salu": 0.75,
        "rabastuv": 0.85, "sooviku": 0.85, "rohusoo": 0.70, "samblasoo": 0.70, "loo": 0.60,
    },
    "porcini": {
        "nõmme": 0.70, "palu": 0.95, "laane": 1.00, "sürja": 0.65, "salu": 0.50,
        "rabastuv": 0.40, "sooviku": 0.30, "rohusoo": 0.15, "samblasoo": 0.15, "loo": 0.40,
    },
}

SITE_MODIFIER_FLOOR = 0.5


def site_type_score(species: str, kasvukoht_kood: str | None) -> float | None:
    profile = kasvukoht_profile(kasvukoht_kood)
    if profile is None:
        return None
    return SITE_TYPE_PROFILES[species].get(profile["group"])


def site_modifier(site_type_score_value: float) -> float:
    return SITE_MODIFIER_FLOOR + (1 - SITE_MODIFIER_FLOOR) * site_type_score_value


def stand_habitat_score(
    species: str, fractions: dict[str, float] | None, kasvukoht_kood: str | None
) -> float | None:
    if fractions is None:
        return None
    site_score = site_type_score(species, kasvukoht_kood)
    if site_score is None:
        return None
    return host_score(species, fractions) * site_modifier(site_score)
```

Note: `kasvukoht_profile(None)` returns `None` safely (dict lookup miss), so
`site_type_score` needs no separate guard for a missing `kasvukoht_kood`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_habitat.py -v`
Expected: PASS (14 tests)

- [ ] **Step 5: Commit**

```bash
git add src/shroom_fm/habitat.py tests/test_habitat.py
git commit -m "feat: add site_type_score and stand_habitat_score"
```

---

### Task 3: score_stands orchestrator and runner script

**Files:**
- Modify: `src/shroom_fm/habitat.py` (append)
- Create: `scripts/score_habitat.py`

**Interfaces:**
- Consumes: `TARGET_SPECIES`, `stand_habitat_score` (Task 2); `composition_fractions`, `composition_diversity` from `shroom_fm.ecotone` (existing).
- Produces: `score_stands(eraldis_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame` — adds `composition_diversity` and `stand_habitat_score_<species>` (one column per entry in `TARGET_SPECIES`) to a copy of the input, preserving all existing columns. Task 5's `score_ecotone_habitat` and its runner script depend on `data/eraldis.geojson` already carrying these columns.

This task is orchestration (integration of already-tested pure functions over real
GeoDataFrame data) — per this module's established testing pattern (matching
`ecotone.py`'s `score_ecotones`), it is verified by running the real script against real
local data, not unit tested in isolation.

- [ ] **Step 1: Write the implementation**

Append to `src/shroom_fm/habitat.py`:

```python
import geopandas as gpd

from shroom_fm.ecotone import composition_diversity, composition_fractions


def score_stands(eraldis_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    result = eraldis_gdf.copy()

    fractions_list = [
        composition_fractions(composition) if composition else None
        for composition in result["composition"]
    ]
    result["composition_diversity"] = [
        composition_diversity(fractions) if fractions is not None else None
        for fractions in fractions_list
    ]

    for species in TARGET_SPECIES:
        result[f"stand_habitat_score_{species}"] = [
            stand_habitat_score(species, fractions, kasvukoht_kood)
            for fractions, kasvukoht_kood in zip(fractions_list, result["kasvukoht_kood"])
        ]

    return result
```

Move the `import geopandas as gpd` and `from shroom_fm.ecotone import ...` lines from the
bottom of the file to the top, consolidated with Task 2's existing
`from shroom_fm.ecotone import kasvukoht_profile` import into a single
`from shroom_fm.ecotone import composition_diversity, composition_fractions,
kasvukoht_profile` line — usual order: standard library, then third-party, then local,
matching `ecotone.py`'s existing import style. (Task 1 has no imports yet; Task 4 will be
the first to need `import math`.)

- [ ] **Step 2: Create the runner script**

Create `scripts/score_habitat.py`:

```python
from pathlib import Path

import geopandas as gpd

from shroom_fm.habitat import score_stands

ERALDIS_PATH = Path("data/eraldis.geojson")


def main() -> None:
    eraldis_gdf = gpd.read_file(ERALDIS_PATH)

    scored = score_stands(eraldis_gdf)
    scored.to_file(ERALDIS_PATH, driver="GeoJSON")

    print(f"{len(scored)} stands scored, saved to {ERALDIS_PATH}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run the full test suite to confirm no regressions**

Run: `uv run pytest tests/ -v`
Expected: PASS (all existing tests plus the 14 from Tasks 1-2, no failures)

- [ ] **Step 4: Run the script against real local data**

Run: `uv run python scripts/score_habitat.py`
Expected: prints `14171 stands scored, saved to data/eraldis.geojson` (or the current real
row count — `data/eraldis.geojson` already exists locally from the prior branch's work).

- [ ] **Step 5: Sanity-check the real output**

Run:
```bash
uv run python3 -c "
import geopandas as gpd
df = gpd.read_file('data/eraldis.geojson')
cols = [c for c in df.columns if c.startswith('stand_habitat_score_')] + ['composition_diversity']
print(df[cols].describe())
print('non-null stand_habitat_score_chanterelle:', df['stand_habitat_score_chanterelle'].notna().sum(), '/', len(df))
"
```
Expected: five `stand_habitat_score_*` columns and `composition_diversity`, all with values
in `[0, 1]` (or `NaN`/`None` for the known ~0.4% empty-composition and ~1.5% unmapped-kasvukoht
cases), non-null counts noticeably below 100% but not near 0.

- [ ] **Step 6: Commit**

```bash
git add src/shroom_fm/habitat.py scripts/score_habitat.py
git commit -m "feat: add score_stands orchestrator and score_habitat runner script"
```

---

### Task 4: Ecotone exploration-bonus formulas

**Files:**
- Modify: `src/shroom_fm/habitat.py` (append)
- Test: `tests/test_habitat.py` (append)

**Interfaces:**
- Consumes: nothing new from other tasks (pure functions, independent of Tasks 1-3's stand-scoring code).
- Produces: `normalize_bool_or_none(value) -> bool | None`, `TRANSITION_LENGTH_CAP_M: float`, `EXPLORATION_BONUS_CAP: float`, `kasvukoht_dimension_score(moisture_contrast: float | None, group_changed: bool | None) -> float | None`, `exploration_bonus(composition_contrast, moisture_contrast, group_changed, age_contrast, drainage_changed, transition_length_m) -> tuple[float, float, float]` (returns `(bonus, signal, coverage)`), `base_habitat(score_a: float | None, score_b: float | None) -> float | None`, `ecotone_score(score_a: float | None, score_b: float | None, bonus: float) -> float | None`. Task 5 imports all of these.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_habitat.py`:

```python
import math

from shroom_fm.habitat import (
    base_habitat,
    ecotone_score,
    exploration_bonus,
    kasvukoht_dimension_score,
    normalize_bool_or_none,
)


def test_normalize_bool_or_none_handles_real_bool():
    assert normalize_bool_or_none(True) is True
    assert normalize_bool_or_none(False) is False


def test_normalize_bool_or_none_handles_geojson_roundtrip_string():
    # kasvukoht_group_changed round-trips through GeoJSON as the string
    # "True"/"False", not a real bool, because it's a mixed bool+None column.
    assert normalize_bool_or_none("True") is True
    assert normalize_bool_or_none("False") is False


def test_normalize_bool_or_none_handles_missing():
    assert normalize_bool_or_none(None) is None
    assert normalize_bool_or_none(float("nan")) is None


def test_kasvukoht_dimension_score_prefers_moisture_contrast_when_available():
    score = kasvukoht_dimension_score(0.5, True)

    assert score == pytest.approx(0.5)


def test_kasvukoht_dimension_score_falls_back_to_group_changed():
    assert kasvukoht_dimension_score(None, True) == 1.0
    assert kasvukoht_dimension_score(None, False) == 0.0
    assert kasvukoht_dimension_score(float("nan"), True) == 1.0


def test_kasvukoht_dimension_score_none_when_both_missing():
    assert kasvukoht_dimension_score(None, None) is None


def test_exploration_bonus_full_evidence():
    bonus, signal, coverage = exploration_bonus(
        composition_contrast=1.0,
        moisture_contrast=1.0,
        group_changed=True,
        age_contrast=1.0,
        drainage_changed=True,
        transition_length_m=200.0,
    )

    assert signal == pytest.approx(1.0)
    assert coverage == pytest.approx(1.0)
    assert bonus == pytest.approx(0.3)


def test_exploration_bonus_treats_nan_composition_contrast_as_missing():
    # data/ecotones.geojson uses NaN (not None) for missing composition_contrast.
    bonus, signal, coverage = exploration_bonus(
        composition_contrast=float("nan"),
        moisture_contrast=1.0,
        group_changed=True,
        age_contrast=1.0,
        drainage_changed=True,
        transition_length_m=200.0,
    )

    assert coverage == pytest.approx(0.65)  # 1.0 - composition_contrast's 0.35 weight
    assert bonus == pytest.approx(0.3 * (0.25 + 0.20 + 0.10 + 0.10))


def test_exploration_bonus_single_low_weight_term_does_not_reach_full_cap():
    # transition_length is always computed (transition_length_m is never None),
    # so it always contributes its 0.10 weight to coverage alongside drainage's
    # 0.10 -> coverage=0.20 here, not just drainage's own weight.
    bonus, signal, coverage = exploration_bonus(
        composition_contrast=None,
        moisture_contrast=None,
        group_changed=None,
        age_contrast=None,
        drainage_changed=True,
        transition_length_m=0.0,
    )

    assert coverage == pytest.approx(0.20)
    assert bonus == pytest.approx(0.3 * 0.10)  # only drainage contributes to signal
    assert bonus < 0.05


def test_exploration_bonus_no_evidence_is_zero():
    bonus, signal, coverage = exploration_bonus(
        composition_contrast=None,
        moisture_contrast=None,
        group_changed=None,
        age_contrast=None,
        drainage_changed=None,
        transition_length_m=0.0,
    )

    assert coverage == pytest.approx(0.10)  # transition_length is always available
    assert bonus == pytest.approx(0.0)


def test_base_habitat_weights_max_higher_than_min():
    score = base_habitat(1.0, 0.0)

    assert score == pytest.approx(0.7)


def test_base_habitat_none_if_either_side_missing():
    assert base_habitat(None, 0.5) is None
    assert base_habitat(float("nan"), 0.5) is None


def test_ecotone_score_applies_bonus_multiplicatively():
    score = ecotone_score(1.0, 1.0, 0.3)

    assert score == pytest.approx(1.3)


def test_ecotone_score_none_if_base_habitat_missing():
    assert ecotone_score(None, 0.5, 0.3) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_habitat.py -v`
Expected: FAIL with `ImportError: cannot import name 'normalize_bool_or_none'`

- [ ] **Step 3: Write the implementation**

Append to `src/shroom_fm/habitat.py`. Add `import math` to the top imports (standard
library group) — this is the first task in this module that needs it:

```python
def normalize_bool_or_none(value) -> bool | None:
    if value is True or value == "True":
        return True
    if value is False or value == "False":
        return False
    return None


def _is_missing(value) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


TRANSITION_LENGTH_CAP_M = 200.0
EXPLORATION_BONUS_CAP = 0.3


def kasvukoht_dimension_score(moisture_contrast, group_changed) -> float | None:
    if not _is_missing(moisture_contrast):
        return moisture_contrast
    if group_changed is True:
        return 1.0
    if group_changed is False:
        return 0.0
    return None


def exploration_bonus(
    composition_contrast,
    moisture_contrast,
    group_changed,
    age_contrast,
    drainage_changed,
    transition_length_m,
) -> tuple[float, float, float]:
    dimension = kasvukoht_dimension_score(moisture_contrast, group_changed)
    terms = {
        "composition_contrast": (
            None if _is_missing(composition_contrast) else composition_contrast,
            0.35,
        ),
        "kasvukoht_dimension": (dimension, 0.25),
        "age_contrast": (None if _is_missing(age_contrast) else age_contrast, 0.20),
        "drainage_changed": (
            1.0 if drainage_changed is True else 0.0 if drainage_changed is False else None,
            0.10,
        ),
        "transition_length": (min(1.0, transition_length_m / TRANSITION_LENGTH_CAP_M), 0.10),
    }
    exploration_signal = sum(v * w for v, w in terms.values() if v is not None)
    exploration_coverage = sum(w for v, w in terms.values() if v is not None)
    bonus = EXPLORATION_BONUS_CAP * exploration_signal
    return bonus, exploration_signal, exploration_coverage


def base_habitat(score_a, score_b) -> float | None:
    if _is_missing(score_a) or _is_missing(score_b):
        return None
    return 0.7 * max(score_a, score_b) + 0.3 * min(score_a, score_b)


def ecotone_score(score_a, score_b, bonus: float) -> float | None:
    base = base_habitat(score_a, score_b)
    if base is None:
        return None
    return base * (1 + bonus)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_habitat.py -v`
Expected: PASS (27 tests)

- [ ] **Step 5: Commit**

```bash
git add src/shroom_fm/habitat.py tests/test_habitat.py
git commit -m "feat: add exploration_bonus, base_habitat, and ecotone_score"
```

---

### Task 5: score_ecotone_habitat orchestrator, runner script, and CLAUDE.md update

**Files:**
- Modify: `src/shroom_fm/habitat.py` (append)
- Create: `scripts/score_ecotone_habitat.py`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: `TARGET_SPECIES` (Task 1); `normalize_bool_or_none`, `exploration_bonus`, `ecotone_score` (Task 4). Reads `data/eraldis.geojson`'s `stand_habitat_score_<species>` columns, which must already exist (produced by Task 3's script).
- Produces: `score_ecotone_habitat(ecotones_gdf: gpd.GeoDataFrame, eraldis_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame` — adds `exploration_bonus`, `exploration_signal`, `exploration_coverage`, and `ecotone_score_<species>` (one per `TARGET_SPECIES` entry) to a copy of the input. Final task — nothing downstream in this plan.

Like Task 3, this is orchestration verified against real local data, not unit tested in
isolation.

- [ ] **Step 1: Write the implementation**

Append to `src/shroom_fm/habitat.py`:

```python
def score_ecotone_habitat(
    ecotones_gdf: gpd.GeoDataFrame, eraldis_gdf: gpd.GeoDataFrame
) -> gpd.GeoDataFrame:
    result = ecotones_gdf.copy()

    stand_scores_by_species = {
        species: dict(zip(eraldis_gdf["id"], eraldis_gdf[f"stand_habitat_score_{species}"]))
        for species in TARGET_SPECIES
    }

    bonuses, signals, coverages = [], [], []
    for _, row in result.iterrows():
        bonus, signal, coverage = exploration_bonus(
            composition_contrast=row["composition_contrast"],
            moisture_contrast=row["kasvukoht_moisture_contrast"],
            group_changed=normalize_bool_or_none(row["kasvukoht_group_changed"]),
            age_contrast=row["age_contrast"],
            drainage_changed=normalize_bool_or_none(row["drainage_changed"]),
            transition_length_m=row["transition_length_m"],
        )
        bonuses.append(bonus)
        signals.append(signal)
        coverages.append(coverage)

    result["exploration_bonus"] = bonuses
    result["exploration_signal"] = signals
    result["exploration_coverage"] = coverages

    for species in TARGET_SPECIES:
        lookup = stand_scores_by_species[species]
        result[f"ecotone_score_{species}"] = [
            ecotone_score(lookup.get(id_a), lookup.get(id_b), bonus)
            for id_a, id_b, bonus in zip(result["id_a"], result["id_b"], bonuses)
        ]

    return result
```

- [ ] **Step 2: Create the runner script**

Create `scripts/score_ecotone_habitat.py`:

```python
from pathlib import Path

import geopandas as gpd

from shroom_fm.habitat import score_ecotone_habitat

ECOTONES_PATH = Path("data/ecotones.geojson")
ERALDIS_PATH = Path("data/eraldis.geojson")


def main() -> None:
    ecotones_gdf = gpd.read_file(ECOTONES_PATH)
    eraldis_gdf = gpd.read_file(ERALDIS_PATH)

    if not any(col.startswith("stand_habitat_score_") for col in eraldis_gdf.columns):
        raise RuntimeError(
            f"{ERALDIS_PATH} has no stand_habitat_score_* columns — "
            "run scripts/score_habitat.py first."
        )

    scored = score_ecotone_habitat(ecotones_gdf, eraldis_gdf)
    scored.to_file(ECOTONES_PATH, driver="GeoJSON")

    print(f"{len(scored)} ecotone pairs habitat-scored, saved to {ECOTONES_PATH}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run the full test suite to confirm no regressions**

Run: `uv run pytest tests/ -v`
Expected: PASS (all existing tests plus the 27 from Tasks 1-2, 4, no failures)

- [ ] **Step 4: Run the script against real local data**

Run: `uv run python scripts/score_ecotone_habitat.py`
Expected: prints `<N> ecotone pairs habitat-scored, saved to data/ecotones.geojson` (`data/eraldis.geojson` must already have been scored by Task 3's script in this same working copy — it was, since Task 3 ran and committed against the same real local `data/` directory).

- [ ] **Step 5: Sanity-check the real output**

Run:
```bash
uv run python3 -c "
import geopandas as gpd
df = gpd.read_file('data/ecotones.geojson')
cols = [c for c in df.columns if c.startswith('ecotone_score_')] + ['exploration_bonus', 'exploration_signal', 'exploration_coverage']
print(df[cols].describe())
print('ecotone_score_chanterelle range:', df['ecotone_score_chanterelle'].min(), '-', df['ecotone_score_chanterelle'].max())
"
```
Expected: `ecotone_score_*` values in `[0, 1.3]` (per the documented range — not clamped to
`[0,1]`), `exploration_bonus` in `[0, 0.3]`.

- [ ] **Step 6: Update CLAUDE.md**

In `/home/okuu/python/shroom-fm/CLAUDE.md`, update the status paragraph. Replace:

```
**Status: MVP steps 1-6 done.** `src/shroom_fm/` holds `wfs.py` (WFS capabilities client),
`config.py` (home location loading), `eraldis.py` (bbox download + radius filtering),
`enrich.py` (joins tree composition from `eraldis_element` and resolves `kasvukoht`/`puuliik`
classifier labels), `adjacency.py` (computes which stands are meaningfully adjacent —
`touching` or `near_gap`), and `ecotone.py` (scores every adjacent pair by species
composition contrast, kasvukoht site-type/moisture contrast, development-class age
contrast, and drainage change — all continuous/unfiltered — and buffers the boundary into a
scoutable microtype polygon); `scripts/get_capabilities.py`, `scripts/download_eraldis.py`,
`scripts/enrich_eraldis.py`, `scripts/compute_adjacency.py`, and `scripts/score_ecotones.py`
are runnable. Step 7+ (`HabitatScore`, exporting top N) is not yet built. This file documents
the target architecture so implementation stays consistent; update it as more of the
pipeline lands.
```

with:

```
**Status: MVP steps 1-7 done.** `src/shroom_fm/` holds `wfs.py` (WFS capabilities client),
`config.py` (home location loading), `eraldis.py` (bbox download + radius filtering),
`enrich.py` (joins tree composition from `eraldis_element` and resolves `kasvukoht`/`puuliik`
classifier labels), `adjacency.py` (computes which stands are meaningfully adjacent —
`touching` or `near_gap`), `ecotone.py` (scores every adjacent pair by species
composition contrast, kasvukoht site-type/moisture contrast, development-class age
contrast, and drainage change — all continuous/unfiltered — and buffers the boundary into a
scoutable microtype polygon), and `habitat.py` (per-species `StandHabitatScore` for stand
interiors and `EcotoneScore` for adjacent-pair boundaries, for the five target species —
kitsemampel, chanterelle, aspen bolete, birch bolete, porcini — from host tree composition
and kasvukoht site-type suitability, kept as two distinct scores rather than one combined
`HabitatScore`); `scripts/get_capabilities.py`, `scripts/download_eraldis.py`,
`scripts/enrich_eraldis.py`, `scripts/compute_adjacency.py`, `scripts/score_ecotones.py`,
`scripts/score_habitat.py`, and `scripts/score_ecotone_habitat.py` are runnable (the last
two must run in that order — ecotone habitat scoring depends on stands already being
scored). Step 8+ (`ScoutScore` — combining `EcotoneScore` with weather, observation history,
landscape-mosaic diversity, and access penalty — and exporting top N) is not yet built. This
file documents the target architecture so implementation stays consistent; update it as more
of the pipeline lands.
```

- [ ] **Step 7: Commit**

```bash
git add src/shroom_fm/habitat.py scripts/score_ecotone_habitat.py CLAUDE.md
git commit -m "feat: add score_ecotone_habitat orchestrator and runner script, update CLAUDE.md for step 7"
```

---

## Self-Review Notes

- **Spec coverage:** All spec sections have a task — `StandHabitatScore`/`HOST_PROFILES` (Task 1), `SITE_TYPE_PROFILES`/site modifier (Task 2), `score_stands`/`scripts/score_habitat.py` (Task 3), `exploration_bonus`/`base_habitat`/`ecotone_score` (Task 4), `score_ecotone_habitat`/`scripts/score_ecotone_habitat.py`/`CLAUDE.md` (Task 5).
- **Real-data corrections folded in during planning** (not in the original spec text, discovered by reading `ecotone.py`'s actual source and real `data/ecotones.geojson` before writing tasks): `composition_contrast` uses `NaN` sentinel, not `None`, for missing pairs — `exploration_bonus` and `base_habitat` both guard against this via `_is_missing`. `kasvukoht_group_changed` round-trips through GeoJSON as a string due to the documented bool+`None` quirk — `normalize_bool_or_none` handles both the real-bool (in-memory/test) and string (post-round-trip) forms. `stand_habitat_score_<species>` will hit the same NaN-after-round-trip pattern once written to `data/eraldis.geojson` and reloaded for Task 5 — `base_habitat`'s `_is_missing` check (not a plain `is None`) covers this.
- **Type consistency:** `stand_habitat_score` signature (`species, fractions, kasvukoht_kood`) matches its Task 2 test calls and its Task 3 orchestrator usage. `exploration_bonus`'s 6-parameter signature and 3-tuple return are identical across its Task 4 definition, its tests, and Task 5's call site. `ecotone_score(score_a, score_b, bonus)` — no `species` parameter, since species-specific work already happened via the caller looking up `stand_habitat_score_<species>` before calling it.
