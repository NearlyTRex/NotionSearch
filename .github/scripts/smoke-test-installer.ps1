<#
.SYNOPSIS
    Installs the built installer silently and checks the result is usable.

.DESCRIPTION
    Proves the installer actually works rather than merely compiling. Catching a
    broken installer here is far cheaper than shipping it, because this is the
    very first thing a user would hit.

    Run it on the built .exe:
        .github\scripts\smoke-test-installer.ps1 -InstallerPath dist\NotionSearch-0.1.0-Setup.exe
#>

[CmdletBinding()]
param(
    # The installer to test. Defaults to the newest one in dist\.
    [string]$InstallerPath,

    # Where to install it. Defaults to a temporary folder.
    [string]$TargetDir,

    # Skip the uninstall afterwards, to inspect the result.
    [switch]$KeepInstalled
)

$ErrorActionPreference = "Stop"

if (-not $InstallerPath) {
    $found = Get-ChildItem "dist\*.exe" -ErrorAction SilentlyContinue |
             Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $found) { throw "No installer found in dist\. Compile it first." }
    $InstallerPath = $found.FullName
}
if (-not (Test-Path $InstallerPath)) { throw "Installer not found: $InstallerPath" }

if (-not $TargetDir) {
    $base = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { $env:TEMP }
    $TargetDir = Join-Path $base "NotionSearchSmokeTest"
}
$logFile = Join-Path (Split-Path -Parent $TargetDir) "notionsearch-install.log"

if (Test-Path $TargetDir) { Remove-Item $TargetDir -Recurse -Force }

Write-Host "Installing $(Split-Path -Leaf $InstallerPath) to $TargetDir"
$proc = Start-Process -FilePath $InstallerPath -Wait -PassThru -ArgumentList `
    "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/DIR=$TargetDir", "/LOG=$logFile"

if ($proc.ExitCode -ne 0) {
    if (Test-Path $logFile) { Get-Content $logFile -Tail 40 }
    throw "installer exited with $($proc.ExitCode)"
}

$failures = @()

function Assert-Ok {
    param([string]$Description, [bool]$Condition)
    if ($Condition) {
        Write-Host "  ok    $Description"
    } else {
        Write-Host "  FAIL  $Description"
        $script:failures += $Description
    }
}

# Everything the app needs at runtime must have landed.
foreach ($f in @(
    "docker\docker-compose.yml",
    "docker\Dockerfile",
    "docker\entrypoint.sh",
    "app\main.py",
    "web\index.html",
    "scripts\start-windows.cmd",
    "scripts\stop-windows.cmd"
)) {
    Assert-Ok "installed $f" (Test-Path (Join-Path $TargetDir $f))
}

# entrypoint.sh runs inside a Linux container. A Windows checkout that rewrote
# it with CRLF makes the container fail to start with a baffling
# "no such file or directory". .gitattributes pins it to LF; check it held.
$entrypoint = Join-Path $TargetDir "docker\entrypoint.sh"
if (Test-Path $entrypoint) {
    Assert-Ok "entrypoint.sh has LF line endings" `
        (-not ([IO.File]::ReadAllBytes($entrypoint) -contains 13))
}

# The container bind-mounts this and writes its database into it.
$data = Join-Path $TargetDir "data"
Assert-Ok "data folder created" (Test-Path $data)
if (Test-Path $data) {
    try {
        $probe = Join-Path $data "probe.txt"
        "probe" | Set-Content $probe
        Remove-Item $probe
        Assert-Ok "data folder is writable" $true
    } catch {
        Assert-Ok "data folder is writable" $false
    }
}

# A developer's own .env must never be packaged: it can hold a password.
Assert-Ok "no .env was packaged" (-not (Test-Path (Join-Path $TargetDir "docker\.env")))
Assert-Ok "no build cruft was packaged" `
    (-not (Get-ChildItem $TargetDir -Recurse -Include "*.pyc", "__pycache__" -Force -ErrorAction SilentlyContinue))

if (-not $KeepInstalled) {
    $uninstaller = Get-ChildItem (Join-Path $TargetDir "unins*.exe") -ErrorAction SilentlyContinue |
                   Select-Object -First 1
    if ($uninstaller) {
        Start-Process $uninstaller.FullName -Wait -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES"
        Write-Host "  ok    uninstalled again"
    }
}

if ($failures.Count -gt 0) {
    Write-Host "::error::installer smoke test failed: $($failures -join '; ')"
    exit 1
}

Write-Host ""
Write-Host "Installer smoke test passed."
