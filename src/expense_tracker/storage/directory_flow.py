"""Helpers for moving source files between incoming/processed/rejected directories."""

from __future__ import annotations

import shutil
from pathlib import Path


def _build_unique_destination(path: Path) -> Path:
    if not path.exists():
        return path

    counter = 1
    while True:
        candidate = path.with_name(f"{path.stem}_{counter}{path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def move_source_file(
    source_path: str | Path,
    *,
    source_root: str | Path,
    destination_root: str | Path,
) -> Path:
    source = Path(source_path)
    root = Path(source_root)
    destination_base = Path(destination_root)

    try:
        relative_path = source.relative_to(root)
    except ValueError:
        relative_path = Path(source.name)

    destination = _build_unique_destination(destination_base / relative_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(destination))
    return destination
