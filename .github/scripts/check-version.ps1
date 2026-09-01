<#
.SYNOPSIS
    Works out the version to build, and refuses a tag that disagrees with
    pyproject.toml.

.DESCRIPTION
    A tag and the packaged version have to be the same number, or we ship an
    installer whose filename disagrees with what the app reports about itself.

    Writes `version=<v>` to $GITHUB_OUTPUT when running in Actions.

.EXAMPLE
    .github/scripts/check-version.ps1 -Ref refs/tags/v0.1.0
    .github/scripts/check-version.ps1 -Ref refs/tags/0.1.0

.EXAMPLE
    # Rehearse a build at any version.
    .github/scripts/check-version.ps1 -Ref refs/heads/main -FallbackVersion 0.0.0-test
#>

[CmdletBinding()]
param(
    # The git ref, e.g. refs/tags/v0.1.0 or refs/tags/0.1.0
    [Parameter(Mandatory = $true)]
    [string]$Ref,

    # Version to use when Ref is not a tag (manual runs).
    [string]$FallbackVersion = "0.0.0-dev",

    # Overrides reading pyproject.toml. For testing this script itself.
    [string]$DeclaredVersion,

    # Where pyproject.toml lives.
    [string]$ProjectRoot = "."
)

$ErrorActionPreference = "Stop"

if (-not $DeclaredVersion) {
    $pyproject = Join-Path $ProjectRoot "pyproject.toml"
    if (-not (Test-Path $pyproject)) {
        throw "pyproject.toml not found at $pyproject"
    }
    # tomllib rather than a regex: a regex would happily match a commented-out
    # or similarly-named key. Needs Python 3.11+.
    $DeclaredVersion = python -c "import tomllib,sys; print(tomllib.load(open(sys.argv[1],'rb'))['project']['version'])" $pyproject
    if ($LASTEXITCODE -ne 0) { throw "could not read the version from $pyproject" }
    $DeclaredVersion = $DeclaredVersion.Trim()
}

if ($Ref -like "refs/tags/*") {
    # The "v" is optional: the GitHub Releases page does not add it, so both
    # "v0.1.0" and "0.1.0" have to resolve to the same version.
    $tag = $Ref -replace "^refs/tags/v?", ""

    if ($tag -ne $DeclaredVersion) {
        Write-Host "::error::Tag v$tag does not match pyproject.toml version $DeclaredVersion."
        Write-Host "::error::Fix it with one of:"
        Write-Host "::error::  - set version = ""$tag"" in pyproject.toml, commit, then retag; or"
        Write-Host "::error::  - delete the tag and retag as $DeclaredVersion (or v$DeclaredVersion)"
        exit 1
    }

    $version = $tag
    Write-Host "Tag v$tag matches pyproject.toml. Building $version"
} else {
    # Manual runs are for rehearsing a build, so any version is allowed.
    $version = $FallbackVersion
    Write-Host "Manual build of $version (pyproject.toml declares $DeclaredVersion)"
}

if ($env:GITHUB_OUTPUT) {
    "version=$version" | Out-File -FilePath $env:GITHUB_OUTPUT -Append
}
Write-Output $version
