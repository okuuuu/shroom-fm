# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

shroom-fm predicts where to forage for mushrooms in Estonian forests. It scores forest
stands (`eraldis`) from the state Metsaregister (Estonian Forest Registry) on habitat
suitability for specific species (chanterelles, spruce milk caps / `kuuseriisikas`, etc.),
then layers recent weather on top to produce a current, ranked shortlist of places worth
scouting — instead of manually clicking around the Metsaregister web map.

**Status: MVP steps 1-8 done.** `src/shroom_fm/` holds `wfs.py` (WFS capabilities client),
`config.py` (home location loading), `eraldis.py` (server-side CQL annulus download via
`fetch_eraldis_annulus`), `cql.py` (shared `estonian_grid_point`/`annulus_filter` helpers
that build the `DWITHIN`/`BEYOND` CQL_FILTER string used by both the eraldis and roads
annulus fetches), `enrich.py` (joins tree composition from `eraldis_element` and resolves
`kasvukoht`/`puuliik` classifier labels), `adjacency.py` (computes which stands are
meaningfully adjacent — `touching` or `near_gap`), `ecotone.py` (scores every adjacent pair
by species composition contrast, kasvukoht site-type/moisture contrast, development-class
age contrast, and drainage change — all continuous/unfiltered — and buffers the boundary
into a scoutable microtype polygon), `habitat.py` (per-species `StandHabitatScore` for stand
interiors and `EcotoneScore` for adjacent-pair boundaries, for the five target species —
kitsemampel, chanterelle, aspen bolete, birch bolete, porcini — from host tree composition
and kasvukoht site-type suitability, kept as two distinct scores rather than one combined
`HabitatScore`), `roads.py` (ETAK road/barrier WFS fetch, `car_class` classification of road
segments, and barrier-snap exclusion of segments near a permanently-closed barrier),
`access.py` (per-eraldis `AccessScore` from nearest-road distances, additive-only onto
`data/eraldis.geojson`), and `scout.py` (`ScoutScore` v0 — joins each ecotone to its two
stands' `AccessScore`, taking `access_modifier = max(access_score_a, access_score_b)` and
splitting candidates per species into a `ranked` tier, `scout_score = ecotone_score ×
access_modifier`, and a `remote_high_value` tier for ecologically-strong candidates the v1
access distance-proxy couldn't confirm a nearby road for — never a fabricated `0` or floor,
see `docs/superpowers/specs/2026-08-17-scout-candidates-export-design.md`). All scripts are
runnable — see "Running the full pipeline" below for the exact command sequence and
dependency order. The road-access piece of the Access/Eligibility layer has landed as
`AccessScore` (see `docs/superpowers/specs/2026-08-17-road-access-design.md`) — additive-only
onto `data/eraldis.geojson`, never modifying `StandHabitatScore`/`EcotoneScore`. MVP step 8
(export top N → GeoJSON) has landed as `ScoutScore` v0 + `scripts/export_scout_candidates.py`
→ `data/scout_candidates.geojson`. Still deferred: `FruitingScore` (weather), personal
observation history, and a landscape-mosaic diversity bonus — none exist yet, and `ScoutScore`
v0 simply omits them from its formula rather than faking neutral placeholder values for them.
This file documents the target architecture so implementation stays consistent; update it as
more of the pipeline lands.

## Running the full pipeline

Unit tests (fast, no network): `uv run pytest tests/`

Real pipeline (hits live WFS endpoints; needs `config.toml` with home coordinates — copy
`config.example.toml` and fill in `home_lat`/`home_lon`). Steps 1-6 and step 7 are
independent of each other (either branch can run first, or in parallel); step 8 needs both
branches done; step 9 needs everything upstream:

1. `uv run python scripts/download_eraldis.py` — Metsaregister stands within home radius →
   `data/eraldis.geojson` (`RADIUS_KM`/`INNER_RADIUS_KM` script constants; currently a
   38km/18km annulus)
2. `uv run python scripts/enrich_eraldis.py` — join tree composition + kasvukoht/puuliik
   labels onto `data/eraldis.geojson`
3. `uv run python scripts/compute_adjacency.py` — find adjacent stand pairs →
   `data/adjacency.geojson`
4. `uv run python scripts/score_ecotones.py` — score boundary contrast →
   `data/ecotones.geojson`
5. `uv run python scripts/score_habitat.py` — `StandHabitatScore` per species onto
   `data/eraldis.geojson` (must run before step 6)
6. `uv run python scripts/score_ecotone_habitat.py` — `EcotoneScore` per species onto
   `data/ecotones.geojson` (depends on step 5's `stand_habitat_score_*` columns already
   existing)
7. `uv run python scripts/download_roads.py` — ETAK roads/barriers within the same home
   radius → `data/roads.geojson`/`data/barriers.geojson` (only needs home coordinates, not
   steps 1-6)
8. `uv run python scripts/score_access.py` — `AccessScore` onto `data/eraldis.geojson`
   (needs steps 1 and 7 already done)
9. `uv run python scripts/export_scout_candidates.py` — top-5-per-species `ScoutScore` v0
   shortlist → `data/scout_candidates.geojson` (needs steps 5, 6, and 8 already done)

At real scale (~65k stands, ~50k road segments within a 20km-wide annulus around Tallinn):
step 1 is fast (CQL server-side filtering), steps 3-6 are fast (local computation), step 7
takes several minutes (paginated WFS fetch, ETAK's road network is dense), step 8 takes well
under a minute (spatial-indexed via `geopandas.sjoin_nearest`, not a brute-force loop), step
9 is near-instant.

**Known real-data quirks** (found only via live verification against real Metsaregister
data, not visible from synthetic test fixtures):
- ETAK's WFS (`etak:e_501_tee_j` and presumably other ETAK layers) behaves differently from
  Metsaregister's WFS in two ways, both confirmed live 2026-08-17: (1) it only allows
  `srsName=EPSG:3301` for output geometry on this layer — `EPSG:4326` is rejected with an
  `ows:ExceptionReport` — still true today and directly relied on in `roads.py`'s
  `fetch_layer_annulus` via its `_ETAK_OUTPUT_CRS` constant; (2) its `bbox` GetFeature
  parameter enforces the EPSG:4326 URN's strict authority-defined axis order (**lat, lon** —
  not the "GIS convention" lon,lat that Metsaregister's WFS accepts), confirmed by testing
  both orders directly against the live server. Separately, `owslib`'s
  `WebFeatureService.getfeature()` silently re-serializes any bbox tuple it's given back into
  lon,lat order on the wire regardless of the order passed in — confirmed by inspecting the
  actual outgoing request URL — so an axis-order fix cannot be applied through `owslib` at
  all for this endpoint. (Historical: (2) and the owslib bypass applied to the old bbox-based
  `fetch_layer_bbox`, since removed — no longer relevant since both the eraldis and roads
  fetches now use `CQL_FILTER` instead of the `bbox` parameter. Kept here since the
  underlying axis-order/owslib facts about ETAK's WFS remain true and could matter again if a
  bbox-based fetch is ever needed there.)
- A composition entry's `osakaal` (share) can be `NaN` for understory/undergrowth layers
  (`rinne_kood: "A"`) — the registry doesn't measure a stocking share for undergrowth. Around
  15% of real stands have at least one such entry. Any code summing `osakaal` across a
  stand's composition must filter out NaN entries first, or the sum (and everything derived
  from it) becomes NaN. `ecotone.py`'s `composition_fractions` does this; `enrich.py`'s
  `compute_species_shares` does not yet filter NaN and is a latent risk if a future
  download/radius happens to include an affected stand with a NaN entry on one of the four
  target species (not observed in current data, but not structurally prevented either).
- A small fraction of stands (~0.4% in one real sample) have an entirely empty `composition`
  list (no matching `eraldis_element` rows at all). Downstream code must treat "no
  composition data" as `None`/`NaN`, not silently compute a plausible-looking but fabricated
  value from an empty/all-zero input.
- `ecotone.py`'s `kasvukoht_group_changed` column (nullable bool: `True`/`False`/`None` for
  unmapped kasvukoht codes) round-trips through GeoJSON as the **string** `"True"`/`"False"`,
  not a real bool — confirmed by reading `data/ecotones.geojson` back with `geopandas`. This
  is a `fiona`/GeoDataFrame GeoJSON quirk affecting mixed bool+`None` columns specifically:
  `drainage_changed` (always a real bool, never `None`) round-trips fine and stays `bool`.
  Any code reading `kasvukoht_group_changed` back from the saved GeoJSON must compare against
  the strings `"True"`/`"False"` (or cast explicitly), not `is True`/`is False`.

## Planned architecture

Data pipeline (this is the long-term target shape; see "Running the full pipeline" above
for the actual current script sequence — `habitat`/`ecotone`/`access` scores and the
`ScoutScore` v0 export are real today, `FruitingScore` and observation history are not yet
built, and this diagram omits the ETAK roads/barriers WFS that `access` score now depends on
alongside Metsaregister):

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
        ScoutScore
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
- `metsaregister:eraldis`'s real attribute columns (confirmed via a live `GetFeature` call,
  2026-08-16) include: `kvartali_nr`, `eraldise_nr` (the `Kvartal`/`Eraldis` identifiers),
  `peapuuliik_kood` (`Peamine puuliik`), `kasvukoht_kood` (`Kasvukoht`), `arengukl_kood`
  (`Arenguklass`), plus `pindala` (area), `korgus` (height), `keskm_vanus` (mean age),
  `omandivorm_kood` (ownership form), and others. `scripts/download_eraldis.py` downloads
  this layer already joined with these attributes — no separate attribute fetch needed for
  the fields already present here.
- `metsaregister:eraldis_element` (confirmed live, 2026-08-16) has **no geometry** — it can't
  be bbox-filtered, only filtered by `eraldis_id` (GeoServer's `CQL_FILTER=eraldis_id IN
  (...)` vendor extension works; `owslib.getfeature()` has no CQL support, so this fetch uses
  `requests` directly). Real columns: `eraldis_id`, `rinne_kood` (canopy layer), `puuliik_kood`
  (species), `osakaal` (share), `vanus`, `korgus`, `enamus`, `sunniaasta`, `paritolu`,
  `diameeter`, `rinnaspindala`, `tagavara`, `arv` — multiple rows per stand.
- `kl_puuliik`/`kl_kasvukoht` classifiers are small, non-spatial, `{kood, kirjeldus}` shape
  (confirmed live: `kl_puuliik` has 30 rows, e.g. `MA`→`mänd` (pine), `KU`→`kuusk` (spruce),
  `KS`→`kask` (birch), `HB`→`haab` (aspen)). `scripts/enrich_eraldis.py` resolves both onto
  `eraldis` as `peapuuliik_kirjeldus`/`kasvukoht_kirjeldus` (real example value seen live:
  `kasvukoht_kirjeldus = "jänesekapsa-mustika"`), and adds `pine_share`/`spruce_share`/
  `birch_share`/`aspen_share` columns computed from composition for this project's four
  target host-tree species.

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
