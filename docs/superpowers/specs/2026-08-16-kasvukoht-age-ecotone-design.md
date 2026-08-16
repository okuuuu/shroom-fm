# Kasvukoht + Age Ecotone Contrast — Design

Date: 2026-08-16
Status: Approved

## Purpose

Extends MVP step 6 (ecotone scoring) with two more contrast axes beyond species
composition: `kasvukoht` (site type — moisture/fertility gradient) and stand age
(`arengukl_kood`, development class). The original design notes named these as equally
valuable ecotone types (`dry site ↔ moist site`, `old stand ↔ young stand`) alongside
species; this extension adds them to the same per-pair scoring the species-contrast step
already established, rather than treating them as a separate later concern.

## Why not a simple categorical mismatch

An earlier draft considered `kasvukoht_contrast = 1.0 if codes differ else 0.0`, matching
`dominant_species`'s categorical style. Rejected: Estonian forest site types exist along
real ecological gradients (moisture, fertility) documented in EMÜ (Estonian University of
Life Sciences) forestry typology — e.g. a real transition sequence
`sinika → karusambla/karusambla-mustika → tarna/osja → angervaksa → sõnajala` as moisture
and acidity change. Collapsing `PH → MS` (pohla → mustika, both `palu` group, one moisture
step apart — a mild transition) and `PH → RB` (pohla → raba, `palu` → `samblasoo`, four
moisture steps apart — a strong transition) into the same binary "different" signal would
destroy exactly the information that makes one pair more scoutable than the other.

## Domain mapping — verified against two independent sources

`src/shroom_fm/ecotone.py` gains `KASVUKOHT_PROFILES: dict[str, dict]`, a derived (not
official — no such classifier exists in the WFS) mapping from `kasvukoht_kood` to
`{"group": str, "moisture": int | "special" | None}`. Moisture scale: `0` = very dry,
`1` = dry/fresh, `2` = fresh/moist, `3` = wet, `4` = waterlogged/peat. `"special"` marks
hydrologically variable types that shouldn't be forced onto the ordinal scale (`LU` —
seasonally wet/dry; `JO`/`MO` — drainage-altered former bog, "kõdusoo" family). `None` marks
`puistang` (spoil/tailings ground — `MP`/`TP`), which isn't a natural gradient position.

```python
KASVUKOHT_PROFILES = {
    "SM": {"group": "nõmme", "moisture": 0},       # sambliku
    "KN": {"group": "nõmme", "moisture": 0},        # kanarbiku
    "LL": {"group": "loo", "moisture": 0},          # leesikaloo
    "KL": {"group": "loo", "moisture": 1},          # kastikuloo
    "PH": {"group": "palu", "moisture": 1},         # pohla
    "JP": {"group": "palu", "moisture": 1},         # jänesekapsa-pohla
    "MS": {"group": "palu", "moisture": 2},         # mustika
    "JM": {"group": "laane", "moisture": 2},        # jänesekapsa-mustika
    "JK": {"group": "laane", "moisture": 2},        # jänesekapsa
    "SL": {"group": "sürja", "moisture": 2},        # sinilille
    "ND": {"group": "salu", "moisture": 2},         # naadi
    "SN": {"group": "rabastuv", "moisture": 3},     # sinika
    "KM": {"group": "rabastuv", "moisture": 3},     # karusambla-mustika
    "KR": {"group": "rabastuv", "moisture": 3},     # karusambla
    "SJ": {"group": "salu", "moisture": 3},         # sõnajala
    "AN": {"group": "sooviku", "moisture": 3},      # angervaksa
    "TA": {"group": "sooviku", "moisture": 3},      # tarna-angervaksa
    "OS": {"group": "sooviku", "moisture": 3},      # osja
    "TR": {"group": "sooviku", "moisture": 3},      # tarna
    "LD": {"group": "rohusoo", "moisture": 4},      # lodu
    "MD": {"group": "rohusoo", "moisture": 4},      # madalsoo
    "SS": {"group": "samblasoo", "moisture": 4},    # siirdesoo
    "RB": {"group": "samblasoo", "moisture": 4},    # raba
    "LU": {"group": "loo", "moisture": "special"},  # lubikaloo (seasonally variable)
    "JO": {"group": "kõdusoo", "moisture": "special"},  # jänesekapsa-kõdusoo
    "MO": {"group": "kõdusoo", "moisture": "special"},  # mustika-kõdusoo
    "MP": {"group": "puistang", "moisture": None},  # mineraalne puistang
    "TP": {"group": "puistang", "moisture": None},  # turbane puistang
}
```

**Verified, not guessed:** this table was cross-checked against both the live
`metsaregister:kl_kasvukoht` WFS classifier (28 codes, fetched live) and an independent
Estonian forestry reference (`docs/superpowers/MaaPartner.html`) — both list the identical
28 codes. Real `data/eraldis.geojson` has 31 distinct `kasvukoht_kood` values; the 3 not in
either source (`KP`, `KS`, `LP` — 1.5% of stands, 213/14171) are **not** in
`KASVUKOHT_PROFILES` and resolve to `None` — treated as unknown, not guessed. (An earlier
draft of this mapping assumed a code `Ks` for "kõdusoo," but `KS` is one of the three
genuinely unmapped codes — corrected before finalizing.)

`AGE_CLASS_RANKS: dict[str, int]`, also verified against `MaaPartner.html`'s `Arenguklass`
table (real succession order, not inferred):

```python
AGE_CLASS_RANKS = {
    "A": 0,  # lage ala (clear/open, no canopy)
    "S": 1,  # selguseta ala (establishing regeneration)
    "N": 2,  # noorendik (young stand, ⌀≤6cm)
    "L": 3,  # latimets (pole stage, ⌀6-12cm)
    "K": 4,  # keskealine mets (middle-aged)
    "V": 5,  # valmiv mets (maturing, within 10y of maturity)
    "Y": 6,  # küps mets (mature)
}
```

`kuivendatud` (drainage) is already a clean boolean column on every stand (100% coverage,
confirmed live) — used directly as the drainage signal; no mapping needed.

## Components

### `src/shroom_fm/ecotone.py` (extends the existing module)

- `kasvukoht_profile(kood: str) -> dict | None` — pure lookup into `KASVUKOHT_PROFILES`,
  `None` for unmapped codes.
- `kasvukoht_contrast(kood_a: str, kood_b: str) -> dict` — pure function. Returns
  `{"site_type_changed": bool, "group_changed": bool | None, "moisture_contrast": float | None}`,
  deliberately **not collapsed into one number**:
  - `site_type_changed = kood_a != kood_b` — always computable (direct code comparison, no
    profile lookup needed).
  - `group_changed` — `None` if either side is unmapped, else `profile_a["group"] != profile_b["group"]`.
  - `moisture_contrast` — `None` if either side is unmapped or has `"special"` moisture
    (can't force a hydrologically-variable type onto the ordinal scale), else
    `abs(moisture_a - moisture_b) / 4` (normalized to `[0, 1]`, matching `composition_contrast`'s range).
- `age_contrast(arengukl_a: str, arengukl_b: str) -> float | None` — pure function.
  `abs(rank_a - rank_b) / 6` (normalized `[0, 1]`) via `AGE_CLASS_RANKS`; `None` if either
  code is missing from the mapping (defensive — not expected given 100% real coverage, but
  not assumed either).
- `score_ecotones` (existing orchestrator, extended) — adds a `kasvukoht_kood`/
  `arengukl_kood`/`kuivendatud` lookup (by `id`, same pattern as the existing `composition`
  lookup) and computes the new columns per pair in the same loop — one pass, not a second
  file or function.

## Output — extends `data/ecotones.geojson` in place

New columns: `kasvukoht_site_type_changed`, `kasvukoht_group_changed`,
`kasvukoht_moisture_contrast`, `age_contrast`, `drainage_changed` (simple
`kuivendatud_a != kuivendatud_b`, always a real bool — the field itself has no missing
values). No existing columns change.

## Error handling

Same posture as the rest of this module: no network calls, no defensive handling beyond the
explicit `None`-for-unmapped/unknown cases already described — those are real, verified data
gaps (unmapped kasvukoht codes, in principle a missing `arengukl_kood`), not hypothetical
ones.

## Testing

- `kasvukoht_profile`, `kasvukoht_contrast`, `age_contrast` are pure and unit tested,
  covering: a real graded example (`PH→MS`: same group, `moisture_contrast = 0.25`), a
  strong ecotone (`PH→RB`: group changed, `moisture_contrast = 0.75`), the special-case
  moisture types (`LU`/`JO`/`MO` → `moisture_contrast = None` even when paired with a
  numeric-moisture code), the unmapped-code case (`KS` → `kasvukoht_profile` returns `None`,
  `kasvukoht_contrast` returns `group_changed=None, moisture_contrast=None` but
  `site_type_changed` still computable), and `age_contrast` across a few real rank pairs.
- `score_ecotones`'s extension is orchestration — verified live against real local data, not
  unit tested in isolation, same as the rest of this module.

## Out of scope

- Substrate class (mineral/peat/drained-peat) as a distinct dimension — the user's original
  sketch mentioned it but didn't provide a verified per-group mapping; `group` already
  implicitly carries most of this distinction (e.g. `samblasoo`/`rohusoo` groups are peat,
  `nõmme`/`palu`/`laane` are mineral) well enough for now. Add explicitly later if the
  implicit signal proves insufficient.
- Combining `composition_contrast`, `kasvukoht_*`, `age_contrast`, and `drainage_changed`
  into one overall score — that's `HabitatScore`, MVP step 7, not this step.
- Any change to `src/shroom_fm/eraldis.py`, `enrich.py`, `adjacency.py`, or their outputs.
