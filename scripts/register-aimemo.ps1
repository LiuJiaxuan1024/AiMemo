[CmdletBinding()]
param(
  [switch]$DryRun,
  [switch]$NoPathUpdate,
  [switch]$Quiet,
  [switch]$Help
)

$ErrorActionPreference = "Stop"

function Show-RegisterHelp {
  Write-Host "Usage: .\scripts\register-aimemo.ps1 [-DryRun] [-NoPathUpdate] [-Quiet]"
  Write-Host ""
  Write-Host "Registers the global aimemo command for Windows."
  Write-Host ""
  Write-Host "What it does:"
  Write-Host "  1. Writes aimemo.cmd and aimemo.ps1 wrappers to `%LOCALAPPDATA%\AiMemo\bin."
  Write-Host "  2. Adds that bin directory to the user PATH unless -NoPathUpdate is set."
  Write-Host "  3. Lets new terminals run aimemo from any directory."
  Write-Host ""
  Write-Host "Options:"
  Write-Host "  -DryRun        Show planned changes without writing files."
  Write-Host "  -NoPathUpdate  Write wrappers but do not edit the user PATH."
  Write-Host "  -Quiet         Reduce output."
  Write-Host ""
  Write-Host "Recommended entry point:"
  Write-Host "  aimemo help register"
}

if ($Help) {
  Show-RegisterHelp
  exit 0
}

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$aimemoScript = Join-Path $PSScriptRoot "aimemo.ps1"

if (-not (Test-Path $aimemoScript)) {
  throw "Expected command router not found: $aimemoScript"
}

function Get-AiMemoBinDir {
  if ($env:LOCALAPPDATA) {
    return (Join-Path $env:LOCALAPPDATA "AiMemo\bin")
  }
  if ($env:USERPROFILE) {
    return (Join-Path $env:USERPROFILE ".aimemo\bin")
  }
  throw "Could not resolve a user-local bin directory."
}

function Split-PathList {
  param([string]$Value)
  if ([string]::IsNullOrWhiteSpace($Value)) {
    return @()
  }
  return @($Value -split ";" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
}

function Test-PathListContains {
  param(
    [string[]]$Entries,
    [string]$Target
  )

  $targetFullPath = [System.IO.Path]::GetFullPath($Target).TrimEnd("\")
  foreach ($entry in $Entries) {
    try {
      $entryFullPath = [System.IO.Path]::GetFullPath($entry).TrimEnd("\")
      if ([string]::Equals($entryFullPath, $targetFullPath, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $true
      }
    }
    catch {
      # Ignore malformed PATH entries owned by other tools.
    }
  }
  return $false
}

$binDir = Get-AiMemoBinDir
$cmdPath = Join-Path $binDir "aimemo.cmd"
$psWrapperPath = Join-Path $binDir "aimemo.ps1"
$escapedAimemoScript = $aimemoScript.Replace("'", "''")
$cmdContent = @"
@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0aimemo.ps1" %*
"@
$psWrapperContent = @"
`$ErrorActionPreference = "Stop"
`$target = '$escapedAimemoScript'
& `$target @args
exit `$LASTEXITCODE
"@

if (-not $Quiet) {
  Write-Host "AiMemo command registration"
  Write-Host "Repository: $repoRoot"
  Write-Host "Command router: $aimemoScript"
  Write-Host "Global wrapper: $cmdPath"
  Write-Host "PowerShell wrapper: $psWrapperPath"
}

if ($DryRun) {
  if (-not $Quiet) {
    Write-Host "[dry-run] Would create directory: $binDir"
    Write-Host "[dry-run] Would write wrapper: $cmdPath"
    Write-Host "[dry-run] Would write PowerShell wrapper: $psWrapperPath"
  }
} else {
  New-Item -ItemType Directory -Path $binDir -Force | Out-Null
  Set-Content -LiteralPath $cmdPath -Value $cmdContent -Encoding ASCII
  Set-Content -LiteralPath $psWrapperPath -Value $psWrapperContent -Encoding Unicode
  if (-not $Quiet) {
    Write-Host "[OK] Wrote $cmdPath"
    Write-Host "[OK] Wrote $psWrapperPath"
  }
}

if ($NoPathUpdate) {
  if (-not $Quiet) {
    Write-Host "[SKIP] PATH update skipped by -NoPathUpdate."
  }
} else {
  $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
  $pathEntries = Split-PathList -Value $userPath
  if (Test-PathListContains -Entries $pathEntries -Target $binDir) {
    if (-not (Test-PathListContains -Entries (Split-PathList -Value $env:Path) -Target $binDir)) {
      $env:Path = if ([string]::IsNullOrWhiteSpace($env:Path)) { $binDir } else { "$env:Path;$binDir" }
    }
    if (-not $Quiet) {
      Write-Host "[OK] AiMemo bin directory is already in the user PATH."
    }
  } elseif ($DryRun) {
    if (-not $Quiet) {
      Write-Host "[dry-run] Would add to user PATH: $binDir"
    }
  } else {
    $nextEntries = @($pathEntries + $binDir)
    $nextPath = ($nextEntries -join ";")
    [Environment]::SetEnvironmentVariable("Path", $nextPath, "User")
    if (-not (Test-PathListContains -Entries (Split-PathList -Value $env:Path) -Target $binDir)) {
      $env:Path = if ([string]::IsNullOrWhiteSpace($env:Path)) { $binDir } else { "$env:Path;$binDir" }
    }
    if (-not $Quiet) {
      Write-Host "[OK] Added AiMemo bin directory to the user PATH."
      Write-Host "Restart terminals that were already open before running this registration."
    }
  }
}

if (-not $Quiet) {
  Write-Host ""
  Write-Host "Try:"
  Write-Host "  aimemo doctor"
  Write-Host "  aimemo start -NoDesktop"
}
