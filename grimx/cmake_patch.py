"""
grimx.cmake_patch
Automatically patch CMakeLists.txt after a package is installed.

Assumes manifest mode: vcpkg_installed/ lives in the project root.
No VCPKG_ROOT dependency.

Three-layer strategy:
  Layer 1: vcpkg usage file         — authoritative, covers 2000+ ports
  Layer 2: CMake *Config.cmake scan — covers everything with CMake support
  Layer 3: pkg-config .pc scan      — covers Conan and system-style libs
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


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def patch_all_from_lock(lock: dict, cmake_path: Path) -> None:
    """
    Patch CMakeLists.txt for every package in grimx.lock.
    Called after every successful install.
    """
    if not cmake_path.exists():
        return

    deps = lock.get("dependencies", {})
    if not deps:
        return

    project_root = cmake_path.parent
    content = cmake_path.read_text()

    # Pre-normalise once — O(1) per subsequent presence check
    normalised = _normalise(content)

    any_changed = False

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
    """
    Apply directives to content. Returns (new_content, new_normalised, changed).
    Keeps normalised in sync so subsequent checks in the same pass stay O(1).
    """
    changed = False

    for line in directives.find_package + directives.find_path_lines:
        if _normalise(line) not in normalised:
            content = _inject_find_package(content, line)
            normalised = _normalise(content)
            changed = True

    for target in directives.link_targets:
        if _normalise(target) not in normalised:
            content = _inject_link_target(content, target)
            normalised = _normalise(content)
            changed = True

    return content, normalised, changed


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
) -> UsageDirectives | None:
    """Single share-dir scan, three layers."""
    share_dirs = _find_package_share_dirs(package, project_root)

    # Layer 1: vcpkg usage file
    for share_dir in share_dirs:
        usage = share_dir / "usage"
        if usage.exists():
            d = _parse_usage_file(usage.read_text())
            if d.find_package or d.find_path_lines:
                return d

    # Layer 2: CMake *Config.cmake scan
    for share_dir in share_dirs:
        d = _parse_cmake_config(share_dir)
        if d:
            return d

    # Layer 3: pkg-config .pc fallback
    return _parse_pkgconfig(package, project_root)


# ---------------------------------------------------------------------------
# Share directory discovery
# ---------------------------------------------------------------------------

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

# Matches the alternative-block marker vcpkg uses in every usage file
_ALTERNATIVE_RE = re.compile(r'#\s*or\b', re.IGNORECASE)


def _parse_usage_file(content: str) -> UsageDirectives:
    """
    Parse a vcpkg usage file using explicit signal detection.

    vcpkg usage files have a consistent structure:
      - Primary block: find_package + target_link_libraries (no preceding comment)
      - Alternative blocks: preceded by a comment containing "or"
        e.g. "# Or use the header-only version"

    Strategy:
      1. Split into blocks on blank lines.
      2. Mark blocks as alternative if any line matches _ALTERNATIVE_RE.
      3. Collect find_package from ALL blocks (deduplicated) — the same
         find_package call is required regardless of which target is used.
      4. Collect link targets ONLY from non-alternative blocks.

    CMake function calls are parsed with balanced-paren matching so
    multi-line calls and calls with COMPONENTS work correctly.
    """
    d = UsageDirectives()
    blocks = re.split(r'\n\s*\n', content.strip())

    for block in blocks:
        lines = block.splitlines()
        is_alternative = any(_ALTERNATIVE_RE.search(l) for l in lines)

        for call in _extract_cmake_calls(block):
            name = call.split('(')[0].strip().lower()

            if name == 'find_package':
                if call not in d.find_package:
                    d.find_package.append(call)

            elif name == 'find_path':
                if call not in d.find_path_lines:
                    d.find_path_lines.append(call)

            elif name == 'target_link_libraries' and not is_alternative:
                # Extract targets after PRIVATE/PUBLIC/INTERFACE keyword
                m = re.search(
                    r'(?:PRIVATE|PUBLIC|INTERFACE)\s+(.*?)\s*\)\s*$',
                    call,
                    re.DOTALL,
                )
                if m:
                    d.link_targets.extend(m.group(1).split())

    return d


def _extract_cmake_calls(text: str) -> list[str]:
    """
    Extract complete CMake function calls using balanced-paren matching.

    Handles:
      - Single-line: find_package(fmt CONFIG REQUIRED)
      - Multi-line:  find_package(Boost REQUIRED
                         COMPONENTS filesystem system)
      - Indented calls in usage files
    """
    calls = []
    i = 0
    text = text.strip()

    # Match the start of a CMake call: word chars followed by '('
    call_start = re.compile(r'[A-Za-z_]\w*\s*\(')

    while i < len(text):
        m = call_start.search(text, i)
        if not m:
            break

        # Skip if preceded by '#' on the same line (commented out)
        line_start = text.rfind('\n', 0, m.start()) + 1
        prefix = text[line_start:m.start()]
        if '#' in prefix:
            i = m.end()
            continue

        # Walk forward counting parens to find the matching close
        depth = 0
        j = m.start()
        while j < len(text):
            if text[j] == '(':
                depth += 1
            elif text[j] == ')':
                depth -= 1
                if depth == 0:
                    # Normalise internal whitespace for clean output
                    raw = text[m.start():j + 1]
                    normalised = re.sub(r'\s+', ' ', raw).strip()
                    calls.append(normalised)
                    i = j + 1
                    break
            j += 1
        else:
            # Unmatched paren — skip
            i = m.end()

    return calls


# ---------------------------------------------------------------------------
# Layer 2 — CMake Config file scan
# ---------------------------------------------------------------------------

def _parse_cmake_config(share_pkg_dir: Path) -> UsageDirectives | None:
    config_files = (
        list(share_pkg_dir.glob("*Config.cmake")) +
        list(share_pkg_dir.glob("*-config.cmake"))
    )
    if not config_files:
        return None

    content = config_files[0].read_text(errors="replace")
    pkg_name = re.sub(r'(?i)(Config|-config)$', '', config_files[0].stem)

    # Extract namespaced IMPORTED targets — most reliable indicator
    targets = list(dict.fromkeys(re.findall(
        r'add_library\(([A-Za-z0-9_:]+)\s+(?:STATIC|SHARED|INTERFACE)\s+IMPORTED\)',
        content,
    )))
    ns_targets = [t for t in targets if "::" in t]

    if not ns_targets:
        return None

    return UsageDirectives(
        find_package=[f"find_package({pkg_name} CONFIG REQUIRED)"],
        link_targets=ns_targets,
    )


# ---------------------------------------------------------------------------
# Layer 3 — pkg-config fallback
# ---------------------------------------------------------------------------

def _parse_pkgconfig(package: str, project_root: Path) -> UsageDirectives | None:
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
# Normalisation
# ---------------------------------------------------------------------------

def _normalise(s: str) -> str:
    """Collapse all whitespace and lowercase for robust presence checks."""
    return re.sub(r'\s+', '', s).lower()


# ---------------------------------------------------------------------------
# Injection helpers
# ---------------------------------------------------------------------------

def _inject_find_package(content: str, find_pkg: str) -> str:
    """
    Inject find_package() after the last existing find_package call
    to preserve install order. Falls back to anchor strategies when
    no find_package exists yet.
    """
    last = None
    for m in re.finditer(r'find_(?:package|path|library)\s*\(', content, re.IGNORECASE):
        last = m

    if last:
        # Walk to end of this call using balanced parens
        end = _find_call_end(content, last.start())
        if end != -1:
            return f"{content[:end]}\n\n{find_pkg}{content[end:]}"

    # No existing find calls — anchor strategies
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
    """
    tll = re.compile(
        r'(target_link_libraries\(\s*(?:\$\{PROJECT_NAME\}|\w+)\s+'
        r'(?:PRIVATE|PUBLIC|INTERFACE))(.*?)(\))',
        re.DOTALL,
    )
    m = tll.search(content)
    if m:
        return tll.sub(rf'\g<1>\g<2> {target}\g<3>', content, count=1)

    ae = re.compile(r'(add_(?:executable|library)\([^\)]+\))', re.DOTALL)
    if ae.search(content):
        return ae.sub(
            rf'\1\n\ntarget_link_libraries(${{PROJECT_NAME}} PRIVATE {target})',
            content, count=1,
        )

    return f"{content}\ntarget_link_libraries(${{PROJECT_NAME}} PRIVATE {target})\n"


def _find_call_end(text: str, start: int) -> int:
    """
    Given the start index of a CMake call, return the index just after
    the matching closing paren. Returns -1 if unmatched.
    """
    depth = 0
    for i in range(start, len(text)):
        if text[i] == '(':
            depth += 1
        elif text[i] == ')':
            depth -= 1
            if depth == 0:
                return i + 1
    return -1