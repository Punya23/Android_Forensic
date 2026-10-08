#!/usr/bin/env bash
# SNAGR launcher: sets everything up, starts it, and tells you where to go.
#
#   ./run.sh          dev   engine :5057 + hot-reload dashboard :5173
#   ./run.sh serve    one process: the engine serves the built dashboard on :5057,
#                     and is restarted if it crashes (use this to present or demo)
#   ./run.sh build    rebuild the dashboard only
#
# PORT=5060 ./run.sh serve   to use another engine port.
set -euo pipefail

MODE="${1:-dev}"
PORT="${PORT:-5057}"
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

case "$MODE" in dev | serve | build) ;; *) echo "usage: $0 [dev|serve|build]" >&2; exit 2 ;; esac
for tool in python3 node npm curl; do
  command -v "$tool" >/dev/null || { echo "missing required tool: $tool" >&2; exit 1; }
done

healthy() { curl -fsS -m 2 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; }

setup_engine() {
  echo "==> Setting up Python engine"
  cd "$ROOT/engine"
  [ -d .venv ] || python3 -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install -q -r requirements.txt
  [ -f .env ] || { cp .env.example .env && echo "    created engine/.env from .env.example (change the default login before real use)"; }
  if [ ! -d _corpus/device_A ]; then
    echo "==> Generating synthetic mock corpus"
    python tools/make_corpus.py _corpus/device_A
  fi
  cd "$ROOT"
}

build_dashboard() {
  cd "$ROOT/app"
  [ -d node_modules ] || npm install
  # Rebuild when there is no build, or any source is newer than it (dist/ is not in git).
  if [ ! -f dist/index.html ] || [ -n "$(find src index.html package.json tailwind.config.js vite.config.ts -newer dist/index.html -print -quit 2>/dev/null)" ]; then
    echo "==> Building dashboard"
    npm run build
  else
    echo "==> Dashboard build is up to date"
  fi
  cd "$ROOT"
}

# Wait until the engine answers, or fail loudly instead of leaving a dashboard with no engine.
wait_healthy() {
  local pid="$1"
  for _ in $(seq 1 60); do
    healthy && return 0
    kill -0 "$pid" 2>/dev/null || { echo "engine exited before it became healthy" >&2; return 1; }
    sleep 0.5
  done
  echo "engine did not answer on :$PORT within 30s" >&2
  return 1
}

PIDS=()
cleanup() {
  trap - EXIT INT TERM
  echo; echo "==> Shutting down"
  # The serve loop is a subshell whose child is the engine: stop the child too, or it keeps the port.
  for p in "${PIDS[@]:-}"; do [ -n "$p" ] && { pkill -P "$p" 2>/dev/null; kill "$p" 2>/dev/null; } || true; done
}
trap cleanup EXIT INT TERM

# Reuse an engine that is already up rather than failing on a taken port.
engine_already_running() {
  if healthy; then
    echo "==> Engine already running on :$PORT — reusing it"
    return 0
  fi
  if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "port $PORT is taken by something that is not a SNAGR engine; free it or set PORT=" >&2
    exit 1
  fi
  return 1
}

if [ "$MODE" = build ]; then
  build_dashboard
  exit 0
fi

if [ "$MODE" = serve ]; then
  build_dashboard
  if engine_already_running; then
    echo "==> Open http://127.0.0.1:$PORT"
    exit 0
  fi
  setup_engine
  echo "==> Starting engine on http://127.0.0.1:$PORT (restarts on crash; Ctrl-C stops)"
  (
    cd "$ROOT/engine"
    # shellcheck disable=SC1091
    source .venv/bin/activate
    while true; do
      python -m triage.server --port "$PORT" || echo "engine exited ($?), restarting in 2s" >&2
      sleep 2
    done
  ) &
  PIDS+=("$!")
  wait_healthy "${PIDS[0]}"
  echo "==> Ready: open http://127.0.0.1:$PORT"
  wait
  exit 0
fi

# dev: engine in the background, hot-reload dashboard in front
if ! engine_already_running; then
  setup_engine
  echo "==> Starting engine on http://127.0.0.1:$PORT"
  (cd "$ROOT/engine" && source .venv/bin/activate && exec python -m triage.server --port "$PORT") &
  PIDS+=("$!")
  wait_healthy "${PIDS[0]}"
fi

cd "$ROOT/app"
[ -d node_modules ] || npm install
echo "==> Ready: open http://localhost:5173  (engine API on :$PORT)"
npm run dev
