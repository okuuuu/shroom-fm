# Engineering starting points, not biological constants — retune once real
# field scouting results validate or invalidate them.
MAX_GAP_M = 10.0  # near_gap: max distance between boundaries to consider
MIN_CONTACT_LENGTH_M = 20.0  # touching: min shared-boundary length to keep (discards corners)
MIN_PROXIMITY_LENGTH_M = 20.0  # near_gap: min estimated parallel-run length to keep


def classify_pair(geom_a, geom_b) -> dict | None:
    shared = geom_a.boundary.intersection(geom_b.boundary)
    if shared.length >= MIN_CONTACT_LENGTH_M:
        return {
            "adjacency_type": "touching",
            "transition_length_m": shared.length,
            "gap_m": 0.0,
            "geometry": shared,
        }

    gap = geom_a.distance(geom_b)
    if 0 < gap <= MAX_GAP_M:
        zone = geom_a.buffer(MAX_GAP_M).intersection(geom_b.buffer(MAX_GAP_M))
        proximity_length = zone.area / MAX_GAP_M
        if proximity_length >= MIN_PROXIMITY_LENGTH_M:
            return {
                "adjacency_type": "near_gap",
                "transition_length_m": proximity_length,
                "gap_m": gap,
                "geometry": zone,
            }

    return None
