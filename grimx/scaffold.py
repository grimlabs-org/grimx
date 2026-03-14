"""
grimx.scaffold
Interactive project creation — create-next-app style.
"""

from __future__ import annotations

import shutil
from importlib import resources
from pathlib import Path

import click

from grimx.config import write_config, write_lock, DEFAULT_CONFIG

TEMPLATE_MAP = {
    "c":            "c",
    "cpp":          "cpp",
    "embedded-c":   "embedded_c",
    "embedded-cpp": "embedded_cpp",
}

PROJECT_TYPES  = ["cpp", "c", "embedded-cpp", "embedded-c"]
CPP_STANDARDS  = ["17", "20", "14"]
C_STANDARDS    = ["11", "17", "99"]
MANAGERS       = ["vcpkg", "conan", "both", "none"]


def create_project(name: str | None, project_type: str | None) -> None:
    click.echo("")

    # ── Project name ──────────────────────────────────────────────────────
    if not name:
        name = click.prompt("  Project name", default="my_project")

    dest = Path.cwd() / name
    if dest.exists():
        click.echo(f"\nerror: directory '{name}' already exists.", err=True)
        raise SystemExit(1)

    # ── Project type ──────────────────────────────────────────────────────
    if not project_type:
        click.echo("")
        click.echo("  What type of project?")
        for i, t in enumerate(PROJECT_TYPES, 1):
            marker = " (default)" if t == "cpp" else ""
            click.echo(f"    {i}. {t}{marker}")
        choice = click.prompt("  Choice", default="1", show_default=False)
        try:
            project_type = PROJECT_TYPES[int(choice) - 1]
        except (ValueError, IndexError):
            project_type = "cpp"

    # ── Language standard ─────────────────────────────────────────────────
    is_cpp = "cpp" in project_type
    if is_cpp:
        standards = CPP_STANDARDS
        lang      = "C++"
        default_std = "17"
    else:
        standards = C_STANDARDS
        lang      = "C"
        default_std = "11"

    click.echo("")
    click.echo(f"  {lang} standard?")
    for i, s in enumerate(standards, 1):
        marker = " (default)" if s == default_std else ""
        click.echo(f"    {i}. {lang}{s}{marker}")
    std_choice = click.prompt("  Choice", default="1", show_default=False)
    try:
        std = standards[int(std_choice) - 1]
    except (ValueError, IndexError):
        std = default_std

    # ── Package manager ───────────────────────────────────────────────────
    click.echo("")
    click.echo("  Package manager?")
    for i, m in enumerate(MANAGERS, 1):
        marker = " (default)" if m == "vcpkg" else ""
        click.echo(f"    {i}. {m}{marker}")
    mgr_choice = click.prompt("  Choice", default="1", show_default=False)
    try:
        mgr = MANAGERS[int(mgr_choice) - 1]
    except (ValueError, IndexError):
        mgr = "vcpkg"

    if mgr == "both":
        priority = ["vcpkg", "conan"]
    elif mgr == "none":
        priority = []
    else:
        priority = [mgr]

    # ── Summary ───────────────────────────────────────────────────────────
    click.echo("")
    click.echo(f"  Creating project '{name}'")
    click.echo(f"    type     : {project_type}")
    click.echo(f"    standard : {lang}{std}")
    click.echo(f"    managers : {', '.join(priority) if priority else 'none'}")
    click.echo("")

    # ── Scaffold ──────────────────────────────────────────────────────────
    template_src = _get_template_path(TEMPLATE_MAP[project_type])
    shutil.copytree(template_src, dest)

    for gitkeep in dest.rglob(".gitkeep"):
        gitkeep.unlink()

    _patch_cmakelists(dest, name, project_type, std)
    _write_readme(dest, name, project_type, std)
    _write_gitignore(dest)
    _write_clang_format(dest)

    # Ensure all expected directories exist
    for d in ["include", "docs", "cmake"]:
        (dest / d).mkdir(exist_ok=True)

    write_config({"package_manager": {"priority": priority}}, root=dest)
    write_lock({"dependencies": {}}, root=dest)

    click.echo(f"  ✓ {dest}")
    click.echo("")
    click.echo("  Next steps:")
    click.echo(f"    cd {name}")
    if priority:
        click.echo(f"    grimx install <package>")
    click.echo(f"    grimx build")
    click.echo(f"    grimx run")
    click.echo("")


# ---------------------------------------------------------------------------
# Patching and file generation
# ---------------------------------------------------------------------------

def _patch_cmakelists(dest: Path, name: str, project_type: str, std: str) -> None:
    cmake = dest / "CMakeLists.txt"
    if not cmake.exists():
        return
    text = cmake.read_text()
    is_cpp = "cpp" in project_type
    text = text.replace("project(PROJECT_NAME", f"project({name}")
    if is_cpp:
        text = text.replace("set(CMAKE_CXX_STANDARD 17)", f"set(CMAKE_CXX_STANDARD {std})")
    else:
        text = text.replace("set(CMAKE_C_STANDARD 11)", f"set(CMAKE_C_STANDARD {std})")
    cmake.write_text(text)


def _write_readme(dest: Path, name: str, project_type: str, std: str) -> None:
    is_cpp = "cpp" in project_type
    lang   = "C++" if is_cpp else "C"
    content = f"""# {name}

A {lang}{std} project.

## Build

```bash
grimx build
grimx test
grimx run
```

## Dependencies

Install a dependency:

```bash
grimx install <package>
```

Restore from lock file:

```bash
grimx install
```

## Project Structure

```
{name}/
  src/        source files
  include/    project headers
  tests/      unit tests
  docs/       documentation
  cmake/      cmake modules
  CMakeLists.txt
  grimx.config
  grimx.lock
```
"""
    (dest / "README.md").write_text(content)


def _write_gitignore(dest: Path) -> None:
    (dest / ".gitignore").write_text(
        "# Build output\n"
        "build/\n"
        "out/\n\n"
        "# Dependencies\n"
        "vcpkg_installed/\n"
        ".conan/\n\n"
        "# Editor\n"
        ".vscode/\n"
        ".idea/\n"
        "*.swp\n"
        "*.swo\n\n"
        "# OS\n"
        ".DS_Store\n"
        "Thumbs.db\n\n"
        "# GRIMX\n"
        "grimx.lock\n\n"
    )


def _write_clang_format(dest: Path) -> None:
    (dest / ".clang-format").write_text(
        "---\n"
        "BasedOnStyle: LLVM\n"
        "IndentWidth: 4\n"
        "ColumnLimit: 100\n"
        "AllowShortFunctionsOnASingleLine: None\n"
        "AllowShortIfStatementsOnASingleLine: Never\n"
        "BreakBeforeBraces: Attach\n"
        "---\n"
    )


# ---------------------------------------------------------------------------
# Template resolution
# ---------------------------------------------------------------------------

def _get_template_path(template_key: str) -> Path:
    pkg = resources.files("grimx") / "templates" / template_key
    with resources.as_file(pkg) as path:
        return Path(str(path)).resolve()
