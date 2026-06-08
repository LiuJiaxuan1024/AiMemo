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
  [switch]$SeparateWindows,
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
  Write-Host "  restart   Stop and then start AiMemo dev services."
  Write-Host "  stop      Stop AiMemo dev services."
  Write-Host "  help      Show this help, or help for one command."
  Write-Host ""
  Write-Host "Planned commands:"
  Write-Host "  setup     Create minimal local config/directories. Not implemented yet."
  Write-Host "  onboard   Run first-use configuration. Not implemented yet."
  Write-Host ""
  Write-Host "Examples:"
  Write-Host "  aimemo doctor"
  Write-Host "  aimemo start"
  Write-Host "  aimemo restart -NoDesktop"
  Write-Host "  aimemo help register"
}

function Show-CommandHelp {
  param([string]$Topic)

  switch ($Topic.ToLowerInvariant()) {
    "doctor" {
      Write-Host "Usage: aimemo doctor [-Json] [-Fix] [-NonInteractive] [-NoDesktop]"
      Write-Host ""
      Write-Host "Runs environment diagnostics for the current AiMemo checkout."
      Write-Host ""
      Write-Host "Options:"
      Write-Host "  -Json            Print machine-readable JSON."
      Write-Host "  -Fix             Apply safe automatic fixes when supported."
      Write-Host "  -NonInteractive  Do not prompt for user input."
      Write-Host "  -NoDesktop       Skip desktop/Rust checks."
    }
    "register" {
      Write-Host "Usage: aimemo register [-DryRun] [-NoPathUpdate]"
      Write-Host ""
      Write-Host "Registers the global aimemo command for this checkout."
      Write-Host ""
      Write-Host "What it does on Windows:"
      Write-Host "  1. Writes user-local wrappers to `%LOCALAPPDATA%\AiMemo\bin."
      Write-Host "  2. Adds that bin directory to the user PATH unless -NoPathUpdate is set."
      Write-Host "  3. Lets new terminals run aimemo from any directory."
      Write-Host ""
      Write-Host "Options:"
      Write-Host "  -DryRun        Show the planned changes without writing files."
      Write-Host "  -NoPathUpdate  Write wrappers but do not edit the user PATH."
      Write-Host ""
      Write-Host "After registration:"
      Write-Host "  aimemo doctor"
      Write-Host "  aimemo start"
      Write-Host "  aimemo restart"
      Write-Host "  aimemo stop"
    }
    "install" {
      Show-CommandHelp "register"
    }
    "start" {
      Write-Host "Usage: aimemo start [-SkipInstall] [-SkipDoctor] [-NoDesktop] [-SeparateWindows]"
      Write-Host ""
      Write-Host "Starts AiMemo development services. If this checkout is already running,"
      Write-Host "start exits with a reminder to use aimemo restart."
      Write-Host ""
      Write-Host "Options:"
      Write-Host "  -SkipInstall  Skip dependency installation checks."
      Write-Host "  -SkipDoctor   Skip the doctor preflight."
      Write-Host "  -NoDesktop    Start backend/frontend only."
      Write-Host "  -SeparateWindows  Show backend/frontend/desktop service consoles for debugging."
    }
    "dev" {
      Show-CommandHelp "start"
    }
    "restart" {
      Write-Host "Usage: aimemo restart [-KeepWindows] [-SkipInstall] [-SkipDoctor] [-NoDesktop] [-SeparateWindows]"
      Write-Host ""
      Write-Host "Stops AiMemo development services for this checkout, then starts them again."
      Write-Host ""
      Write-Host "Options:"
      Write-Host "  -KeepWindows  Leave terminal windows open when stopping."
      Write-Host "  -SkipInstall  Skip dependency installation checks during start."
      Write-Host "  -SkipDoctor   Skip the doctor preflight during start."
      Write-Host "  -NoDesktop    Start backend/frontend only."
      Write-Host "  -SeparateWindows  Show backend/frontend/desktop service consoles for debugging."
    }
    "stop" {
      Write-Host "Usage: aimemo stop [-KeepWindows]"
      Write-Host ""
      Write-Host "Stops AiMemo development services for this checkout."
      Write-Host ""
      Write-Host "Options:"
      Write-Host "  -KeepWindows  Leave terminal windows open after stopping processes."
    }
    default {
      Write-Error "Unknown help topic: $Topic"
      Show-Help
      exit 2
    }
  }
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
    if (($RemainingArgs -contains "--help") -or ($RemainingArgs -contains "-h") -or ($RemainingArgs -contains "help")) {
      Show-CommandHelp "doctor"
      exit 0
    }
    $forwardParams = @{}
    if ($Json) { $forwardParams.Json = $true }
    if ($Fix) { $forwardParams.Fix = $true }
    if ($NonInteractive) { $forwardParams.NonInteractive = $true }
    if ($NoDesktop) { $forwardParams.NoDesktop = $true }
    & (Resolve-Script "doctor.ps1") @forwardParams @RemainingArgs
    exit $LASTEXITCODE
  }
  "start" {
    if (($RemainingArgs -contains "--help") -or ($RemainingArgs -contains "-h") -or ($RemainingArgs -contains "help")) {
      Show-CommandHelp "start"
      exit 0
    }
    $forwardParams = @{}
    if ($SkipInstall) { $forwardParams.SkipInstall = $true }
    if ($NoDesktop) { $forwardParams.NoDesktop = $true }
    if ($SkipDoctor) { $forwardParams.SkipDoctor = $true }
    if ($SeparateWindows) { $forwardParams.SeparateWindows = $true }
    & (Resolve-Script "start-dev.ps1") @forwardParams @RemainingArgs
    exit $LASTEXITCODE
  }
  "dev" {
    if (($RemainingArgs -contains "--help") -or ($RemainingArgs -contains "-h") -or ($RemainingArgs -contains "help")) {
      Show-CommandHelp "start"
      exit 0
    }
    $forwardParams = @{}
    if ($SkipInstall) { $forwardParams.SkipInstall = $true }
    if ($NoDesktop) { $forwardParams.NoDesktop = $true }
    if ($SkipDoctor) { $forwardParams.SkipDoctor = $true }
    if ($SeparateWindows) { $forwardParams.SeparateWindows = $true }
    & (Resolve-Script "start-dev.ps1") @forwardParams @RemainingArgs
    exit $LASTEXITCODE
  }
  "restart" {
    if (($RemainingArgs -contains "--help") -or ($RemainingArgs -contains "-h") -or ($RemainingArgs -contains "help")) {
      Show-CommandHelp "restart"
      exit 0
    }
    $stopParams = @{}
    if ($KeepWindows) { $stopParams.KeepWindows = $true }
    & (Resolve-Script "stop-dev.ps1") @stopParams
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    $forwardParams = @{}
    if ($SkipInstall) { $forwardParams.SkipInstall = $true }
    if ($NoDesktop) { $forwardParams.NoDesktop = $true }
    if ($SkipDoctor) { $forwardParams.SkipDoctor = $true }
    if ($SeparateWindows) { $forwardParams.SeparateWindows = $true }
    & (Resolve-Script "start-dev.ps1") @forwardParams @RemainingArgs
    exit $LASTEXITCODE
  }
  "stop" {
    if (($RemainingArgs -contains "--help") -or ($RemainingArgs -contains "-h") -or ($RemainingArgs -contains "help")) {
      Show-CommandHelp "stop"
      exit 0
    }
    $forwardParams = @{}
    if ($KeepWindows) { $forwardParams.KeepWindows = $true }
    & (Resolve-Script "stop-dev.ps1") @forwardParams @RemainingArgs
    exit $LASTEXITCODE
  }
  "help" {
    if ($RemainingArgs.Count -gt 0) {
      Show-CommandHelp $RemainingArgs[0]
    } else {
      Show-Help
    }
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
    if (($RemainingArgs -contains "--help") -or ($RemainingArgs -contains "-h") -or ($RemainingArgs -contains "help")) {
      Show-CommandHelp "register"
      exit 0
    }
    $forwardParams = @{}
    if ($DryRun) { $forwardParams.DryRun = $true }
    if ($NoPathUpdate) { $forwardParams.NoPathUpdate = $true }
    & (Resolve-Script "register-aimemo.ps1") @forwardParams @RemainingArgs
    exit $LASTEXITCODE
  }
  "install" {
    if (($RemainingArgs -contains "--help") -or ($RemainingArgs -contains "-h") -or ($RemainingArgs -contains "help")) {
      Show-CommandHelp "install"
      exit 0
    }
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
