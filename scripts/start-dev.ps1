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

  # The backend-hosted product entry uses frontend/dist through http://127.0.0.1:8000/app.
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

Write-Host "Starting AiMemo backend, frontend, and Memo Elf..."
Write-Host "Backend:  http://127.0.0.1:8000"
Write-Host "Frontend: http://127.0.0.1:5173/app/"
if (-not $NoDesktop) {
  Write-Host "Memo Elf: Tauri desktop window"
}

$backendArgs = @("-NoExit", "-ExecutionPolicy", "Bypass", "-File", $backendScript)
$frontendArgs = @("-NoExit", "-ExecutionPolicy", "Bypass", "-File", $frontendScript)
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
