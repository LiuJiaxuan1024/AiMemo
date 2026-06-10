[CmdletBinding()]
param(
  [switch]$DryRun,
  [switch]$NoPathUpdate,
  [switch]$Quiet
)

$ErrorActionPreference = "Stop"

$registerScript = Join-Path $PSScriptRoot "register-aimemo.ps1"
if (-not (Test-Path $registerScript)) {
  throw "Expected registration script not found: $registerScript"
}

& $registerScript -DryRun:$DryRun -NoPathUpdate:$NoPathUpdate -Quiet:$Quiet
if ($?) {
  exit 0
}
exit 1
