# Kasvukoht + Age Ecotone Contrast Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `data/ecotones.geojson` with kasvukoht (site-type moisture/fertility gradient) and age (development-class) contrast columns for every adjacency pair, alongside the existing species-composition contrast.

**Architecture:** Adds two domain-mapping constants and three pure functions to the existing `src/shroom_fm/ecotone.py` module, then extends the existing `score_ecotones` orchestrator to compute the new columns in the same per-pair loop that already computes species contrast — no new file, no second pass over the data.

**Tech Stack:** Python (`uv`-managed), stdlib only for the new functions, `pytest`.

## Global Constraints

- Package layout: all changes in `src/shroom_fm/ecotone.py` (existing module) and `tests/test_ecotone.py` (existing test file).
- `KASVUKOHT_PROFILES` and `AGE_CLASS_RANKS` are verified mappings (cross-checked against the live `metsaregister:kl_kasvukoht` WFS classifier and `docs/superpowers/MaaPartner.html`), not guessed — use the exact values given in this plan, do not alter them.
- Real `kasvukoht_kood` codes `KP`, `KS`, `LP` are genuinely absent from every available source (not in the WFS classifier, not in `MaaPartner.html`) — `KASVUKOHT_PROFILES` must NOT include entries for them; lookups must return `None`.
- `kasvukoht_contrast`'s three output fields must NOT be collapsed into one number — this was an explicit design decision (see spec's "Why not a simple categorical mismatch" section).
- `moisture_contrast` and `age_contrast` are normalized to `[0, 1]` (divide by 4 and 6 respectively — the max possible difference on each scale).
- `None` results (unmapped kasvukoht code, "special" moisture, missing age class) must propagate as `None`, never silently coerced to `0` or `False` — that would fabricate a "no difference" signal for an actually-unknown case, the same class of bug already fixed once in this module for missing composition data.
- No retries/error handling — no network calls in this extension.
- Testing: `kasvukoht_profile`, `kasvukoht_contrast`, `age_contrast` are pure and unit tested. The `score_ecotones` extension is orchestration, not unit tested in isolation — verified live against real local data.
- Output: extends `data/ecotones.geojson` in place with new columns `kasvukoht_site_type_changed`, `kasvukoht_group_changed`, `kasvukoht_moisture_contrast`, `age_contrast`, `drainage_changed`. No existing columns change.

---

### Task 1: `kasvukoht_profile` — verified site-type domain mapping

**Files:**
- Modify: `src/shroom_fm/ecotone.py`
- Modify: `tests/test_ecotone.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `KASVUKOHT_PROFILES: dict[str, dict]` constant and `kasvukoht_profile(kood: str) -> dict | None`. Consumed by Task 2 (`kasvukoht_contrast`) and Task 4 (`score_ecotones` extension).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_ecotone.py` (add `kasvukoht_profile` to the existing multi-line import
from `shroom_fm.ecotone` at the top of the file, alongside the existing names):

```python
def test_kasvukoht_profile_returns_known_site_type():
    profile = kasvukoht_profile("PH")

    assert profile == {"group": "palu", "moisture": 1}


def test_kasvukoht_profile_returns_none_for_unmapped_code():
    assert kasvukoht_profile("KS") is None
    assert kasvukoht_profile("KP") is None
    assert kasvukoht_profile("LP") is None


def test_kasvukoht_profile_marks_special_hydrology_types():
    lu = kasvukoht_profile("LU")
    jo = kasvukoht_profile("JO")

    assert lu == {"group": "loo", "moisture": "special"}
    assert jo == {"group": "kõdusoo", "moisture": "special"}


def test_kasvukoht_profile_marks_puistang_moisture_as_none():
    mp = kasvukoht_profile("MP")
    tp = kasvukoht_profile("TP")

    assert mp == {"group": "puistang", "moisture": None}
    assert tp == {"group": "puistang", "moisture": None}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ecotone.py -v`
Expected: FAIL with `ImportError: cannot import name 'kasvukoht_profile'`.

- [ ] **Step 3: Write minimal implementation**

Add to `src/shroom_fm/ecotone.py` (place near the top, alongside the other module-level
constants like `TARGET_SPECIES_CODES`-derived ones — after the existing imports, before the
pure functions):

```python
KASVUKOHT_PROFILES = {
    "SM": {"group": "nõmme", "moisture": 0},
    "KN": {"group": "nõmme", "moisture": 0},
    "LL": {"group": "loo", "moisture": 0},
    "KL": {"group": "loo", "moisture": 1},
    "PH": {"group": "palu", "moisture": 1},
    "JP": {"group": "palu", "moisture": 1},
    "MS": {"group": "palu", "moisture": 2},
    "JM": {"group": "laane", "moisture": 2},
    "JK": {"group": "laane", "moisture": 2},
    "SL": {"group": "sürja", "moisture": 2},
    "ND": {"group": "salu", "moisture": 2},
    "SN": {"group": "rabastuv", "moisture": 3},
    "KM": {"group": "rabastuv", "moisture": 3},
    "KR": {"group": "rabastuv", "moisture": 3},
    "SJ": {"group": "salu", "moisture": 3},
    "AN": {"group": "sooviku", "moisture": 3},
    "TA": {"group": "sooviku", "moisture": 3},
    "OS": {"group": "sooviku", "moisture": 3},
    "TR": {"group": "sooviku", "moisture": 3},
    "LD": {"group": "rohusoo", "moisture": 4},
    "MD": {"group": "rohusoo", "moisture": 4},
    "SS": {"group": "samblasoo", "moisture": 4},
    "RB": {"group": "samblasoo", "moisture": 4},
    "LU": {"group": "loo", "moisture": "special"},
    "JO": {"group": "kõdusoo", "moisture": "special"},
    "MO": {"group": "kõdusoo", "moisture": "special"},
    "MP": {"group": "puistang", "moisture": None},
    "TP": {"group": "puistang", "moisture": None},
}


def kasvukoht_profile(kood: str) -> dict | None:
    return KASVUKOHT_PROFILES.get(kood)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ecotone.py -v`
Expected: PASS (4 new tests pass; 31 total)

- [ ] **Step 5: Commit**

```bash
git add src/shroom_fm/ecotone.py tests/test_ecotone.py
git commit -m "feat: add kasvukoht_profile verified site-type mapping"
```

---

### Task 2: `kasvukoht_contrast` — uncollapsed multi-component contrast

**Files:**
- Modify: `src/shroom_fm/ecotone.py`
- Modify: `tests/test_ecotone.py`

**Interfaces:**
- Consumes: `kasvukoht_profile` (Task 1).
- Produces: `kasvukoht_contrast(kood_a: str, kood_b: str) -> dict` returning
  `{"site_type_changed": bool, "group_changed": bool | None, "moisture_contrast": float | None}`.
  Consumed by Task 4 (`score_ecotones` extension).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_ecotone.py` (add `kasvukoht_contrast` to the existing import line):

```python
def test_kasvukoht_contrast_graded_transition_within_same_group():
    result = kasvukoht_contrast("PH", "MS")

    assert result == {
        "site_type_changed": True,
        "group_changed": False,
        "moisture_contrast": 0.25,
    }


def test_kasvukoht_contrast_strong_transition_across_groups():
    result = kasvukoht_contrast("PH", "RB")

    assert result == {
        "site_type_changed": True,
        "group_changed": True,
        "moisture_contrast": 0.75,
    }


def test_kasvukoht_contrast_special_hydrology_type_has_no_moisture_contrast():
    result = kasvukoht_contrast("PH", "LU")

    assert result["site_type_changed"] is True
    assert result["group_changed"] is True
    assert result["moisture_contrast"] is None


def test_kasvukoht_contrast_unmapped_code_only_has_site_type_changed():
    result = kasvukoht_contrast("PH", "KS")

    assert result == {
        "site_type_changed": True,
        "group_changed": None,
        "moisture_contrast": None,
    }


def test_kasvukoht_contrast_identical_codes():
    result = kasvukoht_contrast("PH", "PH")

    assert result == {
        "site_type_changed": False,
        "group_changed": False,
        "moisture_contrast": 0.0,
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ecotone.py -v`
Expected: FAIL with `ImportError: cannot import name 'kasvukoht_contrast'`.

- [ ] **Step 3: Write minimal implementation**

Add to `src/shroom_fm/ecotone.py`, after `kasvukoht_profile`:

```python
def kasvukoht_contrast(kood_a: str, kood_b: str) -> dict:
    profile_a = kasvukoht_profile(kood_a)
    profile_b = kasvukoht_profile(kood_b)
    site_type_changed = kood_a != kood_b

    if profile_a is None or profile_b is None:
        return {
            "site_type_changed": site_type_changed,
            "group_changed": None,
            "moisture_contrast": None,
        }

    group_changed = profile_a["group"] != profile_b["group"]
    moisture_a = profile_a["moisture"]
    moisture_b = profile_b["moisture"]
    if isinstance(moisture_a, (int, float)) and isinstance(moisture_b, (int, float)):
        moisture_contrast = abs(moisture_a - moisture_b) / 4
    else:
        moisture_contrast = None

    return {
        "site_type_changed": site_type_changed,
        "group_changed": group_changed,
        "moisture_contrast": moisture_contrast,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ecotone.py -v`
Expected: PASS (5 new tests pass; 36 total)

- [ ] **Step 5: Commit**

```bash
git add src/shroom_fm/ecotone.py tests/test_ecotone.py
git commit -m "feat: add kasvukoht_contrast uncollapsed multi-component contrast"
```

---

### Task 3: `age_contrast` — verified development-class ordering

**Files:**
- Modify: `src/shroom_fm/ecotone.py`
- Modify: `tests/test_ecotone.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `AGE_CLASS_RANKS: dict[str, int]` constant and
  `age_contrast(arengukl_a: str, arengukl_b: str) -> float | None`. Consumed by Task 4
  (`score_ecotones` extension).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_ecotone.py` (add `age_contrast` to the existing import line):

```python
def test_age_contrast_between_young_and_mature_stand():
    assert age_contrast("N", "Y") == pytest.approx(0.6666666666666666)


def test_age_contrast_between_clearing_and_mature_stand_is_maximal():
    assert age_contrast("A", "Y") == pytest.approx(1.0)


def test_age_contrast_identical_class_is_zero():
    assert age_contrast("K", "K") == 0.0


def test_age_contrast_returns_none_for_unrecognized_class():
    assert age_contrast("N", "X") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ecotone.py -v`
Expected: FAIL with `ImportError: cannot import name 'age_contrast'`.

- [ ] **Step 3: Write minimal implementation**

Add to `src/shroom_fm/ecotone.py`, alongside `KASVUKOHT_PROFILES` (near the top, with the
other constants):

```python
AGE_CLASS_RANKS = {
    "A": 0,
    "S": 1,
    "N": 2,
    "L": 3,
    "K": 4,
    "V": 5,
    "Y": 6,
}
```

Add after `kasvukoht_contrast`:

```python
def age_contrast(arengukl_a: str, arengukl_b: str) -> float | None:
    if arengukl_a not in AGE_CLASS_RANKS or arengukl_b not in AGE_CLASS_RANKS:
        return None
    return abs(AGE_CLASS_RANKS[arengukl_a] - AGE_CLASS_RANKS[arengukl_b]) / 6
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ecotone.py -v`
Expected: PASS (4 new tests pass; 40 total)

- [ ] **Step 5: Commit**

```bash
git add src/shroom_fm/ecotone.py tests/test_ecotone.py
git commit -m "feat: add age_contrast verified development-class ordering"
```

---

### Task 4: Extend `score_ecotones` — kasvukoht/age/drainage columns

**Files:**
- Modify: `src/shroom_fm/ecotone.py`

**Interfaces:**
- Consumes: `kasvukoht_contrast` (Task 2), `age_contrast` (Task 3).
- Produces: `score_ecotones` now also returns the columns `kasvukoht_site_type_changed`,
  `kasvukoht_group_changed`, `kasvukoht_moisture_contrast`, `age_contrast`,
  `drainage_changed` on every row, in addition to its existing columns. Signature and the
  existing species-contrast columns are unchanged.

No test for this step (per Global Constraints: orchestration, verified live).

- [ ] **Step 1: Extend the lookups and per-pair loop**

Read the current `score_ecotones` function in `src/shroom_fm/ecotone.py` before editing —
it currently builds one lookup (`composition_by_id`) from `eraldis_gdf` and, in a loop over
`projected_adjacency.iterrows()`, computes species-contrast fields into a `records` list.

Add three more lookups alongside the existing `composition_by_id` line, built the same way
(`dict(zip(eraldis_gdf["id"], eraldis_gdf[<column>]))`):

```python
kasvukoht_by_id = dict(zip(eraldis_gdf["id"], eraldis_gdf["kasvukoht_kood"]))
arengukl_by_id = dict(zip(eraldis_gdf["id"], eraldis_gdf["arengukl_kood"]))
drained_by_id = dict(zip(eraldis_gdf["id"], eraldis_gdf["kuivendatud"]))
```

Inside the per-pair loop, after the existing species-contrast fields are computed (and
before the `records.append({...})` call), compute the new fields:

```python
kasvukoht_a = kasvukoht_by_id.get(row["id_a"])
kasvukoht_b = kasvukoht_by_id.get(row["id_b"])
kasvukoht_result = (
    kasvukoht_contrast(kasvukoht_a, kasvukoht_b)
    if kasvukoht_a is not None and kasvukoht_b is not None
    else {"site_type_changed": None, "group_changed": None, "moisture_contrast": None}
)

arengukl_a = arengukl_by_id.get(row["id_a"])
arengukl_b = arengukl_by_id.get(row["id_b"])
age_contrast_value = (
    age_contrast(arengukl_a, arengukl_b)
    if arengukl_a is not None and arengukl_b is not None
    else None
)

drained_a = drained_by_id.get(row["id_a"])
drained_b = drained_by_id.get(row["id_b"])
drainage_changed_value = (
    drained_a != drained_b if drained_a is not None and drained_b is not None else None
)
```

Then add these five keys to the existing `records.append({...})` dict literal (alongside
the existing keys — do not remove any existing key):

```python
"kasvukoht_site_type_changed": kasvukoht_result["site_type_changed"],
"kasvukoht_group_changed": kasvukoht_result["group_changed"],
"kasvukoht_moisture_contrast": kasvukoht_result["moisture_contrast"],
"age_contrast": age_contrast_value,
"drainage_changed": drainage_changed_value,
```

Also add these five column names to the existing `ECOTONE_COLUMNS` list constant (append
after the existing entries, before `"geometry"` — `geometry` must stay last since
`gpd.GeoDataFrame` expects it there for the empty-records guard):

```python
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
    "kasvukoht_site_type_changed",
    "kasvukoht_group_changed",
    "kasvukoht_moisture_contrast",
    "age_contrast",
    "drainage_changed",
    "geometry",
]
```

- [ ] **Step 2: Run the existing test suite to confirm nothing broke**

Run: `uv run pytest -v`
Expected: PASS (40 passed) — this step only adds new columns to existing orchestration, it
doesn't change `kasvukoht_profile`/`kasvukoht_contrast`/`age_contrast`/the species-contrast
functions.

- [ ] **Step 3: Run it against your real local data**

This requires `data/adjacency.geojson` and `data/eraldis.geojson` to already exist (from
prior branches). If missing, rebuild them using the established local-fixture chain
(`download_eraldis.py` at a reduced radius → `enrich_eraldis.py` → `compute_adjacency.py`)
before proceeding.

Run:
```bash
uv run scripts/score_ecotones.py
```

Expected: same pair-count summary as before (this step doesn't change row count).

- [ ] **Step 4: Verify the new columns**

Run:
```bash
uv run python -c "
import geopandas as gpd
gdf = gpd.read_file('data/ecotones.geojson')
print(gdf.columns.tolist())
print()
print('kasvukoht_moisture_contrast stats:')
print(gdf['kasvukoht_moisture_contrast'].describe())
print()
print('age_contrast stats:')
print(gdf['age_contrast'].describe())
print()
print('drainage_changed value counts:')
print(gdf['drainage_changed'].value_counts(dropna=False))
print()
print('a row with a real graded kasvukoht transition:')
graded = gdf[gdf['kasvukoht_moisture_contrast'].notna() & (gdf['kasvukoht_moisture_contrast'] > 0)]
print(graded[['id_a', 'id_b', 'kasvukoht_site_type_changed', 'kasvukoht_group_changed', 'kasvukoht_moisture_contrast', 'age_contrast']].iloc[0])
"
```
Expected: `kasvukoht_moisture_contrast` and `age_contrast` show real `[0, 1]`-ranged
variation (not all zero, not all `None`), `drainage_changed` has both `True` and `False`
values present, and the sample graded row shows sane, non-degenerate values.

- [ ] **Step 5: Commit**

```bash
git add src/shroom_fm/ecotone.py
git commit -m "feat: extend score_ecotones with kasvukoht/age/drainage contrast"
```

(Do not `git add data/ecotones.geojson` — it's already gitignored; confirm via `git status`
that it doesn't appear as a trackable change.)

---

## Post-plan note

This plan extends MVP step 6's output with two more contrast dimensions. Combining
`composition_contrast`, `kasvukoht_*`, `age_contrast`, and `drainage_changed` into one
overall priority score is `HabitatScore` — MVP step 7, separate follow-up work, not part of
this plan.
