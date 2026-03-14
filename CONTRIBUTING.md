# Contributing to GRIMX

## Prerequisites

- Python 3.10+
- CMake 3.20+
- A C/C++ compiler (gcc or clang)

## Dev Setup

Clone the repo and run the setup script:

```bash
git clone https://github.com/grimlabs/grimx
cd grimx
./setup.sh
source .venv/bin/activate
grimx --version
```

Or use the Makefile:

```bash
make dev
source .venv/bin/activate
```

This installs GRIMX in editable mode (`pip install -e .`) so changes to the source are reflected immediately without reinstalling.

## Makefile Targets

```bash
make dev      # create .venv and install editable + dev deps
make test     # run GRIMX's test suite
make clean    # remove .venv and build artifacts
```

## Repository Structure

```
grimx/
  grimx/         Python package
    cli.py      Entry point and command definitions
    scaffold.py grimx new
    install.py  grimx install
    build.py    grimx build / test / run
    config.py   grimx.config and grimx.lock read/write
  templates/    Project scaffolding templates
    c_app/
    cpp_app/
    embedded_c/
    embedded_cpp/
  docs/
  tests/        GRIMX's own test suite
  pyproject.toml
  Makefile
  setup.sh
```

## Areas for Contribution

- Platform testing (Windows, macOS, Linux)
- Dependency manager integrations (vcpkg, Conan)
- New project templates
- Documentation improvements
