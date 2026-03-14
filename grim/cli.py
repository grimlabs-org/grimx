"""
grim.cli
Entry point for the GRIM command-line interface
"""

import click 
from grim import __version__
from grim import scaffold, install as install_mod, build as build_mod 

@click.group()
@click.version_option(__version__, prog_name="grim")
def main():
    """GRIM - GNU Runtime & Installation Manager.
    
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
    """Install a dependency, or restore all from grim.lock."""
    install_mod.run(package)

@main.command("build")
def build_cmd():
    """Build the project via CMake."""
    build_mod.run()

@main.command("test")
def test_cmd():
    """Run tests via CTest."""
    build_mod.run_tests()

@main.command("run")
def run_cmd():
    """Run the compiled application."""
    build_mod.run_app()