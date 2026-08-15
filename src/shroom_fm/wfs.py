import json
from pathlib import Path

from owslib.wfs import WebFeatureService

METSAREGISTER_OWS_URL = "https://gsavalik.envir.ee/geoserver/metsaregister/ows"


def fetch_capabilities(url: str = METSAREGISTER_OWS_URL) -> WebFeatureService:
    return WebFeatureService(url, version="2.0.0")


def layer_summary(wfs) -> list[dict]:
    layers = [
        {"name": name, "title": meta.title, "abstract": meta.abstract}
        for name, meta in wfs.contents.items()
    ]
    return sorted(layers, key=lambda layer: layer["name"])


def save_layers_json(layers: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(layers, indent=2, ensure_ascii=False) + "\n")
