"""
grimx.scaffold
Interactive project creation — create-next-app style.
"""

from __future__ import annotations

import shutil
from importlib import resources
from pathlib import Path

import click

from grimx.config import write_config, write_lock, add_dev_dependency, DEFAULT_CONFIG

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

CPP_TEST_FRAMEWORKS = ["catch2", "doctest", "googletest", "none"]
C_TEST_FRAMEWORKS   = ["cmocka", "none"]

# A framework's menu name is not always its vcpkg port name — googletest ships
# as the 'gtest' port. Anything absent here uses its own name as the port.
TEST_FRAMEWORK_PORTS = {
    "googletest": "gtest",
}


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

    # ── Test framework ────────────────────────────────────────────────────
    test_frameworks = CPP_TEST_FRAMEWORKS if is_cpp else C_TEST_FRAMEWORKS
    default_fw      = test_frameworks[0]

    click.echo("")
    click.echo("  Test framework?")
    for i, fw in enumerate(test_frameworks, 1):
        marker = " (default)" if fw == default_fw else ""
        label  = fw if fw != "none" else "none (configure manually)"
        click.echo(f"    {i}. {label}{marker}")
    fw_choice = click.prompt("  Choice", default="1", show_default=False)
    try:
        test_framework = test_frameworks[int(fw_choice) - 1]
    except (ValueError, IndexError):
        test_framework = default_fw

    # ── Summary ───────────────────────────────────────────────────────────
    click.echo("")
    click.echo(f"  Creating project '{name}'")
    click.echo(f"    type      : {project_type}")
    click.echo(f"    standard  : {lang}{std}")
    click.echo(f"    managers  : {', '.join(priority) if priority else 'none'}")
    click.echo(f"    tests     : {test_framework}")
    click.echo("")

    # ── Scaffold ──────────────────────────────────────────────────────────
    template_src = _get_template_path(TEMPLATE_MAP[project_type])
    shutil.copytree(template_src, dest)

    for gitkeep in dest.rglob(".gitkeep"):
        gitkeep.unlink()

    _patch_cmakelists(dest, name, project_type, std)
    _write_tests_cmake(dest, name, project_type, test_framework)
    _write_starter_test(dest, project_type, test_framework)
    _write_readme(dest, name, project_type, std)
    _write_gitignore(dest)
    _write_clang_format(dest)

    for d in ["include", "docs", "cmake"]:
        (dest / d).mkdir(exist_ok=True)

    write_config({"package_manager": {"priority": priority}}, root=dest)
    write_lock({"dependencies": {}, "dev_dependencies": {}}, root=dest)

    test_port = TEST_FRAMEWORK_PORTS.get(test_framework, test_framework)

    if test_framework != "none" and priority:
        add_dev_dependency(test_port, "vcpkg", "unknown", root=dest)

    click.echo(f"  ✓ {dest}")
    click.echo("")

    if test_framework != "none" and priority:
        click.echo(f"  The following will be installed:")
        click.echo(f"    {test_port} (dev dependency, via vcpkg)")
        click.echo("")
        if click.confirm("  Install now?", default=True):
            import os
            orig = os.getcwd()
            os.chdir(dest)
            try:
                from grimx import install as install_mod
                install_mod.run(None)
            finally:
                os.chdir(orig)
            click.echo("")

    click.echo("  Next steps:")
    click.echo(f"    cd {name}")
    click.echo(f"    grimx build")
    click.echo(f"    grimx run")
    click.echo(f"    grimx test")
    click.echo("")


# ---------------------------------------------------------------------------
# Patching and file generation
# ---------------------------------------------------------------------------

def _write_tests_cmake(dest: Path, name: str, project_type: str, framework: str) -> None:
    tests_cmake = dest / "tests" / "CMakeLists.txt"
    tests_cmake.parent.mkdir(exist_ok=True)
    is_cpp = "cpp" in project_type

    if framework == "none":
        tests_cmake.write_text(
            "# Add test executables here.\n"
            "# Example:\n"
            "#   find_package(Catch2 CONFIG REQUIRED)\n"
            "#   add_executable(my_tests test_main.cpp)\n"
            "#   target_link_libraries(my_tests PRIVATE\n"
            f"#       {name}_core\n"
            "#       Catch2::Catch2WithMain\n"
            "#   )\n"
            "#   add_test(NAME my_tests COMMAND my_tests)\n"
        )
        return

    glob_ext = "*.cpp" if is_cpp else "*.c"

    if framework == "catch2":
        tests_cmake.write_text(
            f"find_package(Catch2 CONFIG REQUIRED)\n\n"
            f"file(GLOB_RECURSE TEST_SOURCES CONFIGURE_DEPENDS {glob_ext})\n"
            f"add_executable({name}_tests ${{TEST_SOURCES}})\n"
            f"target_link_libraries({name}_tests PRIVATE\n"
            f"    {name}_core\n"
            f"    Catch2::Catch2WithMain\n"
            f")\n"
            f"add_test(NAME {name}_tests COMMAND {name}_tests)\n"
        )
    elif framework == "doctest":
        tests_cmake.write_text(
            f"find_package(doctest CONFIG REQUIRED)\n\n"
            f"file(GLOB_RECURSE TEST_SOURCES CONFIGURE_DEPENDS {glob_ext})\n"
            f"add_executable({name}_tests ${{TEST_SOURCES}})\n"
            f"target_link_libraries({name}_tests PRIVATE\n"
            f"    {name}_core\n"
            f"    doctest::doctest\n"
            f")\n"
            f"add_test(NAME {name}_tests COMMAND {name}_tests)\n"
        )
    elif framework == "googletest":
        tests_cmake.write_text(
            f"find_package(GTest CONFIG REQUIRED)\n\n"
            f"file(GLOB_RECURSE TEST_SOURCES CONFIGURE_DEPENDS {glob_ext})\n"
            f"add_executable({name}_tests ${{TEST_SOURCES}})\n"
            f"target_link_libraries({name}_tests PRIVATE\n"
            f"    {name}_core\n"
            f"    GTest::gtest_main\n"
            f")\n"
            f"add_test(NAME {name}_tests COMMAND {name}_tests)\n"
        )
    elif framework == "cmocka":
        tests_cmake.write_text(
            f"find_package(cmocka CONFIG REQUIRED)\n\n"
            f"file(GLOB_RECURSE TEST_SOURCES CONFIGURE_DEPENDS {glob_ext})\n"
            f"add_executable({name}_tests ${{TEST_SOURCES}})\n"
            f"target_link_libraries({name}_tests PRIVATE\n"
            f"    {name}_core\n"
            f"    cmocka::cmocka\n"
            f")\n"
            f"add_test(NAME {name}_tests COMMAND {name}_tests)\n"
        )


def _write_starter_test(dest: Path, project_type: str, framework: str) -> None:
    if framework == "none":
        return

    is_cpp  = "cpp" in project_type
    ext     = ".cpp" if is_cpp else ".c"
    outfile = dest / "tests" / f"test_main{ext}"

    if framework == "catch2":
        outfile.write_text(
            '#include <catch2/catch_test_macros.hpp>\n\n'
            'TEST_CASE("placeholder", "[core]") {\n'
            '    REQUIRE(1 + 1 == 2);\n'
            '}\n'
        )
    elif framework == "doctest":
        outfile.write_text(
            '#include <doctest/doctest.h>\n\n'
            'TEST_CASE("placeholder") {\n'
            '    CHECK(1 + 1 == 2);\n'
            '}\n'
        )
    elif framework == "googletest":
        outfile.write_text(
            '#include <gtest/gtest.h>\n\n'
            'TEST(Placeholder, BasicAssertion) {\n'
            '    EXPECT_EQ(1 + 1, 2);\n'
            '}\n'
        )
    elif framework == "cmocka":
        outfile.write_text(
            '#include <stdarg.h>\n'
            '#include <stddef.h>\n'
            '#include <setjmp.h>\n'
            '#include <cmocka.h>\n\n'
            'static void test_placeholder(void **state) {\n'
            '    (void)state;\n'
            '    assert_int_equal(1 + 1, 2);\n'
            '}\n\n'
            'int main(void) {\n'
            '    const struct CMUnitTest tests[] = {\n'
            '        cmocka_unit_test(test_placeholder),\n'
            '    };\n'
            '    return cmocka_run_group_tests(tests, NULL, NULL);\n'
            '}\n'
        )


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
