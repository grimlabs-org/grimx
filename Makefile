VENV   := .venv
PYTHON := $(VENV)/bin/python
GRIM   := $(VENV)/bin/grim

.DEFAULT_GOAL := help

# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------

.PHONY: help
help:
	@echo "GRIM — available targets:"
	@echo ""
	@echo "  make install   Create .venv and install GRIM (runtime)"
	@echo "  make dev       Create .venv and install GRIM + dev dependencies"
	@echo "  make test      Run GRIM's own test suite"
	@echo "  make clean     Remove .venv and build artifacts"
	@echo ""
	@echo "Alternatively, use the setup script directly:"
	@echo "  ./setup.sh          # runtime"
	@echo "  ./setup.sh --dev    # dev"

# ---------------------------------------------------------------------------
# Environment setup (delegates to setup.sh)
# ---------------------------------------------------------------------------

.PHONY: install
install:
	@bash setup.sh

.PHONY: dev
dev:
	@bash setup.sh --dev

# ---------------------------------------------------------------------------
# Testing
# ---------------------------------------------------------------------------

.PHONY: test
test:
	@if [ ! -f "$(PYTHON)" ]; then \
		echo "error: .venv not found. Run 'make dev' first."; \
		exit 1; \
	fi
	$(PYTHON) -m pytest tests/ -v

# ---------------------------------------------------------------------------
# Clean
# ---------------------------------------------------------------------------

.PHONY: clean
clean:
	rm -rf $(VENV)
	rm -rf dist/ build/ *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
	@echo "✓ cleaned"
