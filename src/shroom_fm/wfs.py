import json
from pathlib import Path


def layer_summary(wfs) -> list[dict]:
    layers = [
        {"name": name, "title": meta.title, "abstract": meta.abstract}
        for name, meta in wfs.contents.items()
    ]
    return sorted(layers, key=lambda layer: layer["name"])


def save_layers_json(layers: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(layers, indent=2, ensure_ascii=False) + "\n")
