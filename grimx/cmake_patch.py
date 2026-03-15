"""
grimx.cmake_patch
Automatically patch CMakeLists.txt after a package is installed.

Assumes manifest mode: vcpkg_installed/ lives in the project root.
No VCPKG_ROOT dependency.

Three-layer strategy:
  Layer 1: vcpkg usage file           — authoritative, covers 2000+ ports
  Layer 2: CMake *Config.cmake scan   — covers everything with CMake support
  Layer 3: pkg-config .pc scan        — covers Conan and system-style libs
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import click


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class UsageDirectives:
    find_package:    list[str] = field(default_factory=list)
    link_targets:    list[str] = field(default_factory=list)
    find_path_lines: list[str] = field(default_factory=list)
    include_vars:    list[str] = field(default_factory=list)
    is_header_only:  bool = False


# ---------------------------------------------------------------------------
# Public entry point — catch-up pass for all lock entries
# ---------------------------------------------------------------------------

def patch_all_from_lock(lock: dict, cmake_path: Path) -> None:
    """
    Patch CMakeLists.txt for every package currently in grimx.lock.
    Called after every successful install.
    """
    if not cmake_path.exists():
        return

    deps = lock.get("dependencies", {})
    if not deps:
        return

    project_root = cmake_path.parent
    content = cmake_path.read_text()
    any_changed = False

    for package in deps:
        directives, source = _resolve_directives(package, project_root)
        if directives is None:
            continue
        content, changed = _apply_directives(content, directives, source, silent=True)
        if changed:
            click.echo(f"  [cmake] patched '{package}' (source: {source})")
            any_changed = True

    if any_changed:
        _atomic_write(cmake_path, content)
        click.echo(f"  [cmake] ✓ CMakeLists.txt updated.")
    else:
        click.echo(f"  [cmake] CMakeLists.txt already up to date.")


# ---------------------------------------------------------------------------
# Internal — apply directives to content string
# ---------------------------------------------------------------------------

def _apply_directives(
    content: str,
    directives: UsageDirectives,
    source: str,
    silent: bool = False,
) -> tuple[str, bool]:
    changed = False

    for fp_line in directives.find_package:
        if not _already_present(content, fp_line):
            content = _inject_find_package(content, fp_line)
            if not silent:
                click.echo(f"  [cmake] + {fp_line}")
            changed = True

    for fp_line in directives.find_path_lines:
        if not _already_present(content, fp_line):
            content = _inject_find_package(content, fp_line)
            if not silent:
                click.echo(f"  [cmake] + {fp_line}")
            changed = True

    for target in directives.link_targets:
        if not _already_present(content, target):
            content = _inject_link_target(content, target)
            if not silent:
                click.echo(f"  [cmake] + target_link_libraries ... {target}")
            changed = True

    return content, changed


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------

def _atomic_write(cmake_path: Path, content: str) -> None:
    tmp = cmake_path.with_suffix(".tmp")
    tmp.write_text(content)
    tmp.replace(cmake_path)


# ---------------------------------------------------------------------------
# Layer resolution
# ---------------------------------------------------------------------------

def _resolve_directives(
    package: str, project_root: Path
) -> tuple[UsageDirectives | None, str]:

    # Layer 1: vcpkg usage file
    for share_dir in _find_package_share_dirs(package, project_root):
        usage = share_dir / "usage"
        if usage.exists():
            d = _classify_usage_file(usage.read_text())
            if d.find_package or d.find_path_lines:
                return d, "vcpkg usage file"

    # Layer 2: CMake *Config.cmake scan
    for share_dir in _find_package_share_dirs(package, project_root):
        result = _extract_from_cmake_config(share_dir)
        if result:
            pkg_name, targets = result
            d = UsageDirectives(
                find_package=[f"find_package({pkg_name} CONFIG REQUIRED)"],
                link_targets=targets,
            )
            return d, "CMake config file"

    # Layer 3: pkg-config .pc fallback
    result = _extract_from_pkgconfig(package, project_root)
    if result:
        find_lines, link_targets = result
        d = UsageDirectives(
            find_package=find_lines,
            link_targets=link_targets,
        )
        return d, "pkg-config"

    return None, ""


# ---------------------------------------------------------------------------
# Share directory discovery — vcpkg_installed/ only, no global paths
# ---------------------------------------------------------------------------

def _find_package_share_dirs(package: str, project_root: Path) -> list[Path]:
    """
    Find share/<package> dirs inside vcpkg_installed/ in the project root.

    This is the local manifest-mode install tree — equivalent to node_modules/.
    vcpkg_installed/ is guaranteed to exist after _vcpkg_install runs because
    install.py always writes vcpkg.json first and runs `vcpkg install` with no
    arguments, which forces vcpkg to populate the local tree every time.
    """
    candidates: list[Path] = []

    vcpkg_installed = project_root / "vcpkg_installed"
    if not vcpkg_installed.exists():
        return candidates

    for triplet_dir in vcpkg_installed.iterdir():
        if not triplet_dir.is_dir():
            continue
        share = triplet_dir / "share" / package
        if share.exists():
            candidates.append(share)

    return candidates


# ---------------------------------------------------------------------------
# Layer 1 — vcpkg usage file parser
# ---------------------------------------------------------------------------

def _classify_usage_file(content: str) -> UsageDirectives:
    """
    Line-by-line classifier.

    Skips alternative/header-only blocks preceded by a comment line
    (e.g. '# Or use the header-only version') so only the primary
    compiled target is linked, not both variants.
    Deduplicates find_package lines — usage files repeat them per variant.
    """
    d = UsageDirectives()
    skip_next_tll = False

    for line in content.splitlines():
        line = line.strip()

        if not line:
            # Blank line resets skip — new block starting
            skip_next_tll = False
            continue

        if line.startswith("#"):
            # Comment before target_link_libraries marks it as an alternative
            skip_next_tll = True
            continue

        if line.startswith("find_package("):
            if line not in d.find_package:
                d.find_package.append(line)
            skip_next_tll = False

        elif re.match(r'target_link_libraries\(', line):
            if not skip_next_tll:
                targets = re.findall(
                    r'(?:PRIVATE|PUBLIC|INTERFACE)\s+(.*?)\s*\)',
                    line,
                )
                if targets:
                    d.link_targets.extend(targets[0].split())
            skip_next_tll = False

        elif line.startswith("find_path("):
            d.find_path_lines.append(line)
            d.is_header_only = True
            skip_next_tll = False

        elif line.startswith("target_include_directories("):
            vars_used = re.findall(r'\$\{(\w+)\}', line)
            d.include_vars.extend(vars_used)
            skip_next_tll = False

    return d


# ---------------------------------------------------------------------------
# Layer 2 — CMake Config file scan
# ---------------------------------------------------------------------------

def _extract_from_cmake_config(share_pkg_dir: Path) -> tuple[str, list[str]] | None:
    config_files = (
        list(share_pkg_dir.glob("*Config.cmake")) +
        list(share_pkg_dir.glob("*-config.cmake"))
    )
    if not config_files:
        return None

    config = config_files[0]
    content = config.read_text(errors="replace")

    stem = config.stem
    pkg_name = re.sub(r'(?i)(Config|-config)$', '', stem)

    targets = re.findall(
        r'add_library\(([A-Za-z0-9_:]+)\s+(?:STATIC|SHARED|INTERFACE)\s+IMPORTED\)',
        content,
    )
    prop_targets = re.findall(
        r'set_target_properties\(([A-Za-z0-9_:]+)\s+PROPERTIES',
        content,
    )
    all_targets = list(dict.fromkeys(targets + prop_targets))
    ns_targets = [t for t in all_targets if "::" in t]
    final_targets = ns_targets if ns_targets else all_targets

    if not final_targets:
        return None

    return pkg_name, final_targets


# ---------------------------------------------------------------------------
# Layer 3 — pkg-config fallback
# ---------------------------------------------------------------------------

def _extract_from_pkgconfig(
    package: str, project_root: Path
) -> tuple[list[str], list[str]] | None:
    vcpkg_installed = project_root / "vcpkg_installed"
    if not vcpkg_installed.exists():
        return None

    pc_candidates = (
        list(vcpkg_installed.rglob(f"{package}.pc")) +
        list(vcpkg_installed.rglob(f"lib{package}.pc"))
    )

    if not pc_candidates:
        return None

    pc_content = pc_candidates[0].read_text(errors="replace")
    libs_line = re.search(r'^Libs:(.+)$', pc_content, re.MULTILINE)
    if not libs_line:
        return None

    libs = re.findall(r'-l(\S+)', libs_line.group(1))
    if not libs:
        return None

    find_lines = [f"find_library({lib.upper()}_LIB {lib})" for lib in libs]
    link_targets = [f"${{{lib.upper()}_LIB}}" for lib in libs]

    return find_lines, link_targets


# ---------------------------------------------------------------------------
# Idempotency check
# ---------------------------------------------------------------------------

def _normalise(s: str) -> str:
    return re.sub(r'\s+', '', s).lower()


def _already_present(content: str, cmake_fragment: str) -> bool:
    return _normalise(cmake_fragment) in _normalise(content)


# ---------------------------------------------------------------------------
# CMakeLists.txt injection helpers
# ---------------------------------------------------------------------------

def _inject_find_package(content: str, find_pkg: str) -> str:
    """
    Inject find_package() after the C++ standard config block so ordering
    is always: cmake_minimum_required → project → CXX standard → find_package.
    """
    strategies = [
        # After set(CMAKE_CXX_STANDARD_REQUIRED ON) — ideal position
        (r'(set\s*\(\s*CMAKE_CXX_STANDARD_REQUIRED\s+ON\s*\))', rf'\1\n\n{find_pkg}'),
        # After set(CMAKE_CXX_STANDARD ...) — if REQUIRED line absent
        (r'(set\s*\(\s*CMAKE_CXX_STANDARD\b[^\)]*\))', rf'\1\n\n{find_pkg}'),
        # After project() — standard grimx scaffold fallback
        (r'(project\s*\([^\)]*\)(?:\s+\w+\s+\d+)?)', rf'\1\n\n{find_pkg}'),
        # After cmake_minimum_required
        (r'(cmake_minimum_required\s*\([^\)]*\))', rf'\1\n\n{find_pkg}'),
        # Before first add_executable or add_library
        (r'(add_(?:executable|library)\s*\()', rf'{find_pkg}\n\n\1'),
    ]

    for pattern, replacement in strategies:
        if re.search(pattern, content, re.IGNORECASE):
            return re.sub(pattern, replacement, content, count=1, flags=re.IGNORECASE)

    return find_pkg + "\n\n" + content


def _inject_link_target(content: str, target: str) -> str:
    tll = re.compile(
        r'(target_link_libraries\(\s*(?:\$\{PROJECT_NAME\}|\w+)\s+'
        r'(?:PRIVATE|PUBLIC|INTERFACE))(.*?)(\))',
        re.DOTALL,
    )
    match = tll.search(content)
    if match:
        return tll.sub(rf'\g<1>\g<2> {target}\g<3>', content, count=1)

    ae = re.compile(r'(add_(?:executable|library)\([^\)]+\))', re.DOTALL)
    if ae.search(content):
        return ae.sub(
            rf'\1\n\ntarget_link_libraries(${{PROJECT_NAME}} PRIVATE {target})',
            content, count=1,
        )

    return content + f"\ntarget_link_libraries(${{PROJECT_NAME}} PRIVATE {target})\n"