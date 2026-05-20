param(
  [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$frontendDir = Join-Path $repoRoot "frontend"

Set-Location $frontendDir

if ((-not $SkipInstall) -or (-not (Test-Path "node_modules"))) {
  Write-Host "Installing frontend dependencies..."
  npm install
}

Write-Host "Starting AiMemo frontend dev server at http://127.0.0.1:5173/app/ ..."
Write-Host "Product entry remains http://127.0.0.1:8000/app after frontend build."
npm run dev -- --host 127.0.0.1
