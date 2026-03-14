#!/usr/bin/env bash
# GRIMX contributor setup script
# For end users: pip install grimx
#
# This script is for contributors working on GRIMX itself.
# It creates a local .venv and installs GRIMX in editable mode.
#
# Usage:
#   ./setup.sh          # editable install into .venv
#   ./setup.sh --help

set -euo pipefail

VENV=".venv"

for arg in "$@"; do
    case $arg in
        --help|-h)
            echo "Usage: ./setup.sh"
            echo ""
            echo "  Sets up a local .venv for contributing to GRIMX."
            echo "  End users should run: pip install grimx"
            exit 0
            ;;
        *)
            echo "Unknown argument: $arg"
            exit 1
            ;;
    esac
done

# Python check
if ! command -v python3 &>/dev/null; then
    echo "error: python3 not found in PATH."
    exit 1
fi

if ! python3 -c "import sys; exit(0 if sys.version_info >= (3,10) else 1)"; then
    PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    echo "error: Python 3.10+ required (found $PYTHON_VERSION)."
    exit 1
fi

echo "✓ Python $(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")"

# Create venv
if [ ! -d "$VENV" ]; then
    echo "Creating virtual environment at $VENV..."
    python3 -m venv "$VENV"
else
    echo "✓ $VENV already exists"
fi

"$VENV/bin/pip" install --upgrade pip --quiet
"$VENV/bin/pip" install -e ".[dev]" --quiet

echo ""
echo "✓ GRIMX installed (editable) into $VENV"
echo ""
echo "Activate with:"
echo "  source $VENV/bin/activate"