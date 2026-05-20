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

& $stopScript

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
