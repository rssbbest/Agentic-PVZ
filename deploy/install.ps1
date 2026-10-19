# Autopilot SDK: install and start, on Windows (PowerShell).
#
#   irm https://13-200-183-134.sslip.io/install.ps1 | iex
#
# Puts everything in %USERPROFILE%\autopilot (AUTOPILOT_DIR to change it): the
# SDK program and a starter agent.py (an existing agent.py is never
# overwritten). If the program does not run here, it installs the SDK with
# Python instead: a venv first, then pip --user. Then it starts the SDK.
# Run it again any time to update.
$ErrorActionPreference = 'Continue'   # python/pip may write warnings to stderr: not fatal
$ProgressPreference = 'SilentlyContinue'   # downloads are far faster without the progress bar
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$Base = if ($env:AUTOPILOT_BASE) { $env:AUTOPILOT_BASE } else { 'https://13-200-183-134.sslip.io' }
$Dir = if ($env:AUTOPILOT_DIR) { $env:AUTOPILOT_DIR } else { Join-Path $HOME 'autopilot' }

function Say([string]$Text) { Write-Host "==> $Text" -ForegroundColor Cyan }
function Warn([string]$Text) { Write-Host "!! $Text" -ForegroundColor Yellow }
function Fetch([string]$Url, [string]$File) { Invoke-WebRequest -Uri $Url -OutFile $File -UseBasicParsing -ErrorAction Stop }

New-Item -ItemType Directory -Force -Path $Dir | Out-Null
Set-Location $Dir
try {
    if (-not (Test-Path 'agent.py')) { Fetch "$Base/download/agent.py" 'agent.py' }
} catch {
    Warn "Cannot reach $Base ($($_.Exception.Message)). Check the internet connection and run this line again."
    return
}
try { Fetch "$Base/download/README_PARTICIPANT.md" 'README_PARTICIPANT.md' } catch { }

$program = 'autopilot-windows-x86_64.exe'
$start = $null
if (-not $env:AUTOPILOT_FORCE_PYTHON) {
    try {
        Say 'Downloading the Autopilot SDK for Windows'
        Fetch "$Base/download/$program" "$program.new"
        Move-Item -Force "$program.new" $program -ErrorAction Stop
        Unblock-File $program   # downloaded file: no SmartScreen prompt
        & ".\$program" version | Out-Null
        if ($LASTEXITCODE -eq 0) { $start = @((Join-Path $Dir $program)) }
        else { Warn 'The SDK program does not run on this computer; installing it with Python instead' }
    } catch {
        Warn "The SDK program could not be set up ($($_.Exception.Message)); installing it with Python instead"
    }
}

if (-not $start) {
    $python = $null
    foreach ($candidate in 'py -3', 'python', 'python3') {
        $parts = @($candidate -split ' ')
        $rest = @($parts | Select-Object -Skip 1)
        try {
            & $parts[0] @rest -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>$null
            if ($LASTEXITCODE -eq 0) { $python = $parts; break }
        } catch { }
    }
    if (-not $python) {
        Warn 'Needs Python 3.10 or newer: install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH") and run this line again.'
        return
    }
    $listing = (Invoke-WebRequest -Uri "$Base/download/" -UseBasicParsing -ErrorAction Stop).Content
    $wheel = [regex]::Match($listing, 'autopilot_sdk-[0-9][0-9.]*-py3-none-any\.whl').Value
    if (-not $wheel) { Warn 'The SDK package is missing on the server; tell the organisers'; return }
    $pyArgs = @($python | Select-Object -Skip 1)
    Say "Installing the SDK with Python ($(& $python[0] @pyArgs --version 2>&1))"
    & $python[0] @pyArgs -m venv .venv 2>$null
    $venvPython = Join-Path $Dir '.venv\Scripts\python.exe'
    if ($LASTEXITCODE -eq 0 -and (Test-Path $venvPython)) {
        & $venvPython -m pip install -q --disable-pip-version-check --no-cache-dir --upgrade "$Base/download/$wheel"
        # the same version number with new code still installs (see updater.py)
        if ($LASTEXITCODE -eq 0) { & $venvPython -m pip install -q --disable-pip-version-check --no-cache-dir --force-reinstall --no-deps "$Base/download/$wheel" }
    }
    if ($LASTEXITCODE -eq 0 -and (Test-Path $venvPython)) {
        $start = @($venvPython, '-m', 'autopilot_sdk')
    } else {
        Warn 'Could not make a venv; using pip --user'
        & $python[0] @pyArgs -m pip install -q --user --no-cache-dir --upgrade "$Base/download/$wheel"
        & $python[0] @pyArgs -m pip install -q --user --no-cache-dir --force-reinstall --no-deps "$Base/download/$wheel"
        $start = @($python[0]) + $pyArgs + @('-m', 'autopilot_sdk')
    }
    $cmd = '@echo off' + "`r`n" + 'cd /d "%~dp0"' + "`r`n" + '"' + $start[0] + '" ' + (($start | Select-Object -Skip 1) -join ' ') + ' %*' + "`r`n"
    Set-Content -Path (Join-Path $Dir 'autopilot.cmd') -Value $cmd -Encoding Ascii
}

Say "Installed in $Dir"
if ($start.Count -eq 1) { Say "Next time: double-click $program in that folder" }
else { Say "Next time: double-click autopilot.cmd in that folder" }
if ($env:AUTOPILOT_NO_START) { return }
$rest = @($start | Select-Object -Skip 1)
& $start[0] @rest
