import math

import geopandas as gpd

from shroom_fm.ecotone import composition_diversity, composition_fractions, kasvukoht_profile

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


def score_stands(eraldis_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    result = eraldis_gdf.copy()

    fractions_list = [
        composition_fractions(composition) if composition else None
        for composition in result["composition"]
    ]
    result["composition_diversity"] = [
        composition_diversity(fractions) if fractions is not None else None
        for fractions in fractions_list
    ]

    for species in TARGET_SPECIES:
        result[f"stand_habitat_score_{species}"] = [
            stand_habitat_score(species, fractions, kasvukoht_kood)
            for fractions, kasvukoht_kood in zip(fractions_list, result["kasvukoht_kood"])
        ]

    return result


def normalize_bool_or_none(value) -> bool | None:
    if value is True or value == "True":
        return True
    if value is False or value == "False":
        return False
    return None


def _is_missing(value) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


TRANSITION_LENGTH_CAP_M = 200.0
EXPLORATION_BONUS_CAP = 0.3


def kasvukoht_dimension_score(moisture_contrast, group_changed) -> float | None:
    if not _is_missing(moisture_contrast):
        return moisture_contrast
    if group_changed is True:
        return 1.0
    if group_changed is False:
        return 0.0
    return None


def exploration_bonus(
    composition_contrast,
    moisture_contrast,
    group_changed,
    age_contrast,
    drainage_changed,
    transition_length_m,
) -> tuple[float, float, float]:
    dimension = kasvukoht_dimension_score(moisture_contrast, group_changed)
    terms = {
        "composition_contrast": (
            None if _is_missing(composition_contrast) else composition_contrast,
            0.35,
        ),
        "kasvukoht_dimension": (dimension, 0.25),
        "age_contrast": (None if _is_missing(age_contrast) else age_contrast, 0.20),
        "drainage_changed": (
            1.0 if drainage_changed is True else 0.0 if drainage_changed is False else None,
            0.10,
        ),
        "transition_length": (min(1.0, transition_length_m / TRANSITION_LENGTH_CAP_M), 0.10),
    }
    exploration_signal = sum(v * w for v, w in terms.values() if v is not None)
    exploration_coverage = sum(w for v, w in terms.values() if v is not None)
    bonus = EXPLORATION_BONUS_CAP * exploration_signal
    return bonus, exploration_signal, exploration_coverage


def base_habitat(score_a, score_b) -> float | None:
    if _is_missing(score_a) or _is_missing(score_b):
        return None
    return 0.7 * max(score_a, score_b) + 0.3 * min(score_a, score_b)


def ecotone_score(score_a, score_b, bonus: float) -> float | None:
    base = base_habitat(score_a, score_b)
    if base is None:
        return None
    return base * (1 + bonus)
