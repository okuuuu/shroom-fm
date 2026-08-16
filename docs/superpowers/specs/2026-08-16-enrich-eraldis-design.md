# Join Tree Composition + Classifier Labels onto Eraldis — Design

Date: 2026-08-16
Status: Approved

## Purpose

This is MVP pipeline steps 3–4 from `CLAUDE.md`, combined into one spec since they share the
same mechanism (fetch a WFS reference layer, join it onto the already-downloaded `eraldis`
dataset):

- **Step 3**: join tree composition (`metsaregister:eraldis_element`) — per-stand species
  breakdown by canopy layer, richer than the single `peapuuliik_kood` column already present.
- **Step 4**: join `kasvukohatüüp` — resolve `kasvukoht_kood`/`peapuuliik_kood` codes already
  on `eraldis` against the `kl_kasvukoht`/`kl_puuliik` classifier layers to get human-readable
  labels.

Output: `data/eraldis.geojson` (from the prior branch) is enriched **in place** with
composition and classifier-label columns.

## Live findings that shape this design

Confirmed live against the real Metsaregister WFS before finalizing this design:

- `metsaregister:eraldis_element` has **no geometry**
  (`boundingBoxWGS84 = (-1.0, -1.0, 0.0, 0.0)`) — it cannot be bbox-filtered like `eraldis`
  was. It must be filtered by `eraldis_id`.
- GeoServer's `CQL_FILTER=eraldis_id IN (id1,id2,...)` vendor-extension query parameter works
  against this endpoint (verified via a raw request). `owslib`'s `getfeature()` has no
  parameter for this (its signature has no `CQL_FILTER`/CQL support), so this one fetch uses
  `requests` directly rather than the `owslib` client used elsewhere — a deliberate, scoped
  exception, not a pattern to generalize.
- `eraldis_element` has multiple rows per stand: one per `(rinne_kood, puuliik_kood)`
  combination. Sample columns: `eraldis_id`, `rinne_kood`, `puuliik_kood`, `osakaal`,
  `vanus`, `korgus`, `enamus`, `sunniaasta`, `paritolu`, `diameeter`, `rinnaspindala`,
  `tagavara`, `arv` (plus `id`/`sys_id`/`versioon` bookkeeping fields, not composition data).
- Classifier layers are small and non-spatial: `kl_puuliik` confirmed at 30 rows
  (`kood`, `kirjeldus` columns, e.g. `MA`→`mänd`, `KU`→`kuusk`, `KS`→`kask`, `HB`→`haab`).
  `kl_kasvukoht` is assumed to share the same `kood`/`kirjeldus` shape (same `kl_*` family) —
  confirm this live during implementation; if the column names differ, adjust
  `fetch_classifier` accordingly rather than guessing further here.
- Target-species codes (confirmed via the full `kl_puuliik` listing), matching the host
  trees for this project's target mushrooms: **pine** = `MA`, **spruce** = `KU`,
  **birch** = `KS`, **aspen** = `HB`.

## Components

### `src/shroom_fm/enrich.py` (new module)

Kept separate from `eraldis.py` (download/radius-filter) and `wfs.py` (capabilities) — this
module's responsibility is joining reference data onto an already-downloaded dataset.

- `fetch_eraldis_element(eraldis_ids: list[int]) -> pandas.DataFrame` — the network call.
  Batches `eraldis_ids` into chunks (500 per batch — comfortably under safe URL length
  limits for a GET request), and for each batch issues
  `requests.get(METSAREGISTER_OWS_URL, params={..., "CQL_FILTER": f"eraldis_id IN ({...})"})`
  against `metsaregister:eraldis_element`, parsing the GeoJSON `features[].properties` into
  rows. Returns a plain `DataFrame` (no geometry column, since the source layer has none).
- `fetch_classifier(wfs: WebFeatureService, typename: str) -> dict[str, str]` — fetches a
  classifier layer in full via `wfs.getfeature(typename=typename, outputFormat="application/json")`
  (no filtering needed — both classifier layers are small), returns `{kood: kirjeldus}`.
- `summarize_composition(element_df: pandas.DataFrame) -> dict[int, list[dict]]` — pure
  function. Groups `element_df` by `eraldis_id`; each stand's value is the full list of its
  composition records as dicts, keeping every field except the internal `id`/`sys_id`/
  `versioon` bookkeeping columns.
- `compute_species_shares(composition: list[dict]) -> dict[str, float]` — pure function.
  For each of the four target species (`pine`/`spruce`/`birch`/`aspen`, mapped to
  `MA`/`KU`/`KS`/`HB`), sums `osakaal` across all matching composition entries regardless of
  canopy layer. Values stay in the source's own units (whatever `osakaal` is — percentage
  points per the sample data) — no unit conversion invented at this stage. Missing species →
  `0.0`.
- `enrich_eraldis(gdf: geopandas.GeoDataFrame, wfs: WebFeatureService) -> geopandas.GeoDataFrame`
  — orchestrator. Fetches composition for `gdf`'s `id` values, attaches:
  - `composition` — full nested list per stand (source of truth, full fidelity)
  - `pine_share`, `spruce_share`, `birch_share`, `aspen_share` — computed via
    `compute_species_shares`, precomputed now since the project's scoring design already
    names these as direct inputs
  - `peapuuliik_kirjeldus` — `peapuuliik_kood` resolved via `kl_puuliik`
  - `kasvukoht_kirjeldus` — `kasvukoht_kood` resolved via `kl_kasvukoht`

### `scripts/enrich_eraldis.py`

Runner: loads `data/eraldis.geojson` → `fetch_capabilities()` → `enrich_eraldis()` →
overwrites `data/eraldis.geojson` in place.

## Data flow

```
data/eraldis.geojson (from prior branch)
        │
        ├── eraldis_id list ──► fetch_eraldis_element (CQL_FILTER, batched)
        │                              │
        │                              ▼
        │                     summarize_composition ──► {id: [composition records]}
        │                              │
        │                              ▼
        │                     compute_species_shares ──► pine/spruce/birch/aspen shares
        │
        ├── peapuuliik_kood ──► fetch_classifier(kl_puuliik) ──► peapuuliik_kirjeldus
        └── kasvukoht_kood  ──► fetch_classifier(kl_kasvukoht) ──► kasvukoht_kirjeldus
                │
                ▼
        data/eraldis.geojson (overwritten, enriched)
```

## Open question to resolve during implementation (not blocking this design)

`GeoJSON` properties can technically hold nested arrays/objects, but whether `geopandas`'s
GeoJSON writer (via `fiona`/`pyogrio`) reliably round-trips a nested-list-valued column
(`composition`) has not been verified. If a test write shows it doesn't work cleanly, the
fallback is storing `composition` as a `json.dumps`-encoded string column (with consumers
`json.loads`-ing it back) instead of a native nested value. This is an implementation-time
empirical check, not a design decision to guess at now.

## Error handling

Same posture as the existing modules: `fetch_eraldis_element` and `fetch_classifier`
(network calls) are not retried — errors from `requests`/`owslib` propagate as-is. This
remains a manually-run script, not an unattended pipeline stage.

## Testing

- `summarize_composition` and `compute_species_shares` are pure and unit tested with small
  synthetic inputs, no network.
- `fetch_eraldis_element` and `fetch_classifier` (network calls) are not unit tested —
  verified by running `scripts/enrich_eraldis.py` against the live endpoint, same pattern
  used throughout this project.
- `enrich_eraldis` (orchestrator) is exercised via the live script run, not unit tested in
  isolation, since it's mostly wiring around the already-tested pure functions and the
  untested network calls.

## Out of scope

- Neighbouring-stand calculation, ecotone detection, `HabitatScore` — later MVP steps.
- Resolving species labels *within* the nested `composition` list entries (only the
  single-value `peapuuliik_kood`/`kasvukoht_kood` columns on the main `eraldis` row are
  resolved to labels) — the classifier table is small enough to join in-memory whenever
  needed later, so this isn't precomputed now.
- Any change to `src/shroom_fm/eraldis.py` or the download/radius-filter behavior from the
  prior branch.
