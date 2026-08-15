# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

shroom-fm predicts where to forage for mushrooms in Estonian forests. It scores forest
stands (`eraldis`) from the state Metsaregister (Estonian Forest Registry) on habitat
suitability for specific species (chanterelles, spruce milk caps / `kuuseriisikas`, etc.),
then layers recent weather on top to produce a current, ranked shortlist of places worth
scouting — instead of manually clicking around the Metsaregister web map.

**Status: pre-implementation.** The repository currently contains only `LICENSE` and
`.gitignore` — no code has been written yet. This file documents the target architecture
so implementation stays consistent; update it as real modules, commands, and scripts land.

## Planned architecture

Data pipeline (build first, as a CLI):

```
Metsaregister WFS (GeoServer OWS)
      │
      ├── eraldis            geometry + stand metadata (species mix, age, ownership)
      ├── eraldis_element    tree composition detail, joined via eraldis.id
      └── classifiers        kasvukohatüüp, puuliik, etc. lookup tables
              │
              ▼
        GeoPandas (feature engineering)
              │
      ┌───────┼─────────┐
      │       │         │
   habitat  ecotone   access
    score    score     score
      │       │         │
      └───────┼─────────┘
              ▼
        HabitatScore (static, recomputed rarely)
              │
              + FruitingScore(t)  — rainfall/temp history, recency of rain
              + observation history (your own logged finds)
              ▼
        CurrentScore
              │
              ▼
        GeoJSON export → QGIS / map viewer
```

Longer-term target stack: **PostGIS** (stores `eraldis` geometry, computed scores, weather
history, personal find log) + **Python/GeoPandas** (WFS ingestion, feature engineering,
scoring) + a **React** frontend for browsing scored areas, with QGIS as an interim/backup
viewer. Model the scoring initially as hand-picked weighted heuristics per species; once a
season of personal observations (`date, lat, lon, species, kg, minutes, microtype,
fresh/old`) accumulates, revisit as a trained model (e.g. LightGBM) predicting
`P(productive | forest, weather, season)`.

### MVP build order (CLI, no DB yet)

1. Download Metsaregister polygons via WFS
2. Restrict to ≤80 km from home
3. Join tree composition (`eraldis_element`)
4. Join `kasvukohatüüp` (site/habitat type)
5. Calculate neighbouring stands (spatial adjacency)
6. Detect interesting ecotones (species-boundary transitions, e.g. pine↔spruce)
7. Calculate `HabitatScore`
8. Export top N results → GeoJSON
9. View in QGIS (or a lighter-weight viewer if one turns out to fit better)

Weather-driven `FruitingScore` and PostGIS storage are explicitly deferred until the static
habitat scoring pipeline is validated.

## Data source: Metsaregister WFS

- Endpoint: `https://gsavalik.envir.ee/geoserver/metsaregister/ows` — a single GeoServer OWS
  endpoint; request type is chosen via the `service=WFS` or `service=WMS` query param.
- **Use WFS only for data extraction.** It returns actual geometry + attributes, so
  GeoPandas can load it directly (`gpd.read_file(WFS_URL)`), join, filter, and score. WMS
  returns pre-rendered map tiles — useful only as a visual reference layer in QGIS, not for
  computing anything.
- Key layers: `metsaregister:eraldis` (stand geometry/metadata), `metsaregister:eraldis_element`
  (tree species composition, joins to `eraldis` via `eraldis.id = eraldis_element.eraldis_id`),
  plus classifier lookup layers `metsaregister:kl_kasvukoht` (kasvukohatüüp) and
  `metsaregister:kl_puuliik` (puuliik).
- Do **not** use the `AKS` WFS shown alongside it in Keskkonnaagentuur's service listing —
  that's the address/place-name registry (`Aadressandmete ja kohanimede süsteem`), unrelated
  to forest data.
- Data is published as open data under CC-BY 4.0.
- Real layer names have been confirmed via a live `GetCapabilities` call (23 layers as of
  2026-08-15, recorded in `data/wfs_capabilities.json`) — the names above are verified, not
  assumed. Re-run `scripts/get_capabilities.py` and diff the output if the service changes.
- EELIS (Keskkonnaagentuur) also exposes public WMS/WFS and may be a useful supplementary
  source later (e.g. protected areas, hydrology) but is not part of the core pipeline.

## Domain glossary (Estonian forestry terms used throughout the data and code)

- **`Metsaeraldised`** — the Metsaregister layer/dataset of forest stands.
- **`Eraldis`** — a stand: the core scoring unit. A single polygon that Metsaregister itself
  models as homogeneous in species composition, age, height, and site type — this is why
  it's the right granularity for scoring, finer than `Kvartal`.
- **`Kvartal`** — forest quarter/compartment; treat as a coarser grouping identifier, not an
  analysis unit.
- **`Peamine puuliik`** — main tree species of the stand.
- **`Kasvukoht` / `Kasvukohatüüp`** — forest site/habitat type (soil moisture, fertility
  class). Combined with `Peamine puuliik`, this carries much more signal than species alone
  — e.g. pine correlates with poorer/drier sandy, peaty, or some very wet sites, spruce with
  different conditions.
- **`Arenguklass`** — development/age class of the stand.
- **Ecotone** — a boundary between two adjacent stands of different composition (e.g.
  pine↔spruce, forest↔bog, old↔young stand). These transition zones, not stand interiors,
  are often the most interesting scouting targets and can be generated automatically by
  intersecting adjacent stand boundaries and buffering the resulting line (~30–50 m).

## Species heuristics (informing the scoring model)

The scoring model targets only high-value edible species: **kitsemampel** (gypsy
mushroom), **chanterelles**, **aspen boletes**, **birch boletes**, and **porcini**. Milk
caps, russulas, and other lower-priority edible mushrooms are not included in the target
score.

- **Kitsemampel** (`Cortinarius caperatus`): strongly favor sparse pine stands and
  pine-dominated forests approaching boggy or paludifying conditions. Particularly
  promising candidates are open pine forests, pine/bog transitions, and mosaics containing
  both relatively dry pine ground and wetter depressions. Dense closed spruce forest and
  deciduous-dominated stands should receive little or no species-specific score. Because
  kitsemampel may fruit abundantly when conditions are suitable, contiguous areas of
  suitable habitat are valuable in addition to individual ecotones.
- **Chanterelles** (`Cantharellus cibarius`): favor pine and pine-mixed forests, while
  retaining mixed coniferous/deciduous stands as viable habitat. Useful candidate
  structures include `pine dominant → pine/birch mixed → spruce inclusion`, especially
  where several such stand types can be sampled along one route. Stand boundaries should
  receive an exploration bonus rather than being treated as an intrinsic biological
  requirement: the purpose is to sample several tree/moisture combinations efficiently.
  Chanterelles commonly occur in groups, so previous positive observations should strongly
  increase the local historical score.
- **Aspen boletes** (`Leccinum` spp., especially haavapuravik): strongly favor stands
  containing **aspen**, including deciduous and mixed forests. Aspen does not need to be
  the dominant tree: an otherwise mixed stand with a substantial aspen component should
  remain a strong candidate. Useful structures include `aspen stand → mixed deciduous
  forest`, `aspen → birch`, and `aspen-containing mixed forest → forest edge`. Birch and
  willow presence may contribute a weaker positive signal because related red-capped
  `Leccinum` can associate with these hosts as well.
- **Birch boletes** (`Leccinum scabrum` group): make **birch presence/share** the primary
  species-specific feature. Favor birch stands, birch-dominated mixed forest, and
  coniferous stands with a meaningful birch component. Do not require dry forest:
  birch-associated boletes also occur in moist forests and around bog margins, so `birch
  forest → wetter depression/bog edge` should remain a valid candidate rather than being
  filtered out. For this group, tree-composition data is more important than the nominal
  dominant-tree class alone.
- **Porcini / king boletes** (`Boletus edulis` group): in Estonia, prioritize **spruce and
  spruce-mixed forest** for the common `Boletus edulis`, but do not make spruce mandatory.
  Birch and pine are also valid mycorrhizal hosts, while pine-associated porcini are
  particularly relevant in sandy pine forests. High-value candidate structures therefore
  include `spruce dominant → spruce/birch mixed`, `spruce → pine`, and mixed stands
  containing several suitable host species. The model should score host-tree composition
  rather than encode a single rigid "porcini forest" type. If desired later,
  pine-associated and deciduous-associated porcini can be represented as separate habitat
  profiles rather than forcing all `Boletus` into one heuristic.
- **Multi-species / general high-value foraging**: prefer forest mosaics that contain
  several target habitats within a short walking distance, for example `pine | spruce |
  birch | aspen | wet depression | forest road`. Such areas should receive an additional
  **diversity/exploration score** because one short scouting loop can test habitat for
  several target species. This bonus should be separate from the individual species
  scores, so a highly suitable single-species stand is not penalized merely for being
  homogeneous.
- **Orthophoto sanity check**: discard or heavily penalize candidates showing a recent
  large clear-cut, farmland or regrowing field instead of established forest, extremely
  dense young growth, clearly unusable access, or a large uniform plantation where a
  structurally richer candidate is available nearby. This is initially a manual filter and
  can later be encoded using land-cover, canopy, disturbance, and road-access features.
