"""
grimx.cmake_patch
Automatically patch CMakeLists.txt after a package is installed.

Two patching strategies:

  Primary — vcpkg output hints (used after grimx install):
    vcpkg prints CMake hints for every installed package after each install
    run. grimx parses all of them from stdout in one pass. Fast, no
    filesystem access, works for every package vcpkg prints hints for.

  Fallback — filesystem layers (used for grimx install restore from lock):
    Layer 1: vcpkg usage file   — fast, no subprocess, covers most ports
    Layer 2: CMake probe        — authoritative for everything else;
                                  runs find_package() and reports IMPORTED
                                  targets directly from CMake's resolver
    Layer 3: pkg-config         — last resort for Conan/system libs
"""

from __future__ import annotations

import re
import subprocess
import tempfile
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


# ---------------------------------------------------------------------------
# Primary path — parse vcpkg install output
# ---------------------------------------------------------------------------

def parse_vcpkg_output_hints(output: str) -> dict[str, UsageDirectives]:
    """
    Parse ALL CMake hints from vcpkg install stdout in one pass.
    Returns {package_name: UsageDirectives} for every package mentioned.

    vcpkg prints hints for every package in the manifest after each install.
    This gives the complete picture in one shot — no filesystem access needed.

    Uses section splitting + balanced-paren call extraction so formatting
    variations and indentation don't affect correctness.
    """
    results: dict[str, UsageDirectives] = {}

    section_re = re.compile(
        r'(?:The package\s+)?(\S+)\s+provides CMake(?:\s+targets)?:',
        re.IGNORECASE,
    )

    sections = section_re.split(output)
    # sections = [preamble, pkg1, pkg1_content, pkg2, pkg2_content, ...]

    i = 1
    while i < len(sections) - 1:
        package = sections[i].strip().lower()
        content  = sections[i + 1]
        i += 2

        # Stop at next non-cmake block ("provides pkg-config modules:" etc.)
        content = re.split(r'\n\S.*provides\s', content)[0]

        d = UsageDirectives()
        sub_blocks = re.split(r'\n\s*\n', content.strip())

        for block in sub_blocks:
            lines = block.strip().splitlines()
            if not lines:
                continue

            # Alternative block = any comment line containing the word "or"
            is_alternative = any(
                l.strip().startswith("#") and
                re.search(r'\bor\b', l, re.IGNORECASE)
                for l in lines
            )

            for call in _extract_cmake_calls(block):
                fname = call.split('(')[0].strip().lower()

                if fname == 'find_package':
                    if call not in d.find_package:
                        d.find_package.append(call)

                elif fname == 'target_link_libraries' and not is_alternative:
                    m = re.search(
                        r'(?:PRIVATE|PUBLIC|INTERFACE)\s+(.*?)\s*\)$',
                        call, re.DOTALL,
                    )
                    if m:
                        d.link_targets.extend(m.group(1).split())

                elif fname == 'find_path':
                    if call not in d.find_path_lines:
                        d.find_path_lines.append(call)

        if d.find_package or d.find_path_lines:
            results[package] = d

    return results


def patch_from_vcpkg_output(output: str, cmake_path: Path) -> None:
    """
    Parse vcpkg install output and patch CMakeLists.txt for all packages.
    Called after every successful grimx install.
    """
    if not cmake_path.exists():
        return

    hints = parse_vcpkg_output_hints(output)
    if not hints:
        click.echo("  [cmake] no CMake hints found in vcpkg output.")
        return

    content    = cmake_path.read_text()
    normalised = _normalise(content)
    any_changed = False

    for package, directives in hints.items():
        content, normalised, changed = _apply_directives(
            content, normalised, directives
        )
        if changed:
            click.echo(f"  [cmake] patched '{package}'")
            any_changed = True

    if any_changed:
        _atomic_write(cmake_path, content)
        click.echo("  [cmake] ✓ CMakeLists.txt updated.")
    else:
        click.echo("  [cmake] CMakeLists.txt already up to date.")


# ---------------------------------------------------------------------------
# Fallback path — filesystem layers (restore from lock)
# ---------------------------------------------------------------------------

def patch_all_from_lock(lock: dict, cmake_path: Path) -> None:
    """
    Patch CMakeLists.txt for every package in grimx.lock.
    Used by grimx install (restore) where there is no fresh vcpkg output.
    """
    if not cmake_path.exists():
        return

    deps = lock.get("dependencies", {})
    if not deps:
        return

    project_root = cmake_path.parent
    content      = cmake_path.read_text()
    normalised   = _normalise(content)
    any_changed  = False

    for package in deps:
        directives = _resolve_directives(package, project_root)
        if directives is None:
            continue
        content, normalised, changed = _apply_directives(
            content, normalised, directives
        )
        if changed:
            click.echo(f"  [cmake] patched '{package}'")
            any_changed = True

    if any_changed:
        _atomic_write(cmake_path, content)
        click.echo("  [cmake] ✓ CMakeLists.txt updated.")
    else:
        click.echo("  [cmake] CMakeLists.txt already up to date.")


# ---------------------------------------------------------------------------
# Apply directives
# ---------------------------------------------------------------------------

def _apply_directives(
    content: str,
    normalised: str,
    directives: UsageDirectives,
) -> tuple[str, str, bool]:
    changed = False

    for line in directives.find_package + directives.find_path_lines:
        if _normalise(line) not in normalised:
            content    = _inject_find_package(content, line)
            normalised = _normalise(content)
            changed    = True

    for target in directives.link_targets:
        if _normalise(target) not in normalised:
            content    = _inject_link_target(content, target)
            normalised = _normalise(content)
            changed    = True

    return content, normalised, changed


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------

def _atomic_write(cmake_path: Path, content: str) -> None:
    tmp = cmake_path.with_suffix(".tmp")
    tmp.write_text(content)
    tmp.replace(cmake_path)


# ---------------------------------------------------------------------------
# Filesystem layer resolution
# ---------------------------------------------------------------------------

def _resolve_directives(
    package: str, project_root: Path
) -> UsageDirectives | None:

    share_dirs = _find_package_share_dirs(package, project_root)

    # Layer 1: vcpkg usage file — fast, no subprocess
    for share_dir in share_dirs:
        usage = share_dir / "usage"
        if usage.exists():
            d = _parse_usage_file(usage.read_text())
            if d.find_package or d.find_path_lines:
                return d

    # Layer 2: CMake probe — authoritative, works for everything
    d = _query_cmake_targets(package, project_root)
    if d:
        return d

    # Layer 3: pkg-config — last resort for Conan/system libs
    return _parse_pkgconfig(package, project_root)


def _find_package_share_dirs(package: str, project_root: Path) -> list[Path]:
    vcpkg_installed = project_root / "vcpkg_installed"
    if not vcpkg_installed.exists():
        return []
    return [
        t / "share" / package
        for t in vcpkg_installed.iterdir()
        if t.is_dir() and (t / "share" / package).exists()
    ]


# ---------------------------------------------------------------------------
# Layer 1 — vcpkg usage file parser
# ---------------------------------------------------------------------------

_ALTERNATIVE_RE = re.compile(r'#\s*or\b', re.IGNORECASE)


def _parse_usage_file(content: str) -> UsageDirectives:
    """
    Parse a vcpkg usage file using block splitting + balanced-paren extraction.
    Skips alternative blocks (header-only variants etc.) marked with '# or'.
    """
    d      = UsageDirectives()
    blocks = re.split(r'\n\s*\n', content.strip())

    for block in blocks:
        lines        = block.splitlines()
        is_alternative = any(_ALTERNATIVE_RE.search(l) for l in lines)

        for call in _extract_cmake_calls(block):
            fname = call.split('(')[0].strip().lower()

            if fname == 'find_package':
                if call not in d.find_package:
                    d.find_package.append(call)

            elif fname == 'find_path':
                if call not in d.find_path_lines:
                    d.find_path_lines.append(call)

            elif fname == 'target_link_libraries' and not is_alternative:
                m = re.search(
                    r'(?:PRIVATE|PUBLIC|INTERFACE)\s+(.*?)\s*\)\s*$',
                    call, re.DOTALL,
                )
                if m:
                    d.link_targets.extend(m.group(1).split())

    return d


# ---------------------------------------------------------------------------
# Layer 2 — CMake probe (authoritative)
# ---------------------------------------------------------------------------

def _query_cmake_targets(
    package: str, project_root: Path
) -> UsageDirectives | None:
    """
    Run a minimal CMake script that calls find_package() and reports every
    IMPORTED target it created. CMake resolves *Config.cmake, *Export.cmake,
    *Targets.cmake, and built-in Find modules internally.

    This is the only approach guaranteed to work for every package regardless
    of whether it ships a usage file or how its cmake files are structured.

    Only runs when Layer 1 fails — typically adds 1-2 seconds per package
    that has no usage file.
    """
    vcpkg_installed = project_root / "vcpkg_installed"
    if not vcpkg_installed.exists():
        return None

    triplet_dirs = [t for t in vcpkg_installed.iterdir() if t.is_dir()]
    if not triplet_dirs:
        return None

    prefix = triplet_dirs[0]

    # CMake script: find the package, report every IMPORTED target created
    cmake_script = f'''\
cmake_minimum_required(VERSION 3.20)
project(grimx_probe LANGUAGES NONE)

set(CMAKE_PREFIX_PATH "{prefix}")

find_package({package} CONFIG QUIET)
if(NOT {package}_FOUND)
    string(TOUPPER "{package}" _PKG_UPPER)
    if(NOT ${{_PKG_UPPER}}_FOUND)
        find_package({package} QUIET)
    endif()
endif()

get_property(_targets DIRECTORY PROPERTY IMPORTED_TARGETS)
foreach(_t ${{_targets}})
    message("GRIMX_TARGET:${{_t}}")
endforeach()

if({package}_FOUND)
    message("GRIMX_PKG:{package}:CONFIG")
else()
    string(TOUPPER "{package}" _U)
    if(${{_U}}_FOUND)
        message("GRIMX_PKG:{package}:MODULE")
    endif()
endif()
'''

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "CMakeLists.txt").write_text(cmake_script)
        build_path = tmp_path / "build"
        build_path.mkdir()

        result = subprocess.run(
            ["cmake", str(tmp_path), f"-B{build_path}"],
            capture_output=True, text=True,
        )

        combined   = result.stdout + result.stderr
        all_targets: list[str] = []
        mode       = "CONFIG"

        for line in combined.splitlines():
            if "GRIMX_TARGET:" in line:
                t = line.split("GRIMX_TARGET:")[-1].strip()
                all_targets.append(t)
            elif "GRIMX_PKG:" in line:
                parts = line.split("GRIMX_PKG:")[-1].strip().split(":")
                if len(parts) == 2:
                    mode = parts[1]

        if not all_targets:
            return None

        # Filter to targets that plausibly belong to this package
        pkg_key = package.lower().replace("-", "").replace("_", "")
        relevant = [
            t for t in all_targets
            if pkg_key in t.lower().replace("::", "").replace("_", "").replace("-", "")
        ]

        # Fall back to all targets if filter was too aggressive
        final_targets = relevant if relevant else all_targets

        # Prefer namespaced targets (Foo::Bar)
        ns = [t for t in final_targets if "::" in t]
        final_targets = ns if ns else final_targets

        # Deduplicate preserving order
        final_targets = list(dict.fromkeys(final_targets))

        if not final_targets:
            return None

        if mode == "CONFIG":
            find_pkg = f"find_package({package} CONFIG REQUIRED)"
        else:
            find_pkg = f"find_package({package} REQUIRED)"

        return UsageDirectives(
            find_package=[find_pkg],
            link_targets=final_targets,
        )


# ---------------------------------------------------------------------------
# Layer 3 — pkg-config fallback
# ---------------------------------------------------------------------------

def _parse_pkgconfig(
    package: str, project_root: Path
) -> UsageDirectives | None:
    vcpkg_installed = project_root / "vcpkg_installed"
    if not vcpkg_installed.exists():
        return None

    pc_files = (
        list(vcpkg_installed.rglob(f"{package}.pc")) +
        list(vcpkg_installed.rglob(f"lib{package}.pc"))
    )
    if not pc_files:
        return None

    m = re.search(
        r'^Libs:(.+)$', pc_files[0].read_text(errors="replace"), re.MULTILINE
    )
    if not m:
        return None

    libs = re.findall(r'-l(\S+)', m.group(1))
    if not libs:
        return None

    return UsageDirectives(
        find_package=[f"find_library({lib.upper()}_LIB {lib})" for lib in libs],
        link_targets=[f"${{{lib.upper()}_LIB}}" for lib in libs],
    )


# ---------------------------------------------------------------------------
# Balanced-paren CMake call extractor
# ---------------------------------------------------------------------------

def _extract_cmake_calls(text: str) -> list[str]:
    """
    Extract complete CMake function calls using balanced-paren matching.
    Handles single-line, multi-line, indented, and COMPONENTS calls correctly.
    Skips commented-out lines.
    """
    calls      = []
    i          = 0
    call_start = re.compile(r'[A-Za-z_]\w*\s*\(')

    while i < len(text):
        m = call_start.search(text, i)
        if not m:
            break

        line_start = text.rfind('\n', 0, m.start()) + 1
        prefix     = text[line_start:m.start()]
        if '#' in prefix:
            i = m.end()
            continue

        depth = 0
        j     = m.start()
        while j < len(text):
            if text[j] == '(':
                depth += 1
            elif text[j] == ')':
                depth -= 1
                if depth == 0:
                    raw = text[m.start():j + 1]
                    calls.append(re.sub(r'\s+', ' ', raw).strip())
                    i = j + 1
                    break
            j += 1
        else:
            i = m.end()

    return calls


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def _normalise(s: str) -> str:
    """Collapse whitespace and lowercase for robust idempotency checks."""
    return re.sub(r'\s+', '', s).lower()


# ---------------------------------------------------------------------------
# Injection helpers
# ---------------------------------------------------------------------------

def _inject_find_package(content: str, find_pkg: str) -> str:
    """
    Inject find_package() after the last existing find_package call
    to preserve install order. Falls back to anchor strategies.
    """
    last = None
    for m in re.finditer(
        r'find_(?:package|path|library)\s*\(', content, re.IGNORECASE
    ):
        last = m

    if last:
        end = _find_call_end(content, last.start())
        if end != -1:
            return f"{content[:end]}\n\n{find_pkg}{content[end:]}"

    anchors = [
        r'set\s*\(\s*CMAKE_CXX_STANDARD_REQUIRED\s+ON\s*\)',
        r'set\s*\(\s*CMAKE_CXX_STANDARD\b[^\)]*\)',
        r'project\s*\([^\)]*\)',
        r'cmake_minimum_required\s*\([^\)]*\)',
    ]
    for pattern in anchors:
        m = re.search(pattern, content, re.IGNORECASE)
        if m:
            end = _find_call_end(content, m.start())
            pos = end if end != -1 else m.end()
            return f"{content[:pos]}\n\n{find_pkg}{content[pos:]}"

    m = re.search(r'add_(?:executable|library)\s*\(', content, re.IGNORECASE)
    if m:
        return f"{content[:m.start()]}{find_pkg}\n\n{content[m.start():]}"

    return f"{find_pkg}\n\n{content}"


def _inject_link_target(content: str, target: str) -> str:
    """
    Append target to existing target_link_libraries block,
    or create one after add_executable/add_library.
    Uses string slicing to avoid $ in target names corrupting re.sub.
    """
    tll = re.compile(
        r'(target_link_libraries\(\s*(?:\$\{PROJECT_NAME\}|\w+)\s+'
        r'(?:PRIVATE|PUBLIC|INTERFACE))(.*?)(\))',
        re.DOTALL,
    )
    m = tll.search(content)
    if m:
        replacement = m.group(1) + m.group(2) + f" {target}" + m.group(3)
        return content[:m.start()] + replacement + content[m.end():]

    ae = re.compile(r'(add_(?:executable|library)\([^\)]+\))', re.DOTALL)
    if ae.search(content):
        return ae.sub(
            rf'\1\n\ntarget_link_libraries(${{PROJECT_NAME}} PRIVATE {target})',
            content, count=1,
        )

    return f"{content}\ntarget_link_libraries(${{PROJECT_NAME}} PRIVATE {target})\n"


def _find_call_end(text: str, start: int) -> int:
    """Return index just after the matching closing paren. -1 if unmatched."""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == '(':
            depth += 1
        elif text[i] == ')':
            depth -= 1
            if depth == 0:
                return i + 1
    return -1