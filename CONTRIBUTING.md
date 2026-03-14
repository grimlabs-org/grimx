# Contributing to GRIM

## Prerequisites

- Python 3.10+
- CMake 3.20+
- A C/C++ compiler (gcc or clang)

## Dev Setup

Clone the repo and run the setup script:

```bash
git clone https://github.com/grimlabs/grim
cd grim
./setup.sh
source .venv/bin/activate
grim --version
```

Or use the Makefile:

```bash
make dev
source .venv/bin/activate
```

This installs GRIM in editable mode (`pip install -e .`) so changes to the source are reflected immediately without reinstalling.

## Makefile Targets

```bash
make dev      # create .venv and install editable + dev deps
make test     # run GRIM's test suite
make clean    # remove .venv and build artifacts
```

## Repository Structure

```
grim/
  grim/         Python package
    cli.py      Entry point and command definitions
    scaffold.py grim new
    install.py  grim install
    build.py    grim build / test / run
    config.py   grim.config and grim.lock read/write
  templates/    Project scaffolding templates
    c_app/
    cpp_app/
    embedded_c/
    embedded_cpp/
  docs/
  tests/        GRIM's own test suite
  pyproject.toml
  Makefile
  setup.sh
```

## Areas for Contribution

- Platform testing (Windows, macOS, Linux)
- Dependency manager integrations (vcpkg, Conan)
- New project templates
- Documentation improvements
