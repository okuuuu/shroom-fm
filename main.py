"""Runs the full shroom-fm pipeline end-to-end.

See CLAUDE.md's "Running the full pipeline" section for the dependency graph
this step order encodes.
"""

import argparse
import time

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


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the full shroom-fm pipeline.")
    parser.add_argument(
        "--skip",
        default="",
        help="Comma-separated step names to skip, e.g. --skip download_eraldis,download_roads",
    )
    args = parser.parse_args()
    skip = {name.strip() for name in args.skip.split(",") if name.strip()}

    unknown = skip - {name for name, _ in STEPS}
    if unknown:
        raise ValueError(f"Unknown step name(s) in --skip: {sorted(unknown)}")

    for name, step_main in STEPS:
        if name in skip:
            print(f"[skip] {name}")
            continue
        print(f"[run]  {name}")
        start = time.monotonic()
        step_main()
        elapsed = time.monotonic() - start
        print(f"[done] {name} ({elapsed:.1f}s)")


if __name__ == "__main__":
    main()
