[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$clientPath = Join-Path $projectRoot 'MFAAvalonia.exe'
if (-not (Test-Path -LiteralPath $clientPath)) {
    throw 'Runtime is missing. Run: py -3.12 tools/setup_runtime.py'
}
$previousMaaFwPath = $env:MAAFW_BINARY_PATH
try {
    $env:MAAFW_BINARY_PATH = Join-Path $projectRoot 'runtimes\win-x64\native'
    Start-Process -FilePath $clientPath -WorkingDirectory $projectRoot -WindowStyle Normal
} finally {
    $env:MAAFW_BINARY_PATH = $previousMaaFwPath
}
