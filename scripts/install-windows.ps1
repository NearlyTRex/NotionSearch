<#
.SYNOPSIS
    Installs everything NotionSearch needs on Windows, then starts it.

.DESCRIPTION
    Installs WSL2 and Docker Desktop via winget, waits for Docker to be ready,
    starts NotionSearch, and opens it in your browser.

    Safe to run more than once: anything already installed is skipped. If a
    reboot is needed it will tell you, and you just run it again afterwards.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1 -Port 9000
#>

[CmdletBinding()]
param(
    # Port the web page will be served on: http://localhost:<Port>
    [int]$Port = 8080,

    # Set a password to require a login before searching.
    [string]$AppPassword = "",

    # Install prerequisites but don't start NotionSearch.
    [switch]$SkipStart
)

$ErrorActionPreference = "Stop"

# --- output helpers -------------------------------------------------------

function Write-Step   { param($m) Write-Host "`n==> $m" -ForegroundColor Cyan }
function Write-Ok     { param($m) Write-Host "    $m" -ForegroundColor Green }
function Write-Info   { param($m) Write-Host "    $m" -ForegroundColor Gray }
function Write-Warn   { param($m) Write-Host "    $m" -ForegroundColor Yellow }
function Write-Err    { param($m) Write-Host "    $m" -ForegroundColor Red }

function Stop-WithMessage {
    param($Message, $Hint)
    Write-Host ""
    Write-Err $Message
    if ($Hint) { Write-Host ""; Write-Info $Hint }
    Write-Host ""
    exit 1
}

# --- preflight ------------------------------------------------------------

Write-Host ""
Write-Host "  NotionSearch setup for Windows" -ForegroundColor White
Write-Host "  ------------------------------" -ForegroundColor DarkGray

$repoRoot = Split-Path -Parent $PSScriptRoot
$composeDir = Join-Path $repoRoot "docker"

if (-not (Test-Path (Join-Path $composeDir "docker-compose.yml"))) {
    Stop-WithMessage "Can't find docker\docker-compose.yml." `
        "Run this from inside the NotionSearch folder you cloned or downloaded."
}

Write-Step "Checking Windows"

if ([Environment]::OSVersion.Version.Major -lt 10) {
    Stop-WithMessage "Windows 10 or 11 is required." `
        "Docker Desktop does not support older versions."
}
if ([Environment]::Is64BitOperatingSystem -eq $false) {
    Stop-WithMessage "A 64-bit version of Windows is required."
}
Write-Ok "Windows $([Environment]::OSVersion.Version) (64-bit)"

# winget ships with App Installer. Present on Windows 11 and current Windows 10.
if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Stop-WithMessage "winget is not available on this machine." @"
winget comes from 'App Installer'. To get it:

  1. Open the Microsoft Store
  2. Search for 'App Installer'
  3. Install or update it, then run this script again

Or install Docker Desktop by hand from:
  https://www.docker.com/products/docker-desktop/
"@
}
Write-Ok "winget is available"

# --- WSL2 -----------------------------------------------------------------

Write-Step "Checking WSL2 (Docker Desktop needs it)"

$needsReboot = $false
$wslOk = $false
try {
    # 'wsl --status' exits non-zero when WSL isn't set up at all.
    $null = wsl --status 2>&1
    if ($LASTEXITCODE -eq 0) { $wslOk = $true }
} catch {
    $wslOk = $false
}

if ($wslOk) {
    Write-Ok "WSL2 is already set up"
} else {
    Write-Info "Installing WSL2. This needs administrator approval."
    try {
        # --no-distribution: Docker Desktop provides its own; we don't need Ubuntu.
        Start-Process -FilePath "wsl.exe" -ArgumentList "--install","--no-distribution" `
            -Verb RunAs -Wait
        Write-Ok "WSL2 installed"
        $needsReboot = $true
    } catch {
        Stop-WithMessage "Could not install WSL2 automatically." @"
Open PowerShell as Administrator and run:

  wsl --install

Then restart your computer and run this script again.
"@
    }
}

# --- Docker Desktop -------------------------------------------------------

Write-Step "Checking Docker Desktop"

$dockerInstalled = $null -ne (Get-Command docker -ErrorAction SilentlyContinue)
if (-not $dockerInstalled) {
    # winget may know about it even when docker.exe isn't on PATH yet.
    $listed = winget list --id Docker.DockerDesktop --exact 2>&1 | Out-String
    if ($listed -match "Docker Desktop") { $dockerInstalled = $true }
}

if ($dockerInstalled) {
    Write-Ok "Docker Desktop is already installed"
} else {
    Write-Info "Installing Docker Desktop (a few hundred MB, this takes a while)..."
    winget install --id Docker.DockerDesktop --exact --silent `
        --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        Stop-WithMessage "Docker Desktop failed to install (winget exit $LASTEXITCODE)." @"
Install it by hand instead:
  https://www.docker.com/products/docker-desktop/

Then run this script again.
"@
    }
    Write-Ok "Docker Desktop installed"
    $needsReboot = $true
}

if ($needsReboot) {
    Write-Host ""
    Write-Warn "A restart is needed to finish setting up WSL2 / Docker Desktop."
    Write-Host ""
    Write-Info "Restart your computer, then run this script again to start NotionSearch."
    Write-Host ""
    exit 0
}

# --- start Docker ---------------------------------------------------------

Write-Step "Starting Docker"

function Test-DockerReady {
    try {
        $null = docker info 2>&1
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

if (-not (Test-DockerReady)) {
    $dockerExe = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    if (Test-Path $dockerExe) {
        Write-Info "Launching Docker Desktop..."
        Start-Process $dockerExe | Out-Null
    } else {
        Write-Info "Start Docker Desktop from the Start menu if it isn't running."
    }

    Write-Info "Waiting for Docker to be ready (this can take a minute)..."
    $waited = 0
    while (-not (Test-DockerReady)) {
        Start-Sleep -Seconds 3
        $waited += 3
        if ($waited % 30 -eq 0) { Write-Info "  still waiting... ($waited seconds)" }
        if ($waited -ge 300) {
            Stop-WithMessage "Docker didn't become ready within 5 minutes." @"
Open Docker Desktop manually and wait for it to say 'Engine running',
then run this script again.

First launch sometimes asks you to accept its terms.
"@
        }
    }
}
Write-Ok "Docker is running"

if ($SkipStart) {
    Write-Host ""
    Write-Ok "Prerequisites are installed. Start NotionSearch with scripts\start-windows.cmd"
    Write-Host ""
    exit 0
}

# --- configure ------------------------------------------------------------

Write-Step "Configuring"

$envFile = Join-Path $composeDir ".env"
if (Test-Path $envFile) {
    Write-Ok "Keeping your existing docker\.env"
} else {
    # On Windows the container must run as root: bind mounts from the Windows
    # filesystem carry no Unix ownership, so the Linux-host default of uid 1000
    # cannot reliably write to them.
    $lines = @(
        "# Created by install-windows.ps1",
        "PORT=$Port",
        "APP_PASSWORD=$AppPassword",
        "",
        "# Windows: bind mounts have no Unix ownership, so run as root.",
        "PUID=0",
        "PGID=0"
    )
    $lines | Set-Content -Path $envFile -Encoding ASCII
    Write-Ok "Wrote docker\.env (port $Port)"
}

# --- start ----------------------------------------------------------------

Write-Step "Starting NotionSearch"
Write-Info "First run builds the image, which takes a few minutes."

Push-Location $composeDir
try {
    docker compose up -d
    if ($LASTEXITCODE -ne 0) {
        Stop-WithMessage "docker compose failed to start." `
            "Run 'docker compose logs' in the docker folder to see why."
    }
} finally {
    Pop-Location
}

Write-Info "Waiting for it to come up..."
$url = "http://localhost:$Port"
$ready = $false
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Seconds 2
    try {
        $resp = Invoke-WebRequest -Uri "$url/health" -UseBasicParsing -TimeoutSec 3
        if ($resp.StatusCode -eq 200) { $ready = $true; break }
    } catch { }
}

Write-Host ""
if ($ready) {
    Write-Ok "NotionSearch is running at $url"
    Write-Host ""
    Write-Info "Opening it in your browser. Follow the three steps on the page to"
    Write-Info "connect your Notion account."
    Start-Process $url
} else {
    Write-Warn "It didn't respond in time, but it may still be starting."
    Write-Info "Try opening $url in a minute."
    Write-Info "To see what's happening: cd docker; docker compose logs -f api"
}

Write-Host ""
Write-Info "To start it again later:  scripts\start-windows.cmd"
Write-Info "To stop it:               cd docker; docker compose down"
Write-Host ""
