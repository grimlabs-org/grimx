"""
grimx.install
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

from grimx.config import load_config, load_lock, add_dependency
from grimx.cmake_patch import patch_from_vcpkg_output, patch_all_from_lock


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
            "  Edit grimx.config and set priority = [\"vcpkg\"] or [\"conan\"].",
            err=True,
        )
        raise SystemExit(1)

    for manager in priority:
        if not _is_manager_available(manager):
            if not _prompt_and_install_manager(manager):
                continue

        click.echo(f"  trying {manager}...")
        ok, version, vcpkg_output = _try_install(manager, package)
        if ok:
            add_dependency(package, manager, version)
            click.echo(f"  ✓ installed {package}=={version} via {manager}")
            click.echo(f"  ✓ grimx.lock updated")
            if manager == "vcpkg":
                _sync_vcpkg_manifest()
                click.echo(f"  ✓ vcpkg.json updated")
                # Primary path — parse cmake hints from vcpkg stdout.
                # vcpkg prints hints for every installed package so this
                # covers all packages in the manifest in one pass.
                patch_from_vcpkg_output(vcpkg_output, Path.cwd() / "CMakeLists.txt")
            else:
                patch_all_from_lock(load_lock(), Path.cwd() / "CMakeLists.txt")
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
        click.echo("grimx.lock is empty — nothing to install.")
        return

    click.echo(f"Restoring {len(deps)} dependencies from grimx.lock...")

    vcpkg_deps = {k: v for k, v in deps.items() if v["manager"] == "vcpkg"}
    conan_deps = {k: v for k, v in deps.items() if v["manager"] == "conan"}

    failed = []

    if vcpkg_deps:
        if not _is_manager_available("vcpkg"):
            if not _prompt_and_install_manager("vcpkg"):
                failed.extend(vcpkg_deps.keys())
                vcpkg_deps = {}

        if vcpkg_deps:
            ok, vcpkg_output = _restore_vcpkg(vcpkg_deps)
            if ok:
                for name in vcpkg_deps:
                    click.echo(f"  ✓ {name}")
                if vcpkg_output:
                    patch_from_vcpkg_output(vcpkg_output, Path.cwd() / "CMakeLists.txt")
                else:
                    patch_all_from_lock(load_lock(), Path.cwd() / "CMakeLists.txt")
            else:
                failed.extend(vcpkg_deps.keys())

    for name, meta in conan_deps.items():
        if not _is_manager_available("conan"):
            if not _prompt_and_install_manager("conan"):
                click.echo(f"  ✗ {name} — skipped (conan unavailable)", err=True)
                failed.append(name)
                continue

        click.echo(f"  installing {name}=={meta['version']} via conan...")
        ok, _, _ = _try_install("conan", name)
        if ok:
            click.echo(f"  ✓ {name}")
        else:
            click.echo(f"  ✗ {name} — install failed", err=True)
            failed.append(name)

    if failed:
        click.echo(f"\n{len(failed)} package(s) failed: {', '.join(failed)}", err=True)
        raise SystemExit(1)

    if conan_deps:
        patch_all_from_lock(load_lock(), Path.cwd() / "CMakeLists.txt")

    click.echo(f"\n✓ all dependencies restored")


def _restore_vcpkg(deps: dict) -> tuple[bool, str]:
    """Restore all vcpkg deps. Returns (success, vcpkg_output)."""
    if not _sync_vcpkg_manifest():
        return False, ""

    click.echo(f"  generated vcpkg.json with {len(deps)} package(s)")

    install_root = Path.cwd() / "vcpkg_installed"
    result = subprocess.run(
        [_vcpkg_bin(), "install", f"--x-install-root={install_root}"],
        capture_output=True, text=True,
    )

    if result.stdout:
        click.echo(result.stdout, nl=False)
    if result.stderr:
        click.echo(result.stderr, nl=False)

    if result.returncode != 0:
        click.echo("  error: vcpkg install failed.", err=True)
        return False, ""

    click.echo("  ✓ all vcpkg dependencies restored")
    return True, result.stdout + result.stderr


def _sync_vcpkg_manifest() -> bool:
    """Write vcpkg.json from current grimx.lock state."""
    lock = load_lock()
    deps = {
        k: v for k, v in lock.get("dependencies", {}).items()
        if v["manager"] == "vcpkg"
    }

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
        "name": "grimx-project",
        "version": "0.1.0",
        "builtin-baseline": baseline,
        "dependencies": [
            {"name": name, "version>=": meta["version"]}
            if meta["version"] != "unknown"
            else name
            for name, meta in deps.items()
        ],
    }

    (Path.cwd() / "vcpkg.json").write_text(json.dumps(manifest, indent=2))
    return True


def _write_vcpkg_manifest_with(new_package: str) -> bool:
    """
    Write vcpkg.json with all existing lock entries + new package.
    Called before vcpkg install to ensure manifest mode is active.
    """
    lock = load_lock()
    existing_deps = {
        k: v for k, v in lock.get("dependencies", {}).items()
        if v["manager"] == "vcpkg"
    }

    baseline_result = subprocess.run(
        ["git", "-C", str(Path.home() / ".vcpkg"), "rev-parse", "HEAD"],
        capture_output=True, text=True,
    )
    if baseline_result.returncode != 0:
        click.echo("  error: could not get vcpkg baseline.", err=True)
        return False

    baseline = baseline_result.stdout.strip()

    dependencies: list = [
        {"name": name, "version>=": meta["version"]}
        if meta["version"] != "unknown"
        else name
        for name, meta in existing_deps.items()
    ]

    existing_names = {
        (d["name"] if isinstance(d, dict) else d)
        for d in dependencies
    }
    if new_package not in existing_names:
        dependencies.append(new_package)

    manifest = {
        "name": "grimx-project",
        "version": "0.1.0",
        "builtin-baseline": baseline,
        "dependencies": dependencies,
    }

    (Path.cwd() / "vcpkg.json").write_text(json.dumps(manifest, indent=2))
    return True


# ---------------------------------------------------------------------------
# Manager availability
# ---------------------------------------------------------------------------

def _is_manager_available(manager: str) -> bool:
    if manager == "vcpkg":
        return bool(shutil.which("vcpkg")) or (Path.home() / ".vcpkg" / "vcpkg").exists()
    return bool(shutil.which(manager))


def _vcpkg_bin() -> str:
    return shutil.which("vcpkg") or str(Path.home() / ".vcpkg" / "vcpkg")


# ---------------------------------------------------------------------------
# Auto-install managers
# ---------------------------------------------------------------------------

def _prompt_and_install_manager(manager: str) -> bool:
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
    vcpkg_dir = Path.home() / ".vcpkg"

    if vcpkg_dir.exists() and (vcpkg_dir / "vcpkg").exists():
        click.echo("  vcpkg binary found, skipping clone and bootstrap.")
        os.environ["PATH"] = str(vcpkg_dir) + os.pathsep + os.environ.get("PATH", "")
        return True

    if vcpkg_dir.exists() and not (vcpkg_dir / "vcpkg").exists():
        click.echo("  ~/.vcpkg exists but vcpkg binary is missing. Removing and re-cloning...")
        shutil.rmtree(vcpkg_dir)

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
        return False

    if platform.system() != "Windows":
        bootstrap.chmod(bootstrap.stat().st_mode | 0o111)

    result = subprocess.run(cmd)
    if result.returncode != 0:
        click.echo("  error: bootstrap failed.", err=True)
        return False

    _persist_vcpkg_env(vcpkg_dir)
    os.environ["PATH"] = str(vcpkg_dir) + os.pathsep + os.environ.get("PATH", "")
    click.echo(f"  ✓ vcpkg installed at {vcpkg_dir}")
    return True


def _persist_vcpkg_env(vcpkg_dir: Path) -> None:
    export_line = f'export PATH="{vcpkg_dir}:$PATH"'
    shell   = os.environ.get("SHELL", "")
    profile = Path.home() / (".zshrc" if "zsh" in shell else ".bashrc")
    existing = profile.read_text() if profile.exists() else ""
    if export_line in existing:
        return
    with profile.open("a") as f:
        f.write(f"\n# Added by grimx\n{export_line}\n")
    click.echo(f"  ✓ added to {profile} — run 'source {profile}' or open a new terminal")


def _auto_install_conan() -> bool:
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

def _try_install(manager: str, package: str) -> tuple[bool, str, str]:
    """Returns (success, version, raw_output)."""
    if manager == "vcpkg":
        return _vcpkg_install(package)
    if manager == "conan":
        ok, version = _conan_install(package)
        return ok, version, ""
    return False, "", ""


def _vcpkg_install(package: str) -> tuple[bool, str, str]:
    """
    Install via vcpkg manifest mode.
    Returns (success, version, combined_output).
    combined_output is parsed by patch_from_vcpkg_output for cmake hints.
    """
    if not _write_vcpkg_manifest_with(package):
        return False, "", ""

    install_root = Path.cwd() / "vcpkg_installed"

    result = subprocess.run(
        [_vcpkg_bin(), "install", f"--x-install-root={install_root}"],
        capture_output=True, text=True,
    )

    if result.stdout:
        click.echo(result.stdout, nl=False)
    if result.stderr:
        click.echo(result.stderr, nl=False)

    if result.returncode != 0:
        return False, "", ""

    combined = result.stdout + result.stderr
    return True, _parse_vcpkg_version(combined, package), combined


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
        if package.lower() in line.lower() and "@" in line:
            return line.strip().split("@")[-1]
    return "unknown"


def _parse_conan_version(output: str, package: str) -> str:
    base = package.split("/")[0]
    for line in output.splitlines():
        if "/" in line and base.lower() in line.lower():
            return line.strip().split("/")[-1].split("@")[0]
    return "unknown"