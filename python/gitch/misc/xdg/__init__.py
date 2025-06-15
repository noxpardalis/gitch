import os
from pathlib import Path

CACHE_HOME = (
    (p := os.environ.get("XDG_CACHE_HOME")) and Path(p)
) or Path.home() / ".cache"


def cache_dir(path: Path) -> Path:
    path = CACHE_HOME / path
    path.mkdir(parents=True, exist_ok=True)
    return path
