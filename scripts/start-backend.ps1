param(
  [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$backendDir = Join-Path $repoRoot "backend"
$venvPython = Join-Path $backendDir ".venv\Scripts\python.exe"

Set-Location $backendDir

if (-not (Test-Path $venvPython)) {
  Write-Host "Creating backend virtual environment..."
  if (Get-Command py -ErrorAction SilentlyContinue) {
    py -3.12 -m venv .venv
  } else {
    python -m venv .venv
  }
}

if (-not $SkipInstall) {
  Write-Host "Installing backend dependencies..."
  & $venvPython -m pip install -U pip
  & $venvPython -m pip install -e ".[dev]"
}

Write-Host "Starting AiMemo backend at http://127.0.0.1:8000 ..."
& $venvPython -m uvicorn app.main:app --host 127.0.0.1 --port 8000
