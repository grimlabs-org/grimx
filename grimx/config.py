"""
grimx.config
Read and write grimx.config and grimx.lock using TOML.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import tomlkit

CONFIG_FILE = "grimx.config"
LOCK_FILE = "grimx.lock"

DEFAULT_CONFIG: dict[str, Any] = {
    "package_manager": {
        "priority": ["vcpkg", "conan"],
    }
}


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def load_config(root: Path | None = None) -> dict[str, Any]:
    """Load grimx.config from root (defaults to cwd)."""
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
        doc = tomlkit.document()
        doc["dependencies"] = tomlkit.table()
        return doc
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

    if "dependencies" not in lock:
        lock["dependencies"] = tomlkit.table()

    entry = tomlkit.inline_table()
    entry.append("manager", manager)
    entry.append("version", version)

    lock["dependencies"][name] = entry
    write_lock(lock, root)

def remove_dependency(name: str, root: Path | None = None) -> None:
    """Remove a dependency entry from grimx.lock."""
    lock = load_lock(root)
    if "dependencies" in lock and name in lock["dependencies"]:
        del lock["dependencies"][name]
    write_lock(lock, root)

# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _resolve(root: Path | None, filename: str) -> Path:
    base = root or Path(os.getcwd())
    return base / filename
