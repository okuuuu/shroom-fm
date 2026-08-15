# Download Eraldis Polygons Within 80km of Home Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Download `eraldis` (forest stand) polygons from the Metsaregister WFS, restrict them to those within 80 km of a configurable home location, and save the result to a local (gitignored) GeoJSON file for later pipeline steps to read.

**Architecture:** `src/shroom_fm/config.py` loads home coordinates from a gitignored `config.toml`. `src/shroom_fm/eraldis.py` holds three functions: `compute_bbox` (pure), `filter_within_radius` (pure, uses GeoPandas/CRS reprojection), and `fetch_eraldis_bbox` (the paginated network call, reusing the `owslib` client from `wfs.py`). A thin `scripts/download_eraldis.py` runner wires them together.

**Tech Stack:** Python (`uv`-managed), `owslib` (already a dependency), `geopandas` (new dependency added in this plan), stdlib `tomllib`, `pytest`.

## Global Constraints

- Dependency/environment management: `uv` (`pyproject.toml` + `uv.lock`).
- WFS endpoint/client: reuse `fetch_capabilities()` from `src/shroom_fm/wfs.py` (already built) to get the `WebFeatureService` client — do not duplicate endpoint/version constants.
- Target layer: `metsaregister:eraldis` (confirmed real layer name from the prior GetCapabilities work).
- Package layout: reusable logic under `src/shroom_fm/`; thin runners under `scripts/`.
- No retries/fallback/custom exception handling for network calls — errors from `owslib`/`requests` propagate as-is. Exception: `load_home_location`'s missing-file case gets a clear, actionable error message (a predictable setup mistake, not a network failure).
- Privacy: `config.toml` (home coordinates) and `data/eraldis.geojson` (output, geographically correlated with home) are both gitignored. `config.example.toml` (placeholder values) is committed. `data/wfs_capabilities.json` (already committed, no personal data) is unaffected — gitignore `data/eraldis.geojson` specifically, not the whole `data/` directory.
- Radius: 80 km, passed as a function parameter / script constant — not a config value.
- Testing: `compute_bbox`, `filter_within_radius`, and `load_home_location` are pure/deterministic and unit tested. `fetch_eraldis_bbox` (network + pagination) is not unit tested — verified by running the real script against the live endpoint.
- CRS: bounding box computed and requested in WGS84 (`EPSG:4326`); precise radius filtering done in `EPSG:3301` (Estonian national grid, meters).

---

### Task 1: Home location config

**Files:**
- Create: `config.example.toml`
- Create: `src/shroom_fm/config.py`
- Test: `tests/test_config.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `CONFIG_PATH: Path` constant and `load_home_location(path: Path = CONFIG_PATH) -> tuple[float, float]` returning `(home_lat, home_lon)`. Consumed by Task 5 (`scripts/download_eraldis.py`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_config.py`:

```python
import pytest

from shroom_fm.config import load_home_location


def test_load_home_location_reads_lat_lon(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text("home_lat = 59.437\nhome_lon = 24.7536\n")

    lat, lon = load_home_location(config_file)

    assert lat == 59.437
    assert lon == 24.7536


def test_load_home_location_missing_file_raises_clear_error(tmp_path):
    missing_path = tmp_path / "config.toml"

    with pytest.raises(FileNotFoundError, match="config.example.toml"):
        load_home_location(missing_path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shroom_fm.config'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/shroom_fm/config.py`:

```python
import tomllib
from pathlib import Path

CONFIG_PATH = Path("config.toml")


def load_home_location(path: Path = CONFIG_PATH) -> tuple[float, float]:
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Copy config.example.toml to {path} and fill in "
            "your home_lat/home_lon."
        )
    with path.open("rb") as f:
        data = tomllib.load(f)
    return data["home_lat"], data["home_lon"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Create the committed example config**

Create `config.example.toml`:

```toml
home_lat = 59.4370
home_lon = 24.7536
```

(Tallinn city-center coordinates as a placeholder — not real personal data.)

- [ ] **Step 6: Gitignore the real config file**

Add to `.gitignore`, in a new section after the existing `# superpowers subagent-driven-development scratch state` block:

```
# shroom-fm local config (personal home coordinates)
config.toml
```

- [ ] **Step 7: Commit**

```bash
git add src/shroom_fm/config.py tests/test_config.py config.example.toml .gitignore
git commit -m "feat: add home location config loader"
```

---

### Task 2: `compute_bbox` — bounding box around home

**Files:**
- Create: `src/shroom_fm/eraldis.py`
- Test: `tests/test_eraldis.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `compute_bbox(lat: float, lon: float, radius_km: float) -> tuple[float, float, float, float]` returning `(minx, miny, maxx, maxy)` in WGS84 (x=longitude, y=latitude). Consumed by Task 5 (`scripts/download_eraldis.py`) and used internally to feed Task 4's `fetch_eraldis_bbox`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_eraldis.py`:

```python
import math

import pytest

from shroom_fm.eraldis import compute_bbox


def test_compute_bbox_returns_padded_box_around_point():
    lat, lon, radius_km = 59.4370, 24.7536, 80.0

    minx, miny, maxx, maxy = compute_bbox(lat, lon, radius_km)

    padded_radius_km = radius_km * 1.1
    expected_delta_lat = padded_radius_km / 111.32
    expected_delta_lon = padded_radius_km / (111.32 * math.cos(math.radians(lat)))

    assert minx == pytest.approx(lon - expected_delta_lon)
    assert maxx == pytest.approx(lon + expected_delta_lon)
    assert miny == pytest.approx(lat - expected_delta_lat)
    assert maxy == pytest.approx(lat + expected_delta_lat)

    # sanity: the unpadded radius must fit strictly inside the box
    unpadded_delta_lat = radius_km / 111.32
    assert (maxy - lat) > unpadded_delta_lat
    assert (lat - miny) > unpadded_delta_lat
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_eraldis.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shroom_fm.eraldis'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/shroom_fm/eraldis.py`:

```python
import math

KM_PER_DEGREE_LAT = 111.32
BBOX_PADDING_FACTOR = 1.1


def compute_bbox(
    lat: float, lon: float, radius_km: float
) -> tuple[float, float, float, float]:
    padded_radius_km = radius_km * BBOX_PADDING_FACTOR
    delta_lat = padded_radius_km / KM_PER_DEGREE_LAT
    delta_lon = padded_radius_km / (KM_PER_DEGREE_LAT * math.cos(math.radians(lat)))
    return (lon - delta_lon, lat - delta_lat, lon + delta_lon, lat + delta_lat)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_eraldis.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add src/shroom_fm/eraldis.py tests/test_eraldis.py
git commit -m "feat: add compute_bbox for home-radius bounding box"
```

---

### Task 3: `filter_within_radius` — precise circular cutoff

**Files:**
- Modify: `pyproject.toml`, `uv.lock` (via `uv add geopandas`)
- Modify: `src/shroom_fm/eraldis.py`
- Modify: `tests/test_eraldis.py`

**Interfaces:**
- Consumes: nothing from other tasks (independent of `compute_bbox`).
- Produces: `filter_within_radius(gdf: geopandas.GeoDataFrame, lat: float, lon: float, radius_km: float) -> geopandas.GeoDataFrame`. Consumed by Task 5 (`scripts/download_eraldis.py`).

- [ ] **Step 1: Add the geopandas dependency**

Run:
```bash
uv add geopandas
```

Expected: `geopandas` (and transitive deps including `shapely`, `pyproj`, `fiona`) appear in `pyproject.toml` and `uv.lock`.

- [ ] **Step 2: Write the failing test**

Add to `tests/test_eraldis.py`:

```python
import geopandas as gpd
from shapely.geometry import Point

from shroom_fm.eraldis import compute_bbox, filter_within_radius


def test_filter_within_radius_keeps_only_points_inside_cutoff():
    home_lat, home_lon = 59.4370, 24.7536

    home_point_3301 = (
        gpd.GeoSeries([Point(home_lon, home_lat)], crs="EPSG:4326")
        .to_crs("EPSG:3301")
        .iloc[0]
    )

    near_point = Point(home_point_3301.x + 10_000, home_point_3301.y)  # 10km away
    far_point = Point(home_point_3301.x + 200_000, home_point_3301.y)  # 200km away

    gdf = gpd.GeoDataFrame(
        {"name": ["near", "far"]},
        geometry=[near_point, far_point],
        crs="EPSG:3301",
    )

    result = filter_within_radius(gdf, home_lat, home_lon, radius_km=80.0)

    assert list(result["name"]) == ["near"]
```

Update the top of `tests/test_eraldis.py` to include this test's imports alongside the existing `compute_bbox` test — the file now imports both `compute_bbox` and `filter_within_radius` from `shroom_fm.eraldis`, plus `geopandas as gpd` and `shapely.geometry.Point`. Keep the existing `test_compute_bbox_returns_padded_box_around_point` test and its `math`/`pytest` imports intact.

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_eraldis.py -v`
Expected: FAIL with `ImportError: cannot import name 'filter_within_radius'`.

- [ ] **Step 4: Write minimal implementation**

Rewrite `src/shroom_fm/eraldis.py` in full to this exact content (adds imports and
constants at the top, and `filter_within_radius` after `compute_bbox`):

```python
import math

import geopandas as gpd
from shapely.geometry import Point

KM_PER_DEGREE_LAT = 111.32
BBOX_PADDING_FACTOR = 1.1
ESTONIAN_GRID_CRS = "EPSG:3301"
WGS84_CRS = "EPSG:4326"


def compute_bbox(
    lat: float, lon: float, radius_km: float
) -> tuple[float, float, float, float]:
    padded_radius_km = radius_km * BBOX_PADDING_FACTOR
    delta_lat = padded_radius_km / KM_PER_DEGREE_LAT
    delta_lon = padded_radius_km / (KM_PER_DEGREE_LAT * math.cos(math.radians(lat)))
    return (lon - delta_lon, lat - delta_lat, lon + delta_lon, lat + delta_lat)


def filter_within_radius(
    gdf: gpd.GeoDataFrame, lat: float, lon: float, radius_km: float
) -> gpd.GeoDataFrame:
    projected = gdf.to_crs(ESTONIAN_GRID_CRS)
    home_point = (
        gpd.GeoSeries([Point(lon, lat)], crs=WGS84_CRS)
        .to_crs(ESTONIAN_GRID_CRS)
        .iloc[0]
    )
    distances_km = projected.geometry.distance(home_point) / 1000.0
    return gdf[distances_km <= radius_km]
```

This consolidates the constants (`KM_PER_DEGREE_LAT`, `BBOX_PADDING_FACTOR` from Task 2,
plus the two new CRS constants) at the top of the file, after imports and before any
function — keep this layout going forward in Task 4.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_eraldis.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/shroom_fm/eraldis.py tests/test_eraldis.py
git commit -m "feat: add filter_within_radius precise circular cutoff"
```

---

### Task 4: `fetch_eraldis_bbox` — paginated network call

**Files:**
- Modify: `src/shroom_fm/eraldis.py`

**Interfaces:**
- Consumes: `WebFeatureService` type from `owslib.wfs` (already used in `src/shroom_fm/wfs.py`); the `(minx, miny, maxx, maxy)` tuple shape produced by Task 2's `compute_bbox`.
- Produces: `fetch_eraldis_bbox(wfs: WebFeatureService, bbox: tuple[float, float, float, float]) -> geopandas.GeoDataFrame`. Consumed by Task 5 (`scripts/download_eraldis.py`).

No test for this step (per Global Constraints: the network/pagination call is verified by running the real script in Task 5, not unit tested).

- [ ] **Step 1: Add the function**

Add two new imports (`io` and `pandas as pd`, plus `WebFeatureService` for the type hint)
to the top of `src/shroom_fm/eraldis.py`, two new constants alongside the existing ones,
and the new function appended after `filter_within_radius` (the last function in the file
from Task 3):

```python
import io
import math

import geopandas as gpd
import pandas as pd
from owslib.wfs import WebFeatureService
from shapely.geometry import Point

KM_PER_DEGREE_LAT = 111.32
BBOX_PADDING_FACTOR = 1.1
ESTONIAN_GRID_CRS = "EPSG:3301"
WGS84_CRS = "EPSG:4326"
ERALDIS_TYPENAME = "metsaregister:eraldis"
PAGE_SIZE = 1000


def compute_bbox(
    lat: float, lon: float, radius_km: float
) -> tuple[float, float, float, float]:
    padded_radius_km = radius_km * BBOX_PADDING_FACTOR
    delta_lat = padded_radius_km / KM_PER_DEGREE_LAT
    delta_lon = padded_radius_km / (KM_PER_DEGREE_LAT * math.cos(math.radians(lat)))
    return (lon - delta_lon, lat - delta_lat, lon + delta_lon, lat + delta_lat)


def filter_within_radius(
    gdf: gpd.GeoDataFrame, lat: float, lon: float, radius_km: float
) -> gpd.GeoDataFrame:
    projected = gdf.to_crs(ESTONIAN_GRID_CRS)
    home_point = (
        gpd.GeoSeries([Point(lon, lat)], crs=WGS84_CRS)
        .to_crs(ESTONIAN_GRID_CRS)
        .iloc[0]
    )
    distances_km = projected.geometry.distance(home_point) / 1000.0
    return gdf[distances_km <= radius_km]


def fetch_eraldis_bbox(
    wfs: WebFeatureService, bbox: tuple[float, float, float, float]
) -> gpd.GeoDataFrame:
    pages = []
    start_index = 0
    while True:
        response = wfs.getfeature(
            typename=ERALDIS_TYPENAME,
            bbox=bbox,
            srsname=WGS84_CRS,
            outputFormat="application/json",
            startindex=start_index,
            maxfeatures=PAGE_SIZE,
        )
        page = gpd.read_file(io.BytesIO(response.read()))
        pages.append(page)
        if len(page) < PAGE_SIZE:
            break
        start_index += PAGE_SIZE
    return gpd.GeoDataFrame(pd.concat(pages, ignore_index=True), crs=pages[0].crs)
```

This is the complete file after this task: imports, then all constants together, then
`compute_bbox`, `filter_within_radius`, `fetch_eraldis_bbox` in the order they were added
across Tasks 2-4. `fetch_eraldis_bbox` references `WGS84_CRS` and `ERALDIS_TYPENAME`, both
already defined above it — no forward references.

- [ ] **Step 2: Run the existing test suite to confirm nothing broke**

Run: `uv run pytest tests/test_eraldis.py tests/test_config.py -v`
Expected: PASS (3 passed) — this step only adds code, it doesn't change `compute_bbox`/`filter_within_radius` behavior.

- [ ] **Step 3: Sanity-check the import resolves**

Run:
```bash
uv run python -c "from shroom_fm.eraldis import fetch_eraldis_bbox, compute_bbox, filter_within_radius, ERALDIS_TYPENAME; print(ERALDIS_TYPENAME)"
```
Expected output: `metsaregister:eraldis`

(This only checks imports and the constant — it does not make a network call. The real network call, including pagination behavior, is exercised in Task 5, Step 2.)

- [ ] **Step 4: Commit**

```bash
git add src/shroom_fm/eraldis.py
git commit -m "feat: add fetch_eraldis_bbox paginated WFS query"
```

---

### Task 5: `scripts/download_eraldis.py` — runnable download script

**Files:**
- Create: `scripts/download_eraldis.py`
- Modify: `.gitignore`
- Create (at runtime, gitignored — not committed): `data/eraldis.geojson`

**Interfaces:**
- Consumes: `load_home_location` (Task 1), `compute_bbox`/`fetch_eraldis_bbox`/`filter_within_radius` (Tasks 2-4), `fetch_capabilities` (already in `src/shroom_fm/wfs.py`).
- Produces: nothing consumed by other tasks — this is the pipeline's end-user entry point for this step.

- [ ] **Step 1: Gitignore the output file**

Add to `.gitignore`, in the same section added in Task 1 Step 6 (or immediately after it):

```
# shroom-fm local output (geographically correlated with home location)
data/eraldis.geojson
```

- [ ] **Step 2: Write the runner script**

Create `scripts/download_eraldis.py`:

```python
from pathlib import Path

from shroom_fm.config import load_home_location
from shroom_fm.eraldis import compute_bbox, fetch_eraldis_bbox, filter_within_radius
from shroom_fm.wfs import fetch_capabilities

RADIUS_KM = 80.0
OUTPUT_PATH = Path("data/eraldis.geojson")


def main() -> None:
    home_lat, home_lon = load_home_location()
    wfs = fetch_capabilities()

    bbox = compute_bbox(home_lat, home_lon, RADIUS_KM)
    gdf = fetch_eraldis_bbox(wfs, bbox)
    nearby = filter_within_radius(gdf, home_lat, home_lon, RADIUS_KM)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    nearby.to_file(OUTPUT_PATH, driver="GeoJSON")

    print(f"{len(nearby)} stands within {RADIUS_KM:.0f}km of home")
    print(f"Saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Create your local config**

This step requires a real `config.toml` with your actual home coordinates, which is not part of this plan's committed content (it's gitignored — see Task 1). Before running the script:

```bash
cp config.example.toml config.toml
```

Then edit `config.toml` and replace the placeholder `home_lat`/`home_lon` with your real coordinates.

- [ ] **Step 4: Run it against the live Metsaregister endpoint**

Run:
```bash
uv run scripts/download_eraldis.py
```

Expected: prints a line like `N stands within 80km of home`, followed by `Saved to data/eraldis.geojson`.

If this fails, treat it as a real finding rather than silently working around it — in particular:
- A `KeyError` on `home_lat`/`home_lon` means Step 3 (creating `config.toml`) wasn't done — do that first, this is not a code defect.
- An error from `owslib`/`fiona` about the response format or pagination parameters is a genuine integration finding — stop and report the exact error rather than guessing a fix, the same way a WFS version-negotiation error would be handled.

- [ ] **Step 5: Confirm the output file**

Run:
```bash
uv run python -c "import geopandas as gpd; gdf = gpd.read_file('data/eraldis.geojson'); print(len(gdf), 'rows'); print(gdf.columns.tolist())"
```
Expected: a row count greater than 0, and a column list that includes `geometry` plus `eraldis` attribute columns (exact attribute names depend on what the live server returns — report them, don't assume specific names beyond `geometry`).

- [ ] **Step 6: Run the full test suite one more time**

Run: `uv run pytest -v`
Expected: PASS (6 passed: 2 in `test_wfs.py`, 2 in `test_config.py`, 2 in `test_eraldis.py`), confirming Task 5's changes didn't touch tested behavior.

- [ ] **Step 7: Commit**

```bash
git add scripts/download_eraldis.py .gitignore
git commit -m "feat: add download_eraldis runner script"
```

(Do not `git add data/eraldis.geojson` — it's gitignored per Task 5 Step 1, and `git status` should confirm it does not appear as a trackable change.)

---

## Post-plan note

This plan only covers MVP steps 1-2 (download + radius restriction) from `CLAUDE.md`. The next steps — joining tree composition (`eraldis_element`) and `kasvukohatüüp` — are separate follow-up work, not part of this plan.
