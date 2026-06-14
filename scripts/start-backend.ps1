param(
  [switch]$SkipInstall,
  [switch]$NoReload,
  [switch]$ProfileStartup,
  [string]$HostName = $(if ($env:AIMEMO_HOST) { $env:AIMEMO_HOST } else { "127.0.0.1" }),
  [int]$Port = $(if ($env:AIMEMO_BACKEND_PORT) { [int]$env:AIMEMO_BACKEND_PORT } else { 8000 })
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$backendDir = Join-Path $repoRoot "backend"
$venvPython = Join-Path $backendDir ".venv\Scripts\python.exe"
$startupProfiler = [System.Diagnostics.Stopwatch]::StartNew()

function Write-StartupProfile {
  param([string]$Step)

  if (-not $ProfileStartup) {
    return
  }

  $elapsed = $startupProfiler.Elapsed.TotalSeconds
  Write-Host ("[backend-startup {0,7:N2}s] {1}" -f $elapsed, $Step)
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

function Test-Python312 {
  param([string]$PythonExe)
  if (-not (Test-Path $PythonExe)) {
    return $false
  }
  & $PythonExe -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)" 2>$null
  return $LASTEXITCODE -eq 0
}

function Test-PythonCandidate {
  param(
    [string]$PythonExe,
    [string[]]$PrefixArgs = @()
  )
  if (-not $PythonExe) {
    return $false
  }
  if (-not (Test-Path $PythonExe) -and -not (Get-Command $PythonExe -ErrorAction SilentlyContinue)) {
    return $false
  }
  & $PythonExe @PrefixArgs -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)" 2>$null
  return $LASTEXITCODE -eq 0
}

function Get-Python312Candidate {
  $candidates = @()
  if ($env:AIMEMO_PYTHON) {
    $candidates += @{ Exe = $env:AIMEMO_PYTHON; Args = @() }
  }
  $candidates += @(
    @{ Exe = "py"; Args = @("-3.12") },
    @{ Exe = "python3.12"; Args = @() },
    @{ Exe = "python"; Args = @() }
  )

  foreach ($candidate in $candidates) {
    if (Test-PythonCandidate -PythonExe $candidate.Exe -PrefixArgs $candidate.Args) {
      return $candidate
    }
  }
  return $null
}

function Invoke-Python312 {
  param([string[]]$Arguments)

  $candidate = Get-Python312Candidate
  if ($candidate) {
    & $candidate.Exe @($candidate.Args) @Arguments
    return
  }

  Write-Host "Python 3.12 was not found. Trying to install it with winget..."
  if (Get-Command winget -ErrorAction SilentlyContinue) {
    winget install --id Python.Python.3.12 -e --source winget
    if (Test-PythonCandidate -PythonExe "py" -PrefixArgs @("-3.12")) {
      & py -3.12 @Arguments
      return
    }
  }

  throw "Python 3.12 is required. Install Python 3.12, or set AIMEMO_PYTHON to a Python 3.12 python.exe."
}

Set-Location $backendDir

Invoke-ProfiledStep -Name "venv check/create" -Script {
  if (-not (Test-Python312 $venvPython)) {
    if (Test-Path ".venv") {
      Write-Host "Existing backend virtual environment is not Python 3.12. Recreating .venv..."
      Remove-Item -Recurse -Force ".venv"
    } else {
      Write-Host "Creating backend virtual environment with Python 3.12..."
    }
    Invoke-Python312 -Arguments @("-m", "venv", ".venv")
  }
}

Invoke-ProfiledStep -Name "venv python version check" -Script {
  if (-not (Test-Python312 $venvPython)) {
    throw "Backend virtual environment was created, but it is not Python 3.12."
  }
}

Invoke-ProfiledStep -Name "backend dependency install/check" -Script {
  if (-not $SkipInstall) {
    Write-Host "Installing backend dependencies..."
    & $venvPython -m pip install -U pip
    & $venvPython -m pip install -e ".[dev]"
  }
}

Write-Host "Starting AiMemo gateway at http://${HostName}:$Port ..."
Write-Host "AiMemo app will be available at http://${HostName}:$Port/app after frontend build."
$uvicornArgs = @("-m", "uvicorn", "app.main:app", "--host", $HostName, "--port", "$Port")
if (-not $NoReload) {
  # Development startup should pick up Python source edits without requiring a full script restart.
  $uvicornArgs += "--reload"
}
if ($ProfileStartup) {
  $env:AIMEMO_PROFILE_STARTUP = "1"
  Write-StartupProfile "launch uvicorn"
}
& $venvPython @uvicornArgs
