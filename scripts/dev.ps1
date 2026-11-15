# Start the Hexam backend (FastAPI :8000) and frontend (Vite :5173) together on Windows.
# Usage (repo root):  powershell -ExecutionPolicy Bypass -File scripts/dev.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root "backend/.venv/Scripts/python.exe"
if (-not (Test-Path $py)) {
  Write-Host "Creating backend venv..."
  python -m venv (Join-Path $root "backend/.venv")
  & $py -m pip install -e "$root/backend[dev]"
}
if (-not (Test-Path (Join-Path $root "frontend/node_modules"))) {
  npm --prefix (Join-Path $root "frontend") install
}
$api = Start-Process -PassThru -NoNewWindow $py -ArgumentList "-m","uvicorn","hexam.api.main:app","--app-dir","$root/backend","--port","8000"
try {
  Write-Host "API docs: http://127.0.0.1:8000/docs   App: http://localhost:5173"
  npm --prefix (Join-Path $root "frontend") run dev
} finally {
  Stop-Process -Id $api.Id -ErrorAction SilentlyContinue
}
