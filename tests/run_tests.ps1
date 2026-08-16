<#
.SYNOPSIS
    Run the Easy Tasks headless test suite against one or more Blender builds.

.EXAMPLE
    .\run_tests.ps1
    .\run_tests.ps1 -Blender "C:\Program Files\Blender Foundation\Blender 4.5\blender.exe"
    .\run_tests.ps1 -IncludeBench
#>
[CmdletBinding()]
param(
    [string[]]$Blender,
    [switch]$IncludeBench
)

$ErrorActionPreference = 'Stop'

if (-not $Blender) {
    $Blender = Get-ChildItem "C:\Program Files\Blender Foundation" -Filter blender.exe `
                   -Recurse -Depth 2 -ErrorAction SilentlyContinue |
               Select-Object -ExpandProperty FullName
}
if (-not $Blender) { throw "No blender.exe found. Pass -Blender <path>." }

# install_test needs a built zip and writes into a sandboxed config dir
$suite = @('smoke_test.py', 'icon_test.py', 'install_test.py', 'redundancy_test.py')
if ($IncludeBench) { $suite += 'bench.py' }

$sandbox = Join-Path $env:TEMP "et_test_cfg"
New-Item -ItemType Directory -Force $sandbox | Out-Null
$env:BLENDER_USER_RESOURCES = $sandbox

$failed = @()
foreach ($exe in $Blender) {
    foreach ($script in $suite) {
        $path = Join-Path $PSScriptRoot $script
        if (-not (Test-Path $path)) { continue }

        Write-Host "`n--- $script  @  $exe ---" -ForegroundColor Cyan

        # Do NOT redirect stderr here. Blender writes harmless notices (TBBmalloc,
        # GPU backend) to stderr, and PowerShell 5.1 wraps native stderr lines in
        # ErrorRecords — which, under ErrorActionPreference='Stop', aborts the run
        # even though Blender exited 0. Results are on stdout.
        $previous = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            $output = & $exe --background --factory-startup --python $path
        }
        finally {
            $ErrorActionPreference = $previous
        }

        $output | Select-String -Pattern 'PASS|FAIL|RESULT|old .* new ' |
            ForEach-Object { $_.Line }

        if ($output -match 'RESULT: .*FAILURE' -or $LASTEXITCODE -ne 0) {
            $failed += "$script @ $exe"
        }
    }
}

Write-Host ""
if ($failed) {
    Write-Host "FAILED: $($failed -join '; ')" -ForegroundColor Red
    exit 1
}
Write-Host "All suites passed." -ForegroundColor Green
