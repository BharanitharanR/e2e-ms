# Makefile — e2MS Marqeta E2E Simulator
#
# Quickstart:
#   export ANTHROPIC_API_KEY=sk-ant-...
#   make demo
#
# Run all tests locally:
#   make test
#
# CI smoke (no Docker):
#   make test-vertical-slice
#   make test-lifecycle

.DEFAULT_GOAL := help

# ── Variables ────────────────────────────────────────────────────────────────
COMPOSE      := docker compose
DEMO_COMPOSE := $(COMPOSE) -f docker-compose.demo.yml
FULL_COMPOSE := $(COMPOSE) -f docker-compose.yml
PYTHON       := python3
PYTEST       := $(PYTHON) -m pytest

# ── Colours ──────────────────────────────────────────────────────────────────
BOLD  := \033[1m
RESET := \033[0m
GREEN := \033[0;32m
BLUE  := \033[0;34m

# ── Help ─────────────────────────────────────────────────────────────────────
.PHONY: help
help:
	@echo ""
	@echo "$(BOLD)e2MS — Marqeta E2E Simulator$(RESET)"
	@echo ""
	@echo "$(BOLD)Demo (no Ollama/MongoDB required):$(RESET)"
	@echo "  $(GREEN)make demo$(RESET)          Start 6-service demo stack and open UI URL"
	@echo "  $(GREEN)make demo-build$(RESET)    Force-rebuild images then start demo"
	@echo "  $(GREEN)make demo-stop$(RESET)     Stop the demo stack (keep volumes)"
	@echo "  $(GREEN)make demo-reset$(RESET)    Stop + remove all demo volumes"
	@echo ""
	@echo "$(BOLD)Full stack (includes Ollama + MongoDB):$(RESET)"
	@echo "  $(GREEN)make up$(RESET)            Start the full docker-compose stack"
	@echo "  $(GREEN)make down$(RESET)          Stop the full stack"
	@echo "  $(GREEN)make logs$(RESET)          Tail all container logs"
	@echo ""
	@echo "$(BOLD)Tests (no Docker):$(RESET)"
	@echo "  $(GREEN)make test$(RESET)                Run all pytest suites (78 tests)"
	@echo "  $(GREEN)make test-vertical-slice$(RESET)  Phase 1 ISO/JPF vertical-slice tests (26)"
	@echo "  $(GREEN)make test-lifecycle$(RESET)        Phase 2 lifecycle ledger tests (12)"
	@echo "  $(GREEN)make test-phase3$(RESET)           Phase 3 network/settlement/interchange/jPOS tests (40)"
	@echo ""
	@echo "$(BOLD)Utilities:$(RESET)"
	@echo "  $(GREEN)make pos-simulate$(RESET)  Run the PC/SC agent in software-simulation mode"
	@echo "  $(GREEN)make lint$(RESET)          Run ruff linter over backend + tests"
	@echo "  $(GREEN)make clean$(RESET)         Remove __pycache__ and .pytest_cache"
	@echo ""


# ── Demo targets ─────────────────────────────────────────────────────────────
.PHONY: demo
demo:
	@echo ""
	@echo "$(BOLD)Starting e2MS demo stack…$(RESET)"
	@bash demo.sh

.PHONY: demo-build
demo-build:
	@bash demo.sh --no-smoke
	$(DEMO_COMPOSE) build

.PHONY: demo-stop
demo-stop:
	$(DEMO_COMPOSE) stop

.PHONY: demo-reset
demo-reset:
	$(DEMO_COMPOSE) down -v
	@echo "Demo stack stopped and volumes removed."


# ── Full stack ────────────────────────────────────────────────────────────────
.PHONY: up
up:
	$(FULL_COMPOSE) up --build -d

.PHONY: down
down:
	$(FULL_COMPOSE) down

.PHONY: logs
logs:
	$(FULL_COMPOSE) logs -f


# ── Tests ─────────────────────────────────────────────────────────────────────
.PHONY: test
test: test-vertical-slice test-lifecycle test-phase3
	@echo ""
	@echo "$(GREEN)$(BOLD)All test suites passed! (78 tests)$(RESET)"

.PHONY: test-vertical-slice
test-vertical-slice:
	@echo "$(BOLD)Running vertical-slice tests…$(RESET)"
	$(PYTEST) tests/test_vertical_slice.py -v --tb=short

.PHONY: test-lifecycle
test-lifecycle:
	@echo "$(BOLD)Running lifecycle ledger tests…$(RESET)"
	$(PYTEST) tests/test_lifecycle.py -v --tb=short

.PHONY: test-phase3
test-phase3:
	@echo "$(BOLD)Running Phase 3 tests (network routing, settlement, interchange, jPOS)…$(RESET)"
	$(PYTEST) tests/test_phase3.py -v --tb=short


# ── POS agent ─────────────────────────────────────────────────────────────────
.PHONY: pos-simulate
pos-simulate:
	@echo "$(BOLD)Running PC/SC agent in software-simulation mode…$(RESET)"
	$(PYTHON) -m pos_agent.agent --simulate


# ── Lint ──────────────────────────────────────────────────────────────────────
.PHONY: lint
lint:
	@command -v ruff &>/dev/null || pip install ruff -q
	ruff check backend/ tests/ pos_agent/ --select E,W,F --ignore E501


# ── Clean ─────────────────────────────────────────────────────────────────────
.PHONY: clean
clean:
	find . -type d -name __pycache__ -not -path './.venv/*' -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -not -path './.venv/*' -exec rm -rf {} + 2>/dev/null || true
	@echo "Cleaned."
