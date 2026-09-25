<#
.SYNOPSIS
    Build the BetterRef extension zip.

.DESCRIPTION
    PowerShell port of package.sh. Stages the add-on into a temporary dist/
    directory, leaving out development files, has Blender validate and build
    it, moves the resulting .zip next to this script and removes the staging
    directory again.

.PARAMETER Blender
    Path to blender.exe. Falls back to $env:BLENDER_BIN, then to blender on
    PATH, then to the newest install under Program Files.

.PARAMETER OutputDir
    Where the built .zip is placed. Defaults to the project directory.

.PARAMETER KeepStaging
    Leave dist/ in place afterwards, to inspect what was packaged.

.EXAMPLE
    .\package.ps1

.EXAMPLE
    .\package.ps1 -Blender "C:\Program Files\Blender Foundation\Blender 4.5\blender.exe"
#>

#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$Blender = $env:BLENDER_BIN,
    # Resolved below rather than defaulted here: $PSScriptRoot is not yet
    # populated while param() defaults are evaluated under PowerShell 5.1.
    [string]$OutputDir,
    [switch]$KeepStaging
)

$ErrorActionPreference = 'Stop'

# Mirrors the --exclude list in package.sh. *.ps1 is added so this script does
# not ship inside the extension the way *.sh already did not.
$InlineExcludes = @(
    '__pycache__'
    '.ruff_cache'
    '.venv'
    '.git'
    '*.sh'
    '*.ps1'
    'tests'
    'dist'
    'docs'
    'legacy'
    'README.md'
    'pyrefly.toml'
    'requirements.txt'
    'ruff.toml'
    '.gitignore'
    '.gitattributes'
    '.vscode'
    'logo'
)

function Resolve-BlenderPath {
    param([string]$Explicit)

    # An explicit path is taken at its word: falling back to some other install
    # would quietly build with a Blender the caller did not ask for.
    if ($Explicit) {
        if (Test-Path -LiteralPath $Explicit -PathType Leaf) {
            return (Resolve-Path -LiteralPath $Explicit).Path
        }
        $resolved = Get-Command $Explicit -CommandType Application -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($resolved) { return $resolved.Source }
        throw "Blender was not found at '$Explicit'."
    }

    $candidates = New-Object System.Collections.Generic.List[string]

    $onPath = Get-Command 'blender' -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($onPath) { $candidates.Add($onPath.Source) }

    Get-ChildItem -Path 'C:\Program Files\Blender Foundation' -Filter 'Blender *' `
            -Directory -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending |
        ForEach-Object { $candidates.Add((Join-Path $_.FullName 'blender.exe')) }

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    throw 'Blender was not found. Pass -Blender <path> or set BLENDER_BIN.'
}

function Get-IgnorePatterns {
    <#
        A pragmatic subset of .gitignore syntax: comments, blanks and negations
        are skipped and leading/trailing slashes are dropped, which covers the
        plain directory and *.ext patterns this project uses. It is not a full
        gitignore implementation.
    #>
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return @() }

    Get-Content -LiteralPath $Path | ForEach-Object {
        $line = $_.Trim()
        if (-not $line) { return }
        if ($line.StartsWith('#')) { return }
        if ($line.StartsWith('!')) { return }
        $line.Trim('/')
    } | Where-Object { $_ }
}

function Test-Excluded {
    param(
        [string]$RelativePath,
        [string[]]$Patterns
    )

    $segments = $RelativePath -split '/'
    $leaf = $segments[-1]

    foreach ($pattern in $Patterns) {
        if ($pattern -match '[*?]') {
            if ($leaf -like $pattern) { return $true }
            if ($RelativePath -like $pattern) { return $true }
        }
        else {
            # Bare names match any path segment, the way rsync --exclude does.
            if ($segments -contains $pattern) { return $true }
            # Multi-segment patterns such as tests/artifacts match by prefix.
            if ($RelativePath -eq $pattern -or $RelativePath -like "$pattern/*") {
                return $true
            }
        }
    }

    return $false
}

$projectRoot = $PSScriptRoot
$staging = Join-Path $projectRoot 'dist'
$blenderExe = Resolve-BlenderPath -Explicit $Blender

if (-not $OutputDir) { $OutputDir = $projectRoot }
if (-not (Test-Path -LiteralPath $OutputDir -PathType Container)) {
    New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
}
$OutputDir = (Resolve-Path -LiteralPath $OutputDir).Path

Write-Host "Blender: $blenderExe"
Write-Host "Staging: $staging"

if (Test-Path -LiteralPath $staging) {
    Remove-Item -LiteralPath $staging -Recurse -Force
}
New-Item -ItemType Directory -Path $staging | Out-Null

$patterns = @($InlineExcludes) + @(Get-IgnorePatterns (Join-Path $projectRoot '.gitignore'))

$copied = 0
Get-ChildItem -LiteralPath $projectRoot -Recurse -File -Force | ForEach-Object {
    $relative = $_.FullName.Substring($projectRoot.Length).TrimStart('\', '/') -replace '\\', '/'
    if (Test-Excluded -RelativePath $relative -Patterns $patterns) { return }

    $destination = Join-Path $staging $relative
    $parent = Split-Path -Parent $destination
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    Copy-Item -LiteralPath $_.FullName -Destination $destination
    $script:copied++
}

Write-Host "Staged $copied file(s)."

try {
    Push-Location -LiteralPath $staging

    & $blenderExe --command extension validate
    if ($LASTEXITCODE -ne 0) {
        throw "Extension validation failed (exit code $LASTEXITCODE)."
    }

    & $blenderExe --command extension build
    if ($LASTEXITCODE -ne 0) {
        throw "Extension build failed (exit code $LASTEXITCODE)."
    }
}
finally {
    Pop-Location
}

$built = @(Get-ChildItem -LiteralPath $staging -Filter '*.zip' -Recurse -File)
if ($built.Count -eq 0) {
    throw 'Blender reported success but produced no .zip.'
}

foreach ($zip in $built) {
    Move-Item -LiteralPath $zip.FullName -Destination $OutputDir -Force
    Write-Host "Built: $(Join-Path $OutputDir $zip.Name)"
}

if (-not $KeepStaging) {
    Remove-Item -LiteralPath $staging -Recurse -Force
}
