"""Secret loading utilities.

Supports two sources:
1. Environment variables (handled by Settings/pydantic-settings)
2. File-based secrets (e.g. Docker secrets mounted at /run/secrets/)
"""

from __future__ import annotations

from pathlib import Path


def load_secret_file(path: str) -> str:
    """Read a secret from a file path, stripping trailing whitespace.

    Returns empty string if the file does not exist or is empty.
    """
    p = Path(path)
    if not p.is_file():
        return ""
    return p.read_text().strip()
