param(
  [switch]$SkipInstall,
  [switch]$NoReload,
  [string]$HostName = $(if ($env:AIMEMO_HOST) { $env:AIMEMO_HOST } else { "127.0.0.1" }),
  [int]$Port = $(if ($env:AIMEMO_BACKEND_PORT) { [int]$env:AIMEMO_BACKEND_PORT } else { 8000 })
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$backendDir = Join-Path $repoRoot "backend"
$venvPython = Join-Path $backendDir ".venv\Scripts\python.exe"

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

function Test-Python312 {
  param([string]$PythonExe)
  if (-not (Test-Path $PythonExe)) {
    return $false
  }
  $version = & $PythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
  return $version -eq "3.12"
}

function Invoke-Python312 {
  param([string[]]$Arguments)

  if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3.12 @Arguments
    return
  }
  if (Get-Command python3.12 -ErrorAction SilentlyContinue) {
    & python3.12 @Arguments
    return
  }
  if (Get-Command python -ErrorAction SilentlyContinue) {
    $version = & python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
    if ($version -eq "3.12") {
      & python @Arguments
      return
    }
  }

  Write-Host "Python 3.12 was not found. Trying to install it with winget..."
  if (Get-Command winget -ErrorAction SilentlyContinue) {
    winget install --id Python.Python.3.12 -e --source winget
    if (Get-Command py -ErrorAction SilentlyContinue) {
      & py -3.12 @Arguments
      return
    }
  }

  throw "Python 3.12 is required. Please install Python 3.12 and rerun this script."
}

Set-Location $backendDir

if (-not (Test-Python312 $venvPython)) {
  if (Test-Path ".venv") {
    Write-Host "Existing backend virtual environment is not Python 3.12. Recreating .venv..."
    Remove-Item -Recurse -Force ".venv"
  } else {
    Write-Host "Creating backend virtual environment with Python 3.12..."
  }
  Invoke-Python312 -Arguments @("-m", "venv", ".venv")
}

if (-not (Test-Python312 $venvPython)) {
  throw "Backend virtual environment was created, but it is not Python 3.12."
}

if (-not $SkipInstall) {
  Write-Host "Installing backend dependencies..."
  & $venvPython -m pip install -U pip
  & $venvPython -m pip install -e ".[dev]"
}

Write-Host "Starting AiMemo gateway at http://${HostName}:$Port ..."
Write-Host "AiMemo app will be available at http://${HostName}:$Port/app after frontend build."
$uvicornArgs = @("-m", "uvicorn", "app.main:app", "--host", $HostName, "--port", "$Port")
if (-not $NoReload) {
  # Development startup should pick up Python source edits without requiring a full script restart.
  $uvicornArgs += "--reload"
}
& $venvPython @uvicornArgs
