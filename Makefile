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
	@echo "$(BOLD)Local demo (no Docker, no Ollama, no MongoDB):$(RESET)"
	@echo "  $(GREEN)make demo-local$(RESET)    Start full stack on localhost (6 services + Streamlit)"
	@echo ""
	@echo "$(BOLD)Docker demo (recommended for pitch / shared environments):$(RESET)"
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
	@echo "  $(GREEN)make test$(RESET)                Run all pytest suites (127 tests)"
	@echo "  $(GREEN)make test-vertical-slice$(RESET)  Phase 1 ISO/JPF vertical-slice tests (26)"
	@echo "  $(GREEN)make test-lifecycle$(RESET)        Phase 2 lifecycle ledger tests (12)"
	@echo "  $(GREEN)make test-phase3$(RESET)           Phase 3 network/settlement/interchange/jPOS tests (40)"
	@echo "  $(GREEN)make test-phase5$(RESET)           Phase 5 local-demo/AI-config/enrichment/mandate tests (49)"
	@echo ""
	@echo "$(BOLD)Utilities:$(RESET)"
	@echo "  $(GREEN)make iso-engine$(RESET)    Start jPOS ISO sidecar (port 8200; Java 11+ required)"
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

.PHONY: demo-local
demo-local:
	@echo ""
	@echo "$(BOLD)Starting e2MS local stack (no Docker)…$(RESET)"
	@echo "$(BLUE)Logs will appear in .runlogs/ — press Ctrl-C to stop.$(RESET)"
	@bash start-local.sh

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
test: test-vertical-slice test-lifecycle test-phase3 test-phase5
	@echo ""
	@echo "$(GREEN)$(BOLD)All test suites passed! (127 tests)$(RESET)"

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

.PHONY: test-phase5
test-phase5:
	@echo "$(BOLD)Running Phase 5 tests (local demo, AI config, enrichment trace, mandate)…$(RESET)"
	$(PYTEST) tests/test_phase5.py -v --tb=short


# ── ISO Engine (jPOS sidecar) ─────────────────────────────────────────────────
.PHONY: iso-engine
iso-engine:
	@echo "$(BOLD)Starting jPOS ISO engine sidecar (port 8200)…$(RESET)"
	@echo "$(BLUE)Requires Java 11+ and iso-engine/ directory with jPOS Q2 config.$(RESET)"
	@if [ -d iso-engine ] && [ -f iso-engine/pom.xml ]; then \
		cd iso-engine && mvn -q package -DskipTests && \
		java -jar target/iso-engine-*.jar; \
	elif [ -d iso-engine ] && [ -f iso-engine/build.gradle ]; then \
		cd iso-engine && ./gradlew -q run; \
	else \
		echo "$(BOLD)iso-engine/ not found or missing pom.xml / build.gradle.$(RESET)"; \
		echo "$(BLUE)Without the jPOS sidecar, the Python pyiso8583 packer is used as fallback.$(RESET)"; \
		echo "$(BLUE)Set ISO_ENGINE_URL=http://localhost:8200 when the sidecar is running.$(RESET)"; \
	fi


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
