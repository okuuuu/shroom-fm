import math

from shroom_fm.ecotone import kasvukoht_profile

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


# {species: {group: score in [0,1]}}. Groups not present in a species' table
# (kõdusoo, puistang — special hydrology / spoil ground, not on the normal
# ecological gradient) resolve to None via dict.get, not a guessed default.
SITE_TYPE_PROFILES = {
    "kitsemampel": {
        "nõmme": 0.85, "palu": 1.00, "laane": 0.45, "sürja": 0.20, "salu": 0.10,
        "rabastuv": 1.00, "sooviku": 0.25, "rohusoo": 0.10, "samblasoo": 0.15, "loo": 0.15,
    },
    "chanterelle": {
        "nõmme": 0.70, "palu": 1.00, "laane": 0.85, "sürja": 0.40, "salu": 0.20,
        "rabastuv": 0.45, "sooviku": 0.20, "rohusoo": 0.10, "samblasoo": 0.10, "loo": 0.25,
    },
    "aspen_bolete": {
        "nõmme": 0.60, "palu": 0.75, "laane": 0.90, "sürja": 0.85, "salu": 0.85,
        "rabastuv": 0.60, "sooviku": 0.75, "rohusoo": 0.55, "samblasoo": 0.35, "loo": 0.70,
    },
    "birch_bolete": {
        "nõmme": 0.65, "palu": 0.85, "laane": 0.85, "sürja": 0.75, "salu": 0.75,
        "rabastuv": 0.85, "sooviku": 0.85, "rohusoo": 0.70, "samblasoo": 0.70, "loo": 0.60,
    },
    "porcini": {
        "nõmme": 0.70, "palu": 0.95, "laane": 1.00, "sürja": 0.65, "salu": 0.50,
        "rabastuv": 0.40, "sooviku": 0.30, "rohusoo": 0.15, "samblasoo": 0.15, "loo": 0.40,
    },
}

SITE_MODIFIER_FLOOR = 0.5


def site_type_score(species: str, kasvukoht_kood: str | None) -> float | None:
    profile = kasvukoht_profile(kasvukoht_kood)
    if profile is None:
        return None
    return SITE_TYPE_PROFILES[species].get(profile["group"])


def site_modifier(site_type_score_value: float) -> float:
    return SITE_MODIFIER_FLOOR + (1 - SITE_MODIFIER_FLOOR) * site_type_score_value


def stand_habitat_score(
    species: str, fractions: dict[str, float] | None, kasvukoht_kood: str | None
) -> float | None:
    if fractions is None:
        return None
    site_score = site_type_score(species, kasvukoht_kood)
    if site_score is None:
        return None
    return host_score(species, fractions) * site_modifier(site_score)
