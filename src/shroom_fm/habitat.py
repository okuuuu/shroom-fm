import math


TARGET_SPECIES = ["kitsemampel", "chanterelle", "aspen_bolete", "birch_bolete", "porcini"]

# {species: {tree: (affinity, saturation_share)}}
# Engineering priors from mycorrhizal-host literature and Estonian forestry
# sources (RMK), not yet calibrated against field observations.
HOST_PROFILES = {
    "kitsemampel": {
        "pine": (1.00, 0.35),
        "spruce": (0.65, 0.30),
        "birch": (0.40, 0.25),
    },
    "chanterelle": {
        "pine": (1.00, 0.40),
        "spruce": (0.75, 0.35),
        "birch": (0.75, 0.35),
    },
    "aspen_bolete": {
        "aspen": (1.00, 0.15),
        "birch": (0.40, 0.20),
    },
    "birch_bolete": {
        "birch": (1.00, 0.20),
    },
    # Practical porcini/white-bolete target group (includes pine-associated
    # ecology), not molecularly verified B. edulis sensu stricto.
    "porcini": {
        "spruce": (1.00, 0.30),
        "pine": (0.90, 0.30),
        "birch": (0.75, 0.25),
    },
}


def host_score(species: str, fractions: dict[str, float]) -> float:
    contributions = [
        affinity * min(1.0, fractions[tree] / saturation_share)
        for tree, (affinity, saturation_share) in HOST_PROFILES[species].items()
    ]
    return max(contributions, default=0.0)
