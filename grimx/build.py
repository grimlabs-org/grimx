"""
grimx.build
Orchestrate CMake configure, build, test, and run.
"""

from __future__ import annotations

import os
import subprocess
import shutil
from pathlib import Path

import click

BUILD_DIR = "build"


def run() -> None:
    """Configure and build the project."""
    _require_tool("cmake")
    _guard_project_root()
    _cmake_configure()
    _cmake_build()


def run_tests() -> None:
    """Run tests via CTest."""
    _require_tool("ctest")
    _guard_project_root()

    build_path = _build_path()
    if not build_path.exists():
        click.echo("No build directory found — running build first...")
        run()

    click.echo("Running tests...")
    result = subprocess.run(
        ["ctest", "--output-on-failure", "--test-dir", str(build_path)],
    )
    if result.returncode != 0:
        raise SystemExit(result.returncode)
    click.echo("✓ all tests passed")


def run_app(args: list[str] | None = None) -> None:
    """Run the compiled application binary, forwarding any extra args."""
    _guard_project_root()

    build_path = _build_path()
    if not build_path.exists():
        click.echo("error: no build directory found. Run 'grimx build' first.", err=True)
        raise SystemExit(1)

    project_name = Path.cwd().name
    binary = build_path / project_name

    if not binary.exists():
        candidates = [
            p for p in build_path.iterdir()
            if p.is_file() and _is_executable(p)
        ]
        if not candidates:
            click.echo("error: no binary found in build/. Run 'grimx build' first.", err=True)
            raise SystemExit(1)
        binary = candidates[0]

    click.echo(f"Running {binary.name}...")
    result = subprocess.run([str(binary)] + (args or []))
    raise SystemExit(result.returncode)


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _cmake_configure() -> None:
    project_root = Path.cwd()
    build_path = _build_path()
    build_path.mkdir(exist_ok=True)

    toolchain = _vcpkg_toolchain()

    cmd = [
        "cmake",
        str(project_root),
        f"-B{build_path}",
        "-DCMAKE_BUILD_TYPE=Debug",
    ]

    if toolchain:
        cmd.append(f"-DCMAKE_TOOLCHAIN_FILE={toolchain}")
    else:
        click.echo(
            "  warning: vcpkg toolchain not found — packages may not resolve.\n"
            "  Expected: ~/.vcpkg/scripts/buildsystems/vcpkg.cmake",
            err=True,
        )

    click.echo("Configuring with CMake...")
    result = subprocess.run(cmd, cwd=project_root)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def _cmake_build() -> None:
    build_path = _build_path()
    click.echo("Building...")
    result = subprocess.run(
        ["cmake", "--build", str(build_path), "--parallel"],
    )
    if result.returncode != 0:
        raise SystemExit(result.returncode)
    click.echo("✓ build succeeded")


def _vcpkg_toolchain() -> str | None:
    """
    Locate the vcpkg CMake toolchain file.
    Checks ~/.vcpkg — the grimx install location.
    Does not depend on VCPKG_ROOT being set.
    """
    candidate = Path.home() / ".vcpkg" / "scripts" / "buildsystems" / "vcpkg.cmake"
    if candidate.exists():
        return str(candidate)
    return None


def _build_path() -> Path:
    return Path.cwd() / BUILD_DIR


def _guard_project_root() -> None:
    cwd = Path.cwd()
    has_cmake = (cwd / "CMakeLists.txt").exists()
    has_config = (cwd / "grimx.config").exists()

    if not has_cmake and not has_config:
        click.echo(
            "error: no CMakeLists.txt or grimx.config found in current directory.\n"
            "  Run this command from inside a GRIMX project.",
            err=True,
        )
        raise SystemExit(1)

    if not has_cmake:
        click.echo(
            "error: CMakeLists.txt not found.\n"
            "  Make sure your project was scaffolded correctly.",
            err=True,
        )
        raise SystemExit(1)


def _require_tool(name: str) -> None:
    if not shutil.which(name):
        click.echo(f"error: '{name}' not found in PATH.", err=True)
        click.echo("  Install it and re-run, or check 'grimx doctor' (coming in v2).")
        raise SystemExit(1)


def _is_executable(path: Path) -> bool:
    return os.access(path, os.X_OK)