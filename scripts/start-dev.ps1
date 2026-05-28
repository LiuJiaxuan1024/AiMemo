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

function Ensure-FrontendDistForBackendApp {
  $indexHtml = Join-Path $frontendDir "dist\index.html"
  $nodeModules = Join-Path $frontendDir "node_modules"

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
  Push-Location $frontendDir
  try {
    if ((-not $SkipInstall) -or (-not (Test-Path $nodeModules))) {
      npm install
    }
    npm run build
  }
  finally {
    Pop-Location
  }
}

& $stopScript
Ensure-FrontendDistForBackendApp

$hostName = if ($env:AIMEMO_HOST) { $env:AIMEMO_HOST } else { "127.0.0.1" }
$preferredBackendPort = if ($env:AIMEMO_BACKEND_PORT) { [int]$env:AIMEMO_BACKEND_PORT } else { 8000 }
$preferredFrontendPort = if ($env:AIMEMO_FRONTEND_PORT) { [int]$env:AIMEMO_FRONTEND_PORT } else { 5173 }
$preferredDesktopPort = if ($env:AIMEMO_DESKTOP_PORT) { [int]$env:AIMEMO_DESKTOP_PORT } else { 1420 }
$backendPort = Find-AvailablePort -HostName $hostName -PreferredPort $preferredBackendPort
$frontendPort = Find-AvailablePort -HostName $hostName -PreferredPort $preferredFrontendPort
$desktopPort = Find-AvailablePort -HostName $hostName -PreferredPort $preferredDesktopPort
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
if (-not $NoDesktop) {
  Write-PortFallback -Name "Memo Elf webview" -PreferredPort $preferredDesktopPort -ActualPort $desktopPort
}
Write-Host "Backend:  http://${hostName}:$backendPort"
Write-Host "Frontend: http://${hostName}:$frontendPort/app/"
Write-Host "Product:  http://${hostName}:$backendPort/app/"
if (-not $NoDesktop) {
  Write-Host "Memo Elf: Tauri desktop window"
}

$backendArgs = @("-NoExit", "-ExecutionPolicy", "Bypass", "-File", $backendScript, "-HostName", $hostName, "-Port", $backendPort)
$frontendArgs = @("-NoExit", "-ExecutionPolicy", "Bypass", "-File", $frontendScript, "-HostName", $hostName, "-Port", $frontendPort, "-BackendPort", $backendPort)
if ($SkipInstall) {
  $backendArgs += "-SkipInstall"
  $frontendArgs += "-SkipInstall"
}

Start-Process powershell -ArgumentList $backendArgs -WorkingDirectory $repoRoot
Start-Sleep -Seconds 2
Start-Process powershell -ArgumentList $frontendArgs -WorkingDirectory $repoRoot
if (-not $NoDesktop) {
  $desktopCommand = if ($SkipInstall) { "npm run dev" } else { "npm install; npm run dev" }
  Start-Sleep -Seconds 2
  Start-Process powershell -ArgumentList @("-NoExit", "-ExecutionPolicy", "Bypass", "-Command", $desktopCommand) -WorkingDirectory $desktopDir
}

Write-Host "Dev services were started in separate PowerShell windows."
