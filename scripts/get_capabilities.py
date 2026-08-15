from pathlib import Path

from shroom_fm.wfs import fetch_capabilities, layer_summary, save_layers_json

OUTPUT_PATH = Path("data/wfs_capabilities.json")


def main() -> None:
    wfs = fetch_capabilities()
    layers = layer_summary(wfs)

    for layer in layers:
        print(f"{layer['name']} — {layer['title']}")

    save_layers_json(layers, OUTPUT_PATH)
    print(f"\nSaved {len(layers)} layers to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
