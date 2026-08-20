# Macroclustering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a stable, geography-driven "operational search region" layer
(`forest_block` → `macrocluster`) between raw `eraldis` and the existing per-day
`ScoutScore` ranking, plus a daily rollup summarizing each macrocluster's current
candidates.

**Architecture:** `forest_block`s are connected components of the already-shipped
adjacency graph. `macrocluster`s are a connectivity-constrained, complete-linkage
partition of a new block-level proximity graph, with a real geometry-extent cap
enforced (not just a centroid-distance approximation) via bounded recursive
repartitioning. Macrocluster membership never depends on any score/weather — a
separate daily-state file rolls today's `scout_candidates.geojson` up by cluster.

**Tech Stack:** Python, existing project stack (geopandas/pandas/numpy) plus two new
dependencies this plan adds: `networkx` (graph construction/connected components) and
`scikit-learn` (connectivity-constrained `AgglomerativeClustering`).

**Spec:** `docs/superpowers/specs/2026-08-20-macroclustering-design.md`

## Global Constraints

- Macrocluster/forest_block membership must never depend on `StandHabitatScore`,
  `EcotoneScore`, `FruitingScore`, or `ScoutScore` — only geometry/adjacency/proximity.
  This is architectural, not just convention: the new pipeline steps run right after
  `compute_adjacency`, before any scoring step.
- `data/macroclusters.geojson` is the **stable base** — geometry, extent, block/eraldis
  counts, diagnostic flags only. Never today's scores. Regenerated only when
  `eraldis`/adjacency data changes, not on every run.
- `data/macrocluster_state.geojson` is **today's snapshot only** (has `as_of`),
  regenerated every `export_scout_candidates.py`+rollup run, kept as a separate file
  from `macroclusters.geojson` on purpose.
- All v0 numeric constants (`BLOCK_NEIGHBOR_PROXY_M`, `MACROCLUSTER_MAX_EXTENT_M`,
  `MACROCLUSTER_TARGET_EXTENT_M`, `TARGET_BLOCK_COUNT`) are geometric proxies for a
  future road-network travel-time graph, not asserted travel-time facts — same
  documentation discipline as this project's other v0 engineering priors.
- `MACROCLUSTER_MAX_EXTENT_M` is a **hard** cap, enforced against real
  `geometry_extent_m` (convex-hull diameter of the actual unioned geometry), not just
  centroid distance — centroid distance is only the clustering algorithm's input
  metric, never the thing the cap is checked against.
- `MACROCLUSTER_TARGET_EXTENT_M` and `TARGET_BLOCK_COUNT` are **diagnostics only** —
  never used to force a split. A naturally compact region of very few large blocks (or
  very many tiny ones) stays one macrocluster.
- "Never fabricate, never silently drop data" — this project's core discipline — applies
  throughout: a macrocluster with zero candidates for a species gets `None` fields in
  the daily rollup, never a fabricated `0`/neutral value; a cross-macrocluster ecotone
  is counted and warned about, never silently misassigned without a trace.
- `TARGET_SPECIES` is imported from `shroom_fm.habitat`, matching the convention
  `scripts/export_scout_candidates.py` already uses (not `shroom_fm.fruiting`'s own
  copy — the two are required to stay identical, but the export/rollup layer has
  always imported the `habitat.py` one).
- Baseline before this plan: 223 tests passing (`uv run pytest tests/ -q`).

---

### Task 1: `forest_block.py` — forest blocks from the existing adjacency graph

**Files:**
- Create: `src/shroom_fm/forest_block.py`
- Test: `tests/test_forest_block.py`
- Modify: `pyproject.toml` (add `networkx` dependency)

**Interfaces:**
- Produces: `geometry_extent_m(geometry) -> float` (geometry must already be in a
  projected/meters CRS); `MACROCLUSTER_TARGET_EXTENT_M = 25_000` (module constant,
  reused — not redefined — by `macrocluster.py` in later tasks);
  `compute_forest_blocks(eraldis_gdf: gpd.GeoDataFrame, adjacency_gdf: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]`
  — returns `(eraldis_gdf with a new forest_block_id column, forest_blocks_gdf)`.
  `forest_blocks_gdf` columns: `forest_block_id`, `geometry`, `eraldis_count`,
  `geometry_extent_m`, `oversized_block`.

- [ ] **Step 1: Add the `networkx` dependency**

Edit `pyproject.toml`'s `dependencies` list (currently: `geopandas>=1.1.4`,
`h5py>=3.16.0`, `netcdf4>=1.7.4`, `owslib>=0.36.0`, `xarray>=2026.7.0`) to add
`"networkx>=3.4",` (alphabetically, after `netcdf4`). Run `uv sync` and confirm it
completes without error.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_forest_block.py`:

```python
import geopandas as gpd
import pytest
from shapely.geometry import Point, box

from shroom_fm.forest_block import (
    MACROCLUSTER_TARGET_EXTENT_M,
    compute_forest_blocks,
    geometry_extent_m,
)


def test_geometry_extent_m_of_a_square():
    square = box(0, 0, 100, 100)
    assert geometry_extent_m(square) == pytest.approx(141.4213562373095)


def test_geometry_extent_m_of_a_point_is_zero():
    assert geometry_extent_m(Point(0, 0)) == 0.0


def test_geometry_extent_m_of_two_collinear_points():
    line = box(0, 0, 100, 0.0000001).convex_hull  # degenerately thin, effectively a line
    # Use an actual LineString to be explicit about the degenerate hull case:
    from shapely.geometry import LineString

    assert geometry_extent_m(LineString([(0, 0), (100, 0)]).convex_hull) == pytest.approx(100.0)


def test_compute_forest_blocks_merges_touching_trio():
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1, 2, 3]},
        geometry=[box(0, 0, 100, 100), box(100, 0, 200, 100), box(200, 0, 300, 100)],
        crs="EPSG:3301",
    )
    adjacency_gdf = gpd.GeoDataFrame(
        {"id_a": [1, 2], "id_b": [2, 3]},
        geometry=[Point(100, 50), Point(200, 50)],
        crs="EPSG:3301",
    )

    eraldis_result, blocks_gdf = compute_forest_blocks(eraldis_gdf, adjacency_gdf)

    assert len(blocks_gdf) == 1
    assert blocks_gdf.iloc[0]["eraldis_count"] == 3
    assert eraldis_result["forest_block_id"].nunique() == 1


def test_compute_forest_blocks_keeps_disconnected_pair_separate():
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1, 2]},
        geometry=[box(0, 0, 100, 100), box(100_000, 0, 100_100, 100)],
        crs="EPSG:3301",
    )
    adjacency_gdf = gpd.GeoDataFrame({"id_a": [], "id_b": []}, geometry=[], crs="EPSG:3301")

    eraldis_result, blocks_gdf = compute_forest_blocks(eraldis_gdf, adjacency_gdf)

    assert len(blocks_gdf) == 2
    assert list(blocks_gdf["eraldis_count"]) == [1, 1]
    assert eraldis_result["forest_block_id"].nunique() == 2


def test_compute_forest_blocks_isolated_stand_is_its_own_singleton_block():
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1]}, geometry=[box(0, 0, 100, 100)], crs="EPSG:3301"
    )
    adjacency_gdf = gpd.GeoDataFrame({"id_a": [], "id_b": []}, geometry=[], crs="EPSG:3301")

    eraldis_result, blocks_gdf = compute_forest_blocks(eraldis_gdf, adjacency_gdf)

    assert len(blocks_gdf) == 1
    assert blocks_gdf.iloc[0]["eraldis_count"] == 1
    assert eraldis_result.iloc[0]["forest_block_id"] == blocks_gdf.iloc[0]["forest_block_id"]


def test_compute_forest_blocks_flags_oversized_block():
    side = MACROCLUSTER_TARGET_EXTENT_M + 1000
    big_square = box(0, 0, side, side)
    eraldis_gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[big_square], crs="EPSG:3301")
    adjacency_gdf = gpd.GeoDataFrame({"id_a": [], "id_b": []}, geometry=[], crs="EPSG:3301")

    _, blocks_gdf = compute_forest_blocks(eraldis_gdf, adjacency_gdf)

    assert blocks_gdf.iloc[0]["oversized_block"] == True


def test_compute_forest_blocks_not_oversized_when_under_threshold():
    small_square = box(0, 0, 100, 100)
    eraldis_gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[small_square], crs="EPSG:3301")
    adjacency_gdf = gpd.GeoDataFrame({"id_a": [], "id_b": []}, geometry=[], crs="EPSG:3301")

    _, blocks_gdf = compute_forest_blocks(eraldis_gdf, adjacency_gdf)

    assert blocks_gdf.iloc[0]["oversized_block"] == False
```

- [ ] **Step 3: Run to verify it fails**

Run: `uv run pytest tests/test_forest_block.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shroom_fm.forest_block'`

- [ ] **Step 4: Implement `src/shroom_fm/forest_block.py`**

```python
import geopandas as gpd
import networkx as nx
from shapely.geometry import Point

from shroom_fm.eraldis import ESTONIAN_GRID_CRS, WGS84_CRS

# Diagnostic threshold only — a block flagged oversized isn't auto-split in v0.
# See macrocluster.py for the hard MACROCLUSTER_MAX_EXTENT_M cap this is set
# relative to (imported from here, not redefined, to avoid the two drifting apart).
MACROCLUSTER_TARGET_EXTENT_M = 25_000


def geometry_extent_m(geometry) -> float:
    """Max pairwise distance between vertices of geometry's convex hull — a cheap,
    exact diameter measurement since hull vertex count is small, and the two points
    achieving maximum pairwise distance in any point set are always both on its
    convex hull. `geometry` must already be in a projected (meters) CRS."""
    hull = geometry.convex_hull
    if hull.geom_type == "Point":
        return 0.0
    elif hull.geom_type == "LineString":
        coords = list(hull.coords)
    else:
        coords = list(hull.exterior.coords)
    points = [Point(c) for c in coords]
    return max(
        (a.distance(b) for i, a in enumerate(points) for b in points[i + 1 :]),
        default=0.0,
    )


def compute_forest_blocks(
    eraldis_gdf: gpd.GeoDataFrame, adjacency_gdf: gpd.GeoDataFrame
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    graph = nx.Graph()
    graph.add_nodes_from(eraldis_gdf["id"])
    graph.add_edges_from(zip(adjacency_gdf["id_a"], adjacency_gdf["id_b"]))

    components = [set(c) for c in nx.connected_components(graph)]
    # Deterministic numbering: sort components by their minimum member id so
    # re-running against unchanged input reproduces the same forest_block_ids.
    components.sort(key=min)

    id_to_block = {}
    for block_id, member_ids in enumerate(components):
        for eraldis_id in member_ids:
            id_to_block[eraldis_id] = block_id

    result = eraldis_gdf.copy()
    result["forest_block_id"] = result["id"].map(id_to_block)

    projected = eraldis_gdf.to_crs(ESTONIAN_GRID_CRS)
    id_to_geom = dict(zip(projected["id"], projected.geometry))

    records = []
    for block_id, member_ids in enumerate(components):
        member_geoms = [id_to_geom[i] for i in member_ids]
        dissolved = gpd.GeoSeries(member_geoms, crs=ESTONIAN_GRID_CRS).union_all()
        extent = geometry_extent_m(dissolved)
        records.append(
            {
                "forest_block_id": block_id,
                "eraldis_count": len(member_ids),
                "geometry_extent_m": extent,
                "oversized_block": extent > MACROCLUSTER_TARGET_EXTENT_M,
                "geometry": dissolved,
            }
        )

    blocks_gdf = gpd.GeoDataFrame(records, crs=ESTONIAN_GRID_CRS).to_crs(WGS84_CRS)
    return result, blocks_gdf
```

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/test_forest_block.py -v`
Expected: 9 passed

- [ ] **Step 6: Run the full suite to confirm no regressions**

Run: `uv run pytest tests/ -q`
Expected: 232 passed (223 baseline + 9 new)

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock src/shroom_fm/forest_block.py tests/test_forest_block.py
git commit -m "feat: add forest_block construction from the existing adjacency graph"
```

---

### Task 2: `scripts/compute_forest_blocks.py` + `main.py` wiring

**Files:**
- Create: `scripts/compute_forest_blocks.py`
- Modify: `main.py`

**Interfaces:**
- Consumes: `forest_block.compute_forest_blocks(eraldis_gdf, adjacency_gdf) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]` from Task 1.
- Produces: `data/forest_blocks.geojson`; adds `forest_block_id` to `data/eraldis.geojson`.

- [ ] **Step 1: Implement `scripts/compute_forest_blocks.py`**

```python
from pathlib import Path

import geopandas as gpd

from shroom_fm.forest_block import compute_forest_blocks

ERALDIS_PATH = Path("data/eraldis.geojson")
ADJACENCY_PATH = Path("data/adjacency.geojson")
FOREST_BLOCKS_PATH = Path("data/forest_blocks.geojson")


def main() -> None:
    eraldis_gdf = gpd.read_file(ERALDIS_PATH)
    adjacency_gdf = gpd.read_file(ADJACENCY_PATH)

    eraldis_gdf, blocks_gdf = compute_forest_blocks(eraldis_gdf, adjacency_gdf)

    eraldis_gdf.to_file(ERALDIS_PATH, driver="GeoJSON")
    blocks_gdf.to_file(FOREST_BLOCKS_PATH, driver="GeoJSON")

    print(
        f"{len(blocks_gdf)} forest blocks from {len(eraldis_gdf)} eraldis, "
        f"{int(blocks_gdf['oversized_block'].sum())} oversized, "
        f"saved to {FOREST_BLOCKS_PATH}"
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify it imports cleanly**

Run: `uv run python3 -c "import scripts.compute_forest_blocks"`
Expected: no error

- [ ] **Step 3: Wire into `main.py`**

`main.py`'s current `STEPS` list (do not touch anything outside what's shown):

```python
from scripts import (
    compute_adjacency,
    download_eraldis,
    download_roads,
    enrich_eraldis,
    export_scout_candidates,
    score_access,
    score_ecotone_fruiting,
    score_ecotone_habitat,
    score_ecotones,
    score_fruiting,
    score_habitat,
)

STEPS = [
    ("download_eraldis", download_eraldis.main),
    ("enrich_eraldis", enrich_eraldis.main),
    ("compute_adjacency", compute_adjacency.main),
    ("score_ecotones", score_ecotones.main),
    ("score_habitat", score_habitat.main),
    ("score_ecotone_habitat", score_ecotone_habitat.main),
    ("download_roads", download_roads.main),
    ("score_access", score_access.main),
    ("score_fruiting", score_fruiting.main),
    ("score_ecotone_fruiting", score_ecotone_fruiting.main),
    ("export_scout_candidates", export_scout_candidates.main),
]
```

Add `compute_forest_blocks` to the import block (alphabetically, after
`compute_adjacency`) and insert the new step into `STEPS` right after
`compute_adjacency` (before `score_ecotones`) — macrocluster construction depends only
on `eraldis`/adjacency, not on any score, and must run before every scoring step to
keep that dependency direction explicit in the pipeline itself, not just in comments:

```python
from scripts import (
    compute_adjacency,
    compute_forest_blocks,
    download_eraldis,
    download_roads,
    enrich_eraldis,
    export_scout_candidates,
    score_access,
    score_ecotone_fruiting,
    score_ecotone_habitat,
    score_ecotones,
    score_fruiting,
    score_habitat,
)

STEPS = [
    ("download_eraldis", download_eraldis.main),
    ("enrich_eraldis", enrich_eraldis.main),
    ("compute_adjacency", compute_adjacency.main),
    ("compute_forest_blocks", compute_forest_blocks.main),
    ("score_ecotones", score_ecotones.main),
    ("score_habitat", score_habitat.main),
    ("score_ecotone_habitat", score_ecotone_habitat.main),
    ("download_roads", download_roads.main),
    ("score_access", score_access.main),
    ("score_fruiting", score_fruiting.main),
    ("score_ecotone_fruiting", score_ecotone_fruiting.main),
    ("export_scout_candidates", export_scout_candidates.main),
]
```

- [ ] **Step 4: Verify `main.py` still imports and lists the new step**

Run: `uv run python3 -c "import main; print([n for n, _ in main.STEPS])"`
Expected: prints a list containing `'compute_forest_blocks'` positioned right after
`'compute_adjacency'`

- [ ] **Step 5: Run the full suite to confirm no regressions**

Run: `uv run pytest tests/ -q`
Expected: 232 passed (this task adds no new tests — no dedicated test file, matching
this project's established thin-orchestrator-script convention)

- [ ] **Step 6: Commit**

```bash
git add scripts/compute_forest_blocks.py main.py
git commit -m "feat: add compute_forest_blocks.py pipeline script"
```

---

### Task 3: `macrocluster.py` — block-level proximity graph

**Files:**
- Create: `src/shroom_fm/macrocluster.py`
- Test: `tests/test_macrocluster.py`
- Modify: `pyproject.toml` (add `scikit-learn` dependency — needed by Task 4, added
  here since this task starts the module and both tasks share one `uv sync`)

**Interfaces:**
- Consumes: `forest_block.geometry_extent_m`, `forest_block.MACROCLUSTER_TARGET_EXTENT_M` from Task 1.
- Produces: `BLOCK_NEIGHBOR_PROXY_M = 8_000` (module constant);
  `build_block_proximity_graph(forest_blocks_gdf: gpd.GeoDataFrame) -> networkx.Graph`
  — nodes are `forest_block_id` values, edges exist between blocks within
  `BLOCK_NEIGHBOR_PROXY_M` of each other (boundary-to-boundary distance).

- [ ] **Step 1: Add the `scikit-learn` dependency**

Edit `pyproject.toml`'s `dependencies` list to add `"scikit-learn>=1.7",`
(alphabetically, after `owslib`). Run `uv sync` and confirm it completes without error.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_macrocluster.py`:

```python
import geopandas as gpd
from shapely.geometry import box

from shroom_fm.macrocluster import BLOCK_NEIGHBOR_PROXY_M, build_block_proximity_graph


def test_build_block_proximity_graph_connects_nearby_blocks():
    # Two blocks 3km apart — well within BLOCK_NEIGHBOR_PROXY_M (8km)
    blocks_gdf = gpd.GeoDataFrame(
        {"forest_block_id": [0, 1]},
        geometry=[box(0, 0, 100, 100), box(3_100, 0, 3_200, 100)],
        crs="EPSG:3301",
    )

    graph = build_block_proximity_graph(blocks_gdf)

    assert graph.has_edge(0, 1)


def test_build_block_proximity_graph_does_not_connect_far_blocks():
    # Two blocks ~20km apart — beyond BLOCK_NEIGHBOR_PROXY_M (8km)
    blocks_gdf = gpd.GeoDataFrame(
        {"forest_block_id": [0, 1]},
        geometry=[box(0, 0, 100, 100), box(20_100, 0, 20_200, 100)],
        crs="EPSG:3301",
    )

    graph = build_block_proximity_graph(blocks_gdf)

    assert not graph.has_edge(0, 1)


def test_build_block_proximity_graph_connects_every_pair_within_threshold():
    # A row of 3 blocks each 3km from its immediate neighbor, so 0-1 and 1-2
    # both connect, but 0-2 (6km apart) also connects since it's still under
    # the 8km cap — this is NOT a chaining test, just confirming every pair
    # within threshold gets an edge, not just nearest-neighbor pairs.
    blocks_gdf = gpd.GeoDataFrame(
        {"forest_block_id": [0, 1, 2]},
        geometry=[
            box(0, 0, 100, 100),
            box(3_100, 0, 3_200, 100),
            box(6_200, 0, 6_300, 100),
        ],
        crs="EPSG:3301",
    )

    graph = build_block_proximity_graph(blocks_gdf)

    assert graph.has_edge(0, 1)
    assert graph.has_edge(1, 2)
    assert graph.has_edge(0, 2)


def test_build_block_proximity_graph_includes_isolated_block_as_a_node():
    blocks_gdf = gpd.GeoDataFrame(
        {"forest_block_id": [0, 1]},
        geometry=[box(0, 0, 100, 100), box(100_000, 0, 100_100, 100)],
        crs="EPSG:3301",
    )

    graph = build_block_proximity_graph(blocks_gdf)

    assert set(graph.nodes) == {0, 1}
    assert graph.number_of_edges() == 0


def test_block_neighbor_proxy_m_default_value():
    assert BLOCK_NEIGHBOR_PROXY_M == 8_000
```

- [ ] **Step 3: Run to verify it fails**

Run: `uv run pytest tests/test_macrocluster.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shroom_fm.macrocluster'`

- [ ] **Step 4: Implement the proximity-graph portion of `src/shroom_fm/macrocluster.py`**

```python
import geopandas as gpd
import networkx as nx

from shroom_fm.eraldis import ESTONIAN_GRID_CRS

BLOCK_NEIGHBOR_PROXY_M = 8_000


def build_block_proximity_graph(forest_blocks_gdf: gpd.GeoDataFrame) -> nx.Graph:
    projected = forest_blocks_gdf.to_crs(ESTONIAN_GRID_CRS)

    graph = nx.Graph()
    graph.add_nodes_from(projected["forest_block_id"])

    buffered = projected.copy()
    buffered["geometry"] = buffered.geometry.buffer(BLOCK_NEIGHBOR_PROXY_M)
    joined = gpd.sjoin(buffered, projected, how="inner", predicate="intersects")

    id_to_geom = dict(zip(projected["forest_block_id"], projected.geometry))

    seen = set()
    for _, row in joined.iterrows():
        block_a = row["forest_block_id_left"]
        block_b = row["forest_block_id_right"]
        if block_a == block_b:
            continue
        pair = (min(block_a, block_b), max(block_a, block_b))
        if pair in seen:
            continue
        seen.add(pair)
        gap = id_to_geom[block_a].distance(id_to_geom[block_b])
        if gap <= BLOCK_NEIGHBOR_PROXY_M:
            graph.add_edge(block_a, block_b, distance_m=gap)

    return graph
```

This mirrors `adjacency.py`'s `find_candidate_pairs`/`classify_pair` shape exactly:
buffer-then-`sjoin`-then-exact-distance-filter, not `sjoin_nearest` (which would only
return each block's single nearest neighbor, missing valid edges to other blocks also
within threshold — see the third test above, which specifically proves multiple edges
from one block are all found).

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/test_macrocluster.py -v`
Expected: 5 passed

- [ ] **Step 6: Run the full suite to confirm no regressions**

Run: `uv run pytest tests/ -q`
Expected: 237 passed (232 + 5 new)

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock src/shroom_fm/macrocluster.py tests/test_macrocluster.py
git commit -m "feat: add block-level proximity graph construction"
```

---

### Task 4: `macrocluster.py` — constrained partitioning into macroclusters

**Files:**
- Modify: `src/shroom_fm/macrocluster.py` (append to the file Task 3 created)
- Modify: `tests/test_macrocluster.py` (append)

**Interfaces:**
- Consumes: `build_block_proximity_graph` from Task 3; `geometry_extent_m`,
  `MACROCLUSTER_TARGET_EXTENT_M` from Task 1 (`forest_block.py`).
- Produces: `MACROCLUSTER_MAX_EXTENT_M = 35_000`, `TARGET_BLOCK_COUNT = (5, 15)`
  (module constants);
  `compute_macroclusters(forest_blocks_gdf: gpd.GeoDataFrame, graph: networkx.Graph) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]`
  — returns `(forest_blocks_gdf with a new macrocluster_id column, macroclusters_gdf)`.
  `macroclusters_gdf` columns: `macrocluster_id`, `geometry`, `forest_block_count`,
  `eraldis_count`, `centroid_extent_m`, `geometry_extent_m`, `oversized_macrocluster`,
  `within_target_extent`, `within_target_block_count`.

This is the highest-complexity task in this plan — read it carefully. The core
algorithm question is: how do you partition a chain of blocks (each within
`BLOCK_NEIGHBOR_PROXY_M` of its neighbor, but transitively spanning far more than
`MACROCLUSTER_MAX_EXTENT_M`) into valid-sized macroclusters, without either (a)
treating the whole chain as one giant cluster (naive connected-components' chaining
bug) or (b) ever producing a cluster whose *real geometry* exceeds the cap even if its
*centroid spacing* happened to pass?

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_macrocluster.py`:

```python
from shroom_fm.macrocluster import (
    MACROCLUSTER_MAX_EXTENT_M,
    TARGET_BLOCK_COUNT,
    compute_macroclusters,
)


def _square_block(block_id, x0, y0, side, eraldis_count=1):
    from shapely.geometry import box

    return {
        "forest_block_id": block_id,
        "eraldis_count": eraldis_count,
        "geometry_extent_m": side * 1.4142135623730951,
        "oversized_block": False,
        "geometry": box(x0, y0, x0 + side, y0 + side),
    }


def test_compute_macroclusters_keeps_compact_super_component_as_one_cluster():
    # 3 blocks, all mutually within a few km, well under MAX_EXTENT_M overall.
    import geopandas as gpd
    import networkx as nx
    from shapely.geometry import box

    blocks_gdf = gpd.GeoDataFrame(
        [
            _square_block(0, 0, 0, 100),
            _square_block(1, 3_000, 0, 100),
            _square_block(2, 6_000, 0, 100),
        ],
        crs="EPSG:3301",
    )
    graph = nx.Graph()
    graph.add_nodes_from([0, 1, 2])
    graph.add_edge(0, 1)
    graph.add_edge(1, 2)
    graph.add_edge(0, 2)

    blocks_result, clusters_gdf = compute_macroclusters(blocks_gdf, graph)

    assert len(clusters_gdf) == 1
    assert clusters_gdf.iloc[0]["forest_block_count"] == 3
    assert blocks_result["macrocluster_id"].nunique() == 1
    assert clusters_gdf.iloc[0]["oversized_macrocluster"] == False


def test_compute_macroclusters_splits_a_chain_that_transitively_spans_too_far():
    # 5 blocks each 7km from the next (0-1-2-3-4), every ADJACENT pair is within
    # BLOCK_NEIGHBOR_PROXY_M (8km), but block 0 and block 4 are 28km apart —
    # well beyond MACROCLUSTER_MAX_EXTENT_M (35km) is NOT violated by the whole
    # chain's raw span here on purpose: use a bigger step so naive connected-
    # components chaining would produce one 40km+ cluster if not split.
    import geopandas as gpd
    import networkx as nx

    step = 9_000  # each block 9km from the next along a line
    n = 5
    blocks_gdf = gpd.GeoDataFrame(
        [_square_block(i, i * step, 0, 100) for i in range(n)],
        crs="EPSG:3301",
    )
    graph = nx.Graph()
    graph.add_nodes_from(range(n))
    for i in range(n - 1):
        graph.add_edge(i, i + 1)  # only adjacent pairs connected — a real chain

    blocks_result, clusters_gdf = compute_macroclusters(blocks_gdf, graph)

    # Total chain span is (n-1)*step = 36km > MAX_EXTENT_M (35km), so this must
    # NOT collapse into one cluster the way naive connected-components would.
    assert len(clusters_gdf) > 1
    for _, cluster in clusters_gdf.iterrows():
        assert cluster["geometry_extent_m"] <= MACROCLUSTER_MAX_EXTENT_M


def test_compute_macroclusters_does_not_force_split_for_small_block_count():
    # 3 large-but-compact blocks — block count (3) is below TARGET_BLOCK_COUNT's
    # minimum (5), but this must NOT be split just to hit the target range,
    # since within_target_block_count is diagnostic-only.
    import geopandas as gpd
    import networkx as nx

    blocks_gdf = gpd.GeoDataFrame(
        [
            _square_block(0, 0, 0, 5_000),
            _square_block(1, 5_500, 0, 5_000),
            _square_block(2, 11_000, 0, 5_000),
        ],
        crs="EPSG:3301",
    )
    graph = nx.Graph()
    graph.add_nodes_from([0, 1, 2])
    graph.add_edge(0, 1)
    graph.add_edge(1, 2)
    graph.add_edge(0, 2)

    _, clusters_gdf = compute_macroclusters(blocks_gdf, graph)

    assert len(clusters_gdf) == 1
    assert clusters_gdf.iloc[0]["forest_block_count"] == 3
    assert clusters_gdf.iloc[0]["within_target_block_count"] == False


def test_compute_macroclusters_flags_a_single_block_that_is_already_oversized():
    import geopandas as gpd
    import networkx as nx

    side = MACROCLUSTER_MAX_EXTENT_M + 5_000
    blocks_gdf = gpd.GeoDataFrame([_square_block(0, 0, 0, side)], crs="EPSG:3301")
    graph = nx.Graph()
    graph.add_node(0)

    _, clusters_gdf = compute_macroclusters(blocks_gdf, graph)

    assert len(clusters_gdf) == 1
    assert clusters_gdf.iloc[0]["oversized_macrocluster"] == True


def test_macrocluster_constants():
    assert MACROCLUSTER_MAX_EXTENT_M == 35_000
    assert TARGET_BLOCK_COUNT == (5, 15)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_macrocluster.py -v`
Expected: the 5 new tests FAIL — `ImportError` on `compute_macroclusters` and the two
new constants (the 5 tests from Task 3 should still pass).

- [ ] **Step 3: Implement the partitioning portion — append to `src/shroom_fm/macrocluster.py`**

Add these imports at the top of the file (alongside the existing `geopandas`/`networkx`
imports): `import numpy as np`, `from sklearn.cluster import AgglomerativeClustering`,
`from shroom_fm.forest_block import MACROCLUSTER_TARGET_EXTENT_M, geometry_extent_m`,
`from shroom_fm.eraldis import WGS84_CRS` (in addition to the already-imported
`ESTONIAN_GRID_CRS`).

Append to `src/shroom_fm/macrocluster.py`:

```python
MACROCLUSTER_MAX_EXTENT_M = 35_000
TARGET_BLOCK_COUNT = (5, 15)

_MAX_REPARTITION_DEPTH = 5
_REPARTITION_SHRINK_FACTOR = 0.8


def _dissolve(geoms):
    return gpd.GeoSeries(geoms, crs=ESTONIAN_GRID_CRS).union_all()


def _partition_component(
    block_ids: list[int],
    id_to_centroid: dict,
    id_to_geom: dict,
    graph: nx.Graph,
    max_extent_m: float,
    depth: int = 0,
) -> list[list[int]]:
    geom = _dissolve([id_to_geom[i] for i in block_ids])
    if geometry_extent_m(geom) <= max_extent_m:
        return [block_ids]

    if len(block_ids) == 1 or depth >= _MAX_REPARTITION_DEPTH:
        # Either a single block already exceeds max_extent_m on its own (nothing
        # to partition), or we've recursed too many times — give up and let the
        # caller flag this group oversized rather than looping indefinitely.
        return [block_ids]

    ordered_ids = sorted(block_ids)
    coords = np.array([[id_to_centroid[i].x, id_to_centroid[i].y] for i in ordered_ids])
    subgraph = graph.subgraph(ordered_ids)
    connectivity = nx.to_scipy_sparse_array(subgraph, nodelist=ordered_ids)

    threshold = max_extent_m * (_REPARTITION_SHRINK_FACTOR**depth)
    clustering = AgglomerativeClustering(
        n_clusters=None,
        distance_threshold=threshold,
        linkage="complete",
        connectivity=connectivity,
        metric="euclidean",
    ).fit(coords)

    groups: dict[int, list[int]] = {}
    for block_id, label in zip(ordered_ids, clustering.labels_):
        groups.setdefault(label, []).append(block_id)

    result = []
    for group_block_ids in groups.values():
        group_geom = _dissolve([id_to_geom[i] for i in group_block_ids])
        if geometry_extent_m(group_geom) <= max_extent_m:
            result.append(group_block_ids)
        else:
            result.extend(
                _partition_component(
                    group_block_ids, id_to_centroid, id_to_geom, graph, max_extent_m, depth + 1
                )
            )
    return result


def compute_macroclusters(
    forest_blocks_gdf: gpd.GeoDataFrame, graph: nx.Graph
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    projected = forest_blocks_gdf.to_crs(ESTONIAN_GRID_CRS)
    id_to_geom = dict(zip(projected["forest_block_id"], projected.geometry))
    id_to_centroid = {i: geom.centroid for i, geom in id_to_geom.items()}
    id_to_eraldis_count = dict(zip(projected["forest_block_id"], projected["eraldis_count"]))

    super_components = [set(c) for c in nx.connected_components(graph)]
    super_components.sort(key=min)

    all_groups: list[list[int]] = []
    for component in super_components:
        block_ids = sorted(component)
        groups = _partition_component(
            block_ids, id_to_centroid, id_to_geom, graph, MACROCLUSTER_MAX_EXTENT_M
        )
        all_groups.extend(groups)

    all_groups.sort(key=min)

    block_id_to_cluster = {}
    records = []
    for cluster_id, group in enumerate(all_groups):
        for block_id in group:
            block_id_to_cluster[block_id] = cluster_id

        member_geoms = [id_to_geom[i] for i in group]
        dissolved = _dissolve(member_geoms)
        centroid_geom = _dissolve([id_to_centroid[i] for i in group])
        geom_extent = geometry_extent_m(dissolved)
        centroid_extent = geometry_extent_m(centroid_geom)
        eraldis_count = int(sum(id_to_eraldis_count[i] for i in group))

        records.append(
            {
                "macrocluster_id": cluster_id,
                "geometry": dissolved,
                "forest_block_count": len(group),
                "eraldis_count": eraldis_count,
                "centroid_extent_m": centroid_extent,
                "geometry_extent_m": geom_extent,
                "oversized_macrocluster": geom_extent > MACROCLUSTER_MAX_EXTENT_M,
                "within_target_extent": geom_extent <= MACROCLUSTER_TARGET_EXTENT_M,
                "within_target_block_count": (
                    TARGET_BLOCK_COUNT[0] <= len(group) <= TARGET_BLOCK_COUNT[1]
                ),
            }
        )

    clusters_gdf = gpd.GeoDataFrame(records, crs=ESTONIAN_GRID_CRS).to_crs(WGS84_CRS)

    result_blocks = forest_blocks_gdf.copy()
    result_blocks["macrocluster_id"] = result_blocks["forest_block_id"].map(block_id_to_cluster)

    return result_blocks, clusters_gdf
```

Notes for the implementer:
- `_partition_component`'s recursion always operates within one connected component's
  subgraph, so a `connectivity`-constrained `AgglomerativeClustering` call can never
  merge two blocks that aren't actually graph-reachable — this is what prevents a
  macrocluster from ever containing a block it can't really pivot to.
- The `centroid_extent_m` computed here is diagnostic/audit only — `geometry_extent_m`
  (computed on the real dissolved block geometries) is what `oversized_macrocluster`
  and `within_target_extent` are checked against, per this plan's Global Constraints.
- `geometry_extent_m` on a dissolved `MultiPoint` of centroids works correctly with no
  special-casing: the same convex-hull-diameter logic applies to point sets as to
  polygons (the two farthest points in any set are always both hull vertices).

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_macrocluster.py -v`
Expected: 10 passed (5 from Task 3 + 5 new)

- [ ] **Step 5: Run the full suite to confirm no regressions**

Run: `uv run pytest tests/ -q`
Expected: 242 passed (237 + 5 new)

- [ ] **Step 6: Commit**

```bash
git add src/shroom_fm/macrocluster.py tests/test_macrocluster.py
git commit -m "feat: add connectivity-constrained macrocluster partitioning with geometry-extent validation"
```

---

### Task 5: `scripts/compute_macroclusters.py` + `main.py` wiring

**Files:**
- Create: `scripts/compute_macroclusters.py`
- Modify: `main.py`

**Interfaces:**
- Consumes: `macrocluster.build_block_proximity_graph`, `macrocluster.compute_macroclusters`
  from Tasks 3-4.
- Produces: `data/macroclusters.geojson`; adds `macrocluster_id` to
  `data/forest_blocks.geojson` and (propagated via `forest_block_id`) to
  `data/eraldis.geojson`.

- [ ] **Step 1: Implement `scripts/compute_macroclusters.py`**

```python
from pathlib import Path

import geopandas as gpd

from shroom_fm.macrocluster import build_block_proximity_graph, compute_macroclusters

ERALDIS_PATH = Path("data/eraldis.geojson")
FOREST_BLOCKS_PATH = Path("data/forest_blocks.geojson")
MACROCLUSTERS_PATH = Path("data/macroclusters.geojson")


def main() -> None:
    eraldis_gdf = gpd.read_file(ERALDIS_PATH)
    blocks_gdf = gpd.read_file(FOREST_BLOCKS_PATH)

    graph = build_block_proximity_graph(blocks_gdf)
    blocks_gdf, clusters_gdf = compute_macroclusters(blocks_gdf, graph)

    eraldis_gdf = eraldis_gdf.merge(
        blocks_gdf[["forest_block_id", "macrocluster_id"]], on="forest_block_id", how="left"
    )

    eraldis_gdf.to_file(ERALDIS_PATH, driver="GeoJSON")
    blocks_gdf.to_file(FOREST_BLOCKS_PATH, driver="GeoJSON")
    clusters_gdf.to_file(MACROCLUSTERS_PATH, driver="GeoJSON")

    print(
        f"{len(clusters_gdf)} macroclusters from {len(blocks_gdf)} forest blocks, "
        f"{int(clusters_gdf['oversized_macrocluster'].sum())} oversized, "
        f"saved to {MACROCLUSTERS_PATH}"
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify it imports cleanly**

Run: `uv run python3 -c "import scripts.compute_macroclusters"`
Expected: no error

- [ ] **Step 3: Wire into `main.py`**

Add `compute_macroclusters` to the import block (alphabetically, after
`compute_forest_blocks`) and insert the new step into `STEPS` right after
`compute_forest_blocks` (still before `score_ecotones`):

```python
from scripts import (
    compute_adjacency,
    compute_forest_blocks,
    compute_macroclusters,
    download_eraldis,
    download_roads,
    enrich_eraldis,
    export_scout_candidates,
    score_access,
    score_ecotone_fruiting,
    score_ecotone_habitat,
    score_ecotones,
    score_fruiting,
    score_habitat,
)

STEPS = [
    ("download_eraldis", download_eraldis.main),
    ("enrich_eraldis", enrich_eraldis.main),
    ("compute_adjacency", compute_adjacency.main),
    ("compute_forest_blocks", compute_forest_blocks.main),
    ("compute_macroclusters", compute_macroclusters.main),
    ("score_ecotones", score_ecotones.main),
    ("score_habitat", score_habitat.main),
    ("score_ecotone_habitat", score_ecotone_habitat.main),
    ("download_roads", download_roads.main),
    ("score_access", score_access.main),
    ("score_fruiting", score_fruiting.main),
    ("score_ecotone_fruiting", score_ecotone_fruiting.main),
    ("export_scout_candidates", export_scout_candidates.main),
]
```

- [ ] **Step 4: Verify `main.py` lists the new step in the right place**

Run: `uv run python3 -c "import main; print([n for n, _ in main.STEPS])"`
Expected: `'compute_macroclusters'` appears right after `'compute_forest_blocks'`

- [ ] **Step 5: Run the full suite to confirm no regressions**

Run: `uv run pytest tests/ -q`
Expected: 242 passed

- [ ] **Step 6: Commit**

```bash
git add scripts/compute_macroclusters.py main.py
git commit -m "feat: add compute_macroclusters.py pipeline script"
```

---

### Task 6: cross-macrocluster invariant + daily rollup scoring

**Files:**
- Modify: `src/shroom_fm/macrocluster.py` (append)
- Modify: `tests/test_macrocluster.py` (append)

**Interfaces:**
- Consumes: `shroom_fm.scout.weather_coverage_ratio(joined_gdf, species) -> float`
  (already shipped); `shroom_fm.habitat.TARGET_SPECIES` (already shipped).
- Produces:
  `ecotone_macrocluster_id(id_a: int, id_b: int, eraldis_to_macrocluster: dict[int, int]) -> tuple[int, bool]`;
  `rollup_daily_state(scout_candidates_gdf: gpd.GeoDataFrame, joined_gdf: gpd.GeoDataFrame, eraldis_gdf: gpd.GeoDataFrame, macroclusters_gdf: gpd.GeoDataFrame, now: datetime) -> gpd.GeoDataFrame`.

**Why `rollup_daily_state` needs `joined_gdf`, not just `scout_candidates_gdf`:**
`today_ranked_count_{species}`/`today_top_score_{species}`/`today_top3_mean_score_{species}`/
`today_top_target_id_{species}` can all be computed directly from `scout_candidates_gdf`'s
`ranked`-tier rows. But `today_weather_coverage_{species}` needs
`scout.weather_coverage_ratio`'s actual denominator — the full
ecologically-and-access-eligible candidate pool (hundreds of thousands of ecotones),
not just the small exported Top-10-per-tier subset — so it needs the same `joined`
GeoDataFrame `scripts/export_scout_candidates.py` already builds internally via
`join_ecotone_access` + `join_ecotone_fruiting`. The orchestrator script in Task 7 will
rebuild that frame itself.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_macrocluster.py`:

```python
from datetime import datetime, timezone

from shroom_fm.habitat import TARGET_SPECIES
from shroom_fm.macrocluster import ecotone_macrocluster_id, rollup_daily_state


def _utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def _joined_columns(n: int, chanterelle_ecotone, chanterelle_eligible, chanterelle_fruiting):
    """rollup_daily_state loops over every TARGET_SPECIES internally, so a real
    joined_gdf always has ecotone_score_*/fruiting_modifier_* for all 5 species
    (join_ecotone_fruiting guarantees this — it nulls a species' column rather than
    omitting it when weather data is missing). Test fixtures must match that shape:
    fill the 4 non-chanterelle species with a neutral, fully-covered value (ecotone
    score 1.0, fruiting modifier 0.5) so weather_coverage_ratio doesn't KeyError,
    while only chanterelle's values are what each test actually asserts against."""
    columns = {
        "scout_eligible": pd.array(chanterelle_eligible, dtype=object),
    }
    for species in TARGET_SPECIES:
        if species == "chanterelle":
            columns["ecotone_score_chanterelle"] = chanterelle_ecotone
            columns["fruiting_modifier_chanterelle"] = chanterelle_fruiting
        else:
            columns[f"ecotone_score_{species}"] = [1.0] * n
            columns[f"fruiting_modifier_{species}"] = [0.5] * n
    return columns


def test_ecotone_macrocluster_id_same_cluster():
    mapping = {1: 5, 2: 5}
    cluster_id, is_cross = ecotone_macrocluster_id(1, 2, mapping)
    assert cluster_id == 5
    assert is_cross is False


def test_ecotone_macrocluster_id_cross_cluster_assigns_by_id_a():
    mapping = {1: 5, 2: 6}
    cluster_id, is_cross = ecotone_macrocluster_id(1, 2, mapping)
    assert cluster_id == 5
    assert is_cross is True


def test_rollup_daily_state_computes_ranked_stats_for_a_populated_cluster():
    import geopandas as gpd
    from shapely.geometry import Point

    scout_candidates_gdf = gpd.GeoDataFrame(
        {
            "species": ["chanterelle", "chanterelle", "chanterelle"],
            "tier": ["ranked", "ranked", "ranked"],
            "scout_score": [0.9, 0.7, 0.5],
            "id_a": [1, 3, 5],
            "id_b": [2, 4, 6],
        },
        geometry=[Point(0, 0)] * 3,
        crs="EPSG:4326",
    )
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1, 2, 3, 4, 5, 6], "macrocluster_id": [10, 10, 10, 10, 10, 10]},
        geometry=[Point(0, 0)] * 6,
        crs="EPSG:4326",
    )
    macroclusters_gdf = gpd.GeoDataFrame(
        {"macrocluster_id": [10]}, geometry=[Point(0, 0)], crs="EPSG:4326"
    )
    joined_gdf = gpd.GeoDataFrame(
        {
            "id_a": [1, 3, 5],
            "id_b": [2, 4, 6],
            **_joined_columns(3, [1.0, 1.0, 1.0], [True, True, True], [0.9, 0.7, None]),
        },
        geometry=[Point(0, 0)] * 3,
        crs="EPSG:4326",
    )

    result = rollup_daily_state(
        scout_candidates_gdf, joined_gdf, eraldis_gdf, macroclusters_gdf, _utc(2026, 8, 20)
    )

    row = result[result["macrocluster_id"] == 10].iloc[0]
    assert row["today_ranked_count_chanterelle"] == 3
    assert row["today_top_score_chanterelle"] == pytest.approx(0.9)
    assert row["today_top3_mean_score_chanterelle"] == pytest.approx((0.9 + 0.7 + 0.5) / 3)
    assert row["today_top_target_id_chanterelle"] is not None
    assert row["today_weather_coverage_chanterelle"] == pytest.approx(2 / 3)
    assert row["as_of"] == _utc(2026, 8, 20)


def test_rollup_daily_state_top3_mean_with_fewer_than_three_candidates():
    import geopandas as gpd
    from shapely.geometry import Point

    scout_candidates_gdf = gpd.GeoDataFrame(
        {
            "species": ["chanterelle"],
            "tier": ["ranked"],
            "scout_score": [0.6],
            "id_a": [1],
            "id_b": [2],
        },
        geometry=[Point(0, 0)],
        crs="EPSG:4326",
    )
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1, 2], "macrocluster_id": [10, 10]},
        geometry=[Point(0, 0)] * 2,
        crs="EPSG:4326",
    )
    macroclusters_gdf = gpd.GeoDataFrame(
        {"macrocluster_id": [10]}, geometry=[Point(0, 0)], crs="EPSG:4326"
    )
    joined_gdf = gpd.GeoDataFrame(
        {
            "id_a": [1],
            "id_b": [2],
            **_joined_columns(1, [1.0], [True], [0.6]),
        },
        geometry=[Point(0, 0)],
        crs="EPSG:4326",
    )

    result = rollup_daily_state(
        scout_candidates_gdf, joined_gdf, eraldis_gdf, macroclusters_gdf, _utc(2026, 8, 20)
    )

    row = result[result["macrocluster_id"] == 10].iloc[0]
    assert row["today_top3_mean_score_chanterelle"] == pytest.approx(0.6)


def test_rollup_daily_state_cluster_with_zero_candidates_gets_none_not_zero():
    import geopandas as gpd
    from shapely.geometry import Point

    scout_candidates_gdf = gpd.GeoDataFrame(
        {"species": [], "tier": [], "scout_score": [], "id_a": [], "id_b": []},
        geometry=[],
        crs="EPSG:4326",
    )
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1], "macrocluster_id": [10]}, geometry=[Point(0, 0)], crs="EPSG:4326"
    )
    macroclusters_gdf = gpd.GeoDataFrame(
        {"macrocluster_id": [10]}, geometry=[Point(0, 0)], crs="EPSG:4326"
    )
    joined_gdf = gpd.GeoDataFrame(
        {
            "id_a": [],
            "id_b": [],
            **_joined_columns(0, [], [], []),
        },
        geometry=[],
        crs="EPSG:4326",
    )

    result = rollup_daily_state(
        scout_candidates_gdf, joined_gdf, eraldis_gdf, macroclusters_gdf, _utc(2026, 8, 20)
    )

    row = result[result["macrocluster_id"] == 10].iloc[0]
    assert row["today_ranked_count_chanterelle"] == 0
    assert row["today_top_score_chanterelle"] is None
    assert row["today_top3_mean_score_chanterelle"] is None
    assert row["today_top_target_id_chanterelle"] is None


def test_rollup_daily_state_counts_cross_macrocluster_ecotones():
    import geopandas as gpd
    from shapely.geometry import Point

    scout_candidates_gdf = gpd.GeoDataFrame(
        {"species": [], "tier": [], "scout_score": [], "id_a": [], "id_b": []},
        geometry=[],
        crs="EPSG:4326",
    )
    eraldis_gdf = gpd.GeoDataFrame(
        {"id": [1, 2], "macrocluster_id": [10, 20]},
        geometry=[Point(0, 0)] * 2,
        crs="EPSG:4326",
    )
    macroclusters_gdf = gpd.GeoDataFrame(
        {"macrocluster_id": [10, 20]}, geometry=[Point(0, 0)] * 2, crs="EPSG:4326"
    )
    joined_gdf = gpd.GeoDataFrame(
        {
            "id_a": [1],
            "id_b": [2],  # id_a is in cluster 10, id_b is in cluster 20 — cross-cluster
            **_joined_columns(1, [1.0], [True], [0.5]),
        },
        geometry=[Point(0, 0)],
        crs="EPSG:4326",
    )

    result = rollup_daily_state(
        scout_candidates_gdf, joined_gdf, eraldis_gdf, macroclusters_gdf, _utc(2026, 8, 20)
    )

    row_10 = result[result["macrocluster_id"] == 10].iloc[0]
    row_20 = result[result["macrocluster_id"] == 20].iloc[0]
    assert row_10["cross_macrocluster_ecotone_count"] == 1
    assert row_20["cross_macrocluster_ecotone_count"] == 0
```

Neither `pytest` nor `pandas` is imported at module scope in `tests/test_macrocluster.py`
yet (Tasks 3-4's tests only needed bare equality/bool assertions, so they never needed
either) — this task's tests are the first to use `pytest.approx(...)` and
`pd.array(...)` inline. Add these two lines to the top of `tests/test_macrocluster.py`,
alongside its existing `from shroom_fm.macrocluster import ...` imports:

```python
import pandas as pd
import pytest
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_macrocluster.py -v`
Expected: the 7 new tests FAIL — `ImportError` on `ecotone_macrocluster_id`/
`rollup_daily_state`.

- [ ] **Step 3: Implement — append to `src/shroom_fm/macrocluster.py`**

Add `from datetime import datetime` and `from shroom_fm.habitat import TARGET_SPECIES`
and `from shroom_fm.scout import weather_coverage_ratio` to the imports.

```python
def ecotone_macrocluster_id(
    id_a: int, id_b: int, eraldis_to_macrocluster: dict[int, int]
) -> tuple[int, bool]:
    cluster_a = eraldis_to_macrocluster[id_a]
    cluster_b = eraldis_to_macrocluster[id_b]
    return cluster_a, cluster_a != cluster_b


def rollup_daily_state(
    scout_candidates_gdf: gpd.GeoDataFrame,
    joined_gdf: gpd.GeoDataFrame,
    eraldis_gdf: gpd.GeoDataFrame,
    macroclusters_gdf: gpd.GeoDataFrame,
    now: datetime,
) -> gpd.GeoDataFrame:
    eraldis_to_macrocluster = dict(zip(eraldis_gdf["id"], eraldis_gdf["macrocluster_id"]))

    # Assign every candidate and every scored ecotone to a macrocluster, counting
    # cross-cluster anomalies as we go (diagnostic, never a hard failure).
    candidate_cluster_ids = []
    for id_a, id_b in zip(scout_candidates_gdf["id_a"], scout_candidates_gdf["id_b"]):
        cluster_id, _ = ecotone_macrocluster_id(id_a, id_b, eraldis_to_macrocluster)
        candidate_cluster_ids.append(cluster_id)
    candidates = scout_candidates_gdf.copy()
    candidates["macrocluster_id"] = candidate_cluster_ids

    joined_cluster_ids = []
    cross_flags = []
    for id_a, id_b in zip(joined_gdf["id_a"], joined_gdf["id_b"]):
        cluster_id, is_cross = ecotone_macrocluster_id(id_a, id_b, eraldis_to_macrocluster)
        joined_cluster_ids.append(cluster_id)
        cross_flags.append(is_cross)
    joined = joined_gdf.copy()
    joined["macrocluster_id"] = joined_cluster_ids
    joined["is_cross_macrocluster"] = cross_flags

    records = []
    for cluster_id in macroclusters_gdf["macrocluster_id"]:
        record = {"macrocluster_id": cluster_id, "as_of": now}
        cluster_candidates = candidates[candidates["macrocluster_id"] == cluster_id]
        cluster_joined = joined[joined["macrocluster_id"] == cluster_id]
        record["cross_macrocluster_ecotone_count"] = int(
            cluster_joined["is_cross_macrocluster"].sum()
        )

        for species in TARGET_SPECIES:
            ranked = cluster_candidates[
                (cluster_candidates["species"] == species)
                & (cluster_candidates["tier"] == "ranked")
            ]
            ranked_count = len(ranked)
            record[f"today_ranked_count_{species}"] = ranked_count
            if ranked_count == 0:
                record[f"today_top_score_{species}"] = None
                record[f"today_top3_mean_score_{species}"] = None
                record[f"today_top_target_id_{species}"] = None
            else:
                sorted_ranked = ranked.sort_values("scout_score", ascending=False)
                record[f"today_top_score_{species}"] = float(sorted_ranked.iloc[0]["scout_score"])
                top3 = sorted_ranked["scout_score"].head(3)
                record[f"today_top3_mean_score_{species}"] = float(top3.mean())
                record[f"today_top_target_id_{species}"] = (
                    f"{sorted_ranked.iloc[0]['id_a']}_{sorted_ranked.iloc[0]['id_b']}"
                )
            record[f"today_weather_coverage_{species}"] = weather_coverage_ratio(
                cluster_joined, species
            )

        records.append(record)

    return gpd.GeoDataFrame(records, geometry=macroclusters_gdf.geometry.values, crs=macroclusters_gdf.crs)
```

Note for the implementer: `today_top_target_id_{species}` is built as an `"id_a_id_b"`
string since `scout_candidates_gdf` has no single ecotone-id column of its own — that's
sufficient to look the candidate back up (both `data/ecotones.geojson` and
`data/scout_candidates.geojson` are keyed by `(id_a, id_b)` pairs, not a separate id).

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_macrocluster.py -v`
Expected: 17 passed (10 from Tasks 3-4 + 7 new)

- [ ] **Step 5: Run the full suite to confirm no regressions**

Run: `uv run pytest tests/ -q`
Expected: 249 passed (242 + 7 new)

- [ ] **Step 6: Commit**

```bash
git add src/shroom_fm/macrocluster.py tests/test_macrocluster.py
git commit -m "feat: add cross-macrocluster ecotone invariant and daily rollup scoring"
```

---

### Task 7: `scripts/rollup_macroclusters.py` + `main.py` wiring

**Files:**
- Create: `scripts/rollup_macroclusters.py`
- Modify: `main.py`

**Interfaces:**
- Consumes: `macrocluster.rollup_daily_state` from Task 6;
  `scout.join_ecotone_access(ecotones_gdf, eraldis_gdf) -> gpd.GeoDataFrame` and
  `fruiting.join_ecotone_fruiting(ecotones_gdf, weather_gdf) -> gpd.GeoDataFrame`
  (already shipped — same two functions `scripts/export_scout_candidates.py` already
  calls, rebuilt here independently per this plan's Global Constraints discussion in
  the spec).
- Produces: `data/macrocluster_state.geojson`.

- [ ] **Step 1: Implement `scripts/rollup_macroclusters.py`**

```python
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd

from shroom_fm.fruiting import join_ecotone_fruiting
from shroom_fm.macrocluster import rollup_daily_state
from shroom_fm.scout import join_ecotone_access

ERALDIS_PATH = Path("data/eraldis.geojson")
ECOTONES_PATH = Path("data/ecotones.geojson")
WEATHER_PATH = Path("data/weather_eraldis.geojson")
SCOUT_CANDIDATES_PATH = Path("data/scout_candidates.geojson")
MACROCLUSTERS_PATH = Path("data/macroclusters.geojson")
OUTPUT_PATH = Path("data/macrocluster_state.geojson")


def main() -> None:
    eraldis_gdf = gpd.read_file(ERALDIS_PATH)
    ecotones_gdf = gpd.read_file(ECOTONES_PATH)
    weather_gdf = gpd.read_file(WEATHER_PATH)
    scout_candidates_gdf = gpd.read_file(SCOUT_CANDIDATES_PATH)
    macroclusters_gdf = gpd.read_file(MACROCLUSTERS_PATH)

    joined = join_ecotone_access(ecotones_gdf, eraldis_gdf)
    joined = join_ecotone_fruiting(joined, weather_gdf)

    now = datetime.now(timezone.utc)
    state = rollup_daily_state(scout_candidates_gdf, joined, eraldis_gdf, macroclusters_gdf, now)
    state.to_file(OUTPUT_PATH, driver="GeoJSON")

    total_cross = int(state["cross_macrocluster_ecotone_count"].sum())
    print(
        f"{len(state)} macrocluster states rolled up, "
        f"{total_cross} cross-macrocluster ecotones (diagnostic), "
        f"saved to {OUTPUT_PATH}"
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify it imports cleanly**

Run: `uv run python3 -c "import scripts.rollup_macroclusters"`
Expected: no error

- [ ] **Step 3: Wire into `main.py`**

Add `rollup_macroclusters` to the import block (alphabetically, after
`score_habitat`) and append the new step to the very end of `STEPS`, after
`export_scout_candidates` — it depends on `data/scout_candidates.geojson`, which only
exists once that step has run:

```python
from scripts import (
    compute_adjacency,
    compute_forest_blocks,
    compute_macroclusters,
    download_eraldis,
    download_roads,
    enrich_eraldis,
    export_scout_candidates,
    rollup_macroclusters,
    score_access,
    score_ecotone_fruiting,
    score_ecotone_habitat,
    score_ecotones,
    score_fruiting,
    score_habitat,
)

STEPS = [
    ("download_eraldis", download_eraldis.main),
    ("enrich_eraldis", enrich_eraldis.main),
    ("compute_adjacency", compute_adjacency.main),
    ("compute_forest_blocks", compute_forest_blocks.main),
    ("compute_macroclusters", compute_macroclusters.main),
    ("score_ecotones", score_ecotones.main),
    ("score_habitat", score_habitat.main),
    ("score_ecotone_habitat", score_ecotone_habitat.main),
    ("download_roads", download_roads.main),
    ("score_access", score_access.main),
    ("score_fruiting", score_fruiting.main),
    ("score_ecotone_fruiting", score_ecotone_fruiting.main),
    ("export_scout_candidates", export_scout_candidates.main),
    ("rollup_macroclusters", rollup_macroclusters.main),
]
```

- [ ] **Step 4: Verify `main.py` lists the new step last**

Run: `uv run python3 -c "import main; print(main.STEPS[-1][0])"`
Expected: `rollup_macroclusters`

- [ ] **Step 5: Run the full suite to confirm no regressions**

Run: `uv run pytest tests/ -q`
Expected: 249 passed

- [ ] **Step 6: Commit**

```bash
git add scripts/rollup_macroclusters.py main.py
git commit -m "feat: add rollup_macroclusters.py pipeline script"
```

---

### Task 8: Real-scale verification and CLAUDE.md update

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest tests/ -q`
Expected: 249 passed — confirm the actual number from the real run rather than trusting
this plan's arithmetic blindly (this project's history shows plan-time test-count
arithmetic has been wrong before).

- [ ] **Step 2: Run the real pipeline steps against production data**

Requires `data/eraldis.geojson` and `data/adjacency.geojson` to already exist from
earlier pipeline steps (and, for the rollup step, `data/scout_candidates.geojson` from
a full real run). Run, in order, timing each:

```bash
time uv run python scripts/compute_forest_blocks.py
time uv run python scripts/compute_macroclusters.py
time uv run python scripts/rollup_macroclusters.py
```

Report the real forest_block count, real macrocluster count, real
`oversized_block`/`oversized_macrocluster` counts, and real
`cross_macrocluster_ecotone_count` totals from `scripts/compute_forest_blocks.py`'s,
`scripts/compute_macroclusters.py`'s, and `scripts/rollup_macroclusters.py`'s own
printed output — do not estimate these, use the actual numbers from the real run
against the real 262,054-stand dataset. If `cross_macrocluster_ecotone_count` turns out
nonzero and non-trivial (more than a handful out of hundreds of thousands of ecotones),
say so explicitly and investigate whether `forest_block`/adjacency construction needs a
follow-up — per this plan's Global Constraints, this is a real diagnostic signal, not
something to silently note and move past.

Spot-check `data/macroclusters.geojson` (2-3 rows): confirm `geometry_extent_m` is
genuinely `<= MACROCLUSTER_MAX_EXTENT_M` for every non-oversized row (a direct
correctness check on the real algorithm's real output, not just trusting the unit
tests). Spot-check `data/macrocluster_state.geojson` (2-3 rows): confirm at least one
row's `today_ranked_count_{species}`/`today_top_score_{species}` fields look plausible
against the corresponding rows in `data/scout_candidates.geojson`.

- [ ] **Step 3: Update CLAUDE.md**

Add a new subsection documenting macroclustering — after the existing "FruitingScore
(weather-driven scoring)" section, before "Planned architecture" — covering: the
`forest_block` → `macrocluster` hierarchy and why membership doesn't depend on any
score; the new pipeline steps (`compute_forest_blocks.py`, `compute_macroclusters.py`,
`rollup_macroclusters.py`) and their position in `main.py`'s `STEPS`; the v0 engineering
priors (`BLOCK_NEIGHBOR_PROXY_M`, `MACROCLUSTER_MAX_EXTENT_M`,
`MACROCLUSTER_TARGET_EXTENT_M`, `TARGET_BLOCK_COUNT`) with the same "not a real
travel-time claim yet" framing used elsewhere; the real counts/timings/diagnostics from
Step 2. Update the project-status paragraph near the top of CLAUDE.md and the "Running
the full pipeline"/"Planned architecture" sections to include the 3 new steps in the
dependency-order description, matching how the FruitingScore steps were documented
there previously.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document macroclustering in CLAUDE.md"
```
