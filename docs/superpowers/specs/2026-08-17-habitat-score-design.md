# HabitatScore — Design

Date: 2026-08-17
Status: Approved

## Purpose

MVP step 7 from `CLAUDE.md`: given `data/eraldis.geojson` (per-stand tree composition,
kasvukoht, age) and `data/ecotones.geojson` (per-adjacent-pair contrast signals from the
prior ecotone-scoring step), calculate per-species habitat suitability for the five target
species (kitsemampel, chanterelles, aspen boletes, birch boletes, porcini) — first for stand
interiors, then for ecotone boundaries.

## Two separate scores, not one

Score both surfaces (stand interior and ecotone boundary), but as two distinct, separately
named scores — never collapsed into a single `HabitatScore`:

- **`StandHabitatScore(species, eraldis)`** — intrinsic biological suitability of the stand
  interior: host tree composition + site type (kasvukoht).
- **`EcotoneScore(species, pair)`** — scouting value of the boundary/microtype: derived from
  both adjacent stands' `StandHabitatScore` plus boundary-specific contrast signals
  (composition contrast, moisture contrast, site-group change, age contrast, drainage
  change, transition length).

A later `ScoutScore` (out of scope here) will combine `EcotoneScore` with weather
(`FruitingScore(t)`), personal observation history, landscape-mosaic/diversity bonuses, and
an access penalty — none of which exist in the pipeline yet. Keeping the names distinct now
prevents ambiguity later about whether a `0.82` is forest quality, boundary interest, or
trip priority.

## Explicitly deferred (not part of this step)

- **Stand structure/density** (`taius_1`/`taius_2`, the "sparse vs. dense" signal
  kitsemampel's heuristic wants). Real coverage is only 59%/15% and values exceed 100 —
  an unverified quirk, likely the same multi-layer-summing pattern already documented for
  `osakaal`. Not used until independently verified.
- **Landscape-mosaic/diversity bonus** (`CLAUDE.md`'s "multi-species / general foraging"
  heuristic). This is a property of scouting-area efficiency, not of any one stand's or
  ecotone's biological suitability, so it does not belong in either score computed here.
  `composition_diversity` (already implemented in `ecotone.py`) is retained as a *stand-level
  feature* — computed and stored, not applied as a bonus to any score. Neighborhood-level
  mosaic features (neighbor species/site-type diversity, count of distinct nearby target
  habitats) are deferred to `ScoutScore`.
- **Road/edge accessibility.** No road-access data exists in the pipeline yet (`CLAUDE.md`'s
  architecture diagram lists it as a separate future input). `exploration_bonus` omits it.
- **Development-class (`arengukl_kood`) as a direct StandHabitatScore input.** Species
  heuristics don't call for an intrinsic age preference beyond what's already implied by
  host composition (a genuinely clear-cut stand has near-zero tree fractions, which
  `host_score` already handles without a separate age gate). `age_contrast` remains part of
  `EcotoneScore` via the existing ecotone-scoring step.
- **`FruitingScore`, observation history, `ScoutScore` itself** — per `CLAUDE.md`'s already
  -documented architecture, these come after a season of personal observations.

## `StandHabitatScore`

```python
def host_score(species, fractions):
    """None if composition data is missing (empty composition list / total_osakaal == 0)."""
    contributions = [
        affinity * min(1, fractions[tree] / saturation_share)
        for tree, (affinity, saturation_share) in HOST_PROFILES[species].items()
    ]
    return max(contributions, default=0.0)

SITE_MODIFIER_FLOOR = 0.5

def site_modifier(site_type_score):
    return SITE_MODIFIER_FLOOR + (1 - SITE_MODIFIER_FLOOR) * site_type_score

def stand_habitat_score(species, fractions, kasvukoht_kood):
    h = host_score(species, fractions)
    s = site_type_score(species, kasvukoht_kood)
    if h is None or s is None:
        return None
    return h * site_modifier(s)
```

**Why multiplicative, bounded floor:** a mycorrhizal species needs both a compatible host
*and* compatible site conditions, but the site-type mapping (`KASVUKOHT_PROFILES`, derived
not official) is an approximation — it shouldn't be able to crush a strong host match to
near-zero on its own. At `SITE_MODIFIER_FLOOR=0.5`: `site_type_score=1.0 → modifier=1.00`,
`0.8 → 0.90`, `0.5 → 0.75`, `0.0 → 0.50`. A genuinely absent host (`host_score≈0.05`) stays
near-zero regardless of site type — that's the property that matters, not the site-type
floor.

**Why max, not sum, across hosts:** `host_score = max(contributions)` answers "how good is
the *best* available host opportunity for this species" — not a sum that would implicitly
reward tree-species diversity inside a per-species score (diversity is deliberately tracked
separately, see above). A stand with three mediocre hosts should not out-score a stand with
one excellent host.

**None-propagation:** `host_score` returns `None` (not `0.0`) when a stand's `composition`
list is empty — the ~0.4% "no data" case already documented in `CLAUDE.md`. A fabricated
`0.0` would misrepresent "no host trees present" as indistinguishable from "we don't know."
`site_type_score` returns `None` for the ~1.5% of stands with an unmapped `kasvukoht_kood`
(`KP`/`KS`/`LP`) or a special-hydrology/`puistang` group (`kõdusoo`, `puistang` — not on the
normal ecological gradient, no defensible heuristic). `stand_habitat_score` is `None` if
either input is `None`.

### `HOST_PROFILES` — `{species: {tree: (affinity, saturation_share)}}`

Engineering priors grounded in mycorrhizal-host literature and Estonian forestry sources
(RMK), not derived from field data yet — calibrate later from real observations.

```python
HOST_PROFILES = {
    "kitsemampel": {
        "pine":   (1.00, 0.35),
        "spruce": (0.65, 0.30),
        "birch":  (0.40, 0.25),
    },
    "chanterelle": {
        "pine":   (1.00, 0.40),
        "spruce": (0.75, 0.35),
        "birch":  (0.75, 0.35),
    },
    "aspen_bolete": {
        "aspen": (1.00, 0.15),
        "birch": (0.40, 0.20),
    },
    "birch_bolete": {
        "birch": (1.00, 0.20),
    },
    # Practical porcini/white-bolete target group (includes pine-associated
    # B. pinophilus-like ecology), not molecularly verified B. edulis sensu stricto.
    "porcini": {
        "spruce": (1.00, 0.30),
        "pine":   (0.90, 0.30),
        "birch":  (0.75, 0.25),
    },
}
```

Kitsemampel is deliberately not pine-only: `Cortinarius caperatus` is documented in both
spruce and pine forests, and birch habitats, so `spruce=0`/`birch=0` would create false
negatives — but pine carries the strongest practical signal for Estonia. Its
"sparse pine approaching bog" character lives in `SITE_TYPE_PROFILES`, not doubled here.

### `SITE_TYPE_PROFILES` — `{species: {group: score}}`

Uses the `group` field already computed by `ecotone.py`'s `KASVUKOHT_PROFILES`
(`nõmme`=poorest/driest → `palu` → `laane` → `sürja` → `salu`=richest, plus
`rabastuv`/`sooviku`/`rohusoo`/`samblasoo` on the wet/paludifying side, `loo`=alvar).

```python
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
# kõdusoo, puistang groups (special hydrology / spoil ground — not on the normal
# gradient): site_type_score = None for all species, propagates to
# stand_habitat_score = None.
```

Kitsemampel favors `palu`/`rabastuv` (peak) with `nõmme` close behind (poor, acidic sandy
soils including dry lichen pine forest are documented habitat); true peat/bog (`samblasoo`)
is explicitly lower than `rabastuv` (a paludification *transition*, not an established bog).
Chanterelle peaks at `palu`, close at `laane`/`nõmme` (acidic, low-nitrogen, sandy —
pH ~4–5.5 typical); `salu` (rich, wet) is markedly less characteristic. Aspen bolete is
deliberately flat — its strongest signal is host association (already in `HOST_PROFILES`),
not site type; only extreme dry or true bog sites get a moderate penalty. Birch bolete is
modeled as a practical "birch-`Leccinum` target group" (not strict `L. scabrum sensu
stricto`, which is edaphically drier) — high scores extend into `rabastuv`/`sooviku`/
`samblasoo`. Porcini is modeled as a practical "porcini/white-bolete group": `B. edulis` has
a wide host and soil range including productive Scots-pine-forest populations on acidic
sandy soils, so `palu`/`nõmme` are much stronger than a spruce-only model would suggest;
ranking is roughly `palu ≈ laane > nõmme > sürja/salu > rabastuv > wet peat`.

## `EcotoneScore`

```python
TRANSITION_LENGTH_CAP_M = 200.0
EXPLORATION_BONUS_CAP = 0.3

def kasvukoht_dimension_score(row):
    if row.kasvukoht_moisture_contrast is not None:
        return row.kasvukoht_moisture_contrast  # already normalized [0,1] by ecotone.py
    if row.kasvukoht_group_changed is True:
        return 1.0
    if row.kasvukoht_group_changed is False:
        return 0.0
    return None  # kasvukoht unmapped on at least one side

def exploration_bonus(row):
    terms = {
        "composition_contrast": (row.composition_contrast, 0.35),
        "kasvukoht_dimension":  (kasvukoht_dimension_score(row), 0.25),
        "age_contrast":         (row.age_contrast, 0.20),  # already normalized [0,1]
        "drainage_changed":     (1.0 if row.drainage_changed else 0.0, 0.10),
        "transition_length":    (min(1.0, row.transition_length_m / TRANSITION_LENGTH_CAP_M), 0.10),
    }
    exploration_signal = sum(v * w for v, w in terms.values() if v is not None)
    exploration_coverage = sum(w for v, w in terms.values() if v is not None)
    return EXPLORATION_BONUS_CAP * exploration_signal, exploration_signal, exploration_coverage

def base_habitat(score_a, score_b):
    if score_a is None or score_b is None:
        return None
    return 0.7 * max(score_a, score_b) + 0.3 * min(score_a, score_b)

def ecotone_score(score_a, score_b, row):
    base = base_habitat(score_a, score_b)
    if base is None:
        return None
    bonus, _, _ = exploration_bonus(row)
    return base * (1 + bonus)
```

`ecotone_score` takes no `species` argument — the species-specific work already happened via
the caller looking up `stand_habitat_score_<species>` for both sides before calling it, and
`exploration_bonus` is species-independent. `score_ecotone_habitat` calls it once per species
per pair, passing that species' two stand scores.

**Why weights are never renormalized on missing data:** if a term is unavailable and the
remaining weights were renormalized to sum to 1, a single available low-weight signal (e.g.
`drainage_changed` at nominal weight 0.10) could produce the *full* 0.30 bonus cap — treating
one secondary binary signal as 100% of the evidence rather than the 10% its weight implies.
Instead, `exploration_signal` sums `value × nominal_weight` directly over available terms
only (never above the nominal weight's contribution), and `exploration_coverage` (sum of
available terms' weights) is exported separately so partial-evidence bonuses are
diagnosable rather than silently inflated.

`kasvukoht_moisture_contrast` and `age_contrast` are *already* normalized to `[0,1]` by the
existing `ecotone.py` code (`abs(moisture_a - moisture_b) / 4` and
`abs(rank_a - rank_b) / 6` respectively) — confirmed by reading the current
`src/shroom_fm/ecotone.py` source, not assumed. No additional per-field cap is applied to
them. `transition_length_m` is the only raw-unit term and gets its own
`min(1, length / 200)` normalization.

**Why `base_habitat = 0.7·max + 0.3·min`, not average:** a boundary is interesting primarily
because at least one side is good habitat, but the other side's quality still matters (a
transition between two excellent stands shouldn't be under-valued relative to one excellent
+ one poor stand). Not switching to geometric mean or a different split without field data
to justify it.

**Value ranges (not probabilities):** `StandHabitatScore ∈ [0,1]`, `ExplorationBonus ∈
[0,0.3]`, `EcotoneScore ∈ [0,1.3]` — deliberately *not* clamped to `[0,1]`, since that would
destroy differentiation between the best ecotones. These are ranking scores.

## Module: `src/shroom_fm/habitat.py`

Pure functions (unit tested): `host_score`, `site_type_score`, `site_modifier`,
`stand_habitat_score`, `kasvukoht_dimension_score`, `exploration_bonus`, `base_habitat`,
`ecotone_score`, plus the `HOST_PROFILES`/`SITE_TYPE_PROFILES` constants and a
`TARGET_SPECIES` list (`["kitsemampel", "chanterelle", "aspen_bolete", "birch_bolete",
"porcini"]`).

Two orchestrators (verified against real local data, not unit tested in isolation — same
pattern as `score_ecotones` in `ecotone.py`):

- `score_stands(eraldis_gdf) -> GeoDataFrame` — for every stand, computes
  `composition_fractions` (reusing `ecotone.py`'s existing function) and
  `composition_diversity` (stored, no bonus applied), then `stand_habitat_score` for each of
  the 5 species. Adds columns `stand_habitat_score_kitsemampel`, `..._chanterelle`,
  `..._aspen_bolete`, `..._birch_bolete`, `..._porcini`, and `composition_diversity`.
- `score_ecotone_habitat(ecotones_gdf, eraldis_gdf) -> GeoDataFrame` — for every ecotone
  pair, looks up both sides' per-species `stand_habitat_score_*` from the (already-scored)
  `eraldis_gdf` by `id_a`/`id_b`, computes `ecotone_score` for each of the 5 species, plus
  `exploration_signal`, `exploration_coverage`, `exploration_bonus`. Adds columns
  `ecotone_score_kitsemampel`, `..._chanterelle`, `..._aspen_bolete`, `..._birch_bolete`,
  `..._porcini`, `exploration_signal`, `exploration_coverage`, `exploration_bonus`.
  **Requires `eraldis_gdf` to already carry `stand_habitat_score_*` columns** — i.e.
  `scripts/score_habitat.py` must run before `scripts/score_ecotone_habitat.py`.

### `scripts/score_habitat.py`

Runner: loads `data/eraldis.geojson` → `score_stands()` → saves `data/eraldis.geojson` in
place (extends columns, same pattern as `scripts/enrich_eraldis.py`). No network calls.

### `scripts/score_ecotone_habitat.py`

Runner: loads `data/ecotones.geojson` and `data/eraldis.geojson` (must already be
habitat-scored) → `score_ecotone_habitat()` → saves `data/ecotones.geojson` in place
(extends columns). No network calls.

## Output

`data/eraldis.geojson` gains: `stand_habitat_score_kitsemampel`,
`stand_habitat_score_chanterelle`, `stand_habitat_score_aspen_bolete`,
`stand_habitat_score_birch_bolete`, `stand_habitat_score_porcini`, `composition_diversity`.

`data/ecotones.geojson` gains: `ecotone_score_kitsemampel`, `ecotone_score_chanterelle`,
`ecotone_score_aspen_bolete`, `ecotone_score_birch_bolete`, `ecotone_score_porcini`,
`exploration_signal`, `exploration_coverage`, `exploration_bonus`.

## Error handling

No network calls in this step. `None`-propagation is the primary error-handling mechanism
(no fabricated defaults for missing composition, unmapped kasvukoht, or missing counterpart
scores), consistent with the rest of the pipeline. One known GeoJSON round-trip quirk already
documented in `CLAUDE.md` applies here too: any newly added nullable-bool-style column would
round-trip as a string, not a real bool — none of this step's new columns are nullable
booleans, so this doesn't recur here, but is worth remembering if a future column is.

## Testing

- `host_score`, `site_type_score`, `site_modifier`, `stand_habitat_score`,
  `kasvukoht_dimension_score`, `exploration_bonus`, `base_habitat`, `ecotone_score` are pure
  and unit tested, covering: a strong host match, a weak/absent host (verify `None`, not
  `0.0`, for empty composition), an unmapped kasvukoht code (verify `None` propagation), the
  `max`-not-`sum` host aggregation (a 3-mediocre-host stand should not outscore a
  1-excellent-host stand), the site-modifier floor bounds, missing-term
  weight behavior in `exploration_bonus` (a single low-weight available term must not reach
  the full 0.30 cap), and the `EcotoneScore` `None`-propagation when either side's stand
  score is `None`.
- `score_stands` and `score_ecotone_habitat` are orchestration — verified live against real
  local data (no network required), same pattern as the rest of this module.

## Out of scope

- `ScoutScore` (combining `EcotoneScore` with weather/`FruitingScore`, observation history,
  landscape-mosaic bonus, access penalty) and exporting top-N results — MVP step 8+.
- Stand structure/density, road accessibility, and per-species `SITE_MODIFIER_FLOOR`
  tuning — all explicitly deferred above.
- Splitting `porcini`/`birch_bolete` into narrower taxonomic profiles (e.g.
  `porcini_pine` vs. `porcini_spruce`) — noted as a future refinement if field data
  supports it, not attempted now.
- Any change to `src/shroom_fm/adjacency.py`, `enrich.py`, or `ecotone.py`'s existing
  contrast-scoring logic (only consumed, not modified).
