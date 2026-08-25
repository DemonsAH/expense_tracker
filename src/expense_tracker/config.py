"""Shared configuration helpers."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _candidate_dotenv_paths(dotenv_path: str | Path = ".env") -> list[Path]:
    """Resolve candidate locations for the .env file.

    In a frozen (PyInstaller onefile) build the working directory is wherever
    the user launched the exe from, so a bare ".env" may not exist there.
    Candidates, in order:
      1. the explicitly requested path (when not the default ".env")
      2. the current working directory
      3. next to the executable (frozen builds)
      4. the project source root (found by walking up from this file)
      5. parents of the working directory (walking up a few levels)
    The first existing file wins.
    """
    requested = Path(dotenv_path)

    candidates: list[Path] = []
    if dotenv_path != ".env":
        candidates.append(requested)

    candidates.append(Path.cwd() / ".env")

    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / ".env")

    # Project source root: walk up from this file looking for the package root.
    src_root = Path(__file__).resolve().parent.parent
    for current in [src_root, src_root.parent]:
        candidate = current / ".env"
        if candidate not in candidates:
            candidates.append(candidate)

    # Walk up from the working directory a few levels.
    cwd = Path.cwd().resolve()
    for _ in range(4):
        cwd = cwd.parent
        candidate = cwd / ".env"
        if candidate not in candidates:
            candidates.append(candidate)

    return candidates


def load_dotenv_file(dotenv_path: str | Path = ".env") -> None:
    for candidate in _candidate_dotenv_paths(dotenv_path):
        if candidate.is_file():
            for raw_line in candidate.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
            return


def get_required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def get_bool_env(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
