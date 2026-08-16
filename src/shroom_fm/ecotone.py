from shroom_fm.enrich import TARGET_SPECIES_CODES


def composition_fractions(composition: list[dict]) -> dict[str, float]:
    categories = list(TARGET_SPECIES_CODES) + ["other"]
    total = sum(entry["osakaal"] for entry in composition)
    if total == 0:
        return {category: 0.0 for category in categories}

    target_sums = {
        name: sum(entry["osakaal"] for entry in composition if entry["puuliik_kood"] == code)
        for name, code in TARGET_SPECIES_CODES.items()
    }
    other = total - sum(target_sums.values())
    raw = {**target_sums, "other": other}
    return {category: raw[category] / total for category in categories}


def composition_contrast(fractions_a: dict[str, float], fractions_b: dict[str, float]) -> float:
    return 0.5 * sum(abs(fractions_a[key] - fractions_b[key]) for key in fractions_a)


def dominant_species(fractions: dict[str, float]) -> tuple[str, float]:
    return max(fractions.items(), key=lambda item: item[1])
