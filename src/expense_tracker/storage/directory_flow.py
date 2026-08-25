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
    dest_stem: str | None = None,
) -> Path:
    source = Path(source_path)
    root = Path(source_root)
    destination_base = Path(destination_root)

    if dest_stem is not None:
        # 按指定新文件名移动（保留原扩展名），用于把处理成功的图片
        # 重命名为分配的 receipt id。
        destination = _build_unique_destination(destination_base / f"{dest_stem}{source.suffix}")
    else:
        try:
            relative_path = source.relative_to(root)
        except ValueError:
            relative_path = Path(source.name)
        destination = _build_unique_destination(destination_base / relative_path)

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(destination))
    return destination
