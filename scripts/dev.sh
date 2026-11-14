#!/usr/bin/env bash
# Start the Hexam backend (FastAPI :8000) and frontend (Vite :5173) together (macOS/Linux/Git Bash).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ -x "$ROOT/backend/.venv/bin/python" ]; then PY="$ROOT/backend/.venv/bin/python"; else PY="$ROOT/backend/.venv/Scripts/python"; fi
if [ ! -e "$PY" ] && [ ! -e "$PY.exe" ]; then
  python3 -m venv "$ROOT/backend/.venv"
  if [ -x "$ROOT/backend/.venv/bin/python" ]; then PY="$ROOT/backend/.venv/bin/python"; fi
  "$PY" -m pip install -e "$ROOT/backend[dev]"
fi
[ -d "$ROOT/frontend/node_modules" ] || npm --prefix "$ROOT/frontend" install
"$PY" -m uvicorn hexam.api.main:app --app-dir "$ROOT/backend" --port 8000 &
API_PID=$!
trap 'kill $API_PID 2>/dev/null' EXIT
echo "API: http://127.0.0.1:8000/docs   App: http://localhost:5173"
npm --prefix "$ROOT/frontend" run dev
