<#
.SYNOPSIS
    Writes the release notes for a GitHub Release.

.EXAMPLE
    .github\scripts\release-notes.ps1 -Version 0.1.0 -Repo NearlyTRex/NotionSearch -OutFile notes.md
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][string]$Repo,

    # Where to write the notes. Prints to stdout when omitted.
    [string]$OutFile
)

$ErrorActionPreference = "Stop"

# Single-quoted strings throughout: PowerShell treats a backtick literally in
# them, so markdown code spans survive without escaping.
#
# Note the parentheses around every concatenation. Inside an array literal,
# `'a' + $v + 'b'` without them is parsed as three separate elements, not one
# joined string — which silently breaks the line across three lines of output.
$notes = @(
    '## Installing on Windows',
    '',
    ('1. Download `NotionSearch-' + $Version + '-Setup.exe` below'),
    '2. Run it. Windows SmartScreen warns that the publisher is unknown, because',
    '   the installer is not code-signed. Choose **More info**, then **Run anyway**.',
    '3. Launch **NotionSearch** from the Start Menu',
    '',
    'You also need [Docker Desktop](https://www.docker.com/products/docker-desktop/).',
    'If it is missing, the shortcut says so, and `scripts\install-windows.ps1`',
    'installs it for you.',
    '',
    '## Installing on Linux or macOS',
    '',
    '```bash',
    ('git clone https://github.com/' + $Repo + '.git'),
    'cd NotionSearch/docker',
    'docker compose up -d',
    '```',
    '',
    'Then open <http://localhost:8080>.',
    '',
    '## Verifying your download',
    '',
    'Compare against `SHA256SUMS.txt`:',
    '',
    '```powershell',
    ('Get-FileHash NotionSearch-' + $Version + '-Setup.exe -Algorithm SHA256'),
    '```'
)

if ($OutFile) {
    $notes | Set-Content -Path $OutFile -Encoding utf8
    Write-Host "Wrote $($notes.Count) lines to $OutFile"
} else {
    $notes | Write-Output
}
