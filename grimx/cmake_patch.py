"""
grimx.cmake_patch
Automatically patch CMakeLists.txt after a package is installed.

Primary path — vcpkg output hints (used after grimx install):
  Parses CMake hints from vcpkg stdout in one pass. Fast, no filesystem access.

Fallback path — filesystem layers (used for grimx install restore):
  Layer 1: vcpkg usage file
  Layer 2: CMake probe via find_package()
  Layer 3: pkg-config
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import click


@dataclass
class UsageDirectives:
    find_package:    list[str] = field(default_factory=list)
    link_targets:    list[str] = field(default_factory=list)
    find_path_lines: list[str] = field(default_factory=list)


def parse_vcpkg_output_hints(output: str) -> dict[str, UsageDirectives]:
    """Parse all CMake hints from vcpkg install stdout in one pass."""
    results: dict[str, UsageDirectives] = {}

    # Match all known vcpkg phrasing variants:
    #   "The package fmt provides CMake targets:"
    #   "fmt provides CMake targets:"
    #   "The package zlib is compatible with built-in CMake targets:"
    #   "zlib is compatible with built-in CMake targets:"
    section_re = re.compile(
        r'(?:The package\s+)?(\S+)\s+(?:provides|is compatible with built-in)\s+CMake(?:\s+targets)?:',
        re.IGNORECASE,
    )

    sections = section_re.split(output)

    i = 1
    while i < len(sections) - 1:
        package = sections[i].strip().lower()
        content  = sections[i + 1]
        i += 2

        content = re.split(r'\n\S.*provides\s', content)[0]

        d = UsageDirectives()
        sub_blocks = re.split(r'\n\s*\n|(?=\n\s*#\s*[Oo]r\b)', content.strip())

        for block in sub_blocks:
            lines = block.strip().splitlines()
            if not lines:
                continue

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


def patch_from_vcpkg_output(
    output: str,
    cmake_path: Path,
    include: set[str] | None = None,
    exclude: set[str] | None = None,
) -> None:
    """
    Parse vcpkg install output and patch CMakeLists.txt for all packages.
    Called after every successful grimx install.

    include: if set, only patch packages in this set
    exclude: if set, skip packages in this set
    """
    if not cmake_path.exists():
        return

    hints = parse_vcpkg_output_hints(output)
    if not hints:
        click.echo("  [cmake] no CMake hints found in vcpkg output.")
        return

    content     = cmake_path.read_text()
    normalised  = _normalise(content)
    any_changed = False

    for package, directives in hints.items():
        if include is not None and package not in include:
            continue
        if exclude is not None and package in exclude:
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


def patch_all_from_lock(lock: dict, cmake_path: Path) -> None:
    """Patch CMakeLists.txt for every package in grimx.lock."""
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


def sync_sources(cmake_path: Path) -> None:
    """Sync CMakeLists.txt with source files found in src/ and include/."""
    if not cmake_path.exists():
        click.echo("  [sync] CMakeLists.txt not found.", err=True)
        return

    project_root = cmake_path.parent
    src_dir      = project_root / "src"
    include_dir  = project_root / "include"

    src_extensions = {".c", ".cpp", ".cc", ".cxx"}
    discovered: list[str] = []
    if src_dir.exists():
        discovered = sorted(
            f.relative_to(project_root).as_posix()
            for f in src_dir.rglob("*")
            if f.is_file() and f.suffix in src_extensions
        )

    content     = cmake_path.read_text()
    any_changed = False

    # Projects scaffolded by `grimx new` compile every source through a
    # file(GLOB_RECURSE ... CONFIGURE_DEPENDS) driven target. Listing those same
    # files in add_executable() compiles them twice and the link fails with
    # "multiple definition of `main'". Never add sources to such projects — and
    # strip any that an earlier sync already added.
    if _sources_are_globbed(content):
        content, changed = _prune_duplicate_sources(content, discovered)
        if changed:
            click.echo("  [sync] removed duplicate source(s) from add_executable "
                       "— already compiled via file(GLOB_RECURSE ...)")
            any_changed = True
        else:
            click.echo("  [sync] sources auto-discovered via file(GLOB_RECURSE ...) — nothing to add.")
    elif discovered:
        content, changed = _sync_add_executable(content, discovered)
        if changed:
            click.echo(f"  [sync] updated add_executable with {len(discovered)} source(s)")
            any_changed = True

    if include_dir.exists() and not _includes_are_globbed(content):
        content, changed = _sync_include_directories(content)
        if changed:
            click.echo("  [sync] added target_include_directories PRIVATE include")
            any_changed = True

    if not any_changed:
        click.echo("  [sync] CMakeLists.txt already up to date.")
        return

    content = re.sub(r'\n{3,}', '\n\n', content)
    _atomic_write(cmake_path, content)
    click.echo("  [sync] ✓ CMakeLists.txt updated.")


def _sync_add_executable(content: str, discovered: list[str]) -> tuple[str, bool]:
    ae_re = re.compile(r'add_executable\s*\(', re.IGNORECASE)
    m     = ae_re.search(content)
    if not m:
        return content, False

    end = _find_call_end(content, m.start())
    if end == -1:
        return content, False

    call_text        = content[m.start():end]
    inner            = call_text[call_text.index('(') + 1:-1].strip()
    tokens           = inner.split()
    if not tokens:
        return content, False

    existing_sources = set(tokens[1:])
    norm_existing    = {_normalise(s) for s in existing_sources}
    missing          = [s for s in discovered if _normalise(s) not in norm_existing]

    if not missing:
        return content, False

    new_call = call_text[:-1] + "\n    " + "\n    ".join(missing) + "\n)"
    return content[:m.start()] + new_call + content[end:], True


def _sync_include_directories(content: str) -> tuple[str, bool]:
    norm = _normalise(content)

    if 'target_include_directories' in norm and _normalise('include') in norm:
        tid_re = re.compile(r'target_include_directories\s*\(.*?\)', re.DOTALL | re.IGNORECASE)
        for m in tid_re.finditer(content):
            if any(t.strip('()') == 'include' for t in m.group().split()):
                return content, False

    new_line = "target_include_directories(${PROJECT_NAME} PRIVATE include)"

    ae_re = re.compile(r'add_executable\s*\(', re.IGNORECASE)
    m     = ae_re.search(content)
    if m:
        end = _find_call_end(content, m.start())
        if end != -1:
            return content[:end] + "\n\n" + new_line + content[end:], True

    return content.rstrip() + "\n\n" + new_line + "\n", True


_SRC_GLOB_EXT_RE = re.compile(r'\*\.(?:c|cc|cpp|cxx)\b', re.IGNORECASE)
_HDR_GLOB_EXT_RE = re.compile(r'\*\.(?:h|hh|hpp|hxx)\b', re.IGNORECASE)


def _glob_vars(content: str, ext_re: re.Pattern) -> set[str]:
    """Names of variables populated by file(GLOB...) over files matching ext_re."""
    found: set[str] = set()
    for m in re.finditer(r'file\s*\(\s*GLOB(?:_RECURSE)?\s+(\w+)', content, re.IGNORECASE):
        end = _find_call_end(content, m.start())
        if end != -1 and ext_re.search(content[m.start():end]):
            found.add(m.group(1))
    return found


def _sources_are_globbed(content: str) -> bool:
    """True when a target already compiles a file(GLOB...) source list."""
    src_vars = _glob_vars(content, _SRC_GLOB_EXT_RE)
    if not src_vars:
        return False

    for m in re.finditer(r'add_(?:library|executable)\s*\(', content, re.IGNORECASE):
        end = _find_call_end(content, m.start())
        if end == -1:
            continue
        call = content[m.start():end]
        if any(f'${{{v}}}' in call for v in src_vars):
            return True

    return False


def _includes_are_globbed(content: str) -> bool:
    """True when include dirs are derived from a globbed header list."""
    if not _glob_vars(content, _HDR_GLOB_EXT_RE):
        return False

    for m in re.finditer(r'target_include_directories\s*\(', content, re.IGNORECASE):
        end = _find_call_end(content, m.start())
        if end == -1:
            continue
        call = content[m.start():end]
        vis  = re.search(r'\b(?:PRIVATE|PUBLIC|INTERFACE)\b', call)
        if vis and re.search(r'\$\{\w+\}', call[vis.end():]):
            return True

    return False


def _glob_exclude_patterns(content: str, var: str) -> list[str]:
    """Regexes applied to a globbed list via list(FILTER <var> EXCLUDE REGEX ...)."""
    call_re = re.compile(
        r'list\s*\(\s*FILTER\s+' + re.escape(var) + r'\s+EXCLUDE\s+REGEX\s+(.+?)\s*\)',
        re.IGNORECASE | re.DOTALL,
    )

    patterns: list[str] = []
    for m in call_re.finditer(content):
        arg = m.group(1).strip()
        if len(arg) >= 2 and arg[0] == '"' and arg[-1] == '"':
            arg = arg[1:-1]
        # CMake quoted arguments escape a literal backslash as '\\'.
        patterns.append(arg.replace('\\\\', '\\'))

    return patterns


def _globbed_source_files(content: str, discovered: list[str]) -> set[str]:
    """
    Subset of `discovered` that the CMake source globs actually collect, i.e.
    after the list(FILTER ... EXCLUDE REGEX ...) calls are applied. A file the
    glob deliberately drops — typically the app entry point — still belongs in
    add_executable() and must never be pruned from it.
    """
    src_vars = _glob_vars(content, _SRC_GLOB_EXT_RE)
    if not src_vars:
        return set()

    excludes: list[str] = []
    for var in src_vars:
        excludes.extend(_glob_exclude_patterns(content, var))

    collected: set[str] = set()
    for rel in discovered:
        # GLOB_RECURSE yields absolute paths; the leading slash lets patterns
        # anchored like ".*/src/main\.cpp$" match a repo-relative path.
        probe = "/" + rel
        for pattern in excludes:
            try:
                if re.search(pattern, probe):
                    break
            except re.error:
                continue
        else:
            collected.add(rel)

    return collected


def _prune_duplicate_sources(content: str, discovered: list[str]) -> tuple[str, bool]:
    """
    Drop explicitly listed sources from add_executable() when the same files are
    already compiled through a globbed target — they would otherwise be compiled
    twice and break the link with duplicate symbols.
    """
    globbed = _globbed_source_files(content, discovered)
    if not globbed:
        return content, False

    dupes       = {_normalise(s) for s in globbed}
    ae_re       = re.compile(r'add_executable\s*\(', re.IGNORECASE)
    any_changed = False
    pos         = 0

    while True:
        m = ae_re.search(content, pos)
        if not m:
            break

        end = _find_call_end(content, m.start())
        if end == -1:
            pos = m.end()
            continue

        call   = content[m.start():end]
        tokens = call[call.index('(') + 1:-1].split()

        if len(tokens) > 1:
            kept = [tokens[0]] + [t for t in tokens[1:] if _normalise(t) not in dupes]
            if len(kept) != len(tokens):
                new_call    = f"add_executable({' '.join(kept)})"
                content     = content[:m.start()] + new_call + content[end:]
                any_changed = True
                pos         = m.start() + len(new_call)
                continue

        pos = end

    return content, any_changed


def unpatch_package(
    package: str,
    cmake_path: Path,
    directives: UsageDirectives | None = None,
) -> None:
    """
    Remove CMake directives for a package from CMakeLists.txt.
    Accepts pre-resolved directives to avoid dependency on vcpkg_installed/
    being intact at call time.
    """
    if not cmake_path.exists():
        return

    if directives is None:
        directives = _resolve_directives(package, cmake_path.parent)

    if directives is None:
        click.echo(f"  [cmake] no directives found for '{package}' — nothing to remove.")
        return

    content     = cmake_path.read_text()
    any_changed = False

    for line in directives.find_package + directives.find_path_lines:
        norm_line = _normalise(line)
        while norm_line in _normalise(content):
            content, removed = _remove_cmake_call(content, line)
            if removed:
                any_changed = True

    for target in directives.link_targets:
        content, changed = _remove_link_target(content, target)
        if changed:
            any_changed = True

    if not any_changed:
        click.echo(f"  [cmake] '{package}' directives not found in CMakeLists.txt — nothing to remove.")
        return

    content = re.sub(r'\n{3,}', '\n\n', content)
    _atomic_write(cmake_path, content)
    click.echo(f"  [cmake] ✓ removed '{package}' directives from CMakeLists.txt.")


def _remove_cmake_call(content: str, call: str) -> tuple[str, bool]:
    norm_target = _normalise(call)
    call_re     = re.compile(r'[A-Za-z_]\w*\s*\(')
    i = 0

    while i < len(content):
        m = call_re.search(content, i)
        if not m:
            break

        line_start = content.rfind('\n', 0, m.start()) + 1
        if '#' in content[line_start:m.start()]:
            i = m.end()
            continue

        end = _find_call_end(content, m.start())
        if end == -1:
            i = m.end()
            continue

        if _normalise(content[m.start():end]) == norm_target:
            remove_start = m.start()
            if remove_start >= 2 and content[remove_start - 2:remove_start] == '\n\n':
                remove_start -= 1
            return content[:remove_start] + content[end:], True

        i = end

    return content, False


def _remove_link_target(content: str, target: str) -> tuple[str, bool]:
    """Remove a target token from every TLL call. Uses string slicing — safe for ${VAR} targets."""
    tll_re = re.compile(
        r'target_link_libraries\s*\(\s*'
        r'(?:\$\{[^}]+\}|\w+)\s+'
        r'(PRIVATE|PUBLIC|INTERFACE)\s+'
        r'(.*?)\s*\)',
        re.DOTALL,
    )

    changed = False
    result  = content
    offset  = 0

    for m in tll_re.finditer(content):
        visibility = m.group(1)
        tokens     = m.group(2).split()

        if target not in tokens:
            continue

        tokens.remove(target)
        changed = True

        if tokens:
            new_targets = ' '.join(tokens)
            vis_start   = m.start(1) + offset
            close_pos   = m.end() - 1 + offset
            result      = result[:vis_start] + visibility + ' ' + new_targets + result[close_pos:]
            offset     += len(visibility + ' ' + new_targets) - (close_pos - vis_start)
        else:
            abs_start = m.start() + offset
            abs_end   = m.end() + offset
            if abs_start >= 2 and result[abs_start - 2:abs_start] == '\n\n':
                abs_start -= 1
            result  = result[:abs_start] + result[abs_end:]
            offset -= (abs_end - abs_start)

    return result, changed


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


def _atomic_write(cmake_path: Path, content: str) -> None:
    tmp = cmake_path.with_suffix(".tmp")
    tmp.write_text(content)
    tmp.replace(cmake_path)


def _resolve_directives(
    package: str, project_root: Path
) -> UsageDirectives | None:
    for share_dir in _find_package_share_dirs(package, project_root):
        usage = share_dir / "usage"
        if usage.exists():
            d = _parse_usage_file(usage.read_text())
            if d.find_package or d.find_path_lines:
                return d

    d = _query_cmake_targets(package, project_root)
    if d:
        return d

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


_ALTERNATIVE_RE = re.compile(r'#\s*or\b', re.IGNORECASE)


def _parse_usage_file(content: str) -> UsageDirectives:
    d      = UsageDirectives()
    blocks = re.split(r'\n\s*\n|(?=\n\s*#\s*[Oo]r\b)', content.strip())

    for block in blocks:
        lines          = block.splitlines()
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


def _query_cmake_targets(
    package: str, project_root: Path
) -> UsageDirectives | None:
    vcpkg_installed = project_root / "vcpkg_installed"
    if not vcpkg_installed.exists():
        return None

    triplet_dirs = [t for t in vcpkg_installed.iterdir() if t.is_dir()]
    if not triplet_dirs:
        return None

    prefix       = triplet_dirs[0]
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
        tmp_path   = Path(tmp)
        build_path = tmp_path / "build"
        (tmp_path / "CMakeLists.txt").write_text(cmake_script)
        build_path.mkdir()

        result = subprocess.run(
            ["cmake", str(tmp_path), f"-B{build_path}"],
            capture_output=True, text=True,
        )

        combined    = result.stdout + result.stderr
        all_targets: list[str] = []
        mode        = "CONFIG"

        for line in combined.splitlines():
            if "GRIMX_TARGET:" in line:
                all_targets.append(line.split("GRIMX_TARGET:")[-1].strip())
            elif "GRIMX_PKG:" in line:
                parts = line.split("GRIMX_PKG:")[-1].strip().split(":")
                if len(parts) == 2:
                    mode = parts[1]

        if not all_targets:
            return None

        pkg_key       = package.lower().replace("-", "").replace("_", "")
        relevant      = [
            t for t in all_targets
            if pkg_key in t.lower().replace("::", "").replace("_", "").replace("-", "")
        ]
        final_targets = relevant if relevant else all_targets
        ns            = [t for t in final_targets if "::" in t]
        final_targets = list(dict.fromkeys(ns if ns else final_targets))

        if not final_targets:
            return None

        find_pkg = (
            f"find_package({package} CONFIG REQUIRED)"
            if mode == "CONFIG"
            else f"find_package({package} REQUIRED)"
        )

        return UsageDirectives(find_package=[find_pkg], link_targets=final_targets)


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


def _extract_cmake_calls(text: str) -> list[str]:
    calls      = []
    i          = 0
    call_start = re.compile(r'[A-Za-z_]\w*\s*\(')

    while i < len(text):
        m = call_start.search(text, i)
        if not m:
            break

        line_start = text.rfind('\n', 0, m.start()) + 1
        if '#' in text[line_start:m.start()]:
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


def _normalise(s: str) -> str:
    return re.sub(r'\s+', '', s).lower()


def _inject_find_package(content: str, find_pkg: str) -> str:
    last = None
    for m in re.finditer(
        r'find_(?:package|path|library)\s*\(', content, re.IGNORECASE
    ):
        last = m

    if last:
        end = _find_call_end(content, last.start())
        if end != -1:
            return f"{content[:end]}\n\n{find_pkg}{content[end:]}"

    prefix_m = re.search(
        r'list\s*\(\s*APPEND\s+CMAKE_PREFIX_PATH\b[^\)]*\)',
        content, re.IGNORECASE,
    )
    if prefix_m:
        end = _find_call_end(content, prefix_m.start())
        pos = end if end != -1 else prefix_m.end()
        return f"{content[:pos]}\n\n{find_pkg}{content[pos:]}"

    anchors = [
        r'set\s*\(\s*CMAKE_CXX_STANDARD_REQUIRED\s+ON\s*\)',
        r'set\s*\(\s*CMAKE_C_STANDARD_REQUIRED\s+ON\s*\)',
        r'set\s*\(\s*CMAKE_CXX_STANDARD\b[^\)]*\)',
        r'set\s*\(\s*CMAKE_C_STANDARD\b[^\)]*\)',
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
    core_tll = re.compile(
        r'(target_link_libraries\(\s*(?:\$\{PROJECT_NAME\}|\w+)_core\s+'
        r'(?:PRIVATE|PUBLIC|INTERFACE))(.*?)(\))',
        re.DOTALL,
    )
    m = core_tll.search(content)
    if m:
        replacement = m.group(1) + m.group(2) + f" {target}" + m.group(3)
        return content[:m.start()] + replacement + content[m.end():]

    core_lib = re.compile(
        r'(add_library\(\s*(?:\$\{PROJECT_NAME\}|\w+)_core\b[^\)]*\))',
        re.DOTALL,
    )
    m = core_lib.search(content)
    if m:
        end = _find_call_end(content, m.start())
        pos = end if end != -1 else m.end()
        return content[:pos] + f"\ntarget_link_libraries(${{PROJECT_NAME}}_core PUBLIC {target})" + content[pos:]

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
            rf'\1\n\ntarget_link_libraries(${{PROJECT_NAME}}_core PUBLIC {target})',
            content, count=1,
        )

    return f"{content}\ntarget_link_libraries(${{PROJECT_NAME}}_core PUBLIC {target})\n"


def _find_call_end(text: str, start: int) -> int:
    depth = 0
    for i in range(start, len(text)):
        if text[i] == '(':
            depth += 1
        elif text[i] == ')':
            depth -= 1
            if depth == 0:
                return i + 1
    return -1
