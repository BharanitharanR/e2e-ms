#!/usr/bin/env bash
# demo.sh — one-command demo launcher for the e2MS Marqeta Simulator
#
# Usage:
#   export ANTHROPIC_API_KEY=sk-ant-...
#   ./demo.sh [--no-build] [--no-smoke]
#
# What this script does:
#   1. Validates that ANTHROPIC_API_KEY is set.
#   2. Brings up the 6 core services via docker-compose.demo.yml.
#   3. Waits for all /health endpoints to respond (up to 90 s).
#   4. Runs a quick smoke test (POST /execute/authorization_approve).
#   5. Prints the Streamlit UI URL.
#
# Requirements:  docker (compose v2), curl
set -euo pipefail

# ── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${BLUE}[e2MS]${RESET} $*"; }
success() { echo -e "${GREEN}[e2MS]${RESET} $*"; }
warn()    { echo -e "${YELLOW}[e2MS]${RESET} $*"; }
error()   { echo -e "${RED}[e2MS] ERROR:${RESET} $*" >&2; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.demo.yml"

# ── Arg defaults ─────────────────────────────────────────────────────────────
NO_BUILD=false
SMOKE=true

for arg in "$@"; do
  case $arg in
    --no-build)  NO_BUILD=true ;;
    --no-smoke)  SMOKE=false ;;
    --help|-h)
      echo "Usage: $0 [--no-build] [--no-smoke]"
      echo "  --no-build   Skip docker compose build step (faster restart)"
      echo "  --no-smoke   Skip the quick smoke test after startup"
      exit 0
      ;;
  esac
done

# ── Banner ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║   e2MS — Marqeta E2E Simulator  •  Demo Launcher    ║${RESET}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════╝${RESET}"
echo ""

# ── Pre-flight: Docker ────────────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
  error "Docker is not installed. Install Docker Desktop from https://docker.com/products/docker-desktop"
  exit 1
fi

# ── Pre-flight: ANTHROPIC_API_KEY ────────────────────────────────────────────
if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
  error "ANTHROPIC_API_KEY is not set."
  error "Run:  export ANTHROPIC_API_KEY=sk-ant-..."
  error "Get a key at: https://console.anthropic.com/settings/keys"
  exit 1
fi

if [[ ! "${ANTHROPIC_API_KEY}" =~ ^sk-ant- ]]; then
  warn "ANTHROPIC_API_KEY doesn't look like a valid Anthropic key (expected sk-ant-...)."
  warn "Proceeding anyway — the AI Copilot features may not work."
fi

info "API key found: ${ANTHROPIC_API_KEY:0:12}...${ANTHROPIC_API_KEY: -4}"

# ── Build & bring up services ─────────────────────────────────────────────────
BUILD_FLAG=""
if [[ "${NO_BUILD}" == "false" ]]; then
  info "Building Docker images… (this may take a few minutes on first run)"
  BUILD_FLAG="--build"
fi

info "Starting services via docker-compose.demo.yml…"
docker compose -f "${COMPOSE_FILE}" up -d ${BUILD_FLAG}

# ── Health-check poll ─────────────────────────────────────────────────────────
declare -A SVC_URLS=(
  ["Backend API"]="http://localhost:8000/health"
  ["Acquirer"]="http://localhost:8101/health"
  ["Visa Network"]="http://localhost:8102/health"
  ["Marqeta Sim"]="http://localhost:8103/health"
  ["Customer JIT"]="http://localhost:8001/health"
  ["Frontend"]="http://localhost:8501/_stcore/health"
)

MAX_WAIT=90
POLL_INTERVAL=3
echo ""
info "Waiting for all services to be healthy (max ${MAX_WAIT}s)…"

all_ok=false
for ((elapsed=0; elapsed<MAX_WAIT; elapsed+=POLL_INTERVAL)); do
  all_ok=true
  for url in "${SVC_URLS[@]}"; do
    if ! curl -sf --max-time 2 "${url}" >/dev/null 2>&1; then
      all_ok=false
      break
    fi
  done
  [[ "${all_ok}" == "true" ]] && break
  echo -ne "\r  Elapsed: ${elapsed}s / ${MAX_WAIT}s …  "
  sleep "${POLL_INTERVAL}"
done
echo ""  # newline after \r progress

if [[ "${all_ok}" != "true" ]]; then
  error "One or more services did not become healthy within ${MAX_WAIT}s."
  echo ""
  echo "Service status:"
  for name in "${!SVC_URLS[@]}"; do
    url="${SVC_URLS[$name]}"
    if curl -sf --max-time 2 "${url}" >/dev/null 2>&1; then
      echo -e "  ${GREEN}✅${RESET}  ${name}"
    else
      echo -e "  ${RED}❌${RESET}  ${name}  (${url})"
    fi
  done
  echo ""
  warn "Check logs with:  docker compose -f docker-compose.demo.yml logs --tail=50"
  exit 1
fi

echo ""
success "All services are healthy:"
for name in "${!SVC_URLS[@]}"; do
  echo -e "  ${GREEN}✅${RESET}  ${name}"
done

# ── Quick smoke test ──────────────────────────────────────────────────────────
if [[ "${SMOKE}" == "true" ]]; then
  echo ""
  info "Running quick smoke test (POST /execute/authorization_approve)…"
  SMOKE_RESULT=$(
    curl -sf --max-time 10 -X POST \
      -H "Content-Type: application/json" \
      "http://localhost:8000/execute/authorization_approve" \
      2>/dev/null || echo '{"error":"curl_failed"}'
  )

  if echo "${SMOKE_RESULT}" | grep -q '"passed": true\|"passed":true'; then
    success "Smoke test PASSED ✅"
  elif echo "${SMOKE_RESULT}" | grep -q '"error"'; then
    warn "Smoke test returned an error — backend may still be warming up."
    warn "Response snippet: ${SMOKE_RESULT:0:200}"
  else
    info "Smoke test response: ${SMOKE_RESULT:0:300}"
  fi
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}══════════════════════════════════════════════════════${RESET}"
echo -e "${GREEN}${BOLD}  🚀  e2MS is ready!${RESET}"
echo ""
echo -e "  ${BOLD}Streamlit UI:${RESET}    ${BLUE}http://localhost:8501${RESET}"
echo -e "  ${BOLD}Backend API:${RESET}     ${BLUE}http://localhost:8000/docs${RESET}"
echo -e "  ${BOLD}OpenAPI docs:${RESET}    ${BLUE}http://localhost:8000/redoc${RESET}"
echo ""
echo -e "  Stop:   ${YELLOW}docker compose -f docker-compose.demo.yml down${RESET}"
echo -e "  Logs:   ${YELLOW}docker compose -f docker-compose.demo.yml logs -f${RESET}"
echo -e "  Reset:  ${YELLOW}make demo-reset${RESET}  (stop + remove volumes)"
echo -e "${BOLD}══════════════════════════════════════════════════════${RESET}"
echo ""
