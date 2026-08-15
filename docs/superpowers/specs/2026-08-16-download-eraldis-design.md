# Download Eraldis Polygons Within 80km of Home — Design

Date: 2026-08-16
Status: Approved

## Purpose

This is MVP pipeline steps 1–2 from `CLAUDE.md`: download `eraldis` (forest stand) polygons
from the Metsaregister WFS and restrict them to those within 80 km of home. It builds on the
prior GetCapabilities work (`src/shroom_fm/wfs.py`), which confirmed the real layer name
`metsaregister:eraldis` and established the `owslib`-based client pattern this step reuses.

The output is a GeoJSON file of nearby stands that later pipeline steps (join tree
composition, join `kasvukohatüüp`, scoring) will read from, without re-hitting the network
each time.

## Config: home location

Home coordinates are personal data and are kept out of git:

- `config.toml` (gitignored) — holds `home_lat` / `home_lon`. Not created by this
  implementation; the user fills it in themselves after the code lands, copying it from:
- `config.example.toml` (committed) — same shape, placeholder values, documents the format.
- `src/shroom_fm/config.py`:
  - `CONFIG_PATH = Path("config.toml")`
  - `load_home_location(path: Path = CONFIG_PATH) -> tuple[float, float]` — reads TOML via
    stdlib `tomllib`, returns `(home_lat, home_lon)`. If the file is missing, raises with a
    clear, actionable message ("copy config.example.toml to config.toml and fill in your
    coordinates") rather than letting a raw `FileNotFoundError` traceback surface.

## Components

### `src/shroom_fm/eraldis.py` (new module)

Kept separate from `wfs.py` — `wfs.py` is scoped to the GetCapabilities/layer-discovery
concern; this module owns downloading and geographically filtering actual feature data, a
distinct responsibility.

- `compute_bbox(lat: float, lon: float, radius_km: float) -> tuple[float, float, float, float]`
  — pure function. Returns a WGS84 (EPSG:4326) bounding box `(minx, miny, maxx, maxy)`
  around `(lat, lon)`, sized to fully contain a circle of `radius_km`. A bounding box always
  over-covers a circle (extra area at the corners) — that's fine, this is a server-side
  pre-filter, not the final cutoff.
- `fetch_eraldis_bbox(wfs: WebFeatureService, bbox: tuple[float, float, float, float]) -> GeoDataFrame`
  — the network call. Requests `metsaregister:eraldis` via
  `wfs.getfeature(typename="metsaregister:eraldis", bbox=bbox, srsname="EPSG:4326", outputFormat="application/json")`,
  loads the response into a GeoDataFrame with `geopandas.read_file()`. Loops on
  `startindex`/page size, concatenating pages, until a response returns fewer rows than the
  page size — defensive against the server capping results per request (a real WFS
  behavior, unconfirmed for this specific endpoint until run live).
- `filter_within_radius(gdf: GeoDataFrame, lat: float, lon: float, radius_km: float) -> GeoDataFrame`
  — pure function. Reprojects `gdf` to `EPSG:3301` (Estonian national grid, meters) for
  accurate planar distance, computes each row's distance from `(lat, lon)`, and returns only
  rows with distance `<= radius_km`. This is what turns the bbox's square into the actual
  circular ≤80 km cutoff the plan requires.

### `scripts/download_eraldis.py`

Runner: `load_home_location()` → `fetch_capabilities()` (reused from `wfs.py`) →
`compute_bbox()` → `fetch_eraldis_bbox()` → `filter_within_radius()` → save to
`data/eraldis.geojson` via `GeoDataFrame.to_file(path, driver="GeoJSON")`. Prints a count
summary, e.g. `"N stands within 80km of home"`.

## Data flow

```
config.toml (home_lat, home_lon)
        │
        ▼
compute_bbox(lat, lon, 80)  →  WGS84 bbox (square, over-covers)
        │
        ▼
fetch_eraldis_bbox(wfs, bbox)  →  GeoDataFrame (paginated GetFeature, bbox pre-filter)
        │
        ▼
filter_within_radius(gdf, lat, lon, 80)  →  GeoDataFrame (precise circular cutoff)
        │
        ▼
data/eraldis.geojson  (+ printed count summary)
```

## Error handling

Same posture as the GetCapabilities script: no retries or fallback logic for the network
call in `fetch_eraldis_bbox` — errors from `owslib`/`requests` propagate as-is, since this
remains a manually-run diagnostic/data-prep script, not an unattended pipeline stage. The one
addition is `load_home_location`'s clear missing-config error, since that is a predictable
first-run setup mistake worth a good message.

## Testing

- `compute_bbox` — unit tested with known lat/lon/radius inputs, asserting the returned box
  fully contains the expected circle extent.
- `filter_within_radius` — unit tested against a small synthetic GeoDataFrame (a handful of
  points at known distances from a reference home point), asserting the correct subset
  passes the radius cutoff. No network involved.
- `load_home_location` — unit tested against a `tmp_path` TOML fixture (happy path) and the
  missing-file case (asserts the clear error message).
- `fetch_eraldis_bbox` — not unit tested (network + pagination against a live WFS server).
  Verified by running `scripts/download_eraldis.py` against the live endpoint, same
  verification pattern used for `fetch_capabilities` in the prior branch.

## Out of scope

- Joining tree composition (`eraldis_element`) or `kasvukohatüüp` — later MVP steps.
- Neighbouring-stand calculation, ecotone detection, scoring — later MVP steps.
- Any UI/viewer beyond the saved GeoJSON file.
- Config file format for anything beyond home coordinates (e.g. radius is a function
  parameter/constant for now, not a config value, since 80 km is the plan's stated default).
