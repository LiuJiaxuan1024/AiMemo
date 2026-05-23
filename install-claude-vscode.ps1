[CmdletBinding()]
param(
  [Parameter(Mandatory=$false)][string]$Token,
  [Parameter(Mandatory=$false)][string]$BaseUrl,
  [switch]$Yes
)

$script:AutoApprove = $Yes.IsPresent
$script:IsWindows = $false
$script:NodeMirror = 'https://npmmirror.com/mirrors/node/'
$script:NpmMirror = 'https://npmmirror.com/mirrors/npm/'

function Read-Token {
  param([string]$Prompt)
  $sec = Read-Host -AsSecureString -Prompt $Prompt
  $cred = New-Object System.Net.NetworkCredential("", $sec)
  return $cred.Password
}

function Assert-AdminAndChdir {
  # Require elevated PowerShell and switch to the user's home directory
  $isAdmin = $false
  try {
    $wi = [Security.Principal.WindowsIdentity]::GetCurrent()
    $wp = New-Object Security.Principal.WindowsPrincipal($wi)
    $isAdmin = $wp.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
  } catch { $isAdmin = $false }
  if (-not $isAdmin) {
    Write-Error "This installer must be run as Administrator. Right-click PowerShell and choose 'Run as administrator'."; exit 95
  }
  $userHome = $env:USERPROFILE
  if ([string]::IsNullOrWhiteSpace($userHome)) { $userHome = $HOME }
  if ($userHome -and (Test-Path $userHome)) {
    try { Set-Location -Path $userHome -ErrorAction SilentlyContinue } catch { }
  }
}

function Test-Command {
  param([string]$Name)
  try {
    Get-Command $Name -ErrorAction Stop | Out-Null
    return $true
  } catch {
    return $false
  }
}

function Invoke-WingetInstall {
  param(
    [string]$PackageId,
    [string[]]$AdditionalArgs = @()
  )
  $args = @('install','-e','--id',$PackageId,'--accept-package-agreements','--accept-source-agreements','--scope','user') + $AdditionalArgs
  & winget @args
  return $LASTEXITCODE
}

function Get-NodeVersion {
  # Try node on PATH first
  try { $raw = node -v 2>$null } catch { $raw = $null }
  if (-not $raw) {
    # Fallback to explicit node.exe path detection
    $nodePath = $null
    try {
      $cmd = Get-Command node -ErrorAction SilentlyContinue
      if ($cmd -and $cmd.Source) { $nodePath = $cmd.Source }
    } catch { $nodePath = $null }
    if (-not $nodePath -or -not (Test-Path $nodePath)) {
      $probables = @()
      $prog = [System.Environment]::GetFolderPath('ProgramFiles')
      if ($prog) { $probables += (Join-Path $prog 'nodejs\node.exe') }
      $progx86 = ${env:ProgramFiles(x86)}
      if ($progx86) { $probables += (Join-Path $progx86 'nodejs\node.exe') }
      $localApp = $env:LOCALAPPDATA
      if ($localApp) { $probables += (Join-Path $localApp 'Programs\nodejs\node.exe') }
      foreach ($p in $probables) { if ($p -and (Test-Path $p)) { $nodePath = $p; break } }
    }
    if (-not $nodePath) {
      try {
        $paths = (& where.exe node 2>$null)
        if ($paths) { foreach ($p in @($paths)) { if ($p -and (Test-Path $p)) { $nodePath = $p; break } } }
      } catch { $nodePath = $null }
    }
    if ($nodePath) {
      try { $dir = Split-Path -Parent $nodePath; Add-PathIfMissing $dir } catch { }
      try { $raw = (& $nodePath -v 2>$null) } catch { $raw = $null }
    }
  }
  if (-not $raw) { return $null }
  $match = [regex]::Match($raw.Trim(), '^v(\d+)\.(\d+)\.(\d+)$')
  if ($match.Success) {
    return [PSCustomObject]@{
      Raw = $raw.Trim()
      Major = [int]$match.Groups[1].Value
      Minor = [int]$match.Groups[2].Value
      Patch = [int]$match.Groups[3].Value
      Parsed = $true
    }
  }
  return [PSCustomObject]@{
    Raw = $raw.Trim()
    Major = 0
    Minor = 0
    Patch = 0
    Parsed = $false
  }
}

function Parse-NodeVersionString {
  param([string]$Raw)
  if (-not $Raw) { return $null }
  $m = [regex]::Match($Raw.Trim(), '^v(\d+)\.(\d+)\.(\d+)$')
  if (-not $m.Success) { return $null }
  return [PSCustomObject]@{
    Raw = 'v{0}.{1}.{2}' -f $m.Groups[1].Value,$m.Groups[2].Value,$m.Groups[3].Value
    Major = [int]$m.Groups[1].Value
    Minor = [int]$m.Groups[2].Value
    Patch = [int]$m.Groups[3].Value
    Parsed = $true
  }
}

function Add-PathIfMissing {
  param([string]$Dir)
  if (-not $Dir) { return }
  if (-not (Test-Path $Dir)) { return }
  $separator = [IO.Path]::PathSeparator
  $paths = $env:PATH -split [regex]::Escape($separator)
  if (-not ($paths | Where-Object { $_.Trim() -ieq $Dir })) {
    $env:PATH = "$Dir$separator$($env:PATH)"
  }
}

function Refresh-PathFromRegistry {
  # Refresh current process PATH from User and Machine so newly installed apps become visible
  try {
    $userPath = [Environment]::GetEnvironmentVariable('Path','User')
  } catch { $userPath = $null }
  try {
    $machinePath = [Environment]::GetEnvironmentVariable('Path','Machine')
  } catch { $machinePath = $null }
  $combined = @()
  if ($userPath) { $combined += $userPath }
  if ($machinePath) { $combined += $machinePath }
  if ($combined.Count -gt 0) {
    $env:Path = ($combined -join [IO.Path]::PathSeparator)
  }
}

function Get-EnvCascade {
  param([string]$Name)
  $value = [Environment]::GetEnvironmentVariable($Name, 'Process')
  if (-not [string]::IsNullOrWhiteSpace($value)) { return $value }
  $value = [Environment]::GetEnvironmentVariable($Name, 'User')
  if (-not [string]::IsNullOrWhiteSpace($value)) { return $value }
  $value = [Environment]::GetEnvironmentVariable($Name, 'Machine')
  if (-not [string]::IsNullOrWhiteSpace($value)) { return $value }
  return $null
}

function Get-NvmSettingsPath {
  $nvmHome = Get-EnvCascade 'NVM_HOME'
  if (-not $nvmHome) {
    if ($env:APPDATA) {
      $nvmHome = Join-Path $env:APPDATA 'nvm'
    } else {
      $nvmHome = Join-Path $HOME 'AppData\\Roaming\\nvm'
    }
  }
  return Join-Path $nvmHome 'settings.txt'
}

function Set-NvmSettingValue {
  param(
    [string]$SettingsPath,
    [string]$Key,
    [string]$Value
  )
  if (-not (Test-Path $SettingsPath)) { return }
  $lines = Get-Content $SettingsPath -ErrorAction SilentlyContinue
  $pattern = "^(?i)$([regex]::Escape($Key))\s*:"
  $updated = $false
  $result = foreach ($line in $lines) {
    if ($line -match $pattern) {
      $updated = $true
      "${Key}: $Value"
    } else {
      $line
    }
  }
  if (-not $updated) {
    $result += "${Key}: $Value"
  }
  Set-Content -Path $SettingsPath -Value $result -Encoding ascii
}

function Ensure-NvmSettings {
  $settingsPath = Get-NvmSettingsPath
  $nvmHome = Split-Path $settingsPath -Parent
  if (-not (Test-Path $nvmHome)) {
    New-Item -ItemType Directory -Path $nvmHome -Force | Out-Null
  }
  if (-not (Test-Path $settingsPath)) {
    $defaultSymlink = Get-EnvCascade 'NVM_SYMLINK'
    if (-not $defaultSymlink) {
      $programFiles = [System.Environment]::GetFolderPath('ProgramFiles')
      if ([string]::IsNullOrWhiteSpace($programFiles)) {
        $programFiles = "${env:SystemDrive}\\Program Files"
      }
      $defaultSymlink = Join-Path $programFiles 'nodejs'
    }
    if (-not (Test-Path $defaultSymlink)) {
      New-Item -ItemType Directory -Path $defaultSymlink -Force | Out-Null
    }
    $content = @(
      "root: $nvmHome",
      "path: $defaultSymlink",
      "arch: 64",
      "proxy: none",
      "node_mirror: $($script:NodeMirror)",
      "npm_mirror: $($script:NpmMirror)"
    )
    Set-Content -Path $settingsPath -Value $content -Encoding ascii
  } else {
    Set-NvmSettingValue -SettingsPath $settingsPath -Key 'node_mirror' -Value $script:NodeMirror
    Set-NvmSettingValue -SettingsPath $settingsPath -Key 'npm_mirror' -Value $script:NpmMirror
  }
  return $settingsPath
}

function Load-NvmSettings {
  $settingsPath = Ensure-NvmSettings
  $settings = @{}
  foreach ($line in Get-Content $settingsPath -ErrorAction SilentlyContinue) {
    if ($line -match '^(?<key>[^:]+):\s*(?<value>.*)$') {
      $settings[$Matches['key'].Trim().ToLower()] = $Matches['value'].Trim()
    }
  }
  if (-not $settings.ContainsKey('root')) {
    $settings['root'] = Split-Path $settingsPath -Parent
  }
  if (-not $settings.ContainsKey('path')) {
    $settings['path'] = Join-Path ([System.Environment]::GetFolderPath('ProgramFiles')) 'nodejs'
  }
  return $settings
}

function Get-NvmExecutable {
  $candidates = @()
  if ($env:NVM_HOME) { $candidates += (Join-Path $env:NVM_HOME 'nvm.exe') }
  if ($env:ProgramFiles) {
    $candidates += (Join-Path $env:ProgramFiles 'nodejs\\nvm\\nvm.exe')
    $candidates += (Join-Path $env:ProgramFiles 'nvm\\nvm.exe')
  }
  $programFilesX86 = ${env:ProgramFiles(x86)}
  if ($programFilesX86) {
    $candidates += (Join-Path $programFilesX86 'nodejs\\nvm\\nvm.exe')
    $candidates += (Join-Path $programFilesX86 'nvm\\nvm.exe')
  }
  if ($env:APPDATA) { $candidates += (Join-Path $env:APPDATA 'nvm\\nvm.exe') }
  if ($env:LOCALAPPDATA) { $candidates += (Join-Path $env:LOCALAPPDATA 'nvm\\nvm.exe') }
  foreach ($candidate in $candidates) {
    if ($candidate -and (Test-Path $candidate)) { return $candidate }
  }
  return $null
}

function Ensure-Nvm {
  $nvmExe = $null
  if (Test-Command 'nvm') {
    $cmd = Get-Command nvm -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source) { $nvmExe = $cmd.Source }
  }
  if (-not $nvmExe) {
    $nvmExe = Get-NvmExecutable
  }
  if ($nvmExe) {
    Ensure-NvmSettings | Out-Null
    return $nvmExe
  }
  if (-not (Test-Command 'winget')) {
    Write-Error "winget not available. Install NVM for Windows manually (https://github.com/coreybutler/nvm-windows) or install Node.js >= 18 yourself.";
    return $null
  }
  Write-Host "Installing NVM for Windows via winget (CoreyButler.NVMforWindows)..." -ForegroundColor Yellow
  $exitCode = Invoke-WingetInstall -PackageId 'CoreyButler.NVMforWindows'
  if ($exitCode -ne 0) {
    Write-Host "winget install returned exit code $exitCode; retrying with output..." -ForegroundColor Yellow
    & winget install -e --id CoreyButler.NVMforWindows --accept-package-agreements --accept-source-agreements
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
      Write-Error "Failed to install NVM for Windows (winget exit code $exitCode). Install it manually and re-run.";
      return $null
    }
  }
  $nvmExe = Get-NvmExecutable
  if (-not $nvmExe) {
    Write-Error "NVM installation completed but nvm.exe was not found. Restart PowerShell or install manually.";
    return $null
  }
  Ensure-NvmSettings | Out-Null
  return $nvmExe
}

function Prepare-NvmEnvironment {
  param([string]$NvmExe)
  if (-not $NvmExe) { return }
  $nvmExeDir = Split-Path -Parent $NvmExe
  if ($nvmExeDir) { Add-PathIfMissing $nvmExeDir }

  $settings = Load-NvmSettings
  $nvmHome = $settings['root']
  $symlink = $settings['path']

  if (-not (Test-Path $nvmHome)) {
    New-Item -ItemType Directory -Path $nvmHome -Force | Out-Null
  }
  if (-not (Test-Path $symlink)) {
    New-Item -ItemType Directory -Path $symlink -Force | Out-Null
  }

  $env:NVM_HOME = $nvmHome
  [Environment]::SetEnvironmentVariable('NVM_HOME', $env:NVM_HOME, 'User')
  Add-PathIfMissing $env:NVM_HOME

  $env:NVM_SYMLINK = $symlink
  [Environment]::SetEnvironmentVariable('NVM_SYMLINK', $env:NVM_SYMLINK, 'User')
  Add-PathIfMissing $env:NVM_SYMLINK

  $env:NVM_NODEJS_ORG_MIRROR = $script:NodeMirror
  [Environment]::SetEnvironmentVariable('NVM_NODEJS_ORG_MIRROR', $env:NVM_NODEJS_ORG_MIRROR, 'User')
  $env:NVM_NPM_MIRROR = $script:NpmMirror
  [Environment]::SetEnvironmentVariable('NVM_NPM_MIRROR', $env:NVM_NPM_MIRROR, 'User')
}

function Install-NodeViaMsi {
  param(
    [string]$Url = 'https://nodejs.org/dist/v24.11.0/node-v24.11.0-x64.msi'
  )
  if (-not $script:IsWindows) {
    Write-Error "Automated Node.js installation is only supported on Windows using MSI. Install Node.js >= 18 manually from https://nodejs.org/.";
    return $false
  }
  $tempDir = [System.IO.Path]::GetTempPath()
  $fileName = Split-Path -Leaf $Url
  $msiPath = Join-Path $tempDir $fileName
  try { if (Test-Path $msiPath) { Remove-Item -Path $msiPath -Force -ErrorAction SilentlyContinue } } catch { }
  Write-Host "Downloading Node.js MSI from $Url ..." -ForegroundColor Yellow
  try {
    Invoke-WebRequest -Uri $Url -OutFile $msiPath -UseBasicParsing -ErrorAction Stop
  } catch {
    Write-Error "Failed to download Node.js MSI: $($_.Exception.Message)";
    return $false
  }
  Write-Host "Installing Node.js from $msiPath ..." -ForegroundColor Yellow
  $msiexec = 'msiexec.exe'
  # Try per-machine install first (ALLUSERS=1). If it fails, attempt per-user install.
  $machineArgs = ('/i "{0}" /qn /norestart ALLUSERS=1' -f $msiPath)
  try {
    $p = Start-Process -FilePath $msiexec -ArgumentList $machineArgs -Wait -PassThru -WindowStyle Hidden
    $exitCode = $p.ExitCode
  } catch { $exitCode = $null }
  if ($exitCode -ne 0 -and $exitCode -ne 3010) {
    Write-Host "msiexec exited with code $exitCode (per-machine). Falling back to per-user install..." -ForegroundColor Yellow
    $userArgs = ('/i "{0}" /qn /norestart' -f $msiPath)
    try {
      $p2 = Start-Process -FilePath $msiexec -ArgumentList $userArgs -Wait -PassThru -WindowStyle Hidden
      $exitCode2 = $p2.ExitCode
    } catch { $exitCode2 = $null }
    if ($exitCode2 -ne 0 -and $exitCode2 -ne 3010) {
      Write-Host "msiexec exited with code $exitCode2 (per-user). Will verify installation before failing." -ForegroundColor Yellow
    } else {
      $exitCode = 0
    }
  }
  # Try to locate the installed node.exe in common locations and update PATH in current session
  Refresh-PathFromRegistry
  $candidates = @()
  $prog = [System.Environment]::GetFolderPath('ProgramFiles')
  if ($prog) { $candidates += (Join-Path $prog 'nodejs') }
  $progx86 = ${env:ProgramFiles(x86)}
  if ($progx86) { $candidates += (Join-Path $progx86 'nodejs') }
  $localApp = $env:LOCALAPPDATA
  if ($localApp) { $candidates += (Join-Path $localApp 'Programs\nodejs') }
  foreach ($dir in $candidates) {
    if ($dir -and (Test-Path (Join-Path $dir 'node.exe'))) {
      Add-PathIfMissing $dir
    }
  }
  Start-Sleep -Seconds 2
  # Treat as success if Node is now visible or node.exe is found in common locations
  if (Test-Command 'node') { return $true }
  foreach ($dir in $candidates) {
    if ($dir -and (Test-Path (Join-Path $dir 'node.exe'))) { return $true }
  }
  return $false
}
function Install-NodeViaWinget {
  if (-not $script:IsWindows) {
    Write-Error "Automated Node.js installation is only supported on Windows via winget. Install Node.js >= 18 manually from https://nodejs.org/.";
    return $false
  }
  $nvmExe = Ensure-Nvm
  if (-not $nvmExe) { return $false }
  Prepare-NvmEnvironment $nvmExe
  Write-Host "Using $(Split-Path -Leaf $nvmExe) located at $(Split-Path -Parent $nvmExe)" -ForegroundColor DarkGray
  $targetVersion = '20.12.0'
  $listOutput = (& $nvmExe ls) 2>$null
  if ($listOutput -notmatch "v?$targetVersion") {
    Write-Host "Installing Node.js $targetVersion via nvm..." -ForegroundColor Yellow
    & $nvmExe install $targetVersion
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
      Write-Host "nvm install exited with code $exitCode; attempting to continue..." -ForegroundColor Yellow
    }
  } else {
    Write-Host "Node.js $targetVersion already present in NVM cache." -ForegroundColor DarkGray
  }
  & $nvmExe use $targetVersion
  $exitCode = $LASTEXITCODE
  if ($exitCode -ne 0) {
    Write-Host "nvm use exited with $exitCode; refreshing PATH and retrying..." -ForegroundColor Yellow
    Add-PathIfMissing $env:NVM_HOME
    Add-PathIfMissing $env:NVM_SYMLINK
    & $nvmExe use $targetVersion
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
      Write-Host "nvm use still failing (exit $exitCode); falling back to manual PATH configuration." -ForegroundColor Yellow
      $nodeDir = Join-Path $env:NVM_HOME "v$targetVersion"
      if (-not (Test-Path $nodeDir)) {
        Write-Error "Expected Node.js directory $nodeDir not found after nvm installation.";
        return $false
      }
      Add-PathIfMissing $nodeDir
      Add-PathIfMissing (Join-Path $nodeDir 'node_modules\\npm')
    }
  }
  Add-PathIfMissing $env:NVM_SYMLINK
  try { & $nvmExe alias default $targetVersion | Out-Null } catch { }
  if (-not (Test-Command 'node')) {
    $nodeDir = Join-Path $env:NVM_HOME "v$targetVersion"
    if (Test-Path $nodeDir) {
      Add-PathIfMissing $nodeDir
      Add-PathIfMissing (Join-Path $nodeDir 'node_modules\\npm')
    }
  }
  if (-not (Test-Command 'node')) {
    Write-Error "Node.js executable not found on PATH after fallback configuration. Run 'nvm use $targetVersion' manually (as Administrator if required) and re-run the installer.";
    return $false
  }
  return $true
}

function Install-GitForWindows {
  param(
    [string]$Url = 'https://mirrors.huaweicloud.com/git-for-windows/v2.51.0.windows.1/Git-2.51.0-64-bit.exe'
  )
  if (-not $script:IsWindows) {
    Write-Error "Automated Git installation is only supported on Windows. Install Git manually from https://git-scm.com/download/win.";
    return $false
  }
  $tempDir = [System.IO.Path]::GetTempPath()
  $fileName = Split-Path -Leaf $Url
  $exePath = Join-Path $tempDir $fileName
  try { if (Test-Path $exePath) { Remove-Item -Path $exePath -Force -ErrorAction SilentlyContinue } } catch { }
  Write-Host "Downloading Git for Windows from $Url ..." -ForegroundColor Yellow
  try {
    Invoke-WebRequest -Uri $Url -OutFile $exePath -UseBasicParsing -ErrorAction Stop
  } catch {
    Write-Error "Failed to download Git for Windows: $($_.Exception.Message)";
    return $false
  }
  Write-Host "Installing Git for Windows from $exePath ..." -ForegroundColor Yellow
  $args = @('/VERYSILENT','/NORESTART','/SUPPRESSMSGBOXES','/SP-','/ALLUSERS')
  & $exePath @args
  $exitCode = $LASTEXITCODE
  if ($exitCode -ne 0) {
    Write-Host "Git installer exited with code $exitCode. You may need to run PowerShell as Administrator." -ForegroundColor Red
    return $false
  }
  $gitCmdDir = Join-Path ([System.Environment]::GetFolderPath('ProgramFiles')) 'Git\\cmd'
  $gitBinDir = Join-Path ([System.Environment]::GetFolderPath('ProgramFiles')) 'Git\\bin'
  Add-PathIfMissing $gitCmdDir
  Add-PathIfMissing $gitBinDir
  Start-Sleep -Seconds 1
  return $true
}

function Ensure-Node {
  # Refresh current session PATH from registry first so we can detect existing Node immediately
  Refresh-PathFromRegistry
  $version = Get-NodeVersion
  $needInstall = $false
  if (-not $version) {
    Write-Host "Node.js not detected." -ForegroundColor Yellow
    $needInstall = $true
  } elseif (-not $version.Parsed) {
    Write-Host "Unable to parse Node.js version '$($version.Raw)'. Installing Node.js 24.11.0 for consistency." -ForegroundColor Yellow
    $needInstall = $true
  } elseif ($version.Major -lt 18) {
    Write-Host "Node.js $($version.Raw) detected but >= 18 is required." -ForegroundColor Yellow
    $needInstall = $true
  } elseif ($version.Major -lt 24 -or ($version.Major -eq 24 -and $version.Minor -lt 11)) {
    Write-Host "Node.js $($version.Raw) detected; installing recommended 24.11.0." -ForegroundColor Yellow
    $needInstall = $true
  }

  if ($needInstall) {
    $installOk = Install-NodeViaMsi -Url 'https://nodejs.org/dist/v24.11.0/node-v24.11.0-x64.msi'
    # After installation, refresh PATH and attempt to detect node again with fallbacks
    $maxWait = 10
    for ($i = 0; $i -lt $maxWait; $i++) {
      Refresh-PathFromRegistry
      # First try direct command
      $version = Get-NodeVersion
      if ($version -and $version.Parsed) { break }

      # Try where.exe to locate node on disk
      $nodeExe = $null
      try {
        $paths = (& where.exe node 2>$null)
        if ($paths) {
          foreach ($p in @($paths)) {
            if ($p -and (Test-Path $p)) { $nodeExe = $p; break }
          }
        }
      } catch { }
      if (-not $nodeExe) {
        # Probe common install locations
        $probables = @()
        $prog = [System.Environment]::GetFolderPath('ProgramFiles')
        if ($prog) { $probables += (Join-Path $prog 'nodejs\node.exe') }
        $progx86 = ${env:ProgramFiles(x86)}
        if ($progx86) { $probables += (Join-Path $progx86 'nodejs\node.exe') }
        $localApp = $env:LOCALAPPDATA
        if ($localApp) { $probables += (Join-Path $localApp 'Programs\nodejs\node.exe') }
        foreach ($p in $probables) { if ($p -and (Test-Path $p)) { $nodeExe = $p; break } }
      }
      if ($nodeExe) {
        $dir = Split-Path -Parent $nodeExe
        Add-PathIfMissing $dir
        try {
          $raw = (& $nodeExe -v 2>$null)
          $parsed = Parse-NodeVersionString -Raw $raw
          if ($parsed) { $version = $parsed; break }
        } catch { }
      }
      Start-Sleep -Seconds 1
    }
    if (-not $version -or -not $version.Parsed) {
      if (-not $installOk) {
        Write-Error "Failed to provision Node.js automatically. Install Node.js >= 18 manually and re-run."; exit 31
      }
    }
  }
  if (-not $version -or -not $version.Parsed) {
    Write-Error "Failed to verify Node.js version after installation."; exit 31
  }
  if ($version.Major -lt 18) {
    Write-Error "Node.js version $($version.Raw) remains below required 18."; exit 31
  }
  if ($version.Major -lt 24 -or ($version.Major -eq 24 -and $version.Minor -lt 11)) {
    Write-Host "Warning: Node.js $($version.Raw) detected. Version 24.11.0 remains recommended." -ForegroundColor Yellow
  } else {
    Write-Host "Node.js $($version.Raw) detected." -ForegroundColor Green
  }
}

function Ensure-Git {
  if (Test-Command 'git') { return }
  Write-Host "git not detected. Installing Git for Windows..." -ForegroundColor Yellow
  if (-not (Install-GitForWindows)) {
    Write-Error "Failed to install Git for Windows automatically. Install it manually and re-run."; exit 33
  }
  if (-not (Test-Command 'git')) {
    $gitCmdDir = Join-Path ([System.Environment]::GetFolderPath('ProgramFiles')) 'Git\\cmd'
    $gitBinDir = Join-Path ([System.Environment]::GetFolderPath('ProgramFiles')) 'Git\\bin'
    Add-PathIfMissing $gitCmdDir
    Add-PathIfMissing $gitBinDir
  }
  if (-not (Test-Command 'git')) {
    Write-Error "git still not available on PATH after installation."; exit 33
  }
}

function Ensure-ClaudeCLI {
  if (Test-Command 'claude') { return }
  if (-not (Test-Command 'npm')) {
    Write-Error "npm not found. Please ensure your Node.js installation provides npm before continuing."; exit 32
  }
  $originalRegistry = ''
  try {
    $originalRegistry = (npm config get registry 2>$null).Trim()
  } catch { $originalRegistry = '' }
  try { npm config set registry https://registry.npmmirror.com | Out-Null } catch { }
  $installSucceeded = $false
  Write-Host "Installing @anthropic-ai/claude-code globally via npm..." -ForegroundColor Cyan
  try {
    npm install -g @anthropic-ai/claude-code | Out-Null
    $installSucceeded = $true
  } catch {
    Write-Host "Global install failed, retrying with output..." -ForegroundColor Yellow
    try {
      npm install -g @anthropic-ai/claude-code
      $installSucceeded = $true
    } catch {
      $installSucceeded = $false
    }
  } finally {
    if (-not [string]::IsNullOrWhiteSpace($originalRegistry)) {
      try { npm config set registry $originalRegistry | Out-Null } catch { }
    }
  }
  if (-not $installSucceeded -or -not (Test-Command 'claude')) {
    Write-Error "Failed to install 'claude' CLI. If this is a permissions issue, try running PowerShell as Administrator or adjust npm prefix."; exit 32
  }
}

function Normalize-BaseUrl {
  param([string]$Url)
  if (-not $Url) { return $Url }
  $trimmed = $Url.TrimEnd('/')
  if ($trimmed -ne $Url) { Write-Host "Normalized BASE_URL to $trimmed" }
  return $trimmed
}

function Write-OnboardingConfig {
  param([string]$Path)

  $data = @{}
  if (Test-Path $Path) {
    try {
      $raw = Get-Content -Path $Path -Raw -ErrorAction Stop
      if (-not [string]::IsNullOrWhiteSpace($raw)) {
        $parsed = $raw | ConvertFrom-Json -ErrorAction Stop
        if ($parsed -and -not ($parsed -is [System.Array])) {
          foreach ($prop in $parsed.PSObject.Properties) {
            $data[$prop.Name] = $prop.Value
          }
        }
      }
    } catch {
      $data = @{}
    }
  }

  $data["hasCompletedOnboarding"] = $true
  $json = $data | ConvertTo-Json -Depth 20
  [System.IO.File]::WriteAllText($Path, ($json + [Environment]::NewLine), [System.Text.UTF8Encoding]::new($false))
  Write-Host "`u2714 Wrote $Path" -ForegroundColor Green
}

function Detect-Platform {
  $osDescription = [System.Runtime.InteropServices.RuntimeInformation]::OSDescription
  $arch = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture
  Write-Host "Detected $osDescription ($arch) environment."
  $script:IsWindows = [System.Runtime.InteropServices.RuntimeInformation]::IsOSPlatform([System.Runtime.InteropServices.OSPlatform]::Windows)
  if (-not $script:IsWindows) {
    Write-Host "Warning: install-claude.ps1 is optimized for Windows. For macOS/Linux, use install-claude.sh." -ForegroundColor Yellow
  }
}

if (-not $Token -and $env:ANTHROPIC_AUTH_TOKEN) { $Token = $env:ANTHROPIC_AUTH_TOKEN }
if (-not $BaseUrl -and $env:ANTHROPIC_BASE_URL) { $BaseUrl = $env:ANTHROPIC_BASE_URL }

Assert-AdminAndChdir
Detect-Platform
Ensure-Node
Ensure-Git
Ensure-ClaudeCLI

if (-not $script:AutoApprove) {
  if (-not $Token) { $Token = Read-Token -Prompt "Enter ANTHROPIC_AUTH_TOKEN" }
  if (-not $BaseUrl) { $BaseUrl = Read-Host -Prompt "Enter ANTHROPIC_BASE_URL (e.g. https://api.anthropic.com)" }
  if (-not $BaseUrl) { Write-Error "Base URL is required."; exit 2 }

  $BaseUrl = Normalize-BaseUrl -Url $BaseUrl

  Write-Host "About to write settings to $HOME\.claude\settings.json with:" -ForegroundColor Cyan
  Write-Host "  ANTHROPIC_AUTH_TOKEN=$Token"
  Write-Host "  ANTHROPIC_BASE_URL=$BaseUrl"
  $confirm = Read-Host -Prompt "Proceed? [y/N]"
  if ($confirm -notin @('y','Y','yes','YES')) { Write-Host 'Aborted.'; exit 3 }
} else {
  if (-not $Token) { Write-Error "--Token or ANTHROPIC_AUTH_TOKEN is required in non-interactive mode."; exit 2 }
  if (-not $BaseUrl) { Write-Error "--BaseUrl or ANTHROPIC_BASE_URL is required in non-interactive mode."; exit 2 }
  $BaseUrl = Normalize-BaseUrl -Url $BaseUrl
}

$targetDir = Join-Path $HOME '.claude'
$targetFile = Join-Path $targetDir 'settings.json'
$configFile = Join-Path $targetDir 'config.json'
$onboardingFile = Join-Path $HOME ".claude.json"
New-Item -ItemType Directory -Path $targetDir -Force | Out-Null

$json = @"
{
  "env": {
    "ANTHROPIC_AUTH_TOKEN": "$Token",
    "ANTHROPIC_BASE_URL": "$BaseUrl",
    "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "64000",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "API_TIMEOUT_MS": "600000",
    "BASH_DEFAULT_TIMEOUT_MS": "600000",
    "BASH_MAX_TIMEOUT_MS": "600000",
    "MCP_TIMEOUT": "30000",
    "MCP_TOOL_TIMEOUT": "600000",
    "CLAUDE_API_TIMEOUT": "600000"
  },
  "permissions": {
    "allow": [],
    "deny": []
  }
}
"@.Trim()
[System.IO.File]::WriteAllText($targetFile, $json, [System.Text.UTF8Encoding]::new($false))

Write-Host "`u2714 Wrote $targetFile" -ForegroundColor Green
Write-Host "   ANTHROPIC_AUTH_TOKEN=$Token"
Write-Host "   ANTHROPIC_BASE_URL=$BaseUrl"

$configJson = @"
{
  "primaryApiKey": "default"
}
"@.Trim()
[System.IO.File]::WriteAllText($configFile, $configJson, [System.Text.UTF8Encoding]::new($false))
Write-Host "`u2714 Wrote $configFile" -ForegroundColor Green

Write-OnboardingConfig -Path $onboardingFile


function Find-CodeInstall {
  $candidates = @()
  if ($env:LOCALAPPDATA) {
    $candidates += (Join-Path $env:LOCALAPPDATA 'Programs\Microsoft VS Code\Code.exe')
    $candidates += (Join-Path $env:LOCALAPPDATA 'Programs\Microsoft VS Code\bin')
  }
  if ($env:ProgramFiles) {
    $candidates += (Join-Path $env:ProgramFiles 'Microsoft VS Code\Code.exe')
    $candidates += (Join-Path $env:ProgramFiles 'Microsoft VS Code\bin')
  }
  foreach ($p in $candidates) { if ($p -and (Test-Path $p)) { return $p } }
  return $null
}

function Ensure-CodeOnPath {
  Refresh-PathFromRegistry
  $binDirs = @()
  if ($env:LOCALAPPDATA) { $binDirs += (Join-Path $env:LOCALAPPDATA 'Programs\Microsoft VS Code\bin') }
  if ($env:ProgramFiles) { $binDirs += (Join-Path $env:ProgramFiles 'Microsoft VS Code\bin') }
  foreach ($d in $binDirs) { Add-PathIfMissing $d }
}

function Get-CodeVersion {
  try { $ver = (& code --version 2>$null) } catch { $ver = $null }
  if ($ver) { return ($ver -join "\n") }
  $codePath = Find-CodeInstall
  if ($codePath -and $codePath.ToLower().EndsWith('code.exe')) {
    try { $ver = (& $codePath --version 2>$null) } catch { $ver = $null }
  }
  if ($ver) { return ($ver -join "\n") }
  return $null
}

function Install-VSCode {
  param([string]$SetupUrl = 'https://vscode.download.prss.microsoft.com/dbazure/download/stable/7d842fb85a0275a4a8e4d7e040d2625abbf7f084/VSCodeUserSetup-x64-1.105.1.exe')
  if (-not $script:IsWindows) { return $false }
  $tempDir = [IO.Path]::GetTempPath()
  $fileName = Split-Path -Leaf $SetupUrl
  $exePath = Join-Path $tempDir $fileName
  try { if (Test-Path $exePath) { Remove-Item -Path $exePath -Force -ErrorAction SilentlyContinue } } catch { }
  Write-Host "Downloading VS Code from $SetupUrl ..." -ForegroundColor Yellow
  try {
    Invoke-WebRequest -Uri $SetupUrl -OutFile $exePath -UseBasicParsing -ErrorAction Stop
  } catch {
    Write-Error "Failed to download VS Code: $($_.Exception.Message)"; return $false
  }
  Write-Host "Installing VS Code from $exePath ..." -ForegroundColor Yellow
  $args = @('/VERYSILENT','/NORESTART','/SUPPRESSMSGBOXES','/SP-','/MERGETASKS=!runcode,addcontextmenufiles,addcontextmenufolders,associatewithfiles,addtopath')
  try {
    $p = Start-Process -FilePath $exePath -ArgumentList ($args -join ' ') -Wait -PassThru -WindowStyle Hidden
    $exitCode = $p.ExitCode
  } catch { $exitCode = $null }
  if ($exitCode -ne 0 -and $exitCode -ne $null) {
    Write-Host "VS Code installer exited with code $exitCode. Will verify installation before failing." -ForegroundColor Yellow
  }
  Ensure-CodeOnPath
  Start-Sleep -Seconds 1
  $ver = Get-CodeVersion
  if ($ver) { return $true }
  $found = Find-CodeInstall
  return -not ([string]::IsNullOrWhiteSpace($found))
}

function Ensure-VSCode {
  Ensure-CodeOnPath
  $v = Get-CodeVersion
  if ($v) { Write-Host "VS Code detected:" -ForegroundColor Green; Write-Host $v; return }
  if (-not (Install-VSCode)) {
    Write-Error "Failed to install VS Code."; exit 41
  }
  Ensure-CodeOnPath
  $v2 = Get-CodeVersion
  if ($v2) { Write-Host "VS Code installed:" -ForegroundColor Green; Write-Host $v2; return }
  Write-Host "VS Code installation completed but CLI not detected. You may need to open a new PowerShell session." -ForegroundColor Yellow
}

# Ensure VS Code after all functions are defined
Ensure-VSCode
