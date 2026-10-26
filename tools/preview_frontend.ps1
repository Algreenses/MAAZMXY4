[CmdletBinding()]
param(
    [string]$OutputDirectory,
    [string]$PythonRuntimeDirectory,
    [switch]$Demo,
    [switch]$Launch
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..')).TrimEnd([IO.Path]::DirectorySeparatorChar)
$distRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot 'dist')).TrimEnd([IO.Path]::DirectorySeparatorChar)
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $distRoot 'script-studio' }
elseif (-not [IO.Path]::IsPathRooted($OutputDirectory)) { $OutputDirectory = Join-Path $projectRoot $OutputDirectory }
$previewRoot = [IO.Path]::GetFullPath($OutputDirectory).TrimEnd([IO.Path]::DirectorySeparatorChar)

if ($previewRoot.Equals($projectRoot, [StringComparison]::OrdinalIgnoreCase) -or
    $projectRoot.StartsWith($previewRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -or
    $previewRoot.Equals([IO.Path]::GetPathRoot($previewRoot).TrimEnd([IO.Path]::DirectorySeparatorChar), [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Preview output must be an independent directory, not the project root, its parent, or a drive root.'
}
if ($previewRoot.StartsWith($projectRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -and
    -not $previewRoot.StartsWith($distRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Preview directories inside the project must be under dist/. An explicitly named external directory is also supported.'
}
$frontendPath = Join-Path $previewRoot 'MFAAvalonia.exe'
if (-not (Test-Path -LiteralPath $frontendPath)) {
    throw "Build the frontend into this directory first: $previewRoot"
}
foreach ($process in @(Get-Process -Name 'MFAAvalonia' -ErrorAction SilentlyContinue)) {
    try { $processPath = $process.Path } catch { $processPath = $null }
    if ($processPath -and [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($processPath)).Equals($previewRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "This preview is running (PID $($process.Id)). Close it before preparing it again. No process was stopped."
    }
}

function Get-PreviewPath {
    param([string]$RelativePath)
    $resolved = [IO.Path]::GetFullPath((Join-Path $previewRoot $RelativePath))
    if (-not $resolved.StartsWith($previewRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Preview destination escaped the output directory: $RelativePath"
    }
    # Reject junctions/symlinks rather than accidentally writing through one to user data.
    $current = $resolved
    while ($current -and ($current.Equals($previewRoot, [StringComparison]::OrdinalIgnoreCase) -or
        $current.StartsWith($previewRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase))) {
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Preview destination cannot be a junction or symbolic link: $current"
            }
        }
        $current = [IO.Path]::GetDirectoryName($current)
    }
    return $resolved
}

function Copy-PreviewFile {
    param([string]$SourcePath, [string]$RelativePath)
    $destination = Get-PreviewPath $RelativePath
    New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($destination)) -Force | Out-Null
    Copy-Item -LiteralPath $SourcePath -Destination $destination -Force
}

function Copy-PreviewTree {
    param([string]$SourcePath, [string]$RelativePath, [string]$Filter = '*')
    if (-not (Test-Path -LiteralPath $SourcePath -PathType Container)) { throw "Required directory is missing: $SourcePath" }
    $sourceRoot = [IO.Path]::GetFullPath($SourcePath).TrimEnd([IO.Path]::DirectorySeparatorChar)
    New-Item -ItemType Directory -Path (Get-PreviewPath $RelativePath) -Force | Out-Null
    foreach ($file in Get-ChildItem -LiteralPath $sourceRoot -Recurse -File -Filter $Filter) {
        if (($file.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Source file cannot be a symbolic link: $($file.FullName)"
        }
        $child = $file.FullName.Substring($sourceRoot.Length).TrimStart([IO.Path]::DirectorySeparatorChar)
        Copy-PreviewFile $file.FullName (Join-Path $RelativePath $child)
    }
}

Copy-PreviewFile (Join-Path $projectRoot 'assets/interface.json') 'interface.json'
$previewInterfacePath = Get-PreviewPath 'interface.json'
$previewInterface = Get-Content -LiteralPath $previewInterfacePath -Raw -Encoding UTF8 | ConvertFrom-Json
$previewInterface.label = [string]$previewInterface.label + ' [Script Studio Preview]'
[IO.File]::WriteAllText($previewInterfacePath, (($previewInterface | ConvertTo-Json -Depth 100) + "`n"), (New-Object Text.UTF8Encoding($false)))
Copy-PreviewTree (Join-Path $projectRoot 'assets/resource') 'resource'
Copy-PreviewTree (Join-Path $projectRoot 'agent/recordings') 'agent/recordings'
$publicDataFiles = @(& git -C $projectRoot ls-files -- assets/data)
if ($LASTEXITCODE -ne 0) { throw 'Cannot determine the public data file list.' }
foreach ($relative in $publicDataFiles) {
    Copy-PreviewFile (Join-Path $projectRoot $relative) $relative
}

# Only versioned Agent files and the new script-studio sources are distributable.
# Do not copy ignored local plugins, hooks, logs, or bytecode from the developer checkout.
$publicAgentFiles = @(& git -C $projectRoot ls-files -- agent)
if ($LASTEXITCODE -ne 0) { throw 'Cannot determine the public Agent file list with git ls-files.' }
$publicAgentFiles += @(Get-ChildItem -LiteralPath (Join-Path $projectRoot 'agent') -File -Filter 'script_studio*.py' |
    ForEach-Object { 'agent/' + $_.Name })
$studioPackage = Join-Path $projectRoot 'agent/script_studio'
if (Test-Path -LiteralPath $studioPackage) {
    $publicAgentFiles += @(Get-ChildItem -LiteralPath $studioPackage -Recurse -File -Filter '*.py' |
        ForEach-Object { $_.FullName.Substring($projectRoot.Length + 1).Replace([IO.Path]::DirectorySeparatorChar, [char]'/') })
}
foreach ($relative in $publicAgentFiles | Sort-Object -Unique) {
    if ($relative -notmatch '^agent/' -or $relative -match '(^|/)(__pycache__|adventure_order|local_hook)([./]|$)' -or
        $relative -match '\.(pyc|log)$') { continue }
    Copy-PreviewFile (Join-Path $projectRoot $relative) $relative
}

Write-Host 'Copying the embedded Python runtime...'
if (-not $PythonRuntimeDirectory) { $PythonRuntimeDirectory = Join-Path $projectRoot 'runtimes/python' }
Copy-PreviewTree $PythonRuntimeDirectory 'runtimes/python'
foreach ($relative in @('docs/images/icon.png', 'LICENSE', 'NOTICE')) {
    $source = Join-Path $projectRoot $relative
    if (Test-Path -LiteralPath $source) { Copy-PreviewFile $source $relative }
}
New-Item -ItemType Directory -Path (Get-PreviewPath 'recordings') -Force | Out-Null
$previewConfig = Get-PreviewPath 'config/instances/default.json'
if (-not (Test-Path -LiteralPath $previewConfig)) {
    New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($previewConfig)) -Force | Out-Null
    $initialConfig = [ordered]@{ InstanceName = 'Script Studio Preview'; TaskItems = @(); CurrentTasks = @(); 'UI.LiveView.EnableLiveView' = $false }
    [IO.File]::WriteAllText($previewConfig, (($initialConfig | ConvertTo-Json -Depth 5) + "`n"), (New-Object Text.UTF8Encoding($false)))
}

if ($Demo) {
    $sample = Join-Path $projectRoot 'frontend/samples/editor-demo.json'
    if (-not (Test-Path -LiteralPath $sample)) { throw "The editor sample is missing: $sample" }
    $demoPath = Get-PreviewPath 'recordings/editor-demo.json'
    if (-not (Test-Path -LiteralPath $demoPath)) {
        Copy-PreviewFile $sample 'recordings/editor-demo.json'
    } else {
        Write-Host 'The preview sample already exists; keeping its current contents.'
    }
}

Write-Host "Isolated preview is ready: $previewRoot"
if ($Launch) {
    $previousRoot = [Environment]::GetEnvironmentVariable('MAAZMXY4_PROJECT_ROOT', 'Process')
    try {
        # The child must use this copy even if a developer override exists in the caller.
        [Environment]::SetEnvironmentVariable('MAAZMXY4_PROJECT_ROOT', $previewRoot, 'Process')
        $previewProcess = Start-Process -FilePath $frontendPath -WorkingDirectory $previewRoot -WindowStyle Normal -PassThru
        Write-Host "Preview started (PID $($previewProcess.Id))."
    } finally {
        [Environment]::SetEnvironmentVariable('MAAZMXY4_PROJECT_ROOT', $previousRoot, 'Process')
    }
}
