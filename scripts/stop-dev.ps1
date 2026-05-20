param(
  [switch]$KeepWindows
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$desktopExe = Join-Path $repoRoot "desktop\src-tauri\target\debug\memo-elf-desktop.exe"

function Stop-PortProcess {
  param(
    [int]$Port,
    [string]$Name
  )

  $processIds = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue |
    Where-Object { $_.OwningProcess -gt 0 } |
    Select-Object -ExpandProperty OwningProcess -Unique
  foreach ($processId in $processIds) {
    Stop-DevProcess -ProcessId $processId -Name "$Name on port $Port"
  }
}

function Stop-DevProcess {
  param(
    [int]$ProcessId,
    [string]$Name
  )

  try {
    $process = Get-Process -Id $ProcessId -ErrorAction Stop
    Write-Host "Stopping $Name (PID $ProcessId)..."
    Stop-Process -Id $ProcessId -Force
  } catch {
    # 进程可能已经退出。停止脚本应该幂等，静默跳过即可。
  }
}

function Stop-ProcessesByPath {
  param(
    [string]$Path,
    [string]$Name
  )

  $resolvedPath = [System.IO.Path]::GetFullPath($Path)
  Get-Process -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and ([System.IO.Path]::GetFullPath($_.Path) -eq $resolvedPath) } |
    ForEach-Object { Stop-DevProcess -ProcessId $_.Id -Name $Name }
}

function Stop-NodeProcessesInRepo {
  $escapedRoot = [regex]::Escape($repoRoot.Path)
  $nodeProcesses = Get-CimInstance Win32_Process -Filter "name = 'node.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match $escapedRoot }
  foreach ($process in $nodeProcesses) {
    Stop-DevProcess -ProcessId $process.ProcessId -Name "node dev process"
  }
}

function Stop-PowerShellDevWindows {
  if ($KeepWindows) {
    return
  }

  $escapedRoot = [regex]::Escape($repoRoot.Path)
  $windows = Get-CimInstance Win32_Process -Filter "name = 'powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object {
      $_.CommandLine -match $escapedRoot -and
      (
        $_.CommandLine -match "start-backend.ps1" -or
        $_.CommandLine -match "start-frontend.ps1" -or
        $_.CommandLine -match "npm run dev"
      )
    }
  foreach ($window in $windows) {
    Stop-DevProcess -ProcessId $window.ProcessId -Name "AiMemo dev PowerShell window"
  }
}

Stop-PortProcess -Port 8000 -Name "backend"
Stop-PortProcess -Port 5173 -Name "frontend"
Stop-PortProcess -Port 1420 -Name "desktop webview"
Stop-ProcessesByPath -Path $desktopExe -Name "Memo Elf desktop"
Stop-NodeProcessesInRepo
Stop-PowerShellDevWindows

Write-Host "AiMemo dev services stopped."
