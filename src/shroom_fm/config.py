import tomllib
from pathlib import Path

CONFIG_PATH = Path("config.toml")


def load_home_location(path: Path = CONFIG_PATH) -> tuple[float, float]:
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Copy config.example.toml to {path} and fill in "
            "your home_lat/home_lon."
        )
    with path.open("rb") as f:
        data = tomllib.load(f)
    return data["home_lat"], data["home_lon"]
