param(
  [switch]$Json,
  [switch]$Fix,
  [switch]$NonInteractive,
  [switch]$NoDesktop
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$backendDir = Join-Path $repoRoot "backend"
$frontendDir = Join-Path $repoRoot "frontend"
$desktopDir = Join-Path $repoRoot "desktop"
$venvPython = Join-Path $backendDir ".venv\Scripts\python.exe"

$checks = New-Object System.Collections.Generic.List[object]

function Add-Check {
  param(
    [string]$Id,
    [ValidateSet("ok", "warn", "error", "skip")]
    [string]$Status,
    [string]$Message,
    [string]$Hint = ""
  )

  $checks.Add([pscustomobject]@{
    id = $Id
    status = $Status
    message = $Message
    hint = $Hint
  })
}

function Test-Command {
  param([string]$Name)
  return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Test-Python312 {
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
  try {
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $PythonExe @PrefixArgs -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)" *> $null
    return $LASTEXITCODE -eq 0
  }
  catch {
    return $false
  }
  finally {
    $ErrorActionPreference = $previousErrorActionPreference
  }
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
    if (Test-Python312 -PythonExe $candidate.Exe -PrefixArgs $candidate.Args) {
      return $candidate
    }
  }
  return $null
}

function Test-PortAvailable {
  param(
    [string]$HostName,
    [int]$Port
  )

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

function Test-FrontendPackageInstalled {
  param([string]$PackageName)

  Push-Location $frontendDir
  try {
    if (-not (Test-Path "node_modules")) {
      return $false
    }
    npm ls $PackageName --depth=0 --silent *> $null
    return $LASTEXITCODE -eq 0
  }
  finally {
    Pop-Location
  }
}

function Test-HttpOk {
  param(
    [string]$Url,
    [string]$Contains = ""
  )

  try {
    $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
    if ($response.StatusCode -lt 200 -or $response.StatusCode -ge 300) {
      return $false
    }
    if ($Contains -and ([string]$response.Content) -notlike "*$Contains*") {
      return $false
    }
    return $true
  }
  catch {
    return $false
  }
}

function Test-BackendHealth {
  param(
    [string]$HostName,
    [int]$Port
  )

  try {
    $response = Invoke-WebRequest -Uri "http://${HostName}:$Port/api/health" -UseBasicParsing -TimeoutSec 2
    if ($response.StatusCode -lt 200 -or $response.StatusCode -ge 300) {
      return $false
    }
    return ([string]$response.Content) -like '*"status"*"ok"*'
  }
  catch {
    return $false
  }
}

function Test-PortOwnedByRepoProcess {
  param(
    [int]$Port,
    [string[]]$Markers
  )

  try {
    $connections = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    $escapedRoot = [regex]::Escape($repoRoot.Path)
    foreach ($connection in $connections) {
      if (-not $connection.OwningProcess -or $connection.OwningProcess -le 0) {
        continue
      }
      $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($connection.OwningProcess)" -ErrorAction SilentlyContinue
      if (-not $process -or -not $process.CommandLine) {
        continue
      }
      $commandLine = [string]$process.CommandLine
      if ($commandLine -notmatch $escapedRoot) {
        continue
      }
      foreach ($marker in $Markers) {
        if ($commandLine -match $marker) {
          return $true
        }
      }
    }
  }
  catch {
    return $false
  }
  return $false
}

if ($Fix) {
  Add-Check -Id "fix" -Status "warn" -Message "doctor -Fix is not implemented in Phase 1." -Hint "Run existing start scripts to let them install dependencies, or implement Phase 2 repair actions."
}

if (Test-Path (Join-Path $repoRoot ".git")) {
  Add-Check -Id "repo.git" -Status "ok" -Message "Git repository detected."
} else {
  Add-Check -Id "repo.git" -Status "warn" -Message "This directory does not look like a Git checkout." -Hint "Run doctor from the AiMemo repository root."
}

if (Test-Command "git") {
  Add-Check -Id "git.command" -Status "ok" -Message "git is available."
} else {
  Add-Check -Id "git.command" -Status "error" -Message "git was not found on PATH." -Hint "Install Git and reopen PowerShell."
}

$expectedAimemoBinDir = if ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA "AiMemo\bin" } elseif ($env:USERPROFILE) { Join-Path $env:USERPROFILE ".aimemo\bin" } else { "" }
$expectedAimemoCmd = if ($expectedAimemoBinDir) { Join-Path $expectedAimemoBinDir "aimemo.cmd" } else { "" }
$expectedAimemoPsWrapper = if ($expectedAimemoBinDir) { Join-Path $expectedAimemoBinDir "aimemo.ps1" } else { "" }
$expectedAimemoScript = Join-Path $PSScriptRoot "aimemo.ps1"
$aimemoCommand = Get-Command "aimemo" -ErrorAction SilentlyContinue
if ($aimemoCommand -and $aimemoCommand.Source) {
  try {
    $resolvedCommand = [System.IO.Path]::GetFullPath($aimemoCommand.Source)
    $resolvedExpected = if ($expectedAimemoCmd) { [System.IO.Path]::GetFullPath($expectedAimemoCmd) } else { "" }
    $resolvedExpectedPsWrapper = if ($expectedAimemoPsWrapper) { [System.IO.Path]::GetFullPath($expectedAimemoPsWrapper) } else { "" }
    $isExpectedWrapper =
      ($resolvedExpected -and [string]::Equals($resolvedCommand, $resolvedExpected, [System.StringComparison]::OrdinalIgnoreCase)) -or
      ($resolvedExpectedPsWrapper -and [string]::Equals($resolvedCommand, $resolvedExpectedPsWrapper, [System.StringComparison]::OrdinalIgnoreCase))
    if ($isExpectedWrapper) {
      $wrapperContent = if (Test-Path $expectedAimemoPsWrapper) { Get-Content -LiteralPath $expectedAimemoPsWrapper -Raw } else { "" }
      if ($wrapperContent -like "*$expectedAimemoScript*") {
        Add-Check -Id "aimemo.global" -Status "ok" -Message "Global aimemo command is registered for this checkout."
      } else {
        Add-Check -Id "aimemo.global" -Status "warn" -Message "Global aimemo command exists but points to a different checkout." -Hint "Run .\scripts\register-aimemo.ps1 to refresh the wrapper."
      }
    } else {
      Add-Check -Id "aimemo.global" -Status "warn" -Message "An aimemo command exists, but it is not this checkout's wrapper." -Hint "Found: $($aimemoCommand.Source). Run .\scripts\register-aimemo.ps1 to refresh the user-local wrapper."
    }
  }
  catch {
    Add-Check -Id "aimemo.global" -Status "warn" -Message "Could not validate the global aimemo command." -Hint $_.Exception.Message
  }
} elseif ($expectedAimemoCmd -and (Test-Path $expectedAimemoCmd)) {
  Add-Check -Id "aimemo.global" -Status "warn" -Message "Global aimemo wrapper exists but is not on PATH." -Hint "Run .\scripts\register-aimemo.ps1, then restart PowerShell if needed."
} else {
  Add-Check -Id "aimemo.global" -Status "warn" -Message "Global aimemo command is not registered." -Hint "Run .\scripts\register-aimemo.ps1 to enable commands like aimemo doctor."
}

if (Test-Command "node") {
  try {
    $nodeVersion = (& node --version).Trim()
    $nodeMajor = [int](& node -p "process.versions.node.split('.')[0]")
    if ($nodeMajor -ge 20) {
      Add-Check -Id "node.version" -Status "ok" -Message "Node.js is available: $nodeVersion."
    } else {
      Add-Check -Id "node.version" -Status "error" -Message "Node.js 20+ is required. Current version: $nodeVersion." -Hint "Install Node.js 20+."
    }
  }
  catch {
    Add-Check -Id "node.version" -Status "error" -Message "Failed to read Node.js version." -Hint $_.Exception.Message
  }
} else {
  Add-Check -Id "node.version" -Status "error" -Message "node was not found on PATH." -Hint "Install Node.js 20+."
}

if (Test-Command "npm") {
  Add-Check -Id "npm.command" -Status "ok" -Message "npm is available."
} else {
  Add-Check -Id "npm.command" -Status "error" -Message "npm was not found on PATH." -Hint "Install Node.js 20+ with npm."
}

$venvReady = Test-Python312 -PythonExe $venvPython
$pythonCandidate = Get-Python312Candidate
if ($pythonCandidate) {
  Add-Check -Id "python.312" -Status "ok" -Message "Python 3.12 is available."
} elseif ($venvReady) {
  Add-Check -Id "python.312" -Status "warn" -Message "Standalone Python 3.12 was not found, but backend/.venv is usable." -Hint "Install Python 3.12 or set AIMEMO_PYTHON before rebuilding backend/.venv."
} else {
  Add-Check -Id "python.312" -Status "error" -Message "Python 3.12 was not found." -Hint "Install Python 3.12 or set AIMEMO_PYTHON to a Python 3.12 executable."
}

if ($venvReady) {
  Add-Check -Id "backend.venv" -Status "ok" -Message "backend/.venv uses Python 3.12."
} elseif (Test-Path (Join-Path $backendDir ".venv")) {
  Add-Check -Id "backend.venv" -Status "error" -Message "backend/.venv exists but is not Python 3.12." -Hint "Run scripts/start-backend.ps1 or Phase 2 doctor -Fix to rebuild it."
} else {
  Add-Check -Id "backend.venv" -Status "error" -Message "backend/.venv is missing." -Hint "Run scripts/start-backend.ps1 or Phase 2 doctor -Fix to create it."
}

if ($venvReady) {
  Push-Location $backendDir
  try {
    $previousErrorActionPreference = $ErrorActionPreference
    $previousPythonWarnings = $env:PYTHONWARNINGS
    $ErrorActionPreference = "Continue"
    $env:PYTHONWARNINGS = "ignore"
    & $venvPython -W ignore -c "import fastapi, sqlmodel, langgraph; import app.main" *> $null
    if ($LASTEXITCODE -eq 0) {
      Add-Check -Id "backend.imports" -Status "ok" -Message "Backend core imports are available."
    } else {
      Add-Check -Id "backend.imports" -Status "error" -Message "Backend core imports failed." -Hint "Install backend dependencies with scripts/start-backend.ps1."
    }
  }
  finally {
    $ErrorActionPreference = $previousErrorActionPreference
    if ($null -eq $previousPythonWarnings) {
      Remove-Item Env:\PYTHONWARNINGS -ErrorAction SilentlyContinue
    } else {
      $env:PYTHONWARNINGS = $previousPythonWarnings
    }
    Pop-Location
  }
} else {
  Add-Check -Id "backend.imports" -Status "skip" -Message "Skipped backend import check because backend/.venv is not ready."
}

$dashscopeKeyPresent = -not [string]::IsNullOrWhiteSpace($env:DASHSCOPE_API_KEY)
$envPath = Join-Path $repoRoot ".env"
if (Test-Path $envPath) {
  Add-Check -Id "config.env" -Status "ok" -Message ".env exists."
} elseif ($dashscopeKeyPresent) {
  Add-Check -Id "config.env" -Status "ok" -Message ".env is missing, but required model key is available in the current environment." -Hint "Create .env if you want this configuration to persist across terminals."
} else {
  Add-Check -Id "config.env" -Status "warn" -Message ".env is missing." -Hint "Copy .env.example to .env and fill required API keys."
}

if ($dashscopeKeyPresent) {
  Add-Check -Id "config.dashscope" -Status "ok" -Message "DASHSCOPE_API_KEY is present in the current process environment."
} else {
  Add-Check -Id "config.dashscope" -Status "warn" -Message "DASHSCOPE_API_KEY is not present in the current process environment." -Hint "If it is stored in .env, backend startup will load it; onboard should make this explicit later."
}

$frontendNodeModules = Join-Path $frontendDir "node_modules"
if (Test-Path $frontendNodeModules) {
  Add-Check -Id "frontend.node_modules" -Status "ok" -Message "frontend/node_modules exists."
} else {
  Add-Check -Id "frontend.node_modules" -Status "error" -Message "frontend/node_modules is missing." -Hint "Run npm install in frontend/ or Phase 2 doctor -Fix."
}

if (Test-Command "npm") {
  if (Test-FrontendPackageInstalled -PackageName "mermaid") {
    Add-Check -Id "frontend.mermaid" -Status "ok" -Message "Frontend dependency mermaid is installed."
  } else {
    Add-Check -Id "frontend.mermaid" -Status "error" -Message "Frontend dependency mermaid is missing." -Hint "Run npm install in frontend/."
  }
} else {
  Add-Check -Id "frontend.mermaid" -Status "skip" -Message "Skipped frontend package check because npm is missing."
}

$frontendDistIndex = Join-Path $frontendDir "dist\index.html"
if (Test-Path $frontendDistIndex) {
  Add-Check -Id "frontend.dist" -Status "ok" -Message "frontend/dist/index.html exists."
} else {
  Add-Check -Id "frontend.dist" -Status "warn" -Message "frontend/dist/index.html is missing." -Hint "Run npm run build in frontend/ before using the backend-hosted /app entry."
}

$hostName = if ($env:AIMEMO_HOST) { $env:AIMEMO_HOST } else { "127.0.0.1" }
$backendPort = if ($env:AIMEMO_BACKEND_PORT) { [int]$env:AIMEMO_BACKEND_PORT } else { 8000 }
$frontendPort = if ($env:AIMEMO_FRONTEND_PORT) { [int]$env:AIMEMO_FRONTEND_PORT } else { 5173 }

if (Test-PortAvailable -HostName $hostName -Port $backendPort) {
  Add-Check -Id "port.backend" -Status "ok" -Message "Backend port $backendPort is available."
} elseif (Test-BackendHealth -HostName $hostName -Port $backendPort) {
  Add-Check -Id "port.backend" -Status "ok" -Message "Backend port $backendPort is already serving AiMemo."
} elseif (Test-PortOwnedByRepoProcess -Port $backendPort -Markers @("uvicorn", "app\.main:app", "start-backend")) {
  Add-Check -Id "port.backend" -Status "warn" -Message "Backend port $backendPort is used by an AiMemo process, but health check is not ready." -Hint "If startup is still in progress, rerun doctor in a moment."
} else {
  Add-Check -Id "port.backend" -Status "warn" -Message "Backend port $backendPort is already in use by another process." -Hint "start-dev can choose a fallback port; stop the other service if this is unexpected."
}

if (Test-PortAvailable -HostName $hostName -Port $frontendPort) {
  Add-Check -Id "port.frontend" -Status "ok" -Message "Frontend port $frontendPort is available."
} elseif (
  (Test-HttpOk -Url "http://${hostName}:$frontendPort/app/" -Contains "AiMemo") -or
  (Test-HttpOk -Url "http://${hostName}:$frontendPort/" -Contains "/src/main.tsx")
) {
  Add-Check -Id "port.frontend" -Status "ok" -Message "Frontend port $frontendPort is already serving AiMemo."
} elseif (Test-PortOwnedByRepoProcess -Port $frontendPort -Markers @("vite", "start-frontend", "npm.*run.*dev")) {
  Add-Check -Id "port.frontend" -Status "ok" -Message "Frontend port $frontendPort is used by the AiMemo dev server."
} else {
  Add-Check -Id "port.frontend" -Status "warn" -Message "Frontend port $frontendPort is already in use by another process." -Hint "start-dev can choose a fallback port; stop the other service if this is unexpected."
}

if ($NoDesktop) {
  Add-Check -Id "desktop.rust" -Status "skip" -Message "Desktop checks skipped by -NoDesktop."
} elseif (Test-Command "cargo") {
  Add-Check -Id "desktop.rust" -Status "ok" -Message "Rust/Cargo is available."
} else {
  Add-Check -Id "desktop.rust" -Status "warn" -Message "Rust/Cargo was not found." -Hint "Web startup can continue. Install Rust only if you need Memo Elf desktop."
}

$errorCount = @($checks | Where-Object { $_.status -eq "error" }).Count
$warnCount = @($checks | Where-Object { $_.status -eq "warn" }).Count

if ($Json) {
  [pscustomobject]@{
    ok = $errorCount -eq 0
    errors = $errorCount
    warnings = $warnCount
    checks = $checks
  } | ConvertTo-Json -Depth 6
} else {
  $summaryText = if ($errorCount -gt 0) {
    "Needs attention"
  } elseif ($warnCount -gt 0) {
    "Usable with warnings"
  } else {
    "Ready"
  }
  $summaryColor = if ($errorCount -gt 0) {
    "Red"
  } elseif ($warnCount -gt 0) {
    "Yellow"
  } else {
    "Green"
  }

  Write-Host ""
  Write-Host "AiMemo Doctor" -ForegroundColor Cyan
  Write-Host "-------------" -ForegroundColor DarkCyan
  Write-Host "Status: " -NoNewline
  Write-Host $summaryText -ForegroundColor $summaryColor
  Write-Host "Checks: " -NoNewline
  Write-Host "$($checks.Count)" -ForegroundColor White -NoNewline
  Write-Host " total, " -NoNewline
  Write-Host "$errorCount" -ForegroundColor Red -NoNewline
  Write-Host " error(s), " -NoNewline
  Write-Host "$warnCount" -ForegroundColor Yellow -NoNewline
  Write-Host " warning(s)"
  Write-Host ""

  foreach ($check in $checks) {
    $label = switch ($check.status) {
      "ok" { "OK" }
      "warn" { "WARN" }
      "error" { "ERROR" }
      "skip" { "SKIP" }
    }
    $color = switch ($check.status) {
      "ok" { "Green" }
      "warn" { "Yellow" }
      "error" { "Red" }
      "skip" { "DarkGray" }
    }

    Write-Host ("  [{0,-5}] " -f $label) -ForegroundColor $color -NoNewline
    Write-Host $check.id -ForegroundColor White -NoNewline
    Write-Host "  $($check.message)" -ForegroundColor Gray
    if ($check.hint) {
      Write-Host "          $($check.hint)" -ForegroundColor DarkGray
    }
  }

  Write-Host ""
  Write-Host "Next: " -ForegroundColor DarkCyan -NoNewline
  if ($errorCount -gt 0) {
    Write-Host "fix the error checks above, then rerun aimemo doctor." -ForegroundColor Gray
  } elseif ($warnCount -gt 0) {
    Write-Host "warnings do not block startup, but they explain rough edges." -ForegroundColor Gray
  } else {
    Write-Host "run aimemo start to launch AiMemo." -ForegroundColor Gray
  }
  Write-Host ""
}

if ($errorCount -gt 0) {
  exit 1
}
exit 0
