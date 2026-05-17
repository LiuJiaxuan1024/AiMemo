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

Write-Host "Starting AiMemo frontend at http://127.0.0.1:5173 ..."
npm run dev -- --host 127.0.0.1
