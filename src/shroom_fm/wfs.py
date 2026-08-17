import json
from pathlib import Path

from owslib.wfs import WebFeatureService

from shroom_fm.retry import call_with_retry

METSAREGISTER_OWS_URL = "https://gsavalik.envir.ee/geoserver/metsaregister/ows"
ETAK_WFS_URL = "https://gsavalik.envir.ee/geoserver/etak/wfs"


def fetch_capabilities(url: str = METSAREGISTER_OWS_URL) -> WebFeatureService:
    return call_with_retry(WebFeatureService, url, version="2.0.0")


def layer_summary(wfs) -> list[dict]:
    layers = [
        {"name": name, "title": meta.title, "abstract": meta.abstract}
        for name, meta in wfs.contents.items()
    ]
    return sorted(layers, key=lambda layer: layer["name"])


def save_layers_json(layers: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(layers, indent=2, ensure_ascii=False) + "\n")
