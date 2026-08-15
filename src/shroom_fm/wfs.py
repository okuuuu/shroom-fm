def layer_summary(wfs) -> list[dict]:
    layers = [
        {"name": name, "title": meta.title, "abstract": meta.abstract}
        for name, meta in wfs.contents.items()
    ]
    return sorted(layers, key=lambda layer: layer["name"])
