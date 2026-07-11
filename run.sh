#!/usr/bin/env bash
# One-command demo launcher for eRakshak.
# Sets up the engine venv, generates a mock corpus, then starts the engine + dashboard.
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

echo "==> Setting up Python engine"
cd engine
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
pip install -q -r requirements.txt

if [ ! -d _corpus/device_A ]; then
  echo "==> Generating synthetic mock corpus"
  python tools/make_corpus.py _corpus/device_A
fi

echo "==> Starting engine on http://127.0.0.1:5057"
python -m triage.server --port 5057 &
ENGINE_PID=$!
cd "$ROOT"

cleanup() { echo; echo "==> Shutting down"; kill $ENGINE_PID 2>/dev/null || true; }
trap cleanup EXIT INT TERM

echo "==> Starting dashboard"
cd app
if [ ! -d node_modules ]; then
  npm install
fi
npm run dev

wait
