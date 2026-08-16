COMPOSITION_DETAIL_COLUMNS = [
    "rinne_kood",
    "puuliik_kood",
    "osakaal",
    "vanus",
    "korgus",
    "enamus",
    "sunniaasta",
    "paritolu",
    "diameeter",
    "rinnaspindala",
    "tagavara",
    "arv",
]

TARGET_SPECIES_CODES = {
    "pine": "MA",
    "spruce": "KU",
    "birch": "KS",
    "aspen": "HB",
}


def summarize_composition(element_df) -> dict[int, list[dict]]:
    composition_by_id: dict[int, list[dict]] = {}
    for eraldis_id, group in element_df.groupby("eraldis_id"):
        composition_by_id[eraldis_id] = group[COMPOSITION_DETAIL_COLUMNS].to_dict("records")
    return composition_by_id


def compute_species_shares(composition: list[dict]) -> dict[str, float]:
    shares = {f"{name}_share": 0.0 for name in TARGET_SPECIES_CODES}
    for entry in composition:
        for name, code in TARGET_SPECIES_CODES.items():
            if entry["puuliik_kood"] == code:
                shares[f"{name}_share"] += entry["osakaal"]
    return shares
