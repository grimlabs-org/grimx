"""
grimx.cli
Entry point for the GRIMX command-line interface
"""

import click 
from grimx import __version__
from grimx import scaffold, install as install_mod, build as build_mod 
from grimx.config import load_lock

@click.group()
@click.version_option(__version__, prog_name="grimx")
def main():
    """GRIMX - GCC Runtime & Installation Manager, Cross-platform.
    
    Minimal tooling for reproducible C and C++ environments.
    """

@main.command()
@click.argument("name", required=False, default=None)
@click.option(
    "--type",
    "project_type",
    default=None,
    type=click.Choice(["c", "cpp", "embedded-c", "embedded-cpp"], case_sensitive=False),
    help="Project type (skips prompt if provided).",
)
def new(name: str | None, project_type: str | None):
    """Scaffold a new project interactivity, or pass NAME to skip the name prompt."""
    scaffold.create_project(name, project_type)


@main.command("install")
@click.argument("package", required=False, default=None)
def install_cmd(package):
    """Install a dependency, or restore all from grimx.lock."""
    install_mod.run(package)

@main.command("build")
def build_cmd():
    """Build the project via CMake."""
    build_mod.run()

@main.command("test")
def test_cmd():
    """Run tests via CTest."""
    build_mod.run_tests()

@main.command("run", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def run_cmd(args):
    """Run the compiled application."""
    build_mod.run_app(list(args))

@main.command("clean")
@click.option(
    "--full",
    is_flag=True,
    default=False,
    help="Also remove vcpkg_installed/ in addition to build/.",
)
def clean_cmd(full: bool):
    """Remove build artifacts.
    
    By default remove the build/directory.
    Use --full to also remove vcpkg_installed/.
    """
    build_mod.clean(full)

@main.command("list")
def list_cmd():
    """List all installed dependencies from grimx.lock."""
    deps: dict = load_lock().get("dependencies", {})
 
    if not deps:
        click.echo(
            "\n  No dependencies installed."
            "\n  Run 'grimx install <package>' to get started.\n"
        )
        return
 
    # Dynamic column widths based on content
    pkg_w = max(len("Package"), max(len(n) for n in deps)) + 2
    ver_w = max(len("Version"), max(len(str(m.get("version", ""))) for m in deps.values())) + 2
    mgr_w = max(len("Manager"), max(len(str(m.get("manager", ""))) for m in deps.values())) + 2
 
    click.echo("")
    click.echo(f"  {'Package':<{pkg_w}}{'Version':<{ver_w}}{'Manager':<{mgr_w}}")
    click.echo(f"  {'-' * (pkg_w - 2)}  {'-' * (ver_w - 2)}  {'-' * (mgr_w - 2)}")
 
    for name, meta in deps.items():
        version = meta.get("version", "unknown")
        manager = meta.get("manager", "unknown")
        click.echo(f"  {name:<{pkg_w}}{version:<{ver_w}}{manager:<{mgr_w}}")
 
    count = len(deps)
    click.echo(f"\n  {count} package{'s' if count != 1 else ''} installed\n")