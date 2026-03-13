"""
grim.config
Read and write grim.config and grim.lock using TOML.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import tomlkit

CONFIG_FILE = "grim.config"
LOCK_FILE = "grim.lock"

DEFAULT_CONFIG: dict[str, Any] = {
    "package_manager": {
        "priority": ["vcpkg", "conan"],
    }
}


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def load_config(root: Path | None = None) -> dict[str, Any]:
    """Load grim.config from root (defaults to cwd)."""
    path = _resolve(root, CONFIG_FILE)
    if not path.exists():
        return dict(DEFAULT_CONFIG)
    return tomlkit.loads(path.read_text())


def write_config(data: dict[str, Any], root: Path | None = None) -> None:
    path = _resolve(root, CONFIG_FILE)
    path.write_text(tomlkit.dumps(data))


# ---------------------------------------------------------------------------
# Lock file helpers
# ---------------------------------------------------------------------------

def load_lock(root: Path | None = None) -> dict[str, Any]:
    path = _resolve(root, LOCK_FILE)
    if not path.exists():
        return {"dependencies": {}}
    return tomlkit.loads(path.read_text())


def write_lock(data: dict[str, Any], root: Path | None = None) -> None:
    path = _resolve(root, LOCK_FILE)
    path.write_text(tomlkit.dumps(data))


def add_dependency(
    name: str,
    manager: str,
    version: str,
    root: Path | None = None,
) -> None:
    lock = load_lock(root)
    lock["dependencies"][name] = {"manager": manager, "version": version}
    write_lock(lock, root)


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _resolve(root: Path | None, filename: str) -> Path:
    base = root or Path(os.getcwd())
    return base / filename
