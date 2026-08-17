# ScoutScore v0 + Top-N Export — Design

Date: 2026-08-17
Status: Approved

## Purpose

MVP step 8 from `CLAUDE.md`: "Export top N results → GeoJSON". `CLAUDE.md`'s target
`ScoutScore` formula (`ecological_candidate_score × access_modifier × fruiting_modifier +
mosaic_bonus + history_bonus`) depends on `FruitingScore` (weather), personal observation
history, and a landscape-mosaic diversity bonus — none of which exist yet. This builds a
"ScoutScore v0" using only what's currently built (`EcotoneScore` + `AccessScore`), with
`fruiting_modifier`/`mosaic_bonus`/`history_bonus` simply absent from the v0 formula rather
than faked with neutral placeholder values, plus a script exporting the top N candidates per
species to GeoJSON.

## Result unit: ecotones, not stands

Each exported candidate is an ecotone (a buffered boundary "scoutable microtype" polygon
from `data/ecotones.geojson`), not a stand interior. `CLAUDE.md`'s own domain glossary
frames ecotones as the primary scouting target ("these transition zones... are often the
most interesting scouting targets"). Stand interiors (`StandHabitatScore`) are not exported
by this feature.

## Ranking scope: per species

Five separate top-N lists (one per target species: kitsemampel, chanterelle, aspen bolete,
birch bolete, porcini), each ranked by that species' own `ecotone_score_<species>` /
`ScoutScore`. No cross-species aggregation — matches this project's consistent choice to
never collapse per-species scores into a single number.

## Combining two stands' `AccessScore` into one `access_modifier`

Each ecotone sits between two stands (`id_a`, `id_b`), each with its own `access_score` in
`data/eraldis.geojson`. `access_modifier = max(access_score_a, access_score_b)` — you park
near whichever stand is better served by roads and approach the boundary from there, so the
better-served side sets the real practical difficulty, not the worse one. The stand that
"wins" this comparison also supplies the ecotone's reported `access_confidence`,
`access_reason`, and `nearest_car_road_m` (a coherent, single approach description, not a
blended/ambiguous one).

## Eligibility is separate from the score — never a hard zero or a floor

`AccessScore` itself is never modified, floored, or hard-zeroed by this feature — it stays
exactly as `access.py` already computes it. The reasoning: `nearest_car_road_m > 1500`
(today's `ACCESS_DISTANCE_CAP_M`) is not proof of true physical unreachability — it only
means "no car-accessible road found within 1500m by the current straight-line distance-proxy
model" (no real road-network graph exists yet, per the road-access spec's explicitly stated
v1 limitation). Treating that as `access_modifier = 0` overclaims certainty; a `floor=0.5`
(mirroring `SITE_MODIFIER_FLOOR`'s pattern) is equally wrong in the other direction — a
candidate 1.5km from a road doesn't deserve half credit just to remain visible.

Instead, a new `scout_eligible` boolean, driven by `MAX_WALK_FROM_CAR_M = 
ACCESS_DISTANCE_CAP_M` (reusing the existing 1500m constant rather than introducing a second
one that merely starts equal to it — a real config-profile system, as discussed, is future
work, not v1 scope): eligible if the winning stand's `nearest_car_road_m` is not `None` and
`<= MAX_WALK_FROM_CAR_M`.

```
ScoutScore = ecotone_score × access_modifier   if scout_eligible
ScoutScore = None                              otherwise, with exclusion_reason set
```

An ineligible ecotone is never deleted or hidden — it's exported in a separate
**`remote_high_value`** tier (see below), sorted by `ecotone_score` alone, so a
biologically excellent but currently-unreachable-by-proxy spot stays visible rather than
silently vanishing behind a `0`.

## Two export tiers per species

For each species, from all ecotones with a real `ecotone_score_<species>`:

- **`ranked`** tier: candidates with `scout_score is not None` (i.e., `scout_eligible` and
  both `ecotone_score`/`access_modifier` present), top `TOP_N` by `scout_score` descending.
- **`remote_high_value`** tier: candidates with `scout_score is None` but
  `ecotone_score_<species> is not None` (scored ecologically, excluded only on the v1
  access proxy), top `TOP_N` by `ecotone_score_<species>` descending.

Both tiers are capped at `TOP_N` (default `5`) independently — a species could contribute up
to 10 rows total (5 ranked + 5 remote), not 5.

## `src/shroom_fm/scout.py` (new module)

```python
import geopandas as gpd
import pandas as pd

from shroom_fm.access import ACCESS_DISTANCE_CAP_M
from shroom_fm.habitat import TARGET_SPECIES

MAX_WALK_FROM_CAR_M = ACCESS_DISTANCE_CAP_M
REMOTE_EXCLUSION_REASON = "REMOTE_BY_V1_ACCESS_PROXY"

ACCESS_COLUMNS = ["access_score", "access_confidence", "access_reason", "nearest_car_road_m"]


def join_ecotone_access(
    ecotones_gdf: gpd.GeoDataFrame, eraldis_gdf: gpd.GeoDataFrame
) -> gpd.GeoDataFrame:
    access_by_id = eraldis_gdf.set_index("id")[ACCESS_COLUMNS]
    access_a = access_by_id.reindex(ecotones_gdf["id_a"]).reset_index(drop=True)
    access_b = access_by_id.reindex(ecotones_gdf["id_b"]).reset_index(drop=True)

    result = ecotones_gdf.copy().reset_index(drop=True)

    access_modifier = []
    access_confidence = []
    access_reason = []
    nearest_car_road_m = []
    scout_eligible = []

    for a, b in zip(access_a.itertuples(index=False), access_b.itertuples(index=False)):
        score_a = 0.0 if pd.isna(a.access_score) else a.access_score
        score_b = 0.0 if pd.isna(b.access_score) else b.access_score
        winner = a if score_a >= score_b else b
        winner_score = score_a if score_a >= score_b else score_b
        winner_distance = None if pd.isna(winner.nearest_car_road_m) else winner.nearest_car_road_m

        access_modifier.append(winner_score)
        access_confidence.append(None if pd.isna(winner.access_confidence) else winner.access_confidence)
        access_reason.append(None if pd.isna(winner.access_reason) else winner.access_reason)
        nearest_car_road_m.append(winner_distance)
        scout_eligible.append(winner_distance is not None and winner_distance <= MAX_WALK_FROM_CAR_M)

    result["access_modifier"] = access_modifier
    result["access_confidence"] = access_confidence
    result["access_reason"] = access_reason
    result["nearest_car_road_m"] = nearest_car_road_m
    result["scout_eligible"] = scout_eligible

    return result


def scout_score(
    ecotone_score: float | None, access_modifier: float | None, eligible: bool
) -> float | None:
    if not eligible or ecotone_score is None or access_modifier is None:
        return None
    return ecotone_score * access_modifier


def scout_candidates_for_species(
    joined_gdf: gpd.GeoDataFrame, species: str, top_n: int
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    ecotone_col = f"ecotone_score_{species}"
    scored = joined_gdf[joined_gdf[ecotone_col].notna()].copy()
    scored["ecotone_score"] = scored[ecotone_col]
    scored["scout_score"] = [
        scout_score(ecotone_score_value, access_modifier_value, eligible)
        for ecotone_score_value, access_modifier_value, eligible in zip(
            scored["ecotone_score"], scored["access_modifier"], scored["scout_eligible"]
        )
    ]

    ranked = (
        scored[scored["scout_score"].notna()]
        .sort_values("scout_score", ascending=False)
        .head(top_n)
    )
    remote = (
        scored[scored["scout_score"].isna()]
        .assign(exclusion_reason=REMOTE_EXCLUSION_REASON)
        .sort_values("ecotone_score", ascending=False)
        .head(top_n)
    )
    return ranked, remote
```

`scout_candidates_for_species` copies the species-specific `ecotone_score_<species>` column
into a generic `ecotone_score` column on its return value — this is what lets the export
script below use one fixed output column name (`ecotone_score`) instead of five
differently-named per-species columns, since each row in the final combined export already
carries an explicit `species` column identifying which target species it's for.

`TARGET_SPECIES` is imported from `habitat.py` (already defines the 5-species list — reused,
not redefined).

## `scripts/export_scout_candidates.py` (new script)

```python
from pathlib import Path

import geopandas as gpd
import pandas as pd

from shroom_fm.habitat import TARGET_SPECIES
from shroom_fm.scout import join_ecotone_access, scout_candidates_for_species

TOP_N = 5
ERALDIS_PATH = Path("data/eraldis.geojson")
ECOTONES_PATH = Path("data/ecotones.geojson")
OUTPUT_PATH = Path("data/scout_candidates.geojson")


def main() -> None:
    eraldis_gdf = gpd.read_file(ERALDIS_PATH)
    ecotones_gdf = gpd.read_file(ECOTONES_PATH)
    joined = join_ecotone_access(ecotones_gdf, eraldis_gdf)

    rows = []
    for species in TARGET_SPECIES:
        ranked, remote = scout_candidates_for_species(joined, species, TOP_N)

        ranked = ranked.copy()
        ranked["species"] = species
        ranked["tier"] = "ranked"
        ranked["rank"] = range(1, len(ranked) + 1)
        ranked["exclusion_reason"] = None

        remote = remote.copy()
        remote["species"] = species
        remote["tier"] = "remote_high_value"
        remote["rank"] = range(1, len(remote) + 1)

        rows.append(ranked)
        rows.append(remote)

    combined = gpd.GeoDataFrame(pd.concat(rows, ignore_index=True), crs=ecotones_gdf.crs)

    output_columns = [
        "species", "tier", "rank", "scout_score", "ecotone_score",
        "access_modifier", "access_confidence", "access_reason", "nearest_car_road_m",
        "exclusion_reason", "transition_length_m", "dominant_species_a", "dominant_species_b",
        "id_a", "id_b", "geometry",
    ]
    combined = combined[output_columns]

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    combined.to_file(OUTPUT_PATH, driver="GeoJSON")
    print(f"{len(combined)} scout candidates across {len(TARGET_SPECIES)} species saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
```

## Testing

`join_ecotone_access` and `scout_score` are pure/data functions (no network calls) — fully
unit-tested with small real-geometry fixtures, matching `access.py`'s/`habitat.py`'s
established testing style:

- `join_ecotone_access`: picks the higher-`access_score` stand's fields; ties break toward
  either side without affecting eligibility; `NaN` access fields (e.g. an unmatched stand
  id) normalize to `0.0`/`None`, never propagate as `NaN`; `scout_eligible` is `True` only
  when the winning side's distance is present and within `MAX_WALK_FROM_CAR_M`.
- `scout_score`: returns `None` when ineligible, when `ecotone_score` is `None`, or when
  `access_modifier` is `None`; returns the product otherwise.
- `scout_candidates_for_species`: correctly splits eligible vs. ineligible-but-scored rows
  into the two tiers, each capped independently at `top_n`, sorted by the right column
  (`scout_score` for `ranked`, `ecotone_score_<species>` for `remote_high_value`).

`scripts/export_scout_candidates.py` stays a thin, untested wrapper — matches every other
runner script in this project.

## Out of scope

- `FruitingScore` (weather), personal observation history, landscape-mosaic diversity
  bonus — none exist yet; this v0 formula has no placeholder/neutral stand-ins for them,
  it simply omits them until they're built.
- Recomputing `AccessScore` against the ecotone's own boundary geometry (discussed and
  rejected for v1 — real added scope, not just a join; the per-stand join is the v0
  approximation).
- A configurable "scouting profile" system (`fast_scout`/`deep_forest_day`/etc. with
  different `MAX_WALK_FROM_CAR_M` values) — `MAX_WALK_FROM_CAR_M` is a single constant for
  now, structured so it isn't hard to extend into a profile system later, but not built now.
- Richer future access-confidence states (`REACHABLE_WITH_LONG_WALK`, `BLOCKED_BY_BARRIER`,
  `NO_CAR_ROUTE`, etc.) — depends on a real road-network graph, which doesn't exist yet
  (the road-access spec's explicitly deferred v2 item).
- Exporting `StandHabitatScore`-only candidates (stand interiors without an ecotone) — this
  feature only exports ecotones, per the Result Unit decision above.
