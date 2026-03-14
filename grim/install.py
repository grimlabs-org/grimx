"""
grim.install
Delegate package installation to vcpkg or Conan with fallback logic.
Offers to auto-install missing package managers.
"""

from __future__ import annotations

import os
import platform
import subprocess
import shutil
import sys
from pathlib import Path

import click

from grim.config import load_config, load_lock, add_dependency


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run(package: str | None) -> None:
    if package:
        _install_package(package)
    else:
        _restore_from_lock()


# ---------------------------------------------------------------------------
# Install / restore
# ---------------------------------------------------------------------------

def _install_package(package: str) -> None:
    cfg = load_config()
    priority: list[str] = cfg.get("package_manager", {}).get("priority", [])

    if not priority:
        click.echo(
            "error: no package managers configured.\n"
            "  Edit grim.config and set priority = [\"vcpkg\"] or [\"conan\"].",
            err=True,
        )
        raise SystemExit(1)

    for manager in priority:
        if not shutil.which(manager):
            if not _prompt_and_install_manager(manager):
                continue

        click.echo(f"  trying {manager}...")
        ok, version = _try_install(manager, package)
        if ok:
            add_dependency(package, manager, version)
            click.echo(f"  ✓ installed {package}=={version} via {manager}")
            click.echo(f"  ✓ grim.lock updated")
            return

    click.echo(
        f"\nerror: could not install '{package}' with any configured manager "
        f"({', '.join(priority)}).",
        err=True,
    )
    raise SystemExit(1)


def _restore_from_lock() -> None:
    lock = load_lock()
    deps: dict = lock.get("dependencies", {})

    if not deps:
        click.echo("grim.lock is empty — nothing to install.")
        return

    click.echo(f"Restoring {len(deps)} dependencies from grim.lock...")
    failed = []

    for name, meta in deps.items():
        manager = meta["manager"]
        version = meta["version"]

        if not shutil.which(manager):
            if not _prompt_and_install_manager(manager):
                click.echo(f"  ✗ {name} — skipped ({manager} unavailable)", err=True)
                failed.append(name)
                continue

        click.echo(f"  installing {name}=={version} via {manager}...")
        ok, _ = _try_install(manager, f"{name}/{version}")
        if ok:
            click.echo(f"  ✓ {name}")
        else:
            click.echo(f"  ✗ {name} — install failed", err=True)
            failed.append(name)

    if failed:
        click.echo(f"\n{len(failed)} package(s) failed: {', '.join(failed)}", err=True)
        raise SystemExit(1)

    click.echo(f"\n✓ all dependencies restored")


# ---------------------------------------------------------------------------
# Auto-install managers
# ---------------------------------------------------------------------------

def _prompt_and_install_manager(manager: str) -> bool:
    """Offer to install a missing package manager. Returns True if now available."""
    hints = {
        "vcpkg": "https://vcpkg.io/en/getting-started",
        "conan": "pip install conan",
    }
    installers = {
        "vcpkg": _auto_install_vcpkg,
        "conan": _auto_install_conan,
    }

    click.echo(f"\n  '{manager}' is not installed.")

    if manager not in installers:
        click.echo(f"  No auto-install available for '{manager}'.", err=True)
        return False

    click.echo(f"  Install hint: {hints[manager]}")
    if not click.confirm(f"  Install {manager} now?", default=True):
        click.echo(f"  Skipping {manager}.")
        return False

    success = installers[manager]()

    if success and shutil.which(manager):
        return True

    click.echo(
        f"\n  '{manager}' still not found in PATH after install.\n"
        f"  You may need to open a new terminal or add it to PATH manually.",
        err=True,
    )
    return False


def _auto_install_vcpkg() -> bool:
    """Clone and bootstrap vcpkg into ~/.vcpkg and add to session PATH."""
    vcpkg_dir = Path.home() / ".vcpkg"

    if not vcpkg_dir.exists():
        click.echo("  Cloning vcpkg into ~/.vcpkg ...")
        result = subprocess.run(
            ["git", "clone", "https://github.com/microsoft/vcpkg.git", str(vcpkg_dir)],
        )
        if result.returncode != 0:
            click.echo("  error: git clone failed.", err=True)
            return False

    click.echo("  Bootstrapping vcpkg...")
    if platform.system() == "Windows":
        bootstrap = str(vcpkg_dir / "bootstrap-vcpkg.bat")
    else:
        bootstrap = str(vcpkg_dir / "bootstrap-vcpkg.sh")

    result = subprocess.run([bootstrap, "-disableMetrics"])
    if result.returncode != 0:
        click.echo("  error: bootstrap failed.", err=True)
        return False

    # Inject into PATH for the current process
    os.environ["PATH"] = str(vcpkg_dir) + os.pathsep + os.environ.get("PATH", "")

    click.echo(f"  ✓ vcpkg installed at {vcpkg_dir}")
    click.echo(f"  To make this permanent, add to your shell profile:")
    click.echo(f'    export PATH="$HOME/.vcpkg:$PATH"')
    return True


def _auto_install_conan() -> bool:
    """Install conan via pip, falling back to pipx if pip is blocked."""
    click.echo("  Installing conan via pip...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "conan", "--quiet"],
    )
    if result.returncode == 0:
        click.echo("  ✓ conan installed")
        return True

    # pip may be blocked on system-managed Pythons (PEP 668) — try pipx
    if shutil.which("pipx"):
        click.echo("  pip blocked — trying pipx...")
        result = subprocess.run(["pipx", "install", "conan"])
        if result.returncode == 0:
            # pipx installs into ~/.local/bin — ensure it's on PATH
            local_bin = str(Path.home() / ".local" / "bin")
            os.environ["PATH"] = local_bin + os.pathsep + os.environ.get("PATH", "")
            click.echo("  ✓ conan installed via pipx")
            return True

    click.echo("  error: could not install conan.", err=True)
    click.echo("  Try manually: pip install conan  or  pipx install conan", err=True)
    return False


# ---------------------------------------------------------------------------
# Package manager wrappers
# ---------------------------------------------------------------------------

def _try_install(manager: str, package: str) -> tuple[bool, str]:
    if manager == "vcpkg":
        return _vcpkg_install(package)
    if manager == "conan":
        return _conan_install(package)
    return False, ""


def _vcpkg_install(package: str) -> tuple[bool, str]:
    result = subprocess.run(
        ["vcpkg", "install", package],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        return True, _parse_vcpkg_version(result.stdout, package)
    return False, ""


def _conan_install(package: str) -> tuple[bool, str]:
    result = subprocess.run(
        ["conan", "install", "--requires", package, "--build=missing"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        return True, _parse_conan_version(result.stdout, package)
    return False, ""


def _parse_vcpkg_version(output: str, package: str) -> str:
    for line in output.splitlines():
        if package.lower() in line.lower() and "version" in line.lower():
            parts = line.split()
            for i, p in enumerate(parts):
                if p.lower() == "version" and i + 1 < len(parts):
                    return parts[i + 1]
    return "unknown"


def _parse_conan_version(output: str, package: str) -> str:
    base = package.split("/")[0]
    for line in output.splitlines():
        if "/" in line and base.lower() in line.lower():
            return line.strip().split("/")[-1].split("@")[0]
    return "unknown"
