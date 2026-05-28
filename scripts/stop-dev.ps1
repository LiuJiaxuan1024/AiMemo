param(
  [switch]$KeepWindows
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$desktopExe = Join-Path $repoRoot "desktop\src-tauri\target\debug\memo-elf-desktop.exe"

function Stop-DevProcess {
  param(
    [int]$ProcessId,
    [string]$Name
  )

  try {
    $null = Get-Process -Id $ProcessId -ErrorAction Stop
    Write-Host "Stopping $Name (PID $ProcessId)..."
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
  }
  catch {
    # Process may have already exited. Keep this script idempotent.
  }
}

function Stop-DevProcessTree {
  param(
    [int]$ProcessId,
    [string]$Name
  )

  try {
    $null = Get-Process -Id $ProcessId -ErrorAction Stop
    Write-Host "Stopping $Name process tree (PID $ProcessId)..."
    & taskkill.exe /PID $ProcessId /T /F | Out-Null
  }
  catch {
    # Process tree may have already exited. Keep this script idempotent.
  }
}

function Stop-PortProcess {
  param(
    [int]$Port,
    [string]$Name
  )

  $connections = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
  $processIds = @()
  foreach ($connection in $connections) {
    if ($connection.OwningProcess -gt 0 -and $processIds -notcontains $connection.OwningProcess) {
      $processIds += $connection.OwningProcess
    }
  }
  foreach ($processId in $processIds) {
    Stop-DevProcess -ProcessId $processId -Name "$Name on port $Port"
  }
}

function Stop-ProcessesByPath {
  param(
    [string]$Path,
    [string]$Name
  )

  $resolvedPath = [System.IO.Path]::GetFullPath($Path)
  $processes = Get-Process -ErrorAction SilentlyContinue
  foreach ($process in $processes) {
    if (-not $process.Path) {
      continue
    }
    if ([System.IO.Path]::GetFullPath($process.Path) -eq $resolvedPath) {
      Stop-DevProcess -ProcessId $process.Id -Name $Name
    }
  }
}

function Stop-NodeProcessesInRepo {
  $escapedRoot = [regex]::Escape($repoRoot.Path)
  $nodeProcesses = Get-CimInstance Win32_Process -Filter "name = 'node.exe'" -ErrorAction SilentlyContinue
  foreach ($process in $nodeProcesses) {
    if ($process.CommandLine -match $escapedRoot) {
      Stop-DevProcess -ProcessId $process.ProcessId -Name "node dev process"
    }
  }
}

function Stop-PowerShellDevWindows {
  if ($KeepWindows) {
    return
  }

  $escapedRoot = [regex]::Escape($repoRoot.Path)
  $processes = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue
  foreach ($process in $processes) {
    if ($process.Name -ne "powershell.exe" -and $process.Name -ne "pwsh.exe") {
      continue
    }
    if ($process.CommandLine -notmatch $escapedRoot) {
      continue
    }

    $isDevWindow =
      $process.CommandLine -match "start-backend.ps1" -or
      $process.CommandLine -match "start-frontend.ps1" -or
      $process.CommandLine -match "npm run dev" -or
      $process.CommandLine -match "tauri dev"

    if ($isDevWindow) {
      Stop-DevProcessTree -ProcessId $process.ProcessId -Name "AiMemo dev PowerShell window"
    }
  }
}

function Stop-DesktopDevProcessTree {
  $escapedRoot = [regex]::Escape($repoRoot.Path)
  $processes = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
  $processById = @{}
  foreach ($process in $processes) {
    $processById[[int]$process.ProcessId] = $process
  }

  $desktopLeafProcesses = @()
  foreach ($process in $processes) {
    $commandLine = [string]$process.CommandLine
    $isDesktopLeaf =
      $commandLine -match $escapedRoot -and
      (
        $commandLine -match "tauri\.js.* dev" -or
        $commandLine -match "cargo.*run --no-default-features" -or
        $commandLine -match "memo-elf-desktop\.exe" -or
        $commandLine -match "desktop\\node_modules.*vite"
      )

    if ($isDesktopLeaf) {
      $desktopLeafProcesses += $process
    }
  }

  $stoppedRoots = @()
  foreach ($leaf in $desktopLeafProcesses) {
    $current = $leaf
    while ($current -and $processById.ContainsKey([int]$current.ParentProcessId)) {
      $parent = $processById[[int]$current.ParentProcessId]
      if (
        ($parent.Name -eq "powershell.exe" -or $parent.Name -eq "pwsh.exe") -and
        $parent.ProcessId -ne $PID
      ) {
        if ($stoppedRoots -notcontains $parent.ProcessId) {
          Stop-DevProcessTree -ProcessId $parent.ProcessId -Name "Memo Elf desktop dev window"
          $stoppedRoots += $parent.ProcessId
        }
        break
      }
      $current = $parent
    }
  }
}

# Stop dev PowerShell windows first so Tauri dev, Vite, and uvicorn exit as one process tree.
# This avoids killing the desktop exe first and leaving Tauri dev to print a child-process error.
Stop-PowerShellDevWindows
Stop-DesktopDevProcessTree
Start-Sleep -Milliseconds 300

# Fallback cleanup for manually started or previously crashed AiMemo processes.
Stop-ProcessesByPath -Path $desktopExe -Name "Memo Elf desktop"
Stop-NodeProcessesInRepo

Write-Host "AiMemo dev services stopped."
