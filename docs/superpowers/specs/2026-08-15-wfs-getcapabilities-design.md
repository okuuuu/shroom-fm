# WFS GetCapabilities Script — Design

Date: 2026-08-15
Status: Approved

## Purpose

This is the first piece of code in shroom-fm and the first step of the MVP pipeline
described in `CLAUDE.md`: confirm the real layer names exposed by the Metsaregister WFS
(GeoServer OWS) endpoint before any later script hardcodes them. It queries
`GetCapabilities` against `https://gsavalik.envir.ee/geoserver/metsaregister/ows` and
produces a readable, saved list of available layers (name, title, abstract).

It also establishes the project's Python tooling and package layout, since no code exists
yet.

## Project setup

- Dependency/environment management: **uv** (`pyproject.toml` + `uv.lock`).
- First dependency: `owslib` — the standard Python client for OGC WFS/WMS services.
- Layout: an installable package under `src/shroom_fm/`, plus a `scripts/` directory for
  thin, directly-runnable entry points. This mirrors how later pipeline steps (join tree
  composition, join `kasvukohatüüp`, scoring, export) will be organized: reusable logic in
  the package, one small runner script per pipeline step.
- Run via `uv run scripts/get_capabilities.py`.

## Components

### `src/shroom_fm/wfs.py`

- `METSAREGISTER_OWS_URL` — module-level constant:
  `https://gsavalik.envir.ee/geoserver/metsaregister/ows`.
- `fetch_capabilities(url: str = METSAREGISTER_OWS_URL) -> WebFeatureService` — thin wrapper
  around `owslib.wfs.WebFeatureService(url, version="2.0.0")`. Performs the network call.
  This same client type is intended for reuse in the next pipeline step (the actual
  `GetFeature` polygon download), so this function is the seed of that shared client code,
  not a one-off.
- `layer_summary(wfs: WebFeatureService) -> list[dict]` — pure function. Iterates
  `wfs.contents` and returns a list of
  `{"name": str, "title": str | None, "abstract": str | None}`, sorted by `name`. No I/O; the
  natural unit for a fast unit test.
- `save_layers_json(layers: list[dict], path: Path) -> None` — writes `layers` as
  pretty-printed JSON to `path`, creating parent directories as needed.

### `scripts/get_capabilities.py`

- Calls `fetch_capabilities()`, then `layer_summary()`, then:
  - prints one `name — title` line per layer to stdout, for immediate human inspection;
  - calls `save_layers_json(layers, Path("data/wfs_capabilities.json"))` so the confirmed
    layer list is recorded in the repo for later scripts/steps to reference or diff against.

## Data flow

```
GET .../ows?service=WFS&request=GetCapabilities&version=2.0.0  (via owslib)
        │
        ▼
  WebFeatureService.contents            (owslib-parsed capabilities)
        │
        ▼
  layer_summary()  →  list[{name, title, abstract}]
        │
        ├── printed to stdout (human check)
        └── save_layers_json() → data/wfs_capabilities.json (recorded reference)
```

## Error handling

No retries, fallback logic, or custom exception handling. This script is run manually and
interactively as a diagnostic step, not as part of an unattended pipeline yet. Network
failures or WFS version-negotiation problems from `owslib`/`requests` are allowed to
propagate with their own error messages — sufficient to act on in this context. Revisit if
and when this logic moves into an unattended/scheduled pipeline.

## Testing

- `layer_summary` and `save_layers_json` are pure functions and are unit tested:
  - `layer_summary` against a small fake object exposing a `.contents`-like mapping (no real
    `owslib` network object needed).
  - `save_layers_json` against a `tmp_path`, checking the written JSON content and that
    parent directories are created.
- `fetch_capabilities` (the actual network call) is not unit tested. It is verified by
  running `scripts/get_capabilities.py` once against the live endpoint and confirming real
  layer names appear (expected: something resembling `eraldis`, `eraldis_element`, and
  classifier layers, per `CLAUDE.md`).

## Out of scope

- Actual feature (`GetFeature`) download of `eraldis` polygons — next pipeline step.
- WMS usage — not needed for data extraction (see `CLAUDE.md`).
- Any scoring, joining, or filtering logic.
- Config files / env vars for the endpoint URL — it's a fixed public constant for now.
