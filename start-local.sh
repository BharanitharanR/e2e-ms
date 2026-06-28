#!/usr/bin/env bash
# start-local.sh — Launch the full e2MS stack on the host (no Docker needed).
#
# Usage:
#   export ANTHROPIC_API_KEY=sk-ant-...   # optional but needed for AI features
#   bash start-local.sh
#
# Services started:
#   :8001  customer_jit  — System Under Test (SUT) / JIT Funding stub
#   :8101  acquirer      — Acquirer simulator
#   :8102  visa          — Network (Visa/MC/Amex/Discover) pass-through
#   :8103  marqeta_sim   — Marqeta issuer-processor simulator
#   :8000  backend       — Orchestrator / API gateway
#   :8501  frontend      — Streamlit UI
#
# Prerequisites (all Python — no Docker, no Java, no Mongo, no Ollama needed):
#   pip install fastapi uvicorn requests streamlit pyyaml pyiso8583 anthropic
#
# Stop: Ctrl-C (kills the process group)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$REPO_ROOT/.runlogs"
mkdir -p "$LOG_DIR"

# ── Resolve python / uvicorn ──────────────────────────────────────────────────
PYTHON="${PYTHON:-}"
if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
    PYTHON="$REPO_ROOT/.venv/bin/python"
elif command -v python3 &>/dev/null; then
    PYTHON=python3
elif command -v python &>/dev/null; then
    PYTHON=python
else
    echo "❌ python3 not found. Install Python 3.9+ and re-run." >&2
    exit 1
fi
echo "🐍 Using Python: $($PYTHON --version)"

UVICORN="$PYTHON -m uvicorn"

# ── Colour helpers ─────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; BLUE='\033[0;34m'; BOLD='\033[1m'; RESET='\033[0m'

# ── Cleanup: kill the whole process group on exit / Ctrl-C ────────────────────
_PIDS=()
cleanup() {
    echo ""
    echo -e "${BOLD}Stopping all services…${RESET}"
    for pid in "${_PIDS[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
    wait 2>/dev/null || true
    echo "Done."
}
trap cleanup EXIT INT TERM

# ── Service launch helper ──────────────────────────────────────────────────────
# Usage: _start_service <name> <log_file> <env_block> <uvicorn_args>
start_service() {
    local name="$1"; shift
    local log_file="$1"; shift
    local env_block="$1"; shift   # "KEY=VAL KEY2=VAL2" — space-separated
    local module="$1"; shift      # e.g. backend.main:app
    local host="${1:-0.0.0.0}"; shift || true
    local port="${1:-8000}"; shift || true

    env $env_block $UVICORN "$module" \
        --host "$host" --port "$port" \
        --log-level warning \
        > "$log_file" 2>&1 &
    local pid=$!
    _PIDS+=($pid)
    echo -e "  ${GREEN}✓${RESET} ${BOLD}$name${RESET} → :$port  (PID $pid, log: $log_file)"
}

# ── Wait for a service to respond ─────────────────────────────────────────────
wait_for() {
    local url="$1"
    local name="$2"
    local attempts="${3:-30}"
    local i=0
    while ! curl -sf "$url" >/dev/null 2>&1; do
        i=$((i+1))
        if [[ $i -ge $attempts ]]; then
            echo "❌ $name did not start in time (${attempts}s). Check log." >&2
            return 1
        fi
        sleep 1
    done
    echo -e "  ${GREEN}✓${RESET} $name ready"
}

# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo -e "${BOLD}${BLUE}e2MS — Local Stack Launcher${RESET}"
echo -e "${BOLD}Working directory:${RESET} $REPO_ROOT"
echo ""

# ── Localhost service URLs (no Docker DNS) ────────────────────────────────────
export ACQUIRER_URL="http://127.0.0.1:8101/authorize"
export CUSTOMER_JIT_URL="http://127.0.0.1:8001"
export CUSTOMER_JIT_RESET_URL="http://127.0.0.1:8001/reset"
export VISA_URL="http://127.0.0.1:8102/network/authorize"
export MARQETA_URL="http://127.0.0.1:8103/issuer/authorize"
export MARQETA_SIM_URL="http://127.0.0.1:8103"
export ACQUIRER_SVC_URL="http://127.0.0.1:8101"
export VISA_SVC_URL="http://127.0.0.1:8102"
# Frontend API pointer — Streamlit talks to the orchestrator on :8000
export API_URL="http://127.0.0.1:8000"

echo -e "${BOLD}Starting services…${RESET}"

# 1. Customer JIT (SUT) — 8001
start_service "customer_jit" "$LOG_DIR/customer_jit.log" \
    "APPROVAL_LIMIT_CENTS=${APPROVAL_LIMIT_CENTS:-5000}" \
    "customer_jit.app:app" "0.0.0.0" "8001"

# 2. Acquirer simulator — 8101
start_service "acquirer" "$LOG_DIR/acquirer.log" \
    "VISA_URL=$VISA_URL" \
    "backend.acquirer:app" "0.0.0.0" "8101"

# 3. Visa/Network pass-through — 8102
start_service "visa" "$LOG_DIR/visa.log" \
    "MARQETA_URL=$MARQETA_URL" \
    "backend.visa:app" "0.0.0.0" "8102"

# 4. Marqeta issuer-processor simulator — 8103
start_service "marqeta_simulator" "$LOG_DIR/marqeta_simulator.log" \
    "CUSTOMER_JIT_URL=$CUSTOMER_JIT_URL/jit/authorize" \
    "backend.marqeta_simulator:app" "0.0.0.0" "8103"

# 5. Orchestrator / backend — 8000
start_service "backend (orchestrator)" "$LOG_DIR/backend.log" \
    "ACQUIRER_URL=$ACQUIRER_URL CUSTOMER_JIT_URL=$CUSTOMER_JIT_URL CUSTOMER_JIT_RESET_URL=$CUSTOMER_JIT_RESET_URL MARQETA_SIM_URL=$MARQETA_SIM_URL ACQUIRER_SVC_URL=$ACQUIRER_SVC_URL VISA_SVC_URL=$VISA_SVC_URL ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-} ANTHROPIC_MODEL=${ANTHROPIC_MODEL:-claude-opus-4-5}" \
    "backend.main:app" "0.0.0.0" "8000"

echo ""
echo -e "${BOLD}Waiting for services to be ready…${RESET}"
sleep 2   # give uvicorn a moment to bind

wait_for "http://127.0.0.1:8001/health"  "customer_jit  :8001"
wait_for "http://127.0.0.1:8101/health"  "acquirer       :8101"
wait_for "http://127.0.0.1:8102/health"  "visa           :8102"
wait_for "http://127.0.0.1:8103/health"  "marqeta_sim    :8103"
wait_for "http://127.0.0.1:8000/health"  "backend        :8000"

# ── Quick smoke test ──────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}Running smoke test…${RESET}"
SMOKE_RESULT=$(curl -sf -X POST "http://127.0.0.1:8000/execute/authorization_approve" \
    -H "Content-Type: application/json" -d '{}' 2>&1 || echo '{"error":"smoke_failed"}')
if echo "$SMOKE_RESULT" | grep -q '"passed": *true\|"passed":true'; then
    echo -e "  ${GREEN}✓${RESET} Smoke test PASSED — end-to-end flow OK"
else
    echo "  ⚠️  Smoke test did not confirm 'passed: true' — check logs in $LOG_DIR"
fi

# ── Start Streamlit frontend ───────────────────────────────────────────────────
echo ""
echo -e "${BOLD}Starting Streamlit frontend on :8501…${RESET}"
FRONTEND_DIR="$REPO_ROOT/frontend"
$PYTHON -m streamlit run "$FRONTEND_DIR/01_home.py" \
    --server.port 8501 \
    --server.address 0.0.0.0 \
    --server.headless true \
    --browser.gatherUsageStats false \
    >> "$LOG_DIR/frontend.log" 2>&1 &
_PIDS+=($!)

wait_for "http://127.0.0.1:8501/_stcore/health" "frontend :8501" 20

echo ""
echo -e "${BOLD}${GREEN}╔══════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}${GREEN}║  e2MS demo stack is ready (local mode)      ║${RESET}"
echo -e "${BOLD}${GREEN}╠══════════════════════════════════════════════╣${RESET}"
echo -e "${BOLD}${GREEN}║  UI:       http://localhost:8501             ║${RESET}"
echo -e "${BOLD}${GREEN}║  API docs: http://localhost:8000/docs        ║${RESET}"
echo -e "${BOLD}${GREEN}║  Logs:     $LOG_DIR/${RESET}"
echo -e "${BOLD}${GREEN}╚══════════════════════════════════════════════╝${RESET}"
echo ""
echo -e "Press ${BOLD}Ctrl-C${RESET} to stop all services."
echo ""

# Wait for all background jobs to finish (they won't unless killed)
wait
