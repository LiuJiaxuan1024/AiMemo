param(
  [switch]$SkipInstall,
  [switch]$NoDesktop
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$backendScript = Join-Path $PSScriptRoot "start-backend.ps1"
$frontendScript = Join-Path $PSScriptRoot "start-frontend.ps1"
$desktopDir = Join-Path $repoRoot "desktop"
$stopScript = Join-Path $PSScriptRoot "stop-dev.ps1"
$frontendDir = Join-Path $repoRoot "frontend"

function Assert-CommandAvailable {
  param(
    [string]$CommandName,
    [string]$InstallHint
  )

  if (-not (Get-Command $CommandName -ErrorAction SilentlyContinue)) {
    throw "$CommandName is required. $InstallHint"
  }
}

function Assert-NodeVersion {
  Assert-CommandAvailable -CommandName "node" -InstallHint "Install Node.js 20+ from https://nodejs.org/ and make sure node is in PATH."
  Assert-CommandAvailable -CommandName "npm" -InstallHint "Install Node.js 20+ from https://nodejs.org/ and make sure npm is in PATH."

  $majorText = & node -p "process.versions.node.split('.')[0]"
  $major = [int]$majorText
  if ($major -lt 20) {
    throw "Node.js 20+ is required. Current version: $(node --version). Install Node.js 20+ from https://nodejs.org/."
  }
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

function Find-AvailablePort {
  param(
    [string]$HostName,
    [int]$PreferredPort,
    [int]$MaxAttempts = 100
  )

  $port = $PreferredPort
  for ($i = 0; $i -lt $MaxAttempts; $i++) {
    if (Test-PortAvailable -HostName $HostName -Port $port) {
      return $port
    }
    $port++
  }
  throw "Could not find a free port starting at $PreferredPort for $HostName."
}

function Write-PortFallback {
  param(
    [string]$Name,
    [int]$PreferredPort,
    [int]$ActualPort
  )

  if ($PreferredPort -ne $ActualPort) {
    Write-Host "$Name port $PreferredPort is busy; using $ActualPort instead."
  }
}

function Wait-HttpReady {
  param(
    [string]$Url,
    [string]$Name,
    [int]$TimeoutSeconds = 60
  )

  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    try {
      Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3 | Out-Null
      Write-Host "$Name is ready: $Url"
      return $true
    }
    catch {
      Start-Sleep -Seconds 2
    }
  }

  Write-Warning "$Name did not become ready within ${TimeoutSeconds}s. Check the service window for details: $Url"
  return $false
}

function Test-PathIsNewerThan {
  param(
    [string]$Path,
    [datetime]$ReferenceTime
  )

  if (-not (Test-Path $Path)) {
    return $false
  }

  $item = Get-Item -LiteralPath $Path
  if (-not $item.PSIsContainer) {
    return $item.LastWriteTime -gt $ReferenceTime
  }

  $newerChild = Get-ChildItem -LiteralPath $Path -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -gt $ReferenceTime } |
    Select-Object -First 1
  return $null -ne $newerChild
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

function Ensure-FrontendDependencies {
  $nodeModules = Join-Path $frontendDir "node_modules"
  $missingMermaid = -not (Test-FrontendPackageInstalled -PackageName "mermaid")

  if ((-not $SkipInstall) -or (-not (Test-Path $nodeModules)) -or $missingMermaid) {
    Push-Location $frontendDir
    try {
      Write-Host "Installing frontend dependencies..."
      npm install
    }
    finally {
      Pop-Location
    }
  }

  if (-not (Test-FrontendPackageInstalled -PackageName "mermaid")) {
    throw "Frontend dependency 'mermaid' is missing. Run 'npm install' in frontend/ or rerun without -SkipInstall."
  }
}

function Ensure-DesktopDependencies {
  if ($NoDesktop) {
    return $false
  }

  if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    Write-Warning "Rust/Cargo was not found. Skipping Memo Elf desktop window. Install Rust from https://rustup.rs/ and rerun without -NoDesktop."
    return $false
  }

  $nodeModules = Join-Path $desktopDir "node_modules"
  if ((-not $SkipInstall) -or (-not (Test-Path $nodeModules))) {
    Push-Location $desktopDir
    try {
      Write-Host "Installing desktop dependencies..."
      npm install
    }
    finally {
      Pop-Location
    }
  }

  return $true
}

function Ensure-FrontendDistForBackendApp {
  $indexHtml = Join-Path $frontendDir "dist\index.html"

  # The backend-hosted product entry uses frontend/dist through /app on the selected backend port.
  # Vite on 5173 is hot-reloaded, but /app on 8000 will stay stale unless dist is rebuilt.
  $shouldBuild = -not (Test-Path $indexHtml)
  $stalePath = $null

  if (-not $shouldBuild) {
    $distTime = (Get-Item -LiteralPath $indexHtml).LastWriteTime
    $watchPaths = @(
      (Join-Path $frontendDir "src"),
      (Join-Path $frontendDir "public"),
      (Join-Path $frontendDir "index.html"),
      (Join-Path $frontendDir "package.json"),
      (Join-Path $frontendDir "package-lock.json"),
      (Join-Path $frontendDir "vite.config.ts"),
      (Join-Path $frontendDir "tsconfig.json"),
      (Join-Path $frontendDir "tsconfig.app.json")
    )

    foreach ($path in $watchPaths) {
      if (Test-PathIsNewerThan -Path $path -ReferenceTime $distTime) {
        $shouldBuild = $true
        $stalePath = $path
        break
      }
    }
  }

  if (-not $shouldBuild) {
    return
  }

  if ($stalePath) {
    Write-Host "Frontend dist is stale because this path changed after the last build:"
    Write-Host "  $stalePath"
  } else {
    Write-Host "Frontend dist is missing."
  }

  Write-Host "Building frontend for backend-hosted /app entry..."
  Ensure-FrontendDependencies
  Push-Location $frontendDir
  try {
    npm run build
  }
  finally {
    Pop-Location
  }
}

Assert-NodeVersion
& $stopScript
Ensure-FrontendDependencies
Ensure-FrontendDistForBackendApp

$hostName = if ($env:AIMEMO_HOST) { $env:AIMEMO_HOST } else { "127.0.0.1" }
$preferredBackendPort = if ($env:AIMEMO_BACKEND_PORT) { [int]$env:AIMEMO_BACKEND_PORT } else { 8000 }
$preferredFrontendPort = if ($env:AIMEMO_FRONTEND_PORT) { [int]$env:AIMEMO_FRONTEND_PORT } else { 5173 }
$preferredDesktopPort = if ($env:AIMEMO_DESKTOP_PORT) { [int]$env:AIMEMO_DESKTOP_PORT } else { 1420 }
$backendPort = Find-AvailablePort -HostName $hostName -PreferredPort $preferredBackendPort
$frontendPort = Find-AvailablePort -HostName $hostName -PreferredPort $preferredFrontendPort
$desktopPort = Find-AvailablePort -HostName $hostName -PreferredPort $preferredDesktopPort
$desktopEnabled = Ensure-DesktopDependencies
$env:AIMEMO_HOST = $hostName
$env:AIMEMO_BACKEND_PORT = [string]$backendPort
$env:AIMEMO_FRONTEND_PORT = [string]$frontendPort
$env:AIMEMO_DESKTOP_PORT = [string]$desktopPort
$env:AIMEMO_BACKEND_URL = "http://${hostName}:$backendPort"
$env:VITE_API_BASE_URL = $env:AIMEMO_BACKEND_URL
$env:VITE_AIMEMO_BACKEND_URL = $env:AIMEMO_BACKEND_URL

Write-Host "Starting AiMemo backend, frontend, and Memo Elf..."
Write-PortFallback -Name "Backend" -PreferredPort $preferredBackendPort -ActualPort $backendPort
Write-PortFallback -Name "Frontend" -PreferredPort $preferredFrontendPort -ActualPort $frontendPort
if ($desktopEnabled) {
  Write-PortFallback -Name "Memo Elf webview" -PreferredPort $preferredDesktopPort -ActualPort $desktopPort
}
Write-Host "Backend:  http://${hostName}:$backendPort"
Write-Host "Frontend: http://${hostName}:$frontendPort/app/"
Write-Host "Product:  http://${hostName}:$backendPort/app/"
if ($desktopEnabled) {
  Write-Host "Memo Elf: Tauri desktop window"
} elseif (-not $NoDesktop) {
  Write-Host "Memo Elf: skipped because Rust/Cargo is not installed"
}

$backendArgs = @("-NoExit", "-ExecutionPolicy", "Bypass", "-File", $backendScript, "-HostName", $hostName, "-Port", $backendPort)
$frontendArgs = @("-NoExit", "-ExecutionPolicy", "Bypass", "-File", $frontendScript, "-HostName", $hostName, "-Port", $frontendPort, "-BackendPort", $backendPort)
if ($SkipInstall) {
  $backendArgs += "-SkipInstall"
  $frontendArgs += "-SkipInstall"
}

Start-Process powershell -ArgumentList $backendArgs -WorkingDirectory $repoRoot
Wait-HttpReady -Url "http://${hostName}:$backendPort/api/health" -Name "Backend" -TimeoutSeconds 75 | Out-Null
Start-Process powershell -ArgumentList $frontendArgs -WorkingDirectory $repoRoot
if ($desktopEnabled) {
  $desktopCommand = "npm run dev"
  Start-Sleep -Seconds 2
  Start-Process powershell -ArgumentList @("-NoExit", "-ExecutionPolicy", "Bypass", "-Command", $desktopCommand) -WorkingDirectory $desktopDir
}

Write-Host "Dev services were started in separate PowerShell windows."
