"""File fingerprint helpers for deduplication and skip logic."""

from __future__ import annotations

import hashlib
from pathlib import Path


def compute_file_sha256(path: str | Path) -> str:
    file_path = Path(path)
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
