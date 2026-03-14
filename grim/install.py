"""
grim.install
Delegate package installation to vcpkg or Conan with fallback logic.
Offers to auto-install missing package managers.
"""

from __future__ import annotations

import json
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
        if not _is_manager_available(manager):
            if not _prompt_and_install_manager(manager):
                continue

        click.echo(f"  trying {manager}...")
        ok, version = _try_install(manager, package)
        if ok:
            add_dependency(package, manager, version)
            click.echo(f"  ✓ installed {package}=={version} via {manager}")
            click.echo(f"  ✓ grim.lock updated")
            if manager == "vcpkg":
                _sync_vcpkg_manifest()
                click.echo(f"  ✓ vcpkg.json updated")
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

    # Group by manager
    vcpkg_deps = {k: v for k, v in deps.items() if v["manager"] == "vcpkg"}
    conan_deps = {k: v for k, v in deps.items() if v["manager"] == "conan"}

    failed = []

    if vcpkg_deps:
        if not _is_manager_available("vcpkg"):
            if not _prompt_and_install_manager("vcpkg"):
                failed.extend(vcpkg_deps.keys())
                vcpkg_deps = {}

        if vcpkg_deps:
            if _restore_vcpkg(vcpkg_deps):
                for name in vcpkg_deps:
                    click.echo(f"  ✓ {name}")
            else:
                failed.extend(vcpkg_deps.keys())

    for name, meta in conan_deps.items():
        if not _is_manager_available("conan"):
            if not _prompt_and_install_manager("conan"):
                click.echo(f"  ✗ {name} — skipped (conan unavailable)", err=True)
                failed.append(name)
                continue

        click.echo(f"  installing {name}=={meta['version']} via conan...")
        ok, _ = _try_install("conan", name)
        if ok:
            click.echo(f"  ✓ {name}")
        else:
            click.echo(f"  ✗ {name} — install failed", err=True)
            failed.append(name)

    if failed:
        click.echo(f"\n{len(failed)} package(s) failed: {', '.join(failed)}", err=True)
        raise SystemExit(1)

    click.echo(f"\n✓ all dependencies restored")


def _restore_vcpkg(deps: dict) -> bool:
    """Restore vcpkg deps via vcpkg.json manifest for version pinning."""
    if not _sync_vcpkg_manifest():
        return False

    click.echo(f"  generated vcpkg.json with {len(deps)} package(s)")

    result = subprocess.run([_vcpkg_bin(), "install"], text=True)

    if result.returncode != 0:
        click.echo("  error: vcpkg install failed.", err=True)
        return False

    click.echo("  ✓ all vcpkg dependencies restored")
    return True


def _sync_vcpkg_manifest() -> bool:
    """Generate vcpkg.json from current grim.lock state."""
    lock = load_lock()
    deps = {k: v for k, v in lock.get("dependencies", {}).items() if v["manager"] == "vcpkg"}

    if not deps:
        return True

    baseline_result = subprocess.run(
        ["git", "-C", str(Path.home() / ".vcpkg"), "rev-parse", "HEAD"],
        capture_output=True, text=True,
    )
    if baseline_result.returncode != 0:
        click.echo("  error: could not get vcpkg baseline.", err=True)
        return False

    baseline = baseline_result.stdout.strip()

    manifest = {
        "name": "grim-project",
        "version": "0.1.0",
        "builtin-baseline": baseline,
        "dependencies": [
            {"name": name, "version>=": meta["version"]}
            if meta["version"] != "unknown"
            else name
            for name, meta in deps.items()
        ]
    }

    manifest_path = Path.cwd() / "vcpkg.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    return True


# ---------------------------------------------------------------------------
# Manager availability
# ---------------------------------------------------------------------------

def _is_manager_available(manager: str) -> bool:
    """Check if a package manager is available via PATH or known install location."""
    if manager == "vcpkg":
        return bool(shutil.which("vcpkg")) or (Path.home() / ".vcpkg" / "vcpkg").exists()
    return bool(shutil.which(manager))


def _vcpkg_bin() -> str:
    """Return vcpkg binary — from PATH or known install location."""
    return shutil.which("vcpkg") or str(Path.home() / ".vcpkg" / "vcpkg")


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

    if success and _is_manager_available(manager):
        return True

    click.echo(
        f"\n  '{manager}' still not found after install.\n"
        f"  You may need to open a new terminal or add it to PATH manually.",
        err=True,
    )
    return False


def _auto_install_vcpkg() -> bool:
    """Clone and bootstrap vcpkg into ~/.vcpkg and add to session PATH."""
    vcpkg_dir = Path.home() / ".vcpkg"

    # Already fully installed
    if vcpkg_dir.exists() and (vcpkg_dir / "vcpkg").exists():
        click.echo("  vcpkg binary found, skipping clone and bootstrap.")
        os.environ["PATH"] = str(vcpkg_dir) + os.pathsep + os.environ.get("PATH", "")
        return True

    # Stale/incomplete clone
    if vcpkg_dir.exists() and not (vcpkg_dir / "vcpkg").exists():
        click.echo("  ~/.vcpkg exists but vcpkg binary is missing. Removing and re-cloning...")
        shutil.rmtree(vcpkg_dir)

    # Fresh clone
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
        bootstrap = vcpkg_dir / "bootstrap-vcpkg.bat"
        cmd = [str(bootstrap), "-disableMetrics"]
    else:
        bootstrap = vcpkg_dir / "bootstrap-vcpkg.sh"
        cmd = ["bash", str(bootstrap), "-disableMetrics"]

    if not bootstrap.exists():
        click.echo(f"  error: bootstrap script not found at {bootstrap}", err=True)
        click.echo(f"  The clone may have failed silently. Try: rm -rf ~/.vcpkg and retry.", err=True)
        return False

    if platform.system() != "Windows":
        bootstrap.chmod(bootstrap.stat().st_mode | 0o111)

    result = subprocess.run(cmd)
    if result.returncode != 0:
        click.echo("  error: bootstrap failed.", err=True)
        return False

    _persist_to_path(vcpkg_dir)
    os.environ["PATH"] = str(vcpkg_dir) + os.pathsep + os.environ.get("PATH", "")

    click.echo(f"  ✓ vcpkg installed at {vcpkg_dir}")
    return True


def _persist_to_path(directory: Path) -> None:
    """Append directory to PATH in the user's shell profile."""
    export_line = f'export PATH="{directory}:$PATH"'

    shell = os.environ.get("SHELL", "")
    if "zsh" in shell:
        profile = Path.home() / ".zshrc"
    else:
        profile = Path.home() / ".bashrc"

    if profile.exists() and export_line in profile.read_text():
        return

    with profile.open("a") as f:
        f.write(f"\n# Added by grim\n{export_line}\n")

    click.echo(f"  ✓ added to {profile} — run 'source {profile}' or open a new terminal")


def _auto_install_conan() -> bool:
    """Install conan via pip, falling back to pipx if pip is blocked."""
    click.echo("  Installing conan via pip...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "conan", "--quiet"],
    )
    if result.returncode == 0:
        click.echo("  ✓ conan installed")
        return True

    if shutil.which("pipx"):
        click.echo("  pip blocked — trying pipx...")
        result = subprocess.run(["pipx", "install", "conan"])
        if result.returncode == 0:
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
        [_vcpkg_bin(), "install", package],
        capture_output=True, text=True,
    )
    if result.stdout:
        click.echo(result.stdout, nl=False)
    if result.stderr:
        click.echo(result.stderr, nl=False)

    if result.returncode == 0:
        combined = result.stdout + result.stderr
        return True, _parse_vcpkg_version(combined, package)
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
    """Parse version from vcpkg output — handles 'fmt:x64-linux@12.1.0' format."""
    for line in output.splitlines():
        if package.lower() in line.lower() and "@" in line:
            return line.strip().split("@")[-1]
    return "unknown"


def _parse_conan_version(output: str, package: str) -> str:
    base = package.split("/")[0]
    for line in output.splitlines():
        if "/" in line and base.lower() in line.lower():
            return line.strip().split("/")[-1].split("@")[0]
    return "unknown"