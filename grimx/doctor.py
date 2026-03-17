"""
grimx.doctor
Diagnose the development environment and project health.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import click

from grimx.config import load_lock


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    label:  str
    status: str          # "ok" | "error" | "warning"
    detail: str = ""     # version string, path, or error message
    hint:   str = ""     # install/fix command


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run() -> None:
    """Run all diagnostic checks and print a structured report."""
    click.echo("")

    sections: list[tuple[str, list[CheckResult]]] = [
        ("Environment", _check_environment()),
        ("vcpkg",       _check_vcpkg()),
    ]

    # Only run project checks if we're inside a grimx project
    if _is_project_root(Path.cwd()):
        sections.append(("Project", _check_project()))

    error_count   = 0
    warning_count = 0

    for title, results in sections:
        click.echo(f"  {title}")
        for r in results:
            _print_result(r)
            if r.status == "error":
                error_count += 1
            elif r.status == "warning":
                warning_count += 1
        click.echo("")

    # Summary
    if error_count == 0 and warning_count == 0:
        click.echo("  ✓ all checks passed\n")
    else:
        parts = []
        if error_count:
            parts.append(f"{error_count} error{'s' if error_count != 1 else ''}")
        if warning_count:
            parts.append(f"{warning_count} warning{'s' if warning_count != 1 else ''}")
        click.echo(f"  {', '.join(parts)}\n")

    if error_count > 0:
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# Section: Environment
# ---------------------------------------------------------------------------

def _check_environment() -> list[CheckResult]:
    results = []

    # cmake — required, minimum 3.20
    cmake_version = _get_version(["cmake", "--version"])
    if cmake_version is None:
        results.append(CheckResult(
            label="cmake",
            status="error",
            detail="not found",
            hint="https://cmake.org/download/",
        ))
    else:
        major, minor = _parse_version(cmake_version)
        if (major, minor) < (3, 20):
            results.append(CheckResult(
                label="cmake",
                status="error",
                detail=f"{cmake_version} (need ≥ 3.20)",
                hint="upgrade cmake: https://cmake.org/download/",
            ))
        else:
            results.append(CheckResult(
                label="cmake",
                status="ok",
                detail=cmake_version,
            ))

    # C++ compiler — required, at least one of gcc/clang
    gcc_version   = _get_version(["gcc",   "--version"])
    clang_version = _get_version(["clang", "--version"])

    if gcc_version:
        results.append(CheckResult(label="gcc",   status="ok",    detail=gcc_version))
    if clang_version:
        results.append(CheckResult(label="clang", status="ok",    detail=clang_version))
    if not gcc_version and not clang_version:
        results.append(CheckResult(
            label="compiler",
            status="error",
            detail="no C++ compiler found (gcc or clang required)",
            hint="sudo apt install build-essential",
        ))

    # make — required fallback if ninja absent
    if shutil.which("make"):
        results.append(CheckResult(label="make", status="ok"))
    else:
        results.append(CheckResult(
            label="make",
            status="error",
            detail="not found",
            hint="sudo apt install make",
        ))

    # ninja — optional, recommended
    if shutil.which("ninja"):
        ninja_version = _get_version(["ninja", "--version"]) or ""
        results.append(CheckResult(label="ninja", status="ok", detail=ninja_version))
    else:
        results.append(CheckResult(
            label="ninja",
            status="warning",
            detail="not found (recommended for faster builds)",
            hint="sudo apt install ninja-build",
        ))

    # ccache — optional, recommended
    if shutil.which("ccache"):
        ccache_version = _get_version(["ccache", "--version"]) or ""
        results.append(CheckResult(label="ccache", status="ok", detail=ccache_version))
    else:
        results.append(CheckResult(
            label="ccache",
            status="warning",
            detail="not found (recommended for faster rebuilds)",
            hint="sudo apt install ccache",
        ))

    # mold — optional, recommended
    if shutil.which("mold"):
        mold_version = _get_version(["mold", "--version"]) or ""
        results.append(CheckResult(label="mold", status="ok", detail=mold_version))
    else:
        results.append(CheckResult(
            label="mold",
            status="warning",
            detail="not found (recommended for faster linking)",
            hint="sudo apt install mold",
        ))

    # git — required for vcpkg baseline
    if shutil.which("git"):
        git_version = _get_version(["git", "--version"]) or ""
        results.append(CheckResult(label="git", status="ok", detail=git_version))
    else:
        results.append(CheckResult(
            label="git",
            status="error",
            detail="not found",
            hint="sudo apt install git",
        ))

    return results


# ---------------------------------------------------------------------------
# Section: vcpkg
# ---------------------------------------------------------------------------

def _check_vcpkg() -> list[CheckResult]:
    results = []

    vcpkg_dir    = Path.home() / ".vcpkg"
    vcpkg_bin    = vcpkg_dir / "vcpkg"
    toolchain    = vcpkg_dir / "scripts" / "buildsystems" / "vcpkg.cmake"

    # Binary
    system_vcpkg = shutil.which("vcpkg")
    if vcpkg_bin.exists():
        results.append(CheckResult(
            label="binary",
            status="ok",
            detail=str(vcpkg_bin),
        ))
    elif system_vcpkg:
        results.append(CheckResult(
            label="binary",
            status="ok",
            detail=system_vcpkg,
        ))
    else:
        results.append(CheckResult(
            label="binary",
            status="error",
            detail="not found",
            hint="run grimx install to auto-install vcpkg",
        ))

    # Bootstrap — binary must be executable
    if vcpkg_bin.exists():
        if os.access(vcpkg_bin, os.X_OK):
            results.append(CheckResult(label="bootstrap", status="ok"))
        else:
            results.append(CheckResult(
                label="bootstrap",
                status="error",
                detail="vcpkg binary not executable",
                hint=f"chmod +x {vcpkg_bin}",
            ))

    # Toolchain file
    if toolchain.exists():
        results.append(CheckResult(
            label="toolchain",
            status="ok",
            detail=str(toolchain),
        ))
    else:
        results.append(CheckResult(
            label="toolchain",
            status="error",
            detail=f"{toolchain} not found",
            hint="re-run vcpkg bootstrap: bash ~/.vcpkg/bootstrap-vcpkg.sh",
        ))

    return results


# ---------------------------------------------------------------------------
# Section: Project
# ---------------------------------------------------------------------------

def _check_project() -> list[CheckResult]:
    results = []
    cwd = Path.cwd()

    # CMakeLists.txt
    if (cwd / "CMakeLists.txt").exists():
        results.append(CheckResult(label="CMakeLists.txt", status="ok"))
    else:
        results.append(CheckResult(
            label="CMakeLists.txt",
            status="error",
            detail="not found",
            hint="run grimx new to scaffold a project",
        ))

    # grimx.config
    if (cwd / "grimx.config").exists():
        results.append(CheckResult(label="grimx.config", status="ok"))
    else:
        results.append(CheckResult(
            label="grimx.config",
            status="error",
            detail="not found",
            hint="run grimx new to scaffold a project",
        ))

    # grimx.lock
    lock_path = cwd / "grimx.lock"
    if lock_path.exists():
        results.append(CheckResult(label="grimx.lock", status="ok"))
    else:
        results.append(CheckResult(
            label="grimx.lock",
            status="warning",
            detail="not found — no packages installed yet",
            hint="run grimx install <package>",
        ))
        return results  # remaining checks need the lock

    # vcpkg.json in sync with grimx.lock
    vcpkg_json_path = cwd / "vcpkg.json"
    if not vcpkg_json_path.exists():
        results.append(CheckResult(
            label="vcpkg.json",
            status="warning",
            detail="not found",
            hint="run grimx install to generate it",
        ))
    else:
        lock_pkgs  = set(load_lock().get("dependencies", {}).keys())
        try:
            vcpkg_data  = json.loads(vcpkg_json_path.read_text())
            vcpkg_deps  = vcpkg_data.get("dependencies", [])
            vcpkg_names = {
                (d["name"] if isinstance(d, dict) else d)
                for d in vcpkg_deps
            }
            missing = lock_pkgs - vcpkg_names
            if missing:
                results.append(CheckResult(
                    label="vcpkg.json",
                    status="warning",
                    detail=f"out of sync — missing: {', '.join(sorted(missing))}",
                    hint="run grimx install to resync",
                ))
            else:
                results.append(CheckResult(label="vcpkg.json", status="ok"))
        except Exception:
            results.append(CheckResult(
                label="vcpkg.json",
                status="warning",
                detail="could not parse",
                hint="run grimx install to regenerate it",
            ))

    # vcpkg_installed/ populated
    vcpkg_installed = cwd / "vcpkg_installed"
    if not vcpkg_installed.exists():
        results.append(CheckResult(
            label="vcpkg_installed/",
            status="warning",
            detail="not populated",
            hint="run grimx install",
        ))
    else:
        results.append(CheckResult(
            label="vcpkg_installed/",
            status="ok",
            detail=str(vcpkg_installed),
        ))

    return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_project_root(path: Path) -> bool:
    return (path / "grimx.config").exists() or (path / "CMakeLists.txt").exists()


def _get_version(cmd: list[str]) -> str | None:
    """Run a --version command and return the first version-like token found."""
    if not shutil.which(cmd[0]):
        return None
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        output = (result.stdout + result.stderr).strip()
        # Extract first version-like token (digits and dots)
        for token in output.split():
            if token[0].isdigit() and "." in token:
                return token.strip(".,")
        return output.splitlines()[0] if output else None
    except Exception:
        return None


def _parse_version(version_str: str) -> tuple[int, int]:
    """Parse 'X.Y.Z' into (major, minor). Returns (0, 0) on failure."""
    try:
        parts = version_str.split(".")
        return int(parts[0]), int(parts[1])
    except Exception:
        return 0, 0


def _print_result(r: CheckResult) -> None:
    """Print a single check result with consistent formatting."""
    if r.status == "ok":
        icon  = click.style("✓", fg="green")
        label = click.style(f"{r.label:<16}", fg="white")
        detail = click.style(r.detail, fg="bright_black") if r.detail else ""
        click.echo(f"    {icon} {label} {detail}")

    elif r.status == "warning":
        icon  = click.style("⚠", fg="yellow")
        label = click.style(f"{r.label:<16}", fg="white")
        detail = click.style(r.detail, fg="yellow") if r.detail else ""
        line = f"    {icon} {label} {detail}"
        if r.hint:
            line += click.style(f"\n      → {r.hint}", fg="bright_black")
        click.echo(line)

    elif r.status == "error":
        icon  = click.style("✗", fg="red")
        label = click.style(f"{r.label:<16}", fg="white")
        detail = click.style(r.detail, fg="red") if r.detail else ""
        line = f"    {icon} {label} {detail}"
        if r.hint:
            line += click.style(f"\n      → {r.hint}", fg="bright_black")
        click.echo(line)