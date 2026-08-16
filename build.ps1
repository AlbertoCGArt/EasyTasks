<#
.SYNOPSIS
    Package the Easy Tasks addon into an installable Blender zip.

.DESCRIPTION
    Blender installs an addon under the folder name inside the zip, and that
    folder name becomes the Python module name. The source lives in EasyTasks/
    for exactly that reason, so the installed module matches
    ET_AddonPreferences.bl_idname (which is __name__).

    Note the archive must contain the FOLDER, not its contents — zipping
    'EasyTasks\*' produces a zip Blender cannot install.

.PARAMETER OutputDir
    Where to write the zip. Defaults to the repo root.

.PARAMETER Install
    Also copy the staged folder straight into a Blender addons directory.

.EXAMPLE
    .\build.ps1
    .\build.ps1 -Install "$env:APPDATA\Blender Foundation\Blender\4.5\scripts\addons"
#>
[CmdletBinding()]
param(
    [string]$OutputDir = $PSScriptRoot,
    [string]$Install
)

$ErrorActionPreference = 'Stop'

$moduleName = 'EasyTasks'
$sourceDir  = Join-Path $PSScriptRoot $moduleName
$source     = Join-Path $sourceDir '__init__.py'

if (-not (Test-Path $source)) {
    throw "Source not found: $source"
}

# --- read the version out of bl_info so the zip name can never drift --------
$versionMatch = Select-String -Path $source -Pattern '^\s*"version":\s*\((\d+),\s*(\d+),\s*(\d+)\)' |
                Select-Object -First 1
if (-not $versionMatch) {
    throw "Could not parse the version tuple from bl_info in $source"
}
$version = '{0}.{1}.{2}' -f $versionMatch.Matches[0].Groups[1].Value,
                             $versionMatch.Matches[0].Groups[2].Value,
                             $versionMatch.Matches[0].Groups[3].Value

# --- sanity check: the file must at least parse ----------------------------
$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if ($python) {
    & $python -c "import ast,sys; ast.parse(open(sys.argv[1],encoding='utf-8').read())" $source
    if ($LASTEXITCODE -ne 0) { throw "Syntax check failed for $source" }
    Write-Host "Syntax check passed" -ForegroundColor DarkGray
}

# --- stage ------------------------------------------------------------------
$staging = Join-Path ([System.IO.Path]::GetTempPath()) ("et_build_" + [guid]::NewGuid().ToString('N'))
$payload = Join-Path $staging $moduleName
New-Item -ItemType Directory -Path $payload -Force | Out-Null

try {
    # Copy the package contents, excluding anything Python or the tests leave behind.
    Copy-Item -Path (Join-Path $sourceDir '*') -Destination $payload -Recurse -Exclude '__pycache__', '*.pyc'

    $zipPath = Join-Path $OutputDir ("easy_tasks_{0}.zip" -f $version)
    if (Test-Path $zipPath) { Remove-Item $zipPath -Force }

    Compress-Archive -Path $payload -DestinationPath $zipPath -CompressionLevel Optimal

    $size = [math]::Round((Get-Item $zipPath).Length / 1KB, 1)
    Write-Host "Built $zipPath ($size KB)" -ForegroundColor Green
    Write-Host "  archive root: $moduleName/__init__.py" -ForegroundColor DarkGray

    if ($Install) {
        if (-not (Test-Path $Install)) {
            throw "Addons directory not found: $Install"
        }
        $dest = Join-Path $Install $moduleName
        if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
        Copy-Item -Path $payload -Destination $dest -Recurse
        Write-Host "Installed to $dest" -ForegroundColor Green
    }
}
finally {
    Remove-Item $staging -Recurse -Force -ErrorAction SilentlyContinue
}
