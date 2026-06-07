[CmdletBinding()]
param(
  [Parameter(Position = 0)]
  [string]$Command = "help",

  [switch]$Json,
  [switch]$Fix,
  [switch]$NonInteractive,
  [switch]$NoDesktop,
  [switch]$SkipInstall,
  [switch]$SkipDoctor,
  [switch]$KeepWindows,
  [switch]$DryRun,
  [switch]$NoPathUpdate,

  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$RemainingArgs = @()
)

$ErrorActionPreference = "Stop"


function Show-Help {
  Write-Host "Usage: aimemo <command> [args]"
  Write-Host ""
  Write-Host "Commands:"
  Write-Host "  doctor    Run environment diagnostics."
  Write-Host "  register  Register the global aimemo command."
  Write-Host "  install   Alias for register."
  Write-Host "  start     Start AiMemo dev services."
  Write-Host "  stop      Stop AiMemo dev services."
  Write-Host "  help      Show this help."
  Write-Host ""
  Write-Host "Planned commands:"
  Write-Host "  setup     Create minimal local config/directories. Not implemented yet."
  Write-Host "  onboard   Run first-use configuration. Not implemented yet."
}

function Resolve-Script {
  param([string]$ScriptName)
  $scriptPath = Join-Path $PSScriptRoot $ScriptName
  if (-not (Test-Path $scriptPath)) {
    throw "Expected script not found: $scriptPath"
  }
  return $scriptPath
}

$normalizedCommand = $Command.ToLowerInvariant()

switch ($normalizedCommand) {
  "doctor" {
    $forwardParams = @{}
    if ($Json) { $forwardParams.Json = $true }
    if ($Fix) { $forwardParams.Fix = $true }
    if ($NonInteractive) { $forwardParams.NonInteractive = $true }
    if ($NoDesktop) { $forwardParams.NoDesktop = $true }
    & (Resolve-Script "doctor.ps1") @forwardParams @RemainingArgs
    exit $LASTEXITCODE
  }
  "start" {
    $forwardParams = @{}
    if ($SkipInstall) { $forwardParams.SkipInstall = $true }
    if ($NoDesktop) { $forwardParams.NoDesktop = $true }
    if ($SkipDoctor) { $forwardParams.SkipDoctor = $true }
    & (Resolve-Script "start-dev.ps1") @forwardParams @RemainingArgs
    exit $LASTEXITCODE
  }
  "dev" {
    $forwardParams = @{}
    if ($SkipInstall) { $forwardParams.SkipInstall = $true }
    if ($NoDesktop) { $forwardParams.NoDesktop = $true }
    if ($SkipDoctor) { $forwardParams.SkipDoctor = $true }
    & (Resolve-Script "start-dev.ps1") @forwardParams @RemainingArgs
    exit $LASTEXITCODE
  }
  "stop" {
    $forwardParams = @{}
    if ($KeepWindows) { $forwardParams.KeepWindows = $true }
    & (Resolve-Script "stop-dev.ps1") @forwardParams @RemainingArgs
    exit $LASTEXITCODE
  }
  "help" {
    Show-Help
    exit 0
  }
  "-h" {
    Show-Help
    exit 0
  }
  "--help" {
    Show-Help
    exit 0
  }
  "register" {
    $forwardParams = @{}
    if ($DryRun) { $forwardParams.DryRun = $true }
    if ($NoPathUpdate) { $forwardParams.NoPathUpdate = $true }
    & (Resolve-Script "register-aimemo.ps1") @forwardParams @RemainingArgs
    exit $LASTEXITCODE
  }
  "install" {
    $forwardParams = @{}
    if ($DryRun) { $forwardParams.DryRun = $true }
    if ($NoPathUpdate) { $forwardParams.NoPathUpdate = $true }
    & (Resolve-Script "install.ps1") @forwardParams @RemainingArgs
    exit $LASTEXITCODE
  }
  "setup" {
    Write-Error "aimemo setup is planned but not implemented yet. Create .env from .env.example manually for now."
    exit 2
  }
  "onboard" {
    Write-Error "aimemo onboard is planned but not implemented yet. Configure .env manually for now."
    exit 2
  }
  default {
    Write-Error "Unknown aimemo command: $Command"
    Show-Help
    exit 2
  }
}
