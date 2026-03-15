"""
grimx.cli
Entry point for the GRIMX command-line interface
"""

import click 
from grimx import __version__
from grimx import scaffold, install as install_mod, build as build_mod 

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