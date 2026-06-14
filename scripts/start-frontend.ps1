param(
  [switch]$SkipInstall,
  [switch]$ProfileStartup,
  [string]$HostName = $(if ($env:AIMEMO_HOST) { $env:AIMEMO_HOST } else { "127.0.0.1" }),
  [int]$Port = $(if ($env:AIMEMO_FRONTEND_PORT) { [int]$env:AIMEMO_FRONTEND_PORT } else { 5173 }),
  [int]$BackendPort = $(if ($env:AIMEMO_BACKEND_PORT) { [int]$env:AIMEMO_BACKEND_PORT } else { 8000 })
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$frontendDir = Join-Path $repoRoot "frontend"
$startupProfiler = [System.Diagnostics.Stopwatch]::StartNew()

function Write-StartupProfile {
  param([string]$Step)

  if (-not $ProfileStartup) {
    return
  }

  $elapsed = $startupProfiler.Elapsed.TotalSeconds
  Write-Host ("[frontend-startup {0,7:N2}s] {1}" -f $elapsed, $Step)
}

function Invoke-ProfiledStep {
  param(
    [string]$Name,
    [scriptblock]$Script
  )

  Write-StartupProfile "begin $Name"
  $stepTimer = [System.Diagnostics.Stopwatch]::StartNew()
  try {
    & $Script
  }
  finally {
    $stepTimer.Stop()
    Write-StartupProfile ("end {0} ({1:N2}s)" -f $Name, $stepTimer.Elapsed.TotalSeconds)
  }
}

Invoke-ProfiledStep -Name "node/npm version check" -Script {
  if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw "npm is required. Install Node.js 20+ from https://nodejs.org/ and make sure npm is in PATH."
  }
  if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    throw "node is required. Install Node.js 20+ from https://nodejs.org/ and make sure node is in PATH."
  }
  $nodeMajor = [int](& node -p "process.versions.node.split('.')[0]")
  if ($nodeMajor -lt 20) {
    throw "Node.js 20+ is required. Current version: $(node --version). Install Node.js 20+ from https://nodejs.org/."
  }
}

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

Invoke-ProfiledStep -Name "port availability scan" -Script {
  while (-not (Test-PortAvailable -HostName $HostName -Port $Port)) {
    $script:Port++
  }
}

Set-Location $frontendDir

function Test-NpmPackageInstalled {
  param([string]$PackageName)
  if (-not (Test-Path "node_modules")) {
    return $false
  }
  npm ls $PackageName --depth=0 --silent *> $null
  return $LASTEXITCODE -eq 0
}

Invoke-ProfiledStep -Name "frontend dependency install/check" -Script {
  $missingRequiredPackage = -not (Test-NpmPackageInstalled -PackageName "mermaid")

  if ((-not $SkipInstall) -or (-not (Test-Path "node_modules")) -or $missingRequiredPackage) {
    Write-Host "Installing frontend dependencies..."
    npm install
  }

  if (-not (Test-NpmPackageInstalled -PackageName "mermaid")) {
    throw "Frontend dependency 'mermaid' is missing. Run 'npm install' in frontend/ or rerun without -SkipInstall."
  }
}

$env:VITE_API_BASE_URL = if ($env:VITE_API_BASE_URL) { $env:VITE_API_BASE_URL } else { "http://${HostName}:$BackendPort" }
Write-Host "Starting AiMemo frontend dev server at http://${HostName}:$Port/app/ ..."
Write-Host "Product entry remains http://${HostName}:$BackendPort/app after frontend build."
Write-StartupProfile "launch vite"
npm run dev -- --host $HostName --port $Port --strictPort
