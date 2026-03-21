.PHONY: install install-dev test test-quick lint clean build help

# Default Python
PYTHON ?= python3

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

install: ## Install in editable mode
	uv pip install -e .

install-dev: ## Install with dev dependencies
	uv pip install -e ".[dev]"

install-all: ## Install with all optional dependencies
	uv pip install -e ".[dev,openai]"

sync: ## Sync dependencies (lock + install)
	uv sync

venv: ## Create virtual environment
	uv venv
	@echo "Run: source .venv/bin/activate"

test: ## Run all tests
	$(PYTHON) -m pytest tests/ -v

test-quick: ## Run tests (quiet output)
	$(PYTHON) -m pytest tests/ -q

test-file: ## Run tests for a specific file (usage: make test-file F=test_feature_queue.py)
	$(PYTHON) -m pytest tests/$(F) -v

clean: ## Remove build artifacts and caches
	rm -rf dist/ build/ *.egg-info/ .pytest_cache/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true

build: ## Build package
	uv build

init-project: ## Init a new nezha project (usage: make init-project DIR=./my-project)
	nezha init $(DIR)
