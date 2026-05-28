param(
  [switch]$SkipInstall,
  [string]$HostName = $(if ($env:AIMEMO_HOST) { $env:AIMEMO_HOST } else { "127.0.0.1" }),
  [int]$Port = $(if ($env:AIMEMO_FRONTEND_PORT) { [int]$env:AIMEMO_FRONTEND_PORT } else { 5173 }),
  [int]$BackendPort = $(if ($env:AIMEMO_BACKEND_PORT) { [int]$env:AIMEMO_BACKEND_PORT } else { 8000 })
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$frontendDir = Join-Path $repoRoot "frontend"

function Test-PortAvailable {
  param([string]$HostName, [int]$Port)
  $listener = $null
  try {
    $address = [System.Net.IPAddress]::Parse($HostName)
    $listener = [System.Net.Sockets.TcpListener]::new($address, $Port)
    $listener.Start()
    return $true
  }
  catch {
    return $false
  }
  finally {
    if ($listener) {
      $listener.Stop()
    }
  }
}

while (-not (Test-PortAvailable -HostName $HostName -Port $Port)) {
  $Port++
}

Set-Location $frontendDir

if ((-not $SkipInstall) -or (-not (Test-Path "node_modules"))) {
  Write-Host "Installing frontend dependencies..."
  npm install
}

$env:VITE_API_BASE_URL = if ($env:VITE_API_BASE_URL) { $env:VITE_API_BASE_URL } else { "http://${HostName}:$BackendPort" }
Write-Host "Starting AiMemo frontend dev server at http://${HostName}:$Port/app/ ..."
Write-Host "Product entry remains http://${HostName}:$BackendPort/app after frontend build."
npm run dev -- --host $HostName --port $Port --strictPort
